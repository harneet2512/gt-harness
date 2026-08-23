from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_engine.dense_semantic_index import (
    DENSE_INDEX_SCHEMA,
    DenseIndexStatus,
    PersistentDenseSemanticIndex,
)


class RecordingDenseBackend:
    def __init__(self, *, identity: str = "fake:model-a", provider_calls: int = 0) -> None:
        self.identity = identity
        self.provider_calls = provider_calls
        self.document_batches: list[tuple[str, ...]] = []
        self.query_texts: list[str] = []

    @staticmethod
    def _vector(text: str) -> tuple[float, float, float]:
        lowered = text.lower()
        return (
            float(lowered.count("billing") + lowered.count("invoice")),
            float(lowered.count("authentication") + lowered.count("token")),
            1.0,
        )

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.document_batches.append(texts)
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        self.query_texts.append(text)
        return self._vector(text)

    def receipt(self) -> dict[str, object]:
        return {
            "backend": "fake_local",
            "model_name": self.identity,
            "model_revision": "test-revision",
            "model_sha256": self.identity.rsplit(":", 1)[-1],
            "tokenizer_sha256": "tokenizer-test",
            "pooling": "cls",
            "normalization": "l2",
            "max_length": 512,
            "embedding_dimension": 3,
            "network_calls": 0,
            "provider_calls": self.provider_calls,
        }


class FailingDenseBackend(RecordingDenseBackend):
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("deliberate local encoder failure")


class QueryProviderBackend(RecordingDenseBackend):
    def embed_query(self, text: str) -> tuple[float, ...]:
        result = super().embed_query(text)
        self.provider_calls = 1
        return result


class UnreceiptedDenseBackend:
    identity = "unreceipted"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0,) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0,)


