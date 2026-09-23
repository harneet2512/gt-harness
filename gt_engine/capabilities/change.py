"""Change capability facade — edit transactions and impact queries."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    engine_of,
    graph_conn,
    graph_db_path,
    last_journal_event,
    read_cas_blob,
    run_typed,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def edit_transaction(session: "GTSession", latest: bool = True) -> dict[str, Any] | None:
    """The recorded edit transaction — the journal's last ``edit_transaction``
    event's content-addressed payload.

    ``latest=False`` is reserved for historical enumeration; only the latest
    transaction is exposed because that is the state the engine acts on.
    """

    row = last_journal_event(session, "edit_transaction")
    if row is None:
        return None
    digest = str(row.get("artifact_sha256") or "")
    raw = read_cas_blob(session, "edit_transactions", digest)
    payload: dict[str, Any]
    if raw is not None:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
    else:
        payload = {}
    return {
        "transaction_sha256": row.get("transaction_sha256"),
        "artifact_sha256": digest,
        "pre_revision": row.get("pre_revision"),
        "post_revision": row.get("post_revision"),
        "changed_paths": row.get("changed_paths") or [],
        "complete": row.get("complete"),
        "omissions": row.get("omissions") or [],
        "action_index": row.get("action_index"),
        "transaction": payload or None,
        "blob_missing": raw is None,
    }


def patch_impact(
    session: "GTSession", edited_files: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Impact of a proposed patch → certified ``patch_impact`` kind.

    ``edited_files`` maps each repository path to ``{"before", "after"}``
    exactly as the model-facing contract defines.
    """

    return run_typed(
        session, "patch_impact", {"edited_files": dict(edited_files)}
    )


def route_impact(
    session: "GTSession",
    route: str | None = None,
    handler: str | None = None,
) -> dict[str, Any]:
    """Consumers/blast radius of a route or handler → ``api_impact`` kind."""

    args: dict[str, Any] = {}
    if route:
        args["route"] = route
    if handler:
        args["handler"] = handler
    return run_typed(session, "api_impact", args)


def shape_change(
    session: "GTSession", symbol: str, **hints: Any
) -> dict[str, Any]:
    """Signature/shape drift of ``symbol`` → certified ``shape_check`` kind."""

    args: dict[str, Any] = {"symbol": symbol}
    args.update({k: v for k, v in hints.items() if v not in (None, "")})
    return run_typed(session, "shape_check", args)


def affected_tests(
    session: "GTSession", files: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Repo test files graph-covering the edited ``files`` — selection only.

    Same composition the covering lane runs pre-execution:
    ``_symbols_for_files`` (file→edited-symbol resolution) then the wheel's
    ``select_covering_tests`` (FACT-tier CALLS reachability). Nothing is
    executed here.
    """

    conn = graph_conn(session)
    if conn is not None:
        conn.close()
    graph_path = graph_db_path(session)
    if not graph_path:
        return {"files": list(files), "tests": [], "omissions": ["graph_unavailable"]}
    engine = engine_of(session)
    repo_root = str(getattr(engine, "repo_root", "") or "")
    try:
        from gt_engine.miniswe_covering import _symbols_for_files
        from groundtruth.runtime.covering_runner import select_covering_tests
    except ModuleNotFoundError:
        return {
            "files": list(files),
            "tests": [],
            "omissions": ["covering_wheel_absent"],
        }
    symbols = _symbols_for_files(graph_path, tuple(files), repo_root)
    if not symbols:
        return {
            "files": list(files),
            "tests": [],
            "omissions": ["no_symbols_resolved"],
        }
    selected = select_covering_tests(
        graph_path, symbols, limit=8, repo_root=repo_root
    )
    return {
        "files": list(files),
        "symbols": sorted(symbols),
        "tests": [
            {"file": row["file"], "confidence": row.get("confidence")}
            for row in (selected or [])
        ],
        "omissions": [] if selected else ["no_covering_tests"],
    }
