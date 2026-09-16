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
    """D2: `ledger=None` is the ONE remaining no-op, and it now means "no plan
    inputs at all" rather than "no baseline" - a missing baseline still yields
    a ledger whose basis is the agent's own suite runs."""
    assert classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=None,
    ) is None


def test_suite_observed_ledger_classifies_without_any_baseline():
    """D2: run 34996816912 captured `status=no_test_verdicts` (collection
    interrupted, 4 errors, 0 names), so `baseline.captured` was False and the
    classifier was a no-op for the whole 3061 s run. Absent a baseline the
    agent's own full-suite runs are still evidence."""
    ledger = SuiteVerdictLedger(
        baseline_passing=(), baseline_failing=(), basis="suite_observed"
    )
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert verdict is not None
    assert verdict.basis == "suite_observed"
    payload = verdict.as_dict()
    assert payload["basis"] == "suite_observed"
    assert payload["layout_schema"] == "gt.baseline_classification.v1"


@pytest.mark.parametrize(("command", "expected"), [
    ("pytest", "suite"),
    ("pytest -q", "suite"),
    ("python -m pytest -vv", "suite"),
    ("pytest tests/test_base.py", "scoped"),
    ("pytest tests/test_base.py::test_get_item -q", "scoped"),
    ("pytest -k merge", "scoped"),
    ("pytest --deselect tests/test_x.py", "scoped"),
    # cargo is a runner family in its own right (E1): `cargo test` with no
    # selection is the whole crate's suite.
    ("cargo test", "suite"),
    ("git status", "unknown"),
    # D3: run 34996816912 issued ten test commands and the parser read
    # `unknown` for every one of them, so the classifier never ran once in
    # 3061 s. Each shape below is drawn from that run or its family.
    # Shell redirection words are not positional paths.
    ("pytest -q 2>&1 | tail -5", "suite"),
    ("pytest -q > out.log 2>&1", "suite"),
    ("pytest -q >out.log", "suite"),
    ("pytest -q >> out.log", "suite"),
    ("pytest -q | tee out.log", "suite"),
    ("pytest -q 2>&1 | sed -n 1,5p", "suite"),
    ("pytest -q | grep -c PASSED", "suite"),
    ("pytest -q | tail -20 | head -3", "suite"),
    # The value of a value-taking option is not a positional path.
    ("pytest -p no:cacheprovider -q", "suite"),
    ("pytest -o addopts= -q", "suite"),
    ("pytest -n 4 -q", "suite"),
    ("pytest --tb short -q", "suite"),
    ("pytest --tb=short -q", "suite"),
    ("pytest --maxfail 1 -q", "suite"),
    ("pytest -W ignore::DeprecationWarning", "suite"),
    ("pytest --junitxml report.xml", "suite"),
    ("pytest --basetemp /tmp/pt -q", "suite"),
    ("pytest --import-mode importlib -q", "suite"),
    ("pytest --rootdir /testbed -q", "suite"),
    ("pytest -c setup.cfg -q", "suite"),
    ("pytest --durations 10 -q", "suite"),
    ("pytest --timeout 60 -q", "suite"),
    # A leading `cd <dir> &&` prefix is not a compound command. Nine of the
    # ten commands in the incident carried exactly this prefix.
    ("cd /testbed && pytest -q", "suite"),
    ("cd /testbed && python -m pytest -q 2>&1 | tail -20", "suite"),
    ("cd /testbed && python -m pytest tests/test_base.py -q 2>&1 | tail -20",
     "scoped"),
    # Positional selection still reads scoped.
    ("pytest tests/ -q", "scoped"),
    ("pytest -q tests/test_base.py", "scoped"),
    ("pytest --ignore=tests/test_base.py -q", "scoped"),
    ("pytest -m not-slow -q", "scoped"),
    ("pytest -m slow", "scoped"),
    # `python -m pytest` is the interpreter form: that -m is not a marker.
    ("python -m pytest", "suite"),
    ("python3 -m pytest -q 2>&1 | tail -5", "suite"),
    ("/usr/local/bin/pytest -q", "suite"),
    ("py.test -q", "suite"),
    # Genuinely compound or unparseable stays unknown. A benign companion
    # (echo prints bytes; it cannot change what the run collected) does not
    # make the command compound - E1.
    ("pytest -q && echo done", "suite"),
    ("pytest -q; make lint", "unknown"),
    ("pytest -q | python analyze.py", "unknown"),
    ("make test && pytest -q", "unknown"),
    ("grep pytest out.log", "unknown"),
    ("echo pytest", "unknown"),
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


def _adapter_with_baseline(tmp_path, *, captured=True, with_repo=False,
                           extra_repo_files=()):
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan.baseline import BaselineResult

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    if with_repo:
        # Whole-suite claims need the repo's real test inventory: write the
        # files the baseline names so `pytest tests/` provably covers them.
        repo = tmp_path / "repo"
        rels = {n.split("::", 1)[0] for n in (*BASELINE_PASSING,
                                             *BASELINE_FAILING)}
        for rel in rels | set(extra_repo_files):
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def test_x(): pass\n", encoding="utf-8")
        adapter.repo_root = str(repo)
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
    assert "run the full suite before investigating" in line
    assert "tests/test_base.py::test_get_item" in line


def test_evidence_without_baseline_classifies_on_suite_observed_basis(tmp_path):
    """D2: an uncaptured baseline returned None from `_suite_ledger`, which is
    precisely why the dynaconf run received no classification at all."""
    adapter = _adapter_with_baseline(tmp_path, captured=False)
    line = adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert row["baseline_classification"]["basis"] == "suite_observed"
    assert "baseline:" in line


def test_evidence_without_plan_inputs_carries_no_classification(tmp_path):
    """No plan inputs at all is the only remaining no-op."""
    from gt_engine.miniswe_integration import MiniSweAdapter

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    assert adapter.plan_inputs is None
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
    assert "isolation artifact, not a regression" in line


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
    assert "passed before your edits and fails in the full suite" in line
    assert "this one is yours" in line


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
    assert "this one is yours" in line


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
    assert "passed at baseline" in line
    assert "run the full suite before investigating" in line
    assert "tests/test_base.py::test_get_item" in line


# --- C1/H4/H5/H3/H6/M7/M8/M9/M10 and the pristine-tree probe -------------
#
# Every case below is anchored in run 34996816912 / dynaconf-1241, replayed
# provider-free: 10 execution_evidence rows, all of the form
# `cd /testbed && python -m pytest <paths> ... 2>&1 | tail`, zero
# plan_gate_decision rows, and a baseline whose capture ended
# `no_test_verdicts`. The agent chased tests/test_base.py::test_get_item from
# +1055 s to +2859 s of a 3061 s budget.

ACTION_45 = (
    "cd /testbed && python -m pytest tests/ -q -x "
    "--ignore=tests/test_base.py --ignore=tests/test_cli.py "
    "--ignore=tests/test_vault.py --ignore=tests/test_redis.py 2>&1 | tail -5"
)
ACTION_26 = (
    "cd /testbed && git stash && python -m pytest "
    "tests/test_base.py::test_get_item -q 2>&1 | tail -10; git stash pop"
)


def _evidence(command, output, returncode=1, action_id=1):
    from gt_engine.runtime_observation import compile_execution_evidence

    return compile_execution_evidence(
        command=command, output=output, returncode=returncode,
        action_id=action_id, repository_revision="source",
    )


def _last_row(adapter):
    return json.loads(adapter.store.path.read_text().splitlines()[-1])


# --- D3 coverage extraction ---------------------------------------------


def test_coverage_paths_exclude_ignored_targets():
    """M10/addendum: covered prefixes are the positional args MINUS every
    --ignore/--deselect target. Action 45 ran `tests/` while excluding four
    files; it proves nothing about the names inside them."""
    from gt_engine.runtime_observation import test_command_coverage

    coverage = test_command_coverage(ACTION_45)
    assert coverage.scope == "scoped"
    assert coverage.paths == ("tests/",)
    assert set(coverage.excluded) == {
        "tests/test_base.py", "tests/test_cli.py",
        "tests/test_vault.py", "tests/test_redis.py",
    }
    assert coverage.narrowed is False


def test_coverage_marks_nodeid_and_keyword_runs_as_narrowed():
    """A -k/-m/::nodeid run may fold names but must never promote: it did not
    attempt the rest of its own coverage."""
    from gt_engine.runtime_observation import test_command_coverage

    assert test_command_coverage(
        "pytest tests/test_base.py::test_get_item -q").narrowed is True
    assert test_command_coverage("pytest tests/ -k merge").narrowed is True
    assert test_command_coverage("pytest tests/ -m slow").narrowed is True
    assert test_command_coverage("pytest tests/ -q").narrowed is False


# --- C1 truncated output must not promote --------------------------------


def test_truncated_verbose_tail_does_not_promote_or_open_window(tmp_path):
    """C1: `| tail -20` of a verbose run cuts the summary line off. Three
    parsed PASSED rows are not an aggregate, and the old guard
    (`failed == 0 and passed == 0 and not passing`) let them promote the
    entire baseline and open the submit window."""
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    truncated = (
        "tests/test_utils.py::test_alpha PASSED                     [ 33%]\n"
        "tests/test_utils.py::test_beta PASSED                      [ 66%]\n"
        "tests/test_utils.py::test_gamma PASSED                     [100%]\n"
    )
    line = adapter.record_execution_evidence(
        _evidence("pytest -v 2>&1 | tail -20", truncated, returncode=0,
                  action_id=2),
        command="pytest -v 2>&1 | tail -20",
    )
    assert "submit window" not in line
    assert "suite green" not in line
    after = adapter.record_execution_evidence(
        _evidence("pytest tests/test_base.py -q",
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=3),
        command="pytest tests/test_base.py -q",
    )
    assert "isolation artifact" not in after
    assert _last_row(adapter)["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "unverified_scope"]
    ]


def test_summary_marker_required_but_quiet_summary_counts(tmp_path):
    """The bare `-q` summary line IS a terminal marker; only its absence
    blocks promotion."""
    from gt_engine.runtime_observation import has_terminal_summary

    assert has_terminal_summary("375 passed, 2 skipped in 42.0s\n")
    assert has_terminal_summary("===== 375 passed in 42.0s =====\n")
    assert has_terminal_summary("Ran 12 tests in 0.4s\n\nOK\n")
    assert not has_terminal_summary(
        "tests/t.py::test_a PASSED   [100%]\n")


# --- H4 fail wins over pass for the same identity ------------------------


def test_unittest_identity_failing_and_passing_reads_fail():
    """H4: the unittest FAIL pattern captures the CLASS, so the same identity
    lands in both parsed lists. Writing failing-then-passing let the pass win
    and a real failure read green."""
    ledger = SuiteVerdictLedger(
        baseline_passing={"tests.test_base.BaseTest"}, baseline_failing=()
    )
    identity = "tests.test_base.BaseTest"
    ledger.record_suite_run(
        passing=[identity], failing=[identity],
        observed_names=[identity], failed_count=0, passed_count=11,
    )
    assert ledger.suite_verdict(identity) == "fail"


def test_conflicting_identity_blocks_promotion():
    """A run that both passed and failed one identity has not established
    suite truth for anything: skip its promotion entirely."""
    ledger = SuiteVerdictLedger(
        baseline_passing={"tests/test_base.py::test_get_item"},
        baseline_failing=(),
    )
    ledger.note_failing(["tests/test_base.py::test_get_item"])
    ledger.record_suite_run(
        passing=["tests.test_base.BaseTest"],
        failing=["tests.test_base.BaseTest"],
        observed_names=["tests.test_base.BaseTest"],
        failed_count=0, passed_count=11,
    )
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") is None


# --- H5 only test commands with a known scope are classified -------------


def test_non_test_commands_are_never_classified(tmp_path):
    """H5: `cat ci_failures.txt` and `grep FAILED out.log` echo `FAILED x::y`
    rows. Classifying them gave those names verdicts and, worse, entered them
    into the ledger's observed set for a later promotion."""
    adapter = _adapter_with_baseline(tmp_path)
    ledger = adapter._suite_ledger()
    payload = _failing_output("tests/test_base.py::test_get_item").encode()
    for command in ("cat ci_failures.txt", "grep -rn FAILED out.log",
                    "sed -n 1,5p out.log"):
        artifact = SimpleNamespace(
            kind="test", protocol="pytest", raw_output=payload,
            output_artifact_path="",
        )
        assert adapter._classify_execution_vs_baseline(
            artifact, command) == (None, False)
    assert ledger._observed == set()


def test_build_kind_with_test_rows_in_log_is_not_classified(tmp_path):
    """A build log that echoes a test summary is not a test observation."""
    adapter = _adapter_with_baseline(tmp_path)
    evidence = _evidence(
        "make lint",
        _failing_output("tests/test_base.py::test_get_item"),
        returncode=1, action_id=1,
    )
    assert evidence.kind == "build"
    line = adapter.record_execution_evidence(evidence, command="make lint")
    assert "baseline_classification" not in _last_row(adapter)
    assert "baseline:" not in line
    assert adapter._suite_ledger()._observed == set()


# --- H6 the clause is an instruction, not a histogram --------------------


def test_model_clause_renders_one_instruction_per_class():
    from gt_engine.runtime_observation import execution_evidence_model_line

    payload = {
        "layout_schema": "gt.baseline_classification.v1",
        "scope": "scoped", "basis": "baseline",
        "verdicts": [
            ["tests/test_a.py::test_one", "regression_new"],
            ["tests/test_b.py::test_two", "baseline_noise"],
        ],
        "summary": {"regression_new": 1, "baseline_noise": 1},
        "hints": {},
    }
    line = execution_evidence_model_line(
        command="pytest -q", kind="test", outcome="fail", returncode=1,
        baseline_classification=payload,
    )
    assert "tests/test_a.py::test_one passed before your edits and fails in "
    assert "this one is yours" in line
    assert "already failed at baseline - not your change" in line
    assert "unverified-scope" not in line


def test_model_clause_is_bounded_and_carries_no_internal_ids():
    from gt_engine.runtime_observation import _baseline_model_clause

    verdicts = [[f"tests/test_{i}.py::test_{i}", "regression_new"]
                for i in range(12)]
    verdicts += [[f"tests/test_s{i}.py::test_s{i}", "suite_failing"]
                 for i in range(12)]
    clause = _baseline_model_clause({
        "verdicts": verdicts, "basis": "baseline",
        "summary": {"regression_new": 12, "suite_failing": 12}, "hints": {},
    })
    assert len(clause) <= 240
    assert "sha256" not in clause and "layout_schema" not in clause


def test_model_clause_suite_observed_wording_without_baseline():
    from gt_engine.runtime_observation import _baseline_model_clause

    clause = _baseline_model_clause({
        "verdicts": [["tests/test_base.py::test_get_item", "unverified_scope"]],
        "summary": {"unverified_scope": 1},
        "basis": "suite_observed", "hints": {},
    })
    assert "not yet observed in a full-suite run" in clause
    assert "run the full suite first" in clause
    assert "passed at baseline" not in clause


def test_model_clause_covers_every_verdict_class():
    from gt_engine.runtime_observation import _baseline_model_clause

    expected = {
        "regression_new": "this one is yours",
        "unverified_scope": "run the full suite before investigating",
        "scope_artifact": "isolation artifact, not a regression",
        "baseline_noise": "already failed at baseline - not your change",
        "suite_failing": "fails in the full suite and was not in the baseline",
        "untracked": "is not in the baseline or any suite run",
    }
    for verdict, fragment in expected.items():
        clause = _baseline_model_clause({
            "verdicts": [["tests/t.py::t", verdict]],
            "summary": {verdict: 1}, "basis": "baseline", "hints": {},
        })
        assert fragment in clause, verdict
        assert clause.startswith("tests/t.py::t ")


# --- H3 the advisory may not overclaim -----------------------------------


def test_advisory_refuses_window_while_plan_rows_are_unverified(tmp_path):
    """H3: gate-one submitted with all 28 rows UNVERIFIED while the run's own
    `verified` flag read True. `unmet_plan_rows()` empty is not verified."""
    adapter = _adapter_with_baseline(tmp_path)
    adapter.persistent_plan = SimpleNamespace(
        rows=[SimpleNamespace(row_id="r1", text="merge tokens compose",
                              verification_command="pytest tests/test_base.py")]
    )
    adapter.unmet_plan_rows = lambda: ()
    adapter.plan_row_state = lambda row_id: "UNVERIFIED"
    line = adapter.record_execution_evidence(
        _evidence("pytest -q", "375 passed in 42.0s\n", returncode=0),
        command="pytest -q",
    )
    assert "submit window is open" not in line
    assert "1 plan row(s) have no current-tree evidence" in line


def test_advisory_wording_on_suite_observed_basis(tmp_path):
    """Without a baseline the claim is about the agent's OWN suite run."""
    adapter = _adapter_with_baseline(tmp_path, captured=False)
    line = adapter.record_execution_evidence(
        _evidence("pytest -q", "375 passed in 42.0s\n", returncode=0),
        command="pytest -q",
    )
    assert "no regression observed in your own full-suite run" in line
    assert "green vs baseline" not in line


def test_advisory_consults_latest_run_failing_set(tmp_path):
    """M9: after a green promotion every name reads pass, so
    `regression_count()` could never be positive again. The advisory must also
    look at the failing set of the most recent suite run."""
    adapter = _adapter_with_baseline(tmp_path)
    ledger = adapter._suite_ledger()
    ledger.record_suite_run(
        passing=(), failing={"tests/test_base.py::test_get_item"},
        observed_names={"tests/test_base.py::test_get_item"},
        failed_count=1, passed_count=374,
    )
    assert ledger.regression_count() == 1
    assert ledger.last_run_regressions() == (
        "tests/test_base.py::test_get_item",)
    assert adapter._submit_window_advisory() == ""


# --- M8/M9 promotion only promotes what the run actually covered ---------


def test_promotion_is_limited_to_names_the_run_named(tmp_path):
    """M8: a run whose summary count equals the number of names it printed
    observed nothing else - skipped, deselected and uncollected names must not
    ride along."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.note_failing(["tests/test_base.py::test_get_item"])
    ledger.record_suite_run(
        passing=["tests/test_utils.py::test_meta_values"], failing=(),
        observed_names=["tests/test_utils.py::test_meta_values"],
        failed_count=0, passed_count=1,
    )
    assert ledger.suite_verdict("tests/test_utils.py::test_meta_values") == "pass"
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") is None


def test_dot_output_promotion_requires_conservation(tmp_path):
    """M8: `-q` prints no names at all (action 45: 375 passed, 0 names), so
    promoting the observed set is the whole mechanism - but promoting the
    BASELINE set needs the conservation guard from
    persistent_plan/baseline.py:718."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.note_failing(["tests/test_base.py::test_get_item"])
    ledger.record_suite_run(
        passing=(), failing=(), observed_names=(),
        failed_count=0, passed_count=1,
    )
    # observed names promote; the three baseline names do not (1 < 3).
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") == "pass"
    assert ledger.suite_verdict(
        "tests/test_base.py::test_set_explicit_merge_token") is None
    ledger.record_suite_run(
        passing=(), failing=(), observed_names=(),
        failed_count=0, passed_count=375,
    )
    assert ledger.suite_verdict(
        "tests/test_base.py::test_set_explicit_merge_token") == "pass"


# --- M10 covered prefixes threaded from the real command -----------------


def test_action_45_promotes_under_coverage_but_not_ignored_files(tmp_path):
    """The exact action-45 command: 375 passed under `tests/` with four files
    ignored. It must promote observed names under `tests/` and must NOT mark
    tests/test_base.py::test_get_item passing - that run excluded it."""
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _evidence("cd /testbed && python -m pytest tests/test_base.py -q "
                  "2>&1 | tail -20",
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=1),
        command=("cd /testbed && python -m pytest tests/test_base.py -q "
                 "2>&1 | tail -20"),
    )
    adapter.record_execution_evidence(
        _evidence("cd /testbed && python -m pytest tests/test_utils.py -q "
                  "2>&1 | tail -20",
                  _failing_output("tests/test_utils.py::test_meta_values"),
                  action_id=2),
        command=("cd /testbed && python -m pytest tests/test_utils.py -q "
                 "2>&1 | tail -20"),
    )
    adapter.record_execution_evidence(
        _evidence(ACTION_45, "375 passed in 42.0s\n", returncode=0,
                  action_id=3),
        command=ACTION_45,
    )
    ledger = adapter._suite_ledger()
    assert ledger.suite_verdict("tests/test_utils.py::test_meta_values") == "pass"
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") is None
    line = adapter.record_execution_evidence(
        _evidence("cd /testbed && python -m pytest tests/test_base.py -q "
                  "2>&1 | tail -20",
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=4),
        command=("cd /testbed && python -m pytest tests/test_base.py -q "
                 "2>&1 | tail -20"),
    )
    assert "isolation artifact" not in line


def test_scoped_green_run_does_not_open_the_submit_window(tmp_path):
    """A scoped run promotes inside its coverage but is not a full suite."""
    adapter = _adapter_with_baseline(tmp_path)
    line = adapter.record_execution_evidence(
        _evidence(ACTION_45, "375 passed in 42.0s\n", returncode=0),
        command=ACTION_45,
    )
    assert "submit window" not in line
    assert "suite green" not in line


def test_scoped_failure_never_becomes_suite_truth(tmp_path):
    """The core invariant: an isolated failure is not a suite failure, however
    wide its coverage. This is the whole incident."""
    adapter = _adapter_with_baseline(tmp_path)
    command = "cd /testbed && python -m pytest tests/test_base.py -q 2>&1 | tail -20"
    adapter.record_execution_evidence(
        _evidence(command,
                  _failing_output("tests/test_base.py::test_get_item")
                  + "1 failed, 83 passed in 3.0s\n"),
        command=command,
    )
    assert adapter._suite_ledger().suite_verdict(
        "tests/test_base.py::test_get_item") is None
    assert _last_row(adapter)["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "unverified_scope"]
    ]


# --- M7 an edit invalidates suite verdicts -------------------------------


def test_edit_makes_a_suite_pass_stale(tmp_path):
    """M7: a green full-suite run before the latest edit says nothing about
    the tree the agent just changed."""
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _scoped_failure_evidence(), command="pytest tests/test_base.py -q"
    )
    adapter.record_execution_evidence(
        _evidence("pytest -q", "375 passed in 42.0s\n", returncode=0,
                  action_id=2),
        command="pytest -q",
    )
    ledger = adapter._suite_ledger()
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") == "pass"
    adapter.begin_implement()
    adapter.note_edit(["src/dynaconf/base.py"])
    line = adapter.record_execution_evidence(
        _evidence("pytest tests/test_base.py -q",
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=3),
        command="pytest tests/test_base.py -q",
    )
    row = _last_row(adapter)
    assert row["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "unverified_scope"]
    ]
    assert "before your latest edit" in line
    assert "re-run the full suite" in line


