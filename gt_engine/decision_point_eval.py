"""Fail-closed validation for paired GroundTruth decision-point captures.

This module does not call a model and does not infer hidden reasoning. It only
proves that an opt-in replay row contains a first-visible control/treatment
pair whose sole provider-visible difference is the recorded GT payload.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DecisionPointValidity(StrEnum):
    VALID = "valid"
    MISSING_CONTROL = "missing_control"
    MISSING_TREATMENT = "missing_treatment"
    MISSING_RESPONSE = "missing_response"
    MISSING_TOOLS = "missing_tools"
    MISSING_INTERVENTION = "missing_intervention"
    PRIOR_GT_VISIBLE = "prior_gt_visible"
    STALE_EVIDENCE = "stale_evidence"
    LATE_EVIDENCE = "late_evidence"
    INVALID_MESSAGE_INDEX = "invalid_message_index"
    NON_GT_BYTES_DIFFER = "non_gt_bytes_differ"


@dataclass(frozen=True, slots=True)
class DecisionPointCase:
    task_id: str
    call: int
    model_name: str
    temperature: float
    source_revision: str
    workspace_revision: str
    control_provider_messages: tuple[dict[str, Any], ...]
    treatment_provider_messages: tuple[dict[str, Any], ...]
    treatment_response: dict[str, Any]
    provider_tools: tuple[dict[str, Any], ...]
    payload: str
    message_index: int
    selected_contribution_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionPointValidation:
    validity: DecisionPointValidity
    reason: str
    case: DecisionPointCase | None = None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _expected_treatment(
    control: list[dict[str, Any]], *, payload: str, message_index: int
) -> list[dict[str, Any]] | None:
    expected = deepcopy(control)
    if message_index < 0 or message_index > len(expected):
        return None
    if message_index == len(expected):
        expected.append({"role": "user", "content": payload})
        return expected
    content = expected[message_index].get("content")
    if not isinstance(content, str):
        return None
    expected[message_index]["content"] = content + "\n\n" + payload
    return expected


def validate_decision_point_row(
    row: dict[str, Any], *, task_id: str
) -> DecisionPointValidation:
    control = row.get("control_provider_messages")
    treatment = row.get("provider_messages")
    response = row.get("response")
    provider_tools = row.get("provider_tools")
    intervention = row.get("intervention")
    if not isinstance(control, list):
        return DecisionPointValidation(
            DecisionPointValidity.MISSING_CONTROL, "exact control request absent"
        )
    if not isinstance(treatment, list):
        return DecisionPointValidation(
            DecisionPointValidity.MISSING_TREATMENT, "exact treatment request absent"
        )
    if not isinstance(response, dict):
        return DecisionPointValidation(
            DecisionPointValidity.MISSING_RESPONSE, "treatment response absent"
        )
    if not isinstance(provider_tools, list) or not provider_tools:
        return DecisionPointValidation(
            DecisionPointValidity.MISSING_TOOLS,
            "exact provider tool schema absent",
        )
    if not isinstance(intervention, dict) or not str(intervention.get("payload") or ""):
        return DecisionPointValidation(
            DecisionPointValidity.MISSING_INTERVENTION,
            "compiled GT intervention absent",
        )
    if int(intervention.get("prior_visible_gt_count") or 0) != 0:
        return DecisionPointValidation(
            DecisionPointValidity.PRIOR_GT_VISIBLE,
            "case is not the first visible GT intervention",
        )
    source_revision = str(row.get("source_revision") or "")
    if str(intervention.get("source_revision") or "") != source_revision:
        return DecisionPointValidation(
            DecisionPointValidity.STALE_EVIDENCE,
            "intervention source revision does not match request",
        )
    call = int(row.get("call") or 0)
    if int(intervention.get("eligible_call") or 0) != call:
        return DecisionPointValidation(
            DecisionPointValidity.LATE_EVIDENCE,
            "intervention was not delivered in its eligible call",
        )
    try:
        message_index = int(intervention["message_index"])
    except (KeyError, TypeError, ValueError):
        return DecisionPointValidation(
            DecisionPointValidity.INVALID_MESSAGE_INDEX,
            "provider message index is absent or invalid",
        )
    payload = str(intervention["payload"])
    expected = _expected_treatment(
        control, payload=payload, message_index=message_index
    )
    if expected is None:
        return DecisionPointValidation(
            DecisionPointValidity.INVALID_MESSAGE_INDEX,
            "payload cannot be applied at the recorded message index",
        )
    if _canonical(expected) != _canonical(treatment):
        return DecisionPointValidation(
            DecisionPointValidity.NON_GT_BYTES_DIFFER,
            "control and treatment differ beyond the recorded GT payload",
        )
    sampling = row.get("sampling") or {}
    case = DecisionPointCase(
        task_id=str(task_id),
        call=call,
        model_name=str(row.get("model_name") or ""),
        temperature=float(sampling.get("temperature") or 0.0),
        source_revision=source_revision,
        workspace_revision=str(row.get("workspace_revision") or ""),
        control_provider_messages=tuple(deepcopy(control)),
        treatment_provider_messages=tuple(deepcopy(treatment)),
        treatment_response=deepcopy(response),
        provider_tools=tuple(deepcopy(provider_tools)),
        payload=payload,
        message_index=message_index,
        selected_contribution_ids=tuple(
            str(item)
            for item in intervention.get("selected_contribution_ids") or ()
        ),
    )
    return DecisionPointValidation(DecisionPointValidity.VALID, "valid", case)


__all__ = [
    "DecisionPointCase",
    "DecisionPointValidation",
    "DecisionPointValidity",
    "validate_decision_point_row",
]
