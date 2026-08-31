from __future__ import annotations

import pytest

from gt_engine.resolution_provenance import (
    CallCandidate,
    CallsiteRecord,
    DispatchState,
    NormalizedSymbolKind,
    ProvenanceMechanism,
    SymbolRecord,
    VerificationStatus,
    legacy_callsite_from_edge,
    normalize_symbol_kind,
    stable_callsite_id,
    stable_symbol_id,
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
