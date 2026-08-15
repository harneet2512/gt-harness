"""Provider-free replay of archived GT-on trajectories through the repaired policy.

Replays each archived task's action stream through the repaired
``CentralFeatureRuntime`` and a fresh evidence ledger, then compares the
repaired behavior against the archived receipt.  This is the Phase 9 gate:
no paid run is allowed until the per-task outcomes are reviewed.

The archived receipts are v2 (produced before this repair); the replay is the
only place the repaired policy is exercised against real trajectories without
a provider.  Workspace transitions are reconstructed from the archived
``GT_CHANGE_SURFACE`` receipts, so change classification exercises the new
source-revision model on the same paths the smoke actually touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.central_runtime import (  # noqa: E402
    CENTRAL_FEATURE_IDS,
    CentralFeatureRuntime,
    EvidenceLedger,
    WorkspaceSnapshot,
    WorkspaceTransition,
    classify_change,
    classify_validation_command,
    explicit_check_commands,
    feature_payload_grounded,
    is_submit_command,
    normalize_command,
    task_deliverable_paths,
)
from gt_engine.preflight import ActionDisposition, adapt_proposed_action  # noqa: E402
from gt_engine.provider_view import build_provider_view  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _instruction(trajectory: dict[str, Any]) -> str:
    for message in trajectory.get("messages") or []:
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _iter_events(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one event per executed action with command, returncode, output."""
    tool_results: dict[str, list[dict[str, Any]]] = {}
    for message in trajectory.get("messages") or []:
        if message.get("role") == "tool":
            tool_results.setdefault(str(message.get("tool_call_id") or ""), []).append(message)
    cursors: dict[str, int] = {}
    events: list[dict[str, Any]] = []
    index = 0
    model_call = 0
    for message in trajectory.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        model_call += 1
        actions = tuple((message.get("extra") or {}).get("actions") or ())
        for batch_index, action in enumerate(actions):
            index += 1
            command = str(action.get("command") or "")
            tool_id = str(action.get("tool_call_id") or "")
            results = tool_results.get(tool_id) or []
            cursor = cursors.get(tool_id, 0)
            tool_message = results[cursor] if cursor < len(results) else None
            cursors[tool_id] = cursor + 1
            extra = (tool_message or {}).get("extra") or {}
            returncode = extra.get("returncode")
            if returncode is None:
                returncode = -1
            output = str(extra.get("raw_output") or (tool_message or {}).get("content") or "")
            events.append(
                {
                    "index": index,
                    "command": command,
                    "tool_call_id": tool_id,
                    "model_call": model_call,
                    "batch_index": batch_index,
                    "batch_size": len(actions),
                    "returncode": returncode,
                    "output": output,
                }
            )
    return events


def _archived_transitions(receipt: dict[str, Any]) -> dict[int, dict[str, tuple[str, ...]]]:
    by_action: dict[int, dict[str, tuple[str, ...]]] = {}
    for row in (receipt.get("features") or {}).get("receipts") or []:
        if row.get("feature_id") != "GT_CHANGE_SURFACE":
            continue
        payload = row.get("payload") or {}
        by_action[int(row.get("action") or 0)] = {
            "created": tuple(payload.get("created") or ()),
            "modified": tuple(payload.get("modified") or ()),
            "deleted": tuple(payload.get("deleted") or ()),
        }
    return by_action


