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
import re
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


def _query_snapshot(session: "GTSession") -> Any:
    """The EngineState freshness snapshot, when the engine tracks one."""
    engine_state = getattr(engine_of(session), "engine_state", None)
    if engine_state is None:
        return None
    return engine_state.query_snapshot()


def graph_db_path(session: "GTSession") -> str:
    """The freshness-gated graph path — empty while the tracked graph is
    stale, exactly as ``EngineState.query_snapshot`` conceals it.

    Facades must not serve pre-edit graph rows as current; when the
    EngineState tracks a graph, only a current graph is visible here.
    An untracked ``engine.graph_db`` (bare/test sessions with no published
    graph identity) still falls through. The handle is also withheld when
    the ``graph_queries`` capability is off — the model-facing runtime
    applies the same gate, and a capability switched off is not fail-closed
    if the internal surface can still open the graph.
    """
    if not getattr(session, "capability_active", lambda _c: True)(
        "graph_queries"
    ):
        return ""
    engine = engine_of(session)
    engine_state = getattr(engine, "engine_state", None)
    if engine_state is not None and getattr(
        engine_state, "graph_path", ""
    ):
        snapshot = engine_state.query_snapshot()
        return snapshot.graph_path
    return str(getattr(engine, "graph_db", "") or "")


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
    return sqlite3.connect(graph.resolve().as_uri() + "?mode=ro", uri=True)


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
        _harness_args_from,
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
    # Bind the graph the same way the model-facing runtime does: the
    # RevisionVector.graph is the source revision the published graph was
    # built from, bound only while the graph is current — otherwise the
    # producer would report graph_revision_mismatch on every call, and a
    # stale graph would be queried as if fresh.
    snapshot = _query_snapshot(session)
    snapshot_current = bool(snapshot and snapshot.graph_current)
    # Mirror the model-facing runtime: the graph handle itself is withheld
    # when the graph_queries capability is off — the revision fields still
    # report snapshot truth, only the handle is gated.
    queries_active = bool(
        getattr(session, "capability_active", lambda _c: True)(
            "graph_queries")
    )
    effective_configuration = {
        "graph_db": (
            snapshot.graph_path
            if snapshot_current and queries_active
            else ""
        ),
        "graph_fresh": snapshot_current,
        "graph_source_revision": (
            snapshot.graph_source_revision if snapshot_current else ""
        ),
        "repository_revision": (
            snapshot.source_revision if snapshot is not None else ""
        ),
    }
    if configuration:
        effective_configuration.update(configuration)
    try:
        request = build_action_request(
            {
                "gt_action": {"kind": kind, "arguments": dict(arguments)},
                "tool_call_id": f"capability:{kind}",
            },
            repo_root=repo_root,
            configuration=effective_configuration,
        )
        result = execute_typed_action(
            request,
            repo_root=repo_root,
            graph_db=graph_db_path(session) or None,
            harness_args=_harness_args_from(arguments),
        )
    except Exception as exc:  # noqa: BLE001 - facades abstain, never raise
        return wrap(
            session, kind,
            status="error",
            omissions=(f"capability_dispatch_failed:{type(exc).__name__}",),
            semantics=semantics,
            provenance=provenance,
            started_ms=started,
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
    # Evidence omissions stay evidence-vocabulary; decision reason codes
    # (capability gates, refusals, budgets) are caveats on the answer, not
    # missing evidence, so they surface under ``limitations``.
    omissions = list(evidence.get("omissions") or ())
    limitations: list[str] = [
        str(reason) for reason in (decision.get("reason_codes") or ())
    ]
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


_CAS_DIGEST_RE = re.compile(r"[0-9a-f]{16,64}\Z")


def read_cas_blob(session: "GTSession", namespace: str, digest: str) -> bytes | None:
    store = getattr(engine_of(session), "store", None)
    root = getattr(store, "root", None)
    if root is None or not digest:
        return None
    # Strict digest/namespace shapes — both land in a filesystem path, so
    # anything outside the CAS token alphabet (``..``, separators) is not a
    # blob name at all.
    if not _CAS_DIGEST_RE.fullmatch(str(digest)):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(namespace)):
        return None
    target = Path(root) / str(namespace) / f"{digest}.json"
    try:
        return target.read_bytes() if target.is_file() else None
    except OSError:
        return None
