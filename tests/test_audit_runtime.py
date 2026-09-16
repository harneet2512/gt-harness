"""Runtime-lane audit reproducers (docs/AUDIT-ABILITY-SPEC.md).

Each case drives the real Mini-SWE/GT seam the shipping workflow reaches —
``execute_actions`` -> ``compile_execution_evidence`` ->
``record_execution_evidence`` -> admission -> exact payload binding — not a
paraphrase of it. Cases whose root cause lived outside the harness-owned
files were ``xfail(strict=True)`` during the audit; both defects (partial
test inventory overclaim, prepared-only accounting credit) are now fixed at
their owning boundaries and the cases assert the corrected contract.

Required RED cases (per task spec):
  1. commit-message text mistaken for verification
  2. partial test inventory mistaken for complete suite coverage
  3. fully named green suite failing to open the submit-window advisory
  4. ``uv run --project NAME`` wrappers losing the test boundary
  5. ``vendor/src/a.py`` failure attributed to edited ``src/a.py``
  6. prepared-only evidence counted as model exposure

Positive controls that must stay green:
  * piped pytest still yields a typed test observation
  * recovery steer reaches the wire through real admission + binding
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.event_journal import verify_event_journal
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.persistent_plan.baseline import BaselineResult
from gt_engine.runtime_observation import (
    SuiteVerdictLedger,
    compile_execution_evidence,
)
from gt_engine.runtime_observation import (
    test_command_coverage as _command_coverage,
)
from gt_engine.runtime_observation import (
    test_command_scope as _command_scope,
)
from tests.test_miniswe_runtime import (
    FakeAgent,
    TransportFakeModel,
    _configure_fixture_provider,
    _session,
)


def _journal(adapter: MiniSweAdapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _adapter(tmp_path: Path, repo: Path | None = None, **kwargs) -> MiniSweAdapter:
    adapter = MiniSweAdapter(
        task_id="audit", state_dir=tmp_path / "state", predicates=[], **kwargs
    )
    if repo is not None:
        adapter.repo_root = str(repo)
    return adapter


def _baseline_adapter(tmp_path: Path, *, passing=(), failing=()) -> MiniSweAdapter:
    adapter = _adapter(tmp_path)
    adapter.plan_inputs = SimpleNamespace(
        baseline=BaselineResult(
            status="captured",
            passing_names=tuple(passing),
            failing_names=tuple(failing),
        )
    )
    return adapter


def _execute(agent: FakeAgent, command: str, output: str, returncode: int = 0):
    agent.env.execute = lambda action: {"output": output, "returncode": returncode}
    return agent.execute_actions({"extra": {"actions": [{"command": command}]}})


# ---------------------------------------------------------------------------
# RED-1: commit-message text must never be verification evidence
# ---------------------------------------------------------------------------


def test_quoted_semicolon_in_commit_message_is_not_test_evidence(
    tmp_path, monkeypatch
):
    """``git commit -m 'x;pytest tests/'``: the ``;`` is inside a quoted word,
    so no shell boundary exists. The canonical runner regex reads raw text and
    treats the quoted ``;`` as a separator - the runtime must classify the
    shell-honest surface, not the raw string."""
    _configure_fixture_provider(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter.start_task()
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    install_runtime_hooks(agent, _session(adapter))

    # Preload a verification-failure streak the commit must not erase.
    adapter.churn_governor.observe("pytest tests/", returncode=1)
    adapter.churn_governor.observe("pytest tests/", returncode=1)
    assert adapter.churn_governor.verify_fail_streak == 2

    _execute(
        agent,
        "git commit -m 'x;pytest tests/'",
        "[main deadbeef] x;pytest tests/\n 1 file changed, 1 insertion(+)\n",
    )

    rows = _journal(adapter)
    assert not [r for r in rows if r["event"] == "execution_evidence"], (
        "a commit message produced an execution-evidence artifact"
    )
    assert not [r for r in rows if r["event"] == "delivery_prepared"]
    # A commit message must not reset the verification-failure streak either:
    # the churn governor reads the same shell surface.
    assert adapter.churn_governor.verify_fail_streak == 2

    verification = verify_event_journal(adapter.store.path)
    assert verification.valid, verification.issues


def test_double_quoted_and_escaped_commit_text_is_not_test_evidence(
    tmp_path, monkeypatch
):
    """Same defect class, double quotes and a backslash-escaped separator."""
    _configure_fixture_provider(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter.start_task()
    agent = FakeAgent()
    install_runtime_hooks(agent, _session(adapter))

    _execute(
        agent,
        'git commit -m "fix; pytest tests/ fails on test_x"',
        "[main deadbeef] fix; pytest tests/ fails on test_x\n",
    )
    _execute(agent, "echo \\;pytest", "\\;pytest\n")

    rows = _journal(adapter)
    assert not [r for r in rows if r["event"] == "execution_evidence"]
    verification = verify_event_journal(adapter.store.path)
    assert verification.valid, verification.issues


def test_commit_message_flag_value_is_not_suite_scope():
    """``git commit -m pytest``: the ``-m`` belongs to git, not pytest. The
    word-parser must not let a bare ``pytest`` word inside another command's
    argument vector claim suite scope."""
    assert _command_scope("git commit -m pytest") == "unknown"
    assert _command_scope("git commit -m 'fix pytest failures'") == "unknown"
    assert _command_scope("git commit -am pytest") == "unknown"
    # Controls: real launcher forms keep their scopes.
    assert _command_scope("python -m pytest tests/") == "scoped"
    assert _command_scope("pytest tests/test_a.py::test_x -q") == "scoped"
    assert _command_scope("cd /testbed && pytest") == "suite"


def test_unquoted_commit_message_never_mints_a_pytest_segment():
    """``-m 'x;pytest'`` quotes protect the semicolon; ``-m x\\;pytest`` escapes
    it. Neither may split the word into a fake pytest segment."""
    from gt_engine.runtime_observation import _shell_words

    assert _shell_words("git commit -m 'x;pytest tests/'") == [
        "git", "commit", "-m", "x;pytest tests/",
    ]
    assert _shell_words("git commit -m x\\;pytest") == [
        "git", "commit", "-m", "x;pytest",
    ]
    # Control: real separators still split.
    assert _shell_words("cd /t && pytest tests/ | tail -5") == [
        "cd", "/t", "&&", "pytest", "tests/", "|", "tail", "-5",
    ]


# ---------------------------------------------------------------------------
# RED-2: partial test inventory is not complete suite coverage
# ---------------------------------------------------------------------------


def test_directory_run_over_partial_inventory_is_not_whole_suite(tmp_path):
    """``pytest tests/unit/`` covers every baseline name while
    ``tests/integration/`` exists on disk: the observed-name universe cannot
    prove suite extent, so the run stays scoped and the advisory stays
    silent. ``pytest tests/`` DOES cover the inventoried suite and opens it."""
    repo = tmp_path / "repo"
    for rel in (
        "tests/unit/test_a.py",
        "tests/unit/test_b.py",
        "tests/integration/test_c.py",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_x(): pass\n", encoding="utf-8")
    adapter = _baseline_adapter(
        tmp_path,
        passing=("tests/unit/test_a.py::test_a", "tests/unit/test_b.py::test_b"),
    )
    adapter.repo_root = str(repo)
    evidence = compile_execution_evidence(
        command="pytest tests/unit/ -q",
        output="2 passed in 0.3s\n",
        returncode=0,
        action_id=1,
        repository_revision="rev",
    )
    assert evidence is not None and evidence.kind == "test"
    adapter.record_execution_evidence(evidence, command="pytest tests/unit/ -q")
    ledger = adapter._suite_ledger()
    assert not ledger.has_whole_suite_green()
    assert adapter._submit_window_advisory() == ""

    # The positive control: covering the entire inventoried suite opens it.
    evidence = compile_execution_evidence(
        command="pytest tests/ -q",
        output="3 passed in 0.4s\n",
        returncode=0,
        action_id=2,
        repository_revision="rev",
    )
    adapter.record_execution_evidence(evidence, command="pytest tests/ -q")
    assert ledger.has_whole_suite_green()
    assert "suite green" in adapter._submit_window_advisory()


def test_file_scoped_run_never_claims_suite_even_over_known_names(tmp_path):
    """Control for RED-2 that must hold today: a single-file run over a
    known-name universe confined to that file is still not the suite."""
    adapter = _baseline_adapter(
        tmp_path, passing=("tests/test_a.py::test_one", "tests/test_a.py::test_two")
    )
    evidence = compile_execution_evidence(
        command="pytest tests/test_a.py -q",
        output="2 passed in 0.3s\n",
        returncode=0,
        action_id=1,
        repository_revision="rev",
    )
    adapter.record_execution_evidence(evidence, command="pytest tests/test_a.py -q")
    assert not adapter._suite_ledger().has_whole_suite_green()
    assert adapter._submit_window_advisory() == ""


def test_narrowed_and_excluded_runs_never_promote_suite(tmp_path):
    adapter = _baseline_adapter(
        tmp_path,
        passing=("tests/test_a.py::test_one", "tests/test_b.py::test_two"),
    )
    for command in (
        "pytest tests/ -k test_one -q",
        "pytest tests/ --ignore tests/test_b.py -q",
    ):
        evidence = compile_execution_evidence(
            command=command,
            output="1 passed in 0.2s\n",
            returncode=0,
            action_id=1,
            repository_revision="rev",
        )
        adapter.record_execution_evidence(evidence, command=command)
        assert not adapter._suite_ledger().has_whole_suite_green(), command


def test_empty_known_universe_cannot_establish_suite(tmp_path):
    ledger = SuiteVerdictLedger(baseline_passing=(), baseline_failing=())
    assert not ledger.covers_known_suite(("tests/",), ())


# ---------------------------------------------------------------------------
# RED-3: a fully named green suite must mark whole-suite state
# ---------------------------------------------------------------------------


def test_fully_named_green_suite_marks_whole_suite_state(tmp_path):
    """A verbose run names every test it passed. ``record_suite_run`` folded
    those names and returned before setting ``_whole_suite_green`` - the
    submit-window advisory could never open on exactly the evidence shape a
    careful agent produces. Suite extent is proven by the repo's real test
    inventory, so the fixture writes the two files the baseline names."""
    repo = tmp_path / "repo"
    for rel in ("tests/test_a.py", "tests/test_b.py"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_x(): pass\n", encoding="utf-8")
    adapter = _baseline_adapter(
        tmp_path,
        passing=("tests/test_a.py::test_one", "tests/test_b.py::test_two"),
    )
    adapter.repo_root = str(repo)
    output = (
        "tests/test_a.py::test_one PASSED\n"
        "tests/test_b.py::test_two PASSED\n"
        "========================= 2 passed in 0.4s =========================\n"
    )
    evidence = compile_execution_evidence(
        command="python -m pytest tests/ -v",
        output=output,
        returncode=0,
        action_id=1,
        repository_revision="rev",
    )
    assert evidence is not None
    line = adapter.record_execution_evidence(
        evidence, command="python -m pytest tests/ -v"
    )
    ledger = adapter._suite_ledger()
    assert ledger.has_whole_suite_green()
    assert "suite green" in line
    assert adapter._submit_window_advisory() == "suite green vs baseline"


def test_named_green_scoped_run_still_does_not_claim_suite(tmp_path):
    """The RED-3 fix must not weaken scope: a verbose green run confined to
    one file names everything it ran and still is not the suite."""
    adapter = _baseline_adapter(
        tmp_path,
        passing=("tests/test_a.py::test_one", "tests/test_b.py::test_two"),
    )
    output = (
        "tests/test_a.py::test_one PASSED\n"
        "========================= 1 passed in 0.2s =========================\n"
    )
    evidence = compile_execution_evidence(
        command="python -m pytest tests/test_a.py -v",
        output=output,
        returncode=0,
        action_id=1,
        repository_revision="rev",
    )
    adapter.record_execution_evidence(
        evidence, command="python -m pytest tests/test_a.py -v"
    )
    assert not adapter._suite_ledger().has_whole_suite_green()


def test_fully_named_suite_with_a_failure_records_verdicts_not_green(tmp_path):
    repo = tmp_path / "repo"
    for rel in ("tests/test_a.py", "tests/test_b.py"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_x(): pass\n", encoding="utf-8")
    adapter = _baseline_adapter(
        tmp_path,
        passing=("tests/test_a.py::test_one", "tests/test_b.py::test_two"),
    )
    adapter.repo_root = str(repo)
    output = (
        "tests/test_a.py::test_one PASSED\n"
        "tests/test_b.py::test_two FAILED\n"
        "=================== 1 failed, 1 passed in 0.4s =====================\n"
    )
    evidence = compile_execution_evidence(
        command="python -m pytest tests/ -v",
        output=output,
        returncode=1,
        action_id=1,
        repository_revision="rev",
    )
    adapter.record_execution_evidence(
        evidence, command="python -m pytest tests/ -v"
    )
    ledger = adapter._suite_ledger()
    assert not ledger.has_whole_suite_green()
    assert ledger._verdict_for("tests/test_b.py::test_two") == "regression_new"


# ---------------------------------------------------------------------------
# RED-4: ``uv run --project`` must not lose the pytest boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "uv run --project foo pytest tests/test_a.py -q",
        "uv run --project=foo pytest tests/test_a.py -q",
        "uv run --project foo python -m pytest tests/test_a.py -q",
        "cd /testbed && uv run --project dynaconf pytest tests/test_a.py -q",
    ],
)
def test_uv_project_wrapper_preserves_test_evidence(command):
    """The harness word-parser already sees the pytest segment, but the
    canonical runner regex does not match ``uv run --project foo pytest`` -
    ``--project``'s operand sits between the wrapper and the runner - so the
    command produced no execution evidence at all. The harness must hand the
    canonical classifier the pytest invocation it found."""
    evidence = compile_execution_evidence(
        command=command,
        output="tests/test_a.py::test_a FAILED - assert 1 == 2\n"
        "1 failed in 0.5s\n",
        returncode=1,
        action_id=1,
        repository_revision="rev",
    )
    assert evidence is not None, command
    assert evidence.kind == "test"
    assert evidence.observed_test_outcome == "fail"
    assert _command_coverage(command).scope == "scoped"
    assert _command_coverage(command).paths == ("tests/test_a.py",)


def test_uv_project_wrapper_pass_outcome_is_classified():
    evidence = compile_execution_evidence(
        command="uv run --project foo pytest tests/ -q",
        output="2 passed in 0.4s\n",
        returncode=0,
        action_id=1,
        repository_revision="rev",
    )
    assert evidence is not None
    assert evidence.observed_test_outcome == "pass"


def test_uv_project_wrapper_failure_reaches_baseline_classification(tmp_path):
    """The classification lane must see the scoped run, not drop it."""
    adapter = _baseline_adapter(
        tmp_path, passing=("tests/test_a.py::test_a",), failing=()
    )
    evidence = compile_execution_evidence(
        command="uv run --project foo pytest tests/test_a.py -q",
        output="tests/test_a.py::test_a FAILED - assert 1 == 2\n"
        "1 failed in 0.5s\n",
        returncode=1,
        action_id=1,
        repository_revision="rev",
    )
    assert evidence is not None
    line = adapter.record_execution_evidence(
        evidence, command="uv run --project foo pytest tests/test_a.py -q"
    )
    # The baseline join must see the scoped failure: a baseline-passing name
    # failing under a file-scoped run is an unverified-scope verdict.
    assert "passed at baseline" in line


# ---------------------------------------------------------------------------
# RED-5: a foreign path must not be attributed to an edited file
# ---------------------------------------------------------------------------


def test_vendor_path_is_not_attributed_to_edited_surface(tmp_path):
    """``vendor/src/a.py`` contains ``src/a.py`` as a substring but is not the
    edited file. Attribution must compare repository-relative paths, not raw
    substrings."""
    from gt_engine.miniswe_covering import attribute_test_failure

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter._edited_files.add("src/a.py")
    output = (
        "FAILED tests/test_a.py::test_a - Traceback:\n"
        '  File "vendor/src/a.py", line 12, in helper\n'
        "AssertionError: compute() returned 2\n"
    )
    covering = attribute_test_failure(
        adapter,
        "pytest tests/test_a.py -q",
        output,
        returncode=1,
        observed="fail",
    )
    assert covering is None, (
        "vendor/src/a.py was attributed to edited src/a.py - substring match"
    )


def test_direct_edited_path_is_still_attributed(tmp_path):
    """Positive control for RED-5: the edited file's own path still links."""
    from gt_engine.miniswe_covering import attribute_test_failure

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter._edited_files.add("src/a.py")
    output = (
        "FAILED tests/test_a.py::test_a - Traceback:\n"
        '  File "src/a.py", line 12, in helper\n'
        "AssertionError: compute() returned 2\n"
    )
    covering = attribute_test_failure(
        adapter,
        "pytest tests/test_a.py -q",
        output,
        returncode=1,
        observed="fail",
    )
    assert covering is not None


