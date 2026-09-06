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
DELIVERY_REFUSAL_REASONS = frozenset({
    "boundary_claim_ceiling",
    "cochange_task_ceiling",
    "delivery_byte_ceiling",
    "duplicate_delivery_identity",
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
    return "\n".join(selected) if len(selected) > 1 else ""


def delivery_byte_limit(*, lane: str, kind: str) -> int:
    """Return the immutable cap for the actual delivery lane and content kind."""

    if lane == "sealed":
        return DELIVERY_BYTE_LIMITS["sealed"]
    if lane == "prompt" and kind in PROMPT_DELIVERY_KINDS:
        return DELIVERY_BYTE_LIMITS[kind]
    raise ValueError(f"unsupported delivery budget lane/kind: {lane}/{kind}")


def truncate_utf8(value: str, limit: int) -> str:
    """Return a deterministic valid-UTF-8 prefix no larger than ``limit`` bytes."""

    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


__all__ = [
    "DELIVERY_BYTE_LIMITS",
    "MAX_TASK_DELIVERIES",
    "PROMPT_CONTEXT_BYTE_LIMIT",
    "TOTAL_DELIVERY_BYTE_LIMIT",
    "delivery_byte_limit",
    "truncate_utf8",
]
