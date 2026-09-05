"""Shared model-visible delivery budgets for every Groundtruth prompt lane."""

from __future__ import annotations

import re

DELIVERY_BYTE_LIMITS = {
    "sealed": 1_400,
    "context_contract": 2_000,
    "context_delta": 1_400,
}
PROMPT_CONTEXT_BYTE_LIMIT = DELIVERY_BYTE_LIMITS["context_delta"]
TOTAL_DELIVERY_BYTE_LIMIT = 9_600
# This is a pathological re-offer-loop backstop, not a context dose policy.
# Legitimate distinct deliveries are controlled by content identity and bytes.
MAX_TASK_DELIVERIES = 24
# The legacy constant above is retained for historical receipt readers only.
MAX_BOUNDARY_CLAIMS = 4


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
    if lane == "prompt" and kind in {"context_contract", "context_delta"}:
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
