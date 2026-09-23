"""Freshness capability facade (C5).

Internal state only: these report revisions the engine already tracks.
Nothing here changes what is admitted, rendered, or sent to the model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _engine_state(session: "GTSession"):
    return getattr(session._engine, "engine_state", None)


def index_revision(session: "GTSession") -> str:
    """The workspace source revision the engine currently observes."""

    engine_state = _engine_state(session)
    return str(
        getattr(engine_state, "source_revision", "")
        or getattr(session._engine, "repository_revision", "")
        or ""
    )


def graph_state(session: "GTSession") -> dict[str, Any]:
    """Whether the published graph is usable against the current source.

    Delegates to ``EngineState.query_snapshot()`` — the same view the
    engine itself queries, including its completeness enum and omissions.
    """

    engine_state = _engine_state(session)
    if engine_state is None:
        return {
            "graph_revision": "",
            "graph_source_revision": "",
            "source_revision": index_revision(session),
            "completeness": "unavailable",
            "graph_current": False,
            "omissions": [],
            "masked_paths": [],
        }
    snapshot = engine_state.query_snapshot()
    return {
        "graph_revision": snapshot.graph_revision,
        "graph_source_revision": snapshot.graph_source_revision,
        "source_revision": snapshot.source_revision,
        "completeness": snapshot.completeness.value,
        "graph_current": snapshot.graph_current,
        "omissions": list(snapshot.omissions),
        "masked_paths": list(snapshot.masked_paths),
    }


def unit_state(session: "GTSession", unit_id: str) -> dict[str, Any] | None:
    """Freshness record for one admitted context unit (C5).

    ``None`` for a unit this session never admitted; ``is_stale`` is
    ``None`` when the revision comparison is undecidable.
    """

    return session.unit_state(unit_id)
