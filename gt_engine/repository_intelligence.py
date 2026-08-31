"""Central runtime reconciliation for repository and graph identity."""

from __future__ import annotations

from dataclasses import dataclass

from gt_engine.graph_lease import GraphLease
from gt_engine.persistent_execution_state import ExecutionStateSnapshot


class CentralRuntimeIdentityError(ValueError):
    """Raised when graph lease and execution state disagree on revision identity."""


@dataclass(frozen=True, slots=True)
class CentralRuntimeBinding:
    lease: GraphLease
    execution_state: ExecutionStateSnapshot

    @property
    def repository_revision(self) -> str:
        return self.lease.graph_repository_revision

    @property
    def workspace_revision(self) -> str:
        return self.lease.workspace_revision

    @property
    def graph_revision(self) -> str:
        return self.lease.graph_revision

    @property
    def graph_path(self) -> str:
        return self.lease.graph_path


def bind_central_runtime(
    *,
    repository_revision: str,
    workspace_revision: str,
    graph_revision: str,
    graph_path: str,
    execution_state: ExecutionStateSnapshot | None = None,
) -> CentralRuntimeBinding:
    """Bind graph lease and execution state to one repository identity.

    Every named runtime service must resolve the same revision tuple. Mismatched
    persisted execution state is rejected fail-closed instead of silently reused.
    """

    lease = GraphLease.current(
        graph_repository_revision=repository_revision,
        workspace_revision=workspace_revision,
        graph_revision=graph_revision,
        graph_path=graph_path,
    )
    state = execution_state or ExecutionStateSnapshot(
        repository_revision=repository_revision,
        workspace_revision=workspace_revision,
        graph_revision=graph_revision,
        graph_path=graph_path,
    )
    if state.repository_revision != lease.graph_repository_revision:
        raise CentralRuntimeIdentityError("repository_revision mismatch")
    if state.workspace_revision != lease.workspace_revision:
        raise CentralRuntimeIdentityError("workspace_revision mismatch")
    if state.graph_revision != lease.graph_revision:
        raise CentralRuntimeIdentityError("graph_revision mismatch")
    if state.graph_path != lease.graph_path:
        raise CentralRuntimeIdentityError("graph_path mismatch")
    return CentralRuntimeBinding(lease=lease, execution_state=state)
