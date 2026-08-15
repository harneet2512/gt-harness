from __future__ import annotations

import sqlite3
from pathlib import Path

from gt_engine.graph_context import build_graph_projection
from gt_engine.graph_evidence import build_evidence_need, rank_graph_evidence
from gt_engine.task_contract import Obligation, TaskContract


def _contract(text: str = "Repair needle behavior") -> TaskContract:
    return TaskContract(
        role="implementation",
        obligations=(
            Obligation(
                obligation_id="o1",
                text=text,
                source="instruction",
                subjects=("needle",),
            ),
        ),
    )


def _base_graph(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE nodes ("
        "id INTEGER PRIMARY KEY,label TEXT,name TEXT,qualified_name TEXT,"
        "file_path TEXT,start_line INTEGER,end_line INTEGER,signature TEXT,"
        "language TEXT,is_test INTEGER)"
    )
    connection.execute(
        "CREATE TABLE edges ("
        "id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,type TEXT,"
        "source_line INTEGER,source_file TEXT,resolution_method TEXT,"
        "confidence REAL,metadata TEXT,trust_tier TEXT,candidate_count INTEGER,"
        "evidence_type TEXT,verification_status TEXT)"
    )
    connection.execute(
        "CREATE TABLE assertions ("
        "id INTEGER PRIMARY KEY,test_node_id INTEGER,target_node_id INTEGER,"
        "resolution_score REAL,kind TEXT,expression TEXT,expected TEXT,line INTEGER)"
    )
    connection.execute(
        "CREATE TABLE properties ("
        "id INTEGER PRIMARY KEY,node_id INTEGER,kind TEXT,value TEXT,"
        "line INTEGER,confidence REAL)"
    )
    connection.execute(
        "CREATE TABLE cochanges (file_a TEXT,file_b TEXT,count INTEGER)"
    )
    connection.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)")
    return connection


def test_projection_preserves_fts_rank_instead_of_sorting_by_node_id(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    connection = _base_graph(graph)
    try:
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "function",
                    "needle_misc",
                    "needle_misc",
                    "src/misc.py",
                    1,
                    2,
                    "def needle_misc()",
                    "python",
                    0,
                ),
                (
                    99,
                    "function",
                    "needle",
                    "needle",
                    "src/target.py",
                    4,
                    8,
                    "def needle()",
                    "python",
                    0,
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (?,?,?)",
            ((1, "needle misc unrelated tokens", "src/misc.py"), (99, "needle", "src/target.py")),
        )
        connection.commit()
    finally:
        connection.close()

    projection = build_graph_projection(str(graph), _contract(), limit=10)
    fts_ids = [
        fact.node_id for fact in projection.semantic_facts if fact.surface == "nodes_fts"
    ]

    assert fts_ids[:2] == [99, 1]


def test_projection_emits_test_graph_neighbor_as_semantic_candidate(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    connection = _base_graph(graph)
    try:
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "function",
                    "needle",
                    "needle",
                    "src/service.py",
                    5,
                    9,
                    "def needle()",
                    "python",
                    0,
                ),
                (
                    2,
                    "function",
                    "test_needle",
                    "test_needle",
                    "tests/test_service.py",
                    10,
                    14,
                    "def test_needle()",
                    "python",
                    1,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (1,'needle','src/service.py')"
        )
        connection.execute(
            "INSERT INTO edges VALUES (1,2,1,'CALLS',11,'tests/test_service.py',"
            "'parser',0.99,'','CERTIFIED',1,'ast','verified')"
        )
        connection.commit()
    finally:
        connection.close()

    projection = build_graph_projection(str(graph), _contract(), limit=10)
    relation_facts = [fact for fact in projection.semantic_facts if fact.surface == "edges"]

    assert any(fact.file_path == "tests/test_service.py" for fact in relation_facts)
    assert any(fact.kind == "CALLS" and fact.semantic_certainty >= 0.95 for fact in relation_facts)

    need = build_evidence_need(
        _contract(),
        projection,
        boundary="post_edit",
        active_paths=("src/service.py",),
    )
    ranked = rank_graph_evidence(_contract(), projection, need, limit=10)
    assert any(item.file_path == "tests/test_service.py" for item in ranked)


def test_projection_assertion_names_the_test_node_not_only_target(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    connection = _base_graph(graph)
    try:
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "function",
                    "needle",
                    "needle",
                    "src/service.py",
                    5,
                    9,
                    "def needle()",
                    "python",
                    0,
                ),
                (
                    2,
                    "function",
                    "test_needle",
                    "test_needle",
                    "tests/test_service.py",
                    20,
                    24,
                    "def test_needle()",
                    "python",
                    1,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (1,'needle','src/service.py')"
        )
        connection.execute(
            "INSERT INTO assertions VALUES (1,2,1,0.99,'assert','needle() == 1','1',22)"
        )
        connection.commit()
    finally:
        connection.close()

    projection = build_graph_projection(str(graph), _contract(), limit=10)
    assertion_facts = [
        fact for fact in projection.semantic_facts if fact.surface == "assertions"
    ]

    assert [(fact.node_id, fact.file_path) for fact in assertion_facts] == [
        (2, "tests/test_service.py")
    ]
    assert assertion_facts[0].value.endswith("[target:src/service.py]")


def test_projection_property_value_does_not_depend_on_assertion_target(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    connection = _base_graph(graph)
    try:
        connection.execute(
            "INSERT INTO nodes VALUES (1,'function','needle','needle','src/service.py',5,9,"
            "'def needle()','python',0)"
        )
        connection.execute(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (1,'needle','src/service.py')"
        )
        connection.execute(
            "INSERT INTO properties VALUES (1,1,'signature','def needle()',5,0.99)"
        )
        connection.commit()
    finally:
        connection.close()

    projection = build_graph_projection(str(graph), _contract(), limit=10)
    property_facts = [
        fact for fact in projection.semantic_facts if fact.surface == "properties"
    ]

    assert len(property_facts) == 1
    assert property_facts[0].value == "def needle()"


def test_projection_emits_cochange_partner_as_semantic_candidate(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    connection = _base_graph(graph)
    try:
        connection.execute(
            "INSERT INTO nodes VALUES (1,'function','needle','needle','src/service.py',5,9,"
            "'def needle()','python',0)"
        )
        connection.execute(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (1,'needle','src/service.py')"
        )
        connection.execute(
            "INSERT INTO cochanges VALUES ('src/service.py','src/consumer.py',7)"
        )
        connection.commit()
    finally:
        connection.close()

    projection = build_graph_projection(str(graph), _contract(), limit=10)
    cochange_facts = [fact for fact in projection.semantic_facts if fact.surface == "cochanges"]

    assert any(fact.file_path == "src/consumer.py" for fact in cochange_facts)
