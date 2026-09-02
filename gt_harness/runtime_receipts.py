"""Bind the installed Mini-SWE runtime to durable benchmark receipts.

This module deliberately derives claims from files produced by the running
installed product.  It does not infer a successful treatment from the later
benchmark grade and it refuses inconsistent provider counts or model identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from gt_engine.delivery_budget import (
    DELIVERY_BYTE_LIMITS,
    MAX_TASK_DELIVERIES,
    TOTAL_DELIVERY_BYTE_LIMIT,
    delivery_byte_limit,
)

_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_TOTAL_DELIVERY_BYTE_LIMIT = TOTAL_DELIVERY_BYTE_LIMIT


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    _atomic_bytes(path, payload)


def _single_optional(root: Path, name: str) -> tuple[Path | None, dict[str, Any] | None]:
    paths = sorted(root.rglob(name)) if root.is_dir() else []
    if len(paths) > 1:
        raise ValueError(f"duplicate_runtime_artifact:{name}")
    if not paths:
        return None, None
    return paths[0], _read_object(paths[0])


def _events(state_dir: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    paths = sorted(state_dir.rglob("events.jsonl")) if state_dir.is_dir() else []
    if len(paths) > 1:
        raise ValueError("duplicate_runtime_artifact:events.jsonl")
    if not paths:
        return None, []
    rows: list[dict[str, Any]] = []
    for line in paths[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_event_journal_json") from exc
        if not isinstance(row, dict):
            raise ValueError("invalid_event_journal_row")
        rows.append(row)
    return paths[0], rows


def _delivery_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") not in {"evidence_delivery", "context_addition_delivery"}:
            continue
        event_hash = str(row.get("event_hash") or "")
        artifact_hash = str(row.get("artifact_sha256") or "")
        if not _SHA64.fullmatch(event_hash):
            raise ValueError("invalid_evidence_delivery_event_hash")
        if artifact_hash and not _SHA64.fullmatch(artifact_hash):
            raise ValueError("invalid_evidence_delivery_artifact_hash")
        lane = str(row.get("lane") or "sealed")
        kind = str(row.get("kind") or row.get("evidence_type") or "")
        payload_sha256 = str(row.get("payload_sha256") or "")
        if lane not in {"prompt", "sealed"}:
            raise ValueError("invalid_delivery_lane")
        if lane == "prompt" and (
            kind not in {"context_contract", "context_delta"}
            or not _SHA64.fullmatch(payload_sha256)
        ):
            raise ValueError("invalid_prompt_delivery_kind")
        if lane != "prompt" and payload_sha256 and not _SHA64.fullmatch(payload_sha256):
            raise ValueError("invalid_delivery_payload_hash")
        deliveries.append(
            {
                "event_hash": event_hash,
                "lane": lane,
                "kind": kind,
                "dedup_key": str(row.get("dedup_key") or ""),
                "evidence_type": str(row.get("evidence_type") or ""),
                "artifact_sha256": artifact_hash or None,
                "payload_sha256": payload_sha256 or None,
                "delivery_identity": payload_sha256 or None,
                "action_index": int(row.get("action_index") or 0),
                "rendered_bytes": int(row.get("rendered_bytes") or 0),
                "semantics": str(row.get("semantics") or ""),
                "target": str(row.get("target") or ""),
            }
        )
    return deliveries


def _provider_delivery_receipts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = {
        (str(row.get("dedup_key") or ""), int(row.get("iteration") or 0)): row
        for row in events
        if row.get("event") in {"evidence_delivery", "context_addition_delivery"}
        and row.get("dedup_key")
    }
    provider_rows = [row for row in events if row.get("event") == "provider_delivery"]
    receipts: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") != "receipt" or row.get("transition") != "delivered":
            continue
        iteration = int(row.get("iteration") or 0)
        match = evidence.get((str(row.get("dedup_key") or ""), iteration))
        if match is None:
            raise ValueError("delivery_receipt_evidence_join_failed")
        provider = next(
            (
                candidate
                for candidate in provider_rows
                if int(candidate.get("sequence") or 0) > int(row.get("sequence") or 0)
            ),
            None,
        )
        index = len(receipts) + 1
        kind = "repository_start" if index == 1 else "repository_update"
        lane = str(match.get("lane") or "sealed")
        delivery_kind = str(match.get("kind") or match.get("evidence_type") or "")
        context_byte_count = int(match.get("rendered_bytes") or 0)
        byte_limit = delivery_byte_limit(lane=lane, kind=delivery_kind)
        if context_byte_count > byte_limit:
            raise ValueError("delivery_context_budget_exceeded")
        delivered_before_call = int(provider.get("iteration") or 0) if provider else 0
        receipts.append(
            {
                "schema": "gt.provider_delivery.v2",
                "delivery_index": index,
                "kind": kind,
                "lane": lane,
                "delivery_kind": delivery_kind,
                "evidence_type": str(row.get("evidence_type") or ""),
                "dedup_key": str(row.get("dedup_key") or ""),
                "context_sha256": str(row.get("payload_hash") or ""),
                "delivery_identity": str(row.get("payload_hash") or ""),
                "context_byte_count": context_byte_count,
                "byte_limit": byte_limit,
                "event_sequence": int(match.get("sequence") or 0),
                "observed_iteration": iteration,
                "delivered_before_call": delivered_before_call,
                "same_observation": delivered_before_call == iteration + 1,
                "provider_request_id": str(provider.get("request_id") or "") if provider else "",
            }
        )
    return receipts


def _delivery_refusals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_reasons = {
        "delivery_byte_ceiling",
        "task_delivery_byte_ceiling",
        "task_delivery_dose_ceiling",
        "task_delivery_storm_backstop",
        "duplicate_delivery_identity",
    }
    refusals: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") != "delivery_refused":
            continue
        reason = str(row.get("reason") or "")
        payload_sha256 = str(row.get("payload_sha256") or "")
        lane = str(row.get("lane") or "")
        if reason not in allowed_reasons:
            raise ValueError("invalid_delivery_refusal_reason")
        if lane not in {"prompt", "sealed"}:
            raise ValueError("invalid_delivery_refusal_lane")
        if not _SHA64.fullmatch(payload_sha256):
            raise ValueError("invalid_delivery_refusal_payload_hash")
        if not _SHA64.fullmatch(str(row.get("event_hash") or "")):
            raise ValueError("invalid_delivery_refusal_event_hash")
        if int(row.get("candidate_ordinal") or 0) < 1:
            raise ValueError("invalid_delivery_refusal_ordinal")
        refusals.append(
            {
                "event_hash": str(row.get("event_hash") or ""),
                "event_sequence": int(row.get("sequence") or 0),
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "dedup_key": str(row.get("dedup_key") or ""),
                "reason": reason,
                "candidate_ordinal": int(row.get("candidate_ordinal") or 0),
                "rendered_bytes": int(row.get("rendered_bytes") or 0),
                "payload_sha256": payload_sha256,
                "delivery_identity": str(row.get("delivery_identity") or payload_sha256),
                "admitted_count": int(row.get("admitted_count") or 0),
                "admitted_bytes": int(row.get("admitted_bytes") or 0),
            }
        )
    return refusals


def _provider_usage(
    events: list[dict[str, Any]], *, attempted_calls: int
) -> dict[str, int | float]:
    responses = [row for row in events if row.get("event") == "provider_response"]
    completed_calls = len(responses)
    if completed_calls > attempted_calls:
        raise ValueError("provider_response_count_exceeds_attempts")
    prompt = completion = cached = 0
    cost = 0.0
    for row in responses:
        usage = row.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("provider_response_usage_missing")
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        cached += int(details.get("cached_tokens") or 0)
        cost += float(usage.get("cost") or 0)
    return {
        "provider_completed_calls": completed_calls,
        "provider_failed_calls": attempted_calls - completed_calls,
        "input_tokens": prompt,
        "output_tokens": completion,
        "cached_tokens": cached,
        "total_cost": round(cost, 12),
    }


def issue_runtime_receipts(
    *,
    report_path: Path,
    trajectory_path: Path,
    state_dir: Path,
    product_receipt_path: Path,
    adapter_receipt_path: Path,
    task_id: str,
    product_source_sha: str,
    treatment: str,
    requested_model: str,
    scaffold_version: str,
    time_budget_seconds: int,
) -> dict[str, Any]:
    """Issue the product and adapter receipts or fail without partial output."""

    if not task_id.strip():
        raise ValueError("task_id_required")
    if not _SHA40.fullmatch(product_source_sha):
        raise ValueError("product_source_sha_invalid")
    if treatment not in {"bare", "groundtruth"}:
        raise ValueError("treatment_invalid")
    if time_budget_seconds < 1:
        raise ValueError("time_budget_seconds_invalid")

    report = _read_object(report_path)
    trajectory = _read_object(trajectory_path)
    info = trajectory.get("info")
    info = info if isinstance(info, dict) else {}
    model_stats = info.get("model_stats")
    model_stats = model_stats if isinstance(model_stats, dict) else {}
    provider_calls = int(model_stats.get("api_calls") or 0)
    gt = report.get("gt")
    gt = gt if isinstance(gt, dict) else {}
    terminal_requests = gt.get("terminal_requests")
    if terminal_requests is not None and int(terminal_requests) != provider_calls:
        raise ValueError("provider_call_count_mismatch")

    reported_model = str(gt.get("provider_reported_model") or requested_model)
    effective_model = str(gt.get("resolved_model") or requested_model)
    if reported_model != requested_model:
        raise ValueError("provider_model_mismatch")
    report_model = str(report.get("model") or requested_model)
    if report_model != requested_model:
        raise ValueError("runner_model_mismatch")

    events_path, event_rows = _events(state_dir)
    delivery_events = _delivery_rows(event_rows)
    evidence_events = [row for row in delivery_events if row["lane"] == "sealed"]
    prompt_events = [row for row in delivery_events if row["lane"] == "prompt"]
    refused_deliveries = _delivery_refusals(event_rows)
    deliveries = _provider_delivery_receipts(event_rows)
    if len(deliveries) != len(delivery_events):
        raise ValueError("delivery_receipt_census_mismatch")
    delivery_identities = [str(row.get("delivery_identity") or "") for row in deliveries]
    if len(delivery_identities) != len(set(delivery_identities)):
        raise ValueError("duplicate_delivery_identity")
    if sum(row["context_byte_count"] for row in deliveries) > _TOTAL_DELIVERY_BYTE_LIMIT:
        raise ValueError("delivery_total_budget_exceeded")
    delivered_keys = {str(row.get("dedup_key") or "") for row in delivery_events}
    delivered_identities = {str(row.get("delivery_identity") or "") for row in delivery_events}
    for refusal in refused_deliveries:
        duplicate = refusal["reason"] == "duplicate_delivery_identity"
        later_delivery = any(
            int(delivery["event_sequence"]) > int(refusal["event_sequence"])
            and (
                delivery["delivery_identity"] == refusal["delivery_identity"]
                or delivery["dedup_key"] == refusal["dedup_key"]
            )
            for delivery in deliveries
        )
        if later_delivery:
            raise ValueError("refused_then_delivered")
        if refusal["dedup_key"] in delivered_keys and not duplicate:
            raise ValueError("refused_delivery_present")
        if duplicate and refusal["delivery_identity"] not in delivered_identities:
            raise ValueError("invalid_duplicate_delivery_refusal")
    provider_usage = _provider_usage(event_rows, attempted_calls=provider_calls)
    repro_path, reproduction = _single_optional(state_dir, "reproducibility_manifest.json")
    graph_path, graph = _single_optional(state_dir, "graph.manifest.json")
    reproduction = reproduction or {}
    graph = graph or {}
    dense_rows = [row for row in event_rows if row.get("event") == "dense_index_ready"]
    if len(dense_rows) > 1:
        raise ValueError("duplicate_dense_index_receipt")
    dense_index = (
        {
            "schema": "gt.dense_index_receipt.v1",
            "query_ready": dense_rows[0].get("query_ready") is True,
            "model_sha256": dense_rows[0].get("model_sha256"),
            "tokenizer_sha256": dense_rows[0].get("tokenizer_sha256"),
            "dimension": dense_rows[0].get("dimension"),
            "document_count": dense_rows[0].get("document_count"),
            "query_result_count": dense_rows[0].get("query_result_count"),
            "index_sha256": dense_rows[0].get("index_sha256"),
            "reason": dense_rows[0].get("reason"),
        }
        if dense_rows
        else {
            "schema": "gt.dense_index_receipt.v1",
            "query_ready": False,
            "reason": "dense_index_receipt_missing",
        }
    )
    provider_receipts = reproduction.get("provider_receipts")
    provider_receipts = provider_receipts if isinstance(provider_receipts, dict) else {}
    manifest_count = provider_receipts.get("request_count")
    if manifest_count is not None and int(manifest_count) != provider_calls:
        raise ValueError("provider_manifest_count_mismatch")

    exit_code = int(report.get("exit_code") or 0)
    terminal = str(report.get("terminal") or "internal_error")
    status = "COMPLETED" if exit_code == 0 else "ERROR"
    usage = gt.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    if int(usage.get("prompt_tokens") or 0) != provider_usage["input_tokens"]:
        raise ValueError("provider_prompt_token_count_mismatch")
    if int(usage.get("completion_tokens") or 0) != provider_usage["output_tokens"]:
        raise ValueError("provider_completion_token_count_mismatch")
    event_journal = reproduction.get("event_journal")
    event_journal = event_journal if isinstance(event_journal, dict) else {}
    if not (
        event_journal.get("valid") is True
        and not event_journal.get("issues")
        and int(event_journal.get("event_count") or 0) == len(event_rows)
        and event_rows
        and event_journal.get("event_head") == event_rows[-1].get("event_hash")
    ):
        raise ValueError("event_journal_conservation_failed")
    treatment_receipt: dict[str, Any] = {
        "schema": "gt.miniswe_treatment_receipt.v1",
        "treatment": treatment,
        "treatment_status": (
            "ACTIVE"
            if treatment == "groundtruth" and report.get("gt_mode") != "off"
            else "INACTIVE"
        ),
        "gt_mode": str(report.get("gt_mode") or "off"),
        "contract_shipped": bool(gt.get("contract_shipped")),
        "verified": bool(gt.get("verified")),
        "unmet_predicates": list(gt.get("unmet_predicates") or []),
        "unverified_predicates": list(gt.get("unverified_predicates") or []),
        "delivery_count": len(deliveries),
        "prompt_delivery_count": len(prompt_events),
        "sealed_delivery_count": len(evidence_events),
        "evidence_items_delivered": len(evidence_events),
        "evidence_event_count": len(evidence_events),
        "evidence_deliveries": evidence_events,
        "prompt_context_deliveries": prompt_events,
        "provider_delivery_receipts": deliveries,
        "refused_deliveries": refused_deliveries,
        "delivery_budget": {
            "unit": "utf8_bytes",
            "conversion_from_legacy_tokens": "4_bytes_per_token",
            "sealed_limit": DELIVERY_BYTE_LIMITS["sealed"],
            "prompt_contract_limit": DELIVERY_BYTE_LIMITS["context_contract"],
            "prompt_delta_limit": DELIVERY_BYTE_LIMITS["context_delta"],
            "total_limit": _TOTAL_DELIVERY_BYTE_LIMIT,
            "total_observed": sum(row["context_byte_count"] for row in deliveries),
            "task_delivery_limit": MAX_TASK_DELIVERIES,
            "admitted_count": len(deliveries),
            "refused_count": len(refused_deliveries),
        },
        "retrieval_mode": "hybrid_required",
        "dense_index_receipt": dense_index,
        "event_journal": event_journal,
        "completion_state_event_journal": dict(gt.get("event_journal") or {}),
        "provider_identity": {
            "requested": requested_model,
            "resolved": effective_model,
            "reported": reported_model,
            "match": reported_model == requested_model,
        },
        "reproducibility_manifest": reproduction,
        "graph_certification": graph,
    }
    product: dict[str, Any] = {
        "schema": "gt.run_receipt.v1",
        "task_id": task_id,
        "status": status,
        "terminal": terminal,
        "stop_reason": str(info.get("exit_status") or terminal),
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "provider_calls": provider_calls,
        **provider_usage,
        "research_valid": bool(report.get("research_valid")),
        "treatment_receipt": treatment_receipt,
        "integrity": {
            "report_sha256": _sha256(report_path),
            "trajectory_sha256": _sha256(trajectory_path),
            "events_sha256": _sha256(events_path) if events_path else None,
            "reproducibility_manifest_sha256": (_sha256(repro_path) if repro_path else None),
            "graph_manifest_sha256": _sha256(graph_path) if graph_path else None,
        },
    }
    adapter = {
        "schema": "gt.benchmark_adapter_receipt.v1",
        "task_id": task_id,
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
    }

    trajectory_copy = product_receipt_path.with_name("gt-run.trajectory.json")
    _atomic_bytes(trajectory_copy, trajectory_path.read_bytes())
    _atomic_json(adapter_receipt_path, adapter)
    _atomic_json(product_receipt_path, product)
    return product


def issue_runtime_receipt_failure(
    *,
    report_path: Path,
    trajectory_path: Path,
    product_receipt_path: Path,
    adapter_receipt_path: Path,
    task_id: str,
    product_source_sha: str,
    treatment: str,
    requested_model: str,
    scaffold_version: str,
    time_budget_seconds: int,
    terminal: str,
    exit_code: int,
    error: BaseException,
) -> dict[str, Any]:
    """Write fail-closed receipts without changing the completed task outcome.

    This path makes a receipt-construction defect visible to downstream
    attestation while preserving the agent's native terminal state and exit
    code.  It intentionally makes no treatment, evidence, or research claim.
    """

    report: dict[str, Any] = {}
    trajectory: dict[str, Any] = {}
    try:
        report = _read_object(report_path)
    except ValueError:
        pass
    try:
        trajectory = _read_object(trajectory_path)
    except ValueError:
        pass
    model_identity = report.get("model_identity")
    model_identity = model_identity if isinstance(model_identity, dict) else {}
    gt = report.get("gt")
    gt = gt if isinstance(gt, dict) else {}
    effective_model = str(gt.get("resolved_model") or model_identity.get("resolved") or "") or None
    info = trajectory.get("info")
    info = info if isinstance(info, dict) else {}
    model_stats = info.get("model_stats")
    model_stats = model_stats if isinstance(model_stats, dict) else {}
    provider_calls = model_stats.get("api_calls")
    provider_calls = int(provider_calls) if provider_calls is not None else None

    failure = {
        "code": "runtime_receipt_issuance_failed",
        "type": type(error).__name__,
        "message": str(error),
    }
    integrity = {
        "report_sha256": _sha256(report_path) if report_path.is_file() else None,
        "trajectory_sha256": (_sha256(trajectory_path) if trajectory_path.is_file() else None),
    }
    product: dict[str, Any] = {
        "schema": "gt.run_receipt.v1",
        "task_id": task_id,
        "status": "ERROR",
        "terminal": terminal,
        "stop_reason": terminal,
        "exit_code": exit_code,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "provider_calls": provider_calls,
        "research_valid": False,
        "receipt_issuance": failure,
        "integrity": integrity,
    }
    adapter: dict[str, Any] = {
        "schema": "gt.benchmark_adapter_receipt.v1",
        "task_id": task_id,
        "status": "ERROR",
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "receipt_issuance": failure,
    }

    if trajectory_path.is_file():
        trajectory_copy = product_receipt_path.with_name("gt-run.trajectory.json")
        _atomic_bytes(trajectory_copy, trajectory_path.read_bytes())
    _atomic_json(adapter_receipt_path, adapter)
    _atomic_json(product_receipt_path, product)
    return product


def verify_runtime_receipt(receipt_path: Path) -> list[str]:
    """Verify a completed receipt against the exact adjacent runtime files."""

    receipt = _read_object(receipt_path)
    errors: list[str] = []
    if receipt.get("schema") != "gt.run_receipt.v1":
        errors.append("product_receipt_schema")
    if receipt.get("status") != "COMPLETED":
        errors.append("product_not_completed")
    if receipt.get("treatment") != "groundtruth":
        errors.append("product_treatment_mismatch")
    if receipt.get("agent_scaffold_version") != "2.4.6":
        errors.append("product_scaffold_version_mismatch")

    trajectory_path = receipt_path.with_name("gt-run.trajectory.json")
    report_path = receipt_path.with_name("miniswe_report.json")
    integrity = receipt.get("integrity")
    integrity = integrity if isinstance(integrity, dict) else {}
    if not trajectory_path.is_file():
        errors.append("product_trajectory_missing")
        trajectory: dict[str, Any] = {}
    else:
        trajectory = _read_object(trajectory_path)
        if integrity.get("trajectory_sha256") != _sha256(trajectory_path):
            errors.append("product_trajectory_digest_mismatch")
    if not report_path.is_file():
        errors.append("product_report_missing")
        report: dict[str, Any] = {}
    else:
        report = _read_object(report_path)
        if integrity.get("report_sha256") != _sha256(report_path):
            errors.append("product_report_digest_mismatch")
    calls = ((trajectory.get("info") or {}).get("model_stats") or {}).get("api_calls")
    if calls is None:
        errors.append("product_provider_calls_missing")
    elif int(calls) != int(receipt.get("provider_calls") or 0):
        errors.append("product_provider_calls_mismatch")
    attempted_calls = int(receipt.get("provider_calls") or 0)
    completed_calls = int(receipt.get("provider_completed_calls") or 0)
    failed_calls = int(receipt.get("provider_failed_calls") or 0)
    if (
        min(attempted_calls, completed_calls, failed_calls) < 0
        or completed_calls + failed_calls != attempted_calls
    ):
        errors.append("product_provider_call_conservation_failed")
    gt_report = report.get("gt") if isinstance(report, dict) else None
    gt_report = gt_report if isinstance(gt_report, dict) else {}
    report_usage = gt_report.get("usage")
    report_usage = report_usage if isinstance(report_usage, dict) else {}
    if int(report_usage.get("prompt_tokens") or 0) != int(receipt.get("input_tokens") or 0):
        errors.append("product_input_token_conservation_failed")
    if int(report_usage.get("completion_tokens") or 0) != int(receipt.get("output_tokens") or 0):
        errors.append("product_output_token_conservation_failed")
    if str(gt_report.get("resolved_model") or receipt.get("requested_model")) != receipt.get(
        "effective_model"
    ):
        errors.append("product_effective_model_report_mismatch")
    state_dir = receipt_path.parent / "gt-state"
    try:
        events_path, runtime_events = _events(state_dir)
    except ValueError as exc:
        errors.append(str(exc))
        events_path, runtime_events = None, []
    if events_path is None:
        errors.append("product_event_journal_missing")
    else:
        if integrity.get("events_sha256") != _sha256(events_path):
            errors.append("product_event_journal_digest_mismatch")
        journal = (receipt.get("treatment_receipt") or {}).get("event_journal") or {}
        if (
            int(journal.get("event_count") or 0) != len(runtime_events)
            or not runtime_events
            or journal.get("event_head") != runtime_events[-1].get("event_hash")
        ):
            errors.append("product_event_journal_conservation_failed")
        try:
            observed_usage = _provider_usage(runtime_events, attempted_calls=attempted_calls)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            for field in (
                "provider_completed_calls",
                "provider_failed_calls",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "total_cost",
            ):
                if observed_usage[field] != receipt.get(field):
                    errors.append(f"product_{field}_conservation_failed")

    treatment = receipt.get("treatment_receipt")
    if not isinstance(treatment, dict):
        errors.append("treatment_receipt_missing")
        return errors
    if treatment.get("schema") != "gt.miniswe_treatment_receipt.v1":
        errors.append("treatment_receipt_schema")
    if treatment.get("treatment_status") != "ACTIVE":
        errors.append("treatment_not_active")
    if treatment.get("contract_shipped") is not True:
        errors.append("treatment_contract_not_shipped")
    evidence_events = treatment.get("evidence_deliveries")
    if not isinstance(evidence_events, list):
        errors.append("treatment_deliveries_invalid")
        evidence_events = []
    prompt_events = treatment.get("prompt_context_deliveries")
    if not isinstance(prompt_events, list):
        errors.append("treatment_prompt_deliveries_invalid")
        prompt_events = []
    refused_deliveries = treatment.get("refused_deliveries")
    if not isinstance(refused_deliveries, list):
        errors.append("treatment_refused_deliveries_invalid")
        refused_deliveries = []
    deliveries = treatment.get("provider_delivery_receipts")
    if not isinstance(deliveries, list):
        errors.append("treatment_provider_deliveries_invalid")
        deliveries = []
    delivery_identities = [
        str(row.get("delivery_identity") or row.get("context_sha256") or "")
        for row in deliveries
        if isinstance(row, dict)
    ]
    if len(delivery_identities) != len(deliveries) or len(delivery_identities) != len(
        set(delivery_identities)
    ):
        errors.append("treatment_duplicate_delivery_identity")
    try:
        observed_delivery_events = _delivery_rows(runtime_events)
        observed_refusals = _delivery_refusals(runtime_events)
        observed_provider_deliveries = _provider_delivery_receipts(runtime_events)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if prompt_events != [row for row in observed_delivery_events if row["lane"] == "prompt"]:
            errors.append("treatment_prompt_delivery_census_mismatch")
        if evidence_events != [row for row in observed_delivery_events if row["lane"] == "sealed"]:
            errors.append("treatment_sealed_delivery_census_mismatch")
        if refused_deliveries != observed_refusals:
            errors.append("treatment_refused_delivery_census_mismatch")
        if deliveries != observed_provider_deliveries:
            errors.append("treatment_provider_delivery_census_mismatch")
    all_delivery_events = [*evidence_events, *prompt_events]
    if int(treatment.get("delivery_count") or 0) != len(deliveries) or len(deliveries) != len(
        all_delivery_events
    ):
        errors.append("treatment_delivery_count_mismatch")
    if int(treatment.get("prompt_delivery_count") or 0) != len(prompt_events):
        errors.append("treatment_prompt_delivery_count_mismatch")
    if int(treatment.get("sealed_delivery_count") or 0) != len(evidence_events):
        errors.append("treatment_sealed_delivery_count_mismatch")
    if len(deliveries) > MAX_TASK_DELIVERIES:
        errors.append("treatment_delivery_limit_exceeded")
    core_evidence = int(treatment.get("evidence_items_delivered") or 0)
    if core_evidence < 0 or core_evidence != len(evidence_events):
        errors.append("treatment_evidence_count_invalid")
    event_hashes = [
        str(row.get("event_hash") or "") for row in all_delivery_events if isinstance(row, dict)
    ]
    if len(event_hashes) != len(all_delivery_events) or len(event_hashes) != len(set(event_hashes)):
        errors.append("treatment_delivery_identity_invalid")
    elif any(not _SHA64.fullmatch(value) for value in event_hashes):
        errors.append("treatment_delivery_identity_invalid")
    total_bytes = 0
    delivered_keys: set[str] = set()
    for delivery in deliveries:
        if not isinstance(delivery, dict):
            errors.append("treatment_provider_delivery_invalid")
            continue
        lane = str(delivery.get("lane") or "")
        delivery_kind = str(delivery.get("delivery_kind") or "")
        observed = int(delivery.get("context_byte_count") or 0)
        total_bytes += observed
        delivered_keys.add(str(delivery.get("dedup_key") or ""))
        if delivery.get("same_observation") is not True:
            errors.append("treatment_delivery_late")
        if lane not in {"prompt", "sealed"}:
            errors.append("treatment_delivery_lane_invalid")
        try:
            expected_limit = delivery_byte_limit(lane=lane, kind=delivery_kind)
        except ValueError:
            expected_limit = 0
            errors.append("treatment_delivery_context_kind_invalid")
        if lane == "prompt" and delivery_kind not in {"context_contract", "context_delta"}:
            errors.append("treatment_prompt_context_budget_exceeded")
        if observed > expected_limit or delivery.get("byte_limit") != expected_limit:
            errors.append("treatment_delivery_context_budget_exceeded")
        if not _SHA64.fullmatch(str(delivery.get("context_sha256") or "")):
            errors.append("treatment_delivery_context_digest_invalid")
        if delivery.get("delivery_identity") != delivery.get("context_sha256"):
            errors.append("treatment_delivery_identity_invalid")
    if total_bytes > _TOTAL_DELIVERY_BYTE_LIMIT:
        errors.append("treatment_total_context_budget_exceeded")
    allowed_refusals = {
        "delivery_byte_ceiling",
        "task_delivery_byte_ceiling",
        "task_delivery_dose_ceiling",
        "task_delivery_storm_backstop",
        "duplicate_delivery_identity",
    }
    for refusal in refused_deliveries:
        if not isinstance(refusal, dict):
            errors.append("treatment_delivery_refusal_invalid")
            continue
        duplicate = refusal.get("reason") == "duplicate_delivery_identity"
        refusal_sequence = int(refusal.get("event_sequence") or 0)
        later_delivery = any(
            isinstance(delivery, dict)
            and int(delivery.get("event_sequence") or 0) > refusal_sequence
            and (
                delivery.get("delivery_identity") == refusal.get("delivery_identity")
                or delivery.get("dedup_key") == refusal.get("dedup_key")
            )
            for delivery in deliveries
        )
        if later_delivery:
            errors.append("treatment_refused_then_delivered")
        if (
            refusal.get("reason") not in allowed_refusals
            or refusal.get("lane") not in {"prompt", "sealed"}
            or not _SHA64.fullmatch(str(refusal.get("payload_sha256") or ""))
            or refusal_sequence < 1
            or (str(refusal.get("dedup_key") or "") in delivered_keys and not duplicate)
            or (
                duplicate
                and str(refusal.get("delivery_identity") or "") not in set(delivery_identities)
            )
        ):
            errors.append("treatment_delivery_refusal_invalid")
    budget = treatment.get("delivery_budget")
    budget = budget if isinstance(budget, dict) else {}
    if (
        int(budget.get("task_delivery_limit") or 0) != MAX_TASK_DELIVERIES
        or int(budget.get("admitted_count") or 0) != len(deliveries)
        or int(budget.get("refused_count") or 0) != len(refused_deliveries)
        or int(budget.get("total_observed") or 0) != total_bytes
    ):
        errors.append("treatment_delivery_budget_conservation_failed")
    dense = treatment.get("dense_index_receipt")
    if (
        treatment.get("retrieval_mode") != "hybrid_required"
        or not isinstance(dense, dict)
        or (
            dense.get("schema") != "gt.dense_index_receipt.v1"
            or dense.get("query_ready") is not True
        )
    ):
        errors.append("treatment_dense_index_not_ready")
    identity = treatment.get("provider_identity")
    if not isinstance(identity, dict) or identity.get("match") is not True:
        errors.append("treatment_provider_identity_mismatch")
    elif (
        identity.get("requested") != receipt.get("requested_model")
        or identity.get("reported") != receipt.get("requested_model")
        or identity.get("resolved") != receipt.get("effective_model")
    ):
        errors.append("treatment_provider_route_mismatch")
    reproduction = treatment.get("reproducibility_manifest")
    if not isinstance(reproduction, dict) or reproduction.get("research_valid") is not True:
        errors.append("treatment_reproducibility_invalid")
    provider = reproduction.get("provider_receipts") if isinstance(reproduction, dict) else None
    if not isinstance(provider, dict) or provider.get("valid") is not True:
        errors.append("treatment_provider_receipts_invalid")
    elif int(provider.get("request_count") or 0) != int(receipt.get("provider_calls") or 0):
        errors.append("treatment_provider_receipt_count_mismatch")
    graph = treatment.get("graph_certification")
    if not isinstance(graph, dict) or not (
        graph.get("schema") == "gt.graph_certification.v1"
        and graph.get("binary_certified") is True
        and graph.get("sqlite_quick_check") == "ok"
        and _SHA64.fullmatch(str(graph.get("graph_sha256") or ""))
    ):
        errors.append("treatment_graph_certification_invalid")
    return errors


__all__ = [
    "issue_runtime_receipt_failure",
    "issue_runtime_receipts",
    "verify_runtime_receipt",
]
