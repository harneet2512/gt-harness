"""Determinism: the same fixture indexed twice must answer identically.

Two independent copies of ``fixtures/polyglot`` are indexed into two
databases under *different* absolute roots. The producer must then emit
identical graph content — nodes, edges, assertions, derived processes —
and every certified typed kind must return a byte-identical compiled
observation, modulo the fields that legitimately name the build location
(absolute paths and hash chains derived from them).

The comparison is content-level, not file-level: SQLite freelist and page
layout are allowed to differ; the *rows* are what carry meaning. Per-run
wall-clock columns that do exist (``file_hashes.indexed_at``) are compared
with that column dropped and named — never silently ignored.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("groundtruth.runtime.deterministic_queries")

from gt_engine.miniswe_typed_actions import (  # noqa: E402
    QUERY_RESULT_MAX_BYTES,
    execute_typed_action_fail_open,
)
from tests.canonical.conftest import (  # noqa: E402
    FIXTURE_REVISION,
    CanonWorkspace,
    assert_graphs_isomorphic,
    canon_json,
    dump_table,
    producer_candidates,
    scrub_payload,
)

_PRODUCER_MAY_EXIST = any(
    Path(candidate).is_file() for candidate in producer_candidates()
) or os.environ.get("GT_INDEX_BINARY")

pytestmark = pytest.mark.skipif(
    not _PRODUCER_MAY_EXIST,
    reason="no gt-index producer candidate exists on this host",
)

# Tables whose content must be isomorphic across builds. Derived tables
# (closure, communities, processes, resolution tables) are included: a
# deterministic producer derives deterministic derivations. FTS5 shadow
# tables, file_hashes (has a per-run indexed_at column — covered by its
# own test) and project_meta (embeds the absolute -root — covered by its
# own test) are handled separately.
_CONTENT_TABLES = (
    "nodes",
    "edges",
    "assertions",
    "closure",
    "cochanges",
    "communities",
    "community_members",
    "processes",
    "process_steps",
    "cfg_blocks",
    "cfg_defs",
    "cfg_edges",
    "cfg_uses",
    "properties",
    "resolution_callsites",
    "resolution_candidates",
    "resolution_symbols",
    "parser_assertion_inventory",
    "parser_edge_inventory",
    "parser_node_inventory",
    "parser_property_inventory",
)

_TYPED_CASES = (
    ("definition", {"symbol": "sanitize"}),
    ("references", {"symbol": "sanitize"}),
    ("callers", {"symbol": "execute", "depth": 2}),
    ("symbol_context", {"symbol": "Friendly", "language": "typescript"}),
    ("route_map", {}),
    ("api_impact", {"route": "/api/items"}),
    ("taint", {"source": "list_items", "sink": "execute", "depth": 4}),
    ("processes", {}),
    ("slice", {"symbol": "list_items", "line": 83}),
    ("shape_check", {"symbol": "Friendly", "language": "typescript"}),
    ("tool_map", {}),
    ("exact_literal_search", {"literal": "Depends(get_store)", "paths": ["pyapp"]}),
    ("syntax", {"path": "pyapp/server.py"}),
)


def test_produced_graphs_are_isomorphic(polyglot_pair):
    """nodes/edges and every derived table: identical content, and every
    location-scoped id related by one consistent bijection."""
    ws_a, ws_b = polyglot_pair
    id_map = assert_graphs_isomorphic(ws_a.graph, ws_b.graph, _CONTENT_TABLES)
    # A real graph relabeled real identities — the map must be nonempty.
    assert id_map


def _typed_output(
    workspace: CanonWorkspace, kind: str, arguments: dict[str, Any]
) -> tuple[dict, str]:
    _request, result = execute_typed_action_fail_open(
        {
            "tool_name": "groundtruth",
            "tool_call_id": f"canon-determinism:{kind}",
            "gt_action": {"kind": kind, "arguments": dict(arguments)},
        },
        repo_root=workspace.root,
        configuration={
            "configuration_id": "canon-polyglot",
            "graph_db": str(workspace.graph),
            "graph_source_revision": FIXTURE_REVISION,
        },
    )
    assert len(result["output"].encode("utf-8")) <= QUERY_RESULT_MAX_BYTES
    scrubbed = scrub_payload(json.loads(result["output"]), workspace)
    return result, canon_json(scrubbed)


def test_project_meta_is_identical_modulo_paths(polyglot_pair):
    """Key/value receipt: canonically ordered by key; only `root` (the
    absolute -root path) may differ."""
    ws_a, ws_b = polyglot_pair
    def meta(ws):
        rows = sorted(
            dump_table(ws.graph, "project_meta"), key=lambda r: r["key"]
        )
        return canon_json(scrub_payload(rows, ws))
    assert meta(ws_a) == meta(ws_b)


def test_file_hashes_identical_except_index_timestamps(polyglot_pair):
    """The only sanctioned per-run difference: when the row was written.

    Path, content sha256 and language must still be identical — the
    timestamp column is dropped explicitly, never ignored silently.
    """
    ws_a, ws_b = polyglot_pair
    rows = []
    for ws in (ws_a, ws_b):
        with sqlite3.connect(
            f"file:{ws.graph.as_posix()}?mode=ro", uri=True
        ) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(file_hashes)")]
            keep = [c for c in cols if c != "indexed_at"]
            data = conn.execute(
                f"SELECT {', '.join(keep)} FROM file_hashes ORDER BY file_path"
            ).fetchall()
            rows.append(canon_json(data))
    assert rows[0] == rows[1]


def test_typed_answers_are_byte_identical(polyglot_pair):
    ws_a, ws_b = polyglot_pair
    assert ws_a.root != ws_b.root  # the whole point: distinct build roots
    for kind, arguments in _TYPED_CASES:
        result_a, canon_a = _typed_output(ws_a, kind, arguments)
        result_b, canon_b = _typed_output(ws_b, kind, arguments)
        assert result_a["returncode"] == result_b["returncode"], kind
        assert canon_a == canon_b, f"typed answer diverged for kind {kind}"


def test_index_receipt_is_deterministic(polyglot_pair):
    """The producer stamps the same build identity into both graphs."""
    ws_a, ws_b = polyglot_pair
    for ws in (ws_a, ws_b):
        with sqlite3.connect(
            f"file:{ws.graph.as_posix()}?mode=ro", uri=True
        ) as conn:
            meta = dict(
                conn.execute("SELECT key, value FROM project_meta").fetchall()
            )
        # The build identity is the binary's, so identical across builds.
        assert meta["graph_producer_build_id"] == ws.build_info["build_id"]
        assert meta["source_revision"] == FIXTURE_REVISION
