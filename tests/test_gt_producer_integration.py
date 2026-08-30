from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

import gt_engine.indexer as indexer
from gt_engine.graph_context import build_graph_projection
from gt_engine.indexer import ensure_index
from gt_engine.task_contract import Obligation, TaskContract


def test_vendored_binary_is_not_an_accepted_producer():
    with pytest.raises(indexer.ProducerContractError, match="vendor"):
        indexer._validate_producer_binary("vendor/gt-index-src/gt-index.exe")


def test_build_info_binds_exact_external_producer(tmp_path: Path, monkeypatch):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"producer")
    info = {
        "schema": indexer.PRODUCER_BUILD_INFO_SCHEMA,
        "complete": True,
        "git_commit": indexer.GROUNDTRUTH_PRODUCER_COMMIT,
        "source_fingerprint": indexer.GROUNDTRUTH_PRODUCER_SOURCE_TREE,
        "build_tags": "sqlite_fts5",
        "graph_schema_version": indexer.PRODUCER_GRAPH_SCHEMA_VERSION,
        "capabilities": sorted(indexer.PRODUCER_REQUIRED_CAPABILITIES),
        "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "build_id": "build-id",
        "build_time_utc": "2026-08-30T00:00:00Z",
        "go_toolchain": "go1.24.0",
    }
    monkeypatch.setattr(indexer, "_run_producer_build_info", lambda _path: info)

    identity = indexer._validate_producer_binary(str(binary))

    assert identity["git_commit"] == indexer.GROUNDTRUTH_PRODUCER_COMMIT
    assert identity["source_fingerprint"] == indexer.GROUNDTRUTH_PRODUCER_SOURCE_TREE


