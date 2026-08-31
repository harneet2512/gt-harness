from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_engine.resolution_provenance import (
    CallCandidate,
    CallsiteRecord,
    DispatchState,
    LabeledResolutionCase,
    NormalizedSymbolKind,
    OracleOutcome,
    ProvenanceMechanism,
    ResolutionEvent,
    ResolutionTier,
    SymbolRecord,
    VerificationStatus,
    derive_resolution_tier,
    execute_labeled_resolution_cases,
    legacy_callsite_from_edge,
    normalize_symbol_kind,
    report_per_resolver_metrics,
    stable_callsite_id,
    stable_symbol_id,
    wilson_interval,
)


def test_resolution_tier_uses_structural_provenance_not_confidence_scores():
    exact = derive_resolution_tier(
        provenance=ProvenanceMechanism.IMPORT_EXACT,
        candidate_count=1,
        declared_scope="pkg.mod",
        receiver_type="",
        parser_complete=True,
        dynamic_dispatch=False,
    )
    ambiguous = derive_resolution_tier(
        provenance=ProvenanceMechanism.NAME_MATCH,
        candidate_count=2,
        declared_scope="pkg.mod",
        receiver_type="",
        parser_complete=True,
        dynamic_dispatch=False,
    )
    incomplete = derive_resolution_tier(
        provenance=ProvenanceMechanism.IMPORT_EXACT,
        candidate_count=1,
        declared_scope="pkg.mod",
        receiver_type="",
        parser_complete=False,
        dynamic_dispatch=False,
    )
    assert exact is ResolutionTier.EXACT
    assert ambiguous is ResolutionTier.AMBIGUOUS
    assert incomplete is ResolutionTier.INDETERMINATE


@pytest.mark.parametrize(
    ("provenance", "count", "complete", "dynamic", "expected"),
    [
        (ProvenanceMechanism.DYNAMIC, 1, True, False, ResolutionTier.DYNAMIC),
        (ProvenanceMechanism.EXTERNAL, 1, True, False, ResolutionTier.EXTERNAL),
        (ProvenanceMechanism.SAME_FILE, 0, True, False, ResolutionTier.UNRESOLVED),
        (ProvenanceMechanism.NAME_MATCH, 1, True, False, ResolutionTier.HEURISTIC),
        (ProvenanceMechanism.UNKNOWN_LEGACY, 1, True, False, ResolutionTier.UNKNOWN_LEGACY),
        (ProvenanceMechanism.IMPORT_EXACT, 1, None, False, ResolutionTier.INDETERMINATE),
        (ProvenanceMechanism.IMPORT_EXACT, 1, True, True, ResolutionTier.DYNAMIC),
    ],
)
def test_resolution_tier_has_closed_conservative_outcomes(
    provenance, count, complete, dynamic, expected
):
    assert (
        derive_resolution_tier(
            provenance=provenance,
            candidate_count=count,
            declared_scope="pkg.mod",
            receiver_type="Runner",
            parser_complete=complete,
            dynamic_dispatch=dynamic,
        )
        is expected
    )


def test_resolution_tier_rejects_negative_candidate_count():
    with pytest.raises(ValueError, match="candidate_count"):
        derive_resolution_tier(
            provenance=ProvenanceMechanism.SAME_FILE,
            candidate_count=-1,
            declared_scope="pkg.mod",
            receiver_type="",
            parser_complete=True,
            dynamic_dispatch=False,
        )


def _symbol(name: str, *, native_kind: str = "Function") -> SymbolRecord:
    return SymbolRecord.build(
        native_id=f"native:{name}",
        native_kind=native_kind,
        language="python",
        path="src/mod.py",
        qualified_name=f"mod.{name}",
        start_line=1,
        end_line=3,
        export_status="internal",
    )


def _candidate(target: SymbolRecord, ordinal: int, *, selected: bool = False):
    return CallCandidate(
        target_stable_id=target.stable_id,
        target_native_id=target.native_id,
        ordinal=ordinal,
        mechanism=ProvenanceMechanism.SAME_FILE,
        declared_scope="mod",
        receiver_type="",
        receiver_origin="",
        receiver_shape="",
        receiver_chain=(),
        import_chain=(),
        dynamic_dispatch=False,
        export_status=target.export_status,
        parser_complete=True,
        verification_status=VerificationStatus.UNVERIFIED,
        selected=selected,
    )


