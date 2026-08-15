from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.miniswe_gt_run import (  # noqa: E402
    TERMINAL_EXIT_CODES,
    _classify_terminal,
)


def test_submitted_exception_maps_to_submitted():
    assert _classify_terminal(Exception("Submitted"), {}) == "submitted"


def test_lifecycle_exception_is_a_harness_error_not_solver_stuck():
    assert _classify_terminal(ValueError("tool action after STUCK"), {}) == "internal_error"
    assert _classify_terminal(ValueError("LifecycleError"), {}) == "internal_error"


def test_limits_exceeded_maps_to_budget_exhausted():
    assert _classify_terminal(RuntimeError("LimitsExceeded"), {}) == "budget_exhausted"


def test_provider_errors_map_to_provider_failed():
    assert _classify_terminal(TimeoutError("APIConnectionError"), {}) == "provider_failed"
    assert _classify_terminal(RuntimeError("AuthenticationError"), {}) == "provider_failed"
    assert _classify_terminal(
        RuntimeError("provider model mismatch"), {}
    ) == "provider_failed"


def test_typed_model_mismatch_is_distinct():
    class ProviderModelMismatch(RuntimeError):
        pass

    assert _classify_terminal(
        ProviderModelMismatch("substituted"), {}
    ) == "provider_model_mismatch"


def test_unknown_exception_maps_to_internal_error():
    assert _classify_terminal(KeyError("boom"), {}) == "internal_error"


def test_clean_exit_message_maps_to_submitted():
    assert _classify_terminal(None, {"exit_status": "Submitted"}) == "submitted"
    assert _classify_terminal(None, {"submission": "final"}) == "submitted"


def test_exit_codes_separate_valid_solver_outcomes_from_process_failures():
    assert TERMINAL_EXIT_CODES["submitted_verified"] == 0
    assert TERMINAL_EXIT_CODES["submitted_unverified"] == 0
    # A completed but unsuccessful solver attempt must remain gradable.  Only
    # shared infrastructure/process faults make Harbor treat the agent phase as
    # failed.
    for terminal in ("stuck", "budget_exhausted", "task_failed"):
        assert TERMINAL_EXIT_CODES[terminal] == 0
    for terminal in ("timeout", "provider_failed", "internal_error", "setup_error"):
        assert TERMINAL_EXIT_CODES[terminal] > 0


def test_setup_failure_writes_report_and_manifest_and_returns_nonzero(
    monkeypatch, tmp_path
):
    import scripts.miniswe_gt_run as runner

    def explode(**kwargs):
        raise RuntimeError("injected setup failure")

    metrics = tmp_path / "report.json"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(runner, "build_agent", explode)
    monkeypatch.setattr(sys, "argv", [
        "miniswe_gt_run.py",
        "--task", "fix it",
        "--cwd", str(tmp_path),
        "--state-dir", str(tmp_path / "state"),
        "--metrics", str(metrics),
        "--manifest", str(manifest),
        "--gt-off",
    ])
    assert runner.main() == TERMINAL_EXIT_CODES["setup_error"]
    report = json.loads(metrics.read_text(encoding="utf-8"))
    assert report["terminal"] == "setup_error"
    assert "injected setup failure" in report["exception"]
    repro = json.loads(manifest.read_text(encoding="utf-8"))
    assert repro["research_valid"] is False
