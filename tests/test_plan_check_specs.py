import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gt_engine.persistent_plan.checks import CheckSpec, classify_bound_check


def spec(tmp_path):
    return CheckSpec.from_dict({"argv": ["pytest", "-v", "tests/test_widget.py"],
                               "requirement_ids": ["req-widget"],
                               "selected_test_ids": ["test_widget"]}, str(tmp_path))


@pytest.mark.parametrize("argv", [["bash", "-c", "edit && pytest"], ["python", "-c", "edit()"], []])
def test_no_shell_or_inline_program_spec(tmp_path, argv):
    with pytest.raises(ValueError):
        CheckSpec.from_dict({"argv": argv, "requirement_ids": ["req-widget"]}, str(tmp_path))


def test_bound_pass_is_not_proof(tmp_path):
    check = replace(spec(tmp_path), test_source_digest="test-source")
    execution = SimpleNamespace(environment_sha256="env", repository_revision="rev",
        command_sha256=hashlib.sha256(check.command.encode()).hexdigest(),
        protocol="pytest", timed_out=False, outcome="pass", returncode=0)
    result = classify_bound_check(check, execution, before_revision="rev", after_revision="rev",
                                  capture_complete=True, test_ids=("test_widget",),
                                  test_source_digest="test-source")
    assert result.state == "CHECK_PASSED"
    assert classify_bound_check(check, execution, before_revision="old", after_revision="rev",
                                capture_complete=True, test_ids=("test_widget",),
                                test_source_digest="test-source").state == "UNVERIFIED"
    assert classify_bound_check(check, execution, before_revision="rev", after_revision="rev",
                                capture_complete=False, test_ids=("test_widget",),
                                test_source_digest="test-source").state == "UNVERIFIED"
    assert classify_bound_check(check, execution, before_revision="rev", after_revision="rev",
                                capture_complete=True, test_ids=(),
                                test_source_digest="test-source").state == "UNVERIFIED"
    assert classify_bound_check(replace(check, environment_sha256="other"), execution,
                                before_revision="rev", after_revision="rev", capture_complete=True,
                                test_ids=("test_widget",),
                                test_source_digest="test-source").state == "UNVERIFIED"


@pytest.mark.parametrize(("changes", "state"), [
    ({"timed_out": True}, "UNVERIFIED"),
    ({"returncode": 1}, "UNVERIFIED"),
    ({"environment_sha256": ""}, "UNVERIFIED"),
    ({"command_sha256": "other"}, "UNVERIFIED"),
    ({"protocol": "unittest"}, "UNVERIFIED"),
    ({"outcome": "fail", "returncode": 1}, "CHECK_FAILED"),
    ({"outcome": "env_fail", "returncode": 1}, "CHECK_FAILED"),
])
def test_bound_check_guards_have_independent_witnesses(tmp_path, changes, state):
    check = replace(spec(tmp_path), test_source_digest="test-source", protocol="pytest")
    facts = dict(environment_sha256="env", repository_revision="rev",
                 command_sha256=hashlib.sha256(check.command.encode()).hexdigest(),
                 protocol="pytest", timed_out=False, outcome="pass", returncode=0)
    facts.update(changes)
    result = classify_bound_check(check, SimpleNamespace(**facts), before_revision="rev",
                                  after_revision="rev", capture_complete=True,
                                  test_ids=("test_widget",), test_source_digest="test-source")
    assert result.state == state


def test_changed_test_source_cannot_keep_bound_pass(tmp_path):
    check = replace(spec(tmp_path), test_source_digest="original-test")
    execution = SimpleNamespace(environment_sha256="env", repository_revision="rev",
        command_sha256=hashlib.sha256(check.command.encode()).hexdigest(),
        protocol="pytest", timed_out=False, outcome="pass", returncode=0)
    assert classify_bound_check(check, execution, before_revision="rev", after_revision="rev",
                                capture_complete=True, test_ids=("test_widget",)).state == "UNVERIFIED"


