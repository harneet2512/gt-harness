from __future__ import annotations

from gt_engine.provider_evidence import (
    ProviderEvidenceDisposition,
    ProviderEvidenceLedger,
    ProviderEvidenceSurface,
)


def test_selected_provider_evidence_is_joined_to_the_dispatched_request():
    ledger = ProviderEvidenceLedger()
    event = ledger.prepare(
        surface=ProviderEvidenceSurface.GRAPH_FRONTIER,
        fact_ids=("fact-1",),
        claim_ids=("claim-1",),
        evidence_action=2,
        eligible_call=3,
        prepared_call=3,
        message_indices=(4,),
        chars=120,
        source_revision="s1",
    )

    ledger.mark_dispatched(call=3, request_hash="request-hash")

    row = ledger.as_dict()["events"][0]
    assert row["event_id"] == event.event_id
    assert row["disposition"] == "selected_new_context"
    assert row["dispatched_call"] == 3
    assert row["request_hash"] == "request-hash"


def test_prepared_but_unsent_evidence_is_never_counted_as_model_visible():
    ledger = ProviderEvidenceLedger()
    ledger.prepare(
        surface=ProviderEvidenceSurface.STATE_FRAME,
        fact_ids=("fact-1",),
        eligible_call=5,
        prepared_call=5,
        message_indices=(8,),
        chars=230,
        source_revision="s2",
    )

    ledger.mark_not_sent(call=5, reason="deadline_reserve_reached")

    summary = ledger.as_dict()
    assert summary["events"][0]["disposition"] == "prepared_not_sent"
    assert summary["dispatched_events"] == 0
    assert summary["prepared_not_sent_events"] == 1


def test_represented_evidence_on_an_unsent_request_is_not_counted_as_visible():
    ledger = ProviderEvidenceLedger()
    ledger.prepare(
        surface=ProviderEvidenceSurface.PREFLIGHT_RETURN,
        fact_ids=("preflight-return-1",),
        eligible_call=7,
        prepared_call=7,
        message_indices=(12,),
        chars=0,
        disposition=ProviderEvidenceDisposition.REPRESENTED_MESSAGE,
        source_revision="s3",
    )

    ledger.mark_not_sent(call=7, reason="provider_request_over_budget")

    summary = ledger.as_dict()
    assert summary["events"][0]["disposition"] == "prepared_not_sent"
    assert summary["events"][0]["dispatched_call"] is None
    assert summary["prepared_not_sent_events"] == 1
