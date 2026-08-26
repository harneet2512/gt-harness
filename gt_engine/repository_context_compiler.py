"""Deterministic, revision-bound repository context for coding agents.

The compiler turns the existing hybrid repository and certified graph edges
into a compact decision packet.  Retrieval scores may rank evidence, but only
exact repository identities and certified structural relationships may become
provider-visible facts.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import (
    EvidenceAuthority,
    EvidenceOrigin,
    HybridRetriever,
    RankedFile,
    RetrievalCandidate,
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
from gt_engine.task_contract import extract_task_contract, significant_tokens


class ContextStatus(StrEnum):
    READY = "READY"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


class LocalizationRole(StrEnum):
    """The decision a repository fact may support in the agent context."""

    EDIT = "EDIT"
    PUBLIC_SURFACE = "PUBLIC_SURFACE"
    INTEGRATION = "INTEGRATION"
    VALIDATION = "VALIDATION"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class TaskFacet:
    """One deterministic repository question implied by a task obligation."""

    facet_id: str
    obligation_ids: tuple[str, ...]
    role: LocalizationRole
    exact_symbols: tuple[str, ...] = ()
    unresolved_symbols: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    owning_symbols: tuple[str, ...] = ()
    owning_modules: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["role"] = self.role.value
        return row


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
    localization_role: str = LocalizationRole.EDIT.value
    facet_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GTContextPacket:
    status: ContextStatus
    repository_identity: dict[str, Any]
    task_facets: tuple[TaskFacet, ...] = ()
    task_anchors: tuple[ContextEvidenceItem, ...] = ()
    primary_edit_targets: tuple[ContextEvidenceItem, ...] = ()
    inspection_candidates: tuple[ContextEvidenceItem, ...] = ()
    inspection_public_surface: tuple[ContextEvidenceItem, ...] = ()
    inspection_integration: tuple[ContextEvidenceItem, ...] = ()
    proposed_new_files: tuple[str, ...] = ()
    uncovered_facets: tuple[str, ...] = ()
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
            "task_facets": [facet.as_dict() for facet in self.task_facets],
            "task_anchors": [item.as_dict() for item in self.task_anchors],
            "primary_edit_targets": [
                item.as_dict() for item in self.primary_edit_targets
            ],
            "inspection_candidates": [
                item.as_dict() for item in self.inspection_candidates
            ],
            "inspection_public_surface": [
                item.as_dict() for item in self.inspection_public_surface
            ],
            "inspection_integration": [
                item.as_dict() for item in self.inspection_integration
            ],
            "proposed_new_files": list(self.proposed_new_files),
            "uncovered_facets": list(self.uncovered_facets),
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
_CODE_ENTITY = re.compile(
    r"(?:`|'|\")([A-Za-z_][A-Za-z0-9_]*(?:(?:::|[.#])[A-Za-z_][A-Za-z0-9_]*)*)(?:`|'|\")"
)
_ASSOCIATED_GROUP = re.compile(
    r"(?:`|'|\")?([A-Za-z_][A-Za-z0-9_]*)::\{([^}]+)\}(?:`|'|\")?"
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
_UNQUALIFIED_ANALOG_PREFIX_STOPWORDS = _ISSUE_LANGUAGE_WORDS | frozenset(
    {
        "format",
        "get",
        "set",
        "agent",
        "task",
    }
)

# Task-prose nouns that describe code concepts generically.  A bare lowercase
# occurrence of one of these words names a concept, not a repository symbol,
# so it must stay retrieval vocabulary and never bind symbol identity.
_GENERIC_IDENTITY_NOUNS = frozenset(
    {
        "array",
        "arrays",
        "attribute",
        "boolean",
        "class",
        "config",
        "configuration",
        "constant",
        "content",
        "context",
        "count",
        "data",
        "depth",
        "entry",
        "error",
        "errors",
        "field",
        "function",
        "handler",
        "index",
        "input",
        "interface",
        "item",
        "items",
        "key",
        "keys",
        "kind",
        "length",
        "level",
        "method",
        "mode",
        "module",
        "name",
        "node",
        "nodes",
        "number",
        "object",
        "objects",
        "option",
        "options",
        "order",
        "output",
        "parser",
        "path",
        "paths",
        "property",
        "setting",
        "settings",
        "size",
        "state",
        "string",
        "symbol",
        "target",
        "test",
        "tests",
        "token",
        "type",
        "validator",
        "value",
        "values",
        "variable",
    }
)


def _is_generic_identity_noun(token: str) -> bool:
    """Return whether a bare lowercase token is prose vocabulary, not a name."""

    return bool(token) and token == token.lower() and token in _GENERIC_IDENTITY_NOUNS


def _is_short_acronym(token: str) -> bool:
    """Return whether a token is a short ALL-CAPS abbreviation such as CWE."""

    return bool(token) and len(token) <= 5 and token.isupper()


def _task_cites_path(task: str, path: str) -> bool:
    """Return whether the task text literally cites this file path or name.

    The full normalized path is checked as a substring (task literally cites
    ``cache/config.go``).  The bare filename is checked with word boundaries
    so an extensionless script ``config`` does not hijack edit authority from
    the prose word ``config`` in ``Fix config handling``.
    """

    normalized = _normalized_path(path).casefold()
    name = normalized.rsplit("/", 1)[-1]
    text = " ".join(str(task or "").split()).casefold()
    if normalized and normalized in text:
        return True
    if name and re.search(rf"\b{re.escape(name)}\b", text):
        return True
    return False


def _package_echo_symbol(task: str, path: str, symbol: str) -> bool:
    """Return whether an all-lowercase symbol merely echoes its own module
    filename token as unbackticked task prose (``remarkable-katex.js`` and a
    task that mentions katex).  Such package/plugin echoes are inspection
    evidence.  Backticked or qualified references express real identity and
    keep full authority."""

    raw_symbol = str(symbol or "").strip()
    if not raw_symbol or raw_symbol != raw_symbol.lower():
        return False
    stem = Path(str(path)).stem.strip().casefold()
    parts = frozenset(
        part for part in re.split(r"[_.-]+", stem) if len(part) >= 4
    )
    if not parts:
        return False
    plain_words = re.sub(r"`[^`]*`", " ", str(task or "")).casefold().split()
    return any(part in plain_words for part in parts)


def _segment_identity_eligible(segment: str) -> bool:
    """Return whether a task-text segment may bind repository symbols."""

    token = str(segment or "").strip()
    return bool(token) and not _is_generic_identity_noun(token)


# Entities introduced by a throw/raise verb name an exception the obligation
# must produce.  They are consumption vocabulary, never edit identity.
_THROW_CUE = re.compile(
    r"(?i)\b(?:throw|raise|raises|thrown)\s+(?:an?\s+|the\s+)?['\"`]?([A-Za-z_][\w.]*)"
)


def _exception_cue_tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for match in _THROW_CUE.finditer(str(text or "")):
        value = match.group(1)
        while value:
            tokens.add(value)
            tokens.add(value.casefold())
            if "." not in value:
                break
            value = value.rsplit(".", 1)[-1]
    return frozenset(tokens)
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


def _facet_role(text: str) -> LocalizationRole:
    lowered = str(text or "").lower()
    if re.search(
        r"\b(?:public api|public capabilities|public entry points|public surface|"
        r"crate surface|export|re-export|reexport)\b",
        lowered,
    ):
        return LocalizationRole.PUBLIC_SURFACE
    if re.search(r"\b(?:integrat|wire|lifecycle|execution path|data flow)\w*\b", lowered):
        return LocalizationRole.INTEGRATION
    # A test runner, test fixture, or failing test can itself be the product
    # code being edited.  Only an explicit verification action is validation
    # work; the mere noun "test" is not enough to change localization roles.
    if (
        re.search(r"\b(?:verify|validate|confirm)\b", lowered)
        or re.search(
            r"\b(?:add|create|write|update)\s+(?:an?\s+|the\s+)?"
            r"(?:(?:unit|integration|regression|acceptance)\s+)?tests?\b",
            lowered,
        )
        or re.search(
            r"\b(?:run|execute)\s+(?:the\s+)?(?:tests?|pytest|go test|cargo test)\b",
            lowered,
        )
        or re.search(r"\btests?\s+(?:pass|passes|passing|cover|covers)\b", lowered)
        or re.search(r"\bregression\s+(?:coverage|tests?)\b", lowered)
    ):
        return LocalizationRole.VALIDATION
    return LocalizationRole.EDIT


def _requires_implementation_edit(text: str) -> bool:
    """Return whether an inspection/public obligation also changes code."""

    return bool(
        re.search(
            r"(?i)\b(?:add|change|fix|implement|modify|remove|replace|wire|integrate)\b",
            str(text or ""),
        )
    )


def _code_shaped(value: str) -> bool:
    token = str(value or "").strip()
    return bool(
        token
        and (
            "_" in token
            or "::" in token
            or "." in token
            or (
                not token.isupper()
                and any(character.isupper() for character in token[1:])
            )
        )
    )


def _associated_entities(text: str) -> tuple[str, ...]:
    entities: list[str] = []
    for match in _ASSOCIATED_GROUP.finditer(text):
        owner = match.group(1)
        for raw_member in match.group(2).split(","):
            member = raw_member.strip().strip("`'\" ")
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", member):
                entities.append(f"{owner}::{member}")
    return tuple(dict.fromkeys(entities))


def _quoted_entity_is_literal(text: str, match: re.Match[str]) -> bool:
    """Reject quoted values that describe configuration rather than code.

    Backticks are formatting, not proof of symbol identity.  Simple lowercase
    values next to a value-bearing noun stay retrieval terms even if a
    same-named repository symbol happens to exist.  Qualified and code-shaped
    names remain eligible for exact repository resolution.
    """

    entity = match.group(1)
    if _code_shaped(entity):
        return False
    prefix = text[max(0, match.start() - 80) : match.start()].lower()
    suffix = text[match.end() : min(len(text), match.end() + 50)].lower()
    value_noun = (
        r"(?:mode|option|flag|setting|value|literal|header|key|format|strategy|"
        r"level|state|status|kind|type)"
    )
    return bool(
        re.search(value_noun + r"\s+(?:to\s+|as\s+|is\s+|=\s*)?$", prefix)
        or re.match(r"\s+" + value_noun + r"\b", suffix)
    )


def _symbol_bearing_segments(task: str) -> tuple[str, ...]:
    segments: list[str] = []
    for raw in re.split(r"\n\s*\n", str(task or "")):
        segment = " ".join(line.strip() for line in raw.splitlines() if line.strip())
        if not segment:
            continue
        explicit = tuple(_EXPLICIT_TOKEN.findall(segment))
        if (
            _CODE_ENTITY.search(segment)
            or _ASSOCIATED_GROUP.search(segment)
            or any(_code_shaped(token) for token in explicit)
        ):
            segments.append(segment[:2_000])
        if len(segments) >= 24:
            break
    return tuple(dict.fromkeys(segments))


def compile_task_facets(
    task: str,
    documents: tuple[Any, ...],
) -> tuple[TaskFacet, ...]:
    """Compile task obligations into exact, unresolved, and owner-backed facets.

    The result never claims that a requested new API already exists.  A
    qualified unresolved symbol may name an existing owner, and a suffixed new
    API may name an existing sibling; those are retained only as inspection
    owners/analogs.
    """

    contract = extract_task_contract(task)
    obligation_rows = tuple(
        (obligation.obligation_id, obligation.text)
        for obligation in contract.obligations
    )
    symbol_rows = tuple(
        (
            "symbols-" + hashlib.sha256(segment.encode()).hexdigest()[:12],
            segment,
        )
        for segment in _symbol_bearing_segments(task)
        # Task-contract obligations are sentence-level while symbol-bearing
        # segments are paragraph-level.  Comparing the whole paragraph to the
        # obligation corpus duplicated every code-bearing obligation as a
        # second facet.  Only retain a segment when no extracted obligation is
        # already contained in it.
        if not any(
            text.casefold() in segment.casefold()
            for _identifier, text in obligation_rows
        )
    )
    rows = obligation_rows + symbol_rows or (("task", str(task or "").strip()),)
    task_qualified_entities = (
        *_associated_entities(task),
        *(
            match.group(1)
            for match in _CODE_ENTITY.finditer(task)
            if "::" in match.group(1) or "." in match.group(1)
        ),
    )
    qualified_task_leaves = frozenset(
        re.split(r"(?:::|[.#])", entity)[-1].casefold()
        for entity in task_qualified_entities
    )
    available: dict[str, list[tuple[str, str]]] = {}
    for document in documents:
        symbol = str(getattr(document, "symbol", "") or "").strip()
        if not symbol:
            continue
        path = str(getattr(document, "path", "") or "")
        for key in {
            symbol.casefold(),
            re.split(r"(?:::|[.#])", symbol)[-1].casefold(),
        }:
            row = (symbol, path)
            if row not in available.setdefault(key, []):
                available[key].append(row)

    def resolve_entities(
        entity_values: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        def spelling_preferred(
            key: str,
            spelling: str,
            *,
            exact_case_only: bool = False,
        ) -> list[tuple[str, str]]:
            rows = available.get(key.casefold(), [])
            exact_case = [row for row in rows if row[0] == spelling]
            if exact_case_only:
                return exact_case
            return exact_case or rows

        exact: list[str] = []
        unresolved: list[str] = []
        owners: list[str] = []
        modules: list[str] = []
        for entity in entity_values:
            segments = re.split(r"(?:::|[.#])", entity)
            leaf = segments[-1]
            # If the task names a member through an owner anywhere (for
            # example EvaluationHandle::cancel), a later unqualified
            # clarification must not bind that leaf to a same-named symbol in
            # another subsystem.  The qualified owner is the stronger fact.
            qualified_shadow = (
                len(segments) == 1
                and leaf.casefold() in qualified_task_leaves
            )
            bindable_segments = [
                _segment_identity_eligible(segment) for segment in segments
            ]
            owner_exact_case = _is_short_acronym(segments[0])
            leaf_exact_case = _is_short_acronym(leaf)
            direct = (
                []
                if qualified_shadow
                or not all(bindable_segments)
                else spelling_preferred(
                    entity,
                    entity,
                    exact_case_only=any(
                        _is_short_acronym(segment) for segment in segments
                    ),
                )
            )
            owner_rows = (
                spelling_preferred(
                    segments[0],
                    segments[0],
                    exact_case_only=owner_exact_case,
                )
                if len(segments) > 1 and bindable_segments[0]
                else []
            )
            leaf_rows = (
                spelling_preferred(
                    leaf,
                    leaf,
                    exact_case_only=leaf_exact_case,
                )
                if bindable_segments[-1]
                else []
            )
            if direct:
                exact.extend(symbol for symbol, _path in direct)
                modules.extend(path for _symbol, path in direct if path)
                continue
            if (
                len(segments) == 1
                and leaf_rows
                and not qualified_shadow
            ):
                exact.extend(symbol for symbol, _path in leaf_rows)
                modules.extend(path for _symbol, path in leaf_rows if path)
                continue
            unresolved.append(entity)
            owner_paths = {path for _symbol, path in owner_rows if path}
            if owner_rows:
                owners.extend(symbol for symbol, _path in owner_rows)
                modules.extend(owner_paths)
                colocated_leaf_rows = [
                    row for row in leaf_rows if row[1] and row[1] in owner_paths
                ]
                exact.extend(symbol for symbol, _path in colocated_leaf_rows)
                modules.extend(path for _symbol, path in colocated_leaf_rows if path)
            # New APIs commonly extend an existing sibling name. Keep the
            # longest exact prefix as an analog, never as proof the new API exists.
            analogs = sorted(
                {
                    row
                    for key, values in available.items()
                    if len(key) >= 4 and leaf.casefold().startswith(key + "_")
                    for row in values
                    if leaf.startswith(row[0] + "_")
                    # An unqualified new API such as delete_snapshot or
                    # format_snapshot_task_list must not promote unrelated
                    # generic symbols named delete/format/task.  Qualified
                    # owner-scoped analogs remain valid because the existing
                    # owner independently constrains their subsystem.
                    if not (
                        len(segments) == 1
                        and (
                            row[0].casefold()
                            in _UNQUALIFIED_ANALOG_PREFIX_STOPWORDS
                            or leaf.casefold().endswith("_id")
                        )
                    )
                    if not owner_paths or row[1] in owner_paths
                }
                if len(segments) == 1 or owner_paths
                else set(),
                key=lambda row: (-len(row[0]), row[0].casefold(), row[1]),
            )
            if len(segments) == 1 and entity.casefold() in qualified_task_leaves:
                analogs = []
            if analogs:
                analog, analog_path = analogs[0]
                exact.append(analog)
                if analog_path:
                    modules.append(analog_path)
        return (
            tuple(dict.fromkeys(exact)),
            tuple(dict.fromkeys(unresolved)),
            tuple(dict.fromkeys(owners)),
            tuple(dict.fromkeys(modules)),
        )

    facets: list[TaskFacet] = []
    for obligation_id, text in rows:
        associated = _associated_entities(text)
        associated_members = {
            re.split(r"(?:::|[.#])", entity)[-1].casefold()
            for entity in associated
        }
        entities = list(associated)
        entities.extend(
            match.group(1)
            for match in _CODE_ENTITY.finditer(text)
            if not _quoted_entity_is_literal(text, match)
        )
        for match in _SYMBOL_CUE.finditer(text):
            candidate = match.group(1)
            if candidate.casefold() in available or _code_shaped(candidate):
                entities.append(candidate)
        for token in _EXPLICIT_TOKEN.findall(text):
            token = token.strip("`'\".,:;()[]{}")
            if (
                token
                and token.casefold() not in associated_members
                and "/" not in token
                and "\\" not in token
                and _code_shaped(token)
            ):
                entities.append(token)
        entity_values = tuple(dict.fromkeys(entities))
        exception_tokens = _exception_cue_tokens(text)
        exact, unresolved, owners, modules = resolve_entities(
            tuple(e for e in entity_values if e not in exception_tokens)
        )
        digest = hashlib.sha256(
            f"{obligation_id}\0{text}".encode()
        ).hexdigest()[:16]
        role = _facet_role(text)
        facet = TaskFacet(
            facet_id=f"facet-{digest}",
            obligation_ids=(obligation_id,),
            role=role,
            exact_symbols=exact,
            unresolved_symbols=unresolved,
            query_terms=significant_tokens(text)[:12],
            owning_symbols=owners,
            owning_modules=modules,
        )
        facets.append(facet)
        # Public-surface and integration work often has two independent
        # responsibilities: change an implementation owner and inspect/update
        # the boundary.  Preserve both instead of coercing the whole clause
        # into one candidate list.  Export-only inspection does not authorize
        # editing the underlying definition.
        if (
            role in {LocalizationRole.PUBLIC_SURFACE, LocalizationRole.INTEGRATION}
            and _requires_implementation_edit(text)
            and (exact or unresolved or owners)
        ):
            edit_digest = hashlib.sha256(
                f"{obligation_id}\0{text}\0edit".encode()
            ).hexdigest()[:16]
            facets.append(
                replace(
                    facet,
                    facet_id=f"facet-{edit_digest}",
                    role=LocalizationRole.EDIT,
                )
            )
        qualified_by_owner: dict[str, list[str]] = {}
        for entity in entity_values:
            segments = re.split(r"(?:::|[.#])", entity)
            if len(segments) > 1:
                qualified_by_owner.setdefault(segments[0], []).append(entity)
        if len(qualified_by_owner) > 1:
            for owner, owner_entities in list(qualified_by_owner.items())[:12]:
                child_exact, child_unresolved, child_owners, child_modules = (
                    resolve_entities(tuple(dict.fromkeys(owner_entities)))
                )
                child_digest = hashlib.sha256(
                    f"{obligation_id}\0{text}\0{owner}".encode()
                ).hexdigest()[:16]
                facets.append(
                    TaskFacet(
                        facet_id=f"facet-{child_digest}",
                        obligation_ids=(obligation_id,),
                        role=role,
                        exact_symbols=child_exact,
                        unresolved_symbols=child_unresolved,
                        query_terms=significant_tokens(
                            " ".join(owner_entities)
                        )[:12],
                        owning_symbols=child_owners,
                        owning_modules=child_modules,
                    )
                )
    return tuple(facets)


def _snake_case_type(value: str) -> str:
    token = re.split(r"(?:::|[.#])", str(value or ""))[0]
    token = re.sub(r"(?:Handle|Manager|Service)$", "", token) or token
    token = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", token)
    token = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", token)
    return token.replace("-", "_").casefold().strip("_")


def _proposed_rust_files(
    facets: tuple[TaskFacet, ...],
    documents: tuple[Any, ...],
) -> tuple[str, ...]:
    rust_paths = tuple(
        _normalized_path(str(getattr(document, "path", "") or ""))
        for document in documents
        if str(getattr(document, "path", "") or "").lower().endswith(".rs")
    )
    if not rust_paths:
        return ()
    existing = frozenset(rust_paths)

    def source_root(path: str) -> str:
        if "/src/" in path:
            return path.split("/src/", 1)[0] + "/src"
        if path.startswith("src/"):
            return "src"
        return path.rsplit("/", 1)[0] if "/" in path else ""

    root_counts = Counter(source_root(path) for path in rust_paths)
    default_root = min(root_counts, key=lambda value: (-root_counts[value], value))
    proposals: list[str] = []
    for facet in facets:
        facet_roots = Counter(
            source_root(path)
            for path in facet.owning_modules
            if path.lower().endswith(".rs")
        )
        parent = (
            min(facet_roots, key=lambda value: (-facet_roots[value], value))
            if facet_roots
            else default_root
        )
        existing_owners = {symbol.casefold() for symbol in facet.owning_symbols}
        for symbol in facet.unresolved_symbols:
            owner = re.split(r"(?:::|[.#])", symbol)[0]
            if (
                owner == symbol
                or owner.casefold() in existing_owners
                or not re.match(r"^[A-Z][A-Za-z0-9]+$", owner)
            ):
                continue
            stem = _snake_case_type(owner)
            if not stem:
                continue
            path = f"{parent}/{stem}.rs" if parent else f"{stem}.rs"
            if path not in existing and path not in proposals:
                proposals.append(path)
            if len(proposals) >= 2:
                return tuple(proposals)
    return tuple(proposals)


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
        identifiers.update(
            segment.lower()
            for segment in re.split(r"(?:::|[.#])", token)
            if segment and segment.lower() not in _ISSUE_LANGUAGE_WORDS
        )

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


def _exact_identity_candidate(ranked: RankedFile):
    candidate = _exact_candidate(ranked)
    if candidate is None:
        return None
    if candidate.authority is EvidenceAuthority.IDENTITY_ONLY or (
        "facet_exact_symbol" in set(candidate.provenance)
    ):
        return candidate
    return None


def _task_path_match_count(ranked: RankedFile) -> int:
    """Return the exact channel's deterministic task/path token overlap."""

    candidate = _exact_candidate(ranked)
    if candidate is None or candidate.authority is not EvidenceAuthority.RANKING_SUPPORT:
        return 0
    for item in candidate.provenance:
        if not item.startswith("exact_path_token_count:"):
            continue
        try:
            return max(0, int(item.rsplit(":", 1)[-1]))
        except ValueError:
            return 0
    return 0