def test_check_id_binds_arguments_and_scope(tmp_path):
    check = spec(tmp_path)
    assert CheckSpec.from_dict(check.as_dict(), str(tmp_path)) == check
    with pytest.raises(ValueError, match="identity mismatch"):
        CheckSpec.from_dict({**check.as_dict(), "argv": ["pytest", "other.py"]}, str(tmp_path))


def test_real_isolated_environment_executes_literal_argv(tmp_path):
    import sys

    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=10,
                                             evidence_root=tmp_path / "evidence")
    result = env.execute({"command": "audit text only", "argv": [sys.executable, "-c",
        "import sys; print(sys.argv[1])", "literal && no-shell-expansion"]})
    assert result["returncode"] == 0
    raw = env.evidence_store.bytes(result["extra"]["output_artifact"]["sha256"])
    assert raw.decode().strip() == "literal && no-shell-expansion"


def test_plan_cli_requests_are_applied_by_engine_and_cannot_grant_proof(tmp_path, monkeypatch):
    import json

    from gt_engine.event_journal import verify_event_journal
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.bootstrap import build_plan
    from gt_engine.persistent_plan.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    inputs = build_plan_inputs("The widget must preserve compatibility.",
                               repo_root=str(repo), capture_baseline=False)
    plan = build_plan(None, inputs)
    adapter = MiniSweAdapter(task_id="cli", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = plan
    adapter.publish_plan_state()
    row_id = plan.rows[0].row_id
    monkeypatch.setenv("GT_PLAN_ROOT", str(adapter.store.root / "plan"))
    assert main(["show", row_id]) == 0
    revision = tmp_path / "revision.json"
    revision.write_text(json.dumps({"state": "PROVEN"}), encoding="utf-8")
    assert main(["revise", row_id, "--file", str(revision)]) == 1
    revision.write_text(json.dumps({"approach": "preserve the existing interface"}), encoding="utf-8")
    assert main(["revise", row_id, "--file", str(revision)]) == 0
    adapter.apply_plan_requests()
    assert plan.row(row_id).approach == "preserve the existing interface"
    assert adapter.plan_row_state(row_id) == "UNVERIFIED"
    assert verify_event_journal(adapter.store.path).valid


@pytest.mark.parametrize("value", [[], ["approach"], 7, None, "approach"])
def test_malformed_revision_is_rejected_without_stopping_request_queue(tmp_path, value):
    import json

    from gt_engine.event_journal import verify_event_journal
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.bootstrap import build_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(task_id="invalid-request", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(repo), capture_baseline=False))
    row_id = adapter.persistent_plan.rows[0].row_id
    request = {"plan_digest": hashlib.sha256(adapter.persistent_plan.canonical_json().encode()).hexdigest(),
               "row_id": row_id, "operation": "revise", "value": value}
    inbox = adapter.store.root / "plan" / "requests"
    inbox.mkdir(parents=True)
    (inbox / "01-invalid.json").write_text(json.dumps(request), encoding="utf-8")
    request["value"] = {"approach": "valid subsequent design"}
    (inbox / "02-valid.json").write_text(json.dumps(request), encoding="utf-8")
    adapter.apply_plan_requests()
    assert adapter.persistent_plan.row(row_id).approach == "valid subsequent design"
    assert adapter.plan_row_state(row_id) == "UNVERIFIED"
    journal = adapter.store.path.read_text(encoding="utf-8")
    assert '"plan_revision_rejected"' in journal
    assert '"plan_revision_applied"' in journal
    assert verify_event_journal(adapter.store.path).valid


def test_revising_one_row_preserves_other_shared_check_bindings(tmp_path):
    import json

    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.bootstrap import build_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    inputs = build_plan_inputs("The widget must preserve compatibility.\nThe widget must reject invalid input.",
                               repo_root=str(repo), capture_baseline=False)
    adapter = MiniSweAdapter(task_id="shared", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = build_plan(None, inputs)
    first, second = [row.row_id for row in adapter.persistent_plan.rows[:2]]
    check_id = adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"],
                                       "requirement_ids": [first, second]})
    inbox = adapter.store.root / "plan" / "requests"
    inbox.mkdir(parents=True)
    (inbox / "revision.json").write_text(json.dumps({
        "plan_digest": hashlib.sha256(adapter.persistent_plan.canonical_json().encode()).hexdigest(),
        "row_id": first, "operation": "revise", "value": {"approach": "new design"},
    }), encoding="utf-8")
    adapter.apply_plan_requests()
    assert adapter._check_specs[check_id].requirement_ids == (second,)
    assert adapter._pending_check_ids == {check_id}
    assert adapter.plan_row_state(first) == "UNVERIFIED"


