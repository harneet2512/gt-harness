from __future__ import annotations

import json

import pytest

from scripts.build_trust_calibration_manifest import build_manifest
from scripts.run_trust_oracles import score_calibration, wilson_interval


def test_manifest_is_deterministic_and_holds_out_labeled_cases():
    cases = [
        {"id": "b", "tier": "ambiguous", "label": "incorrect"},
        {"id": "a", "tier": "exact", "label": "correct"},
        {"id": "c", "tier": "dynamic", "label": None},
        {"id": "d", "tier": "exact", "label": "correct"},
        {"id": "e", "tier": "heuristic", "label": "incorrect"},
    ]
    first = build_manifest(cases, source_revision="src-1", seed=7, holdout_fraction=0.2)
    second = build_manifest(list(reversed(cases)), source_revision="src-1", seed=7, holdout_fraction=0.2)
    assert first == second
    assert first["schema"] == "gt.trust_calibration_manifest.v1"
    assert first["manifest_digest"]
    assert len(first["holdout_ids"]) >= 1
    assert not set(first["holdout_ids"]) & set(first["calibration_ids"])
    json.dumps(first, sort_keys=True)


def test_wilson_and_calibration_keep_indeterminate_in_denominator():
    assert wilson_interval(errors=0, labeled=0) == (0.0, 1.0)
    rows = [
        {"id": "a", "label": "correct", "prediction": "correct"},
        {"id": "b", "label": "incorrect", "prediction": "correct"},
        {"id": "c", "label": None, "prediction": "unknown"},
    ]
    summary = score_calibration(rows)
    assert summary["population"] == 3
    assert summary["labeled"] == 2
    assert summary["errors"] == 1
    assert summary["indeterminate"] == 1
    assert summary["error_rate"] == pytest.approx(0.5)
    assert summary["coverage"] == pytest.approx(2 / 3)
    assert summary["wilson_95"][0] < 0.5 < summary["wilson_95"][1]


def test_oracle_rejects_mutated_manifest_identity():
    manifest = build_manifest(
        [{"id": "a", "tier": "exact", "label": "correct"}],
        source_revision="src-1",
        seed=3,
        holdout_fraction=0.2,
    )
    with pytest.raises(ValueError, match="manifest_digest"):
        score_calibration(
            [{"id": "a", "label": "correct", "prediction": "correct"}],
            manifest={**manifest, "source_revision": "src-2"},
        )
