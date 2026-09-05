"""Project stored facts into a diffable behavioural contract for a code symbol.

A generated prose ``description`` of a function is a paraphrase: it cannot be
diffed, it cannot be traced back to the code that justified it, and it goes
stale silently because nothing in the store knows when the prose stopped
matching the body.

GT already stores the facts a description would have been written from. On
arktype the ``properties`` table holds 9,233 rows over 3,511 code symbols --
``param``, ``return_shape``, ``guard_clause``, ``boundary_condition``,
``side_effect``, ``data_flow`` and ``visibility`` among them. This module is a
pure projection of exactly those rows into one structured record per symbol.

Three consequences follow, and they are the whole reason to prefer projection
over prose:

- **No model is involved**, so the same graph yields byte-identical output. A
  contract can therefore be hashed (:func:`contract_digest`) and diffed across
  commits, which is what ``signature_delta`` in ``gt_engine.attribution``
  already does for the narrower case of a changed signature.
- **Every claim carries its source row id**, so a consumer can trace any line of
  a contract back to the ``properties`` row that produced it. Nothing here is
  inferred, summarised, or generated.
- **Absence stays visible.** A symbol with no facts yields an explicitly empty
  contract with ``fact_count`` 0. It never yields a plausible-looking one.

Two derivations are worth naming because neither is one-to-one with a stored
property row, and both are deterministic functions of stored columns rather than
guesses:

- ``symbol.stable_id`` is the producer-minted identity. ``nodes.stable_id`` is
  null on every code symbol, but the producer mints one in
  ``resolution_symbols`` keyed by ``native_id = nodes.id`` -- 3,509 of the 3,511
  arktype symbols join to it. That is the same identity the resolution candidate
  and dedupe machinery use, so a contract can be cross-referenced with resolution
  evidence directly. Only when no minted id exists does it fall back to
  ``gtsym1:<sha256>`` over ``(kind, file_path, qualified_name)``; the prefix marks
  a derived id so a reader can always tell which they got, and line numbers are
  excluded so it survives relocation.
- ``returns.declared_type`` comes from ``nodes.return_type`` rather than from a
  ``properties`` row, so it carries no property id. It is still a stored fact
  about the symbol, not an inference, and it is filled on 851 of 3,511 symbols.

Property kinds outside :data:`CONTRACT_FIELD_KINDS` -- ``caller_usage``,
``fingerprint``, ``field_read``, ``call_order``, ``class_field`` and the rest --
are real facts but are not contract fields, so they are not projected and do not
count toward ``fact_count``. :func:`coverage` reports both densities separately
so the overall 2.6 facts/symbol figure and the contract-bearing subset stay
distinguishable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "CODE_SYMBOL_LABELS",
    "CONTRACT_FIELD_KINDS",
    "CONTRACT_SCHEMA",
    "COVERAGE_SCHEMA",
    "DERIVED_STABLE_ID_PREFIX",
    "canonical_json",
    "contract_digest",
    "contracts",
    "contracts_with_node_ids",
    "coverage",
    "empty_contract",
    "symbol_contract",
    "symbol_node_ids",
]

CONTRACT_SCHEMA = "gt.symbol_contract.v1"
COVERAGE_SCHEMA = "gt.symbol_contract_coverage.v1"

# A derived id is prefixed so a consumer can never mistake it for one the
# producer minted. See the module docstring for why the fallback exists.
DERIVED_STABLE_ID_PREFIX = "gtsym1:"

# The node labels that carry a behavioural contract. `File` is included because
# it is a code symbol in the schema, even though it holds no property rows in
# any graph built so far -- excluding it would quietly flatter the denominator
# that `coverage()` reports.
CODE_SYMBOL_LABELS: tuple[str, ...] = (
    "Annotation",
    "Class",
    "Constant",
    "Constructor",
    "Enum",
    "EnumMember",
    "File",
    "Function",
    "Impl",
    "Interface",
    "Macro",
    "Method",
    "Module",
    "Namespace",
    "Record",
    "Struct",
    "Trait",
    "TypeAlias",
    "Union",
)

# Contract field -> the single `properties.kind` that populates it. One kind per
# field, so no field can be assembled from a mixture whose provenance is unclear.
CONTRACT_FIELD_KINDS: dict[str, str] = {
    "params": "param",
    "returns": "return_shape",
    "guards": "guard_clause",
    "side_effects": "side_effect",
    "boundaries": "boundary_condition",
    "data_flow": "data_flow",
    "visibility": "visibility",
}

# Fields projected as ordered lists. `returns` is an object and `visibility` a
# single row, so both are assembled separately.
LIST_FIELDS: tuple[str, ...] = (
    "params",
    "guards",
    "side_effects",
    "boundaries",
    "data_flow",
)

# Fields whose emptiness `coverage()` reports. The plan names the first five;
# `data_flow` and `visibility` are included because they are projected too and
# their density is part of the same measurement.
COVERAGE_FIELDS: tuple[str, ...] = (
    "params",
    "returns",
    "guards",
    "side_effects",
    "boundaries",
    "data_flow",
    "visibility",
)

# Suffix markers the producer appends to a `param` value. Both are exact literal
# forms observed in the store; anything else leaves `required` unknown rather
# than inventing a default.
_PARAM_REQUIRED_MARKER = "[required]"
_PARAM_OPTIONAL_MARKER = " opt="

# Producer separators, one per kind. Named constants because a producer change
# to any of them must become a visible edit here, not a silently wrong parse.
_PARAM_TYPE_SEPARATOR = "::"
_RETURN_SEPARATOR = "|"
_GUARD_ACTION_SEPARATOR = ": "
_GUARD_EFFECT_SEPARATOR = " -> "
_SIDE_EFFECT_SEPARATOR = ": "
_BOUNDARY_SEPARATOR = "|"
_FLOW_SEPARATOR = " -> "
_FLOW_SINK_SEPARATOR = " | "

_LABEL_PLACEHOLDERS = ",".join("?" * len(CODE_SYMBOL_LABELS))

# Two shapes of the symbol query. The producer mints stable ids in
# `resolution_symbols` (native_id = nodes.id), so that is the preferred source;
# a graph built without the resolution-v2 tables -- or a synthetic fixture --
# falls back to `nodes.stable_id`, which is then usually null and derived.
_SELECT_SYMBOLS_WITH_MINTED = f"""
    SELECT n.id, n.label, n.name, n.qualified_name, n.file_path, n.start_line,
           n.end_line, n.return_type,
           COALESCE(NULLIF(rs.stable_id, ''), NULLIF(n.stable_id, '')) AS stable_id
      FROM nodes n
      LEFT JOIN resolution_symbols rs ON rs.native_id = CAST(n.id AS TEXT)
     WHERE n.label IN ({_LABEL_PLACEHOLDERS})