def test_advisory_does_not_fire_from_stale_verdicts(tmp_path):
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _evidence("pytest -q", "375 passed in 42.0s\n", returncode=0),
        command="pytest -q",
    )
    assert adapter._submit_window_advisory() != ""
    adapter.begin_implement()
    adapter.note_edit(["src/dynaconf/base.py"])
    assert adapter._submit_window_advisory() == ""


# --- the pristine-tree probe (action 26, +216 s) -------------------------


def test_git_stash_probe_is_recognised():
    from gt_engine.runtime_observation import (
        is_pristine_tree_probe,
        test_command_coverage,
    )

    assert is_pristine_tree_probe(ACTION_26) is True
    assert test_command_coverage(ACTION_26).scope == "scoped"
    assert is_pristine_tree_probe("pytest tests/test_base.py -q") is False
    assert is_pristine_tree_probe(
        "cd /testbed && git stash && pytest -q") is False


def test_pristine_probe_makes_later_failures_read_baseline_noise(tmp_path):
    """The evidence that would have ended the chase existed at +216 s: the
    agent's own `git stash` probe showed test_get_item failing on the pristine
    tree. It then chased the test for another 2643 s."""
    adapter = _adapter_with_baseline(tmp_path, captured=False)
    line = adapter.record_execution_evidence(
        _evidence(ACTION_26,
                  _failing_output("tests/test_base.py::test_get_item")),
        command=ACTION_26,
    )
    assert "already failed on the pristine tree" in line
    assert "git-stash probe" in line
    later = "cd /testbed && python -m pytest tests/test_base.py -q 2>&1 | tail -20"
    line2 = adapter.record_execution_evidence(
        _evidence(later,
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=2),
        command=later,
    )
    assert _last_row(adapter)["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "baseline_noise"]
    ]
    assert "not your change" in line2


