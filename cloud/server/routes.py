"""API routes for the cloud coding agent.

Every route here requires an authenticated user (see ``auth.require_user``),
which is attached once at the router so no endpoint can forget it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .auth import issue_ingest_token, require_ingest, require_user
from .deps import get_event_bus, get_manager, get_store
from .events import EventBus
from .models import (
    MAX_INGEST_BATCH,
    MAX_TASKS_PER_SPAWN,
    AgentApplied,
    AgentSpawn,
    AgentsSpawned,
    ExternalAgentCreate,
    ExternalAgentFinish,
    ExternalAgentRegistered,
    ExternalChildCreate,
    IngestAccepted,
    IngestBatch,
    Message,
    MessageAccepted,
    MessageCreate,
    Session,
    SessionCreate,
    SessionDiff,
    SessionGraph,
    SessionTree,
    TurnReceipt,
)
from .runner import (
    EXTERNAL_ROLE,
    MAX_INGEST_BODY_BYTES,
    PRIMARY_ROLE,
    ApplyConflict,
    ApplyRefused,
    ConcurrencyLimit,
    ExternalAgentLimit,
    ExternalAgentRefused,
    ModelUnavailable,
    SessionManager,
)
from .store import SessionStore

router = APIRouter(dependencies=[Depends(require_user)])
#: The external agents' own routes. A SEPARATE router because ``router``
#: attaches ``require_user`` to every endpoint on it, and these two are
#: authenticated by an agent's ingest token instead — the one credential a
#: signed-in browser does not have and an adapter does.
external_router = APIRouter()

_GITHUB_REPO_RE = re.compile(r"^https://github\.com/[\w\-\.]+/[\w\-\.]+(?:\.git)?$")

#: a message cannot start or steer a turn in these states
_CLOSED_TO_MESSAGES = {"creating", "closed", "failed"}
#: a session cannot take on new external agents in these states. ``creating``
#: is fine, unlike for a message: registering an agent we do not run needs
#: nothing from the workspace that is still cloning.
_CLOSED_TO_EXTERNAL = {"closed", "failed"}

IngestDep = Annotated[dict[str, Any], Depends(require_ingest)]

#: one worker per line: ``/spawn <task>``. A message whose first non-blank
#: line is one of these is a spawn command and never reaches a model.
_SPAWN_LINE = re.compile(r"^\s*/spawn\s+(?P<task>\S.*?)\s*$")

StoreDep = Annotated[SessionStore, Depends(get_store)]
ManagerDep = Annotated[SessionManager, Depends(get_manager)]
BusDep = Annotated[EventBus, Depends(get_event_bus)]


def _worker_report(row: dict) -> dict[str, Any] | None:
    """A worker's stored report, with ``applied`` read off the row itself."""
    raw = row.get("report_json")
    if not raw:
        return None
    try:
        report = json.loads(str(raw))
    except ValueError:
        return None
    if not isinstance(report, dict):
        return None
    report["applied"] = bool(row.get("applied_at"))
    return report


