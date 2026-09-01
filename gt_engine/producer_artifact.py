"""Verification helpers for the HAR-68 producer artifact receipt."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "gt.producer_artifact.v2"


def canonical_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    body = dict(receipt)
    body.pop("receipt_digest_sha256", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_producer_artifact(receipt: Mapping[str, Any], *, binary: str | Path | None = None) -> tuple[bool, str]:
    if receipt.get("schema") != SCHEMA or receipt.get("status") != "VERIFIED":
        return False, "receipt_schema_or_status"
    supplied = receipt.get("receipt_digest_sha256")
    if not isinstance(supplied, str) or hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest() != supplied:
        return False, "receipt_digest"
    required = ("source_commit", "source_tree", "source_manifest_sha256", "build_id", "binary_sha256", "graph_schema_version")
    if any(not isinstance(receipt.get(key), str) or not receipt[key] for key in required):
        return False, "identity_missing"
    path = Path(binary or str(receipt.get("binary_path") or ""))
    if not path.is_file():
        return False, "binary_missing"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != receipt.get("binary_sha256"):
        return False, "binary_digest"
    if int(receipt.get("binary_bytes") or -1) != path.stat().st_size:
        return False, "binary_size"
    caps = receipt.get("capabilities")
    if not isinstance(caps, list) or not caps or any(not isinstance(cap, str) for cap in caps):
        return False, "capabilities_missing"
    closure = receipt.get("module_closure")
    if not isinstance(closure, Mapping) or closure.get("complete") is not True:
        return False, "module_closure_incomplete"
    return True, "ok"
