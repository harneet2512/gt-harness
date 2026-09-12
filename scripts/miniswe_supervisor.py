"""Own the whole Mini-SWE attempt deadline, including imports and GT startup.

The worker owns normal receipts. This process survives a stuck worker and owns
failure conservation; it never promotes a partial trajectory to a completed run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gt_harness.canonical_io import atomic_json, atomic_write
from gt_harness.process_boundary import harden_process_secret_boundary


@dataclass(frozen=True)
class SupervisedResult:
    reason: str
    returncode: int | None
    elapsed_seconds: float
    checkpoint_status: str = "not_configured"
    checkpoint_returncode: int | None = None
    checkpoint_error_type: str | None = None


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
              termination_grace_seconds: float = 15.0,
              checkpoint_command: list[str] | None = None,
              checkpoint_status_path: Path | None = None,
              abort_probe: "Callable[[], str | None] | None" = None) -> SupervisedResult:
    """Run a process group until one absolute monotonic deadline, then reap it.

    ``abort_probe`` is an optional cheap callable polled every tick; when it
    returns a reason string the child is terminated through the same path as
    a deadline kill, with the probe's reason preserved on the result. The
    churn governor uses this: detection lives in-process where the command
    stream is, kill authority stays here where journal sealing lives.
    """
    started = time.monotonic()
    if started >= deadline:
        return SupervisedResult("deadline_exceeded", None, 0.0)
    termination_requested = False

    def request_termination(_signum, _frame):
        nonlocal termination_requested
        termination_requested = True

    previous = signal.signal(signal.SIGTERM, request_termination)
    process = None
    checkpoint = None
    result: SupervisedResult | None = None
    checkpoint_status = "not_configured"
    checkpoint_returncode = None
    checkpoint_error_type = None
    try:
        if checkpoint_command:
            try:
                checkpoint = subprocess.Popen(
                    checkpoint_command, start_new_session=os.name != "nt"
                )
                checkpoint_status = "running"
            except (OSError, subprocess.SubprocessError) as exc:
                # Checkpointing is a recovery aid. Its failure must be visible,
                # but cannot prevent the primary agent from starting.
                checkpoint_status = "launch_failed"
                checkpoint_error_type = type(exc).__name__
        process = subprocess.Popen(command, start_new_session=os.name != "nt")
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            abort_reason = abort_probe() if abort_probe is not None else None
            if abort_reason or termination_requested or remaining <= 0:
                reason = (
                    abort_reason
                    or ("supervisor_termination" if termination_requested else "deadline_exceeded")
                )
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
                result = SupervisedResult(reason, process.returncode,
                                          time.monotonic() - started)
                break
            try:
                process.wait(timeout=min(remaining, 0.2))
            except subprocess.TimeoutExpired:
                pass
        if result is None:
            result = SupervisedResult(
                "exited", process.returncode, time.monotonic() - started
            )
    finally:
        try:
            if checkpoint is not None:
                observed = checkpoint.poll()
                try:
                    if observed is None:
                        _signal_worker(checkpoint, force=True)
                    checkpoint_returncode = checkpoint.wait(timeout=10)
                    if observed is None:
                        checkpoint_status = "stopped_after_worker"
                    elif checkpoint_returncode == 0:
                        checkpoint_status = "exited"
                    else:
                        checkpoint_status = "degraded"
                except (OSError, subprocess.SubprocessError) as exc:
                    checkpoint_status = "teardown_failed"
                    checkpoint_error_type = type(exc).__name__
        finally:
            try:
                if process is not None and process.poll() is None:
                    try:
                        _signal_worker(process, force=True)
                        process.wait(timeout=10)
                    except (OSError, subprocess.SubprocessError):
                        # The caller records the primary result; descendant
                        # reaping still runs in main on Linux.
                        pass
            finally:
                signal.signal(signal.SIGTERM, previous)
        checkpoint_available = bool(
            checkpoint_status_path is not None
            and (checkpoint_status_path.parent / "latest.json").is_file()
        )
        if (checkpoint_status in {"stopped_after_worker", "exited"}
                and not checkpoint_available):
            checkpoint_status = "degraded_no_checkpoint"
        if checkpoint_status_path is not None:
            try:
                atomic_json(checkpoint_status_path, {
                    "schema": "gt.checkpoint_helper_result.v1",
                    "status": checkpoint_status,
                    "returncode": checkpoint_returncode,
                    "error_type": checkpoint_error_type,
                    "checkpoint_available": checkpoint_available,
                    "candidate_patch_only": True,
                    "verified": False,
                    "official_score": False,
                })
            except OSError as exc:
                checkpoint_status = "status_publication_failed"
                checkpoint_error_type = type(exc).__name__
    assert result is not None
    return SupervisedResult(
        result.reason, result.returncode, result.elapsed_seconds,
        checkpoint_status, checkpoint_returncode, checkpoint_error_type,
    )


def _git(repo: Path, arguments: list[str], *, env: dict | None = None,
         input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(["git", *arguments], cwd=repo, env=env, check=True,
                          capture_output=True, input=input_bytes, timeout=10).stdout


def submission_patch_state(repo: Path, baseline: str) -> dict:
    """Read collector-shaped commit bytes separately from recoverable work.

    This is an advisory observation, not an atomic workspace snapshot or proof
    that the external collector ran. Never stage or commit on the agent's behalf.
    """
    state = {"layout": "gt.submission_patch_state.v1", "baseline": baseline,
             "official_collection_observed": False, "status": "unavailable"}
    deadline = time.monotonic() + 2.0
    def read(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                              timeout=max(0.001, deadline - time.monotonic())).stdout
    try:
        if not baseline:
            return state
        head = read("rev-parse", "HEAD").decode().strip()
        committed = read("diff", "--binary", baseline, head)
        tracked = read("diff", "--name-only", "-z", head, "--")
        untracked = read("ls-files", "-z", "--others", "--exclude-standard")
        if read("rev-parse", "HEAD").decode().strip() != head:
            return {**state, "reason": "head_changed_during_observation"}
        return {**state, "status": "observed", "head": head,
                "committed_patch_empty": not committed,
                "committed_patch_sha256": hashlib.sha256(committed).hexdigest(),
                "committed_patch_bytes": len(committed),
                "uncommitted_tracked": bool(tracked),
                "untracked_paths": [part.decode("utf-8", "replace")
                                    for part in untracked.split(b"\0") if part]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {**state, "reason": type(exc).__name__}


def export_patch(
    repo: Path, baseline: str, output: Path, *, excluded_roots: tuple[Path, ...] = ()
) -> None:
    """Conserve the workspace using a disposable index, never the agent index."""
    root = repo.resolve()
    pathspecs = ["."]
    # atomic_write owns this exact output-specific temporary namespace.
    # A killed publisher can leave files there after its children are reaped.
    publication_temporaries = tuple(
        path for path in output.parent.iterdir()
        if path.name.startswith(f".{output.name}.tmp.")
    ) if output.parent.is_dir() else ()
    state_exclusions = tuple(path.resolve() for path in excluded_roots)
    artifact_exclusions = tuple(
        path.parent.resolve() / path.name
        for path in (output, output.with_name(output.name + ".tmp"), *publication_temporaries)
    )
    for resolved in (*state_exclusions, *artifact_exclusions):
        if resolved == root or resolved in root.parents:
            raise ValueError("patch_exclusion_contains_repository")
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        pathspecs.append(f":(top,exclude,literal){relative.as_posix()}")
    from gt_engine.repository_identity import is_untracked_runtime_artifact

    untracked = _git(repo, ["ls-files", "-z", "--others", "--exclude-standard"])
    generated = [path for path in untracked.split(b"\0") if path
                 and is_untracked_runtime_artifact(path.decode("utf-8", "surrogateescape"))]
    with tempfile.TemporaryDirectory(prefix="gt-supervisor-index-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(repo, ["read-tree", baseline], env=env)
        _git(repo, ["add", "--all", "--", *pathspecs], env=env)
        if generated:
            _git(repo, ["update-index", "--force-remove", "-z", "--stdin"], env=env,
                 input_bytes=b"\0".join(generated) + b"\0")
        patch = _git(repo, ["diff", "--cached", "--binary", "--full-index", baseline, "--", *pathspecs], env=env)
    atomic_write(output, patch)


def _read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _seal_terminated_journals(state_dir: Path, *, reason: str, terminal: str,
                              exit_code: int) -> dict:
    """Close the run's journals after the child process is dead.

    A deadline/termination kill can leave a provider request admitted but never
    terminally recorded (the orphan), prepared deliveries staged but never
    resolved, and no terminal event in the chained journal. Post-mortem, the
    supervisor is the only writer left, so it seals both journals:

    * ``provider_events.jsonl`` (flat) gets a ``provider_failure`` row for
      every request id without a terminal row — conservation closes.
    * ``events.jsonl`` (hash-chained) gets a ``run_terminal`` row appended via
      the canonical store, recording the orphan set and delivery tallies.
      A torn final line is truncated first; an invalid chain is left alone —
      the verifier already flags it, and a partial seal must never fabricate
      history.
    """
    summary = {"orphaned_provider_requests": [], "provider_failures_sealed": 0,
               "run_terminal_appended": [], "journals_skipped": []}
    error_type = ("DeadlineExceeded" if reason == "deadline_exceeded"
                  else "SupervisorTermination")
    timestamp = datetime.now(UTC).isoformat()
    orphans_by_journal: dict[str, list[str]] = {}
    for journal_path in sorted(state_dir.rglob("provider_events.jsonl")):
        try:
            rows = [json.loads(line) for line in
                    journal_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except (OSError, ValueError):
            summary["journals_skipped"].append(journal_path.name)
            continue
        requests = {str(row.get("request_id") or "") for row in rows
                    if row.get("event") == "provider_request"}
        terminal_ids = {str(row.get("request_id") or "") for row in rows
                        if row.get("event") in {"provider_response", "provider_failure"}}
        orphans = sorted(requests - terminal_ids - {""})
        if orphans:
            with journal_path.open("a", encoding="utf-8") as handle:
                for request_id in orphans:
                    handle.write(json.dumps({
                        "event": "provider_failure",
                        "request_id": request_id,
                        "request_id_kind": "local_correlation",
                        "schema": "gt.provider-receipt.v2",
                        "error_type": error_type,
                        "error": f"supervisor:{reason}",
                        "timestamp_utc": timestamp,
                    }, sort_keys=True, separators=(",", ":")) + "\n")
            summary["provider_failures_sealed"] += len(orphans)
            orphans_by_journal[journal_path.parent.name] = orphans
            summary["orphaned_provider_requests"].extend(orphans)
    from gt_engine.miniswe_integration import ExternalStateStore
    for journal_path in sorted(state_dir.rglob("events.jsonl")):
        try:
            raw = journal_path.read_bytes()
            lines = raw.splitlines()
            # A kill can land mid-append: drop a torn final line before sealing.
            if lines:
                try:
                    json.loads(lines[-1])
                except ValueError:
                    journal_path.write_bytes(b"\n".join(lines[:-1]) + b"\n")
            store = ExternalStateStore(journal_path.parent.parent, journal_path.parent.name)
            if not store.startup_journal_valid:
                summary["journals_skipped"].append(
                    f"{journal_path.parent.name}:journal_invalid")
                continue
            counts = {"requests": 0, "responses": 0, "failures": 0}
            provider_journal = journal_path.parent / "provider_events.jsonl"
            if provider_journal.is_file():
                for line in provider_journal.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line).get("event")
                    except ValueError:
                        continue
                    if event == "provider_request":
                        counts["requests"] += 1
                    elif event == "provider_response":
                        counts["responses"] += 1
                    elif event == "provider_failure":
                        counts["failures"] += 1
            store.append(
                "run_terminal",
                terminal=terminal,
                exit_code=exit_code,
                supervisor_reason=reason,
                orphaned_provider_requests=orphans_by_journal.get(
                    journal_path.parent.name, []),
                provider_request_count=counts["requests"],
                provider_response_count=counts["responses"],
                provider_failure_count=counts["failures"],
            )
            summary["run_terminal_appended"].append(journal_path.parent.name)
        except OSError:
            summary["journals_skipped"].append(journal_path.name)
    return summary


def conserve_failure(args: argparse.Namespace, result: SupervisedResult, baseline: str) -> int:
    """Keep real bytes and publish ERROR receipts, even if no model was started."""
    report_path = Path(args.metrics or (Path(args.state_dir) / "supervisor_report.json"))
    report = _read_report(report_path)
    terminal = "timeout" if result.reason in {"deadline_exceeded", "supervisor_termination"} else "internal_error"
    exit_code = 3 if terminal == "timeout" else 5
    if result.reason == "churn_abort":
        terminal = "churn_abort"
        exit_code = 7
    if result.reason == "exited" and result.returncode in {3, 4, 5, 6, 7}:
        terminal = {3: "timeout", 4: "provider_failed", 5: "internal_error", 6: "setup_error", 7: "churn_abort"}[result.returncode]
        exit_code = result.returncode
    report.update(terminal=terminal, exit_code=exit_code, research_valid=False,
                  synthetic_transport=bool(getattr(args, "synthetic_transport", False)),
                  supervisor={"schema": "gt.supervisor_result.v1", "reason": result.reason,
                              "child_returncode": result.returncode,
                              "elapsed_seconds": result.elapsed_seconds,
                              "checkpoint_status": result.checkpoint_status,
                              "checkpoint_returncode": result.checkpoint_returncode,
                              "checkpoint_error_type": result.checkpoint_error_type})
    report["submission_patch_state"] = submission_patch_state(Path(args.cwd), baseline)
    if args.patch_output:
        try:
            from gt_engine.engine_state import RuntimeLayout

            if not baseline:
                raise ValueError("baseline_unavailable")
            export_patch(
                Path(args.cwd), baseline, Path(args.patch_output),
                excluded_roots=RuntimeLayout.from_run_args(args).excluded_roots,
            )
        except Exception as exc:
            report["patch_export_error"] = {"type": type(exc).__name__}
            try:
                from scripts.miniswe_checkpoint import read_checkpoint

                directory_value = getattr(args, "checkpoint_directory", "")
                run_nonce = getattr(args, "checkpoint_run_nonce", "")
                workspace_sha256 = getattr(args, "checkpoint_workspace_sha256", "")
                if not directory_value or not run_nonce or not workspace_sha256:
                    raise ValueError("checkpoint_attempt_unavailable")
                checkpoint, payload = read_checkpoint(
                    Path(directory_value), baseline, run_nonce=run_nonce,
                    workspace_sha256=workspace_sha256,
                )
                atomic_write(Path(args.patch_output), payload)
                report["supervisor"]["recovered_checkpoint"] = checkpoint
            except Exception as recovery_error:
                report["supervisor"]["checkpoint_recovery_error"] = type(recovery_error).__name__
    # Do not append a synthetic response or fabricate an acknowledged action.
    # Seal the dead child's journals first so the conserved bytes include the
    # terminal event and closed provider-request accounting.
    seal = _seal_terminated_journals(
        Path(args.state_dir), reason=result.reason, terminal=terminal,
        exit_code=exit_code,
    )
    if seal["orphaned_provider_requests"] or seal["run_terminal_appended"]:
        report["supervisor"]["terminal_seal"] = seal
    # Bind the conserved journal bytes for later chain/accounting verification.
    journals = []
    for path in sorted(Path(args.state_dir).rglob("events.jsonl")):
        raw = path.read_bytes()
        journals.append({"path": path.relative_to(args.state_dir).as_posix(),
                         "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    report["supervisor"]["conserved_journals"] = journals
    # A killed child never reaches final_state(), so report["gt"] and the
    # bootstrap counters it carries would be absent — and attestation then
    # reads zeros where the sealed journal proves calls were spent. Rebuild
    # the minimum honest gt section from the sealed journal: resolved model,
    # summed usage, and the GT-internal bootstrap census. Marked as
    # journal-derived so the provenance is auditable, never implied.
    if not args.gt_off and args.gt_mode != "off" and "gt" not in report:
        try:
            gt_state: dict = {
                "verified": False,
                "reconstructed_from_journal": True,
            }
            prompt_tokens = completion_tokens = 0
            for journal in journals:
                journal_path = Path(args.state_dir) / journal["path"]
                for line in journal_path.read_text(
                    encoding="utf-8"
                ).splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    event = row.get("event")
                    if event == "provider_delivery" and row.get("resolved_model"):
                        gt_state["resolved_model"] = str(row["resolved_model"])
                    elif event == "provider_response":
                        usage = row.get("usage")
                        if isinstance(usage, dict):
                            prompt_tokens += int(usage.get("prompt_tokens") or 0)
                            completion_tokens += int(
                                usage.get("completion_tokens") or 0
                            )
                    elif (
                        event == "select_catalog_lifecycle"
                        and row.get("reason") == "provider_request_admitted"
                    ):
                        gt_state["select_catalog_bootstrap_calls"] = (
                            gt_state.get("select_catalog_bootstrap_calls", 0) + 1
                        )
                    elif (
                        event == "persistent_plan_built"
                        and str(row.get("finish_reason") or "") not in {"", "restored"}
                    ):
                        gt_state["persistent_plan_bootstrap_calls"] = (
                            gt_state.get("persistent_plan_bootstrap_calls", 0) + 1
                        )
                    elif event in {"evidence_delivery", "context_addition_delivery"}:
                        if str(row.get("kind") or row.get("evidence_type") or "") in {
                            "context_contract",
                            "context_delta",
                        }:
                            gt_state["contract_shipped"] = True
            gt_state.setdefault("select_catalog_bootstrap_calls", 0)
            gt_state.setdefault("persistent_plan_bootstrap_calls", 0)
            gt_state.setdefault("contract_shipped", False)
            gt_state["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
            report["gt"] = gt_state
            report["gt_mode"] = args.gt_mode
        except (OSError, ValueError):
            # The typed failure report must still be written; a missing gt
            # section stays missing rather than fabricated.
            pass
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
                 "task-id", "task", "product-source-sha", "model"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--gt-off", action="store_true")
    parser.add_argument("--synthetic-transport", action="store_true")
    parser.add_argument("--gt-mode", default="advisory")
    args, _ = parser.parse_known_args()
    baseline = ""
    checkpoint_command = None
    checkpoint_status_path = None
    if args.patch_output:
        try:
            baseline = _git(Path(args.cwd), ["rev-parse", "HEAD"]).decode().strip()
        except (OSError, subprocess.SubprocessError):
            pass  # worker emits setup failure; supervisor still conserves its exit
    if baseline and time.monotonic() < started + max(0, args.time_budget_seconds):
        from gt_engine.engine_state import RuntimeLayout

        layout = RuntimeLayout.from_run_args(args)
        from scripts.miniswe_checkpoint import workspace_identity

        run_nonce = secrets.token_hex(16)
        directory = layout.task_root / "recovery" / run_nonce
        request_path = directory / "request.json"
        workspace_sha256 = workspace_identity(layout.workspace)
        atomic_json(request_path, {
            "workspace": str(layout.workspace), "baseline": baseline,
            "directory": str(directory),
            "run_nonce": run_nonce, "workspace_sha256": workspace_sha256,
            "excluded_roots": [str(path) for path in layout.excluded_roots],
            "deadline": started + max(0, args.time_budget_seconds),
            "synthetic_transport": args.synthetic_transport,
        })
        checkpoint_command = [sys.executable, "-I", "-m", "scripts.miniswe_checkpoint",
                              str(request_path)]
        checkpoint_status_path = directory / "helper_result.json"
        args.checkpoint_directory = str(directory)
        args.checkpoint_run_nonce = run_nonce
        args.checkpoint_workspace_sha256 = workspace_sha256
    _wall_start = time.time()

    def _churn_abort_probe() -> str | None:
        """The in-process churn governor drops a flag when a run loops.

        Cheap poll: a flag file, not a journal parse - the journal row is
        the durable record; this file only hastens the kill. A flag older
        than this child's start is a leftover from a recycled state dir and
        must not kill a fresh run.
        """
        for path in Path(args.state_dir).rglob("churn_abort.json"):
            try:
                if path.is_file() and path.stat().st_mtime >= _wall_start:
                    return "churn_abort"
            except OSError:
                continue
        return None

    try:
        result = supervise([sys.executable, "-m", "scripts.miniswe_gt_run", *sys.argv[1:]],
                           deadline=started + max(0, args.time_budget_seconds),
                           checkpoint_command=checkpoint_command,
                           checkpoint_status_path=checkpoint_status_path,
                           abort_probe=_churn_abort_probe)
    except (OSError, subprocess.SubprocessError) as exc:
        result = SupervisedResult(
            "supervisor_process_failure", None, time.monotonic() - started,
            "supervision_failed", None, type(exc).__name__,
        )
    if sys.platform.startswith("linux"):
        # The indexer starts its own session, so killing only the worker's
        # process group is insufficient. Reap adopted descendants before
        # exporting the now-quiescent workspace.
        try:
            _reap_owned_children()
        except RuntimeError:
            result = SupervisedResult(
                "descendant_teardown_failed", result.returncode,
                time.monotonic() - started, result.checkpoint_status,
                result.checkpoint_returncode, result.checkpoint_error_type,
            )
    result = SupervisedResult(
        result.reason, result.returncode, time.monotonic() - started,
        result.checkpoint_status, result.checkpoint_returncode,
        result.checkpoint_error_type,
    )
    if result.reason != "exited":
        return conserve_failure(args, result, baseline)
    if result.returncode not in {0, 3, 4, 5, 6}:
        return conserve_failure(args, result, baseline)
    required = [args.metrics, args.product_receipt, args.adapter_receipt, args.patch_output]
    if any(path and not Path(path).is_file() for path in required):
        return conserve_failure(args, result, baseline)
    return result.returncode if result.returncode is not None else 5


def command_worker(arguments: list[str]) -> int:
    """Stream native command output; contain the workload on timeout/shutdown."""
    receipt_path, cwd, timeout, command = arguments[:4]
    argv = json.loads(arguments[4]) if len(arguments) == 5 else None
    if argv is not None and (not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)):
        raise ValueError("invalid worker argv")
    os.chdir(cwd)
    _enable_linux_subreaper()
    interrupted = False

    def terminate(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, terminate)
    deadline = time.monotonic() + float(timeout)
    reason = "exited"
    child = subprocess.Popen(argv if argv is not None else command, shell=argv is None, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
    eof = False
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        while not eof or child.poll() is None:
            if interrupted or time.monotonic() >= deadline:
                reason = "supervisor_termination" if interrupted else "deadline_exceeded"
                _signal_worker(child, force=True)
                child.wait(timeout=5)
                _reap_owned_children()
                # All writers are dead; drain bytes already committed to the pipe.
                while chunk := child.stdout.read(65536):
                    sys.stdout.buffer.write(chunk)
                break
            for key, _ in selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                else:
                    selector.unregister(key.fileobj)
                    eof = True
    child.wait(timeout=5)
    child.stdout.close()
    sys.stdout.buffer.flush()
    # Native shell semantics permit a background service that redirects its
    # output to survive a successful action. The outer task supervisor owns it.
    # An inherited output pipe, however, must reach EOF before normal completion.
    atomic_json(Path(receipt_path), {
        "schema": "gt.command_worker.v1", "reason": reason,
        "returncode": child.returncode, "capture_complete": reason == "exited",
        "descendants_reaped": reason != "exited",
        "surviving_descendants": sorted(pid for pid, parent in _linux_children().items()
                                         if parent == os.getpid()),
    })
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--command-worker"]:
        raise SystemExit(command_worker(sys.argv[2:]))
    raise SystemExit(main())
