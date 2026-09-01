"""Typed terminal and efficiency metrics for one official GT run receipt."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from gt_harness.analysis.delivery import DeliveryAuditError, audit_treatment_delivery
from gt_harness.analysis.uptake import measure_delivery_uptake


class TerminalDisposition(StrEnum):
    COMPLETED = "COMPLETED"
    PROVIDER_TRANSPORT = "PROVIDER_TRANSPORT"
    ORCHESTRATOR_TIMEOUT = "ORCHESTRATOR_TIMEOUT"
    PRODUCT_ERROR = "PRODUCT_ERROR"
    CANCELLED = "CANCELLED"
    NONTERMINAL = "NONTERMINAL"


def terminal_disposition(run_receipt: dict[str, Any]) -> TerminalDisposition:
    status = str(run_receipt.get("status") or "")
    if status == "COMPLETED":
        return TerminalDisposition.COMPLETED
    if status == "RUNNING" or not status:
        return TerminalDisposition.NONTERMINAL
    termination = run_receipt.get("termination")
    kind = str(termination.get("kind") or "") if isinstance(termination, dict) else ""
    evaluation = run_receipt.get("evaluation")
    infrastructure = (
        str(evaluation.get("infrastructure_disposition") or "")
        if isinstance(evaluation, dict)
        else ""
    )
    if kind == "PROVIDER_TRANSPORT" or infrastructure == "PROVIDER_TRANSPORT":
        return TerminalDisposition.PROVIDER_TRANSPORT
    if kind in {"TIMEOUT", "ORCHESTRATOR_TIMEOUT"} or infrastructure == "ORCHESTRATOR_TIMEOUT":
        return TerminalDisposition.ORCHESTRATOR_TIMEOUT
    if kind == "CANCELLED":
        return TerminalDisposition.CANCELLED
    return TerminalDisposition.PRODUCT_ERROR


def summarize_run(run_receipt: dict[str, Any]) -> dict[str, Any]:
    if run_receipt.get("schema") != "gt.run_receipt.v1":
        raise ValueError("run receipt schema must be gt.run_receipt.v1")
    disposition = terminal_disposition(run_receipt)
    evaluation = run_receipt.get("evaluation")
    officially_graded = bool(
        isinstance(evaluation, dict)
        and evaluation.get("schema") == "gt.evaluation_binding.v1"
        and evaluation.get("official_verifier_authoritative") is True
        and isinstance(run_receipt.get("resolved"), bool)
    )
    delivery: dict[str, Any] | None = None
    if run_receipt.get("treatment") == "groundtruth":
        try:
            delivery = audit_treatment_delivery(
                run_receipt.get("treatment_receipt"),
                initial_context=str(run_receipt.get("initial_context") or ""),
                repository_end=(
                    run_receipt.get("repository_end")
                    if isinstance(run_receipt.get("repository_end"), dict)
                    else None
                ),
            )
        except DeliveryAuditError as exc:
            delivery = {
                "schema": "gt.delivery_audit.v1",
                "status": "FAIL",
                "failures": [f"receipt_invalid:{exc}"],
            }
    numeric_fields = (
        "iterations",
        "provider_calls",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "duration_ms",
        "total_cost",
    )
    return {
        "schema": "gt.run_metrics.v1",
        "task_id": str(run_receipt.get("task_id") or ""),
        "trial_id": str(run_receipt.get("trial_id") or ""),
        "treatment": str(run_receipt.get("treatment") or ""),
        "terminal_disposition": disposition.value,
        "terminal": disposition is not TerminalDisposition.NONTERMINAL,
        "officially_graded": officially_graded,
        "resolved": run_receipt.get("resolved") if officially_graded else None,
        "efficiency": {
            field: run_receipt.get(field)
            for field in numeric_fields
            if isinstance(run_receipt.get(field), (int, float))
        },
        "delivery": delivery,
        "uptake": measure_delivery_uptake(run_receipt),
    }


__all__ = ["TerminalDisposition", "summarize_run", "terminal_disposition"]

