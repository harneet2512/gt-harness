from __future__ import annotations

import subprocess
import sys

import pytest

from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import HybridRetriever, RetrievalIntent
from gt_engine.persistent_execution_state import ExecutionStateSnapshot
from gt_engine.repository_intelligence import (
    CentralRuntimeIdentityError,
    bind_central_runtime,
)

_IDENTITY = {
    "repository_revision": "repo-1",
    "workspace_revision": "ws-1",
    "graph_revision": "graph-1",
    "graph_path": "/tmp/graph.db",
}


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
    binding = bind_central_runtime(**_IDENTITY)
    assert binding.repository_revision == "repo-1"
    assert binding.workspace_revision == "ws-1"
    assert binding.graph_revision == "graph-1"
    assert binding.graph_path == "/tmp/graph.db"
    assert binding.lease.freshness.value == "CURRENT"
    assert binding.execution_state.as_dict()["repository_revision"] == "repo-1"


def test_execution_state_persists_reopens_and_rebinds(tmp_path):
    state_path = tmp_path / "execution_state.json"
    binding = bind_central_runtime(**_IDENTITY)
    binding.execution_state.persist(state_path)

    reopened = ExecutionStateSnapshot.reopen(state_path, **_IDENTITY)
    rebound = bind_central_runtime(**_IDENTITY, execution_state=reopened)

    assert rebound.repository_revision == binding.repository_revision
    assert rebound.workspace_revision == binding.workspace_revision
    assert rebound.graph_revision == binding.graph_revision
    assert rebound.graph_path == binding.graph_path


def test_execution_state_reopen_rejects_stale_graph_revision(tmp_path):
    state_path = tmp_path / "execution_state.json"
    bind_central_runtime(**_IDENTITY).execution_state.persist(state_path)

    with pytest.raises(ValueError, match="graph_revision mismatch"):
        ExecutionStateSnapshot.reopen(
            state_path,
            repository_revision="repo-1",
            workspace_revision="ws-1",
            graph_revision="graph-stale",
            graph_path="/tmp/graph.db",
        )


def test_execution_state_rebind_rejects_mismatch_after_disk_reload(tmp_path):
    state_path = tmp_path / "execution_state.json"
    bind_central_runtime(**_IDENTITY).execution_state.persist(state_path)
    stale = ExecutionStateSnapshot.load(state_path)

    with pytest.raises(CentralRuntimeIdentityError, match="graph_revision mismatch"):
        bind_central_runtime(
            repository_revision="repo-1",
            workspace_revision="ws-1",
            graph_revision="graph-2",
            graph_path="/tmp/graph.db",
            execution_state=stale,
        )


def test_execution_state_survives_fresh_interpreter_restart(tmp_path):
    state_path = tmp_path / "execution_state.json"
    bind_central_runtime(**_IDENTITY).execution_state.persist(state_path)

    script = f"""
import sys
from gt_engine.persistent_execution_state import ExecutionStateSnapshot
from gt_engine.repository_intelligence import bind_central_runtime

reopened = ExecutionStateSnapshot.reopen(
    r"{state_path}",
    repository_revision="repo-1",
    workspace_revision="ws-1",
    graph_revision="graph-1",
    graph_path="/tmp/graph.db",
)
binding = bind_central_runtime(
    repository_revision="repo-1",
    workspace_revision="ws-1",
    graph_revision="graph-1",
    graph_path="/tmp/graph.db",
    execution_state=reopened,
)
assert binding.repository_revision == "repo-1"
"""
    completed = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_hybrid_repository_rejects_runtime_binding_mismatch(tmp_path):
    binding = bind_central_runtime(**_IDENTITY)
    with pytest.raises(CentralRuntimeIdentityError, match="repository_revision mismatch"):
        HybridRepository(
            tmp_path,
            repository_revision="repo-stale",
            graph_revision="graph-1",
            runtime_binding=binding,
        )


def test_hybrid_retrieval_rejects_revision_mismatch(tmp_path):
    binding = bind_central_runtime(**_IDENTITY)
    repo = HybridRepository(
        tmp_path,
        repository_revision="repo-1",
        graph_revision="graph-1",
        runtime_binding=binding,
    )
    repo.build()
    with pytest.raises(CentralRuntimeIdentityError, match="graph_revision mismatch"):
        HybridRetriever(runtime_binding=binding).retrieve(
            "sample",
            repository_revision="repo-1",
            graph_revision="graph-stale",
            intent=RetrievalIntent.INSPECT,
        )


def test_hybrid_repository_retrieve_honors_binding(tmp_path):
    sample = tmp_path / "module.py"
    sample.write_text("def sample_function():\n    return 1\n", encoding="utf-8")
    binding = bind_central_runtime(**_IDENTITY)
    repo = HybridRepository(
        tmp_path,
        repository_revision="repo-1",
        graph_revision="graph-1",
        runtime_binding=binding,
    )
    repo.build()
    result = repo.retrieve("sample_function")
    assert result.repository_revision == "repo-1"
    assert result.graph_revision == "graph-1"
