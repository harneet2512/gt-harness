"""Derived-layer context: communities and process participation.

The Go producer (``vendor/gt-index-src``) computes and persists two derived
layers the Python consumer never read:

* ``communities`` + ``community_members`` -- a Leiden partition of the
  repository's files. ``community_members.member`` is a *file path*
  (``member_kind='file'``), joined to a symbol through ``nodes.file_path``.
  ``communities.cohesion`` is NULL on purpose when the held-out cohesion
  could not be measured; ``cohesion_reason`` then says why.
* ``processes`` + ``process_steps`` -- test-witnessed interprocedural
  slices. ``process_steps.stable_id`` is the producer's *effective* stable
  id, ``COALESCE(NULLIF(nodes.stable_id,''), resolution_symbols.stable_id)``
  with ``resolution_symbols.native_id = nodes.id`` -- so a symbol whose
  ``nodes.stable_id`` is NULL still joins, through the resolution table.

Both layers publish their own state under ``project_meta`` keys
``derived_community_state`` / ``derived_process_state``. ``ok`` is the only
state that may serve rows; ``not_run``, ``disabled_by_operator``, the
package reason constants and every failure state all mean the table
contents (if any) are not a published partition. A consumer that cannot
tell "clustered, found nothing" from "never clustered" will fabricate the
second as the first, so the states are surfaced verbatim.

Fixture DDL below is copied verbatim from the producer's ``Schema``
constants (``internal/community/persist.go``, ``internal/process/persist.go``)
and the ``nodes``/``resolution_symbols`` DDL in ``internal/store/sqlite.go``.
Copied rather than imported on purpose: a drift between GT's reader and the
producer's writer must break a test.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gt_engine.derived_context import (
    DERIVED_STATE_OK,
    symbol_derived_context,
)

_NODES_DDL = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    is_test BOOLEAN DEFAULT 0,
    language TEXT NOT NULL,
    stable_id TEXT
);
"""

_RESOLUTION_SYMBOLS_DDL = """
CREATE TABLE resolution_symbols (
    stable_id TEXT PRIMARY KEY,
    native_id TEXT NOT NULL UNIQUE,
    native_kind TEXT NOT NULL,
    normalized_kind TEXT NOT NULL,
    language TEXT NOT NULL,
    path TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    export_status TEXT NOT NULL
);
"""

_ASSERTIONS_DDL = """
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    target_node_id INTEGER,
    expression TEXT,
    line INTEGER,
    resolution_score REAL
);
"""

# Verbatim internal/community/persist.go Schema.
_COMMUNITY_DDL = """
CREATE TABLE IF NOT EXISTS communities (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    heuristic_label TEXT NOT NULL,
    keywords TEXT NOT NULL,
    description TEXT NOT NULL,
    enriched_by TEXT NOT NULL,
    cohesion REAL,
    cohesion_lo REAL,
    cohesion_hi REAL,
    cohesion_n INTEGER NOT NULL DEFAULT 0,
    cohesion_reason TEXT NOT NULL,
    structural_cohesion REAL NOT NULL,
    member_count INTEGER NOT NULL,
    internal_weight REAL NOT NULL,
    external_weight REAL NOT NULL,
    evidence_edge_ids TEXT NOT NULL,
    evidence_truncated INTEGER NOT NULL DEFAULT 0,
    algorithm TEXT NOT NULL,
    resolution REAL NOT NULL,
    w_call REAL NOT NULL,
    w_cochange REAL NOT NULL,
    holdout_commits INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS community_members (
    community_id TEXT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    member TEXT NOT NULL,
    member_kind TEXT NOT NULL DEFAULT 'file',
    PRIMARY KEY(community_id, member)
);
"""

# Verbatim internal/process/persist.go Schema.
_PROCESS_DDL = """
CREATE TABLE IF NOT EXISTS processes (
    id                   TEXT PRIMARY KEY,
    entry_stable_id      TEXT NOT NULL,
    terminal_stable_id   TEXT NOT NULL,
    witness_assertion_id INTEGER NOT NULL REFERENCES assertions(id),
    test_stable_id       TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT '',
    depth                INTEGER NOT NULL,
    trust_floor          TEXT NOT NULL,
    CHECK (depth >= 1),
    CHECK (trust_floor <> '')
);

CREATE TABLE IF NOT EXISTS process_steps (
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    stable_id  TEXT NOT NULL,
    PRIMARY KEY(process_id, ordinal)
);
"""

_META_DDL = "CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)"

