"""Verified local ONNX embeddings and an exact-rescore hybrid query receipt."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

_MODEL_SHA256 = "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
_TOKENIZER_SHA256 = "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854"
_MODEL_ID = "Snowflake/snowflake-arctic-embed-m@7802add0519e4bf94c46ef23552176697c7a1ac7"
_DIMENSION = 768
_RECIPE_ID = "cls-query-prefix-512-l2.v1"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=2)
def _verified_assets(model_dir: Path) -> tuple[Path, Path]:
    model = model_dir / "model.onnx"
    tokenizer = model_dir / "tokenizer.json"
    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != "gt.snowflake_onnx_asset.v1":
        raise ValueError("dense_manifest_invalid")
    if (
        manifest.get("model_sha256") != _MODEL_SHA256
        or manifest.get("tokenizer_sha256") != _TOKENIZER_SHA256
        or _sha256(model) != _MODEL_SHA256
        or _sha256(tokenizer) != _TOKENIZER_SHA256
    ):
        raise ValueError("dense_asset_digest_mismatch")
    return model, tokenizer


@lru_cache(maxsize=2)
def _load_encoder(model: Path, tokenizer_path: Path):
    """Pinned assets are immutable for the lifetime of a loaded encoder."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding()
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    return tokenizer, session


def _embed(model: Path, tokenizer_path: Path, texts: list[str]) -> list[tuple[float, ...]]:
    import numpy as np

    tokenizer, session = _load_encoder(model, tokenizer_path)
    encoded = tokenizer.encode_batch(texts)
    input_ids = np.asarray([row.ids for row in encoded], dtype=np.int64)
    attention_mask = np.asarray([row.attention_mask for row in encoded], dtype=np.int64)
    token_type_ids = np.asarray([row.type_ids for row in encoded], dtype=np.int64)
    hidden = session.run(
        ["last_hidden_state"],
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )[0]
    pooled = hidden[:, 0, :]
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    if np.any(~np.isfinite(pooled)) or np.any(norms <= 0):
        raise ValueError("dense_embedding_invalid")
    normalized = pooled / norms
    if normalized.shape[1] != _DIMENSION:
        raise ValueError("dense_embedding_dimension_mismatch")
    return [tuple(float(value) for value in row) for row in normalized]


def model_identity() -> dict[str, str | int]:
    """The pinned asset identity every stored vector must be attributed to.

    Exposed so a persistent index can record which model produced its vectors
    without re-declaring the digests, which would let the two drift apart.
    """
    return {
        "model_id": f"{_MODEL_ID}#{_RECIPE_ID}",
        "recipe_id": _RECIPE_ID,
        "model_sha256": _MODEL_SHA256,
        "tokenizer_sha256": _TOKENIZER_SHA256,
        "dimension": _DIMENSION,
    }


def embed_texts(model_dir: Path, texts: Sequence[str]) -> list[tuple[float, ...]]:
    """Embed ``texts`` with the verified local ONNX assets.

    The single embedding entry point: the contract-embedding index and the
    query side of retrieval both come through here, so there is exactly one
    place where a model digest is checked and exactly one pooling convention.
    Raises rather than degrading -- the caller owns the named degraded reason.
    """
    if not texts:
        return []
    model, tokenizer = _verified_assets(Path(model_dir).resolve())
    return _embed(model, tokenizer, list(texts))


def embed_queries(model_dir: Path, queries: Sequence[str]) -> list[tuple[float, ...]]:
    """Only retrieval queries carry the publisher's instruction prefix."""
    return embed_texts(model_dir, [QUERY_PREFIX + query for query in queries])


