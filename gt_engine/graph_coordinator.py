"""Shared graph build types.

The build coordinator is gone: graph builds are synchronous amends at the
transaction boundary (or inline at a serving boundary), adopted through
``EngineState.publish_graph`` on the owner thread. What remains here are
the value types the still-asynchronous LSP promotion path carries -- a
promotion is scheduled against a frozen input and a base artifact, and its
terminal receipt is drained at provider-wait and serving boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .repository_identity import RepositoryHistory


@dataclass(frozen=True, slots=True)
class FrozenBuildInput:
    source_revision: str
    dirty_paths: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]
    history: RepositoryHistory = RepositoryHistory()
    # The graph a promotion starts from, frozen onto the request because
    # the promotion runs off the owner thread and EngineState is
    # owner-thread only.
    parent_graph_path: str = ""
    parent_graph_revision: str = ""


@dataclass(frozen=True, slots=True)
class GraphBuildArtifact:
    success: bool
    graph_path: str
    graph_revision: str
    error: str = ""


class EnrichmentTaskHandle(Protocol):
    @property
    def done(self) -> bool: ...

    def cancel(self) -> bool: ...

    def terminal_receipt(self, *, timeout: float | None = None) -> Mapping[str, Any]: ...


__all__ = [
    "EnrichmentTaskHandle",
    "FrozenBuildInput",
    "GraphBuildArtifact",
]
