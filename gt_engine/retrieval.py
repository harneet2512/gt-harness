"""Hybrid retrieval over the graph surfaces GT already populates.

Why this exists
---------------
This is the symbol-level companion to :mod:`gt_engine.hybrid_retrieval`, not a
replacement for it.  That module is revision-bound and identity-guarded, ranks
*files* (``RankedFile``) over persisted repository documents, and refuses to run
when repository identity disagrees with the central runtime binding.  This one
ranks *symbols* over the three surfaces the graph itself populates and makes no
identity guarantee beyond recording provenance; a caller that needs the binding
check keeps using ``HybridRetriever``.

The graph stores three independently-derived descriptions of the same symbol
and symbol-level delivery could previously reach only one.  ``nodes_fts`` indexes
identifiers -- ``name``, ``qualified_name``, ``signature``, ``file_path`` --
which answers "what is this called".  ``properties`` stores the extracted
behavioural facts -- ``guard_clause``, ``boundary_condition``, ``side_effect``,
``param``, ``return_shape``, ``data_flow`` -- which answers "what does this
do".  The ONNX runtime in :mod:`gt_engine.dense_runtime` answers "what is this
like".  A query phrased as behaviour ("validates empty input") is invisible to
an identifier index and reachable through a property index; a query phrased as
a paraphrase is invisible to both and visible only to the dense half.

Dense retrievers are known to degrade out of domain, so replacing the lexical
half with the dense half trades one blind spot for another.  Reciprocal Rank
Fusion is used instead because it combines *ranks*, not scores: BM25 values,
property term counts, and cosine similarities are not on a common scale and
must never be added as though they were.  RRF also degrades gracefully -- a
source that returns nothing simply contributes nothing, which is exactly the
behaviour required when the dense model asset is absent.

What this module must never do
------------------------------
**Retrieval never promotes a candidate to verified.**  Ranking is not
evidence.  Nothing here writes to the graph, mutates a trust tier, creates or
re-tiers an edge, or converts a ``candidate_only`` resolution into a certified
one.  A symbol that ranks first is a symbol that ranked first and nothing
more.  The only outputs are symbol identities, their provenance, and the record
of which source produced each one.

Identity note
-------------
On the graphs this runs against, ``nodes.stable_id`` is NULL for exactly the
source-level nodes -- ``Function``, ``Class``, ``Method`` and ``File`` -- while
the analysis fact nodes carry it.  Rather than return a NULL identity or a
volatile ``rowid``, a missing ``stable_id`` is derived with the engine's own
:func:`gt_engine.resolution_provenance.stable_symbol_id`
(``gt.symbol.identity.v1``).  That derivation is bit-identical to the id the
producer mints in ``resolution_symbols`` (verified 400/400 on the arktype graph),
so ``derived`` here means *computed locally*, not *different*: it is the same
identity the resolution candidates and :mod:`gt_engine.contract` carry.  Every
result still records ``stored`` vs ``derived`` so a reader knows which path ran.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

from gt_engine import contract, contract_embeddings
from gt_engine.graph_context import graph_revision
from gt_engine.resolution_provenance import stable_symbol_id

__all__ = [
    "CONTRACT_EMBEDDING_INDEX_ENV",
    "DEFAULT_LIMIT",
    "DENSE_POOL_LIMIT",
    "MAX_SNIPPET_CHARS",
    "MIN_TERM_LENGTH",
    "RRF_K",
    "SCHEMA",
    "SYMBOL_LABELS",
    "HybridRanking",
    "RankedSymbol",
    "RetrievalSource",
    "SourceRanking",
    "SymbolProvenance",
    "dense_rank",
    "fuse",
    "hybrid_rank",
    "lexical_rank",
    "property_rank",
    "query_terms",
]

SCHEMA = "gt.hybrid_retrieval.v1"

# RRF's smoothing constant.  60 is the value from the original formulation and
# is kept as the default so a fused ranking is comparable across runs; it is
# named rather than inlined so an experiment can vary it explicitly.
RRF_K = 60

DEFAULT_LIMIT = 10

# The source-level labels.  These are the nodes a delivery can actually show a
# reader; the remaining labels (CompletenessFact, Callsite, DerivationFact,
# UnresolvedFact) are analysis bookkeeping and outnumber the symbols ~45:1 on
# the reference graph, so leaving them in would drown every ranking in rows
# nobody can open.
SYMBOL_LABELS: tuple[str, ...] = ("Class", "File", "Function", "Method")

# Property matching is substring-based (there is no FTS index over
# ``properties`` today), so single characters match nearly everything and carry
# no signal.  Two is the floor that still admits real identifiers like id/db.
MIN_TERM_LENGTH = 2

MAX_SNIPPET_CHARS = 240

# Dense embedding is the expensive half: every pooled document is a forward
# pass.  Standalone dense_rank therefore works over a bounded, deterministically
# ordered pool rather than the whole symbol table, and records the bound.
DENSE_POOL_LIMIT = 256

# Where a persisted contract-embedding index is looked for when the caller
# names none.  Unset simply means "no cache": the ranker then embeds its pool
# per call, which is slower and identical in result.
CONTRACT_EMBEDDING_INDEX_ENV = "GT_CONTRACT_EMBEDDING_INDEX"

# Same token shape as gt_engine.hybrid_retrieval, so a query tokenises
# identically wherever it enters the engine.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")

_LIKE_ESCAPE = "\\"


class RetrievalSource(StrEnum):
    """The independently-derived surfaces a ranking can come from."""

    LEXICAL = "lexical"
    PROPERTY = "property"
    DENSE = "dense"
    FUSED = "fused"


class RankedSymbol(NamedTuple):
    """One ranked result: ``(stable_id, score, snippet)``.

    A NamedTuple so it unpacks as the plain triple the contract specifies while
    still being readable by field name.  Higher ``score`` is always better,
    including for BM25, whose native sign convention is inverted on the way in.
    """

    stable_id: str
    score: float
    snippet: str


@dataclass(frozen=True, slots=True)
class SymbolProvenance:
    """Where a ranked ``stable_id`` came from in the graph.

    Carried beside the ranking rather than inside it, so :class:`RankedSymbol`
    stays the specified triple and an attribution record can name a symbol
    without re-querying the database.
    """

    stable_id: str
    node_id: int
    label: str
    name: str
    qualified_name: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    identity_origin: str  # "stored" | "derived:gt.symbol.identity.v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "node_id": self.node_id,
            "label": self.label,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "language": self.language,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "identity_origin": self.identity_origin,
        }


@dataclass(frozen=True, slots=True)
class SourceRanking(Sequence[RankedSymbol]):
    """One source's ranking, plus whether that source could run at all.

    Every ranker returns this same shape.  An empty ``ranking`` with
    ``available=False`` and a ``reason`` is how a source says "I did not run" --
    a different fact from "I ran and found nothing" (``available=True`` with an
    empty ranking), and the two must never be confused in a receipt.

    It behaves as a sequence of :class:`RankedSymbol`, so ``list(rank(...))`` is
    literally ``[(stable_id, score, snippet), ...]``.
    """

    source: RetrievalSource
    ranking: tuple[RankedSymbol, ...] = ()
    available: bool = True
    reason: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[RankedSymbol]:
        return iter(self.ranking)

    def __len__(self) -> int:
        return len(self.ranking)

    def __getitem__(self, index: Any) -> Any:
        return self.ranking[index]

    def as_dict(self) -> dict[str, Any]:
        """A content-safe summary: identities and ranks, never snippet text."""
        return {
            "source": str(self.source),
            "available": self.available,
            "reason": self.reason,
            "result_count": len(self.ranking),
            "ranked_stable_ids": [row.stable_id for row in self.ranking],
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class HybridRanking:
    """The fused ranking together with everything needed to attribute it.

    The attribution requirement is why this is an object and not a list: a
    delivery that shows a symbol must be able to say *which retrieval source
    put it there*, and a fused ranking with its inputs discarded cannot.
    """

    query: str
    fused: tuple[RankedSymbol, ...]
    sources: tuple[SourceRanking, ...]
    provenance: Mapping[str, SymbolProvenance]
    rrf_k: int = RRF_K
    graph_revision: str = ""

    @property
    def available_sources(self) -> tuple[str, ...]:
        return tuple(str(s.source) for s in self.sources if s.available)

    @property
    def degraded_sources(self) -> dict[str, str]:
        return {
            str(s.source): (s.reason or "unspecified")
            for s in self.sources
            if not s.available
        }

    def contributing_sources(self, stable_id: str) -> tuple[str, ...]:
        """Which sources ranked ``stable_id`` -- the attribution answer."""
        return tuple(
            str(s.source)
            for s in self.sources
            if any(row.stable_id == stable_id for row in s.ranking)
        )

    def attribution_record(self) -> dict[str, Any]:
        """A content-safe payload shaped for ``AttributionTrace.record``.

        Deliberately not wired into delivery here -- item 4 builds the record,
        a later item consumes it.  It follows the trace's rule that content is
        never persisted: the query is hashed, snippets are omitted entirely,
        and only identities, ranks and source availability survive.
        """
        return {
            "schema": SCHEMA,
            "query_sha256": hashlib.sha256(
                self.query.encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "query_chars": len(self.query),
            "graph_revision": self.graph_revision,
            "rrf_k": self.rrf_k,
            "available_sources": list(self.available_sources),
            "degraded_sources": self.degraded_sources,
            "sources": [source.as_dict() for source in self.sources],
            "fused": [
                {
                    "rank": rank,
                    "stable_id": row.stable_id,
                    "rrf_score": row.score,
                    "contributing_sources": list(
                        self.contributing_sources(row.stable_id)
                    ),
                    "provenance": (
                        self.provenance[row.stable_id].as_dict()
                        if row.stable_id in self.provenance
                        else None
                    ),
                }
                for rank, row in enumerate(self.fused, start=1)
            ],
            # Retrieval is ranking, not evidence.  Stated in the record so a
            # downstream reader cannot mistake a high rank for a trust tier.
            "promotes_trust": False,
        }


# ---------------------------------------------------------------------------
# query handling
# ---------------------------------------------------------------------------


def query_terms(query: str) -> tuple[str, ...]:
    """Deterministic, de-duplicated query tokens in first-appearance order.

    Raw query text is never interpolated into an FTS5 MATCH or a SQL LIKE:
    FTS5 treats punctuation as operators and would raise on an ordinary
    sentence, and LIKE treats ``%``/``_`` as wildcards.  Tokenising first
    removes both problems at the source instead of escaping downstream.
    """
    seen: dict[str, None] = {}
    for match in _TOKEN_RE.findall(query or ""):
        token = match.lower()
        if len(token) >= MIN_TERM_LENGTH:
            seen.setdefault(token, None)
    return tuple(seen)


def _fts_match_expression(terms: Sequence[str]) -> str:
    # Each token is double-quoted, which makes it an FTS5 string literal and
    # neutralises any residual operator meaning.
    return " OR ".join(f'"{term}"' for term in terms)


def _like_pattern(term: str) -> str:
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def _clean_snippet(value: str | None) -> str:
    return " ".join(str(value or "").split())[:MAX_SNIPPET_CHARS]


# ---------------------------------------------------------------------------
# database handling
# ---------------------------------------------------------------------------


def _open(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return ``(connection, owned)``; a passed-in connection is never closed."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    path = os.fspath(db)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    # Read-only by construction: retrieval has no business writing to a graph.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True), True


def _database_path(db: str | Path | sqlite3.Connection) -> str:
    if isinstance(db, sqlite3.Connection):
        return ""
    return os.fspath(db)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


_SYMBOL_COLUMNS = (
    "n.id, n.stable_id, n.label, n.name, n.qualified_name, n.file_path, "
    "n.language, n.start_line, n.end_line"
)


def _provenance_from_row(row: Sequence[Any]) -> SymbolProvenance:
    node_id = int(row[0])
    stored = row[1]
    label = str(row[2] or "")
    name = str(row[3] or "")
    qualified_name = str(row[4] or name)
    file_path = str(row[5] or "").replace("\\", "/")
    language = str(row[6] or "")
    start_line = int(row[7] or 0)
    end_line = int(row[8] or 0)
    if stored:
        return SymbolProvenance(
            stable_id=str(stored),
            node_id=node_id,
            label=label,
            name=name,
            qualified_name=qualified_name,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            identity_origin="stored",
        )
    # Derived with the engine's own identity function rather than a local
    # scheme, so an id minted here is the id the resolver would mint.
    derived = stable_symbol_id(
        language=language,
        path=file_path,
        qualified_name=qualified_name,
        native_kind=label,
        start_line=start_line,
        end_line=max(start_line, end_line),
    )
    return SymbolProvenance(
        stable_id=derived,
        node_id=node_id,
        label=label,
        name=name,
        qualified_name=qualified_name,
        file_path=file_path,
        language=language,
        start_line=start_line,
        end_line=end_line,
        identity_origin="derived:gt.symbol.identity.v1",
    )


def _collapse(
    scored: Iterable[tuple[SymbolProvenance, float, str]],
    *,
    limit: int,
    provenance: dict[str, SymbolProvenance],
) -> tuple[RankedSymbol, ...]:
    """Best row per identity, then a total order with no ties left over.

    Two distinct nodes can derive the same identity when they share language,
    path, qualified name, kind and line range (two such pairs exist on the
    arktype reference graph).  Collapsing on the best score and then the lowest
    node id keeps the output a function of the input rather than of row arrival
    order.
    """
    best: dict[str, tuple[float, int, str, SymbolProvenance]] = {}
    for prov, score, snippet in scored:
        current = best.get(prov.stable_id)
        candidate = (float(score), prov.node_id, snippet, prov)
        if current is None or (-candidate[0], candidate[1]) < (-current[0], current[1]):
            best[prov.stable_id] = candidate
    for stable_id, entry in best.items():
        provenance.setdefault(stable_id, entry[3])
    ordered = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
    return tuple(
        RankedSymbol(stable_id, entry[0], entry[2])
        for stable_id, entry in ordered[: max(0, int(limit))]
    )


# ---------------------------------------------------------------------------
# 1. lexical
# ---------------------------------------------------------------------------


def lexical_rank(
    db: str | Path | sqlite3.Connection,
    query: str,
    k: int = DEFAULT_LIMIT,
    *,
    labels: Sequence[str] = SYMBOL_LABELS,
    provenance: dict[str, SymbolProvenance] | None = None,
) -> SourceRanking:
    """Rank symbols by FTS5 BM25 over ``nodes_fts``.

    ``nodes_fts`` is an external-content FTS5 table over ``nodes`` keyed by
    rowid, so the join back to ``nodes`` is exact rather than a re-lookup by
    name.  BM25 is returned negated: SQLite's ``bm25()`` is smaller-is-better
    and every score in this module is larger-is-better.
    """
    collected = provenance if provenance is not None else {}
    terms = query_terms(query)
    if not terms:
        return SourceRanking(
            RetrievalSource.LEXICAL,
            (),
            available=True,
            reason="query_has_no_indexable_terms",
        )
    con, owned = _open(db)
    try:
        if "nodes_fts" not in _tables(con):
            return SourceRanking(
                RetrievalSource.LEXICAL,
                (),
                available=False,
                reason="nodes_fts_absent",
            )
        label_slots = ",".join("?" for _ in labels)
        sql = (
            f"SELECT {_SYMBOL_COLUMNS}, bm25(nodes_fts), "
            "snippet(nodes_fts,-1,'','',' ',12) "
            "FROM nodes_fts f JOIN nodes n ON n.id = f.rowid "
            "WHERE nodes_fts MATCH ? "
            + (f"AND n.label IN ({label_slots}) " if labels else "")
            # Over-fetch before collapsing so identity collisions cannot make
            # the result shorter than k when more distinct symbols matched.
            + "ORDER BY bm25(nodes_fts), n.id LIMIT ?"
        )
        params: list[Any] = [
            _fts_match_expression(terms),
            *labels,
            max(1, int(k)) * 4,
        ]
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            return SourceRanking(
                RetrievalSource.LEXICAL,
                (),
                available=False,
                reason=f"fts_query_failed:{type(exc).__name__}",
            )
    finally:
        if owned:
            con.close()

    scored = [
        (
            _provenance_from_row(row),
            -float(row[9]),
            _clean_snippet(row[10]) or f"{row[5]}:{row[3]}",
        )
        for row in rows
    ]
    return SourceRanking(
        RetrievalSource.LEXICAL,
        _collapse(scored, limit=k, provenance=collected),
        available=True,
        detail={"terms": list(terms), "matched_rows": len(rows)},
    )


# ---------------------------------------------------------------------------
# 2. property
# ---------------------------------------------------------------------------


def property_rank(
    db: str | Path | sqlite3.Connection,
    query: str,
    k: int = DEFAULT_LIMIT,
    *,
    kinds: Sequence[str] | None = None,
    provenance: dict[str, SymbolProvenance] | None = None,
) -> SourceRanking:
    """Rank symbols by term matches in ``properties.value``, via the owner node.

    This is the surface that makes a behavioural query answerable: "validates
    empty input" has no reason to appear in any identifier, but it can appear
    in a ``guard_clause`` or ``boundary_condition`` value.  The hit is on the
    property row; the *result* is the symbol that owns it, which is what a
    delivery can show.

    When ``properties_fts`` (an external-content FTS5 table keyed by
    ``properties.id``) is present, the query is a MATCH expression — 2–36×
    faster than the LIKE fallback and strictly more precise. When the table
    is absent (pre-item-4-producer graphs), the function falls back to the
    original substring scan transparently.

    Scoring is *query coverage first*::

        score = distinct_query_terms_matched
              + (1 - 1 / (1 + sum(matched_terms * confidence)))

    A plain sum of matches was tried first and is wrong: it rewards symbols for
    having many facts rather than for matching the query, and on the reference
    graph it put a 150-line class at rank 1 for "parse regex pattern" on the
    strength of ``UnitTypeParser`` appearing in a field initialiser.  Coverage
    is the signal that actually discriminates -- a symbol whose facts mention
    *parse* and *regex* and *pattern* is the thing you asked for.  The second
    term is bounded in ``[0, 1)`` and strictly increasing, so accumulated
    confidence-weighted evidence orders symbols **within** a coverage level and
    can never outvote coverage itself.
    """
    collected = provenance if provenance is not None else {}
    terms = query_terms(query)
    if not terms:
        return SourceRanking(
            RetrievalSource.PROPERTY,
            (),
            available=True,
            reason="query_has_no_indexable_terms",
        )
    con, owned = _open(db)
    try:
        if not {"properties", "nodes"} <= _tables(con):
            return SourceRanking(
                RetrievalSource.PROPERTY,
                (),
                available=False,
                reason="properties_table_absent",
            )
        tables = _tables(con)
        use_fts = "properties_fts" in tables
        if use_fts:
            # FTS5 MATCH: one expression, column-filtered on {value}.
            match_expr = " ".join(f'{{value}} : "{t}"' for t in terms)
            kind_clause = ""
            params: list[Any] = [match_expr]
            if kinds:
                kind_clause = " AND p.kind IN (" + ",".join("?" for _ in kinds) + ")"
                params.extend(kinds)
            sql = (
                f"SELECT {_SYMBOL_COLUMNS}, p.id, p.kind, p.value, p.confidence "
                "FROM properties_fts f "
                "JOIN properties p ON p.id = f.rowid "
                "JOIN nodes n ON n.id = p.node_id "
                f"WHERE properties_fts MATCH ?{kind_clause} ORDER BY p.id"
            )
        else:
            # LIKE fallback for pre-item-4-producer graphs.
            where = " OR ".join(
                f"lower(p.value) LIKE ? ESCAPE '{_LIKE_ESCAPE}'" for _ in terms
            )
            params = [_like_pattern(term) for term in terms]
            kind_clause = ""
            if kinds:
                kind_clause = " AND p.kind IN (" + ",".join("?" for _ in kinds) + ")"
                params.extend(kinds)
            sql = (
                f"SELECT {_SYMBOL_COLUMNS}, p.id, p.kind, p.value, p.confidence "
                "FROM properties p JOIN nodes n ON n.id = p.node_id "
                f"WHERE ({where}){kind_clause} ORDER BY p.id"
            )
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            if use_fts:
                # FTS5 may be corrupt; fall back to LIKE rather than failing.
                use_fts = False
                where = " OR ".join(
                    f"lower(p.value) LIKE ? ESCAPE '{_LIKE_ESCAPE}'" for _ in terms
                )
                params = [_like_pattern(term) for term in terms]
                kind_clause = ""
                if kinds:
                    kind_clause = " AND p.kind IN (" + ",".join("?" for _ in kinds) + ")"
                    params.extend(kinds)
                sql = (
                    f"SELECT {_SYMBOL_COLUMNS}, p.id, p.kind, p.value, p.confidence "
                    "FROM properties p JOIN nodes n ON n.id = p.node_id "
                    f"WHERE ({where}){kind_clause} ORDER BY p.id"
                )
                try:
                    rows = con.execute(sql, params).fetchall()
                except sqlite3.Error as exc2:
                    return SourceRanking(
                        RetrievalSource.PROPERTY,
                        (),
                        available=False,
                        reason=f"property_query_failed:{type(exc2).__name__}",
                    )
            else:
                return SourceRanking(
                    RetrievalSource.PROPERTY,
                    (),
                    available=False,
                    reason=f"property_query_failed:{type(exc).__name__}",
                )
    finally:
        if owned:
            con.close()

    coverage: dict[int, set[str]] = {}
    weights: dict[int, float] = {}
    best_row: dict[int, tuple[float, int, str]] = {}
    owners: dict[int, SymbolProvenance] = {}
    for row in rows:
        value = str(row[11] or "")
        lowered = value.lower()
        matched = [term for term in terms if term in lowered]
        if not matched:
            continue
        prov = _provenance_from_row(row)
        property_id = int(row[9])
        kind = str(row[10] or "")
        confidence = row[12]
        weight = 1.0 if confidence is None else max(0.0, min(1.0, float(confidence)))
        row_score = len(matched) * weight
        coverage.setdefault(prov.node_id, set()).update(matched)
        weights[prov.node_id] = weights.get(prov.node_id, 0.0) + row_score
        owners.setdefault(prov.node_id, prov)
        # The snippet names the matching fact kind, so a reader can see *why*
        # this symbol was retrieved rather than only that it was.
        candidate = (row_score, property_id, _clean_snippet(f"{kind}: {value}"))
        current = best_row.get(prov.node_id)
        if current is None or (-candidate[0], candidate[1]) < (-current[0], current[1]):
            best_row[prov.node_id] = candidate

    scored = [
        (
            owners[node_id],
            len(matched_terms) + (1.0 - 1.0 / (1.0 + weights[node_id])),
            best_row[node_id][2],
        )
        for node_id, matched_terms in coverage.items()
    ]
    return SourceRanking(
        RetrievalSource.PROPERTY,
        _collapse(scored, limit=k, provenance=collected),
        available=True,
        detail={
            "terms": list(terms),
            "matched_property_rows": len(rows),
            "index": "properties_fts" if use_fts else "like_scan",
        },
    )


# ---------------------------------------------------------------------------
# 3. dense
# ---------------------------------------------------------------------------


def _symbol_document_text(
    prov: SymbolProvenance, facts: Sequence[tuple[str, str]]
) -> str:
    """The text a symbol is embedded as.

    Identifiers plus the extracted behavioural facts -- the same material the
    structured contract projects -- rather than raw source, so the embedding
    describes behaviour and survives reformatting.
    """
    lines = [f"{prov.label} {prov.qualified_name}", f"file: {prov.file_path}"]
    lines.extend(f"{kind}: {value}" for kind, value in facts)
    return "\n".join(lines)


def _dense_pool(
    con: sqlite3.Connection,
    *,
    limit: int,
    labels: Sequence[str],
    restrict_to: Sequence[str] | None,
    provenance: dict[str, SymbolProvenance],
) -> dict[str, str]:
    label_slots = ",".join("?" for _ in labels)
    sql = (
        f"SELECT {_SYMBOL_COLUMNS} FROM nodes n "
        + (f"WHERE n.label IN ({label_slots}) " if labels else "")
        + "ORDER BY n.id"
    )
    documents: dict[str, str] = {}
    node_ids: dict[int, SymbolProvenance] = {}
    wanted = set(restrict_to) if restrict_to is not None else None
    for row in con.execute(sql, list(labels)):
        prov = _provenance_from_row(row)
        if wanted is not None and prov.stable_id not in wanted:
            continue
        if prov.stable_id in documents:
            continue
        node_ids[prov.node_id] = prov
        documents[prov.stable_id] = ""
        provenance.setdefault(prov.stable_id, prov)
        if wanted is None and len(documents) >= limit:
            break
    if not node_ids:
        return {}
    facts: dict[int, list[tuple[str, str]]] = {}
    if "properties" in _tables(con):
        slots = ",".join("?" for _ in node_ids)
        for node_id, kind, value in con.execute(
            f"SELECT node_id, kind, value FROM properties WHERE node_id IN ({slots}) "
            "ORDER BY id",
            list(node_ids),
        ):
            facts.setdefault(int(node_id), []).append((str(kind), str(value)))
    for node_id, prov in node_ids.items():
        documents[prov.stable_id] = _symbol_document_text(prov, facts.get(node_id, []))
    return documents


def _rank_from_store(
    *,
    query: str,
    model_root: Path,
    lookup: contract_embeddings.StoreLookup,
    documents: Mapping[str, str],
    node_stable_ids: Mapping[int, str],
    k: int,
    store_path: Path,
) -> SourceRanking:
    """Rank a pool against vectors the contract-embedding store already holds.

    Only the query is embedded here.  The symbol side was embedded once, when
    its contract last changed, which is the entire economic argument for the
    store: a query costs one forward pass instead of one per candidate.

    The query encoder is still required.  A populated store is not a licence to
    answer without it, so a missing or broken model asset degrades by the same
    named reasons as the uncached path rather than returning a cached order.
    """
    try:
        from gt_engine.dense_runtime import embed_texts

        query_vector = embed_texts(model_root, [query])[0]
    except Exception as exc:  # noqa: BLE001 - dense fails closed, never loudly
        return SourceRanking(
            RetrievalSource.DENSE,
            (),
            available=False,
            reason=f"dense_runtime_failed:{type(exc).__name__}:{str(exc)[:120]}",
        )
    if len(query_vector) != lookup.dimension:
        return SourceRanking(
            RetrievalSource.DENSE,
            (),
            available=False,
            reason="dense_store_dimension_mismatch",
            detail={
                "query_dimension": len(query_vector),
                "store_dimension": lookup.dimension,
            },
        )
    scored = contract_embeddings.score_pool(query_vector, lookup.vectors)
    ranking = tuple(
        RankedSymbol(
            node_stable_ids[node_id],
            float(score),
            _clean_snippet(documents.get(node_stable_ids[node_id], "")),
        )
        for node_id, score in scored
        if node_id in node_stable_ids
    )[: max(0, int(k))]
    return SourceRanking(
        RetrievalSource.DENSE,
        ranking,
        available=True,
        detail={
            "vector_source": "contract_embedding_store",
            "store_path": str(store_path),
            "pool_size": len(documents),
            "store_hits": lookup.hits,
            "store_misses": lookup.misses,
            "missing_stable_ids": list(lookup.missing_stable_ids),
            "dimension": lookup.dimension,
        },
    )


def _resolved_store_path(store_path: str | Path | None) -> Path | None:
    if store_path is not None:
        return Path(store_path)
    configured = os.environ.get(CONTRACT_EMBEDDING_INDEX_ENV, "").strip()
    return Path(configured) if configured else None


def dense_rank(
    db: str | Path | sqlite3.Connection,
    query: str,
    k: int = DEFAULT_LIMIT,
    *,
    model_dir: str | Path | None = None,
    index_path: str | Path | None = None,
    store_path: str | Path | None = None,
    labels: Sequence[str] = SYMBOL_LABELS,
    restrict_to: Sequence[str] | None = None,
    pool_limit: int = DENSE_POOL_LIMIT,
    provenance: dict[str, SymbolProvenance] | None = None,
) -> SourceRanking:
    """Rank symbols by ONNX embedding similarity via :mod:`gt_engine.dense_runtime`.

    Degrades explicitly and never crashes: a missing ``GT_DENSE_MODEL_DIR``, an
    absent or digest-mismatched model asset, or a runtime import failure all
    return ``available=False`` with a named reason and an empty ranking.  The
    alternative -- silently substituting the lexical order -- would make a
    degraded run indistinguishable from a healthy one, which is precisely the
    failure this project exists to avoid.

    ``restrict_to`` narrows the pool to an existing candidate set (what
    :func:`hybrid_rank` passes) so the common path costs one forward pass per
    candidate instead of per symbol; with it unset the ranker is standalone and
    pools up to ``pool_limit`` symbols in ``nodes.id`` order.

    ``dense_runtime.rank_documents`` returns an order and not scores, so the
    reported score is ``1/rank`` -- a monotone stand-in, used only for display.
    Fusion consumes ranks, so no information is lost by this.
    """
    collected = provenance if provenance is not None else {}
    if not (query or "").strip():
        return SourceRanking(
            RetrievalSource.DENSE, (), available=False, reason="dense_query_empty"
        )

    resolved_model = (
        model_dir if model_dir is not None else os.environ.get("GT_DENSE_MODEL_DIR")
    )
    if not resolved_model:
        return SourceRanking(
            RetrievalSource.DENSE, (), available=False, reason="dense_model_dir_unset"
        )
    model_root = Path(resolved_model)
    missing = [
        name
        for name in ("model.onnx", "tokenizer.json", "manifest.json")
        if not (model_root / name).is_file()
    ]
    if missing:
        return SourceRanking(
            RetrievalSource.DENSE,
            (),
            available=False,
            reason="dense_model_assets_absent",
            detail={"model_dir": str(model_root), "missing": missing},
        )

    resolved_store = _resolved_store_path(store_path)
    con, owned = _open(db)
    try:
        documents = _dense_pool(
            con,
            limit=max(1, int(pool_limit)),
            labels=labels,
            restrict_to=restrict_to,
            provenance=collected,
        )
        # node id -> the id `gt_engine.contract` keys that symbol by, which is
        # the id the store holds.  Retrieval's own stable id is line-bearing and
        # therefore not durable across a reformat; the two are joined on the
        # node id of the graph in hand and never conflated.
        contract_ids: dict[int, str] = (
            contract.symbol_node_ids(con) if resolved_store is not None else {}
        )
        source_revision = "unknown-source-revision"
        row = con.execute(
            "SELECT source_revision FROM nodes WHERE source_revision IS NOT NULL "
            "AND source_revision <> '' ORDER BY source_revision LIMIT 1"
        ).fetchone()
        if row:
            source_revision = str(row[0])
    except sqlite3.Error as exc:
        return SourceRanking(
            RetrievalSource.DENSE,
            (),
            available=False,
            reason=f"dense_pool_query_failed:{type(exc).__name__}",
        )
    finally:
        if owned:
            con.close()

    if not documents:
        return SourceRanking(
            RetrievalSource.DENSE, (), available=False, reason="dense_pool_empty"
        )

    node_stable_ids = {
        collected[stable_id].node_id: stable_id
        for stable_id in documents
        if stable_id in collected
    }
    store_detail: dict[str, Any] = {"vector_source": "dense_runtime_pool"}
    if resolved_store is not None:
        lookup = contract_embeddings.lookup_vectors(
            resolved_store, contract_ids, node_stable_ids
        )
        if lookup.reason is None:
            return _rank_from_store(
                query=query,
                model_root=model_root,
                lookup=lookup,
                documents=documents,
                node_stable_ids=node_stable_ids,
                k=k,
                store_path=resolved_store,
            )
        # Named, never silent: the caller asked for a cache and did not get one.
        store_detail["store_reason"] = lookup.reason

    path = _database_path(db)
    revision = graph_revision(path) if path else ""

    with tempfile.TemporaryDirectory(prefix="gt-dense-") as scratch:
        target = (
            Path(index_path) if index_path else Path(scratch) / "dense-index.sqlite"
        )
        try:
            from gt_engine.dense_runtime import rank_documents

            ordered, receipt = rank_documents(
                query_text=query,
                documents=documents,
                # Empty: this is the *dense-only* ranking.  Letting the vector
                # index blend a lexical channel here would double-count the
                # lexical source once RRF runs.
                lexical_scores={},
                model_dir=model_root,
                index_path=target,
                source_revision=source_revision,
                graph_revision=revision or "unknown-graph-revision",
                limit=min(max(1, int(k)), len(documents)),
            )
        except Exception as exc:  # noqa: BLE001 - dense fails closed, never loudly
            return SourceRanking(
                RetrievalSource.DENSE,
                (),
                available=False,
                reason=f"dense_runtime_failed:{type(exc).__name__}:{str(exc)[:120]}",
            )

    ranking = tuple(
        RankedSymbol(
            stable_id, 1.0 / float(rank), _clean_snippet(documents.get(stable_id, ""))
        )
        for rank, stable_id in enumerate(ordered, start=1)
        if stable_id in documents
    )[: max(0, int(k))]
    query_ready = bool(receipt.get("query_ready", False))
    return SourceRanking(
        RetrievalSource.DENSE,
        ranking,
        available=query_ready,
        reason=None if query_ready else str(receipt.get("reason") or "dense_not_ready"),
        detail={
            **store_detail,
            "pool_size": len(documents),
            "pool_bounded": restrict_to is None and len(documents) >= pool_limit,
            "model_sha256": receipt.get("model_sha256"),
            "index_sha256": receipt.get("index_sha256"),
        },
    )


# ---------------------------------------------------------------------------
# 4. fusion
# ---------------------------------------------------------------------------


def fuse(
    rankings: Iterable[SourceRanking | Sequence[RankedSymbol]],
    k: int = RRF_K,
    *,
    limit: int | None = None,
) -> list[RankedSymbol]:
    """Reciprocal Rank Fusion: ``score(d) = sum_i 1 / (k + rank_i(d))``.

    ``k`` here is RRF's smoothing constant, *not* a result count -- it damps the
    influence of any single source's top position so one confident-but-wrong
    ranker cannot dominate.  Ranks are 1-based.

    Ranks rather than scores are fused deliberately: BM25 (unbounded, negative
    in its native sign), summed property term weights, and cosine similarity
    share no scale, and normalising them against each other would invent a
    calibration nobody measured.

    Ties are broken by ``stable_id`` ascending, so the output is a total order
    and two runs over the same inputs are identical.  The snippet shown is the
    one from the first ranking (in argument order) that produced the symbol,
    which keeps the fused row traceable to a real source row.
    """
    constant = int(k)
    if constant < 0:
        raise ValueError("rrf_k_must_be_non_negative")
    totals: dict[str, float] = {}
    snippets: dict[str, str] = {}
    for ranking in rankings:
        rows = ranking.ranking if isinstance(ranking, SourceRanking) else tuple(ranking)
        for rank, row in enumerate(rows, start=1):
            totals[row.stable_id] = totals.get(row.stable_id, 0.0) + 1.0 / (
                constant + rank
            )
            snippets.setdefault(row.stable_id, row.snippet)
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        ordered = ordered[: max(0, int(limit))]
    return [
        RankedSymbol(stable_id, score, snippets.get(stable_id, ""))
        for stable_id, score in ordered
    ]


# ---------------------------------------------------------------------------
# 5. hybrid
# ---------------------------------------------------------------------------


def hybrid_rank(
    db: str | Path | sqlite3.Connection,
    query: str,
    k: int = DEFAULT_LIMIT,
    *,
    rrf_k: int = RRF_K,
    labels: Sequence[str] = SYMBOL_LABELS,
    use_dense: bool = True,
    model_dir: str | Path | None = None,
    index_path: str | Path | None = None,
    store_path: str | Path | None = None,
) -> HybridRanking:
    """Run the lexical, property and dense rankers and fuse them.

    Source order is fixed -- lexical, property, dense -- because :func:`fuse`
    takes its snippet from the first source that produced a symbol, so a stable
    order is part of determinism, not a stylistic choice.

    The dense pool is restricted to the union of the lexical and property
    candidates.  That is a real trade and it is recorded: dense reorders the
    lexical/property candidate set rather than contributing candidates of its
    own, in exchange for a cost proportional to the candidates instead of to
    the symbol table.  Call :func:`dense_rank` directly with ``restrict_to=None``
    for an independent dense pool.

    Returns a :class:`HybridRanking`.  It never writes to ``db``, never touches
    a trust tier, and never creates an edge.
    """
    provenance: dict[str, SymbolProvenance] = {}
    con, owned = _open(db)
    try:
        # A wider per-source cut than k: fusion needs candidates below each
        # source's top-k to have anything to disagree about.
        source_k = max(1, int(k)) * 3
        lexical = lexical_rank(
            con, query, source_k, labels=labels, provenance=provenance
        )
        properties = property_rank(con, query, source_k, provenance=provenance)
        candidates = [row.stable_id for row in lexical] + [
            row.stable_id for row in properties
        ]
        if not use_dense:
            dense = SourceRanking(
                RetrievalSource.DENSE,
                (),
                available=False,
                reason="dense_disabled_by_caller",
            )
        elif not candidates:
            dense = SourceRanking(
                RetrievalSource.DENSE, (), available=False, reason="dense_pool_empty"
            )
        else:
            dense = dense_rank(
                con,
                query,
                source_k,
                model_dir=model_dir,
                index_path=index_path,
                store_path=store_path,
                labels=labels,
                restrict_to=candidates,
                provenance=provenance,
            )
        path = _database_path(db)
    finally:
        if owned:
            con.close()

    sources = (lexical, properties, dense)
    return HybridRanking(
        query=query,
        fused=tuple(fuse(sources, rrf_k, limit=k)),
        sources=sources,
        provenance=provenance,
        rrf_k=rrf_k,
        graph_revision=graph_revision(path) if path else "",
    )
