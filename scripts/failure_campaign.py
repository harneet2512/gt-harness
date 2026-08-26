#!/usr/bin/env python3
"""Adversarial failure campaign for the canonical repository graph boundary."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.repository_graph_service import (  # noqa: E402
    GraphStatus,
    RepositoryGraphService,
)
from gt_harness.indexer_setup import ensure_source_indexer  # noqa: E402


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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _case(name: str, expected: str, observed: str, **evidence: Any) -> dict[str, Any]:
    return {
        "case": name,
        "status": "PASS",
        "expected": expected,
        "observed": observed,
        **evidence,
    }


def _clone(source: Path, commit: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run("git", "clone", "--quiet", "--no-hardlinks", str(source), str(destination))
    _run("git", "checkout", "--detach", "--quiet", commit, cwd=destination)
    _run("git", "config", "user.email", "gt-failure@example.invalid", cwd=destination)
    _run("git", "config", "user.name", "GT Failure Audit", cwd=destination)
    _require(_run("git", "rev-parse", "HEAD", cwd=destination) == commit, "wrong clone SHA")


def _wait_building(receipt_path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and process.poll() is None:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            if payload.get("build_status") == "BUILDING":
                return
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.005)
    raise RuntimeError("could not intercept update after BUILDING receipt")


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
        "# GroundTruth Failure Campaign",
        "",
        f"Observed: `{report['completed']}`",
        "",
        f"Platform campaign: **{report['status']}**",
        "",
        f"Machine receipt: `{receipt_path}`",
        "",
        "| Attack | Expected behavior | Observed | Result |",
        "| --- | --- | --- | --- |",
    ]
    for row in report["cases"]:
        lines.append(
            f"| {row['case']} | {row['expected']} | {row['observed']} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## Explicitly non-applicable dependencies",
            "",
            "The canonical structural graph does not invoke a language server, embedding model, "
            "ONNX runtime, or provider. Missing/broken LSP and missing/corrupt model cases are "
            "therefore `NOT_APPLICABLE` to graph readiness, rather than hidden dependencies.",
            "",
        ]
    )
    if report["platform_gaps"]:
        lines.extend(
            [
                "## Remaining Linux-only attacks",
                "",
                "Unreadable-source permissions, state-directory permission denial, and "
                "symlink-loop behavior cannot be credibly certified on this Windows checkout. "
                "They remain mandatory for the final Codespaces/Linux proof. This report does "
                "not convert them into PASS.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Linux-specific attacks",
                "",
                "Unreadable-source permissions, state-directory permission denial, source "
                "symlinks, and symlink loops were executed and passed on Linux.",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--large-repository", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    if run_dir.exists():
        raise SystemExit(f"refusing to reuse failure run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    repository = run_dir / "repository"
    _clone(Path(args.source_repository).resolve(), args.commit, repository)
    cases: list[dict[str, Any]] = []

    setup = ensure_source_indexer()
    _require(setup.status == "READY", f"certified indexer unavailable: {setup.as_dict()}")
    previous_binary = os.environ.get("GT_INDEX_BINARY")
    try:
        os.environ["GT_INDEX_BINARY"] = str(run_dir / "missing-gt-index")
        missing = RepositoryGraphService(repository, state_dir=run_dir / "missing-binary").build(
            force=True
        )
        _require(
            missing.build_status is GraphStatus.FAILED and not missing.query_ready,
            "missing indexer was not explicit failure",
        )
        cases.append(
            _case(
                "missing indexer binary",
                "FAILED and non-queryable",
                missing.build_status.value,
                degraded_reasons=list(missing.degraded_reasons),
            )
        )

        corrupt_binary = run_dir / ("corrupt-indexer.exe" if os.name == "nt" else "corrupt-indexer")
        corrupt_binary.write_bytes(b"not an executable\n")
        if os.name != "nt":
            corrupt_binary.chmod(0o700)
        os.environ["GT_INDEX_BINARY"] = str(corrupt_binary)
        corrupt_runtime = RepositoryGraphService(
            repository, state_dir=run_dir / "corrupt-binary"
        ).build(force=True)
        _require(
            corrupt_runtime.build_status is GraphStatus.FAILED and not corrupt_runtime.query_ready,
            "corrupt indexer was not explicit failure",
        )
        cases.append(
            _case(
                "corrupt indexer binary",
                "FAILED and non-queryable",
                corrupt_runtime.build_status.value,
                degraded_reasons=list(corrupt_runtime.degraded_reasons),
            )
        )
    finally:
        if previous_binary is None:
            os.environ.pop("GT_INDEX_BINARY", None)
        else:
            os.environ["GT_INDEX_BINARY"] = previous_binary

    state = run_dir / "state"
    service = RepositoryGraphService(repository, state_dir=state)
    baseline = service.build(force=True)
    _require(baseline.query_ready, "baseline graph is not ready")

    graph = Path(baseline.persistent_graph_path)
    graph.write_bytes(graph.read_bytes() + b"corruption")
    corrupt_db = RepositoryGraphService(repository, state_dir=state).status()
    _require(
        corrupt_db.build_status is GraphStatus.FAILED and not corrupt_db.query_ready,
        "corrupt graph DB was presented as healthy",
    )
    recovered_corrupt = service.build(force=True)
    _require(recovered_corrupt.query_ready, "corrupt graph DB did not recover")
    cases.append(
        _case(
            "corrupt graph database / wrong checksum",
            "FAILED, then atomic rebuild",
            f"{corrupt_db.build_status.value} -> {recovered_corrupt.build_status.value}",
        )
    )

    service.receipt_path.write_text("{not-json", encoding="utf-8")
    corrupt_receipt = RepositoryGraphService(repository, state_dir=state).status()
    _require(
        corrupt_receipt.build_status is GraphStatus.FAILED and not corrupt_receipt.query_ready,
        "corrupt receipt was presented as healthy",
    )
    recovered_receipt = service.build(force=True)
    _require(recovered_receipt.query_ready, "corrupt receipt did not recover")
    cases.append(
        _case(
            "corrupt graph receipt",
            "FAILED, then atomic rebuild",
            f"{corrupt_receipt.build_status.value} -> {recovered_receipt.build_status.value}",
        )
    )

    Path(recovered_receipt.persistent_graph_path).unlink()
    deleted_cache = RepositoryGraphService(repository, state_dir=state).status()
    _require(
        deleted_cache.build_status is GraphStatus.FAILED and not deleted_cache.query_ready,
        "deleted graph DB was presented as healthy",
    )
    recovered_deleted = service.build(force=True)
    _require(recovered_deleted.query_ready, "deleted graph DB did not recover")
    cases.append(
        _case(
            "deleted graph cache",
            "FAILED, then rebuild",
            f"{deleted_cache.build_status.value} -> {recovered_deleted.build_status.value}",
        )
    )

    connection = sqlite3.connect(recovered_deleted.persistent_graph_path, timeout=1.0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        locked = RepositoryGraphService(repository, state_dir=state).status()
    finally:
        connection.rollback()
        connection.close()
    if locked.query_ready:
        locked_query = RepositoryGraphService(repository, state_dir=state).query(
            "definition", "Signer"
        )
        _require(
            any(
                row["name"] == "Signer" and row["file_path"] == "src/itsdangerous/signer.py"
                for row in locked_query["evidence"]
            ),
            "locked graph DB returned incorrect repository evidence",
        )
        locked_observation = "consistent READY read"
    else:
        _require(
            locked.build_status is GraphStatus.FAILED,
            "locked graph DB returned neither correct output nor explicit failure",
        )
        locked_observation = "explicit FAILED"
    unlocked = RepositoryGraphService(repository, state_dir=state).status()
    _require(unlocked.query_ready, "graph did not recover after lock release")
    cases.append(
        _case(
            "exclusive graph DB lock",
            "correct immutable read or explicit failure; READY after release",
            f"{locked_observation} -> {unlocked.build_status.value}",
        )
    )

    malformed = repository / "gt_malformed_probe.py"
    malformed.write_text("def broken(:\n    return 1\n", encoding="utf-8")
    malformed_receipt = service.update()
    _require(
        malformed_receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
        and malformed_receipt.query_ready,
        "malformed source was not an explicit declared limitation",
    )
    _require(
        any("gt_malformed_probe.py" in item for item in malformed_receipt.parser_limitations)
        or any("gt_malformed_probe.py" in item for item in malformed_receipt.failed_paths),
        "malformed source path was not receipted",
    )
    cases.append(
        _case(
            "malformed source",
            "READY_WITH_DECLARED_LIMITATIONS with file evidence",
            malformed_receipt.build_status.value,
        )
    )
    malformed.unlink()
    service.update()

    huge = repository / "gt_huge_probe.py"
    huge.write_text("value = 1\n" * 60_000, encoding="utf-8")
    huge_receipt = service.update()
    _require(
        huge_receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
        and huge_receipt.skipped_reasons.get("too_large") == 1,
        "oversized source was not explicitly skipped",
    )
    cases.append(
        _case(
            "oversized source file",
            "READY_WITH_DECLARED_LIMITATIONS and too_large receipt",
            huge_receipt.build_status.value,
        )
    )
    huge.unlink()
    service.update()

    generated = repository / "gt_generated_probe.py"
    generated.write_text(
        "# Code generated by GT failure campaign. DO NOT EDIT.\n"
        "def generated_probe():\n    return 1\n",
        encoding="utf-8",
    )
    generated_receipt = service.update()
    _require(
        generated_receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
        and generated_receipt.skipped_reasons.get("generated") == 1,
        "generated source was not explicitly skipped",
    )
    cases.append(
        _case(
            "generated source",
            "READY_WITH_DECLARED_LIMITATIONS and generated receipt",
            generated_receipt.build_status.value,
        )
    )
    generated.unlink()
    service.update()

    mixed = repository / "gt_mixed_probe.js"
    mixed.write_text(
        "function mixedTarget(value) { return value + 1 }\n"
        "function mixedCaller(value) { return mixedTarget(value) }\n",
        encoding="utf-8",
    )
    mixed_receipt = service.update()
    mixed_callers = service.query("callers", "mixedTarget", file_path="gt_mixed_probe.js")
    _require(
        {(row["name"], row["file_path"]) for row in mixed_callers["evidence"]}
        == {("mixedCaller", "gt_mixed_probe.js")},
        "mixed-language graph relationship is wrong",
    )
    cases.append(
        _case(
            "mixed-language repository",
            "query-ready with exact cross-language inventory",
            mixed_receipt.build_status.value,
        )
    )

    killed_update_probe = repository / "gt_killed_update_probe.py"
    killed_update_probe.write_text(
        "def killed_update_probe():\n    return 'new-revision'\n",
        encoding="utf-8",
    )
    _require(
        service.status().build_status is GraphStatus.STALE,
        "pre-kill source mutation did not stale the current generation",
    )
    update_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gt_harness.cli",
            "graph",
            "build",
            "--root",
            str(repository),
            "--state-dir",
            str(state),
            "--force",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_isolated_process_kwargs(),
    )
    _wait_building(service.build_attempt_path, update_process)
    _kill_process_tree(update_process)
    update_process.communicate(timeout=30)
    killed_update = RepositoryGraphService(repository, state_dir=state).status()
    _require(not killed_update.query_ready, "killed update exposed a queryable old/partial graph")
    recovered_update = service.build(force=True)
    _require(recovered_update.query_ready, "killed update did not recover")
    cases.append(
        _case(
            "process killed during update",
            "non-queryable partial state; atomic recovery",
            f"{killed_update.build_status.value} -> {recovered_update.build_status.value}",
        )
    )

    timeout_service = RepositoryGraphService(
        Path(args.large_repository).resolve(), state_dir=run_dir / "timeout"
    )
    timeout_receipt = timeout_service.build(force=True, timeout=0.01)
    _require(
        timeout_receipt.build_status is GraphStatus.FAILED and not timeout_receipt.query_ready,
        "graph timeout was not explicit failure",
    )
    cases.append(
        _case(
            "graph build timeout",
            "FAILED and non-queryable",
            timeout_receipt.build_status.value,
            degraded_reasons=list(timeout_receipt.degraded_reasons),
        )
    )

    unsupported = run_dir / "unsupported-repository"
    unsupported.mkdir()
    _run("git", "init", "-q", cwd=unsupported)
    _run("git", "config", "user.email", "gt-failure@example.invalid", cwd=unsupported)
    _run("git", "config", "user.name", "GT Failure Audit", cwd=unsupported)
    (unsupported / "program.unknown-language").write_text("opaque source\n", encoding="utf-8")
    _run("git", "add", ".", cwd=unsupported)
    _run("git", "commit", "-qm", "unsupported source", cwd=unsupported)
    unsupported_receipt = RepositoryGraphService(
        unsupported, state_dir=run_dir / "unsupported-state"
    ).build(force=True)
    _require(
        not unsupported_receipt.query_ready
        and unsupported_receipt.build_status in {GraphStatus.DEGRADED, GraphStatus.FAILED},
        "unsupported-only repository was presented as query ready",
    )
    cases.append(
        _case(
            "unsupported-only repository",
            "explicit non-queryable failure/degradation",
            unsupported_receipt.build_status.value,
            degraded_reasons=list(unsupported_receipt.degraded_reasons),
        )
    )

    worktree = run_dir / "linked-worktree"
    _run(
        "git",
        "worktree",
        "add",
        "--detach",
        "--quiet",
        str(worktree),
        args.commit,
        cwd=repository,
    )
    worktree_receipt = RepositoryGraphService(
        worktree, state_dir=run_dir / "worktree-state"
    ).build(force=True)
    _require(worktree_receipt.query_ready, "linked worktree graph is not query ready")
    cases.append(
        _case(
            "Git linked worktree / detached HEAD",
            "repository-bound query-ready graph",
            worktree_receipt.build_status.value,
        )
    )

    submodule_child = run_dir / "submodule-child"
    submodule_child.mkdir()
    _run("git", "init", "-q", cwd=submodule_child)
    _run("git", "config", "user.email", "gt-failure@example.invalid", cwd=submodule_child)
    _run("git", "config", "user.name", "GT Failure Audit", cwd=submodule_child)
    (submodule_child / "child.py").write_text(
        "def child_symbol():\n    return 1\n", encoding="utf-8"
    )
    _run("git", "add", ".", cwd=submodule_child)
    _run("git", "commit", "-qm", "child", cwd=submodule_child)
    submodule_parent = run_dir / "submodule-parent"
    submodule_parent.mkdir()
    _run("git", "init", "-q", cwd=submodule_parent)
    _run("git", "config", "user.email", "gt-failure@example.invalid", cwd=submodule_parent)
    _run("git", "config", "user.name", "GT Failure Audit", cwd=submodule_parent)
    (submodule_parent / "app.py").write_text(
        "def parent_symbol():\n    return 1\n", encoding="utf-8"
    )
    _run(
        "git",
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(submodule_child),
        "deps/child",
        cwd=submodule_parent,
    )
    _run("git", "add", ".", cwd=submodule_parent)
    _run("git", "commit", "-qm", "parent with submodule", cwd=submodule_parent)
    submodule_receipt = RepositoryGraphService(
        submodule_parent, state_dir=run_dir / "submodule-state"
    ).build(force=True)
    _require(
        submodule_receipt.query_ready
        and submodule_receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
        and submodule_receipt.skipped_reasons.get("non_regular_file") == 1,
        "submodule boundary was not explicitly declared",
    )
    cases.append(
        _case(
            "Git submodule",
            "query-ready parent with explicit non_regular_file limitation",
            submodule_receipt.build_status.value,
        )
    )

    platform_gaps: list[str] = []
    if os.name != "nt":
        external_target = run_dir / "external-symlink-target.py"
        external_target.write_text("def external_symbol():\n    return 1\n", encoding="utf-8")
        source_link = repository / "gt_symlink_probe.py"
        source_link.symlink_to(external_target)
        symlink_receipt = service.update()
        _require(
            symlink_receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
            and symlink_receipt.skipped_reasons.get("non_regular_file", 0) >= 1,
            "source symlink was not explicitly excluded",
        )
        external_target.write_text("def external_symbol_v2():\n    return 2\n", encoding="utf-8")
        _require(service.status().query_ready, "excluded symlink target made graph silently stale")
        _require(
            service.query("definition", "external_symbol")["status"] == "NOT_FOUND",
            "external symlink target gained graph authority",
        )
        loop_link = repository / "gt_symlink_loop.py"
        loop_link.symlink_to(loop_link)
        loop_receipt = service.update()
        _require(
            loop_receipt.query_ready
            and loop_receipt.skipped_reasons.get("non_regular_file", 0) >= 2,
            "symlink loop was not explicitly excluded",
        )
        cases.append(
            _case(
                "source symlink and symlink loop",
                "READY_WITH_DECLARED_LIMITATIONS; no external authority",
                loop_receipt.build_status.value,
            )
        )
        source_link.unlink()
        loop_link.unlink()
        service.update()

        unreadable = repository / "gt_unreadable_probe.py"
        unreadable.write_text("def unreadable_symbol():\n    return 1\n", encoding="utf-8")
        unreadable.chmod(0)
        try:
            unreadable_receipt = service.update()
        finally:
            unreadable.chmod(0o600)
        _require(
            unreadable_receipt.build_status
            in {GraphStatus.READY_WITH_DECLARED_LIMITATIONS, GraphStatus.DEGRADED}
            and any("gt_unreadable_probe.py" in item for item in unreadable_receipt.failed_paths),
            "unreadable source was not explicitly receipted",
        )
        cases.append(
            _case(
                "unreadable source permission",
                "declared limitation/degradation with exact path",
                unreadable_receipt.build_status.value,
            )
        )
        unreadable.unlink()
        service.update()

        denied_state = run_dir / "permission-denied-state"
        denied_state.mkdir()
        denied_state.chmod(0o500)
        try:
            denied = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "gt_harness.cli",
                    "graph",
                    "build",
                    "--root",
                    str(repository),
                    "--state-dir",
                    str(denied_state),
                    "--force",
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        finally:
            denied_state.chmod(0o700)
        denied_payload = json.loads(denied.stdout)
        _require(
            denied.returncode != 0
            and denied_payload.get("status") == "FAILED"
            and denied_payload.get("query_ready") is False,
            "state permission denial was not a structured CLI failure",
        )
        cases.append(
            _case(
                "state-directory permission denial",
                "structured FAILED CLI payload",
                str(denied_payload.get("error_type") or "FAILED"),
            )
        )
    else:
        platform_gaps.extend(
            [
                "unreadable_source_permission_linux",
                "state_directory_permission_denied_linux",
                "symlink_and_symlink_loop_linux",
            ]
        )
    report = {
        "schema": "gt.failure_campaign_receipt.v1",
        "test_repository": str(repository),
        "frozen_commit": args.commit,
        "provider_calls": 0,
        "provider_credentials_inspected": False,
        "non_applicable": {
            "missing_language_server": "canonical graph does not invoke LSP",
            "broken_language_server": "canonical graph does not invoke LSP",
            "missing_model": "canonical graph does not invoke embeddings or ONNX",
            "corrupt_model": "canonical graph does not invoke embeddings or ONNX",
            "wrong_model_checksum": "canonical graph does not invoke embeddings or ONNX",
        },
        "external_receipts": {
            "huge_repository": "matrix-2b1b648e.json: Django and pnpm",
            "shallow_clone": "all ten repositories in matrix-2b1b648e.json",
            "uncommitted_changes": "graph-lifecycle-2b1b648e.json",
            "cold_build_interruption": "graph-lifecycle-2b1b648e.json",
            "concurrent_reads_update": "graph-lifecycle-2b1b648e.json",
        },
        "cases": cases,
        "platform_gaps": platform_gaps,
        "status": "PASS" if not platform_gaps else "PASS_WITH_PLATFORM_GAPS",
        "completed": _now(),
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(_markdown(report, output), encoding="utf-8")
    print(json.dumps({"status": report["status"], "receipt": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
