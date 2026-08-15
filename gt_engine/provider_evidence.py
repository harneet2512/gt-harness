"""Typed lifecycle ledger for every GT provider-context surface."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any


class ProviderEvidenceSurface(StrEnum):
    PREEMPTIVE_RETRIEVAL = "preemptive_retrieval"
    GRAPH_FRONTIER = "graph_frontier"
    FEATURE_FACT = "feature_fact"
    STATE_FRAME = "state_frame"
    PROGRESS_FRAME = "progress_frame"
    PREFLIGHT_RETURN = "preflight_return"
    PERSISTENT_EXECUTION_STATE = "persistent_execution_state"


class ProviderEvidenceDisposition(StrEnum):
    REPRESENTED_MESSAGE = "represented_message"
    SELECTED_NEW_CONTEXT = "selected_new_context"
    CONTROLLER_ONLY = "controller_only"
    STALE = "stale"
    EXPIRED = "expired"
    BUDGET = "budget"
    PREPARED_NOT_SENT = "prepared_not_sent"
    NO_ELIGIBLE_MODEL_CALL = "no_eligible_model_call"


@dataclass(frozen=True, slots=True)
class ProviderEvidenceEvent:
    event_id: str
    surface: ProviderEvidenceSurface
    fact_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    evidence_action: int
    eligible_call: int
    prepared_call: int
    dispatched_call: int | None
    message_indices: tuple[int, ...]
    request_hash: str
    chars: int
    disposition: ProviderEvidenceDisposition
    reason_codes: tuple[str, ...]
    source_revision: str

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["surface"] = self.surface.value
        row["disposition"] = self.disposition.value
        return row


class ProviderEvidenceLedger:
    """Join preparation, dispatch, and omission without relying on markers."""

    def __init__(self) -> None:
        self._events: list[ProviderEvidenceEvent] = []

    def prepare(
        self,
        *,
        surface: ProviderEvidenceSurface,
        fact_ids: tuple[str, ...] = (),
        claim_ids: tuple[str, ...] = (),
        evidence_action: int = 0,
        eligible_call: int,
        prepared_call: int,
        message_indices: tuple[int, ...] = (),
        chars: int = 0,
        disposition: ProviderEvidenceDisposition | None = None,
        reason_codes: tuple[str, ...] = (),
        source_revision: str = "",
    ) -> ProviderEvidenceEvent:
        chosen = disposition or (
            ProviderEvidenceDisposition.SELECTED_NEW_CONTEXT
            if chars > 0 and message_indices
            else ProviderEvidenceDisposition.CONTROLLER_ONLY
        )
        identity = "\0".join(
            (
                surface.value,
                ",".join(fact_ids),
                ",".join(claim_ids),
                str(evidence_action),
                str(eligible_call),
                str(prepared_call),
                source_revision,
            )
        )
        event = ProviderEvidenceEvent(
            event_id="provider-evidence-"
            + hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:20],
            surface=surface,
            fact_ids=tuple(fact_ids),
            claim_ids=tuple(claim_ids),
            evidence_action=max(0, int(evidence_action)),
            eligible_call=max(1, int(eligible_call)),
            prepared_call=max(1, int(prepared_call)),
            dispatched_call=None,
            message_indices=tuple(int(index) for index in message_indices),
            request_hash="",
            chars=max(0, int(chars)),
            disposition=chosen,
            reason_codes=tuple(reason_codes),
            source_revision=str(source_revision),
        )
        self._events.append(event)
        return event

    def mark_dispatched(self, *, call: int, request_hash: str) -> None:
        self._events = [
            replace(event, dispatched_call=call, request_hash=str(request_hash))
            if event.prepared_call == call
            and event.disposition
            in {
                ProviderEvidenceDisposition.SELECTED_NEW_CONTEXT,
                ProviderEvidenceDisposition.REPRESENTED_MESSAGE,
            }
            else event
            for event in self._events
        ]

    def mark_not_sent(self, *, call: int, reason: str) -> None:
        self._events = [
            replace(
                event,
                disposition=ProviderEvidenceDisposition.PREPARED_NOT_SENT,
                reason_codes=tuple(dict.fromkeys((*event.reason_codes, str(reason)))),
            )
            if event.prepared_call == call
            and event.disposition
            in {
                ProviderEvidenceDisposition.SELECTED_NEW_CONTEXT,
                ProviderEvidenceDisposition.REPRESENTED_MESSAGE,
            }
            else event
            for event in self._events
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.provider_evidence.v1",
            "events": [event.as_dict() for event in self._events],
            "event_count": len(self._events),
            "dispatched_events": sum(
                event.dispatched_call is not None for event in self._events
            ),
            "prepared_not_sent_events": sum(
                event.disposition is ProviderEvidenceDisposition.PREPARED_NOT_SENT
                for event in self._events
            ),
            "accounted_events": len(self._events),
        }


__all__ = [
    "ProviderEvidenceDisposition",
    "ProviderEvidenceEvent",
    "ProviderEvidenceLedger",
    "ProviderEvidenceSurface",
]
