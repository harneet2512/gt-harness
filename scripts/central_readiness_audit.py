#!/usr/bin/env python3
"""Provider-free structural gate for the host-owned central runtime.

This gate proves the isolation architecture and stock tool contract.  It does
not claim model efficacy or replace the live task-container surface audit.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Mini-SWE may print a first-run Unicode banner during import.  Windows CI can
# expose a legacy CP1252 stdout even though the repository itself is UTF-8.
# Make the provider-free audit deterministic before importing Mini-SWE.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import BaseInstalledAgent
from minisweagent.models.litellm_model import BASH_TOOL, LitellmModel

from eval.gt_central_agent import GTIntegrationMode, MiniSweCentralAgent
from gt_engine.central_runtime import (
    CENTRAL_FEATURE_IDS,
    CentralFeatureRuntime,
    ValidationClassification,
)
from gt_engine.component_registry import audit_component_registry
from gt_engine.host_execution import HostExecutionRecorder
from gt_engine.intervention_chain import build_intervention_chain
from gt_engine.preflight import PreflightMode
from gt_engine.treatment_adapter import treatment_from_descriptor
from scripts.central_feature_census import census as central_feature_census
from scripts.central_release_gate import (
    _product_mechanism_census,
    _provider_value_contract,
)
from scripts.release_manifest import load_release_manifest

ROOT = Path(__file__).resolve().parents[1]

_REQUIRED_GROUNDTRUTH_RUNTIME = (
    "groundtruth.runtime.terminal_evidence",
    "groundtruth.runtime.deterministic_queries",
    "groundtruth.runtime.miniswe_provider_boundary",
)


def _vendored_runtime_surface_available() -> bool:
    """Fail readiness when an older/incomplete groundtruth wheel is installed."""

    try:
        return all(
            importlib.util.find_spec(module_name) is not None
            for module_name in _REQUIRED_GROUNDTRUTH_RUNTIME
        )
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _has_explicit_policy_arms(workflow: str) -> bool:
    """Accept YAML-safe quoting without weakening the exact arm contract."""

    choices = (
        'options: ["off", audit, certified_context, certified_controllers, certified_full]',
        "options: [off, audit, certified_context, certified_controllers, certified_full]",
    )
    return (
        any(line in workflow for line in choices)
        and "default: audit" in workflow
        and "--ak integration_mode=off --ak policy_mode=off --ak preflight_mode=off" in workflow
        and "--ak integration_mode=audit --ak policy_mode=audit --ak preflight_mode=shadow"
        in workflow
        and "--ak policy_mode=certified_active" in workflow
    )


def _first_position_after(source: str, needles: tuple[str, ...], *, start: int = 0) -> int:
    """Return the first executable lifecycle boundary after ``start``.

    Production uses the one-shot direct provider transport while deterministic
    scripted tests retain Mini-SWE's public query adapter.  Readiness must
    recognize both branches without depending on the removed retry-wrapped
    call spelling.
    """

    positions = tuple(
        position for needle in needles if (position := source.find(needle, start)) >= 0
    )
    return min(positions, default=-1)


def audit() -> dict[str, bool]:
    source = inspect.getsource(MiniSweCentralAgent)
    # ``run`` is the failure-safe receipt wrapper.  Readiness invariants about
    # provider ordering and action postflight belong to the actual central
    # orchestration loop.
    run_source = inspect.getsource(MiniSweCentralAgent._run_central)
    setup_source = inspect.getsource(MiniSweCentralAgent.setup)
    validation_source = inspect.getsource(ValidationClassification)
    observation_source = inspect.getsource(CentralFeatureRuntime.observe_action)
    persistent_compile_position = run_source.find("persistent_state_engine.compile_context(")
    provider_dispatch_position = _first_position_after(
        run_source,
        ("_direct_provider_message,", "model.query,"),
        start=max(0, persistent_compile_position),
    )
    provider_request_receipt_position = _first_position_after(
        run_source,
        ("_provider_request_receipt(",),
        start=max(0, persistent_compile_position),
    )
    # The paid ten-task smoke dispatches the central matrix workflow.  Keep
    # the older engine workflow in the audit as a second release surface, but
    # never let a correctly configured sibling mask a stale dispatch target.
    workflow_paths = (
        ROOT / ".github/workflows/tb2_miniswe_central.yml",
        ROOT / ".github/workflows/tb2_miniswe_engine.yml",
    )
    workflows = tuple(path.read_text(encoding="utf-8") for path in workflow_paths)
    workflow = workflows[0]
    verification_workflow = workflows[1]
    release_manifest = load_release_manifest(root=ROOT)
    treatment_descriptor = json.loads(release_manifest.treatment_path.read_text(encoding="utf-8"))
    treatment_runtime = treatment_descriptor.get("runtime_agent_kwargs") or {}
    treatment_identity_descriptor = {
        key: value for key, value in treatment_descriptor.items() if key != "runtime_agent_kwargs"
    }
    treatment_identity_descriptor["source_sha"] = "0" * 40
    try:
        treatment_effective_runtime = treatment_from_descriptor(
            treatment_identity_descriptor
        ).agent_kwargs()
        treatment_effective_runtime.update(treatment_runtime)
    except (TypeError, ValueError):
        treatment_effective_runtime = {}
    central_uses_typed_treatment = (
        "scripts.render_treatment_agent_args" in workflow
        and "--release-manifest eval/release/active_release.json" in workflow
        and "tb2_central_relational_v2.json" not in workflow
        and "gt-treatment-runtime.json" in workflow
    )
    provider_free_workflow = (ROOT / ".github/workflows/central_provider_free.yml").read_text(
        encoding="utf-8"
    )
    deepswe_workflow = (ROOT / ".github/workflows/deepswe_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    deep_metrics_source = (ROOT / "gt_engine/deep_metrics.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        agent = MiniSweCentralAgent(logs_dir=Path(directory), model_name="audit-model")
        model = agent._build_model()
    feature_result = central_feature_census()
    component_result = audit_component_registry()
    deterministic_census_check = _product_mechanism_census(
        {
            "treatment_profile": "central_relational_v2",
            "executor_calls": 1,
            "product_mechanism_census": {
                "accounting_contract": "17_legacy_features_plus_1_persistent_state",
                "legacy_feature_count": 17,
                "product_mechanism_count": 18,
                "mechanism_ids": [*CENTRAL_FEATURE_IDS, "persistent_execution_state"],
                "configured_mechanism_count": 18,
                "configured_mechanism_ids": [
                    *CENTRAL_FEATURE_IDS,
                    "persistent_execution_state",
                ],
                "naturally_fired_legacy_feature_count": 0,
                "persistent_execution_state": {
                    "configured": True,
                    "applicable": True,
                    "correctly_abstained": False,
                    "exercised": True,
                    "repeated_deterministic_use": True,
                    "lifecycle_use_count": 2,
                    "selection_mode": "deterministic_v1",
                    "selection_event_count": 1,
                    "selection_provider_calls": 0,
                    "bootstrap_provider_calls": 0,
                    "bootstrap_calls": 0,
                },
            },
        },
        "readiness",
    )
    provider_value_check = _provider_value_contract(
        {
            "treatment_profile": "central_relational_v2",
            "contribution_compiler": {
                "schema": "gt.contribution_compiler.runtime.v2",
                "provider_value_contract": "gt.provider_value.v1",
                "calls": [
                    {
                        "call": 1,
                        "selected_ids": ["readiness-contribution"],
                        "accounting": [
                            {
                                "contribution_id": "readiness-contribution",
                                "surface": "repository_context",
                                "disposition": "selected",
                                "reason_codes": [],
                            }
                        ],
                        "value_certificates": [
                            {
                                "contribution_id": "readiness-contribution",
                                "surface": "repository_context",
                                "claim_id": "readiness-claim",
                                "value_class": "action_local_relation",
                                "disposition": "same_observation",
                                "completeness": "exact",
                                "authority": "certified_relation",
                                "source_revision": "readiness-source-revision",
                                "anchors": ["src/readiness.py"],
                                "novelty_basis": "nonlocal_relation_absent_from_observation",
                                "decision_point": "next_executor_request",
                                "replaces_operation": "repository_relationship_search",
                                "materiality_reason": "decision_relevant_repository_context",
                            }
                        ],
                    }
                ],
            },
        },
        "readiness",
    )
    intervention_probe = build_intervention_chain(
        {
            "model_call_contexts": [
                {
                    "call": 1,
                    "request_payload_sha256": "request",
                    "provider_messages_sha256": "messages",
                }
            ],
            "task_semantic_substrate": {
                "deliveries": [
                    {
                        "delivery_id": "probe",
                        "delivered_before_call": 1,
                        "claim_ids": ["probe-claim"],
                    }
                ]
            },
        }
    )
    return {
        "host_base_agent": issubclass(MiniSweCentralAgent, BaseAgent),
        "not_installed_agent": not issubclass(MiniSweCentralAgent, BaseInstalledAgent),
        "setup_has_no_exec": ".exec(" not in setup_source,
        "setup_has_no_upload": "upload_" not in setup_source,
        "stock_litellm_model": type(model) is LitellmModel,
        "stock_bash_tool_only": BASH_TOOL["function"]["name"] == "bash",
        "vendored_groundtruth_runtime_surface": _vendored_runtime_surface_available(),
        "paid_central_installs_vendored_groundtruth": (
            workflow.count("vendor/groundtruth_mcp-*.whl") >= 2
            and workflow.count("chmod +x vendor/gt-index-linux-amd64") >= 2
        ),
        "paid_central_exports_index_binary": (
            workflow.count("GT_INDEX_BINARY:") >= 2
            and workflow.count("vendor/gt-index-linux-amd64") >= 4
        ),
        "paid_central_executes_index_fixture": (
            workflow.count("python scripts/verify_gt_index_runtime.py") >= 2
        ),
        "paid_central_executes_language_contract": (
            workflow.count("python scripts/verify_tb2_language_contract.py") >= 2
        ),
        "preflight_default_is_off": agent.preflight_mode is PreflightMode.OFF,
        # TB2 is now a single treatment-only dispatch and deliberately uses
        # ASSISTIVE_SAFE. The legacy engine workflow remains the shadow-only
        # diagnostic surface; DeepSWE is also audited independently below.
        "paid_preflight_contract_is_explicit": (
            central_uses_typed_treatment
            and treatment_effective_runtime.get("preflight_mode") == "assistive_safe"
            and "--ak preflight_mode=shadow" in verification_workflow
            and all("--ak enable_preflight=true" not in item for item in workflows)
        ),
        "treatment_and_legacy_policy_contracts_are_explicit": (
            (
                (
                    "options: [repair20-v1]" in workflow
                    or "options: [repair20-v1, regression-smoke-v1]" in workflow
                )
                and "inputs.arm" not in workflow
                and central_uses_typed_treatment
                and treatment_effective_runtime.get("integration_mode") == "active"
                and treatment_effective_runtime.get("policy_mode") == "certified_active"
            )
            and _has_explicit_policy_arms(verification_workflow)
        ),
        "one_switch_off_is_provider_neutral": (
            GTIntegrationMode.OFF.value == "off"
            and "transform=False" in run_source
            and "self.integration_mode is GTIntegrationMode.OFF" in source
        ),
        "provider_free_gate_covers_preflight": (
            "tests/test_gt_preflight.py" in provider_free_workflow
        ),
        "provider_free_gate_covers_trajectory_audit": (
            "scripts/central_trajectory_audit.py" in provider_free_workflow
            and "tests/test_central_trajectory_audit.py" in provider_free_workflow
        ),
        "replay_capture_defaults_off_outside_final_profile": (
            "enable_replay_capture: bool = False" in source
            and "tests/test_replay_bundle.py" in provider_free_workflow
        ),
        "paid_replay_capture_is_hardwired": (
            "replay_capture:" not in workflow
            and "REPLAY_CAPTURE:" not in workflow
            and '--ak enable_replay_capture="true"' in workflow
        ),
        "paid_replay_capture_required_for_final_profile": (
            treatment_runtime.get("enable_replay_capture") is True
        ),
        "provider_free_gate_covers_intervention_audit": (
            "tests/test_intervention_chain.py" in provider_free_workflow
            and "gt_engine/intervention_chain.py" in provider_free_workflow
        ),
        "provider_free_gate_covers_benchmark_reports": (
            "tests/test_benchmark_reports.py" in provider_free_workflow
            and "gt_engine/benchmark_reports.py" in provider_free_workflow
            and "tests/test_verify_frozen_outcome_prediction.py" in provider_free_workflow
        ),
        "final_zero_provider_selection_contract_executes": (deterministic_census_check.passed),
        "canonical_intervention_coverage_executes": (
            intervention_probe.get("schema") == "gt.intervention_chain.v2"
            and int((intervention_probe.get("counts") or {}).get("rows") or 0) == 1
            and int((intervention_probe.get("counts") or {}).get("canonical_delivery_rows") or 0)
            == 1
        ),
        "provider_free_gate_covers_context_compiler": (
            "tests/test_provider_view.py" in provider_free_workflow
            and "tests/test_gt_deep_metrics.py" in provider_free_workflow
            and "tests/test_gt_uplift_policy.py" in provider_free_workflow
            and "tests/test_gt_on_experiment.py" in provider_free_workflow
        ),
        "provider_baseline_shield_is_exact_until_measured_pressure": (
            "stock_provider_messages_sha256" in run_source
            and "provider_changed_message_indices" in run_source
            and "provider_compaction_required(" in run_source
            and "feature_guidance_chars" in run_source
            and "certified_graph_chars" in run_source
        ),
        "common_certification_boundary_is_applied": (
            "certify_opportunity(" in run_source
            and "completion_opportunity.certified" in run_source
            and "certification_decisions" in run_source
            and (ROOT / "gt_engine/uplift_policy.py").is_file()
        ),
        "repeated_control_release_gate_is_available": (
            "assess_repeated_release"
            in (ROOT / "gt_engine/experiment.py").read_text(encoding="utf-8")
            and "crossover_arm" in (ROOT / "gt_engine/experiment.py").read_text(encoding="utf-8")
        ),
        "provider_free_gate_covers_repository_intelligence": (
            "tests/test_gt_intelligence_layer.py" in provider_free_workflow
            and "tests/test_gt_repository_intelligence.py" in provider_free_workflow
            and "tests/test_gt_repository_mirror.py" in provider_free_workflow
            and "gt_engine/context_frontier.py" in provider_free_workflow
            and "gt_engine/language_registry.py" in provider_free_workflow
            and "gt_engine/repository_mirror.py" in provider_free_workflow
        ),
        "provider_free_gate_covers_preemptive_hybrid_retrieval": (
            "tests/test_preemptive_retrieval_frame.py" in provider_free_workflow
            and "tests/test_hybrid_retrieval.py" in provider_free_workflow
            and "tests/test_hybrid_repository.py" in provider_free_workflow
            and "tests/test_graph_retrieval_repairs.py" in provider_free_workflow
            and "tests/test_snowflake_onnx_backend.py" in provider_free_workflow
            and "gt_engine/preemptive_retrieval.py" in provider_free_workflow
            and "gt_engine/hybrid_retrieval.py" in provider_free_workflow
            and "gt_engine/hybrid_repository.py" in provider_free_workflow
            and "gt_engine/snowflake_onnx.py" in provider_free_workflow
        ),
        "provider_free_gate_covers_persistent_execution_state": (
            "tests/test_persistent_execution_state.py" in provider_free_workflow
            and "gt_engine/persistent_execution_state.py" in provider_free_workflow
            and "gt_engine/thin_compiler.py" in provider_free_workflow
            and "test_persistent_state_bootstraps_once_then_runs_at_every_live_boundary"
            in (ROOT / "tests/test_gt_central_agent.py").read_text(encoding="utf-8")
            and "_persistent_execution_state"
            in (ROOT / "scripts/central_release_gate.py").read_text(encoding="utf-8")
        ),
        "paid_persistent_state_contract_is_explicit": (
            central_uses_typed_treatment
            and treatment_descriptor.get("profile_id") == "central_relational_v2"
            and treatment_runtime.get("persistent_state_bootstrap_timeout_sec") == 45
            and treatment_runtime.get("persistent_state_bootstrap_input_tokens") == 2000
            and treatment_runtime.get("persistent_state_bootstrap_output_tokens") == 512
            and treatment_runtime.get("persistent_state_context_tokens") == 512
            and all(
                marker in verification_workflow
                for marker in (
                    "--ak enable_persistent_execution_state=true",
                    "--ak persistent_state_bootstrap_timeout_sec=45",
                    "--ak persistent_state_bootstrap_input_tokens=2000",
                    "--ak persistent_state_bootstrap_output_tokens=512",
                    "--ak persistent_state_context_tokens=512",
                )
            )
            and deepswe_workflow.count("--ak enable_persistent_execution_state=true") >= 2
            and deepswe_workflow.count("--ak persistent_state_bootstrap_timeout_sec=45") >= 2
            and "comparison_profile:" in deepswe_workflow
            and "diagnostic_only:" in deepswe_workflow
        ),
        "persistent_state_is_graph_first_and_repeated": (
            0
            <= run_source.find("initial_retrieval_state = RetrievalState(")
            < run_source.find("build_query_hybrid_repository,")
            < run_source.find("preemptive_retriever = HybridRetriever(")
            < run_source.find("preemptive_retriever.retrieve,")
            < run_source.find("build_bootstrap_catalog(")
            < run_source.find("await self._run_persistent_state_bootstrap(")
            < persistent_compile_position
            < provider_dispatch_position
            and run_source.find("persistent_state_engine.project_preflight(")
            < run_source.find("self._host_executions.exec(")
            and run_source.find("persistent_state_engine.commit_postflight(")
            > run_source.find("self._host_executions.exec(")
            and "persistent_state_engine.rebase_graph(" in run_source
            and "initial_retrieval=initial_retrieval_result" in run_source
            and "preemptive_retrieval_cache[initial_retrieval_cache_key]" in run_source
            and "persistent_initial_retrieval_not_in_catalog"
            in (ROOT / "scripts/central_release_gate.py").read_text(encoding="utf-8")
        ),
        "product_mechanism_contract_is_17_plus_1": (
            "PRODUCT_MECHANISM_IDS" in run_source
            and '"product_mechanism_census": product_mechanism_census' in run_source
            and '"legacy_feature_count": len(CENTRAL_FEATURE_IDS)' in run_source
            and '"product_mechanism_count": len(PRODUCT_MECHANISM_IDS)' in run_source
            # The workflow deliberately routes all integrity failures through
            # one merge-gate accumulator.  Do not pin this readiness check to
            # an obsolete direct SystemExit spelling: verify the live gate
            # records the 17+1 failure and fails outside diagnostic mode.
            and 'gate_errors.append("17+1 GT product mechanism census failed")' in deepswe_workflow
            and 'if os.environ.get("DIAGNOSTIC_ONLY") != "1":' in deepswe_workflow
        ),
        "paid_live_retrieval_matches_arb_profile": all(
            marker in verification_workflow
            for marker in (
                "--ak enable_preemptive_retrieval=true",
                "preemptive_retrieval_model_dir=",
                "python -m pip install -e '.[retrieval]'",
                "Provision pinned Snowflake ONNX runtime asset",
            )
        )
        and central_uses_typed_treatment
        and treatment_descriptor.get("preemptive_retrieval") is True
        and "preemptive_retrieval_model_dir=" in workflow
        and "python -m pip install -e '.[retrieval]'" in workflow
        and "Provision pinned Snowflake ONNX runtime asset" in workflow
        and "FINAL_RETRIEVAL_PROFILE" in source
        and "preemptive_retrieval_dense_candidate_limit" in run_source,
        "provider_free_gate_uses_real_live_dense_asset": (
            "GT_TEST_SNOWFLAKE_MODEL_DIR" in provider_free_workflow
            and "tests/test_live_retrieval_profile.py" in provider_free_workflow
            and "test_live_snowflake_retrieval_is_cold_once_then_steady_state"
            in (ROOT / "tests/test_gt_central_agent.py").read_text(encoding="utf-8")
        ),
        "provider_contributions_are_typed_and_accounted": (
            "compile_contributions(" in run_source
            and "contribution_compilations" in run_source
            and "tests/test_gt_contributions.py" in provider_free_workflow
            and "gt_engine/contributions.py" in provider_free_workflow
        ),
        "active_component_registry_is_complete": bool(component_result["ready"])
        and int(component_result["feature_count"]) == 17
        and "tests/test_gt_component_registry.py" in provider_free_workflow,
        "provider_free_gate_covers_pinned_benchmark_languages": (
            "tests/test_gt_language_resolution.py" in provider_free_workflow
            and "tests/test_gt_benchmark_language_contract.py" in provider_free_workflow
            and "scripts/verify_tb2_language_contract.py --dataset-root" in provider_free_workflow
            and "2fd12b88aafdd04a52c298e3940bcb189f9766d6" in provider_free_workflow
        ),
        "paid_context_frontier_is_explicit": (
            treatment_runtime.get("enable_context_frontier") is True
            and "--ak enable_context_frontier=true" in verification_workflow
        ),
        "provider_information_value_contract_executes": provider_value_check.passed,
        "paid_persistent_selection_is_deterministic": (
            central_uses_typed_treatment
            and treatment_runtime.get("persistent_state_selection_mode") == "deterministic_v1"
            and 'persistent_state_selection_mode: str = "generative"' in source
            and '"deterministic_v1"' in source
        ),
        "paid_retrieval_delivery_is_integrated": (
            central_uses_typed_treatment
            and treatment_runtime.get("retrieval_delivery_mode") == "integrated_same_observation"
            and 'retrieval_delivery_mode: str = "standalone_preemptive"' in source
            and '"integrated_same_observation"' in source
        ),
        "paid_graph_gate_is_explicit": (
            treatment_runtime.get("require_graph_ready") is True
            and "--ak require_graph_ready=true" in verification_workflow
        )
        and "graph_gate_failures" in run_source
        and "graph_degraded_fallback" in run_source
        and "graph_gate_blocked = False" in run_source,
        "paid_deterministic_compaction_enabled": (
            treatment_runtime.get("enable_context_compaction") is True
            and "--ak enable_context_compaction=true" in verification_workflow
        )
        and "tests/test_provider_view.py" in provider_free_workflow,
        "paid_adaptive_validation_timeout_is_explicit": (
            treatment_runtime.get("enable_adaptive_validation_timeout") is True
            and "--ak enable_adaptive_validation_timeout=true" in verification_workflow
        ),
        "provider_free_gate_covers_execution_accounting": (
            "tests/test_gt_host_execution.py" in provider_free_workflow
            and "gt_engine/host_execution.py" in provider_free_workflow
            and inspect.isclass(HostExecutionRecorder)
        ),
        "context_compiler_precedes_model_query": (
            0 <= run_source.find("record_context_compiler_call(") < provider_dispatch_position
        ),
        "provider_prepared_hash_precedes_model_query": (
            persistent_compile_position
            < provider_request_receipt_position
            < provider_dispatch_position
        ),
        "repository_frontier_precedes_model_query": (
            0 <= run_source.find("compile_incremental_frontier(") < provider_dispatch_position
        ),
        "preemptive_hybrid_retrieval_precedes_model_query": (
            0 <= run_source.find("preemptive_retriever.retrieve,") < provider_dispatch_position
            and "ProviderEvidenceSurface.PREEMPTIVE_RETRIEVAL" in run_source
            and "preemptive_retrieval_deliveries" in run_source
        ),
        "action_conditioned_graph_query_precedes_postflight": (
            0
            <= run_source.find("repository_service.prepare(")
            < run_source.find("self._features.observe_action(")
            and "active_paths=action_graph_paths" in run_source
            and "boundary=decision_boundary" in run_source
        ),
        "repository_intelligence_failure_is_receipted": (
            "material_frontier_not_delivered" in run_source
            and "context_frontier_coverage" in run_source
            and "repository_intelligence_valid" in run_source
            and "frontier_fact_accounting_incomplete" in run_source
        ),
        "repository_mirror_is_source_only_and_bounded": (
            "plan_source_mirror" in source
            and "SourceMirrorIncomplete" in source
            and "source_only_archive" in source
            and "REPOSITORY_TRANSFER" in source
        ),
        "repository_intelligence_failure_blocks_outcome_gate": (
            "invalid_treatments" in deep_metrics_source
            and "repository_intelligence_required" in deep_metrics_source
            and "context_frontier_chars_added" in deep_metrics_source
            and "INVALID GT TREATMENT" in workflow
        ),
        "validation_status_is_attributed_not_outer_rc_only": (
            "status_attributed=True" in validation_source
            and "later_shell_segment_owns_action_status" in validation_source
            and "classification.status is ValidationStatus.PASS" in observation_source
            and "classification.status is ValidationStatus.FAIL" in observation_source
        ),
        "validation_authority_is_explicit": (
            "authority: ValidationAuthority" in validation_source
            and "CUSTOM_PROBE"
            in inspect.getsource(sys.modules[ValidationClassification.__module__])
        ),
        "typed_proposal_precedes_environment_exec": (
            0
            <= run_source.find("adapt_proposed_action(")
            < run_source.find("self._host_executions.exec(")
        ),
        "workflow_deep_metrics_receive_harbor_result": (
            "harbor_result=got[0] if got else None" in workflow
        ),
        "task_exec_env_is_empty": "env={}," in source,
        "treatment_workflow_central": (
            'AGENT="eval.gt_central_agent:MiniSweCentralAgent"' in workflow
        ),
        "all_component_arms_use_one_central_engine": (
            'AGENT="eval.gt_central_agent:MiniSweCentralAgent"' in workflow
            and "MiniSweCentralShadowAgent" not in workflow
        ),
        "custom_agent_uses_import_path": '--agent-import-path "$AGENT"' in workflow,
        "frozen_miniswe_version": '"mini-swe-agent==2.4.6"' in workflow,
        "legacy_agent_not_in_paid_workflow": (
            "eval.miniswe_agent:MiniSweEngineAgent" not in workflow
        ),
        "paid_exact_harbor_deadline_is_propagated": (
            treatment_runtime.get("enable_lint") is True
            and treatment_runtime.get("enable_submit_readiness") is True
            and all(
                (
                    "scripts/resolve_harbor_budget.py" in item
                    or "from scripts.resolve_harbor_budget import resolve_budget" in item
                )
                and '--ak execution_budget_sec="$EXECUTION_BUDGET"' in item
                and "--agent-timeout-multiplier 1.0" in item
                and "--ak model_timeout_sec" not in item
                and "--ak model_loop_timeout_sec" not in item
                for item in workflows
            )
            and "--ak enable_lint=true" in verification_workflow
            and "--ak enable_submit_readiness=true" in verification_workflow
        ),
        "paid_completion_and_progress_control_enabled": (
            treatment_runtime.get("enable_completion_controller") is True
            and treatment_runtime.get("enable_progress_control") is True
            and "--ak enable_completion_controller=true" in verification_workflow
            and "--ak enable_progress_control=true" in verification_workflow
            and "tests/test_gt_completion.py" in verification_workflow
            and "tests/test_gt_progress.py" in verification_workflow
            and "tests/test_harbor_budget.py" in verification_workflow
        ),
        "provider_budget_and_reasoning_preservation_gated": (
            "test_provider_request_budget_fails_closed_before_provider_overflow"
            in (ROOT / "scripts/central_pre_smoke_gate.py").read_text(encoding="utf-8")
            and "test_over_budget_next_request_does_not_confirm_pending_guidance"
            in (ROOT / "scripts/central_pre_smoke_gate.py").read_text(encoding="utf-8")
            and "test_compaction_never_removes_distinct_assistant_reasoning"
            in (ROOT / "scripts/central_pre_smoke_gate.py").read_text(encoding="utf-8")
            and "test_provider_view_session_reuses_an_immutable_compacted_prefix"
            in (ROOT / "scripts/central_pre_smoke_gate.py").read_text(encoding="utf-8")
            and "tests/test_gt_progress.py" in verification_workflow
        ),
        "central_features_consumer_paths_proven": bool(
            feature_result["all_17_consumer_paths_proven"]
        ),
        "all_effects_context_accounted": bool(feature_result["all_effects_context_accounted"]),
        "repository_substrate_and_frontier_proven": bool(
            feature_result["repository_substrate_proven"]
            and feature_result["context_frontier_proven"]
        ),
    }


def main() -> int:
    results = audit()
    print(json.dumps(results, indent=2, sort_keys=True))
    if results.get("product_mechanism_contract_is_17_plus_1"):
        print("PRODUCT_MECHANISM_COUNT=18")
        print("ALL_18_PRODUCT_MECHANISMS_PROVEN")
    ready = all(results.values())
    print("READY" if ready else "NOT READY")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