def test_pristine_probe_is_not_folded_into_suite_truth(tmp_path):
    """The probe ran against a tree the agent is not submitting."""
    adapter = _adapter_with_baseline(tmp_path, captured=False)
    probe = ("cd /testbed && git stash && python -m pytest tests/ -q 2>&1 "
             "| tail -5; git stash pop")
    adapter.record_execution_evidence(
        _evidence(probe, "375 passed in 42.0s\n", returncode=0),
        command=probe,
    )
    ledger = adapter._suite_ledger()
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") is None
    assert adapter._submit_window_advisory() == ""


def test_real_baseline_outranks_the_probe(tmp_path):
    """A captured baseline is a clean capture; the probe only governs where
    the baseline has no opinion."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.note_baseline_probe(
        failing=["tests/test_base.py::test_get_item"], passing=())
    verdict = classify_failures_vs_baseline(
        command="pytest tests/test_base.py -q",
        output=_failing_output("tests/test_base.py::test_get_item"),
        ledger=ledger,
    )
    assert verdict.verdicts == (
        ("tests/test_base.py::test_get_item", "unverified_scope"),
    )


# --- suite-equivalent scope: `pytest tests/` IS the suite -----------------
#
# Run 35016130850: dynaconf's agent ran `pytest tests/` - every test the repo
# has - and the scope classifier still read `scoped` because the path was
# positional. No suite verdict was ever recorded, so the submit-window
# advisory stayed silent on a run the model was entitled to hear about.


def test_directory_run_covering_the_known_suite_writes_suite_truth(tmp_path):
    adapter = _adapter_with_baseline(tmp_path, with_repo=True)
    adapter.record_execution_evidence(
        _evidence("pytest tests/ -q",
                  "378 passed, 2 skipped in 40.0s\n", returncode=0),
        command="pytest tests/ -q",
    )
    adapter.record_execution_evidence(
        _evidence("pytest tests/test_base.py -q",
                  _failing_output("tests/test_base.py::test_get_item"),
                  action_id=2),
        command="pytest tests/test_base.py -q",
    )
    assert _last_row(adapter)["baseline_classification"]["verdicts"] == [
        ["tests/test_base.py::test_get_item", "scope_artifact"]
    ]


def test_directory_run_covering_the_known_suite_opens_the_window(tmp_path):
    """The advisory's whole-suite-green gate must credit `pytest tests/`."""
    adapter = _adapter_with_baseline(tmp_path, with_repo=True)
    line = adapter.record_execution_evidence(
        _evidence("pytest tests/ -q",
                  "378 passed, 2 skipped in 40.0s\n", returncode=0),
        command="pytest tests/ -q",
    )
    assert _last_row(adapter)["submit_window"]["layout_schema"] == (
        "gt.submit_window.v1"
    )
    assert "suite green vs baseline" in line


