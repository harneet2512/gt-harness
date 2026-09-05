"""API routes for the cloud coding agent.

Every route here requires an authenticated user (see ``auth.require_user``),
which is attached once at the router so no endpoint can forget it.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .auth import require_user
from .deps import get_event_bus, get_manager, get_store
from .events import EventBus
from .models import (
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
from .runner import ConcurrencyLimit, SessionManager
from .store import SessionStore

router = APIRouter(dependencies=[Depends(require_user)])

_GITHUB_REPO_RE = re.compile(r"^https://github\.com/[\w\-\.]+/[\w\-\.]+(?:\.git)?$")

#: a message cannot start or steer a turn in these states
_CLOSED_TO_MESSAGES = {"creating", "closed", "failed"}

StoreDep = Annotated[SessionStore, Depends(get_store)]
ManagerDep = Annotated[SessionManager, Depends(get_manager)]
BusDep = Annotated[EventBus, Depends(get_event_bus)]


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
        "current_turn_id": row.get("current_turn_id"),
        "closed_reason": row.get("closed_reason"),
    }


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

    session_id = await store.create_session(
        repo=body.repo,
        ref=body.ref,
        model=body.model,
        gt_mode=body.gt_mode,
        config=_session_config(body),
    )
    await manager.create_workspace(session_id)
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
    try:
        message, delivery = await manager.post_message(session_id, body.content)
    except ConcurrencyLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"message": message, "delivery": delivery}


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
        try:
            after_id = int(last_event_id)
        except ValueError:
            after_id = 0

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
