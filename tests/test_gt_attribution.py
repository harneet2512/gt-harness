from __future__ import annotations

import json

import pytest

from gt_engine.attribution import (
    CAPABILITY_OWNERS,
    DIRECT_FEATURES,
    AttributionTrace,
    feature_for_evidence,
    summarize_features,
    verify_lifecycle_rows,
    verify_trace_rows,
)


def test_direct_feature_registry_is_exact_and_complete():
    facts = {name for name, spec in DIRECT_FEATURES.items() if spec["kind"] == "FACT"}
    caps = {name for name, spec in DIRECT_FEATURES.items() if spec["kind"] == "CAP"}

    assert facts == {
        "caller_contract",
        "covering_red",
        "def_partition",
        "localization",
        "newfile_precedent",
        "obligations",
        "recovery",
        "signature_delta",
        "submit_refusal",
        "syntax_result",
    }
    assert caps == {
        "GT_CERT_DELIVERY",
        "GT_CHANGE_SURFACE",
        "GT_EDIT_CHECK",
        "GT_HYPOTHESIS",
        "GT_LOC_RESLOT",
        "GT_PATCH_DELTA",
        "GT_SS_SUBMIT_RED",
        "select_catalog",
    }
    assert len(DIRECT_FEATURES) == 18
    assert all(spec["boundaries"] for spec in DIRECT_FEATURES.values())
    assert all(spec["trigger"] for spec in DIRECT_FEATURES.values())
    assert all(spec["intended_action"] for spec in DIRECT_FEATURES.values())
    assert CAPABILITY_OWNERS == {
        "GT_CHANGE_SURFACE": "newfile_precedent",
        "GT_PATCH_DELTA": "signature_delta",
        "GT_LOC_RESLOT": "localization",
        "GT_SS_SUBMIT_RED": "submit_refusal",
        "GT_EDIT_CHECK": "syntax_result",
        "GT_HYPOTHESIS": "recovery",
        "GT_CERT_DELIVERY": "submit_refusal",
    }


def test_attribution_trace_is_append_only_hash_chained(tmp_path):
    path = tmp_path / "gt_attribution.jsonl"
    trace = AttributionTrace(lambda: path, trace_id="a" * 32)

    first = trace.record(
        "observation.received",
        action_index=1,
        boundary="gateway",
        payload={"tool_name": "bash", "changed_files": ["src/a.py"]},
    )
    second = trace.record(
        "decision.committed",
        action_index=1,
        boundary="gateway",
        payload={"decision": "no_candidate", "reason": "producer_abstained"},
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [first, second]
    assert rows[0]["previous_hash"] == ""
    assert rows[1]["previous_hash"] == rows[0]["row_hash"]
    assert rows[0]["sequence"] == 1
    assert rows[1]["sequence"] == 2
    assert verify_trace_rows(rows) == []


def test_newfile_missing_role_evidence_maps_to_canonical_feature():
    assert feature_for_evidence("missing_role:registration") == (
        "newfile_precedent"
    )
    assert feature_for_evidence("missing_role_postcreate:template") == (
        "newfile_precedent"
    )


def test_groundtruth_registry_aliases_map_to_the_same_17_identities():
    assert feature_for_evidence("name_fold") == "def_partition"
    assert feature_for_evidence("wrong_surface") == "def_partition"
    assert feature_for_evidence("body_concept") == "def_partition"
    assert feature_for_evidence("trace_frame") == "localization"
    assert feature_for_evidence("brief_localization") == "localization"
    assert feature_for_evidence("companion_surface") == "signature_delta"
    assert feature_for_evidence("caller_contract_search") == "caller_contract"
    assert feature_for_evidence("coherence_collapse") == "recovery"
    assert feature_for_evidence("obligation_unexercised") == "obligations"
    assert feature_for_evidence("select_catalog") == "select_catalog"


def test_trace_integrity_rejects_mutated_payload(tmp_path):
    path = tmp_path / "gt_attribution.jsonl"
    trace = AttributionTrace(lambda: path, trace_id="b" * 32)
    trace.record(
        "decision.committed",
        action_index=2,
        boundary="submit",
        payload={"decision": "suppressed", "reason": "over_budget"},
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["payload"]["reason"] = "sealed_and_delivered"

    assert verify_trace_rows(rows) == ["row 1: row_hash mismatch"]


def test_sensitive_payload_values_are_hashed_not_persisted(tmp_path):
    path = tmp_path / "gt_attribution.jsonl"
    trace = AttributionTrace(lambda: path, trace_id="c" * 32)
    secret = "provider-secret-value"
    trace.record_content(
        "model.response",
        content=secret,
        action_index=3,
        boundary="model",
        payload={"delivery_ids": ["d1"]},
    )

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw)
    assert secret not in raw
    assert row["payload"]["content_chars"] == len(secret)
    assert len(row["payload"]["content_sha256"]) == 64


def test_lifecycle_verifier_allows_provider_after_multi_tool_batch():
    rows = [
        {
            "sequence": 1,
            "action_index": 4,
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "d4",
                "rendered_bytes_hash": "a" * 64,
            },
        },
        {
            "sequence": 2,
            "action_index": 4,
            "event_type": "provider.request",
            "payload": {
                "iteration": 5,
                "delivery_ids": ["d4"],
                "matches": [{
                    "delivery_id": "d4",
                    "rendered_sha256": "a" * 64,
                    "locations": ["1.content"],
                }],
            },
        },
        {
            "sequence": 3,
            "action_index": 4,
            "event_type": "model.response",
            "payload": {"iteration": 5, "delivery_ids": ["d4"]},
        },
    ]

    assert verify_lifecycle_rows(rows) == []

    rows[1]["action_index"] = 7
    rows[2]["action_index"] = 7
    assert verify_lifecycle_rows(rows) == []


