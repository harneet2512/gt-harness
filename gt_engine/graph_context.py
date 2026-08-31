"""Read-only projection of graph.db surfaces into task and verification context."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gt_engine.task_contract import TaskContract, significant_tokens

GRAPH_SURFACES = (
    "nodes",
    "nodes_fts",
    "symbol_content_fts",
    "content_passages",
    "content_passages_fts",
    "edges",
    "edge_metadata",
    "closure",
    "properties",
    "assertions",
    "cochanges",
    "cochange_sets",
    "file_hashes",
    "project_meta",
)
CAPABILITY_MATRIX_SCHEMA = "gt.capability_matrix.v2"
CAPABILITY_STATES = frozenset({"demonstrated", "partial", "absent", "stale"})
GITNEXUS_PINNED_REVISION = "7e993ab8972386294fb96bf14a8665d0b5325397"

# These are probes, not claims copied from a ticket.  A cell is produced only
# after the exact blob at the supplied revision has been read and its symbol
# located in that blob.
_CAPABILITY_PROBES = (
    ("indexing and freshness", "gt_engine/indexer.py", "ensure_index_with_receipt",
     "gitnexus/src/core/analysis-features.ts", "resolveAnalysisFeatureVersions"),
    ("hybrid retrieval", "gt_engine/graph_context.py", "build_graph_projection",
     "gitnexus/src/core/search/hybrid-search.ts", "hybridSearch"),
    ("community analysis", "gt_engine/graph_context.py", "build_graph_projection",
     "gitnexus/src/core/ingestion/community-processor.ts", "processCommunities"),
    ("resolution provenance", "gt_engine/resolution_provenance.py", "CallCandidate",
     "gitnexus/src/cli/eval-server.ts", "candidates"),
    ("feature lifecycle accounting", "gt_engine/attribution.py", "summarize_features",
     "gitnexus/src/storage/repo-meta.ts", "RepoMeta"),
    ("production query delivery", "gt_engine/bridge.py", "GTBridge",
     "gitnexus/src/cli/tool.ts", "output"),
)


def _git_blob(root: str | Path, revision: str, path: str) -> bytes | None:
    """Read one immutable Git blob; never inspect a working-tree substitute."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return None
    normalized = path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{normalized}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _symbol_line(blob: bytes, symbol: str) -> int | None:
    text = blob.decode("utf-8", "replace")
    match = re.search(rf"(?m)^.*\b{re.escape(symbol)}\b.*$", text)
    if match is None:
        return None
    return text.count("\n", 0, match.start()) + 1


def _source_cell(
    tool: str,
    capability: str,
    root: str | Path,
    revision: str,
    path: str,
    symbol: str,
) -> tuple[dict[str, Any], bytes] | None:
    blob = _git_blob(root, revision, path)
    if blob is None:
        return None
    line = _symbol_line(blob, symbol)
    citation: dict[str, Any] = {
        "tool": tool,
        "path": path,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "revision": revision,
    }
    if line is not None:
        citation.update({"symbol": symbol, "line": line})
    return {
        "tool": tool,
        "capability": capability,
        "state": "demonstrated" if line is not None else "partial",
        "citation": citation,
    }, blob


def graph_revision(graph_db: str) -> str:
    """Return a bounded revision token for the on-disk graph snapshot.

    The indexer replaces/updates ``graph.db`` as a unit.  Size plus nanosecond
    mtime is sufficient to distinguish snapshots inside one task without
    reading and hashing a potentially large SQLite database.  The path is
    included so a wake to a different database cannot alias the old revision.
    """
    try:
        stat = os.stat(graph_db)
    except (OSError, TypeError, ValueError):
        return ""
    material = (
        f"{os.path.abspath(graph_db)}\0{stat.st_size}\0{stat.st_mtime_ns}"
    )
    return hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()[:24]


@dataclass(frozen=True)
class GraphSemanticFact:
    surface: str
    node_id: int
    file_path: str
    symbol: str
    kind: str
    value: str
    line: int = 0
    confidence: float = 0.0
    revision: str = ""


@dataclass(frozen=True)
class GraphProjection:
    files: frozenset[str]
    symbols: frozenset[str]
    node_ids: frozenset[int]
    surface_hits: tuple[tuple[str, int], ...]
    semantic_facts: tuple[GraphSemanticFact, ...] = ()
    revision: str = ""


