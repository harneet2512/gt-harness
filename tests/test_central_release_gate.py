from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from gt_engine.central_runtime import CENTRAL_FEATURE_IDS
from gt_engine.mechanical_completeness import evaluate_provider_barrier
from gt_engine.snowflake_onnx import (
    SNOWFLAKE_MAX_LENGTH,
    SNOWFLAKE_MODEL_NAME,
    SNOWFLAKE_MODEL_REVISION,
    SNOWFLAKE_MODEL_SHA256,
    SNOWFLAKE_TOKENIZER_SHA256,
)
from scripts.central_release_gate import (
    _completion_integrity,
    _contribution_budget,
    _dense,
    _mechanical_completeness_runtime,
    _replay_and_intervention_audit,
    _substrate,
    audit_release,
    audit_treatment_runtime,
    build_task_certificate,
)

STATIC = {
    "census_passed": True,
    "readiness": "READY",
    "pre_smoke_approved": True,
    "exact_commit": True,
}


def test_operator_entry_point_loads_from_repository_root() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/central_release_gate.py", "--help"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Consolidated fail-closed release gate" in completed.stdout


def _treatment() -> dict:
    return {
        "integration_mode": "active",
        "preflight_mode": "assistive_safe",
        "component_configuration": {
            "context_compaction": True,
            "completion_controller": True,
            "progress_control": True,
            "adaptive_validation_timeout": True,
            "preemptive_retrieval": True,
            "persistent_execution_state": True,
            "task_semantic_substrate": True,
            "convergence_controller": True,
            "gt_request_token_budget": 1200,
        },
        "repository_intelligence": {
            "status": "passed",
            "required": True,
            "failures": [],
            "graph_gate": {"blocked": False},
        },
        "preemptive_retrieval": {
            "dense_backend": {
                "available": True,
                "failed": False,
                "backend": "snowflake_onnx",
                "model_name": SNOWFLAKE_MODEL_NAME,
                "model_revision": SNOWFLAKE_MODEL_REVISION,
                "model_sha256": SNOWFLAKE_MODEL_SHA256,
                "tokenizer_sha256": SNOWFLAKE_TOKENIZER_SHA256,
                "pooling": "cls",
                "normalization": "l2",
                "max_length": SNOWFLAKE_MAX_LENGTH,
                "network_calls": 0,
                "provider_calls": 0,
            },
            "dense_backend_error": "",
            "decisions": [],
            "deliveries": [],
        },
        "decision_sufficiency": {
            "enabled": True,
            "decisions": [],
        },
        "metrics": {
            "repository_intelligence_valid": 1,
            "effects_produced": 0,
            "effects_applied": 0,
            "effect_trace_rows": 0,
            "context_compiler_effects_unaccounted": 0,
            "inert_private_state_effects": 0,
            "pending_decision_claim_effects": 0,
            "provider_requests_prepared": 1,
            "provider_request_hash_coverage": 1.0,
            "late_payload_deliveries": 0,
            "predictive_payload_deliveries": 0,
            "preflight_calls": 0,
            "preflight_duplicate_evidence": 0,
            "provider_view_changed_calls": 0,
            "persistent_state_initial_retrieval_calls": 1,
            "persistent_state_bootstrap_calls": 1,
            "bootstrap_api_calls": 1,
            "effective_actions": 0,
            "effective_actions_schema": "model-selected-tool-actions-v3",
        },
        "calls": 2,
        "executor_calls": 1,
        "bootstrap_calls": 1,
        "actions": 0,
        "action_accounting": {
            "schema": "gt.action_accounting.v1",
            "selected": 0,
            "processed": 0,
            "executed": 0,
            "returned": 0,
            "cancelled": 0,
            "selected_equals_processed_plus_cancelled": True,
            "processed_equals_executed_plus_returned": True,
        },
        "actor_action_accounting": {
            "schema": "gt.action_accounting.v1",
            "counts": {
                "MODEL_DECISION": 0,
                "TOOL_ACTION": 0,
                "CONTROLLER_ACTION": 0,
                "SUBSTRATE_PROBE": 0,
                "HOST_OTHER": 0,
            },
            "host_execution_total": 0,
            "conservation_valid": True,
            "reason_codes": [],
        },
        "runtime_lifecycle": {
            "schema": "gt.runtime_lifecycle.v1",
            "model_agnostic": True,
            "phases": [
                {"phase": "SNAPSHOT", "status": "PASS"},
                {"phase": "SUBSTRATE", "status": "PASS"},
                {"phase": "SOLVER", "status": "PASS"},
                {"phase": "FINALIZATION", "status": "PASS"},
            ],
            "calls": [
                {"sequence": 1, "kind": "persistent_bootstrap", "call": 1},
                {"sequence": 2, "kind": "executor", "call": 1},
            ],
            "prepared_calls": 2,
            "dispatched_calls": 2,
            "received_responses": 2,
            "response_errors": 0,
            "not_sent_calls": 0,
            "lifecycle_conservation_valid": True,
            "action_conservation_valid": True,
            "complete": True,
            "reason_codes": [],
        },
        "host_execution": {"decision_actions": 0, "actual_environment_execs": 0},
        "persistent_execution_state": {
            "activation": {
                "initial_applicability": "source_backed",
                "current_applicability": "source_backed",
                "ever_applicable": True,
                "activation_action": 0,
                "activation_call": 0,
                "correctly_abstained": False,
            },
            "initialization": {
                "status": "initialized",
                "catalog": {
                    "graph_source_revision": "source-1",
                    "items": [
                        {
                            "item_id": "pes-1",
                            "retrieval_rank": 1,
                            "provenance": ["hybrid_ranked_candidate"],
                        }
                    ],
                },
            },
            "initial_retrieval": {
                "status": "selected",
                "calls": 1,
                "provider_calls": 0,
                "action_executions": 0,
                "source_revision": "source-1",
                "query_hash": "retrieval-query-1",
                "runtime_cache_seeded": True,
                "runtime_cache_key": "retrieval-cache-1",
                "ranked_files": [{"path": "src/service.py"}],
                "selected_evidence": [{"path": "src/service.py"}],
                "channel_receipts": [
                    {
                        "channel": channel,
                        "candidate_count": 1,
                        "failed": False,
                        "available": True,
                    }
                    for channel in ("exact", "lexical", "bm25", "dense", "structural")
                ],
            },
            "bootstrap": {
                "status": "selected",
                "bootstrap_mode": "generative_selected",
                "logical_calls": 1,
                "provider_calls": 1,
                "action_executions": 0,
                "response_received": True,
                "transport": "direct_single_provider_call",
                "request_payload_sha256": "bootstrap-request",
                "provider_messages_sha256": "bootstrap-provider",
                "visible_catalog_count": 2,
                "visible_catalog_ids_sha256": "catalog-hash",
            },
            "state": {
                "version": 2,
                "graph_current": True,
                "bootstrap_status": "selected",
                "bootstrap_mode": "generative_selected",
                "field_authority": {
                    "primary_focus_id": "bootstrap_selected",
                    "phase": "deterministic_mutable",
                    "current_focus": "executor_observed",
                },
            },
            "metrics": {
                "context_compilations": 1,
                "preflight_projections": 0,
                "postflight_commits": 0,
            },
            "deliveries": [
                {
                    "delivery_id": "state-1",
                    "claim_ids": ["state-claim-1"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "a" * 64,
                    "provider_messages_sha256": "b" * 64,
                    "message_index": 1,
                    "chars": 30,
                    "claim_metadata": [
                        {
                            "claim_id": "state-claim-1",
                            "origin": "preexisting_repository",
                            "authority": "identity_only",
                            "novel_to_provider_view": True,
                            "known_to_model": False,
                            "materiality_reason": "new_unresolved_task_obligation",
                            "source_revision": "source-1",
                            "origin_revision": "source-1",
                            "relation_endpoint": "",
                            "declared_validation_id": "",
                        }
                    ],
                }
            ],
            "failures": [],
            "valid": True,
        },
        "task_semantic_substrate": {
            "schema": "gt.task_semantic_substrate.v1",
            "status": "abstained",
            "derivation": {"status": "abstained", "facts": []},
            "compilations": [
                {
                    "call": 1,
                    "candidate_count": 0,
                    "accounted_count": 0,
                    "selected_count": 0,
                    "accounting": [],
                }
            ],
            "deliveries": [],
        },
        "convergence_controller": {
            "schema": "gt.convergence_controller.v1",
            "preflights": [],
            "return_candidates": 0,
            "applied_returns": 0,
        },
        "product_mechanism_census": {
            "accounting_contract": "17_legacy_features_plus_1_persistent_state",
            "legacy_feature_count": 17,
            "product_mechanism_count": 18,
            "mechanism_ids": [
                *CENTRAL_FEATURE_IDS,
                "persistent_execution_state",
            ],
            "configured_mechanism_count": 18,
            "configured_mechanism_ids": [
                *CENTRAL_FEATURE_IDS,
                "persistent_execution_state",
            ],
            "naturally_fired_legacy_feature_count": 0,
            "naturally_fired_legacy_feature_ids": [],
            "persistent_execution_state": {
                "configured": True,
                "exercised": True,
                "repeated_deterministic_use": True,
                "lifecycle_use_count": 4,
            },
        },
        "features": {"effect_trace": [], "preflight_receipts": []},
        "contribution_compiler": {
            "calls": [
                {
                    "call": 1,
                    "candidate_count": 1,
                    "accounted_count": 1,
                    "payload_tokens": 20,
                    "token_budget": 1200,
                    "selected_surfaces": ["persistent_execution_state"],
                }
            ]
        },
        "model_call_contexts": [
            {
                "call": 1,
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
                "stock_provider_messages_sha256": "stock-1",
                "provider_view_changed": True,
                "provider_message_count": 2,
                "provider_changed_message_indices": [1],
                "context_fact_candidates": 0,
                "context_facts_accounted": 0,
                "dispatch_status": "response_received",
                "persistent_execution_state_delivered": True,
                "persistent_execution_state": {
                    "kind": "initial",
                    "provider_call": 1,
                    "state_version": 2,
                    "claim_ids": ["state-claim-1"],
                    "reason_codes": [],
                },
            }
        ],
    }


def _off() -> dict:
    receipt = _treatment()
    receipt["integration_mode"] = "off"
    receipt["component_configuration"]["persistent_execution_state"] = False
    receipt["persistent_execution_state"] = {
        "initialization": {"status": "disabled"},
        "initial_retrieval": {"calls": 0},
        "bootstrap": {"provider_calls": 0},
        "state": None,
        "metrics": {},
        "deliveries": [],
        "failures": [],
        "valid": True,
    }
    receipt["calls"] = 1
    receipt["executor_calls"] = 1
    receipt["bootstrap_calls"] = 0
    receipt["preemptive_retrieval"] = {"dense_backend": None, "deliveries": []}
    receipt["repository_intelligence"] = {
        "status": "not_applicable",
        "applicability": "not_applicable_no_supported_source",
        "denominator_excluded": True,
        "failures": [],
    }
    receipt["metrics"]["repository_intelligence_valid"] = 0
    receipt["metrics"]["persistent_state_bootstrap_calls"] = 0
    receipt["metrics"]["persistent_state_initial_retrieval_calls"] = 0
    receipt["metrics"]["bootstrap_api_calls"] = 0
    receipt["model_call_contexts"][0]["stock_provider_messages_sha256"] = "b" * 64
    receipt["model_call_contexts"][0]["provider_view_changed"] = False
    receipt["model_call_contexts"][0]["provider_changed_message_indices"] = []
    return receipt


def _relational_treatment() -> dict:
    receipt = _treatment()
    receipt["treatment_profile"] = "central_relational_v2"
    receipt["repository_evidence"] = {
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "source-1",
        "graph_revision": "1" * 64,
        "index": {
            "graph_revision": "1" * 64,
            "graph_db_sha256": "1" * 64,
            "graph_manifest_sha256": "2" * 64,
            "binary_sha256": "3" * 64,
            "schema_valid": True,
            "source_revision": "source-1",
            "source_files": 1,
            "indexable_files": 1,
            "parser_failures": 0,
        },
    }
    receipt["provider_route"] = {
        "model": "openai/fixture-model",
        "api_base": "https://provider.example.invalid",
        "api_host": "provider.example.invalid",
        "route_id": "fixture:native:provider.example.invalid",
        "credential_in_receipt": False,
        "executor_retry_policy": "provider_once_no_retry",
    }
    receipt["component_configuration"].update(
        {
            "step_limit": 100,
            "treatment_runtime_contract_sha256": "",
            "persistent_execution_state": True,
            "relational_context": True,
            "semantic_evidence": True,
            "dense_fallback_only": True,
            "gt_task_evidence_budget_tokens": 4096,
            "gt_task_critical_reserve_tokens": 512,
        }
    )
    contract = {
        "schema": "gt.treatment_runtime_arguments.v1",
        "treatment_id": "fixture-relational",
        "source_sha": "a" * 40,
        "profile_id": "central_relational_v2",
        "agent_kwargs": {
            "integration_mode": "active",
            "treatment_profile": "central_relational_v2",
            "enable_persistent_execution_state": True,
            "enable_preemptive_retrieval": True,
            "enable_relational_context": True,
            "enable_semantic_evidence": True,
            "dense_fallback_only": True,
            "step_limit": 100,
        },
    }
    contract["contract_sha256"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt["treatment_runtime_contract"] = contract
    receipt["component_configuration"]["effective_runtime_agent_kwargs"] = dict(
        contract["agent_kwargs"]
    )
    receipt["component_configuration"]["treatment_runtime_contract_sha256"] = contract[
        "contract_sha256"
    ]
    receipt["preemptive_retrieval"]["decisions"] = [
        {
            "status": "abstained",
            "opportunity_kind": "post_read_search",
            "reason_codes": ["no_supported_context"],
            "cache_hit": False,
            "channel_receipts": [
                {
                    "channel": "dense",
                    "candidate_count": 0,
                    "failed": False,
                    "available": False,
                    "reason": "sparse_supported_dense_skipped",
                    "latency_ms": 0.0,
                }
            ],
            "retrieval_status": {
                "schema": "gt.retrieval_status.v1",
                "expected_mode": "dense_fallback_only",
                "dense_channel_present": True,
                "dense_backend_available": False,
                "dense_query_attempted": False,
                "dense_candidate_count": 0,
                "dense_result_used": False,
                "fallback_used": False,
                "fallback_reason": "",
                "selected_evidence_count": 0,
            },
        }
    ]
    receipt["preemptive_retrieval"]["opportunity_accounting"] = {
        "schema": "gt.retrieval_opportunity_accounting.v1",
        "opportunities": 1,
    }
    receipt["metrics"].update(
        {
            "relational_context_opportunities": 1,
            "relational_context_deliveries": 1,
            "semantic_evidence_deliveries": 1,
            "repository_context_deliveries": 1,
        }
    )
    receipt["relational_context"] = {
        "schema": "gt.relational_context_runtime.v1",
        "enabled": True,
        "decisions": [
            {
                "call": 1,
                "status": "delivered",
                "opportunity_kind": "post_read_search",
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["process-1"],
                "reason_codes": ["certified_lower_bound"],
            }
        ],
        "deliveries": [
            {
                "delivery_id": "relational-context-1",
                "call": 1,
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["process-1"],
                "evidence_action": 0,
                "first_eligible_call": 1,
                "delivered_before_call": 1,
                "delivered_before_model_query": True,
                "not_predictive": True,
                "one_step_late": False,
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
                "message_index": 1,
                "chars": 40,
                "tokens": 12,
                "epistemic_status": "lower_bound",
                "processes": [
                    {
                        "process_id": "process-1",
                        "anchor": "src/a.py",
                        "rendered": "src/a.py --calls--> src/b.py",
                        "truncated": False,
                        "cycle_terminated": False,
                    }
                ],
            }
        ],
        "delivered_claim_ids": ["process-1"],
    }
    receipt["semantic_evidence"] = {
        "schema": "gt.semantic_evidence_runtime.v1",
        "enabled": True,
        "decisions": [
            {
                "call": 1,
                "status": "delivered",
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["semantic-1"],
                "reason_codes": [],
            }
        ],
        "deliveries": [
            {
                "delivery_id": "semantic-evidence-1",
                "call": 1,
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["semantic-1"],
                "evidence_action": 0,
                "first_eligible_call": 1,
                "delivered_before_call": 1,
                "delivered_before_model_query": True,
                "not_predictive": True,
                "one_step_late": False,
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
                "message_index": 1,
                "chars": 35,
                "tokens": 10,
                "items": [
                    {
                        "kind": "definition",
                        "path": "src/a.py",
                        "line": 1,
                        "claim_id": "semantic-1",
                        "source_revision": "source-1",
                    }
                ],
            }
        ],
        "delivered_claim_ids": ["semantic-1"],
    }
    receipt["repository_context"] = {
        "schema": "gt.repository_context_runtime.v1",
        "enabled": True,
        "decisions": [
            {
                "call": 1,
                "status": "delivered",
                "opportunity_kind": "post_read_search",
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["semantic-1", "process-1"],
                "reason_codes": [],
            }
        ],
        "deliveries": [
            {
                "delivery_id": "repository-context-1",
                "call": 1,
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "claim_ids": ["semantic-1", "process-1"],
                "evidence_action": 0,
                "first_eligible_call": 1,
                "delivered_before_call": 1,
                "delivered_before_model_query": True,
                "not_predictive": True,
                "one_step_late": False,
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
                "message_index": 1,
                "chars": 70,
                "tokens": 20,
                "claim_metadata": [
                    {
                        "claim_id": claim_id,
                        "origin": "preexisting_repository",
                        "authority": "certified_structural",
                        "materiality_reason": "decision_relevant_repository_context",
                        "source_revision": "source-1",
                    }
                    for claim_id in ("semantic-1", "process-1")
                ],
                "projection": {
                    "status": "deliver",
                    "source_revision": "source-1",
                    "graph_revision": "graph-1",
                    "claim_ids": ["semantic-1", "process-1"],
                    "semantic_evidence": {
                        "items": [{"claim_id": "semantic-1"}],
                    },
                    "execution_views": [{"view_id": "process-1"}],
                    "process_coverage": {
                        "profile_id": "gt.certified_process.v1",
                        "max_depth": 6,
                        "max_branching": 3,
                        "max_execution_views": 3,
                        "entries_considered": 1,
                        "paths_considered": 1,
                        "returned_views": 1,
                        "candidate_views": 1,
                        "branch_truncated": 0,
                        "depth_truncated": 0,
                        "cycle_terminated": 0,
                        "deduplicated_paths": 0,
                        "omitted_for_view_limit": 0,
                        "rejected_edges": 0,
                        "lower_bound": 1,
                    },
                    "impact_facts": [],
                },
            }
        ],
        "delivered_claim_ids": ["semantic-1", "process-1"],
    }
    # The strengthened profile composes semantic and relational evidence into
    # one provider surface. The legacy surfaces remain configured for receipt
    # compatibility but do not independently deliver duplicate claims.
    receipt["relational_context"]["deliveries"] = []
    receipt["relational_context"]["delivered_claim_ids"] = []
    receipt["semantic_evidence"]["deliveries"] = []
    receipt["semantic_evidence"]["delivered_claim_ids"] = []
    receipt["metrics"]["relational_context_deliveries"] = 0
    receipt["metrics"]["semantic_evidence_deliveries"] = 0
    receipt["contribution_compiler"]["calls"][0].update(
        {
            "candidate_count": 2,
            "accounted_count": 2,
            "payload_tokens": 20,
            "selected_ids": ["repository-context-contribution"],
            "selected_surfaces": [
                "repository_context",
            ],
            "accounting": [
                {
                    "contribution_id": "persistent-state-contribution",
                    "surface": "persistent_execution_state",
                    "disposition": "value_rejected",
                    "reason_codes": ["provider_value_rejected:state-claim-1"],
                    "chars": 30,
                },
                {
                    "contribution_id": "repository-context-contribution",
                    "surface": "repository_context",
                    "disposition": "selected",
                    "reason_codes": [],
                    "chars": 70,
                },
            ],
            "value_certificates": [
                {
                    "contribution_id": "repository-context-contribution",
                    "surface": "repository_context",
                    "claim_id": claim_id,
                    "value_class": "certified_predecision_gap",
                    "disposition": "predecision",
                    "authority": "certified_structural",
                    "source_revision": "source-1",
                    "graph_revision": "graph-1",
                    "anchors": ["src/a.py"],
                    "novelty_basis": "certified_nonlocal_relation_absent_from_provider_view",
                    "decision_point": "initial_repository_plan",
                    "replaces_operation": "repository_relationship_search",
                    "materiality_reason": "decision_relevant_repository_context",
                    "completeness": "exact",
                    "reason_codes": [],
                }
                for claim_id in ("semantic-1", "process-1")
            ],
        }
    )
    receipt["contribution_compiler"].update(
        {
            "schema": "gt.contribution_compiler.runtime.v2",
            "provider_value_contract": "gt.provider_value.v1",
        }
    )
    receipt["contribution_compiler"]["task_budget"] = {
        "token_budget": 4096,
        "critical_reserve_tokens": 512,
        "used_regular_tokens": 20,
        "used_critical_tokens": 0,
        "used_tokens": 20,
        "remaining_regular_tokens": 3564,
        "remaining_total_tokens": 4076,
        "exhausted": False,
    }
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False
    receipt["model_call_contexts"][0].update(
        {
            "relational_context": {
                "status": "deliver",
                "claim_ids": ["process-1"],
                "reason_codes": ["certified_lower_bound"],
            },
            "relational_context_delivered": False,
            "semantic_evidence": {
                "status": "deliver",
                "claim_ids": ["semantic-1"],
                "reason_codes": [],
            },
            "semantic_evidence_delivered": False,
            "repository_context": {
                "status": "deliver",
                "claim_ids": ["semantic-1", "process-1"],
                "reason_codes": [],
            },
            "repository_context_delivered": True,
        }
    )
    receipt["component_configuration"].update(
        replay_capture=True,
        persistent_state_selection_mode="deterministic_v1",
    )
    receipt["persistent_execution_state"]["bootstrap"].update(
        {
            "selection_mode": "deterministic_v1",
            "bootstrap_mode": "deterministic_selected",
            "logical_calls": 0,
            "provider_calls": 0,
            "response_received": False,
            "selection_input_sha256": "selection-input",
            "selection_event_count": 1,
            "selection_provider_calls": 0,
        }
    )
    receipt["persistent_execution_state"]["state"][
        "bootstrap_mode"
    ] = "deterministic_selected"
    receipt["calls"] = receipt["executor_calls"]
    receipt["bootstrap_calls"] = 0
    receipt["metrics"]["persistent_state_bootstrap_calls"] = 0
    receipt["metrics"]["bootstrap_api_calls"] = 0
    persistent_census = receipt["product_mechanism_census"][
        "persistent_execution_state"
    ]
    persistent_census.update(
        {
            "selection_mode": "deterministic_v1",
            "selection_event_count": 1,
            "selection_provider_calls": 0,
            "bootstrap_provider_calls": 0,
            "bootstrap_calls": 0,
        }
    )
    receipt["replay_bundle"] = {
        "enabled": True,
        "trajectory_replay_ready": True,
        "call_count": len(receipt["model_call_contexts"]),
        "path": "gt_replay",
    }
    receipt["semantic_source_revision"] = {
        "revision": "source-1",
        "complete": True,
        "source_paths": ["src/a.py"],
        "missing_digest_paths": [],
    }
    context = receipt["model_call_contexts"][0]
    context["context_compiler"] = {
        "assistant_messages_input_sha256": "e" * 64,
        "assistant_messages_output_sha256": "e" * 64,
        "assistant_messages_preserved_exactly": True,
    }
    compiler = receipt["contribution_compiler"]["calls"][0]
    barrier = evaluate_provider_barrier(
        call=1,
        request_payload_sha256=context["request_payload_sha256"],
        provider_messages_sha256=context["provider_messages_sha256"],
        source_snapshot_complete=True,
        runtime_contract_ready=True,
        task_semantic_ready=True,
        graph_applicable=True,
        graph_current=True,
        repository_intelligence_ready=True,
        retrieval_ready=True,
        persistent_state_ready=True,
        previous_actions_finalized=True,
        context_candidate_count=context["context_fact_candidates"],
        context_accounted_count=context["context_facts_accounted"],
        contribution_candidate_count=compiler["candidate_count"],
        contribution_accounted_count=compiler["accounted_count"],
        selected_contribution_ids=compiler["selected_ids"],
        provider_value_contribution_ids=[
            row["contribution_id"] for row in compiler["value_certificates"]
        ],
        replay_capture_enabled=True,
    )
    context["mechanical_completeness_barrier"] = barrier
    receipt["mechanical_completeness"] = {
        "schema": "gt.mechanical_completeness_runtime.v1",
        "provider_barriers": [barrier],
    }
    receipt["intervention_chain"] = {
        "schema": "gt.intervention_chain.v2",
        "hidden_reasoning_inferred": False,
        "path": "intervention_chain.json",
        "rows": 2,
        "canonical_delivery_rows": 2,
    }
    receipt["task_artifact_integrity"] = {
        "schema": "gt.task_artifact_integrity.v1",
        "status": "PASS",
        "failures": [],
        "summary": {"chain_rows": 2},
    }
    receipt["completion"] = {
        "plan": {
            "schema": "gt.completion_plan.v1",
            "status": "partial",
            "executable": False,
            "predicates": [],
            "obligation_ids": [],
            "uncovered_obligation_ids": [],
            "target_paths": [],
            "uncovered_obligation_texts": [],
        },
        "certificates": [],
        "latest_certificate": None,
        "auto_submit_attempts": 0,
        "auto_submit_count": 0,
    }
    receipt["observed_facts"] = {
        "enabled": True,
        "max_deliveries_per_task": 4,
        "fact_extractions": [],
        "fact_deliveries": [],
        "fact_decisions": [],
    }
    receipt["task_execution_certificate"] = build_task_certificate(
        receipt, label="fixture"
    )
    return receipt


def test_release_gate_rejects_missing_or_blocked_provider_barrier():
    receipt = _relational_treatment()
    receipt["mechanical_completeness"]["provider_barriers"][0]["status"] = "BLOCKED"
    receipt["mechanical_completeness"]["provider_barriers"][0]["failures"] = [
        "graph_not_current"
    ]

    check = _mechanical_completeness_runtime(receipt, "task")

    assert check.passed is False
    assert "task:provider_barrier_blocked:1" in check.failures


def test_release_gate_rejects_unaccounted_observed_execution_fact():
    receipt = _relational_treatment()
    receipt["observed_facts"]["fact_extractions"] = [
        {"fact_id": "observed-required", "kind": "elf_type"}
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert (
        "treatment-1:observed_fact_terminal_decision_missing:observed-required"
        in report.failures
    )


def test_release_gate_accepts_exact_observed_fact_terminal_decision():
    receipt = _relational_treatment()
    receipt["observed_facts"]["fact_extractions"] = [
        {"fact_id": "observed-required", "kind": "elf_type", "eligible_call": 2}
    ]
    receipt["observed_facts"]["fact_decisions"] = [
        {
            "fact_id": "observed-required",
            "kind": "elf_type",
            "call": 2,
            "eligible_call": 2,
            "disposition": "terminal_before_next_provider_request",
            "reason_codes": ["trajectory_ended"],
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is True


def test_release_gate_rejects_fact_id_only_self_authored_decision():
    receipt = _relational_treatment()
    receipt["observed_facts"]["fact_extractions"] = [
        {"fact_id": "observed-required", "kind": "elf_type", "eligible_call": 2}
    ]
    receipt["observed_facts"]["fact_decisions"] = [
        {"fact_id": "observed-required"}
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert any("decision_disposition_invalid" in failure for failure in report.failures)


def test_release_gate_requires_current_validation_after_material_change():
    receipt = _relational_treatment()
    receipt["metrics"]["workspace_change_actions"] = 1
    receipt["project_validation"] = {
        "discovered_checks": ["pytest -q"],
        "probes": [],
    }

    certificate = build_task_certificate(receipt, label="task")

    assert certificate["status"] == "BLOCKED"
    assert "task:terminal_validation_not_passed" in certificate["failures"]
    assert "task:terminal_validation_stale" in certificate["failures"]


def test_release_gate_accepts_current_failed_validation_as_observed_outcome():
    receipt = _relational_treatment()
    receipt["source_revision"] = "source-1"
    receipt["metrics"]["workspace_change_actions"] = 1
    receipt["project_validation"] = {
        "discovered_checks": ["pytest -q"],
        "probes": [],
    }
    receipt["persistent_execution_state"]["state"]["observed_validation"] = {
        "status": "fail",
        "command": "pytest -q",
        "source_revision": "source-1",
    }

    certificate = build_task_certificate(receipt, label="task")

    assert certificate["status"] == "PASS"
    assert not any(
        failure.startswith("task:terminal_validation_")
        for failure in certificate["failures"]
    )


def test_treatment_gate_rejects_unified_contribution_budget_expansion():
    receipt = _treatment()
    receipt["contribution_compiler"]["calls"][0]["payload_tokens"] = 1201

    report = audit_release([receipt], static_evidence=STATIC)

    assert report.passed is False
    assert "treatment-1:contribution_token_budget_exceeded:1" in report.failures


def test_release_gate_excludes_prepared_not_sent_contribution_from_task_usage():
    receipt = {
        "component_configuration": {
            "gt_request_token_budget": 1200,
            "gt_task_evidence_budget_tokens": 4096,
        },
        "model_call_contexts": [{}, {}],
        "contribution_compiler": {
            "calls": [
                {
                    "token_budget": 1200,
                    "task_budget_tokens": 25,
                    "task_budget_token_limit": 4096,
                    "payload_tokens": 25,
                    "candidate_count": 0,
                    "accounted_count": 0,
                    "dispatch_status": "dispatched",
                },
                {
                    "token_budget": 1200,
                    "task_budget_tokens": 25,
                    "task_budget_token_limit": 4071,
                    "payload_tokens": 25,
                    "candidate_count": 0,
                    "accounted_count": 0,
                    "dispatch_status": "prepared_not_sent",
                },
            ],
            "task_budget": {
                "token_budget": 4096,
                "critical_reserve_tokens": 512,
                "used_regular_tokens": 25,
                "used_critical_tokens": 0,
                "used_tokens": 25,
            },
        },
    }

    check = _contribution_budget(receipt, "task-1")

    assert check.passed is True


def test_treatment_gate_rejects_conflated_selected_and_executed_actions():
    receipt = _relational_treatment()
    receipt["action_accounting"].update(
        {"selected": 2, "processed": 1, "executed": 1, "cancelled": 0}
    )

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:selected_action_accounting_mismatch" in report.failures


def test_treatment_gate_rejects_unobserved_runtime_budget_drift():
    receipt = _relational_treatment()
    receipt["component_configuration"]["effective_runtime_agent_kwargs"][
        "gt_task_evidence_budget_tokens"
    ] = 2048
    receipt["treatment_runtime_contract"]["agent_kwargs"][
        "gt_task_evidence_budget_tokens"
    ] = 4096
    contract = dict(receipt["treatment_runtime_contract"])
    contract.pop("contract_sha256", None)
    receipt["treatment_runtime_contract"]["contract_sha256"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt["component_configuration"]["treatment_runtime_contract_sha256"] = receipt[
        "treatment_runtime_contract"
    ]["contract_sha256"]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert (
        "treatment-1:treatment_runtime_gt_task_evidence_budget_tokens_mismatch"
        in report.failures
    )


def test_release_gate_accepts_complete_evidence_contract():
    report = audit_release(
        [_relational_treatment()], static_evidence=STATIC, off_receipts=[_off()]
    )

    assert report.passed is True
    assert report.status == "READY"
    assert report.schema == "gt.release_gate.v1"
    assert report.summary["checks_passed"] == report.summary["checks_total"]


def test_release_gate_accepts_relational_profile_as_additive_persistent_capability():
    report = audit_release(
        [_relational_treatment()], static_evidence=STATIC, off_receipts=[_off()]
    )

    assert report.passed is True
    assert not any("persistent_" in failure for failure in report.failures)
    assert not any("dense_backend_receipt_missing" in failure for failure in report.failures)


def test_relational_release_gate_rejects_missing_provider_value_contract():
    receipt = _relational_treatment()
    receipt["contribution_compiler"].pop("provider_value_contract")

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:provider_value_contract_missing" in report.failures


def test_relational_release_gate_rejects_selected_uncertified_contribution():
    receipt = _relational_treatment()
    receipt["contribution_compiler"]["calls"][0]["value_certificates"] = []

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:provider_value_selected_uncertified:1" in report.failures


def test_relational_release_gate_rejects_ambiguous_provider_value():
    receipt = _relational_treatment()
    certificate = receipt["contribution_compiler"]["calls"][0][
        "value_certificates"
    ][0]
    certificate["completeness"] = "ambiguous"

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert (
        "treatment-1:provider_value_certificate_rejected:1:semantic-1"
        in report.failures
    )


def test_strengthened_release_rejects_legacy_profile_receipt() -> None:
    report = audit_release([_treatment()], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:required_treatment_profile_mismatch" in report.failures


def test_relational_runtime_gate_rejects_missing_treatment_contract() -> None:
    receipt = _relational_treatment()
    receipt.pop("treatment_runtime_contract")

    failures = [
        failure
        for check in audit_treatment_runtime(receipt, label="task")
        for failure in check.failures
    ]

    assert "task:treatment_runtime_contract_missing" in failures


def test_relational_profile_requires_dense_backend_when_fallback_was_attempted():
    receipt = _relational_treatment()
    receipt["preemptive_retrieval"]["dense_backend"] = None
    receipt["preemptive_retrieval"]["decisions"] = [
        {
            "opportunity_kind": "post_read_search",
            "reason_codes": [],
            "cache_hit": False,
            "channel_receipts": [
                {
                    "channel": "dense",
                    "failed": False,
                    "available": False,
                    "reason": "backend_unavailable",
                }
            ]
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:dense_backend_receipt_missing" in report.failures


def test_relational_profile_requires_provisioned_dense_backend_even_when_skipped():
    receipt = _relational_treatment()
    receipt["preemptive_retrieval"]["dense_backend"] = None

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:dense_backend_receipt_missing" in report.failures


def test_relational_profile_accepts_fully_accounted_correct_abstention():
    receipt = _relational_treatment()
    receipt["repository_context"]["decisions"][0].update(
        {
            "status": "abstain",
            "claim_ids": [],
            "reason_codes": ["no_certified_repository_context"],
        }
    )
    receipt["repository_context"]["deliveries"] = []
    receipt["repository_context"]["delivered_claim_ids"] = []
    receipt["metrics"]["repository_context_deliveries"] = 0
    receipt["contribution_compiler"]["calls"][0].update(
        {
            "candidate_count": 1,
            "accounted_count": 1,
            "payload_tokens": 20,
            "selected_surfaces": ["persistent_execution_state"],
        }
    )
    receipt["model_call_contexts"][0].update(
        {
            "provider_view_changed": False,
            "stock_provider_messages_sha256": "provider-1",
            "provider_changed_message_indices": [],
            "repository_context": {
                "status": "abstain",
                "claim_ids": [],
                "reason_codes": ["no_certified_repository_context"],
            },
            "repository_context_delivered": False,
        }
    )

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    check = next(
        row
        for row in report.checks
        if row.name == "repository_context_integrated_consequence"
    )
    assert check.passed is True
    assert "repository_context_no_integrated_delivery" not in report.failures
    assert check.details["deliveries"] == 0
    assert check.details["opportunities"] == 1
    assert check.details["correct_abstentions_allowed"] is True


def test_release_gate_rejects_legacy_17_only_product_accounting():
    receipt = _treatment()
    receipt["product_mechanism_census"]["product_mechanism_count"] = 17
    receipt["product_mechanism_census"]["mechanism_ids"] = receipt[
        "product_mechanism_census"
    ]["mechanism_ids"][:-1]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:product_mechanism_count_not_18" in report.failures
    assert "treatment-1:product_mechanism_identity_invalid" in report.failures


def test_release_gate_rejects_configured_but_unexercised_persistent_state():
    receipt = _treatment()
    receipt["product_mechanism_census"]["persistent_execution_state"][
        "exercised"
    ] = False

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_product_mechanism_not_exercised" in report.failures


def test_release_gate_rejects_one_time_persistent_state_initialization():
    receipt = _treatment()
    persistent = receipt["product_mechanism_census"]["persistent_execution_state"]
    persistent["repeated_deterministic_use"] = False
    persistent["lifecycle_use_count"] = 1

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_product_mechanism_not_repeated" in report.failures


def test_release_gate_accepts_content_hashed_runtime_dense_identity():
    receipt = _relational_treatment()
    receipt["preemptive_retrieval"]["dense_backend"] = {
        "available": True,
        "failed": False,
        "backend": "snowflake_onnx",
        "model_name": SNOWFLAKE_MODEL_NAME,
        "model_revision": SNOWFLAKE_MODEL_REVISION,
        "model_sha256": SNOWFLAKE_MODEL_SHA256,
        "tokenizer_sha256": SNOWFLAKE_TOKENIZER_SHA256,
        "pooling": "cls",
        "normalization": "l2",
        "max_length": SNOWFLAKE_MAX_LENGTH,
        "network_calls": 0,
        "provider_calls": 0,
    }

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is True


def test_release_gate_fails_closed_when_dense_asset_is_missing():
    receipt = _treatment()
    receipt["preemptive_retrieval"]["dense_backend"] = None

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:dense_backend_receipt_missing" in report.failures


def test_release_gate_fails_closed_when_outcome_preservation_is_disabled():
    receipt = _treatment()
    receipt["component_configuration"]["context_compaction"] = False
    receipt["component_configuration"]["completion_controller"] = False

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:context_compaction_disabled" in report.failures
    assert "treatment-1:completion_controller_disabled" in report.failures


def test_release_gate_rejects_soft_character_compaction_in_final_v2():
    receipt = _treatment()
    receipt["treatment_profile"] = "central_relational_v2"
    receipt["metrics"]["context_compaction_epochs"] = [
        {"epoch": 1, "trigger_kind": "character_pressure"}
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:soft_character_compaction_forbidden" in report.failures


def test_completion_integrity_rejects_stale_or_partial_auto_submit_proof():
    receipt = {
        "treatment_profile": "central_relational_v2",
        "completion": {
            "plan": {
                "schema": "gt.completion_plan.v1",
                "executable": False,
            },
            "certificates": [
                {
                    "schema": "gt.completion_certificate.v1",
                    "status": "complete",
                    "auto_submit_eligible": True,
                    "workspace_revision": "w2",
                    "observations": [
                        {
                            "schema": "gt.completion_predicate_observation.v1",
                            "workspace_revision": "w1",
                            "returncode": 0,
                            "output_sha256": "a" * 64,
                        }
                    ],
                    "missing_predicate_ids": [],
                    "failing_predicate_ids": [],
                    "stale_predicate_ids": [],
                }
            ],
            "auto_submit_attempts": 1,
            "auto_submit_count": 1,
        },
    }

    check = _completion_integrity(receipt, "task")

    assert check.passed is False
    assert "task:completion_eligible_proof_invalid:1" in check.failures
    assert "task:completion_partial_plan_submitted" in check.failures


def test_completion_integrity_rejects_unrelated_self_authored_predicate_proof():
    receipt = {
        "treatment_profile": "central_relational_v2",
        "completion": {
            "plan": {
                "schema": "gt.completion_plan.v1",
                "status": "complete",
                "executable": True,
                "predicates": [
                    {
                        "predicate_id": "p-real",
                        "kind": "command",
                        "command": "false",
                        "obligation_ids": ["o-real"],
                        "target_paths": [],
                        "dependency_paths": [],
                    }
                ],
                "obligation_ids": ["o-real"],
                "uncovered_obligation_ids": [],
                "target_paths": [],
                "uncovered_obligation_texts": [],
            },
            "certificates": [
                {
                    "schema": "gt.completion_certificate.v1",
                    "status": "complete",
                    "auto_submit_eligible": True,
                    "workspace_revision": "w1",
                    "action_id": 1,
                    "observations": [
                        {
                            "schema": "gt.completion_predicate_observation.v1",
                            "predicate_id": "unrelated",
                            "workspace_revision": "w1",
                            "returncode": 0,
                            "output_sha256": "not-a-hash",
                        }
                    ],
                    "missing_predicate_ids": [],
                    "failing_predicate_ids": [],
                    "stale_predicate_ids": [],
                    "reason_codes": ["all_executable_predicates_current_and_passing"],
                }
            ],
            "auto_submit_attempts": 1,
            "auto_submit_count": 1,
        },
    }

    check = _completion_integrity(receipt, "task")

    assert check.passed is False
    assert "task:completion_observation_set_mismatch:1" in check.failures
    assert "task:completion_observation_hash_invalid:1:unrelated" in check.failures


def test_dense_gate_rejects_valid_looking_wrong_model_identity():
    receipt = {
        "treatment_profile": "central_relational_v2",
        "repository_intelligence": {"required": True},
        "preemptive_retrieval": {
            "dense_backend": {
                "available": True,
                "failed": False,
                "backend": "snowflake_onnx",
                "model_name": "attacker/wrong-model",
                "model_sha256": "0" * 64,
                "tokenizer_sha256": "1" * 64,
                "pooling": "cls",
                "normalization": "l2",
                "max_length": 512,
                "network_calls": 0,
                "provider_calls": 0,
            },
            "dense_backend_error": "",
        },
    }

    check = _dense(receipt, "task")

    assert check.passed is False
    assert "task:dense_backend_model_name_mismatch" in check.failures
    assert "task:dense_backend_model_sha256_mismatch" in check.failures


def test_release_gate_requires_graph_independent_semantic_context():
    receipt = _treatment()
    receipt["component_configuration"]["task_semantic_substrate"] = False

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:task_semantic_substrate_disabled" in report.failures


def test_release_gate_rejects_repeated_or_diagnostic_free_project_probe():
    receipt = _treatment()
    receipt["project_validation"] = {
        "probes": [
            {"source_revision": "s1", "status": "fail", "diagnostic": ""},
            {"source_revision": "s1", "status": "pass", "diagnostic": ""},
        ]
    }
    receipt["metrics"]["project_validation_probe_attempts"] = 2

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:project_probe_failure_without_diagnostic:1" in report.failures
    assert "treatment-1:project_probe_repeated_revision:s1" in report.failures


def test_source_less_treatment_does_not_require_repository_or_dense_substrate():
    receipt = _relational_treatment()
    receipt["repository_intelligence"] = {
        "status": "not_applicable",
        "applicability": "not_applicable_no_supported_source",
        "denominator_excluded": True,
        "failures": [],
    }
    receipt["metrics"]["repository_intelligence_valid"] = 0
    receipt["preemptive_retrieval"]["dense_backend"] = None
    receipt["persistent_execution_state"] = {
        "activation": {
            "initial_applicability": "not_applicable_no_supported_source",
            "current_applicability": "not_applicable_no_supported_source",
            "ever_applicable": False,
            "activation_action": None,
            "activation_call": None,
            "correctly_abstained": True,
        },
        "initialization": {"status": "not_applicable"},
        "initial_retrieval": {"calls": 0},
        "bootstrap": {"provider_calls": 0},
        "state": None,
        "metrics": {},
        "deliveries": [],
        "failures": [],
        "valid": True,
    }
    receipt["calls"] = 1
    receipt["bootstrap_calls"] = 0
    receipt["metrics"]["persistent_state_bootstrap_calls"] = 0
    receipt["metrics"]["persistent_state_initial_retrieval_calls"] = 0
    receipt["metrics"]["bootstrap_api_calls"] = 0
    receipt["product_mechanism_census"]["persistent_execution_state"] = {
        "configured": True,
        "applicable": False,
        "exercised": False,
        "repeated_deterministic_use": False,
        "lifecycle_use_count": 0,
        "bootstrap_calls": 0,
        "correctly_abstained": True,
    }
    context = receipt["model_call_contexts"][0]
    compiler = receipt["contribution_compiler"]["calls"][0]
    barrier = evaluate_provider_barrier(
        call=1,
        request_payload_sha256=context["request_payload_sha256"],
        provider_messages_sha256=context["provider_messages_sha256"],
        source_snapshot_complete=True,
        runtime_contract_ready=True,
        task_semantic_ready=True,
        graph_applicable=False,
        graph_current=True,
        repository_intelligence_ready=True,
        retrieval_ready=True,
        persistent_state_ready=True,
        previous_actions_finalized=True,
        context_candidate_count=context["context_fact_candidates"],
        context_accounted_count=context["context_facts_accounted"],
        contribution_candidate_count=compiler["candidate_count"],
        contribution_accounted_count=compiler["accounted_count"],
        selected_contribution_ids=compiler["selected_ids"],
        provider_value_contribution_ids=[
            row["contribution_id"] for row in compiler["value_certificates"]
        ],
        replay_capture_enabled=True,
    )
    context["mechanical_completeness_barrier"] = barrier
    receipt["mechanical_completeness"]["provider_barriers"] = [barrier]
    receipt["task_execution_certificate"] = build_task_certificate(
        receipt, label="fixture"
    )

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is True


def test_release_gate_rejects_missing_persistent_activation_boundary():
    receipt = _treatment()
    receipt["persistent_execution_state"].pop("activation")

    persistent = next(
        check
        for check in audit_treatment_runtime(receipt, label="task")
        if check.name == "persistent_execution_state"
    )

    assert persistent.passed is False
    assert "task:persistent_activation_missing" in persistent.failures


def test_release_gate_counts_only_post_activation_persistent_lifecycle():
    receipt = _treatment()
    receipt["calls"] = 3
    receipt["executor_calls"] = 2
    receipt["actions"] = 2
    receipt["action_accounting"].update(
        {"selected": 2, "processed": 2, "executed": 2}
    )
    receipt["host_execution"]["decision_actions"] = 2
    receipt["persistent_execution_state"]["activation"] = {
        "initial_applicability": "not_applicable_no_supported_source",
        "current_applicability": "source_backed",
        "ever_applicable": True,
        "activation_action": 1,
        "activation_call": 2,
        "correctly_abstained": False,
    }
    receipt["persistent_execution_state"]["metrics"].update(
        {
            "context_compilations": 1,
            "preflight_projections": 1,
            "postflight_commits": 2,
        }
    )
    receipt["model_call_contexts"][0].update(
        {
            "call": 2,
            "persistent_execution_state": {
                "kind": "initial",
                "provider_call": 2,
                "state_version": 2,
                "claim_ids": ["state-claim-1"],
                "reason_codes": [],
            },
        }
    )
    receipt["persistent_execution_state"]["deliveries"][0].update(
        {
            "first_eligible_call": 2,
            "delivered_before_call": 2,
        }
    )
    persistent = next(
        check
        for check in audit_treatment_runtime(receipt, label="dynamic-task")
        if check.name == "persistent_execution_state"
    )

    assert persistent.passed is True


def test_release_gate_uses_lifecycle_counters_not_action_ordinals_after_activation():
    receipt = _treatment()
    receipt["calls"] = 3
    receipt["executor_calls"] = 2
    receipt["actions"] = 4
    receipt["action_accounting"] = {
        "selected": 5,
        "processed": 4,
        "executed": 3,
        "returned": 1,
        "cancelled": 1,
    }
    receipt["host_execution"]["decision_actions"] = 3
    receipt["persistent_execution_state"]["activation"] = {
        "initial_applicability": "not_applicable_no_supported_source",
        "current_applicability": "source_backed",
        "ever_applicable": True,
        "activation_action": 2,
        "activation_call": 2,
        "processed_actions_before_activation": 2,
        "executed_actions_at_activation": 1,
        "correctly_abstained": False,
    }
    receipt["persistent_execution_state"]["metrics"].update(
        {
            "context_compilations": 1,
            "preflight_projections": 2,
            "postflight_commits": 3,
        }
    )
    receipt["model_call_contexts"][0].update(
        {
            "call": 2,
            "persistent_execution_state": {
                "kind": "initial",
                "provider_call": 2,
                "state_version": 2,
                "claim_ids": ["state-claim-1"],
                "reason_codes": [],
            },
        }
    )
    receipt["persistent_execution_state"]["deliveries"][0].update(
        {"first_eligible_call": 2, "delivered_before_call": 2}
    )

    persistent = next(
        check
        for check in audit_treatment_runtime(receipt, label="dynamic-mixed-actions")
        if check.name == "persistent_execution_state"
    )

    assert persistent.passed is True


def test_release_gate_rejects_bootstrap_only_or_silently_missing_living_state():
    receipt = _treatment()
    receipt["persistent_execution_state"]["metrics"]["context_compilations"] = 0
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state"] = None
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_context_compilation_count" in report.failures
    assert "treatment-1:persistent_call_accounting_missing:1" in report.failures


def test_release_gate_rejects_retry_wrapped_bootstrap_transport():
    receipt = _treatment()
    receipt["persistent_execution_state"]["bootstrap"]["transport"] = (
        "model_query_single_call"
    )

    checks = audit_treatment_runtime(receipt, label="task")
    persistent = next(check for check in checks if check.name == "persistent_execution_state")

    assert persistent.passed is False
    assert "task:persistent_bootstrap_transport_not_single_call" in persistent.failures


def test_release_gate_rejects_any_provider_query_marker_failure():
    receipt = _treatment()
    receipt["persistent_execution_state"]["bootstrap"]["provider_query_marker_error"] = "OSError"
    receipt["metrics"]["provider_query_marker_error"] = "OSError"

    checks = audit_treatment_runtime(receipt, label="task")
    persistent = next(check for check in checks if check.name == "persistent_execution_state")

    assert persistent.passed is False
    assert "task:persistent_bootstrap_marker_failed" in persistent.failures
    assert "task:executor_provider_marker_failed" in persistent.failures


def test_release_gate_accepts_materiality_accounted_persistent_abstention():
    receipt = _relational_treatment()
    receipt["executor_calls"] = 2
    receipt["calls"] = 2
    receipt["replay_bundle"]["call_count"] = 2
    receipt["persistent_execution_state"]["metrics"]["context_compilations"] = 2
    receipt["model_call_contexts"].append(
        {
            "call": 2,
            "request_payload_sha256": "c" * 64,
            "provider_messages_sha256": "d" * 64,
            "stock_provider_messages_sha256": "d" * 64,
            "provider_view_changed": False,
            "provider_message_count": 2,
            "provider_changed_message_indices": [],
            "context_fact_candidates": 0,
            "context_facts_accounted": 0,
            "context_compiler": {
                "assistant_messages_input_sha256": "f" * 64,
                "assistant_messages_output_sha256": "f" * 64,
                "assistant_messages_preserved_exactly": True,
            },
            "dispatch_status": "response_received",
            "persistent_execution_state_delivered": False,
            "persistent_execution_state": {
                "kind": "none",
                "provider_call": 2,
                "state_version": 2,
                "claim_ids": [],
                "reason_codes": [
                    "state_change_already_represented_or_not_model_material"
                ],
            },
        }
    )
    receipt["contribution_compiler"]["calls"].append(
        {
            "call": 2,
            "candidate_count": 0,
            "accounted_count": 0,
            "payload_tokens": 0,
            "token_budget": 1200,
            "selected_surfaces": [],
        }
    )
    second_barrier = evaluate_provider_barrier(
        call=2,
        request_payload_sha256="c" * 64,
        provider_messages_sha256="d" * 64,
        source_snapshot_complete=True,
        runtime_contract_ready=True,
        task_semantic_ready=True,
        graph_applicable=True,
        graph_current=True,
        repository_intelligence_ready=True,
        retrieval_ready=True,
        persistent_state_ready=True,
        previous_actions_finalized=True,
        context_candidate_count=0,
        context_accounted_count=0,
        contribution_candidate_count=0,
        contribution_accounted_count=0,
        selected_contribution_ids=(),
        provider_value_contribution_ids=(),
        replay_capture_enabled=True,
    )
    receipt["model_call_contexts"][1]["mechanical_completeness_barrier"] = (
        second_barrier
    )
    receipt["mechanical_completeness"]["provider_barriers"].append(second_barrier)
    receipt["task_semantic_substrate"]["compilations"].append(
        {
            "call": 2,
            "candidate_count": 0,
            "accounted_count": 0,
            "selected_count": 0,
            "accounting": [],
        }
    )
    receipt["task_execution_certificate"] = build_task_certificate(
        receipt, label="fixture"
    )

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is True


def test_persistent_state_only_profile_gates_isolation_not_disabled_full_controls():
    receipt = _treatment()
    receipt["preflight_mode"] = "shadow"
    for name in (
        "context_compaction",
        "completion_controller",
        "progress_control",
        "adaptive_validation_timeout",
    ):
        receipt["component_configuration"][name] = False

    full = audit_treatment_runtime(receipt, label="task", profile="certified_full")
    diagnostic = audit_treatment_runtime(receipt, label="task", profile="persistent_state_only")

    outcome_check = next(check for check in full if check.name == "outcome_preservation_controls")
    assert outcome_check.passed is False
    assert next(
        check for check in diagnostic if check.name == "diagnostic_profile_isolation"
    ).passed is True


def test_release_gate_rejects_missing_or_unwired_initial_hybrid_retrieval():
    receipt = _treatment()
    receipt["persistent_execution_state"]["initial_retrieval"] = {
        "status": "disabled",
        "calls": 0,
    }
    receipt["metrics"]["persistent_state_initial_retrieval_calls"] = 0

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_initial_retrieval_call_count" in report.failures
    assert "treatment-1:persistent_initial_retrieval_incomplete" in report.failures
    assert "treatment-1:persistent_initial_retrieval_channels" in report.failures
    assert "treatment-1:persistent_initial_retrieval_metric_mismatch" in report.failures


def test_graph_substrate_is_not_relabelled_invalid_by_bootstrap_failure():
    receipt = _treatment()
    receipt["repository_evidence"] = {
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
    }
    receipt["repository_intelligence"]["status"] = "failed"
    receipt["repository_intelligence"]["failures"] = ["persistent_bootstrap_not_selected"]
    receipt["persistent_execution_state"]["bootstrap"]["status"] = "invalid_fallback"
    receipt["persistent_execution_state"]["bootstrap"][
        "bootstrap_mode"
    ] = "deterministic_fallback"
    receipt["persistent_execution_state"]["state"]["bootstrap_status"] = "invalid_fallback"
    receipt["persistent_execution_state"]["state"][
        "bootstrap_mode"
    ] = "deterministic_fallback"

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])
    substrate = next(check for check in report.checks if check.name == "repository_substrate")

    assert substrate.passed is True
    assert "treatment-1:persistent_bootstrap_not_generative" in report.failures


def test_release_gate_rejects_fallback_bootstrap_as_invalid_treatment():
    receipt = _treatment()
    receipt["persistent_execution_state"]["bootstrap"]["status"] = "invalid_fallback"
    receipt["persistent_execution_state"]["bootstrap"][
        "bootstrap_mode"
    ] = "deterministic_fallback"
    receipt["persistent_execution_state"]["state"]["bootstrap_status"] = "invalid_fallback"
    receipt["persistent_execution_state"]["state"][
        "bootstrap_mode"
    ] = "deterministic_fallback"

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_bootstrap_not_generative" in report.failures


def test_release_gate_rejects_hidden_extra_calls_with_generative_bootstrap():
    receipt = _treatment()
    receipt["calls"] = 3

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_provider_call_accounting_mismatch" in report.failures


def test_release_gate_rejects_generative_bootstrap_with_zero_material_deliveries():
    receipt = _treatment()
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False
    receipt["model_call_contexts"][0]["persistent_execution_state"] = {
        "kind": "none",
        "claim_ids": [],
        "provider_call": 1,
        "reason_codes": ["not_a_legal_pes_abstention"],
    }

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:persistent_no_material_delivery" in report.failures


def test_release_gate_allows_explicit_no_certified_related_file_abstention():
    receipt = _treatment()
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False
    receipt["model_call_contexts"][0]["persistent_execution_state"] = {
        "kind": "none",
        "claim_ids": [],
        "provider_call": 1,
        "reason_codes": ["no_certified_related_file"],
    }

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert "treatment-1:persistent_no_material_delivery" not in report.failures


def test_release_gate_allows_history_contains_evidence_empty_pes():
    receipt = _treatment()
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False
    receipt["model_call_contexts"][0]["persistent_execution_state"] = {
        "kind": "none",
        "claim_ids": [],
        "provider_call": 1,
        "reason_codes": ["provider_history_already_contains_evidence"],
    }

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert "treatment-1:persistent_no_material_delivery" not in report.failures
    assert "treatment-1:persistent_nonmaterial_abstention_invalid:1" not in report.failures


def test_release_gate_allows_not_model_material_empty_pes():
    receipt = _treatment()
    receipt["persistent_execution_state"]["deliveries"] = []
    receipt["model_call_contexts"][0]["persistent_execution_state_delivered"] = False
    receipt["model_call_contexts"][0]["persistent_execution_state"] = {
        "kind": "none",
        "claim_ids": [],
        "provider_call": 1,
        "reason_codes": ["no_material_certified_localization"],
    }

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert "treatment-1:persistent_no_material_delivery" not in report.failures

def test_release_gate_fails_closed_on_selected_pending_retrieval():
    receipt = _treatment()
    receipt["preemptive_retrieval"]["decisions"] = [{"status": "selected"}]
    receipt["metrics"]["preemptive_retrieval_selected_evidence"] = 1

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:preemptive_selected_not_delivered" in report.failures


def test_release_gate_rejects_retrieval_work_after_task_budget_closed():
    receipt = _treatment()
    receipt["preemptive_retrieval"]["decisions"] = [
        {
            "status": "abstained",
            "opportunity_kind": "post_read_search",
            "ranked_files": [{"path": "src/a.py"}],
            "selected_evidence": [{"path": "src/a.py"}],
            "reason_codes": ["task_character_budget"],
            "channel_receipts": [{"channel": "dense", "latency_ms": 1200}],
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:retrieval_work_after_budget_closed:1" in report.failures


def test_release_gate_fails_closed_on_uncertified_decision_return():
    receipt = _treatment()
    receipt["metrics"]["preflight_calls"] = 1
    receipt["features"]["preflight_receipts"] = [
        {
            "decision": {"disposition": "return_to_model"},
            "applied_disposition": "pass",
        }
    ]
    receipt["decision_sufficiency"]["decisions"] = [
        {
            "disposition": "return_eligible",
            "return_eligible": True,
            "selecting_request_hash": "request-1",
            "bundle": None,
            "applied_disposition": "pass",
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:decision_bundle_missing:1" in report.failures


def test_release_gate_rejects_generic_import_as_decision_sufficiency():
    receipt = _treatment()
    receipt["metrics"]["preflight_calls"] = 1
    receipt["features"]["preflight_receipts"] = [
        {"decision": {"disposition": "pass"}, "applied_disposition": "pass"}
    ]
    receipt["decision_sufficiency"]["decisions"] = [
        {
            "disposition": "return_eligible",
            "return_eligible": True,
            "selecting_request_hash": "request-1",
            "retrieval": {"provider_visible_claim_ids": []},
            "bundle": {
                "complete": True,
                "source_revision": "source-1",
                "graph_revision": "graph-1",
                "selecting_request_hash": "request-1",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "support_kind": "certified_structural",
                        "relation": "inverse:IMPORTS",
                        "provenance": [
                            "structural_certified",
                            "action_target:src/errors.ts",
                            "edge_endpoint_start:80",
                        ],
                    }
                ],
            },
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert "treatment-1:decision_relation_not_material:1" in report.failures


def test_release_gate_fails_closed_on_bad_delivery_hash_and_timing():
    receipt = _treatment()
    receipt["preemptive_retrieval"]["deliveries"] = [
        {
            "frame_id": "frame-1",
            "claim_ids": ["claim-1"],
            "evidence_action": 0,
            "first_eligible_call": 1,
            "delivered_before_call": 1,
            "delivered_before_model_query": True,
            "one_step_late": True,
            "predictive": True,
            "request_payload_sha256": "wrong-request",
            "provider_messages_sha256": "provider-1",
            "chars": 10,
        }
    ]

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert any("delivery_request_hash_context_mismatch" in item for item in report.failures)
    assert any("delivery_timing_invalid" in item for item in report.failures)


def test_release_gate_fails_closed_when_off_arm_changes_provider_view():
    off = _off()
    off["model_call_contexts"][0]["provider_view_changed"] = True

    report = audit_release([_treatment()], static_evidence=STATIC, off_receipts=[off])

    assert report.passed is False
    assert "off-1:provider_view_changed" in report.failures


def test_release_gate_fails_closed_when_static_evidence_is_missing():
    report = audit_release([_treatment()], static_evidence=None, off_receipts=[_off()])

    assert report.passed is False
    assert "missing_static_evidence" in report.failures


def test_replay_and_intervention_audit_requires_final_profile_artifacts():
    receipt = {
        "treatment_profile": "central_relational_v2",
        "component_configuration": {"replay_capture": True},
        "replay_bundle": {"enabled": False, "trajectory_replay_ready": False},
        "model_call_contexts": [],
    }

    check = _replay_and_intervention_audit(receipt, "task")

    assert check.passed is False
    assert "task:replay_capture_disabled" in check.failures
    assert "task:intervention_chain_missing" in check.failures


def test_replay_and_intervention_audit_cannot_be_disabled_for_final_profile():
    receipt = {
        "treatment_profile": "central_relational_v2",
        "component_configuration": {"replay_capture": False},
        "replay_bundle": {},
        "model_call_contexts": [],
    }

    check = _replay_and_intervention_audit(receipt, "task")

    assert check.passed is False
    assert "task:replay_capture_disabled" in check.failures


def test_relational_substrate_rejects_boolean_only_graph_claim() -> None:
    receipt = _relational_treatment()
    receipt["repository_evidence"]["index"].pop("graph_manifest_sha256")

    check = _substrate(receipt, "task")

    assert check.passed is False
    assert "task:repository_graph_manifest_identity_invalid" in check.failures


def test_runtime_barrier_is_recomputed_from_provider_and_compiler_receipts() -> None:
    receipt = _relational_treatment()
    receipt["contribution_compiler"]["calls"][0]["selected_ids"] = ["unproved"]

    check = _mechanical_completeness_runtime(receipt, "task")

    assert check.passed is False
    assert "task:provider_barrier_reconstruction_mismatch:1" in check.failures


def test_runtime_barrier_preserves_temporal_source_less_applicability() -> None:
    """A task may become source-backed after an early N/A provider call."""
    receipt = _relational_treatment()
    receipt["repository_intelligence"]["denominator_excluded"] = False
    context = receipt["model_call_contexts"][0]
    barrier = json.loads(json.dumps(context["mechanical_completeness_barrier"]))
    for requirement in barrier["requirements"]:
        if requirement["requirement_id"] in {
            "graph_current",
            "repository_intelligence",
            "retrieval",
            "persistent_state",
        }:
            requirement["status"] = "PROVEN_NOT_APPLICABLE"
            requirement["evidence"] = {
                "applicable": False,
                **({"current": True} if requirement["requirement_id"] == "graph_current" else {}),
            }
            if requirement["requirement_id"] != "graph_current":
                requirement["evidence"]["ready"] = True
    context["mechanical_completeness_barrier"] = barrier
    receipt["mechanical_completeness"]["provider_barriers"] = [barrier]

    check = _mechanical_completeness_runtime(receipt, "task")

    assert check.passed is True


def test_release_rejects_equal_length_assistant_history_mutation() -> None:
    receipt = _relational_treatment()
    receipt["model_call_contexts"][0]["context_compiler"].update(
        assistant_messages_output_sha256="f" * 64,
        assistant_messages_preserved_exactly=False,
    )

    report = audit_release([receipt], static_evidence=STATIC, off_receipts=[_off()])

    assert report.passed is False
    assert any("assistant_provider_history_not_exact:1" in item for item in report.failures)


def test_product_census_accepts_final_deterministic_selection_without_bootstrap():
    receipt = _relational_treatment()
    persistent = receipt["product_mechanism_census"]["persistent_execution_state"]
    persistent.update(
        {
            "selection_mode": "deterministic_v1",
            "selection_event_count": 1,
            "selection_provider_calls": 0,
            "bootstrap_provider_calls": 0,
            "bootstrap_calls": 0,
        }
    )

    check = next(
        item
        for item in audit_treatment_runtime(receipt, label="task")
        if item.name == "product_mechanism_census"
    )

    assert check.passed is True


def test_release_gate_report_is_json_serializable_and_machine_readable():
    report = audit_release(
        [_relational_treatment()], static_evidence=STATIC, off_receipts=[_off()]
    )
    payload = report.as_dict()

    assert payload["schema"] == "gt.release_gate.v1"
    assert payload["status"] == "READY"
    assert isinstance(payload["checks"], list)
    assert isinstance(payload["failures"], list)


def test_static_gate_accepts_machine_readable_outputs_from_existing_gates():
    static = {
        "census": {"status": "passed"},
        "central_readiness": {"status": "READY"},
        "pre_smoke_approved": {"status": "SMOKE_APPROVED"},
    }
    report = audit_release(
        [_relational_treatment()], static_evidence=static, off_receipts=[_off()]
    )

    assert report.passed is True