def test_lifecycle_verifier_requires_immediate_provider_and_response_link():
    rows = [
        {
            "sequence": 1,
            "action_index": 4,
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "d4",
                "rendered_bytes_hash": "a" * 64,
            },
        },
        {
            "sequence": 2,
            "action_index": 7,
            "event_type": "provider.request",
            "payload": {
                "iteration": 5,
                "delivery_ids": [],
                "matches": [],
            },
        },
        {
            "sequence": 3,
            "action_index": 7,
            "event_type": "model.response",
            "payload": {"iteration": 5, "delivery_ids": []},
        },
        {
            "sequence": 4,
            "action_index": 8,
            "event_type": "provider.request",
            "payload": {
                "iteration": 6,
                "delivery_ids": ["d4"],
                "matches": [{
                    "delivery_id": "d4",
                    "rendered_sha256": "a" * 64,
                    "locations": ["1.content"],
                }],
            },
        },
        {
            "sequence": 5,
            "action_index": 8,
            "event_type": "model.response",
            "payload": {"iteration": 6, "delivery_ids": ["d4"]},
        },
    ]

    assert verify_lifecycle_rows(rows) == [
        "delivery d4: missing from immediate provider-final request",
        "delivery d4: provider byte match missing",
        "delivery d4: missing from immediate model response",
    ]


def test_lifecycle_verifier_rejects_missing_or_hash_mismatched_receipt():
    delivered = {
        "sequence": 1,
        "action_index": 2,
        "event_type": "decision.committed",
        "payload": {
            "decision": "delivered",
            "delivery_id": "d2",
            "rendered_bytes_hash": "b" * 64,
        },
    }
    assert verify_lifecycle_rows([delivered]) == [
        "delivery d2: missing provider-final request receipt",
        "delivery d2: missing linked model response",
    ]

    rows = [
        delivered,
        {
            "sequence": 2,
            "action_index": 2,
            "event_type": "provider.request",
            "payload": {
                "iteration": 3,
                "delivery_ids": ["d2"],
                "matches": [{
                    "delivery_id": "d2",
                    "rendered_sha256": "c" * 64,
                    "locations": ["2.content"],
                }],
            },
        },
        {
            "sequence": 3,
            "action_index": 2,
            "event_type": "model.response",
            "payload": {"iteration": 3, "delivery_ids": ["d2"]},
        },
    ]
    assert verify_lifecycle_rows(rows) == [
        "delivery d2: provider receipt hash does not match sealed bytes"
    ]