"""

_SELECT_SYMBOLS_NODES_ONLY = f"""
    SELECT id, label, name, qualified_name, file_path, start_line, end_line,
           return_type, stable_id
      FROM nodes
     WHERE label IN ({_LABEL_PLACEHOLDERS})
"""

_HAS_RESOLUTION_SYMBOLS = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resolution_symbols'"
)

_COUNT_SYMBOLS_BY_LABEL = f"""
    SELECT label, count(*) AS total
      FROM nodes
     WHERE label IN ({_LABEL_PLACEHOLDERS})
     GROUP BY label
"""

_COUNT_FACTS = f"""
    SELECT count(*) AS fact_rows, count(DISTINCT p.node_id) AS fact_symbols
      FROM properties p
      JOIN nodes n ON n.id = p.node_id
     WHERE n.label IN ({_LABEL_PLACEHOLDERS})
"""

_SELECT_PROPERTY_COLUMNS = "SELECT id, node_id, kind, value, line, confidence FROM properties"


def canonical_json(value: Any) -> str:
    """Render ``value`` in the engine's canonical form.

    Byte-for-byte the convention ``gt_engine.attribution`` hashes, so a contract
    digest and an attribution row hash are computed over the same bytes shape.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def contract_digest(contract: dict[str, Any]) -> str:
    """Return the sha256 of a contract's canonical JSON.

    Two commits whose digests match made no change this projection can see. The
    digest covers ``start_line``/``end_line`` and every fact's ``line``, so
    relocating an otherwise unchanged symbol does change it.
    """
    return hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a graph read-only, so a typo can never create or mutate a store."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"graph not found: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _symbol_query(connection: sqlite3.Connection) -> tuple[str, str]:
    """Return ``(select, column_prefix)`` for this graph.

    Prefers the producer-minted id in ``resolution_symbols``; a graph without
    that table falls back to ``nodes`` alone. The prefix differs between the two
    shapes because the join makes bare ``id``/``file_path``/``start_line`` ambiguous.
    """
    if connection.execute(_HAS_RESOLUTION_SYMBOLS).fetchone() is None:
        return _SELECT_SYMBOLS_NODES_ONLY, ""
    return _SELECT_SYMBOLS_WITH_MINTED, "n."


