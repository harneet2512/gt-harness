"""Persistent execution snapshots for the central runtime substrate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionStateSnapshot:
    repository_revision: str
    workspace_revision: str
    graph_revision: str
    graph_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "repository_revision": self.repository_revision,
            "workspace_revision": self.workspace_revision,
            "graph_revision": self.graph_revision,
            "graph_path": self.graph_path,
        }
