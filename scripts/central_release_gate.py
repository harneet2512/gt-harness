#!/usr/bin/env python3
"""Consolidated fail-closed release gate for the central GT treatment.

This is an evidence gate, not a benchmark runner.  Provider-free gates produce
the ``static_evidence`` object and each task produces a central receipt.  The
gate joins those outputs and refuses release when any required substrate,
dense backend, delivery, preflight, or baseline-shield fact is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.central_runtime import CENTRAL_FEATURE_IDS
from gt_engine.delivery_audit import audit_provider_deliveries
from gt_engine.mechanical_completeness import (
    build_task_execution_certificate,
    evaluate_provider_barrier,
)
from gt_engine.observed_facts import audit_observed_fact_lifecycle
from gt_engine.runtime_gate import audit_runtime_receipt
from gt_engine.snowflake_onnx import (
    SNOWFLAKE_MAX_LENGTH,
    SNOWFLAKE_MODEL_NAME,
    SNOWFLAKE_MODEL_REVISION,
    SNOWFLAKE_MODEL_SHA256,
    SNOWFLAKE_TOKENIZER_SHA256,
)

# Architecture D may send zero PES frames when every dispatched call already
# recorded a legal non-material abstention.  The empty-trajectory gate must
# use this same set; requiring only ``no_certified_related_file`` rejects
# correct history-already-contains and not-model-material abstentions.
_PERSISTENT_NONE_FRAME_REASONS = frozenset(
    {
        "state_change_already_represented_or_not_model_material",
        "no_material_certified_localization",
        "no_certified_related_file",
        "provider_history_already_contains_evidence",
        "context_budget_closed",
        "stale_source_revision",
        "graph_rebase_required",
        "bootstrap_not_applied",
    }
)


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce receipt JSON values without allowing ``None`` to escape."""

    try:
        return int(default if value is None else value)
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True, slots=True)
class ReleaseGateCheck:
    name: str
    passed: bool
    failures: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReleaseGateReport:
    """Stable machine-readable release result (schema ``gt.release_gate.v1``)."""

    schema: str
    status: str
    receipts: int
    checks: tuple[ReleaseGateCheck, ...]
    failures: tuple[str, ...]
    summary: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "passed": self.passed,
            "receipts": self.receipts,
            "checks": [item.as_dict() for item in self.checks],
            "failures": list(self.failures),
            "summary": self.summary,
        }


def _bool(value: Any) -> bool:
    return (
        value is True
        or value == 1
        or str(value).strip().lower()
        in {
            "true",
            "ready",
            "passed",
            "pass",
            "approved",
            "smoke_approved",
        }
    )


def _check_static(static: dict[str, Any] | None) -> ReleaseGateCheck:
    failures: list[str] = []
    if not isinstance(static, dict):
        return ReleaseGateCheck("static_provider_free", False, ("missing_static_evidence",), {})
    # These names intentionally accept the direct output names used by the
    # existing census/readiness/pre-smoke scripts.  Missing is never inferred
    # as pass.
    census_value = static.get("census_passed", static.get("census"))
    if isinstance(census_value, dict):
        census_value = census_value.get("status", census_value.get("passed"))
    readiness = static.get("readiness", static.get("central_readiness"))
    if isinstance(readiness, dict):
        readiness = readiness.get("status", readiness.get("passed"))
    smoke_value = static.get("pre_smoke_approved", static.get("smoke_approved"))
    if isinstance(smoke_value, dict):
        smoke_value = smoke_value.get("status", smoke_value.get("approved"))
    if not _bool(census_value):
        failures.append("census_not_passed")
    if not _bool(readiness):
        failures.append("readiness_not_ready")
    if not _bool(smoke_value):
        failures.append("pre_smoke_not_approved")
    if static.get("exact_commit") is not None and not _bool(static.get("exact_commit")):
        failures.append("exact_commit_not_pushed")
    return ReleaseGateCheck(
        "static_provider_free",
        not failures,
        tuple(failures),
        {"readiness": readiness, "keys": sorted(static)},
    )


