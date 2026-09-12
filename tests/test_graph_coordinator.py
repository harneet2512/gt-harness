from __future__ import annotations

import threading

from gt_engine.engine_state import EngineState
from gt_engine.graph_coordinator import FrozenBuildInput, GraphBuildArtifact, GraphBuildCoordinator


def request(revision, paths=("x.py",)):
    return FrozenBuildInput(revision, tuple(sorted(paths)), (("x.py", b"x = 1\n"),))


def test_schedule_is_nonblocking_and_coalesces_to_latest_request(tmp_path):
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r0")
    gate = threading.Event()
    started = []

    def build(item):
        started.append(item.source_revision)
        if item.source_revision == "r1":
            gate.wait(2)
        graph = tmp_path / f"{item.source_revision}.db"
        graph.write_text("db")
        return GraphBuildArtifact(True, str(graph), item.source_revision)

    coordinator = GraphBuildCoordinator(state, build)
    try:
        # A frozen request's revision is the state's revision at freeze time:
        # the owner thread bumps source_revision on each edit, then freezes.
        state.source_revision = "r1"
        assert coordinator.schedule(request("r1")) == "scheduled"
        state.source_revision = "r2"
        assert coordinator.schedule(request("r2", ("a.py",))) == "coalesced"
        state.source_revision = "r3"
        assert coordinator.schedule(request("r3", ("b.py",))) == "coalesced"
        assert coordinator.pending_request.dirty_paths == ("a.py", "b.py", "x.py")
        gate.set()
        coordinator.wait_idle(timeout=3)
        assert started == ["r1", "r3"]
        assert coordinator.poll() == 2
        assert state.source_revision == "r3"  # builds cannot rewrite source authority
        assert state.graph_current
        assert state.graph_path == str(tmp_path / "r3.db")
    finally:
        coordinator.close()


def test_coalescing_carries_the_parent_graph_forward():
    """A merged request must keep the graph an amend would start from.

    The merge built its replacement request by POSITION, so the two parent
    fields silently defaulted to empty the moment they were added. Every
    coalesced build then had no parent, and a build with no parent is not a
    refusal -- it is simply not an amend, so it fell back to a full rebuild
    and reported no reason at all. Two of the first eleven builds of the
    2026-09-08 run went that way before the blank parent revision gave it
    away.
    """
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r0")
    gate = threading.Event()

    def build(item):
        if item.source_revision == "r1":
            gate.wait(2)
        return GraphBuildArtifact(True, f"{item.source_revision}.db", item.source_revision)

    def parented(revision, paths):
        return FrozenBuildInput(
            revision,
            tuple(sorted(paths)),
            (("x.py", b"x = 1"),),
            parent_graph_path="/graphs/parent.db",
            parent_graph_revision="g-parent",
        )

    coordinator = GraphBuildCoordinator(state, build)
    try:
        state.source_revision = "r1"
        assert coordinator.schedule(parented("r1", ("x.py",))) == "scheduled"
        state.source_revision = "r2"
        assert coordinator.schedule(parented("r2", ("a.py",))) == "coalesced"
        pending = coordinator.pending_request
        assert pending.dirty_paths == ("a.py", "x.py")
        assert pending.parent_graph_path == "/graphs/parent.db"
        assert pending.parent_graph_revision == "g-parent"
    finally:
        gate.set()
        coordinator.close()

def test_only_owner_poll_publishes_current_matching_result(tmp_path):
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    graph = tmp_path / "new.db"
    graph.write_text("db")
    coordinator = GraphBuildCoordinator(
        state, lambda item: GraphBuildArtifact(True, str(graph), "g1")
    )
    try:
        coordinator.schedule(request("r1"))
        coordinator.wait_idle(timeout=3)
        assert not state.graph_current or state.graph_path == "base.db"
        assert coordinator.poll() == 1
        assert state.graph_current
        assert state.graph_path == str(graph)
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


def test_identical_inputs_run_once_before_and_after_publication(tmp_path):
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    gate = threading.Event()
    started = []

    def build(item):
        started.append(item)
        assert gate.wait(3)
        graph = tmp_path / "new.db"
        graph.write_text("db")
        return GraphBuildArtifact(True, str(graph), "g1")

    coordinator = GraphBuildCoordinator(state, build)
    try:
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.schedule(request("r1")) == "already_running"
        gate.set()
        assert coordinator.wait_idle(timeout=3)
        assert coordinator.schedule(request("r1")) == "already_completed"
        assert coordinator.poll() == 1
        assert coordinator.schedule(request("r1")) == "already_completed"
        assert len(started) == 1
    finally:
        gate.set()
        coordinator.close(wait=True)


