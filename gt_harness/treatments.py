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
from gt_engine.repository_context_compiler import (
    ContextCompileRequest,
    ContextStatus,
    GTContextPacket,
    RepositoryContextCompiler,
)
from gt_engine.repository_graph_service import GraphStatus, RepositoryGraphService
from gt_engine.snowflake_onnx import SnowflakeOnnxDenseBackend


class TreatmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"


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


def _bounded_token_count(text: str) -> int:
    """Deterministic conservative provider-token approximation."""

    count = 0
    for token in re.findall(r"\w+|[^\w\s]", str(text or ""), re.UNICODE):
        count += (
            max(1, (len(token) + 3) // 4)
            if re.fullmatch(r"\w+", token, re.UNICODE)
            else 1
        )
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

    def finalize(self, result: Any) -> dict[str, Any]:
        return {
            "schema": "gt.treatment_receipt.v1",
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.delivery_receipt.v2",
            "delivery_index": self.delivery_index,
            "source_revision": self.source_revision,
            "raw_output_sha256": self.raw_output_sha256,
            "context_sha256": self.context_sha256,
            "same_observation": True,
            "context_token_count": self.context_token_count,
        }


@dataclass(slots=True)
class GroundTruthTreatment(BareTreatment):
    root: str | Path = "."
    state_dir: str | Path | None = None
    start_char_budget: int = 6_000
    update_char_budget: int = 4_000
    max_delivery_count: int = 4
    start_token_budget: int = 500
    update_token_budget: int = 350
    retrieval_mode: str | None = None
    treatment_id: str = field(default="groundtruth", init=False)
    service: RepositoryGraphService = field(init=False, repr=False)
    compiler: RepositoryContextCompiler = field(init=False, repr=False)
    task: str = field(default="", init=False)
    treatment_status: TreatmentStatus = field(
        default=TreatmentStatus.FAILED, init=False
    )
    delivery_count: int = field(default=0, init=False)
    context_compile_count: int = field(default=0, init=False)
    retrieval_channel_count: int = field(default=0, init=False)
    action_count: int = field(default=0, init=False)
    evidence_items_delivered: int = field(default=0, init=False)
    suppressed_inspection_only_updates: int = field(default=0, init=False)
    delivery_char_count: int = field(default=0, init=False)
    delivery_calls: list[int] = field(default_factory=list, init=False)
    delivery_receipts: list[dict[str, Any]] = field(default_factory=list, init=False)
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
    dense_index: PersistentDenseSemanticIndex | None = field(
        default=None, init=False, repr=False
    )
    dense_receipt: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    dense_error: str = field(default="", init=False, repr=False)
    dense_query_receipts: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    projection_receipts: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.state_dir is None:
            override = str(os.environ.get("GT_STATE_DIR") or "").strip()
            if override:
                self.state_dir = override
        self.service = RepositoryGraphService(self.root, state_dir=self.state_dir)
        self.compiler = RepositoryContextCompiler()
        mode = str(
            self.retrieval_mode
            or os.environ.get("GT_RETRIEVAL_MODE")
            or "hybrid_if_available"
        ).strip().lower()
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
        if self.retrieval_mode == "hybrid_required" and not self.dense_receipt.get(
            "query_ready"
        ):
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
        reasons = " ".join(
            str(item) for item in getattr(receipt, "degraded_reasons", ())
        ).lower()
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
        """Record an honest no-treatment result without aborting the agent."""
        self.treatment_status = TreatmentStatus.NOT_APPLICABLE
        self.errors.append(f"NOT_APPLICABLE:{reason}")
        self.context_dirty = False
        return ""

    def _context(self, *, update: bool, budget: int) -> GTContextPacket:
        receipt = self.service.status()
        dense_candidates: tuple[tuple[str, float], ...] = ()
        if self.dense_index is not None:
            dense_query = self.dense_index.query(
                "\n".join(
                    item
                    for item in (
                        self.task,
                        *self.diagnostics[-6:],
                        *self.active_paths[-10:],
                    )
                    if item
                ),
                limit=12,
            )
            self.dense_query_receipts.append(
                {
                    "query_ready": dense_query.query_ready,
                    "status": dense_query.status.value,
                    "source_revision": dense_query.source_revision,
                    "model_identity": dense_query.model_identity,
                    "candidate_count": len(dense_query.candidates),
                    "candidate_paths": [
                        candidate.path for candidate in dense_query.candidates[:12]
                    ],
                    "degraded_reasons": list(dense_query.degraded_reasons),
                }
            )
            if dense_query.query_ready:
                dense_candidates = tuple(
                    (candidate.path, candidate.score)
                    for candidate in dense_query.candidates
                )
            elif self.retrieval_mode == "hybrid_required":
                raise self._unavailable(
                    receipt,
                    "dense_query_not_ready:"
                    + ",".join(dense_query.degraded_reasons),
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
        packet = self._project_persisted_graph(packet)
        self.context_compile_count += 1
        self.retrieval_channel_count += packet.retrieval_channel_count
        return packet

    def _project_persisted_graph(self, packet: GTContextPacket) -> GTContextPacket:
        """Replace anchor-local approximations with persisted exact projections."""

        if packet.status is not ContextStatus.READY:
            return packet
        anchor = next(
            (
                item
                for item in (*packet.primary_edit_targets, *packet.inspection_candidates)
                if item.symbol
            ),
            None,
        )
        if anchor is None:
            return replace(
                packet,
                execution_paths=(),
                change_surface=(),
                uncertainties=tuple(
                    dict.fromkeys((*packet.uncertainties, "graph_projection_anchor_unavailable"))
                ),
            )
        projector = PersistedGraphProjector(self.service)
        try:
            processes = projector.project_processes(anchor.symbol, file_path=anchor.path)
            impact = projector.project_impact(anchor.symbol, file_path=anchor.path)
        except Exception as exc:  # noqa: BLE001 - graph evidence fails closed
            return replace(
                packet,
                execution_paths=(),
                change_surface=(),
                uncertainties=tuple(
                    dict.fromkeys(
                        (*packet.uncertainties, f"graph_projection_failed:{type(exc).__name__}")
                    )
                ),
            )

        process_receipt = asdict(processes.receipt)
        impact_receipt = asdict(impact.receipt)
        projection_receipt = {
            "schema": "gt.graph_projection_receipt.v1",
            "anchor": {"path": anchor.path, "symbol": anchor.symbol},
            "process": process_receipt,
            "impact": impact_receipt,
        }
        key = (processes.receipt.source_revision, anchor.path, anchor.symbol)
        if not any(
            (
                item.get("process", {}).get("source_revision"),
                item.get("anchor", {}).get("path"),
                item.get("anchor", {}).get("symbol"),
            )
            == key
            for item in self.projection_receipts
        ):
            self.projection_receipts.append(projection_receipt)

        exposed = self.delivered_claim_ids
        process_lines: list[str] = []
        projection_claims: list[str] = []
        if processes.status is ProjectionStatus.READY:
            for process in processes.processes:
                if process.process_id in exposed:
                    continue
                nodes = []
                edge_receipts = []
                for index, step in enumerate(process.steps):
                    if index == 0:
                        nodes.append(f"{step.source.file_path}#{step.source.name}")
                    nodes.append(f"{step.target.file_path}#{step.target.name}")
                    receiver = f",receiver={step.receiver_type}" if step.receiver_type else ""
                    edge_receipts.append(
                        f"edge={step.evidence.edge_id},resolution="
                        f"{step.evidence.resolution_outcome}{receiver}"
                    )
                process_lines.append(
                    f"{process.process_id} lower_bound=true "
                    + " -> ".join(nodes)
                    + " ["
                    + ";".join(edge_receipts)
                    + "]"
                )
                projection_claims.append(process.process_id)

        impact_lines: list[str] = []
        affected_tests: list[str] = []
        if impact.status is ProjectionStatus.READY:
            for fact in impact.impacts:
                if fact.impact_id in exposed:
                    continue
                receiver = f" receiver={fact.receiver_type}" if fact.receiver_type else ""
                edge_identity = fact.evidence.edge_id or (
                    "assertion:" + str(fact.evidence.assertion_id)
                )
                impact_lines.append(
                    f"{fact.impact_id} depth={fact.depth} {fact.relationship} "
                    f"{fact.impacted.file_path}#{fact.impacted.name} "
                    f"direction={fact.traversal_direction} "
                    f"edge={edge_identity}"
                    f"{receiver}"
                )
                projection_claims.append(fact.impact_id)
                if fact.impacted.is_test:
                    affected_tests.append(fact.impacted.file_path)

        reasons = list(packet.uncertainties)
        if processes.status is not ProjectionStatus.READY:
            reasons.append(f"process_projection_{processes.status.value.lower()}")
        if impact.status is not ProjectionStatus.READY:
            reasons.append(f"impact_projection_{impact.status.value.lower()}")
        reasons.extend(processes.receipt.truncation_reasons)
        reasons.extend(impact.receipt.truncation_reasons)
        coverage = dict(packet.coverage)
        coverage["persisted_process_projection"] = process_receipt
        coverage["persisted_impact_projection"] = impact_receipt
        return replace(
            packet,
            execution_paths=tuple(process_lines),
            change_surface=tuple(impact_lines),
            affected_tests=tuple(dict.fromkeys(affected_tests))[:5],
            uncertainties=tuple(dict.fromkeys(reasons)),
            coverage=coverage,
            projection_claim_ids=tuple(dict.fromkeys(projection_claims)),
            truncated=bool(
                packet.truncated
                or processes.receipt.truncated
                or impact.receipt.truncated
            ),
        )

    def _render(self, *, update: bool, budget: int, delivered_before_call: int) -> str:
        if update and self.delivery_count >= max(1, self.max_delivery_count):
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
        if packet.status is ContextStatus.FAILED:
            reason = ",".join(packet.uncertainties) or "context_compile_failed"
            raise self._unavailable(receipt, reason)
        if packet.status is ContextStatus.ABSTAIN:
            if not update:
                reason = ",".join(packet.uncertainties) or "no_repository_evidence"
                return self._abstain(f"context_abstained:{reason}")
            self.context_dirty = False
            return ""
        normalized_packet = packet.as_dict()

        # An inspection candidate or a loose semantic match is not an
        # instruction.  When retrieval explicitly reports that it lacks
        # independent support and the packet contains no decision-grade
        # evidence, delivering it at repository start only adds noise and can
        # anchor the agent on the wrong file.  Abstain honestly and let the
        # agent inspect the repository itself.  Real edit targets,
        # relationships, impact/test facts, or validation plans remain
        # eligible for delivery even when the graph declares limitations.
        decision_grade_initial = any(
            normalized_packet[name]
            for name in (
                "primary_edit_targets",
                "supporting_files",
                "execution_paths",
                "change_surface",
                "affected_tests",
                "validation_plan",
            )
        ) or any(
            item.get("kind") == "relationship"
            for item in normalized_packet["evidence_items"]
        )
        if (
            not update
            and not decision_grade_initial
            and "insufficient_independent_support" in normalized_packet["uncertainties"]
        ):
            self.suppressed_inspection_only_updates += 1
            return self._abstain("context_abstained:no_decision_grade_evidence")

        def compact_target(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "path": item["path"],
                "lines": [item["start_line"], item["end_line"]],
                "symbol": item["symbol"],
                "source_excerpt": item["source_excerpt"],
                "evidence_id": item["evidence_sha256"][:16],
                "decision_reason": item["decision_reason"],
            }

        # The normalized packet retains every field for local consumers. The
        # provider view binds all rows to one packet revision and carries each
        # claim once, avoiding repeated provenance and excerpts.
        raw_coverage = normalized_packet["coverage"]
        raw_dense = raw_coverage.get("dense_index") or {}
        provider_coverage = {
            "documents_considered": raw_coverage.get("documents_considered", 0),
            "ranked_files": raw_coverage.get("ranked_files", 0),
            "certified_edges_selected": raw_coverage.get(
                "certified_edges_selected", 0
            ),
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
                "inspection_candidates",
                "supporting_files",
                "evidence_items",
            )
            for item in normalized_packet[group_name]
        }
        relationship_evidence = [
            {
                "evidence_id": item["evidence_sha256"][:16],
                "source": item["source_path"]
                + (":" + item["source_symbol"] if item["source_symbol"] else ""),
                "relation": item["relation"],
                "target": item["path"]
                + (":" + item["symbol"] if item["symbol"] else ""),
                "scope": item["completeness"],
            }
            for item in normalized_packet["evidence_items"]
            if item["kind"] == "relationship"
        ]
        semantic_evidence = [
            item
            for item in normalized_packet["evidence_items"]
            if item["kind"] == "semantic_fact"
        ]
        semantic_facts = [
            {
                "fact": fact,
                "evidence_id": semantic_evidence[index]["evidence_sha256"][:16],
            }
            for index, fact in enumerate(normalized_packet["semantic_facts"])
            if index < len(semantic_evidence)
        ]
        packet_dict = {
            "status": normalized_packet["status"],
            "primary_edit_targets": [
                compact_target(item)
                for item in normalized_packet["primary_edit_targets"]
            ],
            "inspection_candidates": [
                compact_target(item)
                for item in normalized_packet["inspection_candidates"]
            ],
            "supporting_files": [
                compact_target(item) for item in normalized_packet["supporting_files"]
            ],
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
        decision_grade_update = any(
            packet_dict[name]
            for name in (
                "primary_edit_targets",
                "supporting_files",
                "relationships",
                "semantic_facts",
                "execution_paths",
                "change_surface",
                "affected_tests",
                "validation_plan",
            )
        )
        if update and not decision_grade_update:
            self.suppressed_inspection_only_updates += 1
            self.context_dirty = False
            return ""

        def encode() -> str:
            kind = "repository_update" if update else "repository_start"
            lines = [
                '<groundtruth-repository-context schema="gt.agent_context.v4" '
                f'kind="{kind}">',
                "RECEIPT "
                f"repository={Path(receipt.repository).name or receipt.repository} "
                f"commit={receipt.commit_sha[:12]} source={receipt.source_revision[:12]} "
                f"graph={receipt.graph_checksum_or_identity[:12]} "
                f"status={receipt.build_status.value}",
            ]
            limitations = tuple(receipt.degraded_reasons)
            if limitations:
                lines.append("LIMITATIONS " + "; ".join(limitations))

            def target_line(prefix: str, item: dict[str, Any]) -> str:
                location = item["path"] + ":" + str(item["lines"][0])
                if item["symbol"]:
                    location += "#" + item["symbol"]
                excerpt = " ".join(str(item.get("source_excerpt") or "").split())
                suffix = f" | {excerpt}" if excerpt else ""
                return (
                    f"{prefix} {location} claim={item['evidence_id']} "
                    f"reason={item['decision_reason']}{suffix}"
                )

            for item in packet_dict["primary_edit_targets"]:
                lines.append(target_line("EXACT_EDIT_TARGET", item))
            for item in packet_dict["inspection_candidates"]:
                lines.append(
                    target_line("INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY", item)
                )
            for item in packet_dict["supporting_files"]:
                lines.append(target_line("SUPPORTING_FILE", item))
            for item in packet_dict["relationships"]:
                lines.append(
                    "VERIFIED_RELATION "
                    f"{item['source']} {item['relation']} {item['target']} "
                    f"claim={item['evidence_id']} scope={item['scope']}"
                )
            for item in packet_dict["semantic_facts"]:
                lines.append(
                    f"SEMANTIC_FACT claim={item['evidence_id']} {item['fact']}"
                )
            lines.extend(f"BOUNDED_PROCESS {item}" for item in packet_dict["execution_paths"])
            lines.extend(f"BOUNDED_IMPACT {item}" for item in packet_dict["change_surface"])
            lines.extend(f"AFFECTED_TEST {item}" for item in packet_dict["affected_tests"])
            lines.extend(f"VALIDATE {item}" for item in packet_dict["validation_plan"])
            lines.extend(f"UNCERTAINTY {item}" for item in packet_dict["uncertainties"])
            coverage = packet_dict["coverage"]
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
        token_ceiling = max(
            1, self.update_token_budget if update else self.start_token_budget
        )

        def too_large() -> bool:
            return len(rendered) > budget or _bounded_token_count(rendered) > token_ceiling

        if too_large():
            packet_dict["coverage"] = {
                "retrieval_mode": provider_coverage["retrieval_mode"],
                "dense_status": provider_coverage["dense_status"],
                "dense_query_ready": provider_coverage["dense_query_ready"],
            }
            packet_dict["semantic_graph_receipt"] = {}
            packet_dict["supporting_files"] = []
            packet_dict["inspection_candidates"] = packet_dict[
                "inspection_candidates"
            ][:2]
            for item in packet_dict["primary_edit_targets"]:
                item["source_excerpt"] = str(item.get("source_excerpt") or "")[:240]
            for item in packet_dict["inspection_candidates"]:
                item["source_excerpt"] = str(item.get("source_excerpt") or "")[:160]
            packet_dict["truncated"] = True
            rendered = encode()
        if too_large():
            for item in packet_dict["primary_edit_targets"]:
                item["source_excerpt"] = ""
            for item in packet_dict["inspection_candidates"]:
                item["source_excerpt"] = ""
            packet_dict["semantic_facts"] = packet_dict["semantic_facts"][:2]
            packet_dict["execution_paths"] = packet_dict["execution_paths"][:2]
            packet_dict["change_surface"] = packet_dict["change_surface"][:4]
            packet_dict["affected_tests"] = packet_dict["affected_tests"][:3]
            packet_dict["validation_plan"] = packet_dict["validation_plan"][:3]
            packet_dict["uncertainties"] = packet_dict["uncertainties"][:4]
            rendered = encode()
        if too_large():
            packet_dict["semantic_facts"] = []
            packet_dict["execution_paths"] = []
            packet_dict["change_surface"] = []
            packet_dict["affected_tests"] = []
            packet_dict["validation_plan"] = []
            packet_dict["relationships"] = []
            rendered = encode()
        if too_large():
            # Never leave a process/impact assertion visible after dropping
            # its evidence record. A too-small budget is an explicit abstain.
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
                    packet_dict["inspection_candidates"],
                    packet_dict["supporting_files"],
                    packet_dict["relationships"],
                    packet_dict["semantic_facts"],
                )
                for item in group
                if item.get("evidence_id") in evidence_identity
            )
        )
        delivered = tuple(
            dict.fromkeys((*delivered, *normalized_packet["projection_claim_ids"]))
        )
        if not delivered:
            if not update:
                raise self._unavailable(receipt, "context_evidence_empty")
            self.context_dirty = False
            return ""
        self.delivered_claim_ids.update(delivered)
        self.delivery_count += 1
        self.delivery_calls.append(delivered_before_call)
        self.delivery_char_count += len(rendered)
        self.evidence_items_delivered += len(delivered)
        self.context_dirty = False
        self.treatment_status = TreatmentStatus.ACTIVE
        if not update:
            self.initial_context = rendered
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
            raise self._unavailable(
                receipt, f"graph_build_failed:{type(exc).__name__}"
            ) from exc
        if not receipt.query_ready:
            if self._not_applicable(receipt):
                self._prepared_context = self._abstain(
                    f"graph_not_ready:{receipt.build_status.value}"
                )
                self._prepare_complete = True
                return self._prepared_context
            raise self._unavailable(
                receipt, f"graph_not_ready:{receipt.build_status.value}"
            )
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
        if iteration <= 1:
            return ""
        if self.treatment_status is TreatmentStatus.NOT_APPLICABLE:
            return ""
        observed = self.service.status()
        if observed.build_status is GraphStatus.STALE:
            raise self._unavailable(observed, "unobserved_repository_change")
        if not observed.query_ready:
            raise self._unavailable(
                observed, f"graph_not_ready:{observed.build_status.value}"
            )
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
            delivered_before_call=self.action_count,
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
        # Observation only. The action and its output remain immutable.
        self.action_count += 1
        text = " ".join(
            (
                " ".join(str(value or "") for value in arguments.values()),
                str(output or "")[:20_000],
            )
        )
        repository_root = Path(self.service.root).resolve()
        paths: list[str] = []
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
        command = " ".join(str(value or "") for value in arguments.values())
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
        ):
            return None
        rendered = self._refresh_and_render_update()
        if not rendered:
            return None
        raw_hash = hashlib.sha256(str(output or "").encode("utf-8")).hexdigest()
        context_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        receipt = self.service.status()
        augmentation = ObservationAugmentation(
            content=rendered,
            raw_output_sha256=raw_hash,
            context_sha256=context_hash,
            delivery_index=self.delivery_count,
            source_revision=receipt.source_revision,
            context_token_count=_bounded_token_count(rendered),
        )
        self.delivery_receipts.append(augmentation.as_dict())
        return augmentation

    def finalize(self, result: Any) -> dict[str, Any]:
        receipt = self.service.status()
        return {
            "schema": "gt.treatment_receipt.v2",
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
            "delivery_char_count": self.delivery_char_count,
            "evidence_items_delivered": self.evidence_items_delivered,
            "suppressed_inspection_only_updates": (
                self.suppressed_inspection_only_updates
            ),
            "context_compile_count": self.context_compile_count,
            "retrieval_channel_count": self.retrieval_channel_count,
            "action_count": self.action_count,
            "degraded_reasons": list(receipt.degraded_reasons),
            "retrieval_mode": self.retrieval_mode,
            "dense_index_receipt": dict(self.dense_receipt),
            "dense_query_receipts": list(self.dense_query_receipts),
            "dense_error": self.dense_error or None,
            "graph_projection_receipts": list(self.projection_receipts),
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
    "BareTreatment",
    "GroundTruthTreatment",
    "ObservationAugmentation",
    "TreatmentStatus",
    "TreatmentUnavailableError",
]
