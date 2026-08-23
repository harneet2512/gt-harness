from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_harness.comparison import ComparisonError, compare_receipt_paths, compare_receipts


def _receipt(
    treatment: str,
    *,
    task: str,
    trial: str = "1",
    resolved: bool = False,
    revision: str = "source-a",
) -> dict[str, object]:
    repository = {
        "repository": "/repo",
        "commit_sha": "a" * 40,
        "branch": "main",
        "working_tree_state": "clean",
        "source_revision": revision,
        "files_discovered": 10,
        "graph_input_files": 5,
        "source_bytes": 100,
    }
    treatment_receipt: dict[str, object]
    if treatment == "groundtruth":
        treatment_receipt = {
            "schema": "gt.treatment_receipt.v1",
            "treatment": "groundtruth",
            "treatment_status": "ACTIVE",
            "provider_calls": 0,
            "graph_available": True,
            "graph_status": "READY",
            "graph_commit_sha": repository["commit_sha"],
            "source_revision": revision,
            "delivery_count": 1,
            "evidence_items_delivered": 3,
        }
    else:
        treatment_receipt = {
            "schema": "gt.treatment_receipt.v1",
            "treatment": "bare",
            "treatment_status": "NOT_APPLICABLE",
            "provider_calls": 0,
            "graph_available": False,
            "graph_status": "NOT_APPLICABLE",
            "delivery_count": 0,
            "evidence_items_delivered": 0,
        }
    return {
        "schema": "gt.run_receipt.v1",
        "run_id": f"{treatment}-{task}-{trial}",
        "task_id": task,
        "task_fingerprint": f"fingerprint-{task}",
        "trial_id": trial,
        "status": "COMPLETED",
        "resolved": resolved,
        "evaluation": {
            "schema": "gt.evaluation_binding.v1",
            "task_id": task,
            "trial_id": trial,
            "resolved": resolved,
            "run_receipt_sha256": "a" * 64,
            "evaluator_receipt_sha256": "b" * 64,
            "evaluator_row_sha256": "c" * 64,
        },
        "treatment": treatment,
        "model": "provider/model-version",
        "base_url_configured": True,
        "base_url_sha256": "endpoint-hash",
        "temperature": 0.0,
        "max_iterations": 30,
        "time_budget_seconds": 900.0,
        "agent_scaffold": "minisweagent.agents.default.DefaultAgent",
        "system_prompt_sha256": "prompt-hash",
        "tool_policy_sha256": "tools-hash",
        "repository_start": dict(repository),
        "repository_end": dict(repository),
        "iterations": 4,
        "provider_calls": 4,
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_tokens": 0,
        "duration_ms": 500.0,
        "total_cost": 0.01,
        "treatment_receipt_present": True,
        "treatment_receipt": treatment_receipt,
    }


def test_comparison_reports_paired_outcomes_statistics_and_efficiency() -> None:
    bare = [
        _receipt("bare", task="both", resolved=True),
        _receipt("bare", task="bare-only", resolved=True),
        _receipt("bare", task="gt-only", resolved=False),
        _receipt("bare", task="neither", resolved=False),
    ]
    gt = [
        _receipt("groundtruth", task="both", resolved=True),
        _receipt("groundtruth", task="bare-only", resolved=False),
        _receipt("groundtruth", task="gt-only", resolved=True),
        _receipt("groundtruth", task="neither", resolved=False),
    ]

    report = compare_receipts(bare, gt)

    assert report["status"] == "COMPLETE"
    assert report["sample_size"] == 4
    assert report["pairwise"] == {
        "both_solve": 1,
        "bare_only_solve": 1,
        "groundtruth_only_solve": 1,
        "neither_solve": 1,
        "groundtruth_regressions": 1,
    }
    assert report["absolute_delta"] == 0.0
    assert report["interpretation"] == "parity"
    assert report["provider_calls_performed_by_comparison"] == 0
    assert report["efficiency"]["provider_calls"] == {
        "bare_mean": 4.0,
        "groundtruth_mean": 4.0,
    }


def test_comparison_rejects_unmatched_or_unevaluated_runs() -> None:
    with pytest.raises(ComparisonError, match="paired task mismatch"):
        compare_receipts(
            [_receipt("bare", task="one", resolved=True)],
            [_receipt("groundtruth", task="two", resolved=True)],
        )

    row = _receipt("bare", task="one", resolved=True)
    row["resolved"] = None
    with pytest.raises(ComparisonError, match="no boolean evaluator outcome"):
        compare_receipts([row], [_receipt("groundtruth", task="one", resolved=True)])

    row = _receipt("bare", task="one", resolved=True)
    row["evaluation"] = None
    with pytest.raises(ComparisonError, match="no bound evaluator evidence"):
        compare_receipts([row], [_receipt("groundtruth", task="one", resolved=True)])


def test_comparison_invalidates_different_scaffolds_or_repository_revisions() -> None:
    bare = _receipt("bare", task="one", resolved=True)
    gt = _receipt("groundtruth", task="one", resolved=True)
    gt["system_prompt_sha256"] = "different"
    start = dict(gt["repository_start"])
    start["source_revision"] = "source-b"
    gt["repository_start"] = start

    report = compare_receipts([bare], [gt])

    assert report["status"] == "INVALID_EXPERIMENT"
    assert report["configuration_mismatches"] == [
        "one::1:system_prompt_sha256",
        "one::1:repository_start",
    ]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("treatment_status", "FAILED", "treatment_not_active"),
        ("graph_available", False, "graph_unavailable"),
        ("source_revision", "stale", "graph_source_revision_mismatch"),
        ("delivery_count", 0, "evidence_not_delivered"),
        ("evidence_items_delivered", 0, "evidence_items_not_delivered"),
    ],
)
def test_comparison_invalidates_nominal_gt_runs_without_valid_delivery(
    field: str, value: object, reason: str
) -> None:
    gt = _receipt("groundtruth", task="one", resolved=True)
    gt["treatment_receipt"][field] = value

    report = compare_receipts([_receipt("bare", task="one", resolved=True)], [gt])

    assert report["status"] == "INVALID_TREATMENT"
    assert report["treatment_delivery_failures"] == [f"one::1:{reason}"]


def test_comparison_loads_receipts_from_directories_and_ignores_unrelated_json(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline"
    treatment = tmp_path / "treatment"
    baseline.mkdir()
    treatment.mkdir()
    (baseline / "run.json").write_text(
        json.dumps(_receipt("bare", task="one", resolved=True)), encoding="utf-8"
    )
    (baseline / "unrelated.json").write_text('{"schema":"other"}', encoding="utf-8")
    (treatment / "run.json").write_text(
        json.dumps(_receipt("groundtruth", task="one", resolved=True)), encoding="utf-8"
    )

    report = compare_receipt_paths(baseline, treatment)

    assert report["status"] == "COMPLETE"
    assert report["sample_size"] == 1
