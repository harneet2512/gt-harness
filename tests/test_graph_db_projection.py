from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from gt_engine.graph_db_projection import (
    GraphProjectionLimits,
    PersistedGraphProjector,
    ProjectionStatus,
)
from gt_engine.repository_graph_service import (
    GRAPH_BUILDER_VERSION,
    GraphNotReadyError,
    GraphReceipt,
    GraphStatus,
    RepositoryGraphService,
    compute_repository_identity,
)
from gt_harness.indexer_setup import ensure_source_indexer


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gt@example.invalid")
    _git(root, "config", "user.name", "GT Test")
    files = {
        "app.py": "def main():\n    return dispatch()\n",
        "service.py": "def dispatch():\n    return work()\n\ndef work():\n    return 1\n",
        "audit.py": "def audit():\n    return None\n",
        "api.py": "def send():\n    return None\n",
        "db.py": "def store():\n    return None\n",
        "guess.py": "def guessed():\n    return None\n",
    }
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _write_process_graph(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT,
                return_type TEXT, is_exported INTEGER, is_test INTEGER, language TEXT,
                parent_id INTEGER, repo_id TEXT
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT,
                source_line INTEGER, source_file TEXT, resolution_method TEXT,
                confidence REAL, metadata TEXT, trust_tier TEXT, candidate_count INTEGER,
                evidence_type TEXT, verification_status TEXT, repo_id TEXT
            );
            CREATE TABLE edge_metadata (
                edge_id INTEGER, key TEXT, value TEXT, schema_version INTEGER,
                PRIMARY KEY(edge_id,key)
            );
            INSERT INTO nodes VALUES
              (1,'Function','main','app.main','app.py',1,2,'main()','',1,0,'python',NULL,'repo'),
              (2,'Function','dispatch','service.dispatch','service.py',1,2,'dispatch()','',1,0,'python',NULL,'repo'),
              (3,'Function','work','service.work','service.py',4,5,'work()','int',1,0,'python',NULL,'repo'),
              (4,'Function','audit','audit.audit','audit.py',1,2,'audit()','',1,0,'python',NULL,'repo'),
              (5,'Function','send','api.send','api.py',1,2,'send()','',1,0,'python',NULL,'repo'),
              (6,'Function','store','db.store','db.py',1,2,'store()','',1,0,'python',NULL,'repo'),
              (7,'Function','guessed','guess.guessed','guess.py',1,2,'guessed()','',1,0,'python',NULL,'repo'),
              (8,'Function','work','guess.work','guess.py',4,5,'work()','',1,0,'python',NULL,'repo');
            INSERT INTO edges VALUES
              (10,1,2,'CALLS',2,'app.py','lsp_verified',1.0,'receiver_type=Application','CERTIFIED',1,'receiver_type','verified','repo'),
              (11,2,3,'CALLS',2,'service.py','lsp_verified',1.0,'receiver_type=Service','CERTIFIED',1,'receiver_type','verified','repo'),
              (12,3,4,'CALLS',5,'service.py','same_file',1.0,'receiver_type=AuditLog','CERTIFIED',1,'receiver_type','verified','repo'),
              (13,3,5,'CALLS',5,'service.py','same_file',1.0,'receiver_type=Gateway','CERTIFIED',1,'receiver_type','verified','repo'),
              (14,3,6,'CALLS',5,'service.py','same_file',1.0,'receiver_type=Repository','CERTIFIED',1,'receiver_type','verified','repo'),
              (15,2,7,'CALLS',2,'service.py','global_name',0.6,'','CANDIDATE',2,'name_match','unverified','repo');
            INSERT INTO edge_metadata VALUES
              (10,'receiver_type','Application',1),
              (11,'receiver_type','Service',1),
              (12,'receiver_type','AuditLog',1),
              (13,'receiver_type','Gateway',1),
              (14,'receiver_type','Repository',1);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _service(root: Path, state: Path) -> RepositoryGraphService:
    state.mkdir()
    graph = state / "graph.db"
    _write_process_graph(graph)
    identity = compute_repository_identity(root)
    receipt = GraphReceipt(
        repository=str(root.resolve()),
        commit_sha=identity.commit_sha,
        branch=identity.branch,
        working_tree_state=identity.working_tree_state,
        source_revision=identity.source_revision,
        graph_schema_version="test-v1",
        graph_builder_version=GRAPH_BUILDER_VERSION,
        build_started="2026-08-23T00:00:00Z",
        build_completed="2026-08-23T00:00:01Z",
        build_status=GraphStatus.READY,
        files_discovered=6,
        files_attempted=6,
        files_indexed=6,
        files_skipped=0,
        files_failed=0,
        symbols=8,
        nodes_by_type={"Function": 8},
        edges_by_type={"CALLS": 6},
        coverage=1.0,
        build_duration_ms=1000.0,
        persistent_graph_path=str(graph),
        graph_checksum_or_identity=RepositoryGraphService.file_sha256(graph),
        query_ready=True,
        degraded_reasons=(),
        repository_files_discovered=identity.files_discovered,
        graph_input_hashes=identity.graph_input_hashes,
        graph_input_sizes=identity.graph_input_sizes,
        graph_input_fingerprints=identity.graph_input_fingerprints,
        git_status_paths=identity.git_status_paths,
        submodule_state=identity.submodule_state,
    )
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    return RepositoryGraphService(root, state_dir=state)