def test_failed_identical_input_can_retry():
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    coordinator = GraphBuildCoordinator(
        state, lambda _: GraphBuildArtifact(False, "", "", "failed")
    )
    try:
        coordinator.schedule(request("r1"))
        assert coordinator.wait_idle(timeout=3)
        coordinator.poll()
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.wait_idle(timeout=3)
    finally:
        coordinator.close(wait=True)


def test_pending_frozen_before_latest_edit_is_dropped(tmp_path):
    """A build that can never be adopted must not start.

    The pending request was frozen while r2 was current; an edit moved the
    state to r3 before the running build finished. publish_graph would refuse
    the pending build's result, so the worker must not burn indexing time on
    it -- the dirty overlay survives in the engine and the next schedule
    re-freezes it. Run 34701523365 paid ~4 minutes each for nineteen such
    stillborn builds.
    """
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    gate = threading.Event()
    started = []

    def build(item):
        started.append(item.source_revision)
        gate.wait(2)
        graph = tmp_path / f"{item.source_revision}.db"
        graph.write_text("db")
        return GraphBuildArtifact(True, str(graph), item.source_revision)

    coordinator = GraphBuildCoordinator(state, build)
    try:
        assert coordinator.schedule(request("r1")) == "scheduled"
        state.source_revision = "r2"
        assert coordinator.schedule(request("r2", ("a.py",))) == "coalesced"
        # An edit lands; refresh has not run, so no r3 request exists yet.
        state.source_revision = "r3"
        gate.set()
        assert coordinator.wait_idle(timeout=3)
        assert started == ["r1"]
        assert coordinator.last_error == "pending_superseded_before_start"
        # The dropped work is recovered by the next schedule, not lost.
        assert coordinator.schedule(request("r3", ("a.py", "b.py"))) == "scheduled"
        assert coordinator.wait_idle(timeout=3)
        assert started == ["r1", "r3"]
    finally:
        gate.set()
        coordinator.close(wait=True)


def test_already_completed_replay_of_reclaimed_artifact_rebuilds(tmp_path):
    """A cached success whose file reclamation removed is not adoptable.

    already_completed replays the recorded (request, result) through poll().
    If retention deleted the artifact in between, publish_graph would install
    a dead path and graph_current would lie; the recorded success must be
    voided so the identical request rebuilds instead.
    """
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    started = []

    def build(item):
        started.append(item)
        graph = tmp_path / "new.db"
        graph.write_text("db")
        return GraphBuildArtifact(True, str(graph), "g1")

    coordinator = GraphBuildCoordinator(state, build)
    try:
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.wait_idle(timeout=3)
        assert coordinator.poll() == 1
        assert state.graph_path == str(tmp_path / "new.db")
        (tmp_path / "new.db").unlink()
        assert coordinator.schedule(request("r1")) == "already_completed"
        coordinator.poll()
        assert coordinator.last_error == "graph_artifact_missing"
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.wait_idle(timeout=3)
        assert len(started) == 2
    finally:
        coordinator.close(wait=True)


def test_reclaimer_sees_adoption_outcome_and_named_set(tmp_path):
    """poll() reports each adoption verdict to the injected reclaimer.

    The protected set must carry everything the authority names: the adopted
    live graph plus the frozen parents of pending and running builds. A
    refused build's output is garbage no reference can reach.
    """
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    calls = []

    def build(item):
        graph = tmp_path / f"{item.source_revision}.db"
        graph.write_text("db")
        return GraphBuildArtifact(True, str(graph), item.source_revision)

    coordinator = GraphBuildCoordinator(
        state, build,
        reclaimer=lambda req, res, adopted, protected: calls.append(
            (req.source_revision, adopted, protected)),
    )
    try:
        assert coordinator.schedule(request("r1")) == "scheduled"
        assert coordinator.wait_idle(timeout=3)
        coordinator.poll()
        assert calls[-1] == ("r1", True, frozenset({str(tmp_path / "r1.db")}))
        # The next edit makes the in-flight r1 result unadoptable.
        state.source_revision = "r2"
        coordinator.schedule(request("r1"))
        coordinator.wait_idle(timeout=3)
        coordinator.poll()
        assert calls[-1][0] == "r1" and calls[-1][1] is False
        assert str(tmp_path / "r1.db") in calls[-1][2]
    finally:
        coordinator.close(wait=True)
