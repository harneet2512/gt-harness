from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.decision_value import (
    ClaimRole,
    DecisionBoundary,
    DecisionClaim,
    DecisionDeliveryCompiler,
    FeatureLifecycle,
    FeatureStage,
    FeatureTriggerContext,
    SourceEvidence,
    evaluate_feature_triggers,
)
from gt_engine.decision_value_gate import evaluate_decision_value_gates
from gt_engine.graph_lease import (
    GraphBuildResult,
    GraphFreshness,
    GraphLease,
)
from gt_engine.run_receipt_v2 import RunReceiptFinalizer, load_run_receipts
from gt_engine.uptake_audit import audit_delivery_uptake


def _evidence(path: str = "src/service.py") -> SourceEvidence:
    return SourceEvidence(
        path=path,
        start_line=10,
        end_line=18,
        content_sha256="a" * 64,
        excerpt="def resolve_user(identity):",
    )


def _claim(**overrides) -> DecisionClaim:
    values = {
        "claim_id": "owner-1",
        "text": "Edit resolve_user in src/service.py.",
        "role": ClaimRole.EDIT_OWNER,
        "requirement_id": "req-identity",
        "repository_revision": "repo-r1",
        "graph_revision": "graph-r1",
        "source_evidence": (_evidence(),),
        "symbol_identity": "python:src/service.py:resolve_user",
        "relationship": "DEFINES",
        "action": "inspect and edit src/service.py:resolve_user",
        "semantic_similarity": 0.9,
        "exact_identifier_match": True,
        "graph_distance": 0,
        "authoritative_edge": True,
        "evidence_quality": 1.0,
    }
    values.update(overrides)
    return DecisionClaim(**values)


def test_feature_lifecycle_requires_certification_before_delivery() -> None:
    lifecycle = FeatureLifecycle.candidate(
        "implementation_owner",
        triggering_event="repository_start:task-1",
        repository_revision="repo-r1",
        graph_revision="graph-r1",
    )

    with pytest.raises(ValueError, match="CERTIFIED"):
        lifecycle.deliver(
            boundary=DecisionBoundary.PRE_EDIT,
            model_visible_bytes=b"owner",
        )

    lifecycle.certify(
        claims=(_claim(),),
        decision_boundary=DecisionBoundary.PRE_EDIT,
    )
    lifecycle.deliver(
        boundary=DecisionBoundary.PRE_EDIT,
        model_visible_bytes=b"[edit owners]\nowner",
    )
    lifecycle.consume(resulting_agent_action="sed -i ... src/service.py")
    lifecycle.validate(
        validation="pytest tests/test_service.py -q: pass",
        contradicted=False,
    )

    receipt = lifecycle.receipt()
    assert [row["to"] for row in receipt["transitions"]] == [
        FeatureStage.CANDIDATE,
        FeatureStage.CERTIFIED,
        FeatureStage.DELIVERED,
        FeatureStage.CONSUMED,
        FeatureStage.VALIDATED,
    ]
    assert receipt["triggering_event"] == "repository_start:task-1"
    assert receipt["model_visible_bytes_hex"] == b"[edit owners]\nowner".hex()
    assert receipt["resulting_agent_action"].startswith("sed")


def test_feature_lifecycle_uses_not_applicable_and_abstained_explicitly() -> None:
    irrelevant = FeatureLifecycle.not_applicable(
        "new_file_proposal",
        triggering_event="task_contract:no_new_file",
        repository_revision="repo-r1",
        graph_revision="graph-r1",
        reason="task has no new-file requirement",
    )
    assert irrelevant.stage is FeatureStage.NOT_APPLICABLE

    uncertain = FeatureLifecycle.candidate(
        "ambiguous_identity",
        triggering_event="search:duplicate symbol",
        repository_revision="repo-r1",
        graph_revision="graph-r1",
    )
    uncertain.abstain("source evidence cannot distinguish the identities")
    assert uncertain.stage is FeatureStage.ABSTAINED
    assert uncertain.receipt()["terminal_reason"].startswith("source evidence")


