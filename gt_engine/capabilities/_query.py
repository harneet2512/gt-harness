"""Shared internals for the capability facades.

Every typed-kind facade runs through the SAME pipeline the model-facing
groundtruth action uses — ``build_action_request`` + ``execute_typed_action``
— so certification gates, language refusals, demotion rules, and bounded
output are identical between the internal API and what a model could call.

Every facade returns one :class:`CapabilityResult`; nothing in this package
emits model-facing text or calls admission.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """The one envelope every capability facade returns (plan item D)."""

    capability: str
    status: str  # ok | partial | abstain | unavailable | error
    answer: Any = None
    omissions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    semantics: str = "partial"  # exact | partial | heuristic
    graph_revision: str = ""
    source_revision: str = ""
    fresh: bool = False
    cost: dict[str, int] = field(default_factory=dict)
    provenance: str = ""


def engine_of(session: "GTSession") -> Any:
    return session._engine


def _revisions(session: "GTSession") -> tuple[str, str, bool]:
    """(graph_revision, source_revision, graph_current) from EngineState."""

    engine_state = getattr(engine_of(session), "engine_state", None)
    if engine_state is None:
        return "", "", False
    return (
        str(getattr(engine_state, "graph_revision", "") or ""),
        str(getattr(engine_state, "source_revision", "")
            or getattr(engine_of(session), "repository_revision", "") or ""),
        bool(getattr(engine_state, "graph_current", False)),
    )


def wrap(
    session: "GTSession",
    capability: str,
    *,
    answer: Any = None,
    status: str = "ok",
    omissions: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    semantics: str = "partial",
    provenance: str = "",
    started_ms: float | None = None,
) -> CapabilityResult:
    """Assemble the uniform envelope: revisions from EngineState, cost from
    the measured call duration and canonical answer bytes."""

    graph_revision, source_revision, fresh = _revisions(session)
    elapsed = 0 if started_ms is None else max(
        0, int(round((time.perf_counter() - started_ms) * 1000))
    )
    try:
        output_bytes = len(
            json.dumps(answer, sort_keys=True, default=str).encode("utf-8")
        )
    except (TypeError, ValueError):
        output_bytes = 0
    return CapabilityResult(
        capability=capability,
        status=status,
        answer=answer,
        omissions=tuple(omissions),
        limitations=tuple(limitations),
        semantics=semantics,
        graph_revision=graph_revision,
        source_revision=source_revision,
        fresh=fresh,
        cost={"elapsed_ms": elapsed, "output_bytes": output_bytes},
        provenance=provenance,
    )


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


_SEMANTICS_MAP = {
    "exact": "exact",
    "partial": "partial",
    "execution_specific": "heuristic",
    "sound_overapprox": "heuristic",
}


def run_typed(
    session: "GTSession",
    kind: str,
    arguments: dict[str, Any],
    *,
    configuration: dict[str, Any] | None = None,
) -> CapabilityResult:
    """Execute a certified typed kind through the canonical dispatcher.

    Same pipeline as the model-facing action — certification gates,
    language refusals, demotion, bounded output — then folded into the
    uniform :class:`CapabilityResult` envelope.
    """

    from gt_engine.generated_typed_capabilities import (
        CERTIFIED_TYPED_KIND_SEMANTICS,
    )
    from gt_engine.miniswe_typed_actions import (
        build_action_request,
        execute_typed_action,
    )

    started = time.perf_counter()
    engine = engine_of(session)
    repo_root = str(getattr(engine, "repo_root", "") or "")
    semantics = _SEMANTICS_MAP.get(
        CERTIFIED_TYPED_KIND_SEMANTICS.get(kind, "partial"), "partial"
    )
    provenance = f"gt_engine.miniswe_typed_actions.execute_typed_action:{kind}"
    if not repo_root:
        return wrap(
            session, kind,
            status="unavailable",
            omissions=("capability_repo_root_missing",),
            semantics=semantics,
            provenance=provenance,
            started_ms=started,
        )
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
        observation = json.loads(result["output"])
    except (KeyError, TypeError, ValueError):
        return wrap(
            session, kind,
            status="error",
            omissions=("capability_result_undecodable",),
            semantics=semantics,
            provenance=provenance,
            started_ms=started,
        )
    evidence = observation.get("evidence")
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    decision = observation.get("decision")
    decision = dict(decision) if isinstance(decision, dict) else {}
    honesty = observation.get("honesty")
    honesty = dict(honesty) if isinstance(honesty, dict) else {}
    mode = str(decision.get("mode") or "")
    status = {
        "REPLACE": "ok",
        "AUGMENT": "partial",
        "PASS_THROUGH": "abstain",
    }.get(mode, "partial")
    omissions = list(evidence.get("omissions") or ())
    for reason in decision.get("reason_codes") or ():
        if reason not in omissions:
            omissions.append(reason)
    limitations: list[str] = []
    if honesty.get("abstention_reason"):
        limitations.append(str(honesty["abstention_reason"]))
    limitations.extend(
        str(value) for value in (honesty.get("unresolved_identities") or ())
    )
    answer = observation.get("direct_answer")
    if answer is None:
        answer = evidence.get("answer")
    return wrap(
        session, kind,
        answer=answer,
        status=status,
        omissions=tuple(omissions),
        limitations=tuple(limitations),
        semantics=semantics,
        provenance=provenance,
        started_ms=started,
    )


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
    answer = result.answer
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
