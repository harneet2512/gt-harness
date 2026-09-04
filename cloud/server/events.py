"""Per-session SSE event bus — bridges agent-thread events to async subscribers.

The stream is session-scoped, not turn-scoped: it stays open across turns and
closes only when the session is ``closed``/``failed`` or the client goes away.
A comment heartbeat keeps proxies from reaping an idle connection.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

from .store import SessionStore

DEFAULT_HEARTBEAT_SECONDS = 15.0
#: session statuses after which nothing more will ever be published
TERMINAL_STATUSES = {"closed", "failed"}


def heartbeat_seconds() -> float:
    try:
        return float(os.environ.get("SSE_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS))
    except ValueError:
        return DEFAULT_HEARTBEAT_SECONDS


class EventBus:
    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._queues: dict[str, list[asyncio.Queue[dict | None]]] = {}

    def _get_queues(self, session_id: str) -> list[asyncio.Queue[dict | None]]:
        return self._queues.setdefault(session_id, [])

    async def publish(self, session_id: str, event: dict[str, Any]) -> dict:
        normalized = _normalize(event)
        event_id = await self._store.append_event(
            session_id,
            normalized["type"],
            normalized["data"],
            normalized["timestamp"],
        )
        normalized["id"] = event_id
        normalized["session_id"] = session_id
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(normalized)
            except asyncio.QueueFull:
                pass
        return normalized

    async def subscribe(
        self, session_id: str, after_id: int = 0
    ) -> AsyncGenerator[str, None]:
        # Register before replaying so an event published mid-replay is not
        # lost; duplicates are filtered by id below.
        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1024)
        queues = self._get_queues(session_id)
        queues.append(q)
        interval = heartbeat_seconds()
        try:
            last_id = after_id
            for stored in await self._store.get_events(session_id, after_id=after_id):
                last_id = max(last_id, int(stored["id"]))
                yield _format_sse(stored)

            session = await self._store.get_session(session_id)
            if session is not None and session["status"] in TERMINAL_STATUSES:
                return

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=interval)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    break
                if int(event.get("id", 0)) <= last_id:
                    continue
                last_id = int(event["id"])
                yield _format_sse(event)
                if _is_terminal(event):
                    break
        finally:
            if q in queues:
                queues.remove(q)

    def finish(self, session_id: str) -> None:
        """Release every subscriber of a session that will never publish again."""
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


_ENVELOPE_KEYS = {"type", "timestamp", "id", "session_id", "data"}


def _is_terminal(event: dict[str, Any]) -> bool:
    return (
        event.get("type") == "lifecycle"
        and event.get("data", {}).get("status") in TERMINAL_STATUSES
    )


def _normalize(event: dict[str, Any]) -> dict[str, Any]:
    """Coerce `{type, data:{...}}` and flat `{type, k: v}` events into one envelope."""
    event_type = str(event.get("type", "unknown"))
    timestamp = float(event.get("timestamp") or time.time())
    if isinstance(event.get("data"), dict):
        data = dict(event["data"])
    else:
        data = {k: v for k, v in event.items() if k not in _ENVELOPE_KEYS}
    return {"type": event_type, "timestamp": timestamp, "data": data}


def _format_sse(event: dict) -> str:
    payload = {
        "id": event.get("id"),
        "type": event.get("type", "message"),
        "timestamp": event.get("timestamp"),
        "data": event.get("data", {}),
    }
    return (
        f"id: {payload['id']}\nevent: {payload['type']}\n"
        f"data: {json.dumps(payload, default=str)}\n\n"
    )
