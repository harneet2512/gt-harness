from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gt_engine import contract
from gt_engine.contract import (
    CONTRACT_SCHEMA,
    DERIVED_STABLE_ID_PREFIX,
    canonical_json,
    contract_digest,
    contracts,
    coverage,
    symbol_contract,
)

# The real arktype graph. Every integration assertion below is skipped rather
# than weakened when it is absent, so a missing graph can never look like a pass.
ARKTYPE_GRAPH = Path(
    "D:/tmp/claude/D--gt-harness/d4578d92-0fad-4131-b9ed-3ade34ece4fc/scratchpad/ark-new.db"
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
    return_type TEXT,
    is_exported BOOLEAN DEFAULT 0,
    is_test BOOLEAN DEFAULT 0,
    stable_id TEXT
)
"""

_PROPERTIES_DDL = """
CREATE TABLE properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    line INTEGER,
    confidence REAL DEFAULT 1.0
)
"""

# Node 1 is a fully described function, node 2 a silent one, node 3 a class that
# only carries facts outside the contract vocabulary. Every property value is a
# verbatim form taken from the arktype graph, so the parsers are exercised
# against the producer's real output rather than an idealised one.
_NODES = [
    (1, "Function", "assertEqual", "assert.assertEqual", "src/assert.ts", 40, 62, "boolean", None),
    (2, "Function", "noop", "util.noop", "src/util.ts", 5, 6, None, None),
    (3, "Class", "Traversal", "ctx.Traversal", "src/ctx.ts", 10, 90, None, "gt:producer:minted"),
]

_PROPERTIES = [
    # (id, node_id, kind, value, line, confidence)
    (1, 1, "guard_clause", "return: (!result) -> {", 44, 1.0),
    (2, 1, "param", "expected:: unknown [required]", 40, 1.0),
    (3, 1, "param", "fractionDigits opt=2", 40, 1.0),
    (4, 1, "param", "actual [required]", 40, 1.0),
    (5, 1, "return_shape", "value|result", 60, 0.9),
    (6, 1, "boundary_condition", "null_check|mapped !== null => {", 50, 0.9),
    (7, 1, "side_effect", "mutates: this.ctx", 55, 1.0),
    (8, 1, "data_flow", "ctx -> ctx.$ | ctx.$.parseOwn(expected)", 47, 0.8),
    (9, 1, "visibility", "exported", 40, 1.0),
    (10, 1, "return_shape", "none", None, 0.9),
    # Not a contract kind. Present so the projection is shown to ignore it.
    (11, 1, "fingerprint", "a1b2c3", 40, 1.0),
    (12, 3, "caller_usage", "Traversal used by parse", 10, 1.0),
    (13, 3, "fingerprint", "d4e5f6", 10, 1.0),
]


def _build_graph(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute(_NODES_DDL)
        connection.execute(_PROPERTIES_DDL)
        connection.executemany(
            "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
            " start_line, end_line, return_type, stable_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _NODES,
        )
        connection.executemany(
            "INSERT INTO properties (id, node_id, kind, value, line, confidence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            _PROPERTIES,
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def graph(tmp_path: Path) -> Path:
    return _build_graph(tmp_path / "synthetic.db")


def test_the_same_graph_yields_byte_identical_json(graph: Path):
    """Requirement 1. Prose cannot promise this; a projection can."""

    first = canonical_json(list(contracts(graph))).encode("utf-8")
    second = canonical_json(list(contracts(graph))).encode("utf-8")

    assert first == second
    assert contract_digest(symbol_contract(graph, 1)) == contract_digest(
        symbol_contract(graph, 1)
    )


def test_facts_are_ordered_by_line_then_id_with_null_lines_last(graph: Path):
    """Ordering has to be total, or 'byte-identical' is luck rather than design."""

    contract = symbol_contract(graph, 1)

    assert [fact["line"] for fact in contract["params"]] == [40, 40, 40]
    assert [fact["property_id"] for fact in contract["params"]] == [2, 3, 4]
    # Property 10 has a null line and must sort behind property 5, which has one.
    assert [fact["property_id"] for fact in contract["returns"]["shapes"]] == [5, 10]


def test_a_symbol_with_no_facts_yields_an_explicitly_empty_contract(graph: Path):
    """Requirement 2. Silence must be reported as silence."""

    contract = symbol_contract(graph, 2)

    assert contract["provenance"]["fact_count"] == 0
    assert contract["provenance"]["property_ids"] == []
    assert contract["params"] == []
    assert contract["guards"] == []
    assert contract["side_effects"] == []
    assert contract["boundaries"] == []
    assert contract["data_flow"] == []
    assert contract["returns"] == {"declared_type": None, "shapes": []}
    assert contract["visibility"] is None
    assert contract["symbol"]["qualified_name"] == "util.noop"


def test_non_contract_facts_do_not_manufacture_a_contract(graph: Path):
    """A symbol with only `caller_usage` and `fingerprint` rows has no contract."""

    contract = symbol_contract(graph, 3)

    assert contract["provenance"]["fact_count"] == 0
    assert contract["provenance"]["property_ids"] == []


def test_every_projected_claim_carries_its_source_property_id(graph: Path):
    """Requirement: a consumer can trace any line of a contract back to a row."""

    contract = symbol_contract(graph, 1)

    projected_ids = sorted(
        fact["property_id"]
        for field in ("params", "guards", "side_effects", "boundaries", "data_flow")
        for fact in contract[field]
    )
    projected_ids += [fact["property_id"] for fact in contract["returns"]["shapes"]]
    projected_ids.append(contract["visibility"]["property_id"])

    assert sorted(projected_ids) == contract["provenance"]["property_ids"]
    assert contract["provenance"]["fact_count"] == len(contract["provenance"]["property_ids"])
    # The `fingerprint` row is a real fact but not a contract field.
    assert 11 not in contract["provenance"]["property_ids"]
    assert contract["provenance"]["schema"] == CONTRACT_SCHEMA


def test_producer_value_formats_are_split_without_losing_the_raw_value(graph: Path):
    """Each parser is a split of stored text, so the original must survive it."""

    contract = symbol_contract(graph, 1)
    required, optional, untyped = contract["params"]

    assert (required["name"], required["type"], required["required"]) == (
        "expected", "unknown", True,
    )
    assert (optional["name"], optional["required"], optional["default"]) == ("fractionDigits", False, "2")
    assert (untyped["name"], untyped["type"], untyped["required"]) == ("actual", None, True)
    assert untyped["value"] == "actual [required]"

    guard = contract["guards"][0]
    assert (guard["action"], guard["condition"], guard["effect"]) == ("return", "(!result)", "{")

    boundary = contract["boundaries"][0]
    assert (boundary["check"], boundary["expression"]) == ("null_check", "mapped !== null => {")

    effect = contract["side_effects"][0]
    assert (effect["effect"], effect["target"]) == ("mutates", "this.ctx")

    flow = contract["data_flow"][0]
    assert flow["source"] == "ctx"
    assert flow["sinks"] == ["ctx.$", "ctx.$.parseOwn(expected)"]

    shape, bare = contract["returns"]["shapes"]
    assert (shape["shape"], shape["detail"]) == ("value", "result")
    assert (bare["shape"], bare["detail"]) == ("none", None)

    assert contract["visibility"]["value"] == "exported"


def test_the_declared_return_type_comes_from_the_node_row(graph: Path):
    """`nodes.return_type` is stored, so it belongs in the contract -- but it is
    not a property row, so it contributes no property id."""

    contract = symbol_contract(graph, 1)

    assert contract["returns"]["declared_type"] == "boolean"
    assert contract["provenance"]["fact_count"] == 10


def test_a_missing_producer_stable_id_falls_back_to_a_marked_derived_id(graph: Path):
    """All 3,511 arktype code symbols have a null `stable_id`, so the fallback is
    the normal path -- and it must be visibly distinguishable from a minted id."""

    derived = symbol_contract(graph, 1)["symbol"]["stable_id"]
    minted = symbol_contract(graph, 3)["symbol"]["stable_id"]

    assert derived.startswith(DERIVED_STABLE_ID_PREFIX)
    assert minted == "gt:producer:minted"


def test_the_derived_stable_id_survives_relocation(tmp_path: Path):
    """A moved but unchanged symbol must keep its id, or cross-commit diffing of
    contracts degenerates into diffing line numbers."""

    moved = [
        (1, "Function", "assertEqual", "assert.assertEqual", "src/assert.ts", 400, 422, "boolean", None),
    ]
    path = tmp_path / "moved.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(_NODES_DDL)
        connection.execute(_PROPERTIES_DDL)
        connection.executemany(
            "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
            " start_line, end_line, return_type, stable_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            moved,
        )
        connection.commit()
    finally:
        connection.close()

    original = _build_graph(tmp_path / "original.db")

    assert (
        symbol_contract(path, 1)["symbol"]["stable_id"]
        == symbol_contract(original, 1)["symbol"]["stable_id"]
    )


def test_the_digest_changes_when_a_fact_changes(graph: Path):
    """Requirement 4. A digest nobody can move is not a change detector."""

    before = contract_digest(symbol_contract(graph, 1))
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "UPDATE properties SET value = ? WHERE id = 1",
            ("return: (!result) -> throw",),
        )
        connection.commit()
    finally:
        connection.close()

    assert contract_digest(symbol_contract(graph, 1)) != before


def test_the_digest_is_computed_over_the_engine_canonical_bytes(graph: Path):
    """It must match `gt_engine.attribution`'s convention, not merely be stable."""

    contract = symbol_contract(graph, 1)
    expected = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert canonical_json(contract) == expected


