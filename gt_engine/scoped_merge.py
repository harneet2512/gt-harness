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
# node map when a row is inserted.
_NODE_ROWID_REFS = {
    "edges": ("source_id", "target_id"),
    "properties": ("node_id",),
    "assertions": ("test_node_id", "target_node_id"),
    "nodes": ("parent_id",),
}


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
        str(row[1]) for row in con.execute(f"PRAGMA {schema}.table_info({table})")
    ]


def _rows(
    con: sqlite3.Connection, schema: str, table: str
) -> dict[int, tuple[Any, ...]]:
    return {
        int(row[0]): tuple(row[1:])
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
        int(rowid): str(file_path or "")
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
    try:
        merged.execute(f"ATTACH DATABASE '{base_uri}' AS base")
        merged.execute(f"ATTACH DATABASE '{candidate_uri}' AS cand")
        merged.execute("BEGIN")

        cand_node_files = _node_file_map(merged, "cand")
        nodes = _NodeSpace(merged)

        present = {
            str(row[0])
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
                        merged, table, columns, cand_row, nodes,
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
                assignments = ",".join(f"{col}=?" for col in columns)
                merged.execute(
                    f"UPDATE {table} SET {assignments} WHERE rowid=?",
                    (*cand_row, rowid),
                )
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
    for name, sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql LIKE '%fts5%'"
    ):
        match = re.search(r"content\s*=\s*'?\"?(\w+)'?\"?", sql or "")
        if match and match.group(1) in touched_tables:
            con.execute(f"INSERT INTO {name}({name}) VALUES('rebuild')")
            rebuilt += 1
    return rebuilt


def _insert_remapped(
    con: sqlite3.Connection,
    table: str,
    columns: list[str],
    cand_row: tuple[Any, ...],
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
    """
    values = list(cand_row)
    if "id" in columns:
        id_idx = columns.index("id")
        explicit = values[id_idx]
        if (
            isinstance(explicit, int)
            and not isinstance(explicit, bool)
            and explicit in merged_rows
            and merged_rows[explicit] != cand_row
        ):
            # The rowid is taken by a row from another lineage. The insert
            # keeps its content and takes a fresh rowid; the insert map
            # records the remap for anything referencing it.
            values[id_idx] = None
    for column in _NODE_ROWID_REFS.get(table, ()):
        if column not in columns:
            continue
        idx = columns.index(column)
        ref = values[idx]
        if not isinstance(ref, int) or isinstance(ref, bool):
            continue
        if ref in nodes.insert_map:
            values[idx] = nodes.insert_map[ref]
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
    if table == "nodes" and "stable_id" in columns:
        stable = values[columns.index("stable_id")]
        if stable:
            stable_idx = columns.index("stable_id")
            for merged_id, merged_row in nodes.merged.items():
                if merged_row[stable_idx] == stable:
                    return merged_id
    placeholders = ",".join("?" for _ in columns)
    cursor = con.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


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
