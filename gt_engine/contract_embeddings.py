"""Embed the behavioural contract of a symbol, and re-embed only when it moves.

Why the contract and not the source
-----------------------------------
Embedding raw source makes a vector a function of the bytes, so a formatter run
changes every vector in the repository and a rename changes the one vector that
should have been most stable.  :mod:`gt_engine.contract` already projects the
stored ``properties`` rows into a deterministic behavioural record -- params,
return shapes, guards, boundaries, side effects, data flow, visibility.  This
module renders that record as text and embeds *that*.  The consequence is the
whole point: a query phrased as behaviour ("rejects a null input") matches the
symbol that behaves that way, not the one that happens to share vocabulary with
it, and reformatting the file changes nothing.

Why the fingerprint is the invalidation key
-------------------------------------------
The producer already computes a semantic ``fingerprint`` property --
``complexity:N|calls:...``, 1,407 rows on the arktype reference graph.  It is a
function of the symbol's branch count and call set, not of its bytes, which is
exactly the property a cache key needs: reformatting cannot move it.

It is necessary but not sufficient.  A changed *return shape* moves no branch
and no call, so the producer's fingerprint does not see it, and a store keyed on
the fingerprint alone would keep serving a vector describing behaviour the
symbol no longer has.  The key is therefore two-part -- the fingerprint value
and a digest of the rendered contract text -- and the receipt reports which half
fired for every re-embed, so "the fingerprint changed" and "the contract text
changed under an unchanged fingerprint" never collapse into one number.  A
symbol with no fingerprint row (2,104 of 3,511 on arktype) keeps an empty
fingerprint and is invalidated by the text half alone; absence is recorded, not
papered over.

The line range is bound alongside the vector but is deliberately *not* in the
key.  A hit has to be openable -- a receipt naming a stable id with no line
range is not replayable -- but a symbol that only moved down the file has not
changed, so the lines are recorded and then ignored when deciding what to embed.

Storage
-------
The vectors go in :class:`gt_engine.hybrid_retrieval.SQLiteVectorIndex`, the
store the engine already uses, in its own ``gt_vector_documents`` table.  One
additive table, :data:`BINDING_TABLE`, sits beside it in the same file and holds
what the vector table has no column for: the node id of the build that produced
the row, the line range, the fingerprint, the two key halves, and the source
revision the embedding was taken at.

That index is normally bound to one ``graph_revision`` and refuses to serve a
different one.  This one is not, and must not be: a cache whose entire purpose
is to survive a rebuild cannot be invalidated by the rebuild.  Both revision
fields are therefore pinned to constants -- :data:`CONTRACT_SOURCE_REVISION`,
which is the contract projection's schema and so *does* invalidate everything if
the projection changes, and :data:`UNBOUND_GRAPH_REVISION`, which says in as
many words that no graph revision binds these rows.

Nothing here promotes evidence.  A vector is a ranking input; tiers and edges
are untouched, and :func:`ContractEmbeddingStore.refresh` opens the graph
read-only so it cannot become otherwise by accident.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gt_engine import contract, dense_runtime
from gt_engine.contract_text import (
    FINGERPRINT_JOINER,
    KEY_SCHEMA,
    contract_text,
    invalidation_key,
    text_digest,
)
from gt_engine.hybrid_retrieval import (
    EmbeddingRecord,
    SQLiteVectorIndex,
    _cosine,  # the engine's own similarity; re-deriving it here would let two
)  # definitions of "nearest" drift apart in the same process.

__all__ = [
    "BINDING_TABLE",
    "CONTRACT_SOURCE_REVISION",
    "DEFAULT_BATCH_SIZE",
    "KEY_SCHEMA",
    "RECEIPT_SCHEMA",
    "UNBOUND_GRAPH_REVISION",
    "Binding",
    "ContractEmbeddingStore",
    "EmbeddingPlan",
    "StoreLookup",
    "SymbolEmbeddingInput",
    "contract_text",
    "embedding_inputs",
    "fingerprints",
    "invalidation_key",
    "lookup_vectors",
    "onnx_embedder",
    "plan_embeddings",
]

RECEIPT_SCHEMA = "gt.contract_embedding_receipt.v1"

BINDING_TABLE = "gt_contract_embedding_bindings"

# The contract projection's schema stands in for "the revision of the source
# text these vectors were made from" -- change the projection and every vector
# is stale, which is exactly what the vector index does with a changed
# source_revision.
CONTRACT_SOURCE_REVISION = contract.CONTRACT_SCHEMA

# Said out loud rather than left as an empty string: these rows are on purpose
# not bound to a graph revision.  See the module docstring.
UNBOUND_GRAPH_REVISION = "gt.contract_embedding.unbound.v1"

DEFAULT_BATCH_SIZE = 32

_FINGERPRINT_KIND = "fingerprint"

_SELECT_FINGERPRINTS = (
    f"SELECT node_id, value FROM properties WHERE kind = '{_FINGERPRINT_KIND}' ORDER BY id"
)

_BINDING_COLUMNS = (
    "stable_id, node_id, file_path, qualified_name, kind, start_line, end_line, "
    "fingerprint, contract_text_sha256, invalidation_key, contract_digest, "
    "contract_schema, model_id, dimension, embedded_at_revision"
)

_CREATE_BINDING_TABLE = f"""
CREATE TABLE IF NOT EXISTS {BINDING_TABLE} (
    stable_id TEXT PRIMARY KEY,
    node_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    fingerprint TEXT NOT NULL,
    contract_text_sha256 TEXT NOT NULL,
    invalidation_key TEXT NOT NULL,
    contract_digest TEXT NOT NULL,
    contract_schema TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    embedded_at_revision TEXT NOT NULL
)
"""


# ---------------------------------------------------------------------------
# 1. reading a graph
# ---------------------------------------------------------------------------


def fingerprints(db_path: str | Path) -> dict[int, str]:
    """Return ``node id -> fingerprint value`` for every node that has one.

    A node with no fingerprint row is absent from the mapping rather than
    present with an empty value, so "the producer said nothing" stays
    distinguishable from "the producer said nothing changed".
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"graph not found: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        grouped: dict[int, list[str]] = {}
        for node_id, value in connection.execute(_SELECT_FINGERPRINTS):
            grouped.setdefault(int(node_id), []).append(str(value))
    finally:
        connection.close()
    return {
        node_id: FINGERPRINT_JOINER.join(values) for node_id, values in grouped.items()
    }


