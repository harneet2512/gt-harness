"""Arm-neutral trajectory metrics and strict GT efficiency gates."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from gt_engine.central_runtime import is_check_command, is_submit_command, normalize_command
from gt_engine.delivery_audit import audit_provider_deliveries

PRIMARY_RESOURCES = (
    "total_tokens",
    "api_calls",
    "actions",
    "assistant_steps",
    "normalized_cost_usd",
)
CONTROLLER_RESOURCES = (
    "effective_actions",
    "controller_environment_execs",
    "controller_cached_reads",
    "sensor_environment_execs",
)
DIAGNOSTIC_METRICS = (
    "input_tokens",
    "output_tokens",
    "uncached_input_tokens",
    "context_chars_sent",
    "provider_request_chars_sent",
    "provider_requests_hashed",
    "provider_request_hash_coverage",
    "provider_request_budget_failures",
    "provider_requests_prepared",
    "executor_provider_requests_prepared",
    "executor_api_calls",
    "bootstrap_api_calls",
    "bootstrap_provider_responses_received",
    "total_provider_responses_received",
    "bootstrap_provider_request_chars",
    "model_query_invocations",
    "provider_responses_received",
    "provider_requests_not_sent",
    "provider_evidence_events",
    "provider_evidence_dispatched",
    "provider_evidence_prepared_not_sent",
    "provider_request_min_headroom_tokens",
    "provider_stable_prefix_chars",
    "provider_stable_prefix_ratio_mean",
    "stock_provider_chars_sent",
    "feature_guidance_chars_sent",
    "certified_graph_chars_sent",
    "provider_compaction_removed_chars",
    "provider_compaction_receipt_chars",
    "final_provider_chars_sent",
    "provider_changed_message_count",
    "provider_view_changed_calls",
    "provider_exact_parity_calls",
    "certified_evidence_changed_calls",
    "provider_budget_compaction_changed_calls",
    "certified_opportunity_evaluations",
    "certified_opportunities",
    "certified_opportunity_abstentions",
    "heuristic_opportunity_abstentions",
    "certified_provider_deliveries",
    "certified_provider_behavior_measurable",
    "certified_provider_anchor_followed",
    "certified_controller_actuations",
    "model_output_chars",
    "failed_actions",
    "repeated_commands",
    "no_action_assistant_steps",
    "responses_with_actions",
    "single_action_responses",
    "multi_action_responses",
    "max_actions_per_response",
    "actions_per_api_call",
    "wasted_action_proxy",
    "steps_to_first_search",
    "steps_to_first_read",
    "steps_to_first_edit",
    "steps_to_first_check",
    "steps_to_submit",
    "gt_context_chars_added",
    "preemptive_retrieval_chars_added",
    "persistent_execution_state_context_chars_added",
    "context_state_frame_chars_added",
    "context_frontier_chars_added",
    "progress_frame_chars_added",
    "persistent_state_enabled",
    "persistent_state_initialized",
    "persistent_state_initial_retrieval_calls",
    "persistent_state_initial_retrieval_latency_ms",
    "persistent_state_initial_ranked_files",
    "persistent_state_initial_selected_evidence",
    "persistent_state_bootstrap_calls",
    "persistent_state_bootstrap_input_tokens",
    "persistent_state_bootstrap_output_tokens",
    "persistent_state_bootstrap_cached_tokens",
    "persistent_state_bootstrap_cost_usd",
    "persistent_state_bootstrap_latency_ms",
    "persistent_state_context_compilations",
    "persistent_state_preflight_projections",
    "persistent_state_postflight_commits",
    "persistent_state_graph_rebases",
    "persistent_state_material_transitions",
    "persistent_state_deliveries",
    "persistent_state_context_tokens",
    "persistent_state_context_chars",
    "newly_inserted_context_chars",
    "represented_context_facts",
    "total_gt_context_chars_added",
    "effects_produced",
    "effects_applied",
    "state_mutations",
    "effect_trace_rows",
    "provider_payload_effects",
    "existing_engine_actuation_effects",
    "engine_internal_state_effects",
    "audit_only_effects",
    "payload_deliveries",
    "timely_payload_deliveries",
    "late_payload_deliveries",
    "predictive_payload_deliveries",
    "provider_delivery_count",
    "provider_delivery_visible_chars",
    "provider_delivery_claim_count",
    "provider_delivery_timely_count",
    "provider_delivery_late_count",
    "provider_delivery_predictive_count",
    "provider_delivery_duplicate_count",
    "provider_delivery_failures",
    "predecided_actions_after_evidence",
    "context_compiler_calls",
    "context_fact_candidates",
    "context_facts_selected",
    "context_facts_represented",
    "context_facts_controller_only",
    "context_facts_omitted",
    "context_facts_accounted",
    "context_frontier_calls",
    "context_frontier_candidates",
    "context_frontier_accounted",
    "context_frontier_deliveries",
    "context_frontier_facts_delivered",
    "context_frontier_zero_tasks",
    "context_frontier_duplicate_facts",
    "context_frontier_duplicate_claims",
    "context_frontier_candidate_languages",
    "context_frontier_delivered_languages",
    "repository_intelligence_valid",
    "repository_substrate_valid",
    "repository_graph_degraded_fallback",
    "repository_graph_schema_valid",
    "repository_graph_nodes",
    "repository_graph_edges",
    "repository_source_files",
    "repository_indexable_files",
    "repository_ambiguous_source_files",
    "repository_unsupported_source_files",
    "repository_resolved_languages",
    "repository_resolution_reason_kinds",
    "repository_parser_failures",
    "repository_refreshes",
    "repository_mirror_transfer_ms",
    "repository_mirror_files",
    "repository_mirror_bytes",
    "repository_mirror_selected_source_files",
    "repository_mirror_selected_metadata_files",
    "repository_mirror_excluded_artifacts",
    "repository_mirror_excluded_deliverables",
    "repository_mirror_excluded_oversize",
    "repository_mirror_excluded_budget",
    "repository_index_refresh_ms",
    "repository_full_refreshes",
    "repository_incremental_refreshes",
    "repository_revision_cache_hits",
    "repository_action_queries",
    "repository_action_query_cache_hits",
    "context_exact_duplicate_chars_removed",
    "context_unique_reasoning_chars_removed",
    "context_bounded_observations",
    "context_bounded_observation_applications",
    "context_bounded_observation_chars_removed",
    "context_duplicate_turns_represented",
    "context_old_tool_results_cleared",
    "context_stale_reads_elided",
    "context_recap_receipts",
    "context_recap_chars_added",
    "context_recap_fallbacks",
    "context_compactions",
    "context_compaction_deferral_count",
    "context_chars_elided",
    "context_state_frame_calls",
    "context_provider_view_changed_calls",
    "context_selected_facts_action_measurable",
    "context_selected_facts_action_aligned",
    "context_compiler_effects_considered",
    "context_compiler_effects_no_eligible_call",
    "context_compiler_effects_unaccounted",
    "preflight_known_segment_operations",
    "preflight_unknown_segment_operations",
    "preflight_typed_targets",
    "validation_attributed_results",
    "validation_unattributed_intents",
    "required_check_claims_without_declared_id",
    "redundant_provider_payloads",
    "completion_predicate_checks",
    "completion_certificate_evaluations",
    "completion_probe_execs",
    "completion_cache_hits",
    "effective_task_actions",
    "actual_environment_execs",
    "controller_environment_execs",
    "controller_cached_reads",
    "sensor_environment_execs",
    "auto_submit_attempts",
    "auto_submits",
    "progress_transitions",
    "task_progress_changes",
    "progress_observations",
    "progress_distinct_attempts",
    "progress_distinct_observations",
    "progress_observation_gains",
    "progress_task_gains",
    "progress_same_state_updates_suppressed",
    "failed_read_anchors_not_consumed",
    "valid_nonzero_observations",
    "declared_validator_proposals",
    "declared_validators_with_redirection",
    "declared_validators_preserved_with_redirection",
    "adaptive_validation_timeouts",
    "default_validation_timeouts",
    "model_action_timeouts",
    "activity_events",
    "agent_wall_time_seconds",
    "trial_wall_time_seconds",
)
# Frozen DeepSeek V4 Flash experiment rates per million tokens. Provider cost
# was configured as ignore_errors and is often zero, so this normalized metric
# is the cross-arm comparable resource measure.
PRICE_INPUT_CACHE_HIT = 0.0028
PRICE_INPUT_CACHE_MISS = 0.14
PRICE_OUTPUT = 0.28
_SEARCH = re.compile(r"(?:^|[;&|()\s/])(?:rg|grep|find|ack|ag)(?:$|\s)", re.I)
_READ = re.compile(r"(?:^|[;&|()\s/])(?:cat|head|tail|less|more|nl)(?:$|\s)", re.I)
_EDIT = re.compile(
    r"(?:apply_patch|sed\s+-i|perl\s+-i|python(?:3)?\s+-c|ruby\s+-i|"
    r"\b(?:touch|tee|cp|mv)\b|>>|\becho\b.*>)",
    re.I,
)
_CENSORED = {
    "ModelTimeout",
    "Cancelled",
}
_SOLVER_EXHAUSTED = {
    "LimitsExceeded",
    "StepLimitExceeded",
    "CostLimitExceeded",
    "WallTimeExceeded",
    "DeadlineReserveReached",
    "ContextBudgetExhausted",
}
_CENSORED_HARBOR_EXCEPTIONS = {
    "AgentTimeoutError",
    "AgentSetupTimeoutError",
    "EnvironmentBuildTimeoutError",
    "TaskTimeoutError",
    "TimeoutError",
    "CancelledError",
    # Provider rejected the request because the assembled conversation
    # exceeded the model context window.  This is an outer-run censor, not a
    # verifier failure and must not be counted as an unsolved task or a clean
    # solve in outcome-preservation gates.
    "ContextWindowExceededError",
}


def normalized_token_cost(cache_miss: int, cache_hit: int, output: int) -> float:
    return (
        cache_miss * PRICE_INPUT_CACHE_MISS
        + cache_hit * PRICE_INPUT_CACHE_HIT
        + output * PRICE_OUTPUT
    ) / 1_000_000


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"messages": payload}
    if not isinstance(payload, dict):
        raise ValueError(f"trajectory must be an object or list: {path}")
    return payload


def _elapsed_seconds(started_at: Any, finished_at: Any) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(max(0.0, (finish - start).total_seconds()), 6)


def _returncode(message: dict[str, Any]) -> int | None:
    extra = message.get("extra") or {}
    value = extra.get("returncode")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    match = re.search(r"<returncode>\s*(-?\d+)\s*</returncode>", str(message.get("content") or ""))
    return int(match.group(1)) if match else None


def _category(command: str) -> str:
    if is_submit_command(command):
        return "submit"
    if is_check_command(command):
        return "check"
    if _EDIT.search(command):
        return "edit"
    if _SEARCH.search(command):
        return "search"
    if _READ.search(command):
        return "read"
    return "other"


def _payload_anchors(payload: dict[str, Any]) -> list[str]:
    """Extract concrete anchor strings from a receipt payload."""
    anchors: list[str] = []
    for key in ("path", "command", "declared_check", "precedent_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            anchors.append(value.strip())
    for key in ("changed_paths", "blockers"):
        value = payload.get(key)
        if isinstance(value, list):
            anchors.extend(str(item) for item in value if str(item).strip())
    for key in ("anchors", "callers", "definition_anchors", "reference_anchors"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    path = item.get("caller_path") or item.get("path")
                    if isinstance(path, str) and path.strip():
                        anchors.append(path.strip())
    return list(dict.fromkeys(anchors))


def _feature_funnel(receipt: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Feature funnel grounded in the receipt, never inferred from prose.

    ``guidance_deliveries`` counts payloads that actually reached a model
    request; ``behaviorally_aligned`` counts deliveries whose concrete anchor
    appears in a later command.  Neither is causal proof.
    """
    features = receipt.get("features") or {}
    produced = sum(int(value) for value in (features.get("produced_counts") or {}).values())
    effects = features.get("effects") or []
    consumed = len(effects)
    applied = sum(1 for effect in effects if effect.get("applied_after_action") is not None)
    deliveries = receipt.get("guidance_deliveries") or []
    _provider_delivery_rows, provider_delivery_failures, provider_delivery_totals = (
        audit_provider_deliveries(receipt)
    )
    receipts_by_key = {
        (row.get("feature_id"), int(row.get("action") or 0)): row
        for row in (features.get("receipts") or [])
        if row.get("model_visible")
    }
    delivered = 0
    aligned = 0
    for row in deliveries:
        delivered += 1
        evidence_action = int(row.get("evidence_action") or 0)
        receipt_row = receipts_by_key.get((row.get("feature_id"), evidence_action)) or {}
        payload = receipt_row.get("payload") or {}
        anchors = _payload_anchors(payload)
        if not anchors:
            continue
        later = [item for item in actions if item["index"] > evidence_action]
        if any(any(anchor in item["command"] for anchor in anchors) for item in later):
            aligned += 1
    surface_counts = {
        surface: int(values.get("delivery_count") or 0)
        for surface, values in provider_delivery_totals.get("surfaces", {}).items()
    }
    surface_chars = {
        surface: int(values.get("visible_chars") or 0)
        for surface, values in provider_delivery_totals.get("surfaces", {}).items()
    }
    return {
        "feature_produced": produced,
        "feature_consumed": consumed,
        "feature_effects_applied": applied,
        "guidance_deliveries": delivered,
        "guidance_behaviorally_aligned": aligned,
        "guidance_suppressed": int(features.get("guidance_suppressed") or 0),
        "provider_delivery_count": int(provider_delivery_totals["delivery_count"]),
        "provider_delivery_visible_chars": int(provider_delivery_totals["visible_chars"]),
        "provider_delivery_claim_count": int(provider_delivery_totals["claim_count"]),
        "provider_delivery_timely_count": int(provider_delivery_totals["timely_count"]),
        "provider_delivery_late_count": int(provider_delivery_totals["late_count"]),
        "provider_delivery_predictive_count": int(
            provider_delivery_totals["predictive_count"]
        ),
        "provider_delivery_duplicate_count": int(
            provider_delivery_totals["duplicate_count"]
        ),
        "provider_delivery_failures": len(provider_delivery_failures),
        "provider_delivery_surface_counts": surface_counts,
        "provider_delivery_surface_chars": surface_chars,
    }


