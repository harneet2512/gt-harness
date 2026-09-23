"""Structure capability facade — graph neighborhood and grouping queries."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import graph_conn, run_typed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _symbol_args(symbol: str, **optional: Any) -> dict[str, Any]:
    args = {"symbol": symbol}
    args.update({k: v for k, v in optional.items() if v not in (None, "")})
    return args


def callers(
    session: "GTSession", symbol: str, depth: int = 3, **hints: Any
) -> dict[str, Any]:
    """Transitive incoming CALLS, banded by hop distance → ``callers`` kind."""

    return run_typed(
        session, "callers", _symbol_args(symbol, depth=depth, **hints)
    )


def callees(session: "GTSession", symbol: str, **hints: Any) -> dict[str, Any]:
    """Outgoing CALLS neighbors of ``symbol``.

    No separate callee kind exists; the certified ``symbol_context`` answer
    already computes both directions via the same ``_call_neighbors`` walk,
    so this surfaces its ``callees`` band verbatim (same caps, same
    ``callees_truncated`` omission).
    """

    result = symbol_context(session, symbol, **hints)
    answer = result.get("direct_answer")
    if isinstance(answer, dict):
        result = dict(result)
        result["direct_answer"] = {
            "symbol": answer.get("symbol"),
            "definition": answer.get("definition"),
            "callees": answer.get("callees", []),
            "callee_count": answer.get("callee_count", 0),
        }
    return result


def symbol_context(session: "GTSession", symbol: str, **hints: Any) -> dict[str, Any]:
    """360° symbol view (definition + callers + callees + flows)."""

    return run_typed(session, "symbol_context", _symbol_args(symbol, **hints))


def processes(
    session: "GTSession", concept: str = "", limit: int = 10
) -> dict[str, Any]:
    """Detected entry→terminal execution-flow library → ``processes`` kind."""

    args: dict[str, Any] = {"limit": limit}
    if concept:
        args["concept"] = concept
    return run_typed(session, "processes", args)


def communities(session: "GTSession", file: str) -> dict[str, Any]:
    """Producer-published communities containing ``file``.

    Reads the ``communities``/``community_members`` tables the producer
    wrote — a data lookup over existing derived state, matching what
    ``graph_context.build_graph_projection`` consumes. No partition is
    computed here.
    """

    conn = graph_conn(session)
    if conn is None:
        return {"file": file, "communities": [], "omissions": ["graph_unavailable"]}
    wanted = file.replace("\\", "/")
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"communities", "community_members"} <= tables:
            return {
                "file": file,
                "communities": [],
                "omissions": ["community_layer_absent"],
            }
        cols = [
            row[1]
            for row in conn.execute("PRAGMA table_info(community_members)")
        ]
        member_col = "member" if "member" in cols else cols[1]
        rows = conn.execute(
            "SELECT c.* FROM community_members cm "
            "JOIN communities c ON c.id = cm.community_id "
            f"WHERE cm.{member_col} = ? OR cm.{member_col} = ? "
            "ORDER BY c.id",
            (wanted, file),
        ).fetchall()
        names = [row[1] for row in conn.execute("PRAGMA table_info(communities)")]
        communities = [dict(zip(names, row)) for row in rows]
        omissions = [] if communities else ["no_community_membership"]
        return {
            "file": wanted,
            "communities": communities,
            "omissions": omissions,
        }
    finally:
        conn.close()


def framework_relationships(
    session: "GTSession", target: str, *, kind: str | None = None
) -> dict[str, Any]:
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
