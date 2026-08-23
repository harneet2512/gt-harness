"""Persistent, revision-bound dense retrieval over every supported source file.

This module is the dense candidate-generation boundary for the canonical
product.  It deliberately does not accept a sparse candidate set: the corpus
is derived from the canonical repository identity, so semantic retrieval can
recover a relevant file that exact, lexical, BM25, or graph seeding missed.

The persisted artifact is safe-by-construction rather than best-effort.  A
query releases candidates only when repository content, model identity,
schema, and payload checksum all match.  Encoding is local; any backend that
reports provider or network activity is rejected.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.hybrid_retrieval import DenseEmbeddingBackend
from gt_engine.language_registry import (
    LanguageResolutionStatus,
    resolve_language,
)
from gt_engine.repository_graph_service import (
    RepositoryIdentity,
    compute_repository_identity,
)

DENSE_INDEX_SCHEMA = "gt.dense_semantic_index.v1"
DENSE_RECEIPT_SCHEMA = "gt.dense_semantic_index_receipt.v1"
_READY = frozenset({"READY", "READY_WITH_DECLARED_LIMITATIONS"})
_MODEL_IDENTITY_FIELDS = (
    "backend",
    "model_name",
    "model_revision",
    "model_sha256",
    "tokenizer_sha256",
    "pooling",
    "normalization",
    "max_length",
    "embedding_dimension",
)
_SEMANTIC_LINE = re.compile(
    r"(?:\b(?:class|def|fn|func|function|interface|trait|struct|enum|type|"
    r"import|from|export|require|package|module|route|test|describe|it)\b|"
    r"(?:->|=>)|\b(?:GET|POST|PUT|PATCH|DELETE)\b)",
    re.IGNORECASE,
)


class DenseIndexStatus(StrEnum):
    ABSENT = "ABSENT"
    BUILDING = "BUILDING"
    READY = "READY"
    READY_WITH_DECLARED_LIMITATIONS = "READY_WITH_DECLARED_LIMITATIONS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class DenseIndexReceipt:
    repository: str
    commit_sha: str
    branch: str
    working_tree_state: str
    source_revision: str
    graph_input_identity: str
    model_identity: str
    model_receipt: dict[str, Any]
    status: DenseIndexStatus
    build_started: str
    build_completed: str
    files_discovered: int
    files_indexed: int
    files_failed: int
    files_skipped: int
    embedding_dimension: int
    index_path: str
    index_checksum: str
    query_ready: bool
    degraded_reasons: tuple[str, ...] = ()
    failed_paths: tuple[str, ...] = ()
    provider_calls: int = 0
    network_calls: int = 0
    schema: str = DENSE_INDEX_SCHEMA
    receipt_schema: str = DENSE_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        expected_ready = self.status.value in _READY
        if self.query_ready != expected_ready:
            raise ValueError("query_ready must exactly match a READY dense index status")
        if self.query_ready and (
            not self.source_revision
            or not self.graph_input_identity
            or not self.model_identity
            or not self.index_checksum
            or self.files_indexed < 1
        ):
            raise ValueError("a READY dense index requires all persisted identities")
        if self.provider_calls < 0 or self.network_calls < 0:
            raise ValueError("backend activity counters must not be negative")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["degraded_reasons"] = list(self.degraded_reasons)
        value["failed_paths"] = list(self.failed_paths)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DenseIndexReceipt:
        row = dict(value)
        if row.get("schema") != DENSE_INDEX_SCHEMA:
            raise ValueError("unsupported dense index schema")
        if row.get("receipt_schema") != DENSE_RECEIPT_SCHEMA:
            raise ValueError("unsupported dense index receipt schema")
        row["status"] = DenseIndexStatus(str(row["status"]))
        row["model_receipt"] = _json_mapping(row.get("model_receipt", {}))
        row["degraded_reasons"] = tuple(
            str(reason) for reason in row.get("degraded_reasons", ())
        )
        row["failed_paths"] = tuple(str(path) for path in row.get("failed_paths", ()))
        return cls(**row)


@dataclass(frozen=True, slots=True)
class DenseFileCandidate:
    path: str
    rank: int
    score: float
    source_revision: str
    content_sha256: str
    summary_sha256: str
    model_identity: str
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DenseQueryResult:
    query_ready: bool
    status: DenseIndexStatus
    source_revision: str
    model_identity: str
    candidates: tuple[DenseFileCandidate, ...]
    degraded_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_ready": self.query_ready,
            "status": self.status.value,
            "source_revision": self.source_revision,
            "model_identity": self.model_identity,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "degraded_reasons": list(self.degraded_reasons),
        }


@dataclass(frozen=True, slots=True)
class _DenseDocument:
    path: str
    content_sha256: str
    summary: str
    summary_sha256: str
    vector: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_sha256": self.content_sha256,
            "summary": self.summary,
            "summary_sha256": self.summary_sha256,
            "vector": list(self.vector),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> _DenseDocument:
        path = str(value.get("path", "")).replace("\\", "/").strip()
        content_sha256 = str(value.get("content_sha256", ""))
        summary = str(value.get("summary", ""))
        summary_sha256 = str(value.get("summary_sha256", ""))
        vector = tuple(float(item) for item in value.get("vector", ()))
        if not path or not content_sha256 or not summary_sha256 or not vector:
            raise ValueError("dense document is incomplete")
        if _sha256_text(summary) != summary_sha256:
            raise ValueError("dense document summary checksum mismatch")
        if not all(math.isfinite(item) for item in vector):
            raise ValueError("dense document vector is not finite")
        return cls(path, content_sha256, summary, summary_sha256, vector)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", "surrogatepass")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    return str(value)


def _json_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _graph_input_identity(identity: RepositoryIdentity) -> str:
    return _sha256_json(
        {
            "graph_input_hashes": identity.graph_input_hashes,
            "submodule_state": identity.submodule_state,
        }
    )


def _backend_receipt(backend: DenseEmbeddingBackend | None) -> dict[str, Any]:
    if backend is None:
        return {}
    receipt = getattr(backend, "receipt", None)
    if not callable(receipt):
        return {}
    return _json_mapping(receipt())


def _model_identity(
    backend: DenseEmbeddingBackend | None, receipt: Mapping[str, Any]
) -> str:
    if backend is None:
        return ""
    declared = str(getattr(backend, "identity", "")).strip()
    stable = {field_name: receipt.get(field_name) for field_name in _MODEL_IDENTITY_FIELDS}
    return f"{declared or type(backend).__qualname__}@{_sha256_json(stable)}"


def _activity(receipt: Mapping[str, Any], name: str) -> int:
    try:
        return max(0, int(receipt.get(name, 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return 1


def _receipt_failure(receipt: Mapping[str, Any]) -> str:
    required = {
        "backend",
        "model_name",
        "model_sha256",
        "embedding_dimension",
        "network_calls",
        "provider_calls",
    }
    missing = sorted(field_name for field_name in required if field_name not in receipt)
    if missing:
        return "dense_backend_receipt_incomplete:" + ",".join(missing)
    if _activity(receipt, "provider_calls") or _activity(receipt, "network_calls"):
        return "nonlocal_backend_activity"
    return ""


def _semantic_summary(
    *, path: str, payload: bytes, max_chars: int
) -> tuple[str, str]:
    text = payload.decode("utf-8", "replace").replace("\x00", " ")
    resolution = resolve_language(path, payload[:65_536])
    language = (
        resolution.capability.name
        if resolution.status is LanguageResolutionStatus.RESOLVED
        and resolution.capability is not None
        else "unknown"
    )
    lines = text.splitlines()
    selected_positions: list[int] = list(range(min(32, len(lines))))
    selected_positions.extend(
        index
        for index, line in enumerate(lines)
        if index >= 32 and _SEMANTIC_LINE.search(line)
    )
    selected: list[str] = []
    seen: set[int] = set()
    used = 0
    limit = max(512, int(max_chars))
    for position in selected_positions:
        if position in seen:
            continue
        seen.add(position)
        rendered = f"L{position + 1}: {lines[position].rstrip()}"
        if used + len(rendered) + 1 > limit:
            break
        selected.append(rendered)
        used += len(rendered) + 1
    truncated = len(seen) < len(lines)
    header = (
        f"path: {path}\n"
        f"language: {language}\n"
        f"source_lines: {len(lines)}\n"
        f"summary_truncated: {str(truncated).lower()}"
    )
    summary = "\n".join((header, *selected))
    return summary, _sha256_text(summary)


class PersistentDenseSemanticIndex:
    """Own one local dense file index bound to the actual repository revision."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        backend: DenseEmbeddingBackend | None,
        state_dir: str | os.PathLike[str] | None = None,
        max_file_bytes: int = 262_144,
        max_summary_chars: int = 12_000,
        embedding_batch_size: int = 64,
    ) -> None:
        self.root = Path(root).resolve()
        self.backend = backend
        self.state_dir = (
            Path(state_dir).resolve() if state_dir is not None else self.root / ".groundtruth"
        )
        self.index_path = self.state_dir / "dense-semantic-index.v1.json"
        self.max_file_bytes = max(65_536, int(max_file_bytes))
        self.max_summary_chars = max(512, int(max_summary_chars))
        self.embedding_batch_size = max(1, int(embedding_batch_size))

    def _empty_receipt(
        self,
        *,
        identity: RepositoryIdentity,
        status: DenseIndexStatus,
        reasons: Sequence[str],
        model_receipt: Mapping[str, Any] | None = None,
        started: str = "",
        completed: str = "",
        files_discovered: int = 0,
        files_indexed: int = 0,
        files_failed: int = 0,
        files_skipped: int = 0,
        failed_paths: Sequence[str] = (),
        dimension: int = 0,
        checksum: str = "",
    ) -> DenseIndexReceipt:
        observed_model_receipt = _json_mapping(model_receipt or {})
        return DenseIndexReceipt(
            repository=identity.repository,
            commit_sha=identity.commit_sha,
            branch=identity.branch,
            working_tree_state=identity.working_tree_state,
            source_revision=identity.source_revision,
            graph_input_identity=_graph_input_identity(identity),
            model_identity=_model_identity(self.backend, observed_model_receipt),
            model_receipt=observed_model_receipt,
            status=status,
            build_started=started,
            build_completed=completed,
            files_discovered=files_discovered,
            files_indexed=files_indexed,
            files_failed=files_failed,
            files_skipped=files_skipped,
            embedding_dimension=dimension,
            index_path=str(self.index_path),
            index_checksum=checksum,
            query_ready=status.value in _READY,
            degraded_reasons=tuple(dict.fromkeys(str(reason) for reason in reasons if reason)),
            failed_paths=tuple(dict.fromkeys(str(path) for path in failed_paths if path)),
            provider_calls=_activity(observed_model_receipt, "provider_calls"),
            network_calls=_activity(observed_model_receipt, "network_calls"),
        )

    @staticmethod
    def _documents_checksum(documents: Sequence[_DenseDocument]) -> str:
        return _sha256_json([document.as_dict() for document in documents])

    def _write_state(
        self, receipt: DenseIndexReceipt, documents: Sequence[_DenseDocument]
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "schema": DENSE_INDEX_SCHEMA,
            "receipt": receipt.as_dict(),
            "documents": [document.as_dict() for document in documents],
        }
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.state_dir,
                prefix=".dense-semantic-index.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.index_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _load_state(self) -> tuple[DenseIndexReceipt, tuple[_DenseDocument, ...]]:
        state = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema") != DENSE_INDEX_SCHEMA:
            raise ValueError("unsupported dense index state")
        receipt = DenseIndexReceipt.from_dict(state.get("receipt", {}))
        raw_documents = state.get("documents", ())
        if not isinstance(raw_documents, list):
            raise ValueError("dense index document collection is invalid")
        documents = tuple(_DenseDocument.from_dict(row) for row in raw_documents)
        if receipt.index_checksum != self._documents_checksum(documents):
            raise ValueError("dense_index_checksum_mismatch")
        if documents:
            dimensions = {len(document.vector) for document in documents}
            if len(dimensions) != 1 or dimensions != {receipt.embedding_dimension}:
                raise ValueError("dense_index_dimension_mismatch")
        elif receipt.query_ready:
            raise ValueError("ready_dense_index_has_no_documents")
        return receipt, documents

    def inspect(
        self, identity: RepositoryIdentity | None = None
    ) -> DenseIndexReceipt:
        current = identity or compute_repository_identity(self.root)
        current_model_receipt = _backend_receipt(self.backend)
        if self.backend is None:
            return self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=("dense_backend_unavailable",),
                model_receipt=current_model_receipt,
            )
        if not self.index_path.is_file():
            return self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.ABSENT,
                reasons=("dense_index_absent",),
                model_receipt=current_model_receipt,
            )
        try:
            stored, _ = self._load_state()
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            reason = str(exc)
            if reason not in {
                "dense_index_checksum_mismatch",
                "dense_index_dimension_mismatch",
                "ready_dense_index_has_no_documents",
            }:
                reason = "dense_index_corrupt"
            return self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=(reason,),
                model_receipt=current_model_receipt,
            )
        stale: list[str] = []
        if Path(stored.repository).resolve() != self.root:
            stale.append("repository_identity_mismatch")
        if stored.commit_sha != current.commit_sha:
            stale.append("commit_sha_mismatch")
        if stored.branch != current.branch:
            stale.append("branch_mismatch")
        if stored.working_tree_state != current.working_tree_state:
            stale.append("working_tree_state_mismatch")
        if stored.source_revision != current.source_revision:
            stale.append("source_revision_mismatch")
        if stored.graph_input_identity != _graph_input_identity(current):
            stale.append("graph_input_identity_mismatch")
        if stored.model_identity != _model_identity(self.backend, current_model_receipt):
            stale.append("model_identity_mismatch")
        if stale:
            return replace(
                stored,
                status=DenseIndexStatus.STALE,
                query_ready=False,
                degraded_reasons=tuple(dict.fromkeys((*stored.degraded_reasons, *stale))),
            )
        return stored

    def _source_paths(self, identity: RepositoryIdentity) -> tuple[str, ...]:
        selected: list[str] = []
        for relative in sorted(identity.graph_input_hashes):
            candidate = self.root / Path(relative)
            try:
                if candidate.is_symlink():
                    prefix = f"SYMLINK\0{os.readlink(candidate)}".encode(
                        "utf-8", "surrogatepass"
                    )
                else:
                    with candidate.open("rb") as handle:
                        prefix = handle.read(65_536)
            except OSError:
                prefix = b""
            resolution = resolve_language(relative, prefix)
            # Dense file retrieval is a code-intelligence channel.  Metadata
            # and prose may affect graph identity, but a language whose
            # registry contract has no symbols must not masquerade as a
            # supported source document.
            if (
                resolution.status is LanguageResolutionStatus.RESOLVED
                and resolution.capability is not None
                and resolution.capability.structural_index
                and resolution.capability.symbol_support
            ):
                selected.append(relative)
        return tuple(selected)

    def _read_summary(self, relative: str) -> tuple[str, str]:
        candidate = self.root / Path(relative)
        if candidate.is_symlink():
            target = os.readlink(candidate)
            payload = f"symbolic link target: {target}".encode("utf-8", "surrogatepass")
        else:
            with candidate.open("rb") as handle:
                payload = handle.read(self.max_file_bytes + 1)
            if len(payload) > self.max_file_bytes:
                payload = payload[: self.max_file_bytes]
        return _semantic_summary(
            path=relative,
            payload=payload,
            max_chars=self.max_summary_chars,
        )

    def ensure(
        self, identity: RepositoryIdentity | None = None
    ) -> DenseIndexReceipt:
        current = identity or compute_repository_identity(self.root)
        inspected = self.inspect(current)
        if inspected.query_ready:
            return inspected
        started = _now()
        initial_model_receipt = _backend_receipt(self.backend)
        if self.backend is None:
            failed = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=("dense_backend_unavailable",),
                model_receipt=initial_model_receipt,
                started=started,
                completed=_now(),
            )
            self._write_state(failed, ())
            return failed
        initial_receipt_failure = _receipt_failure(initial_model_receipt)
        if initial_receipt_failure:
            failed = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=(initial_receipt_failure,),
                model_receipt=initial_model_receipt,
                started=started,
                completed=_now(),
            )
            self._write_state(failed, ())
            return failed

        source_paths = self._source_paths(current)
        if not source_paths:
            degraded = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.DEGRADED,
                reasons=("no_supported_source",),
                model_receipt=initial_model_receipt,
                started=started,
                completed=_now(),
            )
            self._write_state(degraded, ())
            return degraded

        summaries: list[tuple[str, str, str]] = []
        failed_paths: list[str] = []
        for relative in source_paths:
            try:
                summary, summary_sha256 = self._read_summary(relative)
            except OSError:
                failed_paths.append(relative)
                continue
            summaries.append((relative, summary, summary_sha256))
        if not summaries:
            failed = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.DEGRADED,
                reasons=("all_supported_source_unreadable",),
                model_receipt=initial_model_receipt,
                started=started,
                completed=_now(),
                files_discovered=len(source_paths),
                files_failed=len(failed_paths),
                failed_paths=failed_paths,
            )
            self._write_state(failed, ())
            return failed

        vectors: list[tuple[float, ...]] = []
        try:
            for offset in range(0, len(summaries), self.embedding_batch_size):
                texts = tuple(
                    summary
                    for _, summary, _ in summaries[offset : offset + self.embedding_batch_size]
                )
                batch = tuple(
                    tuple(float(item) for item in row)
                    for row in self.backend.embed_documents(texts)
                )
                if len(batch) != len(texts):
                    raise ValueError("dense backend returned an invalid document batch")
                vectors.extend(batch)
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1 or 0 in dimensions:
                raise ValueError("dense backend returned inconsistent dimensions")
            if not all(math.isfinite(item) for vector in vectors for item in vector):
                raise ValueError("dense backend returned non-finite values")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            model_receipt = _backend_receipt(self.backend)
            failed = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=(f"dense_embedding_failed:{type(exc).__name__}",),
                model_receipt=model_receipt,
                started=started,
                completed=_now(),
                files_discovered=len(source_paths),
                files_failed=len(source_paths),
                failed_paths=source_paths,
            )
            self._write_state(failed, ())
            return failed

        model_receipt = _backend_receipt(self.backend)
        final_receipt_failure = _receipt_failure(model_receipt)
        if final_receipt_failure:
            failed = self._empty_receipt(
                identity=current,
                status=DenseIndexStatus.FAILED,
                reasons=(final_receipt_failure,),
                model_receipt=model_receipt,
                started=started,
                completed=_now(),
                files_discovered=len(source_paths),
                files_failed=len(source_paths),
                failed_paths=source_paths,
            )
            self._write_state(failed, ())
            return failed

        documents = tuple(
            _DenseDocument(
                path=relative,
                content_sha256=current.graph_input_hashes[relative],
                summary=summary,
                summary_sha256=summary_sha256,
                vector=vector,
            )
            for (relative, summary, summary_sha256), vector in zip(
                summaries, vectors, strict=True
            )
        )
        checksum = self._documents_checksum(documents)
        limitations = (
            ("supported_source_unreadable",) if failed_paths else ()
        )
        status = (
            DenseIndexStatus.READY_WITH_DECLARED_LIMITATIONS
            if limitations
            else DenseIndexStatus.READY
        )
        receipt = self._empty_receipt(
            identity=current,
            status=status,
            reasons=limitations,
            model_receipt=model_receipt,
            started=started,
            completed=_now(),
            files_discovered=len(source_paths),
            files_indexed=len(documents),
            files_failed=len(failed_paths),
            files_skipped=0,
            failed_paths=failed_paths,
            dimension=len(documents[0].vector),
            checksum=checksum,
        )
        self._write_state(receipt, documents)
        return receipt

    def query(
        self,
        text: str,
        *,
        limit: int = 8,
        identity: RepositoryIdentity | None = None,
    ) -> DenseQueryResult:
        receipt = self.inspect(identity)
        maximum = max(0, int(limit))
        if not receipt.query_ready:
            return DenseQueryResult(
                query_ready=False,
                status=receipt.status,
                source_revision=receipt.source_revision,
                model_identity=receipt.model_identity,
                candidates=(),
                degraded_reasons=receipt.degraded_reasons,
            )
        query_text = str(text or "").strip()
        if not query_text or maximum == 0:
            return DenseQueryResult(
                query_ready=True,
                status=receipt.status,
                source_revision=receipt.source_revision,
                model_identity=receipt.model_identity,
                candidates=(),
                degraded_reasons=("query_empty",) if not query_text else (),
            )
        try:
            stored, documents = self._load_state()
            query_vector = tuple(float(item) for item in self.backend.embed_query(query_text))
            query_model_receipt = _backend_receipt(self.backend)
            query_receipt_failure = _receipt_failure(query_model_receipt)
            if query_receipt_failure:
                return DenseQueryResult(
                    query_ready=False,
                    status=DenseIndexStatus.FAILED,
                    source_revision=receipt.source_revision,
                    model_identity=receipt.model_identity,
                    candidates=(),
                    degraded_reasons=(query_receipt_failure,),
                )
            if _model_identity(self.backend, query_model_receipt) != stored.model_identity:
                return DenseQueryResult(
                    query_ready=False,
                    status=DenseIndexStatus.STALE,
                    source_revision=receipt.source_revision,
                    model_identity=receipt.model_identity,
                    candidates=(),
                    degraded_reasons=("model_identity_changed_during_query",),
                )
            if len(query_vector) != stored.embedding_dimension:
                raise ValueError("dense query dimension mismatch")
            if not all(math.isfinite(item) for item in query_vector):
                raise ValueError("dense query vector is not finite")
            query_norm = math.sqrt(sum(item * item for item in query_vector))
            scored: list[tuple[float, _DenseDocument]] = []
            for document in documents:
                document_norm = math.sqrt(sum(item * item for item in document.vector))
                denominator = query_norm * document_norm
                score = (
                    sum(
                        left * right
                        for left, right in zip(query_vector, document.vector, strict=True)
                    )
                    / denominator
                    if denominator
                    else 0.0
                )
                scored.append((max(0.0, float(score)), document))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return DenseQueryResult(
                query_ready=False,
                status=DenseIndexStatus.FAILED,
                source_revision=receipt.source_revision,
                model_identity=receipt.model_identity,
                candidates=(),
                degraded_reasons=(f"dense_query_failed:{type(exc).__name__}",),
            )
        ordered = sorted(scored, key=lambda row: (-row[0], row[1].path.lower()))
        candidates = tuple(
            DenseFileCandidate(
                path=document.path,
                rank=rank,
                score=score,
                source_revision=receipt.source_revision,
                content_sha256=document.content_sha256,
                summary_sha256=document.summary_sha256,
                model_identity=receipt.model_identity,
                evidence=(
                    "repository_source",
                    "deterministic_file_summary",
                    "dense_cosine",
                    str(getattr(self.backend, "identity", type(self.backend).__qualname__)),
                ),
            )
            for rank, (score, document) in enumerate(ordered[:maximum], 1)
        )
        return DenseQueryResult(
            query_ready=True,
            status=receipt.status,
            source_revision=receipt.source_revision,
            model_identity=receipt.model_identity,
            candidates=candidates,
        )


__all__ = [
    "DENSE_INDEX_SCHEMA",
    "DENSE_RECEIPT_SCHEMA",
    "DenseFileCandidate",
    "DenseIndexReceipt",
    "DenseIndexStatus",
    "DenseQueryResult",
    "PersistentDenseSemanticIndex",
]
