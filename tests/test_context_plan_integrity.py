"""Regressions for the measured false-GREEN and destructive replay paths."""
from types import SimpleNamespace

import pytest

from gt_engine.task_contract import Obligation, TaskContract
from gt_engine.verification_contract import (
    ObligationPredicate,
    evaluate_passing_observation,
)


@pytest.mark.parametrize("command,output", [
    ("pytest", "42 passed"),
    ("rg 'verify widget behavior' src", "verify widget behavior"),
    ("python -c \"print('verify widget behavior')\"", "verify widget behavior"),
])
def test_unbound_behavior_cannot_be_proven_by_passing_output(command, output):
    contract = TaskContract("code_change", (
        Obligation("req-widget", "verify widget behavior", "test"),
    ))
    predicates = {"req-widget": ObligationPredicate("pred-widget", "req-widget", "behavior")}
    assert evaluate_passing_observation(
        contract, predicates, command, output, action_index=1,
    ) == ()


def test_edit_does_not_replay_original_shell_command(monkeypatch):
    from gt_engine.miniswe_integration import MiniSweAdapter

    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="", stderr="", returncode=0)
    monkeypatch.setattr("subprocess.run", run)
    engine = SimpleNamespace(
        store=SimpleNamespace(append=lambda *a, **k: None), workspace_epoch=2,
        repo_root=".", REVERIFY_PASS_BUDGET_SECONDS=30,
        REVERIFY_COMMAND_TIMEOUT_SECONDS=15,
    )
    MiniSweAdapter._reverify_after_edit(engine, {"pred-widget": "edit-source && pytest"})
    assert calls == []


def test_long_requirement_is_not_dropped():
    from gt_engine.persistent_plan.ledger import build_requirement_ledger

    requirement = "The widget must preserve " + "all existing behavior " * 40
    ledger = build_requirement_ledger(requirement)
    assert len(ledger.rows) == 1
    assert ledger.rows[0].text == requirement.strip()


def test_cursor_preserves_exact_check_command():
    from gt_engine.persistent_plan import PersistentPlan, PlanRow
    from gt_engine.persistent_plan.cursor import render_cursor

    command = "pytest tests/" + "a" * 180 + ".py -k 'boundary and regression'"
    plan = PersistentPlan(
        status="READY", inputs=None,
        rows=(PlanRow(row_id="req-widget", text="widget behavior", verification_command=command),),
        interactions=(), edit_order=("req-widget",), abstentions=(), origin="deterministic",
    )
    assert command in render_cursor(plan, ("req-widget",))


def test_baseline_analyzes_summary_after_preview(monkeypatch, tmp_path):
    from gt_engine.persistent_plan import baseline

    output = "x" * 25000 + "\n1 passed\n"
    monkeypatch.setattr(baseline, "_tracked_dirty", lambda root: ())
    monkeypatch.setattr(baseline.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=output, stderr="", returncode=0))
    seen = []
    def parse(text, command):
        seen.append(text)
        return {"passed": int("1 passed" in text), "failed": 0, "errored": 0}, [], []
    monkeypatch.setattr(baseline, "_parse", parse)
    result = baseline.run_baseline(str(tmp_path), budget_seconds=1, command=("pytest",))
    assert result.captured
    assert output in seen[0]


def test_disappearing_baseline_test_is_incomplete_not_intact(monkeypatch):
    from gt_engine.persistent_plan import baseline

    before = baseline.BaselineResult(status="captured", passed=2, passing_names=("a", "b"))
    after = baseline.BaselineResult(status="captured", passed=2, passing_names=("a", "c"))
    monkeypatch.setattr(baseline, "run_baseline", lambda *a, **kw: after)
    assert baseline.compare_to_baseline(before, ".", budget_seconds=1).status == "incomplete"


def test_baseline_never_restores_source(monkeypatch):
    from gt_engine.persistent_plan import baseline

    calls = []
    monkeypatch.setattr(baseline.subprocess, "run", lambda *a, **kw: calls.append(a))
    assert baseline._restore(".", ("src/widget.py",)) == ()
    assert calls == []
