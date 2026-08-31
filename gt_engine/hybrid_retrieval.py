"""Revision-bound hybrid retrieval primitives.

Retrieval is a ranking aid only.  It cannot become an edit-owner authority,
and it refuses to run when its repository identity disagrees with the central
runtime binding.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from gt_engine.repository_intelligence import CentralRuntimeBinding, CentralRuntimeIdentityError

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


class RetrievalState(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    ABSTAINED = "abstained"


class RetrievalIntent(StrEnum):
    INSPECT = "inspect"
    EDIT = "edit"
    VALIDATE = "validate"


class RetrievalChannel(StrEnum):
    LEXICAL = "lexical"
    STRUCTURAL = "structural"


class EvidenceAuthority(StrEnum):
    GRAPH = "graph"
    CHECKOUT = "checkout"
    INFERRED = "inferred"


class EvidenceOrigin(StrEnum):
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    SOURCE_SPAN = "source_span"


@dataclass(frozen=True, slots=True)
class RepositoryDocument:
    path: str
    start_line: int = 1
    end_line: int = 1
    symbol: str = ""
    text: str = ""
    provenance: tuple[str, ...] = ()
    authority: EvidenceAuthority = EvidenceAuthority.CHECKOUT
    document_id: str = ""

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid document line range")
        if not self.document_id:
            value = f"{self.path}\0{self.start_line}\0{self.end_line}\0{self.symbol}"
            object.__setattr__(self, "document_id", "doc-" + hashlib.sha256(value.encode()).hexdigest()[:20])


@dataclass(frozen=True, slots=True)
class StructuralLink:
    source_path: str
    target_path: str
    relation: str
    confidence: float = 1.0
    provenance: tuple[str, ...] = ()
    certified: bool = False
    source_symbol: str = ""
    target_symbol: str = ""


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    path: str
    score: float
    channels: tuple[RetrievalChannel, ...] = ()
    document_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    owner_certified: bool = False
    authority: EvidenceAuthority = EvidenceAuthority.INFERRED


@dataclass(frozen=True, slots=True)
class RankedFile:
    path: str
    score: float
    rank: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    state: RetrievalState
    candidates: tuple[RetrievalCandidate, ...] = ()
    ranked_files: tuple[RankedFile, ...] = ()
    query_digest: str = ""
    repository_revision: str = ""
    graph_revision: str = ""
    fallback_reason: str = ""


def retrieval_query_terms(query: str) -> tuple[str, ...]:
    stop = {"a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "into", "is", "of", "on", "or", "the", "to", "with"}
    return tuple(dict.fromkeys(t.lower() for t in _TOKEN_RE.findall(query) if t.lower() not in stop))


class HybridRetriever:
    def __init__(self, documents: tuple[RepositoryDocument, ...] = (),
                 structural_links: tuple[StructuralLink, ...] = (), *,
                 runtime_binding: CentralRuntimeBinding | None = None) -> None:
        self.documents = tuple(documents)
        self.structural_links = tuple(structural_links)
        self.runtime_binding = runtime_binding

    def _validate_binding(self, *, repository_revision: str, graph_revision: str) -> None:
        if self.runtime_binding is None:
            raise CentralRuntimeIdentityError("hybrid retrieval requires central runtime binding")
        if self.runtime_binding.repository_revision != repository_revision:
            raise CentralRuntimeIdentityError("hybrid retrieval repository_revision mismatch")
        if self.runtime_binding.graph_revision != graph_revision:
            raise CentralRuntimeIdentityError("hybrid retrieval graph_revision mismatch")

    def retrieve(self, query: str, *, repository_revision: str, graph_revision: str,
                 intent: RetrievalIntent = RetrievalIntent.INSPECT, limit: int = 20,
                 include_tests: bool = True) -> HybridRetrievalResult:
        self._validate_binding(repository_revision=repository_revision, graph_revision=graph_revision)
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = retrieval_query_terms(query)
        digest = hashlib.sha256(query.encode()).hexdigest()
        scores: dict[str, float] = {}
        docs: dict[str, list[RepositoryDocument]] = {}
        reasons: dict[str, list[str]] = {}
        for doc in self.documents:
            docs.setdefault(doc.path, []).append(doc)
            haystack = f"{doc.path} {doc.symbol} {doc.text}".lower()
            hits = sum(haystack.count(t) for t in terms)
            if hits:
                scores[doc.path] = scores.get(doc.path, 0.0) + hits / max(1, len(terms))
                reasons.setdefault(doc.path, []).append(f"lexical:{hits}")
        for link in self.structural_links:
            if link.certified and link.confidence >= 1.0 and link.source_path in scores and link.target_path in docs:
                scores[link.target_path] = scores.get(link.target_path, 0.0) + 0.25
                reasons.setdefault(link.target_path, []).append(f"relation:{link.relation}")
        if not include_tests:
            scores = {p: s for p, s in scores.items() if not p.replace("\\", "/").startswith("tests/")}
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        candidates = []
        for path, score in ordered:
            links = [l for l in self.structural_links if l.target_path == path and l.certified and l.confidence >= 1.0]
            candidates.append(RetrievalCandidate(
                path=path, score=score,
                channels=(RetrievalChannel.LEXICAL, RetrievalChannel.STRUCTURAL) if links else (RetrievalChannel.LEXICAL,),
                document_ids=tuple(d.document_id for d in docs[path]),
                reasons=tuple(sorted(reasons.get(path, []))),
                owner_certified=bool(links) and intent is RetrievalIntent.EDIT,
                authority=EvidenceAuthority.GRAPH if links else EvidenceAuthority.CHECKOUT,
            ))
        ranked = tuple(RankedFile(c.path, c.score, i + 1, c.reasons) for i, c in enumerate(candidates))
        return HybridRetrievalResult(
            state=RetrievalState.READY if candidates else RetrievalState.EMPTY,
            candidates=tuple(candidates), ranked_files=ranked, query_digest=digest,
            repository_revision=repository_revision, graph_revision=graph_revision,
        )
