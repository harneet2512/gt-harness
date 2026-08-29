"""Model-agnostic lifecycle accounting for one GT-assisted solver run.

This module does not decide how a provider is called or how a model acts.  It
certifies the host pipeline around that solver: snapshot, graph substrate,
request preparation, dispatch outcome, response outcome, action accounting,
and finalization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from gt_engine.runtime_safety import ActionAccounting


class LifecyclePhase(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    SUBSTRATE = "SUBSTRATE"
    SOLVER = "SOLVER"
    FINALIZATION = "FINALIZATION"


class PhaseStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_STARTED = "NOT_STARTED"


class CallDispatchState(StrEnum):
    PREPARED_NOT_SENT = "PREPARED_NOT_SENT"
    DISPATCHED_RESPONSE_RECEIVED = "DISPATCHED_RESPONSE_RECEIVED"
    DISPATCHED_RESPONSE_ERROR = "DISPATCHED_RESPONSE_ERROR"
    DISPATCHED_INCOMPLETE = "DISPATCHED_INCOMPLETE"
    UNKNOWN = "UNKNOWN"


_DISPATCHED_STATES = frozenset(
    {
        CallDispatchState.DISPATCHED_RESPONSE_RECEIVED,
        CallDispatchState.DISPATCHED_RESPONSE_ERROR,
        CallDispatchState.DISPATCHED_INCOMPLETE,
    }
)


@dataclass(frozen=True, slots=True)
class LifecycleCall:
    sequence: int
    kind: str
    call: int
    state: CallDispatchState
    request_payload_sha256: str = ""
    provider_messages_sha256: str = ""
    provider_barrier_inputs_sha256: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "call": self.call,
            "state": self.state.value,
            "request_payload_sha256": self.request_payload_sha256,
            "provider_messages_sha256": self.provider_messages_sha256,
            "provider_barrier_inputs_sha256": self.provider_barrier_inputs_sha256,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleReceipt:
    phases: tuple[tuple[LifecyclePhase, PhaseStatus], ...]
    calls: tuple[LifecycleCall, ...]
    prepared_calls: int
    dispatched_calls: int
    received_responses: int
    response_errors: int
    not_sent_calls: int
    lifecycle_conservation_valid: bool
    action_conservation_valid: bool
    complete: bool
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.runtime_lifecycle.v1",
            "model_agnostic": True,
            "phases": [
                {"phase": phase.value, "status": status.value}
                for phase, status in self.phases
            ],
            "calls": [item.as_dict() for item in self.calls],
            "prepared_calls": self.prepared_calls,
            "dispatched_calls": self.dispatched_calls,
            "received_responses": self.received_responses,
            "response_errors": self.response_errors,
            "not_sent_calls": self.not_sent_calls,
            "lifecycle_conservation_valid": self.lifecycle_conservation_valid,
            "action_conservation_valid": self.action_conservation_valid,
            "complete": self.complete,
            "reason_codes": list(self.reason_codes),
        }


def _call_state(raw: object) -> CallDispatchState:
    status = str(raw or "").strip().lower()
    if status == "response_received":
        return CallDispatchState.DISPATCHED_RESPONSE_RECEIVED
    if status == "response_error":
        return CallDispatchState.DISPATCHED_RESPONSE_ERROR
    if status == "invoked":
        return CallDispatchState.DISPATCHED_INCOMPLETE
    if status in {"prepared", "prepared_not_sent", "marker_error"}:
        return CallDispatchState.PREPARED_NOT_SENT
    return CallDispatchState.UNKNOWN


def build_runtime_lifecycle_receipt(
    *,
    source_revision: str,
    graph_source_revision: str,
    repository_applicability: str,
    repository_substrate_ready: bool,
    model_call_contexts: Iterable[Mapping[str, Any]],
    action_accounting: ActionAccounting,
    finalization_complete: bool,
) -> RuntimeLifecycleReceipt:
    """Certify lifecycle conservation without depending on a model/provider API."""

    calls = tuple(
        LifecycleCall(
            sequence=position,
            kind=str(row.get("call_kind") or "executor"),
            call=int(row.get("call") or position),
            state=_call_state(row.get("dispatch_status")),
            request_payload_sha256=str(row.get("request_payload_sha256") or ""),
            provider_messages_sha256=str(row.get("provider_messages_sha256") or ""),
            provider_barrier_inputs_sha256=str(
                (
                    row.get("mechanical_completeness_barrier")
                    if isinstance(row.get("mechanical_completeness_barrier"), Mapping)
                    else {}
                ).get("inputs_sha256")
                or ""
            ),
            reason=str(row.get("dispatch_reason") or ""),
        )
        for position, row in enumerate(model_call_contexts, start=1)
    )
    prepared = len(calls)
    dispatched = sum(item.state in _DISPATCHED_STATES for item in calls)
    received = sum(
        item.state is CallDispatchState.DISPATCHED_RESPONSE_RECEIVED for item in calls
    )
    response_errors = sum(
        item.state is CallDispatchState.DISPATCHED_RESPONSE_ERROR for item in calls
    )
    not_sent = sum(
        item.state is CallDispatchState.PREPARED_NOT_SENT for item in calls
    )
    reasons: list[str] = []
    call_identities = tuple((item.kind, item.call) for item in calls)
    if any(item.state is CallDispatchState.UNKNOWN for item in calls):
        reasons.append("unknown_call_dispatch_state")
    if len(set(call_identities)) != len(call_identities):
        reasons.append("duplicate_call_identity")
    for kind in dict.fromkeys(item.kind for item in calls):
        call_numbers = tuple(item.call for item in calls if item.kind == kind)
        if call_numbers != tuple(sorted(call_numbers)):
            reasons.append("non_monotonic_call_number:" + kind)
    if prepared != dispatched + not_sent:
        reasons.append("prepared_call_conservation_failed")
    if received + response_errors > dispatched:
        reasons.append("response_count_exceeds_dispatch_count")
    if any(
        item.state in _DISPATCHED_STATES
        and (not item.request_payload_sha256 or not item.provider_messages_sha256)
        for item in calls
    ):
        reasons.append("dispatched_call_missing_request_identity")
    lifecycle_conservation_valid = not reasons

    applicable = repository_applicability not in {
        "not_applicable_no_supported_source",
        "not_applicable",
        "",
    }
    substrate_status = (
        PhaseStatus.NOT_APPLICABLE
        if not applicable
        else PhaseStatus.PASS
        if repository_substrate_ready and bool(graph_source_revision)
        else PhaseStatus.FAIL
    )
    phases = (
        (
            LifecyclePhase.SNAPSHOT,
            PhaseStatus.PASS if source_revision else PhaseStatus.FAIL,
        ),
        (LifecyclePhase.SUBSTRATE, substrate_status),
        (
            LifecyclePhase.SOLVER,
            PhaseStatus.PASS if dispatched else PhaseStatus.NOT_STARTED,
        ),
        (
            LifecyclePhase.FINALIZATION,
            PhaseStatus.PASS if finalization_complete else PhaseStatus.FAIL,
        ),
    )
    if not action_accounting.conservation_valid:
        reasons.append("action_conservation_failed")
    complete = bool(
        finalization_complete
        and lifecycle_conservation_valid
        and action_accounting.conservation_valid
        and phases[0][1] is PhaseStatus.PASS
    )
    return RuntimeLifecycleReceipt(
        phases=phases,
        calls=calls,
        prepared_calls=prepared,
        dispatched_calls=dispatched,
        received_responses=received,
        response_errors=response_errors,
        not_sent_calls=not_sent,
        lifecycle_conservation_valid=lifecycle_conservation_valid,
        action_conservation_valid=action_accounting.conservation_valid,
        complete=complete,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "CallDispatchState",
    "LifecycleCall",
    "LifecyclePhase",
    "PhaseStatus",
    "RuntimeLifecycleReceipt",
    "build_runtime_lifecycle_receipt",
]
