"""Score frozen resolver labels without turning calibration into authority."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.build_trust_calibration_manifest import verify_manifest


def wilson_interval(*, errors: int, labeled: int, z: float = 1.96) -> tuple[float, float]:
    if errors < 0 or labeled < 0 or errors > labeled:
        raise ValueError("invalid Wilson counts")
    if labeled == 0:
        return (0.0, 1.0)
    rate = errors / labeled
    denominator = 1.0 + z * z / labeled
    center = (rate + z * z / (2 * labeled)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / labeled + z * z / (4 * labeled * labeled)) / denominator
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
        brier = sum((p - float(ok)) ** 2 for p, ok in zip(probabilities, outcomes)) / len(outcomes)
        log_loss = -sum(
            math.log(max(1e-15, p if ok else 1.0 - p))
            for p, ok in zip(probabilities, outcomes)
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
                    "mean_confidence": sum(float(row["confidence"]) for row in members) / len(members),
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


__all__ = ["score_calibration", "wilson_interval"]