def test_coverage_counts_symbols_facts_and_per_field_fill(graph: Path):
    """Requirement 3, against a fixture whose every number is countable by hand."""

    report = coverage(graph)

    assert report["total_code_symbols"] == 3
    assert report["symbols_with_any_fact"] == 2
    assert report["symbols_with_contract_fact"] == 1
    assert report["total_facts"] == 13
    assert report["total_contract_facts"] == 10
    assert report["facts_per_symbol_mean"] == round(13 / 3, 4)
    assert report["contract_facts_per_symbol_mean"] == round(10 / 3, 4)
    assert report["symbols_with_field"] == {
        "boundaries": 1,
        "data_flow": 1,
        "guards": 1,
        "params": 1,
        "returns": 1,
        "side_effects": 1,
        "visibility": 1,
    }


def test_an_unknown_node_id_raises_rather_than_inventing_a_symbol(graph: Path):
    with pytest.raises(LookupError):
        symbol_contract(graph, 9999)


def test_a_missing_graph_raises_rather_than_creating_one(tmp_path: Path):
    """Read-only access: a typo must not leave an empty database behind."""

    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        coverage(missing)
    assert not missing.exists()


@pytest.mark.skipif(not ARKTYPE_GRAPH.exists(), reason=f"real graph absent: {ARKTYPE_GRAPH}")
def test_coverage_on_the_real_arktype_graph():
    """Reports the measured density. Asserts internal consistency only -- there is
    no target here to hit, and the plan forbids inventing one."""

    report = coverage(ARKTYPE_GRAPH)

    assert report["total_code_symbols"] > 0
    assert report["symbols_with_contract_fact"] <= report["symbols_with_any_fact"]
    assert report["symbols_with_any_fact"] <= report["total_code_symbols"]
    assert report["total_contract_facts"] <= report["total_facts"]
    for field, filled in report["symbols_with_field"].items():
        assert filled <= report["total_code_symbols"], field
    assert report["facts_per_symbol_mean"] == round(
        report["total_facts"] / report["total_code_symbols"], 4
    )