def test_validation_digest_tracks_tests_and_configuration_not_implementation(tmp_path):
    from gt_engine.persistent_plan.checks import validation_source_digest
    from gt_engine.runtime_observation import capture_workspace

    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_widget.py"
    test_file.write_text("def test_widget(): assert True\n", encoding="utf-8")
    source = tmp_path / "widget.py"
    source.write_text("value = 1\n", encoding="utf-8")
    check = spec(tmp_path)
    first = validation_source_digest(check, capture_workspace(tmp_path))
    assert first
    source.write_text("value = 2\n", encoding="utf-8")
    assert validation_source_digest(check, capture_workspace(tmp_path)) == first
    test_file.write_text("def test_widget(): assert False\n", encoding="utf-8")
    assert validation_source_digest(check, capture_workspace(tmp_path)) != first
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    config_digest = validation_source_digest(check, capture_workspace(tmp_path))
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -x\n", encoding="utf-8")
    assert validation_source_digest(check, capture_workspace(tmp_path)) != config_digest


def test_real_queue_coalesces_edits_and_checks_current_source(tmp_path, monkeypatch):
    import os
    import sys
    from pathlib import Path

    from gt_engine.event_journal import verify_event_journal
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.bootstrap import build_plan
    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    inputs = build_plan_inputs("The widget must preserve compatibility.",
                               repo_root=str(repo), capture_baseline=False)
    adapter = MiniSweAdapter(task_id="queue", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = build_plan(None, inputs)
    adapter.start_task()
    row_id = adapter.persistent_plan.rows[0].row_id
    check_id = adapter.bind_plan_check({"argv": ["python", "-m", "pytest", "-v", "test_widget.py"],
                                       "requirement_ids": [row_id]})
    adapter._reverify_after_edit({})
    adapter._reverify_after_edit({})
    assert adapter._pending_check_ids == {check_id}
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    env = CredentialIsolatedLocalEnvironment(cwd=str(repo), timeout=15,
        evidence_root=tmp_path / "evidence", env={"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    adapter.drain_plan_checks(env)
    # Windows execution deliberately lacks the Linux descendant/capture proof.
    expected = "CHECK_PASSED" if sys.platform.startswith("linux") else "UNVERIFIED"
    assert adapter.plan_row_state(row_id) == expected
    assert adapter._pending_check_ids == set()
    assert verify_event_journal(adapter.store.path).valid


def test_queue_does_not_spend_expired_allowance_after_snapshot(tmp_path, monkeypatch):
    from gt_engine import runtime_observation
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.bootstrap import build_plan

    (tmp_path / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="snapshot-budget", state_dir=tmp_path / "state",
                             repo_root=str(tmp_path), predicates=())
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve compatibility.", repo_root=str(tmp_path), capture_baseline=False))
    check_id = adapter.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"],
                                       "requirement_ids": [adapter.persistent_plan.rows[0].row_id]})
    elapsed = [0.0]
    capture = runtime_observation.capture_workspace
    def slow_capture(*args, **kwargs):
        snapshot = capture(*args, **kwargs)
        elapsed[0] = 31.0
        return snapshot
    calls = []
    environment = SimpleNamespace(execution_env=lambda: {},
                                  execute=lambda *a, **kw: calls.append(kw))
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    monkeypatch.setattr("gt_engine.miniswe_integration.time.monotonic", lambda: elapsed[0])
    monkeypatch.setattr(runtime_observation, "capture_workspace", slow_capture)
    adapter.drain_plan_checks(environment, budget_seconds=30)
    assert calls == []
    assert check_id in adapter._pending_check_ids
