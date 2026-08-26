#!/usr/bin/env python3
"""Exercise the canonical graph lifecycle on an isolated real-repository clone."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.repository_graph_service import (  # noqa: E402
    GraphNotReadyError,
    GraphReceipt,
    GraphStatus,
    RepositoryGraphService,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False, stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        diagnostic = " ".join((result.stderr or result.stdout).split())[:2000]
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]}: {diagnostic}")
    return result.stdout.strip()


def _compact(receipt: GraphReceipt) -> dict[str, Any]:
    return {
        "repository": receipt.repository,
        "commit_sha": receipt.commit_sha,
        "working_tree_state": receipt.working_tree_state,
        "source_revision": receipt.source_revision,
        "graph_status": receipt.build_status.value,
        "query_ready": receipt.query_ready,
        "graph_identity": receipt.graph_checksum_or_identity,
        "files_discovered": receipt.files_discovered,
        "files_indexed": receipt.files_indexed,
        "symbols": receipt.symbols,
        "nodes_by_type": receipt.nodes_by_type,
        "edges_by_type": receipt.edges_by_type,
        "update_mode": receipt.update_mode,
        "degraded_reasons": list(receipt.degraded_reasons),
    }


def _evidence(result: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(row.get("name") or ""), str(row.get("file_path") or ""))
        for row in result.get("evidence", [])
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def _await_building(receipt_path: Path, process: subprocess.Popen[str]) -> bool:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and process.poll() is None:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            if payload.get("build_status") == "BUILDING":
                return True
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.005)
    return False


def _isolated_process_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)


def _markdown(report: dict[str, Any], receipt_path: Path) -> str:
    lines = [
        "# GroundTruth Graph Lifecycle Audit",
        "",
        f"Observed: `{report['completed']}`",
        "",
        f"Verdict: **{report['status']}**",
        "",
        f"Machine receipt: `{receipt_path}`",
        "",
        "The campaign used an isolated local clone of the frozen real itsdangerous checkout. "
        "All graph operations went through `RepositoryGraphService` or the production CLI.",
        "",
        "| Phase | Result |",
        "| --- | --- |",
    ]
    for phase in report["phases"]:
        lines.append(f"| {phase['phase']} | {phase['status']} |")
    lines.extend(
        [
            "",
            "## Key observations",
            "",
            f"- Cold/warm graph identity stable: `{report['cold_warm_same_graph']}`.",
            f"- Commit A: `{report['commit_a']}`.",
            f"- Commit B: `{report['commit_b']}`.",
            "- Add, modify, rename, and delete each produced an explicit STALE state before "
            "an atomic full rebuild and exact post-update query result.",
            "- A process killed after the BUILDING receipt left no queryable partial graph; a "
            "fresh production build recovered the state.",
            f"- Concurrent read/update unexpected errors: `{len(report['concurrent_errors'])}`.",
            "",
            "This campaign proves the canonical correctness-first full-rebuild lifecycle on one "
            "real Python repository. It does not claim file-keyed incremental parity or certify "
            "the same lifecycle for every language yet.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    source_repository = Path(args.source_repository).resolve()
    run_dir = Path(args.run_dir).resolve()
    if run_dir.exists():
        raise SystemExit(f"refusing to reuse lifecycle run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    repository = run_dir / "repository"
    state = run_dir / "state"
    phases: list[dict[str, Any]] = []

    _run("git", "clone", "--quiet", "--no-hardlinks", str(source_repository), str(repository))
    _run("git", "checkout", "--detach", "--quiet", args.commit, cwd=repository)
    _run("git", "config", "user.email", "gt-lifecycle@example.invalid", cwd=repository)
    _run("git", "config", "user.name", "GT Lifecycle Audit", cwd=repository)
    _require(_run("git", "rev-parse", "HEAD", cwd=repository) == args.commit, "wrong clone SHA")
    _require(not _run("git", "status", "--porcelain=v1", cwd=repository), "clone is dirty")

    service = RepositoryGraphService(repository, state_dir=state)
    cold = service.build(force=True)
    _require(cold.query_ready, "cold graph is not ready")
    cold_query = service.query("definition", "Signer")
    _require(
        ("Signer", "src/itsdangerous/signer.py") in _evidence(cold_query),
        "cold graph query does not match repository truth",
    )
    phases.append({"phase": "cold_start", "status": "PASS", "receipt": _compact(cold)})

    reopened = RepositoryGraphService(repository, state_dir=state)
    warm = reopened.status()
    warm_query = reopened.query("definition", "Signer")
    same_graph = (
        cold.graph_checksum_or_identity == warm.graph_checksum_or_identity
        and cold.source_revision == warm.source_revision
        and _evidence(cold_query) == _evidence(warm_query)
    )
    _require(warm.query_ready and same_graph, "warm graph did not reuse exact cold state")
    phases.append({"phase": "warm_start", "status": "PASS", "receipt": _compact(warm)})

    probe = repository / "gt_lifecycle_probe.py"
    _write(
        probe,
        "def probe_target(value):\n    return value + 1\n\n"
        "def probe_caller(value):\n    return probe_target(value)\n",
    )
    _require(service.status().build_status is GraphStatus.STALE, "new file was not STALE")
    added = service.update()
    added_callers = service.query(
        "callers", "probe_target", file_path="gt_lifecycle_probe.py"
    )
    _require(
        _evidence(added_callers) == {("probe_caller", "gt_lifecycle_probe.py")},
        "new-file call edge is wrong",
    )
    phases.append({"phase": "new_file", "status": "PASS", "receipt": _compact(added)})

    _write(
        probe,
        "def probe_target_v2(value):\n    return value + 2\n\n"
        "def probe_caller(value):\n    return probe_target_v2(value)\n",
    )
    _require(service.status().build_status is GraphStatus.STALE, "modified file was not STALE")
    modified = service.update()
    old = service.query("definition", "probe_target")
    new_callers = service.query(
        "callers", "probe_target_v2", file_path="gt_lifecycle_probe.py"
    )
    _require(old["status"] == "NOT_FOUND" and not old["evidence"], "stale symbol survived edit")
    _require(
        _evidence(new_callers) == {("probe_caller", "gt_lifecycle_probe.py")},
        "modified call edge is wrong",
    )
    phases.append({"phase": "modified_file", "status": "PASS", "receipt": _compact(modified)})

    renamed = repository / "gt_lifecycle_renamed.py"
    os.replace(probe, renamed)
    _require(service.status().build_status is GraphStatus.STALE, "rename was not STALE")
    renamed_receipt = service.update()
    renamed_definition = service.query("definition", "probe_target_v2")
    _require(
        _evidence(renamed_definition) == {("probe_target_v2", "gt_lifecycle_renamed.py")},
        "renamed file path is wrong",
    )
    phases.append({"phase": "renamed_file", "status": "PASS", "receipt": _compact(renamed_receipt)})

    renamed.unlink()
    _require(service.status().build_status is GraphStatus.STALE, "deletion was not STALE")
    deleted = service.update()
    deleted_query = service.query("definition", "probe_target_v2")
    _require(
        deleted_query["status"] == "NOT_FOUND" and not deleted_query["evidence"],
        "deleted symbol or edge survived rebuild",
    )
    phases.append({"phase": "deleted_file", "status": "PASS", "receipt": _compact(deleted)})

    commit_probe = repository / "gt_commit_probe.py"
    _write(commit_probe, "def commit_probe_a():\n    return 'A'\n")
    _run("git", "add", "gt_commit_probe.py", cwd=repository)
    _run("git", "commit", "-qm", "lifecycle commit A", cwd=repository)
    commit_a = _run("git", "rev-parse", "HEAD", cwd=repository)
    at_a = service.update()
    _require(at_a.commit_sha == commit_a, "graph did not bind to commit A")
    _write(commit_probe, "def commit_probe_b():\n    return 'B'\n")
    _run("git", "add", "gt_commit_probe.py", cwd=repository)
    _run("git", "commit", "-qm", "lifecycle commit B", cwd=repository)
    commit_b = _run("git", "rev-parse", "HEAD", cwd=repository)
    stale_b = service.status()
    _require(stale_b.build_status is GraphStatus.STALE, "commit B mismatch was not STALE")
    at_b = service.update()
    _require(at_b.commit_sha == commit_b and at_b.query_ready, "graph did not bind to commit B")
    _require(
        service.query("definition", "commit_probe_a")["status"] == "NOT_FOUND",
        "commit A symbol survived",
    )
    _require(
        _evidence(service.query("definition", "commit_probe_b"))
        == {("commit_probe_b", "gt_commit_probe.py")},
        "commit B symbol missing",
    )
    phases.append({"phase": "commit_change", "status": "PASS", "receipt": _compact(at_b)})

    crash_state = run_dir / "crash-state"
    command = [
        sys.executable,
        "-m",
        "gt_harness.cli",
        "graph",
        "build",
        "--root",
        str(repository),
        "--state-dir",
        str(crash_state),
        "--force",
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_isolated_process_kwargs(),
    )
    saw_building = _await_building(crash_state / "build-attempt.json", process)
    _require(saw_building, "could not intercept graph build after BUILDING publication")
    _kill_process_tree(process)
    process.communicate(timeout=30)
    interrupted_service = RepositoryGraphService(repository, state_dir=crash_state)
    interrupted = interrupted_service.status()
    _require(
        interrupted.build_status
        in {GraphStatus.ABSENT, GraphStatus.BUILDING, GraphStatus.FAILED}
        and not interrupted.query_ready,
        "interrupted graph was presented as healthy or queryable",
    )
    recovered = interrupted_service.build(force=True)
    _require(recovered.query_ready, "interrupted graph did not recover")
    phases.append(
        {
            "phase": "restart_during_build",
            "status": "PASS",
            "interrupted": _compact(interrupted),
            "recovered": _compact(recovered),
        }
    )

    concurrent_probe = repository / "gt_concurrent_probe.py"
    _write(concurrent_probe, "def concurrent_probe():\n    return 7\n")
    _require(service.status().build_status is GraphStatus.STALE, "concurrent edit was not STALE")
    stop = threading.Event()
    concurrent_errors: list[str] = []
    observations: list[str] = []

    def reader() -> None:
        local = RepositoryGraphService(repository, state_dir=state)
        while not stop.is_set():
            try:
                observed = local.status()
                observations.append(observed.build_status.value)
                if observed.query_ready:
                    local.query("definition", "commit_probe_b")
            except GraphNotReadyError:
                observations.append("EXPLICIT_NOT_READY")
            except Exception as exc:  # noqa: BLE001 - campaign must receipt unexpected races
                concurrent_errors.append(f"{type(exc).__name__}: {' '.join(str(exc).split())}")
                stop.set()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reader) for _ in range(4)]
        final = service.update()
        stop.set()
        for future in futures:
            future.result(timeout=30)
    _require(not concurrent_errors, f"concurrent access errors: {concurrent_errors}")
    _require(final.query_ready, "concurrent update final state is not ready")
    _require(
        _evidence(service.query("definition", "concurrent_probe"))
        == {("concurrent_probe", "gt_concurrent_probe.py")},
        "concurrent update final graph is wrong",
    )
    phases.append(
        {
            "phase": "concurrent_reads_update",
            "status": "PASS",
            "receipt": _compact(final),
            "observed_states": dict(
                sorted(
                    (state_name, observations.count(state_name))
                    for state_name in set(observations)
                )
            ),
        }
    )

    report = {
        "schema": "gt.graph_lifecycle_audit_receipt.v1",
        "source_repository": str(source_repository),
        "frozen_commit": args.commit,
        "test_repository": str(repository),
        "provider_calls": 0,
        "provider_credentials_inspected": False,
        "cold_warm_same_graph": same_graph,
        "commit_a": commit_a,
        "commit_b": commit_b,
        "concurrent_errors": concurrent_errors,
        "phases": phases,
        "status": "PASS",
        "completed": _now(),
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(_markdown(report, output), encoding="utf-8")
    print(json.dumps({"status": "PASS", "receipt": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