def _strong_task_path_candidate(ranked: RankedFile) -> bool:
    """Return whether task prose matches at least two path-name tokens.

    Exact-channel path-token overlap is ranking evidence, not symbol/path
    identity.  Keeping the count lets the context compiler distinguish a
    strong natural artifact name such as multiAgentChat from a one-token match
    such as chat without falsely authorizing either as an edit target.
    """

    if _task_path_match_count(ranked) >= 2:
        return True
    candidate = _exact_candidate(ranked)
    return bool(
        candidate
        and any(
            len(token) >= 8 and token not in _ISSUE_LANGUAGE_WORDS
            for item in candidate.provenance
            if item.startswith("exact_path_token_value:")
            for token in (item.split(":", 1)[1].casefold(),)
        )
    )


def _rank_key(ranked: RankedFile, identifiers: dict[str, int]) -> tuple[Any, ...]:
    exact = _exact_candidate(ranked)
    symbol = str(exact.symbol if exact is not None else ranked.representative.symbol or "")
    exact_symbol = bool(symbol and symbol.lower() in identifiers)
    facet_seed = bool(
        exact is not None and "facet_exact_symbol" in set(exact.provenance)
    )
    exact_path = bool(
        exact is not None
        and "exact_path" in set(exact.provenance)
    )
    return (
        0 if exact_symbol else 1 if exact_path else 2,
        0 if facet_seed else 1,
        -float(ranked.fused_score) if facet_seed else 0.0,
        identifiers.get(symbol.lower(), len(identifiers)),
        _path_penalty(ranked.path),
        -float(ranked.fused_score),
        ranked.path.lower(),
        ranked.path,
    )


