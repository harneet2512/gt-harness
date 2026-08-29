from __future__ import annotations

from dataclasses import replace

from gt_engine.decision_value import DecisionBoundary
from gt_engine.repository_intelligence import RepositoryEvidence
from gt_engine.repository_service import (
    RepositoryActionObservation,
    RepositoryDecisionRequest,
    RepositoryIntelligenceService,
)


class _Session:
    def __init__(self) -> None:
        self.evidence = RepositoryEvidence(status="not_indexed")
        self.indexed_source_revision = "r1"
        self.calls: list[str] = []

    def apply_transition(self, transition, *, source_revision, changed_paths):
        self.calls.append("apply")
        return True

    def refresh(self, *, source_revision, timeout):
        self.calls.append("refresh")
        self.indexed_source_revision = source_revision
        self.evidence = replace(
            self.evidence,
            available=True,
            substrate_ready=True,
            index_current=True,
            intelligence_valid=True,
            source_revision=source_revision,
            graph_revision="graph-r2",
            status="source_backed",
        )
        return self.evidence

    def query(self, **kwargs):
        self.calls.append("query")
        return self.evidence

    def invalidate(self, *, source_revision, status):
        self.calls.append("invalidate")

    def close(self):
        self.calls.append("close")


def test_service_coalesces_action_update_and_refreshes_at_decision_boundary():
    session = _Session()
    service = RepositoryIntelligenceService.open(session)  # type: ignore[arg-type]

    update = service.record_action(
        RepositoryActionObservation(object(), "r2", ("src/a.py",))
    )
    assert update.pending_refresh is True
    assert session.calls == ["apply"]

    decision = service.prepare(
        RepositoryDecisionRequest(
            boundary=DecisionBoundary.POST_EDIT_GRAPH_DELTA,
            graph_input_revision="r2",
            active_paths=("src/a.py",),
        )
    )

    assert session.calls == ["apply", "refresh", "query"]
    assert decision.refreshed is True
    assert decision.query_performed is True
    assert decision.graph_revision == "graph-r2"


def test_service_keeps_refresh_pending_when_publication_fails():
    session = _Session()
    service = RepositoryIntelligenceService.open(session)  # type: ignore[arg-type]
    service.record_action(RepositoryActionObservation(object(), "r2", ("src/a.py",)))

    def failed_refresh(*, source_revision, timeout):
        del timeout
        session.calls.append("refresh")
        session.evidence = replace(
            session.evidence,
            source_revision=source_revision,
            substrate_ready=False,
            index_current=False,
            intelligence_valid=False,
        )
        return session.evidence

    session.refresh = failed_refresh
    decision = service.prepare(
        RepositoryDecisionRequest(
            boundary=DecisionBoundary.POST_EDIT_GRAPH_DELTA,
            graph_input_revision="r2",
            active_paths=("src/a.py",),
        )
    )

    assert decision.refreshed is True
    assert decision.query_performed is False
    assert service.final_receipt()["pending_refresh"] is True
