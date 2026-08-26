from __future__ import annotations

import hashlib

from gt_harness.analysis.delivery import audit_treatment_delivery
from gt_harness.analysis.metrics import TerminalDisposition, summarize_run, terminal_disposition
from gt_harness.analysis.uptake import measure_delivery_uptake


def _treatment(context: str = "verified context") -> dict[str, object]:
    claim = "c" * 64
    return {
        "schema": "gt.treatment_receipt.v4",
        "treatment": "groundtruth",
        "treatment_status": "ACTIVE",
        "graph_available": True,
        "graph_status": "READY",
        "graph_commit_sha": "a" * 40,
        "source_revision": "source-a",
        "delivery_reconciliation": "PASS",
        "delivery_count": 1,
        "delivered_claim_ids": [claim],
        "evidence_items_delivered": 1,
        "provider_delivery_receipts": [
            {
                "schema": "gt.provider_delivery.v2",
                "delivery_index": 1,
                "kind": "repository_start",
                "delivered_before_call": 1,
                "source_revision": "source-a",
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "context_token_count": 3,
                "context_char_count": len(context),
                "serialized_claim_ids": [claim],
                "provider_claim_tokens": [claim],
                "provider_visible_feature_counts": {"exact_edit_targets": 1},
                "provider_visible_role_paths": {
                    "EXACT_EDIT_TARGET": ["src/owner.py"]
                },
            }
        ],
    }


def _run(context: str = "verified context") -> dict[str, object]:
    return {
        "schema": "gt.run_receipt.v1",
        "task_id": "task-one",
        "trial_id": "1",
        "status": "COMPLETED",
        "treatment": "groundtruth",
        "resolved": True,
        "evaluation": {
            "schema": "gt.evaluation_binding.v1",
            "official_verifier_authoritative": True,
        },
        "initial_context": context,
        "repository_end": {
            "commit_sha": "a" * 40,
            "source_revision": "source-a",
        },
        "treatment_receipt": _treatment(context),
        "transcript": [
            {
                "role": "assistant",
                "extra": {"actions": [{"command": "sed -n '1,120p' src/owner.py"}]},
            },
            {
                "role": "assistant",
                "extra": {"actions": [{"command": "apply_patch < patch-for-src/owner.py"}]},
            },
        ],
        "provider_calls": 2,
        "input_tokens": 100,
        "output_tokens": 30,
        "duration_ms": 500,
    }


def test_delivery_audit_cross_binds_context_revision_claims_and_paths() -> None:
    run = _run()

    audit = audit_treatment_delivery(
        run["treatment_receipt"],
        initial_context=run["initial_context"],
        repository_end=run["repository_end"],
    )

    assert audit["status"] == "PASS"
    assert audit["claim_count"] == 1
    assert audit["delivered_paths"] == ["src/owner.py"]


def test_delivery_audit_fails_closed_on_dummy_or_unbound_context() -> None:
    treatment = _treatment()
    treatment["provider_delivery_receipts"][0]["serialized_claim_ids"] = []

    audit = audit_treatment_delivery(
        treatment,
        initial_context="different context",
        repository_end={"commit_sha": "b" * 40, "source_revision": "stale"},
    )

    assert audit["status"] == "FAIL"
    assert "graph_commit_mismatch" in audit["failures"]
    assert "graph_source_revision_mismatch" in audit["failures"]
    assert "delivery_1:initial_context_hash_mismatch" in audit["failures"]
    assert "delivered_claim_set_mismatch" in audit["failures"]


def test_uptake_counts_only_later_exact_path_actions() -> None:
    run = _run()

    uptake = measure_delivery_uptake(run)

    assert uptake["used_paths"] == ["src/owner.py"]
    assert uptake["edited_paths"] == ["src/owner.py"]
    assert uptake["path_uptake_rate"] == 1.0
    assert uptake["hidden_reasoning_inferred"] is False


def test_run_metrics_requires_official_grading_and_types_terminal_failures() -> None:
    metrics = summarize_run(_run())
    error = _run()
    error["status"] = "ERROR"
    error["resolved"] = None
    error["evaluation"] = None
    error["termination"] = {"kind": "PROVIDER_TRANSPORT"}

    assert metrics["officially_graded"] is True
    assert metrics["resolved"] is True
    assert metrics["delivery"]["status"] == "PASS"
    assert terminal_disposition(error) is TerminalDisposition.PROVIDER_TRANSPORT
