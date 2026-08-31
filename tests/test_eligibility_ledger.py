from __future__ import annotations

import pytest

from gt_engine.evidence_router import (
    EligibilityReceiptError,
    EvidenceRouter,
    build_eligibility_receipt,
    reconcile_provider_bytes,
    verify_eligibility_receipt,
)
from gt_engine.task_contract import TaskContract


def _claims():
    return [
        {
            "claim_id": "c2",
            "source": "graph",
            "content": "beta",
            "disposition": "refused",
            "reason": "role_irrelevant",
        },
        {
            "claim_id": "c1",
            "source": "graph",
            "content": "alpha",
            "disposition": "admitted",
            "reason": "admitted",
        },
    ]


def test_receipt_is_deterministic_and_conserves_claim_and_provider_bytes():
    baseline = {
        "messages": [{"role": "user", "content": "native"}],
        "headers": {"Authorization": "secret"},
    }
    final = {"messages": [{"role": "user", "content": "nativealpha"}], "credentials": "secret"}
    receipt = build_eligibility_receipt(
        decision_id="decision-1",
        iteration_id="iteration-1",
        claims=_claims(),
        baseline_request=baseline,
        final_request=final,
        framing_encoding_bytes=0,
        prior_event_digest="event-0",
    )
    assert receipt["schema"] == "gt.eligibility_receipt.v1"
    assert receipt["admitted_bytes"] == 5
    assert receipt["refused_bytes"] == 4
    assert receipt["provider_delta_bytes"] == receipt["admitted_bytes"]
    assert receipt["refused_bytes_in_final"] == 0
    assert verify_eligibility_receipt(receipt)
    assert build_eligibility_receipt(
        decision_id="decision-1",
        iteration_id="iteration-1",
        claims=list(reversed(_claims())),
        baseline_request=baseline,
        final_request=final,
        framing_encoding_bytes=0,
        prior_event_digest="event-0",
    ) == receipt


def test_refused_content_in_final_or_delta_mismatch_rejects_atomically():
    baseline = {"messages": [{"content": "native"}]}
    with pytest.raises(EligibilityReceiptError, match="refused_content_in_final"):
        build_eligibility_receipt(
            decision_id="d",
            iteration_id="i",
            claims=_claims(),
            baseline_request=baseline,
            final_request={"messages": [{"content": "nativealphabeta"}]},
            framing_encoding_bytes=0,
        )
    with pytest.raises(EligibilityReceiptError, match="provider_delta_conservation"):
        build_eligibility_receipt(
            decision_id="d",
            iteration_id="i",
            claims=[_claims()[1]],
            baseline_request=baseline,
            final_request={"messages": [{"content": "nativealpha"}]},
            framing_encoding_bytes=99,
        )


def test_zero_evidence_and_digest_tampering_are_explicit():
    baseline = {"messages": [{"content": "native"}]}
    receipt = build_eligibility_receipt(
        decision_id="d",
        iteration_id="i",
        claims=[],
        baseline_request=baseline,
        final_request=baseline,
    )
    assert receipt["admitted_bytes"] == receipt["refused_bytes"] == 0
    assert receipt["status"] == "SEALED"
    assert not verify_eligibility_receipt({**receipt, "receipt_digest_sha256": "0" * 64})


def test_router_model_boundary_emits_receipt_and_degrades_to_native_on_seal_failure():
    router = EvidenceRouter(TaskContract(role="content_scan", obligations=()))
    baseline = {"messages": [{"content": "native"}]}
    final = {"messages": [{"content": "nativealpha"}]}
    transported, receipt = router.seal_eligibility_receipt(
        decision_id="d",
        iteration_id="i",
        claims=[_claims()[1]],
        baseline_request=baseline,
        final_request=final,
    )
    assert transported == final
    assert receipt["status"] == "SEALED"
    transported, degraded = router.seal_eligibility_receipt(
        decision_id="d2",
        iteration_id="i2",
        claims=_claims(),
        baseline_request=baseline,
        final_request={"messages": [{"content": "nativealphabeta"}]},
    )
    assert transported == baseline
    assert degraded["status"] == "DEGRADED"
    assert degraded["native_baseline_only"] is True


def test_admit_decision_always_seals_and_provider_reconciliation_is_explicit():
    router = EvidenceRouter(TaskContract(role="content_scan", obligations=()))
    baseline = {"messages": [{"content": "native"}]}
    final = {"messages": [{"content": "nativealpha"}]}
    transported, receipt = router.admit_decision(
        decision_id="d3",
        iteration_id="i3",
        candidates=[
            {
                "claim_id": "c1",
                "evidence_type": "graph",
                "rendered": "alpha",
                "command": "rg alpha",
                "output": "alpha",
            }
        ],
        baseline_request=baseline,
        final_request=final,
    )
    assert transported == final
    assert router.last_eligibility_receipt == receipt
    reconciliation = reconcile_provider_bytes(receipt, final)
    assert reconciliation["provider_final_matches"] is True
