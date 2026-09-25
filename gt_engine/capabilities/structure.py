"""Structure capability facade — graph neighborhood and grouping queries."""
from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    CapabilityResult,
    graph_conn,
    run_typed,
    wrap,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _symbol_args(symbol: str, **optional: Any) -> dict[str, Any]:
    args = {"symbol": symbol}
    args.update({k: v for k, v in optional.items() if v not in (None, "")})
    return args


def callers(
    session: "GTSession", symbol: str, depth: int = 3, **hints: Any
) -> CapabilityResult:
    """Transitive incoming CALLS, banded by hop distance → ``callers`` kind."""

    return run_typed(
        session, "callers", _symbol_args(symbol, depth=depth, **hints)
    )


def callees(session: "GTSession", symbol: str, **hints: Any) -> CapabilityResult:
    """Outgoing CALLS neighbors of ``symbol``.

    No separate callee kind exists; the certified ``symbol_context`` answer
    already computes both directions via the same ``_call_neighbors`` walk,
    so this surfaces its ``callees`` band verbatim (same caps, same
    ``callees_truncated`` omission).
    """

    result = symbol_context(session, symbol, **hints)
    answer = result.answer
    if not isinstance(answer, dict):
        # Non-answer envelopes (abstain/error/unavailable) still belong to
        # this facade — the caller asked for ``callees``, so the envelope
        # must not keep ``symbol_context``'s capability label.
        return CapabilityResult(
            capability="callees",
            status=result.status,
            answer=result.answer,
            omissions=result.omissions,
            limitations=result.limitations,
            semantics=result.semantics,
            graph_revision=result.graph_revision,
            source_revision=result.source_revision,
            fresh=result.fresh,
            cost=result.cost,
            provenance=result.provenance,
        )
    narrowed = {
        "symbol": answer.get("symbol"),
        "definition": answer.get("definition"),
        "callees": answer.get("callees", []),
        "callee_count": answer.get("callee_count", 0),
    }
    return CapabilityResult(
        capability="callees",
        status=result.status,
        answer=narrowed,
        omissions=result.omissions,
        limitations=result.limitations,
        semantics=result.semantics,
        graph_revision=result.graph_revision,
        source_revision=result.source_revision,
        fresh=result.fresh,
        cost=result.cost,
        provenance="gt_engine.miniswe_typed_actions.execute_typed_action:symbol_context(callees)",
    )


def symbol_context(session: "GTSession", symbol: str, **hints: Any) -> CapabilityResult:
    """360° symbol view (definition + callers + callees + flows)."""

    return run_typed(session, "symbol_context", _symbol_args(symbol, **hints))


def processes(
    session: "GTSession", concept: str = "", limit: int = 10
) -> CapabilityResult:
    """Detected entry→terminal execution-flow library → ``processes`` kind."""

    args: dict[str, Any] = {"limit": limit}
    if concept:
        args["concept"] = concept
    return run_typed(session, "processes", args)


def communities(session: "GTSession", file: str) -> CapabilityResult:
    """Producer-published communities containing ``file``.

    Reads the ``communities``/``community_members`` tables the producer
    wrote — a data lookup over existing derived state, matching what
    ``graph_context.build_graph_projection`` consumes. No partition is
    computed here.
    """

    started = time.perf_counter()
    provenance = "graph:communities/community_members"
    conn = graph_conn(session)
    if conn is None:
        return wrap(
            session, "communities",
            status="unavailable",
            omissions=("graph_unavailable",),
            provenance=provenance,
            started_ms=started,
        )
    wanted = file.replace("\\", "/")
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"communities", "community_members"} <= tables:
            return wrap(
                session, "communities",
                status="unavailable",
                omissions=("community_layer_absent",),
                provenance=provenance,
                started_ms=started,
            )
        rows = conn.execute(
            "SELECT c.* FROM community_members cm "
            "JOIN communities c ON c.id = cm.community_id "
            "WHERE cm.member = ? OR cm.member = ? "
            "ORDER BY c.id",
            (wanted, file),
        ).fetchall()
        names = [row[1] for row in conn.execute("PRAGMA table_info(communities)")]
        communities = [dict(zip(names, row)) for row in rows]
        omissions = () if communities else ("no_community_membership",)
        return wrap(
            session, "communities",
            answer={"file": wanted, "communities": communities},
            status="ok" if communities else "partial",
            omissions=omissions,
            semantics="partial",
            provenance=provenance,
            started_ms=started,
        )
    except sqlite3.Error as exc:
        return wrap(
            session, "communities",
            status="error",
            omissions=(f"graph_query_failed:{type(exc).__name__}",),
            provenance=provenance,
            started_ms=started,
        )
    finally:
        conn.close()


def framework_relationships(
    session: "GTSession", target: str, *, kind: str | None = None
) -> CapabilityResult:
    """Framework wiring for ``target`` (a route/handler file path or symbol).

    Path-like targets delegate to the certified ``route_map`` kind (routes,
    middleware, handler files); symbol-like targets to ``symbol_context``,
    whose answer carries the framework flow membership the producer
    detected. ``kind`` may force ``route_map``, ``api_impact``, or
    ``tool_map`` explicitly.
    """

    if kind is not None:
        if kind not in {"route_map", "api_impact", "tool_map"}:
            raise ValueError(f"unsupported framework kind: {kind}")
        args: dict[str, Any] = {}
        if kind == "api_impact":
            args["route" if "/" in target else "handler"] = target
        else:
            args["path"] = target
        return run_typed(session, kind, args)
    looks_path = "/" in target or "\\" in target or "." in target.rsplit("/", 1)[-1]
    if looks_path:
        return run_typed(session, "route_map", {"path": target})
    return symbol_context(session, target)
