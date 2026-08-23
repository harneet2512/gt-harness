from pathlib import Path

from scripts.release_workflow_guard import audit_release_workflow


def _manifest() -> dict:
    return {
        "schema": "gt.release_manifest.v2",
        "benchmark_authorized": True,
        "runtime_commit": "a" * 40,
        "authorized_workflows": ["tb2_miniswe_central.yml"],
    }


def test_release_workflow_requires_exact_authorized_commit() -> None:
    assert audit_release_workflow(
        _manifest(), workflow="tb2_miniswe_central.yml", runtime_sha="a" * 40
    ) == []
    assert "runtime_sha_not_active_release" in audit_release_workflow(
        _manifest(), workflow="tb2_miniswe_central.yml", runtime_sha="b" * 40
    )


def test_release_workflow_fails_closed_when_promotion_is_pending() -> None:
    manifest = _manifest()
    manifest["benchmark_authorized"] = False
    assert "benchmark_not_authorized" in audit_release_workflow(
        manifest, workflow="tb2_miniswe_central.yml", runtime_sha="a" * 40
    )


def test_historical_workflow_is_not_silently_authorized() -> None:
    assert "workflow_not_authorized" in audit_release_workflow(
        _manifest(), workflow="swebench_live_lite_full.yml", runtime_sha="a" * 40
    )


def test_release_only_descendant_is_authorized_when_every_changed_path_is_allowed() -> None:
    manifest = _manifest()
    manifest["allowed_post_runtime_paths"] = [
        "eval/release/active_release.json",
        "docs/benchmarks/frozen.json",
    ]
    assert audit_release_workflow(
        manifest,
        workflow="tb2_miniswe_central.yml",
        runtime_sha="b" * 40,
        runtime_is_descendant=True,
        changed_paths=["eval/release/active_release.json"],
    ) == []


def test_release_only_descendant_rejects_code_or_workflow_drift() -> None:
    manifest = _manifest()
    manifest["allowed_post_runtime_paths"] = ["eval/release/active_release.json"]
    failures = audit_release_workflow(
        manifest,
        workflow="tb2_miniswe_central.yml",
        runtime_sha="b" * 40,
        runtime_is_descendant=True,
        changed_paths=[
            "eval/release/active_release.json",
            "gt_harness/treatments.py",
        ],
    )
    assert "release_contains_unapproved_runtime_changes" in failures


def test_release_only_descendant_rejects_unrelated_history() -> None:
    manifest = _manifest()
    manifest["allowed_post_runtime_paths"] = ["eval/release/active_release.json"]
    failures = audit_release_workflow(
        manifest,
        workflow="tb2_miniswe_central.yml",
        runtime_sha="b" * 40,
        runtime_is_descendant=False,
        changed_paths=["eval/release/active_release.json"],
    )
    assert "runtime_sha_not_descendant_of_active_implementation" in failures


def test_frozen_benchmark_contract_requires_exact_inputs_and_task_order() -> None:
    manifest = _manifest()
    manifest["benchmark_contract"] = {
        "workflow": "tb2_gt.yml",
        "model": "stealth/ox-alpha",
        "base_url": "https://openrouter.ai/api/v1",
        "provider_secret": "OPENROUTER_NEW",
        "task_ids": ["task-a", "task-b"],
        "temperature": "1",
        "timeout_multiplier": "1.0",
        "concurrency": "10",
    }
    manifest["authorized_workflows"] = ["tb2_gt.yml"]
    exact = {
        "model": "stealth/ox-alpha",
        "base_url": "https://openrouter.ai/api/v1",
        "provider_secret": "OPENROUTER_NEW",
        "task_ids": ["task-a", "task-b"],
        "temperature": "1",
        "timeout_multiplier": "1.0",
        "concurrency": "10",
    }
    assert audit_release_workflow(
        manifest,
        workflow="tb2_gt.yml",
        runtime_sha="a" * 40,
        benchmark_inputs=exact,
    ) == []

    reversed_tasks = {**exact, "task_ids": ["task-b", "task-a"]}
    failures = audit_release_workflow(
        manifest,
        workflow="tb2_gt.yml",
        runtime_sha="a" * 40,
        benchmark_inputs=reversed_tasks,
    )
    assert "benchmark_contract_task_ids_mismatch" in failures


def test_frozen_benchmark_contract_fails_closed_when_inputs_are_missing() -> None:
    manifest = _manifest()
    manifest["benchmark_contract"] = {
        "workflow": "tb2_gt.yml",
        "model": "stealth/ox-alpha",
        "task_ids": ["task-a"],
    }
    manifest["authorized_workflows"] = ["tb2_gt.yml"]
    failures = audit_release_workflow(
        manifest,
        workflow="tb2_gt.yml",
        runtime_sha="a" * 40,
    )
    assert "benchmark_contract_inputs_missing" in failures


def test_canonical_gt_workflow_uses_mini_swe_central_ox_alpha_release_manifest() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/tb2_miniswe_ox_alpha_diagnostic.yml"
    ).read_text(encoding="utf-8")
    assert "MiniSweCentralAgent" in workflow
    assert "eval/release/ox_alpha_smoke20.json" in workflow
    assert "expected 20 unique contract tasks" in workflow
    assert "max-parallel: 20" in workflow
    assert "execution_budget_sec" in workflow
    assert "GT_PROVIDER_ROUTE_ID: openrouter:native:openrouter.ai" in workflow
