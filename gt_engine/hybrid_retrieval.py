"""Deterministic, trajectory-conditioned hybrid repository retrieval.

This module is deliberately independent from provider delivery and graph storage.
Callers adapt their repository index into :class:`RepositoryDocument` and
:class:`StructuralLink` values, then inject an optional dense backend.  Every
retrieval channel runs independently; fusion consumes ranks rather than
incomparable channel scores.
"""

from __future__ import annotations

import hashlib
import math
import re
import shlex
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_PATH_SPLIT_RE = re.compile(r"[/\\.\-]+")
_QUERY_GLUE = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "through",
        "to",
        "up",
        "with",
    }
)
_COMMON_SYMBOLS = frozenset(
    {
        "app",
        "data",
        "get",
        "id",
        "init",
        "item",
        "main",
        "model",
        "n",
        "repr",
        "result",
        "run",
        "set",
        "str",
        "test",
        "value",
        "x",
    }
)
_PROGRAM_ARGUMENT_FLAGS = frozenset(
    {"-c", "-e", "--command", "--eval", "--execute", "-command", "/c"}
)
_SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&"})
_SAFE_ACTION_TOKEN = re.compile(r"^[A-Za-z0-9_./:+@%=-]{1,160}$")
_PATH_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:(?:[A-Za-z]:)?[\\/])?"
    r"(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+"
    r"(?![A-Za-z0-9_.-])"
)
_BACKTICK_ENTITY_RE = re.compile(r"`([^`\r\n]{1,160})`")
_CALL_ENTITY_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_UPPER_ENTITY_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{3,})(?![A-Za-z0-9_])")


def _looks_code_shaped_identifier(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
        and (
            "_" in value
            or any(character.isupper() for character in value[1:])
            or value.isupper()
        )
    )


def _explicit_identifier_tokens(value: str) -> set[str]:
    """Extract syntax-marked task entities without promoting ordinary prose.

    Sparse and dense channels still receive the complete task.  This narrower
    surface controls only mechanical exact-symbol certification.
    """

    text = str(value or "")
    entities: set[str] = set()
    for match in _BACKTICK_ENTITY_RE.finditer(text):
        entities.update(token.lower() for token in _TOKEN_RE.findall(match.group(1)))
    entities.update(match.group(1).lower() for match in _CALL_ENTITY_RE.finditer(text))
    entities.update(match.group(1).lower() for match in _UPPER_ENTITY_RE.finditer(text))
    return entities


class RetrievalIntent(StrEnum):
    """The repository relationship needed for the agent's next decision."""

    IMPLEMENTATION_CONTEXT = "implementation_context"
    VALIDATION_CONTEXT = "validation_context"
    MISSING_CONTEXT = "missing_context"
    DIAGNOSTIC_ROOT_CAUSE = "diagnostic_root_cause"
    CHANGE_IMPACT = "change_impact"
    OTHER = "other"


class RetrievalChannel(StrEnum):
    EXACT = "exact"
    LEXICAL = "lexical"
    BM25 = "bm25"
    DENSE = "dense"
    STRUCTURAL = "structural"


class EvidenceOrigin(StrEnum):
    """Where evidence content entered the task trajectory."""

    PREEXISTING_REPOSITORY = "preexisting_repository"
    MODEL_AUTHORED = "model_authored"
    TASK_DELIVERABLE = "task_deliverable"
    GENERATED_ARTIFACT = "generated_artifact"
    EXTERNAL_RUNTIME = "external_runtime"


class EvidenceAuthority(StrEnum):
    """What a candidate can mechanically establish for the next decision."""

    IDENTITY_ONLY = "identity_only"
    RANKING_SUPPORT = "ranking_support"
    CERTIFIED_RELATION = "certified_relation"
    EXECUTION_OBSERVATION = "execution_observation"
    DETERMINISTIC_DERIVED = "deterministic_derived"


_CHANNEL_ORDER = {
    RetrievalChannel.EXACT: 0,
    RetrievalChannel.LEXICAL: 1,
    RetrievalChannel.BM25: 2,
    RetrievalChannel.DENSE: 3,
    RetrievalChannel.STRUCTURAL: 4,
}


def _normalize_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(str(value or "")):
        tokens.append(raw.lower())
        expanded = raw.replace("_", " ")
        expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", expanded)
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
        tokens.extend(
            part.lower() for part in expanded.split() if part and part.lower() != raw.lower()
        )
    return tuple(tokens)


def _path_tokens(path: str) -> tuple[str, ...]:
    return tuple(
        token for part in _PATH_SPLIT_RE.split(_normalize_path(path)) for token in _tokens(part)
    )


def _canonical_symbol(symbol: str | None) -> str:
    value = str(symbol or "").strip()
    if not value:
        return ""
    leaf = re.split(r"(?:::|[.#])", value)[-1]
    return leaf.lower() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", leaf) else ""


def _explicit_identifiers(state: RetrievalState) -> frozenset[str]:
    identifiers = _explicit_identifier_tokens(state.task_text)
    identifiers.update(
        canonical
        for symbol in state.active_symbols
        if (canonical := _canonical_symbol(symbol))
    )
    identifiers.update(_explicit_identifier_tokens("\n".join(state.diagnostics)))
    if state.action is not None:
        identifiers.update(
            token.lower()
            for token in state.action.semantic_tokens
            if _looks_code_shaped_identifier(token)
        )
    return frozenset(identifiers)


def _canonical_explicit_path(path: str) -> str:
    value = _normalize_path(path).lower()
    return value[len("/app/") :] if value.startswith("/app/") else value


def _explicit_paths(state: RetrievalState) -> frozenset[str]:
    action_targets = state.action.targets if state.action else ()
    material = "\n".join((state.task_text, *state.diagnostics))
    paths = {
        _canonical_explicit_path(match.group(0))
        for match in _PATH_LITERAL_RE.finditer(material.replace("\\", "/"))
    }
    paths.update(_canonical_explicit_path(path) for path in action_targets)
    # Active/changed paths are structural seeds, not proof that an arbitrary
    # span from the same file is missing from provider history.  Making them
    # exact authority caused the retriever to echo the file just read/edited.
    return frozenset(path for path in paths if path)


def _stable_hash(*parts: str) -> str:
    material = "\0".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()


def _default_token_counter(text: str) -> int:
    """Return a deterministic conservative token approximation.

    Provider integrations can inject their exact tokenizer.  The default
    counts words and punctuation independently, so packing never depends on
    an optional model package.
    """

    return len(re.findall(r"\w+|[^\w\s]", str(text or ""), re.UNICODE))


def _bounded_action_tokens(raw_command: str) -> tuple[str, ...]:
    header = str(raw_command or "").splitlines()[0][:2_048].strip()
    if not header:
        return ()
    header = re.split(r"\s+<<-?\s*", header, maxsplit=1)[0].strip()
    try:
        tokens = tuple(shlex.split(header, posix=True))
    except ValueError:
        tokens = tuple(header.split())
    bounded: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _PROGRAM_ARGUMENT_FLAGS or token in _SHELL_OPERATORS:
            break
        if token.startswith(("<<", ">", "<")):
            break
        if _SAFE_ACTION_TOKEN.fullmatch(token):
            bounded.append(token)
        if len(bounded) >= 32:
            break
    return tuple(bounded)


def _action_operation(executable: str, tokens: tuple[str, ...]) -> tuple[str, str | None]:
    lowered = executable.lower()
    arguments = tuple(token.lower() for token in tokens[1:])
    if lowered in {"pytest", "tox", "make", "ctest"}:
        return "validate", lowered
    if lowered in {"python", "python3"} and "pytest" in arguments:
        return "validate", "pytest"
    if lowered == "go" and arguments[:1] == ("test",):
        return "validate", "go_test"
    if lowered == "cargo" and arguments[:1] == ("test",):
        return "validate", "cargo_test"
    if lowered in {"rg", "grep", "find"}:
        return "search", None
    if lowered in {"cat", "head", "tail", "less"}:
        return "read", None
    if lowered in {"touch", "mkdir"}:
        return "create", None
    if lowered in {"rm", "rmdir"}:
        return "delete", None
    return "other", None


