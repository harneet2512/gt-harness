"""D: capability facades delegate to existing implementations honestly —
typed kinds through the certified dispatcher, data lookups over producer
tables, and recorded-state reads over the journal/CAS.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from gt_engine.gt_session import GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import EditTransaction, ExecutionEvidence, FileChange
from gt_engine.capabilities import (
    analysis,
    change,
    freshness,
    localization,
    runtime,
    structure,
)


def _session(tmp_path, *, repo_root="", graph_db=None):
    adapter = MiniSweAdapter(
        task_id="capfac",
        state_dir=tmp_path,
        predicates=[],
        repo_root=repo_root or tmp_path,
        graph_db=graph_db,
    )
    return GTSession(GTSessionConfig(task_id="capfac"), engine=adapter), adapter


def test_lexical_search_runs_the_certified_literal_kind(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def render_widget():\n    pass\n")
    session, _adapter = _session(tmp_path, repo_root=tmp_path)

    result = localization.lexical_search(session, "render_widget")
    answer = result.get("direct_answer")
    assert isinstance(answer, dict), result
    assert any("app.py" in row.get("path", "") for row in answer["matches"])


def test_typed_facade_reports_incomplete_when_repo_root_missing(tmp_path):
    adapter = MiniSweAdapter(task_id="capfac", state_dir=tmp_path, predicates=[])
    adapter.repo_root = ""
    session = GTSession(GTSessionConfig(task_id="capfac"), engine=adapter)

    result = localization.definition(session, "anything")
    assert result["direct_answer"] is None
    assert "capability_repo_root_missing" in (
        result["decision"]["reason_codes"]
    )


def _fixture_graph(path: Path) -> Path:
    db = path / "graph.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE communities (
            id TEXT PRIMARY KEY, label TEXT, cohesion REAL, member_count INT
        );
        CREATE TABLE community_members (
            community_id TEXT, member TEXT, member_kind TEXT
        );
        CREATE TABLE resolution_callsites (
            callsite_id TEXT PRIMARY KEY, callsite_ordinal INT,
            repository_revision TEXT, source_stable_id TEXT,
            source_native_id TEXT, source_id INT
        );
        CREATE TABLE resolution_candidates (
            callsite_id TEXT, target_id INT, target_stable_id TEXT,
            target_native_id TEXT, ordinal INT, mechanism TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO communities VALUES ('c1', 'billing flow', 0.9, 2)"
    )
    con.executemany(
        "INSERT INTO community_members VALUES (?, ?, ?)",
        [("c1", "src/pay.py", "file"), ("c1", "src/bill.py", "file")],
    )
    con.execute(
        "INSERT INTO resolution_callsites VALUES "
        "('site-1', 0, 'rev', 'stable:handler', 'handler', 7)"
    )
    con.executemany(
        "INSERT INTO resolution_candidates VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("site-1", 11, "stable:a", "impl_a", 0, "type_flow"),
            ("site-1", 12, "stable:b", "impl_b", 1, "name_match"),
        ],
    )
    con.commit()
    con.close()
    return db


def test_communities_reads_producer_tables(tmp_path):
    db = _fixture_graph(tmp_path)
    session, _a = _session(tmp_path, graph_db=str(db))

    result = structure.communities(session, "src/pay.py")
    assert result["omissions"] == []
    assert result["communities"][0]["label"] == "billing flow"

    empty = structure.communities(session, "src/none.py")
    assert empty["communities"] == []
    assert "no_community_membership" in empty["omissions"]


def test_callable_values_reads_retained_candidates(tmp_path):
    db = _fixture_graph(tmp_path)
    session, _a = _session(tmp_path, graph_db=str(db))

    result = analysis.callable_values(session, "handler")
    assert result["omissions"] == []
    (site,) = result["callsites"]
    assert [c["target_native_id"] for c in site["candidates"]] == [
        "impl_a",
        "impl_b",
    ]

    empty = analysis.callable_values(session, "nobody")
    assert "no_retained_candidates" in empty["omissions"]


def test_graph_backed_facades_abstain_without_a_graph(tmp_path):
    session, _a = _session(tmp_path)

    assert structure.communities(session, "x.py")["omissions"] == [
        "graph_unavailable"
    ]
    assert analysis.callable_values(session, "f")["omissions"] == [
        "graph_unavailable"
    ]
    assert change.affected_tests(session, ["x.py"])["omissions"] == [
        "graph_unavailable"
    ]
    assert analysis.cfg(session, "f")["omissions"] == ["symbol_not_found"]


def test_edit_transaction_reads_journal_and_cas(tmp_path):
    session, adapter = _session(tmp_path)
    assert change.edit_transaction(session) is None

    txn = EditTransaction(
        action_id=3,
        command_sha256=hashlib.sha256(b"edit").hexdigest(),
        pre_revision="rev-a",
        post_revision="rev-b",
        changes=(
            FileChange(
                path="src/x.py",
                operation="modify",
                before_sha256="b" * 64,
                after_sha256="c" * 64,
                before=b"old",
                after=b"new",
            ),
        ),
        complete=True,
        omissions=(),
        transaction_sha256="t" * 64,
    )
    adapter.record_edit_transaction(txn)

    result = change.edit_transaction(session)
    assert result["post_revision"] == "rev-b"
    assert result["changed_paths"] == ["src/x.py"]
    assert result["complete"] is True
    assert result["blob_missing"] is False
    assert result["transaction"]["kind"] == "edit_transaction"


def test_last_test_result_and_verification_state(tmp_path):
    session, adapter = _session(tmp_path)
    assert runtime.last_test_result(session) is None
    assert runtime.verification_state(session)["state"] == "none"

    artifact = ExecutionEvidence(
        action_id=2,
        kind="test",
        protocol="shell",
        outcome="fail",
        command_sha256=hashlib.sha256(b"pytest").hexdigest(),
        returncode=1,
        repository_revision="rev-a",
        raw_output=b"FAILED tests/test_x.py::test_a\n",
        observed_test_outcome="fail",
    )
    adapter.record_execution_evidence(artifact, command="pytest")

    result = runtime.last_test_result(session)
    assert result["outcome"] == "fail"
    assert result["observed_test_outcome"] == "fail"
    assert result["blob_missing"] is False

    state = runtime.verification_state(session)
    assert state["state"] == "recorded"
    assert state["observed_test_outcome"] == "fail"


def test_failure_fingerprint_and_repeated_failure_state(tmp_path):
    session, adapter = _session(tmp_path)

    assert runtime.failure_fingerprint(session)["omissions"] == [
        "no_execution_recorded"
    ]
    explicit = runtime.failure_fingerprint(
        session, "FAILED tests/test_a.py::test_b - assert 1 == 2"
    )
    assert explicit["fingerprint"]
    assert explicit["basis"] == "explicit_observation"

    adapter._edit_epoch = 1
    adapter.note_failure_fingerprint("fp-1", epoch=1)
    adapter.note_failure_fingerprint("fp-1", epoch=2)
    state = runtime.repeated_failure_state(session)
    assert state["fingerprints"]["fp-1"]["recurrences"] == 2
    assert state["pending_recovery"] == {"fingerprint": "fp-1", "epoch": 2}


def test_freshness_facade_reports_revisions(tmp_path):
    session, adapter = _session(tmp_path)
    adapter.engine_state.bind_initial_source("rev-a")
    assert freshness.index_revision(session) == "rev-a"
    graph = freshness.graph_state(session)
    assert graph["source_revision"] == "rev-a"
    assert graph["graph_current"] is False
    assert freshness.unit_state(session, "nope") is None
