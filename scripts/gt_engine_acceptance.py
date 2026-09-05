#!/usr/bin/env python3
"""Run deterministic, provider-free GT engine acceptance families."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUITES: dict[str, tuple[str, ...]] = {
    "baseline": (
        "tests/test_gt_baseline_preservation.py",
        "tests/test_miniswe_agent_parity.py",
        "tests/test_miniswe_repro.py",
        "tests/test_miniswe_smoke.py",
    ),
    "context": (
        "tests/test_context_packet.py",
        "tests/test_delivery_budget.py",
        "tests/test_request_history.py",
        "tests/test_admission_transactions.py",
    ),
    "state": (
        "tests/test_engine_state.py",
        "tests/test_graph_coordinator.py",
        "tests/test_graph_lease.py",
        "tests/test_runtime_observation.py",
        "tests/test_parser_inspection.py",
    ),
    "retrieval": (
        "tests/test_dense_runtime.py",
        "tests/test_contract_embeddings.py",
        "tests/test_hybrid_retrieval.py",
        "tests/test_index_embedding_refresh.py",
    ),
    "features": (
        "tests/test_feature_matrix.py",
        "tests/test_miniswe_runtime.py",
        "tests/test_miniswe_integration.py",
        "tests/test_miniswe_evidence.py",
        "tests/test_miniswe_receipt.py",
    ),
    "performance": (
        "tests/test_graph_coordinator.py",
        "tests/test_delivery_budget.py",
        "tests/test_dense_runtime.py",
        "tests/test_request_history.py",
    ),
}


def _git(args: list[str]) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout


def _source_identity() -> dict[str, object]:
    head = _git(["rev-parse", "HEAD"]).decode().strip()
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"])
    diff = _git(["diff", "--binary", "--", "gt_engine", "scripts", "tests"])
    untracked = _git(
        [
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "gt_engine",
            "scripts",
            "tests",
        ]
    ).decode().splitlines()
    untracked_digest = hashlib.sha256()
    for relative in sorted(untracked):
        untracked_digest.update(relative.encode("utf-8") + b"\0")
        untracked_digest.update((ROOT / relative).read_bytes())
    return {
        "head": head,
        "dirty": bool(status.strip()),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_source_sha256": untracked_digest.hexdigest(),
    }


def _run_suite(name: str) -> dict[str, object]:
    started = time.perf_counter()
    command = [sys.executable, "-m", "pytest", "-q", *SUITES[name]]
    result = subprocess.run(command, cwd=ROOT, text=True)
    return {
        "suite": name,
        "tests": list(SUITES[name]),
        "exit_code": result.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    names = tuple(SUITES) if args.suite == "all" else (args.suite,)
    results = [_run_suite(name) for name in names]
    receipt = {
        "schema": "gt.engine_acceptance.v1",
        "provider_calls": 0,
        "source_identity": _source_identity(),
        "requested_suite": args.suite,
        "status": "passed" if all(row["exit_code"] == 0 for row in results) else "failed",
        "results": results,
    }
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