_COMMUNITY_ROW = (
    "comm-1",          # id
    "parser pipeline",  # label
    "src/parser",      # heuristic_label
    '["parser","ast"]',  # keywords
    "",                # description
    "heuristic",       # enriched_by
    0.82,              # cohesion
    0.5,               # cohesion_lo
    1.0,               # cohesion_hi
    4,                 # cohesion_n
    "",                # cohesion_reason
    0.9,               # structural_cohesion
    3,                 # member_count
    12.0,              # internal_weight
    2.0,               # external_weight
    "[]",              # evidence_edge_ids
    0,                 # evidence_truncated
    "leiden",          # algorithm
    1.0,               # resolution
    1.0,               # w_call
    1.0,               # w_cochange
    0,                 # holdout_commits
)

_COMMUNITY_MEMBERS = (
    ("comm-1", "src/parser.py", "file"),
    ("comm-1", "src/lexer.py", "file"),
    ("comm-1", "src/ast.py", "file"),
)

# node 1 has stable_id NULL, like every source-level Function/Method/Class on
# a real graph; its effective stable id arrives via resolution_symbols.
_NODE_ROWS = (
    (1, "Function", "parse", "pkg.parser.parse", "src/parser.py",
     10, 30, "def parse(src)", 0, "python", None),
    (2, "Function", "emit", "pkg.emitter.emit", "src/emitter.py",
     5, 20, "def emit(tree)", 0, "python", "sid-emit"),
    (3, "Function", "test_parse", "tests.test_parser.test_parse",
     "tests/test_parser.py", 1, 15, "def test_parse()", 1, "python", None),
)

_RESOLUTION_SYMBOL_ROWS = (
    ("rs-parse", "1", "Function", "function", "python",
     "src/parser.py", "pkg.parser.parse", 10, 30, "exported"),
    ("rs-test", "3", "Function", "function", "python",
     "tests/test_parser.py", "tests.test_parser.test_parse", 1, 15,
     "exported"),
)

_PROCESS_ROW = (
    "proc-1",   # id
    "rs-parse",  # entry_stable_id
    "sid-emit",  # terminal_stable_id
    1,           # witness_assertion_id -> assertions.id
    "rs-test",   # test_stable_id
    "asserts",   # kind
    2,           # depth
    "CERTIFIED",  # trust_floor
)

_PROCESS_STEPS = (
    ("proc-1", 0, "rs-parse"),
    ("proc-1", 1, "sid-mid"),
    ("proc-1", 2, "sid-emit"),
)

_OK_META = (
    ("derived_community_state", "ok"),
    ("derived_community_count", "1"),
    ("derived_community_members", "3"),
    ("derived_process_state", "ok"),
    ("derived_process_count", "1"),
    ("derived_process_steps", "3"),
)


def _graph(
    path: Path,
    *,
    derived: bool = True,
    meta: tuple[tuple[str, str], ...] = _OK_META,
) -> str:
    con = sqlite3.connect(path)
    try:
        con.executescript(_NODES_DDL + _RESOLUTION_SYMBOLS_DDL + _ASSERTIONS_DDL)
        con.executemany(
            "INSERT INTO nodes(id,label,name,qualified_name,file_path,"
            "start_line,end_line,signature,is_test,language,stable_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            _NODE_ROWS,
        )
        con.executemany(
            "INSERT INTO resolution_symbols(stable_id,native_id,native_kind,"
            "normalized_kind,language,path,qualified_name,start_line,end_line,"
            "export_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            _RESOLUTION_SYMBOL_ROWS,
        )
        con.execute("INSERT INTO assertions(id,kind) VALUES(1,'asserts')")
        if derived:
            con.executescript(_COMMUNITY_DDL + _PROCESS_DDL)
            con.execute(
                "INSERT INTO communities(id,label,heuristic_label,keywords,"
                "description,enriched_by,cohesion,cohesion_lo,cohesion_hi,"
                "cohesion_n,cohesion_reason,structural_cohesion,member_count,"
                "internal_weight,external_weight,evidence_edge_ids,"
                "evidence_truncated,algorithm,resolution,w_call,w_cochange,"
                "holdout_commits) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _COMMUNITY_ROW,
            )
            con.executemany(
                "INSERT INTO community_members(community_id,member,member_kind) "
                "VALUES(?,?,?)",
                _COMMUNITY_MEMBERS,
            )
            con.execute(
                "INSERT INTO processes(id,entry_stable_id,terminal_stable_id,"
                "witness_assertion_id,test_stable_id,kind,depth,trust_floor) "
                "VALUES(?,?,?,?,?,?,?,?)",
                _PROCESS_ROW,
            )
            con.executemany(
                "INSERT INTO process_steps(process_id,ordinal,stable_id) "
                "VALUES(?,?,?)",
                _PROCESS_STEPS,
            )
        con.execute(_META_DDL)
        con.executemany(
            "INSERT INTO project_meta(key,value) VALUES(?,?)", meta
        )
        con.commit()
    finally:
        con.close()
    return str(path)


