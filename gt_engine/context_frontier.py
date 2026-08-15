"""Bounded deterministic repository context that advances beyond Mini-SWE history."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

from gt_engine.repository_intelligence import (
    RepositoryEvidence,
    RepositoryIntelligenceStatus,
)
from gt_engine.uplift_policy import (
    CertifiedOpportunity,
    EvidenceAuthority,
    OpportunityKind,
    certify_opportunity,
)


class ContextFrontierKind(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    DEFINITION = "definition"
    SIGNATURE = "signature"
    CALLER = "caller"
    REFERENCE = "reference"
    TEST = "test"
    COUPLED_FILE = "coupled_file"
    PRECEDENT = "precedent"
    VALIDATION = "validation"


class FrontierDisposition(StrEnum):
    SELECTED_FRONTIER = "selected_frontier"
    REPRESENTED_MESSAGE = "represented_message"
    SUBSTRATE_FAILURE = "substrate_failure"
    STALE_SOURCE_REVISION = "stale_source_revision"
    LOW_PRECISION = "low_precision"
    INVALID_RELEVANCE = "invalid_relevance"
    FRONTIER_BUDGET = "frontier_budget"
    NO_DECISION_ANCHOR = "no_decision_anchor"
    LOW_MARGINAL = "low_marginal"
    NO_FRONTIER = "no_frontier"
    CONTROLLER_ONLY = "controller_only"
    EXPIRED_WINDOW = "expired_window"
    NOT_YET_ELIGIBLE = "not_yet_eligible"


class FactOrigin(StrEnum):
    TASK_START = "task_start"
    MODEL_AUTHORED = "model_authored"
    OBSERVED_EXTERNAL = "observed_external"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryFactProvenance:
    origin: FactOrigin
    origin_action: int
    evidence_action: int
    eligible_call: int
    source_path: str
    source_content_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["origin"] = self.origin.value
        return row


@dataclass(slots=True)
class RepositoryFactTracker:
    """Bind each structural claim to one origin and one provider window."""

    task_start_source_paths: frozenset[str] = frozenset()
    task_start_claim_ids: set[str] = field(default_factory=set)
    model_authored_paths: dict[str, int] = field(default_factory=dict)
    claim_provenance: dict[str, RepositoryFactProvenance] = field(default_factory=dict)

    @staticmethod
    def _path(path: str) -> str:
        normalized = str(path or "").replace("\\", "/")
        if normalized.startswith("/app/"):
            normalized = normalized[5:]
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def __post_init__(self) -> None:
        self.task_start_source_paths = frozenset(
            self._path(path) for path in self.task_start_source_paths
        )

    def record_model_authored_paths(
        self, paths: Sequence[str], *, action_id: int
    ) -> None:
        for path in paths:
            normalized = self._path(path)
            if normalized:
                self.model_authored_paths.setdefault(normalized, max(0, int(action_id)))

    def provenance_for(
        self,
        fact: ContextFrontierFact,
        *,
        evidence_action: int,
        eligible_call: int,
    ) -> RepositoryFactProvenance:
        existing = self.claim_provenance.get(fact.claim_id)
        if existing is not None:
            return existing
        path = self._path(fact.path)
        if evidence_action == 0 and path in self.task_start_source_paths:
            origin = FactOrigin.TASK_START
            origin_action = 0
            self.task_start_claim_ids.add(fact.claim_id)
        elif path in self.model_authored_paths:
            origin = FactOrigin.MODEL_AUTHORED
            origin_action = self.model_authored_paths[path]
        elif evidence_action > 0:
            origin = FactOrigin.OBSERVED_EXTERNAL
            origin_action = evidence_action
        else:
            origin = FactOrigin.UNKNOWN
            origin_action = 0
        provenance = RepositoryFactProvenance(
            origin=origin,
            origin_action=origin_action,
            evidence_action=max(0, int(evidence_action)),
            eligible_call=max(1, int(eligible_call)),
            source_path=path,
        )
        self.claim_provenance[fact.claim_id] = provenance
        return provenance


@dataclass(frozen=True, slots=True)
class ContextFrontierFact:
    fact_id: str
    claim_id: str
    kind: ContextFrontierKind
    path: str
    language: str = ""
    line: int = 0
    symbol: str = ""
    value: str = ""
    relation: str = ""
    source_revision: str = ""
    graph_revision: str = ""
    semantic_certainty: float = 0.0
    retrieval_relevance: float = 0.0
    provenance: RepositoryFactProvenance | None = None

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        row["provenance"] = self.provenance.as_dict() if self.provenance else None
        return row


@dataclass(frozen=True, slots=True)
class FrontierDecision:
    disposition: FrontierDisposition
    facts: tuple[ContextFrontierFact, ...] = ()
    rendered: str = ""
    reason_codes: tuple[str, ...] = ()
    candidate_count: int = 0
    accounted_count: int = 0
    accounting: tuple[dict[str, Any], ...] = ()
    opportunity: CertifiedOpportunity | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "facts": [fact.as_dict() for fact in self.facts],
            "rendered": self.rendered,
            "reason_codes": list(self.reason_codes),
            "candidate_count": self.candidate_count,
            "accounted_count": self.accounted_count,
            "accounting": [dict(row) for row in self.accounting],
            "opportunity": self.opportunity.as_dict() if self.opportunity else None,
        }


def _mapping(evidence: RepositoryEvidence | Mapping[str, Any]) -> Mapping[str, Any]:
    return evidence.as_dict() if isinstance(evidence, RepositoryEvidence) else evidence


def _module_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    if normalized.startswith("/app/"):
        normalized = normalized[5:]
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _provider_text(messages: Sequence[Mapping[str, Any]]) -> str:
    pieces: list[str] = []
    for message in messages:
        pieces.append(str(message.get("content") or ""))
        for action in (message.get("extra") or {}).get("actions") or ():
            pieces.append(str(action.get("command") or action.get("cmd") or ""))
    return "\n".join(pieces).replace("\\", "/")


_READ_OPERATION_RE = re.compile(
    r"(?i)^\s*(?:cat|sed|head|tail|less|more|view|vi|vim|nano|bat|awk)"
    r"(?:\s+.*)?$"
)


def _already_read_paths(
    messages: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    """Return workspace paths already exposed by a model READ/SEARCH observation.

    A successful read renders the file's content into the retained provider
    view.  Re-delivering a definition or symbol from that exact path adds no
    marginal value; the model already possesses the source bytes.  Only literal
    single-file read invocations count; compound/opaque commands and tool
    metadata never fabricate a read.
    """
    read_paths: set[str] = set()
    for message in messages:
        extra = message.get("extra") or {}
        for action in extra.get("actions") or ():
            command = str(action.get("command") or action.get("cmd") or "").strip()
            if not command or not _READ_OPERATION_RE.match(command):
                continue
            parts = command.split()
            for part in parts[1:]:
                cleaned = part.strip("'\"`")
                if cleaned in {"-n", "-e", "-r", "-l", "-f", "-q"}:
                    continue
                if (
                    cleaned.startswith("-")
                    or "=" in cleaned
                    or "$(" in cleaned
                    or "," in cleaned
                    or cleaned.isdigit()
                ):
                    continue
                normalized = _module_path(cleaned)
                if normalized and not normalized.startswith("../"):
                    read_paths.add(normalized)
    return frozenset(read_paths)


def _digest(*values: object) -> str:
    return hashlib.sha256("\0".join(map(str, values)).encode("utf-8", "replace")).hexdigest()[:20]


def _claim_id(
    kind: ContextFrontierKind,
    path: str,
    line: int,
    symbol: str,
    value: str,
    relation: str,
    language: str,
) -> str:
    """Semantic identity independent of graph/source refresh versions."""

    # A source edit may move a stable definition without changing what GT
    # knows.  Location remains provenance, but line movement alone must not
    # reopen the provider delivery window.
    return _digest(kind.value, path, symbol, value, relation, language)


_STRUCTURAL_SYMBOL = re.compile(r"^[A-Za-z_~][A-Za-z0-9_.$:@?!+*/<>=~-]*$")


def _valid_structural_symbol(fact: ContextFrontierFact) -> bool:
    if fact.kind is ContextFrontierKind.FILE:
        return bool(fact.path)
    symbol = str(fact.symbol or "").strip()
    if not symbol:
        return fact.kind not in {
            ContextFrontierKind.SYMBOL,
            ContextFrontierKind.DEFINITION,
            ContextFrontierKind.SIGNATURE,
            ContextFrontierKind.CALLER,
            ContextFrontierKind.REFERENCE,
            ContextFrontierKind.TEST,
        }
    return bool(_STRUCTURAL_SYMBOL.fullmatch(symbol))


def _exact_anchor(text: str, value: str) -> bool:
    anchor = str(value or "").strip()
    if not anchor:
        return False
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(anchor)}(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _has_decision_anchor(
    fact: ContextFrontierFact,
    messages: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether Mini-SWE has exposed this exact path or symbol.

    FTS rank and broad task similarity are not decision anchors.  Exact task
    paths/symbols and concrete assistant/tool observations are; this keeps
    repository intelligence action-conditioned without predicting intent.
    """

    text = _provider_text(messages)
    normalized = text.replace("\\", "/")
    path_anchored = bool(
        fact.path and fact.path.replace("\\", "/").lower() in normalized.lower()
    )
    symbol_anchored = _exact_anchor(text, fact.symbol)
    relation_target_anchored = _exact_anchor(text, fact.value) and fact.kind in {
        ContextFrontierKind.CALLER,
        ContextFrontierKind.REFERENCE,
        ContextFrontierKind.TEST,
    }
    if fact.kind is ContextFrontierKind.FILE:
        return path_anchored
    # A file path identifies a repository location, not every definition in
    # that file.  Structural roles require the exact symbol or relationship
    # target to have entered Mini-SWE's decision context.
    return symbol_anchored or relation_target_anchored