def _repository(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "billing.py").write_text(
        "from decimal import Decimal\n\n"
        "def calculate_invoice(total: Decimal) -> Decimal:\n"
        "    return total\n",
        encoding="utf-8",
    )
    (root / "src" / "auth.ts").write_text(
        "export function validateToken(token: string): boolean { return !!token; }\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("billing prose is not source", encoding="utf-8")


def test_builds_repository_wide_source_index_and_returns_evidenced_candidates(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    backend = RecordingDenseBackend()
    index = PersistentDenseSemanticIndex(tmp_path, backend=backend)

    receipt = index.ensure()

    assert receipt.schema == DENSE_INDEX_SCHEMA
    assert receipt.status is DenseIndexStatus.READY
    assert receipt.query_ready is True
    assert receipt.files_discovered == 2
    assert receipt.files_indexed == 2
    assert receipt.provider_calls == 0
    assert receipt.network_calls == 0
    assert receipt.graph_input_identity
    assert receipt.source_revision
    assert receipt.index_checksum
    assert len(backend.document_batches) == 1
    embedded = "\n".join(backend.document_batches[0])
    assert "src/billing.py" in embedded
    assert "src/auth.ts" in embedded
    assert "README.md" not in embedded

    result = index.query("fix invoice billing totals", limit=2)

    assert result.query_ready is True
    assert [candidate.path for candidate in result.candidates] == [
        "src/billing.py",
        "src/auth.ts",
    ]
    assert result.candidates[0].score > result.candidates[1].score
    assert result.candidates[0].source_revision == receipt.source_revision
    assert result.candidates[0].content_sha256
    assert result.candidates[0].summary_sha256
    assert result.candidates[0].evidence == (
        "repository_source",
        "deterministic_file_summary",
        "dense_cosine",
        "fake:model-a",
    )


def test_reopens_valid_persistent_index_without_reembedding_documents(tmp_path: Path) -> None:
    _repository(tmp_path)
    first_backend = RecordingDenseBackend()
    first = PersistentDenseSemanticIndex(tmp_path, backend=first_backend)
    built = first.ensure()
    assert first_backend.document_batches

    second_backend = RecordingDenseBackend()
    reopened = PersistentDenseSemanticIndex(tmp_path, backend=second_backend)
    reused = reopened.ensure()

    assert reused.index_checksum == built.index_checksum
    assert reused.source_revision == built.source_revision
    assert second_backend.document_batches == []
    assert reopened.query("authentication token", limit=1).candidates[0].path == "src/auth.ts"


def test_revision_mismatch_is_stale_and_never_releases_old_candidates(tmp_path: Path) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    before = index.ensure()
    (tmp_path / "src" / "billing.py").write_text(
        "def calculate_invoice():\n    return 'changed'\n", encoding="utf-8"
    )

    inspected = index.inspect()
    result = index.query("invoice", limit=5)

    assert inspected.status is DenseIndexStatus.STALE
    assert inspected.query_ready is False
    assert "source_revision_mismatch" in inspected.degraded_reasons
    assert result.query_ready is False
    assert result.candidates == ()
    assert "source_revision_mismatch" in result.degraded_reasons

    rebuilt = index.ensure()
    assert rebuilt.status is DenseIndexStatus.READY
    assert rebuilt.source_revision != before.source_revision
    assert index.query("invoice", limit=1).query_ready is True


def test_model_mismatch_invalidates_persisted_index(tmp_path: Path) -> None:
    _repository(tmp_path)
    PersistentDenseSemanticIndex(
        tmp_path, backend=RecordingDenseBackend(identity="fake:model-a")
    ).ensure()

    changed = PersistentDenseSemanticIndex(
        tmp_path, backend=RecordingDenseBackend(identity="fake:model-b")
    )
    receipt = changed.inspect()

    assert receipt.status is DenseIndexStatus.STALE
    assert receipt.query_ready is False
    assert "model_identity_mismatch" in receipt.degraded_reasons


def test_corrupt_index_is_explicitly_failed_not_healthy(tmp_path: Path) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    index.ensure()
    index.index_path.write_text("{not valid json", encoding="utf-8")

    receipt = index.inspect()
    result = index.query("invoice")

    assert receipt.status is DenseIndexStatus.FAILED
    assert receipt.query_ready is False
    assert "dense_index_corrupt" in receipt.degraded_reasons
    assert result.candidates == ()


def test_failed_rebuild_persists_failure_and_never_presents_partial_index(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    first = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    first.ensure()
    (tmp_path / "src" / "new.go").write_text(
        "package src\nfunc NewBilling() int { return 1 }\n", encoding="utf-8"
    )
    failing = PersistentDenseSemanticIndex(tmp_path, backend=FailingDenseBackend())

    receipt = failing.ensure()

    assert receipt.status is DenseIndexStatus.FAILED
    assert receipt.query_ready is False
    assert any(reason.startswith("dense_embedding_failed:") for reason in receipt.degraded_reasons)
    persisted = json.loads(failing.index_path.read_text(encoding="utf-8"))
    assert persisted["receipt"]["status"] == "FAILED"
    assert persisted["documents"] == []
    assert failing.query("billing").candidates == ()


def test_provider_backed_backend_is_rejected_even_if_it_returns_vectors(tmp_path: Path) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(
        tmp_path,
        backend=RecordingDenseBackend(provider_calls=1),
    )

    receipt = index.ensure()

    assert receipt.status is DenseIndexStatus.FAILED
    assert receipt.query_ready is False
    assert "nonlocal_backend_activity" in receipt.degraded_reasons
    assert receipt.provider_calls == 1


def test_query_rejects_backend_that_reports_provider_activity_after_build(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    backend = QueryProviderBackend()
    index = PersistentDenseSemanticIndex(tmp_path, backend=backend)
    assert index.ensure().query_ready is True

    result = index.query("billing")

    assert result.query_ready is False
    assert result.status is DenseIndexStatus.FAILED
    assert result.candidates == ()
    assert "nonlocal_backend_activity" in result.degraded_reasons


def test_backend_without_auditable_local_receipt_is_rejected(tmp_path: Path) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=UnreceiptedDenseBackend())

    receipt = index.ensure()

    assert receipt.status is DenseIndexStatus.FAILED
    assert receipt.query_ready is False
    assert any(
        reason.startswith("dense_backend_receipt_incomplete:")
        for reason in receipt.degraded_reasons
    )


def test_no_backend_and_no_supported_source_have_explicit_degraded_states(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.md").write_text("only prose", encoding="utf-8")

    no_backend = PersistentDenseSemanticIndex(empty, backend=None).ensure()
    assert no_backend.status is DenseIndexStatus.FAILED
    assert no_backend.query_ready is False
    assert "dense_backend_unavailable" in no_backend.degraded_reasons

    supported_backend = PersistentDenseSemanticIndex(
        empty, backend=RecordingDenseBackend()
    ).ensure()
    assert supported_backend.status is DenseIndexStatus.DEGRADED
    assert supported_backend.query_ready is False
    assert "no_supported_source" in supported_backend.degraded_reasons


def test_index_checksum_tampering_is_detected(tmp_path: Path) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    index.ensure()
    state = json.loads(index.index_path.read_text(encoding="utf-8"))
    state["documents"][0]["vector"][0] += 1.0
    index.index_path.write_text(json.dumps(state), encoding="utf-8")

    receipt = index.inspect()

    assert receipt.status is DenseIndexStatus.FAILED
    assert receipt.query_ready is False
    assert "dense_index_checksum_mismatch" in receipt.degraded_reasons


def test_atomic_replace_failure_preserves_old_artifact_as_explicitly_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    index.ensure()
    before = index.index_path.read_bytes()
    (tmp_path / "src" / "billing.py").write_text(
        "def calculate_invoice():\n    return 99\n", encoding="utf-8"
    )

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("deliberate replace interruption")

    monkeypatch.setattr("gt_engine.dense_semantic_index.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace interruption"):
        index.ensure()

    assert index.index_path.read_bytes() == before
    receipt = index.inspect()
    assert receipt.status is DenseIndexStatus.STALE
    assert receipt.query_ready is False
    assert "source_revision_mismatch" in receipt.degraded_reasons


@pytest.mark.parametrize("limit", [0, -1])
def test_query_limit_never_leaks_candidates(tmp_path: Path, limit: int) -> None:
    _repository(tmp_path)
    index = PersistentDenseSemanticIndex(tmp_path, backend=RecordingDenseBackend())
    index.ensure()
    assert index.query("billing", limit=limit).candidates == ()