def test_build_info_rejects_unexpected_source_tree(tmp_path: Path, monkeypatch):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"producer")
    info = {
        "schema": indexer.PRODUCER_BUILD_INFO_SCHEMA,
        "complete": True,
        "git_commit": indexer.GROUNDTRUTH_PRODUCER_COMMIT,
        "source_fingerprint": "tampered-source-tree",
        "build_tags": "sqlite_fts5",
        "graph_schema_version": indexer.PRODUCER_GRAPH_SCHEMA_VERSION,
        "capabilities": sorted(indexer.PRODUCER_REQUIRED_CAPABILITIES),
        "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(indexer, "_run_producer_build_info", lambda _path: info)

    with pytest.raises(indexer.ProducerContractError, match="source tree fingerprint"):
        indexer._validate_producer_binary(str(binary))


def test_same_head_dirty_source_checkout_is_rejected(tmp_path: Path, monkeypatch):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"producer")
    info = {
        "schema": indexer.PRODUCER_BUILD_INFO_SCHEMA,
        "complete": True,
        "git_commit": indexer.GROUNDTRUTH_PRODUCER_COMMIT,
        "source_fingerprint": indexer.GROUNDTRUTH_PRODUCER_SOURCE_TREE,
        "build_tags": "sqlite_fts5",
        "graph_schema_version": indexer.PRODUCER_GRAPH_SCHEMA_VERSION,
        "capabilities": sorted(indexer.PRODUCER_REQUIRED_CAPABILITIES),
        "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(indexer, "_run_producer_build_info", lambda _path: info)
    monkeypatch.setenv("GT_INDEX_SOURCE_DIR", str(tmp_path / "gt-index"))

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                command, 0, indexer.GROUNDTRUTH_PRODUCER_COMMIT + "\n", ""
            )
        if command[-2:] == ["rev-parse", "HEAD:gt-index"]:
            return subprocess.CompletedProcess(
                command, 0, indexer.GROUNDTRUTH_PRODUCER_SOURCE_TREE + "\n", ""
            )
        if command[-3:] == ["--untracked-files=all", "--", "."]:
            return subprocess.CompletedProcess(command, 0, " M internal/resolver/resolver.go\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(indexer.subprocess, "run", fake_run)
    with pytest.raises(indexer.ProducerContractError, match="source checkout is dirty"):
        indexer._validate_producer_binary(str(binary))


def test_external_builder_binds_independent_tree_and_static_artifact():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_external_gt_index.sh"
    script = script_path.read_text(encoding="utf-8")
    assert indexer.GROUNDTRUTH_PRODUCER_SOURCE_TREE in script
    assert "status --porcelain --untracked-files=all" in script
    assert r'-extldflags \"-static\"' in script
    assert "readelf -d" in script


def test_graph_completion_rejects_tampered_receipt(tmp_path: Path):
    graph = tmp_path / "graph.db"
    con = sqlite3.connect(graph)
    try:
        con.execute("CREATE TABLE project_meta(key TEXT PRIMARY KEY, value TEXT)")
        con.executemany(
            "INSERT INTO project_meta(key,value) VALUES (?,?)",
            (
                ("graph_resolution_schema_version", "2"),
                ("graph_resolution_complete", "1"),
                ("graph_completion_schema", indexer.PRODUCER_GRAPH_COMPLETION_SCHEMA),
                ("graph_completion_receipt", json.dumps({"complete": True})),
                ("graph_completion_receipt_sha256", "tampered"),
            ),
        )
        con.commit()
    finally:
        con.close()

    with pytest.raises(indexer.ProducerContractError, match="receipt hash"):
        indexer._validate_graph_completion(
            graph,
            {
                "git_commit": indexer.GROUNDTRUTH_PRODUCER_COMMIT,
                "executable_sha256": "binary",
            },
        )


def test_graph_projection_reads_primary_v2_candidates_and_zero_candidate_callsites(
    tmp_path: Path,
):
    graph = tmp_path / "graph.db"
    con = sqlite3.connect(graph)
    try:
        con.executescript(
            """
            CREATE TABLE nodes(
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, is_test INTEGER, start_line INTEGER, node_type TEXT,
                candidate_state TEXT, selected_target_id TEXT, candidate_count_v2 INTEGER
            );
            CREATE TABLE edges(
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
                type TEXT, confidence REAL, target_symbol_id TEXT, ordinal INTEGER,
                viability TEXT
            );
            CREATE TABLE project_meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO project_meta VALUES ('graph_resolution_schema_version','2');
            INSERT INTO project_meta VALUES ('graph_resolution_complete','1');
            INSERT INTO project_meta VALUES ('graph_resolution_revision','rev');
            INSERT INTO nodes VALUES
                (1,'Function','caller','caller','src.py',0,1,'symbol','','',''),
                (2,'Callsite','missing_target','caller.missing_target','src.py',0,2,'callsite','empty','',0),
                (3,'Function','missing_target','missing_target','impl.py',0,1,'symbol','','',''),
                (4,'Callsite','ambiguous_target','caller.ambiguous_target','src.py',0,3,'callsite','ambiguous','',2);
            INSERT INTO edges VALUES (1,1,2,'HAS_CALLSITE',NULL,NULL,NULL,NULL);
            INSERT INTO edges VALUES (2,4,3,'CANDIDATE_TARGET',NULL,'target-3',0,'viable');
            """
        )
        con.commit()
    finally:
        con.close()

    contract = TaskContract(
        role="code_behavior",
        obligations=(
            Obligation(
                "o1", "resolve missing_target callsites", "test", ("missing_target",)
            ),
        ),
    )
    projection = build_graph_projection(str(graph), contract)

    facts = [fact for fact in projection.semantic_facts if fact.surface == "resolution_v2"]
    assert facts
    assert any("empty" in fact.value and "0 candidates" in fact.value for fact in facts)


def test_exact_producer_publishes_v2_completion_and_candidate_rows(tmp_path: Path):
    binary = os.environ.get("GT_INDEX_BINARY", "")
    if not binary or not Path(binary).is_file():
        pytest.skip("set GT_INDEX_BINARY to a built pinned producer")
    (tmp_path / "impl.py").write_text(
        "def target(value):\n    return value\n\n"
        "def caller(value):\n    return target(value)\n",
        encoding="utf-8",
    )

    database = ensure_index(str(tmp_path), state_dir=tmp_path / "state")

    assert database is not None
    con = sqlite3.connect(database)
    try:
        metadata = dict(con.execute("SELECT key,value FROM project_meta"))
        assert metadata["graph_resolution_schema_version"] == "2"
        assert metadata["graph_resolution_complete"] == "1"
        assert metadata["graph_producer_git_commit"] == indexer.GROUNDTRUTH_PRODUCER_COMMIT
        assert int(
            con.execute(
                "SELECT count(*) FROM nodes WHERE node_type='callsite' AND schema_version=2"
            ).fetchone()[0]
        ) > 0
        assert int(
            con.execute(
                "SELECT count(*) FROM edges WHERE type='CANDIDATE_TARGET' "
                "AND schema_version=2 AND confidence IS NULL"
            ).fetchone()[0]
        ) > 0
    finally:
        con.close()


def _write_projection_fixture(
    graph: Path,
    *,
    declared_count: int,
    selected_target: str = "",
    candidates: tuple[str, ...] = ("target-3",),
    include_v2_metadata: bool = True,
) -> None:
    con = sqlite3.connect(graph)
    try:
        con.executescript(
            """
            CREATE TABLE nodes(
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, is_test INTEGER, start_line INTEGER, node_type TEXT,
                candidate_state TEXT, selected_target_id TEXT, candidate_count_v2 INTEGER
            );
            CREATE TABLE edges(
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
                type TEXT, confidence REAL, target_symbol_id TEXT, ordinal INTEGER,
                viability TEXT
            );
            CREATE TABLE project_meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO nodes VALUES
                (1,'Function','caller','caller','src.py',0,1,'symbol','','',NULL),
                (2,'Callsite','target','caller.target','src.py',0,3,'callsite','ambiguous','',0);
            """
        )
        con.execute(
            "UPDATE nodes SET selected_target_id=?, candidate_count_v2=? WHERE id=2",
            (selected_target, declared_count),
        )
        for offset, target in enumerate(candidates, 3):
            con.execute(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (offset, "Function", target, target, "impl.py", 0, 1, "symbol", "", "", None),
            )
            con.execute(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?,?)",
                (offset, 2, offset, "CANDIDATE_TARGET", None, target, offset - 3, "viable"),
            )
        if include_v2_metadata:
            con.executemany(
                "INSERT INTO project_meta VALUES (?,?)",
                (
                    ("graph_resolution_schema_version", "2"),
                    ("graph_resolution_complete", "1"),
                    ("graph_resolution_revision", "rev"),
                ),
            )
        con.commit()
    finally:
        con.close()


