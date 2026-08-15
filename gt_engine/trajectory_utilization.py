"""Deterministic, semantic accounting for provider-visible GT evidence.

The historical ``anchor_followed`` flag is intentionally retained for
backwards compatibility, but it is only an immediate first-command string
match.  This module records the stronger question that can be answered from a
trajectory: did a typed model action target the evidence anchor in the same
response or later before the evidence became stale?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from gt_engine.preflight import ActionOperation, ProposedAction


class SemanticUse(StrEnum):
    PENDING = "pending"
    SAME_RESPONSE = "same_response"
    DEFERRED = "deferred"
    STALE_SOURCE = "stale_source"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    classification: SemanticUse
    call: int
    action_id: str = ""
    action_index: int = -1
    distance_actions: int = 0
    matched_paths: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "call": self.call,
            "action_id": self.action_id,
            "action_index": self.action_index,
            "distance_actions": self.distance_actions,
            "matched_paths": list(self.matched_paths),
            "reason_codes": list(self.reason_codes),
        }


def _normal_path(value: str) -> str:
    value = str(value or "").strip().strip("'\"").replace("\\", "/")
    if value.startswith("/app/"):
        value = value[5:]
    elif value.startswith("./"):
        value = value[2:]
    return value.rstrip("/")


def _anchor_paths(delivery: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for anchor in delivery.get("claim_anchors") or ():
        text = str(anchor or "").strip()
        if text and " " not in text:
            values.append(text.split(":", 1)[0])
    for fact in delivery.get("facts") or ():
        if isinstance(fact, dict):
            path = str(fact.get("path") or "").strip()
            if path:
                values.append(path)
    return tuple(dict.fromkeys(p for p in (_normal_path(v) for v in values) if p))


def _anchor_symbols(delivery: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for fact in delivery.get("facts") or ():
        if isinstance(fact, dict) and fact.get("symbol"):
            values.append(str(fact["symbol"]).strip())
    for anchor in delivery.get("claim_anchors") or ():
        text = str(anchor or "")
        if ":" in text and " " not in text:
            values.append(text.rsplit(":", 1)[-1].strip())
    return tuple(dict.fromkeys(v for v in values if v))


def _target_paths(action: ProposedAction) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for path in (_normal_path(target.path) for target in action.targets)
            if path
        )
    )


def _known_operation(action: ProposedAction) -> bool:
    return action.operation in {
        ActionOperation.READ,
        ActionOperation.SEARCH,
        ActionOperation.EDIT,
        ActionOperation.CREATE,
        ActionOperation.DELETE,
        ActionOperation.VALIDATE,
        ActionOperation.SUBMIT,
    }


def match_delivery_actions(
    delivery: dict[str, Any],
    actions: tuple[ProposedAction, ...],
    *,
    delivery_call: int,
    observed_call: int,
    source_revision: str,
    action_offset: int = 0,
) -> SemanticMatch | None:
    """Match evidence to typed actions without declaring internal model use.

    A path target is the high-confidence signal.  Symbol-only matches are
    accepted only for a known operation and are explicitly marked as a
    weaker reason.  No raw diagnostic or heredoc text is scanned into a
    target.
    """

    # ``revision`` on legacy guidance rows is the workspace revision.  Semantic
    # utilization is source-bound: cache/pyc/tool-output changes must not make a
    # still-valid source fact look stale.  New rows carry ``source_revision``;
    # retain the workspace fallback only for old archived receipts that do not.
    delivery_source_revision = str(
        delivery.get("source_revision")
        or delivery.get("revision")
        or ""
    )
    if observed_call > delivery_call and source_revision != delivery_source_revision:
        return SemanticMatch(
            SemanticUse.STALE_SOURCE,
            observed_call,
            reason_codes=("source_revision_changed_before_use",),
        )
    paths = _anchor_paths(delivery)
    symbols = _anchor_symbols(delivery)
    for index, action in enumerate(actions):
        targets = _target_paths(action)
        matched_paths = tuple(
            path
            for path in targets
            if any(path == anchor or path.endswith("/" + anchor) for anchor in paths)
        )
        if matched_paths and _known_operation(action):
            classification = (
                SemanticUse.SAME_RESPONSE
                if observed_call == delivery_call
                else SemanticUse.DEFERRED
            )
            return SemanticMatch(
                classification,
                observed_call,
                action.action_id,
                action.batch_index,
                action_offset + index,
                matched_paths,
                ("typed_target_path", action.operation.value),
            )
        if symbols and _known_operation(action):
            command = str(action.raw_command or "")
            matched_symbols = tuple(
                symbol
                for symbol in symbols
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", command)
            )
            if matched_symbols:
                classification = (
                    SemanticUse.SAME_RESPONSE
                    if observed_call == delivery_call
                    else SemanticUse.DEFERRED
                )
                return SemanticMatch(
                    classification,
                    observed_call,
                    action.action_id,
                    action.batch_index,
                    action_offset + index,
                    (),
                    ("typed_operation_symbol_reference", action.operation.value),
                )
    return None


class SemanticUtilizationTracker:
    """Bounded tracker for delivery-to-action semantic alignment."""

    def __init__(self, *, max_calls: int = 5, max_actions: int = 10) -> None:
        self.max_calls = max(1, int(max_calls))
        self.max_actions = max(1, int(max_actions))
        self._active: list[dict[str, Any]] = []
        self._completed: list[dict[str, Any]] = []

    def register(self, delivery: dict[str, Any], *, call: int, source_revision: str) -> None:
        delivery.setdefault("semantic_utilization", SemanticUse.PENDING.value)
        delivery.setdefault("semantic_use_call", None)
        delivery.setdefault("semantic_use_action_id", "")
        delivery.setdefault("semantic_use_action_index", -1)
        delivery.setdefault("semantic_use_distance_actions", 0)
        delivery.setdefault("semantic_use_matched_paths", [])
        delivery.setdefault("semantic_use_reason_codes", [])
        delivery.setdefault("semantic_use_window_calls", 0)
        delivery.setdefault("semantic_use_window_actions", 0)
        self._active.append(
            {
                "delivery": delivery,
                "call": int(call),
                "source_revision": str(source_revision),
            }
        )

    def observe(
        self,
        *,
        call: int,
        actions: tuple[ProposedAction, ...],
        source_revision: str,
    ) -> None:
        remaining: list[dict[str, Any]] = []
        for item in self._active:
            delivery = item["delivery"]
            delivery_call = int(item["call"])
            if call < delivery_call:
                remaining.append(item)
                continue
            delivery["semantic_use_window_calls"] += 1
            delivery["semantic_use_window_actions"] += len(actions)
            match = match_delivery_actions(
                delivery,
                actions,
                delivery_call=delivery_call,
                observed_call=call,
                source_revision=source_revision,
                action_offset=0,
            )
            if match is not None:
                delivery["semantic_utilization"] = match.classification.value
                delivery["semantic_use_call"] = match.call
                delivery["semantic_use_action_id"] = match.action_id
                delivery["semantic_use_action_index"] = match.action_index
                delivery["semantic_use_distance_actions"] = match.distance_actions
                delivery["semantic_use_matched_paths"] = list(match.matched_paths)
                delivery["semantic_use_reason_codes"] = list(match.reason_codes)
                self._completed.append(delivery)
                continue
            if (
                call - delivery_call + 1 >= self.max_calls
                or delivery["semantic_use_window_actions"] >= self.max_actions
            ):
                delivery["semantic_utilization"] = SemanticUse.NO_MATCH.value
                delivery["semantic_use_reason_codes"] = ["bounded_window_expired"]
                self._completed.append(delivery)
                continue
            remaining.append(item)
        self._active = remaining

    def finalize(self) -> None:
        for item in self._active:
            delivery = item["delivery"]
            delivery["semantic_utilization"] = SemanticUse.NO_MATCH.value
            delivery["semantic_use_reason_codes"] = ["task_terminated_before_match"]
            self._completed.append(delivery)
        self._active = []

    def summary(self) -> dict[str, int]:
        counts = {item.value: 0 for item in SemanticUse}
        for delivery in self._completed:
            value = str(delivery.get("semantic_utilization") or SemanticUse.PENDING.value)
            counts[value] = counts.get(value, 0) + 1
        counts["matched"] = counts[SemanticUse.SAME_RESPONSE.value] + counts[
            SemanticUse.DEFERRED.value
        ]
        counts["deliveries"] = len(self._completed)
        return counts


__all__ = ["SemanticMatch", "SemanticUse", "SemanticUtilizationTracker", "match_delivery_actions"]
