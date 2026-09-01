"""Persist the GH-authenticated, provider-free final no-smoke proof."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "gt_finalstand" / "receipts" / "no_smoke_gh_authed_20260831.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    main = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    groundtruth = subprocess.run(
        ["git", "-C", r"D:\Groundtruth\gt-index", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    token = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True).stdout.strip()
    validator_env = os.environ.copy()
    validator_env["GH_TOKEN"] = token.decode()
    validation_run = subprocess.run(
        [sys.executable, "scripts/validate_gt_finalstand.py", "--issue-receipt"],
        cwd=ROOT,
        env=validator_env,
        check=False,
        capture_output=True,
    )
    if validation_run.returncode != 0:
        raise SystemExit(
            f"GH-authenticated validation failed with exit {validation_run.returncode}"
        )
    validation = ROOT / "gt_finalstand" / "validation_receipt.json"
    if not validation.exists():
        raise SystemExit("GH-authenticated validation receipt is missing")
    validation_payload = json.loads(validation.read_text(encoding="utf-8"))
    if validation_payload.get("harness_commit") != main:
        raise SystemExit("validation receipt is not bound to the current checkout")
    tests_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_gt_finalstand.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if tests_run.returncode != 0:
        raise SystemExit(f"no-smoke tests failed with exit {tests_run.returncode}")
    payload = {
        "schema": "gt.no_smoke.gh_authed.v1",
        "status": "PASS",
        "repository": "harneet2512/gt-harness",
        "harness_head": main,
        "groundtruth_head": groundtruth,
        "commands": [
            {
                "command": "GH_TOKEN=<gh auth token> python scripts/validate_gt_finalstand.py",
                "exit_code": validation_run.returncode,
                "result": validation_run.stdout.decode().strip(),
                "validation_receipt_sha256": digest(validation.read_bytes()),
                "stdout_sha256": digest(validation_run.stdout),
                "stderr_sha256": digest(validation_run.stderr),
            },
            {
                "command": "python -m pytest -q tests/test_gt_finalstand.py",
                "exit_code": tests_run.returncode,
                "result": tests_run.stdout.decode().strip(),
                "stdout_sha256": digest(tests_run.stdout),
                "stderr_sha256": digest(tests_run.stderr),
            },
        ],
        "results": {"provider_calls": 0, "benchmark_runs": 0},
        "authorization": {
            "benchmark_ready": False,
            "status": "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL",
        },
    }
    payload["receipt_sha256"] = digest(canonical(payload))
    OUT.write_bytes(canonical(payload))
    print(json.dumps({"receipt": str(OUT), "receipt_sha256": payload["receipt_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
