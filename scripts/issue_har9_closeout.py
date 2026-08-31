"""Bind the provider-free HAR-9 closeout to the landed functional heads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from phase2_experiment import verify_closeout_receipt

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "gt_finalstand" / "receipts" / "har9_closeout.json"
MAIN = "8e3c5b808aa64d655b2b039542907b7aa4e541b5"
GROUNDTRUTH = "f2863f8781edaeaef8787c515e36381cdbd692d5"
UNIT_SHA = "3d70a63ce052b260e506ec6025dd804fd50be4a9"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    receipt["harness_head"] = MAIN
    receipt["groundtruth_head"] = GROUNDTRUTH
    receipt["unit_heads"]["har10"] = MAIN
    receipt["unit_heads"]["har5"] = UNIT_SHA
    receipt["unit_heads"]["har37"] = UNIT_SHA
    receipt["unit_heads"]["har9"] = UNIT_SHA
    receipt["input_receipts"]["har5"] = "49ada87e6e8c2eeff3bf3562a2bfd114616a9ceb53f8b91fb51b06bb58935159"
    har37 = ROOT / "gt_finalstand" / "receipts" / "har37_whole_system_audit_20260831.json"
    receipt["input_receipts"]["har37"] = hashlib.sha256(har37.read_bytes()).hexdigest()
    unsigned = dict(receipt)
    unsigned.pop("bundle_sha256", None)
    receipt["bundle_sha256"] = hashlib.sha256(canonical(unsigned)).hexdigest()
    if not verify_closeout_receipt(
        receipt,
        expected_harness_head=MAIN,
        expected_groundtruth_head=GROUNDTRUTH,
        require_terminal=True,
    ):
        raise SystemExit("HAR-9 terminal closeout verification failed")
    RECEIPT.write_bytes(canonical(receipt))
    print(json.dumps({"bundle_sha256": receipt["bundle_sha256"], "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