def _feature_applicability_metrics(features: dict[str, Any]) -> dict[str, Any]:
    """Separate natural firing, justified abstention, and implementation misses."""

    applicability = features.get("feature_applicability") or {}
    produced_counts = features.get("produced_counts") or {}
    fired_ids = sorted(
        feature_id for feature_id, count in produced_counts.items() if int(count or 0) > 0
    )
    if not applicability:
        return {
            "feature_applicability_available": False,
            "features_fired": len(fired_ids),
            "feature_ids_fired": fired_ids,
            "features_correctly_abstained": 0,
            "feature_ids_correctly_abstained": [],
            "features_trigger_absent": 0,
            "feature_ids_trigger_absent": [],
            "feature_missed_triggers": 0,
            "feature_ids_missed_triggers": [],
            "false_feature_fires": 0,
            "feature_ids_false_fires": [],
        }
    correct_abstentions = sorted(
        feature_id
        for feature_id, row in applicability.items()
        if row.get("status") == "correct_abstention"
    )
    trigger_absent = sorted(
        feature_id
        for feature_id, row in applicability.items()
        if row.get("status") == "trigger_absent"
    )
    missed = sorted(
        feature_id
        for feature_id, row in applicability.items()
        if row.get("status") == "missed_trigger"
    )
    false_fires = sorted(
        feature_id
        for feature_id in fired_ids
        if int((applicability.get(feature_id) or {}).get("eligible") or 0) == 0
    )
    orphan_eligible = {
        str(row.get("feature_id") or "")
        for row in (features.get("feature_opportunities") or [])
        if row.get("evidence_status") == "eligible" and not row.get("effect_id")
    }
    missed = sorted(set(missed) | {item for item in orphan_eligible if item})
    return {
        "feature_applicability_available": True,
        "features_fired": len(fired_ids),
        "feature_ids_fired": fired_ids,
        "features_correctly_abstained": len(correct_abstentions),
        "feature_ids_correctly_abstained": correct_abstentions,
        "features_trigger_absent": len(trigger_absent),
        "feature_ids_trigger_absent": trigger_absent,
        "feature_missed_triggers": len(missed),
        "feature_ids_missed_triggers": missed,
        "false_feature_fires": len(false_fires),
        "feature_ids_false_fires": false_fires,
    }