def _archived_syntax_results(receipt: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """Index deterministic host-side syntax results omitted from the trajectory.

    Mini-SWE trajectories contain provider-selected actions only.  GT's lint sensor
    runs after an authored edit and is therefore present in the central receipt but
    absent from that action stream.  Replaying only model actions would erase real
    certifying evidence and produce a false certificate-regression failure.
    """
    by_action: dict[int, list[dict[str, Any]]] = {}
    for row in (receipt.get("features") or {}).get("receipts") or []:
        if row.get("feature_id") != "syntax_result":
            continue
        payload = row.get("payload") or {}
        if not payload.get("fresh") or not payload.get("path") or not payload.get("command"):
            continue
        by_action.setdefault(int(row.get("action") or 0), []).append(payload)
    return by_action


def replay_task(trajectory_path: Path, receipt_path: Path, task_name: str) -> dict[str, Any]:
    trajectory = _load_json(trajectory_path)
    receipt = _load_json(receipt_path)
    instruction = _instruction(trajectory)
    checks = explicit_check_commands(instruction)
    deliverables = task_deliverable_paths(instruction)
    events = _iter_events(trajectory)
    transitions = _archived_transitions(receipt)
    syntax_results = _archived_syntax_results(receipt)

    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        instruction,
        revision="replay-w0",
        source_revision="replay-s0",
        explicit_checks=checks,
        task_deliverables=deliverables,
    )
    ledger = EvidenceLedger(max_holds=1)
    source_revision = "replay-s0"
    source_epoch = 0
    artifact_debt_triggers: list[dict[str, str]] = []
    simulated_deliveries: list[dict[str, Any]] = []
    engine_syntax_checks_replayed = 0
    preflight_operations: Counter[str] = Counter()
    preflight_segment_operations: Counter[str] = Counter()
    preflight_candidates: list[dict[str, Any]] = []
    shadow_barrier_calls: set[int] = set()
    shadow_suffix_actions_prevented = 0
    shadow_barriers: list[dict[str, Any]] = []
    attributed_validation_commands: set[str] = set()

    for event in events:
        action_id = event["index"]
        pre_classification = classify_validation_command(event["command"], checks)
        proposed = adapt_proposed_action(
            {
                "command": event["command"],
                "tool_call_id": event["tool_call_id"],
            },
            source_revision=source_revision,
            workspace_revision="replay-w0",
            model_call=event["model_call"],
            batch_index=event["batch_index"],
            batch_size=event["batch_size"],
            validation=pre_classification,
        )
        preflight_operations[proposed.operation.value] += 1
        preflight_segment_operations.update(
            operation.operation.value for operation in proposed.operations
        )
        # Archived receipts do not contain a complete pre-execution manifest.
        # File-existence policy must therefore abstain. Submit policy can be
        # replayed because its grounded ledger and source revision are complete.
        snapshot_available = proposed.operation.value == "submit"
        preflight = runtime.preflight_action(
            proposed,
            WorkspaceSnapshot(
                "replay-w0",
                {},
                snapshot_available,
                "" if snapshot_available else "archived_snapshot_unavailable",
            ),
            revision="replay-w0",
            source_revision=source_revision,
            ledger=ledger,
        )
        if preflight.disposition != ActionDisposition.PASS:
            preflight_candidates.append(
                {
                    "action": action_id,
                    "operation": proposed.operation.value,
                    "disposition": preflight.disposition.value,
                    "reason_codes": list(preflight.reason_codes),
                    "evidence": list(preflight.evidence),
                    "source_revision": source_revision,
                }
            )
        change = transitions.get(action_id)
        transition = WorkspaceTransition(
            action_id,
            event["command"],
            "replay-w0",
            "replay-w0",
            created=change["created"] if change else (),
            modified=change["modified"] if change else (),
            deleted=change["deleted"] if change else (),
        )
        authored = [
            path
            for path in transition.changed_paths
            if classify_change(path, kind="f", task_deliverables=deliverables).validation_relevant
        ]
        if authored:
            source_epoch += 1
            source_revision = f"replay-s{source_epoch}"
        known_barrier_operation = proposed.operation.value in {"validate", "submit"}
        observed_mutating_change = proposed.mutates_workspace and bool(
            transition.changed_paths
        )
        has_suffix = event["batch_index"] + 1 < event["batch_size"]
        if (
            has_suffix
            and event["model_call"] not in shadow_barrier_calls
            and (known_barrier_operation or bool(authored) or observed_mutating_change)
        ):
            shadow_barrier_calls.add(event["model_call"])
            suffix_count = event["batch_size"] - event["batch_index"] - 1
            shadow_suffix_actions_prevented += suffix_count
            reasons = []
            if known_barrier_operation:
                reasons.append(f"operation:{proposed.operation.value}")
            if authored:
                reasons.append("authored_workspace_change")
            if observed_mutating_change:
                reasons.append("observed_mutating_change")
            shadow_barriers.append(
                {
                    "action": action_id,
                    "model_call": event["model_call"],
                    "batch_index": event["batch_index"],
                    "batch_size": event["batch_size"],
                    "suffix_actions_prevented": suffix_count,
                    "reason_codes": reasons,
                    "command_preview": " ".join(event["command"].split())[:240],
                }
            )
        classification = pre_classification.with_result(
            result_code=event["returncode"],
            output=event["output"],
            source_revision=source_revision,
            workspace_revision="replay-w0",
        )
        if classification.status_attributed:
            attributed_validation_commands.add(classification.normalized_command)
        runtime.observe_action(
            action_id=action_id,
            command=event["command"],
            output=event["output"],
            returncode=event["returncode"],
            transition=transition,
            revision="replay-w0",
            source_revision=source_revision,
            validation=classification,
            proposed=proposed,
        )
        if classification.is_validation:
            ledger.record_check(
                event["command"],
                returncode=event["returncode"],
                revision=source_revision,
                grounded=classification.grounded,
                classification=classification,
            )
        for syntax in syntax_results.get(action_id, []):
            returncode = int(syntax.get("returncode") or 0)
            path = str(syntax.get("path") or "")
            command = str(syntax.get("command") or "")
            failed = not bool(syntax.get("ok")) or returncode != 0
            ledger.record_check(
                f"syntax:{path}",
                returncode=returncode,
                revision=source_revision,
                grounded=True,
            )
            runtime.record_syntax(
                action_id=action_id,
                revision="replay-w0",
                source_revision=source_revision,
                failed=failed,
                reason=(
                    "replayed_changed_file_syntax_failure"
                    if failed
                    else "replayed_changed_file_syntax_pass"
                ),
                path=path,
                command=command,
                returncode=returncode,
                diagnostic=str(syntax.get("diagnostic") or ""),
            )
            engine_syntax_checks_replayed += 1
        if is_submit_command(event["command"]):
            readiness = ledger.readiness_evidence(source_revision)
            runtime.record_submit(
                action_id=action_id,
                revision="replay-w0",
                source_revision=source_revision,
                refused=False,
                sensor_healthy=True,
                check_count=len(readiness),
                passing_checks=sum(item.returncode == 0 for item in readiness),
                failing_checks=sum(item.returncode != 0 for item in readiness),
            )
        runtime.consume_effects(action_id=action_id, call=action_id)
        feedback = runtime.model_feedback(deferred=True)
        if feedback:
            metadata = runtime.confirm_prepared_guidance() or {}
            simulated_deliveries.append(
                {
                    "after_action": action_id,
                    "first_eligible_call": action_id + 1,
                    "feature_id": metadata.get("feature_id"),
                    "contributing_features": metadata.get("contributing_features", []),
                    "chars": len(feedback),
                    "feedback": feedback,
                }
            )
        active_state = {
            **runtime.progress_ledger(),
            "source_revision": source_revision,
            "workspace_revision": "replay-w0",
        }
        _provider_view, compiler = build_provider_view(
            [{"role": "user", "content": instruction}],
            active_state=active_state,
            trigger_chars=10**18,
            target_chars=10**18,
            transform=False,
        )
        runtime.record_context_compiler_call(
            call=action_id + 1,
            request_payload_sha256=f"replay-call-{action_id + 1}",
            fact_accounting=compiler.fact_accounting,
        )

    summary = runtime.summary()
    for row in summary["receipts"]:
        if row["feature_id"] != "GT_EDIT_CHECK":
            continue
        if row["payload"].get("intervention") != "validation_debt":
            continue
        for path in row["payload"].get("changed_paths") or []:
            change = classify_change(path, kind="f", task_deliverables=deliverables)
            if not change.validation_relevant:
                artifact_debt_triggers.append({"path": path, "origin": change.origin.value})

    readiness = ledger.readiness_evidence(source_revision)
    ledger_declared = sorted(
        {
            item.command
            for item in ledger.outcomes.values()
            if item.grounded and item.command_class == "declared_validation"
        }
    )
    ledger_checks_total = sum(1 for item in ledger.outcomes.values() if item.grounded)

    old_features = receipt.get("features") or {}
    old_receipts = old_features.get("receipts") or []
    old_cert = next(
        (row for row in old_receipts if row.get("feature_id") == "GT_CERT_DELIVERY"), None
    )
    old_cert_payload = (old_cert or {}).get("payload") or {}
    old_cert_checks = tuple(str(item) for item in old_cert_payload.get("checks") or ())
    old_grounded_certificate_checks = sorted(
        {
            str(row.get("command") or "")
            for row in old_features.get("validation_log") or ()
            if row.get("grounded")
            and any(
                str(row.get("command") or "").startswith(check)
                or check.startswith(str(row.get("command") or ""))
                for check in old_cert_checks
            )
        }
    )
    old_attributable_certificate_checks = sorted(
        check
        for check in old_grounded_certificate_checks
        if normalize_command(check) in attributed_validation_commands
    )
    old_invalidated_certificate_checks = sorted(
        set(old_grounded_certificate_checks) - set(old_attributable_certificate_checks)
    )
    old_produced = old_features.get("produced_counts")
    if old_produced is None:
        old_produced = Counter(row.get("feature_id") for row in old_receipts)
    old_deliveries = receipt.get("guidance_deliveries") or []
    old_receipts_by_key = {
        (row.get("feature_id"), int(row.get("action") or 0)): row for row in old_receipts
    }
    old_ungrounded_deliveries = []
    for delivery in old_deliveries:
        key = (
            delivery.get("feature_id"),
            int(delivery.get("evidence_action") or 0),
        )
        source = old_receipts_by_key.get(key) or {}
        if not feature_payload_grounded(str(key[0] or ""), source.get("payload") or {}):
            old_ungrounded_deliveries.append({"feature_id": key[0], "evidence_action": key[1]})

    return {
        "task": task_name,
        "actions": len(events),
        "source_epoch": source_epoch,
        "explicit_checks": list(checks),
        "deliverables": list(deliverables),
        "old": {
            "produced": int(sum(old_produced.values())),
            "produced_features": {
                feature_id: int(old_produced.get(feature_id) or 0)
                for feature_id in CENTRAL_FEATURE_IDS
                if old_produced.get(feature_id)
            },
            "guidance_deliveries": len(old_deliveries),
            "guidance_chars": int(old_features.get("guidance_chars") or 0),
            "ungrounded_deliveries": old_ungrounded_deliveries,
            "certificate": old_cert_payload,
            "grounded_certificate_checks": old_grounded_certificate_checks,
            "attributable_certificate_checks": old_attributable_certificate_checks,
            "invalidated_certificate_checks": old_invalidated_certificate_checks,
            "exit_status": str((trajectory.get("info") or {}).get("exit_status") or ""),
        },
        "new": {
            "produced": sum(summary["produced_counts"].values()),
            "produced_features": {
                feature_id: count
                for feature_id, count in summary["produced_counts"].items()
                if count
            },
            "guidance_events": summary["guidance_events"],
            "guidance_chars": summary["guidance_chars"],
            "validation_declared": sum(
                1
                for row in summary["validation_log"]
                if row["command_class"] == "declared_validation"
            ),
            "validation_attributed_declared": sum(
                1
                for row in summary["validation_log"]
                if row["command_class"] == "declared_validation"
                and row.get("status_attributed")
            ),
            "validation_recognized": sum(
                1
                for row in summary["validation_log"]
                if row["command_class"] == "recognized_validation"
            ),
            "certificate": {
                "check_count": len(readiness),
                "passing_checks": sum(item.returncode == 0 for item in readiness),
                "failing_checks": sum(item.returncode != 0 for item in readiness),
            },
            "ledger_checks_total": ledger_checks_total,
            "engine_syntax_checks_replayed": engine_syntax_checks_replayed,
            "ledger_declared_checks": ledger_declared,
            "artifact_debt_triggers": artifact_debt_triggers,
            "consumer_features": sorted(summary["consumer_paths"]),
            "applied_features": sorted(
                {
                    row["feature_id"]
                    for row in summary["effect_applications"]
                    if row["state_fields_changed"]
                }
            ),
            "simulated_deliveries": simulated_deliveries,
            "submit_holds": summary["action_metrics"]["submit_holds"],
            "batch_interrupts": summary["action_metrics"]["batch_interrupts"],
            "interrupted_actions": summary["action_metrics"]["interrupted_actions"],
            "preflight_shadow": {
                "operation_distribution": dict(sorted(preflight_operations.items())),
                "segment_operation_distribution": dict(
                    sorted(preflight_segment_operations.items())
                ),
                "known_segment_operations": sum(
                    count
                    for operation, count in preflight_segment_operations.items()
                    if operation != "other"
                ),
                "unknown_segment_operations": preflight_segment_operations.get(
                    "other", 0
                ),
                "material_candidates": preflight_candidates,
                "file_policy_status": "abstained_without_preexecution_snapshot",
                "barrier_calls": len(shadow_barrier_calls),
                "suffix_actions_prevented": shadow_suffix_actions_prevented,
                "barriers": shadow_barriers,
            },
            "context_compiler_effect_accountability": summary[
                "context_compiler_effect_accountability_counts"
            ],
        },
    }


