"""Localization capability facade — search and symbol-location queries.

Each function delegates to an existing implementation; nothing here adds
new ranking or resolution logic.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import run_typed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession
    from gt_engine.retrieval import HybridRanking


def lexical_search(
    session: "GTSession", query: str, scope: list[str] | tuple[str, ...] = (".",)
) -> dict[str, Any]:
    """Literal text search → ``exact_literal_search`` typed kind."""

    return run_typed(
        session, "exact_literal_search",
        {"literal": query, "paths": list(scope) or ["."]},
    )


def hybrid_rank(session: "GTSession", query: str, k: int = 10) -> "HybridRanking":
    """``retrieval.hybrid_rank`` over the published graph DB.

    Lexical, property, and local-ONNX dense rankers fused by RRF — the same
    graph-DB ranking the task-start localization lane calls. Returns the
    ``HybridRanking`` object so per-source provenance stays inspectable.
    """

    from gt_engine.retrieval import hybrid_rank as _hybrid_rank

    return _hybrid_rank(_graph_db(session), query, k)


def definition(
    session: "GTSession", symbol: str, hints: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Symbol definition sites → ``definition`` typed kind."""

    return run_typed(session, "definition", {"symbol": symbol, **_hints(hints)})


def references(
    session: "GTSession", symbol: str, hints: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Reference sites → ``references`` typed kind."""

    return run_typed(session, "references", {"symbol": symbol, **_hints(hints)})


def _hints(hints: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("path", "language"):
        if hints and hints.get(key):
            out[key] = hints[key]
    return out


def _graph_db(session: "GTSession") -> str:
    from gt_engine.capabilities._query import graph_db_path

    return graph_db_path(session)
