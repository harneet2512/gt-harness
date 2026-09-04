"""API routes for the cloud coding agent."""
from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .deps import get_event_bus, get_runner, get_store
from .events import EventBus
from .models import SessionCreate, SessionStatus, SteeringMessage
from .runner import SessionRunner
from .store import SessionStore

router = APIRouter()

_GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/[\w\-\.]+/[\w\-\.]+(?:\.git)?$"
)


def _session_to_status(row: dict) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "repo": row["repo"],
        "ref": row["ref"],
        "task": row["task"],
        "model": row["model"],
        "gt_mode": row["gt_mode"],
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "steps": row.get("steps", 0),
        "cost": row.get("cost", 0.0),
    }


@router.post("/sessions", response_model=SessionStatus, status_code=201)
async def create_session(
    body: SessionCreate,
    store: Annotated[SessionStore, Depends(get_store)],
    runner: Annotated[SessionRunner, Depends(get_runner)],
) -> dict[str, Any]:
    if not _GITHUB_REPO_RE.match(body.repo):
        raise HTTPException(400, "repo must be a GitHub HTTPS URL")

    session_id = await store.create_session(
        repo=body.repo,
        ref=body.ref,
        task=body.task,
        model=body.model,
        gt_mode=body.gt_mode,
        config={
            "step_limit": body.step_limit,
            "temperature": body.temperature,
        },
    )

    await runner.launch(
        session_id,
        repo=body.repo,
        ref=body.ref,
        task=body.task,
        model=body.model,
        gt_mode=body.gt_mode,
        step_limit=body.step_limit,
        temperature=body.temperature,
    )

    session = await store.get_session(session_id)
    return _session_to_status(session)  # type: ignore[arg-type]


@router.get("/sessions")
async def list_sessions(
    store: Annotated[SessionStore, Depends(get_store)],
) -> list[dict[str, Any]]:
    sessions = await store.list_sessions()
    return [_session_to_status(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(
    session_id: str,
    store: Annotated[SessionStore, Depends(get_store)],
) -> dict[str, Any]:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return _session_to_status(session)


@router.get("/sessions/{session_id}/events")
async def stream_events(
    session_id: str,
    store: Annotated[SessionStore, Depends(get_store)],
    event_bus: Annotated[EventBus, Depends(get_event_bus)],
    after_id: int = 0,
) -> StreamingResponse:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    return StreamingResponse(
        event_bus.subscribe(session_id, after_id=after_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/result")
async def get_result(
    session_id: str,
    store: Annotated[SessionStore, Depends(get_store)],
) -> dict[str, Any]:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session["status"] not in {"completed", "failed", "stopped"}:
        raise HTTPException(409, "session not finished yet")
    result_raw = session.get("result_json")
    if not result_raw:
        raise HTTPException(404, "no result available")
    result = json.loads(result_raw)
    result["id"] = session_id
    return result


@router.post("/sessions/{session_id}/steer", status_code=202)
async def steer_session(
    session_id: str,
    body: SteeringMessage,
    store: Annotated[SessionStore, Depends(get_store)],
    runner: Annotated[SessionRunner, Depends(get_runner)],
) -> dict[str, str]:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session["status"] != "running":
        raise HTTPException(409, "session is not running")
    agent = runner.get_agent(session_id)
    if agent is None:
        raise HTTPException(409, "agent not available")
    agent._steering_queue.put(body.content)
    return {"status": "queued"}


@router.post("/sessions/{session_id}/stop", status_code=202)
async def stop_session(
    session_id: str,
    store: Annotated[SessionStore, Depends(get_store)],
    runner: Annotated[SessionRunner, Depends(get_runner)],
) -> dict[str, str]:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session["status"] != "running":
        raise HTTPException(409, "session is not running")
    agent = runner.get_agent(session_id)
    if agent is None:
        raise HTTPException(409, "agent not available")
    agent._stop_event.set()
    return {"status": "stopping"}
