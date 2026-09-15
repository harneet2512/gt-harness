from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gt_engine.runtime_observation import (
    SuiteVerdictLedger,
    classify_failures_vs_baseline,
)
from gt_engine.runtime_observation import (
    test_command_scope as command_scope,
)

# run 34996816912 dynaconf-1241: the agent chased test_get_item KeyError
# 'DOTENV_INT' on isolated `pytest tests/test_base.py` runs while the same test
# passed at baseline and in the full suite - a fixture-ordering artifact that
# cost the run its remaining budget. The suite-vs-scoped join did not exist.


BASELINE_PASSING = {
    "tests/test_base.py::test_get_item",
    "tests/test_base.py::test_set_explicit_merge_token",
    "tests/test_utils.py::test_meta_values",
}
BASELINE_FAILING = {"tests/test_base.py::test_env_drift"}


def _failing_output(*names: str) -> str:
    return "".join(f"FAILED {name} - boom\n" for name in names) + "1 failed\n"


def test_scope_artifact_when_name_passes_in_current_suite():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.record_suite_run(
        passing={"tests/test_base.py::test_get_item"}, failing=set()
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert verdict is not None
    assert verdict.verdicts == (
        ("tests/test_base.py::test_get_item", "scope_artifact"),
    )


def test_baseline_failing_name_is_noise_not_regression():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_env_drift"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_env_drift", "baseline_noise"),
    )


def test_regression_new_needs_suite_level_evidence():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.record_suite_run(
        passing=set(), failing={"tests/test_utils.py::test_meta_values"}
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_utils.py -q",
        output=_failing_output("tests/test_utils.py::test_meta_values"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_utils.py::test_meta_values", "regression_new"),
    )


def test_baseline_passing_without_suite_observation_is_unverified_scope():
    """The dynaconf [77] case: baseline passed it, no suite run yet - the
    honest verdict directs the agent to suite scope, never calls regression."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_get_item", "unverified_scope"),
    )


def test_untracked_name_stays_untracked():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_agent_authored"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_agent_authored", "untracked"),
    )


def test_suite_failing_name_not_in_baseline_is_suite_failing():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.record_suite_run(
        passing=set(), failing={"tests/test_new.py::test_new"}
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_new.py -q",
        output=_failing_output("tests/test_new.py::test_new"),
        ledger=ledger,
    )
    assert verdict.verdicts == (("tests/test_new.py::test_new", "suite_failing"),)


def test_baseline_failing_now_passing_in_suite_is_scope_artifact():
    """The agent fixed (or the environment healed) a baseline-red test: its
    scoped failure later is isolation noise, not 'still pre-existing'."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.record_suite_run(
        passing={"tests/test_base.py::test_env_drift"}, failing=set()
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_env_drift"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_env_drift", "scope_artifact"),
    )


def test_clean_suite_run_promotes_in_scope_scoped_failures():
    """The [78] shape: a bare `pytest` suite run with zero failures means every
    in-scope name the scoped run saw failing passed in suite - promote it so a
    later scoped failure reads scope_artifact, not unverified forever."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    first = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert first.verdicts[0][1] == "unverified_scope"
    ledger.record_suite_run(
        passing=set(), failing=set(),
        observed_names={"tests/test_base.py::test_get_item"},
        failed_count=0,
    )
    second = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert second.verdicts == (
        ("tests/test_base.py::test_get_item", "scope_artifact"),
    )


def test_clean_scoped_run_does_not_promote_other_files():
    """`pytest tests/test_utils.py` proving nothing about test_base.py names."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    ledger.record_suite_run(
        passing=set(), failing=set(),
        observed_names={"tests/test_base.py::test_get_item"},
        failed_count=0,
        covered_prefixes=("tests/test_utils.py",),
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_get_item", "unverified_scope"),
    )


def test_no_names_means_no_classification():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    assert classify_failures_vs_baseline(
        command="pytest -q", output="375 passed\n", ledger=ledger
    ) is None


def test_no_ledger_means_no_classification():
    """No baseline was captured (baseline.captured False) - never invent one."""
    assert classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=None,
    ) is None


