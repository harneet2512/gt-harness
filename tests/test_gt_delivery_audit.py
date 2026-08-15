from __future__ import annotations

import json

from gt_engine.deep_metrics import extract_trajectory
from gt_engine.delivery_audit import audit_provider_deliveries
from scripts.central_run_diff import _context_summary, _first_visible_call
from scripts.central_trajectory_audit import audit_run_root


def _context(call: int, *, request: str, provider: str) -> dict:
    return {
        "call": call,
        "request_payload_sha256": request,
        "provider_messages_sha256": provider,
        "provider_message_count": 4,
        "provider_changed_message_indices": [1, 2, 3],
        "dispatch_status": "response_received",
        "context_fact_candidates": 0,
        "context_facts_accounted": 0,
    }


def _preemptive(*, request: str = "req-1", call: int = 1) -> dict:
    return {
        "frame_id": "frame-1",
        "claim_ids": ["claim-1"],
        "evidence_action": 0,
        "eligible_call": call,
        "delivered_before_call": call,
        "delivered_before_model_query": True,
        "one_step_late": False,
        "predictive": False,
        "request_payload_sha256": request,
        "provider_messages_sha256": f"provider-{call}",
        "provider_message_indices": [1],
        "chars": 31,
        "selected_evidence": [
            {
                "path": "src/worker.py",
                "start_line": 1,
                "end_line": 8,
                "symbol": "calculateTotal",
                "support_kind": "certified_relation",
                "supporting_channels": ["structural"],
                "origin": "preexisting_repository",
                "authority": "certified_relation",
                "novel_to_provider_view": True,
                "known_to_model": False,
                "materiality_reason": "implementation_direct_relation",
                "source_revision": "source-1",
                "origin_revision": "source-1",
                "relation_endpoint": "src/worker.py",
                "declared_validation_id": "",
            }
        ],
    }


def _legacy(*, request: str = "req-1", call: int = 1) -> dict:
    return {
        "delivery_id": "guidance-1",
        "feature_id": "syntax_result",
        "claim_ids": ["claim-legacy"],
        "evidence_action": 0,
        "first_eligible_call": call,
        "delivered_before_call": call,
        "delivered_before_model_query": True,
        "not_predictive": True,
        "one_step_late": False,
        "request_payload_sha256": request,
        "provider_messages_sha256": f"provider-{call}",
        "message_index": 2,
        "chars": 17,
    }


def _claim_meta(claim_id: str, **overrides: object) -> dict:
    value = {
        "claim_id": claim_id,
        "origin": "preexisting_repository",
        "authority": "identity_only",
        "novel_to_provider_view": True,
        "known_to_model": False,
        "materiality_reason": "new_unresolved_task_obligation",
        "source_revision": "source-1",
        "origin_revision": "source-1",
        "relation_endpoint": "",
        "declared_validation_id": "",
    }
    value.update(overrides)
    return value


def _receipt(*, include_preemptive: bool = True) -> dict:
    return {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "guidance_deliveries": [_legacy()],
        "repository_intelligence": {
            "frontier_deliveries": [
                {
                    "claim_ids": ["claim-frontier"],
                    "fact_ids": ["fact-frontier"],
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 3,
                    "chars": 13,
                }
            ]
        },
        "preemptive_retrieval": {
            "deliveries": [_preemptive()] if include_preemptive else [],
        },
    }


def test_legacy_only_accounting_would_miss_preemptive_delivery():
    rows, failures, totals = audit_provider_deliveries(_receipt())

    assert not failures
    assert totals["delivery_count"] == 3
    assert {row["surface"] for row in rows} == {
        "guidance",
        "preemptive_retrieval",
        "repository_frontier",
    }
    assert totals["visible_chars"] == 61
    assert totals["claim_count"] == 4
    assert totals["surfaces"]["preemptive_retrieval"]["delivery_count"] == 1


def test_run_diff_visible_call_and_summary_include_preemptive_surface():
    receipt = _receipt()
    receipt["guidance_deliveries"] = []

    assert _first_visible_call(receipt) == 1
    summary = _context_summary(receipt)
    assert summary["provider_delivery_count"] == 2
    assert summary["provider_delivery_surfaces"]["preemptive_retrieval"][
        "visible_chars"
    ] == 31


def test_bad_preemptive_hash_and_timing_fail_closed():
    receipt = _receipt()
    receipt["preemptive_retrieval"]["deliveries"][0]["request_payload_sha256"] = "wrong"
    receipt["preemptive_retrieval"]["deliveries"][0]["one_step_late"] = True
    receipt["preemptive_retrieval"]["deliveries"][0]["predictive"] = True

    _rows, failures, totals = audit_provider_deliveries(receipt)

    assert any("delivery_request_hash_context_mismatch" in item for item in failures)
    assert totals["late_count"] == 1
    assert totals["predictive_count"] == 1
    assert totals["timely_count"] == 2


