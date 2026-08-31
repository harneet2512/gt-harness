from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_trust_calibration_manifest import build_manifest
from scripts.run_trust_oracles import (
    load_receipt,
    run_oracles,
    score_calibration,
    wilson_interval,
)


def test_manifest_is_deterministic_and_holds_out_labeled_cases():
    cases = [
        {"id": "b", "tier": "ambiguous", "label": "incorrect"},
        {"id": "a", "tier": "exact", "label": "correct"},
        {"id": "c", "tier": "dynamic", "label": None},
        {"id": "d", "tier": "exact", "label": "correct"},
        {"id": "e", "tier": "heuristic", "label": "incorrect"},
    ]
    first = build_manifest(cases, source_revision="src-1", seed=7, holdout_fraction=0.2)
    second = build_manifest(
        list(reversed(cases)), source_revision="src-1", seed=7, holdout_fraction=0.2
    )
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


def test_probability_metrics_are_separate_from_derived_tier_labels():
    summary = score_calibration(
        [
            {"id": "a", "label": "correct", "prediction": "correct", "confidence": 0.9},
            {"id": "b", "label": "incorrect", "prediction": "correct", "confidence": 0.8},
        ]
    )
    assert summary["brier_score"] == pytest.approx(((0.9 - 1) ** 2 + (0.8 - 0) ** 2) / 2)
    assert summary["log_loss"] > 0
    assert summary["ece"] >= 0
    assert summary["reliability"]
    assert summary["abstention_cost"] == 0


def test_oracle_requires_the_complete_frozen_case_set():
    manifest = build_manifest(
        [{"id": "a", "tier": "exact"}, {"id": "b", "tier": "dynamic"}],
        source_revision="src-1",
        seed=3,
    )
    with pytest.raises(ValueError, match="oracle_case_set_mismatch"):
        run_oracles([{"id": "a", "label": "correct", "prediction": "correct"}], manifest=manifest)


def test_cli_receipts_replay_byte_for_byte_and_reject_mutation(tmp_path: Path):
    root = Path(__file__).parents[1]
    cases = [
        {"id": "a", "tier": "exact"},
        {"id": "b", "tier": "ambiguous"},
        {"id": "c", "tier": "dynamic"},
    ]
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text("\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    manifest_copy = tmp_path / "manifest-copy.jsonl"
    build_command = [
        sys.executable,
        "scripts/build_trust_calibration_manifest.py",
        "--input",
        str(fixture),
        "--source-revision",
        "src-frozen-1",
        "--seed",
        "2512",
        "--output",
        str(manifest),
    ]
    subprocess.run(build_command, cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(build_command[:-1] + [str(manifest_copy)], cwd=root, check=True)
    assert manifest.read_bytes() == manifest_copy.read_bytes()

    rows = tmp_path / "rows.jsonl"
    rows.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": case["id"],
                    "resolver": "compiler",
                    "label": "correct" if case["id"] != "b" else None,
                    "prediction": "correct",
                    "confidence": 0.8,
                }
            )
            for case in cases
        )
        + "\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "oracle_results.jsonl"
    summary_path = tmp_path / "summary.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_trust_oracles.py",
            "--manifest",
            str(manifest),
            "--rows",
            str(rows),
            "--output",
            str(receipt_path),
            "--summary",
            str(summary_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = load_receipt(receipt_path, manifest=json.loads(manifest.read_text(encoding="utf-8")))
    assert receipt["manifest_digest"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    verify = subprocess.run(
        [
            sys.executable,
            "scripts/run_trust_oracles.py",
            "--manifest",
            str(manifest),
            "--verify",
            str(receipt_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(verify.stdout)["verified"] is True

    mutated = tmp_path / "mutated-results.jsonl"
    mutated.write_text(
        receipt_path.read_text(encoding="utf-8").replace(
            receipt["manifest_digest"], "0" * 64, 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="oracle_manifest_digest_mismatch"):
        load_receipt(mutated, manifest=json.loads(manifest.read_text(encoding="utf-8")))
