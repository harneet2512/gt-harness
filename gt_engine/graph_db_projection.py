"""Bounded process and impact projections over the certified SQLite graph.

This module is deliberately a reader, not another graph builder.  It opens the
database named by :class:`RepositoryGraphService` only after the service has
revalidated repository identity, graph checksum, and SQLite integrity.  Every
returned relationship is backed by a persisted row; bounded traversal is
always described as a lower bound.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.hybrid_repository import (
    _edge_resolution_provenance,
    _parse_edge_metadata,
)
from gt_engine.repository_graph_service import (
    GraphNotReadyError,
    GraphReceipt,
    RepositoryGraphService,
)


class ProjectionStatus(StrEnum):
    READY = "READY"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class GraphProjectionLimits:
    """Hard safety ceilings; callers may request only stricter bounds."""

    max_depth: int = 8
    max_branching: int = 4
    max_expansions: int = 128
    max_candidates: int = 24
    max_processes: int = 3
    max_impact_depth: int = 3

    def __post_init__(self) -> None:
        ceilings = {
            "max_depth": 8,
            "max_branching": 4,
            "max_expansions": 128,
            "max_candidates": 24,
            "max_processes": 3,
            "max_impact_depth": 3,
        }
        for name, ceiling in ceilings.items():
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, min(value, ceiling))

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True, order=True)
class GraphNodeEvidence:
    node_id: int
    label: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    return_type: str
    language: str
    is_test: bool


@dataclass(frozen=True, slots=True)
class GraphEdgeEvidence:
    edge_id: int
    relationship: str
    source_line: int
    source_file: str
    resolution_method: str
    resolution_outcome: str
    confidence: float
    trust_tier: str
    candidate_count: int
    evidence_type: str
    verification_status: str
    metadata: tuple[tuple[str, str], ...] = ()
    assertion_id: int = 0
    evidence_source: str = "edges"


@dataclass(frozen=True, slots=True)
class ProcessStep:
    source: GraphNodeEvidence
    target: GraphNodeEvidence
    receiver_type: str
    evidence: GraphEdgeEvidence


@dataclass(frozen=True, slots=True)
class ProcessPath:
    process_id: str
    steps: tuple[ProcessStep, ...]
    truncated: bool = False
    cycle_terminated: bool = False


@dataclass(frozen=True, slots=True)
class ProjectionReceipt:
    repository: str
    commit_sha: str
    source_revision: str
    graph_identity: str
    lower_bound: bool
    truncated: bool
    truncation_reasons: tuple[str, ...]
    limits: dict[str, int]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessProjection:
    status: ProjectionStatus
    symbol: str
    file_path: str
    anchor: GraphNodeEvidence | None
    ambiguous_candidates: tuple[GraphNodeEvidence, ...]
    processes: tuple[ProcessPath, ...]
    receipt: ProjectionReceipt

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = self.status.value
        return row


@dataclass(frozen=True, slots=True)
class CochangeRankEvidence:
    file_a: str
    file_b: str
    count: int
    evidence_source: str = "cochanges"


@dataclass(frozen=True, slots=True)
class ImpactFact:
    impact_id: str
    depth: int
    relationship: str
    source: GraphNodeEvidence
    target: GraphNodeEvidence
    impacted: GraphNodeEvidence
    receiver_type: str
    traversal_direction: str
    evidence: GraphEdgeEvidence
    rank_evidence: tuple[CochangeRankEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ImpactProjection:
    status: ProjectionStatus
    symbol: str
    file_path: str
    anchor: GraphNodeEvidence | None
    ambiguous_candidates: tuple[GraphNodeEvidence, ...]
    impacts: tuple[ImpactFact, ...]
    receipt: ProjectionReceipt

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = self.status.value
        return row


@dataclass(frozen=True, slots=True)
class _ExactEdge:
    source: GraphNodeEvidence
    target: GraphNodeEvidence
    receiver_type: str
    evidence: GraphEdgeEvidence


@dataclass(frozen=True, slots=True)
class _ImpactEdge:
    origin: GraphNodeEvidence
    impacted: GraphNodeEvidence
    source: GraphNodeEvidence
    target: GraphNodeEvidence
    receiver_type: str
    traversal_direction: str
    evidence: GraphEdgeEvidence


_IMPACT_RELATIONSHIPS = (
    "CALLS",
    "IMPORTS",
    "RE_EXPORTS",
    "EXTENDS",
    "IMPLEMENTS",
    "OVERRIDES",
    "TESTED_BY",
    "ASSERTED_BY",
)
_FORWARD_IMPACT_RELATIONSHIPS = frozenset({"TESTED_BY", "ASSERTED_BY"})


def _columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    try:
        return frozenset(str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")'))
    except sqlite3.Error:
        return frozenset()


def _node(row: sqlite3.Row, prefix: str = "") -> GraphNodeEvidence:
    return GraphNodeEvidence(
        node_id=int(row[f"{prefix}id"]),
        label=str(row[f"{prefix}label"] or ""),
        name=str(row[f"{prefix}name"] or ""),
        qualified_name=str(row[f"{prefix}qualified_name"] or ""),
        file_path=str(row[f"{prefix}file_path"] or "").replace("\\", "/"),
        start_line=max(0, int(row[f"{prefix}start_line"] or 0)),
        end_line=max(0, int(row[f"{prefix}end_line"] or 0)),
        signature=str(row[f"{prefix}signature"] or ""),
        return_type=str(row[f"{prefix}return_type"] or ""),
        language=str(row[f"{prefix}language"] or ""),
        is_test=bool(row[f"{prefix}is_test"]),
    )


def _stable_id(prefix: str, graph_identity: str, edge_ids: tuple[int, ...]) -> str:
    material = "\0".join((graph_identity, *(str(value) for value in edge_ids)))
    return prefix + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


class PersistedGraphProjector:
    """Project exact process paths directly from the current persisted graph."""

    def __init__(
        self,
        service: RepositoryGraphService,
        *,
        limits: GraphProjectionLimits | None = None,
    ) -> None:
        self.service = service
        self.limits = limits or GraphProjectionLimits()
        self._session_graph: GraphReceipt | None = None
        self._session_connection: sqlite3.Connection | None = None
        self._exact_calls_cache: tuple[
            tuple[_ExactEdge, ...], int, int, tuple[str, ...], dict[str, int]
        ] | None = None
        self._impact_edges_cache: tuple[
            tuple[_ImpactEdge, ...], dict[str, int], int, tuple[str, ...]
        ] | None = None

    def __enter__(self) -> PersistedGraphProjector:
        if self._session_connection is None:
            graph, connection = self._open()
            self._session_graph = graph
            self._session_connection = connection
        return self

    def __exit__(self, *_args: object) -> None:
        session_graph = self._session_graph
        if self._session_connection is not None:
            self._session_connection.close()
        self._session_graph = None
        self._session_connection = None
        self._exact_calls_cache = None
        self._impact_edges_cache = None
        if session_graph is not None:
            current = self.service.status()
            if (
                not current.query_ready
                or current.source_revision != session_graph.source_revision
                or current.graph_checksum_or_identity
                != session_graph.graph_checksum_or_identity
            ):
                reasons = ",".join(current.degraded_reasons) or current.build_status.value
                raise GraphNotReadyError(
                    "graph changed during projection session: " + reasons
                )

    def _open(self) -> tuple[GraphReceipt, sqlite3.Connection]:
        if self._session_graph is not None and self._session_connection is not None:
            return self._session_graph, self._session_connection
        receipt = self.service.status()
        if not receipt.query_ready:
            reasons = ",".join(receipt.degraded_reasons) or receipt.build_status.value
            raise GraphNotReadyError(f"graph is not query-ready: {reasons}")
        graph = Path(receipt.persistent_graph_path)
        connection = sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return receipt, connection

    def _receipt(
        self,
        graph: GraphReceipt,
        *,
        reasons: set[str],
        evidence: dict[str, Any],
    ) -> ProjectionReceipt:
        normalized = tuple(sorted(reasons))
        return ProjectionReceipt(
            repository=graph.repository,
            commit_sha=graph.commit_sha,
            source_revision=graph.source_revision,
            graph_identity=graph.graph_checksum_or_identity,
            lower_bound=True,
            truncated=bool(normalized),
            truncation_reasons=normalized,
            limits=self.limits.as_dict(),
            evidence=evidence,
        )

    @staticmethod
    def _resolve_anchor(
        connection: sqlite3.Connection,
        symbol: str,
        file_path: str,
    ) -> tuple[ProjectionStatus, GraphNodeEvidence | None, tuple[GraphNodeEvidence, ...]]:
        clause = "AND file_path=?" if file_path else ""
        parameters: tuple[Any, ...] = (symbol, symbol, file_path) if file_path else (symbol, symbol)
        rows = connection.execute(
            "SELECT id,label,name,qualified_name,file_path,start_line,end_line,signature,"
            "return_type,language,is_test FROM nodes "
            f"WHERE (name=? OR qualified_name=?) {clause} "
            "ORDER BY file_path,start_line,id",
            parameters,
        ).fetchall()
        exact_qualified = [row for row in rows if str(row["qualified_name"] or "") == symbol]
        candidates = tuple(_node(row) for row in (exact_qualified or rows))
        if not candidates:
            return ProjectionStatus.NOT_FOUND, None, ()
        if len(candidates) > 1:
            return ProjectionStatus.AMBIGUOUS, None, candidates
        return ProjectionStatus.READY, candidates[0], ()

    @staticmethod
    def _load_exact_calls(
        connection: sqlite3.Connection,
    ) -> tuple[
        tuple[_ExactEdge, ...],
        int,
        int,
        tuple[str, ...],
        dict[str, int],
    ]:
        edge_columns = _columns(connection, "edges")
        required = {
            "id",
            "source_id",
            "target_id",
            "type",
            "source_line",
            "source_file",
            "resolution_method",
            "confidence",
            "metadata",
            "trust_tier",
            "candidate_count",
            "evidence_type",
            "verification_status",
        }
        if not required <= edge_columns:
            return (), 0, 0, ("edges_exact_provenance",), {
                "resolved": 0,
                "ambiguous": 0,
                "external": 0,
                "unresolved": 0,
                "capped": 0,
            }
        normalized_metadata: dict[int, dict[str, str]] = defaultdict(dict)
        if {"edge_id", "key", "value"} <= _columns(connection, "edge_metadata"):
            for edge_id, key, value in connection.execute(
                "SELECT edge_id,key,value FROM edge_metadata ORDER BY edge_id,key"
            ):
                normalized_metadata[int(edge_id)][str(key)] = str(value)
        rows = connection.execute(
            "SELECT e.id AS edge_id,e.source_line,e.source_file,e.resolution_method,"
            "e.confidence,e.metadata,e.trust_tier,e.candidate_count,e.evidence_type,"
            "e.verification_status,"
            "s.id AS s_id,s.label AS s_label,s.name AS s_name,"
            "s.qualified_name AS s_qualified_name,s.file_path AS s_file_path,"
            "s.start_line AS s_start_line,s.end_line AS s_end_line,"
            "s.signature AS s_signature,s.return_type AS s_return_type,"
            "s.language AS s_language,s.is_test AS s_is_test,"
            "t.id AS t_id,t.label AS t_label,t.name AS t_name,"
            "t.qualified_name AS t_qualified_name,t.file_path AS t_file_path,"
            "t.start_line AS t_start_line,t.end_line AS t_end_line,"
            "t.signature AS t_signature,t.return_type AS t_return_type,"
            "t.language AS t_language,t.is_test AS t_is_test "
            "FROM edges e JOIN nodes s ON s.id=e.source_id "
            "JOIN nodes t ON t.id=e.target_id WHERE e.type='CALLS' ORDER BY e.id"
        ).fetchall()
        accepted: list[_ExactEdge] = []
        rejected = 0
        resolution_outcomes = {
            "resolved": 0,
            "ambiguous": 0,
            "external": 0,
            "unresolved": 0,
            "capped": 0,
        }
        for row in rows:
            origin, outcome, candidates = _edge_resolution_provenance(
                resolution_method=row["resolution_method"],
                candidate_count=row["candidate_count"],
                evidence_type=row["evidence_type"],
                verification_status=row["verification_status"],
                metadata=row["metadata"],
                provenance_available=True,
            )
            confidence = float(row["confidence"] or 0.0)
            trust = str(row["trust_tier"] or "")
            if not (
                origin == "program"
                and outcome == "exact"
                and candidates == 1
                and confidence >= 0.95
                and trust.upper() == "CERTIFIED"
            ):
                rejected += 1
                if outcome == "ambiguous":
                    resolution_outcomes["ambiguous"] += 1
                elif outcome == "external" or origin != "program":
                    resolution_outcomes["external"] += 1
                elif outcome == "unresolved":
                    resolution_outcomes["unresolved"] += 1
                else:
                    # A persisted target exists, but its proof is below the
                    # exact threshold (heuristic/dynamic/unknown/unverified).
                    resolution_outcomes["capped"] += 1
                continue
            edge_id = int(row["edge_id"])
            metadata = _parse_edge_metadata(row["metadata"])
            metadata.update(normalized_metadata.get(edge_id, {}))
            accepted.append(
                _ExactEdge(
                    source=_node(row, "s_"),
                    target=_node(row, "t_"),
                    receiver_type=str(metadata.get("receiver_type", "")),
                    evidence=GraphEdgeEvidence(
                        edge_id=edge_id,
                        relationship="CALLS",
                        source_line=max(0, int(row["source_line"] or 0)),
                        source_file=str(row["source_file"] or "").replace("\\", "/"),
                        resolution_method=str(row["resolution_method"] or ""),
                        resolution_outcome=outcome,
                        confidence=confidence,
                        trust_tier=trust,
                        candidate_count=int(candidates),
                        evidence_type=str(row["evidence_type"] or ""),
                        verification_status=str(row["verification_status"] or ""),
                        metadata=tuple(sorted(metadata.items())),
                    ),
                )
            )
            resolution_outcomes["resolved"] += 1
        return tuple(accepted), len(accepted), rejected, (), resolution_outcomes

    @staticmethod
    def _load_exact_impact_edges(
        connection: sqlite3.Connection,
    ) -> tuple[tuple[_ImpactEdge, ...], dict[str, int], int, tuple[str, ...]]:
        node_columns = _columns(connection, "nodes")
        required_nodes = {
            "id",
            "label",
            "name",
            "qualified_name",
            "file_path",
            "start_line",
            "end_line",
            "signature",
            "return_type",
            "language",
            "is_test",
        }
        edge_columns = _columns(connection, "edges")
        required_edges = {
            "id",
            "source_id",
            "target_id",
            "type",
            "source_line",
            "source_file",
            "resolution_method",
            "confidence",
            "metadata",
            "trust_tier",
            "candidate_count",
            "evidence_type",
            "verification_status",
        }
        unsupported: list[str] = []
        if not required_nodes <= node_columns:
            unsupported.append("nodes_projection_identity")
        if not required_edges <= edge_columns:
            unsupported.append("edges_exact_provenance")
        if unsupported:
            return (), {}, 0, tuple(unsupported)
        nodes = {
            int(row["id"]): _node(row)
            for row in connection.execute(
                "SELECT id,label,name,qualified_name,file_path,start_line,end_line,"
                "signature,return_type,language,is_test FROM nodes ORDER BY id"
            )
        }
        normalized_metadata: dict[int, dict[str, str]] = defaultdict(dict)
        if {"edge_id", "key", "value"} <= _columns(connection, "edge_metadata"):
            for edge_id, key, value in connection.execute(
                "SELECT edge_id,key,value FROM edge_metadata ORDER BY edge_id,key"
            ):
                normalized_metadata[int(edge_id)][str(key)] = str(value)
        placeholders = ",".join("?" for _ in _IMPACT_RELATIONSHIPS)
        rows = connection.execute(
            "SELECT id,source_id,target_id,type,source_line,source_file,"
            "resolution_method,confidence,metadata,trust_tier,candidate_count,"
            f"evidence_type,verification_status FROM edges WHERE type IN ({placeholders}) "
            "ORDER BY id",
            _IMPACT_RELATIONSHIPS,
        ).fetchall()
        accepted: list[_ImpactEdge] = []
        counts: dict[str, int] = defaultdict(int)
        rejected = 0
        for row in rows:
            source = nodes.get(int(row["source_id"] or 0))
            target = nodes.get(int(row["target_id"] or 0))
            if source is None or target is None:
                rejected += 1
                continue
            origin, outcome, candidates = _edge_resolution_provenance(
                resolution_method=row["resolution_method"],
                candidate_count=row["candidate_count"],
                evidence_type=row["evidence_type"],
                verification_status=row["verification_status"],
                metadata=row["metadata"],
                provenance_available=True,
            )
            confidence = float(row["confidence"] or 0.0)
            trust = str(row["trust_tier"] or "")
            if not (
                origin == "program"
                and outcome == "exact"
                and candidates == 1
                and confidence >= 0.95
                and trust.upper() == "CERTIFIED"
            ):
                rejected += 1
                continue
            edge_id = int(row["id"])
            relation = str(row["type"] or "").upper()
            metadata = _parse_edge_metadata(row["metadata"])
            metadata.update(normalized_metadata.get(edge_id, {}))
            forward = relation in _FORWARD_IMPACT_RELATIONSHIPS
            accepted.append(
                _ImpactEdge(
                    origin=source if forward else target,
                    impacted=target if forward else source,
                    source=source,
                    target=target,
                    receiver_type=str(metadata.get("receiver_type", "")),
                    traversal_direction="forward" if forward else "reverse",
                    evidence=GraphEdgeEvidence(
                        edge_id=edge_id,
                        relationship=relation,
                        source_line=max(0, int(row["source_line"] or 0)),
                        source_file=str(row["source_file"] or "").replace("\\", "/"),
                        resolution_method=str(row["resolution_method"] or ""),
                        resolution_outcome=outcome,
                        confidence=confidence,
                        trust_tier=trust,
                        candidate_count=int(candidates),
                        evidence_type=str(row["evidence_type"] or ""),
                        verification_status=str(row["verification_status"] or ""),
                        metadata=tuple(sorted(metadata.items())),
                    ),
                )
            )
            counts[relation] += 1

        assertion_columns = _columns(connection, "assertions")
        required_assertions = {
            "id",
            "test_node_id",
            "target_node_id",
            "resolution_score",
            "kind",
            "line",
        }
        if required_assertions <= assertion_columns:
            assertion_rows = connection.execute(
                "SELECT id,test_node_id,target_node_id,resolution_score,kind,line "
                "FROM assertions ORDER BY id"
            ).fetchall()
            for row in assertion_rows:
                source = nodes.get(int(row["target_node_id"] or 0))
                target = nodes.get(int(row["test_node_id"] or 0))
                score = float(row["resolution_score"] or 0.0)
                if source is None or target is None or score < 0.95:
                    rejected += 1
                    continue
                assertion_id = int(row["id"])
                accepted.append(
                    _ImpactEdge(
                        origin=source,
                        impacted=target,
                        source=source,
                        target=target,
                        receiver_type="",
                        traversal_direction="forward",
                        evidence=GraphEdgeEvidence(
                            edge_id=0,
                            relationship="ASSERTED_BY",
                            source_line=max(0, int(row["line"] or 0)),
                            source_file=target.file_path,
                            resolution_method="assertion_target",
                            resolution_outcome="exact",
                            confidence=score,
                            trust_tier="CERTIFIED",
                            candidate_count=1,
                            evidence_type=str(row["kind"] or "assertion"),
                            verification_status="verified",
                            assertion_id=assertion_id,
                            evidence_source="assertions",
                        ),
                    )
                )
                counts["ASSERTED_BY"] += 1
        return tuple(accepted), dict(sorted(counts.items())), rejected, ()

    @staticmethod
    def _cochange_rank_evidence(
        connection: sqlite3.Connection,
        anchor_file: str,
    ) -> tuple[dict[str, tuple[CochangeRankEvidence, ...]], int]:
        if not {"file_a", "file_b", "count"} <= _columns(connection, "cochanges"):
            return {}, 0
        normalized_anchor = str(anchor_file or "").replace("\\", "/")
        by_path: dict[str, list[CochangeRankEvidence]] = defaultdict(list)
        rows = connection.execute(
            "SELECT file_a,file_b,count FROM cochanges "
            "WHERE file_a=? OR file_b=? ORDER BY count DESC,file_a,file_b",
            (normalized_anchor, normalized_anchor),
        ).fetchall()
        for row in rows:
            file_a = str(row["file_a"] or "").replace("\\", "/")
            file_b = str(row["file_b"] or "").replace("\\", "/")
            other = file_b if file_a == normalized_anchor else file_a
            if not other:
                continue
            by_path[other].append(
                CochangeRankEvidence(
                    file_a=file_a,
                    file_b=file_b,
                    count=max(0, int(row["count"] or 0)),
                )
            )
        return {key: tuple(value) for key, value in by_path.items()}, len(rows)

    @staticmethod
    def _edge_order(edge: _ExactEdge) -> tuple[str, int, str, int]:
        return (
            edge.target.file_path.casefold(),
            edge.target.start_line,
            edge.target.qualified_name or edge.target.name,
            edge.evidence.edge_id,
        )

    def project_processes(
        self,
        symbol: str,
        *,
        file_path: str | None = None,
    ) -> ProcessProjection:
        token = str(symbol or "").strip()
        selected_file = str(file_path or "").strip().replace("\\", "/")
        graph, connection = self._open()
        session_connection = connection is self._session_connection
        try:
            status, anchor, ambiguous = self._resolve_anchor(connection, token, selected_file)
            if self._exact_calls_cache is None:
                self._exact_calls_cache = self._load_exact_calls(connection)
            (
                calls,
                exact_count,
                rejected_count,
                unsupported,
                resolution_outcomes,
            ) = self._exact_calls_cache
        finally:
            if not session_connection:
                connection.close()
        reasons: set[str] = set()
        evidence: dict[str, Any] = {
            "exact_calls": exact_count,
            "rejected_calls": rejected_count,
            "expansions": 0,
            "candidate_paths": 0,
            "returned_paths": 0,
            "unsupported_surfaces": list(unsupported),
            "receiver_resolution_outcomes": resolution_outcomes,
            "receiver_resolution_coverage": "persisted_edges_only",
        }
        if unsupported:
            reasons.add("exact_call_provenance_unavailable")
        if status is not ProjectionStatus.READY or anchor is None:
            return ProcessProjection(
                status=status,
                symbol=token,
                file_path=selected_file,
                anchor=None,
                ambiguous_candidates=ambiguous,
                processes=(),
                receipt=self._receipt(graph, reasons=reasons, evidence=evidence),
            )

        adjacency: dict[int, list[_ExactEdge]] = defaultdict(list)
        reverse: dict[int, list[_ExactEdge]] = defaultdict(list)
        for edge in calls:
            adjacency[edge.source.node_id].append(edge)
            reverse[edge.target.node_id].append(edge)
        for rows in (*adjacency.values(), *reverse.values()):
            rows.sort(key=self._edge_order)

        expansions = 0

        def bounded(rows: list[_ExactEdge]) -> tuple[_ExactEdge, ...]:
            nonlocal expansions
            available = max(0, self.limits.max_expansions - expansions)
            selected = tuple(rows[: min(self.limits.max_branching, available)])
            expansions += len(selected)
            if len(rows) > self.limits.max_branching:
                reasons.add("branch_limit")
            if len(selected) < min(len(rows), self.limits.max_branching):
                reasons.add("expansion_limit")
            return selected

        prefixes: list[tuple[tuple[_ExactEdge, ...], bool, bool]] = []
        upstream: deque[tuple[int, tuple[_ExactEdge, ...], frozenset[int]]] = deque(
            [(anchor.node_id, (), frozenset({anchor.node_id}))]
        )
        while upstream and expansions < self.limits.max_expansions:
            current, path, visited = upstream.popleft()
            rows = bounded(reverse.get(current, []))
            if not rows:
                prefixes.append((path, False, False))
                continue
            for edge in rows:
                next_path = (edge, *path)
                cycle = edge.source.node_id in visited
                depth = len(next_path) >= self.limits.max_depth
                root = not reverse.get(edge.source.node_id)
                if cycle or depth or root:
                    prefixes.append((next_path, depth, cycle))
                    reasons.update(("cycle",) if cycle else ())
                    reasons.update(("depth_limit",) if depth else ())
                else:
                    upstream.append(
                        (edge.source.node_id, next_path, visited | {edge.source.node_id})
                    )
            if len(prefixes) >= self.limits.max_candidates:
                reasons.add("candidate_limit")
                break
        if upstream:
            reasons.add("expansion_limit")
        if not prefixes:
            prefixes.append(((), False, False))

        candidates: list[tuple[tuple[_ExactEdge, ...], bool, bool]] = []
        for prefix, prefix_depth, prefix_cycle in prefixes:
            if len(candidates) >= self.limits.max_candidates:
                reasons.add("candidate_limit")
                break
            if prefix_depth or prefix_cycle or len(prefix) >= self.limits.max_depth:
                if prefix:
                    candidates.append((prefix, prefix_depth, prefix_cycle))
                continue
            downstream: deque[tuple[int, tuple[_ExactEdge, ...], frozenset[int]]] = deque(
                [
                    (
                        anchor.node_id,
                        (),
                        frozenset(
                            {
                                anchor.node_id,
                                *(edge.source.node_id for edge in prefix),
                            }
                        ),
                    )
                ]
            )
            emitted = False
            while downstream and expansions < self.limits.max_expansions:
                current, suffix, visited = downstream.popleft()
                rows = bounded(adjacency.get(current, []))
                if not rows:
                    if prefix or suffix:
                        candidates.append(((*prefix, *suffix), False, False))
                        emitted = True
                    if len(candidates) >= self.limits.max_candidates:
                        reasons.add("candidate_limit")
                        break
                    continue
                for edge in rows:
                    next_suffix = (*suffix, edge)
                    full_depth = len(prefix) + len(next_suffix)
                    cycle = edge.target.node_id in visited
                    depth = full_depth >= self.limits.max_depth
                    terminal = not adjacency.get(edge.target.node_id)
                    if cycle or depth or terminal:
                        candidates.append(((*prefix, *next_suffix), depth, cycle))
                        emitted = True
                        reasons.update(("cycle",) if cycle else ())
                        reasons.update(("depth_limit",) if depth else ())
                    else:
                        downstream.append(
                            (edge.target.node_id, next_suffix, visited | {edge.target.node_id})
                        )
                    if len(candidates) >= self.limits.max_candidates:
                        reasons.add("candidate_limit")
                        break
                if len(candidates) >= self.limits.max_candidates:
                    break
            if downstream:
                reasons.add("expansion_limit")
            if not emitted and prefix:
                candidates.append((prefix, False, False))

        unique: dict[tuple[int, ...], tuple[tuple[_ExactEdge, ...], bool, bool]] = {}
        for candidate in candidates:
            key = tuple(edge.evidence.edge_id for edge in candidate[0])
            if key:
                unique.setdefault(key, candidate)

        def base_rank(
            candidate: tuple[tuple[_ExactEdge, ...], bool, bool],
        ) -> tuple[int, int, tuple[int, ...]]:
            path, truncated, cycle = candidate
            return (
                int(truncated or cycle),
                -len(path),
                tuple(edge.evidence.edge_id for edge in path),
            )

        remaining = sorted(unique.values(), key=base_rank)
        selected: list[tuple[tuple[_ExactEdge, ...], bool, bool]] = []
        covered: set[int] = set()
        while remaining and len(selected) < self.limits.max_processes:
            candidate = min(
                remaining,
                key=lambda item: (
                    -len({edge.evidence.edge_id for edge in item[0]} - covered),
                    base_rank(item),
                ),
            )
            remaining.remove(candidate)
            selected.append(candidate)
            covered.update(edge.evidence.edge_id for edge in candidate[0])
        if remaining:
            reasons.add("process_limit")

        processes = tuple(
            ProcessPath(
                process_id=_stable_id(
                    "gt-process-",
                    graph.graph_checksum_or_identity,
                    tuple(edge.evidence.edge_id for edge in path),
                ),
                steps=tuple(
                    ProcessStep(
                        source=edge.source,
                        target=edge.target,
                        receiver_type=edge.receiver_type,
                        evidence=edge.evidence,
                    )
                    for edge in path
                ),
                truncated=truncated,
                cycle_terminated=cycle,
            )
            for path, truncated, cycle in selected
        )
        evidence.update(
            {
                "expansions": expansions,
                "candidate_paths": len(unique),
                "returned_paths": len(processes),
                "omitted_candidates": max(0, len(unique) - len(processes)),
                "returned_edge_ids": sorted(
                    {step.evidence.edge_id for process in processes for step in process.steps}
                ),
                "typed_receiver_outcomes": [
                    {"edge_id": edge_id, "receiver_type": receiver_type}
                    for edge_id, receiver_type in sorted(
                        {
                            (step.evidence.edge_id, step.receiver_type)
                            for process in processes
                            for step in process.steps
                            if step.receiver_type
                        }
                    )
                ],
            }
        )
        return ProcessProjection(
            status=ProjectionStatus.READY,
            symbol=token,
            file_path=selected_file,
            anchor=anchor,
            ambiguous_candidates=(),
            processes=processes,
            receipt=self._receipt(graph, reasons=reasons, evidence=evidence),
        )

    def project_impact(
        self,
        symbol: str,
        *,
        file_path: str | None = None,
    ) -> ImpactProjection:
        token = str(symbol or "").strip()
        selected_file = str(file_path or "").strip().replace("\\", "/")
        graph, connection = self._open()
        session_connection = connection is self._session_connection
        try:
            status, anchor, ambiguous = self._resolve_anchor(connection, token, selected_file)
            if self._impact_edges_cache is None:
                self._impact_edges_cache = self._load_exact_impact_edges(connection)
            relationships, exact_counts, rejected, unsupported = self._impact_edges_cache
            cochange_by_path, cochange_count = (
                self._cochange_rank_evidence(connection, anchor.file_path)
                if anchor is not None
                else ({}, 0)
            )
        finally:
            if not session_connection:
                connection.close()
        reasons: set[str] = set()
        evidence: dict[str, Any] = {
            "exact_relationships": exact_counts,
            "rejected_relationships": rejected,
            "expansions": 0,
            "candidate_impacts": 0,
            "returned_impacts": 0,
            "cochange_rank_only": True,
            "cochange_pairs_considered": cochange_count,
            "unsupported_surfaces": list(unsupported),
        }
        if unsupported:
            reasons.add("exact_impact_provenance_unavailable")
        if status is not ProjectionStatus.READY or anchor is None:
            return ImpactProjection(
                status=status,
                symbol=token,
                file_path=selected_file,
                anchor=None,
                ambiguous_candidates=ambiguous,
                impacts=(),
                receipt=self._receipt(graph, reasons=reasons, evidence=evidence),
            )

        adjacency: dict[int, list[_ImpactEdge]] = defaultdict(list)
        for relationship in relationships:
            adjacency[relationship.origin.node_id].append(relationship)
        for rows in adjacency.values():
            rows.sort(
                key=lambda item: (
                    _IMPACT_RELATIONSHIPS.index(item.evidence.relationship),
                    item.impacted.file_path.casefold(),
                    item.impacted.start_line,
                    item.evidence.edge_id,
                    item.evidence.assertion_id,
                )
            )

        queue: deque[tuple[GraphNodeEvidence, int]] = deque([(anchor, 0)])
        visited = {anchor.node_id}
        candidates: list[ImpactFact] = []
        expansions = 0
        while queue and expansions < self.limits.max_expansions:
            current, depth = queue.popleft()
            if depth >= self.limits.max_impact_depth:
                if adjacency.get(current.node_id):
                    reasons.add("impact_depth_limit")
                continue
            for relationship in adjacency.get(current.node_id, ()):
                if expansions >= self.limits.max_expansions:
                    reasons.add("impact_expansion_limit")
                    break
                expansions += 1
                next_depth = depth + 1
                candidates.append(
                    ImpactFact(
                        impact_id=_stable_id(
                            "gt-impact-",
                            graph.graph_checksum_or_identity,
                            (
                                anchor.node_id,
                                relationship.impacted.node_id,
                                next_depth,
                                relationship.evidence.edge_id,
                                relationship.evidence.assertion_id,
                            ),
                        ),
                        depth=next_depth,
                        relationship=relationship.evidence.relationship,
                        source=relationship.source,
                        target=relationship.target,
                        impacted=relationship.impacted,
                        receiver_type=relationship.receiver_type,
                        traversal_direction=relationship.traversal_direction,
                        evidence=relationship.evidence,
                        rank_evidence=cochange_by_path.get(relationship.impacted.file_path, ()),
                    )
                )
                if relationship.impacted.node_id not in visited:
                    visited.add(relationship.impacted.node_id)
                    queue.append((relationship.impacted, next_depth))
                if len(candidates) >= self.limits.max_candidates:
                    reasons.add("impact_candidate_limit")
                    break
            if len(candidates) >= self.limits.max_candidates:
                break
        if queue and expansions >= self.limits.max_expansions:
            reasons.add("impact_expansion_limit")

        unique: dict[str, ImpactFact] = {}
        for impact in candidates:
            unique.setdefault(impact.impact_id, impact)

        def rank(impact: ImpactFact) -> tuple[int, int, int, str, int, str]:
            cochange = max(
                (item.count for item in impact.rank_evidence),
                default=0,
            )
            return (
                impact.depth,
                -int(impact.impacted.is_test),
                -cochange,
                impact.impacted.file_path.casefold(),
                impact.impacted.start_line,
                impact.impact_id,
            )

        impacts = tuple(sorted(unique.values(), key=rank))
        evidence.update(
            {
                "expansions": expansions,
                "candidate_impacts": len(candidates),
                "returned_impacts": len(impacts),
                "ranked_impacts": sum(bool(item.rank_evidence) for item in impacts),
                "returned_evidence_ids": sorted(
                    {
                        (
                            f"assertion:{item.evidence.assertion_id}"
                            if item.evidence.assertion_id
                            else f"edge:{item.evidence.edge_id}"
                        )
                        for item in impacts
                    }
                ),
                "rank_only_cochange_evidence": [
                    asdict(item)
                    for item in sorted(
                        {item for rows in cochange_by_path.values() for item in rows},
                        key=lambda item: (-item.count, item.file_a, item.file_b),
                    )
                ],
            }
        )
        return ImpactProjection(
            status=ProjectionStatus.READY,
            symbol=token,
            file_path=selected_file,
            anchor=anchor,
            ambiguous_candidates=(),
            impacts=impacts,
            receipt=self._receipt(graph, reasons=reasons, evidence=evidence),
        )


__all__ = [
    "CochangeRankEvidence",
    "GraphEdgeEvidence",
    "GraphNodeEvidence",
    "GraphProjectionLimits",
    "ImpactFact",
    "ImpactProjection",
    "PersistedGraphProjector",
    "ProcessPath",
    "ProcessProjection",
    "ProcessStep",
    "ProjectionReceipt",
    "ProjectionStatus",
]
