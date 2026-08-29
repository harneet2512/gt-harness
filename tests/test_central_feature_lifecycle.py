from scripts.central_feature_lifecycle import build_feature_lifecycle_report

FEATURE_IDS = (
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
    "GT_CERT_DELIVERY",
    "GT_CHANGE_SURFACE",
    "GT_EDIT_CHECK",
    "GT_HYPOTHESIS",
    "GT_LOC_RESLOT",
    "GT_PATCH_DELTA",
    "GT_SS_SUBMIT_RED",
    "select_catalog",
)
FORCED_PROOF = {
    "status": "passed",
    "exact_commit": "c" * 40,
    "feature_ids": list(FEATURE_IDS),
}


def _receipt(*, missed: str = "", pending: bool = False) -> dict:
    applicability = {
        feature_id: {
            "evaluations": 0,
            "eligible": 0,
            "fired": 0,
            "status": "trigger_absent",
            "reason_codes": ["no_lifecycle_evidence_observed"],
        }
        for feature_id in FEATURE_IDS
    }
    applicability["obligations"] = {
        "evaluations": 1,
        "eligible": 1,
        "fired": 1,
        "status": "fired_when_eligible",
        "reason_codes": ["non_empty_task_instruction"],
    }
    if missed:
        applicability[missed] = {
            "evaluations": 1,
            "eligible": 1,
            "fired": 0,
            "status": "missed_trigger",
            "reason_codes": ["fixture_miss"],
        }
    produced = {feature_id: 0 for feature_id in FEATURE_IDS}
    produced["obligations"] = 1
    accountability = [
        {
            "effect_id": "receipt-1",
            "feature_id": "obligations",
            "outcome": "pending_decision_claim" if pending else "engine_internal_state",
            "provider_delivery_ids": [],
            "state_read_count": 0,
        }
    ]
    compiler = [
        {
            "effect_id": "receipt-1",
            "feature_id": "obligations",
            "status": "controller_state_considered",
            "request_payload_sha256": "a" * 64,
            "fact_id": "fact-1",
            "first_considered_call": 2,
        }
    ]
    return {
        "task": "demo",
        "metrics": {
            "effects_produced": 1,
            "effects_applied": 1,
            "effect_trace_rows": 1,
            "context_compiler_effects_unaccounted": 0,
            "inert_private_state_effects": 0,
            "pending_decision_claim_effects": 1 if pending else 0,
            "provider_requests_prepared": 0,
            "provider_request_hash_coverage": 0.0,
            "late_payload_deliveries": 0,
            "predictive_payload_deliveries": 0,
            "preemptive_retrieval_selected_evidence": 0,
            "preemptive_retrieval_claims_delivered": 0,
            "preemptive_retrieval_deliveries": 0,
            "submit_holds": 0,
            "completion_certificate_evaluations": 0,
            "completion_probe_execs": 0,
            "auto_submit_attempts": 0,
            "auto_submits": 0,
            "red_test_probe_attempts": 0,
            "context_recap_receipts": 0,
            "context_recap_fallbacks": 0,
        },
        "completion": {
            "certificates": [],
            "auto_submit_attempts": 0,
            "auto_submit_count": 0,
        },
        "red_test": {"receipts": []},
        "model_call_contexts": [],
        "features": {
            "enabled": True,
            "feature_count": 18,
            "feature_ids": list(FEATURE_IDS),
            "produced_counts": produced,
            "effects": [{"receipt_id": "receipt-1"}],
            "effect_trace": [{"effect_id": "receipt-1"}],
            "feature_applicability": applicability,
            "all_feature_opportunities_accounted": True,
            "action_metrics": {"submit_holds": 0},
            "effect_applications": [
                {
                    "feature_id": "obligations",
                    "receipt_id": "receipt-1",
                    "state_fields_changed": ["contract"],
                }
            ],
            "effect_accountability": accountability,
            "context_compiler_effect_accountability": compiler,
        },
        "product_mechanism_census": {
            "accounting_contract": "18_direct_features_with_select_catalog",
            "product_mechanism_count": 18,
            "configured_mechanism_count": 18,
            "configured_mechanism_ids": list(FEATURE_IDS),
            "persistent_execution_state": {
                "configured": True,
                "applicable": True,
                "correctly_abstained": False,
                "exercised": True,
                "repeated_deterministic_use": True,
                "lifecycle_use_count": 8,
                "bootstrap_calls": 1,
                "context_compilations": 2,
                "preflight_projections": 2,
                "postflight_commits": 2,
                "graph_rebases": 1,
                "failures": [],
            },
        },
    }


