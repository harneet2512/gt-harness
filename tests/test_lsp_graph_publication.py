from __future__ import annotations

from gt_engine.engine_state import EngineState
from gt_engine.graph_coordinator import (
    FrozenBuildInput,
    GraphBuildArtifact,
    GraphBuildCoordinator,
)


def request(revision: str, content: bytes = b"x = 1\n") -> FrozenBuildInput:
    return FrozenBuildInput(revision, ("x.py",), (("x.py", content),))


class EnrichmentHandle:
    def __init__(self, receipt: dict, *, done: bool = True) -> None:
        self.receipt = receipt
        self.done = done
        self.cancelled = False

    def cancel(self) -> bool:
        self.cancelled = True
        return True

    def terminal_receipt(self, *, timeout=None) -> dict:
        assert self.done and timeout == 0
        return dict(self.receipt)


def successful_receipt(source: str = "r1", graph: str = "g1") -> dict:
    return {
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "source_revision": source,
        "input_graph_revision": graph,
        "candidate_path": "candidate.db",
        "output_graph_sha256": "a" * 64,
    }


def test_core_is_published_before_current_enrichment_candidate() -> None:
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    handle = EnrichmentHandle(successful_receipt())
    factory_calls = []

    def factory(item, core):
        factory_calls.append((item.source_revision, core.graph_revision, state.graph_path))
        return handle

    def certify(item, core, receipt):
        assert item.source_revision == receipt["source_revision"]
        assert core.graph_revision == receipt["input_graph_revision"]
        return GraphBuildArtifact(True, receipt["candidate_path"], "g1+lsp")

    coordinator = GraphBuildCoordinator(
        state, lambda _: GraphBuildArtifact(True, "core.db", "g1"),
        enrichment_factory=factory, candidate_certifier=certify,
    )
    try:
        coordinator.schedule(request("r1"))
        assert coordinator.wait_idle(timeout=3)
        assert coordinator.poll() == 1
        assert state.graph_path == "core.db"
        assert factory_calls == [("r1", "g1", "core.db")]

        assert coordinator.poll() == 1
        assert state.graph_path == "candidate.db"
        assert state.graph_revision == "g1+lsp"
    finally:
        coordinator.close(wait=True)


def test_edit_cancels_obsolete_enrichment_and_it_never_publishes() -> None:
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    handle = EnrichmentHandle(successful_receipt(), done=False)
    observed = []
    coordinator = GraphBuildCoordinator(
        state, lambda item: GraphBuildArtifact(True, f"{item.source_revision}.db", item.source_revision),
        enrichment_factory=lambda _item, _core: handle,
        candidate_certifier=lambda _item, _core, receipt: GraphBuildArtifact(
            True, receipt["candidate_path"], "g1+lsp"
        ),
        enrichment_observer=lambda _item, _core, receipt, disposition: observed.append(
            (receipt["status"], disposition)
        ),
    )
    try:
        coordinator.schedule(request("r1"))
        assert coordinator.wait_idle(timeout=3)
        coordinator.poll()
        assert state.graph_path == "r1.db"

        state.mark_paths_dirty(("x.py",), revision="r2")
        coordinator.schedule(request("r2", b"x = 2\n"))
        assert handle.cancelled
        handle.receipt = {
            **handle.receipt,
            "status": "cancelled",
            "publishable": False,
            "reason": "cancel_requested",
        }
        handle.done = True
        coordinator.poll()
        assert state.graph_path == "r1.db"
        assert state.source_revision == "r2"
        assert observed == [("cancelled", "obsolete")]
    finally:
        coordinator.close(wait=True)


