"""Nonblocking, latest-request graph build coordination."""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .engine_state import EngineState
from .repository_identity import RepositoryHistory


@dataclass(frozen=True, slots=True)
class FrozenBuildInput:
    source_revision: str
    dirty_paths: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]
    history: RepositoryHistory = RepositoryHistory()
    # The graph this build starts from, when there is one. Carried on the
    # request because the builder runs on a worker thread and EngineState is
    # owner-thread only, so reading the published graph at build time would be
    # a race; the owner reads it once, here, when the request is frozen.
    #
    # Deliberately NOT part of _same_input: two requests over the same bytes
    # are the same producer input whichever graph they would amend.
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


EnrichmentFactory = Callable[
    [FrozenBuildInput, GraphBuildArtifact], EnrichmentTaskHandle
]
CandidateCertifier = Callable[
    [FrozenBuildInput, GraphBuildArtifact, Mapping[str, Any]], GraphBuildArtifact
]
EnrichmentObserver = Callable[
    [FrozenBuildInput, GraphBuildArtifact, Mapping[str, Any], str], None
]


class GraphBuildCoordinator:
    """Run one build, retain one coalesced latest request, publish on owner poll."""

    def __init__(
        self,
        state: EngineState,
        builder: Callable[[FrozenBuildInput], GraphBuildArtifact],
        *,
        enrichment_factory: EnrichmentFactory | None = None,
        candidate_certifier: CandidateCertifier | None = None,
        enrichment_observer: EnrichmentObserver | None = None,
    ) -> None:
        if (enrichment_factory is None) != (candidate_certifier is None):
            raise ValueError("enrichment factory and candidate certifier are required together")
        self._state = state
        self._builder = builder
        self._enrichment_factory = enrichment_factory
        self._candidate_certifier = candidate_certifier
        self._enrichment_observer = enrichment_observer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gt-graph")
        self._lock = threading.Condition()
        self._running: FrozenBuildInput | None = None
        self._pending: FrozenBuildInput | None = None
        self._completed: list[tuple[FrozenBuildInput, GraphBuildArtifact]] = []
        self._successful: tuple[FrozenBuildInput, GraphBuildArtifact] | None = None
        self._enrichment: tuple[
            FrozenBuildInput, GraphBuildArtifact, EnrichmentTaskHandle
        ] | None = None
        self._enrichment_considered: set[tuple[Any, ...]] = set()
        self._draining_enrichments: list[
            tuple[FrozenBuildInput, GraphBuildArtifact, EnrichmentTaskHandle]
        ] = []
        self._owner = threading.get_ident()
        self.last_error = ""
        self._closed = False

    @property
    def pending_request(self) -> FrozenBuildInput | None:
        with self._lock:
            return self._pending

    @property
    def enrichment_handle(self) -> EnrichmentTaskHandle | None:
        with self._lock:
            return self._enrichment[2] if self._enrichment is not None else None

    @staticmethod
    def _same_input(left: FrozenBuildInput | None, right: FrozenBuildInput) -> bool:
        return left is not None and (
            left.source_revision == right.source_revision
            and left.files == right.files
            and left.history == right.history
        )

    def schedule(self, request: FrozenBuildInput) -> str:
        if not request.source_revision:
            raise ValueError("source_revision is required")
        with self._lock:
            if self._closed:
                return "closed"
            obsolete = None
            if (
                self._enrichment is not None
                and not self._same_input(self._enrichment[0], request)
            ):
                obsolete = self._enrichment[2]
                self._draining_enrichments.append(self._enrichment)
                self._enrichment = None
            # Dirty paths describe invalidation, not producer input identity.
            # Compare frozen bytes too: revision labels alone are not authority.
            if self._same_input(self._running, request):
                self._pending = None  # the latest request supersedes queued work
                disposition = "already_running"
            elif self._same_input(self._pending, request):
                disposition = "already_pending"
            elif self._successful is not None and self._same_input(
                self._successful[0], request
            ):
                self._pending = None
                if self._successful not in self._completed:
                    self._completed.append(self._successful)
                disposition = "already_completed"
            elif self._running is None:
                self._start_locked(request)
                disposition = "scheduled"
            else:
                unresolved = set(self._running.dirty_paths)
                if self._pending is not None:
                    unresolved.update(self._pending.dirty_paths)
                unresolved.update(request.dirty_paths)
                # Carry the parent graph across the merge. Building this by
                # position dropped the two parent fields the moment they were
                # added, so every coalesced build lost its parent and fell back
                # to a full rebuild -- silently, because a request with no
                # parent is not a refusal, it is simply not an amend. Two of
                # the first eleven builds of the 2026-09-08 run went that way.
                self._pending = FrozenBuildInput(
                    source_revision=request.source_revision,
                    dirty_paths=tuple(sorted(unresolved)),
                    files=request.files,
                    history=request.history,
                    parent_graph_path=request.parent_graph_path,
                    parent_graph_revision=request.parent_graph_revision,
                )
                disposition = "coalesced"
        if obsolete is not None:
            obsolete.cancel()
        return disposition

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

    @staticmethod
    def _enrichment_identity(
        request: FrozenBuildInput, base: GraphBuildArtifact
    ) -> tuple[Any, ...]:
        digest = hashlib.sha256()
        for path, content in request.files:
            name = path.encode("utf-8", "surrogatepass")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return (
            request.source_revision,
            digest.hexdigest(),
            request.history,
            base.graph_path,
            base.graph_revision,
        )

    def consider_enrichment(
        self, request: FrozenBuildInput, base: GraphBuildArtifact
    ) -> str:
        """Schedule one enrichment attempt for an owner-published current graph."""
        if threading.get_ident() != self._owner:
            raise RuntimeError("graph publication requires owner thread")
        if self._enrichment_factory is None:
            return "disabled"
        if (
            not base.success
            or self._state.source_revision != request.source_revision
            or self._state.graph_revision != base.graph_revision
            or self._state.graph_path != base.graph_path
        ):
            return "not_current"
        identity = self._enrichment_identity(request, base)
        with self._lock:
            if self._closed:
                return "closed"
            if self._enrichment is not None:
                active_identity = self._enrichment_identity(
                    self._enrichment[0], self._enrichment[1]
                )
                return (
                    "already_active"
                    if active_identity == identity
                    else "another_enrichment_active"
                )
            if identity in self._enrichment_considered:
                return "already_considered"
            self._enrichment_considered.add(identity)
        try:
            handle = self._enrichment_factory(request, base)
        except Exception as exc:
            receipt = {
                "terminal": True,
                "status": "failed",
                "reason": f"factory_exception:{type(exc).__name__}",
                "publishable": False,
                "source_revision": request.source_revision,
                "input_graph_revision": base.graph_revision,
            }
            self._observe_enrichment(request, base, receipt, "factory_exception")
            self.last_error = f"enrichment_factory_exception:{type(exc).__name__}"
            return "failed"
        with self._lock:
            if self._closed:
                handle.cancel()
                return "closed"
            self._enrichment = (request, base, handle)
        return "scheduled"

    def _poll_enrichment(
        self,
        active: tuple[FrozenBuildInput, GraphBuildArtifact, EnrichmentTaskHandle] | None,
    ) -> int:
        if active is None:
            return 0
        with self._lock:
            if self._enrichment is not active:
                return 0
        request, base, handle = active
        try:
            done = handle.done
        except Exception as exc:
            receipt = {
                "terminal": True,
                "status": "failed",
                "reason": f"handle_exception:{type(exc).__name__}",
                "publishable": False,
            }
            self._observe_enrichment(request, base, receipt, "handle_exception")
            self.last_error = f"enrichment_handle_exception:{type(exc).__name__}"
            with self._lock:
                if self._enrichment is active:
                    self._enrichment = None
            return 1
        if not done:
            return 0
        with self._lock:
            if self._enrichment is active:
                self._enrichment = None
        try:
            receipt = dict(handle.terminal_receipt(timeout=0))
        except Exception as exc:
            receipt = {
                "terminal": True,
                "status": "failed",
                "reason": f"receipt_exception:{type(exc).__name__}",
                "publishable": False,
            }
            self._observe_enrichment(request, base, receipt, "receipt_exception")
            self.last_error = f"enrichment_receipt_exception:{type(exc).__name__}"
            return 1
        if (
            receipt.get("terminal") is not True
            or receipt.get("status") != "succeeded"
            or receipt.get("publishable") is not True
        ):
            self._observe_enrichment(request, base, receipt, "not_publishable")
            self.last_error = "enrichment_not_publishable"
            return 1
        # A candidate that changed no edges is not worth publishing, and
        # publishing it is not merely wasteful - it is harmful. The producer
        # computes the same sum and skips the closure rebuild when it is zero
        # (background_promotion.py:436-437), yet still marks the candidate
        # publishable, so the decision lands here.
        #
        # Run 34077224456: four of ten graph publications were this - a 912MB
        # byte-different copy carrying zero new edges, republished at the SAME
        # repository_revision as the publication before it. Each one changes the
        # graph identity the agent is querying against for no gain, and the
        # change cancels whatever enrichment is in flight: two of that run's six
        # promotions terminated `obsolete` against graphs these republications
        # had just replaced. The churn was manufacturing its own cancellations.
        mutations = sum(
            int(receipt.get(key) or 0)
            for key in ("verified", "corrected", "selected", "deleted")
        )
        if not mutations:
            self._observe_enrichment(request, base, receipt, "no_edge_mutations")
            self.last_error = "enrichment_added_no_edges"
            return 1
        if (
            receipt.get("source_revision") != request.source_revision
            or receipt.get("input_graph_revision") != base.graph_revision
        ):
            self._observe_enrichment(request, base, receipt, "identity_mismatch")
            self.last_error = "enrichment_identity_mismatch"
            return 1
        if (
            self._state.source_revision != request.source_revision
            or self._state.graph_revision != base.graph_revision
            or self._state.graph_path != base.graph_path
        ):
            self._observe_enrichment(request, base, receipt, "obsolete")
            self.last_error = "enrichment_obsolete"
            return 1
        assert self._candidate_certifier is not None
        try:
            candidate = self._candidate_certifier(request, base, receipt)
            if not isinstance(candidate, GraphBuildArtifact):
                raise TypeError("invalid enrichment candidate artifact")
        except Exception as exc:
            self._observe_enrichment(request, base, receipt, "certifier_exception")
            self.last_error = f"enrichment_certifier_exception:{type(exc).__name__}"
            return 1
        if not candidate.success:
            self._observe_enrichment(request, base, receipt, "certification_failed")
            self.last_error = candidate.error or "enrichment_certification_failed"
            return 1
        if (
            self._state.graph_revision != base.graph_revision
            or self._state.graph_path != base.graph_path
            or not self._state.publish_graph(
                graph_path=candidate.graph_path,
                graph_revision=candidate.graph_revision,
                source_revision=request.source_revision,
            )
        ):
            self._observe_enrichment(request, base, receipt, "obsolete_after_certification")
            self.last_error = "enrichment_obsolete_after_certification"
            return 1
        self.last_error = ""
        self._observe_enrichment(request, base, receipt, "published")
        return 1

    @staticmethod
    def _discard_candidate(receipt: Mapping[str, Any]) -> None:
        """Delete a candidate database this coordinator has refused.

        The producer deletes on ITS OWN failure paths - no_op, unavailable,
        cancelled, failed all call _delete_sqlite_candidate - and deliberately
        keeps the candidate on success, because publication consumes it. Zero
        edge mutations is not one of the producer's failures: it skips the
        closure rebuild, finalizes, seals, and returns `succeeded` with
        publishable=True. The refusal happens HERE, in the churn gate, and
        nothing deleted what it refused.

        Run 34095557374 shows the correlation across all fifteen receipts with
        no exception:

            cancelled  x7   producer deleted   graph.db on disk: no
            succeeded  x8   producer kept      graph.db on disk: YES

        Ten surviving candidates at ~920MB each - 9.3GB, 96% of an 11.6GB task
        artifact - and every one of them carried zero edge mutations.

        This lives in _observe_enrichment rather than in each refusal branch
        because there are ten of those and a future eleventh would forget. The
        `published` disposition is the only one that must NOT delete, so it is
        the only one named.
        """
        candidate = receipt.get("candidate_path")
        if not isinstance(candidate, str) or not candidate:
            return
        path = Path(candidate)
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            except OSError:
                # Reporting a refusal must never fail on cleanup, and a
                # candidate left behind is wasteful rather than incorrect.
                pass

    def _observe_enrichment(
        self,
        request: FrozenBuildInput,
        base: GraphBuildArtifact,
        receipt: Mapping[str, Any],
        disposition: str,
    ) -> None:
        if disposition != "published":
            self._discard_candidate(receipt)
        if self._enrichment_observer is None:
            return
        try:
            self._enrichment_observer(request, base, receipt, disposition)
        except Exception as exc:
            self.last_error = f"enrichment_observer_exception:{type(exc).__name__}"

    def _poll_draining_enrichment(
        self,
        item: tuple[FrozenBuildInput, GraphBuildArtifact, EnrichmentTaskHandle],
    ) -> int:
        request, base, handle = item
        try:
            done = handle.done
        except Exception as exc:
            receipt = {
                "terminal": True,
                "status": "failed",
                "reason": f"handle_exception:{type(exc).__name__}",
                "publishable": False,
            }
            self._observe_enrichment(request, base, receipt, "obsolete_handle_exception")
            with self._lock:
                if item in self._draining_enrichments:
                    self._draining_enrichments.remove(item)
            return 1
        if not done:
            return 0
        with self._lock:
            if item not in self._draining_enrichments:
                return 0
            self._draining_enrichments.remove(item)
        try:
            receipt = dict(handle.terminal_receipt(timeout=0))
        except Exception as exc:
            receipt = {
                "terminal": True,
                "status": "failed",
                "reason": f"receipt_exception:{type(exc).__name__}",
                "publishable": False,
            }
            disposition = "obsolete_receipt_exception"
        else:
            disposition = "obsolete"
        self._observe_enrichment(request, base, receipt, disposition)
        return 1

    def poll(self) -> int:
        if threading.get_ident() != self._owner:
            raise RuntimeError("graph publication requires owner thread")
        with self._lock:
            completed, self._completed = self._completed, []
            active_enrichment = self._enrichment
            draining_enrichments = list(self._draining_enrichments)
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
                previous = None
                with self._lock:
                    if (
                        self._enrichment is not None
                        and self._enrichment_identity(
                            self._enrichment[0], self._enrichment[1]
                        ) != self._enrichment_identity(request, result)
                    ):
                        previous = self._enrichment[2]
                        self._draining_enrichments.append(self._enrichment)
                        self._enrichment = None
                if previous is not None:
                    previous.cancel()
                self.consider_enrichment(request, result)
        enrichment_count = self._poll_enrichment(active_enrichment)
        enrichment_count += sum(
            self._poll_draining_enrichment(item) for item in draining_enrichments
        )
        return len(completed) + enrichment_count

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
            enrichment = self._enrichment
            if enrichment is not None:
                self._draining_enrichments.append(enrichment)
                self._enrichment = None
        if enrichment is not None:
            enrichment[2].cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
        if wait:
            with self._lock:
                draining = list(self._draining_enrichments)
            for item in draining:
                request, base, handle = item
                try:
                    receipt = dict(handle.terminal_receipt(timeout=None))
                except Exception as exc:
                    receipt = {
                        "terminal": True,
                        "status": "failed",
                        "reason": f"receipt_exception:{type(exc).__name__}",
                        "publishable": False,
                    }
                    disposition = "close_receipt_exception"
                else:
                    disposition = "closed"
                self._observe_enrichment(request, base, receipt, disposition)
                with self._lock:
                    if item in self._draining_enrichments:
                        self._draining_enrichments.remove(item)


__all__ = [
    "CandidateCertifier",
    "EnrichmentFactory",
    "EnrichmentObserver",
    "EnrichmentTaskHandle",
    "FrozenBuildInput",
    "GraphBuildArtifact",
    "GraphBuildCoordinator",
]