def _frontier_fact(
    *,
    kind: ContextFrontierKind,
    path: str,
    line: int,
    symbol: str,
    value: str,
    relation: str,
    language: str,
    source_revision: str,
    graph_revision: str,
    semantic_certainty: float,
    retrieval_relevance: float,
) -> ContextFrontierFact:
    claim_id = _claim_id(kind, path, line, symbol, value, relation, language)
    return ContextFrontierFact(
        fact_id=_digest(claim_id, source_revision, graph_revision),
        claim_id=claim_id,
        kind=kind,
        path=path,
        language=language,
        line=line,
        symbol=symbol,
        value=value,
        relation=relation,
        source_revision=source_revision,
        graph_revision=graph_revision,
        semantic_certainty=semantic_certainty,
        retrieval_relevance=retrieval_relevance,
    )


def _definition_candidates(
    evidence: Mapping[str, Any], source_revision: str
) -> list[ContextFrontierFact]:
    graph_revision = str(evidence.get("graph_revision") or "")
    anchors = {
        (str(item.get("path") or ""), str(item.get("symbol") or "")): item
        for item in evidence.get("anchors") or ()
        if isinstance(item, Mapping)
    }
    candidates: list[ContextFrontierFact] = []
    for item in evidence.get("definitions") or ():
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        symbol = str(item.get("symbol") or "")
        line = int(item.get("line") or 0)
        signature = str(item.get("signature") or "")
        anchor = anchors.get((path, symbol), {})
        certainty = float(item.get("semantic_certainty") or (1.0 if line > 0 else 0.0))
        # Relevance is a task-conditioned retrieval score.  Extractor
        # confidence is a separate semantic-certainty axis and is never a
        # fallback for relevance.
        relevance = float(
            item.get("retrieval_relevance")
            if item.get("retrieval_relevance") is not None
            else anchor.get("retrieval_relevance") or 0.0
        )
        candidates.append(
            _frontier_fact(
                kind=ContextFrontierKind.DEFINITION,
                path=path,
                line=line,
                symbol=symbol,
                value=signature,
                relation="defines",
                language=str(item.get("language") or ""),
                source_revision=source_revision,
                graph_revision=graph_revision,
                semantic_certainty=certainty,
                retrieval_relevance=relevance,
            )
        )
    return candidates