def test_directory_run_covering_the_known_suite_records_suite_failures(tmp_path):
    """The other half of suite truth: a failure inside a covering run is a
    SUITE failure, not a scoped observation."""
    adapter = _adapter_with_baseline(tmp_path, with_repo=True)
    adapter.record_execution_evidence(
        _evidence("pytest tests/ -q",
                  _failing_output("tests/test_base.py::test_get_item")
                  + "377 passed, 1 failed in 40.0s\n"),
        command="pytest tests/ -q",
    )
    ledger = adapter._suite_ledger()
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") == "fail"
    assert ledger.regression_count() == 1


def test_a_subdirectory_run_does_not_claim_suite(tmp_path):
    """The repo carries tests/ AND integration/; `pytest tests/` misses half
    the real inventory - it must stay scoped."""
    adapter = _adapter_with_baseline(
        tmp_path, with_repo=True,
        extra_repo_files=("integration/test_api.py",))
    ledger = adapter._suite_ledger()
    adapter.record_execution_evidence(
        _evidence("pytest tests/ -q",
                  "378 passed in 40.0s\n", returncode=0),
        command="pytest tests/ -q",
    )
    assert not ledger.has_whole_suite_green()
    assert ledger.suite_verdict("tests/test_base.py::test_get_item") is None
    assert adapter._submit_window_advisory() == ""


