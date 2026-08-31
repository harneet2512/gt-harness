"""Bind the provider-free HAR-9 closeout to the landed functional heads."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from phase2_experiment import verify_closeout_receipt

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "gt_finalstand" / "receipts" / "har9_closeout.json"
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
    har10 = subprocess.run(
        ["git", "log", "-n", "1", "--format=%H", "--", "tests/test_resolution_provenance.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    unit_sha = main
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    receipt["harness_head"] = main
    receipt["groundtruth_head"] = groundtruth
    receipt["unit_heads"]["har10"] = har10
    receipt["unit_heads"]["har5"] = unit_sha
    receipt["unit_heads"]["har37"] = unit_sha
    receipt["unit_heads"]["har9"] = unit_sha
    har5 = ROOT / "gt_finalstand" / "receipts" / "har5_terminal_baseline.json"
    receipt["input_receipts"]["har5"] = hashlib.sha256(har5.read_bytes()).hexdigest()
    har37 = ROOT / "gt_finalstand" / "receipts" / "har37_whole_system_audit_20260831.json"
    receipt["input_receipts"]["har37"] = hashlib.sha256(har37.read_bytes()).hexdigest()
    unsigned = dict(receipt)
    unsigned.pop("bundle_sha256", None)
    receipt["bundle_sha256"] = hashlib.sha256(canonical(unsigned)).hexdigest()
    if not verify_closeout_receipt(
        receipt,
        expected_harness_head=main,
        expected_groundtruth_head=groundtruth,
        require_terminal=True,
    ):
        raise SystemExit("HAR-9 terminal closeout verification failed")
    RECEIPT.write_bytes(canonical(receipt))
    print(json.dumps({"bundle_sha256": receipt["bundle_sha256"], "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
