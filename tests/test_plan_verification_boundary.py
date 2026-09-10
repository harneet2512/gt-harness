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