@dataclass(frozen=True)
class RetrievalActionState:
    """Bounded action semantics; never stores a raw shell or program body."""

    operation: str = "other"
    executable: str = ""
    targets: tuple[str, ...] = ()
    validation_kind: str | None = None
    semantic_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        operation = re.sub(r"[^a-z0-9_]+", "_", str(self.operation or "other").lower())[:40]
        executable = str(self.executable or "").replace("\\", "/").rsplit("/", 1)[-1]
        executable = re.sub(r"[^A-Za-z0-9_.+-]+", "", executable)[:80]
        targets = tuple(
            dict.fromkeys(
                _normalize_path(target)[:300]
                for target in self.targets
                if target and "\n" not in target and "\r" not in target
            )
        )[:24]
        semantic_tokens = tuple(
            dict.fromkeys(
                str(token)[:160]
                for token in self.semantic_tokens
                if _SAFE_ACTION_TOKEN.fullmatch(str(token))
            )
        )[:24]
        validation_kind = (
            re.sub(r"[^a-z0-9_.+-]+", "_", str(self.validation_kind).lower())[:80]
            if self.validation_kind
            else None
        )
        object.__setattr__(self, "operation", operation or "other")
        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "semantic_tokens", semantic_tokens)
        object.__setattr__(self, "validation_kind", validation_kind)

    @classmethod
    def from_raw_command(cls, raw_command: str) -> RetrievalActionState:
        tokens = _bounded_action_tokens(raw_command)
        if not tokens:
            return cls()
        executable = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
        operation, validation_kind = _action_operation(executable, tokens)
        operands = tuple(
            token
            for token in tokens[1:]
            if not token.startswith("-") and token.lower() not in {"test", "-m"}
        )
        targets = tuple(
            token
            for token in operands
            if "/" in token or "\\" in token or re.search(r"\.[A-Za-z0-9]{1,12}$", token)
        )
        semantic_tokens = tuple(token for token in operands if token not in targets)
        return cls(
            operation=operation,
            executable=executable,
            targets=targets,
            validation_kind=validation_kind,
            semantic_tokens=semantic_tokens,
        )

    def query_text(self) -> str:
        fields = [f"operation={self.operation}"]
        if self.executable:
            fields.append(f"executable={self.executable}")
        if self.targets:
            fields.append(f"targets={' '.join(self.targets)}")
        if self.validation_kind:
            fields.append(f"validation={self.validation_kind}")
        if self.semantic_tokens:
            fields.append(f"tokens={' '.join(self.semantic_tokens)}")
        return " ".join(fields)


@dataclass(frozen=True)
class RetrievalQueryPlan:
    """Deterministic split between the current decision and task fallback."""

    primary_text: str
    fallback_text: str = ""

    @property
    def full_text(self) -> str:
        return "\n".join(
            part for part in (self.primary_text.strip(), self.fallback_text.strip()) if part
        )


@dataclass(frozen=True)
class RetrievalState:
    task_text: str
    intent: RetrievalIntent
    proposed_action: InitVar[RetrievalActionState | str | None] = None
    action: RetrievalActionState | None = None
    active_paths: tuple[str, ...] = ()
    active_symbols: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    validation_state: str = "unknown"
    source_revision: str = ""
    previously_exposed_claims: tuple[str, ...] = ()

    def __post_init__(self, proposed_action: RetrievalActionState | str | None) -> None:
        if self.action is not None and proposed_action is not None:
            raise ValueError("provide action or legacy proposed_action, not both")
        action = self.action
        if isinstance(proposed_action, RetrievalActionState):
            action = proposed_action
        elif proposed_action is not None:
            action = RetrievalActionState.from_raw_command(str(proposed_action))
        if action is not None and not isinstance(action, RetrievalActionState):
            raise TypeError("action must be RetrievalActionState")
        object.__setattr__(self, "action", action)

    def query_plan(self) -> RetrievalQueryPlan:
        """Prefer observed failure state while retaining task text as fallback.

        A concrete current diagnostic is a stronger retrieval key than the
        original long-form task.  Mixing both at equal weight caused dense and
        sparse channels to return task-adjacent but failure-irrelevant files.
        Other lifecycle states retain the historical task-conditioned query.
        """

        current_sections = (
            self.intent.value,
            self.action.query_text() if self.action else "",
            " ".join(self.active_symbols),
            " ".join(self.diagnostics),
            self.validation_state,
        )
        current = "\n".join(
            section.strip() for section in current_sections if section.strip()
        )
        if self.intent is RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE and self.diagnostics:
            return RetrievalQueryPlan(primary_text=current, fallback_text=self.task_text)
        sections = (
            self.task_text,
            current,
            " ".join(self.active_paths),
            " ".join(self.changed_paths),
        )
        return RetrievalQueryPlan(
            primary_text="\n".join(
                section.strip() for section in sections if section.strip()
            )
        )

    def query_text(self) -> str:
        """Compile full replay identity, including deterministic fallback."""

        return self.query_plan().full_text

    def dense_query_text(self) -> str:
        return self.query_plan().primary_text

    def sparse_query_text(self) -> str:
        """Return sparse terms without creating false same-directory support.

        Active and changed paths seed the exact and structural channels.  Adding
        their generic directory/extension tokens (``src``, ``test``, ``py``)
        to lexical and BM25 makes those correlated channels appear to confirm
        almost every file in a repository.  Sparse retrieval instead consumes
        the task, typed intent, command, symbols, diagnostics, and validation
        state; exact path matching remains independently available.
        """

        current_sections = (
            self.intent.value,
            self.action.query_text() if self.action else "",
            " ".join(self.active_symbols),
            " ".join(self.diagnostics),
            self.validation_state,
        )
        current = "\n".join(
            section.strip() for section in current_sections if section.strip()
        )
        if self.intent is RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE and self.diagnostics:
            return current
        return "\n".join(
            section.strip() for section in (self.task_text, current) if section.strip()
        )

    @property
    def query_hash(self) -> str:
        return _stable_hash(self.query_text(), self.source_revision)


def retrieval_query_terms(
    state: RetrievalState,
    *,
    limit: int = 32,
) -> tuple[str, ...]:
    """Compile literal FTS seeds from the same typed runtime state.

    Task-contract extraction intentionally drops common words because it is an
    obligation parser. Repository retrieval cannot do that: identifiers such
    as ``default``, ``help``, and ``empty`` are often the only vocabulary
    shared by an issue and the relevant source or test body. This compiler
    removes only grammatical glue and controller labels, then ranks terms
    deterministically by identifier specificity, frequency, length, and first
    occurrence.
    """

    maximum = max(0, int(limit))
    if maximum == 0:
        return ()
    raw_tokens = _tokens(state.sparse_query_text())
    controller_tokens = {
        *_tokens(state.intent.value),
        *_tokens(state.validation_state),
        "unknown",
    }
    first_seen: dict[str, int] = {}
    frequency: Counter[str] = Counter()
    for position, token in enumerate(raw_tokens):
        normalized = str(token or "").lower()
        if (
            len(normalized) < 2
            or normalized.isdigit()
            or normalized in _QUERY_GLUE
            or normalized in controller_tokens
        ):
            continue
        first_seen.setdefault(normalized, position)
        frequency[normalized] += 1
    ranked = sorted(
        frequency,
        key=lambda token: (
            -int("_" in token or "." in token),
            -frequency[token],
            -len(token),
            first_seen[token],
            token,
        ),
    )
    return tuple(ranked[:maximum])


