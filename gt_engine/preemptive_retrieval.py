"""Additive, fail-open delivery of a bounded retrieval frame.

This module owns only the provider-view seam.  Retrieval and legacy feature
production remain separate so enabling the frame cannot rewrite or suppress an
existing GT payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PreemptiveFrameStatus(StrEnum):
    DISABLED = "disabled"
    DELIVERED = "delivered"
    ABSTAINED = "abstained"


@dataclass(frozen=True, slots=True)
class PreemptiveFrame:
    frame_id: str
    text: str
    source_revision: str
    eligible_call: int
    evidence_action: int
    evidence_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreemptiveFrameCompilation:
    provider_messages: tuple[dict[str, Any], ...]
    status: PreemptiveFrameStatus
    receipt: dict[str, Any]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _inject(
    messages: list[dict[str, Any]], payload: str
) -> tuple[list[dict[str, Any]], list[int]]:
    prepared = [dict(item) for item in messages]
    for index in range(len(prepared) - 1, -1, -1):
        if prepared[index].get("role") != "tool":
            continue
        prepared[index]["content"] = (
            str(prepared[index].get("content") or "") + "\n\n" + payload
        )
        return prepared, [index]
    prepared.append({"role": "user", "content": payload})
    return prepared, [len(prepared) - 1]


def compile_preemptive_frame(
    messages: list[dict[str, Any]],
    *,
    frame: PreemptiveFrame | None,
    legacy_payload: str,
    enabled: bool,
    current_source_revision: str,
    current_call: int,
    budget_chars: int,
    now_ms: float,
    deadline_ms: float | None = None,
    model_query_count: int = 0,
    agent_action_count: int = 0,
) -> PreemptiveFrameCompilation:
    """Compile a frame into the current provider view without another turn.

    Any invalid frame abstains from the whole additive transformation.  The
    caller's existing legacy path remains authoritative and may deliver its
    payload independently.
    """

    original = tuple(dict(item) for item in messages)
    reason = ""
    if not enabled:
        status = PreemptiveFrameStatus.DISABLED
        reason = "preemptive_retrieval_disabled"
    elif frame is None or not frame.text.strip():
        status = PreemptiveFrameStatus.ABSTAINED
        reason = "no_preemptive_evidence"
    elif frame.source_revision != current_source_revision:
        status = PreemptiveFrameStatus.ABSTAINED
        reason = "stale_source_revision"
    elif deadline_ms is not None and now_ms > deadline_ms:
        status = PreemptiveFrameStatus.ABSTAINED
        reason = "preemptive_frame_timeout"
    elif frame.eligible_call != current_call:
        status = PreemptiveFrameStatus.ABSTAINED
        reason = "ineligible_call"
    elif any(frame.text in str(item.get("content") or "") for item in messages):
        status = PreemptiveFrameStatus.ABSTAINED
        reason = "duplicate_preemptive_frame"
    else:
        payload_parts = [frame.text.strip()]
        if legacy_payload.strip() and legacy_payload.strip() != frame.text.strip():
            payload_parts.append(legacy_payload.strip())
        payload = "\n\n".join(payload_parts)
        if len(payload) > max(0, int(budget_chars)):
            status = PreemptiveFrameStatus.ABSTAINED
            reason = "preemptive_frame_over_budget"
        else:
            prepared, indices = _inject(messages, payload)
            status = PreemptiveFrameStatus.DELIVERED
            receipt = {
                "frame_id": frame.frame_id,
                "status": status.value,
                "reason_code": "delivered",
                "source_revision": frame.source_revision,
                "evidence_action": frame.evidence_action,
                "eligible_call": frame.eligible_call,
                "prepared_call": current_call,
                "first_eligible_request": current_call == frame.eligible_call,
                "delivered_before_model_query": True,
                "one_step_late": current_call != frame.eligible_call,
                "predictive": frame.evidence_action > agent_action_count,
                "provider_message_indices": indices,
                "request_payload_sha256": hashlib.sha256(_canonical(prepared)).hexdigest(),
                "evidence_ids": list(frame.evidence_ids),
                "claim_ids": list(frame.claim_ids),
                "frame_chars": len(frame.text),
                "legacy_payload_chars": len(legacy_payload),
                "model_query_count_before": model_query_count,
                "model_query_count_after": model_query_count,
                "agent_action_count_before": agent_action_count,
                "agent_action_count_after": agent_action_count,
                "extra_model_calls": 0,
                "extra_agent_actions": 0,
            }
            return PreemptiveFrameCompilation(tuple(prepared), status, receipt)

    receipt = {
        "frame_id": "" if frame is None else frame.frame_id,
        "status": status.value,
        "reason_code": reason,
        "source_revision": "" if frame is None else frame.source_revision,
        "evidence_action": -1 if frame is None else frame.evidence_action,
        "eligible_call": 0 if frame is None else frame.eligible_call,
        "prepared_call": current_call,
        "first_eligible_request": False,
        "delivered_before_model_query": False,
        "one_step_late": False,
        "predictive": False,
        "provider_message_indices": [],
        "request_payload_sha256": "",
        "evidence_ids": [] if frame is None else list(frame.evidence_ids),
        "claim_ids": [] if frame is None else list(frame.claim_ids),
        "frame_chars": 0 if frame is None else len(frame.text),
        "legacy_payload_chars": len(legacy_payload),
        "model_query_count_before": model_query_count,
        "model_query_count_after": model_query_count,
        "agent_action_count_before": agent_action_count,
        "agent_action_count_after": agent_action_count,
        "extra_model_calls": 0,
        "extra_agent_actions": 0,
    }
    return PreemptiveFrameCompilation(original, status, receipt)


__all__ = [
    "PreemptiveFrame",
    "PreemptiveFrameCompilation",
    "PreemptiveFrameStatus",
    "compile_preemptive_frame",
]
