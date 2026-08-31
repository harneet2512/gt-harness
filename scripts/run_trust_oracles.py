"""Score frozen resolver labels without turning calibration into authority."""

from __future__ import annotations

import json
import math
import os
import tempfile
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.build_trust_calibration_manifest import load_manifest, verify_manifest
except ModuleNotFoundError:  # Direct ``python scripts/run_trust_oracles.py`` execution.
    from build_trust_calibration_manifest import load_manifest, verify_manifest

RESULT_SCHEMA = "gt.trust_calibration_oracle_results.v1"


def wilson_interval(*, errors: int, labeled: int, z: float = 1.96) -> tuple[float, float]:
    if errors < 0 or labeled < 0 or errors > labeled:
        raise ValueError("invalid Wilson counts")
    if labeled == 0:
        return (0.0, 1.0)
    rate = errors / labeled
    denominator = 1.0 + z * z / labeled
    center = (rate + z * z / (2 * labeled)) / denominator
    margin = z * math.sqrt(
        rate * (1 - rate) / labeled + z * z / (4 * labeled * labeled)
    ) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def score_calibration(
    rows: Sequence[Mapping[str, Any]], *, manifest: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if manifest is not None:
        verify_manifest(manifest)
        allowed = {str(item["id"]) for item in manifest["cases"]}
        if {str(row.get("id") or "") for row in rows} - allowed:
            raise ValueError("row_not_in_manifest")
    population = len(rows)
    labeled_rows = [row for row in rows if row.get("label") is not None]
    errors = sum(1 for row in labeled_rows if row.get("prediction") != row.get("label"))
    labeled = len(labeled_rows)
    probabilistic = [
        row
        for row in labeled_rows
        if isinstance(row.get("confidence"), (int, float))
        and 0.0 <= float(row["confidence"]) <= 1.0
    ]
    brier = None
    log_loss = None
    if probabilistic:
        probabilities = [float(row["confidence"]) for row in probabilistic]
        outcomes = [row.get("prediction") == row.get("label") for row in probabilistic]
        brier = sum(
            (p - float(ok)) ** 2 for p, ok in zip(probabilities, outcomes, strict=True)
        ) / len(outcomes)
        log_loss = -sum(
            math.log(max(1e-15, p if ok else 1.0 - p))
            for p, ok in zip(probabilities, outcomes, strict=True)
        ) / len(outcomes)
    summary = {
        "population": population,
        "labeled": labeled,
        "indeterminate": population - labeled,
        "errors": errors,
        "error_rate": errors / labeled if labeled else None,
        "coverage": labeled / population if population else 0.0,
        "wilson_95": wilson_interval(errors=errors, labeled=labeled),
        "brier_score": brier,
        "log_loss": log_loss,
        "reliability": _reliability(probabilistic),
        "ece": _ece(probabilistic),
        "abstention_cost": population - labeled,
    }
    summary["per_resolver"] = _per_resolver(rows)
    return summary


def _score_core(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate metrics without recursively generating grouped reports."""
    population = len(rows)
    labeled_rows = [row for row in rows if row.get("label") is not None]
    errors = sum(1 for row in labeled_rows if row.get("prediction") != row.get("label"))
    labeled = len(labeled_rows)
    probabilistic = [
        row
        for row in labeled_rows
        if isinstance(row.get("confidence"), (int, float))
        and 0.0 <= float(row["confidence"]) <= 1.0
    ]
    brier = None
    log_loss = None
    if probabilistic:
        probabilities = [float(row["confidence"]) for row in probabilistic]
        outcomes = [row.get("prediction") == row.get("label") for row in probabilistic]
        brier = sum(
            (p - float(ok)) ** 2
            for p, ok in zip(probabilities, outcomes, strict=True)
        ) / len(outcomes)
        log_loss = -sum(
            math.log(max(1e-15, p if ok else 1.0 - p))
            for p, ok in zip(probabilities, outcomes, strict=True)
        ) / len(outcomes)
    return {
        "population": population,
        "labeled": labeled,
        "indeterminate": population - labeled,
        "errors": errors,
        "error_rate": errors / labeled if labeled else None,
        "coverage": labeled / population if population else 0.0,
        "wilson_95": wilson_interval(errors=errors, labeled=labeled),
        "brier_score": brier,
        "log_loss": log_loss,
        "reliability": _reliability(probabilistic),
        "ece": _ece(probabilistic),
        "abstention_cost": population - labeled,
    }


def _per_resolver(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        resolver = str(row.get("resolver") or "unknown")
        groups.setdefault(resolver, []).append(row)
    return {name: _score_core(group) for name, group in sorted(groups.items())}


def _partition_rows(
    rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> dict[str, list[Mapping[str, Any]]]:
    holdout = {str(value) for value in manifest["holdout_ids"]}
    partitions = {"calibration": [], "holdout": []}
    for row in rows:
        case_id = str(row["id"])
        partitions["holdout" if case_id in holdout else "calibration"].append(row)
    return partitions


def _validate_frozen_rows(rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> None:
    expected = {str(item["id"]) for item in manifest["cases"]}
    actual = [str(row.get("id") or "") for row in rows]
    if not all(actual) or len(actual) != len(set(actual)):
        raise ValueError("oracle_case_identity_invalid")
    if set(actual) != expected:
        raise ValueError("oracle_case_set_mismatch")


def run_oracles(
    rows: Sequence[Mapping[str, Any]], *, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """Run the frozen complete case set and bind results to its manifest."""
    verify_manifest(manifest)
    _validate_frozen_rows(rows, manifest)
    ordered = sorted((dict(row) for row in rows), key=lambda row: str(row["id"]))
    partitions = _partition_rows(ordered, manifest)
    return {
        "schema": RESULT_SCHEMA,
        "source_revision": str(manifest["source_revision"]),
        "manifest_digest": str(manifest["manifest_digest"]),
        "rows": ordered,
        "summary": score_calibration(ordered, manifest=manifest),
        "partitions": {name: _score_core(group) for name, group in partitions.items()},
    }


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    if str(receipt.get("schema")) not in {
        RESULT_SCHEMA,
        "gt.trust_calibration_summary.v1",
    }:
        raise ValueError("oracle_schema_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical_json(receipt).decode("utf-8"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_receipt(path: Path, *, manifest: Mapping[str, Any]) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("oracle_record_count_invalid")
    try:
        receipt = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("oracle_json_invalid") from exc
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RESULT_SCHEMA:
        raise ValueError("oracle_schema_invalid")
    if receipt.get("manifest_digest") != manifest.get("manifest_digest"):
        raise ValueError("oracle_manifest_digest_mismatch")
    expected = run_oracles(receipt.get("rows", ()), manifest=manifest)
    if _canonical_json(expected) != _canonical_json(receipt):
        raise ValueError("oracle_receipt_mismatch")
    return dict(receipt)


def read_rows(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(decoded, Mapping):
        decoded = decoded.get("rows")
    if not isinstance(decoded, list) or not all(isinstance(item, Mapping) for item in decoded):
        raise ValueError("oracle_rows_invalid")
    return [dict(item) for item in decoded]


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--rows", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--verify", type=Path, help="verify an existing oracle receipt")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.verify:
        receipt = load_receipt(args.verify, manifest=manifest)
        print(json.dumps({"manifest_digest": receipt["manifest_digest"], "verified": True}))
        return 0
    if not args.rows or not args.output:
        parser.error("--rows and --output are required unless --verify is used")
    receipt = run_oracles(read_rows(args.rows), manifest=manifest)
    write_receipt(args.output, receipt)
    if args.summary:
        write_receipt(args.summary, receipt["summary"] | {
            "schema": "gt.trust_calibration_summary.v1",
            "source_revision": receipt["source_revision"],
            "manifest_digest": receipt["manifest_digest"],
        })
    print(json.dumps({"manifest_digest": receipt["manifest_digest"], "output": str(args.output)}))
    return 0


def _reliability(rows: Sequence[Mapping[str, Any]], bins: int = 10) -> list[dict[str, Any]]:
    result = []
    for index in range(bins):
        members = [
            row
            for row in rows
            if index / bins <= float(row["confidence"]) < (index + 1) / bins
            or (index == bins - 1 and float(row["confidence"]) == 1.0)
        ]
        if members:
            result.append(
                {
                    "bin": index,
                    "count": len(members),
                    "mean_confidence": sum(
                        float(row["confidence"]) for row in members
                    )
                    / len(members),
                    "accuracy": sum(row.get("prediction") == row.get("label") for row in members)
                    / len(members),
                }
            )
    return result


def _ece(rows: Sequence[Mapping[str, Any]], bins: int = 10) -> float | None:
    if not rows:
        return None
    reliability = _reliability(rows, bins)
    return sum(
        item["count"] / len(rows) * abs(item["mean_confidence"] - item["accuracy"])
        for item in reliability
    )


__all__ = [
    "RESULT_SCHEMA",
    "load_receipt",
    "main",
    "run_oracles",
    "score_calibration",
    "wilson_interval",
    "write_receipt",
]


if __name__ == "__main__":
    raise SystemExit(main())
