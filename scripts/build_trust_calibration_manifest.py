"""Build deterministic, content-addressed calibration manifests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from argparse import ArgumentParser
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA = "gt.trust_calibration_manifest.v1"
STRATUM_FIELDS = (
    "language",
    "provenance",
    "candidate_count_bucket",
    "receiver_form",
    "export_state",
)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("manifest_digest", None)
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _case_digest(case: Mapping[str, Any]) -> str:
    """Bind a frozen case's identity before adding derived split metadata."""
    values = _stratum_values(case)
    source = dict(case)
    for field in (*STRATUM_FIELDS, "case_digest", "stratum_id"):
        source.pop(field, None)
    # Include normalized stratum inputs explicitly so the case digest binds the
    # sampling dimensions while remaining stable after derived fields are added.
    source["_stratum_inputs"] = values
    return hashlib.sha256(_canonical_bytes(source)).hexdigest()


def candidate_count_bucket(value: Any) -> str:
    """Return the stable bucket used by the calibration sampling strata."""
    if value is None or value == "":
        return "unknown"
    try:
        count = int(value)
    except (TypeError, ValueError):
        return str(value).strip().lower() or "unknown"
    if count < 0:
        raise ValueError("candidate_count must be non-negative")
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    if count <= 3:
        return "2-3"
    return "4+"


def _stratum_values(case: Mapping[str, Any]) -> dict[str, str]:
    values = {
        "language": str(case.get("language") or "unknown").strip().lower(),
        "provenance": str(case.get("provenance") or "unknown").strip().lower(),
        "candidate_count_bucket": candidate_count_bucket(
            case.get("candidate_count_bucket", case.get("candidate_count"))
        ),
        "receiver_form": str(case.get("receiver_form") or "unknown").strip().lower(),
        "export_state": str(case.get("export_state") or "unknown").strip().lower(),
    }
    return {key: value or "unknown" for key, value in values.items()}


def _stratum_id(values: Mapping[str, str]) -> str:
    return "|".join(f"{key}={values[key]}" for key in STRATUM_FIELDS)


def _prepare_cases(cases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    seen: set[str] = set()
    for case in cases:
        item = dict(case)
        case_id = str(item.get("id") or "")
        if not case_id or case_id in seen:
            raise ValueError("case IDs must be non-empty and unique")
        seen.add(case_id)
        values = _stratum_values(item)
        item["case_digest"] = _case_digest(item)
        item.update(values)
        item["stratum_id"] = _stratum_id(values)
        normalized.append(item)
    normalized.sort(key=lambda item: str(item["id"]))
    return normalized


def _split_cases(
    cases: list[Mapping[str, Any]], *, seed: int
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case["stratum_id"]), []).append(case)
    strata: list[dict[str, Any]] = []
    holdout_ids: list[str] = []
    calibration_ids: list[str] = []
    for stratum_id in sorted(grouped):
        members = grouped[stratum_id]
        ranked = sorted(
            members,
            key=lambda item: hashlib.sha256(f"{seed}:{item['id']}".encode()).hexdigest(),
        )
        population = len(ranked)
        holdout_count = population // 5 if population >= 5 else 0
        selected = sorted(str(item["id"]) for item in ranked[:holdout_count])
        selected_set = set(selected)
        sample = sorted(str(item["id"]) for item in ranked)
        calibration = [case_id for case_id in sample if case_id not in selected_set]
        values = {key: str(ranked[0][key]) for key in STRATUM_FIELDS}
        strata.append(
            {
                "stratum_id": stratum_id,
                **values,
                "population": population,
                "sample_ids": sample,
                "calibration_ids": calibration,
                "holdout_ids": selected,
                "holdout_count": holdout_count,
                "holdout_rule": "floor(n/5)",
                "no_holdout": population < 5,
                "no_holdout_reason": (
                    "stratum_population_below_five" if population < 5 else None
                ),
            }
        )
        holdout_ids.extend(selected)
        calibration_ids.extend(calibration)
    return strata, sorted(calibration_ids), sorted(holdout_ids)


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
    normalized = _prepare_cases(cases)
    strata, calibration_ids, holdout_ids = _split_cases(normalized, seed=seed)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_revision": source_revision,
        "seed": int(seed),
        "holdout_fraction": float(holdout_fraction),
        "holdout_rule": "floor(n/5) per stratum; no holdout when n < 5",
        "cases": normalized,
        "strata": strata,
        "calibration_ids": calibration_ids,
        "holdout_ids": holdout_ids,
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
    cases = manifest.get("cases", ())
    if not isinstance(cases, list) or any(not isinstance(case, Mapping) for case in cases):
        raise ValueError("manifest_cases_invalid")
    expected_cases = _prepare_cases(cases)
    if expected_cases != list(cases):
        raise ValueError("manifest_case_metadata_invalid")
    strata, expected_calibration, expected_holdout = _split_cases(
        expected_cases, seed=int(manifest.get("seed"))
    )
    if expected_calibration != sorted(calibration) or expected_holdout != sorted(holdout):
        raise ValueError("manifest_split_invalid")
    if manifest.get("strata") != strata:
        raise ValueError("manifest_strata_invalid")


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
    "candidate_count_bucket",
    "load_manifest",
    "main",
    "read_cases",
    "verify_manifest",
    "write_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
