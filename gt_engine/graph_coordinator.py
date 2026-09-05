"""Nonblocking, latest-request graph build coordination."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from .engine_state import EngineState


@dataclass(frozen=True, slots=True)
class FrozenBuildInput:
    source_revision: str
    dirty_paths: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class GraphBuildArtifact:
    success: bool
    graph_path: str
    graph_revision: str
    error: str = ""


class GraphBuildCoordinator:
    """Run one build, retain one coalesced latest request, publish on owner poll."""

    def __init__(self, state: EngineState,
                 builder: Callable[[FrozenBuildInput], GraphBuildArtifact]) -> None:
        self._state = state
        self._builder = builder
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gt-graph")
        self._lock = threading.Condition()
        self._running: FrozenBuildInput | None = None
        self._pending: FrozenBuildInput | None = None
        self._completed: list[tuple[FrozenBuildInput, GraphBuildArtifact]] = []
        self._successful: tuple[FrozenBuildInput, GraphBuildArtifact] | None = None
        self._owner = threading.get_ident()
        self.last_error = ""
        self._closed = False

    @property
    def pending_request(self) -> FrozenBuildInput | None:
        with self._lock:
            return self._pending

    def schedule(self, request: FrozenBuildInput) -> str:
        if not request.source_revision:
            raise ValueError("source_revision is required")
        with self._lock:
            if self._closed:
                return "closed"
            # Dirty paths describe invalidation, not producer input identity.
            # Compare frozen bytes too: revision labels alone are not authority.
            def same_input(other: FrozenBuildInput | None) -> bool:
                return other is not None and (
                    other.source_revision == request.source_revision
                    and other.files == request.files
                )

            if same_input(self._running):
                self._pending = None  # the latest request supersedes queued work
                return "already_running"
            if same_input(self._pending):
                return "already_pending"
            if self._successful is not None and same_input(self._successful[0]):
                self._pending = None
                if self._successful not in self._completed:
                    self._completed.append(self._successful)
                return "already_completed"
            if self._running is None:
                self._start_locked(request)
                return "scheduled"
            unresolved = set(self._running.dirty_paths)
            if self._pending is not None:
                unresolved.update(self._pending.dirty_paths)
            unresolved.update(request.dirty_paths)
            self._pending = FrozenBuildInput(
                request.source_revision, tuple(sorted(unresolved)), request.files
            )
            return "coalesced"

    def _start_locked(self, request: FrozenBuildInput) -> None:
        self._running = request
        future = self._executor.submit(self._invoke, request)
        future.add_done_callback(self._finish)

    def _invoke(self, request: FrozenBuildInput) -> GraphBuildArtifact:
        try:
            result = self._builder(request)
            if not isinstance(result, GraphBuildArtifact):
                raise TypeError("invalid graph build artifact")
            return result
        except Exception as exc:  # build failure is data, never a native-loop exception
            return GraphBuildArtifact(False, "", "", f"build_exception:{type(exc).__name__}")

    def _finish(self, future: Future[GraphBuildArtifact]) -> None:
        with self._lock:
            request = self._running
            if request is not None:
                result = future.result()
                self._completed.append((request, result))
                if result.success:
                    self._successful = (request, result)
            self._running = None
            pending, self._pending = self._pending, None
            if pending is not None and not self._closed:
                self._start_locked(pending)
            self._lock.notify_all()

    def poll(self) -> int:
        if threading.get_ident() != self._owner:
            raise RuntimeError("graph publication requires owner thread")
        with self._lock:
            completed, self._completed = self._completed, []
        for request, result in completed:
            if not result.success:
                self.last_error = result.error or "graph_build_failed"
                continue
            if not self._state.publish_graph(
                graph_path=result.graph_path, graph_revision=result.graph_revision,
                source_revision=request.source_revision,
            ):
                self.last_error = "source_revision_superseded"
            else:
                self.last_error = ""
        return len(completed)

    def wait_idle(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            while self._running is not None or self._pending is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lock.wait(remaining)
        return True

    def close(self, *, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
        self._executor.shutdown(wait=wait, cancel_futures=True)


__all__ = ["FrozenBuildInput", "GraphBuildArtifact", "GraphBuildCoordinator"]
