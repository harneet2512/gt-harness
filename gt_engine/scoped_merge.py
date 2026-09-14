"""Scoped salvage for a superseded LSP promotion candidate.

A promotion computes its mutations on a certified base graph. When the
workspace moves before the candidate can be adopted, the coordinator's
all-or-nothing publish refuses the whole artifact and the resolution work
- minutes of LSP server time - is discarded. The mutations the candidate
carries are still valid wherever the source did not move: this module
merges exactly that subset into a copy of the live graph.

Why rowid is the join key
-------------------------
The producer builds a candidate with ``sqlite3.Connection.backup``, which
preserves rowids, and the amend path re-derives the graph: rows whose
files were not re-parsed come back byte-identical at the same rowid. So
the merge diffs candidate-vs-base by rowid - the producer's complete
mutation set, whatever shape it takes - and applies each mutation to the
live copy at the same rowid, but only after verifying the live row still
equals the base row byte-for-byte. That equivalence check is the real
guard: the caller-supplied stale set decides intent, the row comparison
decides truth, and a row that diverged is skipped and counted rather than
merged on faith.

Rowid references (``edges.source_id``/``target_id``,
``properties.node_id``, ``nodes.parent_id``) on inserted rows are
remapped through the node insert map; a reference to a rowid that is not
one of the two honest versions (base row or candidate row) refuses the
insert. Stable-id references (``selected_target_id``,
``callsite_stable_id``, ``target_symbol_id``, ``exclusion_fact_ids``)
are content-identical across graphs and copy verbatim.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The producer's data tables. Anything else - bookkeeping, derived
# products (closure, FTS), per-file hashes - is either not a promotion
# mutation surface or is rebuilt on the output rather than merged. An
# unlisted table is under-merged (safe), never over-merged (wrong).
_MERGEABLE_TABLES = (
    "nodes",
    "edges",
    "properties",
    "assertions",
    "resolution_symbols",
    "resolution_callsites",
    "resolution_candidates",
)

# Columns holding rowids into ``nodes``; resolved through the merge's
# node map when a row is inserted. Verified against the v15.2-trust-tier
# producer schema: edges.source_id/target_id, nodes.parent_id,
# properties.node_id, assertions.test_node_id and
# resolution_candidates.target_id are declared foreign keys;
# resolution_callsites.source_id and assertions.target_node_id carry
# node rowids without a declared constraint (12972/12972 join).
_NODE_ROWID_REFS = {
    "edges": ("source_id", "target_id"),
    "properties": ("node_id",),
    "assertions": ("test_node_id", "target_node_id"),
    "nodes": ("parent_id",),
    "resolution_callsites": ("source_id",),
    "resolution_candidates": ("target_id",),
}

# Stable-string references into other mergeable tables. They are not
# remapped (the identity is content-stable across revisions) but the
# referenced row must exist in the merged graph - an insert or update
# that would dangle is counted as divergence, never written. Declared on
# the real schema: resolution_candidates.target_stable_id and
# callsite_id are foreign keys; resolution_callsites.source_stable_id is
# an undeclared reference into resolution_symbols.
_CROSS_TABLE_REFS = {
    "resolution_callsites": (
        ("source_stable_id", "resolution_symbols", "stable_id"),
        ("selected_target_stable_id", "resolution_symbols", "stable_id"),
    ),
    "resolution_candidates": (
        ("callsite_id", "resolution_callsites", "callsite_id"),
        ("target_stable_id", "resolution_symbols", "stable_id"),
    ),
}


# The batch-amend path trusts these bookkeeping tables absolutely:
# parser_node_inventory maps content identity -> node rowid, and the
# parser_{edge,property,assertion}_inventory tables map fact rowid ->
# content digest. A merge that rewrites or removes an inventoried row
# leaves the entry aliased to content it was not minted for, and the next
# amend aborts inside "batch parent parser row differs from its
# inventory". Every row the merge deletes or overwrites drops its entry;
# every applied candidate row adopts the candidate's own entry remapped
# to the merged rowid. Rows the leg already evicted stay un-inventoried
# and re-enter fresh on the next amend.
_PARSER_INVENTORY = {
    # table: (inventory_table, id_column, value_column, value_first)
    "nodes": ("parser_node_inventory", "node_id", "identity", False),
    "edges": ("parser_edge_inventory", "edge_id", "content_sha256", True),
    "properties": ("parser_property_inventory", "property_id", "content_sha256", True),
    "assertions": ("parser_assertion_inventory", "assertion_id", "content_sha256", True),
}


def _inventory_drop(
    con: sqlite3.Connection, table: str, rowid: int
) -> None:
    inv = _PARSER_INVENTORY.get(table)
    if inv is None or not _table_exists(con, "main", inv[0]):
        return
    con.execute(f"DELETE FROM {inv[0]} WHERE {inv[1]}=?", (rowid,))


def _inventory_adopt(
    con: sqlite3.Connection, table: str, cand_rowid: int, merged_rowid: int
) -> None:
    """Carry the candidate's inventory entry onto the merged rowid so the
    merged graph's bookkeeping still describes the row's actual content."""
    inv = _PARSER_INVENTORY.get(table)
    if inv is None:
        return
    inv_name, id_col, value_col, value_first = inv
    if not (
        _table_exists(con, "main", inv_name)
        and _table_exists(con, "cand", inv_name)
    ):
        return
    if value_first:
        con.execute(
            f"INSERT OR REPLACE INTO {inv_name}({id_col},{value_col}) "
            f"SELECT ?, {value_col} FROM cand.{inv_name} WHERE {id_col}=?",
            (merged_rowid, cand_rowid),
        )
    else:
        con.execute(
            f"INSERT OR REPLACE INTO {inv_name}({value_col},{id_col}) "
            f"SELECT {value_col}, ? FROM cand.{inv_name} WHERE {id_col}=?",
            (merged_rowid, cand_rowid),
        )


def _cross_refs_ok(
    con: sqlite3.Connection,
    table: str,
    columns: list[str],
    values: list[Any],
) -> bool:
    """Every stable-string reference must name a row the merge holds."""
    for column, ref_table, ref_column in _CROSS_TABLE_REFS.get(table, ()):
        if column not in columns:
            continue
        value = values[columns.index(column)]
        if value is None:
            continue
        exists = con.execute(
            f"SELECT 1 FROM {ref_table} WHERE {ref_column}=? LIMIT 1",
            (value,),
        ).fetchone()
        if exists is None:
            return False
    return True


@dataclass(slots=True)
class MergeResult:
    applied: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    skipped_stale: int = 0
    skipped_diverged: int = 0
    tables_merged: int = 0
    fts_rebuilt: int = 0
    detail: dict[str, int] = field(default_factory=dict)


def _columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [
        str(_decode(row[1])) for row in con.execute(f"PRAGMA {schema}.table_info({table})")
    ]


def _decode(value: Any) -> Any:
    """TEXT values arrive as bytes under ``text_factory=bytes``: restore
    the str for any value that is valid UTF-8, keep the raw bytes for
    content the producer stored that UTF-8 cannot represent. Used for
    diffing only - writes copy attach-side and never decode at all."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