def test_native_and_normalized_kinds_round_trip_with_explicit_unknown():
    assert normalize_symbol_kind("Function") is NormalizedSymbolKind.FUNCTION
    assert normalize_symbol_kind("method") is NormalizedSymbolKind.METHOD
    assert normalize_symbol_kind("made-up-parser-label") is NormalizedSymbolKind.UNKNOWN

    symbol = _symbol("run", native_kind="VendorCallable")
    assert symbol.native_kind == "VendorCallable"
    assert symbol.normalized_kind is NormalizedSymbolKind.UNKNOWN
    assert SymbolRecord.from_row(symbol.to_row()) == symbol


def test_stable_ids_are_content_derived_not_transient_row_ids():
    first = stable_symbol_id(
        language="python",
        path="src/mod.py",
        qualified_name="mod.run",
        native_kind="Function",
        start_line=4,
        end_line=7,
    )
    second = stable_symbol_id(
        language="python",
        path="src/mod.py",
        qualified_name="mod.run",
        native_kind="Function",
        start_line=4,
        end_line=7,
    )
    assert first == second
    assert len(first) == 64
    assert first != stable_symbol_id(
        language="python",
        path="src/mod.py",
        qualified_name="mod.run",
        native_kind="Function",
        start_line=5,
        end_line=7,
    )

    assert stable_callsite_id(
        repository_revision="r1",
        source_stable_id=first,
        path="src/mod.py",
        start_line=10,
        end_line=10,
        callee="helper",
    ) != stable_callsite_id(
        repository_revision="r2",
        source_stable_id=first,
        path="src/mod.py",
        start_line=10,
        end_line=10,
        callee="helper",
    )


def test_unique_candidate_selection_must_be_a_retained_member():
    source = _symbol("caller")
    target = _symbol("helper")
    candidate = _candidate(target, 0, selected=True)
    callsite = CallsiteRecord.build(
        repository_revision="rev-1",
        source=source,
        path="src/mod.py",
        start_line=8,
        end_line=8,
        callee="helper",
        language="python",
        dispatch_state=DispatchState.UNIQUE,
        candidates=(candidate,),
        selected_target_stable_id=target.stable_id,
        selected_target_native_id=target.native_id,
        mechanism=ProvenanceMechanism.SAME_FILE,
        verification_status=VerificationStatus.VERIFIED,
    )
    assert callsite.candidate_count == 1
    assert callsite.selected_target_stable_id == target.stable_id

    with pytest.raises(ValueError, match="selected target must be a retained candidate"):
        CallsiteRecord.build(
            repository_revision="rev-1",
            source=source,
            path="src/mod.py",
            start_line=8,
            end_line=8,
            callee="other",
            language="python",
            dispatch_state=DispatchState.UNIQUE,
            candidates=(candidate,),
            selected_target_stable_id="f" * 64,
            mechanism=ProvenanceMechanism.SAME_FILE,
        )


def test_ambiguous_candidates_require_dense_ordinals_and_cannot_verify_selection():
    source = _symbol("caller")
    one = _symbol("one")
    two = _symbol("two")
    with pytest.raises(ValueError, match="dense zero-based"):
        CallsiteRecord.build(
            repository_revision="rev-1",
            source=source,
            path="src/mod.py",
            start_line=9,
            end_line=9,
            callee="pick",
            language="python",
            dispatch_state=DispatchState.AMBIGUOUS,
            candidates=(_candidate(one, 0), _candidate(two, 2)),
            mechanism=ProvenanceMechanism.NAME_MATCH,
        )

    with pytest.raises(ValueError, match="ambiguous callsite cannot certify"):
        CallsiteRecord.build(
            repository_revision="rev-1",
            source=source,
            path="src/mod.py",
            start_line=9,
            end_line=9,
            callee="pick",
            language="python",
            dispatch_state=DispatchState.AMBIGUOUS,
            candidates=(_candidate(one, 0, selected=True), _candidate(two, 1)),
            selected_target_stable_id=one.stable_id,
            mechanism=ProvenanceMechanism.NAME_MATCH,
            verification_status=VerificationStatus.VERIFIED,
        )


@pytest.mark.parametrize(
    "state",
    [
        DispatchState.ZERO,
        DispatchState.DYNAMIC,
        DispatchState.EXTERNAL_UNRESOLVED,
        DispatchState.PARSER_INCOMPLETE,
    ],
)
def test_unresolved_states_have_no_candidates_or_selection(state):
    source = _symbol("caller")
    row = CallsiteRecord.build(
        repository_revision="rev-1",
        source=source,
        path="src/mod.py",
        start_line=11,
        end_line=11,
        callee="unknown",
        language="python",
        dispatch_state=state,
        candidates=(),
        mechanism=ProvenanceMechanism.DYNAMIC,
    )
    assert row.candidate_count == 0
    assert row.selected_target_stable_id is None


