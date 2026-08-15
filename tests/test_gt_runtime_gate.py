from gt_engine.runtime_gate import audit_runtime_receipt


def _receipt() -> dict:
    return {
        "metrics": {
            "effects_produced": 2,
            "effects_applied": 2,
            "effect_trace_rows": 2,
            "context_compiler_effects_unaccounted": 0,
            "inert_private_state_effects": 0,
            "pending_decision_claim_effects": 0,
            "provider_requests_prepared": 1,
            "provider_request_hash_coverage": 1.0,
            "late_payload_deliveries": 0,
            "predictive_payload_deliveries": 0,
            "preemptive_retrieval_selected_evidence": 1,
            "preemptive_retrieval_claims_delivered": 1,
            "preemptive_retrieval_deliveries": 1,
        },
        "contribution_compiler": {
            "calls": [{"candidate_count": 1, "accounted_count": 1}]
        },
        "preemptive_retrieval": {
            "decisions": [
                {"status": "delivered", "delivery_receipt": {"status": "delivered"}}
            ],
            "deliveries": [{"claim_ids": ["claim-1"]}],
        },
    }


def test_runtime_gate_accepts_accounted_delivery():
    failures, summary = audit_runtime_receipt(_receipt(), task="demo")
    assert failures == []
    assert summary["delivered_claims"] == 1


def test_runtime_gate_rejects_selected_retrieval_that_goes_silent():
    receipt = _receipt()
    receipt["metrics"]["preemptive_retrieval_claims_delivered"] = 0
    receipt["metrics"]["preemptive_retrieval_deliveries"] = 0
    receipt["preemptive_retrieval"]["decisions"] = [{"status": "selected"}]
    receipt["preemptive_retrieval"]["deliveries"] = []
    failures, _ = audit_runtime_receipt(receipt, task="demo")
    assert "demo:preemptive_selected_not_delivered" in failures
    assert "demo:preemptive_selected_evidence_silent" in failures


def test_runtime_gate_rejects_unaccounted_private_effect():
    receipt = _receipt()
    receipt["metrics"]["inert_private_state_effects"] = 1
    failures, _ = audit_runtime_receipt(receipt, task="demo")
    assert "demo:inert_private_state_effect" in failures