def _lifecycle_metrics(features: dict[str, Any]) -> dict[str, int | None]:
    lifecycle = features.get("lifecycle") or {}

    def first(phase: str) -> int | None:
        item = lifecycle.get(phase) or {}
        return item.get("first_action")

    return {
        "first_anchored_location_action": first("location_anchored"),
        "first_workspace_edit_action": first("workspace_edited"),
        "first_focused_validation_action": first("focused_check_validated"),
        "first_behavior_observed_action": first("behavior_observed"),
    }


def extract_trajectory(
    path: Path,
    *,
    task: str | None = None,
    reward: int | float | None = None,
    receipt_path: Path | None = None,
    harbor_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract the same resource and behavior metrics from GT-off or GT-on."""
    payload = _load_json(path)
    messages = payload.get("messages") or []
    tool_results: dict[str, list[int | None]] = {}
    for message in messages:
        if message.get("role") == "tool":
            tool_results.setdefault(str(message.get("tool_call_id") or ""), []).append(
                _returncode(message)
            )
    tool_result_cursors: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    command_counts: Counter[str] = Counter()
    action_rows: list[dict[str, Any]] = []
    input_tokens = output_tokens = cache_tokens = cache_miss_tokens = 0
    provider_cost = 0.0
    context_chars_sent = 0
    running_context_chars = 0
    max_actions_per_response = 0
    first: dict[str, int | None] = {
        name: None for name in ("search", "read", "edit", "check", "submit")
    }

    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") != "assistant":
            running_context_chars += len(content)
            continue
        counts["assistant_steps"] += 1
        context_chars_sent += running_context_chars
        extra = message.get("extra") or {}
        usage = (extra.get("response") or {}).get("usage") or {}
        input_tokens += int(usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("completion_tokens") or 0)
        hit = int(
            usage.get("prompt_cache_hit_tokens")
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or 0
        )
        cache_tokens += hit
        cache_miss_tokens += int(usage.get("prompt_cache_miss_tokens") or 0)
        provider_cost += float(extra.get("cost") or 0.0)
        counts["api_calls"] += 1
        actions = extra.get("actions") or []
        if not actions:
            counts["no_action_assistant_steps"] += 1
        else:
            counts["responses_with_actions"] += 1
            if len(actions) == 1:
                counts["single_action_responses"] += 1
            else:
                counts["multi_action_responses"] += 1
            max_actions_per_response = max(max_actions_per_response, len(actions))
        reasoning = str(message.get("reasoning_content") or content)
        for action in actions:
            counts["actions"] += 1
            command = str(action.get("command") or action.get("cmd") or "")
            normalized = normalize_command(command)
            category = _category(normalized)
            counts[f"{category}_actions"] += 1
            if category in first and first[category] is None:
                first[category] = counts["actions"]
            command_counts[normalized] += 1
            if command_counts[normalized] > 1:
                counts["repeated_commands"] += 1
            tool_id = str(action.get("tool_call_id") or "")
            cursor = tool_result_cursors[tool_id]
            candidates = tool_results.get(tool_id) or []
            returncode = candidates[cursor] if cursor < len(candidates) else None
            tool_result_cursors[tool_id] += 1
            if returncode is not None:
                counts["successful_actions" if returncode == 0 else "failed_actions"] += 1
            action_rows.append(
                {
                    "index": counts["actions"],
                    "command": command,
                    "reasoning": reasoning,
                    "returncode": returncode,
                }
            )
        running_context_chars += len(content)

    if cache_miss_tokens == 0:
        cache_miss_tokens = max(0, input_tokens - cache_tokens)
    total_tokens = input_tokens + output_tokens
    normalized_cost = normalized_token_cost(cache_miss_tokens, cache_tokens, output_tokens)
    exit_status = str((payload.get("info") or {}).get("exit_status") or "")
    exception_type = str(
        ((harbor_result or {}).get("exception_info") or {}).get("exception_type") or ""
    )
    censored_reason = exception_type if exception_type in _CENSORED_HARBOR_EXCEPTIONS else ""
    if not censored_reason and exit_status in _CENSORED:
        censored_reason = exit_status
    agent_execution = (harbor_result or {}).get("agent_execution") or {}
    agent_wall_time = _elapsed_seconds(
        agent_execution.get("started_at"), agent_execution.get("finished_at")
    )
    trial_wall_time = _elapsed_seconds(
        (harbor_result or {}).get("started_at"), (harbor_result or {}).get("finished_at")
    )
    official_solved = None if reward is None else bool(reward)
    uncensored_resolved = (
        None if official_solved is None else official_solved and not bool(censored_reason)
    )
    result: dict[str, Any] = {
        "task": task or path.name.removesuffix("_trajectory.json"),
        "reward": reward,
        # ``reward`` is the official verifier signal.  A rewarded process that
        # Harbor interrupted is a useful salvage witness, but it is not a
        # completed solve and must never enter solve-preservation gates as one.
        "official_solved": official_solved,
        "uncensored_resolved": uncensored_resolved,
        "solved": uncensored_resolved,
        "exit_status": exit_status,
        "solver_exhausted": exit_status in _SOLVER_EXHAUSTED,
        "censored": bool(censored_reason),
        "censored_reason": censored_reason,
        "harbor_exception_type": exception_type,
        "agent_wall_time_seconds": agent_wall_time,
        "trial_wall_time_seconds": trial_wall_time,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_tokens": cache_tokens,
        "uncached_input_tokens": cache_miss_tokens,
        "total_tokens": total_tokens,
        "prompt_cache_hit_rate": round(cache_tokens / input_tokens, 6) if input_tokens else 0.0,
        "provider_cost_usd": provider_cost,
        "normalized_cost_usd": normalized_cost,
        "normalized_pricing": "deepseek-v4-flash-frozen-2026",
        "context_chars_sent": context_chars_sent,
        "model_output_chars": sum(
            len(str(message.get("content") or ""))
            + len(str(message.get("reasoning_content") or ""))
            for message in messages
            if message.get("role") == "assistant"
        ),
        **counts,
        "steps_to_first_search": first["search"],
        "steps_to_first_read": first["read"],
        "steps_to_first_edit": first["edit"],
        "steps_to_first_check": first["check"],
        "steps_to_submit": first["submit"],
        "max_actions_per_response": max_actions_per_response,
    }
    result["api_calls"] = max(
        result.get("api_calls", 0),
        int(((payload.get("info") or {}).get("model_stats") or {}).get("api_calls") or 0),
    )
    result["actions_per_api_call"] = (
        round(result.get("actions", 0) / result["api_calls"], 6)
        if result["api_calls"]
        else 0.0
    )
    result["wasted_action_proxy"] = (
        result.get("failed_actions", 0)
        + result.get("repeated_commands", 0)
        + result.get("no_action_assistant_steps", 0)
    )
    result["effective_actions"] = result.get("actions", 0)
    for key in (
        "api_calls",
        "assistant_steps",
        "actions",
        "successful_actions",
        "failed_actions",
        "search_actions",
        "read_actions",
        "edit_actions",
        "check_actions",
        "submit_actions",
        "other_actions",
        "repeated_commands",
        "no_action_assistant_steps",
        "responses_with_actions",
        "single_action_responses",
        "multi_action_responses",
    ):
        result.setdefault(key, 0)
    if receipt_path and receipt_path.exists():
        receipt = _load_json(receipt_path)
        receipt_metrics = receipt.get("metrics") or {}
        repository_intelligence = receipt.get("repository_intelligence") or {}
        feature_summary = receipt.get("features") or {}
        call_contexts = receipt.get("model_call_contexts") or []
        validation_log = feature_summary.get("validation_log") or []
        if validation_log:
            # Unified classification is authoritative when present; never
            # reparse commands in a way that can disagree with the runtime.
            result["check_actions"] = sum(1 for row in validation_log if row.get("is_validation"))
            result["recognized_validation_actions"] = sum(
                1 for row in validation_log if row.get("command_class") == "recognized_validation"
            )
            result["declared_validation_actions"] = sum(
                1 for row in validation_log if row.get("validation_authority") == "declared"
            )
            result["standard_runner_validation_actions"] = sum(
                1 for row in validation_log if row.get("validation_authority") == "standard_runner"
            )
            result["custom_probe_validation_actions"] = sum(
                1 for row in validation_log if row.get("validation_authority") == "custom_probe"
            )
        result.update(_feature_funnel(receipt, action_rows))
        result.update(_feature_applicability_metrics(feature_summary))
        result.update(_lifecycle_metrics(feature_summary))
        result.update(
            {
                "guidance_events": int(feature_summary.get("guidance_events") or 0),
                "guidance_chars": int(feature_summary.get("guidance_chars") or 0),
                "guidance_candidates": int(feature_summary.get("guidance_candidates") or 0),
                "required_check_claims_without_declared_id": int(
                    feature_summary.get("required_check_claims_without_declared_id") or 0
                ),
                "redundant_provider_payloads": int(
                    feature_summary.get("redundant_provider_payloads") or 0
                ),
                "feature_receipts": sum(
                    int(value) for value in (feature_summary.get("produced_counts") or {}).values()
                ),
                "lifecycle": feature_summary.get("lifecycle") or {},
                "runtime_advisory_context_chars": sum(
                    int(item.get("runtime_advisory_chars") or 0) for item in call_contexts
                ),
                "gt_context_chars_added": sum(
                    int(item.get("runtime_advisory_chars") or 0) for item in call_contexts
                ),
                "preemptive_retrieval_chars_added": sum(
                    int(item.get("preemptive_retrieval_chars") or 0)
                    for item in call_contexts
                ),
                "context_state_frame_chars_added": sum(
                    int((item.get("context_compiler") or {}).get("active_state_chars") or 0)
                    for item in call_contexts
                ),
                "context_frontier_chars_added": sum(
                    int(item.get("context_frontier_chars") or 0) for item in call_contexts
                ),
                "progress_frame_chars_added": sum(
                    int(item.get("progress_frame_chars") or 0) for item in call_contexts
                ),
                "stock_context_chars_from_receipt": sum(
                    int(item.get("stock_context_chars") or 0) for item in call_contexts
                ),
                "max_context_chars_from_receipt": max(
                    (int(item.get("context_chars") or 0) for item in call_contexts), default=0
                ),
                "stock_provider_chars_sent": sum(
                    int(item.get("stock_provider_chars") or 0) for item in call_contexts
                ),
                "feature_guidance_chars_sent": sum(
                    int(item.get("feature_guidance_chars") or 0) for item in call_contexts
                ),
                "certified_graph_chars_sent": sum(
                    int(item.get("certified_graph_chars") or 0) for item in call_contexts
                ),
                "provider_compaction_removed_chars": sum(
                    int(item.get("compaction_removed_chars") or 0) for item in call_contexts
                ),
                "provider_compaction_receipt_chars": sum(
                    int(item.get("compaction_receipt_chars") or 0) for item in call_contexts
                ),
                "final_provider_chars_sent": sum(
                    int(item.get("final_provider_chars") or 0) for item in call_contexts
                ),
                "provider_changed_message_count": sum(
                    len(item.get("provider_changed_message_indices") or ())
                    for item in call_contexts
                ),
                "provider_view_changed_calls": sum(
                    bool(item.get("provider_view_changed")) for item in call_contexts
                ),
                "provider_exact_parity_calls": sum(
                    not bool(item.get("provider_view_changed")) for item in call_contexts
                ),
                "certified_evidence_changed_calls": sum(
                    "certified_evidence" in str(item.get("provider_change_reason") or "")
                    for item in call_contexts
                ),
                "provider_budget_compaction_changed_calls": sum(
                    "provider_budget_compaction"
                    in str(item.get("provider_change_reason") or "")
                    for item in call_contexts
                ),
            }
        )
        for key in (
            "gt_context_chars_added",
            "preemptive_retrieval_chars_added",
            "context_state_frame_chars_added",
            "progress_frame_chars_added",
            "newly_inserted_context_chars",
            "represented_context_facts",
            "stock_context_chars_sent",
            "effects_produced",
            "effects_applied",
            "state_mutations",
            "payload_deliveries",
            "timely_payload_deliveries",
            "late_payload_deliveries",
            "predictive_payload_deliveries",
            "first_eligible_delivery_rate",
            "predecided_actions_after_evidence",
            "submit_risks",
            "submit_holds",
            "batch_interrupts",
            "interrupted_actions",
            "context_compiler_calls",
            "context_fact_candidates",
            "context_facts_selected",
            "context_facts_represented",
            "context_facts_controller_only",
            "context_facts_omitted",
            "context_facts_accounted",
            "context_frontier_calls",
            "context_frontier_candidates",
            "context_frontier_accounted",
            "context_frontier_deliveries",
            "context_frontier_facts_delivered",
            "context_frontier_chars_added",
            "provider_requests_prepared",
            "model_query_invocations",
            "provider_responses_received",
            "provider_requests_not_sent",
            "provider_evidence_events",
            "provider_evidence_dispatched",
            "provider_evidence_prepared_not_sent",
            "context_frontier_zero_tasks",
            "context_frontier_duplicate_facts",
            "context_frontier_duplicate_claims",
            "context_frontier_candidate_languages",
            "context_frontier_delivered_languages",
            "repository_intelligence_valid",
            "repository_graph_degraded_fallback",
            "repository_graph_schema_valid",
            "repository_graph_nodes",
            "repository_graph_edges",
            "repository_source_files",
            "repository_indexable_files",
            "repository_ambiguous_source_files",
            "repository_unsupported_source_files",
            "repository_resolved_languages",
            "repository_resolution_reason_kinds",
            "repository_parser_failures",
            "repository_refreshes",
            "repository_mirror_transfer_ms",
            "repository_mirror_files",
            "repository_mirror_bytes",
            "repository_mirror_selected_source_files",
            "repository_mirror_selected_metadata_files",
            "repository_mirror_excluded_artifacts",
            "repository_mirror_excluded_deliverables",
            "repository_mirror_excluded_oversize",
            "repository_mirror_excluded_budget",
            "repository_index_refresh_ms",
            "repository_full_refreshes",
            "repository_incremental_refreshes",
            "repository_revision_cache_hits",
            "repository_action_queries",
            "repository_action_query_cache_hits",
            "context_exact_duplicate_chars_removed",
            "context_unique_reasoning_chars_removed",
            "context_selected_facts_action_measurable",
            "context_selected_facts_action_aligned",
            "context_compiler_effects_considered",
            "context_compiler_effects_no_eligible_call",
            "context_compiler_effects_unaccounted",
            "preflight_known_segment_operations",
            "preflight_unknown_segment_operations",
            "preflight_typed_targets",
            "provider_request_chars_sent",
            "provider_requests_hashed",
            "provider_request_hash_coverage",
            "provider_request_budget_failures",
            "provider_request_min_headroom_tokens",
            "provider_stable_prefix_chars",
            "provider_stable_prefix_ratio_mean",
            "stock_provider_chars_sent",
            "feature_guidance_chars_sent",
            "certified_graph_chars_sent",
            "provider_compaction_removed_chars",
            "provider_compaction_receipt_chars",
            "final_provider_chars_sent",
            "provider_changed_message_count",
            "provider_view_changed_calls",
            "provider_exact_parity_calls",
            "certified_evidence_changed_calls",
            "provider_budget_compaction_changed_calls",
            "certified_opportunity_evaluations",
            "certified_opportunities",
            "certified_opportunity_abstentions",
            "heuristic_opportunity_abstentions",
            "certified_provider_deliveries",
            "certified_provider_behavior_measurable",
            "certified_provider_anchor_followed",
            "certified_controller_actuations",
            "context_state_frame_calls",
            "context_provider_view_changed_calls",
            "context_bounded_observations",
            "context_bounded_observation_applications",
            "context_bounded_observation_chars_removed",
            "context_duplicate_turns_represented",
            "context_old_tool_results_cleared",
            "context_stale_reads_elided",
            "context_recap_receipts",
            "context_recap_chars_added",
            "context_recap_fallbacks",
            "context_compactions",
            "context_compaction_deferral_count",
            "context_chars_elided",
            "validation_attributed_results",
            "validation_unattributed_intents",
            "completion_predicate_checks",
            "completion_certificate_evaluations",
            "auto_submit_attempts",
            "auto_submits",
            "progress_transitions",
            "task_progress_changes",
            "progress_observations",
            "progress_distinct_attempts",
            "progress_distinct_observations",
            "progress_observation_gains",
            "progress_task_gains",
            "progress_same_state_updates_suppressed",
            "failed_read_anchors_not_consumed",
            "valid_nonzero_observations",
            "declared_validator_proposals",
            "declared_validators_with_redirection",
            "declared_validators_preserved_with_redirection",
            "adaptive_validation_timeouts",
            "default_validation_timeouts",
            "model_action_timeouts",
            "effective_actions",
            "effective_task_actions",
            "actual_environment_execs",
            "controller_environment_execs",
            "controller_cached_reads",
            "sensor_environment_execs",
        ):
            result[key] = receipt_metrics.get(key, result.get(key, 0))
        if "model_query_invocations" in receipt_metrics:
            result["api_calls"] = max(
                int(result.get("api_calls") or 0),
                int(receipt_metrics.get("model_query_invocations") or 0),
            )
        result["total_gt_context_chars_added"] = (
            int(result.get("gt_context_chars_added") or 0)
            + int(result.get("preemptive_retrieval_chars_added") or 0)
            + int(result.get("context_frontier_chars_added") or 0)
            + int(result.get("context_state_frame_chars_added") or 0)
            + int(result.get("progress_frame_chars_added") or 0)
        )
        result["repository_intelligence_status"] = str(
            repository_intelligence.get("status") or "unreported"
        )
        result["context_frontier_coverage"] = str(
            receipt_metrics.get("context_frontier_coverage") or "unreported"
        )
        result["context_bounded_observation_operation_counts"] = dict(
            receipt_metrics.get("context_bounded_observation_operation_counts") or {}
        )
        result["repository_intelligence_required"] = bool(repository_intelligence.get("required"))
        result["repository_intelligence_applicability"] = str(
            repository_intelligence.get("applicability") or "unreported"
        )
        result["repository_intelligence_denominator_excluded"] = bool(
            repository_intelligence.get("denominator_excluded")
        )
        result["repository_intelligence_transient_failures"] = list(
            repository_intelligence.get("transient_failures") or ()
        )
        result["repository_intelligence_failures"] = list(
            repository_intelligence.get("failures") or ()
        )
    result["actions_per_api_call"] = (
        round(int(result.get("actions") or 0) / int(result["api_calls"]), 6)
        if int(result.get("api_calls") or 0)
        else 0.0
    )
    return result


def compare_arms(
    baseline: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Apply outcome preservation, aggregate efficiency, and outlier bounds."""
    tasks = sorted(set(baseline) & set(treatment))
    rows: dict[str, Any] = {}
    solve_regressions: list[str] = []
    censored_treatment: list[str] = []
    invalid_treatments: list[str] = []
    pareto_failures: list[str] = []
    per_task_bound_failures: list[str] = []
    comparable_solved: list[str] = []
    outcomes_complete = True
    aggregate_values: dict[str, list[float]] = {
        metric: [] for metric in (*PRIMARY_RESOURCES, *DIAGNOSTIC_METRICS)
    }
    all_task_aggregate_values: dict[str, list[float]] = {
        metric: [] for metric in (*PRIMARY_RESOURCES, *DIAGNOSTIC_METRICS)
    }
    controller_aggregate_values: dict[str, list[float]] = {
        metric: [] for metric in CONTROLLER_RESOURCES
    }
    for task in tasks:
        before, after = baseline[task], treatment[task]
        b_solved, a_solved = before.get("solved"), after.get("solved")
        if b_solved is None or a_solved is None:
            outcomes_complete = False
        if b_solved is True and a_solved is not True:
            solve_regressions.append(task)
        if after.get("censored"):
            censored_treatment.append(task)
        if (
            after.get("repository_intelligence_required")
            and not after.get("repository_intelligence_denominator_excluded")
            and (
                after.get("repository_intelligence_status") != "passed"
                or not bool(after.get("repository_intelligence_valid"))
            )
        ):
            invalid_treatments.append(task)

        def resource_value(row: dict[str, Any], metric: str) -> float:
            if metric == "effective_actions" and row.get(metric) is None:
                return float(row.get("actions", 0) or 0)
            return float(row.get(metric, 0) or 0)

        deltas = {
            metric: resource_value(after, metric) - resource_value(before, metric)
            for metric in PRIMARY_RESOURCES
        }
        common_uncensored_solve = bool(
            b_solved is True
            and a_solved is True
            and not before.get("censored")
            and not after.get("censored")
        )
        controller_deltas = {
            metric: resource_value(after, metric) - resource_value(before, metric)
            for metric in CONTROLLER_RESOURCES
        }
        diagnostic_deltas: dict[str, float | None] = {}
        for metric in DIAGNOSTIC_METRICS:
            before_value = before.get(metric)
            after_value = after.get(metric)
            if before_value is None or after_value is None:
                diagnostic_deltas[metric] = None
                continue
            delta = float(after_value) - float(before_value)
            diagnostic_deltas[metric] = delta
            all_task_aggregate_values[metric].append(delta)
            if common_uncensored_solve:
                aggregate_values[metric].append(delta)
        for metric, delta in deltas.items():
            all_task_aggregate_values[metric].append(delta)
            if common_uncensored_solve:
                aggregate_values[metric].append(delta)
        if common_uncensored_solve:
            for metric, delta in controller_deltas.items():
                controller_aggregate_values[metric].append(delta)
        pareto = None
        exceeded_bounds: list[str] = []
        if common_uncensored_solve:
            comparable_solved.append(task)
            pareto = all(delta <= 0 for delta in deltas.values()) and any(
                delta < 0 for delta in deltas.values()
            )
            if not pareto:
                pareto_failures.append(task)
            for metric in ("api_calls", "actions"):
                baseline_value = resource_value(before, metric)
                if deltas[metric] > max(3.0, baseline_value * 0.20):
                    exceeded_bounds.append(metric)
            baseline_cost = resource_value(before, "normalized_cost_usd")
            if deltas["normalized_cost_usd"] > max(0.01, baseline_cost * 0.25):
                exceeded_bounds.append("normalized_cost_usd")
            before_wall = before.get("agent_wall_time_seconds")
            after_wall = after.get("agent_wall_time_seconds")
            if before_wall is not None and after_wall is not None:
                wall_delta = float(after_wall) - float(before_wall)
                if wall_delta > max(60.0, float(before_wall) * 0.20):
                    exceeded_bounds.append("agent_wall_time_seconds")
            if len(exceeded_bounds) >= 2:
                per_task_bound_failures.append(task)
        rows[task] = {
            "baseline_official_solved": before.get("official_solved", before.get("solved")),
            "treatment_official_solved": after.get("official_solved", after.get("solved")),
            "baseline_solved": b_solved,
            "treatment_solved": a_solved,
            "baseline_censored": bool(before.get("censored")),
            "baseline_censored_reason": str(before.get("censored_reason") or ""),
            "treatment_censored": bool(after.get("censored")),
            "treatment_censored_reason": str(after.get("censored_reason") or ""),
            "deltas": deltas,
            "diagnostic_deltas": diagnostic_deltas,
            "controller_deltas": controller_deltas,
            "strict_pareto": pareto,
            "exceeded_resource_bounds": exceeded_bounds,
        }
    aggregate_deltas = {
        metric: sum(values) if len(values) == len(comparable_solved) else None
        for metric, values in aggregate_values.items()
    }
    all_task_aggregate_deltas = {
        metric: sum(values) if len(values) == len(tasks) else None
        for metric, values in all_task_aggregate_values.items()
    }
    controller_aggregate_deltas = {
        metric: sum(values) if len(values) == len(comparable_solved) else None
        for metric, values in controller_aggregate_values.items()
    }
    aggregate_gate_failures = [
        metric
        for metric in (
            "total_tokens",
            "api_calls",
            "assistant_steps",
            "normalized_cost_usd",
        )
        if aggregate_deltas.get(metric) is None or aggregate_deltas[metric] >= 0
    ]
    if aggregate_deltas.get("actions") is None or aggregate_deltas["actions"] > 0:
        aggregate_gate_failures.append("actions")
    effective_action_delta = controller_aggregate_deltas.get("effective_actions")
    if effective_action_delta is None or effective_action_delta > 0:
        aggregate_gate_failures.append("effective_actions")
    wall_delta = aggregate_deltas.get("agent_wall_time_seconds")
    if wall_delta is not None and wall_delta > 0:
        aggregate_gate_failures.append("agent_wall_time_seconds")
    gate_passed = (
        bool(tasks)
        and outcomes_complete
        and not (
            solve_regressions
            or censored_treatment
            or invalid_treatments
            or per_task_bound_failures
            or aggregate_gate_failures
        )
        and bool(comparable_solved)
    )
    return {
        "tasks": rows,
        "task_count": len(tasks),
        "outcomes": {
            "baseline_official_resolved": sum(
                baseline[task].get("official_solved", baseline[task].get("solved")) is True
                for task in tasks
            ),
            "treatment_official_resolved": sum(
                treatment[task].get("official_solved", treatment[task].get("solved")) is True
                for task in tasks
            ),
            "baseline_uncensored_resolved": sum(
                baseline[task].get("uncensored_resolved", baseline[task].get("solved")) is True
                for task in tasks
            ),
            "treatment_uncensored_resolved": sum(
                treatment[task].get("uncensored_resolved", treatment[task].get("solved")) is True
                for task in tasks
            ),
        },
        "outcomes_complete": outcomes_complete,
        "comparable_solved": comparable_solved,
        "solve_regressions": solve_regressions,
        "censored_treatment": censored_treatment,
        "invalid_treatments": invalid_treatments,
        "pareto_failures": pareto_failures,
        "per_task_bound_failures": per_task_bound_failures,
        "aggregate_gate_failures": aggregate_gate_failures,
        "aggregate_deltas": aggregate_deltas,
        "all_task_aggregate_deltas": all_task_aggregate_deltas,
        "controller_aggregate_deltas": controller_aggregate_deltas,
        "gate_passed": gate_passed,
    }


def render_delta_markdown(name: str, comparison: dict[str, Any]) -> str:
    lines = [
        f"# Deep delta: {name}",
        "",
        f"Gate: **{'PASS' if comparison['gate_passed'] else 'FAIL'}**",
        "",
        "Solve regressions: " + (", ".join(comparison["solve_regressions"]) or "none"),
        "Treatment censors: " + (", ".join(comparison["censored_treatment"]) or "none"),
        "Invalid repository-intelligence treatments: "
        + (", ".join(comparison.get("invalid_treatments") or ()) or "none"),
        "Strict Pareto failures: " + (", ".join(comparison["pareto_failures"]) or "none"),
        "Per-task bound failures: " + (", ".join(comparison["per_task_bound_failures"]) or "none"),
        "Aggregate gate failures: " + (", ".join(comparison["aggregate_gate_failures"]) or "none"),
        "",
        "Delta is treatment minus baseline; positive resource deltas are regressions.",
        "",
        "| task | outcome | tokens | calls | actions | steps | cost | agent sec "
        "| censor | Pareto |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for task, row in comparison["tasks"].items():
        delta = row["deltas"]
        agent_delta = row["diagnostic_deltas"].get("agent_wall_time_seconds")
        agent_value = "n/a" if agent_delta is None else f"{agent_delta:+,.0f}"
        lines.append(
            f"| {task} | {row['baseline_solved']}→{row['treatment_solved']} "
            f"| {delta['total_tokens']:+,.0f} | {delta['api_calls']:+,.0f} "
            f"| {delta['actions']:+,.0f} | {delta['assistant_steps']:+,.0f} "
            f"| ${delta['normalized_cost_usd']:+.6f} "
            f"| {agent_value} "
            f"| {row['treatment_censored_reason'] or 'no'} "
            f"| {row['strict_pareto']} |"
        )
    lines.extend(
        [
            "",
            "## Deep behavior/context deltas",
            "",
            "Milestone deltas are action indices, not resource gates. "
            "Missing paired values are `n/a`.",
            "",
            "| task | uncached | context chars | failed | wasted | to submit | GT guidance "
            "| GT state "
            "| timely payloads | late payloads | predictive payloads |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    def value(row: dict[str, Any], metric: str) -> str:
        item = row["diagnostic_deltas"].get(metric)
        return "n/a" if item is None else f"{item:+,.0f}"

    for task, row in comparison["tasks"].items():
        lines.append(
            f"| {task} | {value(row, 'uncached_input_tokens')} "
            f"| {value(row, 'context_chars_sent')} | {value(row, 'failed_actions')} "
            f"| {value(row, 'wasted_action_proxy')} | {value(row, 'steps_to_submit')} "
            f"| {value(row, 'gt_context_chars_added')} "
            f"| {value(row, 'context_state_frame_chars_added')} "
            f"| {value(row, 'timely_payload_deliveries')} "
            f"| {value(row, 'late_payload_deliveries')} "
            f"| {value(row, 'predictive_payload_deliveries')} |"
        )
    return "\n".join(lines) + "\n"
