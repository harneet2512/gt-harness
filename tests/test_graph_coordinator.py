from __future__ import annotations

import threading

from gt_engine.engine_state import EngineState
from gt_engine.graph_coordinator import FrozenBuildInput, GraphBuildArtifact, GraphBuildCoordinator


def request(revision, paths=("x.py",)):
    return FrozenBuildInput(revision, tuple(sorted(paths)), (("x.py", b"x = 1\n"),))


def test_schedule_is_nonblocking_and_coalesces_to_latest_request():
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r0")
    gate = threading.Event()
    started = []
    def build(item):
        started.append(item.source_revision)
        if item.source_revision == "r1":
            gate.wait(2)
        return GraphBuildArtifact(True, f"{item.source_revision}.db", item.source_revision)
    coordinator = GraphBuildCoordinator(state, build)
    try:
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.schedule(request("r2", ("a.py",))) == "coalesced"
        assert coordinator.schedule(request("r3", ("b.py",))) == "coalesced"
        assert coordinator.pending_request.dirty_paths == ("a.py", "b.py", "x.py")
        gate.set()
        coordinator.wait_idle(timeout=3)
        assert started == ["r1", "r3"]
        assert coordinator.poll() == 2
        assert state.source_revision == "r0"  # builds cannot rewrite source authority
        assert state.graph_current
        assert state.graph_path == "base.db"
    finally:
        coordinator.close()


def test_only_owner_poll_publishes_current_matching_result():
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    coordinator = GraphBuildCoordinator(
        state, lambda item: GraphBuildArtifact(True, "new.db", "g1")
    )
    try:
        coordinator.schedule(request("r1"))
        coordinator.wait_idle(timeout=3)
        assert not state.graph_current or state.graph_path == "base.db"
        assert coordinator.poll() == 1
        assert state.graph_current
        assert state.graph_path == "new.db"
    finally:
        coordinator.close()


def test_build_failure_and_exception_never_publish():
    for builder in (
        lambda item: GraphBuildArtifact(False, "bad.db", "bad", "failed"),
        lambda item: (_ for _ in ()).throw(OSError("boom")),
    ):
        state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
        coordinator = GraphBuildCoordinator(state, builder)
        try:
            coordinator.schedule(request("r1"))
            coordinator.wait_idle(timeout=3)
            coordinator.poll()
            assert state.graph_path == "base.db"
            assert coordinator.last_error
        finally:
            coordinator.close()
