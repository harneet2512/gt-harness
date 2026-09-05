"""Single source/graph/overlay authority for native GT consumers."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class QueryCompleteness(StrEnum):
    CURRENT_COMPLETE = "current_complete"
    CURRENT_PARTIAL = "current_partial"
    HISTORICAL = "historical"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OverlayEntry:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    content: bytes | None
    source_revision: str


@dataclass(frozen=True, slots=True)
class GraphQuerySnapshot:
    source_revision: str
    graph_revision: str
    graph_path: str
    completeness: QueryCompleteness
    overlay: Mapping[str, OverlayEntry]
    masked_paths: tuple[str, ...]
    omissions: tuple[str, ...]

    @property
    def graph_current(self) -> bool:
        return self.completeness is QueryCompleteness.CURRENT_COMPLETE

    @property
    def absence_claims_allowed(self) -> bool:
        return self.graph_current and not self.omissions


class EngineState:
    """Own current source identity, immutable base identity and edit overlay."""

    def __init__(self, *, graph_path: str = "", graph_revision: str = "",
                 source_revision: str = "") -> None:
        self.source_revision = source_revision
        self.graph_source_revision = source_revision if graph_path else ""
        self.graph_revision = graph_revision
        self.graph_path = graph_path
        self._graph_usable = bool(graph_path and graph_revision)
        self._overlay: dict[str, OverlayEntry] = {}
        self._omissions: set[str] = set()

    @property
    def graph_current(self) -> bool:
        return bool(self._graph_usable and self.graph_path and self.graph_revision
                    and self.graph_source_revision == self.source_revision
                    and not self._overlay and not self._omissions)

    def bind_initial_source(self, revision: str) -> None:
        self.source_revision = revision
        if self.graph_path and not self.graph_source_revision and not self._overlay:
            self.graph_source_revision = revision

    def mark_paths_dirty(self, paths: tuple[str, ...], *, revision: str) -> None:
        self.source_revision = revision
        for path in paths:
            if path and path not in self._overlay:
                self._overlay[path] = OverlayEntry(
                    path, "unknown", None, None, None, revision
                )
        self._omissions.add("transaction_bytes_unavailable")

    def apply_transaction(self, transaction: Any) -> None:
        revision = str(transaction.post_revision)
        self.source_revision = revision
        for item in transaction.changes:
            path = str(item.path)
            self._overlay[path] = OverlayEntry(
                path=path, operation=str(item.operation),
                before_sha256=item.before_sha256, after_sha256=item.after_sha256,
                content=item.after, source_revision=revision,
            )
        if not bool(transaction.complete):
            self._omissions.update(str(value) for value in transaction.omissions)

    def publish_graph(self, *, graph_path: str, graph_revision: str,
                      source_revision: str) -> bool:
        if (not graph_path or not graph_revision
                or source_revision != self.source_revision):
            return False
        self.graph_path = graph_path
        self.graph_revision = graph_revision
        self.graph_source_revision = source_revision
        self._graph_usable = True
        self._overlay.clear()
        self._omissions.clear()
        return True

    def mark_graph_failed(self) -> None:
        self._graph_usable = False

    def query_snapshot(self) -> GraphQuerySnapshot:
        if self.graph_current:
            completeness = QueryCompleteness.CURRENT_COMPLETE
            path = self.graph_path
        elif self.graph_path or self._overlay:
            completeness = QueryCompleteness.CURRENT_PARTIAL
            path = ""
        else:
            completeness = QueryCompleteness.UNAVAILABLE
            path = ""
        overlay = MappingProxyType(dict(sorted(self._overlay.items())))
        return GraphQuerySnapshot(
            source_revision=self.source_revision,
            graph_revision=self.graph_revision,
            graph_path=path,
            completeness=completeness,
            overlay=overlay,
            masked_paths=tuple(overlay),
            omissions=tuple(sorted(self._omissions)),
        )


__all__ = ["EngineState", "GraphQuerySnapshot", "OverlayEntry", "QueryCompleteness"]