def retrieval_exact_identifiers(
    state: RetrievalState,
    *,
    limit: int = 64,
) -> tuple[str, ...]:
    """Return syntax-marked identifiers for exact graph candidate seeding.

    This surface is deliberately narrower than sparse query terms: only code
    entities explicitly marked by the task/trajectory are eligible.  It is
    used to ensure a long issue cannot BM25-crowd named owners and APIs out of
    the bounded graph projection.  Downstream owner/facet checks still decide
    whether a matching repository symbol is relevant enough to expose.
    """

    ordered: list[str] = []
    sections = (
        state.task_text,
        *state.active_symbols,
        *state.diagnostics,
    )
    eligible = _explicit_identifiers(state)
    for section in sections:
        for token in _TOKEN_RE.findall(str(section or "")):
            lowered = token.casefold()
            if lowered in eligible and lowered not in ordered:
                ordered.append(lowered)
                if len(ordered) >= max(1, int(limit)):
                    return tuple(ordered)
    return tuple(ordered)


@dataclass(frozen=True)
class RepositoryDocument:
    path: str
    text: str
    start_line: int | None = 1
    end_line: int | None = None
    symbol: str | None = None
    provenance: tuple[str, ...] = ()
    origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY
    origin_revision: str = ""

    def __post_init__(self) -> None:
        normalized = _normalize_path(self.path)
        if not normalized:
            raise ValueError("repository document path must not be empty")
        object.__setattr__(self, "path", normalized)
        if self.end_line is None and self.start_line is not None:
            line_count = max(1, str(self.text or "").count("\n") + 1)
            object.__setattr__(self, "end_line", self.start_line + line_count - 1)
        if not isinstance(self.origin, EvidenceOrigin):
            object.__setattr__(self, "origin", EvidenceOrigin(str(self.origin)))


@dataclass(frozen=True)
class StructuralLink:
    source_path: str
    target_path: str
    relation: str
    confidence: float = 1.0
    provenance: tuple[str, ...] = ()
    certified: bool = False
    source_symbol: str | None = None
    source_start_line: int | None = None
    target_symbol: str | None = None
    target_start_line: int | None = None
    source_content_sha256: str = ""
    target_content_sha256: str = ""
    source_evidence_origin: str = "unknown"
    target_evidence_origin: str = "unknown"
    origin: str = "unknown"
    resolution_outcome: str = "unknown"
    resolution_method: str = ""
    candidate_count: int | None = None
    evidence_type: str = ""
    verification_status: str = ""
    receiver_type: str = ""
    route: str = ""
    http_method: str = ""
    source_kind: str = ""
    target_kind: str = ""
    source_return_type: str = ""
    target_return_type: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", _normalize_path(self.source_path))
        object.__setattr__(self, "target_path", _normalize_path(self.target_path))
        if not self.source_path or not self.target_path:
            raise ValueError("structural link paths must not be empty")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("structural link confidence must be between zero and one")
        for field_name in ("source_content_sha256", "target_content_sha256"):
            value = str(getattr(self, field_name) or "").strip().lower()
            if value and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{field_name} must be empty or a SHA-256 digest")
            object.__setattr__(self, field_name, value)
        allowed_evidence_origins = {
            origin.value for origin in EvidenceOrigin
        } | {"unknown"}
        for field_name in ("source_evidence_origin", "target_evidence_origin"):
            value = str(getattr(self, field_name) or "unknown").strip().lower()
            if value not in allowed_evidence_origins:
                raise ValueError(f"unsupported endpoint evidence origin: {value}")
            object.__setattr__(self, field_name, value)
        origin = str(self.origin or "unknown").strip().lower()
        if origin not in {
            "program",
            "builtin",
            "stdlib",
            "third_party",
            "framework",
            "external",
            "unknown",
        }:
            raise ValueError(f"unsupported structural link origin: {origin}")
        outcome = str(self.resolution_outcome or "unknown").strip().lower()
        if outcome not in {
            "exact",
            "ambiguous",
            "unresolved",
            "external",
            "heuristic",
            "dynamic",
            "global_fallback",
            "reexport_unproven",
            "unknown",
        }:
            raise ValueError(f"unsupported structural resolution outcome: {outcome}")
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "resolution_outcome", outcome)
        candidates = self.candidate_count
        if candidates is not None:
            try:
                candidates = int(candidates)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("candidate_count must be an integer or None") from exc
            if candidates < 0:
                raise ValueError("candidate_count must not be negative")
        object.__setattr__(self, "candidate_count", candidates)
        for field_name in (
            "resolution_method",
            "evidence_type",
            "verification_status",
            "receiver_type",
            "route",
            "http_method",
            "source_kind",
            "target_kind",
            "source_return_type",
            "target_return_type",
        ):
            object.__setattr__(self, field_name, str(getattr(self, field_name) or "").strip())


@dataclass(frozen=True)
class RetrievalCandidate:
    path: str
    start_line: int | None
    end_line: int | None
    symbol: str | None
    text: str
    channel: RetrievalChannel
    channel_rank: int
    relation: str | None
    provenance: tuple[str, ...]
    source_revision: str
    channel_score: float = 0.0
    origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY
    authority: EvidenceAuthority = EvidenceAuthority.RANKING_SUPPORT
    origin_revision: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normalize_path(self.path))
        if not self.path:
            raise ValueError("candidate path must not be empty")
        if self.channel_rank < 1:
            raise ValueError("candidate rank must be one-based")
        if not isinstance(self.origin, EvidenceOrigin):
            object.__setattr__(self, "origin", EvidenceOrigin(str(self.origin)))
        if not isinstance(self.authority, EvidenceAuthority):
            object.__setattr__(self, "authority", EvidenceAuthority(str(self.authority)))

    @property
    def content_claim_id(self) -> str:
        """Stable identity of the bounded semantic content delivered to a model.

        Channel support and GraphDB row IDs prove how a candidate was found;
        they are not part of what the fact *is*.  Keeping them in the identity
        caused an unchanged span to be delivered again after each graph rebuild.
        """

        normalized_text = " ".join(str(self.text or "").split())
        return _stable_hash(
            self.path.lower(),
            str(self.start_line or 0),
            str(self.end_line or 0),
            str(self.symbol or ""),
            str(self.relation or "").lower(),
            normalized_text,
        )

    @property
    def claim_hash(self) -> str:
        """Backward-compatible name for :attr:`content_claim_id`."""

        return self.content_claim_id


@dataclass(frozen=True)
class RankedFile:
    path: str
    fused_score: float
    channel_ranks: tuple[tuple[RetrievalChannel, int], ...]
    representative: RetrievalCandidate
    provenance: tuple[str, ...]
    channel_candidates: tuple[tuple[RetrievalChannel, RetrievalCandidate], ...] = ()

    @property
    def support_count(self) -> int:
        return len(self.channel_ranks)


@dataclass(frozen=True)
class ChannelReceipt:
    channel: RetrievalChannel
    candidate_count: int
    failed: bool
    reason: str
    latency_ms: float
    available: bool = True
    backend_identity: str = ""


@dataclass(frozen=True)
class HybridRetrievalResult:
    ranked_files: tuple[RankedFile, ...]
    ranked_spans: tuple[RetrievalCandidate, ...]
    selected_context: tuple[RetrievalCandidate, ...]
    abstained: bool
    reason_codes: tuple[str, ...]
    channel_receipts: tuple[ChannelReceipt, ...]
    latency_ms: float
    query_hash: str
    token_budget: int
    selected_token_count: int
    character_budget: int | None = None
    selected_character_count: int = 0
    dense_fallback_only: bool = False

    def retrieval_status(self) -> dict[str, object]:
        """Return an explicit dense/fallback accounting boundary.

        A dense backend receipt proves provisioning and a dense channel receipt
        proves an attempt/result.  Neither proves that dense evidence affected
        the selected context.  This method keeps those facts separate so a
        report cannot silently label sparse fallback as dense success.
        """

        dense = next(
            (row for row in self.channel_receipts if row.channel is RetrievalChannel.DENSE),
            None,
        )
        dense_reason = str(dense.reason if dense is not None else "")
        dense_attempted = bool(
            dense is not None
            and not dense.failed
            and dense_reason not in {"backend_unavailable", "candidate_pool_empty"}
        )
        dense_ranked_paths = {
            row.path
            for row in self.ranked_files
            if any(channel is RetrievalChannel.DENSE for channel, _rank in row.channel_ranks)
        }
        dense_selected = any(
            row.path in dense_ranked_paths for row in self.selected_context
        )
        available = bool(dense is not None and dense.available and not dense.failed)
        fallback_used = bool(self.selected_context) and not dense_selected
        fallback_reason = ""
        if fallback_used:
            fallback_reason = (
                dense_reason
                or "dense_not_selected"
                if dense is not None
                else "dense_channel_missing"
            )
        return {
            "schema": "gt.retrieval_status.v1",
            "expected_mode": "dense_fallback_only" if self.dense_fallback_only else "dense_primary",
            "dense_channel_present": dense is not None,
            "dense_backend_available": available,
            "dense_query_attempted": dense_attempted,
            "dense_candidate_count": int(dense.candidate_count) if dense is not None else 0,
            "dense_result_used": dense_selected,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "selected_evidence_count": len(self.selected_context),
        }


