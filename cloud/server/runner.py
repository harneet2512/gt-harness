"""SessionManager — persistent workspaces, chat turns, receipts.

One session owns one clone of a repo and one mini-swe transcript. Workspace
creation and every agent turn run on worker threads (the agent loop is
blocking); everything that touches the store or the event bus is marshalled
back onto the event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codegraph import build_graph
from .conversational_agent import (
    ConversationalAgent,
    Steering,
    TurnResult,
    turn_wall_seconds,
)
from .events import EventBus
from .prompts import CHAT_BRIEF_TEMPLATE, CHAT_SYSTEM_TEMPLATE
from .sandbox import (
    SANDBOX_WORKDIR,
    ensure_running,
    is_docker_mode,
    reap_sandboxes,
    remove_sandbox,
    start_sandbox,
)
from .store import SessionStore, new_id
from .workspace import (
    TRAJECTORY_NAME,
    cap_diff,
    clone_repo,
    compute_diff,
    list_tree,
    load_transcript,
    looks_like_write,
    remove_workspace,
    save_transcript,
    state_dir,
    workspace_path,
)

log = logging.getLogger(__name__)

_STORE_TIMEOUT = 30
#: how long close() waits for a running turn to notice the stop request
_CLOSE_WAIT_SECONDS = 30.0

#: how long a session may sit ``idle`` before the reaper closes it. ``0``
#: disables the reaper entirely.
DEFAULT_SESSION_IDLE_TTL_SECONDS = 6 * 60 * 60
#: how often the reaper looks
DEFAULT_SESSION_REAP_INTERVAL_SECONDS = 300

#: ``closed_reason`` values (see ``models.ClosedReason``)
CLOSED_BY_USER = "user"
CLOSED_EXPIRED = "expired"
CLOSED_FAILED = "failed"

#: a single per-step ``compute_diff`` may take this long before snapshots are
#: switched off for the rest of the turn (a huge tree must not slow the agent)
DIFF_SNAPSHOT_BUDGET_SECONDS = 2.0

RESTART_NOTICE = "Server restarted; turn interrupted"
SUBMIT_REPLY_FALLBACK = "Done — I submitted my changes."


def _positive_int_env(name: str, default: int) -> int:
    """A non-negative integer setting, falling back on anything unparseable."""
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def idle_ttl_seconds() -> int:
    """How long a session may sit idle before it is reaped. ``0`` disables it.

    Without this a workspace (a full repo clone) and its sandbox container
    live until someone remembers to press close, so the host disk is a
    monotonic function of how many sessions anyone ever opened.
    """
    return _positive_int_env(
        "SESSION_IDLE_TTL_SECONDS", DEFAULT_SESSION_IDLE_TTL_SECONDS
    )


def reap_interval_seconds() -> int:
    """How often the reaper wakes up. Never zero — that would be a busy loop."""
    return (
        _positive_int_env(
            "SESSION_REAP_INTERVAL_SECONDS", DEFAULT_SESSION_REAP_INTERVAL_SECONDS
        )
        or DEFAULT_SESSION_REAP_INTERVAL_SECONDS
    )


@dataclass
class _SessionState:
    session_id: str
    workspace: str | None = None
    agent: ConversationalAgent | None = None
    #: name of this session's sandbox container (SANDBOX_MODE=docker only)
    sandbox: str | None = None
    #: commit the workspace was cloned at, for per-step diff snapshots
    base_sha: str = ""
    #: GT graph database built for this workspace, mirrored on the session row
    graph_db: str | None = None
    #: set when a per-step ``compute_diff`` blew its budget; reset each turn
    snapshots_disabled: bool = False
    #: last file-relation graph, keyed by its tree signature (see ``graph``)
    graph_cache: tuple[str, dict] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    turn_done: threading.Event = field(default_factory=threading.Event)
    closed: bool = False
    pending_stop: bool = False
    creation_task: Any = None
    #: steering delivered after the turn was accepted but before the worker
    #: thread finished building the agent
    deferred: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.turn_done.set()


class SessionManager:
    def __init__(self, store: SessionStore, event_bus: EventBus) -> None:
        self._store = store
        self._bus = event_bus
        self._states: dict[str, _SessionState] = {}
        # reentrant: _deliver_steering / _attach_agent call _state() while held
        self._states_lock = threading.RLock()
        self._count_lock = threading.Lock()
        self._running_count = 0
        self._max_concurrent = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))
        self._reaper_task: asyncio.Task | None = None

    # -- state bookkeeping ----------------------------------------------------

    def _state(self, session_id: str) -> _SessionState:
        with self._states_lock:
            state = self._states.get(session_id)
            if state is None:
                state = _SessionState(session_id=session_id)
                self._states[session_id] = state
            return state

    def get_agent(self, session_id: str) -> ConversationalAgent | None:
        with self._states_lock:
            state = self._states.get(session_id)
        return state.agent if state else None

    def _attach_agent(
        self, state: _SessionState, agent: ConversationalAgent
    ) -> None:
        with self._states_lock:
            state.agent = agent
            for message_id, content in state.deferred:
                agent.queue_steering(message_id, content)
            state.deferred = []

    def _deliver_steering(
        self, session_id: str, message_id: str, content: str
    ) -> None:
        """Hand a mid-turn message to the agent, or park it until it exists."""
        with self._states_lock:
            state = self._state(session_id)
            if state.agent is not None:
                state.agent.queue_steering(message_id, content)
            else:
                state.deferred.append((message_id, content))

    @property
    def running_count(self) -> int:
        with self._count_lock:
            return self._running_count

    def _acquire_slot(self) -> bool:
        with self._count_lock:
            if self._running_count >= self._max_concurrent:
                return False
            self._running_count += 1
            return True

    def _release_slot(self) -> None:
        with self._count_lock:
            self._running_count = max(0, self._running_count - 1)

    # -- workspace creation ---------------------------------------------------

    async def create_workspace(self, session_id: str) -> None:
        """Clone the repo (and build the GT index) in the background."""
        session = await self._store.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        await self._bus.publish(
            session_id, {"type": "lifecycle", "data": {"status": "creating"}}
        )
        loop = asyncio.get_running_loop()
        self._state(session_id).creation_task = asyncio.ensure_future(
            asyncio.to_thread(self._create_blocking, dict(session), loop)
        )

    def _create_blocking(self, session: dict, loop: asyncio.AbstractEventLoop) -> None:
        session_id = str(session["id"])
        state = self._state(session_id)
        workspace = workspace_path(session_id)
        try:
            self._emit(loop, session_id, "lifecycle", {
                "status": "cloning",
                "repo": session["repo"],
                "ref": session["ref"],
            })
            base_sha = clone_repo(
                str(session["repo"]), str(session["ref"]), workspace
            )
            state.workspace = workspace
            state.base_sha = base_sha
            state.sandbox = self._start_sandbox(session_id, workspace, loop)

            gt_status, graph_db, gt_error = self._prepare_gt(session, workspace, loop)
            state.graph_db = graph_db
            self._call(loop, self._store.update_status(
                session_id, "idle",
                workspace_path=workspace,
                base_sha=base_sha,
                gt_status=gt_status,
                gt_error=gt_error,
                graph_db=graph_db,
            ))
            self._emit(loop, session_id, "lifecycle", {"status": "idle"})
        except Exception as exc:  # noqa: BLE001 - any failure fails the session
            error = f"{type(exc).__name__}: {exc}"
            self._emit(loop, session_id, "agent_error", {"error": error})
            try:
                self._call(loop, self._store.update_status(
                    session_id, "failed", closed_reason=CLOSED_FAILED
                ))
            except Exception:  # noqa: BLE001
                pass
            self._emit(
                loop, session_id, "lifecycle", {"status": "failed", "error": error}
            )
            self._bus.finish(session_id)

    def _start_sandbox(
        self, session_id: str, workspace: str, loop: asyncio.AbstractEventLoop
    ) -> str | None:
        """Bring up this session's container. Fails the session if it cannot.

        Fail closed: there is no fallback to local execution, because that
        would silently drop both the isolation and the egress policy the
        sandbox exists to enforce.
        """
        if not is_docker_mode():
            return None
        self._emit(loop, session_id, "lifecycle", {"status": "sandbox_starting"})
        try:
            info = start_sandbox(session_id, workspace)
        except Exception as exc:  # noqa: BLE001 - re-raised, the caller fails
            self._emit(loop, session_id, "lifecycle", {
                "status": "sandbox_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        self._emit(loop, session_id, "lifecycle", {"status": "sandbox_ready", **info})
        return info["container"]

    def _prepare_gt(
        self, session: dict, workspace: str, loop: asyncio.AbstractEventLoop
    ) -> tuple[str, str | None, str | None]:
        """Build the GT index if the session asked for it. Never fatal.

        Returns the ``gt_status`` to persist, the graph database path (which
        the session row keeps so ``graph()`` can find it later) and the failure
        text, so a reload can still say *why* GT is unavailable rather than
        only that it is — the ``gt_unavailable`` event scrolls away.
        """
        session_id = str(session["id"])
        if str(session["gt_mode"]) == "off":
            return "off", None, None
        self._emit(loop, session_id, "lifecycle", {"status": "indexing"})
        try:
            from gt_engine.indexer import ensure_index_with_receipt

            receipt = ensure_index_with_receipt(
                workspace, state_dir=str(state_dir(workspace))
            )
            graph_db = _graph_db_of(receipt)
            if graph_db is None:
                raise RuntimeError(_index_failure_reason(receipt))
            self._emit(loop, session_id, "lifecycle", {
                "status": "gt_ready",
                "gt_mode": session["gt_mode"],
                "graph_db": str(graph_db),
            })
            return "ready", str(graph_db), None
        except Exception as exc:  # noqa: BLE001 - GT degrades, it does not fail
            error = f"{type(exc).__name__}: {exc}"
            self._emit(
                loop, session_id, "lifecycle",
                {"status": "gt_unavailable", "error": error},
            )
            return "unavailable", None, error

    # -- messages / turns -----------------------------------------------------

    async def post_message(self, session_id: str, content: str) -> tuple[dict, str]:
        """Deliver a user message: start a turn, or steer the running one."""
        session = await self._store.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")

        if session["status"] == "running":
            message = await self._store.add_message(
                session_id,
                role="user",
                content=content,
                turn_id=session["current_turn_id"],
            )
            self._deliver_steering(session_id, message["id"], content)
            await self._store.update_session(session_id, last_message=content)
            return message, "queued_for_running_turn"

        if not self._acquire_slot():
            raise ConcurrencyLimit(
                f"max concurrent turns ({self._max_concurrent}) reached"
            )
        try:
            turn_id = new_id()
            message = await self._store.add_message(
                session_id, role="user", content=content, turn_id=turn_id
            )
            await self._store.update_status(
                session_id, "running",
                current_turn_id=turn_id,
                last_message=content,
            )
            await self._store.start_turn(
                session_id,
                turn_id,
                model=str(session["model"]),
                gt_status=str(session["gt_status"]),
            )
            await self._bus.publish(
                session_id, {"type": "lifecycle", "data": {"status": "running"}}
            )
            await self._bus.publish(session_id, {
                "type": "turn_started",
                "data": {"turn_id": turn_id, "message_id": message["id"]},
            })
        except Exception:
            self._release_slot()
            raise

        state = self._state(session_id)
        state.turn_done.clear()
        loop = asyncio.get_running_loop()
        asyncio.ensure_future(
            asyncio.to_thread(
                self._turn_worker, dict(session), turn_id, content, loop
            )
        )
        return message, "turn_started"

    def _turn_worker(
        self,
        session: dict,
        turn_id: str,
        user_text: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        session_id = str(session["id"])
        state = self._state(session_id)
        try:
            with state.lock:
                agent = self._ensure_agent(session, state, loop, user_text)
                if state.pending_stop:
                    state.pending_stop = False
                    agent.request_stop()
                while True:
                    # the budget switch is per turn, not per session
                    state.snapshots_disabled = False
                    result = agent.run_turn(user_text, turn_id=turn_id)
                    self._finish_turn(session, state, turn_id, result, loop)
                    pending = agent.take_pending_steering()
                    if not pending or state.closed:
                        break
                    turn_id, user_text = self._chain_turn(session, pending, loop)
            if not state.closed:
                self._set_status(loop, session_id, "idle", current_turn_id=None)
                self._emit(loop, session_id, "lifecycle", {"status": "idle"})
        except Exception as exc:  # noqa: BLE001 - an agent crash fails the session
            error = f"{type(exc).__name__}: {exc}"
            agent = state.agent
            if agent is None or agent.last_error_turn_id != turn_id:
                # The agent already reported failures it saw itself; this covers
                # everything around it (workspace, agent build, bookkeeping).
                self._emit(
                    loop, session_id, "agent_error",
                    {"turn_id": turn_id, "error": error},
                )
            self._call_quietly(loop, self._store.finish_turn(
                turn_id, n_calls=0, cost=0.0, finish_reason="error", patch_sha256=None
            ))
            self._set_status(
                loop, session_id, "failed",
                current_turn_id=None,
                closed_reason=CLOSED_FAILED,
            )
            self._emit(
                loop, session_id, "lifecycle", {"status": "failed", "error": error}
            )
            self._bus.finish(session_id)
        finally:
            self._release_slot()
            state.turn_done.set()

    def _chain_turn(
        self,
        session: dict,
        pending: list[Steering],
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[str, str]:
        """Start a follow-up turn for messages that arrived as the turn ended."""
        session_id = str(session["id"])
        turn_id = new_id()
        text = "\n\n".join(item.content for item in pending)
        self._call(loop, self._store.update_session(
            session_id, current_turn_id=turn_id
        ))
        self._call(loop, self._store.start_turn(
            session_id,
            turn_id,
            model=str(session["model"]),
            gt_status=str(session["gt_status"]),
        ))
        self._emit(loop, session_id, "turn_started", {
            "turn_id": turn_id, "message_id": pending[-1].message_id
        })
        return turn_id, text

    def _finish_turn(
        self,
        session: dict,
        state: _SessionState,
        turn_id: str,
        result: TurnResult,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        session_id = str(session["id"])
        workspace = state.workspace or str(session.get("workspace_path") or "")
        diff = compute_diff(workspace, str(session.get("base_sha") or ""))
        patch_sha256 = (
            hashlib.sha256(diff["patch"].encode("utf-8")).hexdigest()
            if diff["patch"]
            else None
        )
        files_changed = [f["path"] for f in diff["files"]]

        reply = result.reply
        if result.finish_reason == "submitted":
            reply = _submission_summary(reply, diff)

        meta = {
            "finish_reason": result.finish_reason,
            "n_calls": result.n_calls,
            "cost": result.cost,
            "patch_sha256": patch_sha256,
            "files_changed": files_changed,
        }
        message = self._call(loop, self._store.add_message(
            session_id, role="agent", content=reply, turn_id=turn_id, meta=meta
        ))
        self._call(loop, self._store.finish_turn(
            turn_id,
            n_calls=result.n_calls,
            cost=result.cost,
            finish_reason=result.finish_reason,
            patch_sha256=patch_sha256,
            wall_seconds=result.wall_seconds,
        ))
        self._call(loop, self._store.bump_totals(
            session_id,
            turns=1,
            steps=result.n_calls,
            cost=result.cost,
            wall_seconds=result.wall_seconds,
        ))
        self._call(loop, self._store.update_session(
            session_id, last_message=reply
        ))
        self._emit(loop, session_id, "agent_reply", {
            "turn_id": turn_id,
            "message_id": message["id"],
            "content": reply,
            "finish_reason": result.finish_reason,
            "n_calls": result.n_calls,
            "cost": result.cost,
            "patch_sha256": patch_sha256,
            "files_changed": files_changed,
        })
        if result.finish_reason == "stopped":
            self._emit(loop, session_id, "lifecycle", {"status": "stopped"})
        self._emit(loop, session_id, "turn_finished", {
            "turn_id": turn_id,
            "finish_reason": result.finish_reason,
            "n_calls": result.n_calls,
            "cost": result.cost,
            # carried here too, so a turn that ended in stopped/step_limit/error
            # still exposes patch identity without refetching /receipts
            "patch_sha256": patch_sha256,
            "files_changed": files_changed,
        })
        self._persist_transcript(state)

    # -- stop / close ---------------------------------------------------------

    async def stop(self, session_id: str) -> bool:
        """Ask the running turn to end at its next step boundary."""
        # A stop is user activity like any other; without this the row keeps
        # the timestamp of whatever last wrote to it and the idle TTL measures
        # age instead of idleness.
        with contextlib.suppress(Exception):
            await self._store.touch(session_id)
        state = self._state(session_id)
        agent = state.agent
        if agent is None:
            # The turn was accepted but its worker has not built the agent yet.
            state.pending_stop = True
            return True
        agent.request_stop()
        return True

    async def close(self, session_id: str, *, reason: str = CLOSED_BY_USER) -> None:
        """Kill the turn, drop the sandbox and the workspace, close the row.

        ``reason`` is recorded on the session and echoed on the lifecycle
        event, so a session that vanished under a user can say whether they
        closed it or the idle TTL did.
        """
        session = await self._store.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        # A session that already died of something keeps that cause: closing a
        # failed session is bookkeeping, not the reason it ended.
        reason = str(session.get("closed_reason") or "") or reason
        state = self._state(session_id)
        state.closed = True
        agent = self.get_agent(session_id)
        if agent is not None:
            agent.request_stop()
        if session["status"] == "running":
            await asyncio.to_thread(state.turn_done.wait, _CLOSE_WAIT_SECONDS)

        if is_docker_mode():
            # Before the workspace: the container bind-mounts it.
            await asyncio.to_thread(remove_sandbox, session_id)
        workspace = state.workspace or session.get("workspace_path")
        if workspace:
            remove_workspace(str(workspace))
        with self._states_lock:
            self._states.pop(session_id, None)
        if session["status"] != "closed":
            await self._store.update_status(
                session_id, "closed", current_turn_id=None, closed_reason=reason
            )
            await self._bus.publish(
                session_id,
                {"type": "lifecycle", "data": {"status": "closed", "reason": reason}},
            )
        self._bus.finish(session_id)

    # -- idle TTL reaper ------------------------------------------------------

    def start_reaper(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the background idle-session reaper (idempotent)."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        if idle_ttl_seconds() <= 0:
            log.info("session idle reaper disabled (SESSION_IDLE_TTL_SECONDS=0)")
            return
        loop = loop or asyncio.get_running_loop()
        self._reaper_task = loop.create_task(self._reaper_loop())

    async def stop_reaper(self) -> None:
        task, self._reaper_task = self._reaper_task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _reaper_loop(self) -> None:
        """Reap expired sessions forever. One bad pass never ends the loop."""
        interval = reap_interval_seconds()
        log.info(
            "session idle reaper: ttl=%ss interval=%ss",
            idle_ttl_seconds(),
            interval,
        )
        while True:
            try:
                await asyncio.sleep(interval)
                await self.reap_idle_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the reaper must outlive its faults
                log.exception("session idle reaper pass failed")

    async def reap_idle_sessions(self) -> list[str]:
        """Close every session idle for longer than the TTL. Returns their ids.

        Closing is the *same* path as ``/close`` — sandbox, then workspace,
        then the row — so an expired session leaves nothing behind that a
        user-closed one would not.
        """
        ttl = idle_ttl_seconds()
        if ttl <= 0:
            return []
        try:
            expired = await self._store.idle_sessions_before(time.time() - ttl)
        except Exception:  # noqa: BLE001 - a store hiccup is not fatal
            log.exception("session idle reaper could not list expired sessions")
            return []
        reaped: list[str] = []
        for session in expired:
            session_id = str(session["id"])
            try:
                # Re-read: a message may have started a turn since the query.
                current = await self._store.get_session(session_id)
                if current is None or str(current["status"]) != "idle":
                    continue
                await self.close(session_id, reason=CLOSED_EXPIRED)
            except Exception:  # noqa: BLE001 - one bad session, not the pass
                log.exception("session idle reaper could not close %s", session_id)
                continue
            log.info("session %s closed after %ss idle", session_id, ttl)
            reaped.append(session_id)
        return reaped

    async def recover(self) -> None:
        """After a restart: no agent survives, so no turn can still be running."""
        for session in await self._store.sessions_with_status("running"):
            session_id = str(session["id"])
            await self._store.update_status(
                session_id, "idle", current_turn_id=None
            )
            await self._store.add_message(
                session_id, role="system", content=RESTART_NOTICE
            )
            await self._bus.publish(
                session_id, {"type": "lifecycle", "data": {"status": "idle"}}
            )
        for session in await self._store.sessions_with_status("creating"):
            session_id = str(session["id"])
            error = "Server restarted during workspace creation"
            await self._store.update_status(
                session_id, "failed", closed_reason=CLOSED_FAILED
            )
            await self._bus.publish(session_id, {
                "type": "lifecycle",
                "data": {"status": "failed", "error": error},
            })
        await self.reap_idle_sessions()
        await self._reap_sandboxes()

    async def _reap_sandboxes(self) -> None:
        """Drop containers whose session is gone; keep the ones still usable.

        A sandbox outlives a server restart on purpose — the workspace is still
        on disk and the session is still ``idle``, so the same container keeps
        serving it. Only sessions that no longer exist (or are closed/failed)
        lose theirs.
        """
        if not is_docker_mode():
            return
        sessions = await self._store.list_sessions(limit=10_000)
        keep = {
            str(session["id"])
            for session in sessions
            if str(session["status"]) not in {"closed", "failed"}
        }
        await asyncio.to_thread(reap_sandboxes, keep)

    # -- diff -----------------------------------------------------------------

    async def diff(self, session: dict) -> dict:
        workspace = session.get("workspace_path")
        if not workspace or not Path(str(workspace)).is_dir():
            return {"patch": "", "files": [], "base_sha": session.get("base_sha") or ""}
        return await asyncio.to_thread(
            compute_diff, str(workspace), str(session.get("base_sha") or "")
        )

    async def diff_at(self, session: dict, through_event: int) -> dict:
        """The stored diff as of event ``through_event`` — the scrubber's diff.

        Exact, not an approximation: it is the snapshot taken on the worker
        thread right after the write, keyed by the ``tool_result`` event id.
        """
        base_sha = str(session.get("base_sha") or "")
        snapshot = await self._store.latest_diff_snapshot(
            str(session["id"]), through_event
        )
        if snapshot is None:
            return {
                "patch": "",
                "files": [],
                "base_sha": base_sha,
                "as_of_event": 0,
                "approximate": False,
            }
        return {
            "patch": snapshot["patch"],
            "files": snapshot["files"],
            "base_sha": base_sha,
            "as_of_event": snapshot["event_id"],
            "approximate": False,
            "truncated": True if snapshot["truncated"] else None,
        }

    async def tree(self, session: dict) -> dict:
        workspace = session.get("workspace_path")
        base_sha = str(session.get("base_sha") or "")
        if not workspace or not Path(str(workspace)).is_dir():
            return {"base_sha": base_sha, "files": []}
        files = await asyncio.to_thread(list_tree, str(workspace))
        return {"base_sha": base_sha, "files": files}

    async def graph(self, session: dict) -> dict:
        """The file-relation graph of the workspace, cached per tree state."""
        workspace = session.get("workspace_path")
        base_sha = str(session.get("base_sha") or "")
        empty = {"base_sha": base_sha, "gt": False, "nodes": [], "edges": []}
        if not workspace or not Path(str(workspace)).is_dir():
            return empty
        return await asyncio.to_thread(
            self._graph_blocking, dict(session), str(workspace), base_sha
        )

    def _graph_blocking(self, session: dict, workspace: str, base_sha: str) -> dict:
        session_id = str(session["id"])
        state = self._state(session_id)
        files = list_tree(workspace)
        signature = _tree_signature(base_sha, files)
        cached = state.graph_cache
        if cached is not None and cached[0] == signature:
            return cached[1]
        graph = build_graph(workspace, files, self._graph_db_for(session, state))
        graph["base_sha"] = base_sha
        state.graph_cache = (signature, graph)
        return graph

    @staticmethod
    def _graph_db_for(session: dict, state: _SessionState) -> str | None:
        """The GT graph database to read edges from, if GT is actually ready."""
        if str(session.get("gt_status") or "") != "ready":
            return None
        return str(session.get("graph_db") or state.graph_db or "") or None

    # -- agent construction ---------------------------------------------------

    def _ensure_agent(
        self,
        session: dict,
        state: _SessionState,
        loop: asyncio.AbstractEventLoop,
        issue_text: str,
    ) -> ConversationalAgent:
        if state.agent is not None:
            return state.agent
        workspace = state.workspace or str(session.get("workspace_path") or "")
        if not workspace:
            raise RuntimeError("session has no workspace")
        state.workspace = workspace
        state.base_sha = state.base_sha or str(session.get("base_sha") or "")
        # After a restart the container is still there but `state` is fresh, so
        # the name is re-derived and checked rather than trusted.
        sandbox = ensure_running(str(session["id"])) if is_docker_mode() else None
        state.sandbox = sandbox
        config = _session_config(session)
        agent = self._build_agent(
            session_id=str(session["id"]),
            repo=str(session["repo"]),
            ref=str(session["ref"]),
            model=str(session["model"]),
            cwd=workspace,
            sandbox=sandbox,
            gt_mode=str(session["gt_mode"]),
            step_limit=int(config.get("step_limit", 60)),
            wall_seconds=int(config.get("wall_seconds") or turn_wall_seconds()),
            temperature=float(config.get("temperature", 0.0)),
            issue_text=issue_text,
            loop=loop,
        )
        transcript = load_transcript(workspace)
        if transcript:
            agent.restore(transcript)
        else:
            agent.begin_session()
        self._attach_agent(state, agent)
        return agent

    def _build_agent(
        self,
        *,
        session_id: str,
        repo: str,
        ref: str,
        model: str,
        cwd: str,
        sandbox: str | None,
        gt_mode: str,
        step_limit: int,
        wall_seconds: int,
        temperature: float,
        issue_text: str,
        loop: asyncio.AbstractEventLoop,
    ) -> ConversationalAgent:
        from minisweagent.agents.default import AgentConfig
        from minisweagent.models.litellm_model import LitellmModel

        from .environment import CloudLocalEnvironment, LocalEnvironmentConfig

        model_kwargs: dict[str, Any] = {"temperature": temperature, "num_retries": 0}
        model_name = model
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            if not model_name.startswith("openai/"):
                model_name = f"openai/{model_name}"
            model_kwargs["api_base"] = base_url

        gt_off = gt_mode == "off"
        model_obj: Any
        if gt_off:
            model_obj = LitellmModel(model_name=model_name, model_kwargs=model_kwargs)
        else:
            try:
                from .typed_scopes import build_scope_normalizing_model

                # HAR-85: planners write glob scopes ("src/click/**"), which the
                # deterministic literal-search producer stats as a literal path
                # and then abstains on. Make them concrete before dispatch.
                model_obj = build_scope_normalizing_model(
                    repo_root=cwd,
                    model_name=model_name,
                    model_kwargs=model_kwargs,
                )
            except ImportError:
                model_obj = LitellmModel(
                    model_name=model_name, model_kwargs=model_kwargs
                )
                gt_off = True

        # The sandbox sees the workspace at /workspace, the server sees it at
        # its host path. GT indexing, the scratch dir and the diff stay on the
        # host path; only the agent's shell and its brief move.
        env_obj: Any
        if sandbox:
            from .sandbox import DockerSandboxEnvironment

            env_obj = DockerSandboxEnvironment(
                container=sandbox, image=os.environ.get("SANDBOX_IMAGE", ""),
                cwd=SANDBOX_WORKDIR, timeout=30,
            )
            env_cwd = SANDBOX_WORKDIR
        else:
            env_obj = CloudLocalEnvironment(
                config_class=LocalEnvironmentConfig, cwd=cwd, timeout=30
            )
            env_cwd = cwd
        scratch = state_dir(cwd)
        scratch.mkdir(parents=True, exist_ok=True)

        agent = ConversationalAgent(
            model_obj,
            env_obj,
            event_callback=lambda event: self._publish(loop, session_id, event),
            config_class=AgentConfig,
            system_template=CHAT_SYSTEM_TEMPLATE,
            instance_template=CHAT_BRIEF_TEMPLATE,
            step_limit=step_limit,
            wall_seconds=wall_seconds,
            cost_limit=0.0,
            output_path=scratch / TRAJECTORY_NAME,
        )
        agent.extra_template_vars |= {"repo": repo, "ref": ref, "cwd": env_cwd}

        if not gt_off:
            self._install_gt(
                agent=agent,
                session_id=session_id,
                cwd=cwd,
                scratch_dir=str(scratch),
                gt_mode=gt_mode,
                issue_text=issue_text,
                model=model,
                model_name=model_name,
                loop=loop,
            )
        return agent

    def _install_gt(
        self,
        *,
        agent: ConversationalAgent,
        session_id: str,
        cwd: str,
        scratch_dir: str,
        gt_mode: str,
        issue_text: str,
        model: str,
        model_name: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Wire the GT engine onto the agent. Degrades to a plain run on error."""
        try:
            from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
            from gt_engine.indexer import ensure_index_with_receipt
            from gt_engine.miniswe_controller import Predicate
            from gt_engine.miniswe_integration import MiniSweAdapter
            from gt_engine.miniswe_runtime import install_runtime_hooks
            from gt_engine.task_contract import extract_task_contract
            from gt_engine.verification_contract import compile_obligation_predicates

            index_receipt = ensure_index_with_receipt(cwd, state_dir=scratch_dir)
            graph_db = _graph_db_of(index_receipt)

            contract = extract_task_contract(issue_text)
            compiled = compile_obligation_predicates(contract)
            predicates = tuple(
                Predicate(item.predicate_id, co.text)
                for co in contract.obligations
                for item in (compiled[co.obligation_id],)
            )
            task_id = hashlib.sha256(issue_text.encode("utf-8")).hexdigest()[:16]

            adapter = MiniSweAdapter(
                task_id=task_id,
                state_dir=scratch_dir,
                predicates=predicates,
                contract=contract,
                repo_root=cwd,
                graph_db=graph_db,
                issue_text=issue_text,
                requested_model=model,
                resolved_model=model_name,
            )
            adapter.initial_index_receipt = index_receipt

            gt_session = GTSession(
                GTSessionConfig(
                    task_id=task_id,
                    repo_root=cwd,
                    state_dir=scratch_dir,
                    graph_db=graph_db,
                    capabilities=(),
                    issue_text=issue_text,
                    mode=GTMode(gt_mode),
                ),
                engine=adapter,
            )
            install_runtime_hooks(agent, gt_session)
            self._emit(loop, session_id, "lifecycle", {
                "status": "gt_ready",
                "gt_mode": gt_mode,
                "graph_db": str(graph_db or ""),
            })
            self._state(session_id).graph_db = str(graph_db) if graph_db else None
            self._call_quietly(loop, self._store.update_session(
                session_id,
                gt_status="ready",
                gt_error=None,
                graph_db=str(graph_db) if graph_db else None,
            ))
        except Exception as exc:  # noqa: BLE001 - GT degrades, it does not fail
            error = f"{type(exc).__name__}: {exc}"
            self._emit(
                loop, session_id, "lifecycle",
                {"status": "gt_unavailable", "error": error},
            )
            self._call_quietly(loop, self._store.update_session(
                session_id, gt_status="unavailable", gt_error=error
            ))

    # -- persistence / plumbing -----------------------------------------------

    def _persist_transcript(self, state: _SessionState) -> None:
        if state.agent is None or not state.workspace:
            return
        save_transcript(state.workspace, state.agent.messages)

    def _set_status(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        status: str,
        **fields: object,
    ) -> None:
        try:
            self._call(loop, self._store.update_status(session_id, status, **fields))
        except ValueError:
            # The session was closed underneath us; the close path owns the row.
            pass

    def _publish(
        self, loop: asyncio.AbstractEventLoop, session_id: str, event: dict
    ) -> None:
        event.setdefault("timestamp", time.time())
        published = self._call_quietly(loop, self._bus.publish(session_id, event))
        if event.get("type") != "tool_result" or not isinstance(published, dict):
            return
        event_id = int(published.get("id") or 0)
        if event_id:
            self._snapshot_diff(loop, session_id, event_id, event.get("data") or {})

    def _snapshot_diff(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        event_id: int,
        data: dict,
    ) -> None:
        """Store the workspace diff as of a write, for ``/diff?through_event=``.

        Runs inline on the turn worker thread — already off the event loop, and
        between the command and the next model call, so the tree is quiet. If
        one ``compute_diff`` overruns :data:`DIFF_SNAPSHOT_BUDGET_SECONDS` the
        rest of the turn goes without snapshots rather than paying it again.
        """
        state = self._state(session_id)
        workspace = state.workspace
        if state.snapshots_disabled or not workspace:
            return
        if not looks_like_write(str(data.get("command") or "")):
            return
        started = time.monotonic()
        try:
            diff = compute_diff(workspace, state.base_sha)
        except Exception:  # noqa: BLE001 - a snapshot is never worth a turn
            return
        elapsed = time.monotonic() - started
        patch, files, truncated = cap_diff(diff)
        full_patch = str(diff.get("patch") or "")
        self._call_quietly(loop, self._store.add_diff_snapshot(
            session_id,
            event_id=event_id,
            turn_id=data.get("turn_id"),
            step=int(data.get("step") or 0),
            patch_sha256=(
                hashlib.sha256(full_patch.encode("utf-8")).hexdigest()
                if full_patch
                else None
            ),
            files=files,
            patch=patch,
            truncated=truncated,
        ))
        if elapsed > DIFF_SNAPSHOT_BUDGET_SECONDS:
            state.snapshots_disabled = True
            self._emit(loop, session_id, "lifecycle", {
                "status": "diff_snapshots_disabled",
                "reason": (
                    f"compute_diff took {elapsed:.1f}s, over the "
                    f"{DIFF_SNAPSHOT_BUDGET_SECONDS:g}s budget"
                ),
            })

    def _emit(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        event_type: str,
        data: dict,
    ) -> None:
        self._publish(loop, session_id, {"type": event_type, "data": data})

    @staticmethod
    def _call(loop: asyncio.AbstractEventLoop, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, loop).result(
            timeout=_STORE_TIMEOUT
        )

    @staticmethod
    def _call_quietly(loop: asyncio.AbstractEventLoop, coro: Any) -> Any:
        """``_call`` that swallows failures. Returns ``None`` when it swallowed."""
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop).result(
                timeout=_STORE_TIMEOUT
            )
        except Exception:  # noqa: BLE001 - never let bookkeeping kill a turn
            return None


