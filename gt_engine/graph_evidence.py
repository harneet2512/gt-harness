"""Decision-specific, bounded semantic projection of graph facts."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from gt_engine.graph_context import GraphProjection, GraphSemanticFact
from gt_engine.task_contract import TaskContract, TaskResourceRole, significant_tokens

_SURFACE_ACTION = {
    "nodes_fts": "inspect the ranked definition",
    "symbol_content_fts": "inspect the matching implementation body",
    "content_passages_fts": "inspect the requirement-specific source passage",
    "properties": "check the stored signature, constant, or schema property",
    "assertions": "execute or preserve the indexed invariant",
    "edge_metadata": "inspect the proven related symbol",
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _fact_keys(fact: GraphSemanticFact) -> set[str]:
    values = (fact.file_path, fact.symbol, fact.kind, fact.value)
    keys: set[str] = set()
    for value in values:
        whole = _key(value)
        if len(whole) >= 4:
            keys.add(whole)
        keys.update(significant_tokens(str(value or "")))
    return keys


@dataclass(frozen=True)
class EvidenceNeed:
    role: str
    boundary: str
    unresolved_obligation_ids: tuple[str, ...]
    anchors: tuple[str, ...]
    active_paths: tuple[str, ...]
    task_paths: tuple[str, ...]
    recent_red: bool
    graph_revision: str


@dataclass(frozen=True)
class GraphEvidence:
    surface: str
    file_path: str
    symbol: str
    line: int
    claim: str
    confidence: float
    semantic_certainty: float
    retrieval_relevance: float
    relevance_reason_codes: tuple[str, ...]
    revision: str
    obligation_ids: tuple[str, ...]
    active_target_linked: bool
    intended_action: str
    rank: int

    def to_receipt(self) -> dict[str, object]:
        return asdict(self)


def build_evidence_need(
    contract: TaskContract,
    projection: GraphProjection,
    *,
    boundary: str,
    verified_obligation_ids: set[str] | frozenset[str] = frozenset(),
    active_paths: tuple[str, ...] = (),
    recent_red: bool = False,
) -> EvidenceNeed:
    unresolved = tuple(
        item for item in contract.obligations
        if item.obligation_id not in verified_obligation_ids
    )
    anchors: list[str] = []
    for item in unresolved:
        anchors.extend(significant_tokens(item.text))
        anchors.extend(subject.lower() for subject in item.subjects)
    task_paths = tuple(
        dict.fromkeys(
            str(item.path).replace("\\", "/").lower()
            for item in contract.resources
            if item.confidence >= 0.8
            and item.role
            in {
                TaskResourceRole.INPUT,
                TaskResourceRole.REFERENCE,
                TaskResourceRole.EXECUTABLE,
            }
        )
    )
    anchors.extend(task_paths)
    return EvidenceNeed(
        role=contract.role,
        boundary=str(boundary or "unknown"),
        unresolved_obligation_ids=tuple(
            item.obligation_id for item in unresolved
        ),
        anchors=tuple(dict.fromkeys(anchors)),
        active_paths=tuple(dict.fromkeys(
            str(path).replace("\\", "/") for path in active_paths if path
        )),
        task_paths=task_paths,
        recent_red=bool(recent_red),
        graph_revision=projection.revision,
    )


def rank_graph_evidence(
    contract: TaskContract,
    projection: GraphProjection,
    need: EvidenceNeed,
    *,
    limit: int = 12,
) -> tuple[GraphEvidence, ...]:
    """Link graph facts to unresolved obligations or an active changed path."""
    obligations = {
        item.obligation_id: (
            set(significant_tokens(item.text)),
            {_key(subject) for subject in item.subjects if _key(subject)},
        )
        for item in contract.obligations
        if item.obligation_id in need.unresolved_obligation_ids
    }
    anchor_frequency = Counter(
        anchor
        for lexical, subjects in obligations.values()
        for anchor in lexical | subjects
    )
    active = {
        str(path).replace("\\", "/").lower() for path in need.active_paths
    }
    generic_symbols = {
        "app",
        "url",
        "run",
        "main",
        "model",
        "repr",
        "str",
        "init",
    }
    task_paths = set(need.task_paths)
    relation_surfaces = {
        "edges",
        "closure",
        "assertions",
        "cochanges",
        "cochange_sets",
        "edge_metadata",
    }
    scored: list[
        tuple[
            tuple[float, ...],
            GraphSemanticFact,
            tuple[str, ...],
            float,
            tuple[str, ...],
        ]
    ] = []
    for position, fact in enumerate(projection.semantic_facts):
        keys = _fact_keys(fact)
        linked: list[tuple[str, float]] = []
        for obligation_id, (lexical, subjects) in obligations.items():
            overlap = lexical & keys
            exact_subject = subjects & keys
            # Repeated generic words such as "sensitive", "replace", or
            # "output" must not beat a distinctive path/symbol match merely
            # because they occur in many obligations.
            weighted_overlap = sum(
                (1.0 / float(anchor_frequency[anchor]))
                + (0.75 if anchor_frequency[anchor] == 1 else 0.0)
                for anchor in overlap
            )
            strength = weighted_overlap + (3.0 * len(exact_subject))
            if exact_subject or strength >= 1.5:
                linked.append((obligation_id, strength))
        links = tuple(item[0] for item in linked)
        strongest_link = max((item[1] for item in linked), default=0.0)
        path_active = fact.file_path.lower() in active
        relation_material = " ".join(
            (fact.value, fact.kind, fact.symbol, fact.file_path)
        ).replace("\\", "/").lower()
        relation_active = bool(
            fact.surface in relation_surfaces
            and any(path and path in relation_material for path in active)
        )
        if not links and not path_active and not relation_active:
            continue
        normalized_path = fact.file_path.replace("\\", "/").lower()
        path_task = normalized_path in task_paths
        fact_symbol = _key(fact.symbol)
        distinctive_subject = any(
            len(subject) >= 4
            and subject not in generic_symbols
            and subject in keys
            for _obligation_id, (_lexical, subjects) in obligations.items()
            for subject in subjects
        )
        distinctive_symbol = bool(
            len(fact_symbol) >= 4
            and fact_symbol not in generic_symbols
            and any(
                fact_symbol in lexical or fact_symbol in subjects
                for lexical, subjects in obligations.values()
            )
        )
        reasons: list[str] = []
        if path_active:
            relevance = 1.0
            reasons.append("exact_active_path")
        elif relation_active and fact.semantic_certainty >= 0.95:
            relevance = 0.95
            reasons.append("certified_relation_to_active_path")
        elif relation_active:
            relevance = 0.85
            reasons.append("relation_to_active_path")
        elif path_task:
            relevance = 1.0
            reasons.append("exact_task_resource_path")
        elif distinctive_subject or distinctive_symbol:
            relevance = 0.95
            reasons.append(
                "exact_distinctive_subject"
                if distinctive_subject
                else "exact_distinctive_symbol"
            )
        else:
            relevance = 0.85
            reasons.append("distinctive_lexical_overlap")
        semantic_certainty = max(
            0.0,
            min(
                1.0,
                float(fact.semantic_certainty or 0.0),
            ),
        )
        score = (
            float(bool(path_active)),
            float(bool(path_task)),
            relevance,
            strongest_link,
            semantic_certainty,
            float(-len(links)),
            float(-position),
        )
        scored.append((score, fact, links, relevance, tuple(reasons)))
    scored.sort(key=lambda row: row[0], reverse=True)
    out: list[GraphEvidence] = []
    for rank, (_score, fact, links, relevance, reasons) in enumerate(
        scored[: max(1, limit)], 1
    ):
        semantic_certainty = max(0.0, min(1.0, float(fact.semantic_certainty or 0.0)))
        out.append(GraphEvidence(
            surface=fact.surface,
            file_path=fact.file_path,
            symbol=fact.symbol,
            line=fact.line,
            claim=f"{fact.kind}: {fact.value}"[:500],
            confidence=semantic_certainty,
            semantic_certainty=semantic_certainty,
            retrieval_relevance=max(0.0, min(1.0, relevance)),
            relevance_reason_codes=reasons,
            revision=fact.revision,
            obligation_ids=links,
            active_target_linked=bool(
                fact.file_path.lower() in active
            ),
            intended_action=_SURFACE_ACTION.get(
                fact.surface, "inspect this graph-grounded task surface"
            ),
            rank=rank,
        ))
    return tuple(out)
