"""Pinned, local Snowflake Arctic dense retrieval backend.

The backend performs no download and no provider call.  A workflow or image
must provision the exact ONNX and tokenizer artifacts before construction.
Query and passage roles follow Snowflake's published retrieval contract:
queries receive the model's search prefix, passages do not, both use CLS
pooling, and the resulting vectors are L2 normalized.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

SNOWFLAKE_MODEL_NAME = "Snowflake/snowflake-arctic-embed-m"
SNOWFLAKE_MODEL_SHA256 = (
    "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
)
SNOWFLAKE_TOKENIZER_SHA256 = (
    "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854"
)
SNOWFLAKE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
SNOWFLAKE_MAX_LENGTH = 512


class _RoleAwareModel(Protocol):
    model_name: str
    model_sha256: str

    def encode(
        self, texts: tuple[str, ...], *, is_query: bool
    ) -> Sequence[Sequence[float]]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _SnowflakeOnnxModel:
    """Small ONNX Runtime owner implementing Snowflake's exact encode recipe."""

    model_name = SNOWFLAKE_MODEL_NAME

    def __init__(
        self,
        *,
        model_path: Path,
        tokenizer_path: Path,
        model_sha256: str,
        batch_size: int = 32,
    ) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Snowflake ONNX retrieval requires numpy, onnxruntime, and tokenizers"
            ) from exc

        def _thread_count(name: str) -> int:
            raw = os.environ.get(name, "1").strip()
            try:
                value = int(raw)
            except ValueError:
                return 1
            return max(1, min(value, 64))

        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.intra_op_num_threads = _thread_count("GT_DENSE_INTRA_OP_THREADS")
        self.inter_op_num_threads = _thread_count("GT_DENSE_INTER_OP_THREADS")
        options.intra_op_num_threads = self.intra_op_num_threads
        options.inter_op_num_threads = self.inter_op_num_threads
        options.add_session_config_entry("session.use_deterministic_compute", "1")
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {item.name for item in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._np = np
        self._batch_size = max(1, int(batch_size))
        self._lock = threading.Lock()
        self.model_sha256 = model_sha256

    def encode(
        self, texts: tuple[str, ...], *, is_query: bool
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        prepared = tuple(
            f"{SNOWFLAKE_QUERY_PREFIX}{text}" if is_query else str(text)
            for text in texts
        )
        output: list[tuple[float, ...]] = []
        for start in range(0, len(prepared), self._batch_size):
            output.extend(self._encode_chunk(prepared[start : start + self._batch_size]))
        return tuple(output)

    def _encode_chunk(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        np = self._np
        with self._lock:
            self._tokenizer.enable_truncation(max_length=SNOWFLAKE_MAX_LENGTH)
            self._tokenizer.enable_padding()
            encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [item.attention_mask for item in encodings], dtype=np.int64
        )
        feed: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)
        raw = self._session.run(None, feed)[0]
        pooled = raw[:, 0] if getattr(raw, "ndim", 0) == 3 else raw
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.clip(norms, 1e-12, None)
        return tuple(tuple(float(value) for value in row) for row in normalized)


class SnowflakeOnnxDenseBackend:
    """DenseEmbeddingBackend adapter with content-addressed passage caching."""

    def __init__(self, *, model: _RoleAwareModel) -> None:
        self._model = model
        self._document_cache: dict[str, tuple[float, ...]] = {}
        self._query_cache: dict[str, tuple[float, ...]] = {}
        self._document_cache_hits = 0
        self._document_cache_misses = 0
        self._query_cache_hits = 0
        self._query_cache_misses = 0

    @property
    def identity(self) -> str:
        """Content-bound identity persisted in every dense channel receipt."""

        return f"snowflake_onnx:{self._model.model_name}@sha256:{self._model.model_sha256}"

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        expected_model_sha256: str = SNOWFLAKE_MODEL_SHA256,
        expected_tokenizer_sha256: str = SNOWFLAKE_TOKENIZER_SHA256,
        batch_size: int = 32,
    ) -> SnowflakeOnnxDenseBackend:
        root = Path(directory).resolve()
        model_path = root / "model.onnx"
        tokenizer_path = root / "tokenizer.json"
        if not model_path.is_file():
            raise FileNotFoundError(f"Snowflake ONNX model not found: {model_path}")
        if not tokenizer_path.is_file():
            raise FileNotFoundError(f"Snowflake tokenizer not found: {tokenizer_path}")
        actual_sha256 = _sha256(model_path)
        expected = str(expected_model_sha256 or "").lower()
        if expected and actual_sha256.lower() != expected:
            raise ValueError(
                "Snowflake model SHA-256 mismatch: "
                f"expected {expected}, observed {actual_sha256}"
            )
        actual_tokenizer_sha256 = _sha256(tokenizer_path)
        expected_tokenizer = str(expected_tokenizer_sha256 or "").lower()
        if (
            expected_tokenizer
            and actual_tokenizer_sha256.lower() != expected_tokenizer
        ):
            raise ValueError(
                "Snowflake tokenizer SHA-256 mismatch: "
                f"expected {expected_tokenizer}, observed {actual_tokenizer_sha256}"
            )
        return cls(
            model=_SnowflakeOnnxModel(
                model_path=model_path,
                tokenizer_path=tokenizer_path,
                model_sha256=actual_sha256,
                batch_size=batch_size,
            )
        )

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(str(text).encode("utf-8", "surrogatepass")).hexdigest()

    def embed_query(self, text: str) -> tuple[float, ...]:
        key = self._key(text)
        cached = self._query_cache.get(key)
        if cached is not None:
            self._query_cache_hits += 1
            return cached
        self._query_cache_misses += 1
        rows = tuple(self._model.encode((str(text),), is_query=True))
        if len(rows) != 1:
            raise ValueError("Snowflake ONNX model returned an invalid query batch")
        result = tuple(float(value) for value in rows[0])
        self._query_cache[key] = result
        return result

    def embed_documents(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        keys = tuple(self._key(text) for text in texts)
        missing: list[tuple[str, str]] = []
        seen_missing: set[str] = set()
        for key, source in zip(keys, texts, strict=True):
            if key in self._document_cache:
                self._document_cache_hits += 1
            elif key not in seen_missing:
                self._document_cache_misses += 1
                seen_missing.add(key)
                missing.append((key, str(source)))
        if missing:
            encoded = tuple(
                self._model.encode(tuple(text for _, text in missing), is_query=False)
            )
            if len(encoded) != len(missing):
                raise ValueError("Snowflake ONNX model returned an invalid document batch")
            for (key, _), row in zip(missing, encoded, strict=True):
                self._document_cache[key] = tuple(float(value) for value in row)
        return tuple(self._document_cache[key] for key in keys)

    def receipt(self) -> dict[str, Any]:
        return {
            "backend": "snowflake_onnx",
            "model_name": str(self._model.model_name),
            "model_sha256": str(self._model.model_sha256),
            "document_cache_hits": self._document_cache_hits,
            "document_cache_misses": self._document_cache_misses,
            "query_cache_hits": self._query_cache_hits,
            "query_cache_misses": self._query_cache_misses,
            "network_calls": 0,
            "provider_calls": 0,
            "intra_op_num_threads": int(
                getattr(self._model, "intra_op_num_threads", 1)
            ),
            "inter_op_num_threads": int(
                getattr(self._model, "inter_op_num_threads", 1)
            ),
        }


__all__ = [
    "SNOWFLAKE_MAX_LENGTH",
    "SNOWFLAKE_MODEL_NAME",
    "SNOWFLAKE_MODEL_SHA256",
    "SNOWFLAKE_QUERY_PREFIX",
    "SNOWFLAKE_TOKENIZER_SHA256",
    "SnowflakeOnnxDenseBackend",
]
