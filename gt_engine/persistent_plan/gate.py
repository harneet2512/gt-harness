"""The completion gate: refuse a submission that has not shown its work.

Twelve of twenty tasks in the measured run ended ``submitted_unverified`` --
the agent submitted while obligations had no evidence at all. That single fact
is what failed the attestation. It is not a defect in the gate: the benchmark
runs ``--gt-mode advisory``, so ``GTSession.can_enforce`` is False and the
existing enforcing gate is simply unreachable.

This module supplies the decision for a plan-scoped gate that works in advisory
mode. The contract it enforces is model-agnostic: a submission over blocking
evidence is a certain attestation failure wherever it ships, so shipping it is
never better than refusing it. Earlier revisions conceded on budget and on
stalled refusals, reasoning that a dirty submit still had a chance to score --
both free runs proved the opposite: union-alpha submitted inside the reserve
with rows unmet and the run was scored a product failure either way, while the
concession let the model off the contract it was told to satisfy.

* It refuses while blocking evidence exists -- unconditionally. Budget and
  stall facts are journaled as evidence of the pressure the run was under;
  they no longer buy an accept.
* It never blocks on its own ignorance. An unmet row blocks; an unknown
  baseline, a failed probe or a missing plan does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Inside this reserve the drain and baseline recheck are skipped -- a check
# that cannot finish before the deadline cannot change the decision. The
# refusal itself never checks the reserve: a submit over blocking evidence
# fails attestation whether it ships early or late.
MIN_REMAINING_SECONDS = 600.0
MIN_REMAINING_STEPS = 20
MAX_LISTED_ROWS = 12

# Journaled evidence only: how many consecutive refusals passed without the
# agent proving a single new row. Refusing exactly once made the gate a
# formality (run 34374028796, task claude-code: refused with rows unmet,
# accepted the next attempt 34 seconds later with 4,260 seconds and 142 steps
# still available and the same rows still unproven -- seventy-one minutes went
# unused). The count still records that history, but the stall limit no longer
# concedes: a model that ignores the gate does not earn the right to ship the
# defective submission the gate exists to stop.
MAX_REFUSALS_WITHOUT_PROGRESS = 3


@dataclass
class GateDecision:
    accepted: bool
    reason: str
    unmet_rows: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    unresolved_predicates: tuple[str, ...] = ()
    remaining_seconds: float = 0.0
    remaining_steps: int | None = 0
    refusals: int = 0
    directive: str = ""
    escaped: str = ""
    baseline_status: str = ""
    completion_proven: bool = False
    details: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "unmet_rows": list(self.unmet_rows),
            "regressions": list(self.regressions),
            "unresolved_predicates": list(self.unresolved_predicates),
            "remaining_seconds": round(self.remaining_seconds, 1),
            "remaining_steps": self.remaining_steps,
            "refusals": self.refusals,
            "escaped": self.escaped,
            "baseline_status": self.baseline_status,
            "completion_proven": self.completion_proven,
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
    unresolved_predicates: tuple[str, ...] = (),
    predicate_labels: dict[str, str] | None = None,
) -> GateDecision:
    """The whole gate policy, as a pure function of the facts.

    Kept free of session and adapter objects so the policy can be read and
    tested on its own; the caller supplies the numbers and applies the result.
    """
    row_ids = {row.row_id for row in getattr(plan, "rows", ())}
    states = row_states or {}
    verified_states = {"CHECK_PASSED", "PROVEN"}
    if not row_ids:
        completion_assessment = "no_plan_rows"
    elif row_states is None:
        # The caller supplied no row ledger, so completion is genuinely
        # unassessed -- the only honest reading here.
        completion_assessment = "not_established"
    elif all(states.get(key) in verified_states for key in row_ids):
        completion_assessment = "all_rows_verified"
    else:
        completion_assessment = "rows_unverified"
    details = {
        "layout_schema": "gt.plan_gate_evidence.v1",
        "completion_assessment": completion_assessment,
        "baseline_assessment": baseline_status or "unavailable",
        "mapping_assessment": "available" if predicate_mapped_rows is not None else "unavailable",
        "predicate_mapped_rows": sorted(row_ids & set(predicate_mapped_rows or ())),
        "unmapped_rows": sorted(row_ids - set(predicate_mapped_rows)) if predicate_mapped_rows is not None else [],
        "check_passed_rows": sorted(key for key in row_ids if states.get(key) == "CHECK_PASSED"),
        "check_failed_rows": sorted(key for key in row_ids if states.get(key) == "CHECK_FAILED"),
        "deferred_rows": sorted(key for key in row_ids if states.get(key) == "DEFERRED"),
        "proven_rows": sorted(key for key in row_ids if states.get(key) == "PROVEN"),
        "unverified_rows": sorted(key for key in row_ids if states.get(key, "UNVERIFIED") == "UNVERIFIED"),
        # RED predicates bound to no plan row -- the channel the row census
        # cannot see, journaled as ids under their own name so an audit can
        # tell this refusal from an unmet-row one.
        "unresolved_predicates": sorted(unresolved_predicates),
    }
    # completion_proven was hardcoded False: gate-one journaled
    # submitted_verified beside 28 UNVERIFIED rows and this field could not
    # contradict it because it never said anything else either. It is proven
    # only when the row ledger says every row verified -- and a regression is
    # evidence against completion even when every plan row passed.
    # Smoke20 (run 34801009507, bandit-interprocedural-taint-checks) showed the
    # third leg the receipt needs: the gate refused twice, the agent proved
    # every row, completion_proven read true -- and the verifier failed the
    # submission. The baseline was ``no_tests_observed``, so the bound checks
    # were ambient checks, never the graded fail-to-pass suite. Rows verified
    # against a baseline that cannot see the grading signal are verification
    # against a proxy, not proof of the contract -- the receipt must say so.
    # Enumerating blind statuses leaks: "unknown" (identity conservation never
    # established), "incomplete", "new_failures_unattributed", "timeout",
    # "not_attempted" all shipped sighted while saying nothing. A baseline is
    # sighted only when the recheck produced a conservation VERDICT — intact
    # or regressed; regressed still blocks through `regressions` above.
    _baseline_blind = baseline_status not in {"intact", "regressed"}
    completion_proven = (
        bool(row_ids)
        and all(states.get(key) in verified_states for key in row_ids)
        and not regressions
        and not unresolved_predicates
        and not _baseline_blind
    )
    common = {
        "remaining_seconds": remaining_seconds,
        "remaining_steps": remaining_steps,
        "refusals": refusals,
        "baseline_status": baseline_status,
        "completion_proven": completion_proven,
        "details": details,
    }
    if plan is None or not getattr(plan, "rows", ()):
        # No plan is not no evidence. Run 35168421439: the plan bootstrap
        # died on a provider timeout (``persistent_plan_unavailable``), this
        # early return shipped the submit over 3 live RED predicates, and no
        # ``plan_gate_decision`` was ever journaled. Row ids are opaque
        # without the plan that names them, but a regression node id and a
        # RED predicate's obligation are self-describing -- they still
        # block. ``no_plan`` means no row census, never no gate.
        blocking = tuple(regressions) + tuple(unresolved_predicates)
        if not blocking:
            return GateDecision(accepted=True, reason="no_plan", **common)
    else:
        # A RED predicate bound to no plan row is blocking evidence too: the
        # row census can only see what a row's mapping names, and nothing
        # binds a failing contract obligation to a row it was never linked to.
        blocking = tuple(unmet_rows) + tuple(regressions) + tuple(unresolved_predicates)
        if not blocking:
            return GateDecision(accepted=True, reason="no_blocking_evidence", **common)
    # A submission over blocking evidence is refused, unconditionally. The
    # escapes that used to ship it anyway (stalled refusals, near-deadline
    # budget) are journaled below as evidence of the pressure the run was
    # under, not as concessions: a dirty submit fails the same attestation a
    # refused one does, and only refusal leaves the model room to comply.
    allowed, escape = budget_allows_refusal(remaining_seconds, remaining_steps)
    stalled = refusals if refusals_without_progress is None else refusals_without_progress
    details["budget_allows_refusal"] = allowed
    details["escape_available"] = escape
    details["stalled_refusals"] = stalled
    return GateDecision(
        accepted=False,
        reason=(
            "unmet_plan_rows" if unmet_rows
            else "baseline_regression" if regressions
            else "unresolved_predicates"
        ),
        unmet_rows=unmet_rows,
        regressions=regressions,
        unresolved_predicates=unresolved_predicates,
        directive=render_directive(
            plan, unmet_rows, regressions, unresolved_predicates, predicate_labels
        ),
        **common,
    )


def render_directive(
    plan,
    unmet_rows: tuple[str, ...],
    regressions: tuple[str, ...],
    unresolved_predicates: tuple[str, ...] = (),
    predicate_labels: dict[str, str] | None = None,
) -> str:
    """What the agent is told when the gate refuses.

    Names outstanding rows and proposed checks without asserting that executing
    a check proves its requirement. The refusal stands while evidence is
    missing -- there is no retry count that discharges it.
    """
    if plan is None or not getattr(plan, "rows", ()):
        lines = [
            "GT PLAN GATE: submission was not executed. No plan was built "
            "for this run, but blocking evidence still stands. You may run "
            "any command, edit any file, or disagree. A later submission is "
            "reassessed against current evidence, and will not be executed "
            "while this evidence stands.",
        ]
    else:
        lines = [
            "GT PLAN GATE: submission was not executed. The plan built before the "
            "first edit still has requirements with no evidence. You may run any "
            "command, edit any file, or disagree. A later submission is reassessed "
            "against current evidence, and will not be executed while these "
            "requirements stay unproven.",
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
        extra = len(regressions) - MAX_LISTED_ROWS
        if extra > 0:
            lines.append(f"- ... and {extra} more")
    if unresolved_predicates:
        labels = predicate_labels or {}
        lines.append(
            "Requirements with current failing evidence that no plan row "
            "tracks - a failing check was matched to them directly:"
        )
        for predicate_id in unresolved_predicates[:MAX_LISTED_ROWS]:
            lines.append(f"- {labels.get(predicate_id) or predicate_id}")
        extra = len(unresolved_predicates) - MAX_LISTED_ROWS
        if extra > 0:
            lines.append(f"- ... and {extra} more")
    return "\n".join(lines)
