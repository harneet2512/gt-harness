"""Analysis capability facade — CFG/dataflow and taint queries.

Graph-persisted CFGs are recomposed by ``groundtruth.runtime.cfg_store``;
the typed ``slice``/``taint`` kinds run through the certified dispatcher.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    engine_of,
    graph_conn,
    resolve_symbol_node,
    run_typed,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _stored_analysis(session: "GTSession", function: str) -> tuple[Any | None, dict[str, Any]]:
    """Resolve ``function`` and run the wheel's stored-CFG pipeline.

    Returns ``(StoredAnalysis, context)`` or ``(None, error)``. Symbol
    resolution goes through the certified ``symbol_context`` query so the
    facade inherits its ambiguity and preferred-order rules.
    """

    node = resolve_symbol_node(session, function)
    if node is None:
        return None, {"omissions": ["symbol_not_found"], "symbol": function}
    conn = graph_conn(session)
    if conn is None:
        return None, {"omissions": ["graph_unavailable"], "symbol": function}
    root = Path(str(getattr(engine_of(session), "repo_root", "") or ""))
    source_path = root / str(node.get("file_path") or "")
    try:
        source = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        conn.close()
        return None, {
            "omissions": ["source_unavailable"],
            "symbol": function,
            "file_path": node.get("file_path"),
        }
    try:
        from groundtruth.runtime.cfg_store import analyze_stored
    except ModuleNotFoundError:
        conn.close()
        return None, {
            "omissions": ["stored_cfg_wheel_absent"],
            "symbol": function,
        }
    try:
        analysis = analyze_stored(
            conn,
            int(node["id"]),
            source=source,
            function_name=str(node.get("name") or function),
            language=str(node.get("language") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - analysis abstains, never raises
        conn.close()
        return None, {
            "omissions": [f"analysis_failed:{type(exc).__name__}"],
            "symbol": function,
        }
    conn.close()
    return analysis, {
        "symbol": function,
        "node_id": int(node["id"]),
        "file_path": node.get("file_path"),
        "def_line": analysis.def_line,
        "function_name": analysis.function_name,
        "limitations": list(analysis.limitations),
    }


def cfg(session: "GTSession", function: str) -> dict[str, Any]:
    """Persisted control-flow graph of ``function`` (blocks + edges)."""

    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return context
    return {
        **context,
        "entry_id": analysis.cfg.entry_id,
        "exit_id": analysis.cfg.exit_id,
        "blocks": [
            {
                "id": block.id,
                "kind": block.kind,
                "start_line": block.start_line,
                "end_line": block.end_line,
                "statement_lines": list(block.statement_lines),
                "successors": list(block.successors),
                "predecessors": list(block.predecessors),
            }
            for block in sorted(
                analysis.cfg.blocks.values(), key=lambda b: b.id
            )
        ],
        "edges": [list(edge) for edge in sorted(analysis.cfg.edges)],
    }


def reaching_definitions(session: "GTSession", function: str) -> dict[str, Any]:
    """Per-block reaching definitions of ``function``."""

    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return context

    def _flat(table: dict[int, dict[str, set]]) -> dict[str, Any]:
        return {
            str(block): {
                var: sorted(f"{name}:{line}" for name, line in defs)
                for var, defs in sorted(items.items())
            }
            for block, items in sorted(table.items())
        }

    reaching = analysis.reaching
    return {
        **context,
        "in": _flat(reaching.in_),
        "out": _flat(reaching.out),
        "all_defs": {
            var: sorted(f"{name}:{line}" for name, line in defs)
            for var, defs in sorted(reaching.all_defs.items())
        },
    }


def control_dependence(session: "GTSession", function: str) -> dict[str, Any]:
    """Control-dependence edges of ``function`` as (block, depends_on, label)."""

    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return context
    return {
        **context,
        "control_dependence": [
            {"block": a, "depends_on": b, "label": label}
            for a, b, label in sorted(analysis.control_dependence)
        ],
        "dominators": {
            str(block): sorted(doms)
            for block, doms in sorted(analysis.dominators.dom.items())
        },
    }


def slice(
    session: "GTSession",
    symbol: str,
    line: int,
    direction: str = "backward",
    interprocedural: bool = False,
    **optional: Any,
) -> dict[str, Any]:
    """Program slice → certified ``slice`` typed kind."""

    args: dict[str, Any] = {
        "symbol": symbol,
        "line": line,
        "direction": direction,
        "interprocedural": bool(interprocedural),
    }
    args.update({k: v for k, v in optional.items() if v not in (None, "")})
    return run_typed(session, "slice", args)


def callable_values(
    session: "GTSession", symbol: str, **hints: Any
) -> dict[str, Any]:
    """Producer-retained call candidates for callsites naming ``symbol``.

    Reads the ``resolution_callsites``/``resolution_candidates`` tables —
    the ``retained_call_candidates`` producer capability surfaced as data.
    An empty candidate set is reported, never fabricated.
    """

    conn = graph_conn(session)
    if conn is None:
        return {"symbol": symbol, "callsites": [], "omissions": ["graph_unavailable"]}
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        needed = {"resolution_callsites", "resolution_candidates"}
        if not needed <= tables:
            return {
                "symbol": symbol,
                "callsites": [],
                "omissions": ["resolution_substrate_absent"],
            }
        rows = conn.execute(
            "SELECT cs.callsite_id, cs.source_stable_id, cs.source_native_id,"
            " c.target_stable_id, c.target_native_id, c.mechanism, c.ordinal"
            " FROM resolution_callsites cs"
            " JOIN resolution_candidates c ON c.callsite_id = cs.callsite_id"
            " WHERE cs.source_native_id = ? OR cs.source_stable_id = ?"
            " ORDER BY cs.callsite_id, c.ordinal",
            (symbol, symbol),
        ).fetchall()
        by_site: dict[str, dict[str, Any]] = {}
        for cid, sstable, snative, tstable, tnative, mech, _ord in rows:
            site = by_site.setdefault(
                str(cid),
                {
                    "callsite_id": str(cid),
                    "source_stable_id": sstable,
                    "source_native_id": snative,
                    "candidates": [],
                },
            )
            site["candidates"].append(
                {
                    "target_stable_id": tstable,
                    "target_native_id": tnative,
                    "mechanism": mech,
                }
            )
        omissions = [] if by_site else ["no_retained_candidates"]
        return {
            "symbol": symbol,
            "callsites": list(by_site.values()),
            "omissions": omissions,
        }
    finally:
        conn.close()


def taint(
    session: "GTSession",
    sources: str | list[str],
    sinks: str | list[str] | None = None,
    **optional: Any,
) -> dict[str, Any]:
    """Symbol-level call reachability source→sink → certified ``taint`` kind.

    The kind takes one source and an optional sink; several sources map to
    one query per source (same contract as the model-facing action).
    """

    source_list = [sources] if isinstance(sources, str) else list(sources)
    sink = (sinks or [None])[0] if isinstance(sinks, list) else sinks
    results = []
    for source in source_list:
        args: dict[str, Any] = {"source": source}
        if sink:
            args["sink"] = sink
        args.update({k: v for k, v in optional.items() if v not in (None, "")})
        results.append(
            {"source": source, "result": run_typed(session, "taint", args)}
        )
    if len(results) == 1:
        return results[0]["result"]
    return {"sources": results}
