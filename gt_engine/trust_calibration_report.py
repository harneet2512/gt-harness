"""Content-addressed v2 calibration reports for GroundTruth observations.

The module deliberately keeps calibration as measurement only.  A report never
changes an observation's authority or turns an abstention into a label.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SCHEMA = "gt.trust_calibration_report.v2"
CAPABILITY_CLASSES = ("resolution", "retrieval", "community")
_OUTCOME_ALIASES = {
    True: "agreed",
    False: "disagreed",
    None: "indeterminate",
    "agreed": "agreed",
    "disagreed": "disagreed",
    "indeterminate": "indeterminate",
    "correct": "agreed",
    "incorrect": "disagreed",
    "abstain": "indeterminate",
    "abstained": "indeterminate",
    "unknown": "indeterminate",
}


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    """One immutable, source-bound calibration observation."""

    observation_id: str
    capability_class: str
    mechanism: str
    source_id: str
    tool_id: str
    fixture_id: str
    oracle_outcome: str | bool | None
    probability: float | None = None
    probability_source: str | None = None

    def __post_init__(self) -> None:
        values = {
            "observation_id": self.observation_id,
            "mechanism": self.mechanism,
            "source_id": self.source_id,
            "tool_id": self.tool_id,
            "fixture_id": self.fixture_id,
        }
        for name, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}_invalid")
        capability = str(self.capability_class).strip().lower()
        if capability not in CAPABILITY_CLASSES:
            raise ValueError("capability_class_invalid")
        object.__setattr__(self, "capability_class", capability)
        outcome = _normalise_outcome(self.oracle_outcome)
        object.__setattr__(self, "oracle_outcome", outcome)
        if self.probability is not None:
            if isinstance(self.probability, bool):
                raise ValueError("probability_invalid")
            try:
                probability = float(self.probability)
            except (TypeError, ValueError):
                raise ValueError("probability_invalid") from None
            if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                raise ValueError("probability_invalid")
            if not isinstance(self.probability_source, str) or not self.probability_source.strip():
                raise ValueError("probability_source_required")
            object.__setattr__(self, "probability", probability)
            object.__setattr__(self, "probability_source", self.probability_source.strip())
        elif self.probability_source is not None:
            raise ValueError("probability_source_without_probability")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "observation_id": self.observation_id,
            "capability_class": self.capability_class,
            "mechanism": self.mechanism,
            "source_id": self.source_id,
            "tool_id": self.tool_id,
            "fixture_id": self.fixture_id,
            "oracle_outcome": self.oracle_outcome,
        }
        if self.probability is not None:
            result["probability"] = self.probability
            result["probability_source"] = self.probability_source
        else:
            result["probability"] = None
        return result

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> CalibrationObservation:
        """Construct an observation from a wire row without deriving fields."""
        return cls(**dict(row))


def _normalise_outcome(value: str | bool | None) -> str:
    if isinstance(value, str):
        value = value.strip().lower()
    try:
        result = _OUTCOME_ALIASES[value]  # type: ignore[index]
    except (KeyError, TypeError):
        raise ValueError("oracle_outcome_invalid") from None
    return result


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    """Return a Wilson score interval for a binomial correctness rate."""
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("invalid_wilson_interval_inputs")
    if total == 0:
        return (0.0, 1.0)
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(
        rate * (1.0 - rate) / total + z * z / (4.0 * total * total)
    ) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def _probabilistic(
    rows: Sequence[CalibrationObservation],
) -> list[tuple[CalibrationObservation, float, int]]:
    result = []
    for row in rows:
        if row.oracle_outcome == "indeterminate" or row.probability is None:
            continue
        result.append((row, row.probability, int(row.oracle_outcome == "agreed")))
    return result


def _metrics(rows: Sequence[CalibrationObservation]) -> dict[str, Any]:
    population = len(rows)
    agreed = sum(row.oracle_outcome == "agreed" for row in rows)
    disagreed = sum(row.oracle_outcome == "disagreed" for row in rows)
    indeterminate = population - agreed - disagreed
    labeled = agreed + disagreed
    probabilistic = _probabilistic(rows)
    support = len(probabilistic)
    wilson = wilson_interval(agreed, labeled)
    reliability: list[dict[str, Any]] = []
    for index in range(10):
        members = [
            item
            for item in probabilistic
            if index / 10 <= item[1] < (index + 1) / 10
            or (index == 9 and item[1] == 1.0)
        ]
        reliability.append(
            {
                "bin": index,
                "lower": index / 10,
                "upper": (index + 1) / 10,
                "count": len(members),
                "mean_probability": (
                    sum(item[1] for item in members) / len(members) if members else None
                ),
                "accuracy": (
                    sum(item[2] for item in members) / len(members) if members else None
                ),
            }
        )
    brier = None
    log_loss = None
    ece = None
    ecce = None
    if probabilistic:
        brier = (
            sum((probability - outcome) ** 2 for _, probability, outcome in probabilistic)
            / support
        )
        log_loss = (
            -sum(
                math.log(max(1e-15, probability if outcome else 1.0 - probability))
                for _, probability, outcome in probabilistic
            )
            / support
        )
        ece = sum(
            item["count"] / support * abs(item["mean_probability"] - item["accuracy"])
            for item in reliability
            if item["count"]
        )
        ordered = sorted(probabilistic, key=lambda item: (item[1], item[0].observation_id))
        running = 0.0
        ecce = 0.0
        for _, probability, outcome in ordered:
            running += outcome - probability
            ecce = max(ecce, abs(running))
        ecce /= support
    return {
        "population": population,
        "support": labeled,
        "labeled_support": labeled,
        "probabilistic_support": support,
        "agreed": agreed,
        "correct": agreed,
        "disagreed": disagreed,
        "incorrect": disagreed,
        "indeterminate": indeterminate,
        "coverage": labeled / population if population else 0.0,
        "abstention_cost": indeterminate,
        "abstention_rate": indeterminate / population if population else 0.0,
        "wilson_interval": {"low": wilson[0], "high": wilson[1]},
        "wilson_95": wilson,
        "brier_score": brier,
        "log_loss": log_loss,
        "reliability_bins": reliability if probabilistic else [],
        "ece_10": ece,
        "ece": ece,
        "ecce_v1": ecce,
    }


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def build_trust_calibration_report(
    observations: Sequence[CalibrationObservation | Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic v2 report from immutable observations."""
    rows = [
        row
        if isinstance(row, CalibrationObservation)
        else CalibrationObservation.from_mapping(row)
        for row in observations
    ]
    rows.sort(key=lambda row: row.observation_id)
    ids = [row.observation_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_observation_id")
    per_class: dict[str, dict[str, Any]] = {}
    per_class_mechanism: dict[str, dict[str, Any]] = {}
    for capability in CAPABILITY_CLASSES:
        members = [row for row in rows if row.capability_class == capability]
        per_class[capability] = _metrics(members)
        mechanisms = sorted({row.mechanism for row in members})
        per_class_mechanism[capability] = {
            mechanism: _metrics([row for row in members if row.mechanism == mechanism])
            for mechanism in mechanisms
        }
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "capability_classes": list(CAPABILITY_CLASSES),
        "observations": [row.as_dict() for row in rows],
        "overall": _metrics(rows),
        "per_class": per_class,
        "per_class_mechanism": per_class_mechanism,
    }
    report["report_digest_sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report


def verify_trust_calibration_report(report: Mapping[str, Any]) -> bool:
    """Verify schema, digest, and structural identities without changing state."""
    try:
        if report.get("schema") != SCHEMA:
            return False
        supplied = report.get("report_digest_sha256")
        if not isinstance(supplied, str):
            return False
        body = dict(report)
        body.pop("report_digest_sha256", None)
        if hashlib.sha256(_canonical_bytes(body)).hexdigest() != supplied:
            return False
        observations = [CalibrationObservation(**dict(row)) for row in report["observations"]]
        rebuilt = build_trust_calibration_report(observations)
        return rebuilt == dict(report)
    except (KeyError, TypeError, ValueError):
        return False


__all__ = [
    "CAPABILITY_CLASSES",
    "SCHEMA",
    "CalibrationObservation",
    "build_trust_calibration_report",
    "verify_trust_calibration_report",
    "wilson_interval",
]