def test_attribution_follows_cd_prefix_paths(tmp_path):
    """``cd tests && pytest`` emits paths relative to tests/; an edited file
    named in that output still links when the resolved path matches."""
    from gt_engine.miniswe_covering import attribute_test_failure

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter._edited_files.add("src/a.py")
    covering = attribute_test_failure(
        adapter,
        "cd tests && pytest test_a.py -q",
        "FAILED test_a.py::test_a\n"
        '  File "../src/a.py", line 12, in helper\n',
        returncode=1,
        observed="fail",
    )
    assert covering is not None


# ---------------------------------------------------------------------------
# RED-6: prepared-only evidence is not model exposure
# ---------------------------------------------------------------------------


def test_prepared_delivery_without_binding_writes_no_delivery_row(tmp_path):
    """Runtime-side invariant (must hold): admit -> provider refusal/discard
    leaves a ``delivery_prepared`` row and NO delivery row."""
    adapter = _adapter(tmp_path)
    adapter.start_task()
    assert adapter.admit_model_visible_delivery(
        lane="sealed",
        kind="localization",
        rendered="ranked locations",
        action_index=0,
        iteration=0,
        dedup_key="audit-loc",
    )
    adapter.discard_pending_provider_deliveries(reason="provider_refused")
    rows = _journal(adapter)
    assert [r for r in rows if r["event"] == "delivery_prepared"]
    assert not [
        r
        for r in rows
        if r["event"] in ("evidence_delivery", "context_addition_delivery")
    ]
    assert not adapter.deliveries
    verification = verify_event_journal(adapter.store.path)
    assert verification.valid, verification.issues


