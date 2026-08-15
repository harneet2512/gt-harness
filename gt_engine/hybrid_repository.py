"""Bounded adapter from a certified GraphDB and checkout to hybrid retrieval.

The graph selects source spans and relationships; source bytes always come
from the exact checkout.  Missing, malformed, unsafe, or over-limit data is
reported and returned fail-open rather than fabricated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from gt_engine.graph_context import GraphProjection, build_graph_projection
from gt_engine.hybrid_retrieval import (
    EvidenceOrigin,
    RepositoryDocument,
    RetrievalIntent,
    RetrievalState,
    StructuralLink,
    retrieval_query_terms,
)
from gt_engine.task_contract import Obligation, TaskContract, extract_task_contract


@dataclass(frozen=True)
class RepositoryBuildLimits:
    max_documents: int = 50_000
    max_links: int = 200_000
    max_chunk_chars: int = 12_000
    max_total_chars: int = 50_000_000

    def __post_init__(self) -> None:
        for name in (
            "max_documents",
            "max_links",
            "max_chunk_chars",
            "max_total_chars",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")


def _merge_graph_projections(
    legacy: GraphProjection,
    augmentation: GraphProjection,
) -> GraphProjection:
    """Preserve the existing retrieval surface and append literal-query hits.

    The literal workflow query is an additive recall channel. It must not
    replace candidates selected by the established task-contract query. A
    graph revision change between the two bounded reads invalidates the older
    projection, so only the later projection is retained in that rare case.
    """

    if legacy.revision and augmentation.revision and legacy.revision != augmentation.revision:
        return augmentation
    surface_hits: dict[str, int] = dict(legacy.surface_hits)
    for surface, count in augmentation.surface_hits:
        surface_hits[surface] = max(surface_hits.get(surface, 0), int(count))
    return GraphProjection(
        files=legacy.files | augmentation.files,
        symbols=legacy.symbols | augmentation.symbols,
        node_ids=legacy.node_ids | augmentation.node_ids,
        surface_hits=tuple(sorted(surface_hits.items())),
        semantic_facts=tuple(
            dict.fromkeys((*legacy.semantic_facts, *augmentation.semantic_facts))
        ),
        revision=augmentation.revision or legacy.revision,
    )


@dataclass(frozen=True)
class HybridRepository:
    documents: tuple[RepositoryDocument, ...]
    structural_links: tuple[StructuralLink, ...]
    source_revision: str
    complete: bool
    reason_codes: tuple[str, ...]
    source_file_count: int
    document_chars: int


_DEFAULT_BUILD_LIMITS = RepositoryBuildLimits()

# A source span may be bounded for the retrieval corpus without invalidating
# the graph substrate. The bounded text remains certified by its path, graph
# node, and source revision; only its body is partial. All other build reasons
# remain fail-closed because they can remove or falsify repository evidence.
_NON_FATAL_BUILD_REASONS = frozenset({"chunk_character_limit"})


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def _canonical_repo_path(root: Path, raw_path: str) -> str | None:
    value = str(raw_path or "").strip().replace("\\", "/")
    if value.startswith("/app/"):
        value = value[len("/app/") :]
    candidate_path = Path(value)
    candidate = (
        candidate_path.resolve()
        if candidate_path.is_absolute()
        else (root / candidate_path).resolve()
    )
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def _read_source_span(
    root: Path,
    relative_path: str,
    start_line: int,
    end_line: int,
    *,
    max_chars: int,
    cache: dict[str, tuple[str, ...] | None],
) -> tuple[str, int, bool] | None:
    normalized = str(relative_path or "").replace("\\", "/").lstrip("/")
    candidate = (root / Path(normalized)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if normalized not in cache:
        try:
            cache[normalized] = tuple(
                candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        except OSError:
            cache[normalized] = None
    lines = cache[normalized]
    if lines is None:
        return None
    start = max(1, int(start_line or 1))
    end = max(start, int(end_line or start))
    selected = lines[start - 1 : end]
    text = "\n".join(selected)
    if len(text) <= max_chars:
        return text, end, False
    kept: list[str] = []
    used = 0
    for line in selected:
        added = len(line) + (1 if kept else 0)
        if kept and used + added > max_chars:
            break
        if not kept and len(line) > max_chars:
            kept.append(line[:max_chars])
            used = max_chars
            break
        kept.append(line)
        used += added
    bounded = "\n".join(kept)
    bounded_end = start + max(0, len(kept) - 1)
    return bounded, bounded_end, True


def build_hybrid_repository(
    repo_root: str | Path,
    graph_db: str | Path,
    *,
    source_revision: str,
    limits: RepositoryBuildLimits = _DEFAULT_BUILD_LIMITS,
    include_paths: tuple[str, ...] | None = None,
    include_node_ids: tuple[int, ...] | None = None,
    document_origins: Mapping[str, EvidenceOrigin] | None = None,
    origin_revisions: Mapping[str, str] | None = None,
    model_authored_paths: Iterable[str] = (),
    task_deliverables: Iterable[str] = (),
) -> HybridRepository:
    """Build deterministic retrieval documents and directed structural links."""

    root = Path(repo_root).resolve()
    normalized_origins = {
        str(path).replace("\\", "/"): (
            origin if isinstance(origin, EvidenceOrigin) else EvidenceOrigin(str(origin))
        )
        for path, origin in (document_origins or {}).items()
    }
    normalized_origins.update(
        {
            str(path).replace("\\", "/"): EvidenceOrigin.MODEL_AUTHORED
            for path in model_authored_paths
        }
    )
    normalized_origins.update(
        {
            str(path).replace("\\", "/"): EvidenceOrigin.TASK_DELIVERABLE
            for path in task_deliverables
        }
    )
    normalized_origin_revisions = {
        str(path).replace("\\", "/"): str(revision or "")
        for path, revision in (origin_revisions or {}).items()
    }
    for path in normalized_origins:
        normalized_origin_revisions.setdefault(path, source_revision)
    graph = Path(graph_db)
    if not graph.is_file():
        return HybridRepository(
            documents=(),
            structural_links=(),
            source_revision=source_revision,
            complete=False,
            reason_codes=("graph_unavailable",),
            source_file_count=0,
            document_chars=0,
        )
    try:
        connection = sqlite3.connect(
            f"file:{graph.resolve().as_posix()}?mode=ro",
            uri=True,
        )
    except sqlite3.Error:
        return HybridRepository(
            documents=(),
            structural_links=(),
            source_revision=source_revision,
            complete=False,
            reason_codes=("graph_unavailable",),
            source_file_count=0,
            document_chars=0,
        )

    reasons: list[str] = []
    documents: list[RepositoryDocument] = []
    links: list[StructuralLink] = []
    source_cache: dict[str, tuple[str, ...] | None] = {}
    total_chars = 0
    seen_links: set[tuple[str, str, str, tuple[str, ...]]] = set()

    def append_link(
        source_path: str | None,
        target_path: str | None,
        relation: str,
        confidence: float,
        provenance: tuple[str, ...],
        *,
        certified: bool = False,
        source_document: RepositoryDocument | None = None,
        target_document: RepositoryDocument | None = None,
    ) -> None:
        if (
            not source_path
            or not target_path
            or source_path == target_path
            or len(links) >= limits.max_links
        ):
            if len(links) >= limits.max_links:
                reasons.append("link_limit")
            return
        key = (source_path, target_path, relation, provenance)
        if key in seen_links:
            return
        seen_links.add(key)
        links.append(
            StructuralLink(
                source_path=source_path,
                target_path=target_path,
                relation=relation,
                confidence=max(0.0, min(1.0, float(confidence))),
                provenance=provenance,
                certified=bool(certified),
                source_symbol=(source_document.symbol if source_document else None),
                source_start_line=(
                    source_document.start_line if source_document else None
                ),
                target_symbol=(target_document.symbol if target_document else None),
                target_start_line=(
                    target_document.start_line if target_document else None
                ),
            )
        )

    try:
        node_columns = _columns(connection, "nodes")
        required_nodes = {"id", "name", "file_path", "start_line", "end_line"}
        if not required_nodes <= node_columns:
            return HybridRepository(
                documents=(),
                structural_links=(),
                source_revision=source_revision,
                complete=False,
                reason_codes=("nodes_table_unavailable",),
                source_file_count=0,
                document_chars=0,
            )
        signature = "COALESCE(signature,'')" if "signature" in node_columns else "''"
        canonical_include_paths = tuple(
            dict.fromkeys(
                path
                for raw_path in (include_paths or ())
                if (path := _canonical_repo_path(root, raw_path)) is not None
            )
        )
        path_clause = ""
        path_parameters: tuple[object, ...] = ()
        canonical_node_ids = tuple(
            dict.fromkeys(int(node_id) for node_id in (include_node_ids or ()) if int(node_id) > 0)
        )
        if include_node_ids is not None:
            if not canonical_node_ids:
                return HybridRepository(
                    documents=(),
                    structural_links=(),
                    source_revision=source_revision,
                    complete=True,
                    reason_codes=("query_no_candidates",),
                    source_file_count=0,
                    document_chars=0,
                )
            path_clause = "WHERE id IN (" + ",".join("?" for _ in canonical_node_ids) + ") "
            path_parameters = canonical_node_ids
        elif include_paths is not None:
            if not canonical_include_paths:
                return HybridRepository(
                    documents=(),
                    structural_links=(),
                    source_revision=source_revision,
                    complete=True,
                    reason_codes=("query_no_candidates",),
                    source_file_count=0,
                    document_chars=0,
                )
            path_clause = (
                "WHERE file_path IN (" + ",".join("?" for _ in canonical_include_paths) + ") "
            )
            path_parameters = canonical_include_paths
        node_query = (
            "SELECT id,name,file_path,start_line,end_line,"
            + f"{signature} FROM nodes "
            + path_clause
            + "ORDER BY lower(file_path),start_line,id LIMIT ?"
        )
        node_rows = tuple(
            connection.execute(
                node_query,
                (*path_parameters, limits.max_documents + 1),
            )
        )
        if len(node_rows) > limits.max_documents:
            reasons.append("document_limit")
            node_rows = node_rows[: limits.max_documents]

        seen_spans: set[tuple[str, int, int, str]] = set()
        loaded_node_paths: dict[int, str] = {}
        loaded_node_documents: dict[int, RepositoryDocument] = {}
        for raw_id, raw_name, raw_path, raw_start, raw_end, raw_signature in node_rows:
            path = _canonical_repo_path(root, str(raw_path or ""))
            start = max(1, int(raw_start or 1))
            end = max(start, int(raw_end or start))
            name = str(raw_name or "") or None
            if path is None:
                reasons.append("unsafe_source_path")
                continue
            key = (path.lower(), start, end, str(name or "").lower())
            loaded_node_paths[int(raw_id)] = path
            if not path or key in seen_spans:
                continue
            seen_spans.add(key)
            source = _read_source_span(
                root,
                path,
                start,
                end,
                max_chars=limits.max_chunk_chars,
                cache=source_cache,
            )
            if source is None:
                reasons.append("source_span_unavailable")
                continue
            text, bounded_end, truncated = source
            if not text:
                text = str(raw_signature or "")[: limits.max_chunk_chars]
            if not text:
                reasons.append("empty_source_span")
                continue
            if total_chars + len(text) > limits.max_total_chars:
                reasons.append("total_character_limit")
                break
            if truncated:
                reasons.append("chunk_character_limit")
            provenance = [f"graph_node:{int(raw_id)}", "checkout_source"]
            if truncated:
                provenance.append("bounded_source_span")
            document = RepositoryDocument(
                path=path,
                text=text,
                start_line=start,
                end_line=bounded_end,
                symbol=name,
                provenance=tuple(provenance),
                origin=normalized_origins.get(
                    path, EvidenceOrigin.PREEXISTING_REPOSITORY
                ),
                origin_revision=normalized_origin_revisions.get(path, source_revision),
            )
            documents.append(document)
            loaded_node_documents[int(raw_id)] = document
            total_chars += len(text)

        edge_columns = _columns(connection, "edges")
        required_edges = {"id", "source_id", "target_id", "type"}
        if required_edges <= edge_columns:
            confidence = "COALESCE(confidence,0.0)" if "confidence" in edge_columns else "0.0"
            trust_tier = "COALESCE(trust_tier,'')" if "trust_tier" in edge_columns else "''"
            loaded_ids = tuple(loaded_node_paths)
            relation_clause = ""
            relation_parameters: tuple[object, ...] = ()
            if include_paths is not None and loaded_ids:
                placeholders = ",".join("?" for _ in loaded_ids)
                relation_clause = (
                    f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders}) "
                )
                relation_parameters = (*loaded_ids, *loaded_ids)
            edge_query = (
                "SELECT id,source_id,target_id,type,"
                f"{confidence},{trust_tier} FROM edges " + relation_clause + "ORDER BY id LIMIT ?"
            )
            edge_rows = tuple(
                connection.execute(
                    edge_query,
                    (*relation_parameters, limits.max_links + 1),
                )
            )
            if len(edge_rows) > limits.max_links:
                reasons.append("link_limit")
                edge_rows = edge_rows[: limits.max_links]
            for edge_id, source_id, target_id, relation, confidence_value, trust in edge_rows:
                source_path = loaded_node_paths.get(int(source_id))
                target_path = loaded_node_paths.get(int(target_id))
                if not source_path or not target_path or source_path == target_path:
                    continue
                append_link(
                    source_path,
                    target_path,
                    str(relation or "related"),
                    float(confidence_value or 0.0),
                    (
                        f"graph_edge:{int(edge_id)}",
                        f"trust:{str(trust or 'unknown')}",
                    ),
                    certified=(
                        str(trust or "").upper() == "CERTIFIED"
                        and float(confidence_value or 0.0) >= 0.95
                    ),
                    source_document=loaded_node_documents.get(int(source_id)),
                    target_document=loaded_node_documents.get(int(target_id)),
                )

        assertion_columns = _columns(connection, "assertions")
        required_assertions = {
            "id",
            "test_node_id",
            "target_node_id",
            "resolution_score",
        }
        if required_assertions <= assertion_columns:
            assertion_clause = ""
            assertion_parameters: tuple[object, ...] = ()
            if include_paths is not None and loaded_node_paths:
                loaded_ids = tuple(loaded_node_paths)
                placeholders = ",".join("?" for _ in loaded_ids)
                assertion_clause = (
                    f"WHERE test_node_id IN ({placeholders}) OR target_node_id IN ({placeholders}) "
                )
                assertion_parameters = (*loaded_ids, *loaded_ids)
            assertion_rows = connection.execute(
                "SELECT id,test_node_id,target_node_id,resolution_score "
                "FROM assertions " + assertion_clause + "ORDER BY id LIMIT ?",
                (*assertion_parameters, limits.max_links + 1),
            )
            for assertion_id, test_id, target_id, score in assertion_rows:
                append_link(
                    loaded_node_paths.get(int(target_id or 0)),
                    loaded_node_paths.get(int(test_id or 0)),
                    "ASSERTED_BY",
                    float(score or 0.0),
                    (f"graph_assertion:{int(assertion_id)}", "test_assertion"),
                    certified=float(score or 0.0) >= 0.95,
                    source_document=loaded_node_documents.get(int(target_id or 0)),
                    target_document=loaded_node_documents.get(int(test_id or 0)),
                )

        closure_columns = _columns(connection, "closure")
        required_closure = {
            "source_id",
            "target_id",
            "depth",
            "min_confidence",
        }
        if required_closure <= closure_columns:
            closure_clause = ""
            closure_parameters: tuple[object, ...] = ()
            if include_paths is not None and loaded_node_paths:
                loaded_ids = tuple(loaded_node_paths)
                placeholders = ",".join("?" for _ in loaded_ids)
                closure_clause = f"WHERE source_id IN ({placeholders}) "
                closure_parameters = loaded_ids
            closure_rows = connection.execute(
                "SELECT source_id,target_id,depth,min_confidence FROM closure "
                + closure_clause
                + "ORDER BY source_id,target_id,depth LIMIT ?",
                (*closure_parameters, limits.max_links + 1),
            )
            for source_id, target_id, depth, confidence_value in closure_rows:
                append_link(
                    loaded_node_paths.get(int(source_id or 0)),
                    loaded_node_paths.get(int(target_id or 0)),
                    "CALLS_TRANSITIVE",
                    float(confidence_value or 0.0),
                    (f"graph_closure:depth={int(depth or 0)}", "verified_closure"),
                    certified=float(confidence_value or 0.0) >= 0.95,
                    source_document=loaded_node_documents.get(int(source_id or 0)),
                    target_document=loaded_node_documents.get(int(target_id or 0)),
                )

        cochange_columns = _columns(connection, "cochanges")
        if {"file_a", "file_b", "count"} <= cochange_columns:
            cochange_clause = ""
            cochange_parameters: tuple[object, ...] = ()
            if include_paths is not None and canonical_include_paths:
                placeholders = ",".join("?" for _ in canonical_include_paths)
                cochange_clause = f"WHERE file_a IN ({placeholders}) OR file_b IN ({placeholders}) "
                cochange_parameters = (*canonical_include_paths, *canonical_include_paths)
            cochange_rows = connection.execute(
                "SELECT file_a,file_b,count FROM cochanges "
                + cochange_clause
                + "ORDER BY count DESC,file_a,file_b LIMIT ?",
                (*cochange_parameters, limits.max_links + 1),
            )
            for raw_a, raw_b, count in cochange_rows:
                source_path = _canonical_repo_path(root, str(raw_a or ""))
                target_path = _canonical_repo_path(root, str(raw_b or ""))
                append_link(
                    source_path,
                    target_path,
                    "COCHANGE",
                    (max(0, int(count or 0)) / (max(0, int(count or 0)) + 1.0)),
                    (f"graph_cochange:count={int(count or 0)}", "git_cochange"),
                )

        cochange_set_columns = _columns(connection, "cochange_sets")
        if {"commit_hash", "file_path"} <= cochange_set_columns:
            cochange_set_rows = tuple(
                connection.execute(
                    "SELECT commit_hash,file_path FROM cochange_sets "
                    "ORDER BY commit_hash,file_path LIMIT ?",
                    (limits.max_links + 1,),
                )
            )
            if len(cochange_set_rows) > limits.max_links:
                reasons.append("link_limit")
                cochange_set_rows = cochange_set_rows[: limits.max_links]
            grouped: dict[str, list[str]] = {}
            for commit_hash, raw_path in cochange_set_rows:
                path = _canonical_repo_path(root, str(raw_path or ""))
                if path:
                    grouped.setdefault(str(commit_hash or ""), []).append(path)
            for commit_hash, paths in grouped.items():
                unique_paths = tuple(dict.fromkeys(paths))
                if len(unique_paths) < 2 or len(unique_paths) > 50:
                    continue
                for source_path in unique_paths:
                    for target_path in unique_paths:
                        if source_path >= target_path:
                            continue
                        append_link(
                            source_path,
                            target_path,
                            "COCHANGE_SET",
                            1.0 / (len(unique_paths) - 1),
                            (
                                f"graph_cochange_set:{commit_hash}",
                                "git_commit_membership",
                            ),
                        )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        reasons.append("graph_query_failed")
    finally:
        connection.close()

    reason_codes = tuple(dict.fromkeys(reasons))
    return HybridRepository(
        documents=tuple(documents),
        structural_links=tuple(links),
        source_revision=source_revision,
        complete=not any(reason not in _NON_FATAL_BUILD_REASONS for reason in reason_codes),
        reason_codes=reason_codes,
        source_file_count=sum(1 for lines in source_cache.values() if lines is not None),
        document_chars=total_chars,
    )


def build_query_hybrid_repository(
    repo_root: str | Path,
    graph_db: str | Path,
    state: RetrievalState,
    *,
    candidate_limit: int = 128,
    limits: RepositoryBuildLimits = _DEFAULT_BUILD_LIMITS,
) -> HybridRepository:
    """Materialize only task-conditioned graph/FTS candidates.

    This is the online retrieval path. It preserves the full repository
    builder for audits, while preventing each query from rescanning every
    indexed source span merely to discard almost all of them afterward.
    """

    contract = extract_task_contract(state.sparse_query_text())
    if not contract.obligations and state.sparse_query_text().strip():
        contract = TaskContract(
            role=contract.role,
            task_mode=contract.task_mode,
            predicates=contract.predicates,
            obligations=(
                Obligation(
                    obligation_id="retrieval:state",
                    text=" ".join(state.sparse_query_text().split())[:2_000],
                    source="retrieval_state",
                ),
            ),
        )
    graph = Path(graph_db)
    projection_kwargs = {
        "limit": max(8, int(candidate_limit)),
        "active_paths": tuple(
            dict.fromkeys((*state.active_paths, *state.changed_paths))
        ),
        "include_tests": state.intent is RetrievalIntent.VALIDATION_CONTEXT,
    }
    legacy_projection = build_graph_projection(
        str(graph),
        contract,
        **projection_kwargs,
    )
    literal_terms = retrieval_query_terms(state)
    if literal_terms:
        literal_projection = build_graph_projection(
            str(graph),
            contract,
            query_terms=literal_terms,
            **projection_kwargs,
        )
        projection = _merge_graph_projections(legacy_projection, literal_projection)
    else:
        projection = legacy_projection
    ordered_node_ids = tuple(
        dict.fromkeys(fact.node_id for fact in projection.semantic_facts if int(fact.node_id) > 0)
    )[: max(8, int(candidate_limit) * 4)]
    return build_hybrid_repository(
        repo_root,
        graph,
        source_revision=state.source_revision,
        limits=limits,
        include_paths=tuple(sorted(projection.files)),
        include_node_ids=ordered_node_ids,
    )


__all__ = [
    "HybridRepository",
    "RepositoryBuildLimits",
    "build_hybrid_repository",
    "build_query_hybrid_repository",
]
