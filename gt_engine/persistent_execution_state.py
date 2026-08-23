"""Graph-first persistent execution state for the central Mini-SWE runtime.

The repository graph is the fact authority.  A single optional model bootstrap
may select and order immutable catalog IDs; it cannot create repository facts.
After bootstrap, this module is a deterministic state-transition engine used at
provider, preflight, postflight, and graph-refresh boundaries.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from gt_engine.decisive_derivation import (
    DecisiveDerivation,
    DecisiveStatus,
)
from gt_engine.hybrid_retrieval import (
    EvidenceAuthority,
    EvidenceOrigin,
    HybridRetrievalResult,
    RepositoryDocument,
    StructuralLink,
)
from gt_engine.preflight import ActionOperation, ProposedAction
from gt_engine.repository_intelligence import RepositoryEvidence
from gt_engine.thin_compiler import provider_material_relation

SELECT_CATALOG_TOOL_NAME = "select_catalog"
_BOOTSTRAP_SELECTION_KEYS = (
    "primary_focus_id",
    "ordered_item_ids",
    "risk_item_ids",
    "validation_item_ids",
)
_PES_ID_RE = re.compile(r"pes-[0-9a-f]{20}")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]", re.UNICODE)
_PATH_RE = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
_CERTIFIED_RELATIONS = frozenset(
    {
        "calls",
        "called_by",
        "imports",
        "imported_by",
        "implements",
        "implemented_by",
        "inherits",
        "inherited_by",
        "overrides",
        "overridden_by",
        "references",
        "referenced_by",
        "test_assertion",
        "verified_closure",
    }
)
_CERTIFIED_RELATION_ALIASES = {
    "asserted_by": "test_assertion",
    "tested_by": "test_assertion",
    "calls_transitive": "verified_closure",
}


def _certified_relation(link: StructuralLink) -> str:
    if not link.certified:
        return ""
    normalized = str(link.relation or "").strip().lower()
    normalized = _CERTIFIED_RELATION_ALIASES.get(normalized, normalized)
    return normalized if normalized in _CERTIFIED_RELATIONS else ""


def _provider_material_relation(relation: str) -> str:
    """Normalize a stored obligation relation for provider-materiality gating."""
    return provider_material_relation(relation)


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(str(part) for part in parts)
    return f"{prefix}-" + hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def build_select_catalog_tool(visible_item_ids: Iterable[str]) -> dict[str, Any]:
    """OpenAI function schema constrained to the visible catalog ID surface."""

    ids = [item for item in dict.fromkeys(visible_item_ids) if item]
    id_enum = ids or ["__empty_catalog__"]
    primary_enum = ["", *id_enum]
    id_schema: dict[str, Any] = {"type": "string", "enum": id_enum}
    return {
        "type": "function",
        "function": {
            "name": SELECT_CATALOG_TOOL_NAME,
            "description": (
                "Select existing catalog item IDs for the next execution focus. "
                "Do not invent paths, symbols, commands, or IDs."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_BOOTSTRAP_SELECTION_KEYS),
                "properties": {
                    "primary_focus_id": {"type": "string", "enum": primary_enum},
                    "ordered_item_ids": {"type": "array", "items": id_schema},
                    "risk_item_ids": {"type": "array", "items": id_schema},
                    "validation_item_ids": {"type": "array", "items": id_schema},
                },
            },
        },
    }


def attempted_bootstrap_item_ids(raw: Any) -> tuple[str, ...]:
    """Recover candidate IDs from valid JSON, a typed payload, or raw text."""

    payload: Any = raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return tuple(dict.fromkeys(_PES_ID_RE.findall(raw)))
    if not isinstance(payload, dict):
        return ()
    found: list[str] = []
    primary = payload.get("primary_focus_id")
    if isinstance(primary, str) and primary:
        found.append(primary)
    for key in ("ordered_item_ids", "risk_item_ids", "validation_item_ids"):
        values = payload.get(key) or []
        if isinstance(values, list):
            found.extend(
                item for item in values if isinstance(item, str) and item
            )
    found.extend(_PES_ID_RE.findall(json.dumps(payload, sort_keys=True)))
    return tuple(dict.fromkeys(found))


def bootstrap_args_preview(raw: Any, *, limit: int = 480) -> str:
    """Bounded ID-only preview. Never persist secrets or source bytes."""

    ids = attempted_bootstrap_item_ids(raw)
    rendered = json.dumps({"attempted_item_ids": list(ids)}, separators=(",", ":"))
    return rendered[: max(1, int(limit))]


def _bounded(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _complete_excerpt(value: Any, limit: int = 1_200) -> str:
    """Return complete source lines within a fixed byte-independent bound."""

    selected: list[str] = []
    used = 0
    for line in str(value or "").replace("\x00", "").splitlines():
        required = len(line) + (1 if selected else 0)
        if used + required > max(0, int(limit)):
            break
        selected.append(line)
        used += required
    return "\n".join(selected).strip()


def _path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if raw.startswith("/app/"):
        raw = raw[5:]
    while raw.startswith("./"):
        raw = raw[2:]
    normalized = posixpath.normpath(raw) if raw else ""
    return "" if normalized in {"", ".", ".."} or normalized.startswith("../") else normalized


def _default_token_counter(text: str) -> int:
    """Deterministic packing unit; provider-side accounting remains authoritative."""

    return len(_TOKEN_RE.findall(str(text or "")))


class StateFieldAuthority(StrEnum):
    IMMUTABLE_INPUT = "immutable_input"
    DETERMINISTIC_DERIVED = "deterministic_derived"
    GENERATIVE_BOOTSTRAP = "generative_bootstrap"
    DETERMINISTIC_MUTABLE = "deterministic_mutable"
    EXECUTOR_OBSERVED = "executor_observed"
    BOOTSTRAP_SELECTED = "bootstrap_selected"


class CatalogItemKind(StrEnum):
    FOCUS = "focus"
    DEPENDENCY = "dependency"
    VALIDATION = "validation"
    DELIVERABLE = "deliverable"


class BootstrapStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    SELECTED = "selected"
    INVALID_FALLBACK = "invalid_fallback"
    ERROR_FALLBACK = "error_fallback"
    NOT_APPLICABLE = "not_applicable"


class BootstrapMode(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    GENERATIVE_SELECTED = "generative_selected"
    DETERMINISTIC_SELECTED = "deterministic_selected"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class StatePhase(StrEnum):
    LOCALIZING = "localizing"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    READY_TO_SUBMIT = "ready_to_submit"


class ObligationStatus(StrEnum):
    OPEN = "open"
    SATISFIED = "satisfied"
    INVALIDATED = "invalidated"


class StateValidationStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class CompletionReadiness(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"


class CurrentFocusKind(StrEnum):
    REPOSITORY_SOURCE = "repository_source"
    TASK_DELIVERABLE = "task_deliverable"
    EXTERNAL_RUNTIME = "external_runtime"
    ARTIFACT = "artifact"


class ContextFrameKind(StrEnum):
    NONE = "none"
    INITIAL = "initial"
    CORE = "core"
    DELTA = "delta"
    CRITICAL = "critical"


PERSISTENT_STATE_FIELD_AUTHORITIES: dict[str, StateFieldAuthority] = {
    "state_id": StateFieldAuthority.DETERMINISTIC_DERIVED,
    "task_digest": StateFieldAuthority.IMMUTABLE_INPUT,
    "source_revision": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "graph_source_revision": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "graph_revision": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "graph_current": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "phase": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "bootstrap_status": StateFieldAuthority.DETERMINISTIC_DERIVED,
    "bootstrap_mode": StateFieldAuthority.DETERMINISTIC_DERIVED,
    "primary_focus_id": StateFieldAuthority.BOOTSTRAP_SELECTED,
    "ordered_item_ids": StateFieldAuthority.BOOTSTRAP_SELECTED,
    "risk_item_ids": StateFieldAuthority.BOOTSTRAP_SELECTED,
    "validation_item_ids": StateFieldAuthority.BOOTSTRAP_SELECTED,
    "current_focus_id": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "current_focus": StateFieldAuthority.EXECUTOR_OBSERVED,
    "files_inspected": StateFieldAuthority.EXECUTOR_OBSERVED,
    "files_modified": StateFieldAuthority.EXECUTOR_OBSERVED,
    "obligations": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "observed_validation": StateFieldAuthority.EXECUTOR_OBSERVED,
    "declared_validation": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "completion_readiness": StateFieldAuthority.DETERMINISTIC_MUTABLE,
    "current_failure": StateFieldAuthority.EXECUTOR_OBSERVED,
}


@dataclass(frozen=True, slots=True)
class BootstrapCatalogItem:
    item_id: str
    kind: CatalogItemKind
    label: str
    path: str = ""
    symbol: str = ""
    relation: str = ""
    anchors: tuple[str, ...] = ()
    required: bool = False
    certified: bool = True
    authority: StateFieldAuthority = StateFieldAuthority.DETERMINISTIC_DERIVED
    retrieval_rank: int = 0
    support_channels: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    source_start_line: int = 0
    source_end_line: int = 0
    source_claim_id: str = ""
    source_excerpt: str = ""
    origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY
    evidence_authority: EvidenceAuthority = EvidenceAuthority.IDENTITY_ONLY
    origin_revision: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "label": self.label,
            "path": self.path,
            "symbol": self.symbol,
            "relation": self.relation,
            "anchors": list(self.anchors),
            "required": self.required,
            "certified": self.certified,
            "authority": self.authority.value,
            "retrieval_rank": self.retrieval_rank,
            "support_channels": list(self.support_channels),
            "provenance": list(self.provenance),
            "source_start_line": self.source_start_line,
            "source_end_line": self.source_end_line,
            "source_claim_id": self.source_claim_id,
            "origin": self.origin.value,
            "evidence_authority": self.evidence_authority.value,
            "origin_revision": self.origin_revision,
        }


@dataclass(frozen=True, slots=True)
class BootstrapCatalog:
    source_revision: str
    graph_source_revision: str
    graph_revision: str
    items: tuple[BootstrapCatalogItem, ...]
    complete: bool
    reason_codes: tuple[str, ...] = ()

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.persistent_bootstrap_catalog.v1",
            "source_revision": self.source_revision,
            "graph_source_revision": self.graph_source_revision,
            "graph_revision": self.graph_revision,
            "complete": self.complete,
            "reason_codes": list(self.reason_codes),
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class BootstrapSelection:
    valid: bool
    primary_focus_id: str = ""
    ordered_item_ids: tuple[str, ...] = ()
    risk_item_ids: tuple[str, ...] = ()
    validation_item_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "primary_focus_id": self.primary_focus_id,
            "ordered_item_ids": list(self.ordered_item_ids),
            "risk_item_ids": list(self.risk_item_ids),
            "validation_item_ids": list(self.validation_item_ids),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class StateObligation:
    obligation_id: str
    kind: str
    path: str
    relation: str
    source_path: str
    source_origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY
    path_origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY
    source_origin_revision: str = ""
    path_origin_revision: str = ""
    evidence_authority: EvidenceAuthority = EvidenceAuthority.CERTIFIED_RELATION
    blocking: bool = False
    status: ObligationStatus = ObligationStatus.OPEN
    opened_revision: str = ""
    satisfied_revision: str = ""
    authority: StateFieldAuthority = StateFieldAuthority.DETERMINISTIC_MUTABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "path": self.path,
            "relation": self.relation,
            "source_path": self.source_path,
            "source_origin": self.source_origin.value,
            "path_origin": self.path_origin.value,
            "source_origin_revision": self.source_origin_revision,
            "path_origin_revision": self.path_origin_revision,
            "evidence_authority": self.evidence_authority.value,
            "blocking": self.blocking,
            "status": self.status.value,
            "opened_revision": self.opened_revision,
            "satisfied_revision": self.satisfied_revision,
            "authority": self.authority.value,
        }


@dataclass(frozen=True, slots=True)
class StateValidation:
    status: StateValidationStatus = StateValidationStatus.UNKNOWN
    command: str = ""
    source_revision: str = ""
    action_id: str = ""
    declared_check_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "command": self.command,
            "source_revision": self.source_revision,
            "action_id": self.action_id,
            "declared_check_id": self.declared_check_id,
        }


@dataclass(frozen=True, slots=True)
class CurrentFocus:
    path: str
    kind: CurrentFocusKind
    origin: EvidenceOrigin
    source_revision: str
    origin_revision: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "origin": self.origin.value,
            "source_revision": self.source_revision,
            "origin_revision": self.origin_revision,
        }


@dataclass(frozen=True, slots=True)
class StateFailure:
    action_id: str
    operation: str
    diagnostic: str
    source_revision: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "operation": self.operation,
            "diagnostic": self.diagnostic,
            "source_revision": self.source_revision,
        }


@dataclass(frozen=True, slots=True)
class PersistentExecutionState:
    state_id: str
    task_digest: str
    version: int
    source_revision: str
    graph_source_revision: str
    graph_revision: str
    graph_current: bool
    phase: StatePhase
    bootstrap_status: BootstrapStatus
    bootstrap_mode: BootstrapMode = BootstrapMode.DETERMINISTIC_FALLBACK
    primary_focus_id: str = ""
    current_focus_id: str = ""
    current_focus: CurrentFocus | None = None
    ordered_item_ids: tuple[str, ...] = ()
    risk_item_ids: tuple[str, ...] = ()
    validation_item_ids: tuple[str, ...] = ()
    files_inspected: tuple[str, ...] = ()
    files_modified: tuple[str, ...] = ()
    obligations: tuple[StateObligation, ...] = ()
    observed_validation: StateValidation = StateValidation()
    declared_validation: StateValidation = StateValidation()
    completion_readiness: CompletionReadiness = CompletionReadiness.NOT_READY
    current_failure: StateFailure | None = None
    last_transition: str = "initialized"

    @property
    def current_focus_path(self) -> str:
        return self.current_focus.path if self.current_focus is not None else ""

    @property
    def validation(self) -> StateValidation:
        """Compatibility view: execution observation, never completion authority."""

        return self.observed_validation

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.persistent_execution_state.v1",
            "state_id": self.state_id,
            "task_digest": self.task_digest,
            "version": self.version,
            "source_revision": self.source_revision,
            "graph_source_revision": self.graph_source_revision,
            "graph_revision": self.graph_revision,
            "graph_current": self.graph_current,
            "phase": self.phase.value,
            "bootstrap_status": self.bootstrap_status.value,
            "bootstrap_mode": self.bootstrap_mode.value,
            "primary_focus_id": self.primary_focus_id,
            "current_focus_id": self.current_focus_id,
            "current_focus_path": self.current_focus_path,
            "current_focus": self.current_focus.as_dict() if self.current_focus else None,
            "ordered_item_ids": list(self.ordered_item_ids),
            "risk_item_ids": list(self.risk_item_ids),
            "validation_item_ids": list(self.validation_item_ids),
            "files_inspected": list(self.files_inspected),
            "files_modified": list(self.files_modified),
            "obligations": [item.as_dict() for item in self.obligations],
            "validation": self.observed_validation.as_dict(),
            "observed_validation": self.observed_validation.as_dict(),
            "declared_validation": self.declared_validation.as_dict(),
            "completion_readiness": self.completion_readiness.value,
            "current_failure": (
                self.current_failure.as_dict() if self.current_failure is not None else None
            ),
            "last_transition": self.last_transition,
            "field_authority": {
                key: value.value for key, value in PERSISTENT_STATE_FIELD_AUTHORITIES.items()
            },
        }


@dataclass(frozen=True, slots=True)
class PreflightStateProjection:
    action_id: str
    considered: bool
    operation: str
    target_paths: tuple[str, ...]
    open_obligation_ids: tuple[str, ...]
    blocking_obligation_ids: tuple[str, ...]
    material_contradiction: bool
    reason_codes: tuple[str, ...]
    state_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "considered": self.considered,
            "operation": self.operation,
            "target_paths": list(self.target_paths),
            "open_obligation_ids": list(self.open_obligation_ids),
            "blocking_obligation_ids": list(self.blocking_obligation_ids),
            "material_contradiction": self.material_contradiction,
            "reason_codes": list(self.reason_codes),
            "state_version": self.state_version,
        }


@dataclass(frozen=True, slots=True)
class PersistentContextFrame:
    kind: ContextFrameKind
    rendered_text: str
    claim_ids: tuple[str, ...]
    state_version: int
    source_revision: str
    provider_call: int
    token_count: int
    reason_codes: tuple[str, ...] = ()
    selected_evidence: tuple[dict[str, Any], ...] = ()
    claim_metadata: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "rendered_text": self.rendered_text,
            "claim_ids": list(self.claim_ids),
            "state_version": self.state_version,
            "source_revision": self.source_revision,
            "provider_call": self.provider_call,
            "token_count": self.token_count,
            "reason_codes": list(self.reason_codes),
            "selected_evidence": [dict(item) for item in self.selected_evidence],
            "claim_metadata": [dict(item) for item in self.claim_metadata],
        }


def _catalog_item(
    kind: CatalogItemKind,
    label: str,
    *,
    path: str = "",
    symbol: str = "",
    relation: str = "",
    anchors: Iterable[str] = (),
    required: bool = False,
    certified: bool = True,
    retrieval_rank: int = 0,
    support_channels: tuple[str, ...] = (),
    provenance: tuple[str, ...] = (),
    source_start_line: int = 0,
    source_end_line: int = 0,
    source_claim_id: str = "",
    source_excerpt: str = "",
    origin: EvidenceOrigin = EvidenceOrigin.PREEXISTING_REPOSITORY,
    evidence_authority: EvidenceAuthority = EvidenceAuthority.IDENTITY_ONLY,
    origin_revision: str = "",
) -> BootstrapCatalogItem:
    normalized_path = _path(path)
    normalized_anchors = tuple(
        dict.fromkeys(_bounded(anchor, 240) for anchor in anchors if _bounded(anchor, 240))
    )
    clean_label = _bounded(label, 280)
    item_id = _stable_id(
        "pes",
        kind.value,
        normalized_path,
        _bounded(symbol, 160),
        _bounded(relation, 80),
        clean_label,
        *normalized_anchors,
    )
    return BootstrapCatalogItem(
        item_id=item_id,
        kind=kind,
        label=clean_label,
        path=normalized_path,
        symbol=_bounded(symbol, 160),
        relation=_bounded(relation, 80),
        anchors=normalized_anchors,
        required=bool(required),
        certified=bool(certified),
        retrieval_rank=max(0, int(retrieval_rank)),
        support_channels=tuple(
            dict.fromkeys(_bounded(channel, 40) for channel in support_channels if channel)
        ),
        provenance=tuple(dict.fromkeys(_bounded(item, 160) for item in provenance if item))[:16],
        source_start_line=max(0, int(source_start_line)),
        source_end_line=max(0, int(source_end_line)),
        source_claim_id=str(source_claim_id or ""),
        source_excerpt=_complete_excerpt(source_excerpt),
        origin=origin,
        evidence_authority=evidence_authority,
        origin_revision=str(origin_revision or ""),
    )


def _is_hybrid_ranked_catalog_item(item: BootstrapCatalogItem) -> bool:
    return int(item.retrieval_rank) > 0 and "hybrid_ranked_candidate" in item.provenance


def _pack_catalog_items(
    ordered: Sequence[tuple[int, BootstrapCatalogItem]],
    max_items: int,
) -> tuple[BootstrapCatalogItem, ...]:
    """Keep required, hybrid-ranked, and certified-relation rows in the ceiling.

    Certified imports/calls can exceed ``max_items`` on a large graph. Hybrid
    ranks must still occupy catalog slots: they are the bootstrap selection
    surface, and the release gate requires at least one when retrieval ranked
    files exist. Required rows stay first; certified relations fill the
    remainder after hybrid ranks.
    """

    limit = max(1, int(max_items))
    selected: dict[str, BootstrapCatalogItem] = {}

    def take(items: Iterable[BootstrapCatalogItem]) -> None:
        for item in items:
            if item.item_id not in selected and len(selected) < limit:
                selected[item.item_id] = item

    take(item for _, item in ordered if item.required)
    take(item for _, item in ordered if _is_hybrid_ranked_catalog_item(item))
    take(
        item
        for _, item in ordered
        if item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
    )
    take(item for _, item in ordered)
    return tuple(item for _, item in ordered if item.item_id in selected)


def build_bootstrap_catalog(
    *,
    instruction: str,
    evidence: RepositoryEvidence,
    documents: tuple[RepositoryDocument, ...],
    structural_links: tuple[StructuralLink, ...],
    source_revision: str,
    graph_revision: str,
    repository_complete: bool,
    graph_source_revision: str | None = None,
    explicit_checks: tuple[str, ...] = (),
    task_deliverables: tuple[str, ...] = (),
    initial_retrieval: HybridRetrievalResult | None = None,
    max_items: int = 32,
    allow_empty_catalog: bool = False,
) -> BootstrapCatalog:
    """Build the immutable selection surface after a complete graph exists."""

    bound_graph_source_revision = str(graph_source_revision or source_revision)
    reasons: list[str] = []
    if not repository_complete:
        reasons.append("repository_corpus_incomplete")
    if not evidence.substrate_ready:
        reasons.append("repository_substrate_not_ready")
    if not source_revision or not graph_revision:
        reasons.append("revision_missing")
    if evidence.source_revision and evidence.source_revision != bound_graph_source_revision:
        reasons.append("evidence_source_revision_mismatch")
    if reasons:
        return BootstrapCatalog(
            source_revision=source_revision,
            graph_source_revision=bound_graph_source_revision,
            graph_revision=graph_revision,
            items=(),
            complete=False,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    candidates: dict[str, tuple[int, BootstrapCatalogItem]] = {}

    def add(priority: int, item: BootstrapCatalogItem) -> None:
        prior = candidates.get(item.item_id)
        if prior is None or priority < prior[0]:
            candidates[item.item_id] = (priority, item)

    for command in explicit_checks:
        clean = _bounded(command, 280)
        if clean:
            add(
                0,
                _catalog_item(
                    CatalogItemKind.VALIDATION,
                    f"Required validation: {clean}",
                    anchors=(clean,),
                    required=True,
                    evidence_authority=EvidenceAuthority.EXECUTION_OBSERVATION,
                ),
            )
    for command in evidence.project_checks:
        clean = _bounded(command, 280)
        if clean and clean not in explicit_checks:
            add(
                4,
                _catalog_item(
                    CatalogItemKind.VALIDATION,
                    f"Project validation candidate: {clean}",
                    anchors=(clean,),
                    required=False,
                    evidence_authority=EvidenceAuthority.EXECUTION_OBSERVATION,
                ),
            )
    for raw_path in task_deliverables:
        normalized = _path(raw_path)
        if normalized:
            add(
                0,
                _catalog_item(
                    CatalogItemKind.DELIVERABLE,
                    f"Required deliverable: {normalized}",
                    path=normalized,
                    anchors=(normalized,),
                    required=True,
                    origin=EvidenceOrigin.TASK_DELIVERABLE,
                    evidence_authority=EvidenceAuthority.EXECUTION_OBSERVATION,
                ),
            )

    for row in (*evidence.anchors, *evidence.definitions):
        path = _path(row.get("path"))
        symbol = _bounded(row.get("symbol"), 160)
        if not path:
            continue
        line = max(1, int(row.get("line") or row.get("start_line") or 1))
        anchor = f"{path}:{line}" + (f"#{symbol}" if symbol else "")
        add(
            20,
            _catalog_item(
                CatalogItemKind.FOCUS,
                f"Candidate implementation {anchor}",
                path=path,
                symbol=symbol,
                anchors=(anchor,),
            ),
        )

    for row in evidence.callers:
        path = _path(row.get("path"))
        symbol = _bounded(row.get("symbol"), 160)
        if path:
            add(
                21,
                _catalog_item(
                    CatalogItemKind.DEPENDENCY,
                    f"Certified caller {path}" + (f"#{symbol}" if symbol else ""),
                    path=path,
                    symbol=symbol,
                    relation="calls",
                    anchors=(path, symbol),
                    evidence_authority=EvidenceAuthority.CERTIFIED_RELATION,
                ),
            )

    documents_by_path: dict[str, list[RepositoryDocument]] = {}
    for document in documents:
        documents_by_path.setdefault(_path(document.path), []).append(document)
    document_by_path = {
        path: path_documents[0]
        for path, path_documents in documents_by_path.items()
        if path_documents
    }

    def source_document(item: BootstrapCatalogItem) -> RepositoryDocument | None:
        path_documents = documents_by_path.get(item.path, ())
        wanted_symbol = _bounded(item.symbol, 160)
        if wanted_symbol:
            exact = next(
                (
                    document
                    for document in path_documents
                    if _bounded(document.symbol, 160) == wanted_symbol
                ),
                None,
            )
            if exact is not None:
                return exact
        return path_documents[0] if len(path_documents) == 1 else None

    # The accepted HybridRetriever is the task-localization authority shared
    # with the live provider-boundary path.  Its ranking is not a certified
    # claim that a file must be changed; it is a bounded set of current-checkout
    # candidates from which the single bootstrap call may select.  Source
    # identity is mechanical, while relevance remains explicitly ranked.
    if initial_retrieval is not None:
        if not initial_retrieval.query_hash:
            reasons.append("initial_retrieval_query_missing")
        for rank, ranked in enumerate(initial_retrieval.ranked_files[:16], start=1):
            candidate = ranked.representative
            normalized = _path(candidate.path)
            if (
                not normalized
                or normalized not in document_by_path
                or candidate.source_revision != bound_graph_source_revision
            ):
                continue
            line = max(1, int(candidate.start_line or 1))
            symbol = _bounded(candidate.symbol, 160)
            anchor = f"{normalized}:{line}" + (f"#{symbol}" if symbol else "")
            channels = tuple(channel.value for channel, _ in ranked.channel_ranks)
            # Hybrid retrieval is the task-conditioned localization authority.
            # Replace an equivalent generic graph candidate so one path/symbol
            # cannot consume two bootstrap slots and the ranked evidence is not
            # hidden behind repository-order anchors.
            for item_id, (_, existing) in tuple(candidates.items()):
                if (
                    existing.kind is CatalogItemKind.FOCUS
                    and existing.path == normalized
                    and existing.symbol == symbol
                ):
                    candidates.pop(item_id, None)
            add(
                min(18, 1 + rank),
                _catalog_item(
                    CatalogItemKind.FOCUS,
                    f"Hybrid-ranked repository candidate #{rank}: {anchor}",
                    path=normalized,
                    symbol=symbol,
                    anchors=(anchor,),
                    certified=False,
                    retrieval_rank=rank,
                    support_channels=channels,
                    provenance=tuple(
                        dict.fromkeys(
                            (
                                *candidate.provenance,
                                *ranked.provenance,
                                "hybrid_ranked_candidate",
                            )
                        )
                    ),
                    origin=candidate.origin,
                    evidence_authority=EvidenceAuthority.RANKING_SUPPORT,
                    origin_revision=candidate.origin_revision,
                ),
            )
    focus_paths = {
        item.path
        for _, item in candidates.values()
        if item.kind is CatalogItemKind.FOCUS and item.path
    }
    for link in structural_links:
        normalized_relation = _certified_relation(link)
        if not normalized_relation:
            continue
        if link.source_path not in focus_paths and link.target_path not in focus_paths:
            continue
        for path, symbol, role in (
            (link.source_path, link.source_symbol, "source"),
            (link.target_path, link.target_symbol, "target"),
        ):
            normalized = _path(path)
            if not normalized or normalized not in document_by_path:
                continue
            add(
                22,
                _catalog_item(
                    CatalogItemKind.DEPENDENCY,
                    f"Certified {normalized_relation} {role}: {normalized}"
                    + (f"#{symbol}" if symbol else ""),
                    path=normalized,
                    symbol=_bounded(symbol, 160),
                    relation=normalized_relation,
                    anchors=(normalized, _bounded(symbol, 160)),
                    evidence_authority=EvidenceAuthority.CERTIFIED_RELATION,
                ),
            )

    # Exact task paths may seed a graph-backed focus even when task-conditioned
    # evidence is empty.  The path must exist in the certified corpus.
    for match in _PATH_RE.finditer(str(instruction or "").replace("\\", "/")):
        normalized = _path(match.group(0))
        document = document_by_path.get(normalized)
        if document is None:
            continue
        add(
            1,
            _catalog_item(
                CatalogItemKind.FOCUS,
                f"Task-named repository path: {normalized}",
                path=normalized,
                symbol=_bounded(document.symbol, 160),
                anchors=(normalized,),
            ),
        )

    # Source bytes never enter the bootstrap selection request. They remain in
    # the immutable host-owned catalog so a valid selected ID can be resolved
    # to exactly one checkout-backed span for the first executor request.
    enriched: dict[str, tuple[int, BootstrapCatalogItem]] = {}
    for item_id, (priority, item) in candidates.items():
        document = source_document(item)
        if document is None or not item.path:
            enriched[item_id] = (priority, item)
            continue
        excerpt = _complete_excerpt(document.text)
        start_line = max(1, int(document.start_line or 1))
        end_line = max(start_line, int(document.end_line or start_line))
        claim_id = (
            _stable_id(
                "bootstrap-source",
                item.path,
                str(start_line),
                str(end_line),
                item.symbol,
                excerpt,
            )
            if excerpt
            else ""
        )
        enriched[item_id] = (
            priority,
            replace(
                item,
                source_start_line=start_line,
                source_end_line=end_line,
                source_claim_id=claim_id,
                source_excerpt=excerpt,
                origin=document.origin,
                evidence_authority=(
                    item.evidence_authority
                    if item.kind is CatalogItemKind.DEPENDENCY
                    else EvidenceAuthority.RANKING_SUPPORT
                    if item.retrieval_rank
                    else EvidenceAuthority.IDENTITY_ONLY
                ),
                origin_revision=document.origin_revision,
            ),
        )
    candidates = enriched

    ordered = sorted(
        candidates.values(),
        key=lambda row: (
            row[0],
            not row[1].required,
            row[1].path,
            row[1].symbol,
            row[1].relation,
            row[1].item_id,
        ),
    )
    items = _pack_catalog_items(ordered, max_items)
    if not items and allow_empty_catalog and not reasons:
        # A graph can be valid while exposing no symbol/document candidates
        # (for example a shell-only repository).  Keep the catalog explicitly
        # no-op rather than treating persistent-state initialization as a
        # runtime failure.  The sentinel has no path or repository claim; it
        # only makes the empty selection surface mechanically selectable and
        # preserves the declared empty_catalog limitation in the receipt.
        items = (
            BootstrapCatalogItem(
                item_id="__empty_catalog__",
                kind=CatalogItemKind.FOCUS,
                label="No certified symbol-level repository facts are available",
                certified=True,
                evidence_authority=EvidenceAuthority.IDENTITY_ONLY,
                origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
                origin_revision=bound_graph_source_revision,
            ),
        )
    reason_codes = tuple(
        dict.fromkeys(reasons or (("empty_catalog",) if not ordered else ()))
    )
    return BootstrapCatalog(
        source_revision=source_revision,
        graph_source_revision=bound_graph_source_revision,
        graph_revision=graph_revision,
        items=items,
        complete=bool(items) and not reasons,
        reason_codes=reason_codes,
    )


def parse_bootstrap_selection(
    raw: Any,
    catalog: BootstrapCatalog,
    *,
    visible_item_ids: frozenset[str] | None = None,
) -> BootstrapSelection:
    """Accept only catalog identifiers from a typed select_catalog payload."""

    if not catalog.complete:
        return BootstrapSelection(False, reason_codes=("catalog_incomplete",))
    value: Any = raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
        value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return BootstrapSelection(False, reason_codes=("invalid_json",))
    if not isinstance(value, dict):
        return BootstrapSelection(False, reason_codes=("invalid_shape",))
    # The retired Bash envelope stuffed JSON into ``command``. That shape is
    # never a valid select_catalog payload.
    if "command" in value and not any(key in value for key in _BOOTSTRAP_SELECTION_KEYS):
        return BootstrapSelection(False, reason_codes=("unknown_tool",))
    extra_keys = set(value) - set(_BOOTSTRAP_SELECTION_KEYS)
    if extra_keys:
        return BootstrapSelection(False, reason_codes=("unknown_field",))

    def ids(key: str, limit: int) -> tuple[str, ...] | None:
        raw_ids = value.get(key, [])
        if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
            return None
        result = tuple(dict.fromkeys(item for item in raw_ids if item))
        return result if len(result) <= limit else None

    primary = value.get("primary_focus_id", "")
    ordered = ids("ordered_item_ids", 16)
    risks = ids("risk_item_ids", 8)
    validations = ids("validation_item_ids", 8)
    if not isinstance(primary, str) or ordered is None or risks is None or validations is None:
        return BootstrapSelection(False, reason_codes=("invalid_shape",))
    referenced = tuple(
        dict.fromkeys((primary, *(ordered or ()), *(risks or ()), *(validations or ())))
    )
    if any(item and item not in catalog.item_ids for item in referenced):
        return BootstrapSelection(False, reason_codes=("unknown_catalog_id",))
    if visible_item_ids is not None and any(
        item and item not in visible_item_ids for item in referenced
    ):
        return BootstrapSelection(False, reason_codes=("unshown_catalog_id",))
    item_by_id = {item.item_id: item for item in catalog.items}
    if primary and item_by_id[primary].kind is CatalogItemKind.VALIDATION:
        return BootstrapSelection(False, reason_codes=("invalid_primary_focus",))
    kept_validations = tuple(
        item_id
        for item_id in validations
        if item_by_id[item_id].kind is CatalogItemKind.VALIDATION
    )
    return BootstrapSelection(
        True,
        primary_focus_id=primary,
        ordered_item_ids=ordered or (),
        risk_item_ids=risks or (),
        validation_item_ids=kept_validations,
    )


def deterministic_bootstrap_fallback(
    catalog: BootstrapCatalog,
    *,
    status: BootstrapStatus = BootstrapStatus.INVALID_FALLBACK,
) -> tuple[BootstrapSelection, BootstrapStatus]:
    # Generative focus is optional. Deterministically retain task requirements
    # and the highest-ranked safe repository identity so state remains live.
    primary_item = next(
        (
            item
            for item in catalog.items
            if item.kind in {CatalogItemKind.FOCUS, CatalogItemKind.DEPENDENCY}
            and item.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
        ),
        None,
    )
    primary = primary_item.item_id if primary_item is not None else ""
    ordered = tuple(
        item.item_id for item in catalog.items if item.required or item.item_id == primary
    )
    validations = tuple(
        item.item_id for item in catalog.items if item.kind is CatalogItemKind.VALIDATION
    )
    return (
        BootstrapSelection(
            valid=False,
            primary_focus_id=primary,
            ordered_item_ids=ordered[:16],
            validation_item_ids=validations[:8],
            reason_codes=(status.value,),
        ),
        status,
    )


def deterministic_bootstrap_selection(
    catalog: BootstrapCatalog,
) -> BootstrapSelection:
    """Select only immutable catalog facts without a model call.

    This is an intentional treatment mode, distinct from invalid/error
    fallback.  It preserves every required validation and deliverable item,
    chooses the first certified pre-existing focus/dependency as the primary,
    and never invents risks or repository facts.
    """

    if not catalog.complete:
        return BootstrapSelection(False, reason_codes=("catalog_incomplete",))
    primary_item = next(
        (
            item
            for item in catalog.items
            if item.kind in {CatalogItemKind.FOCUS, CatalogItemKind.DEPENDENCY}
            and item.certified
            and item.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
        ),
        None,
    )
    primary = primary_item.item_id if primary_item is not None else ""
    required = tuple(item.item_id for item in catalog.items if item.required)
    ordered = tuple(dict.fromkeys((*required, *(item_id for item_id in (primary,) if item_id))))
    validations = tuple(
        item.item_id
        for item in catalog.items
        if item.kind is CatalogItemKind.VALIDATION
    )[:8]
    required_ids = {item.item_id for item in catalog.items if item.required}
    if not required_ids.issubset(set(ordered[:16]) | set(validations)):
        return BootstrapSelection(False, reason_codes=("required_catalog_item_missing",))
    return BootstrapSelection(
        valid=True,
        primary_focus_id=primary,
        ordered_item_ids=ordered[:16],
        risk_item_ids=(),
        validation_item_ids=validations,
        reason_codes=("deterministic_selected",),
    )


def build_bootstrap_messages(
    *,
    task: str,
    catalog: BootstrapCatalog,
    max_input_tokens: int = 2_000,
) -> list[dict[str, str]]:
    """Create a bounded one-call selection request using select_catalog."""

    compact_items = [
        {
            "id": item.item_id,
            "kind": item.kind.value,
            "label": item.label,
            "required": item.required,
        }
        for item in catalog.items
    ]
    system = (
        "You select an execution focus from repository-certified entities and explicitly "
        "labeled hybrid-ranked candidates. Candidate relevance is not a requirement. "
        "You may order IDs but may not add facts, paths, symbols, or commands. "
        "Use the select_catalog tool only. It is not executed as a shell command."
    )

    def render_user(items: list[dict[str, Any]], task_excerpt: str) -> str:
        has_validation = any(item.get("kind") == CatalogItemKind.VALIDATION.value for item in items)
        validation_rule = (
            "Leave validation_item_ids empty if no validation items are listed."
            if not has_validation
            else "validation_item_ids may contain only listed validation IDs."
        )
        return (
            "TASK\n"
            + task_excerpt
            + "\n\nCERTIFIED CATALOG\n"
            + json.dumps(items, sort_keys=True, separators=(",", ":"))
            + "\n\nSelect only catalog IDs. Return exactly one select_catalog tool call with "
            "primary_focus_id, ordered_item_ids, risk_item_ids, and validation_item_ids. "
            "An empty primary_focus_id is allowed when the ranked candidates do not justify a "
            "focus. "
            + validation_rule
            + " Do not invent IDs, paths, or symbols. Do not emit shell code or a bash command."
        )

    task_excerpt = _bounded(task, 1_200)
    user = render_user(compact_items, task_excerpt)
    # Fixed byte ceiling is an independent transport bound, not a token estimate.
    # One UTF-8 byte is a conservative upper bound on one provider token.  The
    # byte ceiling therefore makes the declared input-token limit true even
    # when the exact tokenizer is unavailable at catalog construction time.
    byte_ceiling = max(1_024, int(max_input_tokens))
    selected_items = list(compact_items)
    task_limits = (1_200, 600, 300, 0)
    for task_limit in task_limits:
        candidate_task = task_excerpt[:task_limit]
        user = render_user(selected_items, candidate_task)
        if len(system.encode("utf-8")) + len(user.encode("utf-8")) <= byte_ceiling:
            break
    else:
        candidate_task = task_excerpt[:300]
        while selected_items:
            selected_items.pop()
            user = render_user(selected_items, candidate_task)
            if len(system.encode("utf-8")) + len(user.encode("utf-8")) <= byte_ceiling:
                break
    return [
        {
            "role": "system",
            "content": system,
        },
        {"role": "user", "content": user},
    ]


def bootstrap_visible_item_ids(messages: list[dict[str, str]]) -> frozenset[str]:
    """Recover the exact ID authority surface carried by a bootstrap request."""

    if not messages:
        return frozenset()
    content = str(messages[-1].get("content") or "")
    try:
        payload = content.split("CERTIFIED CATALOG\n", 1)[1].split("\n\nSelect only", 1)[0]
        rows = json.loads(payload)
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(rows, list):
        return frozenset()
    return frozenset(
        str(row.get("id") or "")
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "")
    )


class PersistentExecutionStateEngine:
    """Task-scoped deterministic controller around one immutable bootstrap catalog."""

    def __init__(
        self,
        *,
        task: str,
        catalog: BootstrapCatalog,
        structural_links: tuple[StructuralLink, ...],
        present_paths: tuple[str, ...],
        workspace_root: str = "/app",
        path_origins: dict[str, EvidenceOrigin] | None = None,
        path_origin_revisions: dict[str, str] | None = None,
        decisive: DecisiveDerivation | None = None,
    ) -> None:
        task_digest = hashlib.sha256(str(task or "").encode("utf-8")).hexdigest()
        self._catalog = catalog
        self._workspace_root = posixpath.normpath(str(workspace_root or "/app").replace("\\", "/"))
        self._links = self._certified_links(structural_links)
        self._present_paths = frozenset(
            self._state_path(item) for item in present_paths if self._state_path(item)
        )
        supplied_origins = path_origins or {}
        supplied_revisions = path_origin_revisions or {}
        self._path_origins = {
            self._state_path(path): (
                origin if isinstance(origin, EvidenceOrigin) else EvidenceOrigin(str(origin))
            )
            for path, origin in supplied_origins.items()
            if self._state_path(path)
        }
        self._path_origin_revisions = {
            self._state_path(path): str(revision or "")
            for path, revision in supplied_revisions.items()
            if self._state_path(path)
        }
        for item in catalog.items:
            if item.path:
                self._path_origins.setdefault(item.path, item.origin)
                self._path_origin_revisions.setdefault(item.path, item.origin_revision)
        self._deliverable_paths = frozenset(
            item.path
            for item in catalog.items
            if item.kind is CatalogItemKind.DELIVERABLE and item.path
        )
        self._snapshot = PersistentExecutionState(
            state_id=_stable_id("state", task_digest, catalog.source_revision),
            task_digest=task_digest,
            version=1,
            source_revision=catalog.source_revision,
            graph_revision=catalog.graph_revision,
            graph_source_revision=catalog.graph_source_revision,
            graph_current=True,
            phase=StatePhase.LOCALIZING,
            bootstrap_status=BootstrapStatus.NOT_REQUESTED,
            bootstrap_mode=BootstrapMode.DETERMINISTIC_FALLBACK,
            obligations=tuple(
                StateObligation(
                    obligation_id=_stable_id("obligation", item.kind.value, item.item_id),
                    kind=(
                        "produce_deliverable"
                        if item.kind is CatalogItemKind.DELIVERABLE
                        else "run_validation"
                    ),
                    path=item.path,
                    relation="task_requirement",
                    source_path=(item.anchors[0] if item.anchors else item.label),
                    blocking=True,
                    opened_revision=catalog.source_revision,
                )
                for item in catalog.items
                if item.required
                and item.kind in {CatalogItemKind.DELIVERABLE, CatalogItemKind.VALIDATION}
            ),
        )
        self._metrics: dict[str, int] = {
            "initializations": 1,
            "bootstrap_applications": 0,
            "context_compilations": 0,
            "preflight_projections": 0,
            "postflight_commits": 0,
            "graph_rebases": 0,
            "material_transitions": 0,
            "stale_rejections": 0,
            "stable_context_abstentions": 0,
            "context_dispatches": 0,
        }
        self._receipts: list[dict[str, Any]] = []
        self._last_dispatched_version = 0
        self._exposed_claim_ids: set[str] = set()
        self._decisive = decisive or DecisiveDerivation(
            status=DecisiveStatus.ABSTAINED,
            reason_codes=("no_decisive_derivation",),
        )
        self._record("initialize", source_revision=catalog.source_revision)
        self._record(
            "decisive_derivation",
            status=self._decisive.status.value,
            reason_codes=list(self._decisive.reason_codes),
            detectors=dict(self._decisive.detectors),
            fact_count=len(self._decisive.facts),
            scan=dict(self._decisive.scan),
        )

    @classmethod
    def initialize_from_graph(
        cls,
        *,
        task: str,
        catalog: BootstrapCatalog,
        structural_links: tuple[StructuralLink, ...],
        present_paths: tuple[str, ...],
        workspace_root: str = "/app",
        path_origins: dict[str, EvidenceOrigin] | None = None,
        path_origin_revisions: dict[str, str] | None = None,
        decisive: DecisiveDerivation | None = None,
    ) -> PersistentExecutionStateEngine:
        if not catalog.complete:
            raise ValueError("persistent execution state requires a complete graph catalog")
        return cls(
            task=task,
            catalog=catalog,
            structural_links=structural_links,
            present_paths=present_paths,
            workspace_root=workspace_root,
            path_origins=path_origins,
            path_origin_revisions=path_origin_revisions,
            decisive=decisive,
        )

    @property
    def snapshot(self) -> PersistentExecutionState:
        return self._snapshot

    @property
    def catalog(self) -> BootstrapCatalog:
        return self._catalog

    @property
    def metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    @property
    def receipts(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._receipts)

    @staticmethod
    def _certified_links(links: tuple[StructuralLink, ...]) -> tuple[StructuralLink, ...]:
        return tuple(
            sorted(
                (
                    replace(link, relation=normalized_relation)
                    for link in links
                    if (normalized_relation := _certified_relation(link))
                ),
                key=lambda item: (
                    item.source_path,
                    item.target_path,
                    item.relation,
                    item.provenance,
                ),
            )
        )

    def _record(self, boundary: str, **payload: Any) -> None:
        self._receipts.append(
            {
                "boundary": boundary,
                "state_id": self._snapshot.state_id,
                "state_version": self._snapshot.version,
                **payload,
            }
        )

    def _state_path(self, value: Any) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith(self._workspace_root.rstrip("/") + "/"):
            raw = raw[len(self._workspace_root.rstrip("/")) + 1 :]
        return _path(raw)

    def _focus_for_path(
        self,
        path: str,
        *,
        source_revision: str,
        source_hint: bool = False,
    ) -> CurrentFocus | None:
        normalized = self._state_path(path)
        if not normalized:
            return None
        if normalized in self._deliverable_paths:
            kind = CurrentFocusKind.TASK_DELIVERABLE
            origin = EvidenceOrigin.TASK_DELIVERABLE
        elif normalized.startswith("/"):
            kind = CurrentFocusKind.EXTERNAL_RUNTIME
            origin = EvidenceOrigin.EXTERNAL_RUNTIME
        elif normalized in self._present_paths or source_hint:
            kind = CurrentFocusKind.REPOSITORY_SOURCE
            origin = self._path_origins.get(
                normalized,
                (
                    EvidenceOrigin.MODEL_AUTHORED
                    if source_hint
                    else EvidenceOrigin.PREEXISTING_REPOSITORY
                ),
            )
        else:
            kind = CurrentFocusKind.ARTIFACT
            origin = EvidenceOrigin.GENERATED_ARTIFACT
        return CurrentFocus(
            path=normalized,
            kind=kind,
            origin=origin,
            source_revision=source_revision,
            origin_revision=self._path_origin_revisions.get(normalized, ""),
        )

    def apply_bootstrap(
        self,
        selection: BootstrapSelection,
        *,
        current_source_revision: str,
        error: bool = False,
        selection_mode: BootstrapMode | str | None = None,
    ) -> PersistentExecutionState:
        self._metrics["bootstrap_applications"] += 1
        if current_source_revision != self._snapshot.source_revision:
            self._metrics["stale_rejections"] += 1
            self._record("bootstrap", disposition="stale_source_revision")
            return self._snapshot
        requested_mode = (
            selection_mode
            if isinstance(selection_mode, BootstrapMode)
            else BootstrapMode(str(selection_mode))
            if selection_mode is not None
            else BootstrapMode.GENERATIVE_SELECTED
        )
        status = BootstrapStatus.SELECTED
        mode = requested_mode
        applied = selection
        if requested_mode is BootstrapMode.DETERMINISTIC_SELECTED:
            applied = deterministic_bootstrap_selection(self._catalog)
            if not applied.valid:
                status = BootstrapStatus.INVALID_FALLBACK
                applied, status = deterministic_bootstrap_fallback(
                    self._catalog, status=status
                )
                mode = BootstrapMode.DETERMINISTIC_FALLBACK
        elif not selection.valid:
            status = BootstrapStatus.ERROR_FALLBACK if error else BootstrapStatus.INVALID_FALLBACK
            applied, status = deterministic_bootstrap_fallback(self._catalog, status=status)
            mode = BootstrapMode.DETERMINISTIC_FALLBACK
        next_snapshot = replace(
            self._snapshot,
            version=self._snapshot.version + 1,
            bootstrap_status=status,
            bootstrap_mode=mode,
            primary_focus_id=applied.primary_focus_id,
            current_focus_id=applied.primary_focus_id,
            ordered_item_ids=applied.ordered_item_ids,
            risk_item_ids=applied.risk_item_ids,
            validation_item_ids=applied.validation_item_ids,
            last_transition="bootstrap_applied",
        )
        self._snapshot = next_snapshot
        self._metrics["material_transitions"] += 1
        self._record(
            "bootstrap",
            disposition=status.value,
            bootstrap_mode=mode.value,
            selected_ids=list(
                dict.fromkeys(
                    (
                        applied.primary_focus_id,
                        *applied.ordered_item_ids,
                        *applied.risk_item_ids,
                        *applied.validation_item_ids,
                    )
                )
            ),
        )
        return self._snapshot

    def project_preflight(
        self,
        proposed: ProposedAction,
        *,
        current_source_revision: str,
    ) -> PreflightStateProjection:
        self._metrics["preflight_projections"] += 1
        targets = tuple(
            dict.fromkeys(
                self._state_path(target.path)
                for target in proposed.targets
                if self._state_path(target.path)
            )
        )
        reasons: list[str] = []
        considered = True
        if proposed.source_revision != current_source_revision:
            considered = False
            reasons.append("stale_proposed_revision")
        if self._snapshot.source_revision != current_source_revision:
            considered = False
            reasons.append("stale_state_revision")
        if not considered:
            self._metrics["stale_rejections"] += 1
        open_obligations = tuple(
            item.obligation_id
            for item in self._snapshot.obligations
            if item.status is ObligationStatus.OPEN
        )
        blocking_obligations = tuple(
            item.obligation_id
            for item in self._snapshot.obligations
            if item.status is ObligationStatus.OPEN and item.blocking
        )
        contradiction = bool(
            considered
            and proposed.operation is ActionOperation.SUBMIT
            and (blocking_obligations or self._snapshot.current_failure is not None)
        )
        if contradiction:
            reasons.append("submit_has_certified_open_state")
        projection = PreflightStateProjection(
            action_id=proposed.action_id,
            considered=considered,
            operation=proposed.operation.value,
            target_paths=targets,
            open_obligation_ids=open_obligations,
            blocking_obligation_ids=blocking_obligations,
            material_contradiction=contradiction,
            reason_codes=tuple(reasons),
            state_version=self._snapshot.version,
        )
        self._record("preflight", **projection.as_dict())
        return projection

    @staticmethod
    def _diagnostic_summary(output: str) -> str:
        lines = [
            clean
            for line in str(output or "").splitlines()
            if (clean := _bounded(line, 280))
            and not re.match(r"^(?:PASS|OK|SUCCESS)\b", clean, re.IGNORECASE)
        ]
        failure = re.compile(
            r"(?:assert|exception|error|fail(?:ed|ure)?|traceback)", re.IGNORECASE
        )
        for line in reversed(lines):
            if failure.search(line):
                return line
        if lines:
            return lines[-1]
        return "validation failed without a diagnostic"

    def _open_adjacent_obligations(
        self,
        changed_paths: tuple[str, ...],
        *,
        source_revision: str,
        obligations: tuple[StateObligation, ...] | None = None,
    ) -> tuple[StateObligation, ...]:
        existing = {
            item.obligation_id: item
            for item in (self._snapshot.obligations if obligations is None else obligations)
        }
        changed = frozenset(changed_paths)
        for link in self._links:
            if link.source_path in changed:
                source_path, target_path = link.source_path, link.target_path
            elif link.target_path in changed:
                source_path, target_path = link.target_path, link.source_path
            else:
                continue
            kind = (
                "validate_related_test"
                if link.relation == "test_assertion"
                else "inspect_dependency"
            )
            obligation_id = _stable_id("obligation", kind, source_path, target_path, link.relation)
            prior = existing.get(obligation_id)
            if (
                prior is not None
                and prior.opened_revision == source_revision
                and prior.status is not ObligationStatus.INVALIDATED
            ):
                continue
            existing[obligation_id] = StateObligation(
                obligation_id=obligation_id,
                kind=kind,
                path=target_path,
                relation=link.relation,
                source_path=source_path,
                source_origin=self._path_origins.get(
                    source_path, EvidenceOrigin.PREEXISTING_REPOSITORY
                ),
                path_origin=self._path_origins.get(
                    target_path, EvidenceOrigin.PREEXISTING_REPOSITORY
                ),
                source_origin_revision=self._path_origin_revisions.get(source_path, ""),
                path_origin_revision=self._path_origin_revisions.get(target_path, ""),
                evidence_authority=EvidenceAuthority.CERTIFIED_RELATION,
                blocking=False,
                opened_revision=source_revision,
            )
        return tuple(
            sorted(
                existing.values(),
                key=lambda item: (
                    0 if item.status is ObligationStatus.OPEN else 1,
                    not item.blocking,
                    item.kind,
                    item.path,
                    item.obligation_id,
                ),
            )[:32]
        )

    @staticmethod
    def _satisfy_paths(
        obligations: tuple[StateObligation, ...],
        paths: frozenset[str],
        *,
        source_revision: str,
        kinds: frozenset[str],
    ) -> tuple[StateObligation, ...]:
        return tuple(
            replace(
                item,
                status=ObligationStatus.SATISFIED,
                satisfied_revision=source_revision,
            )
            if item.status is ObligationStatus.OPEN and item.kind in kinds and item.path in paths
            else item
            for item in obligations
        )

    def commit_postflight(
        self,
        proposed: ProposedAction,
        *,
        returncode: int,
        output: str,
        changed_paths: tuple[str, ...],
        graph_changed_paths: tuple[str, ...] | None = None,
        current_source_revision: str,
        current_graph_revision: str,
        current_graph_source_revision: str | None = None,
        validation_status: str,
        validation_check_id: str | None = None,
    ) -> PersistentExecutionState:
        self._metrics["postflight_commits"] += 1
        # Preflight correctly rejects a proposal selected against stale state.
        # Postflight is different: the host has already executed this exact
        # action.  In a SHADOW batch, an earlier action can advance the source
        # revision before a later, pre-decided validation executes.  Bind that
        # observed result to the execution/current revision instead of losing
        # authoritative validation evidence merely because selection was old.
        selection_revision_rebound = (
            proposed.source_revision != self._snapshot.source_revision
        )
        if selection_revision_rebound and not (
            proposed.batch_size > 1
            and proposed.batch_index > 0
            and current_source_revision == self._snapshot.source_revision
        ):
            self._metrics["stale_rejections"] += 1
            self._record(
                "postflight",
                action_id=proposed.action_id,
                disposition="stale_proposed_revision",
            )
            return self._snapshot

        targets = frozenset(
            self._state_path(target.path)
            for target in proposed.targets
            if self._state_path(target.path)
        )
        normalized_changed = tuple(
            dict.fromkeys(
                self._state_path(path) for path in changed_paths if self._state_path(path)
            )
        )
        normalized_graph_changed = tuple(
            dict.fromkeys(
                self._state_path(path)
                for path in (changed_paths if graph_changed_paths is None else graph_changed_paths)
                if self._state_path(path)
            )
        )
        files_inspected = self._snapshot.files_inspected
        files_modified = self._snapshot.files_modified
        obligations = self._snapshot.obligations
        observed_validation = self._snapshot.observed_validation
        declared_validation = self._snapshot.declared_validation
        completion_readiness = self._snapshot.completion_readiness
        failure = self._snapshot.current_failure
        phase = self._snapshot.phase
        current_focus_id = self._snapshot.current_focus_id
        current_focus = self._snapshot.current_focus
        transition = "postflight_observed"

        focus_paths = tuple(normalized_changed) or tuple(sorted(targets))
        if focus_paths and proposed.operation in {
            ActionOperation.READ,
            ActionOperation.EDIT,
            ActionOperation.CREATE,
            ActionOperation.DELETE,
        }:
            current_focus = self._focus_for_path(
                focus_paths[0],
                source_revision=current_source_revision,
                source_hint=focus_paths[0] in normalized_graph_changed,
            )
            matched_focus = next(
                (
                    item.item_id
                    for item in self._catalog.items
                    if current_focus is not None and item.path == current_focus.path
                ),
                "",
            )
            current_focus_id = matched_focus

        if proposed.operation is ActionOperation.READ and returncode == 0:
            files_inspected = tuple(sorted(set(files_inspected) | targets))
            obligations = self._satisfy_paths(
                obligations,
                targets,
                source_revision=current_source_revision,
                kinds=frozenset({"inspect_dependency"}),
            )
            transition = "repository_path_inspected"
        elif proposed.operation is ActionOperation.SEARCH and returncode == 0:
            phase = StatePhase.LOCALIZING
            transition = "search_observed"

        if normalized_changed:
            files_modified = tuple(sorted(set(files_modified) | set(normalized_changed)))
            obligations = self._satisfy_paths(
                obligations,
                frozenset(normalized_changed),
                source_revision=current_source_revision,
                kinds=frozenset({"produce_deliverable", "inspect_dependency"}),
            )
            if not normalized_graph_changed:
                phase = StatePhase.IMPLEMENTING
                transition = "deliverable_changed"
        if normalized_graph_changed:
            # The pre-edit graph is no longer authoritative.  Current graph
            # obligations are recomputed only after the incremental refresh
            # succeeds in ``rebase_graph``.
            observed_validation = StateValidation(
                status=StateValidationStatus.PENDING,
                source_revision=current_source_revision,
                action_id=proposed.action_id,
            )
            declared_validation = StateValidation(
                status=(
                    StateValidationStatus.PENDING
                    if any(item.kind is CatalogItemKind.VALIDATION for item in self._catalog.items)
                    else StateValidationStatus.UNKNOWN
                ),
                source_revision=current_source_revision,
                action_id=proposed.action_id,
            )
            completion_readiness = CompletionReadiness.NOT_READY
            failure = None
            phase = StatePhase.IMPLEMENTING
            transition = "source_changed"

        normalized_validation = str(validation_status or "unknown").strip().lower()
        if proposed.operation is ActionOperation.VALIDATE:
            command = _bounded(proposed.raw_command, 280)
            # The central agent classifies validation exactly once.  Reuse its
            # canonical declared-check identity here instead of reparsing or
            # requiring byte-for-byte equality with a wrapper/redirection-rich
            # Bash command.
            completed_check = _bounded(validation_check_id, 280)
            if normalized_validation == StateValidationStatus.PASS.value and returncode == 0:
                observed_validation = StateValidation(
                    status=StateValidationStatus.PASS,
                    command=command,
                    source_revision=current_source_revision,
                    action_id=proposed.action_id,
                )
                declared_scope = bool(completed_check) and any(
                    item.kind is CatalogItemKind.VALIDATION
                    and completed_check in item.anchors
                    for item in self._catalog.items
                )
                if declared_scope:
                    declared_validation = StateValidation(
                        status=StateValidationStatus.PASS,
                        command=command,
                        source_revision=current_source_revision,
                        action_id=proposed.action_id,
                        declared_check_id=completed_check,
                    )
                validation_targets = targets
                if declared_scope:
                    validation_targets = validation_targets | frozenset(
                        item.path for item in obligations if item.kind == "validate_related_test"
                    )
                obligations = self._satisfy_paths(
                    obligations,
                    validation_targets,
                    source_revision=current_source_revision,
                    kinds=frozenset({"validate_related_test"}),
                )
                obligations = tuple(
                    replace(
                        item,
                        status=ObligationStatus.SATISFIED,
                        satisfied_revision=current_source_revision,
                    )
                    if item.status is ObligationStatus.OPEN
                    and item.kind == "run_validation"
                    and item.source_path == completed_check
                    else item
                    for item in obligations
                )
                failure = None
                ready = bool(
                    declared_scope
                    and files_modified
                    and not any(
                        item.status is ObligationStatus.OPEN and item.blocking
                        for item in obligations
                    )
                )
                completion_readiness = (
                    CompletionReadiness.READY if ready else CompletionReadiness.NOT_READY
                )
                phase = (
                    StatePhase.READY_TO_SUBMIT
                    if ready
                    else StatePhase.VALIDATING
                )
                transition = "validation_passed"
            elif normalized_validation == StateValidationStatus.FAIL.value:
                observed_validation = StateValidation(
                    status=StateValidationStatus.FAIL,
                    command=command,
                    source_revision=current_source_revision,
                    action_id=proposed.action_id,
                )
                if completed_check and any(
                    item.kind is CatalogItemKind.VALIDATION and completed_check in item.anchors
                    for item in self._catalog.items
                ):
                    declared_validation = StateValidation(
                        status=StateValidationStatus.FAIL,
                        command=command,
                        source_revision=current_source_revision,
                        action_id=proposed.action_id,
                        declared_check_id=completed_check,
                    )
                completion_readiness = CompletionReadiness.NOT_READY
                failure = StateFailure(
                    action_id=proposed.action_id,
                    operation=proposed.operation.value,
                    diagnostic=self._diagnostic_summary(output),
                    source_revision=current_source_revision,
                )
                phase = StatePhase.VALIDATING
                transition = "validation_failed"
            else:
                # A recognized validation-shaped action whose terminal result is
                # not mechanically attributable remains pending.  Raw exit code
                # is not enough to manufacture PASS/FAIL authority.
                observed_validation = StateValidation(
                    status=StateValidationStatus.PENDING,
                    command=command,
                    source_revision=current_source_revision,
                    action_id=proposed.action_id,
                )
                completion_readiness = CompletionReadiness.NOT_READY
                phase = StatePhase.VALIDATING
                transition = "validation_outcome_unattributed"

        bound_graph_source_revision = str(
            current_graph_source_revision or self._snapshot.graph_source_revision
        )
        candidate = replace(
            self._snapshot,
            source_revision=current_source_revision,
            graph_source_revision=bound_graph_source_revision,
            graph_current=(
                not normalized_graph_changed
                and self._snapshot.graph_current
                and bound_graph_source_revision == self._snapshot.graph_source_revision
                and current_graph_revision == self._snapshot.graph_revision
            ),
            phase=phase,
            current_focus_id=current_focus_id,
            current_focus=current_focus,
            files_inspected=files_inspected,
            files_modified=files_modified,
            obligations=obligations,
            observed_validation=observed_validation,
            declared_validation=declared_validation,
            completion_readiness=completion_readiness,
            current_failure=failure,
            last_transition=transition,
        )
        semantic_candidate = replace(
            candidate,
            version=self._snapshot.version,
            last_transition=self._snapshot.last_transition,
        )
        if semantic_candidate != self._snapshot:
            candidate = replace(candidate, version=self._snapshot.version + 1)
            self._metrics["material_transitions"] += 1
        else:
            candidate = replace(candidate, version=self._snapshot.version)
        self._snapshot = candidate
        self._record(
            "postflight",
            action_id=proposed.action_id,
            disposition="committed",
            operation=proposed.operation.value,
            changed_paths=list(normalized_changed),
            graph_changed_paths=list(normalized_graph_changed),
            returncode=int(returncode),
            validation_status=normalized_validation,
            transition=transition,
            selection_revision_rebound=selection_revision_rebound,
            proposed_source_revision=proposed.source_revision,
            committed_source_revision=current_source_revision,
        )
        return self._snapshot

    def rebase_graph(
        self,
        *,
        evidence: RepositoryEvidence,
        structural_links: tuple[StructuralLink, ...],
        current_source_revision: str,
        current_graph_revision: str,
        graph_complete: bool,
        current_graph_source_revision: str | None = None,
        changed_paths: tuple[str, ...] = (),
        present_paths: tuple[str, ...] | None = None,
    ) -> PersistentExecutionState:
        self._metrics["graph_rebases"] += 1
        if not graph_complete or not evidence.substrate_ready:
            self._record("graph_rebase", disposition="graph_incomplete")
            return self._snapshot
        if not current_source_revision or not current_graph_revision:
            self._record("graph_rebase", disposition="revision_missing")
            return self._snapshot
        bound_graph_source_revision = str(current_graph_source_revision or current_source_revision)
        if evidence.source_revision and evidence.source_revision != bound_graph_source_revision:
            self._metrics["stale_rejections"] += 1
            self._record("graph_rebase", disposition="evidence_source_revision_mismatch")
            return self._snapshot
        self._links = self._certified_links(structural_links)
        previous_present_paths = self._present_paths
        if present_paths is not None:
            self._present_paths = frozenset(
                self._state_path(path) for path in present_paths if self._state_path(path)
            )
        # OPEN graph-derived obligations describe the previous certified edge
        # set.  Invalidate them all, then recreate only obligations supported
        # by the freshly certified links below.  Required task obligations and
        # completed historical evidence remain intact.
        obligations = tuple(
            replace(item, status=ObligationStatus.INVALIDATED)
            if item.relation != "task_requirement"
            and item.status is ObligationStatus.OPEN
            else item
            for item in self._snapshot.obligations
        )
        normalized_changed = tuple(
            dict.fromkeys(
                self._state_path(path) for path in changed_paths if self._state_path(path)
            )
        )
        for path in normalized_changed:
            if path in self._deliverable_paths:
                self._path_origins[path] = EvidenceOrigin.TASK_DELIVERABLE
                self._path_origin_revisions[path] = current_source_revision
            elif path not in previous_present_paths:
                self._path_origins[path] = EvidenceOrigin.MODEL_AUTHORED
                self._path_origin_revisions[path] = current_source_revision
            else:
                # Editing an existing path does not change where that path
                # entered the task.  Preserve its original provenance.
                self._path_origins.setdefault(
                    path, EvidenceOrigin.PREEXISTING_REPOSITORY
                )
        if normalized_changed:
            obligations = self._open_adjacent_obligations(
                normalized_changed,
                source_revision=current_source_revision,
                obligations=obligations,
            )
        # Bootstrap selection may legitimately choose a non-required focus.
        # A graph rebase invalidates only catalog items that disappeared; it
        # must not erase a still-current optional selection merely because the
        # item was never a completion requirement.
        catalog_ids = frozenset(item.item_id for item in self._catalog.items)
        current_focus_id = (
            self._snapshot.current_focus_id
            if self._snapshot.current_focus_id in catalog_ids
            else ""
        )
        current_focus = self._snapshot.current_focus
        if (
            current_focus is not None
            and current_focus.kind is CurrentFocusKind.REPOSITORY_SOURCE
            and current_focus.path not in self._present_paths
        ):
            current_focus = None
        elif current_focus is not None:
            current_focus = replace(
                current_focus,
                origin=self._path_origins.get(current_focus.path, current_focus.origin),
                source_revision=current_source_revision,
                origin_revision=self._path_origin_revisions.get(
                    current_focus.path, current_focus.origin_revision
                ),
            )
        candidate = replace(
            self._snapshot,
            source_revision=current_source_revision,
            graph_source_revision=bound_graph_source_revision,
            graph_revision=current_graph_revision,
            graph_current=True,
            current_focus_id=current_focus_id,
            current_focus=current_focus,
            ordered_item_ids=tuple(
                item_id for item_id in self._snapshot.ordered_item_ids if item_id in catalog_ids
            ),
            risk_item_ids=tuple(
                item_id for item_id in self._snapshot.risk_item_ids if item_id in catalog_ids
            ),
            validation_item_ids=tuple(
                item_id for item_id in self._snapshot.validation_item_ids if item_id in catalog_ids
            ),
            obligations=obligations,
            last_transition="graph_rebased",
        )
        canonical_candidate_obligations = tuple(
            sorted(candidate.obligations, key=lambda item: item.obligation_id)
        )
        canonical_snapshot_obligations = tuple(
            sorted(self._snapshot.obligations, key=lambda item: item.obligation_id)
        )
        semantic_candidate = replace(
            candidate,
            version=self._snapshot.version,
            last_transition=self._snapshot.last_transition,
            obligations=canonical_candidate_obligations,
        )
        semantic_snapshot = replace(
            self._snapshot,
            obligations=canonical_snapshot_obligations,
        )
        if semantic_candidate != semantic_snapshot:
            candidate = replace(candidate, version=self._snapshot.version + 1)
            self._metrics["material_transitions"] += 1
        else:
            # Preserve the prior tuple order on a semantic no-op. Reordering
            # controller state without a version change would make replay and
            # frame-delta accounting disagree about whether anything changed.
            candidate = replace(
                candidate,
                version=self._snapshot.version,
                obligations=self._snapshot.obligations,
            )
        self._snapshot = candidate
        self._record("graph_rebase", disposition="current")
        return self._snapshot

    def _item(self, item_id: str) -> BootstrapCatalogItem | None:
        return next((item for item in self._catalog.items if item.item_id == item_id), None)

    def _frame_lines(
        self,
        kind: ContextFrameKind,
        *,
        include_advisory_obligations: bool = True,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        snapshot = self._snapshot
        focus_id = snapshot.current_focus_id
        if not focus_id and snapshot.graph_revision == self._catalog.graph_revision:
            focus_id = snapshot.primary_focus_id
        catalog_is_current = snapshot.graph_revision == self._catalog.graph_revision
        focus = self._item(focus_id) if catalog_is_current else None
        lines: list[tuple[str, str, dict[str, Any]]] = []

        def metadata(
            *,
            origin: EvidenceOrigin,
            authority: EvidenceAuthority,
            materiality_reason: str,
            origin_revision: str = "",
            relation_endpoint: str = "",
            declared_validation_id: str = "",
            known_to_model: bool = False,
            known_texts: tuple[str, ...] = (),
            relation: str = "",
            detector: str = "",
        ) -> dict[str, Any]:
            return {
                "origin": origin.value,
                "authority": authority.value,
                "novel_to_provider_view": True,
                "known_to_model": known_to_model,
                "materiality_reason": materiality_reason,
                "source_revision": snapshot.source_revision,
                "origin_revision": origin_revision,
                "relation_endpoint": relation_endpoint,
                "declared_validation_id": declared_validation_id,
                "relation": relation,
                "detector": detector,
                "decisive": bool(detector),
                "_known_texts": known_texts,
            }

        def _certified_repository_item(
            item: BootstrapCatalogItem | None,
        ) -> BootstrapCatalogItem | None:
            if (
                item is not None
                and item.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
                and item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
                and bool(_provider_material_relation(item.relation))
            ):
                return item
            return None

        if self._last_dispatched_version == 0:
            for fact in self._decisive.facts:
                lines.append(
                    (
                        fact.claim_id,
                        fact.gap_text,
                        metadata(
                            origin=EvidenceOrigin(str(fact.origin)),
                            authority=EvidenceAuthority.DETERMINISTIC_DERIVED,
                            materiality_reason="task_decisive_evidence",
                            origin_revision=fact.source_revision,
                            relation_endpoint=fact.path,
                            known_texts=(),
                            detector=fact.detector,
                        ),
                    )
                )

        initial_focus = _certified_repository_item(focus)
        if (
            kind is ContextFrameKind.INITIAL
            and initial_focus is None
            and catalog_is_current
            and (
                focus is None
                or (
                    focus.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
                    and focus.evidence_authority
                    in {
                        EvidenceAuthority.IDENTITY_ONLY,
                        EvidenceAuthority.RANKING_SUPPORT,
                    }
                )
            )
        ):
            for item_id in snapshot.ordered_item_ids:
                candidate = _certified_repository_item(self._item(item_id))
                if candidate is not None:
                    initial_focus = candidate
                    break
            if initial_focus is None:
                for item in self._catalog.items:
                    candidate = _certified_repository_item(item)
                    if candidate is not None:
                        initial_focus = candidate
                        break

        if kind is ContextFrameKind.INITIAL and initial_focus is not None:
            focus = initial_focus
            lines.append(
                (
                    _stable_id("state-claim", "focus", focus.item_id),
                    f"Certified related repository file: {focus.label}.",
                    metadata(
                        origin=focus.origin,
                        authority=focus.evidence_authority,
                        materiality_reason="newly_certified_related_file",
                        origin_revision=focus.origin_revision,
                        relation_endpoint=focus.path,
                        relation=_provider_material_relation(focus.relation),
                    ),
                )
            )
            if (
                focus.source_excerpt
                and focus.source_claim_id
                and focus.path not in snapshot.files_inspected
            ):
                support_label = "certified graph relation"
                lines.append(
                    (
                        focus.source_claim_id,
                        (
                            "Repository relation context "
                            f"[{support_label}] {focus.path}:"
                            f"{focus.source_start_line}-{focus.source_end_line}\n"
                            "```\n"
                            f"{focus.source_excerpt}\n"
                            "```"
                        ),
                        metadata(
                            origin=focus.origin,
                            authority=EvidenceAuthority.CERTIFIED_RELATION,
                            materiality_reason="newly_certified_related_file",
                            origin_revision=focus.origin_revision,
                            relation_endpoint=focus.path,
                            known_texts=(focus.source_excerpt,),
                            relation=_provider_material_relation(focus.relation),
                        ),
                    )
                )
        open_obligations = [
            item
            for item in snapshot.obligations
            if item.status is ObligationStatus.OPEN
            and bool(_provider_material_relation(item.relation))
            and (item.blocking or include_advisory_obligations)
            and (
                item.relation == "task_requirement"
                or (
                    item.source_origin is EvidenceOrigin.PREEXISTING_REPOSITORY
                    and item.path_origin is EvidenceOrigin.PREEXISTING_REPOSITORY
                    and item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
                )
            )
        ]
        for obligation in open_obligations[:4]:
            obligation_target = obligation.path or obligation.source_path
            relation = _provider_material_relation(obligation.relation)
            if not relation:
                continue
            lines.append(
                (
                    _stable_id(
                        "state-claim",
                        obligation.obligation_id,
                        obligation.opened_revision,
                    ),
                    (
                        f"{'Required' if obligation.blocking else 'Related'} "
                        f"{obligation.kind}: {obligation_target} "
                        f"({relation} from {obligation.source_path})."
                    ),
                    metadata(
                        origin=(
                            EvidenceOrigin.TASK_DELIVERABLE
                            if obligation.kind == "produce_deliverable"
                            else obligation.path_origin
                        ),
                        authority=(
                            obligation.evidence_authority
                            if obligation.relation != "task_requirement"
                            else EvidenceAuthority.IDENTITY_ONLY
                        ),
                        materiality_reason=(
                            "new_unresolved_task_obligation"
                            if obligation.blocking
                            else "related_advisory_obligation"
                        ),
                        origin_revision=(
                            obligation.path_origin_revision
                            or obligation.opened_revision
                        ),
                        relation_endpoint=obligation.path,
                        known_texts=(
                            (obligation_target,)
                            if obligation.relation == "task_requirement"
                            else ()
                        ),
                        relation=relation,
                    ),
                )
            )
        if snapshot.declared_validation.status in {
            StateValidationStatus.PASS,
            StateValidationStatus.FAIL,
        }:
            validation_text = f"Declared validation: {snapshot.declared_validation.status.value}"
            if snapshot.declared_validation.command:
                validation_text += f" - {snapshot.declared_validation.command}"
            lines.append(
                (
                    _stable_id(
                        "state-claim",
                        "validation",
                        snapshot.declared_validation.status.value,
                        snapshot.declared_validation.source_revision,
                    ),
                    validation_text + ".",
                    metadata(
                        origin=EvidenceOrigin.EXTERNAL_RUNTIME,
                        authority=EvidenceAuthority.EXECUTION_OBSERVATION,
                        materiality_reason="declared_validation_status_change",
                        origin_revision=snapshot.declared_validation.source_revision,
                        declared_validation_id=(
                            snapshot.declared_validation.declared_check_id
                        ),
                    ),
                )
            )
        if snapshot.current_failure is not None:
            lines.append(
                (
                    _stable_id(
                        "state-claim",
                        "failure",
                        snapshot.current_failure.action_id,
                        snapshot.current_failure.diagnostic,
                    ),
                    f"Current validation failure: {snapshot.current_failure.diagnostic}.",
                    metadata(
                        origin=EvidenceOrigin.EXTERNAL_RUNTIME,
                        authority=EvidenceAuthority.EXECUTION_OBSERVATION,
                        materiality_reason="current_attributable_failure",
                        origin_revision=snapshot.current_failure.source_revision,
                        known_texts=(snapshot.current_failure.diagnostic,),
                    ),
                )
            )
        return lines

    def compile_context(
        self,
        *,
        current_source_revision: str,
        provider_call: int,
        max_tokens: int,
        token_counter: Callable[[str], int] = _default_token_counter,
        provider_messages: Sequence[Mapping[str, Any]] = (),
        include_advisory_obligations: bool = True,
    ) -> PersistentContextFrame:
        self._metrics["context_compilations"] += 1
        if current_source_revision != self._snapshot.source_revision:
            self._metrics["stale_rejections"] += 1
            frame = PersistentContextFrame(
                kind=ContextFrameKind.NONE,
                rendered_text="",
                claim_ids=(),
                state_version=self._snapshot.version,
                source_revision=current_source_revision,
                provider_call=provider_call,
                token_count=0,
                reason_codes=("stale_source_revision",),
            )
            self._record("provider_context", **frame.as_dict())
            return frame
        if self._snapshot.bootstrap_status in {
            BootstrapStatus.NOT_REQUESTED,
            BootstrapStatus.NOT_APPLICABLE,
        }:
            frame = PersistentContextFrame(
                kind=ContextFrameKind.NONE,
                rendered_text="",
                claim_ids=(),
                state_version=self._snapshot.version,
                source_revision=current_source_revision,
                provider_call=provider_call,
                token_count=0,
                reason_codes=("bootstrap_not_applied",),
            )
            self._record("provider_context", **frame.as_dict())
            return frame
        if not self._snapshot.graph_current:
            frame = PersistentContextFrame(
                kind=ContextFrameKind.NONE,
                rendered_text="",
                claim_ids=(),
                state_version=self._snapshot.version,
                source_revision=current_source_revision,
                provider_call=provider_call,
                token_count=0,
                reason_codes=("graph_rebase_required",),
            )
            self._record("provider_context", **frame.as_dict())
            return frame
        if max_tokens < 1:
            frame = PersistentContextFrame(
                kind=ContextFrameKind.NONE,
                rendered_text="",
                claim_ids=(),
                state_version=self._snapshot.version,
                source_revision=current_source_revision,
                provider_call=provider_call,
                token_count=0,
                reason_codes=("context_budget_closed",),
            )
            self._record("provider_context", **frame.as_dict())
            return frame

        if (
            self._last_dispatched_version == self._snapshot.version
            and self._last_dispatched_version != 0
        ):
            frame = PersistentContextFrame(
                kind=ContextFrameKind.NONE,
                rendered_text="",
                claim_ids=(),
                state_version=self._snapshot.version,
                source_revision=current_source_revision,
                provider_call=provider_call,
                token_count=0,
                reason_codes=("state_change_already_represented_or_not_model_material",),
            )
            self._metrics["stable_context_abstentions"] += 1
            self._record("provider_context", **frame.as_dict())
            return frame
        if self._snapshot.current_failure is not None:
            kind = ContextFrameKind.CRITICAL
        elif self._last_dispatched_version == 0:
            kind = ContextFrameKind.INITIAL
        elif self._last_dispatched_version != self._snapshot.version:
            kind = ContextFrameKind.DELTA
        else:
            kind = ContextFrameKind.DELTA
        ceiling = min(
            max_tokens,
            512 if kind in {ContextFrameKind.INITIAL, ContextFrameKind.CRITICAL} else 256,
        )
        frame_rows = self._frame_lines(
            kind,
            include_advisory_obligations=include_advisory_obligations,
        )
        repository_fact = any(
            row[2].get("authority") == EvidenceAuthority.CERTIFIED_RELATION.value
            for row in frame_rows
        )
        decisive_fact = any(bool(row[2].get("decisive")) for row in frame_rows)
        header = (
            "Task-decisive context:"
            if decisive_fact
            else (
                "Repository facts for the next decision:"
                if repository_fact
                else "Current task execution status:"
            )
        )
        selected: list[str] = []
        claim_ids: list[str] = []
        claim_metadata: list[dict[str, Any]] = []
        visible_strings: list[str] = []

        def collect_visible(value: Any) -> None:
            if isinstance(value, str):
                visible_strings.append(" ".join(value.split()))
            elif isinstance(value, Mapping):
                for key, item in value.items():
                    if key != "extra":
                        collect_visible(item)
            elif isinstance(value, Sequence) and not isinstance(
                value, (str, bytes, bytearray)
            ):
                for item in value:
                    collect_visible(item)

        for message in provider_messages:
            collect_visible(message.get("content"))
        provider_blob = "\n".join(visible_strings)
        provider_known_claims = 0
        for claim_id, line, metadata in frame_rows:
            if claim_id in self._exposed_claim_ids:
                continue
            known_texts = tuple(
                " ".join(str(item).split())
                for item in metadata.get("_known_texts", ())
                if str(item).strip()
            )
            line_normalized = " ".join(line.split())
            already_in_provider = bool(
                provider_blob
                and (
                    (line_normalized and line_normalized in provider_blob)
                    or any(item in provider_blob for item in known_texts)
                )
            )
            if already_in_provider:
                provider_known_claims += 1
                continue
            candidate = "\n".join((header, *selected, line))
            if token_counter(candidate) > ceiling:
                continue
            # Independent byte bound prevents a pathological tokenizer mismatch.
            if len(candidate.encode("utf-8")) > 4_096:
                continue
            selected.append(line)
            claim_ids.append(claim_id)
            claim_metadata.append(
                {
                    "claim_id": claim_id,
                    **{
                        key: value
                        for key, value in metadata.items()
                        if not key.startswith("_")
                    },
                    # Delivered claims were filtered against retained provider text.
                    "known_to_model": False,
                    "novel_to_provider_view": True,
                }
            )
        rendered = "\n".join((header, *selected)) if selected else ""
        token_count = token_counter(rendered) if rendered else 0
        if rendered:
            reason_codes = (
                self._snapshot.bootstrap_mode.value,
                *(
                    ("provider_history_already_contains_evidence",)
                    if provider_known_claims
                    else ()
                ),
            )
        elif provider_known_claims:
            reason_codes = ("provider_history_already_contains_evidence",)
        elif self._last_dispatched_version == 0 and not frame_rows:
            has_certified_neighbor = any(
                item.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
                and item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
                for item in self._catalog.items
            )
            reason_codes = (
                ("no_material_certified_localization",)
                if has_certified_neighbor
                else ("no_certified_related_file",)
            )
        else:
            reason_codes = ("state_change_already_represented_or_not_model_material",)
        focus_id = self._snapshot.current_focus_id or self._snapshot.primary_focus_id
        focus = self._item(focus_id)
        selected_evidence: tuple[dict[str, Any], ...] = ()
        if focus is not None and focus.source_claim_id and focus.source_claim_id in claim_ids:
            selected_evidence = (
                {
                    "path": focus.path,
                    "start_line": focus.source_start_line,
                    "end_line": focus.source_end_line,
                    "symbol": focus.symbol,
                    "claim_id": focus.source_claim_id,
                    "support_kind": (
                        focus.evidence_authority.value
                    ),
                    "retrieval_rank": focus.retrieval_rank,
                    "supporting_channels": list(focus.support_channels),
                    "origin": focus.origin.value,
                    "authority": focus.evidence_authority.value,
                    "novel_to_provider_view": True,
                    "known_to_model": False,
                    "materiality_reason": "newly_certified_related_file",
                    "source_revision": current_source_revision,
                    "origin_revision": focus.origin_revision,
                    "relation_endpoint": focus.path,
                    "declared_validation_id": "",
                },
            )
        frame = PersistentContextFrame(
            kind=kind if rendered else ContextFrameKind.NONE,
            rendered_text=rendered,
            claim_ids=tuple(claim_ids),
            state_version=self._snapshot.version,
            source_revision=current_source_revision,
            provider_call=provider_call,
            token_count=token_count,
            reason_codes=reason_codes,
            selected_evidence=selected_evidence,
            claim_metadata=tuple(claim_metadata),
        )
        self._record("provider_context", **frame.as_dict())
        return frame

    def mark_context_dispatched(self, frame: PersistentContextFrame) -> bool:
        """Commit exposure only after the provider request begins dispatch.

        Request-wide contribution packing can reject a compiled frame. Such a
        frame was never visible and must remain eligible on the next call.
        """

        if frame.kind is ContextFrameKind.NONE or not frame.rendered_text:
            self._record(
                "provider_context_dispatch",
                disposition="empty_frame",
                provider_call=frame.provider_call,
            )
            return False
        if (
            frame.state_version != self._snapshot.version
            or frame.source_revision != self._snapshot.source_revision
        ):
            self._metrics["stale_rejections"] += 1
            self._record(
                "provider_context_dispatch",
                disposition="stale_frame",
                provider_call=frame.provider_call,
                frame_state_version=frame.state_version,
                current_state_version=self._snapshot.version,
            )
            return False
        self._last_dispatched_version = frame.state_version
        self._exposed_claim_ids.update(frame.claim_ids)
        self._metrics["context_dispatches"] += 1
        self._record(
            "provider_context_dispatch",
            disposition="dispatched",
            provider_call=frame.provider_call,
            state_version=frame.state_version,
            claim_ids=list(frame.claim_ids),
        )
        return True

    def evaluate_completion(self, *, current_source_revision: str) -> dict[str, Any]:
        open_ids = tuple(
            item.obligation_id
            for item in self._snapshot.obligations
            if item.status is ObligationStatus.OPEN and item.blocking
        )
        advisory_ids = tuple(
            item.obligation_id
            for item in self._snapshot.obligations
            if item.status is ObligationStatus.OPEN and not item.blocking
        )
        ready = bool(
            current_source_revision == self._snapshot.source_revision
            and self._snapshot.files_modified
            and self._snapshot.declared_validation.status is StateValidationStatus.PASS
            and self._snapshot.declared_validation.source_revision == current_source_revision
            and self._snapshot.completion_readiness is CompletionReadiness.READY
            and not open_ids
            and self._snapshot.current_failure is None
        )
        receipt = {
            "ready": ready,
            "source_revision": current_source_revision,
            "open_obligation_ids": list(open_ids),
            "open_advisory_ids": list(advisory_ids),
            "observed_validation_status": self._snapshot.observed_validation.status.value,
            "declared_validation_status": self._snapshot.declared_validation.status.value,
            "completion_readiness": self._snapshot.completion_readiness.value,
            "state_version": self._snapshot.version,
        }
        self._record("completion", **receipt)
        return receipt


__all__ = [
    "BootstrapMode",
    "BootstrapCatalog",
    "BootstrapCatalogItem",
    "BootstrapSelection",
    "BootstrapStatus",
    "CatalogItemKind",
    "ContextFrameKind",
    "CompletionReadiness",
    "CurrentFocus",
    "CurrentFocusKind",
    "ObligationStatus",
    "PERSISTENT_STATE_FIELD_AUTHORITIES",
    "PersistentContextFrame",
    "PersistentExecutionState",
    "PersistentExecutionStateEngine",
    "PreflightStateProjection",
    "StateFieldAuthority",
    "StateObligation",
    "StatePhase",
    "StateValidation",
    "StateValidationStatus",
    "build_bootstrap_catalog",
    "build_bootstrap_messages",
    "build_select_catalog_tool",
    "bootstrap_args_preview",
    "bootstrap_visible_item_ids",
    "attempted_bootstrap_item_ids",
    "deterministic_bootstrap_fallback",
    "deterministic_bootstrap_selection",
    "parse_bootstrap_selection",
    "SELECT_CATALOG_TOOL_NAME",
]