def _rows(
    con: sqlite3.Connection, schema: str, table: str
) -> dict[int, tuple[Any, ...]]:
    return {
        int(row[0]): tuple(_decode(value) for value in row[1:])
        for row in con.execute(f"SELECT rowid,* FROM {schema}.{table}")
    }


def _table_exists(con: sqlite3.Connection, schema: str, table: str) -> bool:
    return (
        con.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _node_file_map(con: sqlite3.Connection, schema: str) -> dict[int, str]:
    if not _table_exists(con, schema, "nodes"):
        return {}
    return {
        int(rowid): str(_decode(file_path) or "")
        for rowid, file_path in con.execute(
            f"SELECT rowid, file_path FROM {schema}.nodes"
        )
    }


def _row_files(
    table: str,
    row: tuple[Any, ...],
    columns: list[str],
    node_files: dict[int, str],
) -> set[str]:
    """Workspace files a row speaks for, by its own columns plus the files
    of every node it references. An edge whose callsite file is clean but
    whose resolved target lives in a stale file is caught here."""
    files: set[str] = set()
    for column in ("file_path", "source_file"):
        if column in columns:
            value = row[columns.index(column)]
            if value:
                files.add(_norm(str(value)))
    for column in _NODE_ROWID_REFS.get(table, ()):
        if column in columns:
            node_id = row[columns.index(column)]
            if isinstance(node_id, int) and not isinstance(node_id, bool):
                referenced = node_files.get(node_id)
                if referenced:
                    files.add(_norm(referenced))
    return files


class _NodeSpace:
    """The three versions of ``nodes`` the merge arbitrates between, plus
    the candidate-rowid -> merged-rowid map built by node inserts."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.base = (
            _rows(con, "base", "nodes") if _table_exists(con, "base", "nodes") else {}
        )
        self.cand = (
            _rows(con, "cand", "nodes") if _table_exists(con, "cand", "nodes") else {}
        )
        self.merged = (
            _rows(con, "main", "nodes")
            if _table_exists(con, "main", "nodes")
            else {}
        )
        self.columns = _columns(con, "cand", "nodes")
        self.insert_map: dict[int, int] = {}

    def ref_ok(self, ref: int) -> bool:
        live = self.merged.get(ref)
        return live is not None and (
            live == self.base.get(ref) or live == self.cand.get(ref)
        )


def merge_lsp_candidate(
    *,
    base_graph: str | Path,
    candidate_graph: str | Path,
    live_graph: str | Path,
    out_path: str | Path,
    stale_paths: frozenset[str] | set[str] | tuple[str, ...],
) -> MergeResult:
    """Merge the candidate's clean-path mutations into a copy of the live graph.

    ``stale_paths`` names workspace-relative files whose content moved
    since the base was frozen. A mutation touching any of them is skipped;
    a mutation whose live row no longer matches the base row is skipped
    and counted separately - the two answers are never the same failure.
    """
    stale = {_norm(path) for path in stale_paths}
    result = MergeResult()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError("scoped_merge_output_exists")
    shutil.copyfile(live_graph, out)

    base_uri = f"{Path(base_graph).resolve().as_uri()}?mode=ro"
    candidate_uri = f"{Path(candidate_graph).resolve().as_uri()}?mode=ro"
    merged = sqlite3.connect(
        f"file:{out.resolve().as_posix()}?mode=rwc", uri=True
    )
    # Producer graphs store binary payloads in TEXT-affinity columns
    # (properties.value is not always UTF-8). Reading with the default
    # factory explodes on decode; bytes lets the diff normalize what it
    # can and keep what it cannot - and writes go attach-side, so the
    # merged file carries the producer's exact storage classes either way.
    merged.text_factory = bytes
    try:
        merged.execute(f"ATTACH DATABASE '{base_uri}' AS base")
        merged.execute(f"ATTACH DATABASE '{candidate_uri}' AS cand")
        merged.execute("BEGIN")

        cand_node_files = _node_file_map(merged, "cand")
        nodes = _NodeSpace(merged)

        present = {
            str(_decode(row[0]))
            for row in merged.execute(
                "SELECT name FROM cand.sqlite_master WHERE type='table'"
            )
        }
        touched_tables: set[str] = set()
        # nodes first: every other table's rowid references resolve
        # through the state this pass leaves behind.
        for table in sorted(_MERGEABLE_TABLES, key=lambda t: (t != "nodes", t)):
            if table not in present or not _table_exists(merged, "main", table):
                continue
            columns = _columns(merged, "cand", table)
            cand_rows = _rows(merged, "cand", table)
            base_rows = (
                _rows(merged, "base", table)
                if _table_exists(merged, "base", table)
                else {}
            )
            merged_rows = _rows(merged, "main", table)
            touched = False

            for rowid in sorted(set(base_rows) - set(cand_rows)):
                base_row = base_rows[rowid]
                if not _mergeable(
                    table, base_row, columns, cand_node_files, stale, result
                ):
                    continue
                if merged_rows.get(rowid) != base_row:
                    result.skipped_diverged += 1
                    continue
                merged.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
                _inventory_drop(merged, table, rowid)
                if table == "nodes":
                    nodes.merged.pop(rowid, None)
                result.applied += 1
                result.deleted += 1
                touched = True

            for rowid, cand_row in sorted(cand_rows.items()):
                base_row = base_rows.get(rowid)
                if base_row == cand_row:
                    continue
                if not _mergeable(
                    table, cand_row, columns, cand_node_files, stale, result
                ):
                    continue
                if base_row is None:
                    new_id = _insert_remapped(
                        merged, table, columns, cand_row, rowid, nodes,
                        nodes.merged if table == "nodes" else merged_rows,
                        result,
                    )
                    if new_id is None:
                        continue
                    if table == "nodes" and new_id != rowid:
                        nodes.insert_map[rowid] = new_id
                        nodes.merged[new_id] = cand_row
                    result.applied += 1
                    result.inserted += 1
                    touched = True
                    continue
                if merged_rows.get(rowid) != base_row:
                    result.skipped_diverged += 1
                    continue
                if not _cross_refs_ok(
                    merged, table, columns, list(cand_row)
                ):
                    result.skipped_diverged += 1
                    continue
                # Copy attach-side: the candidate's exact bytes and storage
                # classes land verbatim, so binary payloads survive.
                merged.execute(
                    f"UPDATE {table} SET ({','.join(columns)}) = "
                    f"(SELECT {','.join(columns)} FROM cand.{table} "
                    f"WHERE rowid=?) WHERE rowid=?",
                    (rowid, rowid),
                )
                _inventory_drop(merged, table, rowid)
                _inventory_adopt(merged, table, rowid, rowid)
                if table == "nodes":
                    nodes.merged[rowid] = cand_row
                result.applied += 1
                result.updated += 1
                touched = True
            result.tables_merged += int(touched)
            if touched:
                touched_tables.add(table)

        # External-content FTS tables index the content table's rows, not
        # the merge's. Any table the merge touched leaves its FTS stale;
        # 'rebuild' re-indexes it from the merged rows before commit.
        result.fts_rebuilt = _rebuild_derived_fts(merged, touched_tables)
        merged.commit()
    except Exception:
        merged.rollback()
        merged.close()
        out.unlink(missing_ok=True)
        raise
    merged.close()
    result.detail = {
        "applied": result.applied,
        "inserted": result.inserted,
        "updated": result.updated,
        "deleted": result.deleted,
        "skipped_stale": result.skipped_stale,
        "skipped_diverged": result.skipped_diverged,
        "tables_merged": result.tables_merged,
        "fts_rebuilt": result.fts_rebuilt,
    }
    return result


def _rebuild_derived_fts(
    con: sqlite3.Connection, touched_tables: set[str]
) -> int:
    """Rebuild FTS5 external-content indexes whose content table moved."""
    if not touched_tables:
        return 0
    rebuilt = 0
    for raw_name, raw_sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql LIKE '%fts5%'"
    ):
        name = str(_decode(raw_name))
        sql = str(_decode(raw_sql) or "")
        match = re.search(r"content\s*=\s*'?\"?(\w+)'?\"?", sql)
        if match and match.group(1) in touched_tables:
            con.execute(f"INSERT INTO {name}({name}) VALUES('rebuild')")
            rebuilt += 1
    return rebuilt


def _insert_remapped(
    con: sqlite3.Connection,
    table: str,
    columns: list[str],
    cand_row: tuple[Any, ...],
    cand_rowid: int,
    nodes: _NodeSpace,
    merged_rows: dict[int, tuple[Any, ...]],
    result: MergeResult,
) -> int | None:
    """Insert one candidate row, remapping node rowid references.

    A reference resolves through the insert map when the node itself was
    inserted this merge; otherwise the rowid must name a row the merged
    graph already holds as an honest version - the base row it was, or
    the candidate row the merge just made it. Anything else means the
    rowid names a renumbered stale row and the insert refuses. Returns
    the existing merged rowid when a ``stable_id`` match shows the node
    already exists, or None when the insert is refused.

    When nothing needs remapping the row is copied attach-side, so the
    producer's exact bytes and storage classes land verbatim; only a row
    that actually had to be rewritten goes through parameter binding.
    """
    values = list(cand_row)
    remapped = False
    if "id" in columns:
        id_idx = columns.index("id")
        explicit = values[id_idx]
        if (
            isinstance(explicit, int)
            and not isinstance(explicit, bool)
            and explicit in merged_rows
        ):
            if merged_rows[explicit] == cand_row:
                # Live already holds this exact row - a sibling publish
                # landed the same lineage while the candidate was in
                # flight. Inserting it again collides on the id; the
                # merge's goal (row present) is already met.
                return int(explicit)
            # The rowid is taken by a row from another lineage. The insert
            # keeps its content and takes a fresh rowid; the insert map
            # records the remap for anything referencing it.
            values[id_idx] = None
            remapped = True
    for column in _NODE_ROWID_REFS.get(table, ()):
        if column not in columns:
            continue
        idx = columns.index(column)
        ref = values[idx]
        if not isinstance(ref, int) or isinstance(ref, bool):
            continue
        if ref in nodes.insert_map:
            values[idx] = nodes.insert_map[ref]
            remapped = True
            continue
        if ref in nodes.cand and ref not in nodes.base:
            # The reference names a node the candidate itself inserted; if
            # it is not in the insert map it has not been merged yet, and a
            # live rowid collision in the ambiguous zone must not satisfy it.
            result.skipped_diverged += 1
            return None
        if not nodes.ref_ok(ref):
            result.skipped_diverged += 1
            return None
    if not _cross_refs_ok(con, table, columns, values):
        result.skipped_diverged += 1
        return None
    if table == "nodes" and "stable_id" in columns:
        stable = values[columns.index("stable_id")]
        if stable:
            stable_idx = columns.index("stable_id")
            for merged_id, merged_row in nodes.merged.items():
                if merged_row[stable_idx] == stable:
                    return merged_id
    if not remapped:
        cursor = con.execute(
            f"INSERT INTO {table} SELECT * FROM cand.{table} WHERE rowid=?",
            (cand_rowid,),
        )
    else:
        placeholders = ",".join("?" for _ in columns)
        cursor = con.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
    new_id = int(cursor.lastrowid)
    _inventory_adopt(con, table, cand_rowid, new_id)
    # merged_rows was snapshotted before the merge; record the row just
    # placed so a later candidate row colliding on this id remaps instead
    # of crashing the merge. The stored tuple carries the id it landed
    # under, not the candidate's original.
    stored = list(cand_row)
    if "id" in columns:
        stored[columns.index("id")] = new_id
    merged_rows[new_id] = tuple(stored)
    return new_id


def _mergeable(
    table: str,
    row: tuple[Any, ...],
    columns: list[str],
    node_files: dict[int, str],
    stale: set[str],
    result: MergeResult,
) -> bool:
    files = _row_files(table, row, columns, node_files)
    if files & stale:
        result.skipped_stale += 1
        return False
    return True


def merge_receipt(
    *,
    base_graph: str | Path,
    candidate_graph: str | Path,
    live_graph: str | Path,
    out_path: str | Path,
    source_revision: str,
    stale_paths: frozenset[str] | set[str] | tuple[str, ...],
    result: MergeResult,
    closure_rebuilt: bool = False,
) -> dict[str, Any]:
    """The sealed-receipt payload for one scoped merge."""
    stale_sorted = sorted(_norm(path) for path in stale_paths)
    return {
        "schema": "gt.scoped_merge_receipt.v1",
        "terminal": True,
        "status": "succeeded",
        "closure_rebuilt": closure_rebuilt,
        "source_revision": source_revision,
        "input_base_graph_sha256": _sha256(base_graph),
        "input_candidate_graph_sha256": _sha256(candidate_graph),
        "input_live_graph_sha256": _sha256(live_graph),
        "output_graph_sha256": _sha256(out_path),
        "stale_paths_sha256": hashlib.sha256(
            json.dumps(stale_sorted, separators=(",", ":")).encode()
        ).hexdigest(),
        "stale_path_count": len(stale_sorted),
        **result.detail,
    }


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
