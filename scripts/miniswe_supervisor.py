"""Own the whole Mini-SWE attempt deadline, including imports and GT startup.

The worker owns normal receipts. This process survives a stuck worker and owns
failure conservation; it never promotes a partial trajectory to a completed run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from gt_harness.canonical_io import atomic_json, atomic_write
from gt_harness.process_boundary import harden_process_secret_boundary


@dataclass(frozen=True)
class SupervisedResult:
    reason: str
    returncode: int | None
    elapsed_seconds: float


def _linux_children() -> dict[int, int]:
    """Read PID ownership, not executable names or a host-wide kill pattern."""
    parents = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
            parents[int(path.parent.name)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    return parents


def _reap_owned_children() -> None:
    """The dedicated CLI adopts orphaned setsid workers as a Linux subreaper."""
    deadline = time.monotonic() + 5
    while True:
        children = [pid for pid, parent in _linux_children().items() if parent == os.getpid()]
        if not children:
            return
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        if time.monotonic() >= deadline:
            raise RuntimeError("supervisor_descendant_teardown_incomplete")
        time.sleep(0.01)


def _enable_linux_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "supervisor_subreaper_unavailable")


def _signal_worker(process: subprocess.Popen, *, force: bool) -> None:
    if os.name == "nt":
        # Windows terminate() does not reap grandchildren. This PID was created
        # here; never enumerate or kill unrelated processes by executable name.
        if force:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=10)
        elif process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


def supervise(command: list[str], *, deadline: float,
              termination_grace_seconds: float = 15.0) -> SupervisedResult:
    """Run a process group until one absolute monotonic deadline, then reap it."""
    started = time.monotonic()
    if started >= deadline:
        return SupervisedResult("deadline_exceeded", None, 0.0)
    termination_requested = False

    def request_termination(_signum, _frame):
        nonlocal termination_requested
        termination_requested = True

    previous = signal.signal(signal.SIGTERM, request_termination)
    process = None
    try:
        process = subprocess.Popen(command, start_new_session=os.name != "nt")
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if termination_requested or remaining <= 0:
                reason = "supervisor_termination" if termination_requested else "deadline_exceeded"
                # On Windows kill the whole tree before its root can disappear.
                _signal_worker(process, force=os.name == "nt")
                try:
                    process.wait(timeout=max(0.0, termination_grace_seconds))
                except subprocess.TimeoutExpired:
                    _signal_worker(process, force=True)
                    process.wait(timeout=10)
                finally:
                    if os.name != "nt":
                        _signal_worker(process, force=True)
                return SupervisedResult(reason, process.returncode, time.monotonic() - started)
            try:
                process.wait(timeout=min(remaining, 0.2))
            except subprocess.TimeoutExpired:
                pass
        return SupervisedResult("exited", process.returncode, time.monotonic() - started)
    finally:
        if process is not None and process.poll() is None:
            _signal_worker(process, force=True)
            process.wait(timeout=10)
        signal.signal(signal.SIGTERM, previous)


def _git(repo: Path, arguments: list[str], *, env: dict | None = None) -> bytes:
    return subprocess.run(["git", *arguments], cwd=repo, env=env, check=True,
                          capture_output=True, timeout=10).stdout


def export_patch(
    repo: Path, baseline: str, output: Path, *, excluded_roots: tuple[Path, ...] = ()
) -> None:
    """Conserve the workspace using a disposable index, never the agent index."""
    root = repo.resolve()
    pathspecs = ["."]
    for excluded in (*excluded_roots, output, output.with_name(output.name + ".tmp")):
        resolved = excluded.resolve()
        if resolved == root or resolved in root.parents:
            raise ValueError("patch_exclusion_contains_repository")
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        pathspecs.append(f":(top,exclude,literal){relative.as_posix()}")
    with tempfile.TemporaryDirectory(prefix="gt-supervisor-index-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(repo, ["read-tree", baseline], env=env)
        _git(repo, ["add", "--all", "--", *pathspecs], env=env)
        patch = _git(repo, ["diff", "--cached", "--binary", "--full-index", baseline, "--", *pathspecs], env=env)
    atomic_write(output, patch)


def _read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def conserve_failure(args: argparse.Namespace, result: SupervisedResult, baseline: str) -> int:
    """Keep real bytes and publish ERROR receipts, even if no model was started."""
    report_path = Path(args.metrics or (Path(args.state_dir) / "supervisor_report.json"))
    report = _read_report(report_path)
    terminal = "timeout" if result.reason in {"deadline_exceeded", "supervisor_termination"} else "internal_error"
    exit_code = 3 if terminal == "timeout" else 5
    if result.reason == "exited" and result.returncode in {3, 4, 5, 6}:
        terminal = {3: "timeout", 4: "provider_failed", 5: "internal_error", 6: "setup_error"}[result.returncode]
        exit_code = result.returncode
    report.update(terminal=terminal, exit_code=exit_code, research_valid=False,
                  supervisor={"schema": "gt.supervisor_result.v1", "reason": result.reason,
                              "child_returncode": result.returncode,
                              "elapsed_seconds": result.elapsed_seconds})
    if args.patch_output:
        try:
            if not baseline:
                raise ValueError("baseline_unavailable")
            export_patch(
                Path(args.cwd), baseline, Path(args.patch_output),
                excluded_roots=(Path(args.state_dir),),
            )
        except Exception as exc:
            report["patch_export_error"] = {"type": type(exc).__name__}
    # Do not append a synthetic response or fabricate an acknowledged action.
    # Bind the conserved journal bytes for later chain/accounting verification.
    journals = []
    for path in sorted(Path(args.state_dir).rglob("events.jsonl")):
        raw = path.read_bytes()
        journals.append({"path": path.relative_to(args.state_dir).as_posix(),
                         "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    report["supervisor"]["conserved_journals"] = journals
    atomic_json(report_path, report)
    if args.product_receipt and args.adapter_receipt:
        from gt_harness.runtime_receipts import issue_runtime_receipt_failure
        issue_runtime_receipt_failure(
            report_path=report_path,
            trajectory_path=Path(args.output or (Path(args.state_dir) / "trajectory.json")),
            product_receipt_path=Path(args.product_receipt),
            adapter_receipt_path=Path(args.adapter_receipt),
            task_id=args.task_id, product_source_sha=args.product_source_sha,
            treatment="bare" if args.gt_off or args.gt_mode == "off" else "groundtruth",
            requested_model=args.model, scaffold_version="2.4.6",
            time_budget_seconds=args.time_budget_seconds, terminal=terminal,
            exit_code=exit_code, error=RuntimeError("supervisor:" + result.reason),
        )
    return exit_code


def main() -> int:
    started = time.monotonic()
    harden_process_secret_boundary()
    _enable_linux_subreaper()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--time-budget-seconds", type=int, default=1)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--state-dir", default=".gt-state")
    for name in ("metrics", "output", "patch-output", "product-receipt", "adapter-receipt",
                 "task-id", "product-source-sha", "model"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--gt-off", action="store_true")
    parser.add_argument("--gt-mode", default="advisory")
    args, _ = parser.parse_known_args()
    baseline = ""
    if args.patch_output:
        try:
            baseline = _git(Path(args.cwd), ["rev-parse", "HEAD"]).decode().strip()
        except (OSError, subprocess.SubprocessError):
            pass  # worker emits setup failure; supervisor still conserves its exit
    try:
        result = supervise([sys.executable, "-m", "scripts.miniswe_gt_run", *sys.argv[1:]],
                           deadline=started + max(0, args.time_budget_seconds))
    except (OSError, subprocess.SubprocessError):
        result = SupervisedResult("supervisor_process_failure", None, time.monotonic() - started)
    if sys.platform.startswith("linux"):
        # The indexer starts its own session, so killing only the worker's
        # process group is insufficient. Reap adopted descendants before
        # exporting the now-quiescent workspace.
        try:
            _reap_owned_children()
        except RuntimeError:
            result = SupervisedResult("descendant_teardown_failed", result.returncode,
                                      time.monotonic() - started)
    result = SupervisedResult(result.reason, result.returncode, time.monotonic() - started)
    if result.reason != "exited":
        return conserve_failure(args, result, baseline)
    if result.returncode not in {0, 3, 4, 5, 6}:
        return conserve_failure(args, result, baseline)
    required = [args.metrics, args.product_receipt, args.adapter_receipt, args.patch_output]
    if any(path and not Path(path).is_file() for path in required):
        return conserve_failure(args, result, baseline)
    return result.returncode if result.returncode is not None else 5


if __name__ == "__main__":
    raise SystemExit(main())
