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
#: events per replay query. The store caps a single read, so the replay pages
#: rather than stopping at the cap and handing the client a silent hole.
REPLAY_PAGE = 5000
#: ``lifecycle`` status announcing that the resume point asked for has been
#: trimmed away: the history below it is GONE, not empty. A gap the client is
#: told about can be recovered from (refetch /messages, /receipts, /diff); a
#: gap it is not told about is a wrong picture it will keep drawing.
REPLAY_TRUNCATED = "replay_truncated"


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
            if after_id:
                notice = await self._truncation_notice(session_id, after_id)
                if notice is not None:
                    yield notice
            while True:
                stored = await self._store.get_events(
                    session_id, after_id=last_id, limit=REPLAY_PAGE
                )
                for event in stored:
                    last_id = max(last_id, int(event["id"]))
                    yield _format_sse(event)
                if len(stored) < REPLAY_PAGE:
                    break

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

    async def _truncation_notice(self, session_id: str, after_id: int) -> str | None:
        """Say so when the events after ``after_id`` no longer exist.

        Carries no ``id:`` line on purpose: it is not a stored event, and an
        SSE frame without one leaves the client's ``Last-Event-ID`` exactly
        where it was, so a notice can never become somebody's resume point.
        """
        oldest = await self._store.oldest_event_id(session_id)
        if oldest is not None and after_id >= oldest:
            return None
        return _format_sse({
            "type": "lifecycle",
            "timestamp": time.time(),
            "data": {
                "status": REPLAY_TRUNCATED,
                "after_id": after_id,
                "oldest_id": oldest,
                "reason": (
                    "events before this point have been trimmed; the replay "
                    "starts later than you asked"
                ),
            },
        })

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
    body = (
        f"event: {payload['type']}\n"
        f"data: {json.dumps(payload, default=str)}\n\n"
    )
    if payload["id"] is None:
        # A frame the store never saw (the truncation notice). Emitting
        # `id: None` would make the string "None" the client's next
        # Last-Event-ID; omitting the line leaves it untouched, which is
        # exactly what the SSE spec says an id-less event does.
        return body
    return f"id: {payload['id']}\n{body}"