class ConcurrencyLimit(RuntimeError):
    """Raised when a new turn would exceed MAX_CONCURRENT_SESSIONS."""


def _tree_signature(base_sha: str, files: list[dict]) -> str:
    """Cheap identity of a tree: the base commit plus every path and size."""
    digest = hashlib.sha256(base_sha.encode("utf-8"))
    for entry in files:
        digest.update(f"\0{entry['path']}\0{entry.get('size', 0)}".encode())
    return digest.hexdigest()


def _graph_db_of(receipt: Any) -> Any:
    """The built graph database, or ``None`` if this index is not usable.

    ``IndexBuildReceipt`` reports ``status`` (``built``/``build_failed``/
    ``invalid_database``/``not_applicable``) and ``graph_db``; it has no
    ``available`` flag, so readiness is "status is built and a db came back".
    """
    status = str(getattr(getattr(receipt, "status", ""), "value", "") or "")
    graph_db = getattr(receipt, "graph_db", None)
    return graph_db if status == "built" and graph_db else None


def _index_failure_reason(receipt: Any) -> str:
    """Why an index is not usable, in the indexer's own words."""
    status = str(getattr(getattr(receipt, "status", ""), "value", "") or "unknown")
    detail = str(
        getattr(receipt, "error_diagnostic", "")
        or getattr(receipt, "error_type", "")
        or ""
    ).strip()
    if status == "not_applicable":
        detail = detail or "the repository has nothing this indexer can index"
    return f"index status {status}" + (f": {detail}" if detail else "")


def _submission_summary(submission: str, diff: dict) -> str:
    """Turn the legacy submit marker into something a human can read."""
    files = diff.get("files") or []
    if not files:
        return submission.strip() or SUBMIT_REPLY_FALLBACK
    lines = [
        f"- {f['path']} ({f['status']}, +{f['additions']}/-{f['deletions']})"
        for f in files
    ]
    header = f"Submitted. {len(files)} file(s) changed:"
    return "\n".join([header, *lines])


def _session_config(session: dict) -> dict:
    try:
        return json.loads(session.get("config_json") or "{}")
    except Exception:  # noqa: BLE001
        return {}
