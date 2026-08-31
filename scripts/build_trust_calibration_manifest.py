"""Build deterministic, content-addressed calibration manifests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from argparse import ArgumentParser
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA = "gt.trust_calibration_manifest.v1"


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("manifest_digest", None)
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


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
        "calibration_ids": [
            str(item["id"]) for item in normalized if item["id"] not in holdout_set
        ],
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


def read_cases(path: Path) -> list[dict[str, Any]]:
    """Read a frozen case fixture in JSON or JSONL form."""
    raw = path.read_text(encoding="utf-8")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(decoded, Mapping):
        decoded = decoded.get("cases")
    if not isinstance(decoded, list) or not all(isinstance(item, Mapping) for item in decoded):
        raise ValueError("cases_fixture_invalid")
    return [dict(item) for item in decoded]


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Atomically persist one canonical manifest record."""
    verify_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical_bytes(manifest).decode("utf-8"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_manifest(path: Path) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("manifest_record_count_invalid")
    try:
        manifest = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("manifest_json_invalid") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest_json_invalid")
    verify_manifest(manifest)
    return dict(manifest)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="frozen JSON or JSONL case fixture"
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    args = parser.parse_args(argv)
    manifest = build_manifest(
        read_cases(args.input),
        source_revision=args.source_revision,
        seed=args.seed,
        holdout_fraction=args.holdout_fraction,
    )
    write_manifest(args.output, manifest)
    print(json.dumps({"manifest_digest": manifest["manifest_digest"], "output": str(args.output)}))
    return 0


__all__ = [
    "SCHEMA",
    "build_manifest",
    "load_manifest",
    "main",
    "read_cases",
    "verify_manifest",
    "write_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