def test_lifecycle_report_separates_live_and_forced_only_features():
    report = build_feature_lifecycle_report(
        [_receipt()], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    assert report["passed"] is True
    rows = {row["feature_id"]: row for row in report["direct_features"]}
    assert rows["obligations"]["status"] == "working_live"
    assert rows["recovery"]["status"] == "working_forced_only"
    assert report["naturally_fired_direct_feature_count"] == 1
    assert report["persistent_execution_state"]["passed"] is True


def test_lifecycle_report_accepts_one_deterministic_selection_without_provider_call():
    receipt = _receipt()
    persistent = receipt["product_mechanism_census"]["persistent_execution_state"]
    persistent.update(
        {
            "selection_mode": "deterministic_v1",
            "selection_event_count": 1,
            "selection_provider_calls": 0,
            "bootstrap_provider_calls": 0,
            "bootstrap_calls": 0,
            "lifecycle_use_count": 9,
            "context_compilations": 3,
        }
    )

    report = build_feature_lifecycle_report(
        [receipt], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    assert report["passed"] is True
    assert report["persistent_execution_state"]["selection_event_count"] == 1
    assert report["persistent_execution_state"]["selection_provider_calls"] == 0


def test_lifecycle_report_fails_a_missed_trigger():
    report = build_feature_lifecycle_report(
        [_receipt(missed="recovery")],
        forced_feature_ids=FEATURE_IDS,
        forced_proof=FORCED_PROOF,
    )

    assert report["passed"] is False
    assert "demo:recovery:missed_trigger" in report["failures"]


def test_controller_accounted_claim_is_not_reported_as_pending():
    report = build_feature_lifecycle_report(
        [_receipt(pending=True)],
        forced_feature_ids=FEATURE_IDS,
        forced_proof=FORCED_PROOF,
    )

    assert report["passed"] is True
    obligations = next(
        row for row in report["direct_features"] if row["feature_id"] == "obligations"
    )
    assert obligations["accountability"]["controller_state_considered"] == 1
    assert obligations["accountability"].get("pending_decision_claim", 0) == 0


def test_compiler_accounting_overrides_an_archived_expired_claim_label():
    receipt = _receipt()
    receipt["features"]["effect_accountability"][0][
        "outcome"
    ] = "expired_unconsumed_claim"

    report = build_feature_lifecycle_report(
        [receipt], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    obligations = next(
        row for row in report["direct_features"] if row["feature_id"] == "obligations"
    )
    assert obligations["accountability"]["controller_state_considered"] == 1
    assert obligations["accountability"].get("expired_unconsumed_claim", 0) == 0


def test_identical_downloaded_receipts_are_deduplicated_by_task():
    receipt = _receipt()

    report = build_feature_lifecycle_report(
        [receipt, receipt],
        forced_feature_ids=FEATURE_IDS,
        forced_proof=FORCED_PROOF,
    )

    assert report["passed"] is True
    assert report["task_count"] == 1


def test_lifecycle_report_fails_when_an_expected_task_has_no_receipt():
    report = build_feature_lifecycle_report(
        [_receipt()],
        forced_feature_ids=FEATURE_IDS,
        forced_proof=FORCED_PROOF,
        expected_task_ids=("demo", "missing"),
    )

    assert report["passed"] is False
    assert "missing:receipt_missing" in report["failures"]


def test_lifecycle_report_recomputes_persistent_use_instead_of_trusting_booleans():
    receipt = _receipt()
    persistent = receipt["product_mechanism_census"]["persistent_execution_state"]
    persistent.update(
        {
            "exercised": True,
            "repeated_deterministic_use": True,
            "lifecycle_use_count": 0,
            "bootstrap_calls": 0,
            "context_compilations": 0,
            "preflight_projections": 0,
            "postflight_commits": 0,
            "graph_rebases": 0,
        }
    )

    report = build_feature_lifecycle_report(
        [receipt], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    assert report["passed"] is False
    assert "demo:persistent_bootstrap_count" in report["failures"]
    assert "demo:persistent_not_repeated_from_counts" in report["failures"]


def test_conflicted_receipts_are_not_counted_as_positive_evidence():
    first = _receipt()
    second = _receipt()
    second["features"]["produced_counts"]["obligations"] = 2

    report = build_feature_lifecycle_report(
        [first, second], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    assert report["passed"] is False
    assert report["task_count"] == 0
    assert "demo:duplicate_receipt_conflict" in report["failures"]


def test_operational_controls_are_reconciled_from_atomic_rows():
    receipt = _receipt()
    receipt["metrics"].update(
        submit_holds=1,
        completion_certificate_evaluations=1,
        red_test_probe_attempts=1,
        context_recap_receipts=1,
        context_recap_fallbacks=1,
    )

    report = build_feature_lifecycle_report(
        [receipt], forced_feature_ids=FEATURE_IDS, forced_proof=FORCED_PROOF
    )

    assert report["passed"] is False
    failures = report["operational_controls"]["failures"]
    assert "demo:submit_hold_reconciliation" in failures
    assert "demo:completion_certificate_reconciliation" in failures
    assert "demo:red_test_reconciliation" in failures
    assert "demo:recap_receipt_reconciliation" in failures
    assert "demo:recap_fallback_reconciliation" in failures


def test_forced_feature_claim_requires_exact_commit_proof():
    report = build_feature_lifecycle_report(
        [_receipt()], forced_feature_ids=FEATURE_IDS
    )

    assert report["passed"] is False
    assert "forced_feature_proof_missing_or_invalid" in report["failures"]
