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
        event.setdefault("timestamp", time.time())
        event_id = await self._store.append_event(
            session_id, event.get("type", "unknown"), event, event["timestamp"]
        )
        event["id"] = event_id
        event["session_id"] = session_id
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(event)
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

        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=256)
        queues = self._get_queues(session_id)
        queues.append(q)
        try:
            while True:
                event = await q.get()
                if event is None:
                    break
                yield _format_sse(event)
                if event.get("type") == "lifecycle" and event.get("data", {}).get(
                    "status"
                ) in {"completed", "failed", "stopped"}:
                    break
        finally:
            queues.remove(q)

    def finish(self, session_id: str) -> None:
        for q in self._get_queues(session_id):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


def _format_sse(event: dict) -> str:
    event_id = event.get("id", "")
    event_type = event.get("type", "message")
    data = json.dumps(event.get("data", event), default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"