def test_legacy_selected_edge_is_conservatively_reconciled_without_candidate():
    source = _symbol("caller")
    legacy = legacy_callsite_from_edge(
        repository_revision="rev-old",
        source=source,
        path="src/mod.py",
        source_line=15,
        callee="guessed",
        selected_native_target_id="node:123",
        reported_candidate_count=4,
    )
    assert legacy.dispatch_state is DispatchState.UNKNOWN_LEGACY
    assert legacy.candidate_count == 0
    assert legacy.selected_target_stable_id is None
    assert legacy.legacy_reported_candidate_count == 4
    assert legacy.mechanism is ProvenanceMechanism.UNKNOWN_LEGACY
    assert legacy.verification_status is VerificationStatus.UNKNOWN


def test_producer_two_candidate_vta_rows_decode_without_invention():
    # Provisional observed rows from Groundtruth 944d37ec9332eedaca06845462c36a5149587afb
    # indexing closeout/fixture/main.go (HAR-61 closeout SHA a3156504).
    rows = [
        {
            "callsite_id": "01d5978d232e0b32b44d98766adce2feee8d5902b8a8161897e296f1335a3a3b",
            "target_id": 4,
            "target_stable_id": "88e6b52f1afe0d0bd3f18655c6c3d87eee6fc234bb292c5fe08bcb6ff7aaa24b",
            "target_native_id": "4",
            "ordinal": 0,
            "mechanism": "vta",
            "declared_scope": "ImplA.Run",
            "receiver_type": "ImplA,ImplB",
            "receiver_origin": "vta_flow_stable_ids=c4338eb99feb5cf08d4abd27b87a37013b40f3baafed430b79cea0b68f33368c,db099b132726a038e49254ac1b715beb762a7d30426e0fa79fb5f4df24c81f6f",
            "receiver_shape": "runner.Run",
            "receiver_chain": '["runner"]',
            "import_chain": "[]",
            "dynamic_dispatch": 0,
            "export_status": "exported",
            "parser_complete": 1,
            "verification_status": "candidate_only",
            "selected": 0,
        },
        {
            "callsite_id": "01d5978d232e0b32b44d98766adce2feee8d5902b8a8161897e296f1335a3a3b",
            "target_id": 6,
            "target_stable_id": "853d5a7acc47858cba1ce7c11c74ef5ab8fa711d2b35cf72bd32b86b55e98d49",
            "target_native_id": "6",
            "ordinal": 1,
            "mechanism": "vta",
            "declared_scope": "ImplB.Run",
            "receiver_type": "ImplA,ImplB",
            "receiver_origin": "vta_flow_stable_ids=c4338eb99feb5cf08d4abd27b87a37013b40f3baafed430b79cea0b68f33368c,db099b132726a038e49254ac1b715beb762a7d30426e0fa79fb5f4df24c81f6f",
            "receiver_shape": "runner.Run",
            "receiver_chain": '["runner"]',
            "import_chain": "[]",
            "dynamic_dispatch": 0,
            "export_status": "exported",
            "parser_complete": 1,
            "verification_status": "candidate_only",
            "selected": 0,
        },
    ]
    decoded = [CallCandidate.from_row(row) for row in rows]
    assert len(decoded) == 2
    assert [item.ordinal for item in decoded] == [0, 1]
    assert {item.target_stable_id for item in decoded} == {
        "88e6b52f1afe0d0bd3f18655c6c3d87eee6fc234bb292c5fe08bcb6ff7aaa24b",
        "853d5a7acc47858cba1ce7c11c74ef5ab8fa711d2b35cf72bd32b86b55e98d49",
    }
    assert {item.declared_scope for item in decoded} == {"ImplA.Run", "ImplB.Run"}
    assert all(item.mechanism is ProvenanceMechanism.VTA for item in decoded)
    assert all(item.verification_status is VerificationStatus.CANDIDATE_ONLY for item in decoded)
    assert not any(item.selected for item in decoded)


_FROZEN_RESOLUTION_EVENTS = (
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c1"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c2"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c3"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c4"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c5"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.AGREED, "c6"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.DISAGREED, "c7"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.DISAGREED, "c8"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.INDETERMINATE, "c9"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.IMPORT_EXACT, OracleOutcome.INDETERMINATE, "c10"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.NAME_MATCH, OracleOutcome.AGREED, "n1"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.NAME_MATCH, OracleOutcome.DISAGREED, "n2"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.NAME_MATCH, OracleOutcome.DISAGREED, "n3"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.NAME_MATCH, OracleOutcome.INDETERMINATE, "n4"),
    ResolutionEvent("rev-frozen", ProvenanceMechanism.NAME_MATCH, OracleOutcome.INDETERMINATE, "n5"),
)