def test_feature_summary_distinguishes_delivery_dark_suppressed_and_ineligible():
    rows = [
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "reason": "sealed_and_delivered",
                "delivery_id": "1",
                "feature_id": "localization",
                "evidence_type": "localization",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"iteration": 2, "delivery_ids": ["1"]},
        },
        {
            "event_type": "model.response",
            "payload": {"iteration": 2, "delivery_ids": ["1"], "tool_calls": []},
        },
        {
            "event_type": "feature.evaluated",
            "payload": {
                "feature_id": "recovery",
                "eligible": True,
                "outcome": "producer_abstained",
            },
        },
        {
            "event_type": "producer.invocation",
            "payload": {
                "outcome": "returned_fact",
                "evidence_types": ["signature_mismatch"],
            },
        },
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "suppressed",
                "reason": "over_budget",
                "evidence_type": "signature_mismatch",
            },
        },
        {
            "event_type": "producer.invocation",
            "payload": {
                "outcome": "returned_nothing",
                "evidence_types": ["def_ref_partition"],
                "abstention_reasons": [
                    {"category": "correct_quiet", "reason": "definition_absent"}
                ],
            },
        },
    ]

    summary = summarize_features(rows)

    assert summary["localization"]["status"] == "WITNESSED"
    assert summary["localization"]["exposed"] is True
    assert summary["localization"]["response_observed"] is True
    assert summary["recovery"]["status"] == "TRIGGERED_DARK"
    assert summary["signature_delta"]["status"] == "SUPPRESSED_WITH_REASON"
    assert summary["signature_delta"]["reasons"] == ["over_budget"]
    assert summary["def_partition"]["status"] == "INELIGIBLE"
    assert summary["covering_red"]["status"] == "INELIGIBLE"


@pytest.mark.gt_all17
def test_capabilities_require_explicit_application_receipts():
    rows = []
    for action_index, (capability, fact_id) in enumerate(
        CAPABILITY_OWNERS.items(), 1
    ):
        delivery_id = f"d{action_index}"
        rows.extend([
            {
                "event_type": "decision.committed",
                "action_index": action_index,
                "payload": {
                    "decision": "delivered",
                    "delivery_id": delivery_id,
                    "feature_id": fact_id,
                    "evidence_type": fact_id,
                },
            },
            {
                "event_type": "capability.applied",
                "action_index": action_index,
                "payload": {
                    "feature_id": capability,
                    "fact_id": fact_id,
                    "delivery_id": delivery_id,
                    "decision": "APPLIED",
                },
            },
            {
                "event_type": "provider.request",
                "action_index": action_index,
                "payload": {"delivery_ids": [delivery_id]},
            },
            {
                "event_type": "model.response",
                "action_index": action_index,
                "payload": {"delivery_ids": [delivery_id]},
            },
        ])

    summary = summarize_features(rows)

    for capability, fact_id in CAPABILITY_OWNERS.items():
        assert summary[fact_id]["status"] == "WITNESSED"
        assert summary[capability]["status"] == "WITNESSED"
        assert summary[capability]["reasons"] == ["capability_applied"]


def test_delivered_fact_does_not_automatically_credit_capability_owner():
    rows = [
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "d1",
                "feature_id": "localization",
                "evidence_type": "localization",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"delivery_ids": ["d1"]},
        },
        {
            "event_type": "model.response",
            "payload": {"delivery_ids": ["d1"]},
        },
    ]

    summary = summarize_features(rows)

    assert summary["localization"]["status"] == "WITNESSED"
    assert summary["GT_LOC_RESLOT"]["status"] == "INELIGIBLE"


def test_compound_feature_receipt_credits_a_fact_without_second_delivery():
    rows = [
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "0",
                "feature_id": "obligations",
                "evidence_type": "obligations",
            },
        },
        {
            "event_type": "feature.applied",
            "payload": {
                "feature_id": "localization",
                "delivery_id": "0",
                "decision": "APPLIED",
                "reason": "compound_task_start_orientation",
            },
        },
        {
            "event_type": "capability.applied",
            "payload": {
                "feature_id": "GT_LOC_RESLOT",
                "fact_id": "localization",
                "delivery_id": "0",
                "decision": "APPLIED",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"iteration": 1, "delivery_ids": ["0"]},
        },
        {
            "event_type": "model.response",
            "payload": {"iteration": 1, "delivery_ids": ["0"]},
        },
    ]

    summary = summarize_features(rows)

    assert summary["obligations"]["status"] == "WITNESSED"
    assert summary["localization"]["status"] == "WITNESSED"
    assert summary["GT_LOC_RESLOT"]["status"] == "WITNESSED"
    assert summary["localization"]["deliveries"] == ["0"]


