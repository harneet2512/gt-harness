from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from gt_engine import dense_runtime


def test_encoder_uses_cls_not_mean_pooling(tmp_path, monkeypatch):
    encoded = SimpleNamespace(ids=[1, 2], attention_mask=[1, 1], type_ids=[0, 0])
    tokenizer = SimpleNamespace(encode_batch=lambda texts: [encoded for _ in texts],
                                enable_truncation=lambda **_: None, enable_padding=lambda: None)
    session = SimpleNamespace(run=lambda *_: [np.asarray([[[3., 4.], [100., 0.]]])])
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=lambda *_a, **_k: session))
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(
        Tokenizer=SimpleNamespace(from_file=lambda *_: tokenizer)))
    monkeypatch.setattr(dense_runtime, "_DIMENSION", 2)
    assert np.allclose(dense_runtime._embed(tmp_path / "model", tmp_path / "tokenizer", ["doc"]),
                       [(0.6, 0.8)])


def test_query_prefix_does_not_modify_document_encoding(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dense_runtime, "_verified_assets", lambda *_: (tmp_path, tmp_path))
    monkeypatch.setattr(dense_runtime, "_embed", lambda _m, _t, texts:
                        calls.append(texts) or [(1.0,) for _ in texts])
    dense_runtime.embed_texts(tmp_path, ["document"])
    dense_runtime.embed_queries(tmp_path, ["question"])
    assert calls == [["document"], ["Represent this sentence for searching relevant passages: question"]]
    assert dense_runtime.model_identity()["recipe_id"]


def test_warm_corpus_and_unchanged_document_survive_graph_revisions(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dense_runtime, "_DIMENSION", 2)
    monkeypatch.setattr(dense_runtime, "_verified_assets", lambda *_: (tmp_path, tmp_path))

    def embed(_model, _tokenizer, texts):
        calls.extend(texts)
        return [(1.0, 0.0) if "alpha" in text else (0.0, 1.0) for text in texts]

    monkeypatch.setattr(dense_runtime, "_embed", embed)
    kwargs = dict(query_text="alpha", documents={"a": "alpha", "b": "beta"},
                  lexical_scores={}, model_dir=tmp_path, index_path=tmp_path / "dense.sqlite",
                  source_revision="s1", graph_revision="g1")
    dense_runtime.rank_documents(**kwargs)
    calls.clear()
    dense_runtime.rank_documents(**kwargs)
    assert calls == [dense_runtime.QUERY_PREFIX + "alpha"]
    calls.clear()
    _, receipt = dense_runtime.rank_documents(**{
        **kwargs, "documents": {"a": "alpha", "b": "beta edited"},
        "source_revision": "s2", "graph_revision": "g2",
    })
    assert calls == ["beta edited", dense_runtime.QUERY_PREFIX + "alpha"]
    assert receipt["embedded_documents"] == 1
    assert receipt["cached_documents"] == 1


def test_dense_runtime_publishes_query_ready_exact_rescore_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        dense_runtime,
        "_verified_assets",
        lambda _root: (tmp_path / "model.onnx", tmp_path / "tokenizer.json"),
    )
    monkeypatch.setattr(
        dense_runtime,
        "_embed",
        lambda _model, _tokenizer, texts: [
            (0.9, 0.1) if text.startswith(dense_runtime.QUERY_PREFIX) else
            (1.0, 0.0) if text == "module cache" else (0.0, 1.0) for text in texts
        ],
    )
    monkeypatch.setattr(dense_runtime, "_DIMENSION", 2)

    order, receipt = dense_runtime.rank_documents(
        query_text="module cache",
        documents={"a.py": "module cache", "b.py": "date parser"},
        lexical_scores={"a.py": 2.0, "b.py": 0.1},
        model_dir=tmp_path,
        index_path=tmp_path / "dense.sqlite",
        source_revision="source",
        graph_revision="graph",
        limit=2,
    )

    assert order == ["a.py", "b.py"]
    assert receipt["query_ready"] is True
    assert receipt["document_count"] == 2
    assert receipt["query_result_count"] == 2
    assert receipt["sqlite_quick_check"] == "ok"
    assert receipt["exact_rescore"] is True