@dataclass(frozen=True)
class PreemptiveRetrievalFrame:
    query_hash: str
    source_revision: str
    trigger: str
    evidence: tuple[RetrievalCandidate, ...]
    rendered_text: str
    token_count: int
    claim_hashes: tuple[str, ...]


@runtime_checkable
class RetrievalChannelBackend(Protocol):
    channel: RetrievalChannel

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> Sequence[RetrievalCandidate]: ...


@runtime_checkable
class DenseEmbeddingBackend(Protocol):
    def embed_query(self, text: str) -> Sequence[float]: ...

    def embed_documents(self, texts: tuple[str, ...]) -> Sequence[Sequence[float]]: ...


def _document_candidate(
    document: RepositoryDocument,
    *,
    state: RetrievalState,
    channel: RetrievalChannel,
    rank: int,
    score: float,
    relation: str | None = None,
    provenance: tuple[str, ...] = (),
) -> RetrievalCandidate:
    combined_provenance = tuple(
        dict.fromkeys((*document.provenance, *provenance, channel.value))
    )
    authority = EvidenceAuthority.RANKING_SUPPORT
    if channel is RetrievalChannel.EXACT and set(combined_provenance) & {
        "exact_path",
        "exact_symbol",
    }:
        authority = EvidenceAuthority.IDENTITY_ONLY
    elif channel is RetrievalChannel.STRUCTURAL and "structural_certified" in set(
        combined_provenance
    ):
        authority = EvidenceAuthority.CERTIFIED_RELATION
    return RetrievalCandidate(
        path=document.path,
        start_line=document.start_line,
        end_line=document.end_line,
        symbol=document.symbol,
        text=document.text,
        channel=channel,
        channel_rank=rank,
        relation=relation,
        provenance=combined_provenance,
        source_revision=state.source_revision,
        channel_score=float(score),
        origin=document.origin,
        authority=authority,
        origin_revision=document.origin_revision,
    )


def _rank_documents(
    scored: Sequence[tuple[float, RepositoryDocument, str | None, tuple[str, ...]]],
    *,
    state: RetrievalState,
    channel: RetrievalChannel,
    limit: int,
) -> tuple[RetrievalCandidate, ...]:
    ordered = sorted(
        (row for row in scored if row[0] > 0.0),
        key=lambda row: (-row[0], row[1].path.lower(), row[1].start_line or 0),
    )
    return tuple(
        _document_candidate(
            document,
            state=state,
            channel=channel,
            rank=rank,
            score=score,
            relation=relation,
            provenance=provenance,
        )
        for rank, (score, document, relation, provenance) in enumerate(ordered[: max(0, limit)], 1)
    )


class ExactRetrievalChannel:
    channel = RetrievalChannel.EXACT

    def __init__(self, documents: Sequence[RepositoryDocument]) -> None:
        self._documents = tuple(documents)
        self._prepared = tuple(
            (
                document,
                frozenset(_path_tokens(document.path)),
                frozenset(_tokens(document.symbol or "")),
                str(document.text or "").lower(),
            )
            for document in self._documents
        )
        self._path_document_frequency = Counter(
            token for _, path_tokens, _, _ in self._prepared for token in path_tokens
        )
        self._symbol_document_frequency = Counter(
            symbol
            for document in self._documents
            if (symbol := _canonical_symbol(document.symbol))
        )
        self._distinctive_path_frequency = max(1, math.ceil(len(self._documents) * 0.20))

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        query_tokens = set(_tokens(state.query_text()))
        explicit_identifiers = _explicit_identifiers(state)
        explicit_paths = _explicit_paths(state)
        scored: list[tuple[float, RepositoryDocument, str | None, tuple[str, ...]]] = []
        for document, path_tokens, symbol_tokens, text in self._prepared:
            score = 0.0
            reasons: list[str] = []
            task_path_overlap = query_tokens & path_tokens
            path_overlap = {
                token
                for token in task_path_overlap
                if self._path_document_frequency[token] <= self._distinctive_path_frequency
            }
            symbol_overlap = query_tokens & symbol_tokens
            if path_overlap:
                score += 5.0 * len(path_overlap)
                reasons.append("exact_path_token")
                # Preserve the full task/path agreement for downstream
                # inspection ranking. The score still uses only distinctive
                # tokens, so common directory vocabulary cannot manufacture
                # identity authority.
                reasons.append(f"exact_path_token_count:{len(task_path_overlap)}")
                reasons.extend(
                    f"exact_path_token_value:{token}"
                    for token in sorted(path_overlap)
                )
            if symbol_overlap:
                score += 6.0 * len(symbol_overlap)
                reasons.append("exact_symbol_token")
            normalized_path = _canonical_explicit_path(document.path)
            if normalized_path in explicit_paths:
                score += 10.0
                reasons.append("exact_path")
            canonical_symbol = _canonical_symbol(document.symbol)
            if (
                len(canonical_symbol) >= 4
                and canonical_symbol not in _COMMON_SYMBOLS
                and self._symbol_document_frequency[canonical_symbol] == 1
                and canonical_symbol in explicit_identifiers
            ):
                score += 10.0
                reasons.append("exact_symbol")
            exact_phrases = {
                part.strip().lower()
                for part in (state.task_text, *(state.diagnostics or ()))
                if len(part.strip()) >= 8
            }
            if any(phrase in text for phrase in exact_phrases):
                score += 2.0
                reasons.append("exact_phrase")
            scored.append((score, document, None, tuple(reasons)))
        return _rank_documents(scored, state=state, channel=self.channel, limit=limit)


class LexicalRetrievalChannel:
    channel = RetrievalChannel.LEXICAL

    def __init__(self, documents: Sequence[RepositoryDocument]) -> None:
        self._documents = tuple(documents)
        self._prepared = tuple(
            (
                document,
                Counter(
                    (
                        *_path_tokens(document.path),
                        *_tokens(document.symbol or ""),
                        *_tokens(document.text),
                    )
                ),
            )
            for document in self._documents
        )

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        query = Counter(_tokens(state.sparse_query_text()))
        scored: list[tuple[float, RepositoryDocument, str | None, tuple[str, ...]]] = []
        for document, terms in self._prepared:
            overlap = set(query) & set(terms)
            numerator = sum(min(query[token], terms[token]) for token in overlap)
            denominator = sum(query.values()) + sum(terms.values()) - numerator
            score = float(numerator / denominator) if denominator else 0.0
            scored.append((score, document, None, ("lexical_token_overlap",)))
        return _rank_documents(scored, state=state, channel=self.channel, limit=limit)


