"""The tail cursor: one requirement in play, advanced only by evidence."""
from __future__ import annotations

from gt_engine.persistent_plan import PersistentPlan, PlanRow
from gt_engine.persistent_plan.cursor import (
    CURSOR_TAG,
    current_row,
    render_cursor,
)


def _plan(**kw):
    rows = tuple(
        PlanRow(
            row_id=f"req-{i}",
            text=f"requirement number {i}",
            verification_command=f"pytest tests/test_{i}.py",
        )
        for i in range(1, 4)
    )
    base = dict(
        status="READY", inputs=None, rows=rows, interactions=(),
        edit_order=("req-3", "req-1", "req-2"), abstentions=(),
        origin="deterministic",
    )
    base.update(kw)
    return PersistentPlan(**base)


def test_the_cursor_follows_the_graphs_edit_order_not_prompt_order():
    plan = _plan()
    # all three outstanding: the callee-first order puts req-3 first
    assert current_row(plan, ("req-1", "req-2", "req-3")) == "req-3"


def test_the_cursor_skips_what_is_already_proven():
    plan = _plan()
    assert current_row(plan, ("req-1", "req-2")) == "req-1"
    assert current_row(plan, ("req-2",)) == "req-2"


def test_the_cursor_names_one_row_and_how_to_prove_it():
    plan = _plan()
    text = render_cursor(plan, ("req-1", "req-2", "req-3"))
    assert CURSOR_TAG in text
    assert "req-3" in text
    assert "pytest tests/test_3.py" in text
    # exactly one row is in play
    assert text.count("Requirement to work on now:") == 1


def test_the_cursor_never_claims_a_requirement_is_satisfied():
    """It reported "9/12 proven" on a task that had written no code.

    A row turns GREEN by lexical match against a passing command, and commands
    run while merely exploring were enough. The agent read the count, drew the
    obvious conclusion and submitted after 208 turns and zero edits.
    """
    plan = _plan()
    text = render_cursor(plan, ("req-1", "req-2"), proven_delta=("req-3",))
    lowered = text.lower()
    for claim in ("proven", "satisfied", "complete", "/3"):
        assert claim not in lowered, claim


def test_the_cursor_is_small():
    """80 tokens a turn is 0.057% of a measured task. It must stay that way."""
    plan = _plan()
    text = render_cursor(plan, ("req-1", "req-2", "req-3"))
    assert len(text) < 700, len(text)


def test_the_cursor_moves_on_without_announcing_the_row_it_left():
    plan = _plan()
    text = render_cursor(plan, ("req-1", "req-2"), proven_delta=("req-3",))
    assert "req-3" not in text
    assert "req-1" in text


def test_an_empty_unmet_set_is_not_reported_as_completion():
    """Empty is equally the shape of predicates that never mapped."""
    plan = _plan()
    text = render_cursor(plan, ())
    assert "not evidence the change is complete" in text
    assert "check the request yourself" in text
    assert "Requirement to work on now:" not in text


def test_a_row_with_no_check_says_so_rather_than_inventing_one():
    rows = (PlanRow(row_id="req-1", text="a line nobody can prove"),)
    plan = _plan(rows=rows, edit_order=("req-1",))
    text = render_cursor(plan, ("req-1",))
    assert "no check was derived" in text
    assert "prove it with" not in text


def test_an_abstained_plan_says_nothing_at_all():
    plan = _plan(status="ABSTAINED")
    assert render_cursor(plan, ("req-1",)) == ""


def test_an_unknown_row_id_never_inflates_the_count():
    """Predicates can outlive rows; a stale id must not read as progress."""
    plan = _plan()
    text = render_cursor(plan, ("req-1", "ghost-row"))
    assert "req-1" in text
    assert "ghost-row" not in text
