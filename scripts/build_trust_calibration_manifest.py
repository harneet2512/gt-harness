"""Build deterministic, content-addressed calibration manifests."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA = "gt.trust_calibration_manifest.v1"


def _digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("manifest_digest", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_manifest(
    cases: Iterable[Mapping[str, Any]],
    *,
    source_revision: str,
    seed: int,
    holdout_fraction: float = 0.2,
) -> dict[str, Any]:
    if not source_revision:
        raise ValueError("source_revision is required")
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be between zero and one")
    normalized = []
    seen: set[str] = set()
    for case in cases:
        item = dict(case)
        case_id = str(item.get("id") or "")
        if not case_id or case_id in seen:
            raise ValueError("case IDs must be non-empty and unique")
        seen.add(case_id)
        normalized.append(item)
    normalized.sort(key=lambda item: str(item["id"]))
    count = len(normalized)
    holdout_count = max(1, math.ceil(count * holdout_fraction)) if count > 1 else 0
    ranked = sorted(
        normalized,
        key=lambda item: hashlib.sha256(f"{seed}:{item['id']}".encode()).hexdigest(),
    )
    holdout_ids = tuple(sorted(str(item["id"]) for item in ranked[:holdout_count]))
    holdout_set = set(holdout_ids)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_revision": source_revision,
        "seed": int(seed),
        "holdout_fraction": float(holdout_fraction),
        "cases": normalized,
        "calibration_ids": [str(item["id"]) for item in normalized if item["id"] not in holdout_set],
        "holdout_ids": list(holdout_ids),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def verify_manifest(manifest: Mapping[str, Any]) -> None:
    if str(manifest.get("schema")) != SCHEMA:
        raise ValueError("manifest_schema_mismatch")
    expected = str(manifest.get("manifest_digest") or "")
    if not expected or expected != _digest(manifest):
        raise ValueError("manifest_digest_mismatch")
    ids = [str(item.get("id") or "") for item in manifest.get("cases", ())]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("manifest_case_identity_invalid")
    calibration = set(str(value) for value in manifest.get("calibration_ids", ()))
    holdout = set(str(value) for value in manifest.get("holdout_ids", ()))
    if calibration & holdout or calibration | holdout != set(ids):
        raise ValueError("manifest_split_invalid")


__all__ = ["SCHEMA", "build_manifest", "verify_manifest"]