class BM25RetrievalChannel:
    channel = RetrievalChannel.BM25

    def __init__(
        self,
        documents: Sequence[RepositoryDocument],
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        self._documents = tuple(documents)
        self._k1 = float(k1)
        self._b = float(b)
        self._prepared = tuple(
            (
                document,
                Counter(
                    (
                        *_path_tokens(document.path),
                        *_tokens(document.symbol or ""),
                        *_tokens(document.text),
                    )
                ),
            )
            for document in self._documents
        )
        self._document_count = len(self._prepared)
        self._average_length = (
            sum(sum(counts.values()) for _, counts in self._prepared) / self._document_count
            if self._document_count
            else 0.0
        )
        self._document_frequency = Counter(term for _, counts in self._prepared for term in counts)

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        query_terms = tuple(dict.fromkeys(_tokens(state.sparse_query_text())))
        if not self._prepared or not query_terms:
            return ()
        scored: list[tuple[float, RepositoryDocument, str | None, tuple[str, ...]]] = []
        for document, counts in self._prepared:
            document_length = sum(counts.values())
            length_ratio = document_length / self._average_length if self._average_length else 1.0
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if frequency <= 0:
                    continue
                document_frequency = self._document_frequency[term]
                inverse_frequency = math.log(
                    1.0
                    + (self._document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + self._k1 * (1.0 - self._b + self._b * length_ratio)
                score += inverse_frequency * (frequency * (self._k1 + 1.0)) / denominator
            scored.append((score, document, None, ("bm25",)))
        return _rank_documents(scored, state=state, channel=self.channel, limit=limit)


class DenseRetrievalChannel:
    channel = RetrievalChannel.DENSE

    def __init__(
        self,
        documents: Sequence[RepositoryDocument],
        backend: DenseEmbeddingBackend | None,
    ) -> None:
        self._documents = tuple(documents)
        documents_by_path: dict[str, list[RepositoryDocument]] = defaultdict(list)
        for document in self._documents:
            documents_by_path[document.path.lower()].append(document)
        self._documents_by_path = {path: tuple(rows) for path, rows in documents_by_path.items()}
        self._backend = backend
        self._candidate_paths: frozenset[str] | None = None
        self._candidate_path_order: tuple[str, ...] = ()
        self._candidate_document_limit: int | None = None
        self.availability_reason = ""

    def set_candidate_paths(
        self,
        paths: Sequence[str] | None,
        *,
        document_limit: int | None = None,
    ) -> None:
        """Restrict this pass to a deterministic cascade candidate pool."""

        self._candidate_paths = (
            None
            if paths is None
            else frozenset(str(path).strip().lower() for path in paths if str(path).strip())
        )
        self._candidate_path_order = (
            ()
            if paths is None
            else tuple(
                dict.fromkeys(str(path).strip().lower() for path in paths if str(path).strip())
            )
        )
        self._candidate_document_limit = (
            None if document_limit is None else max(1, int(document_limit))
        )

    @property
    def backend_identity(self) -> str:
        if self._backend is None:
            return ""
        return str(getattr(self._backend, "identity", type(self._backend).__qualname__))

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        if self._backend is None:
            self.availability_reason = "backend_unavailable"
            return ()
        if self._candidate_paths is None:
            selected_documents = self._documents
        else:
            selected: list[RepositoryDocument] = []
            # Preserve coverage across files before taking additional spans
            # from any one file.  This is deterministic and bounds ONNX work
            # by documents, not merely by path names.
            limit = self._candidate_document_limit or sum(
                len(self._documents_by_path.get(path, ())) for path in self._candidate_paths
            )
            for offset in range(limit):
                for path in self._candidate_path_order:
                    rows = self._documents_by_path.get(path, ())
                    if offset < len(rows):
                        selected.append(rows[offset])
                        if len(selected) >= limit:
                            break
                if len(selected) >= limit:
                    break
            selected_documents = tuple(selected)
        if not selected_documents:
            self.availability_reason = "candidate_pool_empty"
            return ()
        self.availability_reason = (
            ""
            if self._candidate_paths is None
            else (
                f"candidate_pool={len(selected_documents)}/{len(self._documents)}"
                f"_docs/{len(self._candidate_paths)}_paths"
            )
        )
        query = tuple(float(item) for item in self._backend.embed_query(state.dense_query_text()))
        document_texts = tuple(
            "\n".join(
                part
                for part in (
                    f"path: {document.path}",
                    f"symbol: {document.symbol}" if document.symbol else "",
                    document.text,
                )
                if part
            )
            for document in selected_documents
        )
        embeddings = tuple(
            tuple(float(item) for item in row)
            for row in self._backend.embed_documents(document_texts)
        )
        if len(embeddings) != len(selected_documents):
            raise ValueError("dense backend returned a different number of document embeddings")
        query_norm = math.sqrt(sum(value * value for value in query))
        scored: list[tuple[float, RepositoryDocument, str | None, tuple[str, ...]]] = []
        for document, embedding in zip(selected_documents, embeddings, strict=True):
            if len(embedding) != len(query):
                raise ValueError("dense backend returned inconsistent embedding dimensions")
            document_norm = math.sqrt(sum(value * value for value in embedding))
            denominator = query_norm * document_norm
            cosine = (
                sum(left * right for left, right in zip(query, embedding, strict=True))
                / denominator
                if denominator
                else 0.0
            )
            # Negative cosine is not useful evidence and must not be ranked.
            scored.append((max(0.0, cosine), document, None, ("dense_cosine",)))
        return _rank_documents(scored, state=state, channel=self.channel, limit=limit)


class StructuralRetrievalChannel:
    channel = RetrievalChannel.STRUCTURAL

    def __init__(
        self,
        documents: Sequence[RepositoryDocument],
        links: Sequence[StructuralLink],
    ) -> None:
        documents_by_path: dict[str, list[RepositoryDocument]] = defaultdict(list)
        for document in documents:
            documents_by_path[document.path.lower()].append(document)
        self._documents = {
            path: tuple(
                sorted(
                    rows,
                    key=lambda row: (
                        row.start_line or 0,
                        str(row.symbol or "").lower(),
                        row.text,
                    ),
                )
            )
            for path, rows in documents_by_path.items()
        }
        self._links = tuple(links)

    def _endpoint_document(
        self,
        path: str,
        *,
        symbol: str | None,
        start_line: int | None,
        state: RetrievalState,
    ) -> tuple[RepositoryDocument | None, bool]:
        documents = self._documents.get(path)
        if not documents:
            return None, False
        normalized_symbol = _canonical_symbol(symbol)
        exact = tuple(
            document
            for document in documents
            if (start_line is None or document.start_line == start_line)
            and (
                not normalized_symbol
                or _canonical_symbol(document.symbol) == normalized_symbol
            )
        )
        if exact:
            return exact[0], bool(normalized_symbol or start_line is not None)
        query_terms = set(_tokens(state.sparse_query_text()))

        def relevance(document: RepositoryDocument) -> tuple[int, int, int, str]:
            symbol_terms = set(_tokens(document.symbol or ""))
            text_terms = set(_tokens(document.text))
            return (
                len(query_terms & symbol_terms),
                len(query_terms & text_terms),
                -(document.start_line or 0),
                str(document.symbol or "").lower(),
            )

        return max(documents, key=relevance), False

    def retrieve(
        self,
        state: RetrievalState,
        *,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        action_targets = state.action.targets if state.action is not None else ()
        seeds = {
            _normalize_path(path).lower()
            for path in (*state.active_paths, *state.changed_paths, *action_targets)
            if _normalize_path(path)
        }
        if not seeds:
            return ()
        best: dict[str, tuple[float, RepositoryDocument, str, tuple[str, ...]]] = {}
        for link in self._links:
            source = link.source_path.lower()
            target = link.target_path.lower()
            if source in seeds and target not in seeds:
                candidate_path = target
                relation = link.relation
                action_target = source
                endpoint_symbol = link.target_symbol
                endpoint_start_line = link.target_start_line
            elif target in seeds and source not in seeds:
                candidate_path = source
                relation = f"inverse:{link.relation}"
                action_target = target
                endpoint_symbol = link.source_symbol
                endpoint_start_line = link.source_start_line
            else:
                continue
            document, endpoint_aligned = self._endpoint_document(
                candidate_path,
                symbol=endpoint_symbol,
                start_line=endpoint_start_line,
                state=state,
            )
            if document is None:
                continue
            provenance = (
                *link.provenance,
                f"structural:{relation}",
                f"action_target:{action_target}",
                *(
                    (f"edge_endpoint_symbol:{endpoint_symbol}",)
                    if endpoint_aligned and endpoint_symbol
                    else ()
                ),
                *(
                    (f"edge_endpoint_start:{endpoint_start_line}",)
                    if endpoint_aligned and endpoint_start_line is not None
                    else ()
                ),
                *(("edge_endpoint_unresolved",) if not endpoint_aligned else ()),
            )
            if link.certified:
                provenance = (*provenance, "structural_certified")
            row = (
                float(link.confidence),
                document,
                relation,
                tuple(dict.fromkeys(provenance)),
            )
            previous = best.get(candidate_path)
            if previous is None or (row[0], relation) > (previous[0], previous[2]):
                best[candidate_path] = row
        return _rank_documents(tuple(best.values()), state=state, channel=self.channel, limit=limit)


def reciprocal_rank_fusion(
    channel_results: Mapping[RetrievalChannel, Sequence[RetrievalCandidate]],
    *,
    k: int = 60,
) -> tuple[RankedFile, ...]:
    """Fuse independent ranks with equal-weight RRF and stable path ties."""

    if k < 1:
        raise ValueError("RRF k must be positive")
    per_path: dict[str, dict[RetrievalChannel, RetrievalCandidate]] = defaultdict(dict)
    display_paths: dict[str, str] = {}
    for channel in sorted(channel_results, key=lambda item: _CHANNEL_ORDER[item]):
        for candidate in channel_results[channel]:
            key = candidate.path.lower()
            display_paths.setdefault(key, candidate.path)
            previous = per_path[key].get(channel)
            if previous is None or candidate.channel_rank < previous.channel_rank:
                per_path[key][channel] = candidate

    fused: list[RankedFile] = []
    for key, by_channel in per_path.items():
        channel_ranks = tuple(
            (channel, by_channel[channel].channel_rank)
            for channel in sorted(by_channel, key=lambda item: _CHANNEL_ORDER[item])
        )
        sparse_ranks = [
            rank
            for channel, rank in channel_ranks
            if channel
            in {
                RetrievalChannel.EXACT,
                RetrievalChannel.LEXICAL,
                RetrievalChannel.BM25,
            }
        ]
        independent_ranks = [min(sparse_ranks)] if sparse_ranks else []
        independent_ranks.extend(
            rank
            for channel, rank in channel_ranks
            if channel in {RetrievalChannel.STRUCTURAL, RetrievalChannel.DENSE}
        )
        score = sum(1.0 / (k + rank) for rank in independent_ranks)

        def representative_key(
            row: RetrievalCandidate,
        ) -> tuple[float, int, int, str]:
            provenance = set(row.provenance)
            evidence_priority = 0.0
            if "structural_certified" in provenance:
                evidence_priority = 5.0
            elif "exact_path" in provenance or "exact_symbol" in provenance:
                evidence_priority = 4.0
            elif row.relation:
                evidence_priority = 3.0
            elif row.channel is RetrievalChannel.DENSE:
                evidence_priority = 2.0
            return (
                -evidence_priority,
                row.channel_rank,
                row.start_line or 0,
                row.claim_hash,
            )

        representative = min(
            by_channel.values(),
            key=representative_key,
        )
        provenance = tuple(
            dict.fromkeys(
                item
                for channel in sorted(by_channel, key=lambda value: _CHANNEL_ORDER[value])
                for item in by_channel[channel].provenance
            )
        )
        fused.append(
            RankedFile(
                path=display_paths[key],
                fused_score=score,
                channel_ranks=channel_ranks,
                representative=representative,
                provenance=provenance,
                channel_candidates=tuple(
                    (channel, by_channel[channel])
                    for channel in sorted(by_channel, key=lambda value: _CHANNEL_ORDER[value])
                ),
            )
        )
    return tuple(sorted(fused, key=lambda row: (-row.fused_score, row.path.lower(), row.path)))


def _render_candidate(candidate: RetrievalCandidate) -> str:
    location = candidate.path
    if candidate.start_line is not None:
        location += f":{candidate.start_line}"
        if candidate.end_line is not None and candidate.end_line != candidate.start_line:
            location += f"-{candidate.end_line}"
    metadata = [location]
    if candidate.symbol:
        metadata.append(f"symbol={candidate.symbol}")
    if candidate.relation:
        metadata.append(f"relation={candidate.relation}")
    label = (
        "Candidate repository context"
        if "validation_candidate" in candidate.provenance
        else "Repository facts for the next decision"
    )
    return f"[{label}: {'; '.join(metadata)}]\n{candidate.text.strip()}"


def _is_test_path(path: str) -> bool:
    normalized = "/" + str(path or "").lower().replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    return (
        any(segment in normalized for segment in ("/test/", "/tests/", "/__tests__/"))
        or basename.startswith("test_")
        or "_test." in basename
        or ".test." in basename
        or ".spec." in basename
    )


def _delivery_support(
    ranked: RankedFile,
    state: RetrievalState,
) -> tuple[str, RetrievalCandidate] | None:
    candidates = dict(ranked.channel_candidates)
    channels = set(candidates)
    structural = candidates.get(RetrievalChannel.STRUCTURAL)
    structural_provenance = set(structural.provenance) if structural else set()
    structural_endpoint_aligned = bool(
        structural
        and any(
            item.startswith(("edge_endpoint_symbol:", "edge_endpoint_start:"))
            for item in structural_provenance
        )
    )
    if (
        structural is not None
        and structural_endpoint_aligned
        and "structural_certified" in structural_provenance
    ):
        return "certified_relation", structural
    exact = candidates.get(RetrievalChannel.EXACT)
    exact_provenance = set(exact.provenance) if exact else set()
    if exact is not None and exact_provenance & {"exact_path", "exact_symbol"}:
        return "identity_only", exact
    if (
        state.intent is RetrievalIntent.VALIDATION_CONTEXT
        and _is_test_path(ranked.path)
        and RetrievalChannel.DENSE in channels
        and bool(
            channels
            & {
                RetrievalChannel.EXACT,
                RetrievalChannel.LEXICAL,
                RetrievalChannel.BM25,
            }
        )
    ):
        # Dense reranking of a sparse candidate is not independent proof of
        # relevance. For typed validation retrieval it is nevertheless useful
        # candidate context when the file is mechanically a test. Mark it as a
        # candidate so the provider sees no false certification claim.
        candidate = candidates.get(RetrievalChannel.DENSE) or exact
        if candidate is None:
            candidate = candidates.get(RetrievalChannel.LEXICAL) or candidates.get(
                RetrievalChannel.BM25
            )
        return ("validation_candidate", candidate) if candidate is not None else None
    # Lexical, BM25, and weak exact-token overlap are correlated sparse
    # signals, not three independent confirmations.
    families: set[str] = set()
    if channels & {
        RetrievalChannel.EXACT,
        RetrievalChannel.LEXICAL,
        RetrievalChannel.BM25,
    }:
        families.add("sparse")
    # Dense reranks a pool produced by sparse and structural retrieval.  It is
    # useful for ordering, but cannot independently certify its own input.
    structural_relation = str(structural.relation or "").lower() if structural else ""
    if (
        structural_endpoint_aligned
        and structural is not None
        and "cochange" not in structural_relation
    ):
        families.add("structural")
    if len(families) < 2:
        return None
    # Multi-channel rank support without a certified relation remains useful
    # for ordering, but it cannot authorize model-visible source context.
    return None


def _intent_priority(ranked: RankedFile, state: RetrievalState) -> int:
    """Return a mechanically derived workflow priority, never task labels."""

    if state.intent is not RetrievalIntent.VALIDATION_CONTEXT:
        return 0
    return 0 if _is_test_path(ranked.path) else 1


def _trajectory_novelty_priority(ranked: RankedFile, state: RetrievalState) -> int:
    """Prefer unseen paths only when fused relevance is otherwise tied."""

    already_active = {
        _normalize_path(path).lower()
        for path in (*state.active_paths, *state.changed_paths)
        if _normalize_path(path)
    }
    return 1 if ranked.path.lower() in already_active else 0


_DIRECT_DECISION_RELATIONS = frozenset(
    {
        "calls",
        "inverse:calls",
        "asserted_by",
        "inverse:asserted_by",
        "tested_by",
        "inverse:tested_by",
    }
)
_CHANGE_IMPACT_RELATIONS = frozenset(
    {"calls_transitive", "inverse:calls_transitive", *_DIRECT_DECISION_RELATIONS}
)


def _decision_relevance(
    candidate: RetrievalCandidate,
    *,
    support_kind: str,
    state: RetrievalState,
) -> str | None:
    """Separate evidence truth from usefulness to the current decision."""

    if support_kind == "validation_candidate":
        return "validation_test_candidate" if _is_test_path(candidate.path) else None
    if support_kind == "identity_only" or candidate.authority is EvidenceAuthority.IDENTITY_ONLY:
        return None
    relation = str(candidate.relation or "").strip().lower()
    if not relation or "cochange" in relation:
        return None
    if state.intent is RetrievalIntent.VALIDATION_CONTEXT:
        return (
            "validation_direct_relation"
            if relation
            in {
                "asserted_by",
                "inverse:asserted_by",
                "tested_by",
                "inverse:tested_by",
            }
            else None
        )
    if state.intent is RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE:
        return (
            "diagnostic_direct_relation"
            if relation in _DIRECT_DECISION_RELATIONS
            else None
        )
    if state.intent is RetrievalIntent.CHANGE_IMPACT:
        return "change_impact_relation" if relation in _CHANGE_IMPACT_RELATIONS else None
    if state.intent in {
        RetrievalIntent.IMPLEMENTATION_CONTEXT,
        RetrievalIntent.MISSING_CONTEXT,
    }:
        return (
            "implementation_direct_relation"
            if relation in _DIRECT_DECISION_RELATIONS
            else None
        )
    return None


class HybridRetriever:
    """Run independent channels, fuse files, then pack bounded new evidence."""

    def __init__(
        self,
        documents: Sequence[RepositoryDocument],
        *,
        structural_links: Sequence[StructuralLink] = (),
        dense_backend: DenseEmbeddingBackend | None = None,
        channels: Sequence[RetrievalChannelBackend] | None = None,
        token_counter: Callable[[str], int] = _default_token_counter,
        rrf_k: int = 60,
        dense_candidate_limit: int | None = None,
        dense_fallback_only: bool = False,
    ) -> None:
        documents = tuple(documents)
        registered_channels: tuple[RetrievalChannelBackend, ...] = (
            tuple(channels)
            if channels is not None
            else (
                ExactRetrievalChannel(documents),
                LexicalRetrievalChannel(documents),
                BM25RetrievalChannel(documents),
                StructuralRetrievalChannel(documents, structural_links),
                DenseRetrievalChannel(documents, dense_backend),
            )
        )
        if dense_fallback_only:
            registered_channels = tuple(
                channel
                for channel in registered_channels
                if not isinstance(channel, DenseRetrievalChannel)
            ) + tuple(
                channel
                for channel in registered_channels
                if isinstance(channel, DenseRetrievalChannel)
            )
        self._channels = registered_channels
        present = [channel.channel for channel in registered_channels]
        if len(present) != len(set(present)):
            raise ValueError("each retrieval channel may be registered at most once")
        self._token_counter = token_counter
        self._rrf_k = int(rrf_k)
        self._dense_candidate_limit = (
            None if dense_candidate_limit is None else max(1, int(dense_candidate_limit))
        )
        self._dense_fallback_only = bool(dense_fallback_only)
        self._dense_channel = next(
            (channel for channel in self._channels if isinstance(channel, DenseRetrievalChannel)),
            None,
        )

    def retrieve(
        self,
        state: RetrievalState,
        *,
        channel_limit: int = 100,
        top_k: int = 20,
        selection_limit: int = 3,
        token_budget: int = 1_200,
        character_budget: int | None = None,
    ) -> HybridRetrievalResult:
        started = time.perf_counter()
        normalized_character_budget = (
            None if character_budget is None else max(0, int(character_budget))
        )
        if (
            token_budget < 1
            or selection_limit < 1
            or normalized_character_budget == 0
        ):
            return HybridRetrievalResult(
                ranked_files=(),
                ranked_spans=(),
                selected_context=(),
                abstained=True,
                reason_codes=(
                    "context_character_budget_closed"
                    if normalized_character_budget == 0
                    else "context_budget_closed",
                ),
                channel_receipts=(),
                latency_ms=(time.perf_counter() - started) * 1_000.0,
                query_hash=state.query_hash,
                token_budget=max(0, token_budget),
                selected_token_count=0,
                character_budget=normalized_character_budget,
                selected_character_count=0,
                dense_fallback_only=self._dense_fallback_only,
            )
        channel_results: dict[RetrievalChannel, tuple[RetrievalCandidate, ...]] = {}
        receipts: list[ChannelReceipt] = []
        stale_candidates_rejected = 0
        for channel in self._channels:
            if self._dense_candidate_limit is not None and isinstance(
                channel, DenseRetrievalChannel
            ):
                non_dense = tuple(
                    result
                    for key, result in channel_results.items()
                    if key is not RetrievalChannel.DENSE
                )
                pool: list[str] = []
                seen: set[str] = set()
                if state.intent is RetrievalIntent.VALIDATION_CONTEXT:
                    validation_ranked = sorted(
                        reciprocal_rank_fusion(
                            {
                                key: result
                                for key, result in channel_results.items()
                                if key is not RetrievalChannel.DENSE
                            },
                            k=self._rrf_k,
                        ),
                        key=lambda row: (
                            _intent_priority(row, state),
                            -row.fused_score,
                            _trajectory_novelty_priority(row, state),
                            row.path.lower(),
                            row.path,
                        ),
                    )
                    for ranked in validation_ranked:
                        if not _is_test_path(ranked.path):
                            continue
                        key = ranked.path.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        pool.append(ranked.path)
                        if len(pool) >= self._dense_candidate_limit:
                            break
                width = max(
                    1,
                    self._dense_candidate_limit // max(1, len(non_dense)),
                )
                for rank in range(width):
                    for result in non_dense:
                        if rank >= len(result):
                            continue
                        path = result[rank].path
                        key = path.lower()
                        if key not in seen:
                            seen.add(key)
                            pool.append(path)
                            if len(pool) >= self._dense_candidate_limit:
                                break
                    if len(pool) >= self._dense_candidate_limit:
                        break
                channel.set_candidate_paths(
                    pool,
                    document_limit=self._dense_candidate_limit,
                )
            channel_started = time.perf_counter()
            failed = False
            reason = ""
            try:
                raw_candidates = tuple(channel.retrieve(state, limit=max(0, channel_limit)))
                candidates = tuple(
                    candidate
                    for candidate in raw_candidates
                    if candidate.source_revision == state.source_revision
                )
                stale_count = len(raw_candidates) - len(candidates)
                stale_candidates_rejected += stale_count
                if stale_count:
                    reason = f"stale_revision_rejected={stale_count}"
                if channel.channel is RetrievalChannel.DENSE:
                    reason = reason or str(getattr(channel, "availability_reason", "") or "")
            except Exception as exc:  # noqa: BLE001 - retrieval must fail open
                candidates = ()
                failed = True
                reason = f"{type(exc).__name__}: {exc}"[:300]
            channel_results[channel.channel] = candidates
            receipts.append(
                ChannelReceipt(
                    channel=channel.channel,
                    candidate_count=len(candidates),
                    failed=failed,
                    reason=reason,
                    latency_ms=(time.perf_counter() - channel_started) * 1_000.0,
                    available=not failed and reason != "backend_unavailable",
                    backend_identity=str(getattr(channel, "backend_identity", "") or ""),
                )
            )

        fused = tuple(
            sorted(
                reciprocal_rank_fusion(channel_results, k=self._rrf_k),
                key=lambda row: (
                    _intent_priority(row, state),
                    -row.fused_score,
                    _trajectory_novelty_priority(row, state),
                    row.path.lower(),
                    row.path,
                ),
            )
        )
        ranked_files = fused[: max(0, top_k)]
        ranked_spans = tuple(row.representative for row in ranked_files)
        exposed = set(state.previously_exposed_claims)
        selected: list[RetrievalCandidate] = []
        selected_rendered: list[str] = []
        selected_tokens = 0
        selected_characters = 0
        saw_supported = False
        saw_decision_irrelevant = False
        saw_origin_rejected = False
        saw_active_path_rejected = False
        skipped_budget = False
        skipped_character_budget = False
        skipped_duplicate = False
        for ranked in ranked_files:
            if len(selected) >= max(0, selection_limit):
                break
            supported = _delivery_support(ranked, state)
            if supported is None:
                continue
            support_kind, candidate = supported
            saw_supported = True
            if candidate.origin in {
                EvidenceOrigin.MODEL_AUTHORED,
                EvidenceOrigin.TASK_DELIVERABLE,
                EvidenceOrigin.GENERATED_ARTIFACT,
                EvidenceOrigin.EXTERNAL_RUNTIME,
            }:
                saw_origin_rejected = True
                continue
            active_or_changed = {
                _normalize_path(path).lower()
                for path in (*state.active_paths, *state.changed_paths)
                if _normalize_path(path)
            }
            if (
                candidate.path.lower() in active_or_changed
                and candidate.authority is not EvidenceAuthority.EXECUTION_OBSERVATION
            ):
                saw_active_path_rejected = True
                continue
            decision_relevance = _decision_relevance(
                candidate,
                support_kind=support_kind,
                state=state,
            )
            if decision_relevance is None:
                saw_decision_irrelevant = True
                continue
            candidate = replace(
                candidate,
                provenance=tuple(
                    dict.fromkeys(
                        (
                            *candidate.provenance,
                            f"delivery_support:{support_kind}",
                            f"decision_relevance:{decision_relevance}",
                            *(
                                f"support_channel:{channel.value}"
                                for channel, _rank in ranked.channel_ranks
                            ),
                        )
                    )
                ),
            )
            if support_kind == "validation_candidate":
                candidate = replace(
                    candidate,
                    provenance=tuple(
                        dict.fromkeys((*candidate.provenance, "validation_candidate"))
                    ),
                )
            if candidate.claim_hash in exposed:
                skipped_duplicate = True
                continue
            rendered = _render_candidate(candidate)
            combined = "\n\n".join((*selected_rendered, rendered))
            combined_tokens = self._token_counter(combined)
            if combined_tokens <= selected_tokens:
                continue
            if combined_tokens > max(0, token_budget):
                skipped_budget = True
                continue
            if (
                normalized_character_budget is not None
                and len(combined) > normalized_character_budget
            ):
                skipped_character_budget = True
                continue
            selected.append(candidate)
            selected_rendered.append(rendered)
            selected_tokens = combined_tokens
            selected_characters = len(combined)
            exposed.add(candidate.claim_hash)

        reasons: list[str] = []
        if not ranked_files:
            reasons.append("no_candidates")
        if stale_candidates_rejected:
            reasons.append("stale_candidates_rejected")
        if ranked_files and not saw_supported:
            reasons.append("insufficient_independent_support")
        if saw_decision_irrelevant and not selected:
            reasons.append("no_decision_relevant_evidence")
        if saw_origin_rejected:
            reasons.append("model_authored_context_rejected")
        if saw_active_path_rejected:
            reasons.append("active_path_context_rejected")
        if skipped_duplicate:
            reasons.append("already_visible_or_delivered")
        if skipped_budget:
            reasons.append("context_budget")
        if skipped_character_budget:
            reasons.append("context_character_budget")
        if selected:
            reasons.append("selected_bounded_context")
        elif saw_supported and not skipped_budget and not skipped_duplicate:
            reasons.append("no_complete_evidence")

        return HybridRetrievalResult(
            ranked_files=ranked_files,
            ranked_spans=ranked_spans,
            selected_context=tuple(selected),
            abstained=not selected,
            reason_codes=tuple(dict.fromkeys(reasons)),
            channel_receipts=tuple(receipts),
            latency_ms=(time.perf_counter() - started) * 1_000.0,
            query_hash=state.query_hash,
            token_budget=max(0, token_budget),
            selected_token_count=selected_tokens,
            character_budget=normalized_character_budget,
            selected_character_count=selected_characters,
            dense_fallback_only=self._dense_fallback_only,
        )


def build_preemptive_frame(
    result: HybridRetrievalResult,
    state: RetrievalState,
    *,
    trigger: str,
) -> PreemptiveRetrievalFrame | None:
    """Compile selected evidence without changing any legacy delivery stream."""

    if result.abstained or not result.selected_context:
        return None
    if result.query_hash != state.query_hash:
        return None
    rendered = "\n\n".join(_render_candidate(candidate) for candidate in result.selected_context)
    return PreemptiveRetrievalFrame(
        query_hash=result.query_hash,
        source_revision=state.source_revision,
        trigger=str(trigger or "unknown"),
        evidence=result.selected_context,
        rendered_text=rendered,
        token_count=result.selected_token_count,
        claim_hashes=tuple(candidate.claim_hash for candidate in result.selected_context),
    )


def filter_provider_known_context(
    result: HybridRetrievalResult,
    provider_messages: Sequence[Mapping[str, object]],
    *,
    token_counter: Callable[[str], int] = _default_token_counter,
) -> HybridRetrievalResult:
    """Remove source spans already present in the retained provider view."""

    visible_strings: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            visible_strings.append(" ".join(value.split()))
        elif isinstance(value, Mapping):
            for item in value.values():
                collect(item)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                collect(item)

    collect(provider_messages)
    compact_provider_text = "\n".join(visible_strings)
    selected = tuple(
        candidate
        for candidate in result.selected_context
        if not candidate.text.strip()
        or " ".join(candidate.text.strip().split()) not in compact_provider_text
    )
    if len(selected) == len(result.selected_context):
        return result
    rendered = "\n\n".join(_render_candidate(candidate) for candidate in selected)
    reasons = tuple(
        dict.fromkeys(
            (
                *(
                    reason
                    for reason in result.reason_codes
                    if reason != "selected_bounded_context"
                ),
                "provider_history_already_contains_evidence",
                *(("selected_bounded_context",) if selected else ()),
            )
        )
    )
    return replace(
        result,
        selected_context=selected,
        abstained=not selected,
        reason_codes=reasons,
        selected_token_count=token_counter(rendered) if rendered else 0,
        selected_character_count=len(rendered),
    )


__all__ = [
    "BM25RetrievalChannel",
    "ChannelReceipt",
    "DenseEmbeddingBackend",
    "DenseRetrievalChannel",
    "ExactRetrievalChannel",
    "EvidenceAuthority",
    "EvidenceOrigin",
    "HybridRetrievalResult",
    "HybridRetriever",
    "LexicalRetrievalChannel",
    "PreemptiveRetrievalFrame",
    "RankedFile",
    "RepositoryDocument",
    "RetrievalCandidate",
    "RetrievalChannel",
    "RetrievalChannelBackend",
    "RetrievalActionState",
    "RetrievalIntent",
    "RetrievalQueryPlan",
    "RetrievalState",
    "StructuralLink",
    "StructuralRetrievalChannel",
    "build_preemptive_frame",
    "filter_provider_known_context",
    "reciprocal_rank_fusion",
    "retrieval_query_terms",
]
