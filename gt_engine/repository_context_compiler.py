"""Deterministic, revision-bound repository context for coding agents.

The compiler turns the existing hybrid repository and certified graph edges
into a compact decision packet.  Retrieval scores may rank evidence, but only
exact repository identities and certified structural relationships may become
provider-visible facts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import (
    EvidenceOrigin,
    HybridRetriever,
    RankedFile,
    RetrievalChannel,
    RetrievalIntent,
    RetrievalState,
    StructuralLink,
    retrieval_query_terms,
)
from gt_engine.repository_context import (
    DecisionOpportunity,
    RepositoryContextEngine,
    RepositoryContextStatus,
    RepositorySnapshot,
    RetrievalRankHint,
)
from gt_engine.repository_intelligence import RepositoryEvidence


class ContextStatus(StrEnum):
    READY = "READY"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ContextCompileRequest:
    task: str
    source_revision: str
    graph_revision: str
    intent: RetrievalIntent = RetrievalIntent.IMPLEMENTATION_CONTEXT
    active_paths: tuple[str, ...] = ()
    active_symbols: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    validation_state: str = "unknown"
    previously_exposed_claims: tuple[str, ...] = ()
    token_budget: int = 1_000
    character_budget: int = 4_000
    dense_candidates: tuple[tuple[str, float], ...] = ()
    dense_index_receipt: dict[str, Any] = field(default_factory=dict)
    retrieval_mode: str = "sparse_only"

    def retrieval_state(self) -> RetrievalState:
        return RetrievalState(
            task_text=self.task,
            intent=self.intent,
            active_paths=self.active_paths,
            active_symbols=self.active_symbols,
            changed_paths=self.changed_paths,
            diagnostics=self.diagnostics,
            validation_state=self.validation_state,
            source_revision=self.source_revision,
            previously_exposed_claims=self.previously_exposed_claims,
        )


@dataclass(frozen=True, slots=True)
class ContextEvidenceItem:
    kind: str
    path: str
    start_line: int
    end_line: int
    symbol: str
    relation: str
    confidence: float | None
    verification_status: str
    source_revision: str
    graph_revision: str
    evidence_sha256: str
    decision_reason: str
    completeness: str
    source_path: str = ""
    source_symbol: str = ""
    source_excerpt: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GTContextPacket:
    status: ContextStatus
    repository_identity: dict[str, Any]
    task_anchors: tuple[ContextEvidenceItem, ...] = ()
    primary_edit_targets: tuple[ContextEvidenceItem, ...] = ()
    inspection_candidates: tuple[ContextEvidenceItem, ...] = ()
    supporting_files: tuple[ContextEvidenceItem, ...] = ()
    symbol_contracts: tuple[ContextEvidenceItem, ...] = ()
    semantic_facts: tuple[str, ...] = ()
    semantic_graph_receipt: dict[str, Any] = field(default_factory=dict)
    execution_paths: tuple[str, ...] = ()
    change_surface: tuple[str, ...] = ()
    affected_tests: tuple[str, ...] = ()
    validation_plan: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    evidence_items: tuple[ContextEvidenceItem, ...] = ()
    coverage: dict[str, Any] = field(default_factory=dict)
    selected_token_count: int = 0
    retrieval_channel_count: int = 0
    truncated: bool = False
    projection_claim_ids: tuple[str, ...] = ()

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_sha256 for item in self.evidence_items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "repository_identity": dict(self.repository_identity),
            "task_anchors": [item.as_dict() for item in self.task_anchors],
            "primary_edit_targets": [
                item.as_dict() for item in self.primary_edit_targets
            ],
            "inspection_candidates": [
                item.as_dict() for item in self.inspection_candidates
            ],
            "supporting_files": [item.as_dict() for item in self.supporting_files],
            "symbol_contracts": [item.as_dict() for item in self.symbol_contracts],
            "semantic_facts": list(self.semantic_facts),
            "semantic_graph_receipt": dict(self.semantic_graph_receipt),
            "execution_paths": list(self.execution_paths),
            "change_surface": list(self.change_surface),
            "affected_tests": list(self.affected_tests),
            "validation_plan": list(self.validation_plan),
            "uncertainties": list(self.uncertainties),
            "evidence_items": [item.as_dict() for item in self.evidence_items],
            "coverage": dict(self.coverage),
            "selected_token_count": self.selected_token_count,
            "retrieval_channel_count": self.retrieval_channel_count,
            "truncated": self.truncated,
            "projection_claim_ids": list(self.projection_claim_ids),
        }


_EXPLICIT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]{2,}")
_QUOTED_IDENTIFIER = re.compile(
    r"(?:`|'|\")([A-Za-z_][A-Za-z0-9_.:]{1,})(?:`|'|\")"
)
_SYMBOL_CUE = re.compile(
    r"(?i)\b(?:class|constant|function|interface|method|module|symbol|type|variable)\s+"
    r"(?:`|'|\")?([A-Za-z_][A-Za-z0-9_.:]{1,})(?:`|'|\")?"
)
_ISSUE_LANGUAGE_WORDS = frozenset(
    {
        "add",
        "analyze",
        "breaking",
        "build",
        "call",
        "change",
        "check",
        "clean",
        "code",
        "complete",
        "create",
        "delete",
        "determine",
        "edit",
        "ensure",
        "execute",
        "file",
        "files",
        "find",
        "fix",
        "generate",
        "identify",
        "implement",
        "improve",
        "install",
        "into",
        "keep",
        "list",
        "load",
        "make",
        "modify",
        "move",
        "open",
        "optimize",
        "parse",
        "process",
        "read",
        "remove",
        "repo",
        "repository",
        "reject",
        "replace",
        "report",
        "return",
        "run",
        "save",
        "send",
        "start",
        "stop",
        "support",
        "test",
        "update",
        "use",
        "validate",
        "verify",
        "win",
        "without",
        "wire",
        "write",
    }
)
_TEST_SEGMENTS = ("/test/", "/tests/", "/__tests__/")
_LEGACY_SEGMENTS = (
    "/benchmark/",
    "/benchmarks/",
    "/eval/",
    "/research/",
    "/legacy/",
    "/scripts/",
    "/.github/workflows/",
    "/src/groundtruth/pretask/",
)
_GENERATED_SEGMENTS = ("/vendor/", "/node_modules/", "/dist/", "/build/")
_PROVIDER_RELATIONS = frozenset(
    {
        "API_CALL",
        "API_CALLS",
        "ASSERTED_BY",
        "CALLS",
        "EXTENDS",
        "HANDLES_ROUTE",
        "IMPLEMENTS",
        "IMPORTS",
        "OVERRIDES",
        "REFERENCES",
        "RE_EXPORTS",
        "TESTED_BY",
    }
)


def _normalized_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_test(path: str) -> bool:
    normalized = "/" + _normalized_path(path).lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        any(segment in normalized for segment in _TEST_SEGMENTS)
        or name.startswith("test_")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
    )


def _path_penalty(path: str) -> int:
    normalized = "/" + _normalized_path(path).lower()
    if any(segment in normalized for segment in _GENERATED_SEGMENTS):
        return 4
    if any(segment in normalized for segment in _LEGACY_SEGMENTS):
        return 3
    if _is_test(path):
        return 1
    if normalized.endswith((".md", ".rst", ".txt", ".yml", ".yaml", ".json")):
        return 2
    return 0


def _explicit_identifiers(request: ContextCompileRequest) -> dict[str, int]:
    values = [request.task, *request.active_symbols]
    identifiers: dict[str, int] = {}
    for value in values:
        for token in _EXPLICIT_TOKEN.findall(str(value or "")):
            for identifier in (token.lower(), token.rsplit(".", 1)[-1].lower()):
                if identifier in _ISSUE_LANGUAGE_WORDS:
                    continue
                identifiers.setdefault(identifier, len(identifiers))
    return identifiers


def _authoritative_symbol_identifiers(request: ContextCompileRequest) -> frozenset[str]:
    """Return symbols the task actually identifies, not incidental prose words."""

    identifiers: set[str] = set()

    def add(value: str) -> None:
        token = str(value or "").strip("`'\"")
        if not token or "/" in token or "\\" in token:
            return
        lowered = token.lower()
        if lowered in _ISSUE_LANGUAGE_WORDS:
            return
        identifiers.add(lowered)
        identifiers.add(token.rsplit(".", 1)[-1].rsplit("::", 1)[-1].lower())

    for symbol in request.active_symbols:
        add(symbol)
    for pattern in (_QUOTED_IDENTIFIER, _SYMBOL_CUE):
        for match in pattern.finditer(request.task):
            add(match.group(1))
    for token in _EXPLICIT_TOKEN.findall(request.task):
        if "_" in token or "::" in token or (
            not token.isupper() and any(character.isupper() for character in token[1:])
        ):
            add(token)
    return frozenset(item for item in identifiers if item)


def _concrete_identifiers(request: ContextCompileRequest) -> frozenset[str]:
    """Return task tokens whose spelling identifies a concrete code artifact.

    Paths, qualified names, snake-case constants, and case-significant names
    are stronger than ordinary prose. If the repository cannot match one of
    these anchors, generic lexical similarity is not decision-grade evidence.
    """
    concrete: set[str] = set()
    for value in (request.task, *request.active_symbols):
        for token in _EXPLICIT_TOKEN.findall(str(value or "")):
            lowered = token.lower()
            if lowered in _ISSUE_LANGUAGE_WORDS:
                continue
            if (
                any(separator in token for separator in ("/", "\\", ".", "_", "::"))
                or any(character.isupper() for character in token[1:])
                or token.isupper()
            ):
                concrete.add(lowered)
                concrete.add(token.rsplit(".", 1)[-1].lower())
    return frozenset(item for item in concrete if item)


def _exact_candidate(ranked: RankedFile):
    return dict(ranked.channel_candidates).get(RetrievalChannel.EXACT)


def _rank_key(ranked: RankedFile, identifiers: dict[str, int]) -> tuple[Any, ...]:
    exact = _exact_candidate(ranked)
    symbol = str(exact.symbol if exact is not None else ranked.representative.symbol or "")
    exact_symbol = bool(symbol and symbol.lower() in identifiers)
    exact_path = bool(
        exact is not None
        and "exact_path" in set(exact.provenance)
    )
    return (
        0 if exact_symbol else 1 if exact_path else 2,
        identifiers.get(symbol.lower(), len(identifiers)),
        _path_penalty(ranked.path),
        -float(ranked.fused_score),
        ranked.path.lower(),
        ranked.path,
    )


def _sha(*parts: object) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity_item(
    ranked: RankedFile,
    request: ContextCompileRequest,
    *,
    decision_reason: str,
) -> ContextEvidenceItem:
    candidate = _exact_candidate(ranked) or ranked.representative
    start = max(1, int(candidate.start_line or 1))
    end = max(start, int(candidate.end_line or start))
    symbol = str(candidate.symbol or "")
    evidence_sha = _sha(
        "identity",
        candidate.path,
        start,
        end,
        symbol,
        request.source_revision,
    )
    return ContextEvidenceItem(
        kind="symbol_identity" if symbol else "file_identity",
        path=candidate.path,
        start_line=start,
        end_line=end,
        symbol=symbol,
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision=request.source_revision,
        graph_revision=request.graph_revision,
        evidence_sha256=evidence_sha,
        decision_reason=decision_reason,
        completeness="exact_identity",
        source_excerpt=str(candidate.text or "").strip()[:600],
    )


def _inspection_item(
    ranked: RankedFile,
    request: ContextCompileRequest,
    *,
    decision_reason: str = "hybrid_retrieval_inspection",
) -> ContextEvidenceItem:
    candidate = ranked.representative
    start = max(1, int(candidate.start_line or 1))
    end = max(start, int(candidate.end_line or start))
    symbol = str(candidate.symbol or "")
    return ContextEvidenceItem(
        kind="inspection_candidate",
        path=candidate.path,
        start_line=start,
        end_line=end,
        symbol=symbol,
        relation="",
        confidence=max(0.0, min(1.0, float(ranked.fused_score))),
        verification_status="verified_source_identity",
        source_revision=request.source_revision,
        graph_revision=request.graph_revision,
        evidence_sha256=_sha(
            "inspection", candidate.path, start, end, symbol, request.source_revision
        ),
        decision_reason=decision_reason,
        completeness="ranked_candidate_not_edit_target",
        source_excerpt=str(candidate.text or "").strip()[:600],
    )


def _dense_inspection_item(
    document: Any,
    request: ContextCompileRequest,
    *,
    score: float,
) -> ContextEvidenceItem:
    start = max(1, int(document.start_line or 1))
    end = max(start, int(document.end_line or start))
    return ContextEvidenceItem(
        kind="inspection_candidate",
        path=str(document.path),
        start_line=start,
        end_line=end,
        symbol="",
        relation="",
        confidence=max(-1.0, min(1.0, float(score))),
        verification_status="verified_source_identity",
        source_revision=request.source_revision,
        graph_revision=request.graph_revision,
        evidence_sha256=_sha(
            "dense_inspection", document.path, start, end, request.source_revision
        ),
        decision_reason="dense_semantic_inspection",
        completeness="dense_file_candidate_not_edit_target",
        source_excerpt=str(document.text or "").strip()[:600],
    )


def _safe_link(link: StructuralLink) -> bool:
    return bool(
        link.certified
        and str(link.verification_status or "").lower() == "verified"
        and link.origin == "program"
        and str(link.relation or "").upper() in _PROVIDER_RELATIONS
        and link.resolution_outcome == "exact"
        and bool(str(link.resolution_method or "").strip())
        and link.candidate_count == 1
        and float(link.confidence) >= 0.95
        and link.source_symbol
        and link.target_symbol
        and int(link.source_start_line or 0) > 0
        and int(link.target_start_line or 0) > 0
        and link.source_content_sha256
        and link.target_content_sha256
        and link.source_evidence_origin == EvidenceOrigin.PREEXISTING_REPOSITORY.value
        and link.target_evidence_origin == EvidenceOrigin.PREEXISTING_REPOSITORY.value
    )


def _link_rejection_reason(link: StructuralLink) -> str:
    if not link.certified or str(link.verification_status or "").lower() != "verified":
        return "unverified_edge_rejected"
    if str(link.relation or "").upper() not in _PROVIDER_RELATIONS:
        return "unsupported_relationship_rejected"
    return "relationship_provenance_rejected"


def _link_item(link: StructuralLink, request: ContextCompileRequest) -> ContextEvidenceItem:
    relation = str(link.relation or "").upper()
    evidence_sha = _sha(
        "relationship",
        link.source_path,
        link.source_symbol,
        link.target_path,
        link.target_symbol,
        relation,
        request.source_revision,
    )
    return ContextEvidenceItem(
        kind="relationship",
        path=link.target_path,
        start_line=max(1, int(link.target_start_line or 1)),
        end_line=max(1, int(link.target_start_line or 1)),
        symbol=str(link.target_symbol or ""),
        relation=relation,
        confidence=float(link.confidence),
        verification_status="verified",
        source_revision=request.source_revision,
        graph_revision=request.graph_revision,
        evidence_sha256=evidence_sha,
        decision_reason=f"certified_{relation.lower()}_relationship",
        completeness="certified_direct_edge",
        source_path=link.source_path,
        source_symbol=str(link.source_symbol or ""),
    )


class RepositoryContextCompiler:
    """Compile exact identities and certified relationships into one packet."""

    def compile(
        self,
        repository: HybridRepository,
        request: ContextCompileRequest,
    ) -> GTContextPacket:
        identity = {
            "source_revision": request.source_revision,
            "graph_revision": request.graph_revision,
        }
        if repository.source_revision != request.source_revision:
            return GTContextPacket(
                status=ContextStatus.FAILED,
                repository_identity=identity,
                uncertainties=("repository_source_revision_mismatch",),
            )
        if not repository.complete:
            return GTContextPacket(
                status=ContextStatus.FAILED,
                repository_identity=identity,
                uncertainties=tuple(
                    dict.fromkeys((*repository.reason_codes, "hybrid_repository_incomplete"))
                ),
            )

        state = request.retrieval_state()
        retriever = HybridRetriever(
            repository.documents,
            structural_links=repository.structural_links,
            dense_backend=None,
            dense_fallback_only=True,
        )
        retrieval = retriever.retrieve(
            state,
            channel_limit=100,
            top_k=20,
            selection_limit=5,
            token_budget=max(1, min(1_000, int(request.token_budget))),
            character_budget=max(1, int(request.character_budget)),
        )
        identifiers = _explicit_identifiers(request)
        authoritative_symbols = _authoritative_symbol_identifiers(request)
        concrete_identifiers = _concrete_identifiers(request)
        ranked = tuple(sorted(retrieval.ranked_files, key=lambda row: _rank_key(row, identifiers)))

        exact_symbol_rows = tuple(
            row
            for row in ranked
            if (candidate := _exact_candidate(row)) is not None
            and bool(candidate.symbol)
            and str(candidate.symbol).lower() in authoritative_symbols
        )
        exact_path_rows = tuple(
            row
            for row in ranked
            if (candidate := _exact_candidate(row)) is not None
            and "exact_path" in set(candidate.provenance)
        )
        def matches_concrete_anchor(row: RankedFile) -> bool:
            candidate = _exact_candidate(row) or row.representative
            haystack = " ".join(
                (
                    str(candidate.path or ""),
                    str(candidate.symbol or ""),
                    str(candidate.text or ""),
                )
            ).lower()
            return any(identifier in haystack for identifier in concrete_identifiers)

        inspection_rows = tuple(
            row
            for row in ranked
            if _exact_candidate(row) is None
            and len(row.channel_ranks) >= 2
            and (
                not concrete_identifiers
                or matches_concrete_anchor(row)
            )
        )
        primary_rows = tuple((exact_symbol_rows or exact_path_rows)[:3])
        primary = tuple(
            _identity_item(
                row,
                request,
                decision_reason=(
                    "exact_task_symbol"
                    if row in exact_symbol_rows
                    else "exact_task_path"
                    if row in exact_path_rows
                    else "exact_repository_identity"
                ),
            )
            for row in primary_rows
        )
        exposed = set(request.previously_exposed_claims)
        primary = tuple(item for item in primary if item.evidence_sha256 not in exposed)
        exact_row_paths = {row.path for row in primary_rows}
        documents_by_path_for_dense: dict[str, Any] = {}
        for document in repository.documents:
            documents_by_path_for_dense.setdefault(document.path, document)

        # Fuse independent dense and sparse ranks at the file boundary. Dense
        # similarity remains retrieval evidence only: it can improve inspection
        # ordering, but it can never manufacture an exact symbol or edit target.
        rrf_k = 60
        sparse_by_path = {
            row.path: (rank, row)
            for rank, row in enumerate(inspection_rows, start=1)
            if row.path not in exact_row_paths
        }
        dense_by_path: dict[str, tuple[int, float]] = {}
        for rank, (path, score) in enumerate(request.dense_candidates, start=1):
            if path in documents_by_path_for_dense and path not in dense_by_path:
                dense_by_path[path] = (rank, float(score))
        fusion_rows: list[dict[str, Any]] = []
        for path in sorted(set(sparse_by_path) | set(dense_by_path)):
            sparse_entry = sparse_by_path.get(path)
            dense_entry = dense_by_path.get(path)
            channels = tuple(
                channel
                for channel, present in (
                    ("dense", dense_entry is not None),
                    ("sparse", sparse_entry is not None),
                )
                if present
            )
            fusion_rows.append(
                {
                    "path": path,
                    "rrf_score": (
                        (1.0 / (rrf_k + sparse_entry[0]) if sparse_entry else 0.0)
                        + (1.0 / (rrf_k + dense_entry[0]) if dense_entry else 0.0)
                    ),
                    "sparse_rank": sparse_entry[0] if sparse_entry else None,
                    "dense_rank": dense_entry[0] if dense_entry else None,
                    "dense_score": dense_entry[1] if dense_entry else None,
                    "supporting_channels": channels,
                }
            )
        fusion_rows.sort(
            key=lambda item: (
                -float(item["rrf_score"]),
                _path_penalty(str(item["path"])),
                str(item["path"]).lower(),
                str(item["path"]),
            )
        )
        inspection_items: list[ContextEvidenceItem] = []
        for fused in fusion_rows:
            path = str(fused["path"])
            sparse_entry = sparse_by_path.get(path)
            dense_entry = dense_by_path.get(path)
            if sparse_entry is not None:
                item = _inspection_item(
                    sparse_entry[1],
                    request,
                    decision_reason=(
                        "hybrid_rrf_inspection"
                        if dense_entry is not None
                        else "hybrid_retrieval_inspection"
                    ),
                )
            else:
                document = documents_by_path_for_dense[path]
                item = _dense_inspection_item(
                    document,
                    request,
                    score=float(dense_entry[1]) if dense_entry is not None else 0.0,
                )
            if item.evidence_sha256 in exposed:
                continue
            inspection_items.append(item)
            if len(inspection_items) >= 3:
                break
        inspection = tuple(inspection_items)
        anchors = (*primary, *inspection)
        anchor_paths = frozenset(item.path for item in anchors)
        anchor_symbols = frozenset(item.symbol for item in anchors if item.symbol)
        anchor_identities = frozenset(
            (item.path, item.symbol) for item in anchors if item.symbol
        )
        file_anchors = frozenset(item.path for item in anchors if not item.symbol)

        def related_to_anchor(link: StructuralLink) -> bool:
            return bool(
                (link.source_path, str(link.source_symbol or "")) in anchor_identities
                or (link.target_path, str(link.target_symbol or "")) in anchor_identities
                or link.source_path in file_anchors
                or link.target_path in file_anchors
            )

        relevant_links = tuple(
            link for link in repository.structural_links if related_to_anchor(link)
        )
        unsafe_links = tuple(link for link in relevant_links if not _safe_link(link))
        distinct_links: dict[tuple[str, str, str, str, str], StructuralLink] = {}
        for link in sorted(
            (item for item in relevant_links if _safe_link(item)),
            key=lambda item: (
                str(item.relation or "").upper().endswith("_TRANSITIVE"),
                item.source_path,
                str(item.source_symbol or ""),
                item.target_path,
                str(item.target_symbol or ""),
                str(item.relation or "").upper(),
            ),
        ):
            relation = str(link.relation or "").upper().removesuffix("_TRANSITIVE")
            key = (
                link.source_path,
                str(link.source_symbol or ""),
                link.target_path,
                str(link.target_symbol or ""),
                relation,
            )
            distinct_links.setdefault(key, link)
        certified_relevant_links = tuple(distinct_links.values())
        safe_links = certified_relevant_links[:6]
        link_items = tuple(
            item
            for item in (_link_item(link, request) for link in safe_links)
            if item.evidence_sha256 not in exposed
        )

        documents_by_identity = {
            (document.path, str(document.symbol or "")): document
            for document in repository.documents
        }
        documents_by_path: dict[str, Any] = {}
        for document in repository.documents:
            documents_by_path.setdefault(document.path, document)
        supporting: list[ContextEvidenceItem] = []
        for link in safe_links:
            for path, symbol in (
                (link.source_path, str(link.source_symbol or "")),
                (link.target_path, str(link.target_symbol or "")),
            ):
                if path in anchor_paths or any(item.path == path for item in supporting):
                    continue
                document = documents_by_identity.get(
                    (path, symbol)
                ) or documents_by_path.get(path)
                if document is None:
                    continue
                supporting.append(
                    ContextEvidenceItem(
                        kind="supporting_file",
                        path=path,
                        start_line=max(1, int(document.start_line or 1)),
                        end_line=max(1, int(document.end_line or document.start_line or 1)),
                        symbol=symbol or str(document.symbol or ""),
                        relation=str(link.relation or "").upper(),
                        confidence=float(link.confidence),
                        verification_status="verified",
                        source_revision=request.source_revision,
                        graph_revision=request.graph_revision,
                        evidence_sha256=_sha("support", path, symbol, request.source_revision),
                        decision_reason="certified_relationship_endpoint",
                        completeness="exact_identity",
                        source_excerpt=str(document.text or "").strip()[:400],
                    )
                )
                if len(supporting) >= 5:
                    break
            if len(supporting) >= 5:
                break

        definitions = tuple(
            {
                "path": item.path,
                "line": item.start_line,
                "symbol": item.symbol,
                "signature": item.source_excerpt.splitlines()[0]
                if item.source_excerpt
                else "",
                "origin": "program",
                "resolution_outcome": "exact",
                "provenance": ("hybrid_exact_identity", "checkout_source"),
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            }
            for item in (*anchors, *supporting)
            if item.symbol
        )
        evidence = RepositoryEvidence(
            available=bool(definitions or safe_links),
            graph_revision=request.graph_revision,
            definitions=definitions,
            status="source_backed",
            source_revision=request.source_revision,
            index_current=True,
            intelligence_valid=True,
            substrate_ready=True,
            substrate_status="healthy_current",
            retrieval_disposition="matched" if definitions or safe_links else "empty",
        )
        hints = tuple(
            RetrievalRankHint(
                path=row.path,
                fused_score=float(row.fused_score),
                supporting_channels=tuple(channel.value for channel, _ in row.channel_ranks),
            )
            for row in ranked[:20]
        )
        semantic_paths = frozenset(
            item.path for item in (*anchors, *supporting) if item.path
        )
        snapshot = RepositorySnapshot(
            source_revision=request.source_revision,
            graph_revision=request.graph_revision,
            repository_evidence=evidence,
            structural_links=safe_links,
            diagnostics=request.diagnostics,
            path_origins=tuple(
                (document.path, document.origin.value) for document in repository.documents
            ),
            retrieval_rank_hints=hints,
            documents=tuple(
                document
                for document in repository.documents
                if document.path in semantic_paths
            ),
        )
        projection = RepositoryContextEngine(
            max_tokens=max(1, min(320, int(request.token_budget)))
        ).project(
            DecisionOpportunity(
                kind="task_start" if not request.active_paths else "post_read_search",
                evidence_action=0,
                eligible_call=1,
                source_revision=request.source_revision,
                graph_revision=request.graph_revision,
                anchors=tuple(anchor_paths),
                changed_paths=request.changed_paths,
                changed_symbols=tuple(anchor_symbols),
                task_text=request.task,
            ),
            snapshot,
            delivered_claim_ids=frozenset(request.previously_exposed_claims),
        )
        execution_paths = tuple(view.rendered for view in projection.execution_views[:2])
        change_surface = tuple(fact.rendered for fact in projection.impact_facts[:8])
        affected_tests = tuple(
            dict.fromkeys(
                path
                for fact in projection.impact_facts
                for path in (fact.source.path, fact.target.path)
                if _is_test(path)
            )
        )[:5]
        validation_plan = tuple(
            fact.rendered for fact in projection.validation_facts[:5]
        )
        semantic_projection = projection.semantic_graph
        semantic_items = tuple(
            ContextEvidenceItem(
                kind="semantic_fact",
                path=fact.path,
                start_line=fact.start_line,
                end_line=fact.end_line,
                symbol=fact.scope or fact.subject,
                relation=fact.relation,
                confidence=1.0,
                verification_status="verified",
                source_revision=request.source_revision,
                graph_revision=request.graph_revision,
                evidence_sha256=fact.claim_id,
                decision_reason=f"deterministic_{fact.kind.value}",
                completeness="bounded_semantic_fact",
                source_path=fact.path,
                source_symbol=fact.scope,
                source_excerpt=fact.evidence,
            )
            for fact in (semantic_projection.facts if semantic_projection else ())
        )
        evidence_items = tuple(
            {
                item.evidence_sha256: item
                for item in (*primary, *inspection, *link_items, *semantic_items)
            }.values()
        )
        uncertainty_reasons = [*repository.reason_codes]
        if concrete_identifiers and not primary:
            if inspection:
                uncertainty_reasons.append("inspection_candidate_not_edit_target")
            else:
                uncertainty_reasons.append("concrete_task_anchor_unmatched")
        uncertainty_reasons.extend(
            reason
            for reason in retrieval.reason_codes
            if reason not in {"selected_bounded_context", "already_visible_or_delivered"}
        )
        uncertainty_reasons.extend(
            _link_rejection_reason(link) for link in unsafe_links
        )
        if len(certified_relevant_links) > len(safe_links):
            uncertainty_reasons.append("certified_edge_delivery_limit")
        if projection.status is RepositoryContextStatus.ABSTAIN:
            uncertainty_reasons.extend(projection.reason_codes)
        status = ContextStatus.READY if evidence_items else ContextStatus.ABSTAIN
        selected_tokens = sum(
            max(1, len(item.source_excerpt.split())) for item in evidence_items
        ) + int(projection.token_count)
        return GTContextPacket(
            status=status,
            repository_identity=identity,
            task_anchors=primary,
            primary_edit_targets=primary,
            inspection_candidates=inspection,
            supporting_files=tuple(supporting),
            symbol_contracts=primary,
            semantic_facts=tuple(
                fact.rendered
                for fact in (semantic_projection.facts if semantic_projection else ())
            ),
            semantic_graph_receipt=(
                semantic_projection.receipt.as_dict() if semantic_projection else {}
            ),
            execution_paths=execution_paths,
            change_surface=change_surface,
            affected_tests=affected_tests,
            validation_plan=validation_plan,
            uncertainties=tuple(dict.fromkeys(uncertainty_reasons)),
            evidence_items=evidence_items,
            coverage={
                "documents_considered": len(repository.documents),
                "ranked_files": len(retrieval.ranked_files),
                "certified_edges_considered": len(certified_relevant_links),
                "certified_edges_selected": len(safe_links),
                "certified_edge_limit": 6,
                "rejected_edges": len(unsafe_links),
                "retrieval_channels": {
                    receipt.channel.value: {
                        "candidate_count": receipt.candidate_count,
                        "available": receipt.available,
                        "failed": receipt.failed,
                        "reason": receipt.reason,
                    }
                    for receipt in retrieval.channel_receipts
                },
                "query_terms": list(retrieval_query_terms(state)),
                "retrieval_mode": request.retrieval_mode,
                "dense_index": dict(request.dense_index_receipt),
                "dense_candidates": len(request.dense_candidates),
                "dense_sparse_fusion": {
                    "method": "reciprocal_rank_fusion",
                    "k": rrf_k,
                    "candidate_count": len(fusion_rows),
                    "ranked_paths": [
                        {
                            "path": item["path"],
                            "rrf_score": item["rrf_score"],
                            "sparse_rank": item["sparse_rank"],
                            "dense_rank": item["dense_rank"],
                            "supporting_channels": list(item["supporting_channels"]),
                        }
                        for item in fusion_rows[:20]
                    ],
                },
            },
            selected_token_count=selected_tokens,
            retrieval_channel_count=len(retrieval.channel_receipts),
            truncated=bool(
                projection.truncated_count
                or len(certified_relevant_links) > len(safe_links)
                or retrieval.reason_codes
                and any("budget" in reason for reason in retrieval.reason_codes)
            ),
        )


__all__ = [
    "ContextCompileRequest",
    "ContextEvidenceItem",
    "ContextStatus",
    "GTContextPacket",
    "RepositoryContextCompiler",
]
