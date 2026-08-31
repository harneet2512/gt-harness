from __future__ import annotations

import hashlib

import pytest

from gt_engine.hybrid_retrieval import (
    EmbeddingRecord,
    HybridQuery,
    RetrievalIntent,
    SQLiteVectorIndex,
)


def _index(tmp_path):
    index = SQLiteVectorIndex(
        tmp_path / "index.db",
        model_id="m1",
        tokenizer_id="t1",
        dimension=2,
        source_revision="src",
        graph_revision="graph",
    )
    for doc, text, vector in (("a", "alpha", (1.0, 0.0)), ("b", "beta", (0.0, 1.0))):
        index.upsert([
            EmbeddingRecord(
                document_id=doc,
                text=text,
                embedding=vector,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                model_id="m1",
                tokenizer_id="t1",
                source_revision="src",
                graph_revision="graph",
            )
        ])
    return index


def test_intent_weights_are_receipted_and_candidate_union_is_identical(tmp_path):
    index = _index(tmp_path)
    results = [
        index.query(
            HybridQuery(
                vector=(1.0, 0.0),
                lexical_scores={"b": 1.0},
                graph_scores={"b": 1.0},
                intent=intent,
            )
        )
        for intent in RetrievalIntent
    ]
    assert {result.candidate_ids for result in results} == {("a", "b")}
    assert results[0].selected_weights == {"vector": 0.5, "lexical": 0.2, "graph": 0.3}
    assert results[1].selected_weights == {"vector": 0.3, "lexical": 0.2, "graph": 0.5}
    assert results[2].selected_weights == {"vector": 0.2, "lexical": 0.5, "graph": 0.3}
    assert all(result.policy_version == "gt.hybrid.intent-exact-rescore.v1" for result in results)
    assert all(result.candidate_set_digest for result in results)


def test_metadata_mutation_and_nonfinite_channel_scores_abstain(tmp_path):
    index = _index(tmp_path)
    index._connection.execute("UPDATE gt_vector_documents SET content_hash='bad' WHERE document_id='a'")
    index._connection.commit()
    assert index.query(HybridQuery(vector=(1.0, 0.0))).fallback_reason == "metadata_identity_invalid"
    index.close()
    index = _index(tmp_path)
    with pytest.raises(ValueError, match="channel_score_invalid"):
        index.query(HybridQuery(vector=(1.0, 0.0), lexical_scores={"a": float("nan")}))