@pytest.mark.parametrize("source", ("owner.py", "owner.ts", "owner.go", "owner.rs"))
def test_all_feature_triggers_are_language_neutral_and_independently_applicable(source) -> None:
    values = {
        feature: (source,)
        for feature in (
            "implementation_owners", "ambiguous_identities", "inspection_files",
            "public_surface", "impact", "affected_tests", "processes",
            "supporting_files", "new_file_proposals", "failure_analysis",
            "verification",
        )
    }
    states = evaluate_feature_triggers(
        FeatureTriggerContext(
            event_id="repository-start",
            repository_revision="repo-r1",
            graph_revision="graph-r1",
            **values,
        )
    )
    assert all(item.stage is FeatureStage.CANDIDATE for item in states.values())


def test_irrelevant_feature_triggers_are_not_applicable_not_not_triggered() -> None:
    states = evaluate_feature_triggers(
        FeatureTriggerContext(
            event_id="repository-start",
            repository_revision="repo-r1",
            graph_revision="graph-r1",
        )
    )
    assert all(item.stage is FeatureStage.NOT_APPLICABLE for item in states.values())


def test_delivery_compiler_is_boundary_revision_novelty_and_authority_gated() -> None:
    compiler = DecisionDeliveryCompiler()
    stale = _claim(claim_id="stale", repository_revision="repo-old")
    weak_owner = _claim(
        claim_id="weak",
        symbol_identity="",
        relationship="",
        text="Maybe edit src/guess.py.",
    )
    valid = _claim()

    first = compiler.compile(
        boundary=DecisionBoundary.PRE_EDIT,
        repository_revision="repo-r1",
        graph_revision="graph-r1",
        unmet_requirement_ids=("req-identity",),
        claims=(stale, weak_owner, valid),
    )
    repeated = compiler.compile(
        boundary=DecisionBoundary.PRE_EDIT,
        repository_revision="repo-r1",
        graph_revision="graph-r1",
        unmet_requirement_ids=("req-identity",),
        claims=(valid,),
    )

    assert first is not None
    assert [claim.claim_id for claim in first.claims] == ["owner-1"]
    assert first.model_visible_bytes.startswith(b"[edit owners]")
    assert repeated is None


def test_ambiguity_is_delivered_only_with_competitors_and_disambiguation() -> None:
    compiler = DecisionDeliveryCompiler()
    generic = _claim(
        claim_id="generic-warning",
        role=ClaimRole.UNRESOLVED_IDENTITY,
        text="The target may be ambiguous.",
        symbol_identity="",
        relationship="",
        action="inspect more",
    )
    concrete = _claim(
        claim_id="identity-warning",
        role=ClaimRole.UNRESOLVED_IDENTITY,
        text="resolve_user has two repository identities.",
        symbol_identity="",
        relationship="",
        competing_identities=(
            "python:src/service.py:resolve_user",
            "python:src/legacy.py:resolve_user",
        ),
        disambiguation_action="inspect both definitions and their import callers",
    )

    delivery = compiler.compile(
        boundary=DecisionBoundary.IDENTITY_AMBIGUITY,
        repository_revision="repo-r1",
        graph_revision="graph-r1",
        unmet_requirement_ids=("req-identity",),
        claims=(generic, concrete),
    )

    assert delivery is not None
    assert [claim.claim_id for claim in delivery.claims] == ["identity-warning"]
    assert b"src/legacy.py" in delivery.model_visible_bytes


