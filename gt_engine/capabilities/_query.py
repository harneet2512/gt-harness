"""Shared internals for the capability facades.

Every typed-kind facade runs through the SAME pipeline the model-facing
groundtruth action uses — ``build_action_request`` + ``execute_typed_action``
— so certification gates, language refusals, demotion rules, and bounded
output are identical between the internal API and what a model could call.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def engine_of(session: "GTSession") -> Any:
    return session._engine


def graph_db_path(session: "GTSession") -> str:
    return str(getattr(engine_of(session), "graph_db", "") or "")


def graph_conn(session: "GTSession") -> sqlite3.Connection | None:
    """Read-only handle on the published graph, or None when none exists."""

    path = graph_db_path(session)
    if not path:
        return None
    graph = Path(path)
    if not graph.is_absolute():
        root = str(getattr(engine_of(session), "repo_root", "") or "")
        graph = Path(root) / graph if root else graph
    if not graph.is_file():
        return None
    return sqlite3.connect(str(graph))


def run_typed(
    session: "GTSession",
    kind: str,
    arguments: dict[str, Any],
    *,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a certified typed kind and return the compiled observation.

    The returned mapping is the decoded ``gt.compiled_observation.v1``
    payload: ``evidence``, ``direct_answer``, ``decision``, ``honesty`` —
    exactly what the model-facing path would produce, including omissions
    and abstention reasons.
    """

    from gt_engine.miniswe_typed_actions import (
        build_action_request,
        execute_typed_action,
    )

    engine = engine_of(session)
    repo_root = str(getattr(engine, "repo_root", "") or "")
    if not repo_root:
        return {
            "schema": "gt.compiled_observation.v1",
            "evidence": {"omissions": ["capability_repo_root_missing"]},
            "direct_answer": None,
            "decision": {
                "schema": "gt.interception_decision.v1",
                "mode": "PASS_THROUGH",
                "reason_codes": ["capability_repo_root_missing"],
            },
            "honesty": {"completeness": "incomplete"},
        }
    request = build_action_request(
        {
            "gt_action": {"kind": kind, "arguments": dict(arguments)},
            "tool_call_id": f"capability:{kind}",
        },
        repo_root=repo_root,
        configuration=configuration,
    )
    result = execute_typed_action(
        request, repo_root=repo_root, graph_db=graph_db_path(session) or None
    )
    try:
        return json.loads(result["output"])
    except (KeyError, TypeError, ValueError):
        return {
            "schema": "gt.compiled_observation.v1",
            "evidence": {"omissions": ["capability_result_undecodable"]},
            "direct_answer": None,
            "decision": {
                "schema": "gt.interception_decision.v1",
                "mode": "PASS_THROUGH",
                "reason_codes": ["capability_result_undecodable"],
            },
            "honesty": {"completeness": "incomplete"},
        }


def resolve_symbol_node(
    session: "GTSession",
    symbol: str,
    *,
    path: str = "",
    language: str = "",
) -> dict[str, Any] | None:
    """Resolve ``symbol`` to its graph node via the certified symbol_context
    query — the same ambiguity/preferred-order rules everywhere."""

    arguments: dict[str, Any] = {"symbol": symbol}
    if path:
        arguments["path"] = path
    if language:
        arguments["language"] = language
    result = run_typed(session, "symbol_context", arguments)
    answer = result.get("direct_answer")
    if isinstance(answer, dict):
        definition = answer.get("definition")
        if isinstance(definition, dict) and definition.get("id"):
            return definition
    return None


def last_journal_event(session: "GTSession", event: str) -> dict[str, Any] | None:
    """The most recent journal row of ``event`` type (read-only scan)."""

    store = getattr(engine_of(session), "store", None)
    path = getattr(store, "path", None)
    if path is None or not Path(path).is_file():
        return None
    last: dict[str, Any] | None = None
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("event") == event:
                    last = row
    except OSError:
        return None
    return last


def read_cas_blob(session: "GTSession", namespace: str, digest: str) -> bytes | None:
    store = getattr(engine_of(session), "store", None)
    root = getattr(store, "root", None)
    if root is None or not digest:
        return None
    target = Path(root) / namespace / f"{digest}.json"
    try:
        return target.read_bytes() if target.is_file() else None
    except OSError:
        return None
