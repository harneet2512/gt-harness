"""Measure the contract-embedding store against a real graph.

Run against a copy of the reference graph, never the graph itself.  The
embedder is injected: with the pinned ONNX asset present this measures the real
thing, and without it a deterministic local stand-in measures everything except
the forward pass, which the report must then say out loud.

    python scripts/measure_contract_embeddings.py <graph.db> <scratch dir>
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import time
from pathlib import Path

# This worktree, not whichever gt_engine an installed distribution resolves to.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine import contract_embeddings, dense_runtime, retrieval  # noqa: E402

DIMENSION = 768
LINE_SHIFT = 7


class StubEmbedder:
    """A deterministic 768-d stand-in for arctic-embed-m's forward pass.

    Produces a normalised vector from a digest of the text.  It measures the
    pipeline -- rendering, keying, planning, storage, lookup -- and measures
    nothing at all about embedding quality.  Every number it produces must be
    reported as a pipeline number.
    """

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, texts):
        self.count += len(texts)
        return [self.vector(text) for text in texts]

    @staticmethod
    def vector(text: str) -> tuple[float, ...]:
        raw = b"".join(
            hashlib.sha256(f"{index}:{text}".encode()).digest()
            for index in range((DIMENSION * 4 // 32) + 1)
        )
        values = [float(value) - 2147483647.5 for value in
                  struct.unpack(f"<{DIMENSION}I", raw[: DIMENSION * 4])]
        norm = sum(value * value for value in values) ** 0.5
        return tuple(value / norm for value in values)


def _copy(source: Path, target: Path) -> Path:
    shutil.copyfile(source, target)
    return target


def _mutate(path: Path, statements: list[tuple[str, tuple]]) -> Path:
    connection = sqlite3.connect(path)
    try:
        for sql, params in statements:
            connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()
    return path


def _reformat(path: Path) -> Path:
    """Shift every line number without changing a single stored value."""
    return _mutate(
        path,
        [
            ("UPDATE nodes SET start_line = start_line + ?, end_line = end_line + ?",
             (LINE_SHIFT, LINE_SHIFT)),
            ("UPDATE properties SET line = line + ? WHERE line IS NOT NULL",
             (LINE_SHIFT,)),
        ],
    )


def _fingerprint_targets(path: Path, limit: int) -> list[int]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT node_id FROM properties WHERE kind = 'fingerprint' "
                "ORDER BY node_id LIMIT ?",
                (limit,),
            )
        ]
    finally:
        connection.close()


def _return_shape_targets(path: Path, limit: int) -> list[int]:
    """Distinct symbols, not rows: a symbol can carry several return shapes.

    Counting rows would report more symbols changed than exist and make the
    re-embed count look short by the difference.
    """
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT p.node_id FROM properties p "
                "WHERE p.kind = 'return_shape' AND EXISTS ("
                "  SELECT 1 FROM properties f WHERE f.node_id = p.node_id "
                "  AND f.kind = 'fingerprint') "
                "ORDER BY p.node_id LIMIT ?",
                (limit,),
            )
        ]
    finally:
        connection.close()


def _timed(label: str, fn):
    start = time.perf_counter()
    value = fn()
    return value, round(time.perf_counter() - start, 3), label


def main(graph: Path, scratch: Path) -> dict:
    scratch.mkdir(parents=True, exist_ok=True)
    base = _copy(graph, scratch / "base.db")

    inputs, render_seconds, _ = _timed("render", lambda: contract_embeddings.embedding_inputs(base))
    text_lengths = sorted(len(item.text) for item in inputs)
    report: dict = {
        "graph": str(graph),
        "dimension": DIMENSION,
        "embedder": "deterministic stub (arctic-embed-m asset absent)",
        "symbols": len(inputs),
        "symbols_with_fingerprint": sum(1 for item in inputs if item.fingerprint),
        "unique_stable_ids": len({item.stable_id for item in inputs}),
        "derived_stable_ids": sum(
            1 for item in inputs if item.stable_id.startswith("gtsym1:")
        ),
        "render_seconds": render_seconds,
        "contract_text_chars_median": text_lengths[len(text_lengths) // 2],
        "contract_text_chars_max": text_lengths[-1],
        "contract_text_chars_total": sum(text_lengths),
    }

    store_path = scratch / "contract-vectors.sqlite"
    store = contract_embeddings.ContractEmbeddingStore(store_path, dimension=DIMENSION)
    embedder = StubEmbedder()
    cold, cold_seconds, _ = _timed(
        "cold", lambda: store.refresh(base, embed_fn=embedder)
    )
    report["cold_build"] = {
        "embedded": cold["embedded"],
        "seconds": cold_seconds,
        "documents_after": cold["documents_after"],
        "store_bytes": store_path.stat().st_size,
        "index_sha256": cold["index_sha256"],
        "source_revision": cold["source_revision"],
    }

    reformatted = _reformat(_copy(graph, scratch / "reformat.db"))
    quiet = StubEmbedder()
    warm, warm_seconds, _ = _timed(
        "reformat", lambda: store.refresh(reformatted, embed_fn=quiet)
    )
    report["reformat_only"] = {
        "forward_passes": quiet.count,
        "embedded": warm["embedded"],
        "unchanged": warm["unchanged"],
        "deleted": warm["deleted"],
        "seconds": warm_seconds,
    }

    targets = _fingerprint_targets(base, 25)
    semantic = _mutate(
        _copy(graph, scratch / "semantic.db"),
        [
            (
                "UPDATE properties SET value = value || '|measured-change' "
                f"WHERE kind = 'fingerprint' AND node_id IN ({','.join('?' * len(targets))})",
                tuple(targets),
            )
        ],
    )
    changed = StubEmbedder()
    semantic_receipt, semantic_seconds, _ = _timed(
        "semantic", lambda: store.refresh(semantic, embed_fn=changed)
    )
    report["fingerprint_change"] = {
        "symbols_changed": len(targets),
        "forward_passes": changed.count,
        "embedded": semantic_receipt["embedded"],
        "attributed_to_fingerprint": semantic_receipt["embedded_fingerprint_changed"],
        "attributed_to_contract_text": semantic_receipt["embedded_contract_changed"],
        "unchanged": semantic_receipt["unchanged"],
        "seconds": semantic_seconds,
    }

    # Restore the store to the base graph before the return-shape probe, so the
    # two changed sets do not overlap.
    store.refresh(base, embed_fn=StubEmbedder())
    shape_targets = _return_shape_targets(base, 12)
    shaped = _mutate(
        _copy(graph, scratch / "return-shape.db"),
        [
            (
                "UPDATE properties SET value = value || '|undefined' "
                f"WHERE kind = 'return_shape' AND node_id IN ({','.join('?' * len(shape_targets))})",
                tuple(shape_targets),
            )
        ],
    )
    reshaped = StubEmbedder()
    shape_receipt, shape_seconds, _ = _timed(
        "return_shape", lambda: store.refresh(shaped, embed_fn=reshaped)
    )
    report["return_shape_change"] = {
        "symbols_changed": len(shape_targets),
        "forward_passes": reshaped.count,
        "embedded": shape_receipt["embedded"],
        "attributed_to_fingerprint": shape_receipt["embedded_fingerprint_changed"],
        "attributed_to_contract_text": shape_receipt["embedded_contract_changed"],
        "seconds": shape_seconds,
    }
    store.close()

    # Restore once more so the retrieval probes see a store level with `base`.
    level = contract_embeddings.ContractEmbeddingStore(store_path, dimension=DIMENSION)
    level.refresh(base, embed_fn=StubEmbedder())
    level.close()

    os.environ.pop("GT_DENSE_MODEL_DIR", None)
    degraded, degraded_seconds, _ = _timed(
        "degraded",
        lambda: retrieval.dense_rank(
            base, "rejects a null input", 10, store_path=store_path
        ),
    )
    report["dense_rank_without_onnx"] = {
        "available": degraded.available,
        "reason": degraded.reason,
        "results": len(degraded),
        "seconds": degraded_seconds,
    }

    absent_model = scratch / "no-model"
    absent_model.mkdir(exist_ok=True)
    assets_absent = retrieval.dense_rank(
        base, "rejects a null input", 10, model_dir=absent_model, store_path=store_path
    )
    report["dense_rank_with_empty_model_dir"] = {
        "available": assets_absent.available,
        "reason": assets_absent.reason,
        "results": len(assets_absent),
    }

    stub_model = scratch / "stub-model"
    stub_model.mkdir(exist_ok=True)
    for name in ("model.onnx", "tokenizer.json", "manifest.json"):
        (stub_model / name).write_bytes(b"{}")
    lookup_start = time.perf_counter()
    hit = retrieval.dense_rank(
        base, "rejects a null input", 10, model_dir=stub_model, store_path=store_path
    )
    report["dense_rank_store_lookup_with_unverified_model"] = {
        "available": hit.available,
        "reason": hit.reason,
        "seconds": round(time.perf_counter() - lookup_start, 3),
    }

    # The retrieval-side cost with the forward pass removed: what it takes to
    # join a pool to the store and cosine-rank it.  Measured directly rather
    # than through dense_rank, because dense_rank cannot run without the query
    # encoder and this number must not be confused with an end-to-end one.
    connection = sqlite3.connect(f"{base.resolve().as_uri()}?mode=ro", uri=True)
    try:
        from gt_engine import contract as contract_module

        contract_ids = contract_module.symbol_node_ids(connection)
    finally:
        connection.close()
    pool = list(contract_ids)[: retrieval.DENSE_POOL_LIMIT]
    lookup_started = time.perf_counter()
    lookup = contract_embeddings.lookup_vectors(store_path, contract_ids, pool)
    lookup_seconds = time.perf_counter() - lookup_started
    score_started = time.perf_counter()
    scored = contract_embeddings.score_pool(StubEmbedder.vector("probe"), lookup.vectors)
    score_seconds = time.perf_counter() - score_started
    report["store_lookup"] = {
        "pool": len(pool),
        "hits": lookup.hits,
        "misses": lookup.misses,
        "dimension": lookup.dimension,
        "reason": lookup.reason,
        "lookup_seconds": round(lookup_seconds, 4),
        "cosine_seconds": round(score_seconds, 4),
        "ranked": len(scored),
    }

    whole_started = time.perf_counter()
    whole = contract_embeddings.lookup_vectors(store_path, contract_ids, contract_ids)
    report["store_lookup_whole_graph"] = {
        "pool": len(contract_ids),
        "hits": whole.hits,
        "misses": whole.misses,
        "seconds": round(time.perf_counter() - whole_started, 4),
    }

    dense_runtime_available = True
    try:
        dense_runtime.embed_texts(stub_model, ["probe"])
    except Exception as exc:  # noqa: BLE001 - this *is* the measurement
        dense_runtime_available = False
        report["onnx_asset_check"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    report["onnx_asset_verified"] = dense_runtime_available
    return report


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