def _with_fts(db: str) -> str:
    """Attach the lexical surface so the composite read path can rank."""
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE nodes_fts USING fts5("
            "name, qualified_name, signature, file_path)"
        )
        con.executemany(
            "INSERT INTO nodes_fts(rowid,name,qualified_name,signature,"
            "file_path) VALUES(?,?,?,?,?)",
            [
                (row[0], row[2], row[3], row[6], row[4]) for row in _NODE_ROWS
            ],
        )
        con.commit()
    finally:
        con.close()
    return db


# --- the primitive reader ---------------------------------------------------


def test_symbol_context_reports_community_and_process_participation(tmp_path):
    """A queried symbol surfaces its community and its witnessed process."""
    db = _graph(tmp_path / "g.db")

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is not None
    assert ctx.community.name == "parser pipeline"
    assert ctx.community.cohesion == pytest.approx(0.82)
    assert ctx.community.member_count == 3
    assert ctx.as_dict()["community"] == {
        "name": "parser pipeline",
        "cohesion": pytest.approx(0.82),
        "member_count": 3,
    }
    assert len(ctx.processes) == 1
    participation = ctx.processes[0]
    assert participation.name == "pkg.parser.parse"
    assert participation.step_index == 0
    assert participation.step_count == 3
    assert ctx.as_dict()["processes"] == [
        {"name": "pkg.parser.parse", "step_count": 3, "step_index": 0}
    ]
    assert ctx.states["community"] == DERIVED_STATE_OK
    assert ctx.states["process"] == DERIVED_STATE_OK


def test_process_join_uses_the_producer_effective_stable_id(tmp_path):
    """nodes.stable_id is NULL for source symbols; the join must not need it."""
    db = _graph(tmp_path / "g.db")

    # node 1: stable_id NULL in nodes, resolved via resolution_symbols.
    by_node = symbol_derived_context(db, node_id=1)
    # node 2: stable_id stored directly on the node.
    stored = symbol_derived_context(db, node_id=2)
    # a caller that already knows the stable id needs no node at all.
    by_stable_id = symbol_derived_context(
        db, stable_id="rs-parse", file_path="src/parser.py"
    )

    assert [p.step_index for p in by_node.processes] == [0]
    assert [p.step_index for p in stored.processes] == [2]
    assert by_stable_id.processes == by_node.processes
    assert by_stable_id.community == by_node.community


def test_a_symbol_outside_every_partition_reports_typed_empty(tmp_path):
    """A member of no community and no process is empty, not absent."""
    db = _graph(tmp_path / "g.db")

    ctx = symbol_derived_context(db, node_id=3, file_path="tests/test_parser.py")

    assert ctx.community is None
    assert ctx.processes == ()
    assert ctx.states["community"] == DERIVED_STATE_OK
    assert ctx.states["process"] == DERIVED_STATE_OK
    assert ctx.as_dict() == {
        "community": None,
        "processes": [],
        "derived_states": {"community": "ok", "process": "ok"},
    }


def test_a_null_cohesion_is_reported_not_fabricated(tmp_path):
    """NULL cohesion means unmeasurable, never 0.0."""
    db = _graph(tmp_path / "g.db")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE communities SET cohesion=NULL, cohesion_reason=? "
            "WHERE id='comm-1'",
            ("holdout_window_empty",),
        )
        con.commit()
    finally:
        con.close()

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is not None
    assert ctx.community.cohesion is None
    assert ctx.community.cohesion_reason == "holdout_window_empty"
    assert ctx.as_dict()["community"]["cohesion"] is None


# --- the freshness gate ------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["not_run", "disabled_by_operator", "no_certified_call_pairs"]
)
def test_a_degraded_community_state_serves_no_partition(tmp_path, state):
    """Rows under a non-ok state are not a published partition."""
    meta = tuple(
        (key, state if key == "derived_community_state" else value)
        for key, value in _OK_META
    )
    db = _graph(tmp_path / "g.db", meta=meta)

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is None
    assert ctx.states["community"] == state
    # The process layer's own state is unaffected by the community layer.
    assert len(ctx.processes) == 1
    assert ctx.states["process"] == "ok"


