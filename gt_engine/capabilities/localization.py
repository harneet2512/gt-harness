"""Localization capability facade — search and symbol-location queries.

Each function delegates to an existing implementation; nothing here adds
new ranking or resolution logic.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    CapabilityResult,
    graph_db_path,
    run_typed,
    wrap,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def lexical_search(
    session: "GTSession", query: str, scope: list[str] | tuple[str, ...] = (".",)
) -> CapabilityResult:
    """Literal text search → ``exact_literal_search`` typed kind."""

    return run_typed(
        session, "exact_literal_search",
        {"literal": query, "paths": list(scope) or ["."]},
    )


def hybrid_rank(session: "GTSession", query: str, k: int = 10) -> CapabilityResult:
    """``retrieval.hybrid_rank`` over the published graph DB.

    Lexical, property, and local-ONNX dense rankers fused by RRF — the same
    graph-DB ranking the task-start localization lane calls. The answer
    carries the fused rows plus per-source provenance so attribution stays
    inspectable.
    """

    started = time.perf_counter()
    db = graph_db_path(session)
    if not db:
        return wrap(
            session, "hybrid_rank",
            status="unavailable",
            omissions=("graph_unavailable",),
            provenance="gt_engine.retrieval.hybrid_rank",
            started_ms=started,
        )
    from gt_engine.retrieval import hybrid_rank as _hybrid_rank

    try:
        ranking = _hybrid_rank(db, query, k)
    except Exception as exc:  # noqa: BLE001 - abstain, never raise
        return wrap(
            session, "hybrid_rank",
            status="abstain",
            omissions=(f"ranking_failed:{type(exc).__name__}",),
            provenance="gt_engine.retrieval.hybrid_rank",
            started_ms=started,
        )
    sources = [source.as_dict() for source in ranking.sources]
    omissions: list[str] = [
        f"source_unavailable:{source.source}"
        for source in ranking.sources
        if not source.available
    ]
    answer = {
        "query": ranking.query,
        "fused": [
            {"stable_id": row.stable_id, "score": row.score, "snippet": row.snippet}
            for row in ranking.fused
        ],
        "sources": sources,
        "rrf_k": ranking.rrf_k,
        "provenance": {
            key: provenance.as_dict()
            for key, provenance in sorted(ranking.provenance.items())
        },
    }
    return wrap(
        session, "hybrid_rank",
        answer=answer,
        status="ok" if not omissions else "partial",
        omissions=tuple(omissions),
        semantics="heuristic",
        provenance="gt_engine.retrieval.hybrid_rank",
        started_ms=started,
    )


def definition(
    session: "GTSession", symbol: str, hints: dict[str, Any] | None = None
) -> CapabilityResult:
    """Symbol definition sites → ``definition`` typed kind."""

    return run_typed(session, "definition", {"symbol": symbol, **_hints(hints)})


def references(
    session: "GTSession", symbol: str, hints: dict[str, Any] | None = None
) -> CapabilityResult:
    """Reference sites → ``references`` typed kind."""

    return run_typed(session, "references", {"symbol": symbol, **_hints(hints)})


def _hints(hints: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("path", "language"):
        if hints and hints.get(key):
            out[key] = hints[key]
    return out
