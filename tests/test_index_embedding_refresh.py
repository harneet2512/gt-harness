from __future__ import annotations

from gt_engine import contract_embeddings, indexer


def test_index_receipt_calls_real_store_refresh_and_reports_failure(tmp_path, monkeypatch):
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"graph fixture")
    graph.with_suffix(".manifest.json").write_text('{"graph_revision":"g1"}', encoding="utf-8")
    monkeypatch.setattr(indexer, "is_code_repo", lambda *_: True)
    monkeypatch.setattr(indexer, "ensure_index", lambda *_a, **_k: str(graph))
    monkeypatch.setattr(indexer, "_graph_schema_receipt", lambda *_: (True, ""))
    monkeypatch.setattr(indexer, "_graph_phase_metadata", lambda *_:
                        {"analysis_state": "complete", "analysis_failure_reason": ""})
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "model"))
    monkeypatch.delenv("GT_CONTRACT_EMBEDDING_INDEX", raising=False)
    calls = []

    def refresh(self, path, *, embed_fn, deadline=None):
        calls.append(path)
        raise ValueError("fixture invalid model")

    monkeypatch.setattr(contract_embeddings.ContractEmbeddingStore, "refresh", refresh)
    receipt = indexer.ensure_index_with_receipt(tmp_path)
    assert calls == [graph]
    assert receipt.success
    assert receipt.embedding_state == "failed"
    assert receipt.embedding_failure_reason == "ValueError"