def _derived_stable_id(kind: str, file_path: str, qualified_name: str) -> str:
    """Derive a relocation-stable id when the producer minted none."""
    material = "\x00".join((kind, file_path, qualified_name))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{DERIVED_STABLE_ID_PREFIX}{digest}"


def _symbol_identity(row: sqlite3.Row) -> dict[str, Any]:
    """Project the identity block from a ``nodes`` row."""
    kind = str(row["label"])
    file_path = str(row["file_path"])
    qualified_name = str(row["qualified_name"] or row["name"] or "")
    stored_stable_id = str(row["stable_id"] or "")
    return {
        "stable_id": stored_stable_id or _derived_stable_id(kind, file_path, qualified_name),
        "qualified_name": qualified_name,
        "file_path": file_path,
        "start_line": None if row["start_line"] is None else int(row["start_line"]),
        "end_line": None if row["end_line"] is None else int(row["end_line"]),
        "kind": kind,
    }


def _property_order(row: sqlite3.Row) -> tuple[int, int, int]:
    """Order facts by line then id. A null line sorts last, never arbitrarily."""
    line = row["line"]
    return (1, 0, int(row["id"])) if line is None else (0, int(line), int(row["id"]))


def _fact_base(row: sqlite3.Row) -> dict[str, Any]:
    """The four columns every projected fact keeps verbatim from its row."""
    confidence = row["confidence"]
    return {
        "property_id": int(row["id"]),
        "line": None if row["line"] is None else int(row["line"]),
        "confidence": None if confidence is None else float(confidence),
        "value": str(row["value"]),
    }


