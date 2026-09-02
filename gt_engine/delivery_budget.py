"""Shared model-visible delivery budgets for every Groundtruth prompt lane."""

from __future__ import annotations

DELIVERY_BYTE_LIMITS = {"repository_start": 2_000, "repository_update": 1_400}
PROMPT_CONTEXT_BYTE_LIMIT = DELIVERY_BYTE_LIMITS["repository_update"]
TOTAL_DELIVERY_BYTE_LIMIT = 9_600
# This is a pathological re-offer-loop backstop, not a context dose policy.
# Legitimate distinct deliveries are controlled by content identity and bytes.
MAX_TASK_DELIVERIES = 24


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
    "truncate_utf8",
]
