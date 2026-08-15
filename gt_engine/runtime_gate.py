"""Fail-closed integrity checks for a central GT runtime receipt.

These checks do not infer model usefulness from later actions.  They only prove
that the deterministic engine accounted for its work and that selected,
grounded retrieval was not silently dropped before the provider request.
"""

from __future__ import annotations

from typing import Any


def audit_runtime_receipt(
    receipt: dict[str, Any], *, task: str = "task"
) -> tuple[list[str], dict[str, int]]:
    metrics = receipt.get("metrics") or {}
    failures: list[str] = []

    def require(condition: bool, code: str) -> None:
        if not condition:
            failures.append(f"{task}:{code}")

    produced = int(metrics.get("effects_produced") or 0)
    applied = int(metrics.get("effects_applied") or 0)
    require(produced == applied, "effects_not_fully_applied")
    require(
        int(metrics.get("effect_trace_rows") or 0) == applied,
        "effect_trace_incomplete",
    )
    require(
        int(metrics.get("context_compiler_effects_unaccounted") or 0) == 0,
        "context_effect_unaccounted",
    )
    require(
        int(metrics.get("inert_private_state_effects") or 0) == 0,
        "inert_private_state_effect",
    )
    require(
        int(metrics.get("pending_decision_claim_effects") or 0) == 0,
        "pending_decision_claim",
    )

    prepared = int(metrics.get("provider_requests_prepared") or 0)
    coverage = float(metrics.get("provider_request_hash_coverage") or 0.0)
    require(not prepared or coverage == 1.0, "provider_request_hash_incomplete")
    require(int(metrics.get("late_payload_deliveries") or 0) == 0, "late_delivery")
    require(
        int(metrics.get("predictive_payload_deliveries") or 0) == 0,
        "predictive_delivery",
    )

    compiler = receipt.get("contribution_compiler") or {}
    for call in compiler.get("calls") or ():
        if not isinstance(call, dict):
            failures.append(f"{task}:malformed_contribution_call")
            continue
        require(
            int(call.get("candidate_count") or 0)
            == int(call.get("accounted_count") or 0),
            "contribution_accounting_mismatch",
        )

    retrieval = receipt.get("preemptive_retrieval") or {}
    decisions = [
        row for row in retrieval.get("decisions") or () if isinstance(row, dict)
    ]
    deliveries = [
        row for row in retrieval.get("deliveries") or () if isinstance(row, dict)
    ]
    selected = 0
    for decision in decisions:
        if decision.get("status") == "selected":
            selected += 1
            failures.append(f"{task}:preemptive_selected_not_delivered")
        elif decision.get("status") == "delivered":
            require(
                isinstance(decision.get("delivery_receipt"), dict),
                "preemptive_delivery_receipt_missing",
            )
    selected_evidence = int(
        metrics.get("preemptive_retrieval_selected_evidence") or 0
    )
    delivered_claims = int(
        metrics.get("preemptive_retrieval_claims_delivered") or 0
    )
    if selected_evidence:
        require(delivered_claims > 0, "preemptive_selected_evidence_silent")
    require(
        int(metrics.get("preemptive_retrieval_deliveries") or 0)
        == len(deliveries),
        "preemptive_delivery_count_mismatch",
    )
    return failures, {
        "decisions": len(decisions),
        "selected_not_delivered": selected,
        "deliveries": len(deliveries),
        "selected_evidence": selected_evidence,
        "delivered_claims": delivered_claims,
    }


__all__ = ["audit_runtime_receipt"]