def rank_documents(
    *,
    query_text: str,
    documents: Mapping[str, str],
    lexical_scores: Mapping[str, float],
    model_dir: Path,
    index_path: Path,
    source_revision: str,
    graph_revision: str,
    limit: int = 4,
) -> tuple[list[str], dict[str, Any]]:
    """Embed, publish, query, and independently health-check a local corpus."""

    from .hybrid_retrieval import (
        EmbeddingRecord,
        HybridQuery,
        RetrievalIntent,
        SQLiteVectorIndex,
    )

    if not query_text.strip() or not documents:
        raise ValueError("dense_query_input_empty")
    model, tokenizer = _verified_assets(Path(model_dir).resolve())
    document_ids = sorted(documents)
    embedding_model_id = str(model_identity()["model_id"])
    cache_source = "gt.dense.document-content.v2"
    cache_graph = "content-addressed-unbound"
    index = SQLiteVectorIndex(
        index_path,
        model_id=embedding_model_id,
        tokenizer_id=_TOKENIZER_SHA256,
        dimension=_DIMENSION,
        source_revision=cache_source,
        graph_revision=cache_graph,
    )
    try:
        reader = sqlite3.connect(index_path)
        try:
            rows = reader.execute(
                "SELECT document_id,content_hash,model_id,tokenizer_id,dimension,"
                "embedding_json,embedding_hash FROM gt_vector_documents"
            ).fetchall()
        finally:
            reader.close()
        cached = set()
        for doc_id, content_hash, model_id, tokenizer_id, dimension, encoded, digest in rows:
            if (doc_id not in documents or model_id != embedding_model_id
                    or tokenizer_id != _TOKENIZER_SHA256 or dimension != _DIMENSION
                    or content_hash != hashlib.sha256(documents[doc_id].encode("utf-8")).hexdigest()):
                continue
            try:
                vector = tuple(float(value) for value in json.loads(encoded))
                valid = (len(vector) == _DIMENSION and any(vector)
                         and all(math.isfinite(value) for value in vector)
                         and hashlib.sha256(json.dumps(vector, separators=(",", ":")).encode()).hexdigest() == digest)
            except (TypeError, ValueError):
                valid = False
            if valid:
                cached.add(doc_id)
        missing = [doc_id for doc_id in document_ids if doc_id not in cached]
        records = []
        for start in range(0, len(missing), 32):
            batch = missing[start:start + 32]
            embeddings = _embed(model, tokenizer, [documents[doc_id] for doc_id in batch])
            records.extend(EmbeddingRecord(
                document_id=doc_id, text=documents[doc_id], embedding=embedding,
                content_hash=hashlib.sha256(documents[doc_id].encode("utf-8")).hexdigest(),
                model_id=embedding_model_id, tokenizer_id=_TOKENIZER_SHA256,
                source_revision=cache_source, graph_revision=cache_graph,
            ) for doc_id, embedding in zip(batch, embeddings, strict=True))
        removed = [row[0] for row in rows if row[0] not in documents]
        if records or removed:
            index.upsert(records, delete_ids=removed)
        query_vector = _embed(model, tokenizer, [QUERY_PREFIX + query_text])[0]
        result = index.query(
            HybridQuery(
                vector=query_vector, lexical_scores=lexical_scores, graph_scores={},
                limit=min(limit, len(document_ids)), intent=RetrievalIntent.INSPECT,
            )
        )
    finally:
        index.close()
    connection = sqlite3.connect(index_path)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        count = int(connection.execute("SELECT COUNT(*) FROM gt_vector_documents").fetchone()[0])
    finally:
        connection.close()
    ordered = [item.document_id for item in result.items]
    query_ready = bool(ordered) and count == len(document_ids) and quick_check == "ok"
    receipt = {
        "schema": "gt.dense_index_receipt.v1",
        "query_ready": query_ready,
        "model_sha256": _MODEL_SHA256,
        "tokenizer_sha256": _TOKENIZER_SHA256,
        "dimension": _DIMENSION,
        "recipe_id": _RECIPE_ID,
        "source_revision": source_revision,
        "graph_revision": graph_revision,
        "embedded_documents": len(missing),
        "cached_documents": len(cached),
        "document_count": count,
        "query_result_count": len(ordered),
        "index_sha256": _sha256(index_path),
        "sqlite_quick_check": quick_check,
        "exact_rescore": True,
        "ann_fallback_reason": result.fallback_reason,
        "metadata_digest": result.metadata_digest,
        "candidate_set_digest": result.candidate_set_digest,
        "reason": None if query_ready else "dense_query_not_ready",
    }
    return ordered, receipt


__all__ = ["embed_texts", "embed_queries", "model_identity", "rank_documents"]