def test_provider_messages_hash_must_match_exact_call_context():
    receipt = _receipt()
    receipt["preemptive_retrieval"]["deliveries"][0][
        "provider_messages_sha256"
    ] = "wrong-provider"

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("delivery_provider_hash_context_mismatch" in item for item in failures)


def test_prepared_or_marker_failed_request_cannot_authorize_a_visible_delivery():
    receipt = _receipt()
    receipt["model_call_contexts"][0]["dispatch_status"] = "marker_error"

    rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("delivery_request_not_dispatched" in item for item in failures)
    assert all(row["dispatch_valid"] is False for row in rows)
    assert all(row["deterministic_status"] == "INVALID" for row in rows)


def test_delivery_requires_an_in_range_gt_changed_provider_message_index():
    receipt = _receipt()
    receipt["preemptive_retrieval"]["deliveries"][0]["provider_message_indices"] = [9]

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("delivery_message_index_out_of_range" in item for item in failures)
    assert any("delivery_message_index_not_gt_changed" in item for item in failures)


def test_delivery_without_a_provider_message_index_fails_closed():
    receipt = _receipt()
    receipt["preemptive_retrieval"]["deliveries"][0].pop("provider_message_indices")

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("delivery_message_index_missing" in item for item in failures)


def test_preemptive_delivery_requires_persisted_semantic_support():
    receipt = _receipt()
    receipt["preemptive_retrieval"]["deliveries"][0]["selected_evidence"] = []

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("preemptive_delivery_semantic_support_missing" in item for item in failures)


def test_duplicate_claim_across_visible_surfaces_is_rejected():
    receipt = _receipt()
    receipt["repository_intelligence"]["frontier_deliveries"][0]["claim_ids"] = [
        "claim-1"
    ]
    receipt["repository_intelligence"]["frontier_deliveries"][0]["fact_ids"] = []

    _rows, failures, totals = audit_provider_deliveries(receipt)

    assert any("duplicate_provider_claim" in item for item in failures)
    assert totals["duplicate_count"] == 1


def test_duplicate_preemptive_delivery_is_reported():
    receipt = _receipt()
    duplicate = dict(receipt["preemptive_retrieval"]["deliveries"][0])
    receipt["preemptive_retrieval"]["deliveries"].append(duplicate)

    _rows, failures, totals = audit_provider_deliveries(receipt)

    assert any("duplicate_provider_delivery:id:frame-1" in item for item in failures)
    assert totals["duplicate_count"] == 1


def test_persistent_state_refresh_is_repeated_context_not_duplicate_evidence():
    receipt = {
        "model_call_contexts": [
            _context(1, request="req-1", provider="provider-1"),
            _context(2, request="req-2", provider="provider-2"),
        ],
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-1",
                    "claim_ids": ["phase-current"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 30,
                    "claim_metadata": [_claim_meta("phase-current")],
                },
                {
                    "delivery_id": "state-2",
                    "claim_ids": ["phase-current"],
                    "evidence_action": 1,
                    "first_eligible_call": 2,
                    "delivered_before_call": 2,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-2",
                    "provider_messages_sha256": "provider-2",
                    "message_index": 1,
                    "chars": 30,
                    "claim_metadata": [_claim_meta("phase-current")],
                },
            ]
        },
    }

    rows, failures, totals = audit_provider_deliveries(receipt)

    assert any("duplicate_provider_claim" in item for item in failures)
    assert totals["delivery_count"] == 2
    assert totals["duplicate_count"] == 1
    assert rows[1]["persistent_state_refresh"] is False


def test_model_authored_preemptive_evidence_is_rejected_even_when_hashes_are_valid():
    receipt = _receipt()
    evidence = receipt["preemptive_retrieval"]["deliveries"][0]["selected_evidence"][0]
    evidence["origin"] = "model_authored"

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("preemptive_delivery_semantic_support_missing" in item for item in failures)


def test_persistent_phase_only_or_known_claim_is_rejected():
    receipt = {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-1",
                    "claim_ids": ["phase-only"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 20,
                    "claim_metadata": [
                        _claim_meta(
                            "phase-only",
                            novel_to_provider_view=False,
                            known_to_model=True,
                            materiality_reason="phase_only",
                        )
                    ],
                }
            ]
        },
    }

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any("persistent_delivery_semantic_authority_invalid" in item for item in failures)