def test_new_core_publication_cancels_previous_same_source_enrichment() -> None:
    state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
    handles = [EnrichmentHandle(successful_receipt(graph="g1"), done=False),
               EnrichmentHandle(successful_receipt(graph="g2"), done=False)]
    coordinator = GraphBuildCoordinator(
        state,
        lambda item: GraphBuildArtifact(
            True, "one.db" if item.files[0][1] == b"one" else "two.db",
            "g1" if item.files[0][1] == b"one" else "g2",
        ),
        enrichment_factory=lambda _item, _core: handles.pop(0),
        candidate_certifier=lambda _item, _core, _receipt: GraphBuildArtifact(
            True, "candidate.db", "candidate"
        ),
    )
    try:
        coordinator.schedule(request("r1", b"one"))
        assert coordinator.wait_idle(timeout=3)
        coordinator.poll()
        first = coordinator.enrichment_handle
        coordinator.schedule(request("r1", b"two"))
        assert coordinator.wait_idle(timeout=3)
        coordinator.poll()
        assert first.cancelled
        assert state.graph_path == "two.db"
    finally:
        coordinator.close(wait=True)


def test_failed_or_mismatched_enrichment_receipt_never_publishes() -> None:
    for receipt in (
        {**successful_receipt(), "status": "failed", "publishable": False},
        {**successful_receipt(), "input_graph_revision": "other"},
    ):
        state = EngineState(graph_path="base.db", graph_revision="g0", source_revision="r1")
        observed = []
        coordinator = GraphBuildCoordinator(
            state, lambda _: GraphBuildArtifact(True, "core.db", "g1"),
            enrichment_factory=lambda _item, _core, value=receipt: EnrichmentHandle(value),
            candidate_certifier=lambda _item, _core, value: GraphBuildArtifact(
                True, value["candidate_path"], "g1+lsp"
            ),
            enrichment_observer=(
                lambda _item, _core, value, disposition, sink=observed: sink.append(
                    (value["status"], disposition)
                )
            ),
        )
        try:
            coordinator.schedule(request("r1"))
            assert coordinator.wait_idle(timeout=3)
            coordinator.poll()
            coordinator.poll()
            assert state.graph_path == "core.db"
            assert coordinator.last_error.startswith("enrichment_")
            assert len(observed) == 1
        finally:
            coordinator.close(wait=True)


def test_initial_current_graph_can_be_considered_once_without_rebuild() -> None:
    state = EngineState(graph_path="initial.db", graph_revision="g0", source_revision="r0")
    handle = EnrichmentHandle(successful_receipt("r0", "g0"), done=False)
    calls = []
    coordinator = GraphBuildCoordinator(
        state, lambda _: GraphBuildArtifact(False, "", ""),
        enrichment_factory=lambda item, base: calls.append(
            (item.source_revision, base.graph_revision)
        ) or handle,
        candidate_certifier=lambda _item, _base, _receipt: GraphBuildArtifact(
            True, "initial-lsp.db", "g0+lsp"
        ),
    )
    try:
        initial = GraphBuildArtifact(True, "initial.db", "g0")
        assert coordinator.consider_enrichment(request("r0"), initial) == "scheduled"
        assert coordinator.consider_enrichment(request("r0"), initial) == "already_active"
        assert calls == [("r0", "g0")]
    finally:
        coordinator.close(wait=False)


def test_factory_exception_is_terminal_observed_data_and_core_stays_usable() -> None:
    state = EngineState(graph_path="initial.db", graph_revision="g0", source_revision="r0")
    observed = []

    def unavailable(_item, _base):
        raise ImportError("installed scheduler unavailable")

    coordinator = GraphBuildCoordinator(
        state, lambda _: GraphBuildArtifact(False, "", ""),
        enrichment_factory=unavailable,
        candidate_certifier=lambda _item, _base, _receipt: GraphBuildArtifact(
            False, "", "", "must_not_run"
        ),
        enrichment_observer=lambda _item, _base, receipt, disposition: observed.append(
            (receipt["status"], receipt["reason"], disposition)
        ),
    )
    try:
        initial = GraphBuildArtifact(True, "initial.db", "g0")
        assert coordinator.consider_enrichment(request("r0"), initial) == "failed"
        assert coordinator.consider_enrichment(request("r0"), initial) == "already_considered"
        assert observed == [
            ("failed", "factory_exception:ImportError", "factory_exception")
        ]
        assert state.graph_current and state.graph_path == "initial.db"
    finally:
        coordinator.close(wait=True)