def test_a_degraded_process_state_serves_no_participation(tmp_path):
    meta = tuple(
        (key, "derive_failed" if key == "derived_process_state" else value)
        for key, value in _OK_META
    )
    db = _graph(tmp_path / "g.db", meta=meta)

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.processes == ()
    assert ctx.states["process"] == "derive_failed"
    assert ctx.community is not None


def test_unrecorded_states_never_fabricate(tmp_path):
    """A graph that predates the receipt keys serves nothing derived."""
    db = _graph(tmp_path / "g.db", meta=(("unrelated_key", "1"),))

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is None
    assert ctx.processes == ()
    assert ctx.states["community"] == "unrecorded"
    assert ctx.states["process"] == "unrecorded"


def test_a_recorded_count_mismatch_is_stale_not_a_partition(tmp_path):
    """The count markers are the derived equivalent of closure_count."""
    meta = tuple(
        (key, "9" if key == "derived_community_count" else value)
        for key, value in _OK_META
    )
    db = _graph(tmp_path / "g.db", meta=meta)

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is None
    assert ctx.states["community"] == "count_mismatch"


def test_absent_derived_tables_are_quiet(tmp_path):
    db = _graph(tmp_path / "g.db", derived=False)

    ctx = symbol_derived_context(db, node_id=1)

    assert ctx.community is None
    assert ctx.processes == ()
    assert ctx.states["community"] == "table_absent"
    assert ctx.states["process"] == "table_absent"


def test_an_absent_graph_is_quiet(tmp_path):
    ctx = symbol_derived_context(
        str(tmp_path / "nope.db"), node_id=1, file_path="src/parser.py"
    )

    assert ctx.community is None
    assert ctx.processes == ()
    assert ctx.states["community"] == "graph_absent"


def test_no_identity_at_all_is_quiet(tmp_path):
    db = _graph(tmp_path / "g.db")

    ctx = symbol_derived_context(db)

    assert ctx.community is None
    assert ctx.processes == ()


# --- the composite read path -------------------------------------------------


def test_hybrid_rank_attribution_carries_community_and_processes(tmp_path):
    """The fused symbol row gains its derived context on the existing wire."""
    from gt_engine.retrieval import hybrid_rank

    db = _with_fts(_graph(tmp_path / "g.db"))

    ranking = hybrid_rank(db, "parse emit", k=4, use_dense=False)
    record = ranking.attribution_record()

    rows = {
        row["stable_id"]: row
        for row in record["fused"]
        if row.get("provenance")
    }
    parse_row = next(
        row for row in rows.values()
        if row["provenance"]["file_path"] == "src/parser.py"
    )
    emit_row = next(
        row for row in rows.values()
        if row["provenance"]["file_path"] == "src/emitter.py"
    )
    assert parse_row["community"] == {
        "name": "parser pipeline",
        "cohesion": pytest.approx(0.82),
        "member_count": 3,
    }
    assert parse_row["processes"] == [
        {"name": "pkg.parser.parse", "step_count": 3, "step_index": 0}
    ]
    assert emit_row["community"] is None
    assert emit_row["processes"] == [
        {"name": "pkg.parser.parse", "step_count": 3, "step_index": 2}
    ]
    assert record["derived_states"] == {"community": "ok", "process": "ok"}


def test_hybrid_rank_derived_fields_are_empty_when_layers_not_run(tmp_path):
    """Typed empty fields, not a fabricated partition."""
    from gt_engine.retrieval import hybrid_rank

    meta = tuple(
        (key, "not_run" if key.endswith("_state") else value)
        for key, value in _OK_META
    )
    db = _with_fts(_graph(tmp_path / "g.db", meta=meta))

    record = hybrid_rank(db, "parse", k=4, use_dense=False).attribution_record()

    parse_row = next(
        row for row in record["fused"]
        if (row.get("provenance") or {}).get("file_path") == "src/parser.py"
    )
    assert parse_row["community"] is None
    assert parse_row["processes"] == []
    assert record["derived_states"] == {
        "community": "not_run",
        "process": "not_run",
    }


# --- the localization surface -------------------------------------------------


def _contract(*terms: str):
    from gt_engine.task_contract import Obligation, TaskContract

    return TaskContract(
        "code_behavior",
        (
            Obligation(
                "obl-1",
                "Fix " + " ".join(terms) + " behaviour.",
                "task",
                subjects=terms,
            ),
        ),
    )