def _caller_candidates(
    evidence: Mapping[str, Any], source_revision: str
) -> list[ContextFrontierFact]:
    graph_revision = str(evidence.get("graph_revision") or "")
    candidates: list[ContextFrontierFact] = []
    for item in evidence.get("callers") or ():
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("caller_path") or "").replace("\\", "/")
        symbol = str(item.get("caller") or item.get("caller_symbol") or "")
        line = int(item.get("caller_line") or 0)
        target = str(item.get("target") or item.get("target_symbol") or "")
        certainty = float(item.get("confidence") or item.get("semantic_certainty") or 0.0)
        if str(item.get("semantics") or "") == "graph_recorded" and not certainty:
            certainty = 1.0
        relevance = float(item.get("retrieval_relevance") or 0.0)
        candidates.append(
            _frontier_fact(
                kind=ContextFrontierKind.CALLER,
                path=path,
                line=line,
                symbol=symbol,
                value=target,
                relation="calls",
                language=str(item.get("language") or ""),
                source_revision=source_revision,
                graph_revision=graph_revision,
                semantic_certainty=certainty,
                retrieval_relevance=relevance,
            )
        )
    return candidates


def _reference_candidates(
    evidence: Mapping[str, Any], source_revision: str
) -> list[ContextFrontierFact]:
    graph_revision = str(evidence.get("graph_revision") or "")
    candidates: list[ContextFrontierFact] = []
    for item in evidence.get("references") or ():
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        symbol = str(item.get("symbol") or "")
        line = int(item.get("line") or 0)
        certainty = float(item.get("semantic_certainty") or (1.0 if line > 0 else 0.0))
        kind = (
            ContextFrontierKind.TEST
            if bool(item.get("is_test")) or "test" in path.lower()
            else ContextFrontierKind.REFERENCE
        )
        candidates.append(
            _frontier_fact(
                kind=kind,
                path=path,
                line=line,
                symbol=symbol,
                value=str(item.get("target") or ""),
                relation="references",
                language=str(item.get("language") or ""),
                source_revision=source_revision,
                graph_revision=graph_revision,
                semantic_certainty=certainty,
                retrieval_relevance=float(item.get("retrieval_relevance") or 0.0),
            )
        )
    return candidates


