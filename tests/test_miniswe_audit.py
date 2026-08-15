from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_engine.miniswe_audit import (
    BASELINE_RESOLVED_FLOOR,
    FEATURE_IDS,
    audit_attribution,
    audit_feature_opportunities,
    load_baseline,
    select_tasks,
    validate_tb2_smoke,
)
from scripts import miniswe_gt_audit


def test_load_baseline_requires_deepseek_and_preserves_83_floor(tmp_path: Path):
    (tmp_path / "SUMMARY.md").write_text(
        "Predictions: mini-swe-agent-deepseek-v4-flash (baseline)\n"
        "Real baseline (this re-eval, confirmed) | 83 / 300\n",
        encoding="utf-8",
    )
    (tmp_path / "results_300.json").write_text(
        json.dumps({"a": "resolved", "b": "applied_but_test_failed"}),
        encoding="utf-8",
    )
    baseline = load_baseline(tmp_path)
    assert baseline.resolved_floor == BASELINE_RESOLVED_FLOOR == 83
    assert baseline.total_tasks == 300
    assert baseline.model == "deepseek-v4-flash"


def test_load_baseline_rejects_non_deepseek(tmp_path: Path):
    (tmp_path / "SUMMARY.md").write_text("Predictions: mimo baseline\n", encoding="utf-8")
    (tmp_path / "results_300.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="DeepSeek"):
        load_baseline(tmp_path)


def test_select_tasks_requires_exact_ten_and_known_ids():
    rows = {f"task-{i}": "resolved" for i in range(10)}
    assert select_tasks(rows, [f"task-{i}" for i in range(10)]) == [
        f"task-{i}" for i in range(10)
    ]
    with pytest.raises(ValueError, match="exactly 10"):
        select_tasks(rows, ["task-0"])
    with pytest.raises(ValueError, match="unknown"):
        select_tasks(rows, [f"task-{i}" for i in range(9)] + ["missing"])


def test_attribution_audit_requires_all_features_and_structural_join():
    row = {
        "feature_id": FEATURE_IDS[0],
        "eligible": True,
        "status": "confirmed",
        "trigger_iteration": 1,
        "delivery_iteration": 1,
        "provider_request_id": "req-1",
        "payload_sha256": "a" * 64,
        "action_id": "act-1",
        "receipt_id": "rcpt-1",
    }
    result = audit_attribution([row])
    assert result.ok is False
    assert any("missing feature" in issue for issue in result.issues)


def test_attribution_audit_rejects_late_delivery_and_accepts_complete_rows():
    rows = []
    for feature in FEATURE_IDS:
        rows.append(
            {
                "feature_id": feature,
                "eligible": True,
                "status": "confirmed",
                "trigger_iteration": 2,
                "delivery_iteration": 2,
                "provider_request_id": f"req-{feature}",
                "payload_sha256": "b" * 64,
                "action_id": f"act-{feature}",
                "receipt_id": f"rcpt-{feature}",
            }
        )
    assert audit_attribution(rows).ok is True
    rows[0]["delivery_iteration"] = 1
    assert audit_attribution(rows).ok is False
    assert any("before trigger" in issue for issue in audit_attribution(rows).issues)


def test_cli_validates_real_shape_without_dispatch(tmp_path: Path, capsys):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "SUMMARY.md").write_text(
        "Predictions: mini-swe-agent-deepseek-v4-flash (baseline)\n"
        "Real baseline (this re-eval, confirmed) | 83 / 300\n",
        encoding="utf-8",
    )
    results = {f"task-{i}": "resolved" for i in range(10)}
    (baseline / "results_300.json").write_text(
        json.dumps(results), encoding="utf-8"
    )
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("\n".join(results) + "\n", encoding="utf-8")
    assert miniswe_gt_audit.main(["--baseline", str(baseline), "--tasks", str(tasks)]) == 0
    assert '"resolved_floor": 83' in capsys.readouterr().out


def test_tb2_manifest_validates_ten_tasks_with_frozen_official_rewards(tmp_path: Path):
    manifest = json.loads(
        (Path(__file__).parents[1] / "config" / "tb2_deepseek_smoke10.json").read_text()
    )
    baseline = {task: "resolved" for task in manifest["tasks"]}
    result = validate_tb2_smoke(manifest, baseline)
    assert result["task_count"] == 10
    assert result["ungraded"] == []
    assert manifest["baseline"]["known_rewards"]["write-compressor"] == 1.0
    assert manifest["baseline"]["outcome_contract"]["comparison_gate_field"] == (
        "uncensored_resolved"
    )


def test_feature_opportunity_audit_is_per_task_and_requires_terminal_state():
    rows = [
        {"task_id": "t1", "feature_id": "obligations", "eligible": True,
         "terminal": "DELIVERED", "trigger_iteration": 1,
         "delivery_iteration": 1, "action_id": "a1"},
        {"task_id": "t1", "feature_id": "recovery", "eligible": True,
         "terminal": "APPLIED_QUIET", "trigger_iteration": 2,
         "delivery_iteration": None, "action_id": "a2"},
    ]
    result = audit_feature_opportunities(rows)
    assert result["ok"] is True
    assert result["by_task"]["t1"]["DELIVERED"] == 1
    rows[1]["terminal"] = ""
    assert audit_feature_opportunities(rows)["ok"] is False