def test_graph_lease_suppresses_stale_claims_and_coalesces_one_refresh() -> None:
    lease = GraphLease.current(
        graph_repository_revision="repo-r1",
        workspace_revision="work-r1",
        graph_revision="graph-r1",
        graph_path="graph.db",
    )
    lease.mark_edit(
        workspace_revision="work-r2",
        dirty_paths=("src/a.py", "src/b.py"),
        operations=("modify", "modify"),
        supported_file_count=20,
    )
    lease.mark_edit(
        workspace_revision="work-r2",
        dirty_paths=("src/c.py",),
        operations=("modify",),
        supported_file_count=20,
    )
    assert lease.freshness is GraphFreshness.STALE
    assert lease.claims_current("repo-r1", "graph-r1") is False

    calls = []

    def refresh(request):
        calls.append(request)
        return GraphBuildResult(
            success=True,
            graph_repository_revision="repo-r2",
            graph_revision="graph-r2",
            graph_path="graph.db",
            duration_ms=12.5,
            health_valid=True,
            mode=request.mode,
        )

    first = lease.refresh_for_boundary(
        DecisionBoundary.POST_EDIT_GRAPH_DELTA,
        repository_revision="repo-r2",
        refresh=refresh,
    )
    second = lease.refresh_for_boundary(
        DecisionBoundary.VERIFICATION_SELECTION,
        repository_revision="repo-r2",
        refresh=refresh,
    )

    assert first is not None and first.success is True
    assert second is None
    assert len(calls) == 1
    assert calls[0].dirty_paths == ("src/a.py", "src/b.py", "src/c.py")
    assert lease.freshness is GraphFreshness.CURRENT


def test_graph_lease_falls_back_to_full_for_large_or_unsafe_closure() -> None:
    lease = GraphLease.current(
        graph_repository_revision="repo-r1",
        workspace_revision="work-r1",
        graph_revision="graph-r1",
        graph_path="graph.db",
    )
    lease.mark_edit(
        workspace_revision="work-r2",
        dirty_paths=("src/new.py",),
        operations=("create",),
        supported_file_count=100,
        dependency_closure_size=1,
    )
    requests = []
    lease.refresh_for_boundary(
        DecisionBoundary.PRE_EDIT,
        repository_revision="repo-r2",
        refresh=lambda request: requests.append(request)
        or GraphBuildResult(
            success=False,
            graph_repository_revision="repo-r1",
            graph_revision="graph-r1",
            graph_path="graph.db",
            duration_ms=1,
            health_valid=False,
            mode=request.mode,
            error="adapter cannot safely add files incrementally",
        ),
    )
    assert requests[0].mode.value == "full"


