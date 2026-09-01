"""Verification helpers for the HAR-70 framework-resolution receipt."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SCHEMA = "gt.har70.framework_resolution.v1"
LANGUAGES = ("Python", "TypeScript", "JavaScript", "Go", "Java")


def canonical_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_har70_receipt(receipt: Mapping[str, Any]) -> tuple[bool, str]:
    if receipt.get("schema") != SCHEMA or receipt.get("status") != "PASS":
        return False, "receipt_schema_or_status"
    supplied = receipt.get("receipt_sha256")
    if not isinstance(supplied, str) or hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest() != supplied:
        return False, "receipt_digest"
    producer = receipt.get("producer")
    if not isinstance(producer, Mapping) or not producer.get("commit") or not producer.get("tree"):
        return False, "producer_identity"
    rows = receipt.get("languages")
    if not isinstance(rows, list) or {row.get("language") for row in rows if isinstance(row, Mapping)} != set(LANGUAGES):
        return False, "language_coverage"
    for row in rows:
        if not isinstance(row, Mapping) or row.get("certified_pairs_after", 0) <= row.get("certified_pairs_before", 0):
            return False, "certified_pair_increase"
        if not row.get("red_witness") or not row.get("observed_fact_mechanisms"):
            return False, "red_witness"
    validation = receipt.get("validation")
    if not isinstance(validation, Mapping) or validation.get("exit_code") != 0 or not validation.get("digest_sha256"):
        return False, "validation_run"
    if receipt.get("provider_calls") != 0 or receipt.get("benchmark_runs") != 0:
        return False, "forbidden_external_run"
    return True, "ok"