@pytest.mark.parametrize(("command", "expected"), [
    ("pytest", "suite"),
    ("pytest -q", "suite"),
    ("python -m pytest -vv", "suite"),
    ("pytest tests/test_base.py", "scoped"),
    ("pytest tests/test_base.py::test_get_item -q", "scoped"),
    ("pytest -k merge", "scoped"),
    ("pytest --deselect tests/test_x.py", "scoped"),
    ("pytest -q | tee out.log", "unknown"),
    ("cargo test", "unknown"),
    ("git status", "unknown"),
])
def test_command_scope_shape(command, expected):
    assert command_scope(command) == expected


def test_classification_serializes_with_layout_schema():
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output(
            "tests/test_base.py::test_get_item",
            "tests/test_base.py::test_env_drift",
        ),
        ledger=ledger,
    )
    payload = verdict.as_dict()
    assert payload["layout_schema"] == "gt.baseline_classification.v1"
    assert payload["scope"] == "scoped"
    assert payload["summary"] == {"unverified_scope": 1, "baseline_noise": 1}
    assert {v for _n, v in verdict.verdicts} == {
        "unverified_scope", "baseline_noise",
    }


# --- wiring: record_execution_evidence through MiniSweAdapter -------------


def _adapter_with_baseline(tmp_path, *, captured=True):
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan.baseline import BaselineResult

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    if captured:
        adapter.plan_inputs = SimpleNamespace(
            baseline=BaselineResult(
                status="captured",
                passing_names=tuple(sorted(BASELINE_PASSING)),
                failing_names=tuple(sorted(BASELINE_FAILING)),
            )
        )
    else:
        adapter.plan_inputs = SimpleNamespace(
            baseline=BaselineResult(status="unavailable")
        )
    return adapter


def _scoped_failure_evidence():
    from gt_engine.runtime_observation import compile_execution_evidence

    return compile_execution_evidence(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        returncode=1, action_id=1, repository_revision="source",
    )


def test_evidence_row_and_line_carry_classification(tmp_path):
    adapter = _adapter_with_baseline(tmp_path)
    line = adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    classification = row["baseline_classification"]
    assert classification["layout_schema"] == "gt.baseline_classification.v1"
    assert classification["verdicts"] == [
        ["tests/test_base.py::test_get_item", "unverified_scope"]
    ]
    assert "baseline: 1 unverified-scope" in line
    assert "tests/test_base.py::test_get_item" in line


def test_evidence_without_baseline_carries_no_classification(tmp_path):
    adapter = _adapter_with_baseline(tmp_path, captured=False)
    line = adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert "baseline_classification" not in row
    assert "baseline:" not in line


def test_clean_suite_run_then_scoped_failure_is_artifact(tmp_path):
    """The full dynaconf shape end-to-end through the adapter: scoped failure
    reads unverified, a green suite run promotes it, the next scoped failure
    reads scope_artifact - the 'stop chasing this' signal."""
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    suite = compile_execution_evidence(
        command="pytest -q",
        output="375 passed, 2 skipped, 1 xfailed, 29 warnings in 42.0s\n",
        returncode=0, action_id=2, repository_revision="source",
    )
    adapter.record_execution_evidence(suite, command="pytest -q")
    line = adapter.record_execution_evidence(
        compile_execution_evidence(
            command="pytest tests/test_base.py -q",
            output=_failing_output("tests/test_base.py::test_get_item"),
            returncode=1, action_id=3, repository_revision="source",
        ),
        command="pytest tests/test_base.py -q",
    )
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert row["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "scope_artifact"]
    ]
    assert "scope-artifact" in line


