"""Deterministic, precision-first composition of bounded graph relationships.

The module consumes the repository evidence GT already owns.  It does not
query a model, parse raw shell text, mutate controller state, or deliver text
directly.  Callers pass the result through the canonical contribution compiler.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum

from gt_engine.hybrid_retrieval import (
    HybridRetrievalResult,
    RelationUse,
    StructuralLink,
    certify_structural_link,
)


class RelationalContextStatus(StrEnum):
    DELIVER = "deliver"
    ABSTAIN = "abstain"


class EpistemicStatus(StrEnum):
    """How completely the rendered graph view describes repository behavior."""

    EXACT = "exact"
    LOWER_BOUND = "lower_bound"
    PARTIAL = "partial"


_ELIGIBLE_OPPORTUNITIES = frozenset(
    {
        "post_read_search",
        "post_mutation",
        "post_diagnostic",
        "post_validation",
        "post_submit",
    }
)
_RELATION_ALIASES = {
    "called_by": "calls",
    "inverse:calls": "calls",
    "tested_by": "asserted_by",
    "test_assertion": "asserted_by",
    "inverse:tested_by": "asserted_by",
    "inverse:asserted_by": "asserted_by",
}
_ACCEPTED_RELATIONS = frozenset(
    {
        "calls",
        "asserted_by",
        "imports",
        "implements",
        "inherits",
        "overrides",
        "references",
    }
)
_REJECTION_MARKERS = {
    "origin:builtin": "builtin_edge_rejected",
    "origin:stdlib": "stdlib_edge_rejected",
    "origin:third_party": "third_party_edge_rejected",
    "origin:framework": "framework_edge_rejected",
    "resolution:dynamic": "dynamic_edge_rejected",
    "resolution:reexport_unproven": "unproven_reexport_edge_rejected",
    "ambiguous": "ambiguous_edge_rejected",
    "unresolved": "unresolved_edge_rejected",
    "external": "external_edge_rejected",
    "global_fallback": "ambiguous_edge_rejected",
    "low_confidence": "low_confidence_edge_rejected",
}
_ORIGIN_REJECTIONS = {
    "builtin": "builtin_edge_rejected",
    "stdlib": "stdlib_edge_rejected",
    "third_party": "third_party_edge_rejected",
    "framework": "framework_edge_rejected",
    "external": "external_edge_rejected",
    "unknown": "unknown_origin_edge_rejected",
}
_RESOLUTION_REJECTIONS = {
    "ambiguous": "ambiguous_edge_rejected",
    "unresolved": "unresolved_edge_rejected",
    "dynamic": "dynamic_edge_rejected",
    "global_fallback": "ambiguous_edge_rejected",
    "reexport_unproven": "unproven_reexport_edge_rejected",
    "unknown": "unknown_resolution_edge_rejected",
}
_RELATION_PRIORITY = {
    "calls": 0,
    "asserted_by": 1,
    "implements": 2,
    "inherits": 3,
    "overrides": 4,
    "imports": 5,
    "references": 6,
}


def _path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _tokens(value: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(value or ""), re.UNICODE))


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _endpoint(path: str, symbol: str | None) -> str:
    normalized = _path(path)
    clean_symbol = str(symbol or "").strip()
    return normalized + (f"#{clean_symbol}" if clean_symbol else "")


@dataclass(frozen=True, slots=True)
class ContextOpportunity:
    kind: str
    evidence_action: int
    eligible_call: int
    source_revision: str
    graph_revision: str
    anchors: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind or "").strip().lower())
        object.__setattr__(
            self,
            "anchors",
            tuple(dict.fromkeys(_path(item) for item in self.anchors if _path(item))),
        )
        object.__setattr__(
            self,
            "changed_paths",
            tuple(dict.fromkeys(_path(item) for item in self.changed_paths if _path(item))),
        )


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    retrieval: HybridRetrievalResult
    structural_links: tuple[StructuralLink, ...]
    source_revision: str
    graph_revision: str
    delivered_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessStep:
    source_path: str
    target_path: str
    relation: str
    source_symbol: str = ""
    target_symbol: str = ""
    source_start_line: int | None = None
    target_start_line: int | None = None
    provenance: tuple[str, ...] = ()
    source_content_sha256: str = ""
    target_content_sha256: str = ""

    @property
    def rendered(self) -> str:
        return (
            f"{_endpoint(self.source_path, self.source_symbol)} "
            f"--{self.relation}--> "
            f"{_endpoint(self.target_path, self.target_symbol)}"
        )

    @property
    def claim_material(self) -> str:
        return "\0".join(
            (
                self.source_path,
                self.source_symbol,
                str(self.source_start_line or ""),
                self.source_content_sha256,
                self.relation,
                self.target_path,
                self.target_symbol,
                str(self.target_start_line or ""),
                self.target_content_sha256,
                *sorted(self.provenance),
            )
        )


@dataclass(frozen=True, slots=True)
class RelationalProcess:
    process_id: str
    anchor: str
    steps: tuple[ProcessStep, ...]
    cycle_terminated: bool = False
    truncated: bool = False

    @property
    def rendered(self) -> str:
        return " ; ".join(step.rendered for step in self.steps)


@dataclass(frozen=True, slots=True)
class RelationalContextResult:
    status: RelationalContextStatus
    epistemic_status: EpistemicStatus
    processes: tuple[RelationalProcess, ...]
    rendered_text: str
    claim_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    source_revision: str
    graph_revision: str
    token_count: int
    rejected_edge_count: int = 0
    truncated_process_count: int = 0


@dataclass(frozen=True, slots=True)
class RelationalContextProfile:
    """Versioned, receipt-visible bounds for relational composition."""

    profile_id: str = "relational-context-v1"
    max_depth: int = 6
    max_branching: int = 3
    max_processes: int = 3
    max_tokens: int = 256


FINAL_RELATIONAL_CONTEXT_PROFILE = RelationalContextProfile()


class RelationalContextComposer:
    """Compose small certified relational chains from a current snapshot.

    These chains are graph neighborhoods, not control-flow or runtime traces.
    """

    def __init__(
        self,
        *,
        max_depth: int = FINAL_RELATIONAL_CONTEXT_PROFILE.max_depth,
        max_branching: int = FINAL_RELATIONAL_CONTEXT_PROFILE.max_branching,
        max_processes: int = FINAL_RELATIONAL_CONTEXT_PROFILE.max_processes,
        max_tokens: int = FINAL_RELATIONAL_CONTEXT_PROFILE.max_tokens,
    ) -> None:
        self.max_depth = max(1, int(max_depth))
        self.max_branching = max(1, int(max_branching))
        self.max_processes = max(1, int(max_processes))
        self.max_tokens = max(0, int(max_tokens))

    @staticmethod
    def _accepted_link(link: StructuralLink) -> tuple[ProcessStep | None, str | None]:
        authority = certify_structural_link(link, RelationUse.PROCESS)
        if not authority.certified:
            return None, authority.reason
        if (
            not link.source_content_sha256
            or not link.target_content_sha256
            or not isinstance(link.source_start_line, int)
            or link.source_start_line < 1
            or not isinstance(link.target_start_line, int)
            or link.target_start_line < 1
        ):
            return None, "incomplete_edge_identity_rejected"
        provenance = " ".join(str(item).lower() for item in link.provenance)
        for marker, reason in _REJECTION_MARKERS.items():
            if marker in provenance:
                return None, reason
        if float(link.confidence) < 1.0:
            return None, "low_confidence_edge_rejected"
        raw_relation = str(link.relation or "").strip().lower()
        relation = _RELATION_ALIASES.get(raw_relation, raw_relation)
        if relation not in _ACCEPTED_RELATIONS:
            return None, "unsupported_relation_rejected"
        source_path = _path(link.source_path)
        target_path = _path(link.target_path)
        source_symbol = str(link.source_symbol or "").strip()
        target_symbol = str(link.target_symbol or "").strip()
        source_start_line = link.source_start_line
        target_start_line = link.target_start_line
        source_content_sha256 = link.source_content_sha256
        target_content_sha256 = link.target_content_sha256
        if raw_relation.startswith("inverse:") or raw_relation in {"called_by"}:
            source_path, target_path = target_path, source_path
            source_symbol, target_symbol = target_symbol, source_symbol
            source_start_line, target_start_line = target_start_line, source_start_line
            source_content_sha256, target_content_sha256 = (
                target_content_sha256,
                source_content_sha256,
            )
        return (
            ProcessStep(
                source_path=source_path,
                target_path=target_path,
                relation=relation,
                source_symbol=source_symbol,
                target_symbol=target_symbol,
                source_start_line=source_start_line,
                target_start_line=target_start_line,
                provenance=tuple(link.provenance),
                source_content_sha256=source_content_sha256,
                target_content_sha256=target_content_sha256,
            ),
            None,
        )

    def _build_processes(
        self,
        anchors: tuple[str, ...],
        links: tuple[StructuralLink, ...],
    ) -> tuple[tuple[RelationalProcess, ...], tuple[str, ...], int, int]:
        adjacency: dict[str, list[tuple[str, ProcessStep]]] = defaultdict(list)
        rejection_reasons: list[str] = []
        for link in links:
            step, rejection = self._accepted_link(link)
            if rejection:
                rejection_reasons.append(rejection)
                continue
            assert step is not None
            source_key = step.source_path
            target_key = step.target_path
            adjacency[source_key].append((target_key, step))
            adjacency[target_key].append((source_key, step))
        for rows in adjacency.values():
            rows.sort(
                key=lambda row: (
                    _RELATION_PRIORITY.get(row[1].relation, 99),
                    row[1].source_path.lower(),
                    row[1].target_path.lower(),
                    row[1].source_symbol.lower(),
                    row[1].target_symbol.lower(),
                )
            )

        processes: list[RelationalProcess] = []
        truncated_count = 0
        for anchor in sorted(dict.fromkeys(anchors), key=lambda item: (item.lower(), item)):
            anchor_key = anchor
            queue: deque[tuple[str, tuple[ProcessStep, ...], frozenset[str]]] = deque(
                [(anchor_key, (), frozenset({anchor_key}))]
            )
            while queue and len(processes) < self.max_processes:
                current, path_steps, visited = queue.popleft()
                rows = adjacency.get(current, ())
                if len(rows) > self.max_branching:
                    truncated_count += 1
                expanded = False
                for neighbor, step in rows[: self.max_branching]:
                    if neighbor in visited:
                        continue
                    next_steps = (*path_steps, step)
                    expanded = True
                    at_depth = len(next_steps) >= self.max_depth
                    next_rows = adjacency.get(neighbor, ())
                    terminal = at_depth or not any(
                        candidate not in visited for candidate, _ in next_rows
                    )
                    if terminal:
                        processes.append(
                            RelationalProcess(
                                process_id=_stable_id(
                                    "gt-process",
                                    *(item.claim_material for item in next_steps),
                                ),
                                anchor=anchor,
                                steps=next_steps,
                                truncated=at_depth,
                            )
                        )
                        truncated_count += int(at_depth)
                        if len(processes) >= self.max_processes:
                            break
                    else:
                        queue.append((neighbor, next_steps, visited | {neighbor}))
                if path_steps and not expanded:
                    processes.append(
                        RelationalProcess(
                            process_id=_stable_id(
                                "gt-process",
                                *(item.claim_material for item in path_steps),
                            ),
                            anchor=anchor,
                            steps=path_steps,
                            cycle_terminated=bool(rows),
                        )
                    )
            if len(processes) >= self.max_processes:
                break
        unique: dict[str, RelationalProcess] = {}
        for process in processes:
            unique.setdefault(process.process_id, process)
        return (
            tuple(unique.values()),
            tuple(dict.fromkeys(rejection_reasons)),
            truncated_count,
            len(rejection_reasons),
        )

    def _abstain(
        self,
        opportunity: ContextOpportunity,
        reasons: tuple[str, ...],
        *,
        rejected_edge_count: int = 0,
    ) -> RelationalContextResult:
        return RelationalContextResult(
            status=RelationalContextStatus.ABSTAIN,
            epistemic_status=EpistemicStatus.PARTIAL,
            processes=(),
            rendered_text="",
            claim_ids=(),
            reason_codes=reasons,
            source_revision=opportunity.source_revision,
            graph_revision=opportunity.graph_revision,
            token_count=0,
            rejected_edge_count=rejected_edge_count,
        )

    def compose(
        self,
        opportunity: ContextOpportunity,
        snapshot: EvidenceSnapshot,
    ) -> RelationalContextResult:
        if (
            opportunity.source_revision != snapshot.source_revision
            or opportunity.graph_revision != snapshot.graph_revision
        ):
            return self._abstain(opportunity, ("stale_evidence_snapshot",))
        if opportunity.kind not in _ELIGIBLE_OPPORTUNITIES:
            return self._abstain(opportunity, ("ineligible_opportunity",))

        retrieval_anchors = tuple(
            candidate.path for candidate in snapshot.retrieval.selected_context
        )
        anchors = tuple(
            dict.fromkeys(
                _path(item)
                for item in (
                    *opportunity.anchors,
                    *opportunity.changed_paths,
                    *retrieval_anchors,
                )
                if _path(item)
            )
        )
        if not anchors:
            return self._abstain(opportunity, ("no_repository_anchor",))

        processes, rejected_reasons, truncated_count, rejected_edge_count = (
            self._build_processes(
            anchors,
            snapshot.structural_links,
            )
        )
        if not processes:
            reasons = tuple(dict.fromkeys(("no_certified_process", *rejected_reasons)))
            return self._abstain(
                opportunity,
                reasons,
                rejected_edge_count=rejected_edge_count,
            )

        heading = "Certified relational context (lower bound; omitted edges may exist):"
        selected: list[RelationalProcess] = []
        rendered_lines = [heading]
        used_tokens = _tokens(heading)
        for process in processes:
            line = f"- {process.rendered}"
            required = _tokens(line)
            if used_tokens + required > self.max_tokens:
                continue
            selected.append(process)
            rendered_lines.append(line)
            used_tokens += required
        if not selected:
            return self._abstain(opportunity, ("process_token_budget",))

        delivered = set(snapshot.delivered_claim_ids)
        novel = tuple(
            process
            for process in selected
            if process.process_id not in delivered
        )
        if not novel:
            return self._abstain(opportunity, ("duplicate_relational_claim",))
        rendered_text = "\n".join(
            [heading, *(f"- {process.rendered}" for process in novel)]
        )
        claim_ids = tuple(process.process_id for process in novel)
        reason_codes = tuple(
            dict.fromkeys(
                (
                    "certified_lower_bound",
                    *(("process_truncated",) if truncated_count else ()),
                    *rejected_reasons,
                )
            )
        )
        return RelationalContextResult(
            status=RelationalContextStatus.DELIVER,
            epistemic_status=EpistemicStatus.LOWER_BOUND,
            processes=novel,
            rendered_text=rendered_text,
            claim_ids=claim_ids,
            reason_codes=reason_codes,
            source_revision=snapshot.source_revision,
            graph_revision=snapshot.graph_revision,
            token_count=_tokens(rendered_text),
            rejected_edge_count=rejected_edge_count,
            truncated_process_count=truncated_count,
        )


__all__ = [
    "ContextOpportunity",
    "EpistemicStatus",
    "EvidenceSnapshot",
    "FINAL_RELATIONAL_CONTEXT_PROFILE",
    "ProcessStep",
    "RelationalContextComposer",
    "RelationalContextProfile",
    "RelationalContextResult",
    "RelationalProcess",
    "RelationalContextStatus",
]
