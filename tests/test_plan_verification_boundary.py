"""Pending checks execute before the next verification decision, not each edit."""
import json
import sys
from types import SimpleNamespace

import pytest

from gt_engine.event_journal import verify_event_journal
from gt_engine.gt_session import GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan
from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment


def test_finalization_reminders_are_bounded_and_do_not_commit(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    git("init")
    (repo / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=", "commit", "-m", "fixture")
    head = git("rev-parse", "HEAD").decode().strip()
    (repo / "a.py").write_text("x = 2\n")
    index_before = git("diff", "--cached")
    adapter = MiniSweAdapter(task_id="finalize", repo_root=str(repo), state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    adapter.begin_implement()
    session = GTSession(GTSessionConfig(task_id="finalize", repo_root=str(repo), mode="advisory"), engine=adapter)
    session._patch_baseline = head
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (1000, None))
    assert session._finalization_candidate() is None
    adapter.begin_verify()
    first = session.before_model([], 1)
    assert any("[GT_FINALIZATION]" in part for part in first.context_additions)
    assert session._finalization_candidate() is None
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (599, None))
    assert session._finalization_candidate() is not None
    assert session._finalization_candidate() is None
    assert git("rev-parse", "HEAD").decode().strip() == head
    assert git("diff", "--cached") == index_before
    events = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    assert sum(row["event"] == "submission_patch_observed" for row in events) == 2
    assert verify_event_journal(adapter.store.path).valid


def test_close_journals_the_terminal_patch_observation(tmp_path, monkeypatch):
    """Gate-one's journal had no row stating what the submitted tree
    contained: patch observation fired only on prompt-path finalization
    stages. The terminal row at close() is the submit-time record."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    git("init")
    (repo / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=", "commit", "-m", "fixture")
    head = git("rev-parse", "HEAD").decode().strip()
    (repo / "a.py").write_text("x = 2\n")
    adapter = MiniSweAdapter(task_id="close", repo_root=str(repo),
                           state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    session = GTSession(GTSessionConfig(task_id="close", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._patch_baseline = head

    session.close("submitted_unverified")

    events = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    rows = [row for row in events if row["event"] == "submission_patch_observed"]
    assert len(rows) == 1
    assert rows[0]["stage"] == "submit"
    assert rows[0]["status"] == "observed"
    assert rows[0]["baseline"] == head
    assert rows[0]["committed_patch_empty"] is True
    assert rows[0]["uncommitted_tracked"] is True
    assert verify_event_journal(adapter.store.path).valid


@pytest.mark.parametrize(("phase", "seconds", "steps", "executes"), [
    ("VERIFY", 1000, None, True),
    ("IMPLEMENT", 1000, None, False),
    ("VERIFY", 600, None, False),
    ("VERIFY", 1000, 19, False),
])
def test_pending_check_at_native_decision_boundary(tmp_path, monkeypatch, phase, seconds, steps, executes):
    if executes and not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree capture is required for automatic check authority")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="boundary", repo_root=str(repo),
                             state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    adapter.begin_implement()
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    check_id = adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"],
                                       "requirement_ids": [row_id]})
    if phase == "VERIFY":
        adapter.begin_verify()
    session = GTSession(GTSessionConfig(task_id="boundary", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    session._plan_agent = SimpleNamespace(env=CredentialIsolatedLocalEnvironment(
        cwd=str(repo), timeout=10, evidence_root=tmp_path / "evidence"))
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (seconds, steps))
    session.before_model([], 1)
    assert (check_id not in adapter._pending_check_ids) is executes
    assert adapter.plan_row_state(row_id) == ("CHECK_PASSED" if executes else "UNVERIFIED")
    published = json.loads((adapter.store.root / "plan" / "current.json").read_text(encoding="utf-8"))
    assert published["rows"][0]["state"] == adapter.plan_row_state(row_id)
    session.before_model([], 2)
    events = [json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]
    assert sum(row["event"] == "plan_check_observed" for row in events) == int(executes)
    assert verify_event_journal(adapter.store.path).valid


def test_boundary_executor_failure_does_not_block_the_model(tmp_path, monkeypatch):
    adapter = MiniSweAdapter(task_id="failure", repo_root=str(tmp_path),
                             state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    adapter.begin_verify()
    adapter._pending_check_ids = {"pending"}
    session = GTSession(GTSessionConfig(task_id="failure", repo_root=str(tmp_path), mode="advisory"),
                        engine=adapter)
    session._plan_agent = SimpleNamespace(env=object())
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (1000, None))

    def fail(*args, **kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(adapter, "drain_plan_checks", fail)
    session.before_model([], 1)
    assert adapter._pending_check_ids == {"pending"}
    assert "plan_check_boundary" in adapter.store.path.read_text(encoding="utf-8")
    capability = next(row for row in session._mandatory_capability_rows() if row[0] == "gt_engine_enabled")
    assert capability[2] == "disabled_at_plan_check_boundary:RuntimeError"


@pytest.mark.parametrize(("predicate_state", "check_passes"), [("GREEN", False), ("RED", True)])
def test_current_failure_is_not_hidden_by_another_evidence_channel(tmp_path, monkeypatch, predicate_state, check_passes):
    from gt_engine.miniswe_controller import Predicate

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree capture required")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text(f"def test_widget(): assert {check_passes}\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="precedence", repo_root=str(repo),
                             state_dir=tmp_path / "state", predicates=[Predicate("p", "mapped assertion")])
    adapter.start_task()
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    adapter.plan_row_predicates = {row_id: ("p",)}
    # Seed a typed predicate receipt; the separate check runs real pytest.
    adapter.record_receipt("p", "supported assertion fixture", 0 if predicate_state == "GREEN" else 1,
                           "assertion fixture", epoch=adapter.workspace_epoch,
                           status=predicate_state, semantic=True)
    adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"], "requirement_ids": [row_id]})
    environment = CredentialIsolatedLocalEnvironment(cwd=str(repo), timeout=10,
                                                     evidence_root=tmp_path / "evidence")
    adapter.drain_plan_checks(environment)
    assert row_id in adapter.unmet_plan_rows()
    session = GTSession(GTSessionConfig(task_id="precedence", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (1000, None))
    assert row_id in session._plan_cursor_candidate().rendered
    assert session.plan_submit_gate() is False
    events = [json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]
    evidence = next(row["evidence"] for row in reversed(events) if row["event"] == "plan_gate_decision")
    assert evidence["predicate_mapped_rows"] == [row_id]
    assert evidence["check_passed_rows" if check_passes else "check_failed_rows"] == [row_id]
    assert evidence["completion_assessment"] == (
        "all_rows_verified" if check_passes else "rows_unverified")
    assert verify_event_journal(adapter.store.path).valid


def test_real_agent_check_displaces_automatic_rerun_after_repeated_edits(tmp_path, monkeypatch):
    from gt_engine.runtime_observation import capture_workspace

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree capture required")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "value.txt"
    source.write_text("0", encoding="utf-8")
    (repo / "test_widget.py").write_text(
        "from pathlib import Path\ndef test_widget(): assert Path('value.txt').read_text() == '2'\n",
        encoding="utf-8")
    adapter = MiniSweAdapter(task_id="equivalent", repo_root=str(repo),
                             state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    check_id = adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"], "requirement_ids": [row_id]})
    environment = CredentialIsolatedLocalEnvironment(cwd=str(repo), timeout=10,
                                                     evidence_root=tmp_path / "evidence")
    for value in ("1", "2"):
        source.write_text(value, encoding="utf-8")
        adapter.note_edit(["value.txt"])
    assert adapter._pending_check_ids == {check_id}
    adapter.publish_plan_state()
    spec = adapter._check_specs[check_id]
    before = capture_workspace(str(repo))
    result = environment.execute({"command": spec.command, "argv": list(spec.argv)})
    after = capture_workspace(str(repo))
    adapter.record_repository_snapshot(after, boundary="after_agent_check")
    adapter.observe_plan_checks(spec.command, result, before, after, environment)
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert adapter._pending_check_ids == set()
    published = json.loads((adapter.store.root / "plan" / "current.json").read_text(encoding="utf-8"))
    assert published["rows"][0]["state"] == "CHECK_PASSED"

    def unexpected(*args, **kwargs):
        pytest.fail("equivalent agent execution was repeated automatically")

    monkeypatch.setattr(environment, "execute", unexpected)
    adapter.drain_plan_checks(environment)
    assert verify_event_journal(adapter.store.path).valid


def test_verification_pass_budget_is_shared_by_all_pending_checks(tmp_path, monkeypatch):
    import time

    from gt_engine import miniswe_integration

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree capture required")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("a", "b"):
        (repo / f"test_{name}.py").write_text(f"def test_{name}(): assert True\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="allowance", repo_root=str(repo),
                             state_dir=tmp_path / "state", predicates=())
    adapter.start_task()
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    for name in ("a", "b"):
        adapter.bind_plan_check({"argv": ["pytest", "-v", f"test_{name}.py"], "requirement_ids": [row_id]})
    environment = CredentialIsolatedLocalEnvironment(cwd=str(repo), timeout=10,
                                                     evidence_root=tmp_path / "evidence")
    clock = [0.0]
    monkeypatch.setattr(miniswe_integration, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time, perf_counter=time.perf_counter))
    execute = environment.execute
    calls = []

    def measured(*args, **kwargs):
        calls.append(kwargs["timeout"])
        result = execute(*args, **kwargs)
        clock[0] = 30.0
        return result

    monkeypatch.setattr(environment, "execute", measured)
    adapter.drain_plan_checks(environment, budget_seconds=30)
    assert calls == [15]
    assert len(adapter._pending_check_ids) == 1
    assert adapter.plan_row_state(row_id) == "UNVERIFIED"
    assert verify_event_journal(adapter.store.path).valid


def _stub_check_env(calls, *, returncode=0, writes=None):
    class Env:
        def execution_env(self):
            return lambda *a, **k: None

        def execute(self, payload, **kwargs):
            calls.append(payload)
            if writes is not None:
                writes()
            return {"returncode": returncode,
                    "output": "1 passed" if returncode == 0 else "1 failed",
                    "extra": {"capture_complete": True,
                              "environment_sha256": "env-1"}}
    return Env()


def _seal_session(tmp_path, monkeypatch, *, predicates=()):
    """An adapter that bound+passed one check at revision R, then moved to
    R' - the exact staleness shape that attested product_completion_unverified
    while the official verifier scored the task solved."""
    from gt_engine.runtime_observation import capture_workspace

    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text(
        "def test_widget(): assert True\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="seal", repo_root=str(repo),
                             state_dir=tmp_path / "state",
                             predicates=predicates)
    adapter.start_task()
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.",
        repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    check_id = adapter.bind_plan_check(
        {"argv": ["pytest", "-v", "test_widget.py"],
         "requirement_ids": [row_id]})
    return repo, adapter, row_id, check_id, capture_workspace


def test_seal_recheck_refreshes_stale_check_evidence(tmp_path, monkeypatch):
    from gt_engine.miniswe_controller import Predicate

    repo, adapter, row_id, check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch, predicates=[Predicate("p", "mapped assertion")])
    adapter.plan_row_predicates = {row_id: ("p",)}
    calls = []
    environment = _stub_check_env(calls)
    adapter.drain_plan_checks(environment)
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    adapter.record_receipt("p", "assertion fixture", 0, "1 passed",
                           epoch=adapter.workspace_epoch, status="GREEN",
                           semantic=True)
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    assert adapter.plan_row_state(row_id) == "UNVERIFIED"
    adapter._phase = "FINISHED"
    session = GTSession(GTSessionConfig(
        task_id="seal", repo_root=str(repo), mode="advisory",
        state_dir=str(tmp_path / "sstate"),
        capabilities=("exact_provider_payload", "provider_response_ids",
                      "structured_actions", "structured_results",
                      "workspace_deltas", "filesystem_snapshots",
                      "tool_call_deferral", "parsed_test_results")),
        engine=adapter)
    session._plan_agent = SimpleNamespace(env=environment)
    state = session.completion_state()
    assert len(calls) == 2
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert state["verified"] is True
    events = [json.loads(line) for line in
              adapter.store.path.read_text(encoding="utf-8").splitlines()]
    rechecks = [row for row in events if row["event"] == "plan_seal_recheck"]
    assert rechecks[-1]["reason"] == "converged"
    assert check_id in rechecks[-1]["repended"]
    assert verify_event_journal(adapter.store.path).valid


def test_seal_recheck_failed_check_stays_unverified(tmp_path, monkeypatch):
    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch)
    calls = []
    adapter.drain_plan_checks(_stub_check_env([]))
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    adapter._phase = "FINISHED"
    session = GTSession(GTSessionConfig(task_id="seal", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._plan_agent = SimpleNamespace(
        env=_stub_check_env(calls, returncode=1))
    state = session.completion_state()
    assert len(calls) == 1
    assert adapter.plan_row_state(row_id) == "CHECK_FAILED"
    assert state["verified"] is False
    assert row_id in state["unmet_plan_rows"]
    events = [json.loads(line) for line in
              adapter.store.path.read_text(encoding="utf-8").splitlines()]
    rechecks = [row for row in events if row["event"] == "plan_seal_recheck"]
    assert rechecks[-1]["reason"] == "still_unverified"
    assert verify_event_journal(adapter.store.path).valid


def test_seal_recheck_check_side_effects_do_not_unsubmit(tmp_path, monkeypatch):
    """A check whose own run writes files (pytest cache, coverage data) moves
    the workspace revision inside the drain. The transaction machinery needs
    IMPLEMENT to record that; the seal borrows the phase and must restore
    FINISHED unconditionally or final_state loses the verified block."""
    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch)
    adapter.drain_plan_checks(_stub_check_env([]))
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    adapter._phase = "FINISHED"

    def dirty():
        (repo / ".check-cache").write_text("run\n", encoding="utf-8")

    calls = []
    session = GTSession(GTSessionConfig(task_id="seal", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._plan_agent = SimpleNamespace(
        env=_stub_check_env(calls, writes=dirty))
    session.completion_state()
    assert adapter.phase == "FINISHED"
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert verify_event_journal(adapter.store.path).valid


def test_seal_recheck_bounded_under_perpetual_churn(tmp_path, monkeypatch):
    """Two checks that each dirty the tree can never hold a simultaneous
    fresh revision. The seal must bound the fixpoint instead of chasing it -
    the verdict stays honestly unverified rather than hanging close()."""
    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch)
    (repo / "test_widget2.py").write_text(
        "def test_widget2(): assert True\n", encoding="utf-8")
    adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget2.py"],
                             "requirement_ids": [row_id]})
    adapter.drain_plan_checks(_stub_check_env([]))
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    adapter._phase = "FINISHED"
    counter = [0]

    def churn():
        counter[0] += 1
        (repo / f".churn-{counter[0]}").write_text("x\n", encoding="utf-8")

    calls = []
    session = GTSession(GTSessionConfig(task_id="seal", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._plan_agent = SimpleNamespace(
        env=_stub_check_env(calls, writes=churn))
    state = session.completion_state()
    assert adapter.phase == "FINISHED"
    assert len(calls) <= 2 * adapter.SEAL_PLAN_RECHECK_PASSES
    assert state["verified"] is False
    assert verify_event_journal(adapter.store.path).valid


def test_seal_recheck_skips_when_not_finished(tmp_path, monkeypatch):
    repo, adapter, _row_id, _check_id, _cw = _seal_session(tmp_path, monkeypatch)
    calls = []
    session = GTSession(GTSessionConfig(task_id="seal", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._plan_agent = SimpleNamespace(env=_stub_check_env(calls))
    session.completion_state()
    assert not calls
    events = [json.loads(line) for line in
              adapter.store.path.read_text(encoding="utf-8").splitlines()]
    assert not any(row["event"] == "plan_seal_recheck" for row in events)


def test_seal_recheck_executes_real_pytest_on_submitted_tree(tmp_path, monkeypatch):
    """End-to-end with the real isolation boundary: pytest's own cache writes
    are the check side-effect the phase borrow exists for."""
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree capture required")
    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch)
    environment = CredentialIsolatedLocalEnvironment(
        cwd=str(repo), timeout=20, evidence_root=tmp_path / "evidence")
    adapter.drain_plan_checks(environment)
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    assert adapter.plan_row_state(row_id) == "UNVERIFIED"
    adapter._phase = "FINISHED"
    session = GTSession(GTSessionConfig(task_id="seal", repo_root=str(repo),
                                        mode="advisory"), engine=adapter)
    session._plan_agent = SimpleNamespace(env=environment)
    session.completion_state()
    assert adapter.phase == "FINISHED"
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert verify_event_journal(adapter.store.path).valid


def test_stale_red_receipt_does_not_block_a_current_check_pass(
        tmp_path, monkeypatch):
    """A lexically-associated failure on revision R must not veto a bound
    check passing on R' - the normal edit/test/fix/test cycle would otherwise
    leave every run unverifiable (run 34914512942: task solved, verified=False
    on RED receipts from a tree the workspace had already left)."""
    from gt_engine.miniswe_controller import Predicate, PredicateStatus

    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch, predicates=[Predicate("p", "mapped assertion")])
    adapter.plan_row_predicates = {row_id: ("p",)}
    snapshot = capture_workspace(str(repo))
    adapter.record_repository_snapshot(snapshot, boundary="task_start")
    revision_r = adapter.repository_revision
    adapter.record_receipt(
        "p", "pytest test_widget.py", 1, "1 failed",
        epoch=adapter.workspace_epoch, status="RED", semantic=True,
        evidence_kind="failing_execution",
        coverage_basis="lexically_associated_failure",
        source_revision_at_observation=revision_r)
    (repo / "widget.py").write_text("X = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="agent_edit")
    assert adapter.repository_revision != revision_r
    calls = []
    adapter.drain_plan_checks(_stub_check_env(calls))
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert adapter.predicate_status("p") is PredicateStatus.GREEN
    assert "p" not in adapter.unmet_predicates


def test_current_red_receipt_still_blocks_a_later_check_pass(
        tmp_path, monkeypatch):
    """Precedence preserved: a RED observed on the CURRENT tree is a live
    failure and a passing bound check must not paper over it."""
    from gt_engine.miniswe_controller import Predicate, PredicateStatus

    repo, adapter, row_id, _check_id, capture_workspace = _seal_session(
        tmp_path, monkeypatch, predicates=[Predicate("p", "mapped assertion")])
    adapter.plan_row_predicates = {row_id: ("p",)}
    adapter.record_repository_snapshot(
        capture_workspace(str(repo)), boundary="task_start")
    adapter.record_receipt(
        "p", "pytest test_widget.py", 1, "1 failed",
        epoch=adapter.workspace_epoch, status="RED", semantic=True,
        evidence_kind="failing_execution",
        coverage_basis="lexically_associated_failure",
        source_revision_at_observation=adapter.repository_revision)
    calls = []
    adapter.drain_plan_checks(_stub_check_env(calls))
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    assert adapter.predicate_status("p") is PredicateStatus.RED
    assert "p" in adapter.unmet_predicates


_MISMATCH_OUTPUT = """\
_ ERROR collecting tests_functional/issues/658_nested_envvar_override/app_test.py _
import file mismatch:
imported module 'app_test' has this __file__ attribute:
  /testbed/tests_functional/issues/1005-key-type-error/app_test.py
which is not the same as the test file we want to collect:
  /testbed/tests_functional/issues/658_nested_envvar_override/app_test.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
"""


def _mismatch_then_env(calls, retry_returncode, retry_output):
    """First execute returns the dynaconf collection-mismatch signature; the
    retry result is supplied by the caller."""
    class Env:
        def execution_env(self):
            return lambda *a, **k: None

        def execute(self, payload, **kwargs):
            calls.append(payload)
            if len(calls) == 1:
                return {"returncode": 2, "output": _MISMATCH_OUTPUT,
                        "extra": {"capture_complete": True,
                                  "environment_sha256": "env-1"}}
            return {"returncode": retry_returncode, "output": retry_output,
                    "extra": {"capture_complete": True,
                              "environment_sha256": "env-1"}}
    return Env()


def test_pytest_collection_mismatch_retries_under_importlib(tmp_path, monkeypatch):
    """Run 34919574013 (dynaconf-1241): three bound checks failed on EVERY
    revision because their argv - several tests_functional/**/app_test.py in
    one invocation, or bare pytest over a tree full of same-named
    app_test.py - dies at collection with 'import file mismatch'. No
    assertion ever ran, so no tree could verify. The drain must retry the
    declared check under --import-mode=importlib and classify that verdict."""
    repo, adapter, row_id, _check_id, _cw = _seal_session(tmp_path, monkeypatch)
    calls = []
    adapter.drain_plan_checks(_mismatch_then_env(calls, 0, "1 passed"))
    assert len(calls) == 2
    assert calls[0]["argv"] == ["pytest", "-v", "test_widget.py"]
    assert "--import-mode=importlib" in calls[1]["argv"]
    assert adapter.plan_row_state(row_id) == "CHECK_PASSED"
    events = [json.loads(line) for line in
              adapter.store.path.read_text(encoding="utf-8").splitlines()]
    acc = [row for row in events if row["event"] == "plan_check_argv_accommodated"]
    assert len(acc) == 1
    assert acc[0]["reason"] == "pytest_import_file_mismatch"
    assert acc[0]["declared_argv"] == ["pytest", "-v", "test_widget.py"]
    assert "--import-mode=importlib" in acc[0]["executed_argv"]
    assert verify_event_journal(adapter.store.path).valid


def test_pytest_collection_mismatch_retry_failure_stays_failed(
        tmp_path, monkeypatch):
    """The accommodation is bounded and honest: if the importlib retry also
    fails, the observation is the retry's real verdict (a genuinely failing
    test stays CHECK_FAILED) and the retry fires once, never in a loop."""
    repo, adapter, row_id, _check_id, _cw = _seal_session(tmp_path, monkeypatch)
    calls = []
    adapter.drain_plan_checks(
        _mismatch_then_env(calls, 1, "1 failed in 0.10s"))
    assert len(calls) == 2
    assert adapter.plan_row_state(row_id) == "CHECK_FAILED"
    adapter.drain_plan_checks(
        _mismatch_then_env(calls, 1, "1 failed in 0.10s"))
    assert verify_event_journal(adapter.store.path).valid


def test_pytest_importlib_argv_helpers():
    from gt_engine.persistent_plan.checks import (
        pytest_collection_mismatch,
        pytest_importlib_argv,
    )
    assert pytest_collection_mismatch(_MISMATCH_OUTPUT)
    assert not pytest_collection_mismatch("1 passed in 0.10s")
    assert pytest_importlib_argv(["pytest", "a/x.py", "b/x.py"]) == [
        "pytest", "--import-mode=importlib", "a/x.py", "b/x.py"]
    assert pytest_importlib_argv(
        ["python", "-m", "pytest", "tests/"]) == [
        "python", "-m", "pytest", "--import-mode=importlib", "tests/"]
    already = ["pytest", "--import-mode=importlib", "x.py"]
    assert pytest_importlib_argv(already) == already