def _outcomes(task: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    if task["new"]["artifact_debt_triggers"]:
        failed.append(f"artifact-driven validation debt: {task['new']['artifact_debt_triggers']}")
    compiler_counts = task["new"].get("context_compiler_effect_accountability") or {}
    if compiler_counts.get("unaccounted_bug"):
        failed.append(
            f"unaccounted context-compiler effects: {compiler_counts['unaccounted_bug']}"
        )
    new_declared = int(task["new"].get("validation_attributed_declared") or 0)
    ledger_total = int(task["new"]["ledger_checks_total"])
    if new_declared > 0 and ledger_total == 0:
        failed.append("runtime classified declared validations but the ledger recorded none")
    old_attributable_cert_count = len(
        task["old"].get("attributable_certificate_checks") or ()
    )
    if old_attributable_cert_count > 0 and ledger_total == 0:
        failed.append("old attributable certificate checks were lost by the repaired policy")
    if task["new"]["submit_holds"] or task["new"]["batch_interrupts"]:
        failed.append("repaired policy blocked or interrupted Mini-SWE")
    if task["new"]["interrupted_actions"]:
        failed.append("repaired policy cancelled Mini-SWE actions")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    tasks: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for trajectory_path in sorted(args.run_dir.rglob("miniswe_trajectory.json")):
        receipt_path = trajectory_path.parent / "central_receipt.json"
        if not receipt_path.exists():
            issues.append(f"{trajectory_path}: missing central_receipt.json")
            continue
        task_name = (
            trajectory_path.parent.parent.parent.name.split("__")[0]
            if trajectory_path.parent.name == "agent"
            else trajectory_path.parent.name
        )
        tasks[task_name] = replay_task(trajectory_path, receipt_path, task_name)

    report = {"version": "gt.central_replay.v1", "task_count": len(tasks), "tasks": tasks}
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")

    print(f"replayed {len(tasks)} task(s)")
    for task_name, task in tasks.items():
        failures = _outcomes(task)
        status = "PASS" if not failures else "FAIL"
        print(
            f"  {status} {task_name}: actions={task['actions']} "
            f"old_produced={task['old']['produced']} new_produced={task['new']['produced']} "
            f"old_deliveries={task['old']['guidance_deliveries']} "
            f"new_guidance={task['new']['guidance_events']} "
            f"old_cert={task['old']['certificate'].get('check_count')} "
            f"new_cert={task['new']['certificate']['check_count']} "
            f"debt_triggers={len(task['new']['artifact_debt_triggers'])}"
        )
        for failure in failures:
            print(f"    ! {task_name}: {failure}")
    for issue in issues:
        print(f"  !! {issue}")

    ok = bool(tasks) and not issues and all(not _outcomes(task) for task in tasks.values())
    print("REPLAY_OK" if ok else "REPLAY_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