def test_v2_finalizer_always_writes_terminal_receipt_and_loader_rejects_missing(tmp_path) -> None:
    output = tmp_path / "gt-run-receipt.json"
    finalizer = RunReceiptFinalizer(
        output,
        task_id="task-1",
        requested_model="model-x",
        started_at="2026-08-28T00:00:00Z",
    )
    finalizer.record_provider_usage(calls=2, input_tokens=50, output_tokens=10)
    finalizer.record_graph_build(
        kind="initial", repository_revision="repo-r1", graph_revision="graph-r1",
        duration_ms=4.0, success=True,
    )
    finalizer.finalize(
        terminal="provider_failed",
        infrastructure_classification="PROVIDER_ERROR",
        exception=RuntimeError("route unavailable"),
        trajectory={"messages": []},
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "gt.run_receipt.v2"
    assert payload["terminal"] == "provider_failed"
    assert payload["provider_usage"]["calls"] == 2
    assert payload["artifact_size_bytes"] == output.stat().st_size

    loaded = load_run_receipts((output, tmp_path / "missing.json"))
    assert loaded.valid is False
    assert loaded.missing_paths == (str(tmp_path / "missing.json"),)
    assert loaded.aggregate is None


def test_uptake_auditor_joins_real_trajectory_journal_and_delivery_receipt(tmp_path) -> None:
    trajectory = tmp_path / "gt-run.trajectory.json"
    journal = tmp_path / "events.jsonl"
    receipt = tmp_path / "gt-run-receipt.json"
    delivered = "[edit owners]\n- Edit src/service.py:10.\n"
    trajectory.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": "inspect"},
                    {
                        "role": "assistant",
                        "content": "using the owner",
                        "extra": {"actions": [{"command": "sed -n 1,80p src/service.py"}]},
                    },
                    {"role": "tool", "content": "1 passed"},
                ]
            }
        ),
        encoding="utf-8",
    )
    journal.write_text(
        json.dumps(
            {
                "event": "provider_delivery",
                "request_id": "delivery-1",
                "iteration": 1,
                "suffix": delivered,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt.write_text(
        json.dumps(
            {
                "schema": "gt.run_receipt.v2",
                "deliveries": [
                    {
                        "delivery_id": "delivery-1",
                        "iteration": 1,
                        "model_visible_bytes_hex": delivered.encode().hex(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    audit = audit_delivery_uptake(
        trajectory_path=trajectory,
        event_journal_path=journal,
        run_receipt_path=receipt,
    )

    assert audit.valid is True
    assert audit.consumed == 1
    assert audit.validated == 1
    assert audit.contradicted == 0


def test_decision_value_gate_enforces_exact_acceptance_thresholds() -> None:
    receipt = {
        "schema": "gt.run_receipt.v2",
        "task_id": "task-1",
        "requested_model": "model-x",
        "started_at": "2026-08-28T00:00:00+00:00",
        "finished_at": "2026-08-28T00:00:01+00:00",
        "terminal": "submitted",
        "infrastructure_classification": "NONE",
        "duration_ms": 1000.0,
        "iteration_count": 1,
        "provider_usage": {
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "duration_ms": 20.0,
        },
        "graph_build_count": 1,
        "graph_refresh_count": 0,
        "graph_duration_ms": 4.0,
        "artifact_size_bytes": 100,
        "graph_builds": [
            {
                "workspace_revision": "work-r1",
                "repository_revision": "repo-r1",
                "graph_revision": "graph-r1",
                "mode": "full",
                "success": True,
            }
        ],
        "deliveries": [
            {
                "repository_revision": "repo-r1",
                "graph_revision": "graph-r1",
                "model_visible_bytes_hex": "",
                "model_visible_bytes_sha256": hashlib.sha256(b"").hexdigest(),
            }
        ],
        "feature_lifecycle_transitions": [
            {"feature_id": "owner", "stage": "VALIDATED"}
        ],
    }
    passing = evaluate_decision_value_gates(
        expected_run_count=1,
        run_receipts=(receipt,),
        certified_fact_checks=({"source_supported": True},) * 50,
        implementation_owner_cases=(
            {"expected": "owner", "ranked": ["owner", "other"]},
        )
        * 10,
    )
    stale = evaluate_decision_value_gates(
        expected_run_count=1,
        run_receipts=({
            **receipt,
            "deliveries": [
                {
                    "repository_revision": "repo-r2",
                    "graph_revision": "graph-old",
                    "model_visible_bytes_hex": "",
                    "model_visible_bytes_sha256": hashlib.sha256(b"").hexdigest(),
                }
            ],
        },),
        certified_fact_checks=({"source_supported": True},) * 49
        + ({"source_supported": False},),
        implementation_owner_cases=(
            {"expected": "owner", "ranked": ["other"]},
        ),
    )

    assert passing.passed is True
    assert stale.passed is False
    assert "stale_delivery_detected" in stale.failures
    assert "implementation_owner_top3_recall_below_90_percent" in stale.failures


def test_decision_value_gate_rejects_schema_only_receipt() -> None:
    report = evaluate_decision_value_gates(
        expected_run_count=1,
        run_receipts=({"schema": "gt.run_receipt.v2"},),
        certified_fact_checks=({"source_supported": True},),
        implementation_owner_cases=({"expected": "owner", "ranked": ["owner"]},),
    )

    assert report.passed is False
    assert report.receipt_completeness == 0.0
    assert "receipt_completeness_below_100_percent" in report.failures
