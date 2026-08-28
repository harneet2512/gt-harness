#!/usr/bin/env python3
"""Aggregate live 17+1 feature lifecycles from central runtime receipts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from gt_engine.central_runtime import CENTRAL_FEATURE_IDS
from gt_engine.runtime_gate import audit_runtime_receipt, normalized_effect_accountability


def _task_name(receipt: dict[str, Any], index: int) -> str:
    return str(receipt.get("task") or receipt.get("task_name") or f"task-{index}")


def build_feature_lifecycle_report(
    receipts: Iterable[dict[str, Any]],
    *,
    forced_feature_ids: Iterable[str] = (),
    forced_proof: dict[str, Any] | None = None,
    expected_task_ids: Iterable[str] = (),
) -> dict[str, Any]:
    input_rows = [dict(receipt) for receipt in receipts]
    rows_by_task: dict[str, dict[str, Any]] = {}
    conflicted_tasks: set[str] = set()
    duplicate_failures: list[str] = []
    for index, receipt in enumerate(input_rows, start=1):
        task = _task_name(receipt, index)
        receipt["task"] = task
        previous = rows_by_task.get(task)
        if previous is None:
            rows_by_task[task] = receipt
        elif previous != receipt:
            duplicate_failures.append(f"{task}:duplicate_receipt_conflict")
            conflicted_tasks.add(task)
    for task in conflicted_tasks:
        rows_by_task.pop(task, None)
    rows = list(rows_by_task.values())
    forced = {str(feature_id) for feature_id in forced_feature_ids}
    failures: list[str] = list(duplicate_failures)
    if forced and not (
        isinstance(forced_proof, dict)
        and forced_proof.get("status") == "passed"
        and len(str(forced_proof.get("exact_commit") or "")) == 40
        and set(forced_proof.get("feature_ids") or ()) == set(CENTRAL_FEATURE_IDS)
    ):
        failures.append("forced_feature_proof_missing_or_invalid")
    expected = {str(task) for task in expected_task_ids if str(task)}
    failures.extend(
        f"{task}:receipt_missing" for task in sorted(expected - set(rows_by_task))
    )
    aggregates = {
        feature_id: {
            "feature_id": feature_id,
            "configured_tasks": 0,
            "evaluations": 0,
            "eligible": 0,
            "produced": 0,
            "applied": 0,
            "state_changes": 0,
            "provider_deliveries": 0,
            "correct_abstentions": 0,
            "missed_triggers": 0,
            "false_fires": 0,
            "accountability": Counter(),
            "live_tasks": set(),
        }
        for feature_id in CENTRAL_FEATURE_IDS
    }
    persistent = {
        "configured_tasks": 0,
        "applicable_tasks": 0,
        "correctly_abstained_tasks": 0,
        "exercised_tasks": 0,
        "lifecycle_use_count": 0,
        "bootstrap_calls": 0,
        "selection_event_count": 0,
        "selection_provider_calls": 0,
        "bootstrap_provider_calls": 0,
        "context_compilations": 0,
        "preflight_projections": 0,
        "postflight_commits": 0,
        "graph_rebases": 0,
        "failures": [],
    }
    operational = {
        "submit_holds": 0,
        "completion_certificate_evaluations": 0,
        "completion_probe_execs": 0,
        "red_test_probe_attempts": 0,
        "context_recap_receipts": 0,
        "context_recap_fallbacks": 0,
        "failures": [],
    }

    for index, receipt in enumerate(rows, start=1):
        task = _task_name(receipt, index)
        runtime_failures, _runtime_summary = audit_runtime_receipt(receipt, task=task)
        failures.extend(runtime_failures)
        features = receipt.get("features") or {}
        census = receipt.get("product_mechanism_census") or {}
        configured = set(census.get("configured_mechanism_ids") or ())
        applicability = features.get("feature_applicability") or {}
        produced = features.get("produced_counts") or {}
        applications = [
            row
            for row in features.get("effect_applications") or ()
            if isinstance(row, dict)
        ]
        accountability = normalized_effect_accountability(receipt)
        expected_mechanisms = {*CENTRAL_FEATURE_IDS, "persistent_execution_state"}
        if configured != expected_mechanisms:
            failures.append(f"{task}:configured_mechanism_set_mismatch")
        if int(census.get("product_mechanism_count") or 0) != len(expected_mechanisms):
            failures.append(f"{task}:product_mechanism_count_mismatch")
        if int(census.get("configured_mechanism_count") or 0) != len(configured):
            failures.append(f"{task}:configured_mechanism_count_mismatch")
        if set(features.get("feature_ids") or ()) != set(CENTRAL_FEATURE_IDS):
            failures.append(f"{task}:legacy_feature_set_mismatch")
        if int(features.get("feature_count") or 0) != len(CENTRAL_FEATURE_IDS):
            failures.append(f"{task}:legacy_feature_count_mismatch")
        if not features.get("all_feature_opportunities_accounted", False):
            failures.append(f"{task}:feature_opportunities_unaccounted")

        for feature_id in CENTRAL_FEATURE_IDS:
            bucket = aggregates[feature_id]
            if feature_id in configured:
                bucket["configured_tasks"] += 1
            else:
                failures.append(f"{task}:{feature_id}:not_configured")
            app = applicability.get(feature_id) or {}
            evaluations = int(app.get("evaluations") or 0)
            eligible = int(app.get("eligible") or 0)
            fired = int(produced.get(feature_id) or 0)
            bucket["evaluations"] += evaluations
            bucket["eligible"] += eligible
            bucket["produced"] += fired
            if eligible:
                bucket["live_tasks"].add(task)
            status = str(app.get("lifecycle_state") or app.get("status") or "NOT_APPLICABLE")
            if status in {"correct_abstention", "trigger_absent", "NOT_APPLICABLE"}:
                bucket["correct_abstentions"] += 1
            # ABSTAINED is an explicit, valid terminal for an eligible
            # candidate that could not be independently certified.  Legacy
            # receipts did not carry this distinction and retain the old
            # eligible-vs-fired reconciliation.
            legacy_status = status in {
                "correct_abstention", "trigger_absent", "missed_trigger",
                "ambiguous_evidence", "substrate_unavailable",
            }
            if status in {"missed_trigger", "CANDIDATE"} or (
                legacy_status and eligible > fired
            ):
                bucket["missed_triggers"] += max(1, eligible - fired)
                failures.append(f"{task}:{feature_id}:missed_trigger")
            if fired and eligible == 0 and status in {
                "correct_abstention", "trigger_absent", "NOT_APPLICABLE",
            }:
                bucket["false_fires"] += fired
                failures.append(f"{task}:{feature_id}:false_fire")

            feature_applications = [
                row for row in applications if row.get("feature_id") == feature_id
            ]
            bucket["applied"] += len(feature_applications)
            bucket["state_changes"] += sum(
                bool(row.get("state_fields_changed")) for row in feature_applications
            )
            if len(feature_applications) != fired:
                failures.append(f"{task}:{feature_id}:application_count_mismatch")
            feature_accountability = [
                row for row in accountability if row.get("feature_id") == feature_id
            ]
            if len(feature_accountability) != fired:
                failures.append(f"{task}:{feature_id}:accountability_count_mismatch")
            for row in feature_accountability:
                outcome = str(row.get("outcome") or "unknown")
                bucket["accountability"][outcome] += 1
                bucket["provider_deliveries"] += len(row.get("provider_delivery_ids") or ())
                if outcome == "pending_decision_claim":
                    failures.append(f"{task}:{feature_id}:pending_decision_claim")

        pes = census.get("persistent_execution_state") or {}
        count_fields = (
            "context_compilations",
            "preflight_projections",
            "postflight_commits",
            "graph_rebases",
        )
        counts = {field: int(pes.get(field) or 0) for field in count_fields}
        selection_mode = str(pes.get("selection_mode") or "generative")
        bootstrap_calls = int(pes.get("bootstrap_calls") or 0)
        selection_events = int(
            pes.get("selection_event_count")
            if pes.get("selection_event_count") is not None
            else bootstrap_calls
        )
        selection_provider_calls = int(
            pes.get("selection_provider_calls")
            if pes.get("selection_provider_calls") is not None
            else bootstrap_calls
        )
        bootstrap_provider_calls = int(
            pes.get("bootstrap_provider_calls")
            if pes.get("bootstrap_provider_calls") is not None
            else bootstrap_calls
        )
        computed_lifecycle_uses = selection_events + sum(counts.values())
        recorded_lifecycle_uses = int(pes.get("lifecycle_use_count") or 0)
        if recorded_lifecycle_uses != computed_lifecycle_uses:
            persistent["failures"].append(f"{task}:persistent_lifecycle_count_mismatch")
        if pes.get("configured") is True:
            persistent["configured_tasks"] += 1
        else:
            persistent["failures"].append(f"{task}:persistent_not_configured")
        if pes.get("applicable") is True:
            persistent["applicable_tasks"] += 1
            if selection_events != 1:
                persistent["failures"].append(f"{task}:persistent_selection_count")
            if selection_mode == "deterministic_v1":
                if selection_provider_calls or bootstrap_provider_calls or bootstrap_calls:
                    persistent["failures"].append(
                        f"{task}:deterministic_selection_used_provider"
                    )
            elif (
                selection_mode != "generative"
                or selection_provider_calls != 1
                or bootstrap_provider_calls != 1
                or bootstrap_calls != 1
            ):
                persistent["failures"].append(f"{task}:persistent_bootstrap_count")
            if computed_lifecycle_uses <= 0 or pes.get("exercised") is not True:
                persistent["failures"].append(f"{task}:persistent_not_exercised")
            if computed_lifecycle_uses <= 1 or pes.get("repeated_deterministic_use") is not True:
                persistent["failures"].append(
                    f"{task}:persistent_not_repeated_from_counts"
                )
        elif pes.get("correctly_abstained") is True:
            persistent["correctly_abstained_tasks"] += 1
            if computed_lifecycle_uses or recorded_lifecycle_uses:
                persistent["failures"].append(f"{task}:abstention_has_lifecycle_use")
            if pes.get("exercised") is True or pes.get("repeated_deterministic_use") is True:
                persistent["failures"].append(f"{task}:abstention_marked_exercised")
        else:
            persistent["failures"].append(f"{task}:persistent_applicability_unaccounted")
        if pes.get("exercised") is True:
            persistent["exercised_tasks"] += 1
        persistent["bootstrap_calls"] += bootstrap_calls
        persistent["selection_event_count"] += selection_events
        persistent["selection_provider_calls"] += selection_provider_calls
        persistent["bootstrap_provider_calls"] += bootstrap_provider_calls
        for field in ("lifecycle_use_count", *count_fields):
            persistent[field] += int(pes.get(field) or 0)
        persistent["failures"].extend(
            f"{task}:persistent:{failure}" for failure in pes.get("failures") or ()
        )

        metrics = receipt.get("metrics")
        action_metrics = features.get("action_metrics")
        completion = receipt.get("completion")
        red_test = receipt.get("red_test")
        model_calls = receipt.get("model_call_contexts")
        if not all(
            isinstance(item, expected_type)
            for item, expected_type in (
                (metrics, dict),
                (action_metrics, dict),
                (completion, dict),
                (red_test, dict),
                (model_calls, list),
            )
        ):
            operational["failures"].append(f"{task}:operational_evidence_missing")
        else:
            submit_holds = int(metrics.get("submit_holds") or 0)
            submit_refusals = int(produced.get("submit_refusal") or 0)
            submit_red = int(produced.get("GT_SS_SUBMIT_RED") or 0)
            if not (
                submit_holds
                == int(action_metrics.get("submit_holds") or 0)
                == submit_refusals
                == submit_red
            ):
                operational["failures"].append(f"{task}:submit_hold_reconciliation")
            certificates = completion.get("certificates")
            if not isinstance(certificates, list):
                operational["failures"].append(f"{task}:completion_certificates_malformed")
                certificates = ()
            certificate_evaluations = int(
                metrics.get("completion_certificate_evaluations") or 0
            )
            if certificate_evaluations != len(certificates):
                operational["failures"].append(
                    f"{task}:completion_certificate_reconciliation"
                )
            if int(metrics.get("auto_submit_attempts") or 0) != int(
                completion.get("auto_submit_attempts") or 0
            ):
                operational["failures"].append(f"{task}:auto_submit_attempt_reconciliation")
            if int(metrics.get("auto_submits") or 0) != int(
                completion.get("auto_submit_count") or 0
            ):
                operational["failures"].append(f"{task}:auto_submit_reconciliation")
            red_receipts = red_test.get("receipts")
            if not isinstance(red_receipts, list):
                operational["failures"].append(f"{task}:red_test_receipts_malformed")
                red_receipts = ()
            red_attempts = int(metrics.get("red_test_probe_attempts") or 0)
            if red_attempts != len(red_receipts):
                operational["failures"].append(f"{task}:red_test_reconciliation")
            recap_receipts = sum(
                int((row.get("context_compiler") or {}).get("recap_receipts") or 0)
                for row in model_calls
                if isinstance(row, dict)
            )
            recap_fallbacks = sum(
                int((row.get("context_compiler") or {}).get("recap_fallbacks") or 0)
                for row in model_calls
                if isinstance(row, dict)
            )
            if recap_receipts != int(metrics.get("context_recap_receipts") or 0):
                operational["failures"].append(f"{task}:recap_receipt_reconciliation")
            if recap_fallbacks != int(metrics.get("context_recap_fallbacks") or 0):
                operational["failures"].append(f"{task}:recap_fallback_reconciliation")
            operational["submit_holds"] += submit_holds
            operational["completion_certificate_evaluations"] += certificate_evaluations
            operational["completion_probe_execs"] += int(
                metrics.get("completion_probe_execs") or 0
            )
            operational["red_test_probe_attempts"] += red_attempts
            operational["context_recap_receipts"] += recap_receipts
            operational["context_recap_fallbacks"] += recap_fallbacks

    legacy_rows: list[dict[str, Any]] = []
    naturally_fired: list[str] = []
    for feature_id in CENTRAL_FEATURE_IDS:
        bucket = aggregates[feature_id]
        if bucket["produced"]:
            naturally_fired.append(feature_id)
        feature_failures = [
            failure for failure in failures if f":{feature_id}:" in failure
        ]
        if feature_failures:
            status = "broken"
        elif bucket["eligible"]:
            status = "working_live"
        elif feature_id in forced:
            status = "working_forced_only"
        else:
            status = "not_observed_live"
            failures.append(f"feature:{feature_id}:no_live_or_forced_proof")
        legacy_rows.append(
            {
                **{
                    key: value
                    for key, value in bucket.items()
                    if key not in {"accountability", "live_tasks"}
                },
                "accountability": dict(sorted(bucket["accountability"].items())),
                "live_tasks": sorted(bucket["live_tasks"]),
                "forced_integration_proven": feature_id in forced,
                "status": status,
                "failures": feature_failures,
            }
        )

    failures.extend(persistent["failures"])
    failures.extend(operational["failures"])
    persistent["passed"] = not persistent["failures"]
    operational["passed"] = not operational["failures"]
    return {
        "schema": "gt.feature_lifecycle_report.v2",
        "passed": not failures,
        "task_count": len(rows),
        "legacy_feature_count": len(CENTRAL_FEATURE_IDS),
        "naturally_fired_legacy_feature_count": len(naturally_fired),
        "naturally_fired_legacy_feature_ids": naturally_fired,
        "legacy_features": legacy_rows,
        "forced_feature_proof": dict(forced_proof or {}),
        "persistent_execution_state": persistent,
        "operational_controls": operational,
        "failures": list(dict.fromkeys(failures)),
    }


def _infer_task(path: Path) -> str:
    for part in reversed(path.parts):
        if "__" in part:
            return part.split("__", 1)[0]
        if "-task-" in part:
            return part.split("-task-", 1)[1]
    return path.parent.name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--forced-all-17", action="store_true")
    parser.add_argument("--forced-proof-commit")
    args = parser.parse_args()
    receipts = []
    for path in sorted(args.receipts_root.rglob("central_receipt.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["task"] = _infer_task(path)
        receipts.append(receipt)
    report = build_feature_lifecycle_report(
        receipts,
        forced_feature_ids=CENTRAL_FEATURE_IDS if args.forced_all_17 else (),
        forced_proof=(
            {
                "status": "passed",
                "exact_commit": args.forced_proof_commit,
                "feature_ids": list(CENTRAL_FEATURE_IDS),
            }
            if args.forced_all_17 and args.forced_proof_commit
            else None
        ),
    )
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
