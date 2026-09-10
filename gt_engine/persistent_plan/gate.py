"""The completion gate: refuse a submission that has not shown its work.

Twelve of twenty tasks in the measured run ended ``submitted_unverified`` --
the agent submitted while obligations had no evidence at all. That single fact
is what failed the attestation. It is not a defect in the gate: the benchmark
runs ``--gt-mode advisory``, so ``GTSession.can_enforce`` is False and the
existing enforcing gate is simply unreachable.

This module supplies the decision for a plan-scoped gate that works in advisory
mode, with three properties that keep it honest:

* It bounds consecutive refusals without progress and escapes on budget.
* It escapes on budget. A gate that turns a near-miss into a timeout converts a
  partial score into a zero, and four of the measured losses were one or two
  tests short. Time and steps are checked BEFORE any refusal.
* It never blocks on its own ignorance. An unmet row blocks; an unknown
  baseline, a failed probe or a missing plan does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Refusing with less than this left converts a near-miss into a timeout, which
# scores the same as a wrong answer. The agent needs room to act on the refusal.
MIN_REMAINING_SECONDS = 600.0
MIN_REMAINING_STEPS = 20
MAX_LISTED_ROWS = 12

# How many refusals may pass without the agent proving a single new row.
#
# Refusing exactly once made the gate a formality. Measured on run
# 34374028796, task claude-code: the gate refused with rows unmet, and accepted
# the very next attempt 34 seconds later with 4,260 seconds and 142 steps still
# available and the same rows still unproven. Seventy-one minutes went unused
# because a counter said "you have had your turn". awilix was the same, 1,647
# seconds and 108 steps left.
#
# A plan that yields the moment it is ignored is advice, and thirteen surveyed
# coding agents already ship advice. So refusal now persists while the budget is
# healthy AND refusals are still converting into evidence.
#
# The stall counter is the safety catch. A gate that refuses forever turns a
# partial score into a zero, which is worse than submitting an incomplete patch,
# so when this many consecutive refusals produce no newly proven row the gate
# concedes and says so. Progress resets it: an agent that keeps proving rows is
# never cut off.
MAX_REFUSALS_WITHOUT_PROGRESS = 3


@dataclass
class GateDecision:
    accepted: bool
    reason: str
    unmet_rows: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    remaining_seconds: float = 0.0
    remaining_steps: int | None = 0
    refusals: int = 0
    directive: str = ""
    escaped: str = ""
    baseline_status: str = ""
    details: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "unmet_rows": list(self.unmet_rows),
            "regressions": list(self.regressions),
            "remaining_seconds": round(self.remaining_seconds, 1),
            "remaining_steps": self.remaining_steps,
            "refusals": self.refusals,
            "escaped": self.escaped,
            "baseline_status": self.baseline_status,
            "completion_proven": False,
            "evidence": self.details,
        }


def budget_allows_refusal(remaining_seconds: float, remaining_steps: int | None) -> tuple[bool, str]:
    """Is there room for the agent to act on a refusal and submit again?"""
    if remaining_seconds < MIN_REMAINING_SECONDS:
        return False, "time"
    if remaining_steps is not None and remaining_steps < MIN_REMAINING_STEPS:
        return False, "steps"
    return True, ""


def decide(
    *,
    plan,
    unmet_rows: tuple[str, ...],
    regressions: tuple[str, ...],
    remaining_seconds: float,
    remaining_steps: int | None,
    refusals: int,
    baseline_status: str = "",
    refusals_without_progress: int | None = None,
    row_states: dict[str, str] | None = None,
    predicate_mapped_rows: tuple[str, ...] | None = None,
) -> GateDecision:
    """The whole gate policy, as a pure function of the facts.

    Kept free of session and adapter objects so the policy can be read and
    tested on its own; the caller supplies the numbers and applies the result.
    """
    row_ids = {row.row_id for row in getattr(plan, "rows", ())}
    states = row_states or {}
    details = {
        "layout_schema": "gt.plan_gate_evidence.v1",
        "completion_assessment": "not_established",
        "baseline_assessment": baseline_status or "unavailable",
        "mapping_assessment": "available" if predicate_mapped_rows is not None else "unavailable",
        "predicate_mapped_rows": sorted(row_ids & set(predicate_mapped_rows or ())),
        "unmapped_rows": sorted(row_ids - set(predicate_mapped_rows)) if predicate_mapped_rows is not None else [],
        "check_passed_rows": sorted(key for key in row_ids if states.get(key) == "CHECK_PASSED"),
        "check_failed_rows": sorted(key for key in row_ids if states.get(key) == "CHECK_FAILED"),
        "deferred_rows": sorted(key for key in row_ids if states.get(key) == "DEFERRED"),
        "unverified_rows": sorted(key for key in row_ids if states.get(key, "UNVERIFIED") == "UNVERIFIED"),
    }
    common = {
        "remaining_seconds": remaining_seconds,
        "remaining_steps": remaining_steps,
        "refusals": refusals,
        "baseline_status": baseline_status,
        "details": details,
    }
    if plan is None or not getattr(plan, "rows", ()):  # nothing to gate on
        return GateDecision(accepted=True, reason="no_plan", **common)
    # Callers that predate the stall counter get the old shape, where every
    # refusal counted as a stall, so their behaviour is unchanged.
    stalled = refusals if refusals_without_progress is None else refusals_without_progress
    if stalled >= MAX_REFUSALS_WITHOUT_PROGRESS:
        return GateDecision(
            accepted=True, reason="refusals_without_progress",
            unmet_rows=unmet_rows, regressions=regressions, **common,
        )
    blocking = tuple(unmet_rows) + tuple(regressions)
    if not blocking:
        return GateDecision(accepted=True, reason="no_blocking_evidence", **common)
    allowed, escape = budget_allows_refusal(remaining_seconds, remaining_steps)
    if not allowed:
        return GateDecision(
            accepted=True, reason="budget_escape", escaped=escape,
            unmet_rows=unmet_rows, regressions=regressions, **common,
        )
    return GateDecision(
        accepted=False,
        reason="unmet_plan_rows" if unmet_rows else "baseline_regression",
        unmet_rows=unmet_rows,
        regressions=regressions,
        directive=render_directive(plan, unmet_rows, regressions),
        **common,
    )


def render_directive(
    plan, unmet_rows: tuple[str, ...], regressions: tuple[str, ...]
) -> str:
    """What the agent is told when the gate refuses.

    Names outstanding rows and proposed checks without asserting that executing
    a check proves its requirement. Retries follow the bounded stall policy.
    """
    lines = [
        "GT PLAN GATE: submission was not executed. The plan built before the "
        "first edit still has requirements with no evidence. You may run any "
        "command, edit any file, or disagree. A later submission is reassessed "
        "against current evidence, remaining budget, and the bounded stall limit.",
    ]
    if unmet_rows:
        lines.append("Requirements with no evidence yet:")
        for row_id in unmet_rows[:MAX_LISTED_ROWS]:
            row = plan.row(row_id) if hasattr(plan, "row") else None
            text = row.text if row is not None else row_id
            lines.append(f"- {row_id}: {text}")
            if row is not None and row.verification_command:
                lines.append(f"    proposed check (not proof): {row.verification_command}")
        extra = len(unmet_rows) - MAX_LISTED_ROWS
        if extra > 0:
            lines.append(f"- ... and {extra} more")
    if regressions:
        lines.append(
            "Tests that passed before your edits and fail now - these were "
            "green when you arrived:"
        )
        for name in regressions[:MAX_LISTED_ROWS]:
            lines.append(f"- {name}")
    return "\n".join(lines)
