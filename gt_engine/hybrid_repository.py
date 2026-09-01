"""Source-checkout adapter for the revision-bound hybrid retriever."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from gt_engine.hybrid_retrieval import (
    HybridRetrievalResult,
    HybridRetriever,
    RepositoryDocument,
    RetrievalIntent,
    StructuralLink,
)
from gt_engine.repository_intelligence import CentralRuntimeBinding, CentralRuntimeIdentityError


@dataclass(frozen=True, slots=True)
class RepositoryBuildLimits:
    max_documents: int = 50_000
    max_chunk_chars: int = 12_000
    max_total_chars: int = 50_000_000

    def __post_init__(self) -> None:
        if self.max_documents < 1 or self.max_chunk_chars < 1 or self.max_total_chars < 1:
            raise ValueError("repository build limits must be positive")


@dataclass(frozen=True, slots=True)
class RepositoryBuildReceipt:
    repository_revision: str
    graph_revision: str
    document_count: int
    source_digest: str
    complete: bool
    error: str = ""


class HybridRepository:
    """Bound source documents and graph links to one central identity."""

    def __init__(self, root: str | Path, *, repository_revision: str,
                 graph_revision: str, runtime_binding: CentralRuntimeBinding,
                 limits: RepositoryBuildLimits | None = None) -> None:
        if runtime_binding.repository_revision != repository_revision:
            raise CentralRuntimeIdentityError("hybrid repository repository_revision mismatch")
        if runtime_binding.graph_revision != graph_revision:
            raise CentralRuntimeIdentityError("hybrid repository graph_revision mismatch")
        self.root = Path(root).resolve()
        self.repository_revision = repository_revision
        self.graph_revision = graph_revision
        self.runtime_binding = runtime_binding
        self.limits = limits or RepositoryBuildLimits()
        self.documents: tuple[RepositoryDocument, ...] = ()
        self.structural_links: tuple[StructuralLink, ...] = ()
        self.receipt = RepositoryBuildReceipt(repository_revision, graph_revision, 0, "", False)

    def build(self) -> RepositoryBuildReceipt:
        if not self.root.is_dir():
            self.receipt = RepositoryBuildReceipt(self.repository_revision, self.graph_revision, 0, "", False, "repository_missing")
            return self.receipt
        ignored = {".git", ".venv", "venv", "node_modules", "vendor", "build", "dist", "__pycache__"}
        docs: list[RepositoryDocument] = []
        total = 0
        digests: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or any(part in ignored for part in path.relative_to(self.root).parts):
                continue
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(self.root).as_posix()
            digest = hashlib.sha256(raw).hexdigest()
            digests.append(f"{relative}:{digest}")
            if total >= self.limits.max_total_chars or len(docs) >= self.limits.max_documents:
                break
            chunk = text[: self.limits.max_chunk_chars]
            docs.append(RepositoryDocument(relative, 1, max(1, chunk.count("\n") + 1), text=chunk,
                                            provenance=("checkout", f"sha256:{digest}")))
            total += len(chunk)
        self.documents = tuple(docs)
        source_digest = hashlib.sha256("\n".join(digests).encode()).hexdigest()
        self.receipt = RepositoryBuildReceipt(self.repository_revision, self.graph_revision,
                                              len(docs), source_digest, True)
        return self.receipt

    def retrieve(self, query: str, *, intent: RetrievalIntent = RetrievalIntent.INSPECT,
                 limit: int = 20) -> HybridRetrievalResult:
        return HybridRetriever(self.documents, self.structural_links,
                               runtime_binding=self.runtime_binding).retrieve(
                                   query, repository_revision=self.repository_revision,
                                   graph_revision=self.graph_revision, intent=intent, limit=limit)
