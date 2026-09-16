"""Shared model-visible delivery budgets for every Groundtruth prompt lane."""

from __future__ import annotations

import re

# Split so the prompt-kind domain needs no hand-typed exception. The merged
# table's key space is a union of one LANE name and two KIND names, so deriving
# the kinds from it required subtracting "sealed" - one literal doing the work
# a type boundary should, and correct only while the table stays keyed as
# "exactly one lane plus every prompt kind", an invariant stated and tested
# nowhere. Giving the sealed lane a per-kind limit, which its single limit
# currently covers five kinds' worth of, would have silently promoted each of
# those kinds into the prompt domain and stopped two raising gates rejecting
# them.
SEALED_DELIVERY_BYTE_LIMIT = 1_400
PROMPT_DELIVERY_BYTE_LIMITS = {
    "context_contract": 2_000,
    "context_delta": 1_400,
    # A refused gt-plan request is a fact the agent cannot learn any other
    # way; bounded like a context delta because it rides the same tail.
    "plan_request_rejected": 1_400,
}
DELIVERY_BYTE_LIMITS = {
    "sealed": SEALED_DELIVERY_BYTE_LIMIT,
    **PROMPT_DELIVERY_BYTE_LIMITS,
}
PROMPT_CONTEXT_BYTE_LIMIT = DELIVERY_BYTE_LIMITS["context_delta"]
# The kinds a prompt-lane delivery may carry, derived from the budget table
# rather than restated. The same pair was hand-copied in four places - the
# lookup below, both prompt-kind checks in gt_harness/runtime_receipts.py, and
# the two-valued expression in gt_session that produces it - and two of those
# copies RAISE. All four are correct today; none was defended, so adding a
# third prompt kind would have lost runs with nothing going red. That is the
# same defect class as the refusal allow-list, caught before it was wrong
# rather than after.
PROMPT_DELIVERY_KINDS = frozenset(PROMPT_DELIVERY_BYTE_LIMITS)
TOTAL_DELIVERY_BYTE_LIMIT = 9_600
# This is a pathological re-offer-loop backstop, not a context dose policy.
# Legitimate distinct deliveries are controlled by content identity and bytes.
MAX_TASK_DELIVERIES = 24
# The legacy constant above is retained for historical receipt readers only.
MAX_BOUNDARY_CLAIMS = 4
# Re-localization is permitted when the ranked content changed (the agent's
# searches move the information need), but distinct localizations stay capped
# per task so a drifting ranking cannot become a delivery loop. Because the
# delivery identity is the payload hash, the fire-once rule already dedups
# identical content and this cap only ever sees NOVEL payloads; the pathology
# it must stop is same-top churn - a jittering ranking re-emitting a slightly
# different tail under the same top-ranked file each iteration. A localization
# whose top-ranked target was never delivered before is a genuine information-
# need shift, so it bypasses the soft cap; the hard bound below keeps a
# pathological rotating-top ranking from becoming an unbounded delivery loop.
MAX_LOCALIZATION_DELIVERIES = 3
MAX_LOCALIZATION_HARD_DELIVERIES = 6

# Every reason the runtime can write to a delivery_refused row. The authority
# is here, beside the ceilings the reasons name, and the harness imports it
# rather than keeping its own copy.
#
# It used to be two hand-written copies in gt_harness/runtime_receipts.py, and
# they were stale in BOTH directions: they omitted cochange_task_ceiling, which
# the runtime does emit, and admitted three task_delivery_* reasons that
# nothing emits. Since the harness RAISES on an unlisted reason rather than
# skipping it, a run that legitimately hit the co-change ceiling failed receipt
# construction outright or failed acceptance - a correct refusal by GT losing
# the run. A list wrong in both directions was never derived from the code; it
# was an out-of-date copy of a design note, and the dead entries are what made
# the missing one hard to see.
# The events that record GT declining to deliver. Declared here, beside the
# refusal REASONS, because a consumer asking "is this a refusal" must key on an
# authority rather than guess at vocabulary.
#
# The first version of the feature accounting keyed on the event name containing
# "refus" or "abstain". It returned the right answer on the only run available -
# for the wrong reason. No event name contains "abstain" at all, so half the
# test never fired; and a probe on "invalid" matches graph_invalidated, which is
# not a refusal. A string-shaped key on an open set keeps producing that class
# of answer, and a future ..._declined or ..._withheld would be invisible.
REFUSAL_EVENTS = frozenset({
    "decision_context_unit_refused",
    "delivery_refused",
    # prepared_deliveries_discarded carries ``delivery_ids`` (a list), not a
    # per-row delivery_identity, so identity-shaped consumers must special-case
    # it; it still belongs in this closed vocabulary of declined deliveries.
    "prepared_deliveries_discarded",
})