def build_capability_matrix(
    gt_root: str | Path,
    *,
    gt_revision: str,
    gitnexus_root: str | Path,
    gitnexus_revision: str = GITNEXUS_PINNED_REVISION,
) -> dict[str, Any]:
    """Generate a matrix from pinned source bytes and resolvable symbols."""
    if not re.fullmatch(r"[0-9a-f]{40}", gt_revision) or not re.fullmatch(
        r"[0-9a-f]{40}", gitnexus_revision
    ):
        raise ValueError("matrix revisions must be full Git SHAs")
    cells: list[dict[str, Any]] = []
    for capability, gt_path, gt_symbol, gn_path, gn_symbol in _CAPABILITY_PROBES:
        gt_cell = _source_cell("gt", capability, gt_root, gt_revision, gt_path, gt_symbol)
        gn_cell = _source_cell(
            "gitnexus", capability, gitnexus_root, gitnexus_revision, gn_path, gn_symbol
        )
        if gt_cell is None or gn_cell is None:
            raise ValueError(f"pinned source probe unavailable for {capability}")
        cells.extend((gt_cell[0], gn_cell[0]))
    cells.sort(key=lambda row: (row["capability"], row["tool"]))
    payload = {
        "schema": CAPABILITY_MATRIX_SCHEMA,
        "source_revision": gt_revision,
        "gitnexus_revision": gitnexus_revision,
        "cells": cells,
    }
    payload["matrix_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def verify_capability_matrix(
    matrix: dict[str, Any],
    roots: dict[str, str | Path],
) -> bool:
    """Verify digest, pinned revisions, paths, symbols, and immutable blobs."""
    if not isinstance(matrix, dict) or matrix.get("schema") != CAPABILITY_MATRIX_SCHEMA:
        return False
    cells = matrix.get("cells")
    if not isinstance(cells, list) or len(cells) != len(_CAPABILITY_PROBES) * 2:
        return False
    unsigned = {key: value for key, value in matrix.items() if key != "matrix_sha256"}
    digest = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if matrix.get("matrix_sha256") != digest:
        return False
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("state") not in CAPABILITY_STATES:
            return False
        citation = cell.get("citation", {})
        tool = citation.get("tool")
        path = citation.get("path")
        revision = citation.get("revision")
        if tool not in roots or not isinstance(path, str) or not isinstance(revision, str):
            return False
        blob = _git_blob(roots[tool], revision, path)
        if blob is None or hashlib.sha256(blob).hexdigest() != citation.get("sha256"):
            return False
        symbol = citation.get("symbol")
        line = citation.get("line")
        if symbol is not None and _symbol_line(blob, str(symbol)) != line:
            return False
    return True


