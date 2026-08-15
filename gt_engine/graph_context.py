"""Read-only projection of graph.db surfaces into task and verification context."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from gt_engine.task_contract import TaskContract, TaskResourceRole, significant_tokens

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
    material = f"{os.path.abspath(graph_db)}\0{stat.st_size}\0{stat.st_mtime_ns}"
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
    semantic_certainty: float = 0.0
    retrieval_relevance: float = 0.0


@dataclass(frozen=True)
class GraphProjection:
    files: frozenset[str]
    symbols: frozenset[str]
    node_ids: frozenset[int]
    surface_hits: tuple[tuple[str, int], ...]
    semantic_facts: tuple[GraphSemanticFact, ...] = ()
    revision: str = ""


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
            for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
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
    resource_terms: list[str] = []
    for resource in contract.resources:
        if resource.confidence < 0.8 or resource.role not in {
            TaskResourceRole.INPUT,
            TaskResourceRole.REFERENCE,
            TaskResourceRole.EXECUTABLE,
        }:
            continue
        path = str(resource.path or "").replace("\\", "/").strip("/").lower()
        if not path:
            continue
        basename = path.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0]
        resource_terms.extend((path, basename, stem))
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
    for value in (*resource_terms, *subjects):
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


def _fts_query(
    contract: TaskContract,
    *,
    query_terms: tuple[str, ...] | None = None,
) -> str:
    terms = graph_query_terms(contract) if query_terms is None else query_terms
    safe_terms = tuple(
        dict.fromkeys(
            str(token or "").replace('"', "").strip().lower()
            for token in terms
            if str(token or "").replace("_", "").replace(".", "").isalnum()
        )
    )
    return " OR ".join(f'"{token}"' for token in safe_terms if token)


def build_graph_projection(
    graph_db: str,
    contract: TaskContract,
    *,
    limit: int = 24,
    active_paths: tuple[str, ...] = (),
    include_tests: bool = False,
    query_terms: tuple[str, ...] | None = None,
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
        normalized_active_paths: list[str] = []
        for raw_path in active_paths:
            path = str(raw_path or "").strip().replace("\\", "/")
            if path.startswith("/app/"):
                path = path[5:]
            elif path.startswith("./"):
                path = path[2:]
            if path and not path.startswith("/") and ".." not in Path(path).parts:
                normalized_active_paths.append(path)
        normalized_active_paths = list(dict.fromkeys(normalized_active_paths))
        if normalized_active_paths and "nodes" in tables:
            try:
                placeholders = ",".join("?" for _ in normalized_active_paths)
                rows = con.execute(
                    "SELECT id,file_path,name,COALESCE(start_line,0),"
                    "COALESCE(signature,''),COALESCE(language,'') FROM nodes "
                    "WHERE file_path IN (" + placeholders + ") "
                    "AND COALESCE(is_test,0)=0 ORDER BY file_path,start_line,id LIMIT ?",
                    (*normalized_active_paths, limit),
                ).fetchall()
                hits["nodes"] += len(rows)
                for node_id, file_path, name, start_line, signature, _language in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            surface="nodes",
                            node_id=int(node_id),
                            file_path=str(file_path).replace("\\", "/"),
                            symbol=str(name),
                            kind="active_path_symbol",
                            value=str(signature or f"{file_path}:{name}")[:500],
                            line=int(start_line or 0),
                            confidence=1.0 if int(start_line or 0) > 0 else 0.0,
                            revision=revision,
                            semantic_certainty=1.0 if int(start_line or 0) > 0 else 0.0,
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass
        query = _fts_query(contract, query_terms=query_terms)
        if query and "nodes_fts" in tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,COALESCE(n.start_line,0),"
                    "COALESCE(n.signature,''),"
                    "snippet(nodes_fts,-1,'','',' ',12) FROM nodes_fts f "
                    "JOIN nodes n ON n.id=f.rowid WHERE nodes_fts MATCH ? "
                    + ("" if include_tests else "AND COALESCE(n.is_test,0)=0 ")
                    + "ORDER BY bm25(nodes_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["nodes_fts"] += len(rows)
                for node_id, file_path, name, start_line, signature, excerpt in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            surface="nodes_fts",
                            node_id=int(node_id),
                            file_path=str(file_path).replace("\\", "/"),
                            symbol=str(name),
                            kind="ranked_symbol",
                            value=str(signature or excerpt or f"{file_path}:{name}")[:500],
                            line=int(start_line or 0),
                            # FTS rank orders candidates; it is not evidence
                            # that a candidate is relevant to the current
                            # decision.  The downstream evidence linker owns
                            # that certification.
                            confidence=1.0 if int(start_line or 0) > 0 else 0.0,
                            revision=revision,
                            semantic_certainty=1.0 if int(start_line or 0) > 0 else 0.0,
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass
        if query and {"symbol_content_fts", "nodes"} <= tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,COALESCE(n.start_line,0),"
                    "COALESCE(n.signature,''),"
                    "snippet(symbol_content_fts,0,'','',' ',12) "
                    "FROM symbol_content_fts f "
                    "JOIN nodes n ON n.id=f.rowid "
                    "WHERE symbol_content_fts MATCH ? "
                    + ("" if include_tests else "AND COALESCE(n.is_test,0)=0 ")
                    + "ORDER BY bm25(symbol_content_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["symbol_content_fts"] += len(rows)
                for node_id, file_path, name, start_line, signature, excerpt in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            surface="symbol_content_fts",
                            node_id=int(node_id),
                            file_path=str(file_path).replace("\\", "/"),
                            symbol=str(name),
                            kind="ranked_body",
                            value=str(signature or excerpt or f"{file_path}:{name}")[:500],
                            line=int(start_line or 0),
                            confidence=1.0 if int(start_line or 0) > 0 else 0.0,
                            revision=revision,
                            semantic_certainty=1.0 if int(start_line or 0) > 0 else 0.0,
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass
        if query and {"content_passages_fts", "content_passages", "nodes"} <= tables:
            try:
                rows = con.execute(
                    "SELECT n.id,n.file_path,n.name,p.content,p.start_line "
                    "FROM content_passages_fts f "
                    "JOIN content_passages p ON p.passage_id=f.rowid "
                    "JOIN nodes n ON n.id=p.node_id "
                    "WHERE content_passages_fts MATCH ? "
                    + ("" if include_tests else "AND COALESCE(n.is_test,0)=0 ")
                    + "ORDER BY bm25(content_passages_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                hits["content_passages_fts"] += len(rows)
                hits["content_passages"] += len(rows)
                for node_id, file_path, name, excerpt, start_line in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            "content_passages_fts",
                            int(node_id),
                            str(file_path).replace("\\", "/"),
                            str(name),
                            "ranked_passage",
                            str(excerpt or f"{file_path}:{name}")[:500],
                            int(start_line or 0),
                            confidence=1.0 if int(start_line or 0) > 0 else 0.0,
                            revision=revision,
                            semantic_certainty=1.0 if int(start_line or 0) > 0 else 0.0,
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass

        # Preserve the retrieval order that produced the seed.  Sorting node
        # identifiers here silently replaced FTS/BM25 relevance order with an
        # index-allocation accident.
        seed_ids = list(
            dict.fromkeys(
                fact.node_id
                for fact in semantic_facts
                if fact.node_id > 0
                and fact.surface
                in {
                    "nodes",
                    "nodes_fts",
                    "symbol_content_fts",
                    "content_passages_fts",
                }
            )
        )[:limit]
        if seed_ids and {"edges", "nodes"} <= tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                rows = con.execute(
                    "SELECT DISTINCT n.id,n.file_path,n.name,"
                    "COALESCE(n.start_line,0),e.type,COALESCE(e.confidence,0.0),"
                    "COALESCE(e.trust_tier,''),e.source_id,e.target_id,"
                    "src.file_path,dst.file_path FROM edges e "
                    "JOIN nodes n ON n.id=CASE WHEN e.source_id IN ("
                    + placeholders
                    + ") THEN e.target_id ELSE e.source_id END "
                    "JOIN nodes src ON src.id=e.source_id "
                    "JOIN nodes dst ON dst.id=e.target_id "
                    "WHERE (e.source_id IN ("
                    + placeholders
                    + ") OR e.target_id IN ("
                    + placeholders
                    + ")) AND e.confidence>=0.7 "
                    "ORDER BY COALESCE(e.confidence,0.0) DESC,e.id LIMIT ?",
                    (*seed_ids, *seed_ids, *seed_ids, limit),
                ).fetchall()
                hits["edges"] += len(rows)
                for (
                    node_id,
                    file_path,
                    name,
                    start_line,
                    edge_type,
                    confidence,
                    _trust_tier,
                    _source_id,
                    _target_id,
                    source_path,
                    target_path,
                ) in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            "edges",
                            int(node_id),
                            str(file_path).replace("\\", "/"),
                            str(name),
                            str(edge_type),
                            f"{edge_type}:{source_path}->{target_path}",
                            int(start_line or 0),
                            float(confidence or 0.0),
                            revision,
                            semantic_certainty=float(confidence or 0.0),
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass
        if seed_ids and {"closure", "nodes"} <= tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                rows = con.execute(
                    "SELECT DISTINCT n.id,n.file_path,n.name,"
                    "COALESCE(n.start_line,0),c.depth,"
                    "COALESCE(c.min_confidence,0.0),c.source_id,c.target_id,"
                    "src.file_path FROM closure c "
                    "JOIN nodes n ON n.id=c.target_id "
                    "JOIN nodes src ON src.id=c.source_id WHERE c.source_id IN ("
                    + placeholders
                    + ") AND c.depth<=2 AND c.min_confidence>=0.5 "
                    "ORDER BY c.depth,COALESCE(c.min_confidence,0.0) DESC,c.target_id LIMIT ?",
                    (*seed_ids, limit),
                ).fetchall()
                hits["closure"] += len(rows)
                for (
                    node_id,
                    file_path,
                    name,
                    start_line,
                    depth,
                    confidence,
                    _source_id,
                    _target_id,
                    source_path,
                ) in rows:
                    node_ids.add(int(node_id))
                    files.add(str(file_path).replace("\\", "/"))
                    symbols.add(str(name))
                    semantic_facts.append(
                        GraphSemanticFact(
                            "closure",
                            int(node_id),
                            str(file_path).replace("\\", "/"),
                            str(name),
                            "closure",
                            f"depth={depth}:{source_path}->{file_path}",
                            int(start_line or 0),
                            float(confidence or 0.0),
                            revision,
                            semantic_certainty=float(confidence or 0.0),
                            retrieval_relevance=0.0,
                        )
                    )
            except sqlite3.Error:
                pass
        if seed_ids and "properties" in tables:
            placeholders = ",".join("?" for _ in seed_ids)
            try:
                hits["properties"] = int(
                    con.execute(
                        "SELECT COUNT(*) FROM properties WHERE node_id IN (" + placeholders + ")",
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
                    for node_id, path, symbol, kind, value, line, confidence in rows
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
                    "SELECT a.test_node_id,n.file_path,n.name,a.kind,"
                    "a.expression,COALESCE(a.line,0),"
                    "COALESCE(a.resolution_score,0.0),target.file_path "
                    "FROM assertions a JOIN nodes n ON n.id=a.test_node_id "
                    "JOIN nodes target ON target.id=a.target_node_id "
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
                        f"{value} [target:{target_path}]"[:500],
                        int(line or 0),
                        float(confidence or 0.0),
                        revision,
                    )
                    for node_id, path, symbol, kind, value, line, confidence, target_path in rows
                )
                files.update(str(row[1]).replace("\\", "/") for row in rows)
                node_ids.update(int(row[0]) for row in rows)
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
                    for node_id, path, symbol, kind, value, line, confidence in rows
                )
            except sqlite3.Error:
                pass
        if files and "file_hashes" in tables:
            base_files = sorted(files)[:limit]
            placeholders = ",".join("?" for _ in base_files)
            try:
                rows = con.execute(
                    "SELECT file_path,content_hash,COALESCE(language,''),"
                    "indexed_at FROM file_hashes WHERE file_path IN (" + placeholders + ") LIMIT ?",
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
                    "SELECT file_a,file_b,count FROM cochanges WHERE file_a IN ("
                    + placeholders
                    + ") OR file_b IN ("
                    + placeholders
                    + ") ORDER BY count DESC LIMIT ?",
                    (*base_files, *base_files, limit),
                ).fetchall()
                hits["cochanges"] += len(rows)
                seed_files = set(base_files)
                for left, right, count in rows:
                    normalized_left = str(left).replace("\\", "/")
                    normalized_right = str(right).replace("\\", "/")
                    files.update({normalized_left, normalized_right})
                    for partner, seed in (
                        (normalized_right, normalized_left),
                        (normalized_left, normalized_right),
                    ):
                        if seed not in seed_files or partner in seed_files:
                            continue
                        semantic_facts.append(
                            GraphSemanticFact(
                                "cochanges",
                                0,
                                partner,
                                "",
                                "cochange",
                                f"cochange_with:{seed}:count={int(count or 0)}",
                                confidence=1.0,
                                revision=revision,
                                semantic_certainty=1.0,
                                retrieval_relevance=0.0,
                            )
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
                        "SELECT DISTINCT file_path,commit_hash FROM cochange_sets "
                        "WHERE commit_hash IN (" + commit_ph + ") LIMIT ?",
                        (*commits, limit),
                    ).fetchall()
                    hits["cochange_sets"] += len(rows)
                    for file_path, commit_hash in rows:
                        normalized = str(file_path).replace("\\", "/")
                        files.add(normalized)
                        if normalized in base_files:
                            continue
                        semantic_facts.append(
                            GraphSemanticFact(
                                "cochange_sets",
                                0,
                                normalized,
                                "",
                                "cochange_set",
                                f"commit:{commit_hash}",
                                confidence=1.0,
                                revision=revision,
                                semantic_certainty=1.0,
                                retrieval_relevance=0.0,
                            )
                        )
            except sqlite3.Error:
                pass
        retrieval_surfaces = {
            "nodes": 4,
            "nodes_fts": 3,
            "symbol_content_fts": 2,
            "content_passages_fts": 1,
        }
        canonical_retrieval: dict[int, GraphSemanticFact] = {}
        retained: list[GraphSemanticFact] = []
        for fact in semantic_facts:
            if fact.surface not in retrieval_surfaces or fact.node_id <= 0:
                retained.append(fact)
                continue
            prior = canonical_retrieval.get(fact.node_id)
            if prior is None or (
                fact.semantic_certainty,
                retrieval_surfaces[fact.surface],
                fact.retrieval_relevance,
            ) > (
                prior.semantic_certainty,
                retrieval_surfaces[prior.surface],
                prior.retrieval_relevance,
            ):
                canonical_retrieval[fact.node_id] = fact
        # Dict insertion order retains FTS/BM25 order while still collapsing
        # duplicate node surfaces.  Node ids have no relevance semantics.
        retained.extend(canonical_retrieval.values())
        return GraphProjection(
            files=frozenset(files),
            symbols=frozenset(symbols),
            node_ids=frozenset(node_ids),
            surface_hits=tuple(sorted((k, v) for k, v in hits.items() if v)),
            semantic_facts=tuple(retained),
            revision=revision,
        )
    finally:
        con.close()