def _session_view(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "repo": row["repo"],
        "ref": row["ref"],
        "model": row["model"],
        "gt_mode": row["gt_mode"],
        "gt_status": row["gt_status"],
        "gt_error": row.get("gt_error"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_message": row.get("last_message"),
        "turns": row.get("turns", 0),
        "steps": row.get("steps", 0),
        "cost": row.get("cost", 0.0),
        "total_wall_seconds": row.get("total_wall_seconds", 0.0),
        "gt_actions": row.get("gt_actions", 0),
        "current_turn_id": row.get("current_turn_id"),
        "closed_reason": row.get("closed_reason"),
        "parent_id": row.get("parent_id"),
        "role": row.get("role") or PRIMARY_ROLE,
        "task": row.get("task"),
        "report": _worker_report(row),
        "applied_at": row.get("applied_at"),
        "agent_kind": row.get("agent_kind"),
        "parent_agent_id": row.get("parent_agent_id"),
        "external_cwd": row.get("external_cwd"),
        "label": row.get("label"),
        "activity": row.get("activity"),
        "tokens": row.get("tokens"),
    }


def _spawn_tasks(content: str) -> list[str] | None:
    """The tasks a ``/spawn`` message asks for, or ``None`` if it is a message.

    A message either *is* a spawn command — every non-blank line a
    ``/spawn <task>`` — or it is not one at all. Half a command is a 400
    rather than a turn that quietly runs the word "/spawn" past a model.
    """
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines or _SPAWN_LINE.match(lines[0]) is None:
        return None
    tasks: list[str] = []
    for line in lines:
        match = _SPAWN_LINE.match(line)
        if match is None:
            raise HTTPException(
                400,
                "a /spawn message may only contain /spawn lines "
                "(one task per line)",
            )
        tasks.append(match.group("task"))
    if len(tasks) > MAX_TASKS_PER_SPAWN:
        raise HTTPException(
            400, f"at most {MAX_TASKS_PER_SPAWN} tasks per /spawn message"
        )
    return tasks


async def _require_worker(
    store: SessionStore, session_id: str, worker_id: str
) -> dict:
    worker = await store.get_session(worker_id)
    if worker is None or str(worker.get("parent_id") or "") != session_id:
        raise HTTPException(404, "worker not found for this session")
    return worker


def _spawnable(session: dict) -> None:
    """Raise unless this session may spawn workers right now."""
    if str(session.get("role") or PRIMARY_ROLE) != PRIMARY_ROLE:
        raise HTTPException(409, "a worker cannot spawn workers")
    if session["status"] in _CLOSED_TO_MESSAGES:
        raise HTTPException(
            409, f"session is {session['status']} and cannot spawn workers"
        )


def _session_config(body: SessionCreate) -> dict[str, Any]:
    """The per-session knobs the runner reads back off the row.

    ``wall_seconds`` is stored only when the caller asked for one, so an
    unset request keeps following ``TURN_WALL_SECONDS`` rather than freezing
    today's default into every session row.
    """
    config: dict[str, Any] = {
        "step_limit": body.step_limit,
        "temperature": body.temperature,
    }
    if body.wall_seconds is not None:
        config["wall_seconds"] = body.wall_seconds
    return config


async def _require_session(store: SessionStore, session_id: str) -> dict:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.post("/sessions", response_model=Session, status_code=201)
async def create_session(
    body: SessionCreate, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    if not _GITHUB_REPO_RE.match(body.repo):
        raise HTTPException(400, "repo must be a GitHub HTTPS URL")
    # Before anything is spent: an unusable model used to cost a clone, a
    # sandbox, a GT index and a four-minute first turn (HAR-84 G-11).
    try:
        await manager.check_model(body.model)
    except ModelUnavailable as exc:
        raise HTTPException(400, f"model not available: {exc}") from exc

    session_id = await store.create_session(
        repo=body.repo,
        ref=body.ref,
        model=body.model,
        gt_mode=body.gt_mode,
        config=_session_config(body),
    )
    try:
        await manager.create_workspace(
            session_id, first_message=body.first_message
        )
    except ConcurrencyLimit as exc:
        # The row exists, so the failure is visible rather than a silent 500.
        await store.update_status(session_id, "failed", closed_reason="failed")
        raise HTTPException(429, str(exc)) from exc
    session = await store.get_session(session_id)
    return _session_view(session)  # type: ignore[arg-type]


@router.get("/sessions", response_model=list[Session])
async def list_sessions(store: StoreDep) -> list[dict[str, Any]]:
    return [_session_view(s) for s in await store.list_sessions()]


@router.get("/sessions/{session_id}", response_model=Session)
async def get_session(session_id: str, store: StoreDep) -> dict[str, Any]:
    return _session_view(await _require_session(store, session_id))


@router.get("/sessions/{session_id}/messages", response_model=list[Message])
async def list_messages(session_id: str, store: StoreDep) -> list[dict[str, Any]]:
    await _require_session(store, session_id)
    return await store.list_messages(session_id)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageAccepted,
    status_code=202,
)
async def post_message(
    session_id: str, body: MessageCreate, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    session = await _require_session(store, session_id)
    if session["status"] in _CLOSED_TO_MESSAGES:
        raise HTTPException(
            409, f"session is {session['status']} and cannot accept messages"
        )
    tasks = _spawn_tasks(body.content)
    if tasks is not None:
        _spawnable(session)
        try:
            message = await manager.spawn_from_chat(session, tasks, body.content)
        except ModelUnavailable as exc:
            raise HTTPException(400, f"model not available: {exc}") from exc
        except ConcurrencyLimit as exc:
            raise HTTPException(429, str(exc)) from exc
        return {"message": message, "delivery": "spawned"}
    try:
        message, delivery = await manager.post_message(session_id, body.content)
    except ExternalAgentRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConcurrencyLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"message": message, "delivery": delivery}


@router.post(
    "/sessions/{session_id}/agents",
    response_model=AgentsSpawned,
    status_code=202,
)
async def spawn_agents(
    session_id: str, body: AgentSpawn, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    """Spawn one worker agent per task. All of them, or none at all."""
    session = await _require_session(store, session_id)
    _spawnable(session)
    try:
        workers = await manager.spawn_agents(
            session, body.tasks, model=body.model, gt_mode=body.gt_mode
        )
    except ModelUnavailable as exc:
        raise HTTPException(400, f"model not available: {exc}") from exc
    except ConcurrencyLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"workers": [_session_view(worker) for worker in workers]}


@router.get("/sessions/{session_id}/agents", response_model=list[Session])
async def list_agents(
    session_id: str, store: StoreDep, manager: ManagerDep
) -> list[dict[str, Any]]:
    """Every agent of this session, oldest first.

    Workers *and* external agents: both are child rows of the same session and
    both render as a card, so one list answers "what is working on this?".
    """
    await _require_session(store, session_id)
    return [_session_view(w) for w in await manager.list_workers(session_id)]


@router.post(
    "/sessions/{session_id}/agents/{worker_id}/apply",
    response_model=AgentApplied,
)
async def apply_agent(
    session_id: str, worker_id: str, store: StoreDep, manager: ManagerDep
) -> Any:
    """Merge a worker's cumulative diff into this session's workspace."""
    session = await _require_session(store, session_id)
    worker = await _require_worker(store, session_id, worker_id)
    if session["status"] != "idle":
        raise HTTPException(
            409,
            f"session is {session['status']}; a worker's changes can only be "
            "applied while it is idle",
        )
    try:
        return await manager.apply_worker(session, worker)
    except ApplyRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    except ApplyConflict as exc:
        # `conflicts` is top level, not buried in `detail`: it is the answer,
        # not a decoration on the error string.
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "conflicts": exc.conflicts},
        )


