"""One line of steering per turn, instead of a document read once.

The plan block sits in the durable task message, which is 1,555 tokens of an
80,169-token request: two percent, written at turn one and never spoken of
again. Measured on run 34374028796, across a 300-turn task GT added something
to the request tail three times. So the agent reads its requirements once and
then runs three hundred turns with no statement of which one it is on, what
remains, or what would prove any of it. A document at two percent of context
competes with the agent's own recent output, and recency wins.

This is the other half: a few lines at the tail, refreshed when the world
changes, naming ONE requirement and the command that would prove it.

Two properties matter more than the wording.

It is cheap. Eighty tokens across 161 turns is 12,880 tokens against that task's
22.7 million, or 0.057 percent. Nothing here needs to be rationed.

It advances on PROOF. The cursor moves to the next requirement only when a
receipt records evidence for the current one -- never because the model said it
was done. A survey of thirteen coding agents found not one that verifies a
checked box against execution evidence; they all trust the checkbox. We have
obligations, predicates and receipts already, so we are able to do the thing
that makes a plan a force rather than a suggestion, and this is where that
happens.
"""
from __future__ import annotations

from . import PersistentPlan

CURSOR_TAG = "GT_PLAN_CURSOR"
MAX_TEXT_CHARS = 160
MAX_COMMAND_CHARS = 160


def current_row(plan: PersistentPlan, unmet: tuple[str, ...]) -> str:
    """The row to work on now: first unproven in the plan's own edit order.

    Edit order is a callee-first topological sort the graph already produced and
    that nothing has ever consumed. Ordering is arithmetic the engine can do, so
    the model is handed the answer rather than the puzzle -- the same move
    Cursor makes with ready-task ids and Claude Code with blocking edges.
    """
    if not unmet:
        return ""
    outstanding = set(unmet)
    for row_id in plan.edit_order:
        if row_id in outstanding:
            return row_id
    for row in plan.rows:
        if row.row_id in outstanding:
            return row.row_id
    return ""


def render_cursor(
    plan: PersistentPlan,
    unmet: tuple[str, ...],
    *,
    proven_delta: tuple[str, ...] = (),
) -> str:
    """The tail block. Empty when there is nothing outstanding to say."""
    if plan.status == "ABSTAINED" or not plan.rows:
        return ""
    total = len(plan.rows)
    outstanding = tuple(row_id for row_id in unmet if plan.row(row_id) is not None)
    proven = total - len(outstanding)

    lines = [f"[{CURSOR_TAG}]"]
    for row_id in proven_delta[:3]:
        row = plan.row(row_id)
        if row is not None:
            lines.append(f"  proven: {row_id} {_clip(row.text, 90)}")

    if not outstanding:
        lines.append(
            f"  all {total} requirements have evidence. Re-check the regression "
            "baseline, then submit."
        )
        return "\n".join(lines)

    row_id = current_row(plan, outstanding)
    row = plan.row(row_id)
    if row is None:
        return ""
    lines.append(f"  {proven}/{total} requirements proven. Now: {row_id}")
    lines.append(f"    {_clip(row.text, MAX_TEXT_CHARS)}")
    if row.approach:
        lines.append(f"    design: {_clip(row.approach, MAX_TEXT_CHARS)}")
    if row.verification_command:
        lines.append(
            f"    prove it with: {_clip(row.verification_command, MAX_COMMAND_CHARS)}"
        )
    else:
        lines.append(
            "    no check was derived for this one; state in your own words what "
            "you did and why it satisfies the line."
        )
    remaining = [r for r in outstanding if r != row_id]
    if remaining:
        lines.append(f"    then: {', '.join(remaining[:6])}")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"