def test_a_narrowed_run_does_not_claim_suite(tmp_path):
    """`pytest tests/ -k get_item` selects a subset of its covered dir."""
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _evidence("pytest tests/ -q -k get_item",
                  "1 passed, 377 deselected in 4.0s\n", returncode=0),
        command="pytest tests/ -q -k get_item",
    )
    ledger = adapter._suite_ledger()
    assert not ledger.has_whole_suite_green()
    assert adapter._submit_window_advisory() == ""


def test_an_excluding_run_does_not_claim_suite(tmp_path):
    adapter = _adapter_with_baseline(tmp_path)
    adapter.record_execution_evidence(
        _evidence("pytest tests/ --ignore tests/test_utils.py -q",
                  "300 passed in 30.0s\n", returncode=0),
        command="pytest tests/ --ignore tests/test_utils.py -q",
    )
    ledger = adapter._suite_ledger()
    assert not ledger.has_whole_suite_green()


def test_a_file_scoped_run_never_claims_suite(tmp_path):
    """Even when every known name lives in that one file, a file path is a
    file path - the suite could have grown siblings baseline never named."""
    ledger = SuiteVerdictLedger(
        baseline_passing={"tests/test_base.py::test_a"}, baseline_failing=()
    )
    assert not ledger.covers_known_suite(("tests/test_base.py",), ())


