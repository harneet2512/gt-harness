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
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codegraph import build_graph
from .conversational_agent import (
    TURN_ERROR_REPLY,
    ConversationalAgent,
    Steering,
    TurnResult,
    turn_wall_seconds,
)
from .events import EventBus
from .models import (
    MAX_ACTIVITY_CHARS,
    MAX_INGEST_FILES,
    MAX_INGEST_PATH_CHARS,
    MAX_TASKS_PER_SPAWN,
)
from .prompts import CHAT_BRIEF_TEMPLATE, CHAT_SYSTEM_TEMPLATE
from .sandbox import (
    SANDBOX_WORKDIR,
    SandboxError,
    ensure_running,
    is_docker_mode,
    reap_sandboxes,
    remove_sandbox,
    start_sandbox,
)
from .store import SessionStore, new_id
from .workspace import (
    TRAJECTORY_NAME,
    apply_patch,
    cap_diff,
    clone_repo,
    compute_diff,
    ensure_free_space,
    list_tree,
    load_transcript,
    looks_like_write,
    remove_workspace,
    save_transcript,
    state_dir,
    workspace_max_mb,
    workspace_mb,
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

#: how many sessions may be cloning/indexing at once. Creation used to take no
#: slot at all, so any number of clones + GT indexes could start together
#: (HAR-84 G-21).
#:
#: HAR-84 (external agents): this was 3 while ``MAX_TASKS_PER_SPAWN`` was 4 and
#: a spawn takes its creation slots all-or-nothing — so a **full** four-worker
#: spawn was refused 429 every single time, on a stock deployment, before any
#: work started. The default now covers one whole spawn by construction.
DEFAULT_MAX_CONCURRENT_CREATIONS = MAX_TASKS_PER_SPAWN
#: seconds a creation-time model preflight may take before it is a failure
MODEL_PREFLIGHT_TIMEOUT = 30
#: how long one workspace measurement may take before the quota check starts
#: skipping commands (a huge tree must not slow the agent down)
QUOTA_MEASURE_BUDGET_SECONDS = 2.0
#: how many commands the check then skips between measurements
QUOTA_CHECK_EVERY = 10
#: how long one model call may take, so litellm cannot retry a dead model for
#: four minutes behind a user who is watching a spinner (HAR-84 G-11/G-14)
DEFAULT_MODEL_REQUEST_TIMEOUT = 300

#: ``Session.role`` values
PRIMARY_ROLE = "primary"
WORKER_ROLE = "worker"
#: an agent we do NOT run: a local Claude Code / Codex session (or one of
#: their subagents) that registers itself and pushes its own events at us.
#: Nothing is ever executed for one — no workspace, no sandbox, no model call,
#: and, crucially, no concurrency slot.
EXTERNAL_ROLE = "external"
#: how many live workers one session may have at a time
DEFAULT_MAX_WORKERS_PER_SESSION = 4
#: how many live EXTERNAL agents one session may have. A separate ceiling from
#: the worker one on purpose: a worker costs a clone, a container and a turn
#: slot, so four is generous; an external agent costs a row, and one Claude
#: Code session with a dozen subagents is the case this feature exists for.
#: It is still a ceiling, because the ingest token can now create children and
#: a leaked one must not be able to fan out without bound.
DEFAULT_MAX_EXTERNAL_AGENTS_PER_SESSION = 32
#: how deep the external agent tree may go below a root agent. Two, matching
#: what the fleet list renders: a root, its subagents, and theirs. A chain
#: nobody can see is not a feature, it is a leak with a nice name.
#: how many WORKER turns may run at once, across the whole deployment.
#:
#: Worker turns used to draw from ``MAX_CONCURRENT_SESSIONS`` (default 3), the
#: same pool as the humans' own turns, so a parent spawning its maximum of
#: four workers could not get four turns and ``spawn_agents`` refused the set
#: outright. Workers now have their own pool, sized to a full spawn, so
#: "spawn four" means four agents thinking at the same time rather than a
#: queue. The turns themselves were never serialised — each runs on its own
#: thread via ``asyncio.to_thread`` — the *admission counter* was the whole
#: limit.
DEFAULT_MAX_CONCURRENT_WORKER_TURNS = 4
MAX_EXTERNAL_AGENT_DEPTH = 2
#: how many ingest events one external agent may push per minute. Over it,
#: the surplus is DROPPED and the response says how many were taken: a 500 (or
#: a 429) would make a chatty adapter retry the whole batch and cost more.
DEFAULT_MAX_INGEST_EVENTS_PER_MINUTE = 600
INGEST_WINDOW_SECONDS = 60.0
#: largest ``POST .../events`` body, before parsing
MAX_INGEST_BODY_BYTES = 256 * 1024
#: how much of an external agent's assistant text the row's ``last_message``
#: keeps
EXTERNAL_LAST_MESSAGE_CHARS = 400
#: control characters never belong in a display label or a path
_CONTROL_CHARS = frozenset(chr(c) for c in [*range(0x20), 0x7F])
#: a path that starts like ``C:\`` or ``C:/`` — absolute, on the client's box
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
#: worker frames copied onto the parent's stream, with ``agent_id`` added, so
#: the parent's graph can draw every trail from one subscription
MIRRORED_EVENT_TYPES = frozenset({
    "assistant", "tool_call", "tool_result", "gt_action",
    "turn_started", "turn_finished",
})
#: how much of a worker's reply the stored report keeps
REPORT_EXCERPT_CHARS = 400
#: what a worker says when its opening turn could not get a concurrency slot
FIRST_TURN_BUSY_NOTE = (
    "The opening turn could not start ({reason}). The task is in the "
    "transcript; send a message to run it."
)


def max_workers_per_session() -> int:
    """Live workers one session may have (``MAX_WORKERS_PER_SESSION``)."""
    return _positive_int_env(
        "MAX_WORKERS_PER_SESSION", DEFAULT_MAX_WORKERS_PER_SESSION
    ) or DEFAULT_MAX_WORKERS_PER_SESSION


def max_external_agents_per_session() -> int:
    """Live external agents one session may have.

    ``MAX_EXTERNAL_AGENTS_PER_SESSION``; counted over agents that are not
    closed or failed, which is the number a token could actually create,
    because an ingest token cannot close anything.
    """
    return _positive_int_env(
        "MAX_EXTERNAL_AGENTS_PER_SESSION", DEFAULT_MAX_EXTERNAL_AGENTS_PER_SESSION
    ) or DEFAULT_MAX_EXTERNAL_AGENTS_PER_SESSION


def max_concurrent_worker_turns() -> int:
    """Worker turns that may run at once (``MAX_CONCURRENT_WORKER_TURNS``)."""
    return _positive_int_env(
        "MAX_CONCURRENT_WORKER_TURNS", DEFAULT_MAX_CONCURRENT_WORKER_TURNS
    ) or DEFAULT_MAX_CONCURRENT_WORKER_TURNS


def max_ingest_events_per_minute() -> int:
    """Ingest events one external agent may push per minute."""
    return _positive_int_env(
        "MAX_INGEST_EVENTS_PER_MINUTE", DEFAULT_MAX_INGEST_EVENTS_PER_MINUTE
    ) or DEFAULT_MAX_INGEST_EVENTS_PER_MINUTE


class ModelUnavailable(RuntimeError):
    """The configured provider will not serve this model (a 400 at creation)."""


def model_preflight_enabled() -> bool:
    """Whether a session creation checks the model against the provider."""
    return os.environ.get("MODEL_PREFLIGHT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "",
    }


def model_request_timeout() -> int:
    return _positive_int_env(
        "MODEL_REQUEST_TIMEOUT", DEFAULT_MODEL_REQUEST_TIMEOUT
    ) or DEFAULT_MODEL_REQUEST_TIMEOUT


def resolve_model(model: str, temperature: float = 0.0) -> tuple[str, dict[str, Any]]:
    """The LiteLLM model name and kwargs a session uses, in one place.

    ``_build_agent`` and the creation preflight must agree, or the preflight
    proves nothing about the route the turn will take.
    """
    model_kwargs: dict[str, Any] = {
        "temperature": temperature,
        # both halves: litellm retries at its own layer (num_retries) AND at
        # the provider client's (max_retries). Only setting one left a bad
        # model retrying 11 times with a 60 s backoff (HAR-84 G-11).
        "num_retries": 0,
        "max_retries": 0,
        "timeout": model_request_timeout(),
    }
    model_name = model
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        if not model_name.startswith("openai/"):
            model_name = f"openai/{model_name}"
        model_kwargs["api_base"] = base_url
    return model_name, model_kwargs


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
    #: commands run since the workspace was last measured for the quota
    since_quota_check: int = 0
    #: 1 = measure after every command; raised only when measuring is slow
    quota_stride: int = 1
    #: last file-relation graph, keyed by its tree signature (see ``graph``)
    graph_cache: tuple[str, dict] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: held for microseconds around "is this session running?" (post_message)
    #: and around "flip to idle, then drain" (the turn worker), so a message
    #: can never be queued as steering nothing will drain (HAR-84 G-15)
    steer_lock: threading.Lock = field(default_factory=threading.Lock)
    turn_done: threading.Event = field(default_factory=threading.Event)
    closed: bool = False
    pending_stop: bool = False
    creation_task: Any = None
    #: the session that spawned this one (workers only). Set from the row
    #: wherever one is read, and the reason a worker's frames reach the
    #: parent's stream at all — see ``_mirror``.
    parent_id: str | None = None
    #: opening message that starts its own first turn once the workspace is
    #: idle: a worker's task, or ``SessionCreate.first_message``
    first_message: str | None = None
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
        #: worker turns have their own pool, so a full spawn genuinely runs at
        #: once instead of queueing behind the humans' own turns
        self._running_worker_count = 0
        self._max_worker_turns = max_concurrent_worker_turns()
        #: per external agent: the monotonic timestamps of the ingest events
        #: taken in the last minute (the rate window)
        self._ingest_window: dict[str, deque[float]] = {}
        self._ingest_lock = threading.Lock()
        self._creating_count = 0
        self._max_creations = _positive_int_env(
            "MAX_CONCURRENT_CREATIONS", DEFAULT_MAX_CONCURRENT_CREATIONS
        ) or DEFAULT_MAX_CONCURRENT_CREATIONS
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
        """Turns of PRIMARY sessions running now (the humans' own pool)."""
        with self._count_lock:
            return self._running_count

    @property
    def running_worker_count(self) -> int:
        """Worker turns running now. Its own pool — see the constant."""
        with self._count_lock:
            return self._running_worker_count

    def _acquire_slot(self, role: str = PRIMARY_ROLE) -> bool:
        """Take a turn slot from the pool this role draws on.

        An EXTERNAL agent never gets here: it runs nothing, so it takes
        nothing. Its events are pushed at us, not executed by us.
        """
        with self._count_lock:
            if role == WORKER_ROLE:
                if self._running_worker_count >= self._max_worker_turns:
                    return False
                self._running_worker_count += 1
                return True
            if self._running_count >= self._max_concurrent:
                return False
            self._running_count += 1
            return True

    def _release_slot(self, role: str = PRIMARY_ROLE) -> None:
        with self._count_lock:
            if role == WORKER_ROLE:
                self._running_worker_count = max(
                    0, self._running_worker_count - 1
                )
                return
            self._running_count = max(0, self._running_count - 1)

    @property
    def creating_count(self) -> int:
        with self._count_lock:
            return self._creating_count

    def _acquire_creation_slot(self) -> bool:
        return self._acquire_creation_slots(1)

    def _acquire_creation_slots(self, count: int) -> bool:
        """Take ``count`` creation slots, or none at all.

        All-or-nothing because a spawn of four workers that half succeeds is
        worse than one that is refused: the caller asked for a set.
        """
        with self._count_lock:
            if self._creating_count + count > self._max_creations:
                return False
            self._creating_count += count
            return True

    def _release_creation_slot(self) -> None:
        self._release_creation_slots(1)

    def _release_creation_slots(self, count: int) -> None:
        if count <= 0:
            return
        with self._count_lock:
            self._creating_count = max(0, self._creating_count - count)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def max_worker_turns(self) -> int:
        return self._max_worker_turns

    # -- model preflight ------------------------------------------------------

    async def check_model(self, model: str) -> None:
        """Prove the provider will serve ``model`` before a session is built.

        An unusable model used to buy a clone, a sandbox and a GT index, and
        only failed four minutes into the first turn (HAR-84 G-11). One
        1-token completion over the same LiteLLM route settles it in a second.
        ``MODEL_PREFLIGHT=0`` turns it off (tests, air-gapped runs).
        """
        if not model.strip():
            raise ModelUnavailable("model must not be blank")
        if not model_preflight_enabled():
            return
        await asyncio.to_thread(_preflight_blocking, model)

    # -- workspace creation ---------------------------------------------------

    async def create_workspace(
        self,
        session_id: str,
        *,
        first_message: str | None = None,
        reserved: bool = False,
    ) -> None:
        """Clone the repo (and build the GT index) in the background.

        ``first_message`` makes creation and the first turn one action: the
        turn starts by itself the moment the workspace is ``idle``, so a
        caller never has to poll for it. ``reserved`` says the creation slot
        was already taken by the caller (a spawn takes one per worker, all or
        nothing, before any row exists).
        """
        session = await self._store.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        if not reserved and not self._acquire_creation_slot():
            raise ConcurrencyLimit(
                f"max concurrent session creations ({self._max_creations}) reached"
            )
        state = self._bind(session)
        state.first_message = first_message
        await self._bus.publish(
            session_id, {"type": "lifecycle", "data": {"status": "creating"}}
        )
        loop = asyncio.get_running_loop()
        state.creation_task = asyncio.ensure_future(
            asyncio.to_thread(self._create_blocking, dict(session), loop)
        )

    def _bind(self, session: dict) -> _SessionState:
        """Cache the row's parentage on the in-memory state and return it.

        Every path that reads a session row calls this, because ``_publish``
        runs on a worker thread and cannot go to the store to ask whether the
        frame it is about to emit belongs to a worker.
        """
        state = self._state(str(session["id"]))
        state.parent_id = str(session.get("parent_id") or "") or None
        return state

    def _create_blocking(self, session: dict, loop: asyncio.AbstractEventLoop) -> None:
        session_id = str(session["id"])
        state = self._state(session_id)
        workspace = workspace_path(session_id)
        try:
            # Before the clone: a session that cannot fit is a clean failure,
            # not a host with no disk left for anybody (HAR-84 G-07).
            ensure_free_space()
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
            opening, state.first_message = state.first_message, None
            if opening:
                self._call_quietly(
                    loop, self.start_first_turn(session_id, opening)
                )
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
        finally:
            self._release_creation_slot()

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

    async def start_first_turn(self, session_id: str, content: str) -> None:
        """Run an opening message as the session's own first turn.

        A refused slot is not a lost task: the message is written to the
        transcript anyway and a system note says the turn has to be asked for.
        """
        try:
            await self.post_message(session_id, content)
            return
        except ConcurrencyLimit as exc:
            note = FIRST_TURN_BUSY_NOTE.format(reason=exc)
        except Exception as exc:  # noqa: BLE001 - the session stays usable
            note = FIRST_TURN_BUSY_NOTE.format(reason=f"{type(exc).__name__}: {exc}")
        with contextlib.suppress(Exception):
            await self._store.add_message(session_id, role="user", content=content)
            message = await self._store.add_message(
                session_id, role="system", content=note
            )
            await self._bus.publish(session_id, {
                "type": "system_note",
                "data": {"message_id": message["id"], "content": note},
            })

    async def post_message(self, session_id: str, content: str) -> tuple[dict, str]:
        """Deliver a user message: start a turn, or steer the running one."""
        row = await self._store.get_session(session_id)
        if row is not None and str(row.get("role") or PRIMARY_ROLE) == EXTERNAL_ROLE:
            # Checked before the steering branch, not after: an external agent
            # is permanently ``running``, so it would otherwise swallow the
            # message as steering for a turn that does not exist.
            raise ExternalAgentRefused(
                "an external agent does not run turns; push events instead"
            )
        state = self._state(session_id)
        # The status read and the steering hand-off are one atomic step, and
        # the turn worker takes the same lock *after* it has flipped the row to
        # idle. Either this sees `running` and the worker's post-flip drain
        # picks the message up, or it sees `idle` and starts its own turn —
        # there is no ordering in which the message is lost (HAR-84 G-15).
        with state.steer_lock:
            session = await self._store.get_session(session_id)
            if session is None:
                raise ValueError(f"session {session_id} not found")
            self._bind(session)

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

        role = str(session.get("role") or PRIMARY_ROLE)
        if not self._acquire_slot(role):
            cap = (
                self._max_worker_turns
                if role == WORKER_ROLE
                else self._max_concurrent
            )
            raise ConcurrencyLimit(
                f"max concurrent {role} turns ({cap}) reached"
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
            await self._publish_async(session_id, {
                "type": "turn_started",
                # `content` and `role` so every subscriber can render the
                # prompt. With only `message_id`, a second tab showed the turn
                # and the reply but never the question (HAR-84 G-09).
                "data": {
                    "turn_id": turn_id,
                    "message_id": message["id"],
                    "role": "user",
                    "content": content,
                },
            })
        except Exception:
            self._release_slot(role)
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
                    if not pending and not state.closed:
                        pending = self._settle_idle(loop, session_id, state, agent)
                        if not pending:
                            return
                    if not pending or state.closed:
                        break
                    turn_id, user_text = self._chain_turn(session, pending, loop)
        except Exception as exc:  # noqa: BLE001 - a crash ends the TURN, not the session
            self._fail_turn(session, state, turn_id, exc, loop)
        finally:
            self._release_slot(str(session.get("role") or PRIMARY_ROLE))
            state.turn_done.set()

    def _settle_idle(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        state: _SessionState,
        agent: ConversationalAgent,
    ) -> list[Steering]:
        """Flip the session back to ``idle``, then drain once more.

        The order matters. Between the last drain and the status flip the row
        still said ``running``, so a message landing in that window was queued
        as steering that nothing would ever read. Draining *after* the flip,
        under the lock ``post_message`` also takes, closes it (HAR-84 G-15).
        """
        self._set_status(loop, session_id, "idle", current_turn_id=None)
        with state.steer_lock:
            pending = agent.take_pending_steering()
        if not pending:
            self._emit(loop, session_id, "lifecycle", {"status": "idle"})
            return []
        # Somebody spoke into the window: go back to running, chain a turn.
        self._set_status(loop, session_id, "running")
        return pending

    def _fail_turn(
        self,
        session: dict,
        state: _SessionState,
        turn_id: str,
        exc: BaseException,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """End the TURN in ``error`` and hand the session back, still usable.

        Only a failed *workspace creation* writes off a session. A provider
        blip, a sandbox that had to be restarted or a bug in one turn used to
        take the whole conversation with it, unrecoverably (HAR-84 G-04).
        """
        session_id = str(session["id"])
        error = f"{type(exc).__name__}: {exc}"
        agent = state.agent
        if agent is None or agent.last_error_turn_id != turn_id:
            # The agent already reported failures it saw itself; this covers
            # everything around it (workspace, agent build, bookkeeping).
            self._emit(
                loop, session_id, "agent_error", {"turn_id": turn_id, "error": error}
            )
        result = TurnResult(
            finish_reason="error",
            reply=TURN_ERROR_REPLY.format(reason=_short_error(exc)),
            n_calls=0,
            cost=0.0,
            # the GT work this turn really did still belongs on its receipt,
            # even though the turn ended in an exception
            gt_actions=getattr(agent, "gt_actions", 0) if agent else 0,
            gt_exact_matches=(
                getattr(agent, "gt_exact_matches", 0) if agent else 0
            ),
        )
        try:
            self._finish_turn(session, state, turn_id, result, loop)
        except Exception:  # noqa: BLE001 - the receipt still has to close
            log.exception("could not record the failed turn %s", turn_id)
            self._call_quietly(loop, self._store.finish_turn(
                turn_id, n_calls=0, cost=0.0, finish_reason="error",
                patch_sha256=None,
            ))
        if state.closed:
            return
        self._set_status(loop, session_id, "idle", current_turn_id=None)
        self._emit(loop, session_id, "lifecycle", {"status": "idle"})

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
            "turn_id": turn_id,
            "message_id": pending[-1].message_id,
            "role": "user",
            "content": text,
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
            gt_actions=result.gt_actions,
            gt_exact_matches=result.gt_exact_matches,
        ))
        self._call(loop, self._store.bump_totals(
            session_id,
            turns=1,
            steps=result.n_calls,
            cost=result.cost,
            wall_seconds=result.wall_seconds,
            gt_actions=result.gt_actions,
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
        if str(session.get("role") or PRIMARY_ROLE) == WORKER_ROLE:
            self._report_to_parent(
                session, result, reply, patch_sha256, files_changed, loop
            )

    # -- worker agents --------------------------------------------------------

    def _report_to_parent(
        self,
        session: dict,
        result: TurnResult,
        reply: str,
        patch_sha256: str | None,
        files_changed: list[str],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Hand a finished worker turn back to the session that spawned it.

        Three places, because a stream is not a record: the report goes onto
        the worker's row (so a list view has it), into the parent's
        ``messages`` (so a reload still shows it) and onto the parent's stream
        as ``agent_report`` (so a client watching sees it happen).
        """
        worker_id = str(session["id"])
        parent_id = str(session.get("parent_id") or "")
        if not parent_id:
            return
        row = self._call_quietly(loop, self._store.get_session(worker_id)) or {}
        report = {
            "finish_reason": result.finish_reason,
            "reply_excerpt": reply[:REPORT_EXCERPT_CHARS],
            "patch_sha256": patch_sha256,
            "files_changed": files_changed,
            "applied": bool(row.get("applied_at")),
        }
        self._call_quietly(loop, self._store.update_session(
            worker_id, report_json=json.dumps(report)
        ))
        message = self._call_quietly(loop, self._store.add_message(
            parent_id,
            role="agent",
            content=reply,
            meta={
                "agent_id": worker_id,
                "finish_reason": result.finish_reason,
                "patch_sha256": patch_sha256,
                "files_changed": files_changed,
            },
        )) or {}
        self._call_quietly(loop, self._bus.publish(parent_id, {
            "type": "agent_report",
            "data": {
                "worker_id": worker_id,
                "message_id": message.get("id"),
                "finish_reason": result.finish_reason,
                "content": reply,
                "patch_sha256": patch_sha256,
                "files_changed": files_changed,
                "n_calls": result.n_calls,
                "cost": result.cost,
            },
        }))

    async def spawn_agents(
        self,
        parent: dict,
        tasks: list[str],
        *,
        model: str | None = None,
        gt_mode: str | None = None,
    ) -> list[dict]:
        """Spawn one worker session per task. All of them, or none.

        A worker is a child session: the parent's repo, ref, model, GT mode and
        per-session knobs, its own workspace, sandbox and transcript, and the
        task as an opening message that runs by itself once the clone is done.

        The caller (the route) has already checked that ``parent`` is a primary
        session in a state that can spawn.
        """
        parent_id = str(parent["id"])
        model = (model or str(parent["model"])).strip()
        gt_mode = gt_mode or str(parent["gt_mode"])
        if model != str(parent["model"]):
            await self.check_model(model)

        live = [
            child for child in await self._store.list_children(parent_id)
            # external agents are children too, and they run nothing: they
            # must not spend the worker budget of a session that does
            if str(child.get("role") or WORKER_ROLE) == WORKER_ROLE
            and str(child["status"]) not in {"closed", "failed"}
        ]
        cap = max_workers_per_session()
        if len(live) + len(tasks) > cap:
            raise ConcurrencyLimit(
                f"a session may have at most {cap} live workers "
                f"(MAX_WORKERS_PER_SESSION); {len(live)} already running and "
                f"{len(tasks)} more were asked for"
            )
        if not self._acquire_creation_slots(len(tasks)):
            raise ConcurrencyLimit(
                f"max concurrent session creations ({self._max_creations}) "
                f"reached; {len(tasks)} workers need one each"
            )
        # A worker whose clone finishes with no turn slot free would sit idle
        # holding a workspace, which is not what "spawn" means. Check the turn
        # budget here, where the whole set can still be refused — against the
        # WORKER pool, so a spawn is not rationed by how many humans happen to
        # be mid-turn.
        if self.running_worker_count + len(tasks) > self._max_worker_turns:
            self._release_creation_slots(len(tasks))
            raise ConcurrencyLimit(
                f"max concurrent worker turns ({self._max_worker_turns}) "
                f"reached; {len(tasks)} workers need one each"
            )

        config = _session_config(parent)
        workers: list[dict] = []
        #: creations that took over one of the reserved slots; the rest are
        #: given back below, and only ``_create_blocking`` releases these
        started = 0
        try:
            for task in tasks:
                worker_id = await self._store.create_session(
                    repo=str(parent["repo"]),
                    ref=str(parent["ref"]),
                    model=model,
                    gt_mode=gt_mode,
                    config=config,
                    parent_id=parent_id,
                    role=WORKER_ROLE,
                    task=task,
                )
                self._state(worker_id).parent_id = parent_id
                await self._bus.publish(parent_id, {
                    "type": "agent_spawned",
                    "data": {"worker_id": worker_id, "task": task},
                })
                await self.create_workspace(
                    worker_id, first_message=task, reserved=True
                )
                started += 1
                row = await self._store.get_session(worker_id)
                if row is not None:
                    workers.append(row)
        finally:
            self._release_creation_slots(len(tasks) - started)
        return workers

    async def spawn_from_chat(
        self, parent: dict, tasks: list[str], content: str
    ) -> dict:
        """``/spawn`` in the chat box: the API call, answered with a note.

        The user's own message is recorded (so the transcript says what was
        asked) and the answer is a ``system_note``, not an agent turn — the
        message never reaches a model.
        """
        workers = await self.spawn_agents(parent, tasks)
        parent_id = str(parent["id"])
        await self._store.add_message(parent_id, role="user", content=content)
        lines = [f"Spawned {len(workers)} worker agent(s):"]
        lines += [
            f"- {worker['id']}: {worker['task']}" for worker in workers
        ]
        note = "\n".join(lines)
        message = await self._store.add_message(
            parent_id, role="system", content=note
        )
        await self._bus.publish(parent_id, {
            "type": "system_note",
            "data": {"message_id": message["id"], "content": note},
        })
        await self._store.update_session(parent_id, last_message=note)
        return message

    async def list_workers(self, parent_id: str) -> list[dict]:
        """Every session spawned by ``parent_id``, oldest first."""
        return await self._store.list_children(parent_id)

    async def apply_worker(self, parent: dict, worker: dict) -> dict:
        """Merge a worker's cumulative diff into the parent's workspace.

        All or nothing: on conflict :class:`ApplyConflict` names the paths and
        the parent's workspace is byte-for-byte what it was.
        """
        parent_id = str(parent["id"])
        worker_id = str(worker["id"])
        diff = await self.diff(worker)
        patch = str(diff.get("patch") or "")
        if not patch.strip():
            raise ApplyRefused(f"worker {worker_id} has no changes to apply")
        workspace = str(parent.get("workspace_path") or "")
        if not workspace or not Path(workspace).is_dir():
            raise ApplyRefused("the session has no workspace to apply into")

        applied, conflicts = await asyncio.to_thread(
            apply_patch, workspace, patch
        )
        if not applied:
            raise ApplyConflict(conflicts)

        files = [f["path"] for f in (diff.get("files") or [])]
        patch_sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        await self._store.update_session(
            worker_id, applied_at=time.time(), applied_sha256=patch_sha256
        )
        await self._mark_report_applied(worker_id)
        # The workspace changed under the cached graph, and its signature is
        # tree-shaped, so a stale entry cannot survive — but drop it anyway.
        self._state(parent_id).graph_cache = None
        note = f"applied worker {worker_id}: {len(files)} files"
        message = await self._store.add_message(
            parent_id, role="system", content=note
        )
        await self._bus.publish(parent_id, {
            "type": "system_note",
            "data": {"message_id": message["id"], "content": note},
        })
        await self._bus.publish(parent_id, {
            "type": "agent_applied",
            "data": {
                "worker_id": worker_id,
                "files": files,
                "patch_sha256": patch_sha256,
            },
        })
        return {
            "worker_id": worker_id,
            "files": files,
            "patch_sha256": patch_sha256,
        }

    async def _mark_report_applied(self, worker_id: str) -> None:
        """Flip ``applied`` on the worker's stored report, if it has one."""
        row = await self._store.get_session(worker_id)
        if row is None or not row.get("report_json"):
            return
        try:
            report = json.loads(str(row["report_json"]))
        except Exception:  # noqa: BLE001 - a corrupt report is not fatal
            return
        report["applied"] = True
        await self._store.update_session(
            worker_id, report_json=json.dumps(report)
        )

    async def _close_workers(self, parent_id: str, reason: str) -> None:
        """Close every live worker of a session that is going away."""
        try:
            children = await self._store.list_children(parent_id)
        except Exception:  # noqa: BLE001 - a store hiccup must not block a close
            log.exception("could not list the workers of %s", parent_id)
            return
        for child in children:
            if str(child["status"]) == "closed":
                continue
            try:
                await self.close(str(child["id"]), reason=reason)
            except Exception:  # noqa: BLE001 - one worker, not the close
                log.exception("could not close worker %s", child["id"])

    # -- external agents: agents we do not run --------------------------------

    async def register_external_agent(
        self,
        parent: dict,
        *,
        agent_kind: str,
        label: str,
        task: str | None = None,
        cwd: str | None = None,
        parent_agent_id: str | None = None,
    ) -> dict:
        """Register an agent we will never execute, as a child of ``parent``.

        This is the whole trick of the feature: an external agent is a WORKER
        WE DO NOT RUN. It gets the same row, the same ``agent_id``-tagged
        frames on the parent's stream and the same card in the UI — but no
        workspace, no clone, no sandbox, no model call and no concurrency
        slot, because the only thing we ever do for it is receive.
        """
        parent_id = str(parent["id"])
        nested = (parent_agent_id or "").strip() or None
        if nested is not None:
            owner = await self._store.get_session(nested)
            if (
                owner is None
                or str(owner.get("role") or "") != EXTERNAL_ROLE
                or str(owner.get("parent_id") or "") != parent_id
            ):
                raise ExternalAgentRefused(
                    "parent_agent_id must name an external agent of this session"
                )
            depth = await self._external_depth(owner) + 1
            if depth > MAX_EXTERNAL_AGENT_DEPTH:
                raise ExternalAgentLimit(
                    f"an external agent may nest at most "
                    f"{MAX_EXTERNAL_AGENT_DEPTH} levels below its root"
                )
        cap = max_external_agents_per_session()
        live = [
            child for child in await self._store.list_children(parent_id)
            if str(child.get("role") or "") == EXTERNAL_ROLE
            and str(child["status"]) not in {"closed", "failed"}
        ]
        if len(live) >= cap:
            raise ExternalAgentLimit(
                f"a session may have at most {cap} live external agents "
                f"(MAX_EXTERNAL_AGENTS_PER_SESSION)"
            )
        agent_id = await self._store.create_session(
            repo=str(parent["repo"]),
            ref=str(parent["ref"]),
            # There is no model: we are not calling one. An empty string is
            # the honest answer; borrowing the parent's would be a claim.
            model="",
            gt_mode="off",
            config={},
            parent_id=parent_id,
            role=EXTERNAL_ROLE,
            task=task,
            # born running: there is nothing to create, so there is no
            # `creating` phase for it to sit in
            status="running",
            agent_kind=agent_kind,
            parent_agent_id=nested,
            external_cwd=_clean_cwd(cwd),
            label=label,
        )
        self._state(agent_id).parent_id = parent_id
        await self._bus.publish(parent_id, {
            "type": "agent_spawned",
            "data": {
                "worker_id": agent_id,
                "agent_kind": agent_kind,
                "label": label,
                "task": task,
                "external": True,
                "parent_agent_id": nested,
            },
        })
        row = await self._store.get_session(agent_id)
        if row is None:  # pragma: no cover - the insert just succeeded
            raise ExternalAgentRefused("the agent row disappeared")
        return row

    async def _external_depth(self, agent: dict) -> int:
        """How far below its root ``agent`` sits. A root is 0.

        The walk is bounded rather than trusting the chain to be a tree: the
        rows are written by this server, but a bounded walk costs nothing and
        a cycle would otherwise hang a request instead of refusing it.
        """
        depth = 0
        current = agent
        for _ in range(MAX_EXTERNAL_AGENT_DEPTH + 1):
            nested = str(current.get("parent_agent_id") or "")
            if not nested:
                return depth
            row = await self._store.get_session(nested)
            if row is None:
                return depth
            depth += 1
            current = row
        # deeper than we are willing to walk: deep enough to refuse
        return depth + 1

    async def ingest_external_events(self, agent: dict, events: list) -> int:
        """Mirror an external agent's own events onto its parent's stream.

        Everything here is *translation*: each accepted event becomes one of
        the frames a worker already publishes, with ``agent_id`` set, so the
        browser's worker code path draws an external agent without knowing
        that anything is different about it.

        The text is DATA. It is stored and forwarded, never interpreted: no
        model is called with it and no command is derived from it.
        """
        agent_id = str(agent["id"])
        parent_id = str(agent.get("parent_id") or "")
        allowed = self._take_ingest_budget(agent_id, len(events))
        accepted = list(events)[:allowed]
        if not accepted:
            return 0
        self._bind(agent)
        cwd = str(agent.get("external_cwd") or "")
        step = int(agent.get("steps") or 0)
        tool_calls = 0
        last_text = ""
        #: the fleet list's two live columns, folded over the whole batch:
        #: the LAST activity line wins, the HIGHEST token count wins
        activity = ""
        tokens: int | None = None
        for event in accepted:
            kind = str(getattr(event, "type", ""))
            reported = str(getattr(event, "activity", "") or "").strip()
            if reported:
                activity = reported
            if kind == "tool_call":
                step += 1
                tool_calls += 1
            if kind == "status":
                note = _external_status_note(event)
                if note:
                    last_text = note
                claimed = getattr(event, "tokens", None)
                if claimed is not None:
                    tokens = max(tokens or 0, int(claimed))
                if str(getattr(event, "state", "")) == "error":
                    # `agent_activity` is not a frame type the UI knows, and
                    # inventing one would need UI work for a line of text. An
                    # error is the only state worth interrupting for.
                    await self._publish_external_note(agent_id, parent_id, note)
                continue
            frame = _external_frame(event, cwd=cwd, step=step)
            if frame is None:
                continue
            if kind == "assistant":
                last_text = str(frame["data"].get("content") or "") or last_text
            # the mirror path, verbatim: the agent's own stream first, then
            # the parent's copy with `agent_id` attached
            await self._publish_async(agent_id, frame)
        await self._touch_external(
            agent_id,
            steps=tool_calls,
            text=last_text,
            activity=activity,
            tokens=tokens,
            stored_tokens=agent.get("tokens"),
        )
        return len(accepted)

    async def finish_external_agent(
        self, agent: dict, status: str, summary: str | None
    ) -> dict:
        """Close an external agent out the way a worker's finished turn does.

        Same three places as ``_report_to_parent``: the row (so a list view
        has it), the parent's ``messages`` (so a reload still shows it) and
        the parent's stream (so a client watching sees it happen).
        """
        agent_id = str(agent["id"])
        parent_id = str(agent.get("parent_id") or "")
        new_status = "idle" if status == "done" else "failed"
        # The frames a worker publishes carry a ``FinishReason``, and every
        # consumer of them (the message meta, the worker card) validates
        # against that vocabulary — so the outcome is said in it, and the
        # external-only word goes in its own ``status`` field beside it.
        finish_reason = "reply" if status == "done" else "error"
        excerpt = (summary or "")[:REPORT_EXCERPT_CHARS]
        report = {
            "finish_reason": finish_reason,
            "reply_excerpt": excerpt,
            "patch_sha256": None,
            "files_changed": [],
            "applied": False,
        }
        fields: dict[str, Any] = {"report_json": json.dumps(report)}
        if excerpt:
            fields["last_message"] = excerpt
        activity = str(agent.get("activity") or "") or None
        tokens = agent.get("tokens")
        try:
            await self._store.update_status(agent_id, new_status, **fields)
        except ValueError:
            # Finishing twice (or finishing an agent that already failed) is
            # bookkeeping, not an error the adapter can do anything about.
            await self._store.update_session(agent_id, **fields)
        if parent_id:
            message = None
            if excerpt:
                message = await self._store.add_message(
                    parent_id,
                    role="agent",
                    content=excerpt,
                    meta={
                        "agent_id": agent_id,
                        "finish_reason": finish_reason,
                        "files_changed": [],
                    },
                )
            await self._bus.publish(parent_id, {
                "type": "agent_report",
                "data": {
                    "worker_id": agent_id,
                    "message_id": (message or {}).get("id"),
                    "finish_reason": finish_reason,
                    #: the external agent's own word for how it ended
                    "status": status,
                    "content": excerpt,
                    "patch_sha256": None,
                    "files_changed": [],
                    "n_calls": 0,
                    "cost": 0.0,
                    "external": True,
                    # so a reload still shows the last live state of the row
                    "activity": activity,
                    "tokens": int(tokens) if tokens is not None else None,
                },
            })
            await self._bus.publish(parent_id, {
                "type": "agent_closed",
                "data": {
                    "worker_id": agent_id,
                    "reason": status,
                    "external": True,
                },
            })
        row = await self._store.get_session(agent_id) or dict(agent)
        return row

    async def _publish_external_note(
        self, agent_id: str, parent_id: str, note: str
    ) -> None:
        """One ``system_note`` about an external agent, on both streams."""
        await self._bus.publish(
            agent_id, {"type": "system_note", "data": {"content": note}}
        )
        if parent_id:
            await self._bus.publish(parent_id, {
                "type": "system_note",
                "data": {"agent_id": agent_id, "content": note},
            })

    async def _touch_external(
        self,
        agent_id: str,
        *,
        steps: int,
        text: str,
        activity: str = "",
        tokens: int | None = None,
        stored_tokens: Any = None,
    ) -> None:
        """Keep the row's live columns honest: one write per ingest batch.

        ``updated_at`` moves on every batch (the UI measures idleness from
        it), the activity line is replaced by the newest one the batch
        carried, and the token counter only ever goes UP — a client that
        restarts its own counter must not make the fleet list count down.
        """
        fields: dict[str, Any] = {}
        if text:
            fields["last_message"] = text[:EXTERNAL_LAST_MESSAGE_CHARS]
        if activity:
            fields["activity"] = activity[:MAX_ACTIVITY_CHARS]
        if tokens is not None:
            previous = int(stored_tokens or 0)
            if tokens > previous:
                fields["tokens"] = tokens
        with contextlib.suppress(Exception):
            if steps:
                await self._store.bump_totals(agent_id, steps=steps)
            if fields:
                await self._store.update_session(agent_id, **fields)
            elif not steps:
                await self._store.touch(agent_id)

    def _take_ingest_budget(self, agent_id: str, count: int) -> int:
        """How many of ``count`` events this agent may push right now.

        A sliding one-minute window per agent. Over the limit the surplus is
        dropped and the response says how many were taken, because a 429 on a
        batch makes a chatty adapter retry the whole thing and cost more than
        it did the first time.
        """
        limit = max_ingest_events_per_minute()
        now = time.monotonic()
        with self._ingest_lock:
            window = self._ingest_window.setdefault(agent_id, deque())
            cutoff = now - INGEST_WINDOW_SECONDS
            while window and window[0] < cutoff:
                window.popleft()
            allowed = max(0, min(count, limit - len(window)))
            window.extend([now] * allowed)
            return allowed

    async def _close_external(self, session: dict, reason: str) -> None:
        """Close an external agent: a row and a frame, nothing to kill.

        There is no container and no workspace — the agent runs on somebody
        else's machine — so closing one is exactly the bookkeeping half of
        ``close()`` and none of the teardown.
        """
        session_id = str(session["id"])
        parent_id = str(session.get("parent_id") or "")
        state = self._state(session_id)
        state.closed = True
        with self._states_lock:
            self._states.pop(session_id, None)
        with self._ingest_lock:
            self._ingest_window.pop(session_id, None)
        if str(session["status"]) != "closed":
            await self._store.update_status(
                session_id, "closed", current_turn_id=None, closed_reason=reason
            )
            if parent_id:
                await self._bus.publish(parent_id, {
                    "type": "agent_closed",
                    "data": {
                        "worker_id": session_id,
                        "reason": reason,
                        "external": True,
                    },
                })
            await self._bus.publish(
                session_id,
                {"type": "lifecycle", "data": {"status": "closed", "reason": reason}},
            )
        self._bus.finish(session_id)

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
        if str(session.get("role") or PRIMARY_ROLE) == EXTERNAL_ROLE:
            # No container, no workspace, no turn to wait for — an external
            # agent is a row and a stream, so closing one is only that.
            await self._close_external(session, reason)
            return
        # Workers first, while this session's stream is still open: each of
        # them publishes `agent_closed` onto it on the way out.
        await self._close_workers(session_id, reason)
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
            parent_id = str(session.get("parent_id") or "")
            if parent_id:
                await self._bus.publish(parent_id, {
                    "type": "agent_closed",
                    "data": {"worker_id": session_id, "reason": reason},
                })
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
            if str(session.get("role") or PRIMARY_ROLE) == EXTERNAL_ROLE:
                # Nothing of ours was interrupted: the agent is somebody
                # else's process and is very likely still going.
                continue
            await self._recover_running(session)
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

    async def _recover_running(self, session: dict) -> None:
        """Close out a turn the restart interrupted, on the wire as well.

        The row used to go back to ``idle`` with a system message nobody was
        told about: the ``turn_started`` frame had no matching
        ``turn_finished``, the receipt kept ``finish_reason ""`` and
        ``finished_at null`` forever, and a live client showed the turn as
        still running (HAR-84 G-08 / E-02 / E-03).
        """
        session_id = str(session["id"])
        # so an interrupted WORKER turn still closes out on its parent's
        # stream: the in-memory state is empty after a restart
        self._bind(session)
        turn_id = session.get("current_turn_id")
        await self._store.update_status(session_id, "idle", current_turn_id=None)
        message = await self._store.add_message(
            session_id, role="system", content=RESTART_NOTICE, turn_id=turn_id
        )
        if turn_id:
            receipt = await self._interrupted_receipt(session_id, str(turn_id))
            await self._store.finish_turn(
                str(turn_id),
                n_calls=int(receipt.get("n_calls") or 0),
                cost=float(receipt.get("cost") or 0.0),
                finish_reason="interrupted",
                patch_sha256=receipt.get("patch_sha256"),
                wall_seconds=float(receipt.get("wall_seconds") or 0.0),
            )
            await self._publish_async(session_id, {
                "type": "turn_finished",
                "data": {
                    "turn_id": turn_id,
                    "finish_reason": "interrupted",
                    "n_calls": int(receipt.get("n_calls") or 0),
                    "cost": float(receipt.get("cost") or 0.0),
                },
            })
        await self._bus.publish(session_id, {
            "type": "system_note",
            "data": {
                "turn_id": turn_id,
                "message_id": message["id"],
                "content": RESTART_NOTICE,
            },
        })
        await self._bus.publish(
            session_id, {"type": "lifecycle", "data": {"status": "idle"}}
        )

    async def _interrupted_receipt(self, session_id: str, turn_id: str) -> dict:
        """Whatever the interrupted turn had recorded before the lights went out."""
        try:
            for turn in await self._store.list_turns(session_id):
                if str(turn.get("turn_id")) == turn_id:
                    return turn
        except Exception:  # noqa: BLE001 - recovery never fails on bookkeeping
            log.exception("could not read the receipt for turn %s", turn_id)
        return {}

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
            # Re-check the jail on every turn, not only when the agent is
            # built: a container can stop *between* turns (a daemon restart, an
            # operator, an OOM of pid 1) and the cached agent would otherwise
            # drive a container that is not there (HAR-84 G-04).
            state.sandbox = self._ensure_sandbox(session, state, loop)
            return state.agent
        workspace = state.workspace or str(session.get("workspace_path") or "")
        if not workspace:
            raise RuntimeError("session has no workspace")
        state.workspace = workspace
        state.base_sha = state.base_sha or str(session.get("base_sha") or "")
        # After a restart the container is still there but `state` is fresh, so
        # the name is re-derived and checked rather than trusted. A container
        # the daemon stopped is started again rather than declared dead.
        sandbox = self._ensure_sandbox(session, state, loop)
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

    def _ensure_sandbox(
        self,
        session: dict,
        state: _SessionState,
        loop: asyncio.AbstractEventLoop,
    ) -> str | None:
        """This session's container, running. Started or rebuilt if it is not.

        ``ensure_running`` handles *stopped but present*. A container that is
        gone entirely is rebuilt on the same workspace — the clone and the
        transcript are still on disk, so there is nothing to write off.
        """
        if not is_docker_mode():
            return None
        session_id = str(session["id"])
        workspace = state.workspace or str(session.get("workspace_path") or "")
        try:
            return ensure_running(session_id, workspace)
        except SandboxError:
            if not workspace:
                raise
            container = self._start_sandbox(session_id, workspace, loop)
            self._emit(loop, session_id, "lifecycle", {
                "status": "sandbox_restarted", "container": container,
            })
            return container

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

        model_name, model_kwargs = resolve_model(model, temperature)

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
                # so a wedged container can be recreated in place, and the
                # session can say so on the wire (HAR-84 G-03)
                session_id=session_id,
                workspace=cwd,
                on_restart=lambda container: self._emit(
                    loop, session_id, "lifecycle",
                    {"status": "sandbox_restarted", "container": container},
                ),
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

            from .gt_events import install_gt_action_events

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
            # After the hooks, never before: the timer has to wrap GT's own
            # ``execute_actions`` replacement, which is what dispatches a typed
            # action (it never reaches ``env.execute``, so the environment
            # proxy that emits ``tool_call``/``tool_result`` never sees one).
            install_gt_action_events(agent)
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

    async def _publish_async(self, session_id: str, event: dict) -> dict:
        """Publish on the event loop, mirroring a worker frame to its parent."""
        published = await self._bus.publish(session_id, event)
        await self._mirror(session_id, published)
        return published

    async def _mirror(self, session_id: str, published: dict) -> None:
        """Copy a worker's frame onto its parent's stream, tagged ``agent_id``.

        The parent's stream is the only one the UI has to watch: every worker's
        trail arrives on it, and ``agent_id`` says whose it is. A frame from a
        primary session carries no ``agent_id`` at all.
        """
        if not isinstance(published, dict):
            return
        if published.get("type") not in MIRRORED_EVENT_TYPES:
            return
        parent_id = self._state(session_id).parent_id
        if not parent_id:
            return
        data = dict(published.get("data") or {})
        data["agent_id"] = session_id
        await self._bus.publish(parent_id, {
            "type": published["type"],
            "timestamp": published.get("timestamp"),
            "data": data,
        })

    def _publish(
        self, loop: asyncio.AbstractEventLoop, session_id: str, event: dict
    ) -> None:
        event.setdefault("timestamp", time.time())
        published = self._call_quietly(loop, self._bus.publish(session_id, event))
        if isinstance(published, dict):
            self._call_quietly(loop, self._mirror(session_id, published))
        if event.get("type") != "tool_result" or not isinstance(published, dict):
            return
        data = event.get("data") or {}
        state = self._state(session_id)
        state.since_quota_check += 1
        if state.since_quota_check >= state.quota_stride:
            state.since_quota_check = 0
            self._enforce_quota(loop, session_id)
        event_id = int(published.get("id") or 0)
        if event_id:
            self._snapshot_diff(loop, session_id, event_id, data)

    def _enforce_quota(
        self, loop: asyncio.AbstractEventLoop, session_id: str
    ) -> None:
        """Cut a turn off once its workspace is over ``SANDBOX_WORKSPACE_MAX_MB``.

        The workspaces directory is a bind mount of a shared filesystem, so
        without this one ``dd`` filled the host and took the product down
        (HAR-84 G-07). Measured on the turn worker after **every** command,
        not only write-shaped ones: ``dd if=/dev/zero of=big`` writes a
        gigabyte and matches none of the write verbs, and the audit's repro
        was a single-command turn, so any stride at all would have missed it.
        A measurement that overruns
        :data:`QUOTA_MEASURE_BUDGET_SECONDS` raises the stride to
        :data:`QUOTA_CHECK_EVERY` for the rest of the session, so a huge tree
        costs one ``du`` per ten commands instead of one per command.
        """
        cap = workspace_max_mb()
        state = self._state(session_id)
        workspace = state.workspace
        agent = state.agent
        if cap <= 0 or not workspace or agent is None or agent.turn_error:
            return
        started = time.monotonic()
        try:
            used = workspace_mb(workspace)
        except Exception:  # noqa: BLE001 - a failed measurement is not a verdict
            return
        if time.monotonic() - started > QUOTA_MEASURE_BUDGET_SECONDS:
            state.quota_stride = QUOTA_CHECK_EVERY
        if used <= cap:
            return
        reason = f"workspace quota exceeded ({used} MB > {cap} MB cap)"
        agent.fail_turn(reason)
        self._emit(
            loop, session_id, "lifecycle",
            {"status": "quota_exceeded", "reason": reason},
        )

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
    """Raised when a new turn or creation would exceed its concurrency cap."""


class ExternalAgentRefused(RuntimeError):
    """An external agent cannot be registered, or cannot be talked to."""


class ExternalAgentLimit(ExternalAgentRefused):
    """A session's external agent tree is as wide, or as deep, as it may be."""


class ApplyRefused(RuntimeError):
    """A worker's patch cannot be applied: there is none, or nowhere to put it."""


class ApplyConflict(RuntimeError):
    """A worker's patch does not merge into the parent workspace.

    ``conflicts`` names the paths git could not merge. The parent's workspace
    is exactly what it was before the attempt.
    """

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__("the worker's changes conflict with this workspace")
        self.conflicts = list(conflicts)


#: longest failure text quoted back to the user in a turn's error reply
_ERROR_REASON_CHARS = 300


def _short_error(exc: BaseException) -> str:
    """One bounded line describing a failure, for the user-facing reply."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text[:_ERROR_REASON_CHARS] or type(exc).__name__


def _preflight_blocking(model: str) -> None:
    """One 1-token completion over the session's own LiteLLM route."""
    model_name, model_kwargs = resolve_model(model)
    model_kwargs.pop("temperature", None)
    # A preflight the user is waiting on gets its own, much shorter, budget.
    model_kwargs["timeout"] = MODEL_PREFLIGHT_TIMEOUT
    try:
        import litellm

        litellm.completion(
            model=model_name,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            **model_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - any refusal is "not available"
        raise ModelUnavailable(_short_error(exc)) from exc


def _external_status_note(event: Any) -> str:
    """The one line a ``status`` event is worth on a stream."""
    state = str(getattr(event, "state", "") or "")
    note = str(getattr(event, "note", "") or "").strip()
    if not state:
        return note
    return f"agent {state}: {note}" if note else f"agent {state}"


def _external_frame(event: Any, *, cwd: str, step: int) -> dict | None:
    """Translate one ingest event into the frame a worker would publish.

    The frame types are the EXISTING ones — ``assistant``, ``tool_call``,
    ``tool_result`` — so the browser needs no new concepts: ``_mirror`` adds
    ``agent_id`` on the way to the parent and the worker code path takes it
    from there. Everything here is client-supplied data, and it stays data.
    """
    kind = str(getattr(event, "type", ""))
    if kind == "assistant":
        text = str(getattr(event, "text", "") or "")
        if not text.strip():
            return None
        return {
            "type": "assistant",
            "data": {
                "content": text,
                "actions": [],
                "step": step,
                "n_calls": step,
                "external": True,
            },
        }
    if kind == "tool_call":
        name = str(getattr(event, "name", "") or "tool")
        files = _clean_files(getattr(event, "files", None), cwd)
        command = str(getattr(event, "command", "") or "").strip()
        return {
            "type": "tool_call",
            "data": {
                # the same field a worker's tool_call uses, so the UI's
                # existing renderer has something human to show
                "command": command or _synthetic_command(name, files),
                "step": step,
                "n_calls": step,
                "tool_name": name,
                "files": files,
                "external": True,
            },
        }
    if kind == "tool_result":
        name = str(getattr(event, "name", "") or "tool")
        files = _clean_files(getattr(event, "files", None), cwd)
        ok = bool(getattr(event, "ok", True))
        return {
            "type": "tool_result",
            "data": {
                "command": _synthetic_command(name, files),
                "output": str(getattr(event, "output", "") or ""),
                "returncode": 0 if ok else 1,
                "is_error": not ok,
                "ok": ok,
                "step": step,
                "tool_name": name,
                "files": files,
                "external": True,
            },
        }
    return None


def _synthetic_command(name: str, files: list[str]) -> str:
    """What to show when the client sent no command: the tool and its files."""
    return " ".join([name, *files]).strip() or name


def _clean_cwd(cwd: str | None) -> str | None:
    """The path an external agent SAYS it runs in. Display only, ever.

    Kept as the client sent it (minus control characters and a length cap)
    because it is a label, not a location: nothing on this server opens it,
    stats it, or joins anything onto it.
    """
    if not cwd:
        return None
    text = "".join(ch for ch in str(cwd) if ch not in _CONTROL_CHARS).strip()
    return text[:MAX_INGEST_PATH_CHARS] or None


def _clean_files(files: Any, cwd: str) -> list[str]:
    """Repo-relative display labels, out of untrusted client paths.

    These become node labels on a force-directed graph and nothing else — no
    filesystem call is ever made with one — but "nothing else" has to stay
    true after the next person edits this file, so the hygiene is done once,
    here, at the boundary:

    * a leading ``external_cwd`` prefix is stripped (the agent reports
      absolute paths inside its own checkout);
    * anything still absolute is DROPPED, not "fixed" — a path outside the
      agent's own tree is not something we can label honestly;
    * so is anything with a ``..`` segment, a NUL byte, or over
      ``MAX_INGEST_PATH_CHARS`` characters;
    * at most ``MAX_INGEST_FILES`` survive, de-duplicated, in order.
    """
    if not isinstance(files, list):
        return []
    prefix = _cwd_prefix(cwd)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in files[:MAX_INGEST_FILES]:
        path = _clean_file(raw, prefix)
        if path is None or path in seen:
            continue
        seen.add(path)
        cleaned.append(path)
        if len(cleaned) >= MAX_INGEST_FILES:
            break
    return cleaned


def _cwd_prefix(cwd: str) -> str:
    """``external_cwd`` as a comparable, slash-separated directory prefix."""
    text = str(cwd or "").replace("\\", "/").rstrip("/")
    return f"{text}/" if text else ""


def _clean_file(raw: Any, prefix: str) -> str | None:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or "\x00" in path or len(path) > MAX_INGEST_PATH_CHARS:
        return None
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or _WINDOWS_ABSOLUTE_RE.match(path):
        return None
    segments = path.split("/")
    if any(segment == ".." for segment in segments):
        return None
    if any(ch in _CONTROL_CHARS for ch in path):
        return None
    return path


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