def test_feature_provider_iterations_report_exact_delivery_timing():
    from gt_engine.attribution import feature_provider_iterations

    rows = [
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "0",
                "feature_id": "obligations",
            },
        },
        {
            "event_type": "feature.applied",
            "payload": {
                "decision": "APPLIED",
                "delivery_id": "0",
                "feature_id": "localization",
            },
        },
        {
            "event_type": "capability.applied",
            "payload": {
                "decision": "APPLIED",
                "delivery_id": "0",
                "feature_id": "GT_LOC_RESLOT",
                "fact_id": "localization",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"iteration": 1, "delivery_ids": ["0"]},
        },
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "7",
                "feature_id": "localization",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"iteration": 4, "delivery_ids": ["7"]},
        },
    ]

    timing = feature_provider_iterations(rows)

    assert timing["obligations"] == [1]
    assert timing["localization"] == [1, 4]
    assert timing["GT_LOC_RESLOT"] == [1]


def test_sdlc_timing_requires_pre_edit_before_dispatch_and_post_edit_after():
    from gt_engine.attribution import verify_sdlc_timing_rows

    valid = [
        {
            "sequence": 1,
            "action_index": 0,
            "event_type": "lifecycle.checkpoint",
            "boundary": "pre_edit",
            "payload": {
                "phase": "pre_edit",
                "proposed_action_index": 1,
            },
        },
        {
            "sequence": 2,
            "action_index": 1,
            "event_type": "observation.received",
            "payload": {
                "tool_name": "edit_file",
                "changed_files": ["pkg/a.py"],
            },
        },
        {
            "sequence": 3,
            "action_index": 1,
            "event_type": "lifecycle.checkpoint",
            "boundary": "post_edit",
            "payload": {"phase": "post_edit"},
        },
    ]
    invalid = [
        {**valid[1], "sequence": 1},
        {**valid[0], "sequence": 2},
    ]

    assert verify_sdlc_timing_rows(valid) == []
    issues = verify_sdlc_timing_rows(invalid)
    assert any("pre_edit occurs after dispatch" in issue for issue in issues)
    assert any("missing post_edit" in issue for issue in issues)


def test_unterminated_producer_invocation_is_telemetry_fault():
    rows = [{
        "event_type": "producer.invocation",
        "payload": {
            "invocation_id": "inv-1",
            "outcome": "entered",
            "evidence_types": ["def_ref_partition"],
        },
    }]

    summary = summarize_features(rows)

    assert summary["def_partition"]["status"] == "TELEMETRY_FAULT"
    assert summary["def_partition"]["reasons"] == ["producer_terminal_missing"]


def test_cap_is_witnessed_only_when_same_action_delivers_its_fact():
    rows = [
        {
            "action_index": 9,
            "event_type": "capability.applied",
            "payload": {
                "feature_id": "GT_HYPOTHESIS",
                "fact_id": "recovery",
                "delivery_id": "9",
                "decision": "APPLIED",
            },
        },
        {
            "action_index": 9,
            "event_type": "provider.request",
            "payload": {"delivery_ids": ["9"]},
        },
        {
            "action_index": 9,
            "event_type": "model.response",
            "payload": {"delivery_ids": ["9"]},
        },
        {
            "action_index": 10,
            "event_type": "feature.evaluated",
            "payload": {
                "feature_id": "GT_HYPOTHESIS",
                "eligible": True,
                "outcome": "candidate_returned",
            },
        },
        {
            "action_index": 9,
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "9",
                "feature_id": "recovery",
                "evidence_type": "recovery",
            },
        },
        {
            "action_index": 10,
            "event_type": "feature.evaluated",
            "payload": {
                "feature_id": "GT_CERT_DELIVERY",
                "eligible": True,
                "outcome": "candidate_returned",
            },
        },
    ]

    summary = summarize_features(rows)

    assert summary["GT_HYPOTHESIS"]["status"] == "WITNESSED"
    assert summary["GT_CERT_DELIVERY"]["status"] == "TRIGGERED_DARK"


