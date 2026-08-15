#!/usr/bin/env python3
"""Executable trigger/payload census for the host-owned 17-feature runtime.

This is a producer test, not a claim that every real task triggers every
feature.  It exercises each boundary deliberately and rejects any delivery
whose payload is empty, stale, or attached to the wrong lifecycle boundary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Support the documented direct invocation as well as ``python -m``.  In the
# former case Python puts ``scripts/`` rather than the repository root on
# sys.path, so the host-owned ``gt_engine`` package would otherwise be absent.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.central_runtime import (
    CENTRAL_FEATURE_IDS,
    CentralFeatureRuntime,
    WorkspaceTransition,
    feature_payload_grounded,
    feature_payload_valid,
)
from gt_engine.context_frontier import (
    FrontierDisposition,
    compile_incremental_frontier,
)
from gt_engine.experiment import ExperimentArm, crossover_arm
from gt_engine.provider_view import build_provider_view
from scripts.verify_gt_index_runtime import verify as verify_repository_substrate

EXPECTED_TIMING = {
    "obligations": ("task_start", (0,)),
    "localization": (("task_start", "search_result"), (0, 1)),
    "GT_LOC_RESLOT": (("task_start", "search_result"), (0, 1)),
    "def_partition": ("task_start", (0,)),
    "caller_contract": ("task_start", (0,)),
    "newfile_precedent": ("edit_result", (2,)),
    "GT_CHANGE_SURFACE": ("edit_result", (2,)),
    "GT_PATCH_DELTA": ("edit_result", (2,)),
    "signature_delta": ("edit_result", (2,)),
    # Delivery of signature_delta depends on its consumer being selected, not
    # on the producer alone; the one-message selection path currently discards
    # it in favor of the higher-priority syntax_result.  Consumer delivery is
    # proved by ALL_17_CONSUMERS_PROVEN, not by this producer timing row.
    "syntax_result": ("edit_result", (2,)),
    "GT_EDIT_CHECK": ("edit_result", (2,)),
    "covering_red": ("test_result", (3, 4)),
    "GT_HYPOTHESIS": ("test_result", (3, 4)),
    "recovery": ("test_result", (4,)),
    "submit_refusal": ("test_result", (3,)),
    "GT_SS_SUBMIT_RED": ("test_result", (3,)),
    "GT_CERT_DELIVERY": ("submit", (5,)),
}


def _expected_model_visible(row: dict) -> bool:
    feature_id = row["feature_id"]
    payload = row.get("payload") or {}
    if row["decision"] != "DELIVERED":
        return False
    if feature_id == "signature_delta":
        return bool(payload.get("callers"))
    if feature_id == "GT_EDIT_CHECK":
        return payload.get("intervention") == "validation_debt"
    return feature_id in {
        "covering_red",
        "recovery",
        "submit_refusal",
        "syntax_result",
    }


def audit_timing(summary: dict) -> dict:
    """Judge payload, boundary, chronology, and visibility for every feature."""
    receipts = summary["receipts"]
    audit = {}
    for feature_id in CENTRAL_FEATURE_IDS:
        rows = [row for row in receipts if row["feature_id"] == feature_id]
        expected_boundary, expected_actions = EXPECTED_TIMING[feature_id]
        boundaries = (
            set(expected_boundary) if isinstance(expected_boundary, tuple) else {expected_boundary}
        )
        actual_actions = tuple(row["action"] for row in rows)
        payloads_valid = all(
            feature_payload_valid(
                feature_id,
                row["payload"],
                boundary=row["boundary"],
                revision=row["revision"],
                fresh=row["fresh"],
            )
            for row in rows
        )
        boundaries_valid = bool(rows) and all(row["boundary"] in boundaries for row in rows)
        actions_valid = actual_actions == expected_actions
        visibility_valid = all(
            row["model_visible"] == _expected_model_visible(row)
            or (
                not row["model_visible"]
                and row.get("delivery_status") == "suppressed"
                and row.get("delivery_reason")
                in {
                    "semantic_duplicate",
                    "not_selected_first_eligible_request",
                    "change_surface_self_echo",
                    "task_start_advisory_disabled",
                }
            )
            for row in rows
        )
        audit[feature_id] = {
            "valid": payloads_valid and boundaries_valid and actions_valid and visibility_valid,
            "expected_boundary": expected_boundary,
            "actual_boundaries": [row["boundary"] for row in rows],
            "expected_actions": list(expected_actions),
            "actual_actions": list(actual_actions),
            "payloads_valid": payloads_valid,
            "visibility_valid": visibility_valid,
        }
    audit["_global"] = {
        "receipt_action_order_valid": [row["action"] for row in receipts]
        == sorted(row["action"] for row in receipts),
        "recovery_after_repeat": min(
            row["action"] for row in receipts if row["feature_id"] == "recovery"
        )
        > min(row["action"] for row in receipts if row["feature_id"] == "covering_red"),
        "submit_is_terminal_boundary": max(row["action"] for row in receipts) == 5,
    }
    audit["_global"]["valid"] = all(audit["_global"].values())
    return audit


def census() -> dict:
    runtime = CentralFeatureRuntime(model_visible=True)
    decision_windows = []
    effect_windows = []

    def deliver_next(after_action: int) -> None:
        effects = runtime.consume_effects(action_id=after_action, call=after_action)
        for effect in effects:
            effect_windows.append(effect.as_dict())
        feedback = runtime.model_feedback(deferred=True)
        if feedback:
            metadata = runtime.confirm_prepared_guidance() or {}
            evidence_action = int(metadata.get("evidence_action") or 0)
            decision_windows.append(
                {
                    "feature_id": metadata.get("feature_id"),
                    "contributing_features": metadata.get("contributing_features", []),
                    "evidence_action": evidence_action,
                    "prepared_after_action": after_action,
                    "delivered_before_next_decision": True,
                    "not_predictive": evidence_action <= after_action,
                    "not_late": evidence_action == after_action,
                    "chars": len(feedback),
                }
            )
        active_state = {
            **runtime.progress_ledger(),
            "source_revision": runtime.progress_ledger().get("source_revision") or "r0",
            "workspace_revision": f"r{after_action}",
        }
        _view, compiler = build_provider_view(
            [{"role": "user", "content": "provider-free census"}],
            active_state=active_state,
            trigger_chars=10**18,
            target_chars=10**18,
        )
        runtime.record_context_compiler_call(
            call=after_action + 1,
            request_payload_sha256=f"provider-free-call-{after_action + 1}",
            fact_accounting=compiler.fact_accounting,
        )

    runtime.begin_task(
        "Implement the requested change and run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
    )
    runtime.register_structural_evidence(
        source_revision="r0",
        anchors=(
            {"path": "bottle.py", "line": 10, "symbol": "Bottle"},
            {"path": "tests/test_bottle.py", "line": 20, "symbol": "test_bottle"},
        ),
        definitions=(
            {
                "path": "bottle.py",
                "line": 10,
                "symbol": "Bottle",
                "semantics": "graph_definition",
            },
        ),
        references=(
            {
                "path": "tests/test_bottle.py",
                "line": 20,
                "symbol": "Bottle",
                "semantics": "graph_call_reference",
            },
        ),
        callers=(
            {
                "caller_path": "tests/test_bottle.py",
                "caller_line": 20,
                "caller_symbol": "test_bottle",
                "target_path": "bottle.py",
                "target_symbol": "Bottle",
                "semantics": "graph_recorded",
            },
        ),
        graph_revision="graph-r0",
    )
    deliver_next(0)
    runtime.observe_action(
        action_id=1,
        command="rg -n 'Bottle|caller' .",
        output=(
            "bottle.py:10:class Bottle\n"
            "tests/test_bottle.py:20:caller references Bottle; existing registry pattern\n"
        ),
        returncode=0,
        transition=WorkspaceTransition(1, "search", "r0", "r0"),
        revision="r0",
    )
    deliver_next(1)
    runtime.observe_action(
        action_id=2,
        command="sed -i 's/def f(/def f(x:/' app.py",
        output="def f(x: -> syntax error",
        returncode=0,
        transition=WorkspaceTransition(
            2,
            "edit",
            "r0",
            "r1",
            created=("new_module.py",),
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={
                "app.py": "def f(x, y):\n    return x + y\n",
                "new_module.py": "def helper():\n    pass\n",
            },
        ),
        revision="r1",
    )
    runtime.record_syntax(
        action_id=2,
        revision="r1",
        failed=True,
        reason="fixture_syntax_failure",
        path="app.py",
        command="python3 -m py_compile app.py",
        returncode=1,
        diagnostic="SyntaxError: invalid syntax",
    )
    deliver_next(2)
    for action_id in (3, 4):
        runtime.observe_action(
            action_id=action_id,
            command="pytest -q",
            output="1 failed: Error",
            returncode=1,
            transition=WorkspaceTransition(action_id, "test", "r1", "r1"),
            revision="r1",
        )
        deliver_next(action_id)
    runtime.record_submit(
        action_id=5,
        revision="r1",
        refused=False,
        sensor_healthy=True,
        check_count=1,
        failing_checks=1,
    )
    deliver_next(5)
    summary = runtime.summary()
    summary["timing_audit"] = audit_timing(summary)
    summary["all_17_timing_valid"] = all(row["valid"] for row in summary["timing_audit"].values())
    summary["decision_window_audit"] = decision_windows
    summary["effect_window_audit"] = effect_windows
    summary["all_guidance_on_time"] = bool(decision_windows) and all(
        row["delivered_before_next_decision"] and row["not_predictive"] and row["not_late"]
        for row in decision_windows
    )
    summary["all_17_producers_proven"] = (
        summary["feature_count"] == 17
        and set(summary["feature_ids"]) == set(CENTRAL_FEATURE_IDS)
        and all(summary["produced_counts"][feature] >= 1 for feature in CENTRAL_FEATURE_IDS)
        and all(row["fresh"] and row["payload"].get("message") for row in summary["receipts"])
        and all(
            row["model_visible"] == _expected_model_visible(row)
            or (
                not row["model_visible"]
                and row.get("delivery_status") == "suppressed"
                and row.get("delivery_reason")
                in {
                    "semantic_duplicate",
                    "not_selected_first_eligible_request",
                    "change_surface_self_echo",
                    "task_start_advisory_disabled",
                }
            )
            for row in summary["receipts"]
        )
        and summary["all_17_timing_valid"]
        and summary["all_guidance_on_time"]
    )
    consumer_paths = summary["consumer_paths"]
    summary["all_17_consumers_proven"] = bool(consumer_paths) and set(consumer_paths) >= set(
        CENTRAL_FEATURE_IDS
    )
    effects = summary["effects"]
    # Effect timing is non-vacuous: an empty effect set is a failure, not a
    # pass.  Full timing fields arrive with the consumer registry (Phase 3).
    summary["all_effects_timing_valid"] = bool(effects) and all(
        bool(row.get("evidence_before_effect"))
        and bool(row.get("effect_before_next_action"))
        and bool(row.get("non_late"))
        and not bool(row.get("predictive"))
        for row in effects
    )
    summary["all_payloads_semantically_grounded"] = bool(summary["receipts"]) and all(
        not row["model_visible"] or feature_payload_grounded(row["feature_id"], row["payload"])
        for row in summary["receipts"]
    )
    summary["all_17_consumer_paths_proven"] = (
        summary["all_17_producers_proven"]
        and summary["all_17_consumers_proven"]
        and summary["all_effects_timing_valid"]
        and summary["all_payloads_semantically_grounded"]
        and {
            row["feature_id"]
            for row in summary["effect_applications"]
            if row["state_fields_changed"]
        }
        >= set(CENTRAL_FEATURE_IDS)
        and summary["action_metrics"]["submit_holds"] == 0
        and summary["action_metrics"]["batch_interrupts"] == 0
        and all(
            row["status"] != "unaccounted_bug" and not row.get("one_step_late", False)
            for row in summary["context_compiler_effect_accountability"]
        )
    )
    summary["all_effects_context_accounted"] = bool(
        summary["context_compiler_effect_accountability"]
    ) and all(
        row["status"] not in {"unaccounted_bug", "no_eligible_model_call"}
        and not row.get("one_step_late", False)
        for row in summary["context_compiler_effect_accountability"]
    )
    applied_features = {
        row["feature_id"] for row in summary["effect_applications"] if row["state_fields_changed"]
    }
    summary["all_17_triggers_proven"] = summary["all_17_producers_proven"]
    summary["all_17_payloads_concrete"] = summary["all_payloads_semantically_grounded"]
    summary["all_17_consumers_applied"] = applied_features >= set(CENTRAL_FEATURE_IDS)
    summary["all_visible_payloads_first_eligible"] = summary["all_guidance_on_time"]
    applicability = summary["feature_applicability"]
    summary["all_feature_opportunities_accounted"] = (
        summary["all_feature_opportunities_accounted"]
        and set(applicability) == set(CENTRAL_FEATURE_IDS)
        and all(row["status"] != "missed_trigger" for row in applicability.values())
    )
    summary["no_eligible_trigger_misses"] = all(
        row["status"] != "missed_trigger" for row in applicability.values()
    )
    summary["no_false_feature_fires"] = all(
        row["effect_id"]
        for row in summary["feature_opportunities"]
        if row["evidence_status"] == "eligible"
    ) and all(
        row["status"] != "fired_when_eligible" or row["eligible"] > 0
        for row in applicability.values()
    )
    summary["no_empty_localization_effects"] = all(
        bool(row["payload"].get("anchors"))
        for row in summary["receipts"]
        if row["feature_id"] == "localization"
    ) and all(
        bool(row["payload"].get("selected_anchors"))
        for row in summary["receipts"]
        if row["feature_id"] == "GT_LOC_RESLOT"
    )
    summary["no_unverified_callers"] = all(
        all(caller.get("semantics") == "graph_recorded" for caller in row["payload"]["callers"])
        for row in summary["receipts"]
        if row["feature_id"] == "caller_contract"
    )
    claims_by_id = {row["claim_id"]: row for row in summary["semantic_decisions"]["claims"]}
    summary["no_duplicate_frame_evidence"] = all(
        len(frame["claim_ids"]) == len(set(frame["claim_ids"]))
        and len([claims_by_id[item]["fact"] for item in frame["claim_ids"]])
        == len({claims_by_id[item]["fact"] for item in frame["claim_ids"]})
        for frame in summary["semantic_decisions"]["frames"]
    )
    summary["repository_substrate"] = verify_repository_substrate()
    summary["repository_substrate_proven"] = (
        summary["repository_substrate"].get("status") == "available"
        and int(summary["repository_substrate"].get("definition_count") or 0) >= 2
        and int(summary["repository_substrate"].get("call_count") or 0) >= 1
    )
    frontier = compile_incremental_frontier(
        {
            "status": "source_backed",
            "available": True,
            "substrate_ready": True,
            "index_current": True,
            "intelligence_valid": True,
            "source_revision": "r0",
            "graph_revision": "graph-r0",
            "anchors": (
                {
                    "path": "bottle.py",
                    "line": 10,
                    "symbol": "Bottle",
                    "confidence": 1.0,
                    "semantic_certainty": 1.0,
                    "retrieval_relevance": 1.0,
                },
            ),
            "definitions": (
                {
                    "path": "bottle.py",
                    "line": 10,
                    "symbol": "Bottle",
                    "signature": "class Bottle:",
                    "semantic_certainty": 1.0,
                    "retrieval_relevance": 1.0,
                },
            ),
            "references": (),
            "callers": (),
        },
        [{"role": "user", "content": "Implement Bottle."}],
        source_revision="r0",
    )
    summary["context_frontier"] = frontier.as_dict()
    summary["context_frontier_proven"] = (
        frontier.disposition is FrontierDisposition.SELECTED_FRONTIER
        and frontier.candidate_count == frontier.accounted_count == 1
        and bool(frontier.rendered)
        and "bottle.py:10" in frontier.rendered
        and frontier.opportunity is not None
        and frontier.opportunity.certified
    )
    stock_messages = [
        {"role": "system", "content": "stock system"},
        {"role": "user", "content": "stock task"},
    ]
    shielded_messages, shield_metrics = build_provider_view(
        stock_messages,
        active_state={"source_revision": "r0", "workspace_revision": "w0"},
        trigger_chars=1,
        target_chars=1,
        transform=False,
    )
    summary["provider_baseline_shield_proven"] = (
        shielded_messages == stock_messages
        and not shield_metrics.compacted
        and shield_metrics.input_chars == shield_metrics.output_chars
    )
    crossover = [crossover_arm("census-task", index, seed="census") for index in range(4)]
    summary["repeated_control_gate_proven"] = (
        crossover.count(ExperimentArm.OFF) == 2
        and crossover.count(ExperimentArm.CERTIFIED_FULL) == 2
    )
    summary["no_actions_blocked"] = (
        summary["action_metrics"]["submit_holds"] == 0
        and summary["action_metrics"]["batch_interrupts"] == 0
        and summary["action_metrics"]["interrupted_actions"] == 0
    )
    return summary


def main() -> int:
    result = census()
    print(json.dumps(result, indent=2, sort_keys=True))
    print(
        "ALL_17_PRODUCERS_PROVEN" if result["all_17_producers_proven"] else "PRODUCERS_NOT_PROVEN"
    )
    print(
        "ALL_17_CONSUMERS_PROVEN" if result["all_17_consumers_proven"] else "CONSUMERS_NOT_PROVEN"
    )
    print(
        "ALL_EFFECTS_TIMING_VALID"
        if result["all_effects_timing_valid"]
        else "EFFECTS_TIMING_INVALID"
    )
    print(
        "ALL_PAYLOADS_GROUNDED"
        if result["all_payloads_semantically_grounded"]
        else "PAYLOADS_NOT_GROUNDED"
    )
    print(
        "ALL_17_CONSUMER_PATHS_PROVEN"
        if result["all_17_consumer_paths_proven"]
        else "CONSUMER_PATHS_NOT_PROVEN"
    )
    print("ALL_17_TRIGGERS_PROVEN" if result["all_17_triggers_proven"] else "TRIGGERS_NOT_PROVEN")
    print(
        "ALL_17_PAYLOADS_CONCRETE"
        if result["all_17_payloads_concrete"]
        else "PAYLOADS_NOT_CONCRETE"
    )
    print(
        "ALL_17_CONSUMERS_APPLIED"
        if result["all_17_consumers_applied"]
        else "CONSUMERS_NOT_APPLIED"
    )
    print(
        "ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST"
        if result["all_visible_payloads_first_eligible"]
        else "VISIBLE_PAYLOAD_TIMING_INVALID"
    )
    print("NO_ACTIONS_BLOCKED" if result["no_actions_blocked"] else "ACTIONS_BLOCKED")
    print(
        "ALL_EFFECTS_CONTEXT_ACCOUNTED"
        if result["all_effects_context_accounted"]
        else "EFFECT_CONTEXT_NOT_ACCOUNTED"
    )
    print(
        "ALL_FEATURE_OPPORTUNITIES_ACCOUNTED"
        if result["all_feature_opportunities_accounted"]
        else "FEATURE_OPPORTUNITIES_UNACCOUNTED"
    )
    print(
        "NO_ELIGIBLE_TRIGGER_MISSES"
        if result["no_eligible_trigger_misses"]
        else "ELIGIBLE_TRIGGER_MISSES"
    )
    print("NO_FALSE_FEATURE_FIRES" if result["no_false_feature_fires"] else "FALSE_FEATURE_FIRES")
    print(
        "NO_EMPTY_LOCALIZATION_EFFECTS"
        if result["no_empty_localization_effects"]
        else "EMPTY_LOCALIZATION_EFFECTS"
    )
    print("NO_UNVERIFIED_CALLERS" if result["no_unverified_callers"] else "UNVERIFIED_CALLERS")
    print(
        "NO_DUPLICATE_FRAME_EVIDENCE"
        if result["no_duplicate_frame_evidence"]
        else "DUPLICATE_FRAME_EVIDENCE"
    )
    print(
        "REPOSITORY_SUBSTRATE_PROVEN"
        if result["repository_substrate_proven"]
        else "REPOSITORY_SUBSTRATE_FAILED"
    )
    print(
        "CONTEXT_FRONTIER_PROVEN"
        if result["context_frontier_proven"]
        else "CONTEXT_FRONTIER_FAILED"
    )
    print(
        "CERTIFIED_OPPORTUNITY_POLICY_PROVEN"
        if result["context_frontier_proven"]
        else "CERTIFIED_OPPORTUNITY_POLICY_FAILED"
    )
    print(
        "PROVIDER_BASELINE_SHIELD_PROVEN"
        if result["provider_baseline_shield_proven"]
        else "PROVIDER_BASELINE_SHIELD_FAILED"
    )
    print(
        "REPEATED_CONTROL_GATE_PROVEN"
        if result["repeated_control_gate_proven"]
        else "REPEATED_CONTROL_GATE_FAILED"
    )
    return (
        0
        if all(
            (
                result["all_17_consumer_paths_proven"],
                result["all_feature_opportunities_accounted"],
                result["no_eligible_trigger_misses"],
                result["no_false_feature_fires"],
                result["no_empty_localization_effects"],
                result["no_unverified_callers"],
                result["no_duplicate_frame_evidence"],
                result["repository_substrate_proven"],
                result["context_frontier_proven"],
                result["provider_baseline_shield_proven"],
                result["repeated_control_gate_proven"],
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