@pytest.mark.skipif(not ARKTYPE_GRAPH.exists(), reason=f"real graph absent: {ARKTYPE_GRAPH}")
def test_the_real_graph_projects_byte_identically_twice():
    """Determinism over the producer's actual output, not only over a fixture."""

    def digests() -> list[str]:
        return [contract_digest(contract) for contract in contracts(ARKTYPE_GRAPH)]

    assert digests() == digests()


_RESOLUTION_SYMBOLS_DDL = """
CREATE TABLE resolution_symbols (
    stable_id TEXT, native_id TEXT, native_kind TEXT, normalized_kind TEXT,
    language TEXT, path TEXT, qualified_name TEXT, start_line INTEGER,
    end_line INTEGER, export_status TEXT
)
"""


def test_minted_join_uses_native_id_index_and_rejects_numeric_aliases(graph):
    with sqlite3.connect(graph) as connection:
        connection.execute(_RESOLUTION_SYMBOLS_DDL)
        connection.execute("CREATE INDEX native_lookup ON resolution_symbols(native_id)")
        connection.executemany(
            "INSERT INTO resolution_symbols(stable_id, native_id) VALUES (?, ?)",
            [("canonical", "1"), ("not-node-one", "1junk"), ("not-canonical", "01")],
        )
        query, _ = contract._symbol_query(connection)
        plan = connection.execute(
            "EXPLAIN QUERY PLAN " + query, contract.CODE_SYMBOL_LABELS
        ).fetchall()
        assert any("SEARCH rs USING INDEX native_lookup" in row[3] for row in plan), plan
    projected = list(contracts(graph))
    assert len(projected) == 3
    assert projected[0]["symbol"]["stable_id"] == "canonical"


def test_a_producer_minted_stable_id_is_preferred_over_a_derived_one(tmp_path: Path):
    """nodes.stable_id is null on every code symbol in real graphs, but the
    producer mints one in resolution_symbols keyed by native_id = nodes.id. The
    contract must carry that identity -- it is what the resolution candidate and
    dedupe machinery already use -- and derive one only when none was minted."""
    path = tmp_path / "minted.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(_NODES_DDL)
        connection.execute(_PROPERTIES_DDL)
        connection.execute(_RESOLUTION_SYMBOLS_DDL)
        connection.executemany(
            "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
            " start_line, end_line, return_type, stable_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Function", "minted", "m.minted", "src/m.ts", 1, 3, None, None),
                (2, "Function", "orphan", "m.orphan", "src/m.ts", 5, 7, None, None),
            ],
        )
        # Only symbol 1 has a minted id; symbol 2 must fall back to a derived one.
        connection.execute(
            "INSERT INTO resolution_symbols (stable_id, native_id, native_kind,"
            " normalized_kind, language, path, qualified_name, start_line, end_line,"
            " export_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a" * 64, "1", "function", "function", "typescript", "src/m.ts",
             "m.minted", 1, 3, "exported"),
        )
        connection.commit()
    finally:
        connection.close()

    minted = contract.symbol_contract(path, 1)
    orphan = contract.symbol_contract(path, 2)
    assert minted["symbol"]["stable_id"] == "a" * 64
    assert orphan["symbol"]["stable_id"].startswith(contract.DERIVED_STABLE_ID_PREFIX)
    # And the full projection keeps its stable ordering with the join in place.
    ids = [c["symbol"]["stable_id"] for c in contract.contracts(path)]
    assert ids == [minted["symbol"]["stable_id"], orphan["symbol"]["stable_id"]]