def _projection_for(graph: Path) -> object:
    contract = TaskContract(
        role="code_behavior",
        obligations=(Obligation("o1", "resolve target callsites", "test", ("target",)),),
    )
    return build_graph_projection(str(graph), contract)


@pytest.mark.parametrize(
    ("declared_count", "selected_target", "candidates", "reason"),
    (
        (2, "", ("target-3",), "candidate_count_mismatch"),
        (1, "not-retained", ("target-3",), "selected_target_not_retained"),
        (2, "", ("target-3", "target-3"), "duplicate_candidate_identity"),
    ),
)
def test_v2_projection_withholds_inconsistent_candidate_authority(
    tmp_path: Path,
    declared_count: int,
    selected_target: str,
    candidates: tuple[str, ...],
    reason: str,
):
    graph = tmp_path / "graph.db"
    _write_projection_fixture(
        graph,
        declared_count=declared_count,
        selected_target=selected_target,
        candidates=candidates,
    )

    projection = _projection_for(graph)

    assert not [fact for fact in projection.semantic_facts if fact.surface == "resolution_v2"]
    abstentions = [
        fact for fact in projection.semantic_facts
        if fact.surface == "resolution_v2_abstention"
    ]
    assert len(abstentions) == 1
    assert reason in abstentions[0].value


def test_old_schema_does_not_claim_v2_authority(tmp_path: Path):
    graph = tmp_path / "old-graph.db"
    _write_projection_fixture(
        graph,
        declared_count=1,
        candidates=("target-3",),
        include_v2_metadata=False,
    )

    projection = _projection_for(graph)

    assert not [fact for fact in projection.semantic_facts if fact.surface == "resolution_v2"]
    assert not [
        fact for fact in projection.semantic_facts
        if fact.surface == "resolution_v2_abstention"
    ]
