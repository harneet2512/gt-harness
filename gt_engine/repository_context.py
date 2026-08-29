"""Certified, bounded repository context projected at the provider seam.

This module is pure composition over GT's existing repository substrate.  It
does not parse source, invoke a model, execute a tool, or invent missing graph
edges.  Unknown, ambiguous, external, stale, or content-unbound relationships
remain ranking evidence and cannot enter execution or impact projections.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

from gt_engine.contributions import (
    ContributionKind,
    GTContribution,
    build_provider_value_certificates,
)
from gt_engine.hybrid_retrieval import (
    EvidenceOrigin,
    RelationUse,
    StructuralLink,
    certify_structural_link,
)
from gt_engine.repository_intelligence import RepositoryEvidence
from gt_engine.semantic_evidence import (
    SemanticEvidenceBridge,
    SemanticEvidenceResult,
    SemanticEvidenceStatus,
)


class RepositoryContextStatus(StrEnum):
    DELIVER = "deliver"
    ABSTAIN = "abstain"


class EpistemicCompleteness(StrEnum):
    EXACT = "exact"
    LOWER_BOUND = "lower_bound"
    PARTIAL = "partial"
    TRUNCATED = "truncated"


@dataclass(frozen=True, slots=True)
class EpistemicEnvelope:
    completeness: EpistemicCompleteness
    truncated_count: int
    rejected_edge_count: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, order=True)
class SymbolRef:
    path: str
    symbol: str
    line: int

    @property
    def rendered(self) -> str:
        return f"{self.path}#{self.symbol}" if self.symbol else self.path


@dataclass(frozen=True, slots=True)
class DirectedExecutionStep:
    source: SymbolRef
    target: SymbolRef
    confidence: float
    provenance: tuple[str, ...]
    resolution_method: str = ""
    receiver_type: str = ""
    source_return_type: str = ""
    target_return_type: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionView:
    view_id: str
    steps: tuple[DirectedExecutionStep, ...]
    truncated: bool = False
    cycle_terminated: bool = False
    entry_kind: str = "graph_root"
    route: str = ""

    @property
    def rendered(self) -> str:
        if not self.steps:
            return ""
        nodes = [self.steps[0].source.rendered]
        nodes.extend(step.target.rendered for step in self.steps)
        chain = " -> ".join(nodes)
        annotations = tuple(
            dict.fromkeys(
                annotation
                for step in self.steps
                for annotation in (
                    f"receiver={step.receiver_type}" if step.receiver_type else "",
                    (
                        f"resolution={step.resolution_method}"
                        if step.resolution_method
                        else ""
                    ),
                )
                if annotation
            )
        )
        suffix = f" [{'; '.join(annotations)}]" if annotations else ""
        route = f" route={self.route}" if self.route else ""
        return f"{chain}{route}{suffix}"


@dataclass(frozen=True, slots=True)
class ImpactFact:
    claim_id: str
    kind: str
    depth: int
    source: SymbolRef
    target: SymbolRef
    relation: str
    provenance: tuple[str, ...]
    authority: str = "certified_structural"

    @property
    def rendered(self) -> str:
        if self.kind == "caller":
            return (
                f"caller depth {self.depth}: {self.source.rendered} calls "
                f"{self.target.rendered}"
            )
        if self.kind == "test":
            return f"test: {self.target.rendered} asserts {self.source.rendered}"
        if self.kind == "api_consumer":
            return f"API consumer: {self.source.rendered} consumes {self.target.rendered}"
        if self.kind == "re_export":
            return f"re-export: {self.source.rendered} exports {self.target.rendered}"
        return (
            f"{self.kind}: {self.source.rendered} --{self.relation.lower()}--> "
            f"{self.target.rendered}"
        )


@dataclass(frozen=True, slots=True)
class DecisionOpportunity:
    kind: str
    evidence_action: int
    eligible_call: int
    source_revision: str
    graph_revision: str
    anchors: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalRankHint:
    """Rank-only retrieval signal; never a provider-delivery certificate."""

    path: str
    fused_score: float
    supporting_channels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    source_revision: str
    graph_revision: str
    repository_evidence: RepositoryEvidence
    structural_links: tuple[StructuralLink, ...]
    diagnostics: tuple[str, ...] = ()
    validation_checks: tuple[str, ...] = ()
    represented_checks: frozenset[str] = frozenset()
    path_origins: tuple[tuple[str, str], ...] = ()
    retrieval_rank_hints: tuple[RetrievalRankHint, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticFact:
    claim_id: str
    path: str
    line: int
    message: str

    @property
    def rendered(self) -> str:
        return f"- Observed diagnostic {self.path}:{self.line}: {self.message}"


@dataclass(frozen=True, slots=True)
class ValidationFact:
    claim_id: str
    command: str
    impacted_path: str
    authority: str = "declared_validation"

    @property
    def rendered(self) -> str:
        return f"- Validate impacted path {self.impacted_path} with: {self.command}"


@dataclass(frozen=True, slots=True)
class CoupledChangeObligation:
    """One advisory verification surface composed from certified shared facts."""

    claim_id: str
    changed: SymbolRef
    dependent_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    declared_check: str
    constituent_claim_ids: tuple[str, ...]
    blocking: bool = False

    @property
    def rendered(self) -> str:
        return (
            "- Coupled verification surface (advisory): changed "
            f"{self.changed.rendered}; dependents={', '.join(self.dependent_paths)}; "
            f"tests={', '.join(self.test_paths)}; declared check={self.declared_check}"
        )


@dataclass(frozen=True, slots=True)
class ResolvedConventionRecord:
    """Exact convention composed from agreeing independent semantic sources."""

    claim_id: str
    subject: SymbolRef
    signature: str
    resolved_type: str
    callers: tuple[str, ...]
    tests: tuple[str, ...]
    constituent_claim_ids: tuple[str, ...]

    @property
    def rendered(self) -> str:
        return (
            "- Resolved convention (exact): "
            f"{self.subject.rendered}; signature={self.signature}; "
            f"type={self.resolved_type}; callers={', '.join(self.callers)}; "
            f"tests={', '.join(self.tests)}"
        )


@dataclass(frozen=True, slots=True)
class RepositoryContextProjection:
    status: RepositoryContextStatus
    contributions: tuple[GTContribution, ...]
    rendered_text: str
    claim_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    source_revision: str
    graph_revision: str
    execution_views: tuple[ExecutionView, ...] = ()
    impact_facts: tuple[ImpactFact, ...] = ()
    diagnostic_facts: tuple[DiagnosticFact, ...] = ()
    validation_facts: tuple[ValidationFact, ...] = ()
    coupled_obligations: tuple[CoupledChangeObligation, ...] = ()
    resolved_conventions: tuple[ResolvedConventionRecord, ...] = ()
    convention_coverage: dict[str, int] = field(default_factory=dict)
    semantic_evidence: SemanticEvidenceResult | None = None
    token_count: int = 0
    truncated_count: int = 0
    rejected_edge_count: int = 0
    # A bounded, replayable certificate for the process projection.  The
    # counters are deliberately kept separate from the returned views: a
    # consumer must be able to tell a complete lower-bound projection from a
    # view-list that was silently truncated.
    process_coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def epistemic_envelope(self) -> EpistemicEnvelope:
        process_truncated = any(
            int(self.process_coverage.get(key) or 0) > 0
            for key in ("branch_truncated", "expansion_truncated", "depth_truncated")
        )
        completeness = (
            EpistemicCompleteness.TRUNCATED
            if self.truncated_count or process_truncated
            else EpistemicCompleteness.PARTIAL
            if self.rejected_edge_count
            else EpistemicCompleteness.LOWER_BOUND
        )
        return EpistemicEnvelope(
            completeness=completeness,
            truncated_count=self.truncated_count,
            rejected_edge_count=self.rejected_edge_count,
            reason_codes=self.reason_codes,
        )

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = self.status.value
        row["contributions"] = [asdict(item) for item in self.contributions]
        row["execution_views"] = [asdict(item) for item in self.execution_views]
        row["impact_facts"] = [asdict(item) for item in self.impact_facts]
        row["diagnostic_facts"] = [asdict(item) for item in self.diagnostic_facts]
        row["validation_facts"] = [asdict(item) for item in self.validation_facts]
        row["coupled_obligations"] = [
            asdict(item) for item in self.coupled_obligations
        ]
        row["resolved_conventions"] = [
            asdict(item) for item in self.resolved_conventions
        ]
        row["semantic_evidence"] = (
            self.semantic_evidence.as_dict() if self.semantic_evidence is not None else None
        )
        row["process_coverage"] = dict(self.process_coverage)
        row["epistemic_envelope"] = asdict(self.epistemic_envelope)
        row["epistemic_envelope"]["completeness"] = (
            self.epistemic_envelope.completeness.value
        )
        return row


_ELIGIBLE_KINDS = frozenset(
    {
        "post_read_search",
        "post_mutation",
        "post_diagnostic",
        "post_validation",
        "post_submit",
        "pre_submit",
        "diff",
    }
)
_IMPACT_RELATIONS = frozenset(
    {
        "ASSERTED_BY",
        "TESTED_BY",
        "IMPLEMENTS",
        "EXTENDS",
        "OVERRIDES",
        "HANDLES_ROUTE",
        "IMPORTS",
        "API_CALLS",
        "API_CALL",
        "REFERENCES",
        "RE_EXPORTS",
    }
)
_REVERSE_DEPENDENCY_RELATIONS = frozenset(
    {
        "API_CALL",
        "API_CALLS",
        "EXTENDS",
        "IMPLEMENTS",
        "IMPORTS",
        "OVERRIDES",
        "REFERENCES",
        "RE_EXPORTS",
    }
)


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\0".join(str(part) for part in parts)
    return prefix + hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def _tokens(value: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(value or ""), re.UNICODE))


def _path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _node(source: bool, link: StructuralLink) -> SymbolRef:
    line = link.source_start_line if source else link.target_start_line
    return SymbolRef(
        path=_path(link.source_path if source else link.target_path),
        symbol=str(link.source_symbol if source else link.target_symbol or "").strip(),
        line=max(1, int(line or 1)),
    )


def _certified(link: StructuralLink) -> bool:
    return bool(
        certify_structural_link(link, RelationUse.PROCESS).certified
        and link.source_symbol
        and link.target_symbol
    )


class RepositoryContextEngine:
    """Project semantic, execution, and impact evidence through one interface."""

    def __init__(
        self,
        *,
        max_depth: int = 6,
        max_branching: int = 3,
        max_execution_views: int = 3,
        max_impact_facts: int = 8,
        max_semantic_items: int = 6,
        max_tokens: int = 256,
        max_edge_expansions: int = 256,
    ) -> None:
        self.max_depth = max(1, int(max_depth))
        self.max_branching = max(1, int(max_branching))
        self.max_execution_views = max(1, int(max_execution_views))
        self.max_impact_facts = max(1, int(max_impact_facts))
        self.max_tokens = max(1, int(max_tokens))
        self.max_semantic_items = max(1, int(max_semantic_items))
        self.max_edge_expansions = max(1, int(max_edge_expansions))
        self._process_graph_revision = ""
        self._process_graph_cache: dict[str, Any] = {}
        self._semantic = SemanticEvidenceBridge(
            max_items=self.max_semantic_items, max_tokens=max_tokens
        )

    @staticmethod
    def _matches(node: SymbolRef, paths: frozenset[str], symbols: frozenset[str]) -> bool:
        if symbols:
            return bool(node.symbol and node.symbol in symbols)
        return node.path in paths

    def _execution_views(
        self,
        links: tuple[StructuralLink, ...],
        anchor_paths: frozenset[str],
        anchor_symbols: frozenset[str],
        graph_revision: str,
    ) -> tuple[tuple[ExecutionView, ...], int, dict[str, int]]:
        if graph_revision != self._process_graph_revision:
            adjacency: dict[SymbolRef, list[tuple[SymbolRef, StructuralLink]]] = defaultdict(list)
            reverse: dict[SymbolRef, list[tuple[SymbolRef, StructuralLink]]] = defaultdict(list)
            nodes: set[SymbolRef] = set()
            route_entries: dict[SymbolRef, str] = {}
            rejected = 0
            for link in links:
                relation = link.relation.upper()
                if relation == "CALLS":
                    if not _certified(link):
                        rejected += 1
                        continue
                    source, target = _node(True, link), _node(False, link)
                    adjacency[source].append((target, link))
                    reverse[target].append((source, link))
                    nodes.update((source, target))
                elif relation == "HANDLES_ROUTE" and _certified(link):
                    source = _node(True, link)
                    route_entries[source] = link.route
                    nodes.add(source)
            def ordering(row: tuple[SymbolRef, StructuralLink]) -> tuple[str, str, int]:
                return (row[0].path.lower(), row[0].symbol, row[0].line)

            for mapping in (adjacency, reverse):
                for rows in mapping.values():
                    rows.sort(key=ordering)
            self._process_graph_revision = graph_revision
            self._process_graph_cache = {
                "adjacency": adjacency,
                "reverse": reverse,
                "nodes": frozenset(nodes),
                "route_entries": route_entries,
                "rejected": rejected,
            }
        adjacency = self._process_graph_cache.get("adjacency", {})
        reverse = self._process_graph_cache.get("reverse", {})
        nodes = self._process_graph_cache.get("nodes", frozenset())
        route_entries = self._process_graph_cache.get("route_entries", {})
        rejected = int(self._process_graph_cache.get("rejected", 0))
        anchor_nodes = tuple(
            node
            for node in sorted(nodes)
            if self._matches(node, anchor_paths, anchor_symbols)
        )[:5]

        paths_considered = 0
        branch_truncated = 0
        expansion_truncated = False

        def bounded_rows(rows: tuple[Any, ...] | list[Any]) -> tuple[Any, ...]:
            nonlocal paths_considered, branch_truncated, expansion_truncated
            remaining = max(0, self.max_edge_expansions - paths_considered)
            limit = min(self.max_branching, remaining)
            selected = tuple(rows[:limit])
            branch_truncated += max(0, len(rows) - len(selected))
            paths_considered += len(selected)
            if len(selected) < len(rows) and remaining <= self.max_branching:
                expansion_truncated = True
            return selected

        def step(
            source: SymbolRef,
            target: SymbolRef,
            link: StructuralLink,
        ) -> DirectedExecutionStep:
            return DirectedExecutionStep(
                source=source,
                target=target,
                confidence=float(link.confidence),
                provenance=tuple(link.provenance),
                resolution_method=link.resolution_method,
                receiver_type=link.receiver_type,
                source_return_type=link.source_return_type,
                target_return_type=link.target_return_type,
            )

        views: list[ExecutionView] = []
        entry_candidates = 0
        candidate_limit = self.max_execution_views * 8
        for anchor in anchor_nodes:
            upstream: list[tuple[tuple[DirectedExecutionStep, ...], str, str, bool, bool]] = []
            queue: deque[
                tuple[
                    SymbolRef,
                    tuple[DirectedExecutionStep, ...],
                    frozenset[SymbolRef],
                ]
            ] = deque([(anchor, (), frozenset({anchor}))])
            while queue and paths_considered < self.max_edge_expansions:
                current, steps, visited = queue.popleft()
                rows = bounded_rows(reverse.get(current, ()))
                if not rows:
                    if steps:
                        first = steps[0].source
                        kind = (
                            "route_entry"
                            if first in route_entries
                            else "declared_main"
                            if first.symbol in {"main", "Main"}
                            else "graph_root"
                        )
                        upstream.append((steps, kind, route_entries.get(first, ""), False, False))
                    continue
                for predecessor, link in rows:
                    next_steps = (step(predecessor, current, link), *steps)
                    cycle = predecessor in visited
                    depth_limited = len(next_steps) >= self.max_depth
                    is_entry = (
                        predecessor in route_entries
                        or predecessor.symbol in {"main", "Main"}
                        or not reverse.get(predecessor)
                    )
                    if cycle or depth_limited or is_entry:
                        kind = (
                            "route_entry"
                            if predecessor in route_entries
                            else "declared_main"
                            if predecessor.symbol in {"main", "Main"}
                            else "graph_root"
                            if not reverse.get(predecessor)
                            else "anchored_seed"
                        )
                        upstream.append(
                            (
                                next_steps,
                                kind,
                                route_entries.get(predecessor, ""),
                                depth_limited,
                                cycle,
                            )
                        )
                    else:
                        queue.append(
                            (predecessor, next_steps, visited | {predecessor})
                        )
            if not upstream:
                upstream.append(
                    (
                        (),
                        "route_entry"
                        if anchor in route_entries
                        else "declared_main"
                        if anchor.symbol in {"main", "Main"}
                        else "anchored_seed",
                        route_entries.get(anchor, ""),
                        False,
                        False,
                    )
                )
            entry_candidates += len(upstream)

            downstream: list[tuple[tuple[DirectedExecutionStep, ...], bool, bool]] = []
            queue = deque([(anchor, (), frozenset({anchor}))])
            while queue and paths_considered < self.max_edge_expansions:
                current, steps, visited = queue.popleft()
                rows = bounded_rows(adjacency.get(current, ()))
                if not rows:
                    if steps:
                        downstream.append((steps, False, False))
                    continue
                for target, link in rows:
                    next_steps = (*steps, step(current, target, link))
                    cycle = target in visited
                    depth_limited = len(next_steps) >= self.max_depth
                    is_test = target.path.lower().startswith(("test/", "tests/"))
                    terminal = cycle or depth_limited or is_test or not adjacency.get(target)
                    if terminal:
                        downstream.append((next_steps, depth_limited, cycle))
                    else:
                        queue.append((target, next_steps, visited | {target}))
            if not downstream:
                downstream.append(((), False, False))

            for upstream_steps, entry_kind, route, up_truncated, up_cycle in upstream:
                for downstream_steps, down_truncated, down_cycle in downstream:
                    combined = (*upstream_steps, *downstream_steps)
                    if not combined:
                        continue
                    views.append(
                        ExecutionView(
                            _stable_id(
                                "gt-execution-",
                                *(
                                    item.source.rendered + ">" + item.target.rendered
                                    for item in combined
                                ),
                            ),
                            combined,
                            truncated=up_truncated or down_truncated,
                            cycle_terminated=up_cycle or down_cycle,
                            entry_kind=entry_kind,
                            route=route,
                        )
                    )
                    if len(views) >= candidate_limit:
                        break
                if len(views) >= candidate_limit:
                    break
            if len(views) >= candidate_limit or paths_considered >= self.max_edge_expansions:
                break
        unique = {view.view_id: view for view in views}

        def score(view: ExecutionView) -> tuple[Any, ...]:
            nodes = (view.steps[0].source, *(step.target for step in view.steps))
            exact_symbol = int(any(node.symbol in anchor_symbols for node in nodes))
            exact_path = int(any(node.path in anchor_paths for node in nodes))
            test_related = int(
                any(
                    node.path.lower().startswith(("test/", "tests/"))
                    or "/test/" in node.path.lower()
                    or "/tests/" in node.path.lower()
                    for node in nodes
                )
            )
            entry_rank = {
                "route_entry": 0,
                "declared_main": 1,
                "graph_root": 2,
                "anchored_seed": 3,
            }.get(view.entry_kind, 4)
            terminal = nodes[-1] if nodes else SymbolRef("", "", 0)
            terminal_rank = 0 if terminal.path.lower().startswith(("test/", "tests/")) else 1
            complete_rank = int(view.truncated or view.cycle_terminated)
            sequence = tuple(node.rendered.lower() for node in nodes)
            return (
                -exact_symbol,
                -exact_path,
                -test_related,
                entry_rank,
                terminal_rank,
                complete_rank,
                len(view.steps),
                sequence,
                view.view_id,
            )

        ranked = tuple(sorted(unique.values(), key=score))
        selected = ranked[: self.max_execution_views]
        coverage = {
            "profile_id": "gt.certified_process.v1",
            "max_depth": self.max_depth,
            "max_branching": self.max_branching,
            "max_execution_views": self.max_execution_views,
            "max_edge_expansions": self.max_edge_expansions,
            "anchor_nodes_considered": len(anchor_nodes),
            "entries_considered": entry_candidates,
            "paths_considered": paths_considered,
            "returned_views": len(selected),
            "candidate_views": len(ranked),
            "branch_truncated": branch_truncated,
            "expansion_truncated": int(expansion_truncated),
            "depth_truncated": sum(1 for view in ranked if view.truncated),
            "cycle_terminated": sum(1 for view in ranked if view.cycle_terminated),
            "deduplicated_paths": max(0, len(views) - len(unique)),
            "omitted_for_view_limit": max(0, len(ranked) - len(selected)),
            "rejected_edges": rejected,
            "lower_bound": 1,
        }
        return selected, rejected, coverage

    def _impact(
        self,
        links: tuple[StructuralLink, ...],
        changed_paths: frozenset[str],
        changed_symbols: frozenset[str],
    ) -> tuple[tuple[ImpactFact, ...], int]:
        accepted = tuple(link for link in links if _certified(link))
        rejected = sum(
            1
            for link in links
            if link.relation.upper() in _IMPACT_RELATIONS
            and link.relation.upper() != "CALLS"
            and not _certified(link)
        )
        reverse_calls: dict[SymbolRef, list[tuple[SymbolRef, StructuralLink]]] = defaultdict(list)
        nodes: set[SymbolRef] = set()
        for link in accepted:
            source, target = _node(True, link), _node(False, link)
            nodes.update((source, target))
            if link.relation.upper() == "CALLS":
                reverse_calls[target].append((source, link))
        for rows in reverse_calls.values():
            rows.sort(key=lambda row: (row[0].path.lower(), row[0].symbol, row[0].line))
        seeds = sorted(
            node for node in nodes if self._matches(node, changed_paths, changed_symbols)
        )
        queue = deque((seed, 0) for seed in seeds)
        visited = set(seeds)
        facts: list[ImpactFact] = []
        while queue and len(facts) < self.max_impact_facts:
            target, depth = queue.popleft()
            if depth >= 3:
                continue
            for caller, link in reverse_calls.get(target, ()):
                claim = _stable_id(
                    "gt-impact-", "caller", str(depth + 1), caller.rendered, target.rendered
                )
                facts.append(
                    ImpactFact(
                        claim, "caller", depth + 1, caller, target, "CALLS", tuple(link.provenance)
                    )
                )
                if caller not in visited:
                    visited.add(caller)
                    queue.append((caller, depth + 1))
                if len(facts) >= self.max_impact_facts:
                    break

        for link in accepted:
            if len(facts) >= self.max_impact_facts:
                break
            relation = link.relation.upper()
            if relation not in _IMPACT_RELATIONS or relation == "CALLS":
                continue
            source, target = _node(True, link), _node(False, link)
            changed_endpoint = (
                target if relation in _REVERSE_DEPENDENCY_RELATIONS else source
            )
            if not self._matches(changed_endpoint, changed_paths, changed_symbols):
                continue
            kind = (
                "test"
                if relation in {"ASSERTED_BY", "TESTED_BY"}
                else "api_consumer"
                if relation in {"API_CALL", "API_CALLS"}
                else "re_export"
                if relation == "RE_EXPORTS"
                else relation.lower()
            )
            facts.append(
                ImpactFact(
                    _stable_id("gt-impact-", kind, source.rendered, relation, target.rendered),
                    kind,
                    1,
                    source,
                    target,
                    relation,
                    tuple(link.provenance),
                )
            )
        unique = {fact.claim_id: fact for fact in facts}
        return tuple(unique.values()), rejected

    @staticmethod
    def _preexisting_semantic_evidence(
        evidence: RepositoryEvidence,
        path_origins: tuple[tuple[str, str], ...],
        anchor_paths: frozenset[str] = frozenset(),
        anchor_symbols: frozenset[str] = frozenset(),
    ) -> RepositoryEvidence:
        """Keep provider-visible semantic rows on task-start source only.

        Graph refreshes include model-authored files. Those rows remain useful
        to the controller, but must not become a new provider-visible claim.
        Missing origin binding is unknown and therefore terminal.
        """

        origins = {_path(path): str(origin) for path, origin in path_origins}

        def preexisting(path: Any) -> bool:
            return (
                origins.get(_path(path or ""))
                == EvidenceOrigin.PREEXISTING_REPOSITORY.value
            )

        def matches_anchor(path: Any, symbol: Any) -> bool:
            normalized_path = _path(path or "")
            normalized_symbol = str(symbol or "").strip()
            if anchor_paths and normalized_path not in anchor_paths:
                return False
            if anchor_symbols and normalized_symbol not in anchor_symbols:
                return False
            return bool(anchor_paths or anchor_symbols)

        def keep(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
            return tuple(
                row
                for row in rows
                if preexisting(row.get("path"))
                and (
                    not (anchor_paths or anchor_symbols)
                    or matches_anchor(row.get("path"), row.get("symbol"))
                )
            )

        def keep_calls(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
            return tuple(
                row
                for row in rows
                if preexisting(row.get("caller_path"))
                and preexisting(row.get("target_path"))
                and (
                    not (anchor_paths or anchor_symbols)
                    or matches_anchor(row.get("target_path"), row.get("target"))
                )
            )

        def keep_references(
            rows: tuple[dict[str, Any], ...]
        ) -> tuple[dict[str, Any], ...]:
            return tuple(
                row
                for row in rows
                if preexisting(row.get("path"))
                and preexisting(row.get("target_path"))
                and (
                    not (anchor_paths or anchor_symbols)
                    or matches_anchor(row.get("target_path"), row.get("target"))
                )
            )

        return replace(
            evidence,
            definitions=keep(evidence.definitions),
            references=keep_references(evidence.references),
            callers=keep_calls(evidence.callers),
            semantic_properties=keep(evidence.semantic_properties),
        )

    @staticmethod
    def _diagnostics(
        diagnostics: tuple[str, ...], anchor_paths: frozenset[str]
    ) -> tuple[DiagnosticFact, ...]:
        facts: dict[str, DiagnosticFact] = {}
        pattern = re.compile(
            r"(?P<path>(?:[A-Za-z]:)?[^\s:]+\.[A-Za-z0-9_]+):"
            r"(?P<line>\d+)(?::\d+)?:\s*(?P<message>[^\r\n]{1,240})"
        )
        for diagnostic in diagnostics:
            for match in pattern.finditer(str(diagnostic or "")):
                path = _path(match.group("path"))
                if anchor_paths:
                    matching_anchors = tuple(
                        anchor
                        for anchor in anchor_paths
                        if path == anchor or path.endswith("/" + anchor)
                    )
                    if len(matching_anchors) != 1:
                        continue
                    path = matching_anchors[0]
                line = int(match.group("line"))
                message = " ".join(match.group("message").split())
                claim_id = _stable_id("gt-diagnostic-", path, str(line), message)
                facts.setdefault(claim_id, DiagnosticFact(claim_id, path, line, message))
        return tuple(facts.values())

    @staticmethod
    def _validation(
        impact: tuple[ImpactFact, ...],
        checks: tuple[str, ...],
        represented_checks: frozenset[str],
    ) -> tuple[ValidationFact, ...]:
        facts: dict[str, ValidationFact] = {}
        test_paths = tuple(
            dict.fromkeys(fact.target.path for fact in impact if fact.kind == "test")
        )
        for path in test_paths:
            normalized_checks = tuple(
                " ".join(str(command or "").split()) for command in checks
            )
            specific_checks = tuple(
                command for command in normalized_checks if path in command
            )
            candidates = specific_checks or normalized_checks[:1]
            for normalized in candidates:
                if not normalized or normalized in represented_checks:
                    continue
                claim_id = _stable_id("gt-validation-", path, normalized)
                facts.setdefault(claim_id, ValidationFact(claim_id, normalized, path))
                break
        return tuple(facts.values())

    @staticmethod
    def _coupled_obligations(
        impact: tuple[ImpactFact, ...],
        validation: tuple[ValidationFact, ...],
        anchor_paths: frozenset[str],
        anchor_symbols: frozenset[str],
    ) -> tuple[CoupledChangeObligation, ...]:
        dependencies = tuple(
            fact
            for fact in impact
            if fact.kind in {"caller", "api_consumer", "re_export"}
        )
        tests = tuple(fact for fact in impact if fact.kind == "test")
        if not dependencies or not tests or not validation:
            return ()

        candidates = tuple(
            dict.fromkeys(
                (
                    *(fact.target for fact in dependencies),
                    *(fact.source for fact in tests),
                )
            )
        )
        for changed in sorted(candidates):
            if anchor_paths and changed.path not in anchor_paths:
                continue
            if anchor_symbols and changed.symbol not in anchor_symbols:
                continue
            related_dependencies = tuple(
                fact for fact in dependencies if fact.target == changed
            )
            related_tests = tuple(fact for fact in tests if fact.source == changed)
            if not related_dependencies or not related_tests:
                continue
            test_paths = tuple(
                dict.fromkeys(fact.target.path for fact in related_tests)
            )
            selected_validation = next(
                (fact for fact in validation if fact.impacted_path in test_paths),
                None,
            )
            if selected_validation is None:
                continue
            dependent_paths = tuple(
                dict.fromkeys(fact.source.path for fact in related_dependencies)
            )
            constituents = tuple(
                dict.fromkeys(
                    (
                        *(fact.claim_id for fact in related_dependencies),
                        *(fact.claim_id for fact in related_tests),
                        selected_validation.claim_id,
                    )
                )
            )
            claim_id = _stable_id(
                "gt-coupled-obligation-",
                changed.rendered,
                *dependent_paths,
                *test_paths,
                selected_validation.command,
            )
            return (
                CoupledChangeObligation(
                    claim_id=claim_id,
                    changed=changed,
                    dependent_paths=dependent_paths,
                    test_paths=test_paths,
                    declared_check=selected_validation.command,
                    constituent_claim_ids=constituents,
                ),
            )
        return ()

    @staticmethod
    def _resolved_conventions(
        semantic: SemanticEvidenceResult,
        execution_views: tuple[ExecutionView, ...],
        impact: tuple[ImpactFact, ...],
        anchor_paths: frozenset[str],
        anchor_symbols: frozenset[str],
    ) -> tuple[tuple[ResolvedConventionRecord, ...], dict[str, int]]:
        """Compose only an exact, independently corroborated convention.

        A signature or graph edge alone is intentionally insufficient.  The
        subject must have a resolved type, a certified caller, and a certified
        test relation.  Conflicting type evidence is recorded and abstains.
        """

        coverage = {
            "definition_candidates": 0,
            "exact_records": 0,
            "missing_type_evidence": 0,
            "missing_caller_evidence": 0,
            "missing_test_evidence": 0,
            "conflicting_type_evidence": 0,
        }
        records: list[ResolvedConventionRecord] = []
        for definition in semantic.items:
            if definition.kind != "definition" or not definition.signature:
                continue
            if anchor_paths and _path(definition.path) not in anchor_paths:
                continue
            if anchor_symbols and definition.symbol not in anchor_symbols:
                continue
            coverage["definition_candidates"] += 1
            subject = SymbolRef(
                _path(definition.path), definition.symbol, definition.line
            )
            matching_views: list[ExecutionView] = []
            matching_steps: list[DirectedExecutionStep] = []
            for view in execution_views:
                steps = tuple(
                    step
                    for step in view.steps
                    if step.target.path == subject.path
                    and step.target.symbol == subject.symbol
                )
                if steps:
                    matching_views.append(view)
                    matching_steps.extend(steps)
            tests = tuple(
                fact
                for fact in impact
                if fact.kind == "test"
                and fact.source.path == subject.path
                and fact.source.symbol == subject.symbol
            )
            resolved_types = {
                value
                for value in (
                    definition.return_type,
                    *(step.target_return_type for step in matching_steps),
                )
                if value
            }
            if not resolved_types:
                coverage["missing_type_evidence"] += 1
                continue
            if len(resolved_types) != 1:
                coverage["conflicting_type_evidence"] += 1
                continue
            if not matching_steps:
                coverage["missing_caller_evidence"] += 1
                continue
            if not tests:
                coverage["missing_test_evidence"] += 1
                continue
            callers = tuple(
                dict.fromkeys(step.source.rendered for step in matching_steps)
            )
            test_refs = tuple(dict.fromkeys(fact.target.rendered for fact in tests))
            constituents = tuple(
                dict.fromkeys(
                    (
                        definition.claim_id,
                        *(view.view_id for view in matching_views),
                        *(fact.claim_id for fact in tests),
                    )
                )
            )
            resolved_type = next(iter(resolved_types))
            records.append(
                ResolvedConventionRecord(
                    claim_id=_stable_id(
                        "gt-resolved-convention-",
                        subject.rendered,
                        definition.signature,
                        resolved_type,
                        *callers,
                        *test_refs,
                    ),
                    subject=subject,
                    signature=definition.signature,
                    resolved_type=resolved_type,
                    callers=callers,
                    tests=test_refs,
                    constituent_claim_ids=constituents,
                )
            )
        coverage["exact_records"] = len(records)
        return tuple(records), coverage

    def _abstain(
        self,
        opportunity: DecisionOpportunity,
        reasons: tuple[str, ...],
        *,
        semantic: SemanticEvidenceResult | None = None,
        rejected_edge_count: int = 0,
        process_coverage: dict[str, int] | None = None,
    ) -> RepositoryContextProjection:
        return RepositoryContextProjection(
            status=RepositoryContextStatus.ABSTAIN,
            contributions=(),
            rendered_text="",
            claim_ids=(),
            reason_codes=reasons,
            source_revision=opportunity.source_revision,
            graph_revision=opportunity.graph_revision,
            semantic_evidence=semantic,
            rejected_edge_count=rejected_edge_count,
            process_coverage=dict(process_coverage or {}),
        )

    def project(
        self,
        opportunity: DecisionOpportunity,
        snapshot: RepositorySnapshot,
        *,
        delivered_claim_ids: frozenset[str] = frozenset(),
    ) -> RepositoryContextProjection:
        if opportunity.kind not in _ELIGIBLE_KINDS:
            return self._abstain(opportunity, ("ineligible_opportunity",))
        if (
            opportunity.source_revision != snapshot.source_revision
            or opportunity.graph_revision != snapshot.graph_revision
        ):
            return self._abstain(opportunity, ("stale_repository_snapshot",))

        anchor_paths = frozenset(
            _path(item)
            for item in (*opportunity.anchors, *opportunity.changed_paths)
            if _path(item)
        )
        anchor_symbols = frozenset(
            str(item).strip()
            for item in opportunity.changed_symbols
            if str(item).strip()
        )
        if opportunity.changed_paths and not anchor_symbols:
            anchor_symbols = frozenset(
                str(item.get("symbol") or "").strip()
                for item in snapshot.repository_evidence.definitions
                if _path(item.get("path") or "") in anchor_paths
                and str(item.get("symbol") or "").strip()
            )
        semantic_evidence = self._preexisting_semantic_evidence(
            snapshot.repository_evidence,
            snapshot.path_origins,
            anchor_paths,
            anchor_symbols,
        )
        semantic = self._semantic.compose(
            semantic_evidence,
            source_revision=opportunity.source_revision,
            graph_revision=opportunity.graph_revision,
            delivered_claim_ids=delivered_claim_ids,
        )
        semantic_for_composition = semantic
        if (
            opportunity.kind == "post_read_search"
            and semantic.status is SemanticEvidenceStatus.DELIVER
            and anchor_paths
        ):
            # The normal tool observation already contains the complete bytes
            # of files the model chose to read/search. Retain only nonlocal
            # semantic consequences; repeating local definitions perturbs the
            # trajectory without replacing exploration.
            nonlocal_items = tuple(
                item for item in semantic.items if _path(item.path) not in anchor_paths
            )
            if len(nonlocal_items) != len(semantic.items):
                rendered = "\n".join(item.rendered for item in nonlocal_items)
                semantic = replace(
                    semantic,
                    status=(
                        SemanticEvidenceStatus.DELIVER
                        if nonlocal_items
                        else SemanticEvidenceStatus.ABSTAIN
                    ),
                    items=nonlocal_items,
                    rendered_text=(
                        "Certified semantic context (source-backed; omitted facts may exist):\n"
                        + rendered
                        if rendered
                        else ""
                    ),
                    claim_ids=tuple(item.claim_id for item in nonlocal_items),
                    reason_codes=tuple(
                        dict.fromkeys(
                            (*semantic.reason_codes, "local_observation_already_represented")
                        )
                    ),
                    token_count=_tokens(rendered),
                    truncated_count=(
                        semantic.truncated_count + len(semantic.items) - len(nonlocal_items)
                    ),
                )
        execution_views, rejected_edges, process_coverage = self._execution_views(
            snapshot.structural_links,
            anchor_paths,
            anchor_symbols,
            snapshot.graph_revision,
        )
        impact, rejected_impact_edges = self._impact(
            snapshot.structural_links, anchor_paths, anchor_symbols
        )
        rejected_edges += rejected_impact_edges
        if opportunity.kind == "post_read_search" and anchor_paths:
            # The selected read/search observation already contains every local
            # byte needed to rediscover relations whose endpoints are confined
            # to those files.  Only retain a process/impact fact when it crosses
            # that observation boundary.  This is the regression-safety part of
            # same-observation augmentation: certified does not mean novel.
            local_execution_count = sum(
                1
                for view in execution_views
                if all(
                    _path(ref.path) in anchor_paths
                    for step in view.steps
                    for ref in (step.source, step.target)
                )
            )
            local_impact_count = sum(
                1
                for fact in impact
                if _path(fact.source.path) in anchor_paths
                and _path(fact.target.path) in anchor_paths
            )
            if local_execution_count:
                execution_views = tuple(
                    view
                    for view in execution_views
                    if not all(
                        _path(ref.path) in anchor_paths
                        for step in view.steps
                        for ref in (step.source, step.target)
                    )
                )
            if local_impact_count:
                impact = tuple(
                    fact
                    for fact in impact
                    if not (
                        _path(fact.source.path) in anchor_paths
                        and _path(fact.target.path) in anchor_paths
                    )
                )
            if local_execution_count or local_impact_count:
                process_coverage["local_observation_suppressed_views"] = (
                    local_execution_count
                )
                process_coverage["local_observation_suppressed_impact"] = (
                    local_impact_count
                )
                semantic = replace(
                    semantic,
                    reason_codes=tuple(
                        dict.fromkeys(
                            (*semantic.reason_codes, "local_observation_already_represented")
                        )
                    ),
                )
        conventions, convention_coverage = self._resolved_conventions(
            semantic_for_composition,
            execution_views,
            impact,
            anchor_paths,
            anchor_symbols,
        )
        diagnostics = self._diagnostics(snapshot.diagnostics, anchor_paths)
        validation = self._validation(
            impact,
            snapshot.validation_checks,
            snapshot.represented_checks,
        )
        coupled = self._coupled_obligations(
            impact,
            validation,
            anchor_paths,
            anchor_symbols,
        )
        coupled_constituents = {
            claim_id
            for item in coupled
            for claim_id in item.constituent_claim_ids
        }
        convention_constituents = {
            claim_id
            for item in conventions
            for claim_id in item.constituent_claim_ids
        }
        retrieval_scores = {
            _path(hint.path): float(hint.fused_score)
            for hint in snapshot.retrieval_rank_hints
            if _path(hint.path)
        }

        def retrieval_order(paths: tuple[str, ...], stable_id: str) -> tuple[float, str]:
            score = max((retrieval_scores.get(_path(path), -1.0) for path in paths), default=-1.0)
            return (-score, stable_id)

        execution_views = tuple(
            sorted(
                execution_views,
                key=lambda view: retrieval_order(
                    tuple(
                        dict.fromkeys(
                            (
                                *(step.source.path for step in view.steps),
                                *(step.target.path for step in view.steps),
                            )
                        )
                    ),
                    view.view_id,
                ),
            )
        )
        impact = tuple(
            sorted(
                impact,
                key=lambda fact: retrieval_order(
                    (fact.source.path, fact.target.path),
                    fact.claim_id,
                ),
            )
        )
        process_coverage["retrieval_rank_hints"] = len(snapshot.retrieval_rank_hints)
        process_coverage["retrieval_ranked_items"] = sum(
            any(_path(path) in retrieval_scores for path in paths)
            for paths in (
                *(
                    tuple(
                        dict.fromkeys(
                            (
                                *(step.source.path for step in view.steps),
                                *(step.target.path for step in view.steps),
                            )
                        )
                    )
                    for view in execution_views
                ),
                *((fact.source.path, fact.target.path) for fact in impact),
            )
        )

        semantic_lines = (
            [(item.claim_id, item.rendered) for item in semantic.items]
            if semantic.status is SemanticEvidenceStatus.DELIVER
            else []
        )
        critical_lines = [(item.claim_id, item.rendered) for item in coupled]
        critical_lines.extend((item.claim_id, item.rendered) for item in conventions)
        critical_lines.extend(
            (fact.claim_id, fact.rendered) for fact in diagnostics
        )
        critical_lines.extend(
            (fact.claim_id, fact.rendered)
            for fact in validation
            if fact.claim_id not in coupled_constituents
        )
        process_lines = [
            (
                view.view_id,
                f"- Execution (lower bound; {view.entry_kind}): {view.rendered}",
            )
            for view in execution_views
        ]
        process_lines.extend(
            (fact.claim_id, f"- Impact {fact.rendered}")
            for fact in impact
            if fact.claim_id not in coupled_constituents
            and fact.claim_id not in convention_constituents
        )
        # Diagnostics/validation remain first.  Certified process orientation
        # comes before generic symbol facts because it replaces expensive
        # exploration; semantic snippets remain available when budget permits.
        groups = (
            (
                "repository_context",
                "Current certified repository context:",
                critical_lines,
            ),
            (
                "repository_process",
                "Current certified repository context:",
                process_lines,
            ),
            (
                "repository_semantic",
                "Current certified repository context:",
                semantic_lines,
            ),
        )
        available_groups = tuple(
            (
                surface,
                heading,
                [(claim, line) for claim, line in rows if claim not in delivered_claim_ids],
            )
            for surface, heading, rows in groups
        )
        if not any(rows for _, _, rows in available_groups):
            reasons = ["no_certified_repository_context"]
            if "local_observation_already_represented" in semantic.reason_codes:
                reasons.append("local_observation_already_represented")
            if delivered_claim_ids and (
                semantic.reason_codes == ("semantic_evidence_already_delivered",)
                or execution_views
                or impact
            ):
                reasons = ["repository_context_already_delivered"]
            return self._abstain(
                opportunity,
                tuple(reasons),
                semantic=semantic,
                rejected_edge_count=rejected_edges,
                process_coverage=process_coverage,
            )
        selected_by_surface: dict[str, list[tuple[str, str]]] = {}
        used = 0
        truncated = semantic.truncated_count
        for surface, heading, rows in available_groups:
            if not rows:
                continue
            heading_cost = _tokens(heading)
            if used + heading_cost > self.max_tokens:
                truncated += len(rows)
                if surface == "repository_process":
                    process_coverage["omitted_for_budget"] = process_coverage.get(
                        "omitted_for_budget", 0
                    ) + len(rows)
                continue
            group_rows: list[tuple[str, str]] = []
            used += heading_cost
            for claim, line in rows:
                required = _tokens(line)
                if used + required > self.max_tokens:
                    truncated += 1
                    if surface == "repository_process":
                        process_coverage["omitted_for_budget"] = process_coverage.get(
                            "omitted_for_budget", 0
                        ) + 1
                    continue
                group_rows.append((claim, line))
                used += required
            if group_rows:
                selected_by_surface[surface] = group_rows
            else:
                used -= heading_cost
        selected = [row for rows in selected_by_surface.values() for row in rows]
        if not selected:
            return self._abstain(
                opportunity,
                ("repository_context_token_budget",),
                semantic=semantic,
                rejected_edge_count=rejected_edges,
                process_coverage=process_coverage,
            )
        payloads = []
        for surface, heading, _ in groups:
            rows = selected_by_surface.get(surface, [])
            if rows:
                payloads.append("\n".join((heading, *(line for _, line in rows))))
        rendered = "\n".join(payloads)
        claims = tuple(claim for claim, _ in selected)
        selected_claims = frozenset(claims)
        # The process certificate describes the exact projection delivered to
        # the provider, not merely the pre-budget candidate set.  The shared
        # contribution budget may omit execution views after `_execution_views`
        # has selected them; retaining the candidate count is useful for
        # auditability, but `returned_views` must match the receipt payload.
        delivered_execution_views = tuple(
            view for view in execution_views if view.view_id in selected_claims
        )
        process_coverage["returned_views"] = len(delivered_execution_views)
        process_coverage["omitted_for_budget"] = max(
            int(process_coverage.get("omitted_for_budget") or 0),
            len(execution_views) - len(delivered_execution_views),
        )
        selected_semantic_items = tuple(
            item for item in semantic.items if item.claim_id in selected_claims
        )
        if selected_semantic_items != semantic.items:
            selected_semantic_rendered = (
                "\n".join(
                    (
                        "Certified semantic context "
                        "(source-backed; omitted facts may exist):",
                        *(item.rendered for item in selected_semantic_items),
                    )
                )
                if selected_semantic_items
                else ""
            )
            semantic = replace(
                semantic,
                status=(
                    SemanticEvidenceStatus.DELIVER
                    if selected_semantic_items
                    else SemanticEvidenceStatus.ABSTAIN
                ),
                items=selected_semantic_items,
                rendered_text=selected_semantic_rendered,
                claim_ids=tuple(item.claim_id for item in selected_semantic_items),
                reason_codes=tuple(
                    dict.fromkeys(
                        (*semantic.reason_codes, "repository_context_token_budget")
                    )
                ),
                token_count=_tokens(selected_semantic_rendered),
                truncated_count=(
                    semantic.truncated_count
                    + len(semantic.items)
                    - len(selected_semantic_items)
                ),
            )
        semantic_claims = {item.claim_id for item in semantic.items}
        execution_claims = {view.view_id for view in execution_views}
        impact_claims = {fact.claim_id for fact in impact}
        diagnostic_claims = {fact.claim_id for fact in diagnostics}
        validation_claims = {fact.claim_id for fact in validation}
        coupled_claims = {item.claim_id for item in coupled}
        convention_claims = {item.claim_id for item in conventions}
        coupled_by_id = {item.claim_id: item for item in coupled}
        convention_by_id = {item.claim_id: item for item in conventions}
        semantic_by_id = {item.claim_id: item for item in semantic.items}
        execution_by_id = {item.view_id: item for item in execution_views}
        impact_by_id = {item.claim_id: item for item in impact}
        diagnostic_by_id = {item.claim_id: item for item in diagnostics}
        validation_by_id = {item.claim_id: item for item in validation}

        def anchors_for(claim: str) -> tuple[str, ...]:
            semantic_item = semantic_by_id.get(claim)
            if semantic_item is not None:
                return tuple(
                    dict.fromkeys(
                        item
                        for item in (
                            _path(semantic_item.path),
                            str(semantic_item.symbol or "").strip(),
                        )
                        if item
                    )
                )
            execution = execution_by_id.get(claim)
            if execution is not None:
                return tuple(
                    dict.fromkeys(
                        ref.rendered
                        for step in execution.steps
                        for ref in (step.source, step.target)
                        if ref.rendered
                    )
                )
            impact_item = impact_by_id.get(claim)
            if impact_item is not None:
                return tuple(
                    dict.fromkeys(
                        (impact_item.source.rendered, impact_item.target.rendered)
                    )
                )
            diagnostic = diagnostic_by_id.get(claim)
            if diagnostic is not None:
                return (f"{diagnostic.path}:{diagnostic.line}",)
            validation_item = validation_by_id.get(claim)
            if validation_item is not None:
                return (validation_item.impacted_path, validation_item.command)
            coupled_item = coupled_by_id.get(claim)
            if coupled_item is not None:
                return tuple(
                    dict.fromkeys(
                        (
                            coupled_item.changed.rendered,
                            *coupled_item.dependent_paths,
                            *coupled_item.test_paths,
                            coupled_item.declared_check,
                        )
                    )
                )
            convention = convention_by_id.get(claim)
            if convention is not None:
                return tuple(
                    dict.fromkeys(
                        (
                            convention.subject.rendered,
                            *convention.callers,
                            *convention.tests,
                        )
                    )
                )
            return ()

        def metadata_for(claim: str) -> dict[str, Any]:
            item = coupled_by_id.get(claim)
            convention = convention_by_id.get(claim)
            return {
                "claim_id": claim,
                "origin": (
                    "execution_observation"
                    if claim in diagnostic_claims
                    else EvidenceOrigin.PREEXISTING_REPOSITORY.value
                ),
                "authority": (
                    "execution_observation"
                    if claim in diagnostic_claims
                    else "certified_structural"
                    if claim in execution_claims or claim in impact_claims
                    else "certified_composition"
                    if claim in coupled_claims or claim in convention_claims
                    else {
                        "COMPILER": "compiler_semantic",
                        "LSP": "lsp_semantic",
                        "PARSER_STRUCTURAL": "parser_structural",
                        "HEURISTIC": "heuristic_semantic",
                        "TEXTUAL": "textual_candidate",
                    }.get(
                        str(semantic_by_id[claim].semantic_authority),
                        "unknown_semantic",
                    )
                    if claim in semantic_claims
                    else "declared_validation"
                    if claim in validation_claims
                    else "unknown"
                ),
                "materiality_reason": (
                    "current_attributable_failure"
                    if claim in diagnostic_claims
                    else "new_unresolved_task_obligation"
                    if claim in coupled_claims or claim in convention_claims
                    else "new_unresolved_task_obligation"
                    if claim in validation_claims
                    else "decision_relevant_repository_context"
                ),
                "source_revision": opportunity.source_revision,
                "graph_revision": opportunity.graph_revision,
                "provider_value_anchors": list(anchors_for(claim)),
                **(
                    {
                        "constituent_claim_ids": list(item.constituent_claim_ids),
                        "blocking": item.blocking,
                    }
                    if item is not None
                    else {
                        "constituent_claim_ids": list(
                            convention.constituent_claim_ids
                        ),
                        "resolved_type": convention.resolved_type,
                        "signature": convention.signature,
                    }
                    if convention is not None
                    else {}
                ),
            }
        contributions: list[GTContribution] = []
        priority_by_surface = {
            "repository_context": 4,
            "repository_process": 6,
            "repository_semantic": 18,
        }
        for surface, heading, _ in groups:
            rows = selected_by_surface.get(surface, [])
            if not rows:
                continue
            payload = "\n".join((heading, *(line for _, line in rows)))
            surface_claims = tuple(claim for claim, _ in rows)
            surface_facts = tuple(
                _stable_id("gt-context-fact-", claim, opportunity.source_revision)
                for claim in surface_claims
            )
            surface_metadata = tuple(metadata_for(claim) for claim in surface_claims)
            contributions.append(
                GTContribution.create(
                    surface=surface,
                    kind=ContributionKind.EVIDENCE,
                    payload=payload,
                    claim_ids=surface_claims,
                    fact_ids=surface_facts,
                    evidence_action=opportunity.evidence_action,
                    eligible_call=opportunity.eligible_call,
                    source_revision=opportunity.source_revision,
                    priority=priority_by_surface[surface],
                    claim_metadata=surface_metadata,
                    value_certificates=build_provider_value_certificates(
                        surface=surface,
                        claim_ids=surface_claims,
                        fact_ids=surface_facts,
                        claim_metadata=surface_metadata,
                        source_revision=opportunity.source_revision,
                        evidence_action=opportunity.evidence_action,
                    ),
                )
            )
        return RepositoryContextProjection(
            status=RepositoryContextStatus.DELIVER,
            contributions=tuple(contributions),
            rendered_text=rendered,
            claim_ids=claims,
            reason_codes=tuple(semantic.reason_codes),
            source_revision=opportunity.source_revision,
            graph_revision=opportunity.graph_revision,
            execution_views=delivered_execution_views,
            impact_facts=tuple(
                fact
                for fact in impact
                if fact.claim_id in selected_claims
                or any(
                    item.claim_id in selected_claims
                    and fact.claim_id in item.constituent_claim_ids
                    for item in coupled
                )
                or any(
                    item.claim_id in selected_claims
                    and fact.claim_id in item.constituent_claim_ids
                    for item in conventions
                )
            ),
            diagnostic_facts=tuple(
                fact for fact in diagnostics if fact.claim_id in selected_claims
            ),
            validation_facts=tuple(
                fact
                for fact in validation
                if fact.claim_id in selected_claims
                or any(
                    item.claim_id in selected_claims
                    and fact.claim_id in item.constituent_claim_ids
                    for item in coupled
                )
            ),
            coupled_obligations=tuple(
                item for item in coupled if item.claim_id in selected_claims
            ),
            resolved_conventions=tuple(
                item for item in conventions if item.claim_id in selected_claims
            ),
            convention_coverage=convention_coverage,
            semantic_evidence=semantic,
            token_count=_tokens(rendered),
            truncated_count=truncated,
            rejected_edge_count=rejected_edges,
            process_coverage=process_coverage,
        )


__all__ = [
    "CoupledChangeObligation",
    "DecisionOpportunity",
    "DiagnosticFact",
    "DirectedExecutionStep",
    "ExecutionView",
    "ImpactFact",
    "RepositoryContextEngine",
    "RepositoryContextProjection",
    "RepositoryContextStatus",
    "ResolvedConventionRecord",
    "RepositorySnapshot",
    "SymbolRef",
    "ValidationFact",
]
