from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gt_engine import derived_store
from gt_engine.derived_store import (
    DerivedStoreBinding,
    StoreIncompatible,
    build_derived_store,
    load_derived_store,
)
from gt_engine.resolution_provenance import (
    CallCandidate,
    CallsiteRecord,
    DispatchState,
    ProvenanceMechanism,
    SymbolRecord,
    VerificationStatus,
)


def _records():
    source = SymbolRecord.build(
        native_id="n:caller",
        native_kind="Function",
        language="python",
        path="src/a.py",
        qualified_name="a.caller",
        start_line=1,
        end_line=3,
        export_status="internal",
    )
    target = SymbolRecord.build(
        native_id="n:target",
        native_kind="Function",
        language="python",
        path="src/b.py",
        qualified_name="b.target",
        start_line=4,
        end_line=6,
        export_status="exported",
    )
    candidate = CallCandidate(
        target_stable_id=target.stable_id,
        target_native_id=target.native_id,
        ordinal=0,
        mechanism=ProvenanceMechanism.IMPORT_EXACT,
        declared_scope="b",
        receiver_type="",
        receiver_origin="import",
        receiver_shape="function",
        receiver_chain=(),
        import_chain=("b.target",),
        dynamic_dispatch=False,
        export_status="exported",
        parser_complete=True,
        verification_status=VerificationStatus.VERIFIED,
        selected=True,
    )
    callsite = CallsiteRecord.build(
        repository_revision="repo-1",
        source=source,
        path="src/a.py",
        start_line=2,
        end_line=2,
        callee="target",
        language="python",
        dispatch_state=DispatchState.UNIQUE,
        candidates=(candidate,),
        selected_target_stable_id=target.stable_id,
        selected_target_native_id=target.native_id,
        mechanism=ProvenanceMechanism.IMPORT_EXACT,
        verification_status=VerificationStatus.VERIFIED,
    )
    return (source, target), (callsite,)


def _binding() -> DerivedStoreBinding:
    return DerivedStoreBinding(
        source_revision="source-1",
        repository_revision="repo-1",
        graph_revision="graph-1",
        graph_sha256="a" * 64,
        producer_contract="gt-index.callsites.v1",
    )


def test_build_and_load_round_trip_with_real_sqlite(tmp_path):
    symbols, callsites = _records()
    path = tmp_path / "derived.db"
    receipt = build_derived_store(path, _binding(), symbols, callsites)

    assert receipt.published is True
    assert receipt.symbol_count == 2
    assert receipt.callsite_count == 1
    assert receipt.candidate_count == 1
    loaded = load_derived_store(path, expected=_binding())
    assert loaded.binding == _binding()
    assert loaded.symbols == symbols
    assert loaded.callsites == callsites

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT value FROM derived_metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == "1"
        )


def test_revision_or_producer_mismatch_fails_closed(tmp_path):
    symbols, callsites = _records()
    path = tmp_path / "derived.db"
    build_derived_store(path, _binding(), symbols, callsites)

    mismatch = DerivedStoreBinding(
        source_revision="source-1",
        repository_revision="repo-2",
        graph_revision="graph-1",
        graph_sha256="a" * 64,
        producer_contract="gt-index.callsites.v1",
    )
    with pytest.raises(StoreIncompatible, match="repository_revision"):
        load_derived_store(path, expected=mismatch)


def test_additive_migration_preserves_existing_metadata(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE derived_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO derived_metadata VALUES('legacy_witness','keep-me');"
        )

    derived_store.migrate_derived_store(path)

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"derived_metadata", "symbols", "callsites", "call_candidates"} <= tables
        assert (
            connection.execute(
                "SELECT value FROM derived_metadata WHERE key='legacy_witness'"
            ).fetchone()[0]
            == "keep-me"
        )
        assert (
            connection.execute(
                "SELECT value FROM derived_metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == "1"
        )


def test_failed_candidate_validation_preserves_prior_complete_store(tmp_path):
    symbols, callsites = _records()
    path = tmp_path / "derived.db"
    build_derived_store(path, _binding(), symbols, callsites)
    before = path.read_bytes()

    bad = CallsiteRecord.from_row(
        {
            **callsites[0].to_row(),
            "candidate_count": 2,
        },
        candidates=callsites[0].candidates,
        validate=False,
    )
    with pytest.raises(ValueError, match="candidate_count"):
        build_derived_store(path, _binding(), symbols, (bad,))

    assert path.read_bytes() == before
    assert load_derived_store(path, expected=_binding()).callsites == callsites


def test_killed_or_faulted_temporary_build_never_replaces_prior_store(tmp_path, monkeypatch):
    symbols, callsites = _records()
    path = tmp_path / "derived.db"
    build_derived_store(path, _binding(), symbols, callsites)
    before = path.read_bytes()

    def fault(_path: Path) -> None:
        raise RuntimeError("injected validation fault")

    monkeypatch.setattr(derived_store, "_validate_database", fault)
    with pytest.raises(RuntimeError, match="injected validation fault"):
        build_derived_store(path, _binding(), symbols, callsites)

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".derived.db.*")) == []


def test_selected_target_membership_is_rechecked_on_database_read(tmp_path):
    symbols, callsites = _records()
    path = tmp_path / "derived.db"
    build_derived_store(path, _binding(), symbols, callsites)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE callsites SET selected_target_stable_id=?",
            ("f" * 64,),
        )

    with pytest.raises(StoreIncompatible, match="selected target"):
        load_derived_store(path, expected=_binding())