def test_per_resolver_reporting_matches_frozen_event_fixture():
    reports = report_per_resolver_metrics(_FROZEN_RESOLUTION_EVENTS, repository_revision="rev-frozen")
    by_mechanism = {row.mechanism: row for row in reports}

    import_exact = by_mechanism[ProvenanceMechanism.IMPORT_EXACT]
    assert import_exact.population == 10
    assert import_exact.labeled == 8
    assert import_exact.agreed == 6
    assert import_exact.disagreed == 2
    assert import_exact.indeterminate == 2
    assert import_exact.precision == 0.75
    assert import_exact.coverage == 0.8
    assert import_exact.precision_ci_low == pytest.approx(0.40926987910258916)
    assert import_exact.precision_ci_high == pytest.approx(0.9285223111419724)

    name_match = by_mechanism[ProvenanceMechanism.NAME_MATCH]
    assert name_match.population == 5
    assert name_match.labeled == 3
    assert name_match.agreed == 1
    assert name_match.disagreed == 2
    assert name_match.indeterminate == 2
    assert name_match.precision == pytest.approx(1 / 3)
    assert name_match.coverage == 0.6
    assert name_match.precision_ci_low == pytest.approx(0.0614903152761605)
    assert name_match.precision_ci_high == pytest.approx(0.7923450448735121)


def test_per_resolver_reporting_rejects_revision_mismatch():
    with pytest.raises(ValueError, match="repository_revision mismatch"):
        report_per_resolver_metrics(_FROZEN_RESOLUTION_EVENTS, repository_revision="rev-other")


def test_per_resolver_reporting_marks_zero_labeled_precision_indeterminate():
    events = (
        ResolutionEvent("rev-frozen", ProvenanceMechanism.DYNAMIC, OracleOutcome.INDETERMINATE, "d1"),
        ResolutionEvent("rev-frozen", ProvenanceMechanism.DYNAMIC, OracleOutcome.INDETERMINATE, "d2"),
    )
    reports = report_per_resolver_metrics(events, repository_revision="rev-frozen")
    dynamic = reports[0]
    assert dynamic.mechanism is ProvenanceMechanism.DYNAMIC
    assert dynamic.population == 2
    assert dynamic.labeled == 0
    assert dynamic.indeterminate == 2
    assert dynamic.precision is None
    assert dynamic.precision_ci_low is None
    assert dynamic.precision_ci_high is None
    assert dynamic.coverage == 0.0


def _load_labeled_cases(path: Path) -> tuple[LabeledResolutionCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        LabeledResolutionCase(
            case_id=str(row["case_id"]),
            mechanism=ProvenanceMechanism(str(row["mechanism"])),
            candidate_count=int(row["candidate_count"]),
            declared_scope=str(row["declared_scope"]),
            receiver_type=str(row["receiver_type"]),
            parser_complete=row["parser_complete"],
            dynamic_dispatch=bool(row["dynamic_dispatch"]),
            oracle_outcome=OracleOutcome(str(row["oracle_outcome"])),
            expected_tier=ResolutionTier(str(row["expected_tier"])),
        )
        for row in payload["cases"]
    )


def test_frozen_labeled_case_execution_is_deterministic_and_matches_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "har10_frozen_labeled_cases.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = _load_labeled_cases(fixture_path)
    revision = str(fixture["repository_revision"])

    before = execute_labeled_resolution_cases(cases, repository_revision=revision)
    after = execute_labeled_resolution_cases(cases, repository_revision=revision)

    assert before["payload_sha256"] == after["payload_sha256"]
    assert before["payload_sha256"] == fixture["payload_sha256"]
    assert len(before["events"]) == len(cases)
    assert before["reports"]


def test_groundtruth_deferral_record_is_present_and_complete():
    fixture_path = Path(__file__).parent / "fixtures" / "har10_groundtruth_deferral.json"
    record = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert record["schema"] == "gt.har10.groundtruth_deferral.v1"
    assert "gt-index/internal/resolver/vta.go" in record["deferred_paths"]
    assert "gt-index/internal/store/resolution_v2.go" in record["deferred_paths"]
    assert record["reason"]
    assert record["safe_because"]
    assert record["resume_when"]
