from __future__ import annotations

from pathlib import Path

from gt_engine import dense_runtime


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
        lambda _model, _tokenizer, _texts: [
            (1.0, 0.0),
            (0.0, 1.0),
            (0.9, 0.1),
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