def test_prepared_only_delivery_is_not_counted_as_model_exposure(tmp_path):
    """The feature census must not claim the model saw bytes that never
    reached an admitted provider request. A staged identity revoked by
    ``prepared_deliveries_discarded`` is a refused delivery, not a delivered
    one - and a prepared-never-bound identity is ``prepared_unbound``, not
    DELIVERED."""
    from scripts.feature_accounting import account

    adapter = _adapter(tmp_path)
    adapter.start_task()
    assert adapter.admit_model_visible_delivery(
        lane="sealed",
        kind="localization",
        rendered="ranked locations",
        action_index=0,
        iteration=0,
        dedup_key="audit-loc",
    )
    adapter.discard_pending_provider_deliveries(reason="provider_refused")
    out = account(_journal(adapter))
    row = next(r for r in out["rows"] if r["feature"] == "localization")
    assert row["state"] != "DELIVERED"
    assert row["delivered"] == 0


def test_bound_delivery_is_counted_as_exposure(tmp_path, monkeypatch):
    """Positive control for RED-6: the same bytes admitted and carried in the
    exact provider request DO count."""
    from scripts.feature_accounting import account

    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    adapter.start_task()
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    install_runtime_hooks(agent, _session(adapter))
    rendered = "ranked locations"
    assert adapter.admit_model_visible_delivery(
        lane="sealed",
        kind="localization",
        rendered=rendered,
        action_index=0,
        iteration=0,
        dedup_key="audit-loc",
    )
    agent.model._query([{"role": "user", "content": rendered}])
    assert adapter.deliveries and adapter.deliveries[-1].delivery_ids
    out = account(_journal(adapter))
    row = next(r for r in out["rows"] if r["feature"] == "localization")
    assert row["state"] == "DELIVERED"
    assert row["delivered"] == 1


