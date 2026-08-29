import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.benchmark_parity import audit_runtime_receipt
from gt_engine.benchmark_population import build_benchmark_population
from gt_engine.benchmark_reports import build_benchmark_reports
from gt_engine.bootstrap_canary_contract import validate_canary
from gt_engine.central_runtime import CENTRAL_FEATURE_IDS
from gt_engine.deep_metrics import classify_trial_outcome, extract_trajectory
from gt_engine.delivery_audit import audit_provider_deliveries
from gt_engine.intervention_chain import audit_intervention_artifacts
from gt_engine.treatment_adapter import BenchmarkManifest
from scripts.central_feature_lifecycle import build_feature_lifecycle_report
from scripts.central_integrity_audit import audit_run_root as audit_integrity_run_root
from scripts.central_release_gate import audit_treatment_runtime
from scripts.provider_route_contract import resolve_release_provider_route
from scripts.release_manifest import load_release_manifest


def merge_results() -> int:
    from scripts.tb2_promotion_gate import (
        assess_tb2_promotion,
        treatment_from_merged,
    )
    from scripts.tb2_regression_forensics import build_regression_forensics

    expected = json.loads(os.environ.get("EXPECTED_TASKS_JSON") or "[]")
    release_manifest = load_release_manifest()
    baseline = json.loads(release_manifest.baseline_path.read_text(encoding="utf-8"))
    prediction_path = release_manifest.prediction_path
    prediction_sha256 = (
        hashlib.sha256(prediction_path.read_bytes()).hexdigest() if prediction_path.exists() else ""
    )
    expected_prediction_sha256 = os.environ.get("PREDICTION_SHA256") or ""
    prediction_hash_valid = bool(
        prediction_sha256
        and expected_prediction_sha256
        and prediction_sha256 == expected_prediction_sha256
    )
    dense_required = True
    trials, missing, per_task, receipt_metrics = [], [], [], []
    observed_artifact_tasks = set()
    feature_receipts = []
    observed_treatment_runtime_contracts = []
    deep_tasks = {}
    verified_benchmark_manifests = {}
    artifact_integrity_failures = []

    provider_free_receipts = list(Path("provider-free").rglob("central_provider_free_receipt.json"))
    provider_free_mechanical = list(Path("provider-free").rglob("mechanical-completeness.json"))
    provider_free_documentation = list(
        Path("provider-free").rglob("documentation-consistency.json")
    )
    for artifact_name, artifact_paths in (
        ("provider_free_receipt", provider_free_receipts),
        ("provider_free_mechanical", provider_free_mechanical),
        ("provider_free_documentation", provider_free_documentation),
    ):
        if len(artifact_paths) != 1:
            artifact_integrity_failures.append(
                f"run:{artifact_name}_artifact_count:{len(artifact_paths)}"
            )
    provider_free_receipt = (
        json.loads(provider_free_receipts[0].read_text(encoding="utf-8"))
        if len(provider_free_receipts) == 1
        else {}
    )
    provider_free_mechanical_proof = (
        json.loads(provider_free_mechanical[0].read_text(encoding="utf-8"))
        if len(provider_free_mechanical) == 1
        else {}
    )
    provider_free_documentation_proof = (
        json.loads(provider_free_documentation[0].read_text(encoding="utf-8"))
        if len(provider_free_documentation) == 1
        else {}
    )
    provider_free_valid = bool(
        provider_free_receipt.get("commit") == os.environ.get("GT_COMMIT")
        and provider_free_receipt.get("provider_calls") == 0
        and provider_free_receipt.get("provider_credentials_present") is False
        and provider_free_receipt.get("mechanical_completeness") == "PASS"
        and provider_free_mechanical_proof.get("status") == "PASS"
        and provider_free_documentation_proof.get("status") == "PASS"
        and os.environ.get("PROVIDER_FREE_COMMIT") == os.environ.get("GT_COMMIT")
        and os.environ.get("PROVIDER_FREE_STATUS") == "PASS"
    )
    if not provider_free_valid:
        artifact_integrity_failures.append("run:provider_free_certification_invalid")

    # Join the live one-call canary to the same immutable route contract used by
    # every task.  Task receipts prove which endpoint was configured; this artifact
    # proves that the pre-fan-out call actually reached a provider that returned
    # the expected model/provider identity.  Neither expected configuration alone
    # is allowed to self-certify the observed route.
    canary_receipts = list(Path("bootstrap-canary").rglob("bootstrap-canary.json"))
    canary_routes = list(Path("bootstrap-canary").rglob("provider-route-contract.json"))
    bootstrap_canary = (
        json.loads(canary_receipts[0].read_text(encoding="utf-8"))
        if len(canary_receipts) == 1
        else {}
    )
    bootstrap_route = (
        json.loads(canary_routes[0].read_text(encoding="utf-8")) if len(canary_routes) == 1 else {}
    )
    expected_bootstrap_route = resolve_release_provider_route()
    bootstrap_canary_failures = list(validate_canary(bootstrap_canary))
    bootstrap_route_valid = bool(
        len(canary_receipts) == 1
        and len(canary_routes) == 1
        and not bootstrap_canary_failures
        and bootstrap_route == expected_bootstrap_route
    )
    if not bootstrap_route_valid:
        artifact_integrity_failures.append("run:bootstrap_route_certification_invalid")

    def solved(t):
        rewards = (t.get("verifier_result") or {}).get("rewards") or {}
        vals = [v for v in rewards.values() if isinstance(v, (int, float))]
        return bool(vals) and all(v >= 1 for v in vals)

    for task_dir in sorted(Path("tasks").glob("*")):
        task_name = task_dir.name.split("-task-", 1)[-1]
        observed_artifact_tasks.add(task_name)
        receipt_paths = list(task_dir.rglob("central_receipt.json"))
        if len(receipt_paths) != 1:
            artifact_integrity_failures.append(
                f"{task_name}:receipt_artifact_count:{len(receipt_paths)}"
            )
        if len(receipt_paths) == 1:
            receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
            runtime_contract = receipt.get("treatment_runtime_contract")
            if isinstance(runtime_contract, dict):
                observed_treatment_runtime_contracts.append(dict(runtime_contract))
            receipt_for_lifecycle = dict(receipt)
            receipt_for_lifecycle["task"] = task_name
            feature_receipts.append(receipt_for_lifecycle)
            metrics = receipt.get("metrics") or {}
            intelligence = receipt.get("repository_intelligence") or {}
            preemptive = receipt.get("preemptive_retrieval") or {}
            dense_receipt = preemptive.get("dense_backend") or {}
            contexts = receipt.get("model_call_contexts") or []
            first_context = contexts[0] if contexts else {}
            control_provider_hash = str(
                first_context.get("control_provider_messages_sha256")
                or first_context.get("stock_provider_messages_sha256")
                or ""
            )
            provider_hash = str(first_context.get("provider_messages_sha256") or "")
            dense_proofs = list(task_dir.rglob("dense-backend-proof.json"))
            dense_proof = (
                json.loads(dense_proofs[0].read_text(encoding="utf-8")) if dense_proofs else {}
            )
            provider_identity = receipt.get("provider_response_identity") or {}
            provider_route = receipt.get("provider_route") or {}
            executor_identity = provider_identity.get("executor") or {}
            system_fingerprints = list(executor_identity.get("system_fingerprints") or ()) or [
                (provider_identity.get("bootstrap") or {}).get("system_fingerprint") or ""
            ]
            (
                _provider_delivery_rows,
                provider_delivery_failures,
                provider_delivery_totals,
            ) = audit_provider_deliveries(receipt, task=task_name)
            treatment_release_failures = []
            if (
                dense_required
                and not bool(intelligence.get("denominator_excluded"))
                and len(dense_proofs) != 1
            ):
                treatment_release_failures.append(
                    f"{task_name}:dense_backend_proof_artifact_count:{len(dense_proofs)}"
                )
            chain_paths = list(task_dir.rglob("intervention_chain.json"))
            replay_manifests = list(task_dir.rglob("gt_replay/manifest.json"))
            trajectories = list(task_dir.rglob("miniswe_trajectory.json"))
            for artifact_name, artifact_paths in (
                ("intervention_chain", chain_paths),
                ("replay_manifest", replay_manifests),
                ("trajectory", trajectories),
            ):
                if len(artifact_paths) != 1:
                    treatment_release_failures.append(
                        f"{task_name}:{artifact_name}_artifact_count:{len(artifact_paths)}"
                    )
            artifact_failures, artifact_summary = audit_intervention_artifacts(
                receipt,
                artifact_root=receipt_paths[0].parent,
            )
            treatment_release_failures.extend(
                f"{task_name}:artifact:{failure}" for failure in artifact_failures
            )
            intervention_surface_counts = {}
            behavioral_uptake = {}
            if len(chain_paths) == 1:
                try:
                    chain_payload = json.loads(chain_paths[0].read_text(encoding="utf-8"))
                    intervention_surface_counts = dict(
                        (chain_payload.get("counts") or {}).get("surface_counts") or {}
                    )
                    behavioral_uptake = dict(
                        Counter(
                            str((row.get("behavioral_uptake") or {}).get("status") or "")
                            for row in chain_payload.get("rows") or ()
                            if str((row.get("behavioral_uptake") or {}).get("status") or "")
                        )
                    )
                except (OSError, ValueError):
                    treatment_release_failures.append(f"{task_name}:intervention_chain_unreadable")
            if dense_required:
                treatment_release_failures.extend(
                    failure
                    for check in audit_treatment_runtime(receipt, label=task_name)
                    for failure in check.failures
                )
                manifest_paths = list(task_dir.rglob("benchmark-manifest.json"))
                if len(manifest_paths) != 1:
                    treatment_release_failures.append(
                        f"{task_name}:benchmark_manifest_artifact_count:{len(manifest_paths)}"
                    )
                else:
                    try:
                        benchmark_manifest = BenchmarkManifest.from_dict(
                            json.loads(manifest_paths[0].read_text(encoding="utf-8"))
                        )
                        parity = audit_runtime_receipt(benchmark_manifest, receipt)
                        verified_benchmark_manifests[task_name] = benchmark_manifest.as_dict()
                        treatment_release_failures.extend(
                            f"{task_name}:benchmark_runtime_parity:{failure}"
                            for failure in parity.failures
                        )
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        treatment_release_failures.append(
                            f"{task_name}:benchmark_manifest_invalid:{type(exc).__name__}"
                        )
            receipt_metrics.append(
                {
                    "task": task_name,
                    "feature_count": (receipt.get("features") or {}).get("feature_count"),
                    "features_enabled": (receipt.get("features") or {}).get("enabled"),
                    "sensor_healthy": receipt.get("workspace_sensor_healthy"),
                    "input_tokens": metrics.get("input_tokens"),
                    "output_tokens": metrics.get("output_tokens"),
                    "cache_tokens": metrics.get("cache_tokens"),
                    "total_tokens": metrics.get("total_tokens"),
                    "normalized_cost_usd": metrics.get("normalized_cost_usd"),
                    "api_calls": metrics.get("api_calls"),
                    "executor_api_calls": metrics.get("executor_api_calls"),
                    "bootstrap_api_calls": metrics.get("bootstrap_api_calls"),
                    "persistent_selection_mode": metrics.get("persistent_state_selection_mode"),
                    "persistent_selection_events": metrics.get("persistent_state_selection_events"),
                    "persistent_selection_provider_calls": metrics.get(
                        "persistent_state_selection_provider_calls"
                    ),
                    "persistent_applicable": (
                        (
                            (receipt.get("product_mechanism_census") or {}).get(
                                "persistent_execution_state"
                            )
                            or {}
                        ).get("applicable")
                    ),
                    "actions": metrics.get("actions"),
                    "effective_actions": metrics.get("effective_actions"),
                    "effective_actions_schema": metrics.get("effective_actions_schema"),
                    "legacy_effective_task_environment_execs": metrics.get(
                        "legacy_effective_task_environment_execs"
                    ),
                    "effective_task_actions": metrics.get("effective_task_actions"),
                    "actual_environment_execs": metrics.get("actual_environment_execs"),
                    "controller_environment_execs": metrics.get("controller_environment_execs"),
                    "controller_cached_reads": metrics.get("controller_cached_reads"),
                    "sensor_environment_execs": metrics.get("sensor_environment_execs"),
                    "assistant_steps": metrics.get("assistant_steps"),
                    "trajectory_messages": metrics.get("trajectory_messages"),
                    "guidance_events": metrics.get("guidance_events"),
                    "guidance_chars": metrics.get("guidance_chars"),
                    "guidance_candidates": metrics.get("guidance_candidates"),
                    "guidance_suppressed": metrics.get("guidance_suppressed"),
                    "uncached_input_tokens": metrics.get("uncached_input_tokens"),
                    "cache_hit_rate": metrics.get("prompt_cache_hit_rate"),
                    "successful_actions": metrics.get("successful_actions"),
                    "failed_actions": metrics.get("failed_actions"),
                    "check_actions": metrics.get("check_actions"),
                    "workspace_change_actions": metrics.get("workspace_change_actions"),
                    "repeated_commands": metrics.get("repeated_commands"),
                    "censored": metrics.get("censored"),
                    "censored_reason": metrics.get("censored_reason"),
                    "repository_intelligence_status": intelligence.get("status"),
                    "repository_intelligence_applicability": intelligence.get("applicability"),
                    "repository_intelligence_denominator_excluded": bool(
                        intelligence.get("denominator_excluded")
                    ),
                    "repository_intelligence_required": intelligence.get("required"),
                    "repository_intelligence_failures": intelligence.get("failures") or [],
                    "repository_intelligence_transient_failures": (
                        intelligence.get("transient_failures") or []
                    ),
                    "repository_graph_degraded_fallback": bool(
                        metrics.get("repository_graph_degraded_fallback")
                        or (intelligence.get("graph_gate") or {}).get("degraded_fallback")
                    ),
                    "repository_graph_schema_valid": metrics.get("repository_graph_schema_valid"),
                    "repository_graph_nodes": metrics.get("repository_graph_nodes"),
                    "repository_graph_edges": metrics.get("repository_graph_edges"),
                    "repository_mirror_transfer_ms": metrics.get("repository_mirror_transfer_ms"),
                    "repository_index_refresh_ms": metrics.get("repository_index_refresh_ms"),
                    "context_frontier_deliveries": metrics.get("context_frontier_deliveries"),
                    "context_frontier_chars_added": metrics.get("context_frontier_chars_added"),
                    "preemptive_retrieval_deliveries": metrics.get(
                        "preemptive_retrieval_deliveries"
                    ),
                    "preemptive_retrieval_chars_added": metrics.get(
                        "preemptive_retrieval_chars_added"
                    ),
                    "preemptive_dense_backend_available": bool(
                        metrics.get("preemptive_dense_backend_available") == 1
                        and dense_receipt.get("available") is True
                        and dense_proof.get("available") is True
                    ),
                    "preemptive_dense_backend_error": (
                        metrics.get("preemptive_dense_backend_error")
                        or preemptive.get("dense_backend_error")
                    ),
                    "dense_backend_identity": (
                        dense_receipt.get("model_name") or dense_proof.get("identity")
                    ),
                    "control_provider_messages_sha256": control_provider_hash,
                    "provider_messages_sha256": provider_hash,
                    "system_fingerprints": system_fingerprints,
                    "executor_models": list(executor_identity.get("models") or ()),
                    "executor_providers": list(executor_identity.get("providers") or ()),
                    "executor_identity_complete": bool(
                        executor_identity.get("model_identity_complete") is True
                        and executor_identity.get("provider_identity_complete") is True
                        and executor_identity.get("stable_model_identity") is True
                        and executor_identity.get("stable_provider_identity") is True
                    ),
                    "provider_route_id": str(provider_route.get("route_id") or ""),
                    "provider_api_host": str(provider_route.get("api_host") or ""),
                    "provider_api_base": str(provider_route.get("api_base") or ""),
                    "provider_config_model": str(provider_route.get("model") or ""),
                    "provider_credential_in_receipt": bool(
                        provider_route.get("credential_in_receipt")
                    ),
                    "bootstrap_model": str(
                        (provider_identity.get("bootstrap") or {}).get("model") or ""
                    ),
                    "bootstrap_provider": str(
                        (provider_identity.get("bootstrap") or {}).get("provider") or ""
                    ),
                    "call1_gt_view_changed": bool(
                        control_provider_hash
                        and provider_hash
                        and control_provider_hash != provider_hash
                    ),
                    "total_gt_context_chars_added": metrics.get("total_gt_context_chars_added"),
                    "provider_delivery_count": provider_delivery_totals.get("delivery_count"),
                    "provider_delivery_visible_chars": provider_delivery_totals.get(
                        "visible_chars"
                    ),
                    "provider_delivery_surfaces": provider_delivery_totals.get("surfaces"),
                    "provider_delivery_failures": provider_delivery_failures,
                    "treatment_release_failures": treatment_release_failures,
                    "task_execution_certificate_status": (
                        (receipt.get("task_execution_certificate") or {}).get("status")
                    ),
                    "task_execution_certificate_failures": list(
                        (receipt.get("task_execution_certificate") or {}).get("failures") or ()
                    ),
                    "intervention_chain_rows": artifact_summary.get("chain_rows"),
                    "intervention_surface_counts": intervention_surface_counts,
                    "behavioral_uptake": behavioral_uptake,
                    "preemptive_retrieval_shared_computations": metrics.get(
                        "preemptive_retrieval_shared_computations"
                    ),
                    "preemptive_retrieval_rank_consumptions": metrics.get(
                        "preemptive_retrieval_rank_consumptions"
                    ),
                }
            )
        results = list(task_dir.rglob("result.json"))
        if not results:
            missing.append(task_name)
            trajectories = list(task_dir.rglob("miniswe_trajectory.json"))
            if trajectories:
                deep_tasks[task_name] = extract_trajectory(
                    trajectories[0],
                    task=task_name,
                    reward=None,
                    receipt_path=receipt_paths[0] if receipt_paths else None,
                )
            continue
        got = []
        for rp in results:
            r = json.loads(rp.read_text(encoding="utf-8"))
            # Single-task runs emit per-trial result.json (has
            # verifier_result/task_name) plus a job-level one (only stats,
            # no trial data). Count the per-trial files, skip job-level.
            if not r.get("verifier_result") and not r.get("task_name"):
                continue
            got.append(r)
        trials.extend(got)
        per_task.append((task_dir.name, len(got)))
        if len(got) != 1:
            artifact_integrity_failures.append(f"{task_name}:trial_result_count:{len(got)}")
        trajectories = list(task_dir.rglob("miniswe_trajectory.json"))
        if len(trajectories) != 1:
            artifact_integrity_failures.append(
                f"{task_name}:trajectory_artifact_count:{len(trajectories)}"
            )
        if len(trajectories) == 1:
            trial_rewards = any(
                bool((item.get("verifier_result") or {}).get("rewards")) for item in got
            )
            # An errored row (no verifier rewards) must stay ``None`` in the
            # trajectory metrics â€” never silently converted to reward 0. The
            # verifier reward is the only solve signal; a censored/errored task is
            # not an unsolved task.
            reward = None if not trial_rewards else (1 if any(solved(item) for item in got) else 0)
            deep_tasks[task_name] = extract_trajectory(
                trajectories[0],
                task=task_name,
                reward=reward,
                receipt_path=receipt_paths[0] if receipt_paths else None,
                harbor_result=got[0] if got else None,
            )

    missing.extend(task for task in expected if task not in observed_artifact_tasks)
    missing = list(dict.fromkeys(missing))

    central_integrity_report = audit_integrity_run_root(Path("tasks"))
    if central_integrity_report.get("receipts_audited") != len(receipt_metrics):
        artifact_integrity_failures.append(
            "run:central_integrity_receipt_count:"
            f"{central_integrity_report.get('receipts_audited')}/{len(receipt_metrics)}"
        )
    artifact_integrity_failures.extend(
        f"central_integrity:{failure}" for failure in central_integrity_report.get("failures") or ()
    )

    classified_trials = [(trial, classify_trial_outcome(trial)) for trial in trials]
    population_receipt = build_benchmark_population(expected, trials)
    task_population = population_receipt.as_dict()
    # This is the sole authoritative missing-task derivation. Artifact discovery
    # may add diagnostics, but it cannot claim an empty missing set when a declared
    # task has no trial record.
    missing = list(task_population["missing_tasks"])
    for task in task_population["duplicate_tasks"]:
        artifact_integrity_failures.append(f"{task}:duplicate_trial_population")
    for task in task_population["unexpected_tasks"]:
        artifact_integrity_failures.append(f"{task}:unexpected_trial_population")
    outcome_counts = Counter(outcome.value for _, outcome in classified_trials)
    censored_tasks = list(task_population["censored_tasks"])
    errored_tasks = list(task_population["errored_tasks"])
    missing_verifier_tasks = list(task_population["missing_verifier_tasks"])
    graded_tasks = list(task_population["graded_tasks"])
    solved_tasks = list(task_population["solved_tasks"])
    unsolved_graded_tasks = list(task_population["unsolved_graded_tasks"])
    n_solved = len(solved_tasks)
    n_expected = len(expected) if expected else 0
    invalid_intelligence = [
        row["task"]
        for row in receipt_metrics
        if row["repository_intelligence_required"]
        and not row["repository_intelligence_denominator_excluded"]
        and (
            row["repository_intelligence_status"] != "passed"
            or row["repository_graph_degraded_fallback"]
            or row["repository_intelligence_failures"]
        )
    ]
    invalid_dense = [
        row["task"]
        for row in receipt_metrics
        if dense_required
        and row["repository_intelligence_required"]
        and not row["repository_intelligence_denominator_excluded"]
        and not row["preemptive_dense_backend_available"]
    ]
    invalid_provider_deliveries = [
        row["task"] for row in receipt_metrics if row["provider_delivery_failures"]
    ]
    manifest_hashes = sorted(
        {
            str(row.get("manifest_sha256") or "")
            for row in verified_benchmark_manifests.values()
            if str(row.get("manifest_sha256") or "")
        }
    )
    manifest_task_set_valid = set(verified_benchmark_manifests) == set(expected)
    common_manifest_valid = manifest_task_set_valid and len(manifest_hashes) == 1
    if not common_manifest_valid:
        reason = (
            "run_wide_benchmark_manifest_task_set_mismatch"
            if not manifest_task_set_valid
            else "run_wide_benchmark_manifest_hash_mismatch"
        )
        for row in receipt_metrics:
            row["treatment_release_failures"].append(f"{row['task']}:{reason}")
    invalid_treatment_release = [
        row["task"] for row in receipt_metrics if row["treatment_release_failures"]
    ]
    call1_gt_changed_tasks = [
        row["task"] for row in receipt_metrics if row["call1_gt_view_changed"]
    ]
    # Response model/provider identity is the comparison identity.
    # Serving fingerprints remain non-gating metadata.
    observed_fingerprints = {
        fp for row in receipt_metrics for fp in (row.get("system_fingerprints") or ()) if fp
    }
    fingerprint_metadata = sorted(observed_fingerprints)
    observed_executor_models = sorted(
        {model for row in receipt_metrics for model in row.get("executor_models") or () if model}
    )
    observed_executor_providers = sorted(
        {
            provider
            for row in receipt_metrics
            for provider in row.get("executor_providers") or ()
            if provider
        }
    )
    observed_route_ids = sorted(
        {row["provider_route_id"] for row in receipt_metrics if row["provider_route_id"]}
    )
    observed_api_hosts = sorted(
        {row["provider_api_host"] for row in receipt_metrics if row["provider_api_host"]}
    )
    applicable_rows = [row for row in receipt_metrics if row.get("persistent_applicable") is True]
    observed_bootstrap_models = sorted(
        {row["bootstrap_model"] for row in applicable_rows if row["bootstrap_model"]}
    )
    observed_bootstrap_providers = sorted(
        {row["bootstrap_provider"] for row in applicable_rows if row["bootstrap_provider"]}
    )
    observed_selection_modes = sorted(
        {
            str(row.get("persistent_selection_mode") or "")
            for row in applicable_rows
            if row.get("persistent_selection_mode")
        }
    )
    deterministic_selection = observed_selection_modes == ["deterministic_v1"]
    observed_identity_complete = bool(
        len(receipt_metrics) == len(expected)
        and all(row["executor_identity_complete"] for row in receipt_metrics)
        and all(
            row["provider_route_id"]
            and row["provider_api_host"]
            and row["provider_api_base"]
            and not row["provider_credential_in_receipt"]
            for row in receipt_metrics
        )
        and (
            deterministic_selection
            or all(row["bootstrap_model"] and row["bootstrap_provider"] for row in applicable_rows)
        )
    )
    runtime_contracts_by_hash = {
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(): row
        for row in observed_treatment_runtime_contracts
    }
    treatment_runtime_contract = (
        next(iter(runtime_contracts_by_hash.values()))
        if len(runtime_contracts_by_hash) == 1
        and len(observed_treatment_runtime_contracts) == len(expected)
        else {}
    )
    if not treatment_runtime_contract:
        artifact_integrity_failures.append("run:treatment_runtime_contract_unstable_or_missing")
    expected_response_model = str(expected_bootstrap_route.get("expected_response_model") or "")
    expected_adapter_provider = str(expected_bootstrap_route.get("expected_adapter_provider") or "")
    expected_route_id = str(expected_bootstrap_route.get("route_id") or "")
    expected_api_host = str(expected_bootstrap_route.get("api_host") or "")
    observed_identity_stable = bool(
        expected_response_model
        and expected_adapter_provider
        and observed_executor_models == [expected_response_model]
        and observed_executor_providers == [expected_adapter_provider]
        and observed_route_ids == [expected_route_id]
        and observed_api_hosts == [expected_api_host]
        and (
            (
                deterministic_selection
                and not observed_bootstrap_models
                and not observed_bootstrap_providers
            )
            or (
                observed_bootstrap_models == [expected_response_model]
                and observed_bootstrap_providers == [expected_adapter_provider]
            )
        )
    )

    out = ["# TB2 miniswe central matrix (GT-on)", ""]
    out.append("- arm: **certified_full**")
    out.append("- feature: **integrated 17+1**")
    if missing:
        out.append(
            f"> **INCOMPLETE**: {len(missing)} task(s) produced no "
            f"result.json: {', '.join(missing)}. The score below covers "
            "only tasks that reported."
        )
        out.append("")
    out += [
        f"- tasks planned: **{n_expected}**",
        f"- trials returned: **{len(trials)}**",
        f"- graded (verifier produced rewards): **{len(graded_tasks)}**",
        f"- censored (provider/outer infrastructure): **{len(censored_tasks)}**",
        f"- errored (non-censor exception): **{len(errored_tasks)}**",
        f"- missing verifier result: **{len(missing_verifier_tasks)}**",
    ]
    if graded_tasks:
        out.append(
            f"- **solved: {n_solved}/{len(graded_tasks)} "
            f"({100 * n_solved / len(graded_tasks):.1f}% of graded)**"
        )
    if n_expected:
        out.append(
            f"- **solved of planned: {n_solved}/{n_expected} ({100 * n_solved / n_expected:.1f}%)**"
        )
    if invalid_intelligence:
        out.append(
            f"> **INVALID GT TREATMENT**: repository intelligence failed for "
            f"{len(invalid_intelligence)} task(s): {', '.join(invalid_intelligence)}."
        )
    if invalid_dense:
        out.append(
            f"> **INVALID DENSE TREATMENT**: pinned dense retrieval was unavailable "
            f"for {len(invalid_dense)} applicable task(s): {', '.join(invalid_dense)}."
        )
    if invalid_provider_deliveries:
        out.append(
            f"> **INVALID PROVIDER DELIVERY**: deterministic delivery audit failed "
            f"for {len(invalid_provider_deliveries)} task(s): "
            f"{', '.join(invalid_provider_deliveries)}."
        )
    if invalid_treatment_release:
        out.append(
            f"> **INVALID TREATMENT RELEASE**: mechanical task release gate failed "
            f"for {len(invalid_treatment_release)} task(s): "
            f"{', '.join(invalid_treatment_release)}."
        )
    if call1_gt_changed_tasks:
        out.append(
            f"> **GT CHANGED CALL 1**: final call-1 provider bytes differ from "
            f"the same run's recorded pre-GT control for {len(call1_gt_changed_tasks)} "
            f"task(s): {', '.join(call1_gt_changed_tasks)}. This is intervention "
            "accounting, not a baseline-parity failure."
        )
    if fingerprint_metadata:
        out.append(
            f"> **SERVING FINGERPRINT METADATA**: {fingerprint_metadata}. The model "
            "ID remains the comparison identity; fingerprint drift does not excuse "
            "losses or block the frozen-baseline promotion verdict."
        )
    out += ["", "| task | solved | rewards / error |", "|---|---|---|"]
    for t in sorted(trials, key=lambda x: x.get("task_name") or ""):
        name = (t.get("task_name") or t.get("trial_name", "?")).split("__")[0]
        rewards = (t.get("verifier_result") or {}).get("rewards")
        exc = (t.get("exception_info") or {}).get("exception_type")
        mark = "yes" if rewards and solved(t) else ("no" if rewards else "-")
        out.append(
            f"| {name} | {mark} | {json.dumps(rewards) if rewards else (exc or 'no reward')} |"
        )

    out += [
        "",
        "## Central runtime metrics",
        "",
        (
            "| task | total tokens | uncached input | calls | model actions | "
            "model tool actions | controller execs | cached reads | checks | "
            "changes | failed | repeated | guidance delivered/candidates/suppressed | "
            "frontier deliveries/chars | graph nodes/edges | mirror/index ms | "
            "intelligence | censored |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in sorted(receipt_metrics, key=lambda x: x["task"]):
        out.append(
            f"| {row['task']} | {row['total_tokens'] or '-'} | "
            f"{row['uncached_input_tokens'] or '-'} | {row['api_calls'] or '-'} | "
            f"{row['actions'] or '-'} | {row['effective_actions'] or '-'} | "
            f"{row['controller_environment_execs'] or 0} | "
            f"{row['controller_cached_reads'] or 0} | {row['check_actions'] or 0} | "
            f"{row['workspace_change_actions'] or 0} | {row['failed_actions'] or 0} | "
            f"{row['repeated_commands'] or 0} | {row['guidance_events'] or 0}/"
            f"{row['guidance_candidates'] or 0}/{row['guidance_suppressed'] or 0} | "
            f"{row['context_frontier_deliveries'] or 0}/"
            f"{row['context_frontier_chars_added'] or 0} | "
            f"{row['repository_graph_nodes'] or 0}/"
            f"{row['repository_graph_edges'] or 0} | "
            f"{row['repository_mirror_transfer_ms'] or 0}/"
            f"{row['repository_index_refresh_ms'] or 0} | "
            f"{row['repository_intelligence_status'] or 'unreported'} | "
            f"{row['censored'] or False} |"
        )

    merged_payload = {
        "expected_tasks": n_expected,
        "missing_tasks": missing,
        "task_population": task_population,
        "n_trials": len(trials),
        "n_graded": len(graded_tasks),
        "graded_tasks": graded_tasks,
        "solved_tasks": solved_tasks,
        "unsolved_graded_tasks": unsolved_graded_tasks,
        "n_errored": len(errored_tasks),
        "errored_tasks": errored_tasks,
        "n_censored": len(censored_tasks),
        "censored_tasks": censored_tasks,
        "n_missing_verifier": len(missing_verifier_tasks),
        "missing_verifier_tasks": missing_verifier_tasks,
        "central_integrity_audit": central_integrity_report,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "n_solved": n_solved,
        "invalid_repository_intelligence_tasks": invalid_intelligence,
        "invalid_dense_backend_tasks": invalid_dense,
        "invalid_provider_delivery_tasks": invalid_provider_deliveries,
        "invalid_treatment_release_tasks": invalid_treatment_release,
        "artifact_integrity_failures": list(dict.fromkeys(artifact_integrity_failures)),
        "provider_free_certification": {
            "valid": provider_free_valid,
            "receipt": provider_free_receipt,
            "mechanical_completeness": provider_free_mechanical_proof,
            "documentation": provider_free_documentation_proof,
        },
        "bootstrap_route_certification": {
            "valid": bootstrap_route_valid,
            "canary_failures": bootstrap_canary_failures,
            "route_contract": bootstrap_route,
            "response_identity": (
                (bootstrap_canary.get("receipt") or {}).get("response_identity") or {}
            ),
        },
        "frozen_outcome_prediction": {
            "path": prediction_path.as_posix(),
            "sha256": prediction_sha256,
            "expected_sha256": expected_prediction_sha256,
            "hash_valid": prediction_hash_valid,
        },
        "dense_backend_required": dense_required,
        "call1_gt_changed_tasks": call1_gt_changed_tasks,
        "fingerprint_metadata": fingerprint_metadata,
        "trial_results": trials,
        "receipt_metrics": receipt_metrics,
        "treatment_manifest": {
            "model": os.environ["CANARY_MODEL"],
            "gt_commit": os.environ["GT_COMMIT"],
            "run_id": os.environ["GT_RUN_ID"],
            "system_fingerprints": sorted(observed_fingerprints),
            "profile_id": os.environ["COMPARISON_PROFILE"],
            "planned_task_ids": expected,
            "task_set_sha256": os.environ["TASK_SET_SHA256"],
            "frozen_outcome_prediction_sha256": prediction_sha256,
            "frozen_outcome_prediction_hash_valid": prediction_hash_valid,
            "observed_identity": {
                "executor_models": observed_executor_models,
                "executor_providers": observed_executor_providers,
                "bootstrap_model": (
                    observed_bootstrap_models[0] if len(observed_bootstrap_models) == 1 else ""
                ),
                "bootstrap_provider": (
                    observed_bootstrap_providers[0]
                    if len(observed_bootstrap_providers) == 1
                    else ""
                ),
                "selection_mode": (
                    observed_selection_modes[0] if len(observed_selection_modes) == 1 else ""
                ),
                "selection_provider_calls": sum(
                    int(row.get("persistent_selection_provider_calls") or 0)
                    for row in applicable_rows
                ),
                "canary_model": os.environ["CANARY_MODEL"],
                "canary_provider": os.environ["CANARY_PROVIDER"],
                "route": observed_route_ids[0] if len(observed_route_ids) == 1 else "",
                "api_host": observed_api_hosts[0] if len(observed_api_hosts) == 1 else "",
                "complete": observed_identity_complete,
                "stable": observed_identity_stable,
            },
            "runtime_contract": treatment_runtime_contract,
            "benchmark_manifest_sha256s": manifest_hashes,
            "common_benchmark_manifest": common_manifest_valid,
        },
    }
    lifecycle_report = build_feature_lifecycle_report(
        feature_receipts,
        forced_feature_ids=CENTRAL_FEATURE_IDS,
        forced_proof={
            "status": "passed",
            "exact_commit": os.environ["GT_COMMIT"],
            "feature_ids": list(CENTRAL_FEATURE_IDS),
        },
        expected_task_ids=expected,
    )
    treatment = treatment_from_merged(merged_payload)
    promotion_report = assess_tb2_promotion(baseline, treatment)
    forensics_report = build_regression_forensics(
        baseline,
        treatment,
        treatment_artifact_root=Path("tasks"),
    )
    profile_config = ((baseline.get("manifest") or {}).get("profiles") or {}).get(
        os.environ["COMPARISON_PROFILE"], {}
    )
    diagnostic_only = bool(profile_config.get("diagnostic_only"))
    merged_payload["feature_lifecycle_passed"] = lifecycle_report["passed"]
    merged_payload["diagnostic_only"] = diagnostic_only
    merged_payload["promotion_passed"] = None if diagnostic_only else promotion_report.passed
    merged_payload["forensics_complete"] = forensics_report["passed"]
    integrity_failures = [
        *artifact_integrity_failures,
        *(f"repository_intelligence:{task}" for task in invalid_intelligence),
        *(f"dense_backend:{task}" for task in invalid_dense),
        *(f"provider_delivery:{task}" for task in invalid_provider_deliveries),
        *(f"treatment_release:{task}" for task in invalid_treatment_release),
        *(f"missing_task:{task}" for task in missing),
    ]
    benchmark_reports = build_benchmark_reports(
        expected_tasks=expected,
        baseline=baseline,
        treatment=treatment,
        receipt_metrics=receipt_metrics,
        integrity_failures=integrity_failures,
        efficiency={
            "common_solved_resource_deltas": (promotion_report.common_solved_resource_deltas),
            "full_profile_resource_deltas": promotion_report.full_profile_resource_deltas,
            "per_task_resource_deltas": promotion_report.per_task_resource_deltas,
            "per_task_bound_failures": list(promotion_report.per_task_bound_failures),
        },
    )
    merged_payload["integrity_report_passed"] = benchmark_reports["integrity"]["passed"]

    out += [
        "",
        "## Release verdicts",
        "",
        f"- 17+1 mechanism lifecycle: **{'PASS' if lifecycle_report['passed'] else 'FAIL'}**",
        (
            "- frozen-baseline promotion: **NOT APPLIED (diagnostic smoke profile)**"
            if diagnostic_only
            else f"- frozen-baseline promotion: **{'PASS' if promotion_report.passed else 'FAIL'}**"
        ),
        (
            "- legacy features naturally fired: "
            f"**{lifecycle_report['naturally_fired_legacy_feature_count']}/17**"
        ),
        f"- solve flips: **{', '.join(promotion_report.flips) or 'none'}**",
        f"- baseline solve losses: **{', '.join(promotion_report.losses) or 'none'}**",
        (
            "- frozen outcome prediction hash: **PASS**"
            if prediction_hash_valid
            else "- frozen outcome prediction hash: **FAIL**"
        ),
    ]

    Path("merged.json").write_text(json.dumps(merged_payload, indent=2), encoding="utf-8")
    Path("feature_lifecycle_report.json").write_text(
        json.dumps(lifecycle_report, indent=2), encoding="utf-8"
    )
    Path("tb2_treatment.json").write_text(json.dumps(treatment, indent=2), encoding="utf-8")
    Path("promotion_report.json").write_text(
        json.dumps(promotion_report.as_dict(), indent=2), encoding="utf-8"
    )
    Path("regression_forensics.json").write_text(
        json.dumps(forensics_report, indent=2), encoding="utf-8"
    )
    for report_name, report in benchmark_reports.items():
        Path(f"{report_name}_report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    Path("deep_metrics_certified_full.json").write_text(
        json.dumps(
            {
                "schema": "central-deep-metrics-v2",
                "arm": "certified_full",
                "run_id": os.environ["GT_RUN_ID"],
                "tasks": deep_tasks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    body = "\n".join(out) + "\n"
    Path("SUMMARY.md").write_text(body, encoding="utf-8")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(body)
    print(body[:4000])
    if (
        invalid_intelligence
        or invalid_dense
        or invalid_provider_deliveries
        or invalid_treatment_release
        or artifact_integrity_failures
        or not benchmark_reports["integrity"]["passed"]
        or not prediction_hash_valid
        or not lifecycle_report["passed"]
        or (not diagnostic_only and not promotion_report.passed)
    ):
        return 2

    return 0


def main() -> int:
    return merge_results()


if __name__ == "__main__":
    raise SystemExit(main())