def _symbol_keys(value: str) -> frozenset[str]:
    token = str(value or "").strip()
    return frozenset(
        item.casefold()
        for item in (token, *re.split(r"(?:::|[.#])", token))
        if item
    )


def _matching_facet_ids(
    *,
    symbol: str,
    path: str,
    facets: tuple[TaskFacet, ...],
) -> tuple[str, ...]:
    matched, _has_unscoped = _matching_facet_scopes(
        symbol=symbol, path=path, facets=facets
    )
    return matched


def _matching_facet_scopes(
    *,
    symbol: str,
    path: str,
    facets: tuple[TaskFacet, ...],
) -> tuple[tuple[str, ...], bool]:
    """Return (matched facet ids, whether any match is globally unscoped).

    A facet whose qualified obligation pins an owning module scopes its
    symbol matches to that module; such matches survive cross-file name
    collisions.  A purely global name match does not.
    """

    keys = _symbol_keys(symbol)
    normalized_path = _normalized_path(path)
    matched: list[str] = []
    has_unscoped = False
    for facet in facets:
        symbol_match = bool(
            keys
            and any(
                keys & _symbol_keys(candidate)
                for candidate in (*facet.exact_symbols, *facet.owning_symbols)
            )
        )
        owner_scoped = bool(
            facet.owning_modules
            and any(
                "::" in candidate or "." in candidate
                for candidate in facet.unresolved_symbols
            )
        )
        if symbol_match and (
            not owner_scoped or normalized_path in facet.owning_modules
        ):
            matched.append(facet.facet_id)
            if not owner_scoped:
                has_unscoped = True
    return tuple(matched), has_unscoped


