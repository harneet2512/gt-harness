"""Benchmark treatments for the common coding-agent scaffold.

Treatments may add bounded evidence and record receipts.  They cannot select,
rewrite, reject, retry, or execute an agent action and they make no provider
calls.  This keeps model, prompt, tool policy, and step budget arm-neutral.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.hybrid_repository import build_query_hybrid_repository
from gt_engine.hybrid_retrieval import RetrievalIntent
from gt_engine.repository_context_compiler import (
    ContextCompileRequest,
    ContextStatus,
    GTContextPacket,
    RepositoryContextCompiler,
)
from gt_engine.repository_graph_service import GraphStatus, RepositoryGraphService


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
    ) -> None:
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


@dataclass(slots=True)
class GroundTruthTreatment(BareTreatment):
    root: str | Path = "."
    state_dir: str | Path | None = None
    start_char_budget: int = 6_000
    update_char_budget: int = 4_000
    max_delivery_count: int = 4
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
    delivery_char_count: int = field(default=0, init=False)
    delivery_calls: list[int] = field(default_factory=list, init=False)
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

    def __post_init__(self) -> None:
        if self.state_dir is None:
            override = str(os.environ.get("GT_STATE_DIR") or "").strip()
            if override:
                self.state_dir = override
        self.service = RepositoryGraphService(self.root, state_dir=self.state_dir)
        self.compiler = RepositoryContextCompiler()

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
        )
        repository = build_query_hybrid_repository(
            self.service.root,
            self.service.graph_path,
            state.retrieval_state(),
            candidate_limit=128,
        )
        packet = self.compiler.compile(repository, state)
        self.context_compile_count += 1
        self.retrieval_channel_count += packet.retrieval_channel_count
        return packet

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

        def compact_target(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "path": item["path"],
                "lines": [item["start_line"], item["end_line"]],
                "symbol": item["symbol"],
                "source_excerpt": item["source_excerpt"],
                "evidence_sha256": item["evidence_sha256"],
                "decision_reason": item["decision_reason"],
            }

        # The normalized packet retains every field for local consumers. The
        # provider view binds all rows to one packet revision and carries each
        # claim once, avoiding repeated provenance and excerpts.
        packet_dict = {
            "status": normalized_packet["status"],
            "repository_identity": normalized_packet["repository_identity"],
            "primary_edit_targets": [
                compact_target(item)
                for item in normalized_packet["primary_edit_targets"]
            ],
            "supporting_files": [
                compact_target(item) for item in normalized_packet["supporting_files"]
            ],
            "semantic_facts": normalized_packet["semantic_facts"],
            "semantic_graph_receipt": normalized_packet["semantic_graph_receipt"],
            "execution_paths": normalized_packet["execution_paths"],
            "change_surface": normalized_packet["change_surface"],
            "affected_tests": normalized_packet["affected_tests"],
            "validation_plan": normalized_packet["validation_plan"],
            "uncertainties": normalized_packet["uncertainties"],
            "coverage": normalized_packet["coverage"],
            "selected_token_count": normalized_packet["selected_token_count"],
            "retrieval_channel_count": normalized_packet[
                "retrieval_channel_count"
            ],
            "truncated": normalized_packet["truncated"],
            "evidence_items": [
                {
                    "evidence_sha256": item["evidence_sha256"],
                    "kind": item["kind"],
                    "path": item["path"],
                    "lines": [item["start_line"], item["end_line"]],
                    "symbol": item["symbol"],
                    "relation": item["relation"],
                    "source_path": item["source_path"],
                    "source_symbol": item["source_symbol"],
                    "confidence": item["confidence"],
                    "verification_status": item["verification_status"],
                    "decision_reason": item["decision_reason"],
                    "completeness": item["completeness"],
                }
                for item in normalized_packet["evidence_items"]
            ],
        }
        payload = {
            "schema": "gt.agent_context.v3",
            "kind": "repository_update" if update else "repository_start",
            "repository": receipt.repository,
            "commit_sha": receipt.commit_sha,
            "source_revision": receipt.source_revision,
            "graph_identity": receipt.graph_checksum_or_identity,
            "graph_status": receipt.build_status.value,
            "limitations": list(receipt.degraded_reasons),
            "context_packet": packet_dict,
        }

        def encode() -> str:
            return (
                "<groundtruth-repository-context>\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n</groundtruth-repository-context>"
            )

        rendered = encode()
        if len(rendered) > budget:
            packet_dict["coverage"].pop("retrieval_channels", None)
            packet_dict["coverage"].pop("query_terms", None)
            packet_dict["supporting_files"] = []
            for item in packet_dict["primary_edit_targets"]:
                item["source_excerpt"] = str(item.get("source_excerpt") or "")[:240]
            packet_dict["truncated"] = True
            rendered = encode()
        if len(rendered) > budget:
            for item in packet_dict["primary_edit_targets"]:
                item["source_excerpt"] = ""
            rendered = encode()
        if len(rendered) > budget:
            # Never leave a process/impact assertion visible after dropping
            # its evidence record. A too-small budget is an explicit abstain.
            self.errors.append("context_budget_too_small")
            if not update:
                raise self._unavailable(receipt, "context_budget_too_small")
            self.context_dirty = False
            return ""
        if not packet_dict["evidence_items"]:
            if not update:
                raise self._unavailable(receipt, "context_evidence_empty")
            self.context_dirty = False
            return ""
        delivered = tuple(
            str(item["evidence_sha256"]) for item in packet_dict["evidence_items"]
        )
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
        self._prepared_context = self._render(
            update=False,
            budget=max(0, self.start_char_budget),
            delivered_before_call=1,
        )
        self._prepare_complete = True
        return self._prepared_context

    def before_model_call(self, iteration: int) -> str:
        if iteration <= 1:
            return ""
        if self.treatment_status is TreatmentStatus.NOT_APPLICABLE:
            return ""
        observed = self.service.status()
        if observed.build_status is GraphStatus.STALE:
            self.changed_paths = list(
                dict.fromkeys((*self.changed_paths, *observed.git_status_paths))
            )[-20:]
            try:
                rebuilt = self.service.build()
            except Exception as exc:  # noqa: BLE001 - treatment must fail closed
                raise self._unavailable(
                    observed, f"graph_update_failed:{type(exc).__name__}"
                ) from exc
            if not rebuilt.query_ready:
                raise self._unavailable(
                    rebuilt, f"graph_update_not_ready:{rebuilt.build_status.value}"
                )
        elif not observed.query_ready:
            raise self._unavailable(
                observed, f"graph_not_ready:{observed.build_status.value}"
            )
        if not self.context_dirty and observed.build_status is not GraphStatus.STALE:
            return ""
        return self._render(
            update=True,
            budget=max(0, self.update_char_budget),
            delivered_before_call=iteration,
        )

    def after_action(
        self,
        name: str,
        arguments: dict[str, Any],
        output: str,
        is_error: bool,
    ) -> None:
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
        self.active_paths = list(dict.fromkeys((*self.active_paths, *paths)))[-20:]
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
        if (
            diagnostic_lines
            or diagnostics_cleared
        ):
            self.context_dirty = True

    def finalize(self, result: Any) -> dict[str, Any]:
        receipt = self.service.status()
        return {
            "schema": "gt.treatment_receipt.v1",
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
            "delivery_char_count": self.delivery_char_count,
            "evidence_items_delivered": self.evidence_items_delivered,
            "context_compile_count": self.context_compile_count,
            "retrieval_channel_count": self.retrieval_channel_count,
            "action_count": self.action_count,
            "degraded_reasons": list(receipt.degraded_reasons),
            "errors": list(dict.fromkeys(self.errors)),
            "delivered_claim_ids": sorted(self.delivered_claim_ids),
            "initial_context": self.initial_context,
            "initial_context_sha256": (
                hashlib.sha256(self.initial_context.encode("utf-8")).hexdigest()
                if self.initial_context
                else None
            ),
        }


__all__ = [
    "BareTreatment",
    "GroundTruthTreatment",
    "TreatmentStatus",
    "TreatmentUnavailableError",
]
