#!/usr/bin/env python3
"""Consolidated fail-closed release gate for the central GT treatment.

This is an evidence gate, not a benchmark runner.  Provider-free gates produce
the ``static_evidence`` object and each task produces a central receipt.  The
gate joins those outputs and refuses release when any required substrate,
dense backend, delivery, preflight, or baseline-shield fact is absent.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gt_engine.central_runtime import CENTRAL_FEATURE_IDS
from gt_engine.delivery_audit import audit_provider_deliveries
from gt_engine.runtime_gate import audit_runtime_receipt

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

    if configuration.get("persistent_execution_state") is not True:
        failures.append(f"{label}:persistent_state_disabled")
    if source_less:
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
    # Fallback keeps Mini-SWE alive but is not a valid generative treatment.
    if bootstrap_status != "selected":
        failures.append(f"{label}:persistent_bootstrap_not_generative")
    expected_bootstrap_mode = "generative_selected"
    if str(bootstrap.get("bootstrap_mode") or "") != expected_bootstrap_mode:
        failures.append(f"{label}:persistent_bootstrap_mode_invalid")
    if (
        int(bootstrap.get("logical_calls") or 0) != 1
        or int(bootstrap.get("provider_calls") or 0) != 1
    ):
        failures.append(f"{label}:persistent_bootstrap_not_exactly_one_call")
    if int(bootstrap.get("action_executions") or 0) != 0:
        failures.append(f"{label}:persistent_bootstrap_action_executed")
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
    host_executed = int((receipt.get("host_execution") or {}).get("decision_actions") or 0)
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
            1 <= activation_action <= actions
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
    expected_preflights = (
        actions if activation_action == 0 else max(0, actions - activation_action)
    )
    expected_postflights = (
        host_executed
        if activation_action == 0
        else max(0, host_executed - activation_action + 1)
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
            if kind != "none" or not reasons:
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
            for row in dispatched_contexts
        )
        if not legal_empty:
            failures.append(f"{label}:persistent_no_material_delivery")
    if int(metrics.get("persistent_state_bootstrap_calls") or 0) != 1:
        failures.append(f"{label}:persistent_bootstrap_metric_mismatch")
    if int(metrics.get("persistent_state_initial_retrieval_calls") or 0) != 1:
        failures.append(f"{label}:persistent_initial_retrieval_metric_mismatch")
    if int(metrics.get("bootstrap_api_calls") or 0) != 1:
        failures.append(f"{label}:persistent_bootstrap_api_metric_mismatch")
    if int(receipt.get("bootstrap_calls") or 0) != 1:
        failures.append(f"{label}:persistent_bootstrap_total_mismatch")
    if int(receipt.get("calls") or 0) != executor_calls + 1:
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


def _product_mechanism_census(receipt: dict[str, Any], label: str) -> ReleaseGateCheck:
    """Require the integrated product to account for 17 feature paths plus PES."""

    census = receipt.get("product_mechanism_census") or {}
    mechanism_ids = tuple(str(item) for item in census.get("mechanism_ids") or ())
    configured_ids = tuple(
        str(item) for item in census.get("configured_mechanism_ids") or ()
    )
    persistent = census.get("persistent_execution_state") or {}
    failures: list[str] = []
    if str(census.get("accounting_contract") or "") != (
        "17_legacy_features_plus_1_persistent_state"
    ):
        failures.append(f"{label}:product_mechanism_contract_missing")
    if int(census.get("legacy_feature_count") or 0) != 17:
        failures.append(f"{label}:legacy_feature_count_not_17")
    if int(census.get("product_mechanism_count") or 0) != 18 or len(mechanism_ids) != 18:
        failures.append(f"{label}:product_mechanism_count_not_18")
    expected_ids = (*CENTRAL_FEATURE_IDS, "persistent_execution_state")
    if mechanism_ids != expected_ids or len(set(mechanism_ids)) != 18:
        failures.append(f"{label}:product_mechanism_identity_invalid")
    if int(census.get("configured_mechanism_count") or 0) != 18 or configured_ids != mechanism_ids:
        failures.append(f"{label}:not_all_product_mechanisms_configured")
    persistent_applicable = persistent.get("applicable") is not False
    if persistent.get("configured") is not True:
        failures.append(f"{label}:persistent_product_mechanism_not_configured")
    if persistent_applicable:
        if persistent.get("exercised") is not True:
            failures.append(f"{label}:persistent_product_mechanism_not_exercised")
        if (
            persistent.get("repeated_deterministic_use") is not True
            or int(persistent.get("lifecycle_use_count") or 0) <= 1
        ):
            failures.append(f"{label}:persistent_product_mechanism_not_repeated")
    elif (
        persistent.get("correctly_abstained") is not True
        or persistent.get("exercised") is not False
        or int(persistent.get("bootstrap_calls") or 0) != 0
    ):
        failures.append(f"{label}:persistent_product_abstention_invalid")
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
            "persistent_exercised": persistent.get("exercised") is True,
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
    failures: list[str] = []
    if configured_budget <= 0:
        failures.append(f"{label}:gt_request_token_budget_missing")
    model_call_contexts = receipt.get("model_call_contexts") or []
    if len(calls) != len(model_call_contexts):
        failures.append(f"{label}:contribution_compiler_call_count")
    for index, row in enumerate(calls, start=1):
        if int(row.get("candidate_count") or 0) != int(row.get("accounted_count") or 0):
            failures.append(f"{label}:contribution_unaccounted:{index}")
        if int(row.get("token_budget") or 0) != configured_budget:
            failures.append(f"{label}:contribution_token_budget_mismatch:{index}")
        if int(row.get("payload_tokens") or 0) > configured_budget:
            failures.append(f"{label}:contribution_token_budget_exceeded:{index}")
        selected_surfaces = tuple(row.get("selected_surfaces") or ())
        if len(selected_surfaces) != len(set(selected_surfaces)):
            failures.append(f"{label}:contribution_surface_duplicate:{index}")
    return ReleaseGateCheck(
        "contribution_budget",
        not failures,
        tuple(failures),
        {
            "task": label,
            "calls": len(calls),
            "configured_token_budget": configured_budget,
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
    failures = tuple(
        f"{label}:{name}_disabled" for name in required if configuration.get(name) is not True
    )
    return ReleaseGateCheck(
        "outcome_preservation_controls",
        not failures,
        failures,
        {
            "task": label,
            "configuration": {name: configuration.get(name) for name in required},
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
        if status not in {"pass", "fail", "failed_open"}:
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


def audit_treatment_runtime(
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
    return (
        _substrate(receipt, label),
        _dense(receipt, label),
        _delivery(receipt, label),
        _contribution_budget(receipt, label),
        _preflight(receipt, label),
        _decision_sufficiency(receipt, label),
        _persistent_execution_state(receipt, label),
        _product_mechanism_census(receipt, label),
        profile_check,
        _project_validation(receipt, label),
        _retrieval_efficiency(receipt, label),
    )


def audit_release(
    receipts: Iterable[dict[str, Any]],
    *,
    static_evidence: dict[str, Any] | None = None,
    off_receipts: Iterable[dict[str, Any]] = (),
) -> ReleaseGateReport:
    treatment = list(receipts)
    off = list(off_receipts)
    checks: list[ReleaseGateCheck] = [_check_static(static_evidence)]
    if not treatment:
        checks.append(ReleaseGateCheck("treatment_receipts", False, ("no_treatment_receipts",), {}))
    for index, receipt in enumerate(treatment, start=1):
        label = f"treatment-{index}"
        checks.extend(audit_treatment_runtime(receipt, label=label))
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
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = audit_release(
        [_load(path) for path in args.receipt],
        static_evidence=_load(args.static_evidence),
        off_receipts=[_load(path) for path in args.off_receipt],
    )
    rendered = json.dumps(report.as_dict(), indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