def persist_capability_matrix(path: str | Path, matrix: dict[str, Any]) -> None:
    """Atomically persist the exact matrix bytes for later independent review."""
    destination = Path(path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(matrix, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def load_capability_matrix(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("capability matrix artifact must be an object")
    return value


def _connect(graph_db: str) -> sqlite3.Connection | None:
    if not graph_db or not os.path.isfile(graph_db):
        return None
    try:
        return sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _tables(con: sqlite3.Connection) -> set[str]:
    try:
        return {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
    except sqlite3.Error:
        return set()


def graph_surface_receipt(graph_db: str) -> dict[str, object]:
    counts = {name: 0 for name in GRAPH_SURFACES}
    con = _connect(graph_db)
    if con is None:
        return {"available": False, "surfaces": counts}
    try:
        present = _tables(con)
        for name in GRAPH_SURFACES:
            if name not in present:
                continue
            try:
                counts[name] = int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.Error:
                counts[name] = 0
        return {"available": True, "surfaces": counts}
    finally:
        con.close()


def graph_query_terms(
    contract: TaskContract,
    *,
    limit: int = 24,
) -> tuple[str, ...]:
    """Return decision anchors in specificity order, never alphabetic order."""
    subjects: list[str] = []
    tokens: list[str] = []
    for item in contract.obligations:
        tokens.extend(significant_tokens(item.text))
        subjects.extend(s.lower() for s in item.subjects)

    def clean(value: str) -> str:
        value = str(value or "").replace('"', "").strip().lower()
        return value if value.replace("_", "").replace(".", "").isalnum() else ""

    # Explicit paths/symbols are the strongest anchors. Remaining terms prefer
    # repeated obligation coverage and specificity (longer identifiers) while
    # retaining first occurrence as a deterministic final tie-break.
    ordered: list[str] = []
    for value in subjects:
        value = clean(value)
        if value and value not in ordered:
            ordered.append(value)
    first_seen: dict[str, int] = {}
    frequency: dict[str, int] = {}
    for index, raw in enumerate(tokens):
        value = clean(raw)
        if not value:
            continue
        first_seen.setdefault(value, index)
        frequency[value] = frequency.get(value, 0) + 1
    ranked = sorted(
        frequency,
        key=lambda value: (
            -frequency[value],
            -int("_" in value or "." in value),
            -len(value),
            first_seen[value],
        ),
    )
    for value in ranked:
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered[: max(1, int(limit))])


def _fts_query(contract: TaskContract) -> str:
    return " OR ".join(f'"{token}"' for token in graph_query_terms(contract))


def build_graph_projection(
    graph_db: str,
    contract: TaskContract,
    *,
    limit: int = 24,
) -> GraphProjection:
    """Use lexical, body, relation, closure, property, test, and cochange surfaces."""
    con = _connect(graph_db)
    if con is None:
        return GraphProjection(frozenset(), frozenset(), frozenset(), ())
    files: set[str] = set()
    symbols: set[str] = set()
    node_ids: set[int] = set()
    hits = {name: 0 for name in GRAPH_SURFACES}
    semantic_facts: list[GraphSemanticFact] = []
    revision = graph_revision(graph_db)
    try:
        tables = _tables(con)
        query = _fts_query(contract)
        if query and "nodes_fts" in tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,"
                    "snippet(nodes_fts,-1,'','',' ',12) FROM nodes_fts f "
                    "JOIN nodes n ON n.id=f.rowid WHERE nodes_fts MATCH ? "
                    "AND COALESCE(n.is_test,0)=0 ORDER BY bm25(nodes_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["nodes_fts"] += len(rows)
                for rank, (
                    node_id, file_path, name, excerpt
                ) in enumerate(rows, 1):
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(GraphSemanticFact(
                        "nodes_fts",
                        int(node_id),
                        str(file_path).replace("\\", "/"),
                        str(name),
                        "ranked_symbol",
                        str(excerpt or f"{file_path}:{name}")[:500],
                        confidence=max(0.5, 1.0 - ((rank - 1) / max(1, limit))),
                        revision=revision,
                    ))
            except sqlite3.Error:
                pass
        if query and {"symbol_content_fts", "nodes"} <= tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,"
                    "snippet(symbol_content_fts,0,'','',' ',12) "
                    "FROM symbol_content_fts f "
                    "JOIN nodes n ON n.id=f.rowid "
                    "WHERE symbol_content_fts MATCH ? AND COALESCE(n.is_test,0)=0 "
                    "ORDER BY bm25(symbol_content_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["symbol_content_fts"] += len(rows)
                for rank, (
                    node_id, file_path, name, excerpt
                ) in enumerate(rows, 1):
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(GraphSemanticFact(
                        "symbol_content_fts",
                        int(node_id),
                        str(file_path).replace("\\", "/"),
                        str(name),
                        "ranked_body",
                        str(excerpt or f"{file_path}:{name}")[:500],
                        confidence=max(0.5, 1.0 - ((rank - 1) / max(1, limit))),
                        revision=revision,
                    ))
            except sqlite3.Error:
                pass
        if query and {"content_passages_fts", "content_passages", "nodes"} <= tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,p.content,p.start_line "
                    "FROM content_passages_fts f "
                    "JOIN content_passages p ON p.passage_id=f.rowid "
                    "JOIN nodes n ON n.id=p.node_id "
                    "WHERE content_passages_fts MATCH ? AND COALESCE(n.is_test,0)=0 "
                    "ORDER BY bm25(content_passages_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["content_passages_fts"] += len(rows)
                hits["content_passages"] += len(rows)
                for rank, (
                    node_id, file_path, name, excerpt, start_line
                ) in enumerate(rows, 1):
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(GraphSemanticFact(
                        "content_passages_fts",
                        int(node_id),
                        str(file_path).replace("\\", "/"),
                        str(name),
                        "ranked_passage",
                        str(excerpt or f"{file_path}:{name}")[:500],
                        int(start_line or 0),
                        confidence=max(0.5, 1.0 - ((rank - 1) / max(1, limit))),
                        revision=revision,
                    ))
            except sqlite3.Error:
                pass

        seed_ids = sorted(node_ids)[:limit]
        if seed_ids and {"edges", "nodes"} <= tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                rows = con.execute(
                    "SELECT DISTINCT n.id,n.file_path,n.name FROM edges e "
                    "JOIN nodes n ON n.id=CASE WHEN e.source_id IN ("
                    + placeholders
                    + ") THEN e.target_id ELSE e.source_id END "
                    "WHERE (e.source_id IN ("
                    + placeholders
                    + ") OR e.target_id IN ("
                    + placeholders
                    + ")) AND e.confidence>=0.7 AND COALESCE(n.is_test,0)=0 "
                    "LIMIT ?",
                    (*seed_ids, *seed_ids, *seed_ids, limit),
                ).fetchall()
                hits["edges"] += len(rows)
                for node_id, file_path, name in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
            except sqlite3.Error:
                pass
        if seed_ids and {"closure", "nodes"} <= tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                rows = con.execute(
                    "SELECT DISTINCT n.id,n.file_path,n.name FROM closure c "
                    "JOIN nodes n ON n.id=c.target_id WHERE c.source_id IN ("
                    + placeholders
                    + ") AND c.depth<=2 AND c.min_confidence>=0.5 "
                    "AND COALESCE(n.is_test,0)=0 LIMIT ?",
                    (*seed_ids, limit),
                ).fetchall()
                hits["closure"] += len(rows)
                for node_id, file_path, name in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
            except sqlite3.Error:
                pass
        if seed_ids and "properties" in tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                hits["properties"] = int(
                    con.execute(
                        "SELECT COUNT(*) FROM properties WHERE node_id IN ("
                        + placeholders
                        + ")",
                        seed_ids,
                    ).fetchone()[0]
                )
                rows = con.execute(
                    "SELECT p.node_id,n.file_path,n.name,p.kind,p.value,"
                    "COALESCE(p.line,0),COALESCE(p.confidence,1.0) "
                    "FROM properties p JOIN nodes n ON n.id=p.node_id "
                    "WHERE p.node_id IN (" + placeholders + ") "
                    "ORDER BY COALESCE(p.confidence,1.0) DESC LIMIT ?",
                    (*seed_ids, limit),
                ).fetchall()
                semantic_facts.extend(
                    GraphSemanticFact(
                        "properties",
                        int(node_id),
                        str(path).replace("\\", "/"),
                        str(symbol),
                        str(kind),
                        str(value)[:500],
                        int(line or 0),
                        float(confidence or 0.0),
                        revision,
                    )
                    for node_id, path, symbol, kind, value, line, confidence
                    in rows
                )
            except sqlite3.Error:
                pass
        if seed_ids and "assertions" in tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                hits["assertions"] = int(
                    con.execute(
                        "SELECT COUNT(*) FROM assertions WHERE target_node_id IN ("
                        + placeholders
                        + ")",
                        seed_ids,
                    ).fetchone()[0]
                )
                rows = con.execute(
                    "SELECT a.target_node_id,n.file_path,n.name,a.kind,"
                    "a.expression,COALESCE(a.line,0),"
                    "COALESCE(a.resolution_score,0.0) "
                    "FROM assertions a JOIN nodes n ON n.id=a.target_node_id "
                    "WHERE a.target_node_id IN (" + placeholders + ") "
                    "ORDER BY COALESCE(a.resolution_score,0.0) DESC LIMIT ?",
                    (*seed_ids, limit),
                ).fetchall()
                semantic_facts.extend(
                    GraphSemanticFact(
                        "assertions",
                        int(node_id),
                        str(path).replace("\\", "/"),
                        str(symbol),
                        str(kind),
                        str(value)[:500],
                        int(line or 0),
                        float(confidence or 0.0),
                        revision,
                    )
                    for node_id, path, symbol, kind, value, line, confidence
                    in rows
                )
            except sqlite3.Error:
                pass
        if seed_ids and {"edge_metadata", "edges", "nodes"} <= tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                rows = con.execute(
                    "SELECT e.target_id,n.file_path,n.name,em.key,em.value,"
                    "0,COALESCE(e.confidence,0.0) "
                    "FROM edge_metadata em JOIN edges e ON e.id=em.edge_id "
                    "JOIN nodes n ON n.id=e.target_id "
                    "WHERE e.source_id IN (" + placeholders + ") "
                    "OR e.target_id IN (" + placeholders + ") LIMIT ?",
                    (*seed_ids, *seed_ids, limit),
                ).fetchall()
                hits["edge_metadata"] += len(rows)
                semantic_facts.extend(
                    GraphSemanticFact(
                        "edge_metadata",
                        int(node_id),
                        str(path).replace("\\", "/"),
                        str(symbol),
                        str(kind),
                        str(value)[:500],
                        int(line or 0),
                        float(confidence or 0.0),
                        revision,
                    )
                    for node_id, path, symbol, kind, value, line, confidence
                    in rows
                )
            except sqlite3.Error:
                pass
        if files and "file_hashes" in tables:
            base_files = sorted(files)[:limit]
            placeholders = ",".join("?" for _ in base_files)
            try:
                rows = con.execute(
                    "SELECT file_path,content_hash,COALESCE(language,''),"
                    "indexed_at FROM file_hashes WHERE file_path IN ("
                    + placeholders + ") LIMIT ?",
                    (*base_files, limit),
                ).fetchall()
                hits["file_hashes"] += len(rows)
                semantic_facts.extend(
                    GraphSemanticFact(
                        "file_hashes",
                        0,
                        str(path).replace("\\", "/"),
                        "",
                        str(language),
                        f"{content_hash}:{indexed_at}"[:500],
                        revision=revision,
                    )
                    for path, content_hash, language, indexed_at in rows
                )
            except sqlite3.Error:
                pass
        if "project_meta" in tables:
            try:
                rows = con.execute(
                    "SELECT key,value FROM project_meta ORDER BY key LIMIT ?",
                    (limit,),
                ).fetchall()
                hits["project_meta"] += len(rows)
                semantic_facts.extend(
                    GraphSemanticFact(
                        "project_meta",
                        0,
                        "",
                        "",
                        str(key),
                        str(value)[:500],
                        revision=revision,
                    )
                    for key, value in rows
                )
            except sqlite3.Error:
                pass
        if files and "cochanges" in tables:
            base_files = sorted(files)[:limit]
            placeholders = ",".join("?" for _ in base_files)
            try:
                rows = con.execute(
                    "SELECT file_a,file_b FROM cochanges WHERE file_a IN ("
                    + placeholders
                    + ") OR file_b IN ("
                    + placeholders
                    + ") ORDER BY count DESC LIMIT ?",
                    (*base_files, *base_files, limit),
                ).fetchall()
                hits["cochanges"] += len(rows)
                for left, right in rows:
                    files.update(
                        {str(left).replace("\\", "/"), str(right).replace("\\", "/")}
                    )
            except sqlite3.Error:
                pass
        if files and "cochange_sets" in tables:
            base_files = sorted(files)[:limit]
            placeholders = ",".join("?" for _ in base_files)
            try:
                commits = [
                    row[0]
                    for row in con.execute(
                        "SELECT DISTINCT commit_hash FROM cochange_sets "
                        "WHERE file_path IN (" + placeholders + ") LIMIT ?",
                        (*base_files, limit),
                    ).fetchall()
                ]
                if commits:
                    commit_ph = ",".join("?" for _ in commits)
                    rows = con.execute(
                        "SELECT DISTINCT file_path FROM cochange_sets "
                        "WHERE commit_hash IN (" + commit_ph + ") LIMIT ?",
                        (*commits, limit),
                    ).fetchall()
                    hits["cochange_sets"] += len(rows)
                    files.update(str(row[0]).replace("\\", "/") for row in rows)
            except sqlite3.Error:
                pass
        return GraphProjection(
            files=frozenset(files),
            symbols=frozenset(symbols),
            node_ids=frozenset(node_ids),
            surface_hits=tuple(sorted((k, v) for k, v in hits.items() if v)),
            semantic_facts=tuple(semantic_facts),
            revision=revision,
        )
    finally:
        con.close()
