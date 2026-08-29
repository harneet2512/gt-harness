from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.central_readiness_audit import (
    _first_position_after,
    _has_explicit_policy_arms,
    audit,
)


def test_readiness_audit_direct_script_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/central_readiness_audit.py"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.rstrip().endswith("READY")


def test_readiness_finds_direct_or_scripted_dispatch_after_compilation():
    source = "compile_context()\n_direct_provider_message,\nmodel.query,"

    assert _first_position_after(
        source,
        ("_direct_provider_message,", "model.query,"),
        start=source.index("compile_context()"),
    ) == source.index("_direct_provider_message,")
    assert _first_position_after(
        "compile_context()\nmodel.query,",
        ("_direct_provider_message,", "model.query,"),
    ) == len("compile_context()\n")


def test_policy_arm_audit_accepts_yaml_safe_quoted_off_choice():
    workflow = """
options: ["off", audit, certified_context, certified_controllers, certified_full]
default: audit
--ak integration_mode=off --ak policy_mode=off --ak preflight_mode=off
--ak integration_mode=audit --ak policy_mode=audit --ak preflight_mode=shadow
--ak policy_mode=certified_active
"""

    assert _has_explicit_policy_arms(workflow) is True


def test_readiness_rejects_an_incomplete_groundtruth_runtime_surface():
    result = audit()

    assert result["vendored_groundtruth_runtime_surface"] is True


def test_paid_central_workflow_installs_and_proves_repository_runtime():
    result = audit()

    assert result["paid_preflight_contract_is_explicit"] is True
    assert result["treatment_and_legacy_policy_contracts_are_explicit"] is True
    assert result["paid_central_installs_vendored_groundtruth"] is True
    assert result["paid_central_exports_index_binary"] is True
    assert result["paid_central_executes_index_fixture"] is True
    assert result["paid_central_executes_language_contract"] is True
    assert result["provider_free_gate_covers_pinned_benchmark_languages"] is True
    assert result["provider_free_gate_covers_trajectory_audit"] is True
    assert result["replay_capture_defaults_off_outside_final_profile"] is True
    assert result["paid_replay_capture_is_hardwired"] is True
    assert result["paid_replay_capture_required_for_final_profile"] is True
    assert result["provider_free_gate_covers_intervention_audit"] is True
    assert result["provider_free_gate_covers_benchmark_reports"] is True
    assert result["final_zero_provider_selection_contract_executes"] is True
    assert result["canonical_intervention_coverage_executes"] is True
    assert result["action_conditioned_graph_query_precedes_postflight"] is True
    assert result["paid_live_retrieval_matches_arb_profile"] is True
    assert result["provider_free_gate_uses_real_live_dense_asset"] is True
    assert result["provider_free_gate_covers_persistent_execution_state"] is True
    assert result["paid_persistent_state_contract_is_explicit"] is True
    assert result["paid_persistent_selection_is_graph_conditioned"] is True
    assert result["paid_retrieval_delivery_is_integrated"] is True
    assert result["persistent_state_is_graph_first_and_repeated"] is True
    assert result["provider_contributions_are_typed_and_accounted"] is True
    assert result["active_component_registry_is_complete"] is True
