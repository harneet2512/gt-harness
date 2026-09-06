from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from gt_harness.runtime_receipts import issue_runtime_receipts, verify_runtime_receipt


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("synthetic_transport", [False, True])
def test_successful_miniswe_run_issues_bound_product_and_adapter_receipts(
    tmp_path: Path, synthetic_transport: bool,
) -> None:
    state = tmp_path / "gt-state"
    task_state = state / "task-hash"
    trajectory = tmp_path / "miniswe_trajectory.json"
    report = tmp_path / "miniswe_report.json"
    product = tmp_path / "gt-run.json"
    adapter = tmp_path / "benchmark-adapter.json"
    _write_json(
        trajectory,
        {
            "messages": [{"role": "assistant", "content": "done"}],
            "info": {
                "model_stats": {"api_calls": 3},
                "exit_status": "Submitted",
            },
        },
    )
    _write_json(
        report,
        {
            "synthetic_transport": synthetic_transport,
            "model": "meta/muse-spark-1.2-contributor",
            "terminal": "submitted_unverified",
            "exit_code": 0,
            "gt_mode": "advisory",
            "research_valid": True,
            "gt": {
                "contract_shipped": True,
                "terminal_requests": 3,
                "delivered_evidence": 1,
                "requested_model": "meta/muse-spark-1.2-contributor",
                "provider_reported_model": "meta/muse-spark-1.2-contributor",
                "resolved_model": "openai/meta/muse-spark-1.2-contributor",
                "event_journal": {"event_count": 3, "event_head": "a" * 64},
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 5,
                    "prompt_cache_hit_tokens": 11,
                },
                "verified": False,
                "unmet_predicates": ["pred-1"],
                "unverified_predicates": ["pred-1"],
            },
        },
    )
    events = [
        {
            "event": "context_addition_delivery",
            "event_hash": "1" * 64,
            "sequence": 1,
            "lane": "prompt",
            "kind": "context_contract",
            "evidence_type": "context_contract",
            "dedup_key": "prompt-contract-1",
            "target": "provider_prompt",
            "payload_sha256": "a" * 64,
            "action_index": 0,
            "iteration": 0,
            "rendered_bytes": 100,
        },
        {
            "event": "receipt",
            "event_hash": "2" * 64,
            "sequence": 2,
            "transition": "delivered",
            "dedup_key": "prompt-contract-1",
            "evidence_type": "context_contract",
            "iteration": 0,
            "payload_hash": "a" * 64,
        },
        {
            "event": "provider_delivery",
            "event_hash": "3" * 64,
            "sequence": 3,
            "iteration": 1,
            "request_id": "request-1",
            "delivery_ids": ["a" * 64],
        },
        {
            "event": "provider_admission", "event_hash": "c" * 64, "sequence": 4,
            "status": "admitted", "reason": "within_provider_window",
            "request_tokens": 100, "request_bytes": 400,
            "context_window_tokens": 131072, "reserved_output_tokens": 16384,
            "input_budget_tokens": 114688, "metadata_source": "openrouter:/models",
        },
        {
            "event": "provider_response",
            "event_hash": "4" * 64,
            "sequence": 4,
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 4},
                "cost": 0.01,
            },
        },
        {
            "event": "evidence_delivery",
            "event_hash": "5" * 64,
            "sequence": 5,
            "lane": "sealed",
            "kind": "localization",
            "evidence_type": "localization",
            "dedup_key": "localization-1",
            "artifact_sha256": "c" * 64,
            "payload_sha256": "9" * 64,
            "action_index": 0,
            "iteration": 1,
            "rendered_bytes": 123,
        },
        {
            "event": "receipt",
            "event_hash": "6" * 64,
            "sequence": 6,
            "transition": "delivered",
            "dedup_key": "localization-1",
            "evidence_type": "localization",
            "iteration": 1,
            "payload_hash": "9" * 64,
        },
        {
            "event": "provider_delivery",
            "event_hash": "7" * 64,
            "sequence": 7,
            "iteration": 2,
            "request_id": "request-2",
            "delivery_ids": ["9" * 64],
        },
        {
            "event": "provider_admission", "event_hash": "e" * 64, "sequence": 8,
            "status": "admitted", "reason": "within_provider_window",
            "request_tokens": 200, "request_bytes": 800,
            "context_window_tokens": 131072, "reserved_output_tokens": 16384,
            "input_budget_tokens": 114688, "metadata_source": "openrouter:/models",
        },
        {
            "event": "provider_response",
            "event_hash": "8" * 64,
            "sequence": 8,
            "usage": {
                "prompt_tokens": 23,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 7},
                "cost": 0.02,
            },
        },
        {
            "event": "delivery_refused",
            "event_hash": "0" * 64,
            "sequence": 9,
            "lane": "prompt",
            "kind": "context_delta",
            "dedup_key": "prompt-delta-refused",
            "reason": "delivery_byte_ceiling",
            "candidate_ordinal": 3,
            "rendered_bytes": 1_401,
            "payload_sha256": "f" * 64,
            "admitted_count": 2,
            "admitted_bytes": 223,
        },
        {
            "event": "dense_index_ready",
            "event_hash": "b" * 64,
            "sequence": 10,
            "query_ready": True,
            "model_sha256": "5" * 64,
            "tokenizer_sha256": "4" * 64,
            "dimension": 768,
            "document_count": 4,
            "query_result_count": 4,
            "index_sha256": "3" * 64,
        },
        {
            "event": "provider_admission", "event_hash": "f" * 64, "sequence": 11,
            "status": "admitted", "reason": "within_provider_window",
            "request_tokens": 300, "request_bytes": 1200,
            "context_window_tokens": 131072, "reserved_output_tokens": 16384,
            "input_budget_tokens": 114688, "metadata_source": "openrouter:/models",
        },
        {
            "event": "session_closed",
            "event_hash": "d" * 64,
            "sequence": 11,
            "terminal": "submitted_unverified",
        },
    ]
    task_state.mkdir(parents=True)
    (task_state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    _write_json(
        task_state / "reproducibility_manifest.json",
        {
            "schema": "gt.repro.v1",
            "research_valid": True,
            "gt_mode": "advisory",
            "engine_integrity": {"schema": "gt.engine_integrity.v1", "valid": True,
                                 "mode": "advisory", "issues": [], "disabled_stage": ""},
            "provider_receipts": {"request_count": 3, "valid": True},
            "model": {"match": True},
            "event_journal": {
                "event_count": 14,
                "event_head": "d" * 64,
                "valid": True,
                "issues": [],
            },
        },
    )
    graph_state = state / "repo-hash"
    graph_state.mkdir(parents=True)
    graph_db = graph_state / "graph.db"
    with sqlite3.connect(graph_db) as db:
        db.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE cochanges (id INTEGER)")
        db.executemany("INSERT INTO cochanges VALUES (?)", [(1,), (2,)])
    from gt_engine.indexer import _graph_phase_metadata, _sealed_json
    from gt_harness.product import groundtruth_release

    binding = {"repository_root_sha256": "b" * 64, "source_manifest_sha256": "c" * 64,
               "task_id": "task-a", "product_source_sha": "f" * 40,
               "identity_scope": "benchmark_bound"}
    producer = groundtruth_release()["producer_sha256"]
    resource_path = graph_state / "index-resource.json"
    _sealed_json(resource_path, {"schema": "gt.index_resource.v1", **binding,
                 "status": "completed", "exit_code": 0, "error_code": "", "memory_evidence": False,
                 "producer_binary_sha256": producer}, "evidence_sha256")
    graph_digest = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    _write_json(
        graph_state / "graph.manifest.json",
        {
            "schema": "gt.graph_certification.v1",
            **binding, **_graph_phase_metadata(graph_db),
            "binary_certified": True,
            "binary_sha256": producer,
            "graph_sha256": graph_digest,
            "graph_bytes": graph_db.stat().st_size,
            "index_resource_sha256": hashlib.sha256(resource_path.read_bytes()).hexdigest(),
            "sqlite_quick_check": "ok",
            "cochange_rows": 2,
        },
    )
    # Language servers are mandatory capability and the receipt layer enforces
    # it, so a run without this seal is one that measured GT with the
    # highest-precision edge tier off.
    _sealed_json(
        graph_state / "lsp-promotion.json",
        {"schema": "gt.lsp_promotion.v1", **binding,
         "graph_sha256": graph_digest, "status": "promotion_not_scheduled",
         "servers_detected": ["gopls", "pyright-langserver"], "server_count": 2},
        "promotion_sha256",
    )

    issued = issue_runtime_receipts(
        report_path=report,
        trajectory_path=trajectory,
        state_dir=state,
        product_receipt_path=product,
        adapter_receipt_path=adapter,
        task_id="task-a",
        product_source_sha="f" * 40,
        treatment="groundtruth",
        requested_model="meta/muse-spark-1.2-contributor",
        scaffold_version="2.4.6",
        time_budget_seconds=3600,
    )

    product_row = json.loads(product.read_text(encoding="utf-8"))
    adapter_row = json.loads(adapter.read_text(encoding="utf-8"))
    copied = product.with_name("gt-run.trajectory.json")
    assert issued == product_row
    assert product_row["schema"] == "gt.run_receipt.v1"
    assert product_row["status"] == "COMPLETED"
    assert product_row["provider_calls"] == 3
    assert product_row["provider_completed_calls"] == 2
    assert product_row["provider_failed_calls"] == 1
    assert product_row["input_tokens"] == 40
    assert product_row["cached_tokens"] == 11
    assert product_row["output_tokens"] == 5
    assert product_row["total_cost"] == 0.03
    assert product_row["treatment_receipt"]["schema"] == "gt.miniswe_treatment_receipt.v1"
    treatment = product_row["treatment_receipt"]
    assert treatment["delivery_count"] == 2
    assert treatment["prompt_delivery_count"] == 1
    assert treatment["sealed_delivery_count"] == 1
    assert treatment["prompt_context_deliveries"][0]["kind"] == "context_contract"
    assert treatment["evidence_deliveries"][0]["event_hash"] == "5" * 64
    assert treatment["refused_deliveries"][0]["reason"] == "delivery_byte_ceiling"
    assert treatment["refused_deliveries"][0]["event_sequence"] == 9
    assert [row["request_tokens"] for row in treatment["provider_admissions"]] == [100, 200, 300]
    assert treatment["provider_delivery_receipts"][0]["event_sequence"] == 1
    assert treatment["provider_delivery_receipts"][1]["event_sequence"] == 5
    assert treatment["delivery_budget"] == {
        "schema": "gt.delivery_budget.v2",
        "scope": "provider_decision",
        "boundary_claim_limit": 4,
        "unit": "utf8_bytes",
        "conversion_from_legacy_tokens": "4_bytes_per_token",
        "sealed_limit": 1_400,
        "prompt_contract_limit": 2_000,
        "prompt_delta_limit": 1_400,
        "total_limit": 9_600,
        "total_observed": 223,
        "task_delivery_limit": None,
        "admitted_count": 2,
        "refused_count": 1,
    }
    assert product_row["treatment_receipt"]["graph_certification"]["graph_sha256"] == graph_digest
    assert product_row["treatment_receipt"]["graph_utilisation"]["cochange_rows"] == 2
    assert (
        product_row["integrity"]["trajectory_sha256"]
        == hashlib.sha256(trajectory.read_bytes()).hexdigest()
    )
    assert copied.read_bytes() == trajectory.read_bytes()
    assert adapter_row == {
        "synthetic_transport": synthetic_transport,
        "schema": "gt.benchmark_adapter_receipt.v1",
        "task_id": "task-a",
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": "groundtruth",
        "requested_model": "meta/muse-spark-1.2-contributor",
        "effective_model": "openai/meta/muse-spark-1.2-contributor",
        "agent_scaffold_version": "2.4.6",
        "product_source_sha": "f" * 40,
        "time_budget_seconds": 3600,
    }
    assert product_row["synthetic_transport"] is synthetic_transport
    assert verify_runtime_receipt(product) == (
        ["synthetic_transport_not_paid_evidence"] if synthetic_transport else []
    )
    # Language servers are mandatory capability, so a run that reports none
    # must not be accepted. Driven through the real validator on the real
    # receipt: a gate nothing exercises is the defect it exists to prevent.
    intact = product.read_text(encoding="utf-8")
    degraded = json.loads(intact)
    degraded["treatment_receipt"]["lsp_promotion"] = {
        "schema": "gt.lsp_promotion.v1", "status": "promotion_no_servers",
        "servers_detected": [], "server_count": 0,
    }
    product.write_text(json.dumps(degraded), encoding="utf-8")
    assert "treatment_lsp_servers_absent" in verify_runtime_receipt(product)
    product.write_text(intact, encoding="utf-8")
    assert "treatment_lsp_servers_absent" not in verify_runtime_receipt(product)
    original_resource = resource_path.read_bytes()
    resource_path.unlink()
    assert "treatment_graph_artifact_invalid:index_resource_mismatch" in verify_runtime_receipt(product)
    resource_path.write_bytes(original_resource)
    original_adapter = adapter.read_bytes()
    adapter.unlink()
    assert "product_adapter_receipt_missing_or_invalid" in verify_runtime_receipt(product)
    adapter.write_bytes(original_adapter)
    if synthetic_transport:
        concealed = {**product_row, "synthetic_transport": False}
        _write_json(product, concealed)
        errors = verify_runtime_receipt(product)
        assert "product_transport_report_mismatch" in errors
        assert "synthetic_transport_not_paid_evidence" in errors
        _write_json(product, product_row)

    refused_admission = {
        "event": "provider_admission",
        "event_hash": "9" * 64,
        "sequence": 12,
        "status": "refused",
        "reason": "GT_PROVIDER_REQUEST_TOO_LARGE",
        "request_tokens": 114_689,
        "request_bytes": 458_756,
        "context_window_tokens": 131_072,
        "reserved_output_tokens": 16_384,
        "input_budget_tokens": 114_688,
        "metadata_source": "openrouter:/models",
    }
    events_with_refusal = [*events, refused_admission]
    (task_state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events_with_refusal),
        encoding="utf-8",
    )
    completed_with_refusal = deepcopy(product_row)
    completed_with_refusal["integrity"]["events_sha256"] = hashlib.sha256(
        (task_state / "events.jsonl").read_bytes()
    ).hexdigest()
    completed_with_refusal["treatment_receipt"]["event_journal"].update(
        event_count=len(events_with_refusal),
        event_head=refused_admission["event_hash"],
    )
    completed_with_refusal["treatment_receipt"]["provider_admissions"].append(
        {
            "event_hash": refused_admission["event_hash"],
            "event_sequence": refused_admission["sequence"],
            "status": refused_admission["status"],
            "reason": refused_admission["reason"],
            "request_tokens": refused_admission["request_tokens"],
            "request_bytes": refused_admission["request_bytes"],
            "context_window_tokens": refused_admission["context_window_tokens"],
            "reserved_output_tokens": refused_admission["reserved_output_tokens"],
            "input_budget_tokens": refused_admission["input_budget_tokens"],
            "metadata_source": refused_admission["metadata_source"],
        }
    )
    _write_json(product, completed_with_refusal)
    assert "treatment_provider_admission_count_mismatch" in verify_runtime_receipt(
        product
    )

    (task_state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    _write_json(product, product_row)

    mutations = [
        (
            "treatment_engine_integrity_invalid",
            lambda row: row["treatment_receipt"]["reproducibility_manifest"].pop(
                "engine_integrity"
            ),
        ),
        (
            "treatment_engine_integrity_invalid",
            lambda row: row["treatment_receipt"]["reproducibility_manifest"][
                "engine_integrity"
            ].update(mode="off"),
        ),
        (
            "treatment_engine_integrity_invalid",
            lambda row: row["treatment_receipt"]["reproducibility_manifest"][
                "engine_integrity"
            ].update(disabled_stage="global_kill_switch"),
        ),
        (
            "treatment_delivery_boundary_invalid:duplicate_delivery_identity",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"].extend(
                deepcopy(row["treatment_receipt"]["provider_delivery_receipts"]) * 12
            ),
        ),
        (
            "treatment_delivery_late",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"][0].update(
                same_observation=False
            ),
        ),
        (
            "treatment_delivery_context_budget_exceeded",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"][1].update(
                context_byte_count=1_401
            ),
        ),
        (
            "treatment_prompt_context_budget_exceeded",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"][0].update(
                delivery_kind="localization"
            ),
        ),
        (
            "treatment_delivery_context_budget_exceeded",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"][0].update(
                context_byte_count=2_001,
                byte_limit=2_000,
            ),
        ),
        (
            "treatment_provider_delivery_census_mismatch",
            lambda row: row["treatment_receipt"]["provider_delivery_receipts"][1].update(
                context_sha256=row["treatment_receipt"]["provider_delivery_receipts"][0][
                    "context_sha256"
                ],
                delivery_identity=row["treatment_receipt"]["provider_delivery_receipts"][0][
                    "delivery_identity"
                ],
            ),
        ),
        (
            "treatment_delivery_refusal_invalid",
            lambda row: row["treatment_receipt"]["refused_deliveries"][0].update(
                dedup_key="prompt-contract-1"
            ),
        ),
        (
            "treatment_refused_then_delivered",
            lambda row: row["treatment_receipt"]["refused_deliveries"][0].update(
                dedup_key="prompt-contract-1",
                delivery_identity=row["treatment_receipt"]["provider_delivery_receipts"][0][
                    "delivery_identity"
                ],
                event_sequence=9,
            ),
        ),
        (
            "treatment_dense_index_not_ready",
            lambda row: row["treatment_receipt"]["dense_index_receipt"].update(query_ready=False),
        ),
        (
            "treatment_graph_utilisation_mismatch",
            lambda row: row["treatment_receipt"]["graph_utilisation"].update(
                cochange_rows=99
            ),
        ),
    ]
    for expected_error, mutate in mutations:
        changed = deepcopy(product_row)
        if expected_error == "treatment_refused_then_delivered":
            changed["treatment_receipt"]["provider_delivery_receipts"][0]["event_sequence"] = 10
        mutate(changed)
        _write_json(product, changed)
        assert expected_error in verify_runtime_receipt(product)