# ---------------------------------------------------------------------------
# Positive controls: piped pytest + recovery steer on the real path
# ---------------------------------------------------------------------------


def test_piped_pytest_still_produces_test_evidence(tmp_path, monkeypatch):
    """Regression control: ``cd /testbed && python -m pytest ... | tail`` must
    keep producing a typed test observation with its textual outcome."""
    _configure_fixture_provider(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter.start_task()
    agent = FakeAgent()
    install_runtime_hooks(agent, _session(adapter))

    _execute(
        agent,
        f"cd {repo} && python -m pytest tests/test_base.py -q 2>&1 | tail -20",
        "tests/test_base.py::test_x FAILED - assert 1 == 2\n1 failed in 0.5s\n",
        returncode=0,
    )
    rows = _journal(adapter)
    evidence = [r for r in rows if r["event"] == "execution_evidence"]
    assert evidence, "piped pytest produced no execution evidence"
    assert evidence[-1].get("observed_test_outcome") == "fail"
    verification = verify_event_journal(adapter.store.path)
    assert verification.valid, verification.issues


def test_commit_message_never_mints_evidence_but_real_pytest_does(
    tmp_path, monkeypatch
):
    """Same session, both halves: the commit message stays silent and the
    real run still lands."""
    _configure_fixture_provider(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = _adapter(tmp_path, repo)
    adapter.start_task()
    agent = FakeAgent()
    install_runtime_hooks(agent, _session(adapter))

    _execute(agent, "git commit -m 'x;pytest tests/'", "[main d] x\n")
    _execute(
        agent,
        "python -m pytest tests/test_a.py -q",
        "1 passed in 0.3s\n",
    )
    evidence = [r for r in _journal(adapter) if r["event"] == "execution_evidence"]
    assert len(evidence) == 1
    assert evidence[0].get("observed_test_outcome") == "pass"


def test_recovery_steer_reaches_wire_through_real_admission(tmp_path, monkeypatch):
    """D3 lane: fail -> edit -> same fail -> the next agent turn carries the
    recovery steer, bound to the exact request. Full path: execute_actions
    fingerprints, workspace-epoch edit, query_transport staging and
    bind_provider_payload commit."""
    from gt_engine.miniswe_controller import Predicate
    from gt_engine.task_contract import extract_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    _configure_fixture_provider(monkeypatch)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("def compute():\n    return 1\n")
    contract = extract_task_contract("compute() must pass the pytest suite.")
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    adapter = MiniSweAdapter(
        task_id="audit",
        state_dir=tmp_path / "state",
        predicates=predicates,
        repo_root=str(repo),
        contract=contract,
    )
    adapter.start_task()
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    install_runtime_hooks(agent, _session(adapter))

    failing = "tests/test_a.py::test_a FAILED - assert compute() == 2\n1 failed\n"

    def execute(action):
        command = action.get("command", "")
        if "WRITE_NOW" in command:
            (repo / "src" / "a.py").write_text(
                "def compute():\n    return 2\n"
            )
            return {"output": "", "returncode": 0}
        return {"output": failing, "returncode": 1}

    agent.env.execute = execute

    def _execute2(cmd):
        return agent.execute_actions(
            {"extra": {"actions": [{"command": cmd}]}}
        )

    _execute2("python -m pytest tests/test_a.py -q")
    _execute2(
        "python - <<'WRITE_NOW'\n"
        "open('src/a.py','w').write('def compute():\\n    return 2\\n')\n"
        "WRITE_NOW"
    )
    _execute2("python -m pytest tests/test_a.py -q")

    assert adapter.pending_transient.startswith("GT_RECOVERY"), (
        "the second identical post-edit failure never scheduled a steer"
    )

    agent.model._query([{"role": "user", "content": "next"}])
    last = agent.model.calls[-1]
    assert any(
        isinstance(m.get("content"), str) and "GT_RECOVERY" in m["content"]
        for m in last
    )
    assert adapter._recovery_delivered == 1
    rows = _journal(adapter)
    assert [r for r in rows if r["event"] == "recovery_steer"]
    verification = verify_event_journal(adapter.store.path)
    assert verification.valid, verification.issues
