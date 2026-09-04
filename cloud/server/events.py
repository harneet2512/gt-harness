"""Per-session SSE event bus — bridges agent thread events to async subscribers."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from .store import SessionStore


class EventBus:
    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._queues: dict[str, list[asyncio.Queue[dict | None]]] = {}

    def _get_queues(self, session_id: str) -> list[asyncio.Queue[dict | None]]:
        return self._queues.setdefault(session_id, [])

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        normalized = _normalize(event)
        event_id = await self._store.append_event(
            session_id, normalized["type"], normalized["data"], normalized["timestamp"]
        )
        normalized["id"] = event_id
        normalized["session_id"] = session_id
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(normalized)
            except asyncio.QueueFull:
                pass

    async def publish_threadsafe(
        self,
        session_id: str,
        event: dict[str, Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        asyncio.run_coroutine_threadsafe(
            self.publish(session_id, event), loop
        )

    async def subscribe(
        self,
        session_id: str,
        after_id: int = 0,
    ) -> AsyncGenerator[str, None]:
        stored = await self._store.get_events(session_id, after_id=after_id)
        for ev in stored:
            yield _format_sse(ev)
            if _is_terminal(ev):
                # The session already finished: nothing more will ever be
                # published, and finish()'s sentinel is long gone, so the live
                # loop below would block forever.
                return

        session = await self._store.get_session(session_id)
        if session is not None and session["status"] in _TERMINAL_STATUSES:
            # Same reason: replaying a finished session must not park on a
            # queue that nobody will ever push to (finish() already ran).
            return

        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=256)
        queues = self._get_queues(session_id)
        queues.append(q)
        try:
            while True:
                event = await q.get()
                if event is None:
                    break
                yield _format_sse(event)
                if _is_terminal(event):
                    break
        finally:
            queues.remove(q)

    def finish(self, session_id: str) -> None:
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


_ENVELOPE_KEYS = {"type", "timestamp", "id", "session_id", "data"}
_TERMINAL_STATUSES = {"completed", "failed", "stopped"}


def _is_terminal(event: dict[str, Any]) -> bool:
    return (
        event.get("type") == "lifecycle"
        and event.get("data", {}).get("status") in _TERMINAL_STATUSES
    )


def _normalize(event: dict[str, Any]) -> dict[str, Any]:
    """Coerce both `{type, data:{...}}` and flat `{type, k: v}` events into one envelope."""
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
