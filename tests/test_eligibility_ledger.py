from __future__ import annotations

import pytest

from gt_engine.evidence_router import (
    EligibilityReceiptError,
    build_eligibility_receipt,
    verify_eligibility_receipt,
)


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

