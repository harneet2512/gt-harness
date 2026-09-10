"""With the flag off the run must be indistinguishable from before.

This is the parity pin. The persistent plan changes the prompt, the obligation
set and the submit decision, so an accidental default-on would silently change
what the benchmark measures. Every assertion here is about absence.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gt_engine.persistent_plan import PLAN_FLAG, plan_enabled

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "miniswe_gt_run.py"
RUNTIME = Path(__file__).resolve().parents[1] / "gt_engine" / "miniswe_runtime.py"


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("0", False), ("", False), ("true", False), (None, False)],
)
def test_the_flag_is_off_unless_it_is_exactly_one(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(PLAN_FLAG, raising=False)
    else:
        monkeypatch.setenv(PLAN_FLAG, value)
    assert plan_enabled() is expected


def test_an_explicit_zero_survives_the_runner_default():
    """The runner uses setdefault, so an operator's 0 must win."""
    source = RUNNER.read_text(encoding="utf-8")
    assert 'os.environ.setdefault("GT_PERSISTENT_PLAN", "1")' in source
    assert 'os.environ["GT_PERSISTENT_PLAN"] = "1"' not in source


def _requires_plan_enabled(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == "persistent_plan_enabled"
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return any(_requires_plan_enabled(value) for value in node.values)
    return False


@pytest.mark.parametrize("expression,expected", [
    ("persistent_plan_enabled()", True),
    ("persistent_plan_enabled() and not setup_error", True),
    ("persistent_plan_enabled() or not setup_error", False),
    ("not persistent_plan_enabled()", False),
    ("other_flag()", False),
])
def test_plan_guard_requires_an_unconditional_positive_flag(expression, expected):
    assert _requires_plan_enabled(ast.parse(expression, mode="eval").body) is expected


def test_phase_zero_is_guarded_by_the_flag():
    """build_plan_inputs must sit inside a persistent_plan_enabled() branch."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    calls: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_plan_inputs"
    ]
    assert calls, "build_plan_inputs is not called in the runner"
    guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and _requires_plan_enabled(node.test)
    ]
    assert guards, "no persistent_plan_enabled() guard found"
    guarded = {
        id(node)
        for guard in guards
        for statement in guard.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
    }
    assert all(id(call) in guarded for call in calls)


def test_the_planning_call_is_skipped_when_phase_zero_did_not_run():
    """No plan inputs means no provider call: the bootstrap returns early."""
    source = RUNTIME.read_text(encoding="utf-8")
    body = source.split("def bootstrap_persistent_plan()", 1)[1]
    head = body.split("def query(", 1)[0]
    assert 'inputs = getattr(adapter, "plan_inputs", None)' in head
    assert "if inputs is None:" in head
    early_return = head.index("if inputs is None:")
    native_call = head.index("native_query(")
    assert early_return < native_call, "the guard must precede the provider call"


def test_the_adapter_reports_zero_plan_calls_when_nothing_ran(tmp_path):
    from gt_engine.miniswe_integration import MiniSweAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="off", state_dir=tmp_path / "state", predicates=[], repo_root=str(repo)
    )
    state = adapter.final_state()
    assert state["persistent_plan_bootstrap_calls"] == 0
    assert state["persistent_plan_status"] == ""
    assert state["unmet_plan_rows"] == []


def test_no_plan_journal_rows_without_a_plan(tmp_path):
    from gt_engine.miniswe_integration import MiniSweAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="off", state_dir=tmp_path / "state", predicates=[], repo_root=str(repo)
    )
    adapter.final_state()
    rows = Path(adapter.store.path).read_text(encoding="utf-8")
    for name in (
        "persistent_plan_inputs",
        "persistent_plan_built",
        "persistent_plan_delivered",
        "plan_gate_decision",
    ):
        assert f'"event": "{name}"' not in rows


def test_the_gate_is_inert_without_a_plan(tmp_path):
    """An adapter with no plan must fall through to the advisory path."""
    from gt_engine.gt_session import GTSession, GTSessionConfig
    from gt_engine.miniswe_integration import MiniSweAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="off", state_dir=tmp_path / "state", predicates=[], repo_root=str(repo)
    )
    session = GTSession(
        GTSessionConfig(task_id="off", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )
    assert session.plan_submit_gate() is True


def test_the_receipt_counter_defaults_to_zero_for_older_runs():
    """A receipt written before this feature must still reconcile."""
    from gt_harness.runtime_receipts import _provider_usage  # noqa: F401

    source = (
        Path(__file__).resolve().parents[1] / "gt_harness" / "runtime_receipts.py"
    ).read_text(encoding="utf-8")
    assert 'gt.get("persistent_plan_bootstrap_calls") or 0' in source
    assert 'receipt.get("persistent_plan_bootstrap_calls") or 0' in source