@router.post(
    "/sessions/{session_id}/agents/{worker_id}/close", response_model=Session
)
async def close_agent(
    session_id: str, worker_id: str, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    """Close one worker. The same thing as `/sessions/{worker_id}/close`."""
    await _require_session(store, session_id)
    await _require_worker(store, session_id, worker_id)
    await manager.close(worker_id)
    return _session_view(await store.get_session(worker_id))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# external agents: agents we watch but never run
# --------------------------------------------------------------------------
@router.post(
    "/sessions/{session_id}/external-agents",
    response_model=ExternalAgentRegistered,
    status_code=201,
)
async def register_external_agent(
    session_id: str,
    body: ExternalAgentCreate,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """Register an agent we do not run, as a child of this session.

    Nothing is executed here and nothing ever will be: no workspace, no
    sandbox, no model call, no concurrency slot. The answer is the row plus
    the only credential that can push events into it.
    """
    session = await _require_session(store, session_id)
    if session["status"] in _CLOSED_TO_EXTERNAL:
        raise HTTPException(
            409,
            f"session is {session['status']} and cannot host external agents",
        )
    return await _register(
        request, session, body, parent_agent_id=body.parent_agent_id
    )


async def _register(
    request: Request,
    session: dict,
    body: ExternalChildCreate,
    *,
    parent_agent_id: str | None,
) -> dict[str, Any]:
    """Register one external agent and hand back its own scoped credential.

    Both doors come through here — the user-authed one and the ingest-token
    one — so a child is a first-class agent with a token of its own rather
    than something that shares its parent's.
    """
    manager = get_manager()
    session_id = str(session["id"])
    try:
        agent = await manager.register_external_agent(
            session,
            agent_kind=body.agent_kind,
            label=body.label,
            task=body.task,
            cwd=body.cwd,
            parent_agent_id=parent_agent_id,
        )
    except ExternalAgentLimit as exc:
        # A ceiling, not a malformed request: 409, never a 500.
        raise HTTPException(409, str(exc)) from exc
    except ExternalAgentRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    agent_id = str(agent["id"])
    return {
        "agent": _session_view(agent),
        "ingest_token": issue_ingest_token(agent_id, session_id),
        "ingest_url": _ingest_url(request, agent_id),
    }


def _ingest_url(request: Request, agent_id: str) -> str:
    """Where this agent posts its events.

    ``PUBLIC_BASE_URL`` wins when it is set, because the URL is handed to a
    process on somebody's laptop: behind a TLS-terminating proxy the request's
    own scheme and host are the *internal* ones, and a http://app:8000/... URL
    is useless to the adapter that has to call it.
    """
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/api/external-agents/{agent_id}/events"
    return str(request.url_for("ingest_external_events", agent_id=agent_id))


async def _require_external_agent(store: SessionStore, agent_id: str) -> dict:
    agent = await store.get_session(agent_id)
    if agent is None or str(agent.get("role") or "") != EXTERNAL_ROLE:
        raise HTTPException(404, "external agent not found")
    if str(agent["status"]) == "closed":
        raise HTTPException(409, "external agent is closed")
    return agent


async def _ingest_batch(request: Request) -> IngestBatch:
    """Read and bound the body before it becomes objects.

    Both caps answer 413 rather than 422: they are about the SIZE of what was
    sent, not its shape, and an adapter that can read one status code can
    halve its batch and carry on.
    """
    raw = await request.body()
    if len(raw) > MAX_INGEST_BODY_BYTES:
        raise HTTPException(
            413,
            f"an ingest batch may be at most {MAX_INGEST_BODY_BYTES} bytes",
        )
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(400, "body must be JSON") from exc
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        if len(payload["events"]) > MAX_INGEST_BATCH:
            raise HTTPException(
                413, f"an ingest batch may carry at most {MAX_INGEST_BATCH} events"
            )
    try:
        return IngestBatch.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


@external_router.post(
    "/external-agents/{agent_id}/events",
    response_model=IngestAccepted,
    status_code=202,
)
async def ingest_external_events(
    agent_id: str,
    request: Request,
    store: StoreDep,
    manager: ManagerDep,
    _claims: IngestDep,
) -> dict[str, Any]:
    """Take an external agent's own events onto its parent's stream.

    Authenticated by the ingest token ALONE — a signed-in user's JWT is
    refused here, and this token is refused everywhere else. The events are
    data: they are mirrored and stored, never interpreted as instructions.
    """
    agent = await _require_external_agent(store, agent_id)
    batch = await _ingest_batch(request)
    accepted = await manager.ingest_external_events(agent, batch.events)
    return {"accepted": accepted}


@external_router.post(
    "/external-agents/{agent_id}/children",
    response_model=ExternalAgentRegistered,
    status_code=201,
)
async def register_external_child(
    agent_id: str,
    body: ExternalChildCreate,
    request: Request,
    store: StoreDep,
    _claims: IngestDep,
) -> dict[str, Any]:
    """Register a subagent OF the calling agent, on the calling agent's token.

    This is what makes the ``/connect`` button able to draw a tree at all. The
    browser holds an httpOnly cookie, so the UI cannot hand a local agent the
    user's own JWT — and should not — which left the user-authed registration
    route usable only by somebody who had already been given a server-side
    token out of band. Nesting therefore worked in the documented setup and
    silently did not from the button.

    The parent is the token's own agent and cannot be named: a token creates
    children under itself, in its own session, or nowhere.
    """
    agent = await _require_external_agent(store, agent_id)
    session = await _require_session(store, str(agent.get("parent_id") or ""))
    if session["status"] in _CLOSED_TO_EXTERNAL:
        raise HTTPException(
            409,
            f"session is {session['status']} and cannot host external agents",
        )
    return await _register(request, session, body, parent_agent_id=agent_id)


@external_router.post(
    "/external-agents/{agent_id}/finish", response_model=Session
)
async def finish_external_agent(
    agent_id: str,
    body: ExternalAgentFinish,
    store: StoreDep,
    manager: ManagerDep,
    _claims: IngestDep,
) -> dict[str, Any]:
    """The agent says it is done: report to the parent and settle the row."""
    agent = await _require_external_agent(store, agent_id)
    row = await manager.finish_external_agent(agent, body.status, body.summary)
    return _session_view(row)


@router.get("/sessions/{session_id}/events")
async def stream_events(
    session_id: str,
    store: StoreDep,
    event_bus: BusDep,
    after_id: int = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    await _require_session(store, session_id)
    if not after_id and last_event_id:
        # A malformed resume token used to fall back to replaying the whole
        # history, silently — the client asked to continue and got the start
        # of time instead (HAR-84 G-17).
        try:
            after_id = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(
                400, "Last-Event-ID must be an integer event id"
            ) from exc
        if after_id < 0:
            raise HTTPException(400, "Last-Event-ID must not be negative")

    return StreamingResponse(
        event_bus.subscribe(session_id, after_id=after_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/sessions/{session_id}/diff",
    response_model=SessionDiff,
    # ``as_of_event``/``approximate``/``truncated`` are optional: absent unless
    # the caller asked for a point-in-time diff (or the patch was capped)
    response_model_exclude_none=True,
)
async def get_diff(
    session_id: str,
    store: StoreDep,
    manager: ManagerDep,
    through_event: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """The workspace diff — live, or as of a ``tool_result`` event id."""
    session = await _require_session(store, session_id)
    if through_event is None:
        return await manager.diff(session)
    return await manager.diff_at(session, through_event)


@router.get("/sessions/{session_id}/tree", response_model=SessionTree)
async def get_tree(
    session_id: str, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    session = await _require_session(store, session_id)
    return await manager.tree(session)


@router.get(
    "/sessions/{session_id}/graph",
    response_model=SessionGraph,
    # ``truncated`` is an optional field: absent unless the graph was capped
    response_model_exclude_none=True,
)
async def get_graph(
    session_id: str, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    session = await _require_session(store, session_id)
    return await manager.graph(session)


@router.get("/sessions/{session_id}/receipts", response_model=list[TurnReceipt])
async def get_receipts(session_id: str, store: StoreDep) -> list[dict[str, Any]]:
    await _require_session(store, session_id)
    return await store.list_turns(session_id)


@router.post("/sessions/{session_id}/stop", status_code=202)
async def stop_turn(
    session_id: str, store: StoreDep, manager: ManagerDep
) -> dict[str, str]:
    session = await _require_session(store, session_id)
    if session["status"] != "running":
        raise HTTPException(409, "session has no running turn")
    await manager.stop(session_id)
    return {"status": "stopping"}


@router.post("/sessions/{session_id}/close", response_model=Session, status_code=200)
async def close_session(
    session_id: str, store: StoreDep, manager: ManagerDep
) -> dict[str, Any]:
    await _require_session(store, session_id)
    await manager.close(session_id)
    session = await store.get_session(session_id)
    return _session_view(session)  # type: ignore[arg-type]
