"""Whole-graph work scheduled into provider-wait windows.

The agent blocks on the provider on every turn, and the block is the only
window in the loop where CPU-bound GT work is guaranteed not to compete
with the host. Whole-graph products - the dense contract store refresh is
the first - are enqueued by the owner thread and launched when the agent
goes out on the network.

The window is a launch gate, not a confinement. ONNX batches and LSP
passes cannot be suspended, so work kicked at window-begin continues on
its own thread past window-end; results are collected by the owner
thread at the next boundary. What the window buys is that scheduling
happens while the host is provably idle, and no burst starts while the
agent is mid-action.

Owner-thread calls are ``enqueue``, ``begin_window``, ``drain`` and
``close``; the worker side never touches adapter state.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any


class ProviderWaitScheduler:
    """One worker, latest-state-wins pending queue, owner-drained results."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gt-wait"
        )
        self._lock = threading.Condition()
        self._pending: dict[str, Callable[[], Mapping[str, Any]]] = {}
        self._running: dict[str, Future[tuple[str, Any]]] = {}
        self._completed: list[tuple[str, str, Any]] = []
        self._closed = False

    def enqueue(self, name: str, work: Callable[[], Mapping[str, Any]]) -> str:
        """Queue one named job; a pending same-name job is replaced.

        Names carry the input identity (e.g. ``dense_refresh:<revision>``),
        so replacement is the coalescing rule: a superseded pending job is
        dropped rather than run against a state that already moved.
        """
        with self._lock:
            if self._closed:
                return "closed"
            if name in self._running:
                return "already_running"
            disposition = "replaced" if name in self._pending else "queued"
            self._pending[name] = work
            return disposition

    def drop_pending_family(self, prefix: str, keep: str) -> list[str]:
        """Drop pending ``prefix:*`` jobs except ``keep``; running jobs finish.

        A refresh job's name carries its revision and its output is keyed to
        that revision's graph file, so under churn a queue of stale-revision
        refreshes is pure spend that also delays the live revision's job
        behind them on the single worker (smoke-20 katex scheduled 133)."""
        with self._lock:
            dropped = [
                name for name in self._pending
                if name.startswith(prefix) and name != keep
            ]
            for name in dropped:
                self._pending.pop(name)
            return dropped

    def begin_window(self) -> list[str]:
        """Launch everything pending. Called only from the owner thread."""
        with self._lock:
            if self._closed:
                self._pending.clear()
                return []
            launched: list[str] = []
            for name, work in self._pending.items():
                future = self._executor.submit(self._invoke, work)
                self._running[name] = future
                future.add_done_callback(self._finish)
                launched.append(name)
            self._pending.clear()
            return launched

    @staticmethod
    def _invoke(work: Callable[[], Mapping[str, Any]]) -> tuple[str, Any]:
        try:
            return "ok", work()
        except Exception as exc:  # noqa: BLE001 - work failure is data
            return "error", f"{type(exc).__name__}: {exc}"[:300]

    def _finish(self, future: Future[tuple[str, Any]]) -> None:
        with self._lock:
            for name, running in list(self._running.items()):
                if running is future:
                    self._running.pop(name)
                    self._completed.append((name, *future.result()))
                    break
            self._lock.notify_all()

    def drain(self) -> list[tuple[str, str, Any]]:
        """Collect finished ``(name, status, payload)`` results."""
        with self._lock:
            completed, self._completed = self._completed, []
            return completed

    def in_flight(self, name: str) -> bool:
        with self._lock:
            return name in self._pending or name in self._running

    def pending_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted((*self._pending, *self._running)))

    def close(self, *, wait: bool = False) -> None:
        """Drop queued work; in-flight jobs are not waited on.

        Same trade as the graph coordinator's close: an uncooperative
        in-flight job must never hold the process open past its deadline,
        so shutdown never joins a running worker.
        """
        with self._lock:
            self._closed = True
            self._pending.clear()
        self._executor.shutdown(wait=wait, cancel_futures=True)
