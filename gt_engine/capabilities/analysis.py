"""Analysis capability facade — CFG/dataflow and taint queries.

Graph-persisted CFGs are recomposed by ``groundtruth.runtime.cfg_store``;
the typed ``slice``/``taint`` kinds run through the certified dispatcher.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    CapabilityResult,
    engine_of,
    graph_conn,
    resolve_symbol_node,
    run_typed,
    wrap,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession

_STORED_PROVENANCE = "groundtruth.runtime.cfg_store.analyze_stored"


def _stored_analysis(session: "GTSession", function: str) -> tuple[Any | None, dict[str, Any]]:
    """Resolve ``function`` and run the wheel's stored-CFG pipeline.

    Returns ``(StoredAnalysis, context)`` or ``(None, error)``. Symbol
    resolution goes through the certified ``symbol_context`` query so the
    facade inherits its ambiguity and preferred-order rules.
    """

    conn = graph_conn(session)
    if conn is None:
        return None, {"omissions": ["graph_unavailable"], "symbol": function}
    node = resolve_symbol_node(session, function)
    if node is None:
        conn.close()
        return None, {"omissions": ["symbol_not_found"], "symbol": function}
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
        # ``CFGAnalysisError: no persisted CFG`` is the producer's honest
        # per-language coverage statement — surface it as a named
        # omission rather than a bare exception type.
        reason = str(exc)
        omission = (
            "no_persisted_cfg"
            if "no persisted CFG" in reason
            else f"analysis_failed:{type(exc).__name__}"
        )
        return None, {
            "omissions": [omission],
            "symbol": function,
            "detail": reason[:200],
        }
    conn.close()
    return analysis, {
        "symbol": function,
        "node_id": int(node["id"]),
        "file_path": node.get("file_path"),
        "def_line": analysis.def_line,
        "function_name": analysis.function_name,
    }


def _abstain(
    session: "GTSession", capability: str, error: dict[str, Any], started: float
) -> CapabilityResult:
    return wrap(
        session, capability,
        status="abstain",
        omissions=tuple(str(o) for o in error.get("omissions", ())),
        answer={k: v for k, v in error.items() if k != "omissions"},
        provenance=_STORED_PROVENANCE,
        started_ms=started,
    )


def cfg(session: "GTSession", function: str) -> CapabilityResult:
    """Persisted control-flow graph of ``function`` (blocks + edges)."""

    started = time.perf_counter()
    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return _abstain(session, "cfg", context, started)
    return wrap(
        session, "cfg",
        answer={
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
        },
        status="ok",
        limitations=tuple(analysis.limitations),
        semantics="exact",
        provenance=_STORED_PROVENANCE,
        started_ms=started,
    )


def reaching_definitions(session: "GTSession", function: str) -> CapabilityResult:
    """Per-block reaching definitions of ``function``."""

    started = time.perf_counter()
    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return _abstain(session, "reaching_definitions", context, started)

    def _flat(table: dict[int, dict[str, set]]) -> dict[str, Any]:
        return {
            str(block): {
                var: sorted(f"{name}:{line}" for name, line in defs)
                for var, defs in sorted(items.items())
            }
            for block, items in sorted(table.items())
        }

    reaching = analysis.reaching
    return wrap(
        session, "reaching_definitions",
        answer={
            **context,
            "in": _flat(reaching.in_),
            "out": _flat(reaching.out),
            "all_defs": {
                var: sorted(f"{name}:{line}" for name, line in defs)
                for var, defs in sorted(reaching.all_defs.items())
            },
        },
        status="ok",
        limitations=tuple(analysis.limitations),
        semantics="exact",
        provenance=_STORED_PROVENANCE,
        started_ms=started,
    )


def control_dependence(session: "GTSession", function: str) -> CapabilityResult:
    """Control-dependence edges of ``function`` as (block, depends_on, label)."""

    started = time.perf_counter()
    analysis, context = _stored_analysis(session, function)
    if analysis is None:
        return _abstain(session, "control_dependence", context, started)
    return wrap(
        session, "control_dependence",
        answer={
            **context,
            "control_dependence": [
                {"block": a, "depends_on": b, "label": label}
                for a, b, label in sorted(analysis.control_dependence)
            ],
            "dominators": {
                str(block): sorted(doms)
                for block, doms in sorted(analysis.dominators.dom.items())
            },
        },
        status="ok",
        limitations=tuple(analysis.limitations),
        semantics="exact",
        provenance=_STORED_PROVENANCE,
        started_ms=started,
    )


def slice(
    session: "GTSession",
    symbol: str,
    line: int,
    direction: str = "backward",
    interprocedural: bool = False,
    **optional: Any,
) -> CapabilityResult:
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
) -> CapabilityResult:
    """Producer-retained call candidates for callsites naming ``symbol``.

    Reads the ``resolution_callsites``/``resolution_candidates`` tables —
    the ``retained_call_candidates`` producer capability surfaced as data.
    An empty candidate set is reported, never fabricated.
    """

    started = time.perf_counter()
    provenance = "graph:resolution_callsites/resolution_candidates"
    conn = graph_conn(session)
    if conn is None:
        return wrap(
            session, "callable_values",
            status="unavailable",
            omissions=("graph_unavailable",),
            provenance=provenance,
            started_ms=started,
        )
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        needed = {"resolution_callsites", "resolution_candidates"}
        if not needed <= tables:
            return wrap(
                session, "callable_values",
                status="unavailable",
                omissions=("resolution_substrate_absent",),
                provenance=provenance,
                started_ms=started,
            )
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
        omissions = () if by_site else ("no_retained_candidates",)
        return wrap(
            session, "callable_values",
            answer={"symbol": symbol, "callsites": list(by_site.values())},
            status="ok" if by_site else "partial",
            omissions=omissions,
            semantics="partial",
            provenance=provenance,
            started_ms=started,
        )
    finally:
        conn.close()


def taint(
    session: "GTSession",
    sources: str | list[str],
    sinks: str | list[str] | None = None,
    **optional: Any,
) -> CapabilityResult:
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
    merged_omissions: list[str] = []
    for item in results:
        merged_omissions.extend(item["result"].omissions)
    worst = "ok"
    for item in results:
        order = {"ok": 0, "partial": 1, "abstain": 2, "unavailable": 3, "error": 4}
        if order.get(item["result"].status, 4) > order.get(worst, 4):
            worst = item["result"].status
    base = results[0]["result"]
    return CapabilityResult(
        capability="taint",
        status=worst,
        answer=[{"source": r["source"], "answer": r["result"].answer} for r in results],
        omissions=tuple(dict.fromkeys(merged_omissions)),
        semantics=base.semantics,
        graph_revision=base.graph_revision,
        source_revision=base.source_revision,
        fresh=base.fresh,
        cost={
            "elapsed_ms": sum(r["result"].cost.get("elapsed_ms", 0) for r in results),
            "output_bytes": sum(r["result"].cost.get("output_bytes", 0) for r in results),
        },
        provenance=base.provenance,
    )
