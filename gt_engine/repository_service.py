"""Single task-scoped owner for repository state transitions and decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from gt_engine.decision_value import DecisionBoundary
from gt_engine.repository_intelligence import RepositoryEvidence, RepositorySession


@dataclass(frozen=True, slots=True)
class RepositoryActionObservation:
    transition: Any
    graph_input_revision: str
    changed_paths: tuple[str, ...]
    refresh_timeout: float = 600.0


@dataclass(frozen=True, slots=True)
class RepositoryUpdate:
    advanced: bool
    evidence: RepositoryEvidence
    graph_input_revision: str
    pending_refresh: bool


@dataclass(frozen=True, slots=True)
class RepositoryDecisionRequest:
    boundary: DecisionBoundary
    graph_input_revision: str
    active_paths: tuple[str, ...] = ()
    active_symbols: tuple[str, ...] = ()
    diagnostic_fingerprint: str = ""
    refresh_timeout: float = 600.0


@dataclass(frozen=True, slots=True)
class PreparedRepositoryDecision:
    evidence: RepositoryEvidence
    graph_input_revision: str
    graph_revision: str
    refreshed: bool
    query_performed: bool


class RepositoryIntelligenceService:
    """Sequence one existing RepositorySession behind a narrow lifecycle."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session
        self._pending_refresh = False
        self._closed = False
        self._updates: list[RepositoryUpdate] = []
        self._decisions: list[PreparedRepositoryDecision] = []

    @classmethod
    def open(cls, session: RepositorySession) -> RepositoryIntelligenceService:
        return cls(session)

    @property
    def session(self) -> RepositorySession:
        return self._session

    def record_action(self, observation: RepositoryActionObservation) -> RepositoryUpdate:
        self._ensure_open()
        advanced = self._session.apply_transition(
            observation.transition,
            source_revision=observation.graph_input_revision,
            changed_paths=observation.changed_paths,
        )
        self._pending_refresh = bool(advanced)
        update = RepositoryUpdate(
            advanced=advanced,
            evidence=self._session.evidence,
            graph_input_revision=observation.graph_input_revision,
            pending_refresh=self._pending_refresh,
        )
        self._updates.append(update)
        return update

    def prepare(self, request: RepositoryDecisionRequest) -> PreparedRepositoryDecision:
        self._ensure_open()
        refreshed = False
        evidence = self._session.evidence
        if self._pending_refresh:
            evidence = self._session.refresh(
                source_revision=request.graph_input_revision,
                timeout=request.refresh_timeout,
            )
            self._pending_refresh = False
            refreshed = True
        query_performed = bool(
            request.active_paths
            and evidence.substrate_ready
            and self._session.indexed_source_revision == request.graph_input_revision
        )
        if query_performed:
            evidence = self._session.query(
                source_revision=request.graph_input_revision,
                active_paths=request.active_paths,
                active_symbols=request.active_symbols,
                diagnostic_fingerprint=request.diagnostic_fingerprint,
                boundary=request.boundary.value,
            )
        decision = PreparedRepositoryDecision(
            evidence=evidence,
            graph_input_revision=request.graph_input_revision,
            graph_revision=str(evidence.graph_revision or ""),
            refreshed=refreshed,
            query_performed=query_performed,
        )
        self._decisions.append(decision)
        return decision

    def mark_incomplete(self, *, graph_input_revision: str, status: str) -> None:
        self._ensure_open()
        self._session.invalidate(source_revision=graph_input_revision, status=status)
        self._pending_refresh = False

    def final_receipt(self) -> dict[str, Any]:
        return {
            "schema": "gt.repository_service.v1",
            "closed": self._closed,
            "pending_refresh": self._pending_refresh,
            "update_count": len(self._updates),
            "decision_count": len(self._decisions),
            "updates": [
                asdict(row) | {"evidence": row.evidence.as_dict()}
                for row in self._updates
            ],
            "decisions": [
                asdict(row) | {"evidence": row.evidence.as_dict()}
                for row in self._decisions
            ],
        }

    def close(self) -> None:
        if not self._closed:
            self._session.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("repository_service_closed")


__all__ = [
    "PreparedRepositoryDecision",
    "RepositoryActionObservation",
    "RepositoryDecisionRequest",
    "RepositoryIntelligenceService",
    "RepositoryUpdate",
]