def _substrate(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    intelligence = receipt.get("repository_intelligence") or {}
    evidence = receipt.get("repository_evidence") or {}
    applicability = str(intelligence.get("applicability") or "")
    excluded = bool(intelligence.get("denominator_excluded"))
    failures: list[str] = []
    if excluded and applicability == "not_applicable_no_supported_source":
        return ReleaseGateCheck("repository_substrate", True, (), {"applicability": applicability})
    status = str(intelligence.get("status") or "")
    graph_gate = intelligence.get("graph_gate") or {}
    if graph_gate.get("blocked") is True:
        failures.append(f"{label}:graph_gate_blocked")
    if graph_gate.get("failures"):
        failures.append(f"{label}:graph_gate_failures_present")
    if evidence:
        if evidence.get("substrate_ready") is not True:
            failures.append(f"{label}:repository_substrate_not_ready")
        if evidence.get("index_current") is not True:
            failures.append(f"{label}:repository_index_not_current")
        if evidence.get("intelligence_valid") is not True:
            failures.append(f"{label}:repository_evidence_invalid")
        if str(receipt.get("treatment_profile") or "") == "central_relational_v2":
            index = evidence.get("index") or {}
            sha = re.compile(r"[0-9a-f]{64}")
            graph_revision = str(index.get("graph_revision") or "")
            graph_sha = str(index.get("graph_db_sha256") or "")
            manifest_sha = str(index.get("graph_manifest_sha256") or "")
            binary_sha = str(index.get("binary_sha256") or "")
            if (
                sha.fullmatch(graph_sha) is None
                or sha.fullmatch(graph_revision) is None
                or graph_revision != str(evidence.get("graph_revision") or "")
            ):
                failures.append(f"{label}:repository_graph_content_identity_invalid")
            if sha.fullmatch(manifest_sha) is None:
                failures.append(f"{label}:repository_graph_manifest_identity_invalid")
            if sha.fullmatch(binary_sha) is None:
                failures.append(f"{label}:repository_indexer_identity_invalid")
            if index.get("schema_valid") is not True:
                failures.append(f"{label}:repository_graph_schema_invalid")
            if str(index.get("source_revision") or "") != str(
                evidence.get("source_revision") or ""
            ):
                failures.append(f"{label}:repository_graph_source_identity_mismatch")
            if int(index.get("source_files") or 0) != int(
                index.get("indexable_files") or 0
            ):
                failures.append(f"{label}:repository_source_coverage_incomplete")
            if int(index.get("parser_failures") or 0) != 0:
                failures.append(f"{label}:repository_parser_failures_present")
    else:
        # Legacy receipts did not separate graph health from downstream GT
        # mechanism health. Keep their strict fallback without allowing a
        # bootstrap/delivery failure to redefine a proven current graph in new
        # receipts.
        if status not in {"passed", "source_backed", "healthy", "available"}:
            failures.append(f"{label}:repository_status:{status or 'missing'}")
        if intelligence.get("failures"):
            failures.append(f"{label}:repository_failures_present")
    metrics = receipt.get("metrics") or {}
    if intelligence.get("required"):
        if "repository_substrate_valid" in metrics:
            if int(metrics.get("repository_substrate_valid") or 0) <= 0:
                failures.append(f"{label}:repository_substrate_metric_invalid")
        elif not evidence and int(metrics.get("repository_intelligence_valid") or 0) <= 0:
            failures.append(f"{label}:repository_intelligence_not_valid")
    return ReleaseGateCheck(
        "repository_substrate",
        not failures,
        tuple(failures),
        {"task": label, "status": status, "applicability": applicability},
    )


def _dense(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    intelligence = receipt.get("repository_intelligence") or {}
    if (
        intelligence.get("denominator_excluded") is True
        and str(intelligence.get("applicability") or "") == "not_applicable_no_supported_source"
    ):
        return ReleaseGateCheck(
            "dense_backend",
            True,
            (),
            {"task": label, "applicability": "not_applicable_no_supported_source"},
        )
    retrieval = receipt.get("preemptive_retrieval") or {}
    backend = retrieval.get("dense_backend")
    failures: list[str] = []
    if not isinstance(backend, dict):
        failures.append(f"{label}:dense_backend_receipt_missing")
    else:
        if backend.get("available") is not True:
            failures.append(f"{label}:dense_backend_unavailable")
        if backend.get("failed") is True:
            failures.append(f"{label}:dense_backend_failed")
        if retrieval.get("dense_backend_error"):
            failures.append(f"{label}:dense_backend_error")
        legacy_identity = str(
            backend.get("backend_identity") or backend.get("model_revision") or ""
        )
        content_hashed_identity = (
            str(backend.get("backend") or "") == "snowflake_onnx"
            and bool(str(backend.get("model_name") or ""))
            and re.fullmatch(r"[0-9a-f]{64}", str(backend.get("model_sha256") or "")) is not None
        )
        if not legacy_identity and not content_hashed_identity:
            failures.append(f"{label}:dense_backend_identity_missing")
        if int(backend.get("network_calls") or 0) != 0:
            failures.append(f"{label}:dense_backend_network_calls")
        if int(backend.get("provider_calls") or 0) != 0:
            failures.append(f"{label}:dense_backend_provider_calls")
        if str(receipt.get("treatment_profile") or "") == "central_relational_v2":
            expected = {
                "backend": "snowflake_onnx",
                "model_name": SNOWFLAKE_MODEL_NAME,
                "model_revision": SNOWFLAKE_MODEL_REVISION,
                "model_sha256": SNOWFLAKE_MODEL_SHA256,
                "tokenizer_sha256": SNOWFLAKE_TOKENIZER_SHA256,
                "pooling": "cls",
                "normalization": "l2",
                "max_length": SNOWFLAKE_MAX_LENGTH,
            }
            for field, expected_value in expected.items():
                if backend.get(field) != expected_value:
                    failures.append(f"{label}:dense_backend_{field}_mismatch")
    return ReleaseGateCheck(
        "dense_backend",
        not failures,
        tuple(failures),
        {"task": label, "available": bool(isinstance(backend, dict) and backend.get("available"))},
    )


def _preflight(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    metrics = receipt.get("metrics") or {}
    features = receipt.get("features") or {}
    rows = features.get("preflight_receipts") or []
    failures: list[str] = []
    if int(metrics.get("preflight_calls") or 0) != len(rows):
        failures.append(f"{label}:preflight_receipt_count_mismatch")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            failures.append(f"{label}:preflight_malformed:{index}")
            continue
        decision = row.get("decision") or {}
        applied = str(row.get("applied_disposition") or "")
        disposition = str(decision.get("disposition") or "")
        if disposition == "pending" or applied == "pending":
            failures.append(f"{label}:preflight_pending:{index}")
        if applied == "" and int(metrics.get("preflight_calls") or 0):
            failures.append(f"{label}:preflight_unapplied:{index}")
    if int(metrics.get("preflight_duplicate_evidence") or 0) > 0:
        failures.append(f"{label}:preflight_duplicate_evidence")
    false_interventions = metrics.get("preflight_false_interventions")
    if isinstance(false_interventions, (int, float)) and false_interventions > 0:
        failures.append(f"{label}:preflight_false_interventions")
    return ReleaseGateCheck(
        "preflight_precision",
        not failures,
        tuple(failures),
        {"task": label, "calls": int(metrics.get("preflight_calls") or 0), "rows": len(rows)},
    )


def _decision_sufficiency(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    runtime = receipt.get("decision_sufficiency") or {}
    if runtime.get("enabled") is not True:
        return ReleaseGateCheck(
            "decision_sufficiency",
            True,
            (),
            {"task": label, "enabled": False},
        )
    rows = runtime.get("decisions") or []
    preflight_calls = int((receipt.get("metrics") or {}).get("preflight_calls") or 0)
    failures: list[str] = []
    if len(rows) != preflight_calls:
        failures.append(f"{label}:decision_preflight_count_mismatch")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            failures.append(f"{label}:decision_malformed:{index}")
            continue
        disposition = str(row.get("disposition") or "")
        if disposition not in {"pass", "return_eligible"}:
            failures.append(f"{label}:decision_disposition_invalid:{index}")
            continue
        if disposition != "return_eligible":
            continue
        bundle = row.get("bundle")
        if not isinstance(bundle, dict):
            failures.append(f"{label}:decision_bundle_missing:{index}")
            continue
        claims = bundle.get("claims") or []
        if (
            bundle.get("complete") is not True
            or len(claims) != 1
            or not str(bundle.get("source_revision") or "")
            or not str(bundle.get("graph_revision") or "")
            or str(bundle.get("selecting_request_hash") or "")
            != str(row.get("selecting_request_hash") or "")
        ):
            failures.append(f"{label}:decision_bundle_invalid:{index}")
        visible_ids = set((row.get("retrieval") or {}).get("provider_visible_claim_ids") or [])
        if any(str(claim.get("claim_id") or "") in visible_ids for claim in claims):
            failures.append(f"{label}:decision_repeated_visible_claim:{index}")
        for claim in claims:
            if not str(claim.get("claim_id") or "") or not str(
                claim.get("decision_claim_id") or ""
            ):
                failures.append(f"{label}:decision_claim_identity_missing:{index}")
            support_kind = str(claim.get("support_kind") or "")
            if support_kind != "certified_structural":
                continue
            relation = str(claim.get("relation") or "").strip().lower()
            if relation not in {
                "calls",
                "inverse:calls",
                "asserted_by",
                "inverse:asserted_by",
            }:
                failures.append(f"{label}:decision_relation_not_material:{index}")
            provenance = tuple(str(item).lower() for item in claim.get("provenance") or ())
            if not any(
                item.startswith(("edge_endpoint_symbol:", "edge_endpoint_start:"))
                for item in provenance
            ):
                failures.append(f"{label}:decision_span_not_edge_aligned:{index}")
    return ReleaseGateCheck(
        "decision_sufficiency",
        not failures,
        tuple(failures),
        {"task": label, "enabled": True, "decisions": len(rows)},
    )


def _persistent_execution_state(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Fail closed if the graph-first living state is absent or only bootstrapped."""

    intelligence = receipt.get("repository_intelligence") or {}
    source_less = bool(
        intelligence.get("denominator_excluded") is True
        and str(intelligence.get("applicability") or "") == "not_applicable_no_supported_source"
    )
    configuration = receipt.get("component_configuration") or {}
    runtime = receipt.get("persistent_execution_state") or {}
    activation = runtime.get("activation") or {}
    initialization = runtime.get("initialization") or {}
    initial_retrieval = runtime.get("initial_retrieval") or {}
    bootstrap = runtime.get("bootstrap") or {}
    state = runtime.get("state") or {}
    runtime_metrics = runtime.get("metrics") or {}
    metrics = receipt.get("metrics") or {}
    deliveries = runtime.get("deliveries") or []
    failures: list[str] = []

    activated_then_deactivated = bool(
        activation.get("ever_applicable") is True
        and activation.get("activation_action") is not None
        and str(activation.get("current_applicability") or "")
        == "not_applicable_no_supported_source"
    )

    if configuration.get("persistent_execution_state") is not True:
        failures.append(f"{label}:persistent_state_disabled")
    if source_less and not activated_then_deactivated:
        if not isinstance(runtime.get("activation"), dict):
            failures.append(f"{label}:persistent_activation_missing")
        elif (
            activation.get("initial_applicability")
            != "not_applicable_no_supported_source"
            or activation.get("current_applicability")
            != "not_applicable_no_supported_source"
            or activation.get("ever_applicable") is not False
            or activation.get("activation_action") is not None
            or activation.get("activation_call") is not None
            or activation.get("correctly_abstained") is not True
        ):
            failures.append(f"{label}:persistent_abstention_activation_invalid")
        if str(initialization.get("status") or "") != "not_applicable":
            failures.append(f"{label}:persistent_state_bad_not_applicable_status")
        if int(bootstrap.get("provider_calls") or 0) != 0:
            failures.append(f"{label}:persistent_state_source_less_bootstrap_call")
        if int(initial_retrieval.get("calls") or 0) != 0:
            failures.append(f"{label}:persistent_state_source_less_retrieval_call")
        if state or deliveries:
            failures.append(f"{label}:persistent_state_source_less_artifact")
        return ReleaseGateCheck(
            "persistent_execution_state",
            not failures,
            tuple(failures),
            {"task": label, "applicability": "not_applicable_no_supported_source"},
        )
    if activated_then_deactivated:
        # A task can create a supported source, exercise PES, and later remove
        # that source. Final non-applicability is a valid lifecycle transition,
        # not evidence that the task was incorrectly activated.
        if str(initialization.get("status") or "") != "initialized":
            failures.append(f"{label}:dynamic_deactivation_not_initialized")
        if int(runtime_metrics.get("context_compilations") or 0) <= 1:
            failures.append(f"{label}:dynamic_deactivation_not_repeated")
        if int(runtime_metrics.get("graph_rebases") or 0) <= 0:
            failures.append(f"{label}:dynamic_deactivation_rebase_missing")
        if deliveries:
            failures.append(f"{label}:dynamic_deactivation_stale_delivery")
        return ReleaseGateCheck(
            "persistent_execution_state",
            not failures,
            tuple(failures),
            {
                "task": label,
                "applicability": "dynamic_deactivation",
                "activation_action": activation.get("activation_action"),
            },
        )

    if str(initialization.get("status") or "") != "initialized":
        failures.append(f"{label}:persistent_state_not_initialized")
    initial_catalog = initialization.get("catalog") or {}
    initial_channels = {
        str(row.get("channel") or ""): row
        for row in initial_retrieval.get("channel_receipts") or ()
        if isinstance(row, dict)
    }
    if int(initial_retrieval.get("calls") or 0) != 1:
        failures.append(f"{label}:persistent_initial_retrieval_call_count")
    if str(initial_retrieval.get("status") or "") not in {"selected", "abstained"}:
        failures.append(f"{label}:persistent_initial_retrieval_incomplete")
    if not str(initial_retrieval.get("query_hash") or "") or str(
        initial_retrieval.get("source_revision") or ""
    ) != str(initial_catalog.get("graph_source_revision") or ""):
        failures.append(f"{label}:persistent_initial_retrieval_revision_or_query")
    if set(initial_channels) != {"exact", "lexical", "bm25", "dense", "structural"}:
        failures.append(f"{label}:persistent_initial_retrieval_channels")
    elif any(bool(row.get("failed")) for row in initial_channels.values()):
        failures.append(f"{label}:persistent_initial_retrieval_channel_failed")
    if configuration.get("preemptive_retrieval") is True and (
        initial_retrieval.get("runtime_cache_seeded") is not True
        or not str(initial_retrieval.get("runtime_cache_key") or "")
    ):
        failures.append(f"{label}:persistent_initial_retrieval_cache_not_seeded")
    ranked = initial_retrieval.get("ranked_files") or []
    catalog_items = initial_catalog.get("items") or []
    if ranked and not any(
        int(item.get("retrieval_rank") or 0) > 0
        and "hybrid_ranked_candidate" in set(item.get("provenance") or ())
        for item in catalog_items
        if isinstance(item, dict)
    ):
        failures.append(f"{label}:persistent_initial_retrieval_not_in_catalog")
    bootstrap_status = str(bootstrap.get("status") or "")
    deterministic_selection = str(bootstrap.get("selection_mode") or "") == "deterministic_v1"
    expected_bootstrap_mode = (
        "deterministic_selected" if deterministic_selection else "generative_selected"
    )
    if bootstrap_status != "selected":
        bootstrap_status_label = "deterministic" if deterministic_selection else "generative"
        failures.append(
            f"{label}:persistent_bootstrap_not_{bootstrap_status_label}"
        )
    if str(bootstrap.get("bootstrap_mode") or "") != expected_bootstrap_mode:
        failures.append(f"{label}:persistent_bootstrap_mode_invalid")
    expected_bootstrap_calls = 0 if deterministic_selection else 1
    if (
        int(bootstrap.get("logical_calls") or 0) != (0 if deterministic_selection else 1)
        or int(bootstrap.get("provider_calls") or 0) != expected_bootstrap_calls
    ):
        bootstrap_call_label = (
            "provider_call" if deterministic_selection else "exactly_one_call"
        )
        failures.append(
            f"{label}:persistent_bootstrap_not_{bootstrap_call_label}"
        )
    if int(bootstrap.get("action_executions") or 0) != 0:
        failures.append(f"{label}:persistent_bootstrap_action_executed")
    if deterministic_selection:
        if bootstrap.get("response_received") is True:
            failures.append(f"{label}:persistent_deterministic_bootstrap_provider_response")
        if not str(bootstrap.get("selection_input_sha256") or ""):
            failures.append(f"{label}:persistent_deterministic_selection_hash_missing")
        if int(bootstrap.get("selection_event_count") or 0) != 1:
            failures.append(f"{label}:persistent_deterministic_selection_event_missing")
        if int(bootstrap.get("selection_provider_calls") or 0) != 0:
            failures.append(f"{label}:persistent_deterministic_selection_provider_call")
    else:
        if bootstrap.get("response_received") is not True and not isinstance(
            bootstrap.get("provider_error"), dict
        ):
            failures.append(f"{label}:persistent_bootstrap_response_missing")
        if str(bootstrap.get("transport") or "") != "direct_single_provider_call":
            failures.append(f"{label}:persistent_bootstrap_transport_not_single_call")
        if str(bootstrap.get("provider_query_marker_error") or ""):
            failures.append(f"{label}:persistent_bootstrap_marker_failed")
        if not str(bootstrap.get("request_payload_sha256") or "") or not str(
            bootstrap.get("provider_messages_sha256") or ""
        ):
            failures.append(f"{label}:persistent_bootstrap_hash_missing")
    if int(bootstrap.get("visible_catalog_count") or 0) <= 0 or not str(
        bootstrap.get("visible_catalog_ids_sha256") or ""
    ):
        failures.append(f"{label}:persistent_bootstrap_visible_catalog_missing")
    if runtime.get("valid") is not True or runtime.get("failures"):
        failures.append(f"{label}:persistent_state_runtime_invalid")
    if not state or state.get("graph_current") is not True:
        failures.append(f"{label}:persistent_state_graph_not_current")
    if str(state.get("bootstrap_status") or "") != "selected":
        failures.append(f"{label}:persistent_state_selection_not_applied")
    if str(state.get("bootstrap_mode") or "") != expected_bootstrap_mode:
        failures.append(f"{label}:persistent_state_bootstrap_mode_mismatch")
    authorities = state.get("field_authority") or {}
    if (
        authorities.get("primary_focus_id") != "bootstrap_selected"
        or authorities.get("phase") != "deterministic_mutable"
        or authorities.get("current_focus") != "executor_observed"
    ):
        failures.append(f"{label}:persistent_state_authority_boundary_missing")

    executor_calls = int(receipt.get("executor_calls") or 0)
    actions = int(receipt.get("actions") or 0)
    action_accounting = receipt.get("action_accounting") or {}
    processed_actions = _as_int(
        action_accounting.get("processed")
        if "processed" in action_accounting
        else actions
    )
    executed_actions = _as_int(
        action_accounting.get("executed")
        if "executed" in action_accounting
        else (receipt.get("host_execution") or {}).get("decision_actions") or 0
    )
    if not isinstance(runtime.get("activation"), dict):
        failures.append(f"{label}:persistent_activation_missing")
    elif activation.get("ever_applicable") is not True or activation.get(
        "correctly_abstained"
    ) is not False:
        failures.append(f"{label}:persistent_activation_state_invalid")
    activation_action = int(activation.get("activation_action") or 0)
    activation_call = int(activation.get("activation_call") or 0)
    if activation.get("initial_applicability") == "not_applicable_no_supported_source":
        if not (
            1 <= activation_action <= max(actions, processed_actions)
            and 2 <= activation_call <= executor_calls
            and activation.get("current_applicability") == "source_backed"
        ):
            failures.append(f"{label}:persistent_dynamic_activation_boundary_invalid")
    elif not (
        activation.get("initial_applicability") == "source_backed"
        and activation.get("current_applicability") == "source_backed"
        and activation_action == 0
        and activation_call == 0
    ):
        failures.append(f"{label}:persistent_initial_activation_boundary_invalid")
    if executor_calls <= 0:
        failures.append(f"{label}:persistent_state_no_executor_call")
    model_call_contexts = receipt.get("model_call_contexts") or []
    eligible_contexts = [
        row
        for row in model_call_contexts
        if int(row.get("call") or 0) >= max(1, activation_call)
    ]
    processed_before_activation = _as_int(
        activation.get("processed_actions_before_activation")
        if "processed_actions_before_activation" in activation
        else activation_action
    )
    executed_at_activation = _as_int(
        activation.get("executed_actions_at_activation")
        if "executed_actions_at_activation" in activation
        else activation_action
    )
    expected_preflights = (
        processed_actions
        if activation_action == 0
        else max(0, processed_actions - processed_before_activation)
    )
    expected_postflights = (
        executed_actions
        if activation_action == 0
        else max(0, executed_actions - executed_at_activation + 1)
    )
    if int(runtime_metrics.get("context_compilations") or 0) != len(eligible_contexts):
        failures.append(f"{label}:persistent_context_compilation_count")
    if int(runtime_metrics.get("preflight_projections") or 0) != expected_preflights:
        failures.append(f"{label}:persistent_preflight_projection_count")
    if int(runtime_metrics.get("postflight_commits") or 0) != expected_postflights:
        failures.append(f"{label}:persistent_postflight_commit_count")
    dispatched_contexts = [
        row
        for row in eligible_contexts
        if row.get("dispatch_status") in {"invoked", "response_received", "response_error"}
    ]
    contribution_calls = {
        int(item.get("call") or 0): item
        for item in ((receipt.get("contribution_compiler") or {}).get("calls") or ())
        if isinstance(item, dict) and int(item.get("call") or 0) > 0
    }

    def persistent_value_rejected(call: int) -> bool:
        matches = [
            item
            for item in (contribution_calls.get(call) or {}).get("accounting") or ()
            if isinstance(item, dict)
            and item.get("surface") == "persistent_execution_state"
        ]
        return bool(
            len(matches) == 1
            and matches[0].get("disposition") == "value_rejected"
            and matches[0].get("reason_codes")
            and all(
                str(reason).startswith("provider_value_rejected:")
                for reason in matches[0].get("reason_codes") or ()
            )
        )
    delivered_contexts = 0
    for index, row in enumerate(eligible_contexts, start=1):
        frame = row.get("persistent_execution_state")
        call = int(row.get("call") or index)
        if not isinstance(frame, dict) or int(frame.get("provider_call") or 0) != call:
            failures.append(f"{label}:persistent_call_accounting_missing:{call}")
            continue
        delivered = bool(row.get("persistent_execution_state_delivered"))
        kind = str(frame.get("kind") or "")
        reasons = tuple(frame.get("reason_codes") or ())
        dispatched = row.get("dispatch_status") in {
            "invoked",
            "response_received",
            "response_error",
        }
        if delivered:
            delivered_contexts += 1
            if kind == "none" or not frame.get("claim_ids"):
                failures.append(f"{label}:persistent_delivered_frame_invalid:{call}")
        elif dispatched and runtime.get("valid") is True and state.get("graph_current") is True:
            if kind != "none" and persistent_value_rejected(call):
                pass
            elif kind != "none" or not reasons:
                failures.append(f"{label}:persistent_controller_accounting_missing:{call}")
            elif not set(reasons).intersection(_PERSISTENT_NONE_FRAME_REASONS):
                failures.append(f"{label}:persistent_nonmaterial_abstention_invalid:{call}")
        elif kind == "none" and not reasons:
            failures.append(f"{label}:persistent_controller_accounting_missing:{call}")
    dispatched_delivery_count = sum(
        bool(row.get("persistent_execution_state_delivered")) for row in dispatched_contexts
    )
    if (
        len(deliveries) != dispatched_delivery_count
        or delivered_contexts < dispatched_delivery_count
    ):
        failures.append(f"{label}:persistent_delivery_accounting_mismatch")
    if (
        bootstrap_status == "selected"
        and runtime.get("valid") is True
        and state.get("graph_current") is True
        and dispatched_contexts
        and dispatched_delivery_count == 0
    ):
        legal_empty = all(
            set(
                (row.get("persistent_execution_state") or {}).get("reason_codes") or ()
            ).intersection(_PERSISTENT_NONE_FRAME_REASONS)
            or persistent_value_rejected(int(row.get("call") or 0))
            for row in dispatched_contexts
        )
        if not legal_empty:
            failures.append(f"{label}:persistent_no_material_delivery")
    if int(metrics.get("persistent_state_bootstrap_calls") or 0) != expected_bootstrap_calls:
        failures.append(f"{label}:persistent_bootstrap_metric_mismatch")
    if int(metrics.get("persistent_state_initial_retrieval_calls") or 0) != 1:
        failures.append(f"{label}:persistent_initial_retrieval_metric_mismatch")
    if int(metrics.get("bootstrap_api_calls") or 0) != expected_bootstrap_calls:
        failures.append(f"{label}:persistent_bootstrap_api_metric_mismatch")
    if int(receipt.get("bootstrap_calls") or 0) != expected_bootstrap_calls:
        failures.append(f"{label}:persistent_bootstrap_total_mismatch")
    if int(receipt.get("calls") or 0) != executor_calls + expected_bootstrap_calls:
        failures.append(f"{label}:persistent_provider_call_accounting_mismatch")
    if str(metrics.get("provider_query_marker_error") or ""):
        failures.append(f"{label}:executor_provider_marker_failed")

    return ReleaseGateCheck(
        "persistent_execution_state",
        not failures,
        tuple(failures),
        {
            "task": label,
            "bootstrap_calls": int(bootstrap.get("provider_calls") or 0),
            "executor_calls": executor_calls,
            "deliveries": len(deliveries),
            "state_version": int(state.get("version") or 0),
        },
    )


def _repository_context_state(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Audit the unified semantic/execution/impact projection."""

    configuration = receipt.get("component_configuration") or {}
    runtime = receipt.get("repository_context") or {}
    decisions = runtime.get("decisions") or []
    deliveries = runtime.get("deliveries") or []
    failures: list[str] = []
    if configuration.get("persistent_execution_state") is not True:
        failures.append(f"{label}:repository_context_persistent_state_disabled")
    if configuration.get("relational_context") is not True:
        failures.append(f"{label}:repository_context_relational_disabled")
    if configuration.get("semantic_evidence") is not True:
        failures.append(f"{label}:repository_context_semantic_disabled")
    if runtime.get("enabled") is not True:
        failures.append(f"{label}:repository_context_runtime_disabled")
    source_less = (
        str((receipt.get("repository_intelligence") or {}).get("applicability") or "")
        == "not_applicable_no_supported_source"
    )
    if not source_less and not decisions:
        failures.append(f"{label}:repository_context_opportunity_accounting_missing")
    claims: set[str] = set()
    for index, row in enumerate(deliveries, start=1):
        row_claims = tuple(str(item) for item in row.get("claim_ids") or ())
        if not row_claims or not row.get("projection"):
            failures.append(f"{label}:repository_context_delivery_support_missing:{index}")
        projection = row.get("projection") or {}
        execution_views = tuple(projection.get("execution_views") or ())
        if execution_views:
            coverage = projection.get("process_coverage") or {}
            coverage_valid = bool(
                isinstance(coverage, dict)
                and str(coverage.get("profile_id") or "")
                == "gt.certified_process.v1"
                and int(coverage.get("max_depth") or 0) > 0
                and int(coverage.get("max_branching") or 0) > 0
                and int(coverage.get("max_execution_views") or 0) > 0
                and int(coverage.get("returned_views") or 0) == len(execution_views)
                and int(coverage.get("candidate_views") or 0) >= len(execution_views)
                and int(coverage.get("lower_bound") or 0) == 1
            )
            if not coverage_valid:
                failures.append(
                    f"{label}:repository_context_process_coverage_invalid:{index}"
                )
        for claim in row_claims:
            if claim in claims:
                failures.append(f"{label}:repository_context_duplicate_claim:{claim}")
            claims.add(claim)
    if len(deliveries) != int(
        (receipt.get("metrics") or {}).get("repository_context_deliveries") or 0
    ):
        failures.append(f"{label}:repository_context_delivery_metric_mismatch")
    return ReleaseGateCheck(
        "repository_context_state",
        not failures,
        tuple(failures),
        {
            "task": label,
            "opportunities": len(decisions),
            "deliveries": len(deliveries),
            "claims": len(claims),
        },
    )


def _product_mechanism_census(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Require the one canonical product identity: 17 features plus PES."""

    census = receipt.get("product_mechanism_census") or {}
    mechanism_ids = tuple(str(item) for item in census.get("mechanism_ids") or ())
    configured_ids = tuple(
        str(item) for item in census.get("configured_mechanism_ids") or ()
    )
    mechanism_key = "persistent_execution_state"
    mechanism = census.get(mechanism_key) or {}
    failures: list[str] = []
    expected_contract = "17_legacy_features_plus_1_persistent_state"
    if str(census.get("accounting_contract") or "") != expected_contract:
        failures.append(f"{label}:product_mechanism_contract_missing")
    if int(census.get("legacy_feature_count") or 0) != 17:
        failures.append(f"{label}:legacy_feature_count_not_17")
    if int(census.get("product_mechanism_count") or 0) != 18 or len(mechanism_ids) != 18:
        failures.append(f"{label}:product_mechanism_count_not_18")
    expected_ids = (*CENTRAL_FEATURE_IDS, mechanism_key)
    if mechanism_ids != expected_ids or len(set(mechanism_ids)) != 18:
        failures.append(f"{label}:product_mechanism_identity_invalid")
    if int(census.get("configured_mechanism_count") or 0) != 18 or configured_ids != mechanism_ids:
        failures.append(f"{label}:not_all_product_mechanisms_configured")
    mechanism_applicable = mechanism.get("applicable") is not False
    activation = (receipt.get("persistent_execution_state") or {}).get("activation") or {}
    dynamic_deactivation = bool(
        activation.get("ever_applicable") is True
        and activation.get("activation_action") is not None
        and str(activation.get("current_applicability") or "")
        == "not_applicable_no_supported_source"
    )
    if dynamic_deactivation:
        mechanism_applicable = True
    failure_prefix = "persistent"
    if mechanism.get("configured") is not True:
        failures.append(f"{label}:{failure_prefix}_product_mechanism_not_configured")
    if mechanism_applicable:
        selection_mode = str(mechanism.get("selection_mode") or "generative")
        bootstrap_calls = int(mechanism.get("bootstrap_calls") or 0)
        selection_events = _as_int(
            mechanism.get("selection_event_count")
            if mechanism.get("selection_event_count") is not None
            else bootstrap_calls
        )
        selection_provider_calls = _as_int(
            mechanism.get("selection_provider_calls")
            if mechanism.get("selection_provider_calls") is not None
            else bootstrap_calls
        )
        bootstrap_provider_calls = _as_int(
            mechanism.get("bootstrap_provider_calls")
            if mechanism.get("bootstrap_provider_calls") is not None
            else bootstrap_calls
        )
        if selection_events != 1:
            failures.append(f"{label}:{failure_prefix}_selection_count")
        if selection_mode == "deterministic_v1":
            if selection_provider_calls or bootstrap_provider_calls or bootstrap_calls:
                failures.append(
                    f"{label}:{failure_prefix}_deterministic_selection_used_provider"
                )
        elif (
            selection_mode != "generative"
            or selection_provider_calls != 1
            or bootstrap_provider_calls != 1
            or bootstrap_calls != 1
        ):
            failures.append(f"{label}:{failure_prefix}_bootstrap_count")
        if mechanism.get("exercised") is not True and not dynamic_deactivation:
            failures.append(f"{label}:{failure_prefix}_product_mechanism_not_exercised")
        executor_calls = int(receipt.get("executor_calls") or 0)
        if (
            executor_calls > 0
            and (
                mechanism.get("repeated_deterministic_use") is not True
                or int(mechanism.get("lifecycle_use_count") or 0) <= 1
            )
        ):
            failures.append(f"{label}:{failure_prefix}_product_mechanism_not_repeated")
    elif (
        mechanism.get("correctly_abstained") is not True
        or mechanism.get("exercised") is not False
        or int(mechanism.get("bootstrap_calls") or 0) != 0
    ):
        failures.append(f"{label}:{failure_prefix}_product_abstention_invalid")
    # Natural trigger absence is evidence about the trajectory, not a failed
    # feature implementation. Preserve its separate count and never inflate it
    # to manufacture an 18/18 live-fire claim.
    naturally_fired = int(census.get("naturally_fired_legacy_feature_count") or 0)
    if not 0 <= naturally_fired <= 17:
        failures.append(f"{label}:natural_feature_fire_count_invalid")
    return ReleaseGateCheck(
        "product_mechanism_census",
        not failures,
        tuple(failures),
        {
            "task": label,
            "configured": len(configured_ids),
            "naturally_fired_legacy": naturally_fired,
            "profile_id": str(receipt.get("treatment_profile") or "central_pes_v1"),
            f"{failure_prefix}_exercised": mechanism.get("exercised") is True,
        },
    )


def _delivery(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    runtime_failures, runtime_summary = audit_runtime_receipt(receipt, task=label)
    _rows, delivery_failures, delivery_summary = audit_provider_deliveries(receipt, task=label)
    failures = [*runtime_failures, *delivery_failures]
    return ReleaseGateCheck(
        "delivery_timing_accounting",
        not failures,
        tuple(failures),
        {"runtime": runtime_summary, "provider": delivery_summary},
    )


def _contribution_budget(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    runtime = receipt.get("contribution_compiler") or {}
    calls = runtime.get("calls") or []
    configuration = receipt.get("component_configuration") or {}
    configured_budget = int(configuration.get("gt_request_token_budget") or 0)
    configured_task_budget = configuration.get("gt_task_evidence_budget_tokens")
    task_budget = runtime.get("task_budget")
    failures: list[str] = []
    if configured_budget <= 0:
        failures.append(f"{label}:gt_request_token_budget_missing")
    model_call_contexts = receipt.get("model_call_contexts") or []
    if len(calls) != len(model_call_contexts):
        failures.append(f"{label}:contribution_compiler_call_count")
    for index, row in enumerate(calls, start=1):
        if int(row.get("candidate_count") or 0) != int(row.get("accounted_count") or 0):
            failures.append(f"{label}:contribution_unaccounted:{index}")
        call_budget = int(row.get("token_budget") or 0)
        payload_tokens = int(row.get("payload_tokens") or 0)
        task_payload_tokens = int(
            row.get("task_budget_tokens")
            if "task_budget_tokens" in row
            else payload_tokens
        )
        if (
            call_budget > configured_budget
            or (configured_task_budget is None and call_budget != configured_budget)
            or (configured_task_budget is not None and call_budget < 0)
        ):
            failures.append(f"{label}:contribution_token_budget_mismatch:{index}")
        if payload_tokens > call_budget or payload_tokens > configured_budget:
            failures.append(f"{label}:contribution_token_budget_exceeded:{index}")
        task_call_budget = row.get("task_budget_token_limit")
        if task_call_budget is not None and task_payload_tokens > int(task_call_budget):
            failures.append(f"{label}:contribution_task_budget_exceeded:{index}")
        selected_surfaces = tuple(row.get("selected_surfaces") or ())
        if len(selected_surfaces) != len(set(selected_surfaces)):
            failures.append(f"{label}:contribution_surface_duplicate:{index}")
    if configured_task_budget is None:
        if task_budget is not None:
            failures.append(f"{label}:unexpected_contribution_task_budget")
    else:
        configured_task_budget = int(configured_task_budget)
        if not isinstance(task_budget, dict):
            failures.append(f"{label}:contribution_task_budget_missing")
        else:
            used_regular = int(task_budget.get("used_regular_tokens") or 0)
            used_critical = int(task_budget.get("used_critical_tokens") or 0)
            used_total = int(task_budget.get("used_tokens") or 0)
            reserve = int(task_budget.get("critical_reserve_tokens") or 0)
            # A compiler row is prepared before the durable provider marker.
            # Deadline/marker holds are valid non-deliveries and must not be
            # charged against the cumulative budget. Older receipts lack the
            # status field and are conservatively treated as dispatched.
            dispatched_calls = [
                row
                for row in calls
                if row.get("dispatch_status", "dispatched") == "dispatched"
            ]
            prepared_not_sent = [
                row
                for row in calls
                if row.get("dispatch_status") in {"prepared", "prepared_not_sent"}
            ]
            invalid_statuses = [
                row.get("dispatch_status")
                for row in calls
                if row.get("dispatch_status")
                not in {None, "dispatched", "prepared", "prepared_not_sent"}
            ]
            if invalid_statuses:
                failures.append(f"{label}:contribution_dispatch_status_invalid")
            payload_total = sum(
                int(
                    row.get("task_budget_tokens")
                    if "task_budget_tokens" in row
                    else row.get("payload_tokens") or 0
                )
                for row in dispatched_calls
            )
            if int(task_budget.get("token_budget") or -1) != configured_task_budget:
                failures.append(f"{label}:contribution_task_budget_mismatch")
            if not (0 <= reserve <= configured_task_budget):
                failures.append(f"{label}:contribution_task_reserve_invalid")
            if used_total != used_regular + used_critical or used_total != payload_total:
                failures.append(f"{label}:contribution_task_usage_mismatch")
            if any(
                int(row.get("task_budget_tokens") or 0) < 0
                for row in prepared_not_sent
            ):
                failures.append(f"{label}:contribution_task_unsent_usage_invalid")
            if used_critical > reserve or used_total > configured_task_budget:
                failures.append(f"{label}:contribution_task_budget_exceeded")
    return ReleaseGateCheck(
        "contribution_budget",
        not failures,
        tuple(failures),
        {
            "task": label,
            "calls": len(calls),
            "configured_token_budget": configured_budget,
            "configured_task_budget": configured_task_budget,
        },
    )


def _provider_value_contract(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Fail closed when provider text lacks counterfactual information value."""

    required = str(receipt.get("treatment_profile") or "") == "central_relational_v2"
    if not required:
        return ReleaseGateCheck(
            "provider_value_contract",
            True,
            (),
            {"task": label, "required": False},
        )

    runtime = receipt.get("contribution_compiler") or {}
    calls = runtime.get("calls") or []
    failures: list[str] = []
    if runtime.get("schema") != "gt.contribution_compiler.runtime.v2":
        failures.append(f"{label}:provider_value_compiler_schema")
    if runtime.get("provider_value_contract") != "gt.provider_value.v1":
        failures.append(f"{label}:provider_value_contract_missing")

    allowed_classes = {
        "action_local_relation",
        "execution_contradiction",
        "certified_predecision_gap",
    }
    allowed_dispositions = {"same_observation", "predecision"}
    selected_certificate_count = 0
    for index, call in enumerate(calls, start=1):
        call_number = int(call.get("call") or index)
        accounting = [
            row for row in call.get("accounting") or () if isinstance(row, dict)
        ]
        if any(row.get("disposition") == "value_uncertified" for row in accounting):
            failures.append(f"{label}:provider_value_uncertified:{call_number}")
        selected_ids = {
            str(item) for item in call.get("selected_ids") or () if str(item)
        }
        accounted_selected = {
            str(row.get("contribution_id") or "")
            for row in accounting
            if row.get("disposition") == "selected"
        }
        if selected_ids != accounted_selected:
            failures.append(f"{label}:provider_value_selected_accounting:{call_number}")

        certificates = [
            row
            for row in call.get("value_certificates") or ()
            if isinstance(row, dict)
        ]
        certificate_ids: set[tuple[str, str]] = set()
        certified_contributions: set[str] = set()
        for certificate in certificates:
            contribution_id = str(certificate.get("contribution_id") or "")
            claim_id = str(certificate.get("claim_id") or "")
            identity = (contribution_id, claim_id)
            if not contribution_id or not claim_id or identity in certificate_ids:
                failures.append(
                    f"{label}:provider_value_certificate_identity:{call_number}"
                )
                continue
            certificate_ids.add(identity)
            certified_contributions.add(contribution_id)
            if contribution_id not in selected_ids:
                failures.append(
                    f"{label}:provider_value_certificate_unselected:{call_number}:{claim_id}"
                )
            if (
                certificate.get("value_class") not in allowed_classes
                or certificate.get("disposition") not in allowed_dispositions
                or certificate.get("completeness") != "exact"
                or not certificate.get("authority")
                or not certificate.get("source_revision")
                or not certificate.get("anchors")
                or not certificate.get("novelty_basis")
                or not certificate.get("decision_point")
                or not certificate.get("replaces_operation")
                or not certificate.get("materiality_reason")
            ):
                failures.append(
                    f"{label}:provider_value_certificate_rejected:{call_number}:{claim_id}"
                )
        if selected_ids - certified_contributions:
            failures.append(f"{label}:provider_value_selected_uncertified:{call_number}")
        selected_certificate_count += len(certificates)

    return ReleaseGateCheck(
        "provider_value_contract",
        not failures,
        tuple(failures),
        {
            "task": label,
            "required": True,
            "calls": len(calls),
            "selected_certificates": selected_certificate_count,
        },
    )


def _action_lifecycle(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    accounting = receipt.get("action_accounting")
    metrics = receipt.get("metrics") or {}
    failures: list[str] = []
    if not isinstance(accounting, dict):
        failures.append(f"{label}:action_accounting_missing")
        return ReleaseGateCheck(
            "action_lifecycle",
            False,
            tuple(failures),
            {"task": label},
        )
    if accounting.get("schema") != "gt.action_accounting.v1":
        failures.append(f"{label}:action_accounting_schema")
    values: dict[str, int] = {}
    for key in ("selected", "processed", "executed", "returned", "cancelled"):
        value = accounting.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            failures.append(f"{label}:action_accounting_invalid:{key}")
            value = 0
        values[key] = int(value)
    if values["selected"] != values["processed"] + values["cancelled"]:
        failures.append(f"{label}:selected_action_accounting_mismatch")
    if values["processed"] != values["executed"] + values["returned"]:
        failures.append(f"{label}:processed_action_accounting_mismatch")
    if int(receipt.get("actions") or 0) != values["processed"]:
        failures.append(f"{label}:receipt_action_accounting_mismatch")
    host_executed = int((receipt.get("host_execution") or {}).get("decision_actions") or 0)
    if host_executed != values["executed"]:
        failures.append(f"{label}:host_execution_action_accounting_mismatch")
    for key, metric_key in (
        ("selected", "selected_actions"),
        ("processed", "processed_actions"),
        ("executed", "executed_actions"),
        ("returned", "returned_actions"),
        ("cancelled", "cancelled_actions"),
    ):
        if metric_key in metrics and int(metrics.get(metric_key) or 0) != values[key]:
            failures.append(f"{label}:metric_action_accounting_mismatch:{metric_key}")
    return ReleaseGateCheck(
        "action_lifecycle",
        not failures,
        tuple(failures),
        {"task": label, **values},
    )


def _actor_action_conservation(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    accounting = receipt.get("actor_action_accounting") or {}
    counts = accounting.get("counts") or {}
    expected_actors = {
        "MODEL_DECISION",
        "TOOL_ACTION",
        "CONTROLLER_ACTION",
        "SUBSTRATE_PROBE",
        "HOST_OTHER",
    }
    failures: list[str] = []
    if accounting.get("schema") != "gt.action_accounting.v1":
        failures.append(f"{label}:actor_action_accounting_missing")
    if set(counts) != expected_actors:
        failures.append(f"{label}:actor_action_actor_set_invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts.values()
    ):
        failures.append(f"{label}:actor_action_count_invalid")
    host_total = accounting.get("host_execution_total")
    classified_host = sum(
        int(counts.get(actor) or 0)
        for actor in expected_actors - {"MODEL_DECISION"}
    )
    if (
        isinstance(host_total, bool)
        or not isinstance(host_total, int)
        or host_total < 0
        or classified_host != host_total
    ):
        failures.append(f"{label}:actor_action_host_conservation_mismatch")
    if accounting.get("conservation_valid") is not True:
        failures.append(f"{label}:actor_action_conservation_invalid")
    receipt_host_total = (receipt.get("host_execution") or {}).get(
        "actual_environment_execs"
    )
    if isinstance(receipt_host_total, int) and receipt_host_total != host_total:
        failures.append(f"{label}:actor_action_receipt_host_mismatch")
    metrics = receipt.get("metrics") or {}
    if metrics.get("effective_actions_schema") != "model-selected-tool-actions-v3":
        failures.append(f"{label}:effective_actions_schema_invalid")
    effective_actions = metrics.get("effective_actions")
    if (
        isinstance(effective_actions, bool)
        or not isinstance(effective_actions, int)
        or effective_actions != int(counts.get("TOOL_ACTION") or 0)
    ):
        failures.append(f"{label}:effective_actions_actor_mismatch")
    return ReleaseGateCheck(
        "actor_action_conservation",
        not failures,
        tuple(failures),
        {"task": label, "host_execution_total": host_total, "counts": counts},
    )


def _runtime_lifecycle(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    lifecycle = receipt.get("runtime_lifecycle") or {}
    failures: list[str] = []
    phases = lifecycle.get("phases") or []
    observed_phases = [str(row.get("phase") or "") for row in phases]
    if lifecycle.get("schema") != "gt.runtime_lifecycle.v1":
        failures.append(f"{label}:runtime_lifecycle_missing")
    if lifecycle.get("model_agnostic") is not True:
        failures.append(f"{label}:runtime_lifecycle_model_specific")
    if observed_phases != ["SNAPSHOT", "SUBSTRATE", "SOLVER", "FINALIZATION"]:
        failures.append(f"{label}:runtime_lifecycle_phase_order_invalid")
    prepared = int(lifecycle.get("prepared_calls") or 0)
    dispatched = int(lifecycle.get("dispatched_calls") or 0)
    not_sent = int(lifecycle.get("not_sent_calls") or 0)
    received = int(lifecycle.get("received_responses") or 0)
    response_errors = int(lifecycle.get("response_errors") or 0)
    calls = lifecycle.get("calls") or []
    if prepared != len(calls) or prepared != dispatched + not_sent:
        failures.append(f"{label}:runtime_lifecycle_call_conservation_mismatch")
    if received + response_errors > dispatched:
        failures.append(f"{label}:runtime_lifecycle_response_conservation_mismatch")
    if lifecycle.get("lifecycle_conservation_valid") is not True:
        failures.append(f"{label}:runtime_lifecycle_conservation_invalid")
    if lifecycle.get("action_conservation_valid") is not True:
        failures.append(f"{label}:runtime_lifecycle_action_conservation_invalid")
    if lifecycle.get("complete") is not True:
        failures.append(f"{label}:runtime_lifecycle_incomplete")
    return ReleaseGateCheck(
        "runtime_lifecycle",
        not failures,
        tuple(failures),
        {
            "task": label,
            "prepared_calls": prepared,
            "dispatched_calls": dispatched,
            "not_sent_calls": not_sent,
        },
    )


def _treatment_runtime_identity(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Prove the caller-selected treatment is the treatment that executed."""

    contract = receipt.get("treatment_runtime_contract")
    failures: list[str] = []
    observed_profile = str(receipt.get("treatment_profile") or "")
    if observed_profile != "central_relational_v2":
        failures.append(f"{label}:required_treatment_profile_mismatch")
    if not isinstance(contract, dict):
        failures.append(f"{label}:treatment_runtime_contract_missing")
        return ReleaseGateCheck(
            "treatment_runtime_identity",
            False,
            tuple(failures),
            {"task": label, "observed_profile": observed_profile},
        )
    if contract.get("schema") != "gt.treatment_runtime_arguments.v1":
        failures.append(f"{label}:treatment_runtime_contract_schema")
    supplied_hash = str(contract.get("contract_sha256") or "")
    material = dict(contract)
    material.pop("contract_sha256", None)
    expected_hash = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "surrogatepass")
    ).hexdigest()
    if supplied_hash != expected_hash:
        failures.append(f"{label}:treatment_runtime_contract_hash")
    kwargs = contract.get("agent_kwargs")
    configuration = receipt.get("component_configuration") or {}
    effective_kwargs = configuration.get("effective_runtime_agent_kwargs")
    if not isinstance(kwargs, dict):
        failures.append(f"{label}:treatment_runtime_agent_kwargs_missing")
    elif not isinstance(effective_kwargs, dict):
        failures.append(f"{label}:effective_runtime_agent_kwargs_missing")
    else:
        for key, expected_value in kwargs.items():
            if key == "benchmark_identity":
                continue
            if key not in effective_kwargs:
                failures.append(f"{label}:treatment_runtime_{key}_unobserved")
            elif effective_kwargs.get(key) != expected_value:
                failures.append(f"{label}:treatment_runtime_{key}_mismatch")
    if supplied_hash != str(
        configuration.get("treatment_runtime_contract_sha256") or ""
    ):
        failures.append(f"{label}:treatment_runtime_receipt_hash_mismatch")
    return ReleaseGateCheck(
        "treatment_runtime_identity",
        not failures,
        tuple(failures),
        {
            "task": label,
            "observed_profile": observed_profile,
            "contract_sha256": supplied_hash,
        },
    )


def _deterministic_task_controls(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Require graph-independent context and convergence lifecycle accounting."""

    configuration = receipt.get("component_configuration") or {}
    semantic = receipt.get("task_semantic_substrate") or {}
    convergence = receipt.get("convergence_controller") or {}
    contexts = receipt.get("model_call_contexts") or []
    compilations = semantic.get("compilations") or []
    preflights = convergence.get("preflights") or []
    failures: list[str] = []
    if configuration.get("task_semantic_substrate") is not True:
        failures.append(f"{label}:task_semantic_substrate_disabled")
    if semantic.get("schema") != "gt.task_semantic_substrate.v1":
        failures.append(f"{label}:task_semantic_substrate_receipt_missing")
    if len(compilations) != len(contexts):
        failures.append(f"{label}:task_semantic_compilation_call_count")
    for index, row in enumerate(compilations, start=1):
        if int(row.get("candidate_count") or 0) != int(row.get("accounted_count") or 0):
            failures.append(f"{label}:task_semantic_unaccounted:{index}")
    if convergence.get("schema") != "gt.convergence_controller.v1":
        failures.append(f"{label}:convergence_controller_receipt_missing")
    if len(preflights) != int(receipt.get("actions") or 0):
        failures.append(f"{label}:convergence_preflight_action_count")
    return ReleaseGateCheck(
        "deterministic_task_controls",
        not failures,
        tuple(failures),
        {
            "task": label,
            "semantic_compilations": len(compilations),
            "convergence_preflights": len(preflights),
        },
    )


def _outcome_preservation(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Require the four fail-open controls used by the frozen treatment."""

    configuration = receipt.get("component_configuration") or {}
    required = (
        "context_compaction",
        "completion_controller",
        "progress_control",
        "adaptive_validation_timeout",
    )
    failures = list(
        f"{label}:{name}_disabled" for name in required if configuration.get(name) is not True
    )
    if str(receipt.get("treatment_profile") or "") == "central_relational_v2":
        epochs = (receipt.get("metrics") or {}).get("context_compaction_epochs") or ()
        if any(
            str(row.get("trigger_kind") or "") == "character_pressure"
            for row in epochs
            if isinstance(row, dict)
        ):
            failures.append(f"{label}:soft_character_compaction_forbidden")
        for context in receipt.get("model_call_contexts") or ():
            if context.get("dispatch_status") not in {
                "invoked",
                "response_received",
                "response_error",
            }:
                continue
            compiler = context.get("context_compiler") or {}
            input_hash = str(compiler.get("assistant_messages_input_sha256") or "")
            output_hash = str(compiler.get("assistant_messages_output_sha256") or "")
            if (
                compiler.get("assistant_messages_preserved_exactly") is not True
                or re.fullmatch(r"[0-9a-f]{64}", input_hash) is None
                or input_hash != output_hash
            ):
                failures.append(
                    f"{label}:assistant_provider_history_not_exact:{int(context.get('call') or 0)}"
                )
    return ReleaseGateCheck(
        "outcome_preservation_controls",
        not failures,
        tuple(failures),
        {
            "task": label,
            "configuration": {name: configuration.get(name) for name in required},
        },
    )


def _completion_integrity(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Fail closed on partial, stale, or internally inconsistent auto-submit proof."""

    if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
        return ReleaseGateCheck(
            "completion_integrity",
            True,
            (),
            {"task": label, "applicability": "legacy_profile"},
        )
    completion = receipt.get("completion") or {}
    plan = completion.get("plan") or {}
    certificates = tuple(
        row for row in completion.get("certificates") or () if isinstance(row, dict)
    )
    attempts = int(completion.get("auto_submit_attempts") or 0)
    submitted = int(completion.get("auto_submit_count") or 0)
    failures: list[str] = []
    if plan.get("schema") != "gt.completion_plan.v1":
        failures.append(f"{label}:completion_plan_schema_missing")
    predicates = tuple(
        row for row in plan.get("predicates") or () if isinstance(row, dict)
    )
    expected_predicate_ids = tuple(
        str(row.get("predicate_id") or "") for row in predicates
    )
    if plan.get("executable") is True and (
        plan.get("status") != "complete"
        or not expected_predicate_ids
        or any(not item for item in expected_predicate_ids)
        or len(expected_predicate_ids) != len(set(expected_predicate_ids))
        or any(not str(row.get("command") or "") for row in predicates)
    ):
        failures.append(f"{label}:completion_executable_plan_invalid")
    eligible_count = 0
    for index, certificate in enumerate(certificates, start=1):
        if certificate.get("schema") != "gt.completion_certificate.v1":
            failures.append(f"{label}:completion_certificate_schema:{index}")
        eligible = certificate.get("auto_submit_eligible") is True
        if eligible:
            eligible_count += 1
            observations = tuple(
                row
                for row in certificate.get("observations") or ()
                if isinstance(row, dict)
            )
            workspace_revision = str(certificate.get("workspace_revision") or "")
            observed_ids = tuple(
                str(row.get("predicate_id") or "") for row in observations
            )
            if (
                len(observed_ids) != len(set(observed_ids))
                or set(observed_ids) != set(expected_predicate_ids)
            ):
                failures.append(
                    f"{label}:completion_observation_set_mismatch:{index}"
                )
            for row in observations:
                predicate_id = str(row.get("predicate_id") or "missing")
                if re.fullmatch(
                    r"[0-9a-f]{64}", str(row.get("output_sha256") or "")
                ) is None:
                    failures.append(
                        f"{label}:completion_observation_hash_invalid:"
                        f"{index}:{predicate_id}"
                    )
            if (
                certificate.get("status") != "complete"
                or not observations
                or certificate.get("missing_predicate_ids")
                or certificate.get("failing_predicate_ids")
                or certificate.get("stale_predicate_ids")
                or not workspace_revision
                or any(
                    row.get("schema")
                    != "gt.completion_predicate_observation.v1"
                    or str(row.get("workspace_revision") or "")
                    != workspace_revision
                    or int(row.get("returncode") or 0) != 0
                    or not str(row.get("output_sha256") or "")
                    for row in observations
                )
            ):
                failures.append(f"{label}:completion_eligible_proof_invalid:{index}")
    if plan.get("executable") is not True and (eligible_count or submitted):
        failures.append(f"{label}:completion_partial_plan_submitted")
    if submitted > attempts or submitted > eligible_count:
        failures.append(f"{label}:completion_submit_accounting_invalid")
    return ReleaseGateCheck(
        "completion_integrity",
        not failures,
        tuple(failures),
        {
            "task": label,
            "certificates": len(certificates),
            "eligible": eligible_count,
            "attempts": attempts,
            "submitted": submitted,
        },
    )
def _diagnostic_isolation(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Prove the persistent-state-only ablation did not silently enable controls."""

    configuration = receipt.get("component_configuration") or {}
    disabled = (
        "context_compaction",
        "completion_controller",
        "progress_control",
        "adaptive_validation_timeout",
    )
    failures = [
        f"{label}:diagnostic_{name}_enabled"
        for name in disabled
        if configuration.get(name) is not False
    ]
    if configuration.get("persistent_execution_state") is not True:
        failures.append(f"{label}:diagnostic_persistent_state_disabled")
    if str(receipt.get("preflight_mode") or "") != "shadow":
        failures.append(f"{label}:diagnostic_preflight_not_shadow")
    return ReleaseGateCheck(
        "diagnostic_profile_isolation",
        not failures,
        tuple(failures),
        {
            "task": label,
            "preflight_mode": receipt.get("preflight_mode"),
            "configuration": {name: configuration.get(name) for name in disabled},
        },
    )


def _project_validation(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    runtime = receipt.get("project_validation") or {}
    probes = runtime.get("probes") or []
    failures: list[str] = []
    seen_revisions: set[str] = set()
    for index, probe in enumerate(probes, start=1):
        revision = str(probe.get("source_revision") or "")
        if not revision:
            failures.append(f"{label}:project_probe_revision_missing:{index}")
        elif revision in seen_revisions:
            failures.append(f"{label}:project_probe_repeated_revision:{revision}")
        seen_revisions.add(revision)
        status = str(probe.get("status") or "")
        allowed_statuses = {"pass", "fail"}
        if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
            allowed_statuses.add("failed_open")
        if status not in allowed_statuses:
            failures.append(f"{label}:project_probe_status_invalid:{index}")
        if status == "fail" and not str(probe.get("diagnostic") or "").strip():
            failures.append(f"{label}:project_probe_failure_without_diagnostic:{index}")
    metrics = receipt.get("metrics") or {}
    if "project_validation_probe_attempts" in metrics and int(
        metrics.get("project_validation_probe_attempts") or 0
    ) != len(probes):
        failures.append(f"{label}:project_probe_count_mismatch")
    return ReleaseGateCheck(
        "project_validation",
        not failures,
        tuple(failures),
        {"task": label, "probes": len(probes)},
    )


def _retrieval_efficiency(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    runtime = receipt.get("preemptive_retrieval") or {}
    if runtime.get("enabled") is False:
        return ReleaseGateCheck(
            "retrieval_efficiency",
            True,
            (),
            {"task": label, "enabled": False},
        )
    decisions = runtime.get("decisions") or []
    failures: list[str] = []
    for index, row in enumerate(decisions, start=1):
        if not str(row.get("opportunity_kind") or ""):
            failures.append(f"{label}:retrieval_opportunity_missing:{index}")
        reasons = set(row.get("reason_codes") or ())
        channels = row.get("channel_receipts") or []
        if (
            str(receipt.get("treatment_profile") or "") == "central_relational_v2"
            and (receipt.get("repository_intelligence") or {}).get("denominator_excluded")
            is not True
            and channels
        ):
            status = row.get("retrieval_status") or {}
            dense = next(
                (item for item in channels if str(item.get("channel") or "") == "dense"),
                None,
            )
            expected_mode = (
                "dense_fallback_only"
                if (receipt.get("component_configuration") or {}).get(
                    "dense_fallback_only"
                ) is True
                else "dense_primary"
            )
            status_valid = bool(
                isinstance(status, dict)
                and status.get("schema") == "gt.retrieval_status.v1"
                and status.get("expected_mode") == expected_mode
                and status.get("dense_channel_present") is (dense is not None)
                and int(status.get("dense_candidate_count") or 0)
                == int((dense or {}).get("candidate_count") or 0)
            )
            if not status_valid:
                failures.append(f"{label}:retrieval_status_missing_or_invalid:{index}")
        if (
            reasons
            & {
                "task_character_budget",
                "task_character_budget_closed_precheck",
                "opportunity_budget_reserved_precheck",
            }
            and channels
        ):
            failures.append(f"{label}:retrieval_work_after_budget_closed:{index}")
        if row.get("cache_hit") is True and any(
            float(channel.get("latency_ms") or 0.0) > 0.0 for channel in channels
        ):
            failures.append(f"{label}:retrieval_cache_hit_has_channel_latency:{index}")
    accounting = runtime.get("opportunity_accounting") or {}
    if decisions and (
        accounting.get("schema") != "gt.retrieval_opportunity_accounting.v1"
        or int(accounting.get("opportunities") or -1) != len(decisions)
    ):
        failures.append(f"{label}:retrieval_opportunity_accounting_invalid")
    metrics = receipt.get("metrics") or {}
    configuration = receipt.get("component_configuration") or {}
    if configuration.get("retrieval_delivery_mode") == "integrated_same_observation":
        if runtime.get("delivery_mode") != "integrated_same_observation":
            failures.append(f"{label}:integrated_retrieval_delivery_mode_missing")
        if runtime.get("deliveries"):
            failures.append(f"{label}:integrated_retrieval_has_standalone_delivery")
        if int(metrics.get("preemptive_retrieval_standalone_deliveries") or 0) != 0:
            failures.append(f"{label}:integrated_retrieval_standalone_metric")
    if int(metrics.get("preemptive_retrieval_duplicate_claims") or 0) > 0:
        failures.append(f"{label}:preemptive_duplicate_claims")
    return ReleaseGateCheck(
        "retrieval_efficiency",
        not failures,
        tuple(failures),
        {"task": label, "decisions": len(decisions)},
    )


def _baseline_shield(receipts: Iterable[dict[str, Any]]) -> ReleaseGateCheck:
    failures: list[str] = []
    count = 0
    for index, receipt in enumerate(receipts, start=1):
        count += 1
        label = f"off-{index}"
        if str(receipt.get("integration_mode") or "") != "off":
            failures.append(f"{label}:integration_mode_not_off")
        contexts = receipt.get("model_call_contexts") or []
        if not contexts:
            failures.append(f"{label}:missing_model_call_contexts")
        for call in contexts:
            if not isinstance(call, dict):
                failures.append(f"{label}:malformed_context")
                continue
            if call.get("provider_view_changed") is True:
                failures.append(f"{label}:provider_view_changed")
            stock = str(call.get("stock_provider_messages_sha256") or "")
            provider = str(call.get("provider_messages_sha256") or "")
            if not stock or not provider or stock != provider:
                failures.append(f"{label}:provider_view_not_stock_identical")
        metrics = receipt.get("metrics") or {}
        if int(metrics.get("provider_view_changed_calls") or 0) != 0:
            failures.append(f"{label}:provider_view_changed_metric")
    return ReleaseGateCheck(
        "baseline_shield",
        count > 0 and not failures,
        tuple(failures),
        {"off_receipts": count},
    )


def _audit_treatment_runtime_requirements(
    receipt: dict[str, Any],
    *,
    label: str,
    profile: str = "certified_full",
) -> tuple[ReleaseGateCheck, ...]:
    """Audit one treatment receipt without pretending an A/B control exists."""

    profile_check = (
        _diagnostic_isolation(receipt, label)
        if profile == "persistent_state_only"
        else _outcome_preservation(receipt, label)
    )
    relational_profile = str(receipt.get("treatment_profile") or "") == "central_relational_v2"
    capability_checks: tuple[ReleaseGateCheck, ...] = (
        (
            _provider_route_integrity(receipt, label),
            _repository_context_state(receipt, label),
            _observed_execution_fact_accounting(receipt, label),
        )
        if relational_profile
        else ()
    )
    return (
        _treatment_runtime_identity(receipt, label),
        _substrate(receipt, label),
        _dense(receipt, label),
        _delivery(receipt, label),
        _contribution_budget(receipt, label),
        _provider_value_contract(receipt, label),
        _action_lifecycle(receipt, label),
        _actor_action_conservation(receipt, label),
        _runtime_lifecycle(receipt, label),
        _deterministic_task_controls(receipt, label),
        _preflight(receipt, label),
        _decision_sufficiency(receipt, label),
        _persistent_execution_state(receipt, label),
        *capability_checks,
        _product_mechanism_census(receipt, label),
        profile_check,
        _completion_integrity(receipt, label),
        _project_validation(receipt, label),
        _terminal_validation_state(receipt, label),
        _retrieval_efficiency(receipt, label),
        _replay_and_intervention_audit(receipt, label),
        _task_artifact_integrity(receipt, label),
        _mechanical_completeness_runtime(receipt, label),
    )


def _provider_route_integrity(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Prove the configured endpoint is internally consistent and non-secret."""

    route = receipt.get("provider_route") or {}
    route_id = str(route.get("route_id") or "")
    api_base = str(route.get("api_base") or "")
    api_host = str(route.get("api_host") or "")
    parts = route_id.split(":")
    failures: list[str] = []
    if len(parts) != 3 or parts[1] != "native" or not parts[0]:
        failures.append(f"{label}:provider_route_id_invalid")
    configured_host = str(urlsplit(api_base).hostname or "")
    if not api_base.startswith("https://") or not configured_host:
        failures.append(f"{label}:provider_api_base_invalid")
    if configured_host != api_host or (len(parts) == 3 and parts[2] != api_host):
        failures.append(f"{label}:provider_route_host_mismatch")
    if route.get("credential_in_receipt") is not False:
        failures.append(f"{label}:provider_credential_receipted")
    if str(route.get("executor_retry_policy") or "") != "provider_once_no_retry":
        failures.append(f"{label}:provider_retry_policy_unverified")
    return ReleaseGateCheck(
        "provider_route_integrity",
        not failures,
        tuple(failures),
        {
            "task": label,
            "route_id": route_id,
            "api_host": api_host,
            "model": str(route.get("model") or ""),
        },
    )


def _observed_execution_fact_accounting(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Require an identity-level terminal outcome for every extracted fact."""

    section = receipt.get("observed_facts") or {}
    failures: list[str] = []
    if str(receipt.get("treatment_profile") or "") == "central_relational_v2" and not _bool(
        section.get("enabled")
    ):
        failures.append(f"{label}:observed_fact_surface_disabled")
    lifecycle_failures, details = audit_observed_fact_lifecycle(receipt)
    failures.extend(f"{label}:observed_fact_{failure}" for failure in lifecycle_failures)
    return ReleaseGateCheck(
        "observed_execution_fact_accounting",
        not failures,
        tuple(failures),
        {
            "task": label,
            **details,
        },
    )


def _task_artifact_integrity(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
        return ReleaseGateCheck(
            "task_artifact_integrity",
            True,
            (),
            {"task": label, "required": False},
        )
    integrity = receipt.get("task_artifact_integrity") or {}
    failures: list[str] = []
    if integrity.get("schema") != "gt.task_artifact_integrity.v1":
        failures.append(f"{label}:task_artifact_integrity_missing")
    if integrity.get("status") != "PASS" or integrity.get("failures"):
        failures.append(f"{label}:task_artifact_integrity_blocked")
    return ReleaseGateCheck(
        "task_artifact_integrity",
        not failures,
        tuple(failures),
        {
            "task": label,
            "required": True,
            "status": integrity.get("status"),
            "summary": dict(integrity.get("summary") or {}),
        },
    )


def _terminal_validation_state(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Require a current pass after material change when a check is known."""

    metrics = receipt.get("metrics") or {}
    material_changes = int(metrics.get("workspace_change_actions") or 0)
    checks = tuple(
        str(item)
        for item in (receipt.get("project_validation") or {}).get(
            "discovered_checks"
        )
        or ()
        if str(item)
    )
    if material_changes <= 0 or not checks:
        return ReleaseGateCheck(
            "terminal_validation_state",
            True,
            (),
            {
                "task": label,
                "required": False,
                "reason": (
                    "no_material_change"
                    if material_changes <= 0
                    else "no_source_derived_check"
                ),
            },
        )
    state = (
        (receipt.get("persistent_execution_state") or {}).get("state") or {}
    )
    validation = state.get("observed_validation") or state.get("validation") or {}
    final_revision = str(receipt.get("source_revision") or "")
    failures: list[str] = []
    probe_attempts = int(
        (receipt.get("metrics") or {}).get("project_validation_probe_attempts") or 0
    )
    observed_action_count = int((receipt.get("metrics") or {}).get("actions") or 0)
    action_rows = receipt.get("actions") or ()
    if not isinstance(action_rows, (list, tuple)):
        action_rows = ()
    explicit_validation_actions = sum(
        1
        for row in action_rows
        if isinstance(row, dict)
        if str(row.get("classification") or "").lower() in {"validation", "submit"}
    )
    if (
        validation.get("status") not in {"pass", "fail"}
        and not probe_attempts
        and not explicit_validation_actions
        and observed_action_count > 0
    ):
        return ReleaseGateCheck(
            "terminal_validation_state",
            True,
            (),
            {"task": label, "required": False, "reason": "no_validation_observed"},
        )
    # A current, explicitly observed failed validation is valid evidence of
    # the task outcome.  It is not a release-integrity defect.  Only a missing
    # or unattributed terminal status invalidates the receipt; solve scoring
    # remains responsible for classifying the failed task.
    if validation.get("status") not in {"pass", "fail"}:
        failures.append(f"{label}:terminal_validation_not_passed")
    if not final_revision or validation.get("source_revision") != final_revision:
        failures.append(f"{label}:terminal_validation_stale")
    if not str(validation.get("command") or ""):
        failures.append(f"{label}:terminal_validation_command_missing")
    return ReleaseGateCheck(
        "terminal_validation_state",
        not failures,
        tuple(failures),
        {
            "task": label,
            "required": True,
            "material_changes": material_changes,
            "known_checks": list(checks),
            "validation": dict(validation),
            "final_source_revision": final_revision,
        },
    )


def _mechanical_completeness_runtime(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Require one passing live barrier for every executor provider call."""

    if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
        return ReleaseGateCheck(
            "mechanical_completeness_runtime",
            True,
            (),
            {"task": label, "required": False},
        )
    contexts = receipt.get("model_call_contexts") or []
    dispatched = [
        row
        for row in contexts
        if row.get("dispatch_status")
        in {"invoked", "response_received", "response_error"}
    ]
    barrier_contexts = [
        row for row in contexts if "mechanical_completeness_barrier" in row
    ]
    runtime = receipt.get("mechanical_completeness") or {}
    barriers = runtime.get("provider_barriers") or []
    failures: list[str] = []
    if runtime.get("schema") != "gt.mechanical_completeness_runtime.v1":
        failures.append(f"{label}:mechanical_completeness_runtime_missing")
    if len(barriers) != len(barrier_contexts):
        failures.append(f"{label}:provider_barrier_count_mismatch")
    barrier_by_call = {int(row.get("call") or 0): row for row in barriers}
    if len(barrier_by_call) != len(barriers):
        failures.append(f"{label}:provider_barrier_duplicate_call")
    contribution_by_call = {
        int(row.get("call") or 0): row
        for row in (receipt.get("contribution_compiler") or {}).get("calls") or []
    }
    required_ids = {
        "request_identity",
        "provider_view_identity",
        "runtime_contract",
        "task_semantic_substrate",
        "source_snapshot_complete",
        "graph_current",
        "repository_intelligence",
        "retrieval",
        "persistent_state",
        "previous_action_finalized",
        "context_fact_accounting",
        "contribution_accounting",
        "provider_value_certification",
        "replay_capture",
    }
    for context in barrier_contexts:
        call = int(context.get("call") or 0)
        embedded = context.get("mechanical_completeness_barrier") or {}
        barrier = barrier_by_call.get(call) or {}
        if barrier != embedded:
            failures.append(f"{label}:provider_barrier_join_mismatch:{call}")
            continue
        if barrier.get("schema") != "gt.provider_mechanical_barrier.v1":
            failures.append(f"{label}:provider_barrier_schema:{call}")
        if context.get("dispatch_status") in {
            "prepared_not_sent",
            "mechanical_completeness_blocked",
        }:
            continue
        if barrier.get("status") != "PASS" or barrier.get("failures"):
            failures.append(f"{label}:provider_barrier_blocked:{call}")
        requirements = barrier.get("requirements") or []
        requirement_ids = [str(row.get("requirement_id") or "") for row in requirements]
        if (
            len(requirement_ids) != len(set(requirement_ids))
            or set(requirement_ids) != required_ids
        ):
            failures.append(f"{label}:provider_barrier_requirement_set_invalid:{call}")
        if not requirements or any(
            row.get("status") not in {"SATISFIED", "PROVEN_NOT_APPLICABLE"}
            for row in requirements
        ):
            failures.append(f"{label}:provider_barrier_requirement_invalid:{call}")
        compiler = contribution_by_call.get(call) or {}
        evidence = receipt.get("repository_evidence") or {}
        # Applicability is temporal.  A task may begin with no supported
        # source and create source later; the barrier for an early provider
        # call is legitimately PROVEN_NOT_APPLICABLE even though the final
        # receipt is source-backed.  Recomputing every call from final
        # repository state incorrectly turns that valid transition into a
        # reconstruction mismatch.  Use the call's captured applicability
        # evidence, with the final denominator only as a legacy fallback.
        graph_requirement = next(
            (
                row
                for row in barrier.get("requirements") or ()
                if str(row.get("requirement_id") or "") == "graph_current"
            ),
            {},
        )
        graph_evidence = graph_requirement.get("evidence") or {}
        graph_applicable = (
            bool(graph_evidence["applicable"])
            if "applicable" in graph_evidence
            else (
                (receipt.get("repository_intelligence") or {}).get(
                    "denominator_excluded"
                )
                is not True
            )
        )
        persistent = receipt.get("persistent_execution_state") or {}
        persistent_state = persistent.get("state") or {}
        action_accounting = receipt.get("action_accounting") or {}
        selected_ids = [str(item) for item in compiler.get("selected_ids") or ()]
        value_ids = [
            str(row.get("contribution_id") or "")
            for row in compiler.get("value_certificates") or ()
        ]
        expected = evaluate_provider_barrier(
            call=call,
            request_payload_sha256=str(context.get("request_payload_sha256") or ""),
            provider_messages_sha256=str(context.get("provider_messages_sha256") or ""),
            source_snapshot_complete=bool(
                (receipt.get("semantic_source_revision") or {}).get("complete")
            ),
            runtime_contract_ready=(
                (receipt.get("treatment_runtime_contract") or {}).get("schema")
                == "gt.treatment_runtime_arguments.v1"
            ),
            task_semantic_ready=(
                (receipt.get("task_semantic_substrate") or {}).get("schema")
                == "gt.task_semantic_substrate.v1"
            ),
            graph_applicable=graph_applicable,
            graph_current=bool(
                not graph_applicable
                or (
                    evidence.get("substrate_ready") is True
                    and evidence.get("index_current") is True
                )
            ),
            repository_intelligence_ready=bool(
                not graph_applicable
                or (
                    evidence.get("substrate_ready") is True
                    and evidence.get("index_current") is True
                    and evidence.get("intelligence_valid") is True
                )
            ),
            retrieval_ready=bool(
                not graph_applicable
                or ((receipt.get("preemptive_retrieval") or {}).get("dense_backend") or {}).get(
                    "available"
                )
            ),
            persistent_state_ready=bool(
                not graph_applicable
                or (
                    persistent_state.get("graph_current") is True
                    and persistent_state.get("bootstrap_status") == "selected"
                )
            ),
            previous_actions_finalized=bool(
                action_accounting.get("selected_equals_processed_plus_cancelled") is True
                and action_accounting.get("processed_equals_executed_plus_returned") is True
            ),
            context_candidate_count=int(context.get("context_fact_candidates") or 0),
            context_accounted_count=int(context.get("context_facts_accounted") or 0),
            contribution_candidate_count=int(compiler.get("candidate_count") or 0),
            contribution_accounted_count=int(compiler.get("accounted_count") or 0),
            selected_contribution_ids=selected_ids,
            provider_value_contribution_ids=value_ids,
            replay_capture_enabled=bool((receipt.get("replay_bundle") or {}).get("enabled")),
        )
        if barrier != expected:
            failures.append(f"{label}:provider_barrier_reconstruction_mismatch:{call}")
    return ReleaseGateCheck(
        "mechanical_completeness_runtime",
        not failures,
        tuple(failures),
        {
            "task": label,
            "required": True,
            "dispatched_calls": len(dispatched),
            "barrier_contexts": len(barrier_contexts),
            "provider_barriers": len(barriers),
        },
    )


def build_task_certificate(
    receipt: dict[str, Any], *, label: str
) -> dict[str, Any]:
    """Build the terminal certificate from independently recomputed checks."""

    checks = _audit_treatment_runtime_requirements(receipt, label=label)
    contexts = receipt.get("model_call_contexts") or []
    dispatched_calls = sum(
        row.get("dispatch_status")
        in {"invoked", "response_received", "response_error"}
        for row in contexts
    )
    barriers = (
        (receipt.get("mechanical_completeness") or {}).get("provider_barriers")
        or []
    )
    barrier_context_count = sum(
        "mechanical_completeness_barrier" in row for row in contexts
    )
    non_dispatched_calls = {
        int(row.get("call") or 0)
        for row in contexts
        if "mechanical_completeness_barrier" in row
        and row.get("dispatch_status") in {"prepared_not_sent", "mechanical_completeness_blocked"}
    }
    return build_task_execution_certificate(
        task=label,
        provider_barriers=barriers,
        dispatched_calls=dispatched_calls,
        barrier_context_count=barrier_context_count,
        non_dispatched_calls=non_dispatched_calls,
        release_checks=[check.as_dict() for check in checks],
    )


def _task_execution_certificate(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Verify the embedded certificate against independently recomputed state."""

    if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
        return ReleaseGateCheck(
            "task_execution_certificate",
            True,
            (),
            {"task": label, "required": False},
        )
    certificate = receipt.get("task_execution_certificate") or {}
    expected = build_task_certificate(receipt, label=label)
    failures: list[str] = []
    if certificate.get("schema") != "gt.task_execution_certificate.v1":
        failures.append(f"{label}:task_execution_certificate_missing")
    if certificate.get("status") != "PASS":
        failures.append(f"{label}:task_execution_certificate_blocked")
    if int(certificate.get("pending_requirement_count") or 0) != 0:
        failures.append(f"{label}:task_execution_certificate_pending")
    if int(certificate.get("failed_requirement_count") or 0) != 0:
        failures.append(f"{label}:task_execution_certificate_failed")
    if certificate.get("failures"):
        failures.append(f"{label}:task_execution_certificate_has_failures")
    for key in (
        "provider_barrier_count",
        "dispatched_provider_call_count",
    ):
        if certificate.get(key) != expected.get(key):
            failures.append(f"{label}:task_execution_certificate_{key}_mismatch")
    observed_requirements = {
        str(row.get("requirement_id") or ""): str(row.get("status") or "")
        for row in certificate.get("requirements") or ()
    }
    expected_requirements = {
        str(row.get("requirement_id") or ""): str(row.get("status") or "")
        for row in expected.get("requirements") or ()
    }
    if observed_requirements != expected_requirements:
        failures.append(f"{label}:task_execution_certificate_requirements_mismatch")
    if expected.get("status") != "PASS":
        failures.append(f"{label}:task_execution_recomputation_blocked")
    return ReleaseGateCheck(
        "task_execution_certificate",
        not failures,
        tuple(failures),
        {
            "task": label,
            "required": True,
            "embedded_status": certificate.get("status"),
            "recomputed_status": expected.get("status"),
        },
    )


def audit_treatment_runtime(
    receipt: dict[str, Any],
    *,
    label: str,
    profile: str = "certified_full",
) -> tuple[ReleaseGateCheck, ...]:
    """Audit requirements and verify the receipt's terminal certificate."""

    return (
        *_audit_treatment_runtime_requirements(
            receipt,
            label=label,
            profile=profile,
        ),
        _task_execution_certificate(receipt, label),
    )


def _replay_and_intervention_audit(
    receipt: dict[str, Any], label: str
) -> ReleaseGateCheck:
    """Require exact replay and intervention joins for the final profile."""

    if str(receipt.get("treatment_profile") or "") != "central_relational_v2":
        return ReleaseGateCheck(
            "replay_and_intervention_audit", True, (), {"task": label, "required": False}
        )
    replay = receipt.get("replay_bundle") or {}
    intelligence = receipt.get("repository_intelligence") or {}
    activation = (receipt.get("persistent_execution_state") or {}).get("activation") or {}
    source_less_abstention = bool(
        intelligence.get("denominator_excluded") is True
        and str(intelligence.get("applicability") or "")
        == "not_applicable_no_supported_source"
        and activation.get("ever_applicable") is not True
    )
    chain = receipt.get("intervention_chain") or {}
    failures: list[str] = []
    if replay.get("enabled") is not True:
        failures.append(f"{label}:replay_capture_disabled")
    if replay.get("trajectory_replay_ready") is not True and not source_less_abstention:
        failures.append(f"{label}:trajectory_replay_not_ready")
    if replay.get("call_count", 0) != len(receipt.get("model_call_contexts") or []):
        failures.append(f"{label}:replay_call_count_mismatch")
    if chain.get("schema") != "gt.intervention_chain.v2":
        failures.append(f"{label}:intervention_chain_missing")
    if chain.get("hidden_reasoning_inferred") is not False:
        failures.append(f"{label}:intervention_chain_reasoning_policy")
    if not isinstance(chain.get("path"), str) or not chain.get("path"):
        failures.append(f"{label}:intervention_chain_artifact_missing")
    if int(chain.get("rows") or 0) != int(chain.get("canonical_delivery_rows") or 0):
        failures.append(f"{label}:intervention_chain_delivery_coverage")
    return ReleaseGateCheck(
        "replay_and_intervention_audit",
        not failures,
        tuple(failures),
        {
            "task": label,
            "replay_ready": replay.get("trajectory_replay_ready") is True,
            "chain_rows": int(chain.get("rows") or 0),
        },
    )
def _repository_context_integrated_consequence(
    receipts: Iterable[dict[str, Any]],
) -> ReleaseGateCheck:
    relational = [
        receipt
        for receipt in receipts
        if str(receipt.get("treatment_profile") or "") == "central_relational_v2"
    ]
    applicable = [
        receipt
        for receipt in relational
        if (receipt.get("repository_intelligence") or {}).get("denominator_excluded")
        is not True
    ]
    deliveries = sum(
        len((receipt.get("repository_context") or {}).get("deliveries") or ())
        for receipt in applicable
    )
    opportunities = sum(
        len((receipt.get("repository_context") or {}).get("decisions") or ())
        for receipt in applicable
    )
    unaccounted = sum(
        1
        for receipt in applicable
        if not (receipt.get("repository_context") or {}).get("decisions")
    )
    failures = (
        ("repository_context_opportunity_accounting_missing",)
        if unaccounted
        else ()
    )
    return ReleaseGateCheck(
        "repository_context_integrated_consequence",
        not failures,
        failures,
        {
            "relational_receipts": len(relational),
            "applicable_receipts": len(applicable),
            "opportunities": opportunities,
            "deliveries": deliveries,
            "correct_abstentions_allowed": True,
        },
    )
def audit_release(
    receipts: Iterable[dict[str, Any]],
    *,
    static_evidence: dict[str, Any] | None = None,
    off_receipts: Iterable[dict[str, Any]] = (),
    required_treatment_profile: str = "central_relational_v2",
) -> ReleaseGateReport:
    treatment = list(receipts)
    off = list(off_receipts)
    checks: list[ReleaseGateCheck] = [_check_static(static_evidence)]
    if not treatment:
        checks.append(ReleaseGateCheck("treatment_receipts", False, ("no_treatment_receipts",), {}))
    for index, receipt in enumerate(treatment, start=1):
        label = f"treatment-{index}"
        observed_profile = str(receipt.get("treatment_profile") or "")
        checks.append(
            ReleaseGateCheck(
                "required_treatment_profile",
                observed_profile == required_treatment_profile,
                (
                    ()
                    if observed_profile == required_treatment_profile
                    else (f"{label}:required_treatment_profile_mismatch",)
                ),
                {
                    "task": label,
                    "required": required_treatment_profile,
                    "observed": observed_profile,
                },
            )
        )
        checks.extend(audit_treatment_runtime(receipt, label=label))
    checks.append(_repository_context_integrated_consequence(treatment))
    checks.append(_baseline_shield(off))
    failures = tuple(failure for check in checks for failure in check.failures)
    summary = {
        "treatment_receipts": len(treatment),
        "off_receipts": len(off),
        "checks_passed": sum(check.passed for check in checks),
        "checks_total": len(checks),
    }
    return ReleaseGateReport(
        "gt.release_gate.v1",
        "READY" if not failures and treatment else "BLOCKED",
        len(treatment),
        tuple(checks),
        failures,
        summary,
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", action="append", type=Path, required=True)
    parser.add_argument("--off-receipt", action="append", type=Path, default=[])
    parser.add_argument("--static-evidence", type=Path, required=True)
    parser.add_argument(
        "--required-treatment-profile",
        default="central_relational_v2",
        choices=("central_pes_v1", "central_relational_v2"),
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = audit_release(
        [_load(path) for path in args.receipt],
        static_evidence=_load(args.static_evidence),
        off_receipts=[_load(path) for path in args.off_receipt],
        required_treatment_profile=args.required_treatment_profile,
    )
    rendered = json.dumps(report.as_dict(), indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
