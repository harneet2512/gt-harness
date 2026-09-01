from __future__ import annotations

import copy
import hashlib
import json

from gt_engine.har70_framework_validation import verify_har70_receipt


def _receipt() -> dict:
    rows = [
        {"language": language, "certified_pairs_before": 0, "certified_pairs_after": 1,
         "red_witness": f"fixture/{language}", "observed_fact_mechanisms": ["overlay"]}
        for language in ("Python", "TypeScript", "JavaScript", "Go", "Java")
    ]
    receipt = {
        "schema": "gt.har70.framework_resolution.v1", "status": "PASS",
        "producer": {"commit": "a" * 40, "tree": "b" * 40},
        "manifest_languages": ["Python", "TypeScript", "JavaScript", "Go", "Java"],
        "languages": rows,
        "validation": {"exit_code": 0, "digest_sha256": "c" * 64},
        "provider_calls": 0, "benchmark_runs": 0, "benchmark_ready": False,
    }
    body = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    receipt["receipt_sha256"] = hashlib.sha256(body).hexdigest()
    return receipt


def test_har70_receipt_verifies_and_tamper_rejects() -> None:
    receipt = _receipt()
    assert verify_har70_receipt(receipt) == (True, "ok")
    tampered = copy.deepcopy(receipt)
    tampered["languages"][0]["certified_pairs_after"] = 0
    assert verify_har70_receipt(tampered)[0] is False