def _path_terms(path: str) -> frozenset[str]:
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", _normalized_path(path))
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
    return frozenset(
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", expanded)
        if len(token) >= 4 and token.casefold() not in _ISSUE_LANGUAGE_WORDS
    )


def _matching_path_facet_ids(
    *, path: str, facets: tuple[TaskFacet, ...]
) -> tuple[str, ...]:
    """Scope strong natural artifact names to obligations, never edit identity."""

    terms = _path_terms(path)
    if not terms:
        return ()
    return tuple(
        facet.facet_id
        for facet in facets
        if (
            len(terms & frozenset(facet.query_terms)) >= 2
            or any(
                len(token) >= 8
                for token in terms & frozenset(facet.query_terms)
            )
        )
    )


def _facet_seed_rows(
    documents: tuple[Any, ...],
    facets: tuple[TaskFacet, ...],
    request: ContextCompileRequest,
    *,
    limit: int = 64,
) -> tuple[RankedFile, ...]:
    """Keep exact owner-scoped facts outside the statistical rank window.

    The online repository is already a bounded, revision-checked graph
    projection.  Within it, an explicitly named owner or existing sibling API
    is stronger localization evidence than its BM25 position in a long issue.
    This seed does not accept unresolved leaves or lexical body similarity.
    """

    candidates: list[tuple[int, int, str, int, Any, tuple[str, ...]]] = []
    for document in documents:
        symbol = str(getattr(document, "symbol", "") or "").strip()
        path = _normalized_path(str(getattr(document, "path", "") or ""))
        if not symbol or not path:
            continue
        origin = getattr(document, "origin", EvidenceOrigin.PREEXISTING_REPOSITORY)
        if origin is not EvidenceOrigin.PREEXISTING_REPOSITORY:
            continue
        origin_revision = str(getattr(document, "origin_revision", "") or "")
        if origin_revision and origin_revision != request.source_revision:
            continue
        keys = _symbol_keys(symbol)
        matched: list[str] = []
        owner_affinity = 0
        normalized_parts = frozenset(
            part.casefold()
            for part in re.split(r"[/_.-]+", path)
            if part
        )
        for facet in facets:
            exact_keys = frozenset(
                key
                for value in (*facet.exact_symbols, *facet.owning_symbols)
                for key in _symbol_keys(value)
            )
            if not keys & exact_keys:
                continue
            if facet.owning_modules and path not in facet.owning_modules:
                continue
            matched.append(facet.facet_id)
            if any(
                owner.casefold() in normalized_parts
                for owner in facet.owning_symbols
            ):
                owner_affinity = 1
        if matched:
            candidates.append(
                (
                    len(set(matched)),
                    owner_affinity,
                    path,
                    max(1, int(getattr(document, "start_line", 1) or 1)),
                    document,
                    tuple(dict.fromkeys(matched)),
                )
            )

    # One representative per file prevents a large type from consuming the
    # seed bound. Prefer the symbol covering most task facets, then the path
    # whose component names agree with the qualified owner.
    by_path: dict[str, tuple[int, int, str, int, Any, tuple[str, ...]]] = {}
    for row in sorted(
        candidates,
        key=lambda item: (-item[0], -item[1], item[2].casefold(), item[3]),
    ):
        by_path.setdefault(row[2], row)
    selected = sorted(
        by_path.values(),
        key=lambda item: (-item[0], -item[1], item[2].casefold(), item[3]),
    )[: max(1, int(limit))]
    rows: list[RankedFile] = []
    for rank, (coverage, affinity, path, _line, document, _facets) in enumerate(
        selected, start=1
    ):
        candidate = RetrievalCandidate(
            path=path,
            start_line=getattr(document, "start_line", 1),
            end_line=getattr(document, "end_line", None),
            symbol=str(getattr(document, "symbol", "") or ""),
            text=str(getattr(document, "text", "") or ""),
            channel=RetrievalChannel.EXACT,
            channel_rank=rank,
            relation=None,
            provenance=("facet_exact_symbol", "exact_symbol"),
            source_revision=request.source_revision,
            channel_score=float(coverage + affinity),
            origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
            authority=EvidenceAuthority.IDENTITY_ONLY,
            origin_revision=str(getattr(document, "origin_revision", "") or ""),
        )
        rows.append(
            RankedFile(
                path=path,
                fused_score=float(coverage + affinity),
                channel_ranks=((RetrievalChannel.EXACT, rank),),
                representative=candidate,
                provenance=candidate.provenance,
                channel_candidates=((RetrievalChannel.EXACT, candidate),),
            )
        )
    return tuple(rows)


