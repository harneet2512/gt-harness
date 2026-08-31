"""Revision-bound hybrid retrieval and SQLite vector acceleration.

Identity-bound lexical/structural retrieval refuses to run when repository
identity disagrees with the central runtime binding.  The optional SQLite
vector table is an accelerator only: candidate discovery is followed by
exact, reproducible scoring over persisted source rows.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gt_engine.repository_intelligence import CentralRuntimeBinding, CentralRuntimeIdentityError

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


class RetrievalState(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    ABSTAINED = "abstained"


class RetrievalIntent(StrEnum):
    INSPECT = "inspect"
    EDIT = "edit"
    VALIDATE = "validate"


class RetrievalChannel(StrEnum):
    LEXICAL = "lexical"
    STRUCTURAL = "structural"


class EvidenceAuthority(StrEnum):
    GRAPH = "graph"
    CHECKOUT = "checkout"
    INFERRED = "inferred"


class EvidenceOrigin(StrEnum):
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    SOURCE_SPAN = "source_span"


@dataclass(frozen=True, slots=True)
class RepositoryDocument:
    path: str
    start_line: int = 1
    end_line: int = 1
    symbol: str = ""
    text: str = ""
    provenance: tuple[str, ...] = ()
    authority: EvidenceAuthority = EvidenceAuthority.CHECKOUT
    document_id: str = ""

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid document line range")
        if not self.document_id:
            value = f"{self.path}\0{self.start_line}\0{self.end_line}\0{self.symbol}"
            object.__setattr__(self, "document_id", "doc-" + hashlib.sha256(value.encode()).hexdigest()[:20])


@dataclass(frozen=True, slots=True)
class StructuralLink:
    source_path: str
    target_path: str
    relation: str
    confidence: float = 1.0
    provenance: tuple[str, ...] = ()
    certified: bool = False
    source_symbol: str = ""
    target_symbol: str = ""


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    path: str
    score: float
    channels: tuple[RetrievalChannel, ...] = ()
    document_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    owner_certified: bool = False
    authority: EvidenceAuthority = EvidenceAuthority.INFERRED


@dataclass(frozen=True, slots=True)
class RankedFile:
    path: str
    score: float
    rank: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    state: RetrievalState
    candidates: tuple[RetrievalCandidate, ...] = ()
    ranked_files: tuple[RankedFile, ...] = ()
    query_digest: str = ""
    repository_revision: str = ""
    graph_revision: str = ""
    fallback_reason: str = ""


def retrieval_query_terms(query: str) -> tuple[str, ...]:
    stop = {"a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "into", "is", "of", "on", "or", "the", "to", "with"}
    return tuple(dict.fromkeys(t.lower() for t in _TOKEN_RE.findall(query) if t.lower() not in stop))


class HybridRetriever:
    def __init__(self, documents: tuple[RepositoryDocument, ...] = (),
                 structural_links: tuple[StructuralLink, ...] = (), *,
                 runtime_binding: CentralRuntimeBinding | None = None) -> None:
        self.documents = tuple(documents)
        self.structural_links = tuple(structural_links)
        self.runtime_binding = runtime_binding

    def _validate_binding(self, *, repository_revision: str, graph_revision: str) -> None:
        if self.runtime_binding is None:
            raise CentralRuntimeIdentityError("hybrid retrieval requires central runtime binding")
        if self.runtime_binding.repository_revision != repository_revision:
            raise CentralRuntimeIdentityError("hybrid retrieval repository_revision mismatch")
        if self.runtime_binding.graph_revision != graph_revision:
            raise CentralRuntimeIdentityError("hybrid retrieval graph_revision mismatch")

    def retrieve(self, query: str, *, repository_revision: str, graph_revision: str,
                 intent: RetrievalIntent = RetrievalIntent.INSPECT, limit: int = 20,
                 include_tests: bool = True) -> HybridRetrievalResult:
        self._validate_binding(repository_revision=repository_revision, graph_revision=graph_revision)
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = retrieval_query_terms(query)
        digest = hashlib.sha256(query.encode()).hexdigest()
        scores: dict[str, float] = {}
        docs: dict[str, list[RepositoryDocument]] = {}
        reasons: dict[str, list[str]] = {}
        for doc in self.documents:
            docs.setdefault(doc.path, []).append(doc)
            haystack = f"{doc.path} {doc.symbol} {doc.text}".lower()
            hits = sum(haystack.count(t) for t in terms)
            if hits:
                scores[doc.path] = scores.get(doc.path, 0.0) + hits / max(1, len(terms))
                reasons.setdefault(doc.path, []).append(f"lexical:{hits}")
        for link in self.structural_links:
            if link.certified and link.confidence >= 1.0 and link.source_path in scores and link.target_path in docs:
                scores[link.target_path] = scores.get(link.target_path, 0.0) + 0.25
                reasons.setdefault(link.target_path, []).append(f"relation:{link.relation}")
        if not include_tests:
            scores = {p: s for p, s in scores.items() if not p.replace("\\", "/").startswith("tests/")}
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        candidates = []
        for path, score in ordered:
            links = [l for l in self.structural_links if l.target_path == path and l.certified and l.confidence >= 1.0]
            candidates.append(RetrievalCandidate(
                path=path, score=score,
                channels=(RetrievalChannel.LEXICAL, RetrievalChannel.STRUCTURAL) if links else (RetrievalChannel.LEXICAL,),
                document_ids=tuple(d.document_id for d in docs[path]),
                reasons=tuple(sorted(reasons.get(path, []))),
                owner_certified=bool(links) and intent is RetrievalIntent.EDIT,
                authority=EvidenceAuthority.GRAPH if links else EvidenceAuthority.CHECKOUT,
            ))
        ranked = tuple(RankedFile(c.path, c.score, i + 1, c.reasons) for i, c in enumerate(candidates))
        return HybridRetrievalResult(
            state=RetrievalState.READY if candidates else RetrievalState.EMPTY,
            candidates=tuple(candidates), ranked_files=ranked, query_digest=digest,
            repository_revision=repository_revision, graph_revision=graph_revision,
        )


def _as_vector(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding must contain finite values")
    return vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


@dataclass(frozen=True)
class EmbeddingRecord:
    """A source row and all identity needed to safely reuse its embedding."""

    document_id: str
    text: str
    embedding: tuple[float, ...]
    content_hash: str
    model_id: str
    tokenizer_id: str
    source_revision: str
    graph_revision: str

    def __post_init__(self) -> None:
        if not self.document_id or not self.content_hash:
            raise ValueError("document_id and content_hash are required")
        if not self.model_id or not self.tokenizer_id:
            raise ValueError("model and tokenizer identities are required")
        object.__setattr__(self, "embedding", _as_vector(self.embedding))

    @property
    def embedding_hash(self) -> str:
        encoded = json.dumps(self.embedding, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class HybridQuery:
    vector: tuple[float, ...]
    lexical_scores: Mapping[str, float] | None = None
    graph_scores: Mapping[str, float] | None = None
    limit: int = 10
    candidate_pool: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vector", _as_vector(self.vector))
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if self.candidate_pool is not None and self.candidate_pool < 1:
            raise ValueError("candidate_pool must be positive")


@dataclass(frozen=True)
class HybridItem:
    document_id: str
    exact_score: float
    vector_score: float
    lexical_score: float
    graph_score: float
    content_hash: str
    source_revision: str
    graph_revision: str


@dataclass(frozen=True)
class HybridQueryResult:
    items: tuple[HybridItem, ...]
    candidate_ids: tuple[str, ...]
    fallback_reason: str | None
    metadata_digest: str


class SQLiteVectorIndex:
    """A restartable, transactionally published vector corpus.

    ``extension_loader`` is injected by the runtime so extension discovery is
    observable and testable.  It must return true only after a real vec0
    extension has been loaded and health-checked.  The normal-library fallback
    deliberately does not pretend that a plain SQLite table is vec0.
    """

    INDEX_VERSION = "gt.sqlite.vec0.v1"

    def __init__(
        self,
        path: str | Path,
        *,
        model_id: str,
        tokenizer_id: str,
        dimension: int,
        source_revision: str,
        graph_revision: str,
        extension_loader: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._identity = {
            "model_id": model_id,
            "tokenizer_id": tokenizer_id,
            "dimension": str(dimension),
            "source_revision": source_revision,
            "graph_revision": graph_revision,
            "index_version": self.INDEX_VERSION,
        }
        self._metadata_mismatch = False
        self._initialize_schema()
        self._metadata_digest = self._read_metadata_digest()
        self._vec0_available = False
        self._vec0_error: str | None = None
        if extension_loader is not None:
            try:
                loaded = bool(extension_loader(self._connection))
                self._vec0_available = loaded and self._ensure_vec0_table()
            except (OSError, RuntimeError, sqlite3.Error):
                self._vec0_available = False
                self._vec0_error = "vec0_load_failed"

    def _ensure_vec0_table(self) -> bool:
        """Create and health-check the real vec0 virtual table after loading."""
        try:
            self._connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS gt_vector_vec0 "
                f"USING vec0(embedding float[{int(self._identity['dimension'])}])"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS gt_vector_vec0_map "
                "(rowid INTEGER PRIMARY KEY, document_id TEXT UNIQUE NOT NULL)"
            )
            self._connection.execute("SELECT rowid FROM gt_vector_vec0 LIMIT 0").fetchall()
            self._connection.commit()
            return True
        except sqlite3.Error:
            self._connection.rollback()
            self._vec0_error = "vec0_unavailable"
            return False

    @staticmethod
    def _vector_blob(vector: Sequence[float]) -> bytes:
        return struct.pack(f"<{len(vector)}f", *vector)

    def _initialize_schema(self) -> None:
        connection = self._connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gt_vector_index_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                model_id TEXT NOT NULL,
                tokenizer_id TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                source_revision TEXT NOT NULL,
                graph_revision TEXT NOT NULL,
                index_version TEXT NOT NULL,
                metadata_digest TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gt_vector_documents (
                document_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                embedding_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                model_id TEXT NOT NULL,
                tokenizer_id TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                source_revision TEXT NOT NULL,
                graph_revision TEXT NOT NULL
            )
            """
        )
        row = connection.execute(
            "SELECT model_id, tokenizer_id, dimension, source_revision, graph_revision, "
            "index_version "
            "FROM gt_vector_index_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None:
            digest = self._digest(self._identity)
            connection.execute(
                "INSERT INTO gt_vector_index_metadata VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._identity["model_id"],
                    self._identity["tokenizer_id"],
                    int(self._identity["dimension"]),
                    self._identity["source_revision"],
                    self._identity["graph_revision"],
                    self._identity["index_version"],
                    digest,
                ),
            )
        else:
            existing = dict(zip(self._identity, (str(value) for value in row), strict=True))
            self._metadata_mismatch = existing != self._identity
        connection.commit()

    @staticmethod
    def _digest(identity: Mapping[str, str]) -> str:
        payload = json.dumps(dict(sorted(identity.items())), separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _read_metadata_digest(self) -> str:
        row = self._connection.execute(
            "SELECT metadata_digest FROM gt_vector_index_metadata WHERE singleton = 1"
        ).fetchone()
        return str(row[0]) if row else ""

    def upsert(
        self,
        records: Sequence[EmbeddingRecord],
        *,
        delete_ids: Sequence[str] = (),
    ) -> None:
        if self._metadata_mismatch:
            raise ValueError("metadata_mismatch")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            for document_id in delete_ids:
                if self._vec0_available:
                    mapping = connection.execute(
                        "SELECT rowid FROM gt_vector_vec0_map WHERE document_id = ?",
                        (document_id,),
                    ).fetchone()
                    if mapping is not None:
                        connection.execute(
                            "DELETE FROM gt_vector_vec0 WHERE rowid = ?", (mapping[0],)
                        )
                        connection.execute(
                            "DELETE FROM gt_vector_vec0_map WHERE rowid = ?", (mapping[0],)
                        )
                connection.execute(
                    "DELETE FROM gt_vector_documents WHERE document_id = ?", (document_id,)
                )
            for record in records:
                if len(record.embedding) != int(self._identity["dimension"]):
                    raise ValueError("embedding_dimension_mismatch")
                if {
                    "model_id": record.model_id,
                    "tokenizer_id": record.tokenizer_id,
                    "dimension": str(len(record.embedding)),
                    "source_revision": record.source_revision,
                    "graph_revision": record.graph_revision,
                } != {
                    key: self._identity[key]
                    for key in (
                        "model_id",
                        "tokenizer_id",
                        "dimension",
                        "source_revision",
                        "graph_revision",
                    )
                }:
                    raise ValueError("record_identity_mismatch")
                connection.execute(
                    """
                    INSERT INTO gt_vector_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET
                        text=excluded.text,
                        embedding_json=excluded.embedding_json,
                        embedding_hash=excluded.embedding_hash,
                        content_hash=excluded.content_hash,
                        model_id=excluded.model_id,
                        tokenizer_id=excluded.tokenizer_id,
                        dimension=excluded.dimension,
                        source_revision=excluded.source_revision,
                        graph_revision=excluded.graph_revision
                    """,
                    (
                        record.document_id,
                        record.text,
                        json.dumps(record.embedding, separators=(",", ":")),
                        record.embedding_hash,
                        record.content_hash,
                        record.model_id,
                        record.tokenizer_id,
                        len(record.embedding),
                        record.source_revision,
                        record.graph_revision,
                    ),
                )
                if self._vec0_available:
                    mapping = connection.execute(
                        "SELECT rowid FROM gt_vector_vec0_map WHERE document_id = ?",
                        (record.document_id,),
                    ).fetchone()
                    if mapping is None:
                        cursor = connection.execute(
                            "INSERT INTO gt_vector_vec0(embedding) VALUES (?)",
                            (self._vector_blob(record.embedding),),
                        )
                        connection.execute(
                            "INSERT INTO gt_vector_vec0_map(rowid, document_id) VALUES (?, ?)",
                            (cursor.lastrowid, record.document_id),
                        )
                    else:
                        connection.execute(
                            "DELETE FROM gt_vector_vec0 WHERE rowid = ?", (mapping[0],)
                        )
                        connection.execute(
                            "INSERT INTO gt_vector_vec0(rowid, embedding) VALUES (?, ?)",
                            (mapping[0], self._vector_blob(record.embedding)),
                        )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def query(self, query: HybridQuery) -> HybridQueryResult:
        if self._metadata_mismatch:
            return HybridQueryResult((), (), "metadata_mismatch", self._metadata_digest)
        if len(query.vector) != int(self._identity["dimension"]):
            return HybridQueryResult((), (), "query_dimension_mismatch", self._metadata_digest)
        rows = self._connection.execute(
            "SELECT document_id, text, embedding_json, content_hash, source_revision, "
            "graph_revision "
            "FROM gt_vector_documents ORDER BY document_id"
        ).fetchall()
        if not rows:
            reason = None if self._vec0_available else "vec0_unavailable"
            return HybridQueryResult((), (), reason, self._metadata_digest)
        scored = []
        for row in rows:
            vector = tuple(float(value) for value in json.loads(row[2]))
            scored.append((row, _cosine(query.vector, vector)))
        if self._vec0_available:
            pool_size = min(
                len(scored), query.candidate_pool or max(query.limit * 4, query.limit)
            )
            try:
                ann_rows = self._connection.execute(
                    "SELECT m.document_id FROM gt_vector_vec0 "
                    "JOIN gt_vector_vec0_map AS m ON m.rowid = gt_vector_vec0.rowid "
                    "WHERE embedding MATCH ? AND k = ?",
                    (self._vector_blob(query.vector), pool_size),
                ).fetchall()
                ids = {str(row[0]) for row in ann_rows}
                candidate_rows = [item for item in scored if item[0][0] in ids]
                candidate_rows.sort(key=lambda item: (-item[1], item[0][0]))
                candidate_rows = candidate_rows[:pool_size]
                candidate_rows.sort(key=lambda item: item[0][0])
            except sqlite3.Error:
                self._vec0_error = "vec0_query_failed"
                self._vec0_available = False
                candidate_rows = sorted(scored, key=lambda item: item[0][0])
        else:
            candidate_rows = sorted(scored, key=lambda item: item[0][0])
        candidate_ids = tuple(row[0][0] for row in candidate_rows)
        lexical = query.lexical_scores or {}
        graph = query.graph_scores or {}
        items = []
        for row, vector_score in candidate_rows:
            lexical_score = float(lexical.get(row[0], 0.0))
            graph_score = float(graph.get(row[0], 0.0))
            exact_score = (0.5 * vector_score) + (0.2 * lexical_score) + (0.3 * graph_score)
            items.append(
                HybridItem(
                    document_id=row[0],
                    exact_score=exact_score,
                    vector_score=vector_score,
                    lexical_score=lexical_score,
                    graph_score=graph_score,
                    content_hash=row[3],
                    source_revision=row[4],
                    graph_revision=row[5],
                )
            )
        items.sort(key=lambda item: (-item.exact_score, item.document_id))
        fallback = None if self._vec0_available else "vec0_unavailable"
        return HybridQueryResult(
            tuple(items[: query.limit]), candidate_ids, fallback, self._metadata_digest
        )

    def close(self) -> None:
        self._connection.close()