def _source_revision(db_path: str | Path) -> str:
    path = Path(db_path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT source_revision FROM nodes WHERE source_revision IS NOT NULL "
            "AND source_revision <> '' ORDER BY source_revision LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        connection.close()
    return str(row[0]) if row else ""


@dataclass(frozen=True, slots=True)
class SymbolEmbeddingInput:
    """One symbol's embeddable text and everything the binding row records."""

    stable_id: str
    node_id: int
    kind: str
    file_path: str
    qualified_name: str
    start_line: int | None
    end_line: int | None
    fingerprint: str
    text: str
    text_sha256: str
    contract_digest: str
    invalidation_key: str


def embedding_inputs(db_path: str | Path) -> tuple[SymbolEmbeddingInput, ...]:
    """Project a whole graph into the inputs an embedding pass consumes.

    In :func:`gt_engine.contract.contracts` order -- file, position, node id --
    so two runs over one graph produce the same sequence and a diff of a whole
    repository's inputs is readable.

    Two nodes can derive the same stable id when the producer minted none for
    either (two such pairs exist on the arktype reference graph).  The first in
    that order wins and the rest are dropped, so the result is a function of the
    graph rather than of row arrival; :meth:`ContractEmbeddingStore.refresh`
    counts the drops.
    """
    by_node = fingerprints(db_path)
    seen: set[str] = set()
    inputs: list[SymbolEmbeddingInput] = []
    for node_id, symbol_contract in contract.contracts_with_node_ids(db_path):
        symbol = symbol_contract["symbol"]
        stable_id = str(symbol["stable_id"])
        if stable_id in seen:
            continue
        seen.add(stable_id)
        text = contract_text(symbol_contract)
        fingerprint = by_node.get(node_id, "")
        inputs.append(
            SymbolEmbeddingInput(
                stable_id=stable_id,
                node_id=node_id,
                kind=str(symbol["kind"]),
                file_path=str(symbol["file_path"]),
                qualified_name=str(symbol["qualified_name"]),
                start_line=symbol["start_line"],
                end_line=symbol["end_line"],
                fingerprint=fingerprint,
                text=text,
                text_sha256=text_digest(text),
                contract_digest=contract.contract_digest(symbol_contract),
                invalidation_key=invalidation_key(fingerprint, text),
            )
        )
    return tuple(inputs)


# ---------------------------------------------------------------------------
# 2. the invalidation plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Binding:
    """What the store remembers about a vector it already holds."""

    stable_id: str
    node_id: int
    file_path: str
    qualified_name: str
    kind: str
    start_line: int | None
    end_line: int | None
    fingerprint: str
    contract_text_sha256: str
    invalidation_key: str
    contract_digest: str
    contract_schema: str
    model_id: str
    dimension: int
    embedded_at_revision: str


def _binding_from_row(row: Sequence[Any]) -> Binding:
    """Read one binding row in ``_BINDING_COLUMNS`` order."""
    return Binding(
        stable_id=str(row[0]),
        node_id=int(row[1]),
        file_path=str(row[2]),
        qualified_name=str(row[3]),
        kind=str(row[4]),
        start_line=None if row[5] is None else int(row[5]),
        end_line=None if row[6] is None else int(row[6]),
        fingerprint=str(row[7]),
        contract_text_sha256=str(row[8]),
        invalidation_key=str(row[9]),
        contract_digest=str(row[10]),
        contract_schema=str(row[11]),
        model_id=str(row[12]),
        dimension=int(row[13]),
        embedded_at_revision=str(row[14]),
    )


@dataclass(frozen=True, slots=True)
class EmbeddingPlan:
    """What a refresh will do, decided before a single forward pass is run."""

    to_embed: tuple[SymbolEmbeddingInput, ...]
    unchanged: tuple[str, ...]
    to_delete: tuple[str, ...]
    new_symbols: tuple[str, ...]
    fingerprint_changed: tuple[str, ...]
    contract_changed: tuple[str, ...]
    model_changed: tuple[str, ...]


def plan_embeddings(
    inputs: Sequence[SymbolEmbeddingInput],
    existing: Mapping[str, Binding],
    *,
    model_id: str,
    dimension: int,
) -> EmbeddingPlan:
    """Decide what to embed, keep and drop, and say why for each re-embed.

    A stored vector is reused only when the invalidation key, the model and the
    dimension all agree.  Attribution is reported at the finest grain the key
    supports: a re-embed is charged to the fingerprint when that moved, and to
    the contract text only when the fingerprint held still -- so "the producer
    saw the change" and "only the projection saw it" stay separate numbers.
    """
    to_embed: list[SymbolEmbeddingInput] = []
    unchanged: list[str] = []
    new_symbols: list[str] = []
    fingerprint_changed: list[str] = []
    contract_changed: list[str] = []
    model_changed: list[str] = []

    for item in inputs:
        binding = existing.get(item.stable_id)
        if binding is None:
            new_symbols.append(item.stable_id)
            to_embed.append(item)
            continue
        if binding.model_id != model_id or binding.dimension != dimension:
            model_changed.append(item.stable_id)
            to_embed.append(item)
            continue
        if binding.invalidation_key == item.invalidation_key:
            unchanged.append(item.stable_id)
            continue
        if binding.fingerprint != item.fingerprint:
            fingerprint_changed.append(item.stable_id)
        else:
            contract_changed.append(item.stable_id)
        to_embed.append(item)

    present = {item.stable_id for item in inputs}
    to_delete = tuple(sorted(set(existing) - present))
    return EmbeddingPlan(
        to_embed=tuple(to_embed),
        unchanged=tuple(unchanged),
        to_delete=to_delete,
        new_symbols=tuple(new_symbols),
        fingerprint_changed=tuple(fingerprint_changed),
        contract_changed=tuple(contract_changed),
        model_changed=tuple(model_changed),
    )


# ---------------------------------------------------------------------------
# 3. the store
# ---------------------------------------------------------------------------

Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


def onnx_embedder(model_dir: str | Path) -> Embedder:
    """The production embedder: the verified local ONNX arctic-embed-m runtime.

    Injected rather than reached for, so a test can count forward passes and a
    caller can substitute nothing else by accident.
    """
    root = Path(model_dir)

    def _embed(texts: Sequence[str]) -> list[tuple[float, ...]]:
        return dense_runtime.embed_texts(root, texts)

    return _embed


def _batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield items[start : start + step]


class ContractEmbeddingStore:
    """A restartable contract-vector cache keyed by minted stable id.

    Opens the graph read-only and writes only to its own sidecar file, so a
    refresh can never mutate the store it is reading from.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        model_id: str | None = None,
        tokenizer_id: str | None = None,
        dimension: int | None = None,
    ) -> None:
        identity = dense_runtime.model_identity()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id or str(identity["model_id"])
        self.tokenizer_id = tokenizer_id or str(identity["tokenizer_sha256"])
        self.dimension = int(dimension or identity["dimension"])
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(_CREATE_BINDING_TABLE)
        self._connection.commit()

    # -- reads ------------------------------------------------------------

    def bindings(self) -> dict[str, Binding]:
        rows = self._connection.execute(
            f"SELECT {_BINDING_COLUMNS} FROM {BINDING_TABLE} ORDER BY stable_id"
        ).fetchall()
        return {str(row[0]): _binding_from_row(row) for row in rows}

    def vectors(
        self, stable_ids: Sequence[str] | None = None
    ) -> dict[str, tuple[float, ...]]:
        """Return ``stable id -> vector`` for the requested ids, or for all."""
        if not self._has_vector_table():
            return {}
        if stable_ids is None:
            rows = self._connection.execute(
                "SELECT document_id, embedding_json FROM gt_vector_documents "
                "ORDER BY document_id"
            ).fetchall()
        else:
            wanted = list(dict.fromkeys(stable_ids))
            if not wanted:
                return {}
            slots = ",".join("?" for _ in wanted)
            rows = self._connection.execute(
                "SELECT document_id, embedding_json FROM gt_vector_documents "
                f"WHERE document_id IN ({slots}) ORDER BY document_id",
                wanted,
            ).fetchall()
        return {
            str(row[0]): tuple(float(value) for value in json.loads(row[1]))
            for row in rows
        }

    def document_count(self) -> int:
        """How many vectors the store holds; zero before the first publish.

        The vector table is created by the index on first write, so a store that
        has been opened but never refreshed has none.  That is "empty", not
        "unreadable", and the two must not be reported as the same fault.
        """
        if not self._has_vector_table():
            return 0
        row = self._connection.execute(
            "SELECT COUNT(*) FROM gt_vector_documents"
        ).fetchone()
        return int(row[0]) if row else 0

    def _has_vector_table(self) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'gt_vector_documents'"
        ).fetchone() is not None

    # -- the refresh ------------------------------------------------------

    def refresh(
        self,
        db_path: str | Path,
        *,
        embed_fn: Embedder,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> dict[str, Any]:
        """Bring the store level with ``db_path`` and report exactly what moved."""
        inputs = embedding_inputs(db_path)
        revision = _source_revision(db_path)
        plan = plan_embeddings(
            inputs,
            self.bindings(),
            model_id=self.model_id,
            dimension=self.dimension,
        )
        vectors = self._embed_plan(plan, embed_fn, batch_size)
        index_digest = self._publish(plan, vectors)
        self._write_bindings(plan, revision)
        return self._receipt(db_path, revision, inputs, plan, index_digest)

    def _embed_plan(
        self, plan: EmbeddingPlan, embed_fn: Embedder, batch_size: int
    ) -> dict[str, tuple[float, ...]]:
        vectors: dict[str, tuple[float, ...]] = {}
        for batch in _batched(plan.to_embed, batch_size):
            produced = embed_fn([item.text for item in batch])
            if len(produced) != len(batch):
                raise ValueError("contract_embedding_batch_size_mismatch")
            for item, vector in zip(batch, produced, strict=True):
                vectors[item.stable_id] = tuple(float(value) for value in vector)
        return vectors

    def _publish(
        self, plan: EmbeddingPlan, vectors: Mapping[str, tuple[float, ...]]
    ) -> str:
        if not plan.to_embed and not plan.to_delete:
            return _file_digest(self.path)
        # The vector index owns the write; this connection stays out of its way
        # so the two never contend for the same file lock.
        self._connection.commit()
        index = SQLiteVectorIndex(
            self.path,
            model_id=self.model_id,
            tokenizer_id=self.tokenizer_id,
            dimension=self.dimension,
            source_revision=CONTRACT_SOURCE_REVISION,
            graph_revision=UNBOUND_GRAPH_REVISION,
        )
        try:
            index.upsert(
                [
                    EmbeddingRecord(
                        document_id=item.stable_id,
                        text=item.text,
                        embedding=vectors[item.stable_id],
                        content_hash=item.invalidation_key,
                        model_id=self.model_id,
                        tokenizer_id=self.tokenizer_id,
                        source_revision=CONTRACT_SOURCE_REVISION,
                        graph_revision=UNBOUND_GRAPH_REVISION,
                    )
                    for item in plan.to_embed
                ],
                delete_ids=plan.to_delete,
            )
        finally:
            index.close()
        return _file_digest(self.path)

    def _write_bindings(self, plan: EmbeddingPlan, revision: str) -> None:
        if not plan.to_embed and not plan.to_delete:
            return
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            for stable_id in plan.to_delete:
                connection.execute(
                    f"DELETE FROM {BINDING_TABLE} WHERE stable_id = ?", (stable_id,)
                )
            connection.executemany(
                f"INSERT OR REPLACE INTO {BINDING_TABLE} ({_BINDING_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        item.stable_id,
                        item.node_id,
                        item.file_path,
                        item.qualified_name,
                        item.kind,
                        item.start_line,
                        item.end_line,
                        item.fingerprint,
                        item.text_sha256,
                        item.invalidation_key,
                        item.contract_digest,
                        contract.CONTRACT_SCHEMA,
                        self.model_id,
                        self.dimension,
                        revision,
                    )
                    for item in plan.to_embed
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _receipt(
        self,
        db_path: str | Path,
        revision: str,
        inputs: Sequence[SymbolEmbeddingInput],
        plan: EmbeddingPlan,
        index_digest: str,
    ) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "graph": str(Path(db_path)),
            "store": str(self.path),
            "source_revision": revision,
            "contract_schema": contract.CONTRACT_SCHEMA,
            "key_schema": KEY_SCHEMA,
            "model_id": self.model_id,
            "dimension": self.dimension,
            "symbols_total": len(inputs),
            "symbols_with_fingerprint": sum(1 for item in inputs if item.fingerprint),
            "embedded": len(plan.to_embed),
            "embedded_new": len(plan.new_symbols),
            "embedded_fingerprint_changed": len(plan.fingerprint_changed),
            "embedded_contract_changed": len(plan.contract_changed),
            "embedded_model_changed": len(plan.model_changed),
            "unchanged": len(plan.unchanged),
            "deleted": len(plan.to_delete),
            "re_embedded_stable_ids": sorted(item.stable_id for item in plan.to_embed),
            "deleted_stable_ids": list(plan.to_delete),
            "documents_after": self.document_count(),
            "index_sha256": index_digest,
            # Retrieval ranks; it never promotes.  Stated in the receipt so a
            # reader cannot mistake a stored vector for stored evidence.
            "promotes_trust": False,
        }

    def close(self) -> None:
        self._connection.close()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


# ---------------------------------------------------------------------------
# 4. the retrieval-side lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoreLookup:
    """What a store could and could not answer for one retrieval pool."""

    vectors: Mapping[int, tuple[float, ...]]
    hits: int
    misses: int
    missing_stable_ids: tuple[str, ...]
    dimension: int
    reason: str | None


def _empty_lookup(reason: str) -> StoreLookup:
    return StoreLookup({}, 0, 0, (), 0, reason)


def lookup_vectors(
    store_path: str | Path,
    node_to_stable: Mapping[int, str],
    node_ids: Iterable[int],
) -> StoreLookup:
    """Fetch stored vectors for a pool of graph nodes, keyed back by node id.

    ``node_to_stable`` comes from :func:`gt_engine.contract.symbol_node_ids` for
    the graph being ranked: node ids are per-build and never persisted, minted
    stable ids are what the store holds, and this is the only place the two
    meet.

    Never raises.  Every way this can fail to be useful -- no file, no rows, an
    unreadable file, a pool the store does not cover -- comes back as a named
    ``reason``, because a dense source that returns silently nothing is the one
    outcome this project cannot tolerate.
    """
    path = Path(store_path)
    if not path.is_file():
        return _empty_lookup("contract_embedding_store_absent")
    wanted = list(dict.fromkeys(int(node_id) for node_id in node_ids))
    if not wanted:
        return _empty_lookup("contract_embedding_store_pool_empty")
    try:
        store = ContractEmbeddingStore(path)
        try:
            if store.document_count() == 0:
                return _empty_lookup("contract_embedding_store_empty")
            stable_ids = [
                node_to_stable[node_id] for node_id in wanted if node_id in node_to_stable
            ]
            found = store.vectors(stable_ids)
        finally:
            store.close()
    except sqlite3.Error as exc:
        return _empty_lookup(f"contract_embedding_store_unreadable:{type(exc).__name__}")

    vectors: dict[int, tuple[float, ...]] = {}
    missing: list[str] = []
    for node_id in wanted:
        stable_id = node_to_stable.get(node_id)
        vector = found.get(stable_id) if stable_id else None
        if vector is None:
            missing.append(stable_id or f"node:{node_id}")
        else:
            vectors[node_id] = vector
    if not vectors:
        return _empty_lookup("contract_embedding_store_misses_pool")
    dimension = len(next(iter(vectors.values())))
    return StoreLookup(
        vectors=vectors,
        hits=len(vectors),
        misses=len(missing),
        missing_stable_ids=tuple(sorted(missing)),
        dimension=dimension,
        reason=None,
    )


def score_pool(
    query_vector: Sequence[float], vectors: Mapping[int, tuple[float, ...]]
) -> list[tuple[int, float]]:
    """Cosine-score a pool and return ``(node id, score)`` best first.

    Ties break on node id ascending, so the order is total and two runs over one
    store are identical.
    """
    scored = [
        (node_id, _cosine(query_vector, vector)) for node_id, vector in vectors.items()
    ]
    return sorted(scored, key=lambda item: (-item[1], item[0]))
