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


def test_authorized_workflows_verify_dispatch_commit_against_release_freeze() -> None:
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for workflow_name in ("tb2_miniswe_central.yml", "deepswe_miniswe_central.yml"):
        source = (root / workflow_name).read_text(encoding="utf-8")
        assert '--current-sha "$(git rev-parse HEAD)"' in source
        assert '--runtime-sha "$(git rev-parse HEAD)"' not in source