def _project_param(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``name:: type [required]``, ``name opt=default``, or a bare ``name``."""
    fact = _fact_base(row)
    text = fact["value"].strip()
    required: bool | None = None
    default: str | None = None
    if text.endswith(_PARAM_REQUIRED_MARKER):
        required = True
        text = text[: -len(_PARAM_REQUIRED_MARKER)].rstrip()
    else:
        head, separator, tail = text.rpartition(_PARAM_OPTIONAL_MARKER)
        if separator:
            required = False
            default = tail.strip()
            text = head.rstrip()
    name, separator, type_text = text.partition(_PARAM_TYPE_SEPARATOR)
    fact["name"] = name.strip()
    fact["type"] = type_text.strip() if separator else None
    fact["required"] = required
    fact["default"] = default
    return fact


def _project_return_shape(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``shape|detail``. A bare value (``none``) is a shape with no detail."""
    fact = _fact_base(row)
    shape, separator, detail = fact["value"].partition(_RETURN_SEPARATOR)
    fact["shape"] = shape.strip()
    fact["detail"] = detail if separator else None
    return fact


def _project_guard(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``action: (condition) -> effect``.

    The effect separator is matched from the right because a condition can
    itself contain an arrow, while every effect observed in the store is a bare
    token. The raw ``value`` is retained either way, so a mis-split loses nothing
    a consumer cannot recover.
    """
    fact = _fact_base(row)
    action, separator, remainder = fact["value"].partition(_GUARD_ACTION_SEPARATOR)
    if not separator:
        fact["action"] = None
        fact["condition"] = fact["value"].strip()
        fact["effect"] = None
        return fact
    condition, arrow, effect = remainder.rpartition(_GUARD_EFFECT_SEPARATOR)
    fact["action"] = action.strip()
    fact["condition"] = (condition if arrow else remainder).strip()
    fact["effect"] = effect.strip() if arrow else None
    return fact


def _project_side_effect(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``effect: target``. ``mutates: this.ctx`` is the only observed form."""
    fact = _fact_base(row)
    effect, separator, target = fact["value"].partition(_SIDE_EFFECT_SEPARATOR)
    fact["effect"] = effect.strip() if separator else None
    fact["target"] = target.strip() if separator else fact["value"].strip()
    return fact


def _project_boundary(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``check|expression`` (``null_check``, ``length_check``, ...)."""
    fact = _fact_base(row)
    check, separator, expression = fact["value"].partition(_BOUNDARY_SEPARATOR)
    fact["check"] = check.strip() if separator else None
    fact["expression"] = expression.strip() if separator else fact["value"].strip()
    return fact


def _project_data_flow(row: sqlite3.Row) -> dict[str, Any]:
    """Split ``source -> sink | sink``.

    Sinks split on a spaced pipe only, which cannot match a ``||`` operator
    inside an expression.
    """
    fact = _fact_base(row)
    source, separator, sinks = fact["value"].partition(_FLOW_SEPARATOR)
    if not separator:
        fact["source"] = None
        fact["sinks"] = []
        return fact
    fact["source"] = source.strip()
    fact["sinks"] = [part.strip() for part in sinks.split(_FLOW_SINK_SEPARATOR) if part.strip()]
    return fact


def _project_visibility(row: sqlite3.Row) -> dict[str, Any]:
    """Keep the visibility token verbatim; it is already a closed vocabulary."""
    fact = _fact_base(row)
    fact["value"] = fact["value"].strip()
    return fact


_PROJECTORS = {
    "param": _project_param,
    "return_shape": _project_return_shape,
    "guard_clause": _project_guard,
    "side_effect": _project_side_effect,
    "boundary_condition": _project_boundary,
    "data_flow": _project_data_flow,
    "visibility": _project_visibility,
}


def empty_contract(symbol: dict[str, Any]) -> dict[str, Any]:
    """Return the contract of a symbol with no contract-bearing facts.

    This is the honest answer for every symbol the producer said nothing about.
    It is a distinct value from "not looked up", and it is never a placeholder
    to be filled in from somewhere else.
    """
    return {
        "symbol": dict(symbol),
        "params": [],
        "returns": {"declared_type": None, "shapes": []},
        "guards": [],
        "side_effects": [],
        "boundaries": [],
        "data_flow": [],
        "visibility": None,
        "provenance": {
            "property_ids": [],
            "fact_count": 0,
            "schema": CONTRACT_SCHEMA,
        },
    }


def _build_contract(
    node_row: sqlite3.Row,
    property_rows: Sequence[sqlite3.Row],
) -> dict[str, Any]:
    """Assemble one contract from a node row and that node's property rows."""
    contract = empty_contract(_symbol_identity(node_row))

    declared_type = node_row["return_type"] or None
    contract["returns"]["declared_type"] = None if declared_type is None else str(declared_type)

    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _PROJECTORS}
    for row in sorted(property_rows, key=_property_order):
        projector = _PROJECTORS.get(str(row["kind"]))
        if projector is None:
            # A real fact, but not a contract field. Left out on purpose: the
            # contract never carries a claim its schema does not describe.
            continue
        by_kind[str(row["kind"])].append(projector(row))

    for field in LIST_FIELDS:
        contract[field] = by_kind[CONTRACT_FIELD_KINDS[field]]
    contract["returns"]["shapes"] = by_kind["return_shape"]

    # Visibility is a scalar in the contract and is single-valued in every graph
    # built so far. Were a graph ever to hold more than one row, the first in
    # stable order is projected and the rest stay traceable through provenance.
    visibility_facts = by_kind["visibility"]
    contract["visibility"] = visibility_facts[0] if visibility_facts else None

    property_ids = sorted(
        int(fact["property_id"]) for facts in by_kind.values() for fact in facts
    )
    contract["provenance"]["property_ids"] = property_ids
    contract["provenance"]["fact_count"] = len(property_ids)
    return contract


def symbol_contract(db_path: str | Path, node_id: int) -> dict[str, Any]:
    """Return the contract for one code symbol, or raise if the id names none."""
    connection = _connect(db_path)
    try:
        select, prefix = _symbol_query(connection)
        node_row = connection.execute(
            f"{select} AND {prefix}id = ?", (*CODE_SYMBOL_LABELS, int(node_id)),
        ).fetchone()
        if node_row is None:
            raise LookupError(f"no code symbol with node id {node_id}")
        property_rows = connection.execute(
            f"{_SELECT_PROPERTY_COLUMNS} WHERE node_id = ?", (int(node_id),),
        ).fetchall()
    finally:
        connection.close()
    return _build_contract(node_row, property_rows)


def symbol_node_ids(db: str | Path | sqlite3.Connection) -> dict[int, str]:
    """Return ``node id -> the stable id this module keys that symbol by``.

    The bridge a consumer needs to join a contract-keyed artefact -- a cached
    embedding, say -- back to the ``nodes`` rows of one particular graph.  The
    stable id is durable across rebuilds; ``nodes.id`` is not, so nothing may
    persist a node id, and nothing may re-derive the stable id by hand either.

    Accepts an open connection because a caller mid-query should not have to
    reopen the graph it is already reading.
    """
    connection, owned = (db, False) if isinstance(db, sqlite3.Connection) else (_connect(db), True)
    previous_factory = connection.row_factory
    try:
        connection.row_factory = sqlite3.Row
        select, prefix = _symbol_query(connection)
        rows = connection.execute(
            f"{select} ORDER BY {prefix}file_path, {prefix}start_line, {prefix}id",
            CODE_SYMBOL_LABELS,
        ).fetchall()
    finally:
        if owned:
            connection.close()
        else:
            connection.row_factory = previous_factory
    return {int(row["id"]): _symbol_identity(row)["stable_id"] for row in rows}


def contracts_with_node_ids(db_path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(node id, contract)`` in the same order :func:`contracts` does.

    The node id is deliberately not part of the contract itself: it is a
    per-build row number, and putting it in the projection would make
    ``contract_digest`` change every time the producer renumbered a table.  A
    consumer that needs to join back to ``nodes`` for the graph in hand takes
    it from here instead.
    """
    connection = _connect(db_path)
    try:
        select, prefix = _symbol_query(connection)
        node_rows = connection.execute(
            f"{select} ORDER BY {prefix}file_path, {prefix}start_line, {prefix}id",
            CODE_SYMBOL_LABELS,
        ).fetchall()
        # One pass over `properties` rather than a query per symbol: the table is
        # small relative to the graph and the join order is irrelevant, since
        # `_build_contract` sorts each group itself.
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in connection.execute(_SELECT_PROPERTY_COLUMNS):
            grouped.setdefault(int(row["node_id"]), []).append(row)
    finally:
        connection.close()

    for node_row in node_rows:
        node_id = int(node_row["id"])
        yield node_id, _build_contract(node_row, grouped.get(node_id, []))


def contracts(db_path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield every code symbol's contract in a stable repository-order sequence.

    Ordered by file, then position, then node id, so two runs over the same
    graph emit the same sequence and a diff of the whole projection is readable.
    """
    for _, contract in contracts_with_node_ids(db_path):
        yield contract


def _non_empty_fields(contract: dict[str, Any]) -> set[str]:
    """Which contract fields carry at least one stored fact."""
    filled = {field for field in LIST_FIELDS if contract[field]}
    if contract["returns"]["shapes"] or contract["returns"]["declared_type"]:
        filled.add("returns")
    if contract["visibility"] is not None:
        filled.add("visibility")
    return filled


def coverage(db_path: str | Path) -> dict[str, Any]:
    """Measure how much of a graph's symbol population actually has a contract.

    This reports what is there. The plan's 2.6 facts/symbol is the floor to
    improve on, not a threshold to declare met, so nothing here is compared
    against a target.
    """
    connection = _connect(db_path)
    try:
        symbols_by_label = {
            str(row["label"]): int(row["total"])
            for row in connection.execute(_COUNT_SYMBOLS_BY_LABEL, CODE_SYMBOL_LABELS)
        }
        # Every property kind, not only contract fields: this is the density the
        # baseline table quotes, and it must stay comparable to it.
        fact_row = connection.execute(_COUNT_FACTS, CODE_SYMBOL_LABELS).fetchone()
        total_facts = int(fact_row["fact_rows"])
        symbols_with_any_fact = int(fact_row["fact_symbols"])
    finally:
        connection.close()

    total_symbols = sum(symbols_by_label.values())
    symbols_with_field = dict.fromkeys(COVERAGE_FIELDS, 0)
    symbols_with_contract_fact = 0
    total_contract_facts = 0
    for contract in contracts(db_path):
        fact_count = contract["provenance"]["fact_count"]
        total_contract_facts += fact_count
        symbols_with_contract_fact += 1 if fact_count else 0
        for field in _non_empty_fields(contract):
            symbols_with_field[field] += 1

    def _per_symbol(numerator: int) -> float:
        return round(numerator / total_symbols, 4) if total_symbols else 0.0

    return {
        "schema": COVERAGE_SCHEMA,
        "total_code_symbols": total_symbols,
        "code_symbols_by_label": dict(sorted(symbols_by_label.items())),
        "symbols_with_any_fact": symbols_with_any_fact,
        "symbols_with_contract_fact": symbols_with_contract_fact,
        "symbols_with_field": dict(sorted(symbols_with_field.items())),
        "total_facts": total_facts,
        "total_contract_facts": total_contract_facts,
        "facts_per_symbol_mean": _per_symbol(total_facts),
        "contract_facts_per_symbol_mean": _per_symbol(total_contract_facts),
    }
