"""Verified local ONNX embeddings and an exact-rescore hybrid query receipt."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_MODEL_SHA256 = "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
_TOKENIZER_SHA256 = "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854"
_MODEL_ID = "Snowflake/snowflake-arctic-embed-m@7802add0519e4bf94c46ef23552176697c7a1ac7"
_DIMENSION = 768


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _embed(model: Path, tokenizer_path: Path, texts: list[str]) -> list[tuple[float, ...]]:
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding()
    encoded = tokenizer.encode_batch(texts)
    input_ids = np.asarray([row.ids for row in encoded], dtype=np.int64)
    attention_mask = np.asarray([row.attention_mask for row in encoded], dtype=np.int64)
    token_type_ids = np.asarray([row.type_ids for row in encoded], dtype=np.int64)
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    hidden = session.run(
        ["last_hidden_state"],
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )[0]
    mask = attention_mask[:, :, None].astype(np.float32)
    pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1.0)
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
        "model_id": _MODEL_ID,
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
    model, tokenizer = _verified_assets(Path(model_dir))
    return _embed(model, tokenizer, list(texts))


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
    model, tokenizer = _verified_assets(model_dir)
    document_ids = sorted(documents)
    texts = [documents[document_id] for document_id in document_ids]
    embeddings = _embed(model, tokenizer, [*texts, query_text])
    index = SQLiteVectorIndex(
        index_path,
        model_id=_MODEL_ID,
        tokenizer_id=_TOKENIZER_SHA256,
        dimension=_DIMENSION,
        source_revision=source_revision,
        graph_revision=graph_revision,
    )
    records = [
        EmbeddingRecord(
            document_id=document_id,
            text=text,
            embedding=embedding,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            model_id=_MODEL_ID,
            tokenizer_id=_TOKENIZER_SHA256,
            source_revision=source_revision,
            graph_revision=graph_revision,
        )
        for document_id, text, embedding in zip(document_ids, texts, embeddings[:-1], strict=True)
    ]
    index.upsert(records)
    result = index.query(
        HybridQuery(
            vector=embeddings[-1],
            lexical_scores=lexical_scores,
            graph_scores={},
            limit=min(limit, len(records)),
            intent=RetrievalIntent.INSPECT,
        )
    )
    index.close()
    connection = sqlite3.connect(index_path)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        count = int(connection.execute("SELECT COUNT(*) FROM gt_vector_documents").fetchone()[0])
    finally:
        connection.close()
    ordered = [item.document_id for item in result.items]
    query_ready = bool(ordered) and count == len(records) and quick_check == "ok"
    receipt = {
        "schema": "gt.dense_index_receipt.v1",
        "query_ready": query_ready,
        "model_sha256": _MODEL_SHA256,
        "tokenizer_sha256": _TOKENIZER_SHA256,
        "dimension": _DIMENSION,
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


__all__ = ["embed_texts", "model_identity", "rank_documents"]