def test_an_empty_known_universe_never_claims_suite(tmp_path):
    """suite_observed basis with zero names: `pytest tests/` proves coverage
    of nothing we can name, so it cannot be credited as the suite."""
    ledger = SuiteVerdictLedger(baseline_passing=(), baseline_failing=())
    assert not ledger.covers_known_suite(("tests",), ())


def test_covers_known_suite_unit_table(tmp_path):
    from gt_engine.runtime_observation import repo_test_inventory

    repo = tmp_path / "repo"
    for rel in ("tests/test_base.py", "tests/test_utils.py"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_x(): pass\n", encoding="utf-8")
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    ledger.note_repo_test_inventory(repo_test_inventory(repo))
    assert ledger.covers_known_suite(("tests",), ())
    assert ledger.covers_known_suite(("tests/",), ())
    assert not ledger.covers_known_suite(("tests/test_base.py",), ())
    assert not ledger.covers_known_suite((), ())
    assert not ledger.covers_known_suite(
        ("tests",), ("tests/test_utils.py",)
    )
    # A test FILE outside the covered root defeats the claim.
    extra = repo / "integration" / "test_x.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("def test_x(): pass\n", encoding="utf-8")
    ledger.note_repo_test_inventory(repo_test_inventory(repo))
    assert not ledger.covers_known_suite(("tests",), ())


def test_covers_known_suite_without_extent_evidence():
    """Neither inventory nor baseline scope: extent is unknown and unknown
    never authorises a whole-suite claim."""
    ledger = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING
    )
    assert not ledger.covers_known_suite(("tests",), ())

    # The baseline command's own declared scope is the fallback ground truth.
    declared = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING,
        baseline_scope="scoped", baseline_scope_paths=("tests",),
    )
    assert declared.covers_known_suite(("tests",), ())
    assert not declared.covers_known_suite(("tests/unit",), ())

    # A baseline that ran unrestricted makes every directory run narrower
    # than the suite by construction.
    unrestricted = SuiteVerdictLedger(
        baseline_passing=BASELINE_PASSING, baseline_failing=BASELINE_FAILING,
        baseline_scope="suite",
    )
    assert not unrestricted.covers_known_suite(("tests",), ())
