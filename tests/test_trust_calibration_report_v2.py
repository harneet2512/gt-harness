from __future__ import annotations

import json
import math

import pytest

from gt_engine.trust_calibration_report import (
    CalibrationObservation,
    build_trust_calibration_report,
    collect_calibration_observations,
    emit_trust_calibration_receipt,
    verify_trust_calibration_report,
)


def _rows() -> list[CalibrationObservation]:
    return [
        CalibrationObservation(
            observation_id="z",
            capability_class="resolution",
            mechanism="static",
            source_id="src-z",
            tool_id="tool-1",
            fixture_id="fix-1",
            oracle_outcome="agreed",
            probability=0.9,
            probability_source="oracle-confidence-v1",
        ),
        CalibrationObservation(
            observation_id="a",
            capability_class="resolution",
            mechanism="dynamic",
            source_id="src-a",
            tool_id="tool-1",
            fixture_id="fix-1",
            oracle_outcome="disagreed",
            probability=0.2,
            probability_source="oracle-confidence-v1",
        ),
        CalibrationObservation(
            observation_id="b",
            capability_class="retrieval",
            mechanism="lexical",
            source_id="src-b",
            tool_id="tool-2",
            fixture_id="fix-2",
            oracle_outcome="indeterminate",
        ),
        CalibrationObservation(
            observation_id="c",
            capability_class="community",
            mechanism="leiden",
            source_id="src-c",
            tool_id="tool-3",
            fixture_id="fix-3",
            oracle_outcome="agreed",
        ),
    ]


def test_v2_report_is_deterministic_and_groups_by_class_and_mechanism():
    report = build_trust_calibration_report(list(reversed(_rows())))
    assert report["schema"] == "gt.trust_calibration_report.v2"
    assert [row["observation_id"] for row in report["observations"]] == ["a", "b", "c", "z"]
    assert set(report["per_class"]) == {"resolution", "retrieval", "community"}
    resolution = report["per_class"]["resolution"]
    assert resolution["population"] == 2
    assert resolution["labeled_support"] == 2
    assert resolution["probabilistic_support"] == 2
    assert resolution["coverage"] == 1.0
    assert resolution["brier_score"] == pytest.approx(((0.9 - 1) ** 2 + (0.2 - 0) ** 2) / 2)
    assert resolution["log_loss"] == pytest.approx(-(math.log(0.9) + math.log(0.8)) / 2)
    assert resolution["reliability_bins"]
    assert resolution["ece_10"] >= 0
    assert resolution["ecce_v1"] >= 0
    assert report["per_class"]["retrieval"]["probabilistic_support"] == 0
    assert report["per_class"]["retrieval"]["brier_score"] is None
    assert report["per_class"]["retrieval"]["log_loss"] is None
    assert report["per_class"]["retrieval"]["abstention_cost"] == 1
    assert report["per_class_mechanism"]["resolution"]["static"]["population"] == 1
    assert verify_trust_calibration_report(report)


def test_ecce_sorts_probability_then_observation_id_and_is_normalized_by_support():
    rows = [
        CalibrationObservation("b", "resolution", "m", "s", "t", "f", "disagreed", 0.4, "p"),
        CalibrationObservation("a", "resolution", "m", "s", "t", "f", "agreed", 0.4, "p"),
        CalibrationObservation("c", "resolution", "m", "s", "t", "f", "agreed", 0.9, "p"),
    ]
    metrics = build_trust_calibration_report(rows)["overall"]
    # Sorted (0.4,a), (0.4,b), (0.9,c): residuals .6, -.4, .1 -> max cumulative .6 / 3.
    assert metrics["ecce_v1"] == pytest.approx(0.2)


def test_missing_probability_is_not_imputed_and_unsourced_probability_rejected():
    row = _rows()[2]
    report = build_trust_calibration_report([row])
    metrics = report["overall"]
    assert metrics["probabilistic_support"] == 0
    assert metrics["brier_score"] is None
    assert metrics["log_loss"] is None
    assert metrics["ece_10"] is None
    assert metrics["ecce_v1"] is None
    with pytest.raises(ValueError, match="probability_source_required"):
        CalibrationObservation("x", "resolution", "m", "s", "t", "f", "agreed", 0.5)


def test_identity_and_closed_class_mutations_fail_and_digest_detects_tampering():
    with pytest.raises(ValueError, match="capability_class_invalid"):
        CalibrationObservation("x", "other", "m", "s", "t", "f", "agreed")
    with pytest.raises(ValueError, match="observation_id_invalid"):
        CalibrationObservation("", "resolution", "m", "s", "t", "f", "agreed")
    with pytest.raises(ValueError, match="duplicate_observation_id"):
        build_trust_calibration_report([_rows()[0], _rows()[0]])
    report = build_trust_calibration_report(_rows())
    report["observations"][0]["source_id"] = "tampered"
    assert not verify_trust_calibration_report(report)


def test_collector_binds_shipped_capability_rows_and_emits_atomic_receipt(tmp_path):
    row = {
        "observation_id": "resolution-1",
        "mechanism": "static",
        "source_id": "resolution-receipt",
        "tool_id": "gt-producer",
        "fixture_id": "fixture-1",
        "oracle_outcome": "agreed",
        "probability": 0.75,
        "probability_source": "producer-confidence",
    }
    observations = collect_calibration_observations(resolution=[row])
    assert observations[0].capability_class == "resolution"
    output = tmp_path / "calibration.json"
    report = emit_trust_calibration_receipt(observations, output)
    assert verify_trust_calibration_report(report)
    assert verify_trust_calibration_report(json.loads(output.read_text()))
