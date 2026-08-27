"""Benchmark treatments for the common coding-agent scaffold.

Treatments may add bounded evidence and record receipts.  They cannot select,
rewrite, reject, retry, or execute an agent action and they make no provider
calls.  This keeps model, prompt, tool policy, and step budget arm-neutral.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.dense_semantic_index import PersistentDenseSemanticIndex
from gt_engine.graph_db_projection import PersistedGraphProjector, ProjectionStatus
from gt_engine.hybrid_repository import build_query_hybrid_repository
from gt_engine.hybrid_retrieval import RetrievalIntent
from gt_engine.public_surface import PublicSurfaceResolver
from gt_engine.repository_context_compiler import (
    ContextCompileRequest,
    ContextEvidenceItem,
    ContextStatus,
    GTContextPacket,
    RepositoryContextCompiler,
)
from gt_engine.repository_graph_service import GraphStatus, RepositoryGraphService
from gt_engine.snowflake_onnx import SnowflakeOnnxDenseBackend
from gt_engine.task_contract import extract_task_contract


class TreatmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"


class FeatureState(StrEnum):
    NOT_TRIGGERED = "NOT_TRIGGERED"
    CANDIDATE = "CANDIDATE"
    DELIVERED = "DELIVERED"
    AVAILABLE_TO_AGENT = "AVAILABLE_TO_AGENT"
    FOLLOWED = "FOLLOWED"
    EDITED = "EDITED"
    VALIDATED = "VALIDATED"
    CONTRADICTED = "CONTRADICTED"
    IGNORED = "IGNORED"


_FEATURE_NAMES = (
    "exact_edit_targets",
    "implementation_owner_candidates",
    "ambiguous_identity",
    "inspection_candidates",
    "public_surface",
    "integration",
    "supporting_files",
    "semantic_facts",
    "process",
    "impact",
    "affected_tests",
    "validation",
    "proposed_new_file",
    "uncovered_requirement",
    "uncovered_facet",
)


class TreatmentUnavailableError(RuntimeError):
    """Raised before provider use when a requested treatment is unavailable."""


_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])((?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.-]+"
    r"|[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|c|cc|cpp|h|hpp|rb|php|swift|kt|kts|scala|sh|yml|yaml))"
)
_DIAGNOSTIC_LINE = re.compile(
    r"(?i)(?:^traceback \(most recent call last\):|^failed\b|^e\s+|"
    r"\berror(?:\[[A-Z0-9_-]+\])?:|\b(?:exception|error):\s)"
)
_VALIDATION_COMMAND = re.compile(
    r"(?i)(?:\bpytest\b|\btox\b|\bgo\s+test\b|\bcargo\s+test\b|"
    r"\b(?:npm|pnpm|yarn)\s+test\b|\bmvn(?:w)?\b|\bgradle(?:w)?\b|\bmake\s+test\b)"
)
_VALIDATION_SUCCESS = re.compile(
    r"(?i)(?:\b\d+\s+passed\b|\btests?\s+passed\b|\bbuild\s+success(?:ful)?\b|^ok\b)"
)
_INSPECTION_COMMAND = re.compile(
    r"(?i)(?:^|\s|&&|;)(?:cat|bat|head|tail|less|more|nl)\b|"
    r"(?:^|\s|&&|;)sed\s+(?!-i\b)|"
    r"(?:^|\s|&&|;)(?:rg|grep)\s+(?!--files\b)|"
    r"\bgit\s+(?:diff|show|status)\b"
)


def _bounded_token_count(text: str) -> int:
    """Deterministic conservative provider-token approximation."""

    count = 0
    for token in re.findall(r"\w+|[^\w\s]", str(text or ""), re.UNICODE):
        count += max(1, (len(token) + 3) // 4) if re.fullmatch(r"\w+", token, re.UNICODE) else 1
    return count


def _normalize_relative_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


@dataclass(slots=True)
class BareTreatment:
    treatment_id: str = field(default="bare", init=False)

    def prepare(self, task: str) -> str:
        return ""

    def before_model_call(self, iteration: int) -> str:
        return ""

    def after_action(
        self,
        name: str,
        arguments: dict[str, Any],
        output: str,
        is_error: bool,
    ) -> ObservationAugmentation | None:
        return None

    def after_actions(
        self,
        observations: tuple[ActionObservation, ...],
    ) -> ObservationAugmentation | None:
        """Observe one complete provider turn and emit at most one delta.

        The default preserves compatibility for simple treatments. The GT
        treatment overrides this method so graph refresh and delivery happen
        once after all actions from the same assistant response.
        """

        augmentation = None
        for observation in observations:
            candidate = self.after_action(
                observation.name,
                observation.arguments,
                observation.output,
                observation.is_error,
            )
            if candidate is not None:
                augmentation = candidate
        return augmentation

    def finalize(self, result: Any) -> dict[str, Any]:
        return {
            "schema": "gt.treatment_receipt.v4",
            "treatment": self.treatment_id,
            "provider_calls": 0,
            "treatment_provider_calls": 0,
            "treatment_status": TreatmentStatus.NOT_APPLICABLE.value,
            "graph_available": False,
            "graph_status": "NOT_APPLICABLE",
            "delivery_count": 0,
            "delivery_calls": [],
            "delivery_char_count": 0,
            "evidence_items_delivered": 0,
            "context_compile_count": 0,
            "retrieval_channel_count": 0,
            "action_count": 0,
            "degraded_reasons": [],
            "errors": [],
        }


@dataclass(frozen=True, slots=True)
class ActionObservation:
    """One immutable action/result pair inside a Mini-SWE provider turn."""

    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class ObservationAugmentation:
    """Context attached to the exact tool observation that caused it.

    ``raw_output_sha256`` binds the unmodified environment output.  The runner
    may render ``content`` beside that output, but the treatment never rewrites
    the action or its result.
    """

    content: str
    raw_output_sha256: str
    context_sha256: str
    delivery_index: int
    source_revision: str
    context_token_count: int = 0
    delivered_before_call: int = 0
    serialized_claim_ids: tuple[str, ...] = ()
    provider_visible_feature_counts: dict[str, int] = field(default_factory=dict)
    observation_count: int = 1
    turn_observations_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.delivery_receipt.v3",
            "delivery_index": self.delivery_index,
            "source_revision": self.source_revision,
            "raw_output_sha256": self.raw_output_sha256,
            "context_sha256": self.context_sha256,
            "same_observation": True,
            "context_token_count": self.context_token_count,
            "delivered_before_call": self.delivered_before_call,
            "serialized_claim_ids": list(self.serialized_claim_ids),
            "provider_visible_feature_counts": dict(self.provider_visible_feature_counts),
            "observation_count": self.observation_count,
            "turn_observations_sha256": self.turn_observations_sha256,
        }


@dataclass(slots=True)
class GroundTruthTreatment(BareTreatment):
    root: str | Path = "."
    state_dir: str | Path | None = None
    start_char_budget: int = 6_000
    update_char_budget: int = 4_000
    max_delivery_count: int = 4
    max_update_delivery_count: int = 3
    start_token_budget: int = 500
    update_token_budget: int = 350
    total_context_token_budget: int = 1_200
    retrieval_mode: str | None = None
    treatment_id: str = field(default="groundtruth", init=False)
    service: RepositoryGraphService = field(init=False, repr=False)
    compiler: RepositoryContextCompiler = field(init=False, repr=False)
    task: str = field(default="", init=False)
    treatment_status: TreatmentStatus = field(default=TreatmentStatus.FAILED, init=False)
    delivery_count: int = field(default=0, init=False)
    context_compile_count: int = field(default=0, init=False)
    retrieval_channel_count: int = field(default=0, init=False)
    action_count: int = field(default=0, init=False)
    evidence_items_delivered: int = field(default=0, init=False)
    suppressed_inspection_only_updates: int = field(default=0, init=False)
    initial_delivery_disposition: str = field(default="NOT_ATTEMPTED", init=False)
    initial_delivery_reasons: list[str] = field(default_factory=list, init=False)
    delivery_char_count: int = field(default=0, init=False)
    delivery_calls: list[int] = field(default_factory=list, init=False)
    delivery_receipts: list[dict[str, Any]] = field(default_factory=list, init=False)
    provider_delivery_receipts: list[dict[str, Any]] = field(default_factory=list, init=False)
    current_provider_call: int = field(default=1, init=False)
    errors: list[str] = field(default_factory=list, init=False)
    delivered_claim_ids: set[str] = field(default_factory=set, init=False, repr=False)
    active_paths: list[str] = field(default_factory=list, init=False, repr=False)
    changed_paths: list[str] = field(default_factory=list, init=False, repr=False)
    diagnostics: list[str] = field(default_factory=list, init=False, repr=False)
    validation_state: str = field(default="unknown", init=False, repr=False)
    context_dirty: bool = field(default=False, init=False, repr=False)
    initial_context: str = field(default="", init=False, repr=False)
    _prepared_task: str | None = field(default=None, init=False, repr=False)
    _prepared_context: str = field(default="", init=False, repr=False)
    _prepare_complete: bool = field(default=False, init=False, repr=False)
    dense_index: PersistentDenseSemanticIndex | None = field(default=None, init=False, repr=False)
    dense_receipt: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    dense_error: str = field(default="", init=False, repr=False)
    dense_query_receipts: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    projection_receipts: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    compile_receipts: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    feature_states: dict[str, str] = field(default_factory=dict, init=False)
    feature_paths: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    feature_content_identities: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False, repr=False
    )
    feature_transitions: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _last_delivery_claim_ids: tuple[str, ...] = field(default=(), init=False, repr=False)
    _last_delivery_feature_counts: dict[str, int] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_delivery_before_call: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.feature_states.update(
            {name: FeatureState.NOT_TRIGGERED.value for name in _FEATURE_NAMES}
        )
        if self.state_dir is None:
            override = str(os.environ.get("GT_STATE_DIR") or "").strip()
            if override:
                self.state_dir = override
        self.service = RepositoryGraphService(self.root, state_dir=self.state_dir)
        self.compiler = RepositoryContextCompiler()
        mode = (
            str(self.retrieval_mode or os.environ.get("GT_RETRIEVAL_MODE") or "hybrid_if_available")
            .strip()
            .lower()
        )
        if mode not in {"hybrid_required", "hybrid_if_available", "sparse_only"}:
            raise ValueError(f"unsupported GT retrieval mode: {mode}")
        self.retrieval_mode = mode
        if mode != "sparse_only":
            model_dir = str(os.environ.get("GT_DENSE_MODEL_DIR") or "").strip()
            if not model_dir:
                self.dense_error = "dense_model_not_configured"
            else:
                try:
                    backend = SnowflakeOnnxDenseBackend.from_directory(model_dir)
                    self.dense_index = PersistentDenseSemanticIndex(
                        self.service.root,
                        backend=backend,
                        state_dir=self.service.state_dir,
                    )
                except Exception as exc:  # noqa: BLE001 - readiness is explicit below
                    self.dense_error = f"dense_backend_unavailable:{type(exc).__name__}"

    def _feature_transition(
        self,
        feature: str,
        state: FeatureState,
        *,
        paths: tuple[str, ...] = (),
    ) -> None:
        order = {
            FeatureState.NOT_TRIGGERED.value: 0,
            FeatureState.CANDIDATE.value: 1,
            FeatureState.DELIVERED.value: 2,
            FeatureState.AVAILABLE_TO_AGENT.value: 3,
            FeatureState.IGNORED.value: 4,
            FeatureState.FOLLOWED.value: 5,
            FeatureState.EDITED.value: 6,
            FeatureState.CONTRADICTED.value: 7,
            FeatureState.VALIDATED.value: 8,
        }
        current = self.feature_states.get(feature, FeatureState.NOT_TRIGGERED.value)
        if state is FeatureState.IGNORED and current != FeatureState.AVAILABLE_TO_AGENT.value:
            return
        if order[state.value] < order[current]:
            return
        normalized_paths = tuple(
            dict.fromkeys(_normalize_relative_path(path) for path in paths if path)
        )
        # Candidate generation is private compiler state. Only paths whose
        # facts actually crossed the provider boundary may participate in
        # follow-through, edit, or validation attribution.
        if normalized_paths and state is not FeatureState.CANDIDATE:
            self.feature_paths.setdefault(feature, set()).update(normalized_paths)
        if current == state.value:
            return
        self.feature_states[feature] = state.value
        self.feature_transitions.append(
            {
                "feature": feature,
                "from": current,
                "to": state.value,
                "action_count": self.action_count,
            }
        )

    def _feature_content_identity(self, path: str) -> str:
        root = Path(self.service.root).resolve()
        candidate = (root / _normalize_relative_path(path)).resolve()
        if not candidate.is_relative_to(root):
            return "outside_repository"
        try:
            payload = candidate.read_bytes()
        except FileNotFoundError:
            return "missing"
        except OSError as exc:
            return f"unreadable:{type(exc).__name__}"
        return hashlib.sha256(payload).hexdigest()

    def _snapshot_feature_content(self, feature: str, paths: tuple[str, ...]) -> None:
        identities = self.feature_content_identities.setdefault(feature, {})
        for path in paths:
            identities.setdefault(path, self._feature_content_identity(path))

    def _ensure_dense_ready(self) -> None:
        if self.retrieval_mode == "sparse_only":
            self.dense_receipt = {
                "status": "DISABLED",
                "query_ready": False,
                "reason": "sparse_only_requested",
            }
            return
        if self.dense_index is None:
            self.dense_receipt = {
                "status": "DEGRADED",
                "query_ready": False,
                "reason": self.dense_error or "dense_index_unavailable",
            }
        else:
            self.dense_receipt = self.dense_index.ensure().as_dict()
        if self.retrieval_mode == "hybrid_required" and not self.dense_receipt.get("query_ready"):
            receipt = self.service.status()
            if self._not_applicable(receipt):
                # A repository can become empty or unsupported after the
                # agent's own edit (for example, a source-only task that
                # deletes its last indexable file).  Preserve that fact as an
                # explicit abstention, but do not abort the stock agent loop;
                # there is no graph-derived claim to deliver and the agent can
                # continue with its own observations.
                self._abstain(
                    "dense_retrieval_required:"
                    + str(self.dense_receipt.get("reason") or self.dense_receipt.get("status"))
                )
                return
            raise self._unavailable(
                receipt,
                "dense_retrieval_required:"
                + str(self.dense_receipt.get("reason") or self.dense_receipt.get("status")),
            )

    @staticmethod
    def _not_applicable(receipt: Any) -> bool:
        reasons = " ".join(str(item) for item in getattr(receipt, "degraded_reasons", ())).lower()
        return int(getattr(receipt, "files_attempted", -1)) == 0 and any(
            marker in reasons
            for marker in ("no_supported_source", "no_indexable", "unsupported_language")
        )

    def _unavailable(self, receipt: Any, reason: str) -> TreatmentUnavailableError:
        self.treatment_status = (
            TreatmentStatus.NOT_APPLICABLE
            if self._not_applicable(receipt)
            else TreatmentStatus.FAILED
        )
        error = f"{self.treatment_status.value}:{reason}"
        self.errors.append(error)
        return TreatmentUnavailableError(error)

    def _abstain(self, reason: str) -> str:
        """Record a genuinely unsupported treatment without aborting the agent."""
        self.treatment_status = TreatmentStatus.NOT_APPLICABLE
        self.errors.append(f"NOT_APPLICABLE:{reason}")
        self.context_dirty = False
        return ""

    def _abstain_initial_delivery(self, *reasons: str) -> str:
        """Deliver nothing initially while keeping a healthy substrate active."""

        self.initial_delivery_disposition = "ABSTAINED"
        self.initial_delivery_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
        self.context_dirty = False
        return ""

    def _context(self, *, update: bool, budget: int) -> GTContextPacket:
        receipt = self.service.status()
        dense_candidates: tuple[tuple[str, float], ...] = ()
        if self.dense_index is not None:
            contract = extract_task_contract(self.task)
            query_rows: list[tuple[str, str]] = [
                (obligation.obligation_id, obligation.text)
                for obligation in contract.obligations[:6]
                if obligation.text.strip()
            ]
            if not query_rows:
                query_rows.append(("task", self.task))
            dynamic_query = "\n".join(
                item
                for item in (
                    *self.diagnostics[-6:],
                    *self.active_paths[-10:],
                    *self.changed_paths[-10:],
                )
                if item
            )
            if dynamic_query:
                query_rows.append(("observed-state", dynamic_query))
            unique_queries = tuple(
                dict.fromkeys(
                    (identifier, text.strip()) for identifier, text in query_rows if text.strip()
                )
            )[:8]
            dense_queries = []
            for query_id, query_text in unique_queries:
                dense_query = self.dense_index.query(query_text, limit=8)
                dense_queries.append(dense_query)
                self.dense_query_receipts.append(
                    {
                        "query_id": query_id,
                        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
                        "query_ready": dense_query.query_ready,
                        "status": dense_query.status.value,
                        "source_revision": dense_query.source_revision,
                        "model_identity": dense_query.model_identity,
                        "candidate_count": len(dense_query.candidates),
                        "candidate_paths": [
                            candidate.path for candidate in dense_query.candidates[:8]
                        ],
                        "degraded_reasons": list(dense_query.degraded_reasons),
                    }
                )
            self.dense_query_receipts = self.dense_query_receipts[-64:]
            ready_queries = [query for query in dense_queries if query.query_ready]
            if ready_queries:
                rrf_scores: dict[str, float] = {}
                similarity_scores: dict[str, float] = {}
                for query in ready_queries:
                    for rank, candidate in enumerate(query.candidates, start=1):
                        rrf_scores[candidate.path] = rrf_scores.get(candidate.path, 0.0) + 1.0 / (
                            60 + rank
                        )
                        similarity_scores[candidate.path] = max(
                            similarity_scores.get(candidate.path, 0.0),
                            float(candidate.score),
                        )
                dense_candidates = tuple(
                    (path, similarity_scores[path])
                    for path in sorted(
                        rrf_scores,
                        key=lambda candidate_path: (
                            -rrf_scores[candidate_path],
                            -similarity_scores[candidate_path],
                            candidate_path.lower(),
                            candidate_path,
                        ),
                    )[:16]
                )
            failed_queries = [query for query in dense_queries if not query.query_ready]
            if failed_queries and self.retrieval_mode == "hybrid_required":
                dense_query = failed_queries[0]
                # A normal source edit can make the persisted dense index
                # temporarily stale while the graph has already been
                # refreshed.  Do not abort the treatment or deliver stale
                # vectors: updates may use the current sparse/structural
                # graph projection with an explicit degradation receipt.
                # Initial startup still fails closed when hybrid retrieval is
                # required and no dense index is available.
                if update and dense_query.status.value == "STALE":
                    reason = ",".join(dense_query.degraded_reasons) or "dense_index_stale"
                    self.errors.append(f"dense_retrieval_degraded_on_update:{reason}")
                    self.dense_receipt = {
                        **self.dense_receipt,
                        "status": "DEGRADED",
                        "query_ready": False,
                        "reason": reason,
                    }
                else:
                    raise self._unavailable(
                        receipt,
                        "dense_query_not_ready:" + ",".join(dense_query.degraded_reasons),
                    )
        state = ContextCompileRequest(
            task=self.task,
            source_revision=receipt.source_revision,
            graph_revision=receipt.graph_checksum_or_identity,
            intent=(
                RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE
                if self.diagnostics
                else RetrievalIntent.CHANGE_IMPACT
                if update and self.changed_paths
                else RetrievalIntent.IMPLEMENTATION_CONTEXT
            ),
            active_paths=tuple(self.active_paths),
            changed_paths=tuple(self.changed_paths),
            diagnostics=tuple(self.diagnostics[-12:]),
            validation_state=self.validation_state,
            previously_exposed_claims=tuple(sorted(self.delivered_claim_ids)),
            token_budget=400 if update else 1_000,
            character_budget=max(1, budget),
            dense_candidates=dense_candidates,
            dense_index_receipt=dict(self.dense_receipt),
            retrieval_mode=str(self.retrieval_mode),
        )
        repository = build_query_hybrid_repository(
            self.service.root,
            self.service.graph_path,
            state.retrieval_state(),
            candidate_limit=128,
            additional_candidate_paths=tuple(path for path, _score in dense_candidates),
        )
        packet = self.compiler.compile(repository, state)
        packet = self._resolve_public_surfaces(packet)
        packet = self._project_persisted_graph(packet)
        self.context_compile_count += 1
        self.retrieval_channel_count += packet.retrieval_channel_count
        return packet

    def _resolve_public_surfaces(self, packet: GTContextPacket) -> GTContextPacket:
        """Add existing manifest/language surfaces as inspection-only evidence."""

        if packet.status is not ContextStatus.READY:
            return packet
        candidates = PublicSurfaceResolver(self.service.root).resolve(
            tuple(item.path for item in packet.primary_edit_targets)
        )
        existing = {item.path for item in packet.inspection_public_surface}
        primary_facet_ids = tuple(
            dict.fromkeys(
                facet_id for item in packet.primary_edit_targets for facet_id in item.facet_ids
            )
        )
        additions: list[ContextEvidenceItem] = []
        for candidate in candidates:
            if candidate.path in existing:
                continue
            source = Path(self.service.root, candidate.path)
            try:
                body = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            digest = hashlib.sha256(
                "\0".join(
                    (
                        "public_surface",
                        candidate.path,
                        str(packet.repository_identity.get("source_revision") or ""),
                        hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    )
                ).encode("utf-8")
            ).hexdigest()
            additions.append(
                ContextEvidenceItem(
                    kind="public_surface",
                    path=candidate.path,
                    start_line=1,
                    end_line=max(1, body.count("\n") + 1),
                    symbol="",
                    relation="",
                    confidence=1.0,
                    verification_status="verified_source_identity",
                    source_revision=str(packet.repository_identity.get("source_revision") or ""),
                    graph_revision=str(packet.repository_identity.get("graph_revision") or ""),
                    evidence_sha256=digest,
                    decision_reason=candidate.reason,
                    completeness="existing_public_surface_file",
                    source_excerpt=body.strip()[:400],
                    localization_role="PUBLIC_SURFACE",
                    facet_ids=primary_facet_ids,
                )
            )
        if not additions:
            return packet
        coverage = dict(packet.coverage)
        coverage["deterministic_public_surfaces"] = [
            {"path": item.path, "reason": item.decision_reason} for item in additions
        ]
        return replace(
            packet,
            inspection_public_surface=tuple(
                {
                    item.path: item for item in (*additions, *packet.inspection_public_surface)
                }.values()
            ),
            evidence_items=tuple(
                {
                    item.evidence_sha256: item for item in (*packet.evidence_items, *additions)
                }.values()
            ),
            coverage=coverage,
        )

    def _project_persisted_graph(self, packet: GTContextPacket) -> GTContextPacket:
        """Project up to four role-diverse anchors from one graph session."""

        if packet.status is not ContextStatus.READY:
            return packet
        groups = (
            packet.primary_edit_targets,
            packet.inspection_implementation_owners,
            packet.inspection_public_surface,
            packet.inspection_integration,
            packet.inspection_candidates,
        )

        def eligible(item: ContextEvidenceItem) -> bool:
            return bool(
                item.symbol
                and (
                    item.facet_ids
                    or item in packet.primary_edit_targets
                    and item.decision_reason == "exact_task_path"
                )
            )

        anchors_list: list[ContextEvidenceItem] = []
        seen_anchors: set[tuple[str, str]] = set()

        def add(item: ContextEvidenceItem) -> None:
            key = (item.path, item.symbol)
            if eligible(item) and key not in seen_anchors and len(anchors_list) < 4:
                seen_anchors.add(key)
                anchors_list.append(item)

        # One representative per available role first, then spend remaining
        # capacity in stable group order. Three edit candidates must not crowd
        # public/integration evidence out of the graph projection session.
        for group in groups:
            if candidate := next((item for item in group if eligible(item)), None):
                add(candidate)
        for group in groups:
            for candidate in group:
                add(candidate)
        anchors = tuple(anchors_list)
        if not anchors:
            return replace(
                packet,
                execution_paths=(),
                change_surface=(),
                uncertainties=tuple(
                    dict.fromkeys((*packet.uncertainties, "graph_projection_anchor_unavailable"))
                ),
            )

        results: list[tuple[ContextEvidenceItem, Any, Any]] = []
        reasons = list(packet.uncertainties)
        try:
            with PersistedGraphProjector(self.service) as projector:
                for anchor in anchors:
                    try:
                        processes = projector.project_processes(
                            anchor.symbol, file_path=anchor.path
                        )
                        impact = projector.project_impact(anchor.symbol, file_path=anchor.path)
                    except Exception as exc:  # noqa: BLE001 - one facet fails closed
                        reasons.append(
                            "graph_projection_failed:"
                            f"{anchor.path}#{anchor.symbol}:{type(exc).__name__}"
                        )
                        continue
                    results.append((anchor, processes, impact))
        except Exception as exc:  # noqa: BLE001 - graph session fails closed
            return replace(
                packet,
                execution_paths=(),
                change_surface=(),
                uncertainties=tuple(
                    dict.fromkeys((*reasons, f"graph_projection_failed:{type(exc).__name__}"))
                ),
            )

        projection_receipt = {
            "schema": "gt.graph_projection_receipt.v2",
            "anchors": [
                {
                    "path": anchor.path,
                    "symbol": anchor.symbol,
                    "role": anchor.localization_role,
                    "process": asdict(processes.receipt),
                    "impact": asdict(impact.receipt),
                }
                for anchor, processes, impact in results
            ],
        }
        receipt_key = (
            results[0][1].receipt.source_revision if results else "",
            tuple((anchor.path, anchor.symbol) for anchor, _process, _impact in results),
        )
        if results and not any(
            (
                (item.get("anchors") or [{}])[0].get("process", {}).get("source_revision", ""),
                tuple(
                    (anchor.get("path", ""), anchor.get("symbol", ""))
                    for anchor in item.get("anchors", [])
                ),
            )
            == receipt_key
            for item in self.projection_receipts
        ):
            self.projection_receipts.append(projection_receipt)

        exposed = self.delivered_claim_ids
        process_candidates: list[list[tuple[str, str]]] = []
        impact_candidates: list[list[tuple[str, str, str]]] = []
        affected_tests: list[str] = []
        truncated = packet.truncated
        coverage = dict(packet.coverage)
        coverage["persisted_graph_projections"] = []

        for anchor, processes, impact in results:
            coverage["persisted_graph_projections"].append(
                {
                    "anchor": {
                        "path": anchor.path,
                        "symbol": anchor.symbol,
                        "role": anchor.localization_role,
                    },
                    "process": asdict(processes.receipt),
                    "impact": asdict(impact.receipt),
                }
            )
            if processes.status is not ProjectionStatus.READY:
                reasons.append(
                    f"process_projection_{processes.status.value.lower()}:"
                    f"{anchor.path}#{anchor.symbol}"
                )
            if impact.status is not ProjectionStatus.READY:
                reasons.append(
                    f"impact_projection_{impact.status.value.lower()}:{anchor.path}#{anchor.symbol}"
                )
            reasons.extend(processes.receipt.truncation_reasons)
            reasons.extend(impact.receipt.truncation_reasons)
            truncated = bool(truncated or processes.receipt.truncated or impact.receipt.truncated)

            anchor_processes: list[tuple[str, str]] = []
            if processes.status is ProjectionStatus.READY:
                for process in processes.processes:
                    if process.process_id in exposed:
                        continue
                    nodes: list[str] = []
                    edge_receipts: list[str] = []
                    for index, step in enumerate(process.steps):
                        if index == 0:
                            nodes.append(f"{step.source.file_path}#{step.source.name}")
                        nodes.append(f"{step.target.file_path}#{step.target.name}")
                        receiver = f",receiver={step.receiver_type}" if step.receiver_type else ""
                        edge_receipts.append(
                            f"edge={step.evidence.edge_id},resolution="
                            f"{step.evidence.resolution_outcome}{receiver}"
                        )
                    anchor_processes.append(
                        (
                            process.process_id,
                            f"{process.process_id} lower_bound=true anchor="
                            f"{anchor.path}#{anchor.symbol} "
                            + (
                                "req=" + ",".join(anchor.facet_ids) + " "
                                if anchor.facet_ids
                                else ""
                            )
                            + " -> ".join(nodes)
                            + " ["
                            + ";".join(edge_receipts)
                            + "]",
                        )
                    )
            process_candidates.append(anchor_processes)

            anchor_impacts: list[tuple[str, str, str]] = []
            if impact.status is ProjectionStatus.READY:
                for fact in impact.impacts:
                    if fact.impact_id in exposed:
                        continue
                    receiver = f" receiver={fact.receiver_type}" if fact.receiver_type else ""
                    edge_identity = fact.evidence.edge_id or (
                        "assertion:" + str(fact.evidence.assertion_id)
                    )
                    line = (
                        f"{fact.impact_id} anchor={anchor.path}#{anchor.symbol} "
                        + ("req=" + ",".join(anchor.facet_ids) + " " if anchor.facet_ids else "")
                        + f"depth={fact.depth} {fact.relationship} "
                        f"{fact.impacted.file_path}#{fact.impacted.name} "
                        f"direction={fact.traversal_direction} edge={edge_identity}{receiver}"
                    )
                    anchor_impacts.append((fact.impact_id, fact.impacted.file_path, line))
                    if fact.impacted.is_test:
                        affected_tests.append(fact.impacted.file_path)
            impact_candidates.append(anchor_impacts)

        # Round-robin selection ensures one busy symbol cannot consume every
        # bounded row before another task facet contributes any evidence.
        selected_processes: list[tuple[str, str]] = []
        for offset in range(max((len(rows) for rows in process_candidates), default=0)):
            for rows in process_candidates:
                if offset < len(rows):
                    selected_processes.append(rows[offset])
                if len(selected_processes) >= 3:
                    break
            if len(selected_processes) >= 3:
                break
        selected_impacts: list[tuple[str, str, str]] = []
        seen_impact_ids: set[str] = set()
        for offset in range(max((len(rows) for rows in impact_candidates), default=0)):
            for rows in impact_candidates:
                if offset >= len(rows) or rows[offset][0] in seen_impact_ids:
                    continue
                seen_impact_ids.add(rows[offset][0])
                selected_impacts.append(rows[offset])
                if len(selected_impacts) >= 8:
                    break
            if len(selected_impacts) >= 8:
                break
        projection_claims = [claim for claim, _line in selected_processes]
        projection_claims.extend(claim for claim, _path, _line in selected_impacts)
        return replace(
            packet,
            execution_paths=tuple(line for _claim, line in selected_processes),
            change_surface=tuple(line for _claim, _path, line in selected_impacts),
            affected_tests=tuple(dict.fromkeys((*packet.affected_tests, *affected_tests)))[:5],
            uncertainties=tuple(dict.fromkeys(reasons)),
            coverage=coverage,
            projection_claim_ids=tuple(dict.fromkeys(projection_claims)),
            truncated=truncated,
        )

    def _render(self, *, update: bool, budget: int, delivered_before_call: int) -> str:
        update_deliveries = sum(
            item.get("kind") == "repository_update" for item in self.provider_delivery_receipts
        )
        if update and (
            self.delivery_count >= max(1, self.max_delivery_count)
            or update_deliveries >= max(0, self.max_update_delivery_count)
        ):
            self.errors.append("context_delivery_limit_reached")
            self.context_dirty = False
            return ""
        receipt = self.service.status()
        if not receipt.query_ready:
            raise self._unavailable(receipt, f"graph_not_ready:{receipt.build_status.value}")
        try:
            packet = self._context(update=update, budget=budget)
        except TreatmentUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - treatment must fail closed
            raise self._unavailable(
                receipt, f"context_compile_failed:{type(exc).__name__}"
            ) from exc
        self.compile_receipts.append(
            {
                "schema": "gt.context_compile_receipt.v1",
                "kind": "repository_update" if update else "repository_start",
                "source_revision": receipt.source_revision,
                "graph_revision": receipt.graph_checksum_or_identity,
                "retrieval_mode": self.retrieval_mode,
                "packet": packet.as_dict(),
            }
        )
        self.compile_receipts = self.compile_receipts[-16:]
        if packet.status is ContextStatus.FAILED:
            reason = ",".join(packet.uncertainties) or "context_compile_failed"
            raise self._unavailable(receipt, reason)
        if packet.status is ContextStatus.ABSTAIN:
            if not update:
                reasons = tuple(packet.uncertainties) or ("no_repository_evidence",)
                return self._abstain_initial_delivery(*reasons)
            self.context_dirty = False
            return ""
        normalized_packet = packet.as_dict()
        requirement_ids_by_facet: dict[str, tuple[str, ...]] = {}
        for requirement in normalized_packet["task_requirements"]:
            facet_id = str(requirement.get("facet_id") or "")
            requirement_id = str(requirement.get("requirement_id") or "")
            if facet_id and requirement_id:
                requirement_ids_by_facet[facet_id] = tuple(
                    dict.fromkeys(
                        (*requirement_ids_by_facet.get(facet_id, ()), requirement_id)
                    )
                )

        def scoped_requirements(facet_ids: list[str] | tuple[str, ...]) -> list[str]:
            mapped = [
                requirement_id
                for facet_id in facet_ids
                for requirement_id in requirement_ids_by_facet.get(facet_id, ())
            ]
            return list(dict.fromkeys(mapped or list(facet_ids)))

        def feature_paths(values: list[Any]) -> tuple[str, ...]:
            paths: list[str] = []
            for value in values:
                if isinstance(value, dict) and value.get("path"):
                    paths.append(str(value["path"]))
                    continue
                if isinstance(value, dict) and isinstance(value.get("candidates"), list):
                    paths.extend(
                        str(candidate["path"])
                        for candidate in value["candidates"]
                        if isinstance(candidate, dict) and candidate.get("path")
                    )
                    continue
                text = str(value or "")
                matches = tuple(_PATH_TOKEN.finditer(text))
                if matches:
                    paths.extend(match.group(1) for match in matches)
                elif "/" in text or re.search(r"\.[A-Za-z0-9]{1,5}$", text):
                    paths.append(text.split()[0])
            return tuple(dict.fromkeys(_normalize_relative_path(path) for path in paths))

        candidate_features = {
            "exact_edit_targets": normalized_packet["primary_edit_targets"],
            "implementation_owner_candidates": normalized_packet[
                "inspection_implementation_owners"
            ],
            "ambiguous_identity": normalized_packet["ambiguous_identities"],
            "inspection_candidates": normalized_packet["inspection_candidates"],
            "public_surface": normalized_packet["inspection_public_surface"],
            "integration": normalized_packet["inspection_integration"],
            "supporting_files": normalized_packet["supporting_files"],
            "semantic_facts": normalized_packet["semantic_facts"],
            "process": normalized_packet["execution_paths"],
            "impact": normalized_packet["change_surface"],
            "affected_tests": normalized_packet["affected_tests"],
            "validation": normalized_packet["validation_plan"],
            "proposed_new_file": normalized_packet["proposed_new_files"],
            "uncovered_requirement": normalized_packet["uncovered_requirements"],
            "uncovered_facet": normalized_packet["uncovered_facets"],
        }
        for feature, values in candidate_features.items():
            if values:
                self._feature_transition(
                    feature,
                    FeatureState.CANDIDATE,
                    paths=feature_paths(values),
                )

        # An inspection candidate or a loose semantic match is not an
        # instruction.  When retrieval explicitly reports that it lacks
        # independent support and the packet contains no decision-grade
        # evidence, delivering it at repository start only adds noise and can
        # anchor the agent on the wrong file.  Abstain honestly and let the
        # agent inspect the repository itself.  Real edit targets,
        # relationships, impact/test facts, or validation plans remain
        # eligible for delivery even when the graph declares limitations.
        decision_grade_initial = (
            any(
                normalized_packet[name]
                for name in (
                    "primary_edit_targets",
                    "inspection_implementation_owners",
                    "ambiguous_identities",
                    "inspection_public_surface",
                    "inspection_integration",
                    "supporting_files",
                    "execution_paths",
                    "change_surface",
                    "affected_tests",
                    "validation_plan",
                )
            )
            or any(
                item.get("kind") == "relationship" for item in normalized_packet["evidence_items"]
            )
            or any(
                item.get("decision_reason") == "task_path_phrase_inspection"
                for item in normalized_packet["inspection_candidates"]
            )
        )
        if (
            not update
            and not decision_grade_initial
            and "insufficient_independent_support" in normalized_packet["uncertainties"]
            and "no_decision_relevant_evidence" in normalized_packet["uncertainties"]
            and "no_complete_evidence" in normalized_packet["uncertainties"]
        ):
            self.suppressed_inspection_only_updates += 1
            return self._abstain_initial_delivery("no_decision_grade_evidence")

        def compact_target(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "path": item["path"],
                "lines": [item["start_line"], item["end_line"]],
                "symbol": item["symbol"],
                "source_excerpt": item["source_excerpt"],
                "evidence_id": item["evidence_sha256"][:16],
                "decision_reason": item["decision_reason"],
                "role": item["localization_role"],
                "requirements": scoped_requirements(item["facet_ids"]),
            }

        # The normalized packet retains every field for local consumers. The
        # provider view binds all rows to one packet revision and carries each
        # claim once, avoiding repeated provenance and excerpts.
        raw_coverage = normalized_packet["coverage"]
        raw_dense = raw_coverage.get("dense_index") or {}
        provider_coverage = {
            "documents_considered": raw_coverage.get("documents_considered", 0),
            "ranked_files": raw_coverage.get("ranked_files", 0),
            "certified_edges_selected": raw_coverage.get("certified_edges_selected", 0),
            "rejected_edges": raw_coverage.get("rejected_edges", 0),
            "retrieval_mode": raw_coverage.get("retrieval_mode", self.retrieval_mode),
            "dense_candidates": raw_coverage.get("dense_candidates", 0),
            "dense_status": raw_dense.get("status", "UNAVAILABLE"),
            "dense_query_ready": bool(raw_dense.get("query_ready", False)),
        }
        raw_semantic_receipt = normalized_packet["semantic_graph_receipt"]
        semantic_receipt = {
            key: raw_semantic_receipt[key]
            for key in ("status", "language", "fact_count", "truncated")
            if key in raw_semantic_receipt
        }
        evidence_identity = {
            item["evidence_sha256"][:16]: item["evidence_sha256"]
            for group_name in (
                "primary_edit_targets",
                "inspection_implementation_owners",
                "inspection_candidates",
                "inspection_public_surface",
                "inspection_integration",
                "supporting_files",
                "evidence_items",
            )
            for item in normalized_packet[group_name]
        }
        evidence_identity.update(
            {
                item["evidence_sha256"][:16]: item["evidence_sha256"]
                for item in normalized_packet["ambiguous_identities"]
                if item.get("evidence_sha256")
            }
        )
        relationship_evidence = [
            {
                "evidence_id": item["evidence_sha256"][:16],
                "source": item["source_path"]
                + (":" + item["source_symbol"] if item["source_symbol"] else ""),
                "relation": item["relation"],
                "target": item["path"] + (":" + item["symbol"] if item["symbol"] else ""),
                "scope": item["completeness"],
                "role": item["localization_role"],
                "requirements": scoped_requirements(item["facet_ids"]),
            }
            for item in normalized_packet["evidence_items"]
            if item["kind"] == "relationship"
        ]
        semantic_evidence = [
            item for item in normalized_packet["evidence_items"] if item["kind"] == "semantic_fact"
        ]
        semantic_facts = [
            {
                "fact": fact,
                "evidence_id": semantic_evidence[index]["evidence_sha256"][:16],
                "requirements": scoped_requirements(semantic_evidence[index]["facet_ids"]),
            }
            for index, fact in enumerate(normalized_packet["semantic_facts"])
            if index < len(semantic_evidence)
        ]
        packet_dict = {
            "status": normalized_packet["status"],
            "task_facets": normalized_packet["task_facets"],
            "task_requirements": normalized_packet["task_requirements"],
            "requirement_coverage": normalized_packet["requirement_coverage"],
            "uncovered_requirements": normalized_packet["uncovered_requirements"],
            "primary_edit_targets": [
                compact_target(item) for item in normalized_packet["primary_edit_targets"]
            ],
            "inspection_implementation_owners": [
                compact_target(item)
                for item in normalized_packet["inspection_implementation_owners"]
            ],
            "inspection_candidates": [
                compact_target(item) for item in normalized_packet["inspection_candidates"]
            ],
            "inspection_public_surface": [
                compact_target(item) for item in normalized_packet["inspection_public_surface"]
            ],
            "inspection_integration": [
                compact_target(item) for item in normalized_packet["inspection_integration"]
            ],
            "ambiguous_identities": [
                {
                    **item,
                    "evidence_id": item["evidence_sha256"][:16],
                    "requirements": scoped_requirements(item.get("facet_ids") or ()),
                }
                for item in normalized_packet["ambiguous_identities"]
            ],
            "supporting_files": [
                compact_target(item) for item in normalized_packet["supporting_files"]
            ],
            "proposed_new_files": normalized_packet["proposed_new_files"],
            "uncovered_facets": normalized_packet["uncovered_facets"],
            "semantic_facts": semantic_facts,
            "semantic_graph_receipt": semantic_receipt,
            "execution_paths": normalized_packet["execution_paths"],
            "change_surface": normalized_packet["change_surface"],
            "affected_tests": normalized_packet["affected_tests"],
            "validation_plan": normalized_packet["validation_plan"],
            "uncertainties": normalized_packet["uncertainties"],
            "coverage": provider_coverage,
            "truncated": normalized_packet["truncated"],
            "relationships": relationship_evidence,
        }
        # Provider delivery is an ordered decision surface, not a dump of all
        # compiled candidates. Keep the full typed ledger in compile_receipts,
        # but expose one best inspection owner and one role-specific boundary.
        # A generic candidate is useful only when stronger exact, ambiguous,
        # owner, or certified-integration localization is absent.
        packet_dict["inspection_implementation_owners"] = packet_dict[
            "inspection_implementation_owners"
        ][:1]
        packet_dict["inspection_public_surface"] = packet_dict["inspection_public_surface"][:1]
        packet_dict["inspection_integration"] = packet_dict["inspection_integration"][:1]
        packet_dict["inspection_candidates"] = sorted(
            packet_dict["inspection_candidates"],
            key=lambda item: (
                item.get("decision_reason") != "task_path_phrase_inspection",
                not bool(item.get("requirements")),
                item.get("decision_reason") != "hybrid_rrf_inspection",
                str(item.get("path") or "").casefold(),
            ),
        )[:1]
        last_resort_inspection_candidate = packet_dict["inspection_candidates"][:1]
        decision_grade_update = any(
            packet_dict[name]
            for name in (
                "primary_edit_targets",
                "inspection_implementation_owners",
                "ambiguous_identities",
                "inspection_public_surface",
                "inspection_integration",
                "supporting_files",
                "relationships",
                "semantic_facts",
                "execution_paths",
                "change_surface",
                "affected_tests",
                "validation_plan",
            )
        ) or any(
            item.get("decision_reason") == "task_path_phrase_inspection"
            for item in packet_dict["inspection_candidates"]
        )
        if update and not decision_grade_update:
            self.suppressed_inspection_only_updates += 1
            self.context_dirty = False
            return ""

        compact_output = False
        provider_requirement_limit: int | None = None

        def provider_projection_claim(claim: str) -> str:
            """Return a packet-local claim prefix; the receipt keeps the full ID."""

            return re.sub(
                r"\b(gt-(?:process|impact)-[0-9a-fA-F]{12})[0-9a-fA-F]{4,}\b",
                r"\1",
                str(claim),
            )

        def encode() -> str:
            kind = "repository_update" if update else "repository_start"
            lines = [
                f'<groundtruth-repository-context schema="gt.agent_context.v7" kind="{kind}">',
                "RECEIPT "
                f"repository={Path(receipt.repository).name or receipt.repository} "
                f"commit={receipt.commit_sha[:12]} source={receipt.source_revision[:12]} "
                f"graph={receipt.graph_checksum_or_identity[:12]} "
                f"status={receipt.build_status.value}",
            ]
            limitations = tuple(receipt.degraded_reasons)
            if limitations:
                lines.append("LIMITATIONS " + "; ".join(limitations))

            referenced_requirements = {
                requirement
                for group_name in (
                    "primary_edit_targets",
                    "inspection_implementation_owners",
                    "ambiguous_identities",
                    "inspection_candidates",
                    "inspection_public_surface",
                    "inspection_integration",
                    "supporting_files",
                    "relationships",
                    "semantic_facts",
                )
                for item in packet_dict[group_name]
                for requirement in item.get("requirements", ())
            }
            coverage_by_requirement = {
                item["requirement_id"]: item
                for item in packet_dict["requirement_coverage"]
            }
            if packet_dict["task_requirements"]:
                referenced_rows = [
                    requirement
                    for requirement in packet_dict["task_requirements"]
                    if requirement["requirement_id"] in referenced_requirements
                    or requirement["requirement_id"] in packet_dict["uncovered_requirements"]
                ]

                def requirement_details(
                    requirement: dict[str, Any], *, value_limit: int
                ) -> tuple[str, ...]:
                    coverage = coverage_by_requirement.get(requirement["requirement_id"], {})
                    paths = list(coverage.get("paths") or ())[:value_limit]
                    details = (
                        f"intent={requirement['intent']}",
                        f"entity={requirement['entity']}",
                        f"resolution={requirement['resolution']}",
                        f"coverage={coverage.get('status', 'UNCOVERED')}",
                        f"mechanism={coverage.get('mechanism', 'NONE')}",
                    )
                    return (
                        *details,
                        *((f"paths={','.join(paths)}",) if paths else ()),
                    )

            else:
                referenced_rows = [
                    facet
                    for facet in packet_dict["task_facets"]
                    if facet["facet_id"] in referenced_requirements
                ]

                def requirement_details(
                    requirement: dict[str, Any], *, value_limit: int
                ) -> tuple[str, ...]:
                    details: list[str] = []
                    for label, key in (
                        ("exact", "exact_symbols"),
                        ("unresolved", "unresolved_symbols"),
                        ("owners", "owning_symbols"),
                    ):
                        values = requirement.get(key) or []
                        if values:
                            details.append(f"{label}=" + ",".join(values[:value_limit]))
                    return tuple(details)

            requirement_aliases: dict[str, str] = {}
            visible_facets: list[tuple[dict[str, Any], str, tuple[str, ...]]] = []
            if provider_requirement_limit is not None:
                # Several natural-language obligations can normalize to the
                # same repository requirement.  Serialize that fact once and
                # bind every duplicate facet to the same provider alias.  The
                # full facet ledger remains in the persisted packet receipt.
                aliases_by_signature: dict[tuple[str, tuple[str, ...]], str] = {}
                for requirement in referenced_rows:
                    details = requirement_details(requirement, value_limit=1)
                    if not details:
                        continue
                    identity = str(
                        requirement.get("requirement_id") or requirement.get("facet_id")
                    )
                    signature = (str(requirement.get("intent") or requirement.get("role")), details)
                    alias = aliases_by_signature.get(signature)
                    if alias is None:
                        if len(visible_facets) >= provider_requirement_limit:
                            continue
                        alias = f"R{len(visible_facets) + 1}"
                        aliases_by_signature[signature] = alias
                        visible_facets.append((requirement, alias, details))
                    requirement_aliases[identity] = alias
            else:
                visible_facets = [
                    (
                        requirement,
                        str(requirement.get("requirement_id") or requirement.get("facet_id")),
                        requirement_details(requirement, value_limit=4),
                    )
                    for requirement in referenced_rows[:12]
                ]

            for _requirement, alias, details in visible_facets:
                # A facet with no provider-readable identity only contributes
                # an opaque internal ID. Keep its mapping in the persisted
                # packet receipt, but spend provider tokens on repository
                # facts the agent can act on.
                if not details:
                    continue
                lines.append(f"REQUIREMENT {alias} " + " ".join(details))

            def requirement_text(values: list[str] | tuple[str, ...]) -> str:
                requirements = list(dict.fromkeys(values))
                if not requirements:
                    return "unscoped"
                if provider_requirement_limit is None:
                    return ",".join(requirements)
                visible = list(
                    dict.fromkeys(
                        requirement_aliases[requirement]
                        for requirement in requirements
                        if requirement in requirement_aliases
                    )
                )
                omitted = sum(
                    requirement not in requirement_aliases for requirement in requirements
                )
                if omitted:
                    visible.append(f"+{omitted}")
                return ",".join(visible) or f"+{len(requirements)}"

            def target_line(prefix: str, item: dict[str, Any]) -> str:
                location = item["path"] + ":" + str(item["lines"][0])
                if item["symbol"]:
                    location += "#" + item["symbol"]
                excerpt = " ".join(str(item.get("source_excerpt") or "").split())
                suffix = f" | {excerpt}" if excerpt else ""
                requirements = requirement_text(item.get("requirements") or ())
                if compact_output:
                    return f"{prefix} {location} claim={item['evidence_id']} req={requirements}"
                return (
                    f"{prefix} {location} claim={item['evidence_id']} "
                    f"req={requirements} "
                    f"reason={item['decision_reason']}{suffix}"
                )

            for item in packet_dict["primary_edit_targets"]:
                lines.append(target_line("EXACT_EDIT_TARGET", item))
            for item in packet_dict["inspection_implementation_owners"]:
                lines.append(
                    target_line(
                        "INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY",
                        item,
                    )
                )
            for item in packet_dict["ambiguous_identities"]:
                candidates = ",".join(
                    f"{candidate['path']}:{candidate['line']}#{candidate['symbol']}"
                    for candidate in item["candidates"]
                )
                lines.append(
                    "AMBIGUOUS_IDENTITY "
                    f"symbol={item['entity']} "
                    f"claim={item['evidence_id']} "
                    f"req={requirement_text(item.get('facet_ids') or ())} "
                    f"total={item['total_candidates']} "
                    f"truncated={str(bool(item['truncated'])).lower()} "
                    f"candidates={candidates} action={item['next_action']}"
                )
            for item in packet_dict["inspection_candidates"]:
                lines.append(target_line("INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY", item))
            for item in packet_dict["inspection_public_surface"]:
                lines.append(target_line("INSPECT_PUBLIC_SURFACE", item))
            for item in packet_dict["inspection_integration"]:
                lines.append(target_line("INSPECT_INTEGRATION", item))
            for item in packet_dict["supporting_files"]:
                lines.append(target_line("SUPPORTING_FILE", item))
            for item in packet_dict["relationships"]:
                relationship = (
                    "VERIFIED_RELATION "
                    f"{item['source']} {item['relation']} {item['target']} "
                    f"claim={item['evidence_id']} "
                    f"req={requirement_text(item.get('requirements') or ())}"
                )
                if not compact_output:
                    relationship += f" scope={item['scope']}"
                lines.append(relationship)
            for item in packet_dict["semantic_facts"]:
                lines.append(
                    f"SEMANTIC_FACT claim={item['evidence_id']} "
                    f"req={requirement_text(item.get('requirements') or ())} "
                    f"{item['fact']}"
                )

            def projection_line(item: str) -> str:
                value = item
                for requirement, alias in requirement_aliases.items():
                    value = value.replace(requirement, alias)
                if not compact_output:
                    return value
                # Edge IDs remain in the persisted projection receipt. The
                # provider needs the bounded path and claim, not a repeated
                # per-hop proof ledger that can consume the entire context
                # budget on a deep call chain.
                value = provider_projection_claim(value)
                return re.sub(r"\s+\[[^\]]+\]\s*$", "", value)

            lines.extend(
                f"BOUNDED_PROCESS {projection_line(item)}"
                for item in packet_dict["execution_paths"]
            )
            lines.extend(
                f"BOUNDED_IMPACT {projection_line(item)}" for item in packet_dict["change_surface"]
            )
            lines.extend(f"AFFECTED_TEST {item}" for item in packet_dict["affected_tests"])
            lines.extend(f"VALIDATE {item}" for item in packet_dict["validation_plan"])
            lines.extend(
                f"PROPOSED_NEW_FILE {item} fact=false" for item in packet_dict["proposed_new_files"]
            )
            uncovered_requirement_rows = {
                item["requirement_id"]: item for item in packet_dict["task_requirements"]
            }
            for requirement_id in packet_dict["uncovered_requirements"]:
                item = uncovered_requirement_rows.get(requirement_id, {})
                alias = requirement_aliases.get(requirement_id, requirement_id)
                lines.append(
                    "UNCOVERED_REQUIREMENT "
                    f"{alias} entity={item.get('entity', 'unknown')} "
                    f"intent={item.get('intent', 'unknown')}"
                )
            for item in (
                packet_dict["uncovered_facets"]
                if not packet_dict["uncovered_requirements"]
                else ()
            ):
                uncovered = str(item).strip()
                if compact_output:
                    facet_id, separator, detail = uncovered.partition(" ")
                    # The human-readable role/unresolved payload is the
                    # actionable absence fact.  Its opaque facet identity and
                    # complete binding remain in the packet receipt; emitting
                    # a requirement alias here can misleadingly imply that an
                    # uncovered obligation was satisfied by a delivered fact.
                    uncovered = detail if separator else facet_id
                lines.append(f"UNCOVERED_FACET {uncovered}")
            lines.extend(f"UNCERTAINTY {item}" for item in packet_dict["uncertainties"])
            coverage = packet_dict["coverage"]
            if compact_output:
                lines.append(
                    "RETRIEVAL "
                    f"{coverage.get('retrieval_mode', self.retrieval_mode)} "
                    f"truncated={str(bool(packet_dict['truncated'])).lower()}"
                )
            else:
                lines.append(
                    "RETRIEVAL "
                    f"mode={coverage.get('retrieval_mode', self.retrieval_mode)} "
                    f"dense={coverage.get('dense_status', 'UNAVAILABLE')} "
                    f"dense_ready={str(bool(coverage.get('dense_query_ready', False))).lower()} "
                    f"truncated={str(bool(packet_dict['truncated'])).lower()}"
                )
            lines.append("</groundtruth-repository-context>")
            return "\n".join(lines)

        rendered = encode()
        token_ceiling = max(1, self.update_token_budget if update else self.start_token_budget)
        delivered_tokens = sum(
            int(item.get("context_token_count") or 0) for item in self.provider_delivery_receipts
        )
        remaining_total_tokens = max(0, int(self.total_context_token_budget) - delivered_tokens)
        if remaining_total_tokens == 0:
            self.errors.append("total_context_delivery_limit_reached")
            self.context_dirty = False
            return ""
        token_ceiling = min(token_ceiling, remaining_total_tokens)

        def too_large() -> bool:
            return len(rendered) > budget or _bounded_token_count(rendered) > token_ceiling

        def bound_ambiguities(group_limit: int, candidate_limit: int) -> None:
            original = list(packet_dict["ambiguous_identities"])
            bounded = []
            for group in original[:group_limit]:
                candidates = list(group.get("candidates") or ())
                bounded.append(
                    {
                        **group,
                        "candidates": candidates[:candidate_limit],
                        "truncated": bool(
                            group.get("truncated") or len(candidates) > candidate_limit
                        ),
                    }
                )
            if len(original) > len(bounded):
                packet_dict["truncated"] = True
            packet_dict["ambiguous_identities"] = bounded

        if too_large():
            compact_output = True
            bound_ambiguities(2, 3)
            packet_dict["coverage"] = {
                "retrieval_mode": provider_coverage["retrieval_mode"],
                "dense_status": provider_coverage["dense_status"],
                "dense_query_ready": provider_coverage["dense_query_ready"],
            }
            packet_dict["semantic_graph_receipt"] = {}
            packet_dict["supporting_files"] = []
            packet_dict["inspection_implementation_owners"] = packet_dict[
                "inspection_implementation_owners"
            ][:2]
            packet_dict["inspection_candidates"] = sorted(
                packet_dict["inspection_candidates"],
                key=lambda item: item.get("decision_reason") != "task_path_phrase_inspection",
            )[:1]
            packet_dict["proposed_new_files"] = packet_dict["proposed_new_files"][:1]
            packet_dict["uncovered_requirements"] = packet_dict[
                "uncovered_requirements"
            ][:2]
            packet_dict["uncovered_facets"] = packet_dict["uncovered_facets"][:2]
            packet_dict["uncertainties"] = packet_dict["uncertainties"][:4]
            for item in packet_dict["primary_edit_targets"]:
                item["source_excerpt"] = str(item.get("source_excerpt") or "")[:160]
            for item in packet_dict["inspection_candidates"]:
                item["source_excerpt"] = str(item.get("source_excerpt") or "")[:80]
            for group_name in ("inspection_public_surface", "inspection_integration"):
                for item in packet_dict[group_name]:
                    item["source_excerpt"] = str(item.get("source_excerpt") or "")[:80]
            packet_dict["truncated"] = True
            rendered = encode()
        if too_large():
            bound_ambiguities(1, 2)
            for group_name in (
                "primary_edit_targets",
                "inspection_implementation_owners",
                "inspection_candidates",
                "inspection_public_surface",
                "inspection_integration",
            ):
                for item in packet_dict[group_name]:
                    item["source_excerpt"] = ""
            packet_dict["inspection_candidates"] = [
                item
                for item in packet_dict["inspection_candidates"]
                if item.get("decision_reason") == "task_path_phrase_inspection"
            ][:1]
            packet_dict["uncertainties"] = packet_dict["uncertainties"][:2]
            packet_dict["uncovered_facets"] = packet_dict["uncovered_facets"][:1]
            packet_dict["uncovered_requirements"] = packet_dict[
                "uncovered_requirements"
            ][:1]
            for group_name in (
                "primary_edit_targets",
                "inspection_public_surface",
                "inspection_integration",
            ):
                for item in packet_dict[group_name]:
                    reason = str(item.get("decision_reason") or "")
                    item["decision_reason"] = (
                        "verified" if reason.startswith("verified_") else reason[:32]
                    )
            rendered = encode()
        if too_large():
            # Decision facts replace repository exploration. Preserve at least
            # one row from every available decision role before retaining
            # rank-only candidates or verbose limitation metadata.
            packet_dict["primary_edit_targets"] = packet_dict["primary_edit_targets"][:2]
            packet_dict["inspection_implementation_owners"] = packet_dict[
                "inspection_implementation_owners"
            ][:1]
            bound_ambiguities(1, 1)
            packet_dict["inspection_public_surface"] = packet_dict["inspection_public_surface"][:1]
            packet_dict["inspection_integration"] = packet_dict["inspection_integration"][:1]
            packet_dict["semantic_facts"] = packet_dict["semantic_facts"][:2]
            packet_dict["execution_paths"] = packet_dict["execution_paths"][:1]
            packet_dict["change_surface"] = packet_dict["change_surface"][:2]
            packet_dict["affected_tests"] = packet_dict["affected_tests"][:2]
            packet_dict["validation_plan"] = packet_dict["validation_plan"][:2]
            packet_dict["relationships"] = packet_dict["relationships"][:2]
            packet_dict["proposed_new_files"] = packet_dict["proposed_new_files"][:1]
            packet_dict["uncovered_facets"] = packet_dict["uncovered_facets"][:1]
            packet_dict["uncovered_requirements"] = packet_dict[
                "uncovered_requirements"
            ][:1]
            packet_dict["uncertainties"] = []
            rendered = encode()
        if too_large():
            packet_dict["primary_edit_targets"] = packet_dict["primary_edit_targets"][:1]
            packet_dict["semantic_facts"] = packet_dict["semantic_facts"][:1]
            packet_dict["change_surface"] = packet_dict["change_surface"][:1]
            packet_dict["affected_tests"] = packet_dict["affected_tests"][:1]
            packet_dict["validation_plan"] = packet_dict["validation_plan"][:1]
            packet_dict["relationships"] = packet_dict["relationships"][:2]
            rendered = encode()
        if too_large():
            packet_dict["relationships"] = packet_dict["relationships"][:1]
            rendered = encode()
        if too_large():
            # Semantic facts are useful only after localization.  They must
            # never make an otherwise actionable graph treatment unavailable:
            # the exact target, scoped boundaries, certified relationship,
            # process/impact and affected test carry stronger decision value.
            # Keep those roles and bound their already persisted projections.
            packet_dict["semantic_facts"] = []

            def bounded_projection(item: str, limit: int = 240) -> str:
                value = str(item).strip()
                if len(value) <= limit:
                    return value
                boundary = value.rfind(" -> ", 0, limit)
                if boundary < limit // 2:
                    boundary = value.rfind(" ", 0, limit)
                return value[:boundary].rstrip() + " truncated=true"

            packet_dict["execution_paths"] = [
                bounded_projection(item) for item in packet_dict["execution_paths"][:1]
            ]
            packet_dict["change_surface"] = [
                bounded_projection(item) for item in packet_dict["change_surface"][:1]
            ]
            rendered = encode()
        if too_large() and packet_dict["affected_tests"]:
            # The affected-test fact is a more compact and independently
            # actionable verification instruction than a repeated command.
            packet_dict["validation_plan"] = []
            rendered = encode()
        if too_large():
            # One repository fact can satisfy many task obligations.  The
            # complete fact-to-facet ledger remains in the packet receipt; the
            # provider view carries a deterministic prefix plus an explicit
            # +N count so requirement metadata cannot suppress the treatment.
            provider_requirement_limit = 4
            rendered = encode()
        if too_large() and packet_dict["inspection_candidates"]:
            # Under the hard provider ceiling, rank/phrase-only inspection is
            # lower-value than verified edit, public-surface, integration,
            # relationship, process, impact, and affected-test facts.  Keep
            # the candidate in the persisted packet, but do not let it make
            # the entire deterministic treatment unavailable.
            packet_dict["inspection_candidates"] = []
            rendered = encode()
        if too_large():
            # Last-resort provider view: one truthful localization fact plus
            # the revision receipt. The complete role/facet/projection ledger
            # remains in the compile receipt; a verbose packet must never turn
            # a healthy treatment into a product failure.
            provider_requirement_limit = 1
            packet_dict["primary_edit_targets"] = packet_dict["primary_edit_targets"][:1]
            bound_ambiguities(1, 1)
            packet_dict["inspection_implementation_owners"] = packet_dict[
                "inspection_implementation_owners"
            ][:1]
            if packet_dict["primary_edit_targets"]:
                packet_dict["inspection_implementation_owners"] = []
            elif packet_dict["inspection_implementation_owners"]:
                # A scoped implementation owner is more actionable than an
                # unrelated ambiguous identity or rank-only candidate. Keep
                # ambiguity when it is the only repository identity, but do
                # not let it evict a stronger owner at the emergency floor.
                packet_dict["ambiguous_identities"] = []
            packet_dict["inspection_candidates"] = (
                last_resort_inspection_candidate
                if not packet_dict["primary_edit_targets"]
                and not packet_dict["inspection_implementation_owners"]
                and not packet_dict["ambiguous_identities"]
                else []
            )
            packet_dict["inspection_public_surface"] = []
            packet_dict["inspection_integration"] = []
            packet_dict["supporting_files"] = []
            packet_dict["relationships"] = []
            packet_dict["semantic_facts"] = []
            packet_dict["execution_paths"] = []
            packet_dict["change_surface"] = []
            packet_dict["affected_tests"] = []
            packet_dict["validation_plan"] = []
            packet_dict["proposed_new_files"] = packet_dict["proposed_new_files"][:1]
            packet_dict["uncovered_facets"] = []
            packet_dict["uncovered_requirements"] = []
            packet_dict["uncertainties"] = []
            rendered = encode()
        if too_large():
            # A budget too small for the remaining decision-grade floor is not
            # permission to silently pretend a graph treatment was delivered.
            self.errors.append("context_budget_too_small")
            if not update:
                raise self._unavailable(receipt, "context_budget_too_small")
            self.context_dirty = False
            return ""
        delivered = tuple(
            dict.fromkeys(
                evidence_identity[str(item["evidence_id"])]
                for group in (
                    packet_dict["primary_edit_targets"],
                    packet_dict["inspection_implementation_owners"],
                    packet_dict["ambiguous_identities"],
                    packet_dict["inspection_candidates"],
                    packet_dict["inspection_public_surface"],
                    packet_dict["inspection_integration"],
                    packet_dict["supporting_files"],
                    packet_dict["relationships"],
                    packet_dict["semantic_facts"],
                )
                for item in group
                if item.get("evidence_id") in evidence_identity
            )
        )
        delivered = tuple(
            dict.fromkeys(
                (
                    *delivered,
                    *(
                        str(item["evidence_sha256"])
                        for item in packet_dict["ambiguous_identities"]
                        if str(item.get("evidence_sha256") or "")
                    ),
                )
            )
        )
        visible_projection_claims = tuple(
            claim
            for claim in normalized_packet["projection_claim_ids"]
            if claim and provider_projection_claim(claim) in rendered
        )
        delivered = tuple(dict.fromkeys((*delivered, *visible_projection_claims)))
        if not delivered:
            if not update:
                raise self._unavailable(receipt, "context_evidence_empty")
            self.context_dirty = False
            return ""
        delivered_features = {
            "exact_edit_targets": packet_dict["primary_edit_targets"],
            "implementation_owner_candidates": packet_dict["inspection_implementation_owners"],
            "ambiguous_identity": packet_dict["ambiguous_identities"],
            "inspection_candidates": packet_dict["inspection_candidates"],
            "public_surface": packet_dict["inspection_public_surface"],
            "integration": packet_dict["inspection_integration"],
            "supporting_files": packet_dict["supporting_files"],
            "semantic_facts": packet_dict["semantic_facts"],
            "process": packet_dict["execution_paths"],
            "impact": packet_dict["change_surface"],
            "affected_tests": packet_dict["affected_tests"],
            "validation": packet_dict["validation_plan"],
            "proposed_new_file": packet_dict["proposed_new_files"],
            "uncovered_requirement": packet_dict["uncovered_requirements"],
            "uncovered_facet": packet_dict["uncovered_facets"],
        }
        for feature, values in delivered_features.items():
            if not values:
                continue
            paths = feature_paths(values)
            self._feature_transition(feature, FeatureState.DELIVERED, paths=paths)
            self._feature_transition(feature, FeatureState.AVAILABLE_TO_AGENT, paths=paths)
            self._snapshot_feature_content(feature, paths)
        self.delivered_claim_ids.update(delivered)
        self.delivery_count += 1
        self.delivery_calls.append(delivered_before_call)
        self.delivery_char_count += len(rendered)
        self.evidence_items_delivered += len(delivered)
        visible_feature_counts = {
            feature: len(values) for feature, values in delivered_features.items() if values
        }
        visible_role_paths = {
            role: list(feature_paths(delivered_features[feature]))
            for role, feature in (
                ("EXACT_EDIT_TARGET", "exact_edit_targets"),
                (
                    "INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY",
                    "implementation_owner_candidates",
                ),
                ("AMBIGUOUS_IDENTITY", "ambiguous_identity"),
                ("INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY", "inspection_candidates"),
                ("INSPECT_PUBLIC_SURFACE", "public_surface"),
                ("INSPECT_INTEGRATION", "integration"),
                ("AFFECTED_TEST", "affected_tests"),
                ("PROPOSED_NEW_FILE", "proposed_new_file"),
            )
            if delivered_features[feature]
        }
        self._last_delivery_claim_ids = delivered
        self._last_delivery_feature_counts = visible_feature_counts
        self._last_delivery_before_call = delivered_before_call
        self.provider_delivery_receipts.append(
            {
                "schema": "gt.provider_delivery.v2",
                "delivery_index": self.delivery_count,
                "kind": "repository_update" if update else "repository_start",
                "delivered_before_call": delivered_before_call,
                "source_revision": receipt.source_revision,
                "context_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "context_token_count": _bounded_token_count(rendered),
                "context_char_count": len(rendered),
                "serialized_claim_ids": list(delivered),
                "provider_claim_tokens": list(
                    dict.fromkeys(re.findall(r"\bclaim=([A-Za-z0-9_-]+)", rendered))
                ),
                "provider_visible_feature_counts": visible_feature_counts,
                "provider_visible_role_paths": visible_role_paths,
            }
        )
        self.context_dirty = False
        self.treatment_status = TreatmentStatus.ACTIVE
        if not update:
            self.initial_context = rendered
            self.initial_delivery_disposition = "DELIVERED"
            self.initial_delivery_reasons = []
        return rendered

    def prepare(self, task: str) -> str:
        # The CLI preflights this once before its first durable checkpoint.
        # Agent.run calls prepare again, so cache the exact packet and avoid a
        # second build/delivery or a changed first prompt.
        if self._prepare_complete and self._prepared_task == task:
            return self._prepared_context
        self.task = task
        self._prepared_task = task
        try:
            receipt = self.service.build()
        except Exception as exc:  # noqa: BLE001 - treatment must fail closed
            receipt = self.service.status()
            raise self._unavailable(receipt, f"graph_build_failed:{type(exc).__name__}") from exc
        if not receipt.query_ready:
            if self._not_applicable(receipt):
                self._prepared_context = self._abstain(
                    f"graph_not_ready:{receipt.build_status.value}"
                )
                self._prepare_complete = True
                return self._prepared_context
            raise self._unavailable(receipt, f"graph_not_ready:{receipt.build_status.value}")
        self.treatment_status = TreatmentStatus.ACTIVE
        self._ensure_dense_ready()
        self._prepared_context = self._render(
            update=False,
            budget=max(0, self.start_char_budget),
            delivered_before_call=1,
        )
        self._prepare_complete = True
        return self._prepared_context

    def before_model_call(self, iteration: int) -> str:
        """Fail closed if the repository changed outside an observed action.

        Provider-visible context is intentionally never returned here.  Every
        update must be attached to the tool observation which established the
        new evidence, so timing and uptake remain auditable.
        """
        self.current_provider_call = max(1, int(iteration))
        if iteration <= 1:
            return ""
        if self.treatment_status is TreatmentStatus.NOT_APPLICABLE:
            return ""
        observed = self.service.status()
        if observed.build_status is GraphStatus.STALE:
            raise self._unavailable(observed, "unobserved_repository_change")
        if not observed.query_ready:
            raise self._unavailable(observed, f"graph_not_ready:{observed.build_status.value}")
        return ""

    def _refresh_and_render_update(self) -> str:
        """Refresh stale graph state and render evidence for this observation."""

        observed = self._refresh_stale_graph(self.service.status())
        if not observed.query_ready:
            if self._not_applicable(observed):
                self._abstain(f"graph_update_not_ready:{observed.build_status.value}")
                return ""
            raise self._unavailable(
                observed, f"graph_update_not_ready:{observed.build_status.value}"
            )
        if not self.context_dirty:
            return ""
        return self._render(
            update=True,
            budget=max(0, self.update_char_budget),
            delivered_before_call=max(2, self.current_provider_call + 1),
        )

    def _refresh_stale_graph(self, observed: Any) -> Any:
        """Refresh repository state even when no further context may be delivered."""

        if observed.build_status is GraphStatus.STALE:
            self.changed_paths = list(
                dict.fromkeys((*self.changed_paths, *observed.git_status_paths))
            )[-20:]
            try:
                observed = self.service.build()
            except Exception as exc:  # noqa: BLE001 - treatment must fail closed
                raise self._unavailable(
                    observed, f"graph_update_failed:{type(exc).__name__}"
                ) from exc
            self._ensure_dense_ready()
        return observed

    def after_action(
        self,
        name: str,
        arguments: dict[str, Any],
        output: str,
        is_error: bool,
    ) -> ObservationAugmentation | None:
        return self.after_actions(
            (
                ActionObservation(
                    name=name,
                    arguments=dict(arguments),
                    output=str(output or ""),
                    is_error=bool(is_error),
                ),
            )
        )

    def after_actions(
        self,
        observations: tuple[ActionObservation, ...],
    ) -> ObservationAugmentation | None:
        """Observe a complete assistant turn and deliver at most one update."""

        if not observations:
            return None
        # Observation only. Actions and their raw outputs remain immutable.
        self.action_count += len(observations)
        output = "\n\n".join(observation.output for observation in observations)
        is_error = any(observation.is_error for observation in observations)
        repository_root = Path(self.service.root).resolve()
        paths: list[str] = []
        for observation in observations:
            observation_command = " ".join(
                str(value or "") for value in observation.arguments.values()
            )
            path_relevant = bool(
                observation.name != "bash"
                or _INSPECTION_COMMAND.search(observation_command)
                or _VALIDATION_COMMAND.search(observation_command)
                or observation.is_error
                or _DIAGNOSTIC_LINE.search(observation.output)
            )
            if not path_relevant:
                continue
            text = " ".join((observation_command, observation.output[:20_000]))
            for match in _PATH_TOKEN.finditer(text):
                normalized = _normalize_relative_path(match.group(1))
                candidate = Path(normalized)
                if not normalized or candidate.is_absolute() or ".." in candidate.parts:
                    continue
                resolved = (repository_root / candidate).resolve()
                if not resolved.is_relative_to(repository_root) or not resolved.is_file():
                    continue
                paths.append(normalized)
        prior_paths = set(self.active_paths)
        self.active_paths = list(dict.fromkeys((*self.active_paths, *paths)))[-20:]
        discovered_new_path = any(path not in prior_paths for path in paths)
        diagnostic_lines = tuple(
            line.strip()[:500]
            for line in (output or "").splitlines()
            if line.strip() and (is_error or _DIAGNOSTIC_LINE.search(line.strip()))
        )
        if diagnostic_lines:
            self.diagnostics = [*self.diagnostics, *diagnostic_lines][-12:]
            self.validation_state = "fail"
        command = "\n".join(
            " ".join(str(value or "") for value in observation.arguments.values())
            for observation in observations
        )
        validation_passed = bool(
            not diagnostic_lines
            and not is_error
            and _VALIDATION_COMMAND.search(command)
            and _VALIDATION_SUCCESS.search(output or "")
        )
        diagnostics_cleared = bool(validation_passed and self.diagnostics)
        if validation_passed:
            self.diagnostics = []
            self.validation_state = "pass"
        observed = self.service.status()
        repository_changed = observed.build_status is GraphStatus.STALE
        observed_paths = set(paths)
        for feature, feature_path_set in self.feature_paths.items():
            if observed_paths & feature_path_set:
                self._feature_transition(feature, FeatureState.FOLLOWED)
            baseline_identities = self.feature_content_identities.get(feature, {})
            content_changed = any(
                path in feature_path_set and self._feature_content_identity(path) != baseline
                for path, baseline in baseline_identities.items()
            )
            if repository_changed and content_changed:
                self._feature_transition(feature, FeatureState.EDITED)
            if diagnostic_lines and observed_paths & feature_path_set:
                self._feature_transition(feature, FeatureState.CONTRADICTED)
        if validation_passed:
            for feature, current in tuple(self.feature_states.items()):
                if current in {
                    FeatureState.EDITED.value,
                    FeatureState.CONTRADICTED.value,
                } and observed_paths & self.feature_paths.get(feature, set()):
                    self._feature_transition(
                        feature,
                        FeatureState.VALIDATED,
                        paths=tuple(
                            sorted(observed_paths & self.feature_paths.get(feature, set()))
                        ),
                    )
        if diagnostic_lines or diagnostics_cleared or discovered_new_path or repository_changed:
            self.context_dirty = True
        # The delivery budget limits provider-visible text, not graph freshness.
        # Always consume an observed repository mutation before returning so a
        # later integrity barrier cannot mistake the agent's own edit for an
        # out-of-band change.
        if repository_changed and self.treatment_status is TreatmentStatus.ACTIVE:
            self._refresh_stale_graph(observed)
        if (
            not self.context_dirty
            or self.treatment_status is not TreatmentStatus.ACTIVE
            or self.delivery_count >= max(1, self.max_delivery_count)
            or sum(
                item.get("kind") == "repository_update" for item in self.provider_delivery_receipts
            )
            >= max(0, self.max_update_delivery_count)
        ):
            return None
        rendered = self._refresh_and_render_update()
        if not rendered:
            return None
        # The augmentation is physically attached to the final observation,
        # so raw_output_sha256 binds that exact immutable value. A separate
        # turn hash binds every action/result used to compile the update.
        raw_hash = hashlib.sha256(observations[-1].output.encode("utf-8")).hexdigest()
        turn_hash = hashlib.sha256(
            repr(
                tuple(
                    (
                        observation.name,
                        tuple(sorted(observation.arguments.items())),
                        observation.output,
                        observation.is_error,
                    )
                    for observation in observations
                )
            ).encode("utf-8")
        ).hexdigest()
        context_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        receipt = self.service.status()
        augmentation = ObservationAugmentation(
            content=rendered,
            raw_output_sha256=raw_hash,
            context_sha256=context_hash,
            delivery_index=self.delivery_count,
            source_revision=receipt.source_revision,
            context_token_count=_bounded_token_count(rendered),
            delivered_before_call=self._last_delivery_before_call,
            serialized_claim_ids=self._last_delivery_claim_ids,
            provider_visible_feature_counts=dict(self._last_delivery_feature_counts),
            observation_count=len(observations),
            turn_observations_sha256=turn_hash,
        )
        self.delivery_receipts.append(augmentation.as_dict())
        return augmentation

    def finalize(self, result: Any) -> dict[str, Any]:
        receipt = self.service.status()
        serialized_claim_ids = {
            str(claim)
            for delivery in self.provider_delivery_receipts
            for claim in delivery.get("serialized_claim_ids", ())
        }
        delivery_reconciliation = (
            "PASS" if serialized_claim_ids == self.delivered_claim_ids else "FAIL"
        )
        feature_states = dict(self.feature_states)
        if result is not None:
            feature_states = {
                feature: (
                    FeatureState.IGNORED.value
                    if state == FeatureState.AVAILABLE_TO_AGENT.value
                    else state
                )
                for feature, state in feature_states.items()
            }
        return {
            "schema": "gt.treatment_receipt.v4",
            "treatment": self.treatment_id,
            "treatment_status": self.treatment_status.value,
            "provider_calls": 0,
            "treatment_provider_calls": 0,
            "graph_available": receipt.query_ready,
            "graph_status": receipt.build_status.value,
            "graph_receipt_schema": receipt.receipt_schema,
            "graph_receipt_path": str(self.service.receipt_path),
            "graph_commit_sha": receipt.commit_sha,
            "graph_builder_version": receipt.graph_builder_version,
            "graph_identity": receipt.graph_checksum_or_identity,
            "source_revision": receipt.source_revision,
            "delivery_count": self.delivery_count,
            "delivery_calls": list(self.delivery_calls),
            "delivery_receipts": list(self.delivery_receipts),
            "provider_delivery_receipts": list(self.provider_delivery_receipts),
            "delivery_reconciliation": delivery_reconciliation,
            "delivery_char_count": self.delivery_char_count,
            "evidence_items_delivered": self.evidence_items_delivered,
            "suppressed_inspection_only_updates": (self.suppressed_inspection_only_updates),
            "initial_delivery_disposition": self.initial_delivery_disposition,
            "initial_delivery_reasons": list(self.initial_delivery_reasons),
            "context_compile_count": self.context_compile_count,
            "retrieval_channel_count": self.retrieval_channel_count,
            "action_count": self.action_count,
            "degraded_reasons": list(receipt.degraded_reasons),
            "retrieval_mode": self.retrieval_mode,
            "dense_index_receipt": dict(self.dense_receipt),
            "dense_query_receipts": list(self.dense_query_receipts),
            "dense_error": self.dense_error or None,
            "graph_projection_receipts": list(self.projection_receipts),
            "context_compile_receipts": list(self.compile_receipts),
            "feature_lifecycle_schema": "gt.feature_lifecycle.v2",
            "feature_states": feature_states,
            "feature_paths": {
                feature: sorted(paths) for feature, paths in sorted(self.feature_paths.items())
            },
            "feature_transitions": list(self.feature_transitions),
            "errors": list(dict.fromkeys(self.errors)),
            "delivered_claim_ids": sorted(self.delivered_claim_ids),
            "initial_context": self.initial_context,
            "initial_context_sha256": (
                hashlib.sha256(self.initial_context.encode("utf-8")).hexdigest()
                if self.initial_context
                else None
            ),
            "initial_context_token_count": _bounded_token_count(self.initial_context),
        }


__all__ = [
    "ActionObservation",
    "BareTreatment",
    "GroundTruthTreatment",
    "ObservationAugmentation",
    "TreatmentStatus",
    "TreatmentUnavailableError",
]