def _impact_repository(tmp_path: Path) -> Path:
    root = tmp_path / "impact-repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gt@example.invalid")
    _git(root, "config", "user.name", "GT Test")
    for relative in (
        "service.py",
        "caller.py",
        "app.py",
        "importer.py",
        "exporter.py",
        "child.py",
        "impl.py",
        "override.py",
        "tests/test_edge.py",
        "tests/test_assert.py",
        "docs/only-cochange.md",
        "guess.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "impact fixture")
    return root


def _write_impact_graph(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT,
                return_type TEXT, is_exported INTEGER, is_test INTEGER, language TEXT,
                parent_id INTEGER, repo_id TEXT
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT,
                source_line INTEGER, source_file TEXT, resolution_method TEXT,
                confidence REAL, metadata TEXT, trust_tier TEXT, candidate_count INTEGER,
                evidence_type TEXT, verification_status TEXT, repo_id TEXT
            );
            CREATE TABLE assertions (
                id INTEGER PRIMARY KEY, test_node_id INTEGER, target_node_id INTEGER,
                resolution_score REAL, kind TEXT, expression TEXT, expected TEXT, line INTEGER
            );
            CREATE TABLE cochanges (file_a TEXT, file_b TEXT, count INTEGER);
            INSERT INTO nodes VALUES
              (1,'Function','work','service.work','service.py',1,2,'work()','int',1,0,'python',NULL,'repo'),
              (2,'Function','caller','caller.caller','caller.py',1,2,'caller()','',1,0,'python',NULL,'repo'),
              (3,'Function','main','app.main','app.py',1,2,'main()','',1,0,'python',NULL,'repo'),
              (4,'Module','importer','importer','importer.py',1,2,'','',1,0,'python',NULL,'repo'),
              (5,'Module','exporter','exporter','exporter.py',1,2,'','',1,0,'python',NULL,'repo'),
              (6,'Class','Child','child.Child','child.py',1,2,'Child','',1,0,'python',NULL,'repo'),
              (7,'Class','Implementation','impl.Implementation','impl.py',1,2,'Implementation','',1,0,'python',NULL,'repo'),
              (8,'Method','override','override.override','override.py',1,2,'override()','',1,0,'python',NULL,'repo'),
              (9,'Function','test_edge','tests.test_edge','tests/test_edge.py',1,2,'test_edge()','',1,1,'python',NULL,'repo'),
              (10,'Function','test_assert','tests.test_assert','tests/test_assert.py',1,2,'test_assert()','',1,1,'python',NULL,'repo'),
              (11,'Section','only_cochange','docs.only_cochange','docs/only-cochange.md',1,1,'','',0,0,'markdown',NULL,'repo'),
              (12,'Function','guess','guess.guess','guess.py',1,2,'guess()','',1,0,'python',NULL,'repo');
            INSERT INTO edges VALUES
              (20,2,1,'CALLS',2,'caller.py','lsp_verified',1.0,'receiver_type=Service','CERTIFIED',1,'receiver_type','verified','repo'),
              (21,3,2,'CALLS',2,'app.py','same_file',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (22,4,1,'IMPORTS',1,'importer.py','import_exact',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (23,5,1,'RE_EXPORTS',1,'exporter.py','re_export',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (24,6,1,'EXTENDS',1,'child.py','inheritance',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (25,7,1,'IMPLEMENTS',1,'impl.py','interface_match',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (26,8,1,'OVERRIDES',1,'override.py','override_match',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (27,1,9,'TESTED_BY',1,'service.py','test_target',1.0,'','CERTIFIED',1,'ast','verified','repo'),
              (28,12,1,'CALLS',2,'guess.py','global_name',0.6,'','CANDIDATE',2,'name_match','unverified','repo');
            INSERT INTO assertions VALUES
              (30,10,1,1.0,'assertEqual','work() == 1','1',2);
            INSERT INTO cochanges VALUES
              ('service.py','app.py',9),
              ('service.py','docs/only-cochange.md',100);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _impact_service(root: Path, state: Path) -> RepositoryGraphService:
    state.mkdir()
    graph = state / "graph.db"
    _write_impact_graph(graph)
    identity = compute_repository_identity(root)
    receipt = GraphReceipt(
        repository=str(root.resolve()),
        commit_sha=identity.commit_sha,
        branch=identity.branch,
        working_tree_state=identity.working_tree_state,
        source_revision=identity.source_revision,
        graph_schema_version="test-v1",
        graph_builder_version=GRAPH_BUILDER_VERSION,
        build_started="2026-08-23T00:00:00Z",
        build_completed="2026-08-23T00:00:01Z",
        build_status=GraphStatus.READY,
        files_discovered=12,
        files_attempted=12,
        files_indexed=12,
        files_skipped=0,
        files_failed=0,
        symbols=12,
        nodes_by_type={"Function": 12},
        edges_by_type={"CALLS": 3},
        coverage=1.0,
        build_duration_ms=1000.0,
        persistent_graph_path=str(graph),
        graph_checksum_or_identity=RepositoryGraphService.file_sha256(graph),
        query_ready=True,
        degraded_reasons=(),
        repository_files_discovered=identity.files_discovered,
        graph_input_hashes=identity.graph_input_hashes,
        graph_input_sizes=identity.graph_input_sizes,
        graph_input_fingerprints=identity.graph_input_fingerprints,
        git_status_paths=identity.git_status_paths,
        submodule_state=identity.submodule_state,
    )
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    return RepositoryGraphService(root, state_dir=state)


def _unproven_service(root: Path, state: Path) -> RepositoryGraphService:
    state.mkdir()
    graph = state / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT,
                return_type TEXT, is_exported INTEGER, is_test INTEGER, language TEXT,
                parent_id INTEGER, repo_id TEXT
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT
            );
            INSERT INTO nodes VALUES
              (1,'Function','dispatch','service.dispatch','service.py',1,2,'dispatch()','',1,0,'python',NULL,'repo'),
              (2,'Function','work','service.work','service.py',4,5,'work()','',1,0,'python',NULL,'repo');
            INSERT INTO edges VALUES (1,1,2,'CALLS');
            """
        )
        connection.commit()
    finally:
        connection.close()
    identity = compute_repository_identity(root)
    receipt = GraphReceipt(
        repository=str(root.resolve()),
        commit_sha=identity.commit_sha,
        branch=identity.branch,
        working_tree_state=identity.working_tree_state,
        source_revision=identity.source_revision,
        graph_schema_version="legacy-test",
        graph_builder_version=GRAPH_BUILDER_VERSION,
        build_started="2026-08-23T00:00:00Z",
        build_completed="2026-08-23T00:00:01Z",
        build_status=GraphStatus.READY_WITH_DECLARED_LIMITATIONS,
        files_discovered=6,
        files_attempted=6,
        files_indexed=6,
        files_skipped=0,
        files_failed=0,
        symbols=2,
        nodes_by_type={"Function": 2},
        edges_by_type={"CALLS": 1},
        coverage=1.0,
        build_duration_ms=1000.0,
        persistent_graph_path=str(graph),
        graph_checksum_or_identity=RepositoryGraphService.file_sha256(graph),
        query_ready=True,
        degraded_reasons=("legacy_edge_provenance_missing",),
        repository_files_discovered=identity.files_discovered,
        graph_input_hashes=identity.graph_input_hashes,
        graph_input_sizes=identity.graph_input_sizes,
        graph_input_fingerprints=identity.graph_input_fingerprints,
        git_status_paths=identity.git_status_paths,
        submodule_state=identity.submodule_state,
    )
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    return RepositoryGraphService(root, state_dir=state)


def test_process_projection_reads_only_exact_persisted_calls_and_exposes_receivers(
    tmp_path: Path,
) -> None:
    projector = PersistedGraphProjector(_service(_repository(tmp_path), tmp_path / "state"))

    result = projector.project_processes("work", file_path="service.py")

    assert result.status is ProjectionStatus.READY
    assert len(result.processes) == 3
    assert {tuple(step.target.name for step in process.steps) for process in result.processes} == {
        ("dispatch", "work", "audit"),
        ("dispatch", "work", "send"),
        ("dispatch", "work", "store"),
    }
    assert all(process.steps[0].source.name == "main" for process in result.processes)
    assert all(step.receiver_type for process in result.processes for step in process.steps)
    assert all(
        step.evidence.edge_id > 0
        and step.evidence.relationship == "CALLS"
        and step.evidence.resolution_outcome == "exact"
        for process in result.processes
        for step in process.steps
    )
    assert result.receipt.lower_bound is True
    assert result.receipt.evidence["exact_calls"] == 5
    assert result.receipt.evidence["rejected_calls"] == 1
    assert result.receipt.evidence["receiver_resolution_outcomes"] == {
        "resolved": 5,
        "ambiguous": 1,
        "external": 0,
        "unresolved": 0,
        "capped": 0,
    }
    assert (
        result.receipt.evidence["receiver_resolution_coverage"]
        == "persisted_edges_only"
    )
    assert result.receipt.evidence["returned_edge_ids"] == [10, 11, 12, 13, 14]
    assert result.receipt.evidence["typed_receiver_outcomes"] == [
        {"edge_id": 10, "receiver_type": "Application"},
        {"edge_id": 11, "receiver_type": "Service"},
        {"edge_id": 12, "receiver_type": "AuditLog"},
        {"edge_id": 13, "receiver_type": "Gateway"},
        {"edge_id": 14, "receiver_type": "Repository"},
    ]
    assert result.receipt.limits == {
        "max_depth": 8,
        "max_branching": 4,
        "max_expansions": 128,
        "max_candidates": 24,
        "max_processes": 3,
        "max_impact_depth": 3,
    }


def test_projection_is_ambiguous_without_a_file_anchor_and_never_merges_symbols(
    tmp_path: Path,
) -> None:
    projector = PersistedGraphProjector(_service(_repository(tmp_path), tmp_path / "state"))

    process = projector.project_processes("work")
    impact = projector.project_impact("work")

    assert process.status is ProjectionStatus.AMBIGUOUS
    assert process.processes == ()
    assert len(process.ambiguous_candidates) == 2
    assert impact.status is ProjectionStatus.AMBIGUOUS
    assert impact.impacts == ()
    assert len(impact.ambiguous_candidates) == 2


def test_projection_clamps_limits_and_receipts_every_bounded_omission(
    tmp_path: Path,
) -> None:
    service = _service(_repository(tmp_path), tmp_path / "state")
    ceilings = GraphProjectionLimits(
        max_depth=99,
        max_branching=99,
        max_expansions=999,
        max_candidates=99,
        max_processes=99,
        max_impact_depth=99,
    )
    assert ceilings.as_dict() == {
        "max_depth": 8,
        "max_branching": 4,
        "max_expansions": 128,
        "max_candidates": 24,
        "max_processes": 3,
        "max_impact_depth": 3,
    }

    result = PersistedGraphProjector(
        service,
        limits=GraphProjectionLimits(
            max_depth=8,
            max_branching=1,
            max_expansions=3,
            max_candidates=1,
            max_processes=1,
            max_impact_depth=3,
        ),
    ).project_processes("work", file_path="service.py")

    assert len(result.processes) <= 1
    assert all(len(process.steps) <= 8 for process in result.processes)
    assert result.receipt.evidence["expansions"] <= 3
    assert result.receipt.evidence["candidate_paths"] <= 1
    assert result.receipt.truncated is True
    assert {"branch_limit", "candidate_limit"} <= set(result.receipt.truncation_reasons)


def test_projection_fails_closed_when_repository_identity_changes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    projector = PersistedGraphProjector(_service(root, tmp_path / "state"))
    (root / "service.py").write_text("def work():\n    return 99\n", encoding="utf-8")

    with pytest.raises(GraphNotReadyError, match="source_revision_mismatch"):
        projector.project_processes("work", file_path="service.py")


def test_projection_abstains_and_receipts_missing_exact_edge_provenance(
    tmp_path: Path,
) -> None:
    projector = PersistedGraphProjector(
        _unproven_service(_repository(tmp_path), tmp_path / "legacy-state")
    )

    process = projector.project_processes("work", file_path="service.py")
    impact = projector.project_impact("work", file_path="service.py")

    assert process.status is ProjectionStatus.READY
    assert process.processes == ()
    assert process.receipt.truncated is True
    assert process.receipt.evidence["unsupported_surfaces"] == ["edges_exact_provenance"]
    assert "exact_call_provenance_unavailable" in process.receipt.truncation_reasons
    assert impact.status is ProjectionStatus.READY
    assert impact.impacts == ()
    assert impact.receipt.truncated is True
    assert impact.receipt.evidence["unsupported_surfaces"] == ["edges_exact_provenance"]
    assert "exact_impact_provenance_unavailable" in impact.receipt.truncation_reasons


def test_projection_session_loads_persisted_edge_sets_once_for_multiple_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projector = PersistedGraphProjector(
        _service(_repository(tmp_path), tmp_path / "state")
    )
    original_calls = PersistedGraphProjector._load_exact_calls
    original_impacts = PersistedGraphProjector._load_exact_impact_edges
    loads = {"calls": 0, "impacts": 0}

    def load_calls(connection):
        loads["calls"] += 1
        return original_calls(connection)

    def load_impacts(connection):
        loads["impacts"] += 1
        return original_impacts(connection)

    monkeypatch.setattr(
        PersistedGraphProjector, "_load_exact_calls", staticmethod(load_calls)
    )
    monkeypatch.setattr(
        PersistedGraphProjector,
        "_load_exact_impact_edges",
        staticmethod(load_impacts),
    )

    with projector:
        projector.project_processes("dispatch", file_path="service.py")
        projector.project_processes("work", file_path="service.py")
        projector.project_impact("dispatch", file_path="service.py")
        projector.project_impact("work", file_path="service.py")

    assert loads == {"calls": 1, "impacts": 1}


def test_impact_projection_traverses_typed_exact_edges_and_keeps_cochange_rank_only(
    tmp_path: Path,
) -> None:
    projector = PersistedGraphProjector(
        _impact_service(_impact_repository(tmp_path), tmp_path / "impact-state")
    )

    result = projector.project_impact("work", file_path="service.py")

    assert result.status is ProjectionStatus.READY
    direct_relations = {impact.relationship for impact in result.impacts if impact.depth == 1}
    assert direct_relations == {
        "CALLS",
        "IMPORTS",
        "RE_EXPORTS",
        "EXTENDS",
        "IMPLEMENTS",
        "OVERRIDES",
        "TESTED_BY",
        "ASSERTED_BY",
    }
    assert any(
        impact.impacted.name == "main" and impact.relationship == "CALLS" and impact.depth == 2
        for impact in result.impacts
    )
    assert all(impact.depth <= 3 for impact in result.impacts)
    assert all(impact.impacted.file_path != "docs/only-cochange.md" for impact in result.impacts)
    caller = next(
        impact for impact in result.impacts if impact.relationship == "CALLS" and impact.depth == 1
    )
    assert caller.receiver_type == "Service"
    assert caller.evidence.resolution_outcome == "exact"
    asserted = next(impact for impact in result.impacts if impact.relationship == "ASSERTED_BY")
    assert asserted.evidence.assertion_id == 30
    assert asserted.evidence.evidence_source == "assertions"
    assert result.receipt.lower_bound is True
    assert result.receipt.evidence["cochange_rank_only"] is True
    assert result.receipt.evidence["cochange_pairs_considered"] == 2
    assert result.receipt.evidence["rejected_relationships"] == 1
    assert "assertion:30" in result.receipt.evidence["returned_evidence_ids"]
    assert result.receipt.evidence["rank_only_cochange_evidence"] == [
        {
            "count": 100,
            "evidence_source": "cochanges",
            "file_a": "service.py",
            "file_b": "docs/only-cochange.md",
        },
        {
            "count": 9,
            "evidence_source": "cochanges",
            "file_a": "service.py",
            "file_b": "app.py",
        },
    ]


@pytest.mark.real_graph
def test_projection_reads_the_source_built_indexer_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    setup = ensure_source_indexer()
    assert setup.status == "READY", setup.as_dict()
    monkeypatch.setenv("GT_INDEX_BINARY", setup.binary_path)
    service = RepositoryGraphService(root, state_dir=tmp_path / "real-state")
    graph_receipt = service.build(force=True, timeout=180)
    assert graph_receipt.query_ready, graph_receipt.as_dict()

    projector = PersistedGraphProjector(service)
    process = projector.project_processes("work", file_path="service.py")
    impact = projector.project_impact("work", file_path="service.py")

    assert process.status is ProjectionStatus.READY
    assert process.processes
    assert all(
        step.evidence.resolution_outcome == "exact"
        for path in process.processes
        for step in path.steps
    )
    assert impact.status is ProjectionStatus.READY
    assert any(
        fact.relationship == "CALLS" and fact.impacted.name == "dispatch" for fact in impact.impacts
    )
    assert process.receipt.graph_identity == graph_receipt.graph_checksum_or_identity
    assert impact.receipt.source_revision == graph_receipt.source_revision
