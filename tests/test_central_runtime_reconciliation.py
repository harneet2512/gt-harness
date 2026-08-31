from __future__ import annotations

import pytest

from gt_engine.persistent_execution_state import ExecutionStateSnapshot
from gt_engine.repository_intelligence import (
    CentralRuntimeIdentityError,
    bind_central_runtime,
)


def test_central_runtime_rejects_repository_revision_mismatch():
    stale = ExecutionStateSnapshot(
        repository_revision="repo-a",
        workspace_revision="ws-1",
        graph_revision="graph-1",
        graph_path="/tmp/graph.db",
    )
    with pytest.raises(CentralRuntimeIdentityError, match="repository_revision mismatch"):
        bind_central_runtime(
            repository_revision="repo-b",
            workspace_revision="ws-1",
            graph_revision="graph-1",
            graph_path="/tmp/graph.db",
            execution_state=stale,
        )


def test_central_runtime_unifies_identity_when_revisions_match():
    binding = bind_central_runtime(
        repository_revision="repo-1",
        workspace_revision="ws-1",
        graph_revision="graph-1",
        graph_path="/tmp/graph.db",
    )
    assert binding.repository_revision == "repo-1"
    assert binding.workspace_revision == "ws-1"
    assert binding.graph_revision == "graph-1"
    assert binding.graph_path == "/tmp/graph.db"
    assert binding.lease.freshness.value == "CURRENT"
    assert binding.execution_state.as_dict()["repository_revision"] == "repo-1"