def _select_role_complete_rows(
    rows: tuple[RankedFile, ...],
    facets: tuple[TaskFacet, ...],
    *,
    limit: int,
) -> tuple[RankedFile, ...]:
    """Greedy bounded set cover over task facets with stable rank tie-breaks."""

    remaining = list(rows)
    selected: list[RankedFile] = []
    uncovered = {facet.facet_id for facet in facets}
    while remaining and len(selected) < limit:
        ranked_choices = []
        for position, row in enumerate(remaining):
            candidate = _exact_candidate(row) or row.representative
            covered = set(
                _matching_facet_ids(
                    symbol=str(candidate.symbol or ""),
                    path=candidate.path,
                    facets=facets,
                )
            )
            ranked_choices.append(
                (
                    -len(covered & uncovered),
                    _path_penalty(row.path),
                    position,
                    row.path.casefold(),
                    row,
                    covered,
                )
            )
        *_rank, row, covered = min(ranked_choices, key=lambda item: item[:-2])
        remaining.remove(row)
        selected.append(row)
        uncovered.difference_update(covered)
    return tuple(selected)


def _sha(*parts: object) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity_item(
    ranked: RankedFile,
    request: ContextCompileRequest,
    *,
    decision_reason: str,
    file_only: bool = False,
) -> ContextEvidenceItem:
    candidate = _exact_candidate(ranked) or ranked.representative
    start = 1 if file_only else max(1, int(candidate.start_line or 1))
    end = start if file_only else max(start, int(candidate.end_line or start))
    symbol = "" if file_only else str(candidate.symbol or "")
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
        source_excerpt="" if file_only else str(candidate.text or "").strip()[:600],
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

        task_facets = compile_task_facets(request.task, repository.documents)
        state = request.retrieval_state()
        retriever = HybridRetriever(
            repository.documents,
            structural_links=repository.structural_links,
            dense_backend=None,
            dense_fallback_only=True,
        )
        retrieval = retriever.retrieve(
            state,
            channel_limit=128,
            top_k=40,
            selection_limit=20,
            token_budget=max(1, min(1_000, int(request.token_budget))),
            character_budget=max(1, int(request.character_budget)),
        )
        identifiers = _explicit_identifiers(request)
        authoritative_symbols = _authoritative_symbol_identifiers(request)
        facet_exact_symbols = frozenset(
            symbol.casefold()
            for facet in task_facets
            for symbol in facet.exact_symbols
        )
        concrete_identifiers = _concrete_identifiers(request)
        seeded = _facet_seed_rows(
            repository.documents,
            task_facets,
            request,
        )
        ranked_by_path: dict[str, RankedFile] = {
            row.path: row for row in seeded
        }
        for row in retrieval.ranked_files:
            ranked_by_path.setdefault(row.path, row)
        ranked = tuple(
            sorted(
                ranked_by_path.values(),
                key=lambda row: _rank_key(row, identifiers),
            )
        )

        def _entry_file_symbol(path: str, symbol: str) -> bool:
            return _package_echo_symbol(request.task, path, symbol)

        exact_symbol_rows = tuple(
            row
            for row in ranked
            if (candidate := _exact_candidate(row)) is not None
            and bool(candidate.symbol)
            and str(candidate.symbol).casefold()
            in (authoritative_symbols | facet_exact_symbols)
            and bool(
                _matching_facet_ids(
                    symbol=str(candidate.symbol),
                    path=row.path,
                    facets=task_facets,
                )
            )
        )

        def _export_connected(first: str, second: str, symbol: str) -> bool:
            symbol_key = symbol.casefold()
            for link in repository.structural_links:
                if not _safe_link(link):
                    continue
                relation = str(link.relation or "").upper()
                if relation not in {"RE_EXPORTS", "EXPORTS"}:
                    continue
                if {link.source_path, link.target_path} != {first, second}:
                    continue
                if symbol_key in {
                    str(link.source_symbol or "").casefold(),
                    str(link.target_symbol or "").casefold(),
                }:
                    return True
            return False

        # The same unqualified symbol name in several mutually unrelated files
        # is ambiguous identity evidence.  Demote a name group to inspection
        # only when every member's facet match is globally unscoped AND no
        # certified export structure connects the files (a facade); owner-
        # module-scoped matches survive cross-file collisions by design.
        rows_by_symbol: dict[str, list[RankedFile]] = {}
        for row in exact_symbol_rows:
            symbol_key = str(_exact_candidate(row).symbol).casefold()
            rows_by_symbol.setdefault(symbol_key, []).append(row)
        ambiguous_symbols = set()
        for symbol_key, rows in rows_by_symbol.items():
            if len({row.path for row in rows}) <= 1:
                continue
            all_scoped = True
            for row in rows:
                candidate = _exact_candidate(row)
                _ids, has_unscoped = _matching_facet_scopes(
                    symbol=str(candidate.symbol),
                    path=row.path,
                    facets=task_facets,
                )
                if has_unscoped:
                    all_scoped = False
                    break
            if not all_scoped:
                continue
            connected = any(
                _export_connected(
                    first.path,
                    second.path,
                    str(_exact_candidate(first).symbol),
                )
                for index, first in enumerate(rows)
                for second in rows[index + 1 :]
            )
            if not connected:
                ambiguous_symbols.add(symbol_key)
        if ambiguous_symbols:
            exact_symbol_rows = tuple(
                row
                for row in exact_symbol_rows
                if str(_exact_candidate(row).symbol).casefold()
                not in ambiguous_symbols
            )
        exact_path_rows = tuple(
            row
            for row in ranked
            if (candidate := _exact_candidate(row)) is not None
            and "exact_path" in set(candidate.provenance)
        )

        # A barrel/module facade is a distinct inspection responsibility, not
        # an implementation edit target merely because it repeats the same
        # exported symbol name.  Only certified exact re-export evidence may
        # assign this role.
        public_surface_paths = frozenset(
            link.source_path
            for link in repository.structural_links
            if _safe_link(link)
            and str(link.relation or "").upper() == "RE_EXPORTS"
            and (
                str(link.target_symbol or "").lower() in authoritative_symbols
                or str(link.source_symbol or "").lower() in authoritative_symbols
            )
        )
        def matches_concrete_anchor(row: RankedFile) -> bool:
            if _strong_task_path_candidate(row):
                return True
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
            # The Exact retrieval channel also carries rank-only natural path
            # token matches.  Only identity-authoritative exact candidates are
            # excluded here; otherwise the strongest file-name match vanishes
            # from both the edit and inspection sets.
            if _exact_identity_candidate(row) is None
            and len(row.channel_ranks) >= 2
            and (
                not concrete_identifiers
                or matches_concrete_anchor(row)
            )
        )
        authoritative_rows = tuple(
            dict.fromkeys((*exact_symbol_rows, *exact_path_rows))
        )
        facets_by_id = {facet.facet_id: facet for facet in task_facets}

        def row_facet_ids(row: RankedFile) -> tuple[str, ...]:
            candidate = _exact_candidate(row) or row.representative
            return _matching_facet_ids(
                symbol=str(candidate.symbol or ""),
                path=row.path,
                facets=task_facets,
            )

        def row_roles(row: RankedFile) -> frozenset[LocalizationRole]:
            return frozenset(
                facets_by_id[facet_id].role
                for facet_id in row_facet_ids(row)
                if facet_id in facets_by_id
            )

        non_public_rows = tuple(
            row for row in authoritative_rows if row.path not in public_surface_paths
        )
        validation_rows = tuple(row for row in non_public_rows if _is_test(row.path))
        # Edit authority requires an obligation-backed facet match.  A bare
        # exact-path token match with no facet coverage is inspection evidence
        # only, unless the task itself cites the file path or file name; a
        # literally cited file is decisive user intent.  An entry-file symbol
        # (katex.js#katex) is package/barrel evidence and never edit authority.
        def _is_entry_symbol_row(row: RankedFile) -> bool:
            candidate = _exact_candidate(row)
            return candidate is not None and _entry_file_symbol(
                row.path, str(candidate.symbol)
            )

        edit_rows = tuple(
            row
            for row in non_public_rows
            if not _is_test(row.path)
            and not _is_entry_symbol_row(row)
            and (
                LocalizationRole.EDIT in row_roles(row)
                or (
                    row in exact_path_rows
                    and not row_facet_ids(row)
                    and _task_cites_path(request.task, row.path)
                )
            )
        )
        primary_rows = _select_role_complete_rows(
            edit_rows,
            task_facets,
            limit=3,
        )
        primary = tuple(
            replace(
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
                    file_only=row in exact_path_rows and row not in exact_symbol_rows,
                ),
                localization_role=LocalizationRole.EDIT.value,
                facet_ids=_matching_facet_ids(
                    symbol=str(
                        (_exact_candidate(row) or row.representative).symbol or ""
                    ),
                    path=row.path,
                    facets=task_facets,
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
                    "task_path_token_count": (
                        _task_path_match_count(sparse_entry[1])
                        if sparse_entry is not None
                        else 0
                    ),
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
                -int(item["task_path_token_count"]),
                -float(item["rrf_score"]),
                _path_penalty(str(item["path"])),
                str(item["path"]).lower(),
                str(item["path"]),
            )
        )
        inspection_items: list[ContextEvidenceItem] = []
        for row in non_public_rows:
            roles = row_roles(row)
            role = next(
                (
                    candidate
                    for candidate in (
                        LocalizationRole.PUBLIC_SURFACE,
                        LocalizationRole.INTEGRATION,
                        LocalizationRole.VALIDATION,
                    )
                    if candidate in roles
                ),
                None,
            )
            is_entry_symbol = _is_entry_symbol_row(row)
            if (role is None and not is_entry_symbol) or row in primary_rows:
                continue
            if role is not None:
                inspection_items.append(
                    replace(
                        _identity_item(
                            row,
                            request,
                            decision_reason="exact_task_identity_inspection_only",
                            file_only=row in exact_path_rows
                            and row not in exact_symbol_rows,
                        ),
                        localization_role=role.value,
                        facet_ids=row_facet_ids(row),
                    )
                )
            else:
                # Entry/barrel symbols keep their retrieval value as explicit
                # inspection evidence without ever becoming edit authority.
                inspection_items.append(_inspection_item(row, request))
        for fused in fusion_rows:
            path = str(fused["path"])
            sparse_entry = sparse_by_path.get(path)
            dense_entry = dense_by_path.get(path)
            if sparse_entry is not None:
                item = _inspection_item(
                    sparse_entry[1],
                    request,
                    decision_reason=(
                        "task_path_phrase_inspection"
                        if _strong_task_path_candidate(sparse_entry[1])
                        else "hybrid_rrf_inspection"
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
            inspection_items.append(
                replace(
                    item,
                    localization_role=LocalizationRole.UNCERTAIN.value,
                    facet_ids=(
                        _matching_facet_ids(
                            symbol=item.symbol,
                            path=item.path,
                            facets=task_facets,
                        )
                        or (
                            _matching_path_facet_ids(
                                path=item.path,
                                facets=task_facets,
                            )
                            if sparse_entry is not None
                            and _strong_task_path_candidate(sparse_entry[1])
                            else ()
                        )
                    ),
                )
            )
            if len(inspection_items) >= 3:
                break
        inspection = tuple(inspection_items[:3])
        anchors = (*primary, *inspection)
        task_scope_inspection = tuple(
            item
            for item in inspection
            if item.decision_reason == "task_path_phrase_inspection"
            and item.facet_ids
        )
        task_scope_inspection_paths = frozenset(
            item.path for item in task_scope_inspection
        )
        anchor_paths = frozenset(item.path for item in anchors)
        anchor_symbols = frozenset(item.symbol for item in anchors if item.symbol)
        anchor_identities = frozenset(
            (item.path, item.symbol) for item in anchors if item.symbol
        )
        file_anchors = frozenset(
            item.path
            for item in anchors
            if not item.symbol and item.decision_reason != "dense_semantic_inspection"
        )

        def related_to_anchor(link: StructuralLink) -> bool:
            return bool(
                (link.source_path, str(link.source_symbol or "")) in anchor_identities
                or (link.target_path, str(link.target_symbol or "")) in anchor_identities
                or link.source_path in file_anchors
                or link.target_path in file_anchors
                # A task/path phrase match scopes the file, not the arbitrary
                # representative symbol selected for its retrieval span.
                or link.source_path in task_scope_inspection_paths
                or link.target_path in task_scope_inspection_paths
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
        relation_priority = {
            "RE_EXPORTS": 0,
            "TESTED_BY": 1,
            "CALLS": 2,
            "IMPORTS": 3,
            "IMPLEMENTS": 4,
            "EXTENDS": 5,
        }
        certified_relevant_links = tuple(
            sorted(
                distinct_links.values(),
                key=lambda link: (
                    relation_priority.get(str(link.relation or "").upper(), 6),
                    _is_test(link.source_path) or _is_test(link.target_path),
                    "/examples/" in ("/" + link.source_path.lower())
                    or "/examples/" in ("/" + link.target_path.lower()),
                    _path_penalty(link.source_path) + _path_penalty(link.target_path),
                    link.source_path,
                    str(link.source_symbol or ""),
                    link.target_path,
                    str(link.target_symbol or ""),
                ),
            )
        )
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
                        localization_role=LocalizationRole.UNCERTAIN.value,
                        facet_ids=_matching_facet_ids(
                            symbol=symbol or str(document.symbol or ""),
                            path=path,
                            facets=task_facets,
                        ),
                    )
                )
                if len(supporting) >= 5:
                    break
            if len(supporting) >= 5:
                break

        role_candidates_by_path = {
            item.path: item for item in (*inspection, *supporting)
        }
        primary_paths = frozenset(item.path for item in primary)
        task_scope_paths = frozenset(
            item.path for item in (*primary, *task_scope_inspection)
        )
        anchor_facet_ids = tuple(
            dict.fromkeys(
                facet_id
                for item in (*primary, *task_scope_inspection)
                for facet_id in item.facet_ids
            )
        )
        public_surface = tuple(
            surfaced
            for link in safe_links
            if str(link.relation or "").upper() == "RE_EXPORTS"
            and link.source_path in role_candidates_by_path
            and link.source_path not in primary_paths
            if (
                surfaced := replace(
                    role_candidates_by_path[link.source_path],
                    kind="public_surface",
                    localization_role=LocalizationRole.PUBLIC_SURFACE.value,
                    decision_reason="certified_reexport_public_surface",
                    completeness="certified_public_surface_edge",
                    facet_ids=(
                        role_candidates_by_path[link.source_path].facet_ids
                        or anchor_facet_ids
                    ),
                )
            ).facet_ids
        )
        integration_paths: list[str] = []
        for link in safe_links:
            if str(link.relation or "").upper() not in {"CALLS", "IMPORTS"}:
                continue
            # A rank-only inspection anchor can have many valid repository
            # relationships that are unrelated to the task. Do not relabel
            # those neighbors as task integration surfaces or inherit the
            # primary facet merely because both were in the bounded retrieval
            # set. Integration authority requires a certified edge touching a
            # primary task identity.
            if (
                link.source_path not in task_scope_paths
                and link.target_path not in task_scope_paths
            ):
                continue
            for path in (link.source_path, link.target_path):
                if path not in task_scope_paths and path in role_candidates_by_path:
                    integration_paths.append(path)
        integration = tuple(
            integrated
            for path in dict.fromkeys(integration_paths)
            if (
                integrated := replace(
                    role_candidates_by_path[path],
                    kind="integration_surface",
                    localization_role=LocalizationRole.INTEGRATION.value,
                    decision_reason="certified_integration_relationship",
                    completeness="certified_integration_edge",
                    facet_ids=(
                        role_candidates_by_path[path].facet_ids or anchor_facet_ids
                    ),
                )
            ).facet_ids
        )
        role_paths = frozenset(
            item.path for item in (*public_surface, *integration)
        )
        generic_inspection = tuple(
            item for item in inspection if item.path not in role_paths
        )
        generic_supporting = tuple(
            item for item in supporting if item.path not in role_paths
        )

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
                (
                    *(row.path for row in validation_rows),
                    *(
                        path
                        for fact in projection.impact_facts
                        for path in (fact.source.path, fact.target.path)
                        if _is_test(path)
                    ),
                )
            )
        )[:5]
        validation_plan = tuple(
            fact.rendered for fact in projection.validation_facts[:5]
        )
        semantic_projection = projection.semantic_graph
        facet_ids_by_path: dict[str, tuple[str, ...]] = {}
        for item in (*anchors, *supporting):
            if item.path and item.facet_ids:
                facet_ids_by_path[item.path] = tuple(
                    dict.fromkeys(
                        (*facet_ids_by_path.get(item.path, ()), *item.facet_ids)
                    )
                )
        scoped_link_items = tuple(
            scoped
            for item in link_items
            if (
                scoped := replace(
                    item,
                    facet_ids=tuple(
                        dict.fromkeys(
                            (
                                *_matching_facet_ids(
                                    symbol=item.source_symbol,
                                    path=item.source_path,
                                    facets=task_facets,
                                ),
                                *_matching_facet_ids(
                                    symbol=item.symbol,
                                    path=item.path,
                                    facets=task_facets,
                                ),
                                *facet_ids_by_path.get(item.source_path, ()),
                                *facet_ids_by_path.get(item.path, ()),
                            )
                        )
                    ),
                )
            ).facet_ids
        )
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
                localization_role=LocalizationRole.UNCERTAIN.value,
                facet_ids=(
                    _matching_facet_ids(
                        symbol=fact.scope or fact.subject,
                        path=fact.path,
                        facets=task_facets,
                    )
                    or facet_ids_by_path.get(fact.path, ())
                    or anchor_facet_ids
                ),
            )
            for fact in (semantic_projection.facts if semantic_projection else ())
        )
        evidence_items = tuple(
            {
                item.evidence_sha256: item
                for item in (
                    *primary,
                    *generic_inspection,
                    *generic_supporting,
                    *public_surface,
                    *integration,
                    *scoped_link_items,
                    *semantic_items,
                )
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
        proposed_new_files = _proposed_rust_files(task_facets, repository.documents)
        uncovered_facets = tuple(
            (
                f"{facet.facet_id} role={facet.role.value} unresolved="
                + ",".join(facet.unresolved_symbols)
            )
            for facet in task_facets
            if facet.unresolved_symbols
        )
        selected_tokens = sum(
            max(1, len(item.source_excerpt.split())) for item in evidence_items
        ) + int(projection.token_count)
        return GTContextPacket(
            status=status,
            repository_identity=identity,
            task_facets=task_facets,
            task_anchors=primary,
            primary_edit_targets=primary,
            inspection_candidates=generic_inspection,
            inspection_public_surface=tuple(
                {item.path: item for item in public_surface}.values()
            ),
            inspection_integration=integration,
            proposed_new_files=proposed_new_files,
            uncovered_facets=uncovered_facets,
            supporting_files=generic_supporting,
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
                or any("truncated" in reason for reason in repository.reason_codes)
                or retrieval.reason_codes
                and any("budget" in reason for reason in retrieval.reason_codes)
            ),
        )


__all__ = [
    "ContextCompileRequest",
    "ContextEvidenceItem",
    "ContextStatus",
    "GTContextPacket",
    "LocalizationRole",
    "RepositoryContextCompiler",
    "TaskFacet",
    "compile_task_facets",
]
