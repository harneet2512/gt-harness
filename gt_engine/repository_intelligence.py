"""Central runtime reconciliation for repository and graph identity."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass

from gt_engine.graph_lease import GraphLease
from gt_engine.persistent_execution_state import ExecutionStateSnapshot


class CentralRuntimeIdentityError(ValueError):
    """Raised when graph lease and execution state disagree on revision identity."""


@dataclass(frozen=True, slots=True)
class CentralRuntimeBinding:
    lease: GraphLease
    execution_state: ExecutionStateSnapshot

    @property
    def repository_revision(self) -> str:
        return self.lease.graph_repository_revision

    @property
    def workspace_revision(self) -> str:
        return self.lease.workspace_revision

    @property
    def graph_revision(self) -> str:
        return self.lease.graph_revision

    @property
    def graph_path(self) -> str:
        return self.lease.graph_path


def bind_central_runtime(
    *,
    repository_revision: str,
    workspace_revision: str,
    graph_revision: str,
    graph_path: str,
    execution_state: ExecutionStateSnapshot | None = None,
) -> CentralRuntimeBinding:
    """Bind graph lease and execution state to one repository identity.

    Every named runtime service must resolve the same revision tuple. Mismatched
    persisted execution state is rejected fail-closed instead of silently reused.
    """

    lease = GraphLease.current(
        graph_repository_revision=repository_revision,
        workspace_revision=workspace_revision,
        graph_revision=graph_revision,
        graph_path=graph_path,
    )
    state = execution_state or ExecutionStateSnapshot(
        repository_revision=repository_revision,
        workspace_revision=workspace_revision,
        graph_revision=graph_revision,
        graph_path=graph_path,
    )
    if state.repository_revision != lease.graph_repository_revision:
        raise CentralRuntimeIdentityError("repository_revision mismatch")
    if state.workspace_revision != lease.workspace_revision:
        raise CentralRuntimeIdentityError("workspace_revision mismatch")
    if state.graph_revision != lease.graph_revision:
        raise CentralRuntimeIdentityError("graph_revision mismatch")
    if state.graph_path != lease.graph_path:
        raise CentralRuntimeIdentityError("graph_path mismatch")
    return CentralRuntimeBinding(lease=lease, execution_state=state)


@dataclass(frozen=True, slots=True)
class CommunityEdge:
    """Graph-native edge input for the two community projections."""

    source: str
    target: str
    kind: str
    evidence_label: str
    edge_id: str

    def __post_init__(self) -> None:
        if not all((self.source, self.target, self.kind, self.evidence_label, self.edge_id)):
            raise ValueError("community edge fields must be non-empty")
        if self.source == self.target:
            raise ValueError("self-loops are not community edges")


@dataclass(frozen=True, slots=True)
class CommunityProjection:
    projection_id: str
    revision: str
    algorithm_version: str
    seed: int
    resolution: float
    input_digest: str
    memberships: tuple[tuple[str, ...], ...]
    community_ids: tuple[str, ...]
    excluded_edges: tuple[str, ...]
    cross_community_edges: tuple[str, ...]

    @property
    def receipt(self) -> dict[str, object]:
        return {
            "projection_id": self.projection_id,
            "revision": self.revision,
            "algorithm_version": self.algorithm_version,
            "seed": self.seed,
            "resolution": self.resolution,
            "input_digest": self.input_digest,
            "memberships": self.memberships,
            "community_ids": self.community_ids,
            "excluded_edges": self.excluded_edges,
            "cross_community_edges": self.cross_community_edges,
        }


@dataclass(frozen=True, slots=True)
class CommunityRun:
    revision: str
    input_digest: str
    strict: CommunityProjection
    inclusive: CommunityProjection


_COMMUNITY_ALGORITHM = "gt.deterministic-modularity-local-moving.v1"
_STRICT_LABELS = frozenset({"verified", "verified_edge", "verified_exact"})
_INCLUSIVE_LABELS = frozenset(
    {
        "verified",
        "verified_edge",
        "verified_exact",
        "ast_lexical_exact",
        "ast_member_exact",
        "ast_import_explicit",
        "ast_inheritance_exact",
        "scope_unique_name",
        "scope_ambiguous_name",
        "dynamic_dispatch_set",
        "candidate",
    }
)


def _community_digest(nodes: Iterable[str], edges: Iterable[CommunityEdge]) -> str:
    payload = {
        "nodes": sorted(set(nodes)),
        "edges": sorted(
            (
                edge.edge_id,
                edge.source,
                edge.target,
                edge.kind,
                edge.evidence_label,
            )
            for edge in edges
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _leiden_partition(
    nodes: tuple[str, ...],
    edges: tuple[CommunityEdge, ...],
    *,
    seed: int,
    resolution: float,
) -> dict[str, str]:
    """Deterministic single-level modularity local-moving optimization."""
    adjacency: dict[str, dict[str, float]] = {node: {} for node in nodes}
    for edge in edges:
        adjacency[edge.source][edge.target] = (
            adjacency[edge.source].get(edge.target, 0.0) + 1.0
        )
        adjacency[edge.target][edge.source] = (
            adjacency[edge.target].get(edge.source, 0.0) + 1.0
        )

    total_weight = (
        sum(weight for neighbors in adjacency.values() for weight in neighbors.values())
        / 2.0
    )
    if total_weight == 0.0:
        return {node: node for node in nodes}

    degree = {node: sum(adjacency[node].values()) for node in nodes}
    community = {node: node for node in nodes}
    ordered_nodes = tuple(sorted(nodes))
    if len(ordered_nodes) > 1:
        pivot = seed % len(ordered_nodes)
        ordered_nodes = ordered_nodes[pivot:] + ordered_nodes[:pivot]

    def modularity_delta(node: str, target_community: str) -> float:
        current_community = community[node]
        if current_community == target_community:
            return 0.0

        def score(mapping: dict[str, str]) -> float:
            internal = sum(
                1.0 for edge in edges if mapping[edge.source] == mapping[edge.target]
            )
            totals: dict[str, float] = {}
            for member, label in mapping.items():
                totals[label] = totals.get(label, 0.0) + degree[member]
            expected = sum(
                (community_degree / (2.0 * total_weight)) ** 2
                for community_degree in totals.values()
            )
            return internal / total_weight - resolution * expected

        candidate_mapping = dict(community)
        candidate_mapping[node] = target_community
        return score(candidate_mapping) - score(community)

    improved = True
    passes = 0
    while improved and passes < len(nodes) * 4:
        improved = False
        passes += 1
        for node in ordered_nodes:
            current_community = community[node]
            candidates = {community[neighbor] for neighbor in adjacency[node]}
            candidates.add(current_community)
            best_community = current_community
            best_delta = 0.0
            for candidate in sorted(candidates):
                gain = modularity_delta(node, candidate)
                if gain > best_delta + 1e-12:
                    best_delta = gain
                    best_community = candidate
            if best_community != current_community:
                community[node] = best_community
                improved = True
    groups: dict[str, list[str]] = {}
    for node in nodes:
        groups.setdefault(community[node], []).append(node)
    return {node: min(sorted(group)) for node in nodes for group in [groups[community[node]]]}


def _projection(
    *,
    projection_id: str,
    nodes: tuple[str, ...],
    edges: tuple[CommunityEdge, ...],
    revision: str,
    seed: int,
    resolution: float,
    input_digest: str,
    admitted_labels: frozenset[str],
) -> CommunityProjection:
    admitted: list[CommunityEdge] = []
    excluded: list[str] = []
    for edge in edges:
        if edge.evidence_label in admitted_labels:
            admitted.append(edge)
        else:
            excluded.append(edge.edge_id)

    community_map = _leiden_partition(
        nodes,
        tuple(admitted),
        seed=seed,
        resolution=resolution,
    )
    groups: dict[str, list[str]] = {}
    for node in nodes:
        groups.setdefault(community_map[node], []).append(node)
    memberships = tuple(sorted(tuple(sorted(group)) for group in groups.values()))
    community_ids = tuple(
        hashlib.sha256(
            f"{projection_id}\0{revision}\0{','.join(members)}".encode()
        ).hexdigest()
        for members in memberships
    )
    membership_by_node = {
        node: community_ids[index]
        for index, members in enumerate(memberships)
        for node in members
    }
    cross = tuple(
        sorted(
            edge.edge_id
            for edge in admitted
            if membership_by_node[edge.source] != membership_by_node[edge.target]
        )
    )
    return CommunityProjection(
        projection_id=projection_id,
        revision=revision,
        algorithm_version=_COMMUNITY_ALGORITHM,
        seed=seed,
        resolution=resolution,
        input_digest=input_digest,
        memberships=memberships,
        community_ids=community_ids,
        excluded_edges=tuple(sorted(excluded)),
        cross_community_edges=cross,
    )


def build_leiden_communities(
    *,
    nodes: Iterable[str],
    edges: Iterable[CommunityEdge],
    revision: str,
    seed: int = 2512,
    resolution: float = 1.0,
) -> CommunityRun:
    """Build reproducible strict and inclusive community projections.

    Both projections run deterministic modularity local-moving optimization with
    the pinned seed and resolution, so the partition can split connected graphs
    when the bridge is weaker than intra-community density.
    """

    normalized_nodes = tuple(sorted(set(str(node) for node in nodes)))
    if not normalized_nodes or any(not node for node in normalized_nodes):
        raise ValueError("nodes must contain non-empty identities")
    if not revision:
        raise ValueError("revision is required")
    if seed < 0 or not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("seed and resolution are invalid")
    normalized_edges = tuple(
        sorted(
            edges,
            key=lambda edge: (
                edge.edge_id,
                edge.source,
                edge.target,
                edge.kind,
                edge.evidence_label,
            ),
        )
    )
    unknown_nodes = {
        endpoint
        for edge in normalized_edges
        for endpoint in (edge.source, edge.target)
        if endpoint not in normalized_nodes
    }
    if unknown_nodes:
        raise ValueError(f"edge references unknown nodes: {sorted(unknown_nodes)!r}")
    digest = _community_digest(normalized_nodes, normalized_edges)
    return CommunityRun(
        revision=revision,
        input_digest=digest,
        strict=_projection(
            projection_id="strict_verified_v1",
            nodes=normalized_nodes,
            edges=normalized_edges,
            revision=revision,
            seed=seed,
            resolution=resolution,
            input_digest=digest,
            admitted_labels=_STRICT_LABELS,
        ),
        inclusive=_projection(
            projection_id="inclusive_labeled_v1",
            nodes=normalized_nodes,
            edges=normalized_edges,
            revision=revision,
            seed=seed,
            resolution=resolution,
            input_digest=digest,
            admitted_labels=_INCLUSIVE_LABELS,
        ),
    )
