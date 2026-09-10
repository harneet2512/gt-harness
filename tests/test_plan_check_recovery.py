"""Restart check definitions without resurrecting historical proof."""
import json

import pytest

from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan


def adapter_at(tmp_path, prompt="The widget must preserve compatibility."):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    adapter = MiniSweAdapter(task_id="restart", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        prompt, repo_root=str(repo), capture_baseline=False))
    return adapter


@pytest.mark.parametrize("changed", [False, True])
def test_restart_restores_only_current_check_definitions(tmp_path, changed):
    first = adapter_at(tmp_path)
    test = tmp_path / "repo" / "test_widget.py"
    test.write_text("def test_widget(): assert True\n", encoding="utf-8")
    row_id = first.persistent_plan.rows[0].row_id
    check_id = first.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"],
                                      "requirement_ids": [row_id]})
    first.store.append("plan_check_observed", check_id=check_id, state="CHECK_PASSED")
    if changed:
        test.write_text("def test_widget(): assert False\n", encoding="utf-8")
    resumed = adapter_at(tmp_path)
    resumed.bind_initial_plan_checks()
    assert resumed.plan_row_state(row_id) == "UNVERIFIED"
    if changed:
        assert check_id not in getattr(resumed, "_pending_check_ids", set())
        assert "plan_check_restore_rejected" in resumed.store.path.read_text(encoding="utf-8")
    else:
        assert resumed._pending_check_ids == {check_id}
        assert resumed._check_specs[check_id].requirement_ids == (row_id,)
    count = resumed.store.receipt()["event_count"]
    resumed.bind_initial_plan_checks()
    assert resumed.store.receipt()["event_count"] == count


def test_corrupt_journal_cannot_restore_check_commands(tmp_path):
    first = adapter_at(tmp_path)
    first.store.append("plan_check_bound", argv=["pytest"])
    rows = [json.loads(line) for line in first.store.path.read_text(encoding="utf-8").splitlines()]
    row = rows[-1]
    row["argv"] = ["sh", "-c", "echo unsafe"]
    first.store.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    resumed = adapter_at(tmp_path)
    resumed.bind_initial_plan_checks()
    assert not getattr(resumed, "_check_specs", {})
    assert not getattr(resumed, "_pending_check_ids", set())


def test_restart_preserves_shared_binding_revision(tmp_path):
    import hashlib

    prompt = "The widget must preserve compatibility.\nThe widget must reject invalid input."
    first = adapter_at(tmp_path, prompt)
    (tmp_path / "repo" / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    ids = [row.row_id for row in first.persistent_plan.rows]
    check_id = first.bind_plan_check({"argv": ["pytest", "-v", "test_widget.py"], "requirement_ids": ids})
    inbox = first.store.root / "plan" / "requests"
    inbox.mkdir(parents=True)
    (inbox / "revision.json").write_text(json.dumps({
        "operation": "revise", "row_id": ids[0], "value": {"approach": "changed design"},
        "plan_digest": hashlib.sha256(first.persistent_plan.canonical_json().encode()).hexdigest(),
    }), encoding="utf-8")
    first.apply_plan_requests()
    resumed = adapter_at(tmp_path, prompt)
    resumed.bind_initial_plan_checks()
    assert resumed._check_specs[check_id].requirement_ids == (ids[1],)
    assert resumed.plan_row_state(ids[0]) == "UNVERIFIED"


def test_recovered_check_executes_in_real_isolated_environment(tmp_path, monkeypatch):
    import sys

    from gt_engine.event_journal import verify_event_journal
    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux capture and process-tree boundary required")
    first = adapter_at(tmp_path)
    (tmp_path / "repo" / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    row_id = first.persistent_plan.rows[0].row_id
    first.bind_plan_check({"argv": [sys.executable, "-m", "pytest", "-v", "test_widget.py"],
                           "requirement_ids": [row_id]})
    resumed = adapter_at(tmp_path)
    resumed.start_task()
    resumed.bind_initial_plan_checks()
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    environment = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path / "repo"),
        timeout=15, evidence_root=tmp_path / "evidence")
    resumed.drain_plan_checks(environment)
    assert resumed.plan_row_state(row_id) == "CHECK_PASSED"
    assert not resumed._pending_check_ids
    assert verify_event_journal(resumed.store.path).valid
