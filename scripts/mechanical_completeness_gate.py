#!/usr/bin/env python3
"""Authoritative no-spend configuration gate for the final GT treatment."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.deep_metrics import TrialOutcome, classify_trial_outcome  # noqa: E402
from scripts.central_integrity_audit import _observed_fact_accounting  # noqa: E402
from scripts.documentation_consistency_audit import (  # noqa: E402
    DEFAULT_DOCUMENTS,
    audit_documentation,
)
from scripts.release_manifest import (  # noqa: E402
    ACTIVE_RELEASE_PATH,
    load_release_manifest,
)
from scripts.render_treatment_agent_args import build_runtime_arguments  # noqa: E402
from scripts.verify_frozen_outcome_prediction import (  # noqa: E402
    verify_release_manifest,
)

_REQUIRED_RUNTIME = {
    "integration_mode": "active",
    "policy_mode": "certified_active",
    "preflight_mode": "assistive_safe",
    "require_graph_ready": True,
    "enable_all_features": True,
    "enable_repository_intelligence": True,
    "enable_persistent_execution_state": True,
    "enable_preemptive_retrieval": True,
    "enable_relational_context": True,
    "enable_semantic_evidence": True,
    "enable_decision_sufficiency": True,
    "enable_replay_capture": True,
    "retrieval_delivery_mode": "integrated_same_observation",
    "persistent_state_selection_mode": "deterministic_v1",
}

_REQUIRED_TASK_CHECKS = (
    "treatment_runtime_identity",
    "provider_route_integrity",
    "repository_substrate",
    "dense_backend",
    "delivery_timing_accounting",
    "observed_execution_fact_accounting",
    "contribution_budget",
    "provider_value_contract",
    "action_lifecycle",
    "deterministic_task_controls",
    "preflight_precision",
    "decision_sufficiency",
    "persistent_execution_state",
    "repository_context_state",
    "product_mechanism_census",
    "outcome_preservation_controls",
    "project_validation",
    "terminal_validation_state",
    "retrieval_efficiency",
    "replay_and_intervention_audit",
    "task_artifact_integrity",
    "mechanical_completeness_runtime",
)

_REQUIRED_DOCS = tuple(path.as_posix() for path in DEFAULT_DOCUMENTS)


def _graph_refresh_dispatch_shape(source: str) -> tuple[bool, dict[str, Any]]:
    """Prove the checked-in call shape; live completion is receipt-gated."""

    tree = ast.parse(source)
    refresh_calls = 0
    timeout_bound_calls = 0
    abandonable_workers = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else ""
        )
        if function_name in {"apply_transition_and_refresh", "prepare"}:
            receiver_name = (
                node.func.value.id
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                else ""
            )
            if function_name == "prepare" and receiver_name != "repository_service":
                continue
            refresh_calls += 1
            direct_timeout = any(keyword.arg == "timeout" for keyword in node.keywords)
            request_timeout = any(
                isinstance(argument, ast.Call)
                and (
                    (
                        isinstance(argument.func, ast.Name)
                        and argument.func.id == "RepositoryDecisionRequest"
                    )
                    or (
                        isinstance(argument.func, ast.Attribute)
                        and argument.func.attr == "RepositoryDecisionRequest"
                    )
                )
                and any(keyword.arg == "refresh_timeout" for keyword in argument.keywords)
                for argument in node.args
            )
            if direct_timeout or request_timeout:
                timeout_bound_calls += 1
        if function_name == "to_thread" and node.args:
            target = node.args[0]
            target_name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else target.id
                if isinstance(target, ast.Name)
                else ""
            )
            if "refresh" in target_name or target_name == "prepare":
                abandonable_workers += 1
    passed = (
        bool(refresh_calls) and refresh_calls == timeout_bound_calls and not abandonable_workers
    )
    return passed, {
        "scope": "source_call_shape_live_receipt_still_required",
        "refresh_calls": refresh_calls,
        "timeout_bound_calls": timeout_bound_calls,
        "abandonable_refresh_workers": abandonable_workers,
    }


def _typed_outcome_witnesses() -> tuple[bool, dict[str, Any]]:
    witnesses = {
        "solved": {"verifier_result": {"rewards": {"reward": 1}}},
        "unsolved_graded": {"verifier_result": {"rewards": {"reward": 0}}},
        "censored": {"exception_info": {"exception_type": "AgentTimeoutError"}},
        "error": {"exception_info": {"exception_type": "VerifierTimeoutError"}},
        "missing_verifier": {},
    }
    observed = {name: classify_trial_outcome(payload).value for name, payload in witnesses.items()}
    expected = {item.value for item in TrialOutcome}
    return set(observed.values()) == expected and len(set(observed.values())) == len(expected), {
        "scope": "executable_synthetic_classifier_witnesses",
        "outcomes": observed,
    }


def _observed_fact_accounting_witnesses() -> tuple[bool, dict[str, Any]]:
    trajectory = {"messages": []}
    unaccounted_receipt = {
        "observed_facts": {
            "fact_extractions": [{"fact_id": "observed-witness", "eligible_call": 1}],
            "fact_deliveries": [],
            "fact_decisions": [],
        }
    }
    accounted_receipt = {
        "observed_facts": {
            "fact_extractions": [{"fact_id": "observed-witness", "eligible_call": 1}],
            "fact_deliveries": [{"fact_ids": ["observed-witness"], "call": 1}],
            "fact_decisions": [
                {
                    "fact_id": "observed-witness",
                    "call": 1,
                    "disposition": "selected",
                }
            ],
        }
    }
    _, missing = _observed_fact_accounting(unaccounted_receipt, trajectory, "witness")
    accounted, closed = _observed_fact_accounting(accounted_receipt, trajectory, "witness")
    passed = bool(missing) and not closed and accounted.get("status") == "fully_accounted"
    return passed, {
        "scope": "executable_identity_join_witnesses",
        "unaccounted_rejected": bool(missing),
        "exact_identity_accepted": not closed,
    }


def _check(name: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def audit_configuration(
    root: Path,
    *,
    paid_workflow_path: Path | None = None,
    provider_free_workflow_path: Path | None = None,
    treatment_path: Path | None = None,
) -> dict[str, Any]:
    """Audit immutable configuration without making a provider call."""

    root = root.resolve()
    release = load_release_manifest(root / ACTIVE_RELEASE_PATH, root=root)
    paid_path = paid_workflow_path or root / ".github/workflows/tb2_miniswe_central.yml"
    provider_path = (
        provider_free_workflow_path or root / ".github/workflows/central_provider_free.yml"
    )
    selected_treatment_path = treatment_path or release.treatment_path
    paid = paid_path.read_text(encoding="utf-8")
    provider_free = provider_path.read_text(encoding="utf-8")
    treatment = json.loads(selected_treatment_path.read_text(encoding="utf-8"))
    runtime = build_runtime_arguments(
        treatment,
        source_sha=release.runtime_commit,
        max_steps=100,
    )["agent_kwargs"]
    release_gate_source = (root / "scripts/central_release_gate.py").read_text(encoding="utf-8")
    agent_source = (root / "eval/gt_central_agent.py").read_text(encoding="utf-8")
    merge_source = (root / "scripts/tb2_merge_results.py").read_text(encoding="utf-8")
    graph_shape_ok, graph_shape_evidence = _graph_refresh_dispatch_shape(agent_source)
    outcome_witnesses_ok, outcome_witnesses = _typed_outcome_witnesses()
    observed_witnesses_ok, observed_witnesses = _observed_fact_accounting_witnesses()
    workflows = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / ".github/workflows").glob("*.yml"))
    }
    documentation = audit_documentation(
        root, documents=tuple(Path(path) for path in _REQUIRED_DOCS)
    )

    checks = [
        _check(
            "canonical_task_profile",
            release.task_profile == "repair20-v1"
            and "options: [repair20-v1]" in paid
            and "regression-smoke-v1" not in paid,
            {"task_profile": release.task_profile},
        ),
        _check(
            "final_treatment_contract",
            treatment.get("profile_id") == "central_relational_v2"
            and all(runtime.get(key) == value for key, value in _REQUIRED_RUNTIME.items()),
            {
                "profile_id": treatment.get("profile_id"),
                "required_runtime": _REQUIRED_RUNTIME,
            },
        ),
        _check(
            "paid_dispatch_interlock",
            "needs: [resolve, provider_free, release_identity]" in paid
            and "Verify canonical release identity before provider spend" in paid
            and "Verify exact provider-free certification identity" in paid
            and 'enable_replay_capture="true"' in paid
            and "inputs.replay_capture" not in paid
            and "REPLAY_CAPTURE:" not in paid,
            {
                "release_identity_precedes_plan": (
                    paid.index("release_identity:") < paid.index("plan:")
                )
            },
        ),
        _check(
            "provider_free_is_provider_free",
            all(
                secret not in provider_free
                for secret in (
                    "OPENAI_API_KEY",
                    "DEEPSEEK_API_KEY",
                    "ANTHROPIC_API_KEY",
                )
            )
            and "python -m scripts.central_bootstrap_canary" not in provider_free
            and "python scripts/central_bootstrap_canary" not in provider_free
            and "fetch-depth: 0" in provider_free
            and "timeout-minutes:" not in provider_free,
            {"provider_credentials_declared": False},
        ),
        _check(
            "fail_open_solver_dispatch",
            "evaluate_provider_barrier_v2(" in agent_source
            and "dispatch_assessment = assess_provider_dispatch(provider_barrier)" in agent_source
            and 'model_call_contexts[-1]["provider_dispatch_assessment"]' in agent_source
            and 'contribution_receipt["treatment_validity"]' in agent_source
            and '"solver_continued": bool(' in agent_source
            and 'terminal = "MechanicalCompletenessBlocked"' not in agent_source,
            {
                "optional_substrate_failure_invalidates_treatment": True,
                "optional_substrate_failure_does_not_block_solver": True,
            },
        ),
        _check(
            "terminal_task_check_surface",
            all(f'"{name}"' in release_gate_source for name in _REQUIRED_TASK_CHECKS)
            and "_task_execution_certificate" in release_gate_source,
            {"required_checks": list(_REQUIRED_TASK_CHECKS)},
        ),
        _check(
            "provider_free_test_surface",
            all(
                item in provider_free
                for item in (
                    "tests/test_mechanical_completeness.py",
                    "tests/test_mechanical_completeness_gate.py",
                    "tests/test_release_manifest.py",
                    "tests/test_verify_frozen_outcome_prediction.py",
                    "gt_engine/mechanical_completeness.py",
                    "scripts/mechanical_completeness_gate.py",
                )
            ),
            {"mutation_sensitive": True},
        ),
        _check(
            "release_documentation",
            all((root / path).is_file() for path in _REQUIRED_DOCS)
            and documentation["status"] == "PASS",
            {
                "required_documents": list(_REQUIRED_DOCS),
                "audit": documentation,
            },
        ),
        _check(
            "workflow_secret_and_schedule_safety",
            all("schedule:" not in text for text in workflows.values())
            and all("inputs.api_key" not in text for text in workflows.values())
            and all("\n      api_key:" not in text for text in workflows.values()),
            {
                "workflows_checked": len(workflows),
                "scheduled_workflows": [
                    path for path, text in workflows.items() if "schedule:" in text
                ],
                "secret_value_dispatch_inputs": [
                    path
                    for path, text in workflows.items()
                    if "inputs.api_key" in text or "\n      api_key:" in text
                ],
            },
        ),
        _check(
            "frozen_provider_route_contract",
            "scripts.provider_route_contract" in paid
            and "inputs.model" not in paid
            and "inputs.api_base" not in paid
            and "inputs.api_key" not in paid
            and "OPENAI_BASE_URL: https://" not in paid
            and "GT_LITELLM_MODEL: openai/" not in paid
            and "OPENAI_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}" not in paid
            and "--bind-credential" in paid
            and 'expected_bootstrap_route.get("expected_response_model")' in merge_source
            and 'expected_bootstrap_route.get("expected_adapter_provider")' in merge_source
            and "observed_treatment_runtime_contracts" in merge_source,
            {"identity_source": "treatment_receipts_and_frozen_route_contract"},
        ),
        _check(
            "repository_refresh_dispatch_shape",
            graph_shape_ok,
            graph_shape_evidence,
        ),
        _check(
            "typed_trial_outcome_witnesses",
            outcome_witnesses_ok,
            outcome_witnesses,
        ),
        _check(
            "observed_fact_identity_join_witnesses",
            observed_witnesses_ok,
            observed_witnesses,
        ),
    ]
    failures = [check["name"] for check in checks if not check["passed"]]
    return {
        "schema": "gt.mechanical_completeness_configuration.v1",
        "status": "PASS" if not failures else "BLOCKED",
        "release_id": release.release_id,
        "task_profile": release.task_profile,
        "runtime_commit": release.runtime_commit,
        "checks": checks,
        "failures": failures,
    }


def _head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _tracked_worktree_changes(root: Path) -> tuple[str, ...]:
    """Return tracked changes that are invisible to commit-based release proof."""

    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line.rstrip() for line in completed.stdout.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("mechanical-completeness.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    report = audit_configuration(root)
    try:
        tracked_changes = _tracked_worktree_changes(root)
        report["worktree_identity"] = {
            "clean": not tracked_changes,
            "tracked_change_count": len(tracked_changes),
            "tracked_changes": list(tracked_changes),
        }
        if tracked_changes:
            report["status"] = "BLOCKED"
            report["failures"] = [
                *report["failures"],
                "tracked_worktree_not_clean",
            ]
            raise ValueError("tracked worktree changes are not bound to the release commit")
        report["release_identity_proof"] = verify_release_manifest(
            manifest_path=root / ACTIVE_RELEASE_PATH,
            current_commit=_head(root),
            root=root,
            expected_profile="repair20-v1",
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        report["status"] = "BLOCKED"
        report["failures"] = [
            *report["failures"],
            "release_identity_proof",
        ]
        report["release_identity_error"] = str(exc)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "PASS":
        return 2
    print("GT_MECHANICAL_COMPLETENESS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
