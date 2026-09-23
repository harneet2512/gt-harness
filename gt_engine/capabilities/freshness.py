"""Freshness capability facade (C5).

Internal state only: these report revisions the engine already tracks.
Nothing here changes what is admitted, rendered, or sent to the model.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    CapabilityResult,
    engine_of,
    wrap,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _engine_state(session: "GTSession") -> Any:
    return getattr(engine_of(session), "engine_state", None)


def index_revision(session: "GTSession") -> CapabilityResult:
    """The workspace source revision the engine currently observes."""

    started = time.perf_counter()
    engine_state = _engine_state(session)
    revision = str(
        getattr(engine_state, "source_revision", "")
        or getattr(engine_of(session), "repository_revision", "")
        or ""
    )
    return wrap(
        session, "index_revision",
        answer={"source_revision": revision},
        status="ok" if revision else "partial",
        omissions=() if revision else ("no_source_revision",),
        semantics="exact",
        provenance="gt_engine.engine_state.EngineState.source_revision",
        started_ms=started,
    )


def graph_state(session: "GTSession") -> CapabilityResult:
    """Whether the published graph is usable against the current source.

    Delegates to ``EngineState.query_snapshot()`` — the same view the
    engine itself queries. ``completeness`` is
    ``current_complete``/``current_partial``/``unavailable``; the reason is
    in ``omissions``/``masked_paths`` when not current.
    """

    started = time.perf_counter()
    engine_state = _engine_state(session)
    if engine_state is None:
        return wrap(
            session, "graph_state",
            status="unavailable",
            omissions=("engine_state_absent",),
            provenance="gt_engine.engine_state.EngineState.query_snapshot",
            started_ms=started,
        )
    snapshot = engine_state.query_snapshot()
    answer = {
        "graph_revision": snapshot.graph_revision,
        "graph_source_revision": snapshot.graph_source_revision,
        "source_revision": snapshot.source_revision,
        "completeness": snapshot.completeness.value,
        "graph_current": snapshot.graph_current,
        "omissions": list(snapshot.omissions),
        "masked_paths": list(snapshot.masked_paths),
    }
    status = (
        "ok"
        if snapshot.graph_current
        else "partial"
        if snapshot.completeness.value == "current_partial"
        else "unavailable"
    )
    return wrap(
        session, "graph_state",
        answer=answer,
        status=status,
        omissions=tuple(snapshot.omissions),
        semantics="exact",
        provenance="gt_engine.engine_state.EngineState.query_snapshot",
        started_ms=started,
    )


def amend_state(session: "GTSession") -> CapabilityResult:
    """Incremental-graph amend posture the adapter currently holds.

    Read-only over ``_last_graph_publication``, the amend deferral window,
    per-parent failure streaks, and the edit epoch — the same fields
    ``_sync_amend_graph``/``_amend_graph_inline`` act on.
    """

    started = time.perf_counter()
    engine = engine_of(session)
    publication = getattr(engine, "_last_graph_publication", None)
    defer_until = float(getattr(engine, "_graph_amend_defer_until", 0.0) or 0.0)
    streaks = dict(getattr(engine, "_amend_failure_streak", {}) or {})
    answer = {
        "last_graph_publication": (
            {"manifest_digest": publication[0], "source_revision": publication[1]}
            if publication
            else None
        ),
        "amend_deferred": defer_until > time.time(),
        "amend_defer_until": defer_until,
        "amend_failure_streaks": {k: v for k, v in sorted(streaks.items())},
        "edit_epoch": int(getattr(engine, "_edit_epoch", 0) or 0),
        "incomplete_edit_epoch": int(
            getattr(engine, "_incomplete_edit_epoch", 0) or 0
        ),
    }
    omissions: list[str] = []
    if publication is None:
        omissions.append("no_graph_publication")
    return wrap(
        session, "amend_state",
        answer=answer,
        status="ok",
        omissions=tuple(omissions),
        semantics="exact",
        provenance="gt_engine.miniswe_integration._sync_amend_graph",
        started_ms=started,
    )


def fallback_state(session: "GTSession") -> CapabilityResult:
    """Recovery/fallback posture the adapter currently holds.

    Read-only over ``_recovery_suspended`` (the revision recovery was
    suspended at), failure streaks/totals, and the delivered steer count —
    the same fields ``_recovery_build_inline`` enforces.
    """

    started = time.perf_counter()
    engine = engine_of(session)
    suspended = str(getattr(engine, "_recovery_suspended", "") or "")
    answer = {
        "recovery_suspended": bool(suspended),
        "suspended_at_revision": suspended or None,
        "recovery_failure_streak": int(
            getattr(engine, "_recovery_failure_streak", 0) or 0
        ),
        "recovery_failure_total": int(
            getattr(engine, "_recovery_failure_total", 0) or 0
        ),
        "recovery_failure_revision": str(
            getattr(engine, "_recovery_failure_revision", "") or ""
        ),
        "recovery_delivered": int(
            getattr(engine, "_recovery_delivered", 0) or 0
        ),
        "suspend_streak": int(
            getattr(engine, "RECOVERY_FAILURE_SUSPEND_STREAK", 0) or 0
        ),
        "episode_cap": int(
            getattr(engine, "RECOVERY_FAILURE_EPISODE_CAP", 0) or 0
        ),
    }
    return wrap(
        session, "fallback_state",
        answer=answer,
        status="ok",
        omissions=() if not suspended else ("recovery_suspended",),
        semantics="exact",
        provenance="gt_engine.miniswe_integration._recovery_build_inline",
        started_ms=started,
    )


def unit_state(session: "GTSession", unit_id: str) -> CapabilityResult:
    """Freshness record for one admitted context unit (C5).

    ``unavailable`` for a unit this session never admitted; the answer's
    ``is_stale`` is ``None`` when the revision comparison is undecidable.
    """

    started = time.perf_counter()
    state = session.unit_state(unit_id)
    if state is None:
        return wrap(
            session, "unit_state",
            status="unavailable",
            omissions=("unit_unknown",),
            semantics="exact",
            provenance="gt_engine.gt_session.GTSession.unit_state",
            started_ms=started,
        )
    omissions: list[str] = []
    if state["is_stale"] is None:
        omissions.append("revision_comparison_undecidable")
    elif state["is_stale"]:
        omissions.append("unit_stale")
    return wrap(
        session, "unit_state",
        answer=state,
        status="ok" if state["is_stale"] is False else "partial",
        omissions=tuple(omissions),
        semantics="exact",
        provenance="gt_engine.gt_session.GTSession.unit_state",
        started_ms=started,
    )