def test_trajectory_audit_exposes_all_provider_surface_totals(tmp_path):
    task_dir = tmp_path / "run-task-demo"
    task_dir.mkdir()
    (task_dir / "miniswe_trajectory.json").write_text(
        json.dumps({"messages": []}), encoding="utf-8"
    )
    receipt = _receipt()
    receipt["actions"] = 0
    receipt["features"] = {"effect_trace": []}
    (task_dir / "central_receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    report = audit_run_root(tmp_path)

    assert report["provider_delivery_totals"]["delivery_count"] == 3
    assert report["provider_delivery_totals"]["visible_chars"] == 61
    assert report["tasks"]["demo"]["provider_delivery_totals"]["claim_count"] == 4


def test_deep_metrics_reports_surface_totals_not_just_guidance(tmp_path):
    trajectory = tmp_path / "trajectory.json"
    receipt = tmp_path / "receipt.json"
    trajectory.write_text(
        json.dumps({"messages": [{"role": "assistant", "extra": {"actions": []}}]}),
        encoding="utf-8",
    )
    payload = _receipt()
    payload["metrics"] = {}
    payload["features"] = {}
    payload["info"] = {"exit_status": "Submitted"}
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    metrics = extract_trajectory(
        trajectory, task="delivery-metrics", reward=1, receipt_path=receipt
    )

    assert metrics["guidance_deliveries"] == 1
    assert metrics["provider_delivery_count"] == 3
    assert metrics["provider_delivery_visible_chars"] == 61
    assert metrics["provider_delivery_surface_counts"]["preemptive_retrieval"] == 1
    assert metrics["provider_delivery_surface_chars"]["preemptive_retrieval"] == 31


def test_persistent_import_advisory_materiality_is_rejected():
    receipt = {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "guidance_deliveries": [],
        "repository_intelligence": {"frontier_deliveries": []},
        "preemptive_retrieval": {"deliveries": []},
        "progress": {},
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-import",
                    "claim_ids": ["import-claim"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 40,
                    "claim_metadata": [
                        _claim_meta(
                            "import-claim",
                            authority="certified_relation",
                            materiality_reason="related_advisory_obligation",
                            relation="imports",
                            relation_endpoint="tests/test_pipeline.py",
                        )
                    ],
                }
            ]
        },
    }

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any(
        "persistent_delivery_semantic_authority_invalid" in item for item in failures
    )


def test_persistent_bootstrap_ordered_materiality_is_rejected():
    receipt = {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "guidance_deliveries": [],
        "repository_intelligence": {"frontier_deliveries": []},
        "preemptive_retrieval": {"deliveries": []},
        "progress": {},
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-ordered",
                    "claim_ids": ["ordered-claim"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 40,
                    "claim_metadata": [
                        _claim_meta(
                            "ordered-claim",
                            authority="certified_relation",
                            materiality_reason="bootstrap_ordered_next_item",
                            relation="calls",
                            relation_endpoint="src/api.py",
                        )
                    ],
                }
            ]
        },
    }

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any(
        "persistent_delivery_semantic_authority_invalid" in item for item in failures
    )


def test_persistent_implements_advisory_materiality_is_rejected():
    receipt = {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "guidance_deliveries": [],
        "repository_intelligence": {"frontier_deliveries": []},
        "preemptive_retrieval": {"deliveries": []},
        "progress": {},
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-implements",
                    "claim_ids": ["implements-claim"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 40,
                    "claim_metadata": [
                        _claim_meta(
                            "implements-claim",
                            authority="certified_relation",
                            materiality_reason="related_advisory_obligation",
                            relation="implements",
                            relation_endpoint="src/api.py",
                        )
                    ],
                }
            ]
        },
    }

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any(
        "persistent_delivery_semantic_authority_invalid" in item for item in failures
    )


def test_persistent_certified_related_file_without_material_relation_is_rejected():
    receipt = {
        "model_call_contexts": [_context(1, request="req-1", provider="provider-1")],
        "guidance_deliveries": [],
        "repository_intelligence": {"frontier_deliveries": []},
        "preemptive_retrieval": {"deliveries": []},
        "progress": {},
        "persistent_execution_state": {
            "deliveries": [
                {
                    "delivery_id": "state-empty-relation",
                    "claim_ids": ["related-claim"],
                    "evidence_action": 0,
                    "first_eligible_call": 1,
                    "delivered_before_call": 1,
                    "delivered_before_model_query": True,
                    "not_predictive": True,
                    "one_step_late": False,
                    "request_payload_sha256": "req-1",
                    "provider_messages_sha256": "provider-1",
                    "message_index": 1,
                    "chars": 40,
                    "claim_metadata": [
                        _claim_meta(
                            "related-claim",
                            authority="certified_relation",
                            materiality_reason="newly_certified_related_file",
                            relation="",
                            relation_endpoint="src/api.py",
                        )
                    ],
                }
            ]
        },
    }

    _rows, failures, _totals = audit_provider_deliveries(receipt)

    assert any(
        "persistent_delivery_semantic_authority_invalid" in item for item in failures
    )
