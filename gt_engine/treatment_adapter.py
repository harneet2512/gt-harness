"""Pinned treatment identities for matched coding-agent benchmarks.

The adapters describe treatment preparation and agent configuration.  They do
not execute providers, mutate repositories, or normalize away vendor-native
prompt/hook differences.  A parity manifest can therefore separate a common
scaffold comparison from a secondary bundled-product arm.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from gt_engine.relational_context import FINAL_RELATIONAL_CONTEXT_PROFILE

_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXECUTION_CONTRACT_SHA_FIELDS = frozenset(
    {
        "task_order_sha256",
        "tool_envelope_sha256",
        "hook_envelope_sha256",
        "embedding_configuration_sha256",
        "hardware_assumptions_sha256",
        "retry_policy_sha256",
        "timeout_policy_sha256",
        "token_accounting_sha256",
    }
)
_EXECUTION_CONTRACT_REQUIRED_FIELDS = frozenset(
    {
        "task_count",
        "provider_identity",
        "temperature",
        "sampling_parameters",
        *_EXECUTION_CONTRACT_SHA_FIELDS,
    }
)


def _require_sha40(value: str, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA40.fullmatch(normalized) is None:
        raise ValueError(f"{field} must be a pinned 40-character source SHA")
    return normalized


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", "surrogatepass")


def _require_sha256(value: str, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{field} must be a 64-character SHA-256")
    return normalized


def _normalize_execution_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    materialized = dict(value)
    missing = sorted(_EXECUTION_CONTRACT_REQUIRED_FIELDS - materialized.keys())
    if missing:
        raise ValueError("execution contract missing required fields: " + ", ".join(missing))
    if isinstance(materialized["task_count"], bool):
        raise ValueError("execution contract task_count must be a positive integer")
    try:
        task_count = int(materialized["task_count"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "execution contract task_count must be a positive integer"
        ) from exc
    if task_count < 1:
        raise ValueError("execution contract task_count must be positive")
    if not str(materialized["provider_identity"] or "").strip():
        raise ValueError("execution contract provider_identity is required")
    temperature = materialized["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(float(temperature))
    ):
        raise ValueError("execution contract temperature must be finite and numeric")
    if not isinstance(materialized["sampling_parameters"], dict):
        raise ValueError("execution contract sampling_parameters must be an object")
    for field in _EXECUTION_CONTRACT_SHA_FIELDS:
        materialized[field] = _require_sha256(materialized[field], field=field)
    materialized["task_count"] = task_count
    materialized["provider_identity"] = str(materialized["provider_identity"]).strip()
    materialized["temperature"] = float(temperature)
    return json.loads(_canonical(materialized))


@runtime_checkable
class BenchmarkTreatmentAdapter(Protocol):
    treatment_id: str
    source_sha: str
    executable: bool
    parity_eligible: bool
    scaffold_mode: str

    def agent_kwargs(self) -> dict[str, Any]: ...

    def receipt_identity(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class BareTreatmentAdapter:
    treatment_id: str
    source_sha: str
    executable: bool = True
    parity_eligible: bool = True
    scaffold_mode: str = "manifest_common_scaffold"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_sha",
            _require_sha40(self.source_sha, field="bare source SHA"),
        )

    def agent_kwargs(self) -> dict[str, Any]:
        return {"integration_mode": "off"}

    def receipt_identity(self) -> dict[str, Any]:
        return {
            "treatment_id": self.treatment_id,
            "adapter_kind": "bare",
            "source_sha": self.source_sha,
            "executable": self.executable,
            "parity_eligible": self.parity_eligible,
            "scaffold_mode": self.scaffold_mode,
            "delivery_mode": "stock_agent",
            "agent_kwargs": self.agent_kwargs(),
        }


@dataclass(frozen=True, slots=True)
class GroundTruthTreatmentAdapter:
    treatment_id: str
    source_sha: str
    profile_id: str
    preemptive_retrieval: bool
    relational_context: bool
    dense_fallback_only: bool
    semantic_evidence: bool = False
    executable: bool = True
    parity_eligible: bool = True
    scaffold_mode: str = "manifest_common_scaffold"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_sha",
            _require_sha40(self.source_sha, field="GroundTruth source SHA"),
        )
        if self.profile_id not in {
            "central_pes_v1",
            "central_relational_v2",
            "central_relational_v3",
        }:
            raise ValueError(
                "GroundTruth profile must be central_pes_v1, "
                "central_relational_v2, or central_relational_v3"
            )

    def agent_kwargs(self) -> dict[str, Any]:
        kwargs = {
            "integration_mode": "active",
            "treatment_profile": self.profile_id,
            "enable_persistent_execution_state": True,
            "enable_preemptive_retrieval": self.preemptive_retrieval,
            "enable_relational_context": self.relational_context,
            "dense_fallback_only": self.dense_fallback_only,
            "relational_context_max_depth": FINAL_RELATIONAL_CONTEXT_PROFILE.max_depth,
            "relational_context_max_branching": (
                FINAL_RELATIONAL_CONTEXT_PROFILE.max_branching
            ),
            "relational_context_max_processes": (
                FINAL_RELATIONAL_CONTEXT_PROFILE.max_processes
            ),
            "relational_context_max_tokens": FINAL_RELATIONAL_CONTEXT_PROFILE.max_tokens,
        }
        if self.semantic_evidence:
            kwargs["enable_semantic_evidence"] = True
        return kwargs

    def receipt_identity(self) -> dict[str, Any]:
        return {
            "treatment_id": self.treatment_id,
            "adapter_kind": "groundtruth",
            "source_sha": self.source_sha,
            "profile_id": self.profile_id,
            "executable": self.executable,
            "parity_eligible": self.parity_eligible,
            "scaffold_mode": self.scaffold_mode,
            "delivery_mode": "host_provider_boundary",
            "agent_kwargs": self.agent_kwargs(),
        }


@dataclass(frozen=True, slots=True)
class ExternalTreatmentAdapter:
    treatment_id: str
    source_sha: str
    repository_origin: str
    delivery_mode: str
    preparation_contract_sha256: str
    execution_contract_sha256: str
    executable: bool = False
    parity_eligible: bool = False
    scaffold_mode: str = "external_descriptor"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_sha",
            _require_sha40(self.source_sha, field="external treatment source SHA"),
        )
        normalized_mode = str(self.delivery_mode or "").strip()
        if not normalized_mode:
            raise ValueError("external treatment delivery mode is required")
        object.__setattr__(self, "delivery_mode", normalized_mode)
        origin = str(self.repository_origin or "").strip()
        if not origin:
            raise ValueError("external treatment repository origin is required")
        object.__setattr__(self, "repository_origin", origin)
        object.__setattr__(
            self,
            "preparation_contract_sha256",
            _require_sha256(
                self.preparation_contract_sha256,
                field="external preparation contract",
            ),
        )
        object.__setattr__(
            self,
            "execution_contract_sha256",
            _require_sha256(
                self.execution_contract_sha256,
                field="external execution contract",
            ),
        )
        if self.executable:
            raise ValueError(
                "ExternalTreatmentAdapter is metadata-only; use a runnable adapter "
                "before marking an external arm executable"
            )

    def agent_kwargs(self) -> dict[str, Any]:
        return {}

    def receipt_identity(self) -> dict[str, Any]:
        return {
            "treatment_id": self.treatment_id,
            "adapter_kind": "external",
            "source_sha": self.source_sha,
            "repository_origin": self.repository_origin,
            "preparation_contract_sha256": self.preparation_contract_sha256,
            "execution_contract_sha256": self.execution_contract_sha256,
            "executable": self.executable,
            "parity_eligible": self.parity_eligible,
            "scaffold_mode": self.scaffold_mode,
            "delivery_mode": self.delivery_mode,
            "agent_kwargs": {},
        }


def treatment_from_descriptor(
    value: Mapping[str, Any],
) -> BenchmarkTreatmentAdapter:
    """Build one typed treatment from caller-owned manifest data.

    The benchmark library intentionally owns no default task, budget, arm, or
    competitor. A run author must provide every treatment explicitly.
    """

    row = dict(value)
    kind = str(row.pop("adapter_kind", "") or "").strip().lower()
    if kind == "bare":
        return BareTreatmentAdapter(**row)
    if kind == "groundtruth":
        return GroundTruthTreatmentAdapter(**row)
    if kind == "external":
        return ExternalTreatmentAdapter(**row)
    raise ValueError(f"unknown treatment adapter kind: {kind or '<missing>'}")


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    benchmark_id: str
    task_manifest_sha256: str
    model_id: str
    scaffold_sha: str
    max_steps: int
    trials_per_task: int
    execution_contract_json: str
    treatments: tuple[BenchmarkTreatmentAdapter, ...]
    common_scaffold: bool
    parity_treatment_ids: tuple[str, ...]
    manifest_sha256: str

    @classmethod
    def create(
        cls,
        *,
        benchmark_id: str,
        task_manifest_sha256: str,
        model_id: str,
        scaffold_sha: str,
        max_steps: int,
        trials_per_task: int,
        execution_contract: Mapping[str, Any],
        treatments: tuple[BenchmarkTreatmentAdapter, ...],
    ) -> BenchmarkManifest:
        benchmark = str(benchmark_id or "").strip()
        model = str(model_id or "").strip()
        task_hash = str(task_manifest_sha256 or "").strip().lower()
        if not benchmark or not model:
            raise ValueError("benchmark ID and model ID are required")
        if _SHA256.fullmatch(task_hash) is None:
            raise ValueError("task manifest must have a 64-character SHA-256")
        scaffold = _require_sha40(scaffold_sha, field="scaffold source SHA")
        normalized_execution_contract = _normalize_execution_contract(execution_contract)
        if max_steps < 1 or trials_per_task < 1:
            raise ValueError("max steps and trials per task must be positive")
        materialized = tuple(treatments)
        treatment_ids = tuple(str(item.treatment_id or "").strip() for item in materialized)
        if (
            not materialized
            or any(not treatment_id for treatment_id in treatment_ids)
            or len(set(treatment_ids)) != len(treatment_ids)
        ):
            raise ValueError("treatment IDs must be unique and non-empty")
        treatment_receipts = [item.receipt_identity() for item in materialized]
        parity_ids = tuple(
            item.treatment_id
            for item in materialized
            if item.executable and item.parity_eligible
        )
        parity_receipts = [
            receipt
            for receipt in treatment_receipts
            if receipt["treatment_id"] in parity_ids
        ]
        common_scaffold = bool(
            len(parity_receipts) >= 2
            and all(
                receipt.get("scaffold_mode") == "manifest_common_scaffold"
                for receipt in parity_receipts
            )
        )
        payload = {
            "schema": "gt.benchmark_manifest.v1",
            "benchmark_id": benchmark,
            "task_manifest_sha256": task_hash,
            "model_id": model,
            "scaffold_sha": scaffold,
            "max_steps": int(max_steps),
            "trials_per_task": int(trials_per_task),
            "execution_contract": normalized_execution_contract,
            "common_scaffold": common_scaffold,
            "parity_treatment_ids": list(parity_ids),
            "treatments": treatment_receipts,
        }
        return cls(
            benchmark_id=benchmark,
            task_manifest_sha256=task_hash,
            model_id=model,
            scaffold_sha=scaffold,
            max_steps=int(max_steps),
            trials_per_task=int(trials_per_task),
            execution_contract_json=_canonical(normalized_execution_contract).decode("utf-8"),
            treatments=materialized,
            common_scaffold=common_scaffold,
            parity_treatment_ids=parity_ids,
            manifest_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BenchmarkManifest:
        """Rebuild and verify a serialized manifest without trusting its hash."""

        row = dict(value)
        if row.get("schema") != "gt.benchmark_manifest.v1":
            raise ValueError("unsupported benchmark manifest schema")
        treatment_rows = row.get("treatments")
        if not isinstance(treatment_rows, list) or not treatment_rows:
            raise ValueError("benchmark manifest treatments are missing")
        treatments: list[BenchmarkTreatmentAdapter] = []
        for serialized in treatment_rows:
            if not isinstance(serialized, Mapping):
                raise ValueError("benchmark manifest treatment must be an object")
            descriptor = dict(serialized)
            descriptor.pop("delivery_mode", None)
            kind = str(descriptor.get("adapter_kind") or "")
            agent_kwargs = descriptor.pop("agent_kwargs", None)
            if kind == "groundtruth":
                if not isinstance(agent_kwargs, Mapping):
                    raise ValueError("GroundTruth manifest treatment is missing agent kwargs")
                descriptor.update(
                    {
                        "preemptive_retrieval": bool(
                            agent_kwargs.get("enable_preemptive_retrieval")
                        ),
                        "relational_context": bool(
                            agent_kwargs.get("enable_relational_context")
                        ),
                        "dense_fallback_only": bool(
                            agent_kwargs.get("dense_fallback_only")
                        ),
                        "semantic_evidence": bool(
                            agent_kwargs.get("enable_semantic_evidence")
                        ),
                    }
                )
            if kind == "external":
                descriptor.pop("scaffold_mode", None)
                descriptor.pop("parity_eligible", None)
            treatments.append(treatment_from_descriptor(descriptor))
        rebuilt = cls.create(
            benchmark_id=str(row.get("benchmark_id") or ""),
            task_manifest_sha256=str(row.get("task_manifest_sha256") or ""),
            model_id=str(row.get("model_id") or ""),
            scaffold_sha=str(row.get("scaffold_sha") or ""),
            max_steps=row.get("max_steps"),
            trials_per_task=row.get("trials_per_task"),
            execution_contract=(
                row.get("execution_contract")
                if isinstance(row.get("execution_contract"), Mapping)
                else {}
            ),
            treatments=tuple(treatments),
        )
        if _canonical(rebuilt.as_dict()) != _canonical(row):
            raise ValueError("benchmark manifest content or hash mismatch")
        return rebuilt

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.benchmark_manifest.v1",
            "benchmark_id": self.benchmark_id,
            "task_manifest_sha256": self.task_manifest_sha256,
            "model_id": self.model_id,
            "scaffold_sha": self.scaffold_sha,
            "max_steps": self.max_steps,
            "trials_per_task": self.trials_per_task,
            "execution_contract": json.loads(self.execution_contract_json),
            "common_scaffold": self.common_scaffold,
            "parity_treatment_ids": list(self.parity_treatment_ids),
            "treatments": [item.receipt_identity() for item in self.treatments],
            "manifest_sha256": self.manifest_sha256,
        }

    def runtime_identity(self, treatment_id: str) -> dict[str, Any]:
        """Return the exact identity payload a runtime agent must echo."""
        treatment = next(
            (item for item in self.treatments if item.treatment_id == treatment_id),
            None,
        )
        if treatment is None:
            raise ValueError(f"unknown treatment ID: {treatment_id}")
        return {
            "benchmark_id": self.benchmark_id,
            "task_manifest_sha256": self.task_manifest_sha256,
            "model_id": self.model_id,
            "scaffold_sha": self.scaffold_sha,
            "max_steps": self.max_steps,
            "trials_per_task": self.trials_per_task,
            "execution_contract": json.loads(self.execution_contract_json),
            "treatment": treatment.receipt_identity(),
            "manifest_sha256": self.manifest_sha256,
        }


__all__ = [
    "BareTreatmentAdapter",
    "BenchmarkManifest",
    "BenchmarkTreatmentAdapter",
    "ExternalTreatmentAdapter",
    "GroundTruthTreatmentAdapter",
    "treatment_from_descriptor",
]