DELIVERY_REFUSAL_REASONS = frozenset({
    "boundary_claim_ceiling",
    "cochange_task_ceiling",
    "delivery_byte_ceiling",
    "duplicate_delivery_identity",
    "localization_fire_once",
    "localization_task_ceiling",
    "request_delivery_byte_ceiling",
})

# Refusals whose basis is the candidate's POSITION inside one decision window.
# The boundary scan re-runs as evidence streams in, and a later scan can
# legitimately admit the same payload at a lower ordinal when an earlier
# sibling drops out (run 34925475946, gitingest-94: a cochange unit refused at
# candidate_ordinal 5 twice, then committed at delivery_ordinal 4 after a
# prompt-lane candidate disappeared). The window invariants are proven on the
# committed set by _validate_delivery_boundaries, so a same-identity delivery
# below the refused position is a re-admission, not a rescinded refusal.
# Task-scoped and payload-intrinsic refusals are NOT in this set: a payload
# refused for delivery_byte_ceiling is still too big at any ordinal, and a
# cochange/localization task ceiling does not decrease inside one window.
WINDOW_POSITIONAL_REFUSAL_REASONS = frozenset({
    "boundary_claim_ceiling",
    "request_delivery_byte_ceiling",
})


def compact_localization(value: str, limit: int = 1_400) -> str:
    """Drop whole ranked location items; never slice a factual statement.

    Unknown multiline renderer formats must fit intact or abstain. Only the
    ranked-line localization format has a certified independent item boundary.
    """
    if len(value.encode("utf-8")) <= limit:
        return value
    lines = value.splitlines()
    if (not lines or lines[0] != "[GT_EVIDENCE:localization]"
            or not all(re.match(r"^\S+:\d+(?:\s|$)", line) for line in lines[1:])):
        return ""
    selected = [lines[0]]
    for line in lines[1:]:
        candidate = "\n".join([*selected, line])
        if len(candidate.encode("utf-8")) > limit:
            break
        selected.append(line)
    if len(selected) <= 1:
        return ""
    dropped = len(lines) - len(selected)
    if dropped:
        # Honest selection bound: the withheld count is model-visible, and is
        # worth more than the lowest-ranked item it may displace.
        note = f"- {dropped} further ranked location(s) withheld by byte budget"
        while len(selected) > 1 and len(
            "\n".join([*selected, note]).encode("utf-8")
        ) > limit:
            selected.pop()
            dropped += 1
            note = (
                f"- {dropped} further ranked location(s) withheld by byte budget"
            )
        if len(selected) > 1 and len(
            "\n".join([*selected, note]).encode("utf-8")
        ) <= limit:
            selected.append(note)
    return "\n".join(selected) if len(selected) > 1 else ""


def delivery_byte_limit(*, lane: str, kind: str) -> int:
    """Return the immutable cap for the actual delivery lane and content kind."""

    if lane == "sealed":
        return DELIVERY_BYTE_LIMITS["sealed"]
    if lane == "prompt" and kind in PROMPT_DELIVERY_KINDS:
        return DELIVERY_BYTE_LIMITS[kind]
    raise ValueError(f"unsupported delivery budget lane/kind: {lane}/{kind}")


__all__ = [
    "DELIVERY_BYTE_LIMITS",
    "MAX_TASK_DELIVERIES",
    "PROMPT_CONTEXT_BYTE_LIMIT",
    "TOTAL_DELIVERY_BYTE_LIMIT",
    "delivery_byte_limit",
]
