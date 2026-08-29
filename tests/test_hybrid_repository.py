from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import gt_engine.hybrid_repository as hybrid_repository
from gt_engine.graph_context import GraphProjection, GraphSemanticFact
from gt_engine.hybrid_repository import (
    RepositoryBuildLimits,
    build_hybrid_repository,
    build_query_hybrid_repository,
)
from gt_engine.hybrid_retrieval import EvidenceOrigin, RetrievalIntent, RetrievalState


def _graph(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,"
            "qualified_name TEXT,file_path TEXT,start_line INTEGER,end_line INTEGER,"
            "signature TEXT,language TEXT,is_test BOOLEAN)"
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,source_line INTEGER,source_file TEXT,resolution_method TEXT,"
            "confidence REAL,metadata TEXT,trust_tier TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "function",
                    "allocate",
                    "allocate",
                    "/app/src/allocator.py",
                    1,
                    2,
                    "def allocate()",
                    "python",
                    0,
                ),
                (
                    2,
                    "function",
                    "test_allocate",
                    "test_allocate",
                    "tests/test_allocator.py",
                    1,
                    2,
                    "def test_allocate()",
                    "python",
                    1,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO edges VALUES (1,1,2,'TESTED_BY',1,'/app/src/allocator.py',"
            "'parser',0.99,'','CERTIFIED')"
        )
        connection.commit()
    finally:
        connection.close()


def test_builder_uses_exact_indexed_spans_and_directed_graph_links(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "allocator.py").write_text(
        "def allocate():\n    return 1\nignored = True\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_allocator.py").write_text(
        "def test_allocate():\n    assert allocate()\n",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.db"
    _graph(graph)

    repository = build_hybrid_repository(
        tmp_path,
        graph,
        source_revision="source-1",
        model_authored_paths=("src/allocator.py",),
        task_deliverables=("tests/test_allocator.py",),
    )

    assert repository.complete is True
    assert [row.path for row in repository.documents] == [
        "src/allocator.py",
        "tests/test_allocator.py",
    ]
    assert repository.documents[0].text == "def allocate():\n    return 1"
    assert repository.documents[0].start_line == 1
    assert repository.documents[0].end_line == 2
    assert repository.documents[0].origin is EvidenceOrigin.MODEL_AUTHORED
    assert repository.documents[1].origin is EvidenceOrigin.TASK_DELIVERABLE
    assert repository.structural_links[0].source_path == "src/allocator.py"
    assert repository.structural_links[0].target_path == "tests/test_allocator.py"
    assert repository.structural_links[0].relation == "TESTED_BY"
    assert repository.structural_links[0].source_symbol == "allocate"
    assert repository.structural_links[0].source_start_line == 1
    assert repository.structural_links[0].target_symbol == "test_allocate"
    assert repository.structural_links[0].target_start_line == 1


def test_builder_propagates_resolution_provenance_and_refuses_ambiguous_edges(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "caller.py").write_text(
        "def caller():\n    return target()\n", encoding="utf-8"
    )
    (tmp_path / "src" / "target.py").write_text(
        "def target():\n    return 1\n", encoding="utf-8"
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?)",
            (
                (1, "caller", "src/caller.py", 1, 2, "def caller()"),
                (2, "target", "src/target.py", 1, 2, "def target()"),
            ),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,resolution_method TEXT,confidence REAL,trust_tier TEXT,"
            "candidate_count INTEGER,evidence_type TEXT,verification_status TEXT,metadata TEXT)"
        )
        connection.execute(
            "INSERT INTO edges VALUES (1,1,2,'CALLS','global_name',0.99,'CERTIFIED',2,"
            "'static_resolution','unverified','')"
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(tmp_path, graph, source_revision="source-1")

    assert len(repository.structural_links) == 1
    link = repository.structural_links[0]
    assert link.origin == "program"
    assert link.resolution_outcome == "ambiguous"
    assert link.certified is False
    assert "resolution_method:global_name" in link.provenance
    assert "candidate_count:2" in link.provenance


def test_builder_keeps_same_file_calls_and_typed_receiver_provenance(tmp_path):
    source = (
        "class Service:\n"
        "    def run(self) -> int:\n"
        "        return self.helper()\n"
        "    def helper(self) -> int:\n"
        "        return 1\n"
    )
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT,return_type TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
            (
                (1, "Method", "run", "service.py", 2, 3, "def run(self) -> int", "int"),
                (2, "Method", "helper", "service.py", 4, 5, "def helper(self) -> int", "int"),
            ),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,resolution_method TEXT,confidence REAL,trust_tier TEXT,"
            "candidate_count INTEGER,evidence_type TEXT,verification_status TEXT,metadata TEXT)"
        )
        connection.execute(
            "INSERT INTO edges VALUES (1,1,2,'CALLS','lsp_verified',1.0,'CERTIFIED',1,"
            "'receiver_type','lsp_verified','receiver_type=Service;receiver_origin=explicit_type;receiver_shape=instance')"
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(tmp_path, graph, source_revision="source-1")

    assert len(repository.structural_links) == 1
    link = repository.structural_links[0]
    assert link.source_path == link.target_path == "service.py"
    assert link.source_symbol == "run"
    assert link.target_symbol == "helper"
    assert link.receiver_type == "Service"
    assert link.receiver_origin == "explicit_type"
    assert link.receiver_shape == "instance"
    assert link.resolution_method == "lsp_verified"
    assert link.source_return_type == link.target_return_type == "int"
    assert link.certified is True


def test_builder_preserves_certified_route_metadata_from_graph(tmp_path):
    (tmp_path / "routes.py").write_text(
        '@app.get("/users/{id}")\ndef get_user(id: int):\n    return id\n',
        encoding="utf-8",
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
            (1, "Function", "get_user", "routes.py", 2, 3, "def get_user(id: int)"),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,resolution_method TEXT,confidence REAL,trust_tier TEXT,"
            "candidate_count INTEGER,evidence_type TEXT,verification_status TEXT,metadata TEXT)"
        )
        connection.execute(
            "INSERT INTO edges VALUES (1,1,1,'HANDLES_ROUTE','decorator_route',0.95,"
            "'CERTIFIED',1,'decorator_route','verified','route=/users/{id}')"
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(tmp_path, graph, source_revision="source-1")

    assert len(repository.structural_links) == 1
    link = repository.structural_links[0]
    assert link.relation == "HANDLES_ROUTE"
    assert link.route == "/users/{id}"
    assert link.certified is True


def test_source_indexer_persists_route_path_on_handles_route_edge() -> None:
    source = (
        Path(__file__).parents[1]
        / "vendor"
        / "gt-index-src"
        / "internal"
        / "resolver"
        / "relationships.go"
    ).read_text(encoding="utf-8")

    assert '"route="+pendingRoutePath' in source
    assert 'funcID, funcID, "HANDLES_ROUTE"' in source
    assert 'sourceID == targetID && edgeType != "HANDLES_ROUTE"' in source
    assert "Metadata:           metadataValue" in source


@pytest.mark.parametrize(
    ("method", "candidates", "evidence", "verification", "metadata", "origin", "outcome"),
    (
        ("builtin", 1, "builtin", "verified", "", "builtin", "external"),
        ("stdlib", 1, "stdlib", "verified", "", "stdlib", "external"),
        ("import", 1, "third_party", "verified", "", "third_party", "external"),
        ("framework", 1, "framework", "verified", "", "framework", "external"),
        ("global_name", 2, "static", "unverified", "", "program", "ambiguous"),
        ("unresolved", 0, "static", "unverified", "", "program", "unresolved"),
        ("dynamic", 1, "dynamic", "unverified", "", "program", "dynamic"),
        ("verified_unique", 1, "static", "verified", "", "program", "heuristic"),
        ("unique_method", 1, "static", "verified", "", "program", "heuristic"),
        ("re_export", 1, "re_export", "unverified", "", "program", "reexport_unproven"),
    ),
)
def test_resolution_origin_and_unknown_policy_is_terminal(
    method, candidates, evidence, verification, metadata, origin, outcome
):
    assert hybrid_repository._edge_resolution_provenance(
        resolution_method=method,
        candidate_count=candidates,
        evidence_type=evidence,
        verification_status=verification,
        metadata=metadata,
        provenance_available=True,
    ) == (origin, outcome, candidates)


def test_verified_reexport_is_exact_program_evidence() -> None:
    assert hybrid_repository._edge_resolution_provenance(
        resolution_method="re_export",
        candidate_count=1,
        evidence_type="re_export",
        verification_status="verified",
        metadata="",
        provenance_available=True,
    ) == ("program", "exact", 1)


def test_builder_treats_missing_edge_provenance_as_unknown(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def a():\n    return b()\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?)",
            ((1, "a", "src/a.py", 1, 2, "def a()"), (2, "b", "src/b.py", 1, 2, "def b()")),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,confidence REAL,trust_tier TEXT)"
        )
        connection.execute("INSERT INTO edges VALUES (1,1,2,'CALLS',1.0,'CERTIFIED')")
        connection.commit()
    finally:
        connection.close()

    link = build_hybrid_repository(
        tmp_path, graph, source_revision="source-1"
    ).structural_links[0]

    assert link.origin == "unknown"
    assert link.resolution_outcome == "unknown"
    assert link.certified is False


def test_builder_is_bounded_deterministically_and_reports_incomplete(tmp_path):
    (tmp_path / "src").mkdir()
    for name in ("a.py", "b.py"):
        (tmp_path / "src" / name).write_text(f"def {name[0]}():\n    pass\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?)",
            (
                (2, "b", "src/b.py", 1, 2, "def b()"),
                (1, "a", "src/a.py", 1, 2, "def a()"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(
        tmp_path,
        graph,
        source_revision="source-1",
        limits=RepositoryBuildLimits(max_documents=1, max_links=1),
    )

    assert [row.path for row in repository.documents] == ["src/a.py"]
    assert repository.complete is False
    assert "document_limit" in repository.reason_codes


def test_bounded_source_span_remains_retrievable_and_is_not_corpus_failure(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "large.py").write_text(
        "def important_function():\n    return 'a very long implementation body'\n",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.execute(
            "INSERT INTO nodes VALUES (1, 'important_function', 'src/large.py', 1, 2,"
            "'def important_function()')"
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(
        tmp_path,
        graph,
        source_revision="source-1",
        limits=RepositoryBuildLimits(max_chunk_chars=12),
    )

    assert repository.complete is True
    assert "chunk_character_limit" in repository.reason_codes
    assert repository.documents[0].text
    assert "bounded_source_span" in repository.documents[0].provenance


def test_builder_fails_open_on_missing_or_invalid_graph(tmp_path):
    missing = build_hybrid_repository(
        tmp_path,
        tmp_path / "missing.db",
        source_revision="source-1",
    )
    invalid = tmp_path / "invalid.db"
    sqlite3.connect(invalid).close()
    malformed = build_hybrid_repository(
        tmp_path,
        invalid,
        source_revision="source-1",
    )

    assert missing.documents == ()
    assert missing.reason_codes == ("graph_unavailable",)
    assert malformed.documents == ()
    assert malformed.reason_codes == ("nodes_table_unavailable",)


def test_builder_exposes_assertion_closure_and_cochange_relations(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "def service():\n    return helper()\n", encoding="utf-8"
    )
    (tmp_path / "src" / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_service():\n    assert service() == 1\n", encoding="utf-8"
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?)",
            (
                (1, "service", "src/service.py", 1, 2, "def service()"),
                (2, "helper", "src/helper.py", 1, 2, "def helper()"),
                (3, "test_service", "tests/test_service.py", 1, 2, "def test_service()"),
            ),
        )
        connection.execute(
            "CREATE TABLE assertions (id INTEGER PRIMARY KEY,test_node_id INTEGER,"
            "target_node_id INTEGER,resolution_score REAL,kind TEXT,expression TEXT,"
            "expected TEXT,line INTEGER)"
        )
        connection.execute("INSERT INTO assertions VALUES (7,3,1,0.99,'equals','service()','1',2)")
        connection.execute(
            "CREATE TABLE closure (source_id INTEGER,target_id INTEGER,depth INTEGER,"
            "min_confidence REAL)"
        )
        connection.execute("INSERT INTO closure VALUES (1,2,2,0.97)")
        connection.execute("CREATE TABLE cochanges (file_a TEXT,file_b TEXT,count INTEGER)")
        connection.execute("INSERT INTO cochanges VALUES ('src/service.py','src/helper.py',4)")
        connection.execute("CREATE TABLE cochange_sets (commit_hash TEXT,file_path TEXT)")
        connection.executemany(
            "INSERT INTO cochange_sets VALUES (?,?)",
            (
                ("abc", "src/service.py"),
                ("abc", "tests/test_service.py"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    repository = build_hybrid_repository(
        tmp_path,
        graph,
        source_revision="source-1",
    )

    links = {
        (row.source_path, row.target_path, row.relation): row for row in repository.structural_links
    }
    assert links[("src/service.py", "tests/test_service.py", "ASSERTED_BY")].confidence == 0.99
    assert links[("src/service.py", "tests/test_service.py", "ASSERTED_BY")].certified is True
    assert links[("src/service.py", "tests/test_service.py", "ASSERTED_BY")].origin == "program"
    assert (
        links[("src/service.py", "tests/test_service.py", "ASSERTED_BY")].resolution_outcome
        == "exact"
    )
    assert links[("src/service.py", "src/helper.py", "CALLS_TRANSITIVE")].confidence == 0.97
    assert links[("src/service.py", "src/helper.py", "CALLS_TRANSITIVE")].certified is True
    assert links[("src/service.py", "src/helper.py", "COCHANGE")].certified is False
    assert links[("src/service.py", "tests/test_service.py", "COCHANGE_SET")].certified is False


def test_query_builder_materializes_only_bounded_fts_and_structural_candidates(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "allocator.py").write_text(
        "def allocate():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "network.py").write_text(
        "def connect():\n    return True\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_allocator.py").write_text(
        "def test_allocate():\n    assert allocate()\n", encoding="utf-8"
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT,language TEXT,is_test BOOLEAN)"
        )
        rows = (
            (1, "allocate", "src/allocator.py", 1, 2, "def allocate()", "python", 0),
            (2, "connect", "src/network.py", 1, 2, "def connect()", "python", 0),
            (
                3,
                "test_allocate",
                "tests/test_allocator.py",
                1,
                2,
                "def test_allocate()",
                "python",
                1,
            ),
        )
        connection.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)", rows)
        connection.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)")
        connection.executemany(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (?,?,?)",
            ((row[0], row[1], row[2]) for row in rows),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,confidence REAL,trust_tier TEXT)"
        )
        connection.execute("INSERT INTO edges VALUES (1,1,3,'TESTED_BY',0.99,'CERTIFIED')")
        connection.commit()
    finally:
        connection.close()

    repository = build_query_hybrid_repository(
        tmp_path,
        graph,
        RetrievalState(
            task_text="find allocator regression tests",
            intent=RetrievalIntent.VALIDATION_CONTEXT,
            active_paths=("src/allocator.py",),
            source_revision="source-1",
        ),
        candidate_limit=8,
    )

    paths = {row.path for row in repository.documents}
    assert "src/allocator.py" in paths
    assert "tests/test_allocator.py" in paths
    assert "src/network.py" not in paths
    assert any(row.relation == "TESTED_BY" for row in repository.structural_links)


def test_query_builder_uses_test_body_content_only_for_validation_intent(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "format.py").write_text(
        "def render_value(value):\n    return value\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_help.py").write_text(
        "def test_empty_default():\n    assert render_value('') == '\"\"'\n",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT,language TEXT,is_test BOOLEAN)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
            (
                (1, "render_value", "src/format.py", 1, 2, "def render_value(value)", "python", 0),
                (
                    2,
                    "test_empty_default",
                    "tests/test_help.py",
                    1,
                    2,
                    "def test_empty_default()",
                    "python",
                    1,
                ),
            ),
        )
        connection.execute("CREATE VIRTUAL TABLE symbol_content_fts USING fts5(content)")
        connection.executemany(
            "INSERT INTO symbol_content_fts(rowid,content) VALUES (?,?)",
            (
                (1, "render value"),
                (2, "empty default help quote render value"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    validation = build_query_hybrid_repository(
        tmp_path,
        graph,
        RetrievalState(
            task_text="quote empty default values in help output",
            intent=RetrievalIntent.VALIDATION_CONTEXT,
            source_revision="source-1",
        ),
        candidate_limit=8,
    )
    implementation = build_query_hybrid_repository(
        tmp_path,
        graph,
        RetrievalState(
            task_text="quote empty default values in help output",
            intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
            source_revision="source-1",
        ),
        candidate_limit=8,
    )

    assert "tests/test_help.py" in {row.path for row in validation.documents}
    assert "tests/test_help.py" not in {row.path for row in implementation.documents}


def test_query_builder_augments_without_displacing_legacy_graph_candidates(
    tmp_path, monkeypatch
):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "allocator.py").write_text(
        "def allocate():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_allocator.py").write_text(
        "def test_allocate():\n    assert allocate()\n", encoding="utf-8"
    )
    graph = tmp_path / "graph.db"
    _graph(graph)

    calls: list[tuple[str, ...] | None] = []

    def projection(*_args, query_terms=None, **_kwargs):
        calls.append(query_terms)
        node_id = 1 if query_terms is None else 2
        path = "src/allocator.py" if node_id == 1 else "tests/test_allocator.py"
        symbol = "allocate" if node_id == 1 else "test_allocate"
        fact = GraphSemanticFact(
            surface="nodes_fts",
            node_id=node_id,
            file_path=path,
            symbol=symbol,
            kind="ranked_symbol",
            value=symbol,
            line=1,
            confidence=1.0,
            revision="graph-1",
            semantic_certainty=1.0,
        )
        return GraphProjection(
            files=frozenset({path}),
            symbols=frozenset({symbol}),
            node_ids=frozenset({node_id}),
            surface_hits=(("nodes_fts", 1),),
            semantic_facts=(fact,),
            revision="graph-1",
        )

    monkeypatch.setattr(hybrid_repository, "build_graph_projection", projection)

    repository = build_query_hybrid_repository(
        tmp_path,
        graph,
        RetrievalState(
            task_text="find the affected implementation and its regression test",
            intent=RetrievalIntent.VALIDATION_CONTEXT,
            source_revision="source-1",
        ),
        candidate_limit=8,
    )

    assert calls[0] is None
    assert calls[1]
    assert {row.path for row in repository.documents} == {
        "src/allocator.py",
        "tests/test_allocator.py",
    }