def test_collection_error_suite_does_not_promote(tmp_path):
    """A suite run that errored at collection proves nothing passed - the
    '0 failed' must not promote observed names into false artifacts."""
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    broken = compile_execution_evidence(
        command="pytest -q",
        output="ERROR tests/test_base.py - ImportError: no module\n1 error\n",
        returncode=1, action_id=2, repository_revision="source",
    )
    adapter.record_execution_evidence(broken, command="pytest -q")
    adapter.record_execution_evidence(
        compile_execution_evidence(
            command="pytest tests/test_base.py -q",
            output=_failing_output("tests/test_base.py::test_get_item"),
            returncode=1, action_id=3, repository_revision="source",
        ),
        command="pytest tests/test_base.py -q",
    )
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert row["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "unverified_scope"]
    ]


def test_suite_regression_reads_regression_new(tmp_path):
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    suite = compile_execution_evidence(
        command="pytest -q",
        output=_failing_output("tests/test_utils.py::test_meta_values")
        + "1 failed, 374 passed\n",
        returncode=1, action_id=2, repository_revision="source",
    )
    line = adapter.record_execution_evidence(suite, command="pytest -q")
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert row["baseline_classification"]["verdicts"] == [
        ["tests/test_utils.py::test_meta_values", "regression_new"]
    ]
    assert "regression" in line


def test_green_suite_advisory_without_plan(tmp_path):
    """No bound plan: the suite fact surfaces but 'verified' is not claimed."""
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    suite = compile_execution_evidence(
        command="pytest -q",
        output="375 passed, 2 skipped in 42.0s\n",
        returncode=0, action_id=1, repository_revision="source",
    )
    line = adapter.record_execution_evidence(suite, command="pytest -q")
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert "suite green vs baseline" in line
    assert "submit window is open" not in line
    assert row["submit_window"]["layout_schema"] == "gt.submit_window.v1"


def test_green_suite_advisory_reports_unmet_plan_rows(tmp_path):
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    adapter.persistent_plan = SimpleNamespace(
        rows=[SimpleNamespace(
            row_id="r1", text="merge tokens compose",
            verification_command="pytest tests/test_base.py",
        )]
    )
    suite = compile_execution_evidence(
        command="pytest -q",
        output="375 passed in 42.0s\n",
        returncode=0, action_id=1, repository_revision="source",
    )
    line = adapter.record_execution_evidence(suite, command="pytest -q")
    assert "suite green vs baseline" in line
    assert "1 plan requirement(s) still unverified" in line


def test_green_suite_advisory_opens_window_when_plan_verified(tmp_path):
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    adapter.persistent_plan = SimpleNamespace(
        rows=[SimpleNamespace(
            row_id="r1", text="merge tokens compose",
            verification_command="pytest tests/test_base.py",
        )]
    )
    adapter.plan_row_state = lambda row_id: "CHECK_PASSED"
    suite = compile_execution_evidence(
        command="pytest -q",
        output="375 passed in 42.0s\n",
        returncode=0, action_id=1, repository_revision="source",
    )
    line = adapter.record_execution_evidence(suite, command="pytest -q")
    assert "submit window is open" in line


def test_no_advisory_when_regression_present(tmp_path):
    """A suite run that introduced a regression never opens the window."""
    from gt_engine.runtime_observation import compile_execution_evidence

    adapter = _adapter_with_baseline(tmp_path)
    suite = compile_execution_evidence(
        command="pytest -q",
        output=_failing_output("tests/test_utils.py::test_meta_values")
        + "1 failed, 374 passed\n",
        returncode=1, action_id=1, repository_revision="source",
    )
    line = adapter.record_execution_evidence(suite, command="pytest -q")
    assert "submit window" not in line
    assert "regression" in line


def test_model_line_rebuilds_from_stored_dict():
    """Auditors rebuild the line from the journal payload's dict form."""
    from gt_engine.runtime_observation import execution_evidence_model_line

    payload = {
        "layout_schema": "gt.baseline_classification.v1",
        "scope": "scoped",
        "verdicts": [["tests/test_base.py::test_get_item", "unverified_scope"]],
        "summary": {"unverified_scope": 1},
    }
    line = execution_evidence_model_line(
        command="pytest tests/test_base.py -q", kind="test", outcome="fail",
        returncode=1, baseline_classification=payload,
    )
    assert "baseline: 1 unverified-scope" in line
    assert "tests/test_base.py::test_get_item" in line