def test_runtime_receipt_rejects_provider_count_disagreement(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    trajectory = tmp_path / "trajectory.json"
    _write_json(
        report,
        {
            "exit_code": 0,
            "terminal": "submitted_unverified",
            "research_valid": True,
            "gt": {"terminal_requests": 3, "usage": {}},
        },
    )
    _write_json(trajectory, {"messages": [], "info": {"model_stats": {"api_calls": 2}}})

    try:
        issue_runtime_receipts(
            report_path=report,
            trajectory_path=trajectory,
            state_dir=tmp_path / "state",
            product_receipt_path=tmp_path / "gt-run.json",
            adapter_receipt_path=tmp_path / "adapter.json",
            task_id="task-a",
            product_source_sha="f" * 40,
            treatment="groundtruth",
            requested_model="meta/muse-spark-1.2-contributor",
            scaffold_version="2.4.6",
            time_budget_seconds=3600,
        )
    except ValueError as exc:
        assert str(exc) == "provider_call_count_mismatch"
    else:
        raise AssertionError("provider-call disagreement was accepted")


def test_benchmark_arms_match_what_the_writers_produce():
    """The arm domain crosses four modules; the writers are the authority.

    Five hand-typed copies agreed, which is why no sweep flagged them - two of
    them raise, and project_task_environment runs per task inside the paid
    window, so a third arm would have raised during setup on every task with
    spend committed. Same defect class as the refusal allow-list, caught before
    it was wrong instead of after.

    Scope is stated rather than assumed: this asserts the literals in every
    `treatment=` conditional across the two supervisor entry points, not that
    no other producer exists.
    """
    import ast
    from pathlib import Path as _Path

    from gt_harness.product import BENCHMARK_ARMS

    root = _Path(__file__).resolve().parent.parent
    produced = set()
    sites = 0
    for name in ("scripts/miniswe_gt_run.py", "scripts/miniswe_supervisor.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "treatment":
                continue
            # Only the BRANCHES of a conditional, never its test. Walking the
            # whole node collected "off" from
            # `"bare" if args.gt_off or args.gt_mode == "off" else "groundtruth"`
            # - a string that is compared, not produced. An extraction wider
            # than the thing it claims to extract is the same error as a
            # domain restated instead of derived.
            def _values(expr):
                if isinstance(expr, ast.IfExp):
                    return _values(expr.body) | _values(expr.orelse)
                if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                    return {expr.value}
                return set()

            literals = _values(node.value)
            if not literals:
                continue
            sites += 1
            produced |= literals

    assert sites >= 3, f"expected the three treatment writers, found {sites}"
    assert produced == set(BENCHMARK_ARMS), (
        f"writers produce {sorted(produced)} but every validator keyed on "
        f"BENCHMARK_ARMS allows {sorted(BENCHMARK_ARMS)}"
    )


def test_the_same_prompt_content_twice_in_a_run_is_rejected():
    """GT is a context provider; not repeating itself is its central claim.

    The runtime enforces this run-scoped - _model_visible_delivery_identities
    is initialised once and never cleared - but acceptance only ever checked
    uniqueness within a single decision, and treatment_refused_then_delivered
    cannot cover the gap either: it inspects content that WAS refused, and a
    dedup miss produces no refusal row at all. So the strongest property in the
    path had no check that would notice it breaking.
    """
    import pytest

    from gt_harness.runtime_receipts import _validate_delivery_boundaries

    # Same identity, DIFFERENT decisions: legal per-boundary, illegal per run.
    repeated = [
        {"lane": "prompt", "delivery_identity": "a" * 64, "observed_iteration": 1},
        {"lane": "prompt", "delivery_identity": "a" * 64, "observed_iteration": 5},
    ]
    with pytest.raises(ValueError, match="prompt_delivery_repeated_in_run"):
        _validate_delivery_boundaries(repeated)


def test_sealed_evidence_may_recur_across_decisions():
    """The sealed lane is decision-scoped by design and must not be caught here.

    _decision_delivery_identities is cleared on every iteration change, so the
    same sealed bytes recurring across decisions is intended behaviour. A
    run-scoped check that caught it would fire on correct runs.
    """
    from gt_harness.runtime_receipts import _validate_delivery_boundaries

    _validate_delivery_boundaries([
        {"lane": "sealed", "delivery_identity": "b" * 64, "observed_iteration": 1},
        {"lane": "sealed", "delivery_identity": "b" * 64, "observed_iteration": 5},
    ])