def test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible():
    rows = [
        {
            "action_index": 4,
            "event_type": "feature.evaluated",
            "payload": {
                "feature_id": "GT_EDIT_CHECK",
                "eligible": True,
                "outcome": "ok",
            },
        },
        {
            "action_index": 4,
            "event_type": "capability.applied",
            "payload": {
                "feature_id": "GT_EDIT_CHECK",
                "fact_id": "syntax_result",
                "delivery_id": "",
                "decision": "APPLIED",
            },
        },
    ]
    assert summarize_features(rows)["GT_EDIT_CHECK"]["status"] == "WITNESSED"

    quiet_rows = [{
        "action_index": 4,
        "event_type": "feature.evaluated",
        "payload": {
            "feature_id": "GT_EDIT_CHECK",
            "eligible": False,
            "outcome": "no_edited_syntax_target",
        },
    }]
    assert (
        summarize_features(quiet_rows)["GT_EDIT_CHECK"]["status"]
        == "INELIGIBLE"
    )


def test_named_correct_quiet_outcome_is_retained_for_ineligible_feature():
    rows = [{
        "action_index": 0,
        "event_type": "feature.evaluated",
        "payload": {
            "feature_id": "obligations",
            "eligible": False,
            "outcome": "brief_empty",
        },
    }]

    summary = summarize_features(rows)

    assert summary["obligations"]["status"] == "INELIGIBLE"
    assert summary["obligations"]["reasons"] == ["brief_empty"]


def test_authority_abstention_is_named_suppression_not_triggered_dark():
    rows = [{
        "event_type": "producer.invocation",
        "payload": {
            "outcome": "returned_nothing",
            "evidence_types": ["caller_contract_view"],
            "abstention_reasons": [{
                "category": "authority",
                "reason": "viewed_file_leaky",
            }],
        },
    }]

    summary = summarize_features(rows)

    assert summary["caller_contract"]["status"] == "SUPPRESSED_WITH_REASON"
    assert summary["caller_contract"]["reasons"] == ["viewed_file_leaky"]


def test_registry_abstention_is_named_suppression_not_ineligible():
    rows = [{
        "event_type": "producer.invocation",
        "payload": {
            "outcome": "returned_nothing",
            "evidence_types": ["caller_contract_view"],
            "abstention_reasons": [{
                "category": "registry",
                "reason": "producer_disabled",
            }],
        },
    }]

    summary = summarize_features(rows)

    assert summary["caller_contract"]["status"] == "SUPPRESSED_WITH_REASON"
    assert summary["caller_contract"]["reasons"] == ["producer_disabled"]


def test_router_suppression_is_attributed_to_canonical_evidence_feature():
    rows = [{
        "event_type": "control.decision",
        "payload": {
            "feature_id": "GT_ROLE_DRIVEN_COALITION",
            "decision": "SUPPRESSED",
            "reason": "not_grounded_in_content_search",
            "evidence_type": "localization",
        },
    }]

    summary = summarize_features(rows)

    assert summary["localization"]["status"] == "SUPPRESSED_WITH_REASON"
    assert summary["localization"]["reasons"] == [
        "not_grounded_in_content_search"
    ]


def test_carried_delivery_does_not_overwrite_immediate_response_action():
    """Old GT bytes remain in later conversation history. The first linked
    response is the causal boundary; later actions must not overwrite it."""
    rows = [
        {
            "event_type": "decision.committed",
            "payload": {
                "decision": "delivered",
                "delivery_id": "d1",
                "feature_id": "localization",
                "evidence_type": "localization",
            },
        },
        {
            "event_type": "provider.request",
            "payload": {"delivery_ids": ["d1"]},
        },
        {
            "event_type": "model.response",
            "payload": {"delivery_ids": ["d1"]},
        },
        {
            "event_type": "response.action",
            "payload": {
                "delivery_id": "d1",
                "feature_id": "localization",
                "classification": "target_referenced",
            },
        },
        {
            "event_type": "response.action",
            "payload": {
                "delivery_id": "d1",
                "feature_id": "localization",
                "classification": "no_tool_action",
            },
        },
    ]

    summary = summarize_features(rows)

    assert summary["localization"]["action_observed"] is True
    assert summary["localization"]["action_consistent"] is True


def test_transcript_parser_consumes_gt_index_diagnostics_as_structured_noise():
    from scripts.gt_audit import parse_transcript

    transcript = (
        "GroundTruth: gt-index failed: Pass 1: discovering files\n"
        "Found 1 source files,\n"
        "Pass 2: parsing 1 files (4 workers)......\n"
        "stop: max_iterations  iterations=1  in=1 out=1 cache_read=0\n"
    )
    parsed = parse_transcript(transcript)
    assert parsed.unparsed == []
    assert parsed.stop is not None
