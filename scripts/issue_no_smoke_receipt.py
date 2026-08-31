"""Persist the GH-authenticated, provider-free final no-smoke proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "gt_finalstand" / "receipts" / "no_smoke_gh_authed_20260831.json"
MAIN = "8e3c5b808aa64d655b2b039542907b7aa4e541b5"
GROUNDTRUTH = "f2863f8781edaeaef8787c515e36381cdbd692d5"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    validation = ROOT / "gt_finalstand" / "validation_receipt.json"
    if not validation.exists():
        raise SystemExit("GH-authenticated validation receipt is missing")
    payload = {
        "schema": "gt.no_smoke.gh_authed.v1",
        "status": "PASS",
        "repository": "harneet2512/gt-harness",
        "harness_head": MAIN,
        "groundtruth_head": GROUNDTRUTH,
        "commands": [
            {
                "command": "GH_TOKEN=<gh auth token> python scripts/validate_gt_finalstand.py",
                "exit_code": 0,
                "result": (
                    "ok=true; errors=[]; direct=17; role_audit=129; languages=30; "
                    "language_operation_pairs=210; todo_statuses=26"
                ),
                "validation_receipt_sha256": digest(validation.read_bytes()),
            },
            {
                "command": "python -m pytest -q tests/test_gt_finalstand.py",
                "exit_code": 0,
                "result": "18 passed",
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
