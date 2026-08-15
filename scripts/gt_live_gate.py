"""Acceptance gate for a live GT-on Mini-SWE campaign.

The input is the machine JSON emitted by ``scripts/gt_audit.py``. This gate
does not inspect transcript markers. Attribution comes from the hash-chained
trigger/producer/delivery/provider/response records already verified by the
auditor.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gt_engine.attribution import DIRECT_FEATURES  # noqa: E402


def _model_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"model", "model_name"} and isinstance(item, str):
                found.add(item)
            found.update(_model_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_model_values(item))
    return found


def evaluate_live_gate(
    audit: dict[str, Any],
    *,
    min_witnessed: int,
    min_exercised: int = 0,
    min_action_consistent: int = 0,
    expected_tasks: int,
    expected_model: str,
    expected_temperature: float | None = None,
    require_complete_census: bool = False,
    require_complete_profile: bool = False,
    require_task_contract: bool = False,
    require_graph_surface_receipt: bool = False,
    require_verification_plan_on_graph_edit: bool = False,
    require_improvement_receipts: bool = False,
    require_step0_localization: bool = False,
    required_behavior_flags: tuple[str, ...] = (),
    required_lifecycle: tuple[str, ...] = (),
    run_dir: Path | None = None,
) -> dict[str, Any]:
    tasks = list(audit.get("tasks") or ())
    issues: list[str] = []
    witnessed: set[str] = set()
    dark: list[str] = []
    faults: list[str] = []
    unexposed: list[str] = []
    actions_consistent: set[str] = set()
    exercised: set[str] = set()
    lifecycle_observed: set[str] = set()
    provider_temperatures: set[float] = set()
    complete_census = True
    complete_profile = True
    observed_behavior_flags: set[str] = set()
    improvement_totals = {
        "predicate_observed": 0,
        "graph_semantic_facts": 0,
        "graph_refreshes": 0,
        "capsules_expired": 0,
        "utility_scored": 0,
        "utility_abstained": 0,
        "progress_transitions": 0,
        "progress_controls": 0,
        "tool_controls_rejected": 0,
        "harness_access_rejected": 0,
        "harmful_tool_outcomes": 0,
        "new_capsule_tool_outcomes": 0,
    }
    step0_localization_eligible = 0
    step0_localization_on_time = 0
    expected_feature_ids = set(DIRECT_FEATURES)
    valid_statuses = {
        "INELIGIBLE",
        "TRIGGERED_DARK",
        "SUPPRESSED_WITH_REASON",
        "DELIVERED_UNEXPOSED",
        "EXPOSED",
        "WITNESSED",
        "TELEMETRY_FAULT",
    }

    if len(tasks) != expected_tasks:
        issues.append(
            f"task count {len(tasks)} != expected {expected_tasks}"
        )
    for task in tasks:
        task_name = str(task.get("task_name") or "?")
        if task.get("agent_error") or task.get("exception_info"):
            issues.append(f"{task_name}: unhealthy agent/harness result")
        for issue in task.get("attribution_issues") or ():
            issues.append(f"{task_name}: attribution: {issue}")
        for issue in task.get("ledger_issues") or ():
            issues.append(f"{task_name}: ledger: {issue}")
        for issue in task.get("dose_violations") or ():
            issues.append(f"{task_name}: dose: {issue}")
        lifecycle_observed.update(
            str(phase)
            for phase in (task.get("lifecycle_checkpoints") or {})
        )
        if require_task_contract:
            obligation_count = int(task.get("obligation_count") or 0)
            shipped_count = int(task.get("shipped_obligation_count") or 0)
            verify_total = int(task.get("verify_obligation_total") or 0)
            if (
                not task.get("task_role")
                or obligation_count <= 0
                or shipped_count != obligation_count
            ):
                issues.append(
                    f"{task_name}: incomplete task contract "
                    f"(role={task.get('task_role') or 'missing'}, "
                    f"shipped={shipped_count}/{obligation_count})"
                )
            if "verify" in (task.get("lifecycle_checkpoints") or {}) and (
                verify_total != obligation_count
            ):
                issues.append(
                    f"{task_name}: verification contract mismatch "
                    f"(verify_total={verify_total}, "
                    f"task_total={obligation_count})"
                )
        if (
            require_graph_surface_receipt
            and (
                not task.get("graph_surface_receipt_present")
                or not task.get("graph_projection_present")
            )
        ):
            issues.append(
                f"{task_name}: missing graph surface/projection receipt"
            )
        localization_eligible = bool(
            task.get("task_start_localization_eligible")
        )
        step0_localization_eligible += int(localization_eligible)
        if require_step0_localization and localization_eligible:
            provider_iteration = int(
                task.get(
                    "task_start_localization_provider_iteration"
                ) or 0
            )
            response_iteration = int(
                task.get(
                    "task_start_localization_response_iteration"
                ) or 0
            )
            if not task.get("task_start_localization_compound"):
                issues.append(
                    f"{task_name}: task-start localization is not joined "
                    "to the obligations delivery"
                )
            if provider_iteration != 1:
                issues.append(
                    f"{task_name}: task-start localization reached provider "
                    f"iteration {provider_iteration}, expected 1"
                )
            if response_iteration != 1:
                issues.append(
                    f"{task_name}: task-start localization response iteration "
                    f"{response_iteration}, expected 1"
                )
            if (
                task.get("task_start_localization_compound")
                and provider_iteration == 1
                and response_iteration == 1
            ):
                step0_localization_on_time += 1
        graph_edit = bool(
            task.get("graph_available")
            and "post_edit" in (task.get("lifecycle_checkpoints") or {})
        )
        if (
            require_verification_plan_on_graph_edit
            and graph_edit
            and not task.get("verification_plan_evaluated")
        ):
            issues.append(
                f"{task_name}: graph-backed edit did not evaluate "
                "GT_VERIFICATION_PLAN"
            )
        improvement_totals["predicate_observed"] += int(
            task.get("predicate_observed_count") or 0
        )
        improvement_totals["graph_semantic_facts"] += int(
            task.get("graph_semantic_fact_count") or 0
        )
        improvement_totals.setdefault("graph_evidence_ranked", 0)
        improvement_totals["graph_evidence_ranked"] += int(
            task.get("graph_evidence_ranked_count") or 0
        )
        improvement_totals["graph_refreshes"] += int(
            task.get("graph_refresh_count") or 0
        )
        improvement_totals["capsules_expired"] += int(
            task.get("capsule_expired_count") or 0
        )
        improvement_totals["utility_scored"] += int(
            task.get("utility_scored_count") or 0
        )
        improvement_totals["utility_abstained"] += int(
            task.get("utility_abstained_count") or 0
        )
        improvement_totals["progress_transitions"] += int(
            task.get("progress_transition_count") or 0
        )
        improvement_totals["progress_controls"] += int(
            task.get("progress_control_count") or 0
        )
        improvement_totals["tool_controls_rejected"] += int(
            task.get("tool_control_rejected_count") or 0
        )
        improvement_totals["harness_access_rejected"] += int(
            task.get("harness_access_rejected_count") or 0
        )
        improvement_totals["harmful_tool_outcomes"] += int(
            task.get("tool_outcome_harmful_count") or 0
        )
        improvement_totals["new_capsule_tool_outcomes"] += int(
            task.get("tool_outcome_new_capsule_count") or 0
        )
        if require_improvement_receipts:
            obligation_count = int(task.get("obligation_count") or 0)
            compiled_count = int(
                task.get("predicate_compiled_count") or 0
            )
            classified_count = int(
                task.get("tool_outcome_classified_count") or 0
            )
            tool_results = int(task.get("tool_results") or 0)
            outcome_counts = task.get("tool_outcome_counts") or {}
            if (
                not task.get("role_pack_present")
                or not task.get("role_pack_id")
                or not task.get("role_pack_version")
            ):
                issues.append(f"{task_name}: missing role-pack receipt")
            if compiled_count != obligation_count:
                issues.append(
                    f"{task_name}: predicate compilation mismatch "
                    f"({compiled_count}/{obligation_count})"
                )
            if int(task.get("predicate_invalid_receipt_count") or 0):
                issues.append(
                    f"{task_name}: invalid semantic predicate receipt"
                )
            if classified_count != tool_results:
                issues.append(
                    f"{task_name}: tool-outcome census mismatch "
                    f"({classified_count}/{tool_results})"
                )
            if int(outcome_counts.get("unknown") or 0):
                issues.append(
                    f"{task_name}: unknown tool outcome(s) remain"
                )
            if int(task.get("shell_lifecycle_unrecovered_count") or 0):
                issues.append(
                    f"{task_name}: unrecovered persistent shell failure"
                )
            if int(task.get("tool_budget_violation_count") or 0):
                issues.append(
                    f"{task_name}: bash timeout exceeded affordable "
                    "wall-clock budget"
                )
            if int(task.get("bash_observation_count") or 0) != int(
                task.get("tool_budget_receipt_count") or 0
            ):
                issues.append(
                    f"{task_name}: bash wall-clock receipt census mismatch "
                    f"({task.get('tool_budget_receipt_count') or 0}/"
                    f"{task.get('bash_observation_count') or 0})"
                )
            if int(task.get("forbidden_harness_path_attempt_count") or 0):
                issues.append(
                    f"{task_name}: task agent accessed forbidden harness paths"
                )
            if int(task.get("context_policy_request_count") or 0):
                if int(task.get("replay_issue_count") or 0):
                    issues.append(
                        f"{task_name}: per-iteration replay has join issues"
                    )
                if int(task.get("replay_iteration_count") or 0) != int(
                    task.get("iterations") or 0
                ):
                    issues.append(
                        f"{task_name}: replay iteration count does not match "
                        "the transcript"
                    )
                if int(task.get("replay_accounted_input_tokens") or 0) != int(
                    task.get("in_tokens") or 0
                ):
                    issues.append(
                        f"{task_name}: replay input tokens do not match "
                        "the transcript total"
                    )
            refresh_failures = int(task.get("graph_refresh_failure_count") or 0)
            refresh_recovered = int(task.get("graph_refresh_recovered_count") or 0)
            if refresh_failures > refresh_recovered:
                issues.append(
                    f"{task_name}: graph context refresh failure"
                )
            if int(task.get("graph_evidence_unlinked_count") or 0):
                issues.append(
                    f"{task_name}: decision-irrelevant graph evidence ranked"
                )
            if int(
                task.get("graph_evidence_revision_mismatch_count") or 0
            ):
                issues.append(
                    f"{task_name}: stale graph evidence revision"
                )
            if task.get("graph_available") and (
                not task.get("graph_projection_revision")
                or task.get("graph_projection_revision")
                != task.get("graph_router_revision")
            ):
                issues.append(
                    f"{task_name}: graph projection/router revision mismatch"
                )
            if int(task.get("capsule_repeated_exposure_count") or 0):
                issues.append(
                    f"{task_name}: GT capsule repeated across provider "
                    "decision boundaries"
                )
            if int(task.get("utility_selected_count") or 0) > int(
                task.get("utility_scored_count") or 0
            ):
                issues.append(
                    f"{task_name}: invalid utility selection receipt"
                )
        task_features = task.get("feature_attribution") or {}
        if require_complete_census:
            actual_ids = set(task_features)
            missing = sorted(expected_feature_ids - actual_ids)
            extra = sorted(actual_ids - expected_feature_ids)
            invalid = sorted(
                feature_id
                for feature_id, item in task_features.items()
                if str(item.get("status") or "") not in valid_statuses
            )
            if missing or extra or invalid:
                complete_census = False
                issues.append(
                    f"{task_name}: incomplete feature census "
                    f"(missing={missing}, extra={extra}, invalid={invalid})"
                )
        for value in task.get("provider_temperatures") or ():
            if isinstance(value, int | float):
                provider_temperatures.add(float(value))
        task_behavior_flags = {
            str(value) for value in task.get("profile_behavior_flags") or ()
        }
        observed_behavior_flags.update(task_behavior_flags)
        expected_controls = {
            str(value) for value in task.get("expected_profile_controls") or ()
        }
        active_controls = {
            str(value) for value in task.get("active_profile_controls") or ()
        }
        missing_controls = {
            str(value) for value in task.get("missing_profile_controls") or ()
        }
        receipt_fault = str(task.get("profile_receipt_fault") or "")
        if require_complete_profile and (
            not expected_controls
            or active_controls != expected_controls
            or missing_controls
            or receipt_fault
        ):
            complete_profile = False
            issues.append(
                f"{task_name}: incomplete profile control activation "
                f"(expected={len(expected_controls)}, "
                f"active={len(active_controls)}, "
                f"missing={sorted(missing_controls)}, "
                f"fault={receipt_fault or 'none'})"
            )
        for flag in required_behavior_flags:
            if flag not in task_behavior_flags:
                issues.append(
                    f"{task_name}: required behavior flag {flag} not active"
                )
        for feature_id, item in task_features.items():
            status = str(item.get("status") or "")
            reasons = {
                str(reason) for reason in item.get("reasons") or ()
            }
            if (
                status != "INELIGIBLE"
                or (reasons and reasons != {"no_trigger_observed"})
            ):
                exercised.add(feature_id)
            if status == "WITNESSED":
                witnessed.add(feature_id)
            elif status == "TRIGGERED_DARK":
                dark.append(f"{task_name}:{feature_id}")
            elif status == "TELEMETRY_FAULT":
                faults.append(f"{task_name}:{feature_id}")
            if item.get("deliveries") and not item.get("exposed"):
                unexposed.append(f"{task_name}:{feature_id}")
            if item.get("action_consistent"):
                actions_consistent.add(feature_id)

    if dark:
        issues.append("eligible trigger(s) went dark: " + ", ".join(dark))
    if faults:
        issues.append("telemetry fault(s): " + ", ".join(faults))
    if unexposed:
        issues.append("unexposed delivery/owner(s): " + ", ".join(unexposed))
    if len(witnessed) < min_witnessed:
        issues.append(
            f"witnessed identities {len(witnessed)} < required "
            f"{min_witnessed}"
        )
    if len(actions_consistent) < min_action_consistent:
        issues.append(
            f"action-consistent identities {len(actions_consistent)} < required "
            f"{min_action_consistent}"
        )
    if len(exercised) < min_exercised:
        issues.append(
            f"exercised identities {len(exercised)} < required "
            f"{min_exercised}"
        )
    if expected_temperature is not None and provider_temperatures != {
        float(expected_temperature)
    }:
        issues.append(
            f"provider temperature must be exactly {float(expected_temperature)}; "
            f"observed={sorted(provider_temperatures)}"
        )
    missing_lifecycle = sorted(set(required_lifecycle) - lifecycle_observed)
    if missing_lifecycle:
        issues.append(
            "missing SDLC lifecycle checkpoint(s): "
            + ", ".join(missing_lifecycle)
        )

    observed_models: set[str] = set()
    if run_dir is not None and run_dir.is_dir():
        for result_path in run_dir.glob("*/result.json"):
            try:
                observed_models.update(_model_values(json.loads(
                    result_path.read_text(encoding="utf-8")
                )))
            except (OSError, json.JSONDecodeError):
                issues.append(f"{result_path.parent.name}: unreadable result.json")
    if expected_model and expected_model not in observed_models:
        issues.append(
            f"expected model {expected_model!r} not found in result metadata; "
            f"observed={sorted(observed_models)}"
        )

    return {
        "schema": "gt.live_acceptance.v1",
        "passed": not issues,
        "task_count": len(tasks),
        "expected_tasks": expected_tasks,
        "min_witnessed": min_witnessed,
        "min_exercised": min_exercised,
        "min_action_consistent": min_action_consistent,
        "witnessed_count": len(witnessed),
        "witnessed_features": sorted(witnessed),
        "exercised_count": len(exercised),
        "exercised_features": sorted(exercised),
        "action_consistent_features": sorted(actions_consistent),
        "required_lifecycle": sorted(set(required_lifecycle)),
        "lifecycle_observed": sorted(lifecycle_observed),
        "missing_lifecycle": missing_lifecycle,
        "dark": dark,
        "faults": faults,
        "unexposed": unexposed,
        "observed_models": sorted(observed_models),
        "expected_temperature": expected_temperature,
        "provider_temperatures": sorted(provider_temperatures),
        "complete_census": complete_census,
        "complete_profile": complete_profile,
        "require_task_contract": require_task_contract,
        "require_graph_surface_receipt": require_graph_surface_receipt,
        "require_verification_plan_on_graph_edit": (
            require_verification_plan_on_graph_edit
        ),
        "require_improvement_receipts": require_improvement_receipts,
        "require_step0_localization": require_step0_localization,
        "step0_localization_eligible": step0_localization_eligible,
        "step0_localization_on_time": step0_localization_on_time,
        "improvement_totals": improvement_totals,
        "required_behavior_flags": sorted(set(required_behavior_flags)),
        "observed_behavior_flags": sorted(observed_behavior_flags),
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_json")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--min-witnessed", type=int, default=7)
    parser.add_argument("--min-exercised", type=int, default=12)
    parser.add_argument("--min-action-consistent", type=int, default=0)
    parser.add_argument("--expected-tasks", type=int, default=5)
    parser.add_argument("--expected-model", default="deepseek-v4-flash")
    parser.add_argument("--expected-temperature", type=float)
    parser.add_argument("--require-complete-census", action="store_true")
    parser.add_argument("--require-complete-profile", action="store_true")
    parser.add_argument("--require-task-contract", action="store_true")
    parser.add_argument(
        "--require-graph-surface-receipt", action="store_true"
    )
    parser.add_argument(
        "--require-verification-plan-on-graph-edit", action="store_true"
    )
    parser.add_argument(
        "--require-improvement-receipts", action="store_true"
    )
    parser.add_argument(
        "--require-step0-localization", action="store_true"
    )
    parser.add_argument(
        "--require-behavior-flags",
        default="",
        help="comma-separated profile behavior flags required on every task",
    )
    parser.add_argument(
        "--require-lifecycle",
        default="",
        help="comma-separated SDLC checkpoint phases required across the run",
    )
    parser.add_argument("--json", dest="output_json")
    args = parser.parse_args(argv)

    audit_path = Path(args.audit_json)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    report = evaluate_live_gate(
        audit,
        min_witnessed=args.min_witnessed,
        min_exercised=args.min_exercised,
        min_action_consistent=args.min_action_consistent,
        expected_tasks=args.expected_tasks,
        expected_model=args.expected_model,
        expected_temperature=args.expected_temperature,
        require_complete_census=args.require_complete_census,
        require_complete_profile=args.require_complete_profile,
        require_task_contract=args.require_task_contract,
        require_graph_surface_receipt=args.require_graph_surface_receipt,
        require_verification_plan_on_graph_edit=(
            args.require_verification_plan_on_graph_edit
        ),
        require_improvement_receipts=args.require_improvement_receipts,
        require_step0_localization=args.require_step0_localization,
        required_behavior_flags=tuple(
            flag.strip()
            for flag in args.require_behavior_flags.split(",")
            if flag.strip()
        ),
        required_lifecycle=tuple(
            phase.strip()
            for phase in args.require_lifecycle.split(",")
            if phase.strip()
        ),
        run_dir=Path(args.run_dir),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output_json:
        Path(args.output_json).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