def _anchor_candidates(
    evidence: Mapping[str, Any], source_revision: str
) -> list[ContextFrontierFact]:
    """Convert ranked task anchors into a bounded fallback frontier.

    Repository retrieval can prove a concrete path/line/symbol without
    producing a separate definition, reference, or caller role (common for
    COBOL and small source files).  Keeping those anchors private made GT
    appear silent even though the graph had usable evidence.  An anchor is
    eligible only when both semantic certainty and task relevance are explicit
    and high-confidence; it never invents a role or a caller relationship.
    """

    graph_revision = str(evidence.get("graph_revision") or "")
    candidates: list[ContextFrontierFact] = []
    for item in evidence.get("anchors") or ():
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        line = int(item.get("line") or 0)
        symbol = str(item.get("symbol") or "")
        if not path or line <= 0:
            continue
        certainty = float(item.get("semantic_certainty") or item.get("confidence") or 0.0)
        relevance = float(item.get("retrieval_relevance") or 0.0)
        candidates.append(
            _frontier_fact(
                kind=(ContextFrontierKind.SYMBOL if symbol else ContextFrontierKind.FILE),
                path=path,
                line=line,
                symbol=symbol,
                value=symbol,
                relation="task_anchor",
                language=str(item.get("language") or ""),
                source_revision=source_revision,
                graph_revision=graph_revision,
                semantic_certainty=certainty,
                retrieval_relevance=relevance,
            )
        )
    return candidates


def _represented(fact: ContextFrontierFact, text: str) -> bool:
    if fact.kind is ContextFrontierKind.DEFINITION:
        if fact.value:
            return fact.value in text
        anchors = [anchor for anchor in (fact.path, fact.symbol) if anchor]
        return bool(anchors and all(anchor in text for anchor in anchors))
    if fact.kind in {ContextFrontierKind.FILE, ContextFrontierKind.SYMBOL}:
        location = fact.path + (f":{fact.line}" if fact.line > 0 else "")
        return bool(location and location in text)
    if fact.kind is ContextFrontierKind.CALLER:
        relation = f"{fact.symbol} calls {fact.value}"
        return bool(fact.symbol and fact.value and relation in text)
    anchors = [anchor for anchor in (fact.path, fact.symbol, fact.value) if anchor]
    return bool(anchors and all(anchor in text for anchor in anchors[:2]))