def test_projection_groups_localized_files_by_community(tmp_path):
    """Community peers of a localized file join the work surface, grouped."""
    from gt_engine.graph_context import build_graph_projection

    db = _with_fts(_graph(tmp_path / "g.db"))

    projection = build_graph_projection(db, _contract("parse"), limit=8)

    # The localized file's community peers are pulled into the file set.
    assert "src/parser.py" in projection.files
    assert "src/lexer.py" in projection.files
    assert "src/ast.py" in projection.files
    community_facts = [
        fact for fact in projection.semantic_facts
        if fact.surface == "communities"
    ]
    assert community_facts
    fact = community_facts[0]
    assert fact.file_path == "src/parser.py"
    assert "parser pipeline" in fact.value
    assert fact.revision == projection.revision


def test_projection_emits_process_participation_facts(tmp_path):
    from gt_engine.graph_context import build_graph_projection

    db = _with_fts(_graph(tmp_path / "g.db"))

    projection = build_graph_projection(db, _contract("parse"), limit=8)

    process_facts = [
        fact for fact in projection.semantic_facts
        if fact.surface == "processes"
    ]
    assert process_facts
    parse_fact = next(f for f in process_facts if f.symbol == "parse")
    assert "pkg.parser.parse" in parse_fact.value
    assert "step 1/3" in parse_fact.value


def test_projection_skips_derived_layers_when_not_published(tmp_path):
    """No published state -> no community grouping and no process facts."""
    from gt_engine.graph_context import build_graph_projection

    db = _with_fts(_graph(tmp_path / "g.db", meta=()))

    projection = build_graph_projection(db, _contract("parse"), limit=8)

    assert "src/parser.py" in projection.files
    assert "src/lexer.py" not in projection.files
    assert not [
        fact for fact in projection.semantic_facts
        if fact.surface in ("communities", "processes")
    ]


def test_projection_records_the_derived_states_it_read(tmp_path):
    from gt_engine.graph_context import build_graph_projection

    db = _with_fts(_graph(tmp_path / "g.db"))

    projection = build_graph_projection(db, _contract("parse"), limit=8)

    assert dict(projection.derived_states) == {
        "community": "ok",
        "process": "ok",
    }


def test_projection_surfaces_route_api_edges_for_localized_files(tmp_path):
    """The producer publishes HANDLES_ROUTE / API_CALL edges into graph.db, but
    no consumer read them into push context — a model editing a route handler
    or an API client had to query a tool for the API surface. The projection
    must surface them as semantic facts for in-scope files.
    """
    import sqlite3

    from gt_engine.graph_context import build_graph_projection

    db = _with_fts(_graph(tmp_path / "g.db"))
    con = sqlite3.connect(db)
    try:
        con.executescript(
            "CREATE TABLE edges ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER,"
            " target_id INTEGER, type TEXT NOT NULL, source_line INTEGER,"
            " confidence REAL, resolution_method TEXT);"
            # a route file node the localized handler routes to
            "INSERT INTO nodes(id,label,name,qualified_name,file_path,"
            " start_line,end_line,signature,is_test,language,stable_id) "
            "VALUES(4,'File','api','src/api.py','src/api.py',1,40,'',0,"
            "'python',NULL);"
            # parse() is localized; it is the route handler for src/api.py
            "INSERT INTO edges(source_id,target_id,type,source_line,"
            "confidence,resolution_method) "
            "VALUES(1,4,'HANDLES_ROUTE',12,0.95,'decorator_route');"
            # and a client call into the same route file
            "INSERT INTO edges(source_id,target_id,type,source_line,"
            "confidence,resolution_method) "
            "VALUES(2,4,'API_CALL',8,0.9,'literal_path');"
        )
        con.commit()
    finally:
        con.close()

    projection = build_graph_projection(db, _contract("parse"), limit=8)

    route_facts = [
        fact for fact in projection.semantic_facts if fact.surface == "routes"
    ]
    assert route_facts, "HANDLES_ROUTE/API_CALL edges must reach the projection"
    kinds = {fact.kind for fact in route_facts}
    assert "route_handler" in kinds
    assert "api_call" in kinds
    handler = next(f for f in route_facts if f.kind == "route_handler")
    assert handler.file_path == "src/parser.py"
    assert "HANDLES_ROUTE" in handler.value
    assert "src/api.py" in handler.value
    assert handler.confidence == 0.95
