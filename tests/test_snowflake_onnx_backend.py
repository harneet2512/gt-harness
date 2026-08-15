from __future__ import annotations

import hashlib

import pytest

from gt_engine.snowflake_onnx import SnowflakeOnnxDenseBackend


class _FakeModel:
    model_name = "Snowflake/snowflake-arctic-embed-m"
    model_sha256 = "model-sha"

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def encode(self, texts: tuple[str, ...], *, is_query: bool) -> tuple[tuple[float, ...], ...]:
        self.calls.append((texts, is_query))
        return tuple((float(len(text)), 1.0) for text in texts)


def test_snowflake_backend_uses_query_role_and_passage_role():
    model = _FakeModel()
    backend = SnowflakeOnnxDenseBackend(model=model)

    query = backend.embed_query("repair parser")
    documents = backend.embed_documents(("parser source", "parser test"))

    assert query == (13.0, 1.0)
    assert documents == ((13.0, 1.0), (11.0, 1.0))
    assert model.calls == [
        (("repair parser",), True),
        (("parser source", "parser test"), False),
    ]


def test_snowflake_backend_content_cache_avoids_duplicate_document_encode():
    model = _FakeModel()
    backend = SnowflakeOnnxDenseBackend(model=model)

    first = backend.embed_documents(("same source", "other source"))
    second = backend.embed_documents(("other source", "same source"))

    assert second == (first[1], first[0])
    assert model.calls == [(('same source', 'other source'), False)]
    receipt = backend.receipt()
    assert receipt["document_cache_hits"] == 2
    assert receipt["document_cache_misses"] == 2


def test_snowflake_backend_receipt_binds_model_identity_without_api():
    backend = SnowflakeOnnxDenseBackend(model=_FakeModel())

    receipt = backend.receipt()

    assert receipt["backend"] == "snowflake_onnx"
    assert receipt["model_name"] == "Snowflake/snowflake-arctic-embed-m"
    assert receipt["model_sha256"] == "model-sha"
    assert receipt["network_calls"] == 0
    assert receipt["provider_calls"] == 0
    assert receipt["intra_op_num_threads"] == 1
    assert receipt["inter_op_num_threads"] == 1
    assert backend.identity == (
        "snowflake_onnx:Snowflake/snowflake-arctic-embed-m@sha256:model-sha"
    )


def test_snowflake_backend_requires_exact_pinned_model_digest(tmp_path):
    model_path = tmp_path / "model.onnx"
    tokenizer_path = tmp_path / "tokenizer.json"
    model_path.write_bytes(b"not-the-pinned-model")
    tokenizer_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="model SHA-256 mismatch"):
        SnowflakeOnnxDenseBackend.from_directory(
            tmp_path,
            expected_model_sha256=hashlib.sha256(b"different").hexdigest(),
        )


def test_snowflake_backend_requires_exact_pinned_tokenizer_digest(tmp_path):
    model_path = tmp_path / "model.onnx"
    tokenizer_path = tmp_path / "tokenizer.json"
    model_path.write_bytes(b"model")
    tokenizer_path.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="tokenizer SHA-256 mismatch"):
        SnowflakeOnnxDenseBackend.from_directory(
            tmp_path,
            expected_model_sha256=hashlib.sha256(b"model").hexdigest(),
            expected_tokenizer_sha256=hashlib.sha256(b"expected").hexdigest(),
        )


def test_snowflake_backend_unavailable_directory_fails_before_inference(tmp_path):
    with pytest.raises(FileNotFoundError, match="model.onnx"):
        SnowflakeOnnxDenseBackend.from_directory(tmp_path)