def _render_fact(fact: ContextFrontierFact) -> str:
    location = fact.path + (f":{fact.line}" if fact.line > 0 else "")
    if fact.kind is ContextFrontierKind.DEFINITION:
        detail = fact.value or fact.symbol
        return f"- Definition {location}: {detail}"
    if fact.kind is ContextFrontierKind.CALLER:
        return f"- Caller {location}: {fact.symbol} calls {fact.value}"
    if fact.kind in {ContextFrontierKind.REFERENCE, ContextFrontierKind.TEST}:
        return f"- {fact.kind.value.title()} {location}: {fact.symbol}"
    return f"- {fact.kind.value.title()} {location}: {fact.value or fact.symbol}"


def compile_incremental_frontier(
    evidence: RepositoryEvidence | Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    *,
    source_revision: str,
    delivered_fact_ids: frozenset[str] = frozenset(),
    delivered_claim_ids: frozenset[str] = frozenset(),
    max_facts: int = 3,
    max_chars: int = 1_200,
    certainty_threshold: float = 0.95,
    relevance_threshold: float = 0.95,
    workspace_revision: str = "",
    current_call: int = 1,
    eligible_call: int = 1,
    evidence_action: int = 0,
    fact_tracker: RepositoryFactTracker | None = None,
) -> FrontierDecision:
    """Select the smallest certified repository frame absent from provider history."""

    row = _mapping(evidence)
    status = str(row.get("status") or "")
    substrate_failures = [
        reason
        for reason, healthy in (
            (
                status or "repository_intelligence_unavailable",
                status == RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
            ),
            (
                "repository_substrate_unavailable",
                bool(row.get("substrate_ready", row.get("available"))),
            ),
            ("repository_index_not_current", bool(row.get("index_current"))),
            ("repository_intelligence_not_valid", bool(row.get("intelligence_valid"))),
            ("repository_graph_revision_missing", bool(row.get("graph_revision"))),
        )
        if not healthy
    ]
    if substrate_failures:
        return FrontierDecision(
            FrontierDisposition.SUBSTRATE_FAILURE,
            reason_codes=tuple(dict.fromkeys(substrate_failures)),
        )
    evidence_revision = str(row.get("source_revision") or "")
    if evidence_revision and evidence_revision != source_revision:
        return FrontierDecision(
            FrontierDisposition.STALE_SOURCE_REVISION,
            reason_codes=("repository_source_revision_mismatch",),
        )

    candidates = [
        *_definition_candidates(row, source_revision),
        *_caller_candidates(row, source_revision),
        *_reference_candidates(row, source_revision),
        *_anchor_candidates(row, source_revision),
    ]
    # A ranked anchor often points at the same node as a structural role.  Keep
    # the richer role and use the anchor only as a fallback when no role exists.
    unique_candidates: list[ContextFrontierFact] = []
    # A semantic claim can have several graph occurrences (for example, two
    # call sites of the same symbol).  ``claim_id`` intentionally excludes
    # line number so that a line move does not reopen the delivery window.
    # Deduplicating only by (path, line, symbol) therefore allowed the same
    # claim to be emitted twice in one frame, violating the one-shot provider
    # contract.  Keep the first deterministic candidate, which preserves the
    # richer role ordering above and the earliest source location.
    seen_claim_ids: set[str] = set()
    seen_locations: set[tuple[str, int, str]] = set()
    for fact in candidates:
        location = (fact.path, fact.line, fact.symbol)
        if location in seen_locations or fact.claim_id in seen_claim_ids:
            continue
        seen_locations.add(location)
        seen_claim_ids.add(fact.claim_id)
        unique_candidates.append(fact)
    candidates = unique_candidates
    provider_text = _provider_text(messages)
    already_read_paths = _already_read_paths(messages)
    # A path-only task need may receive a file location, but it must not leak
    # the ranked symbol merely because the symbol happens to live in that
    # file.  Upgrade to SYMBOL only when the exact symbol is already part of
    # Mini-SWE's decision context.
    candidates = [
        (
            _frontier_fact(
                kind=ContextFrontierKind.FILE,
                path=fact.path,
                line=fact.line,
                symbol="",
                value="",
                relation="task_anchor",
                language=fact.language,
                source_revision=fact.source_revision,
                graph_revision=fact.graph_revision,
                semantic_certainty=fact.semantic_certainty,
                retrieval_relevance=fact.retrieval_relevance,
            )
            if fact.kind is ContextFrontierKind.SYMBOL
            and not _exact_anchor(provider_text, fact.symbol)
            and fact.path.replace("\\", "/").lower()
            in provider_text.replace("\\", "/").lower()
            else fact
        )
        for fact in candidates
    ]
    if fact_tracker is not None:
        candidates = [
            replace(
                fact,
                provenance=fact_tracker.provenance_for(
                    fact,
                    evidence_action=evidence_action,
                    eligible_call=eligible_call,
                ),
            )
            for fact in candidates
        ]
    selected: list[ContextFrontierFact] = []
    accounting: list[dict[str, Any]] = []
    # Revisions are controller identities, not useful model context.  Exposing
    # their hashes creates stochastic prompt differences between identical
    # workspaces restored with different filesystem timestamps.
    rendered_lines = ["Repository facts for the next decision:"]
    for fact in candidates:
        valid_relevance = 0.0 <= fact.retrieval_relevance <= 1.0
        valid_certainty = 0.0 <= fact.semantic_certainty <= 1.0
        provenance = fact.provenance
        if provenance is not None and provenance.origin is FactOrigin.MODEL_AUTHORED:
            disposition = FrontierDisposition.CONTROLLER_ONLY
        elif not valid_relevance:
            disposition = FrontierDisposition.INVALID_RELEVANCE
        elif not valid_certainty:
            disposition = FrontierDisposition.LOW_PRECISION
        elif not _valid_structural_symbol(fact):
            disposition = FrontierDisposition.LOW_PRECISION
        elif (
            fact.fact_id in delivered_fact_ids
            or fact.claim_id in delivered_claim_ids
            or _represented(fact, provider_text)
        ):
            disposition = FrontierDisposition.REPRESENTED_MESSAGE
        elif provenance is not None and current_call < provenance.eligible_call:
            disposition = FrontierDisposition.NOT_YET_ELIGIBLE
        elif provenance is not None and current_call > provenance.eligible_call:
            disposition = FrontierDisposition.EXPIRED_WINDOW
        elif (
            fact.semantic_certainty < certainty_threshold
            or fact.retrieval_relevance < relevance_threshold
            or fact.line <= 0
        ):
            disposition = FrontierDisposition.LOW_PRECISION
        elif not _has_decision_anchor(fact, messages):
            disposition = FrontierDisposition.NO_DECISION_ANCHOR
        elif (
            already_read_paths
            and fact.path
            and _module_path(fact.path) in already_read_paths
            and fact.kind
            in {
                ContextFrontierKind.FILE,
                ContextFrontierKind.SYMBOL,
                ContextFrontierKind.DEFINITION,
                ContextFrontierKind.SIGNATURE,
                ContextFrontierKind.CALLER,
                ContextFrontierKind.REFERENCE,
                ContextFrontierKind.TEST,
            }
        ):
            disposition = FrontierDisposition.LOW_MARGINAL
        else:
            line = _render_fact(fact)
            if (
                len(selected) >= max(1, max_facts)
                or len("\n".join((*rendered_lines, line))) > max_chars
            ):
                disposition = FrontierDisposition.FRONTIER_BUDGET
            else:
                selected.append(fact)
                rendered_lines.append(line)
                disposition = FrontierDisposition.SELECTED_FRONTIER
        accounting.append(
            {
                "fact_id": fact.fact_id,
                "claim_id": fact.claim_id,
                "kind": fact.kind.value,
                "path": fact.path,
                "language": fact.language,
                "line": fact.line,
                "symbol": fact.symbol,
                "source_revision": fact.source_revision,
                "graph_revision": fact.graph_revision,
                "semantic_certainty": fact.semantic_certainty,
                "retrieval_relevance": fact.retrieval_relevance,
                "origin": provenance.origin.value if provenance else "",
                "origin_action": provenance.origin_action if provenance else None,
                "evidence_action": provenance.evidence_action if provenance else evidence_action,
                "eligible_call": provenance.eligible_call if provenance else eligible_call,
                "disposition": disposition.value,
            }
        )
    opportunity: CertifiedOpportunity | None = None
    if selected:
        disposition = FrontierDisposition.SELECTED_FRONTIER
        rendered = "\n".join(rendered_lines)
        reasons = ("incremental_repository_frontier",)
        opportunity = certify_opportunity(
            kind=OpportunityKind.LOCALIZATION_CONTRACTION,
            authority=EvidenceAuthority.CERTIFIED_STRUCTURAL,
            source_revision=source_revision,
            current_source_revision=source_revision,
            workspace_revision=workspace_revision or source_revision,
            evidence_ids=tuple(fact.fact_id for fact in selected),
            concrete_anchors=tuple(
                f"{fact.path}:{fact.line}:{fact.symbol}" for fact in selected
            ),
            absent_from_provider_history=True,
            decision_relevant=True,
            eligible_call=current_call,
            current_call=current_call,
        )
        if not opportunity.certified:
            disposition = FrontierDisposition.LOW_PRECISION
            rendered = ""
            reasons = opportunity.reason_codes
    elif candidates and all(
        item["disposition"] == FrontierDisposition.REPRESENTED_MESSAGE.value for item in accounting
    ):
        disposition = FrontierDisposition.REPRESENTED_MESSAGE
        rendered = ""
        reasons = ("all_certified_facts_already_represented",)
    elif candidates and all(
        item["disposition"] == FrontierDisposition.CONTROLLER_ONLY.value
        for item in accounting
    ):
        disposition = FrontierDisposition.CONTROLLER_ONLY
        rendered = ""
        reasons = ("model_authored_claims_remain_controller_only",)
    elif candidates and all(
        item["disposition"] == FrontierDisposition.EXPIRED_WINDOW.value
        for item in accounting
    ):
        disposition = FrontierDisposition.EXPIRED_WINDOW
        rendered = ""
        reasons = ("repository_fact_delivery_window_expired",)
    elif candidates and all(
        item["disposition"] == FrontierDisposition.NOT_YET_ELIGIBLE.value
        for item in accounting
    ):
        disposition = FrontierDisposition.NOT_YET_ELIGIBLE
        rendered = ""
        reasons = ("repository_fact_not_yet_eligible",)
    elif (
        candidates
        and all(
            item["disposition"]
            in {
                FrontierDisposition.REPRESENTED_MESSAGE.value,
                FrontierDisposition.FRONTIER_BUDGET.value,
            }
            for item in accounting
        )
        and any(
            item["disposition"] == FrontierDisposition.FRONTIER_BUDGET.value for item in accounting
        )
    ):
        disposition = FrontierDisposition.FRONTIER_BUDGET
        rendered = ""
        reasons = ("certified_frontier_exceeds_current_budget",)
    elif candidates and all(
        item["disposition"] == FrontierDisposition.LOW_MARGINAL.value
        for item in accounting
    ):
        disposition = FrontierDisposition.LOW_MARGINAL
        rendered = ""
        reasons = ("all_certified_facts_are_same_path_already_read",)
    elif candidates:
        disposition = FrontierDisposition.LOW_PRECISION
        rendered = ""
        reasons = ("no_certified_incremental_fact",)
    else:
        disposition = FrontierDisposition.NO_FRONTIER
        rendered = ""
        reasons = ("repository_returned_no_structural_facts",)
    return FrontierDecision(
        disposition,
        tuple(selected),
        rendered,
        reasons,
        len(candidates),
        len(accounting),
        tuple(accounting),
        opportunity,
    )


__all__ = [
    "ContextFrontierFact",
    "ContextFrontierKind",
    "FactOrigin",
    "FrontierDecision",
    "FrontierDisposition",
    "RepositoryFactProvenance",
    "RepositoryFactTracker",
    "compile_incremental_frontier",
]
