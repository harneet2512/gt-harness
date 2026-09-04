"""SQLite job store for cloud coding agent sessions."""
from __future__ import annotations

import json
import time
import uuid

import aiosqlite

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running"},
    "running": {"completed", "failed", "stopped"},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    ref TEXT NOT NULL,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    gt_mode TEXT NOT NULL DEFAULT 'advisory',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    steps INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    result_json TEXT,
    config_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
"""


class SessionStore:
    def __init__(self, db_path: str = "cloud_harness.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection = None  # type: ignore[assignment]

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def create_session(
        self,
        *,
        repo: str,
        ref: str,
        task: str,
        model: str,
        gt_mode: str = "advisory",
        config: dict | None = None,
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        await self._db.execute(
            """INSERT INTO sessions (id, repo, ref, task, model, gt_mode, status, created_at, config_json)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (session_id, repo, ref, task, model, gt_mode, now, json.dumps(config or {})),
        )
        await self._db.commit()
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        cursor = await self._db.execute(
            # rowid breaks ties: two sessions created in the same clock tick
            # would otherwise come back in arbitrary order.
            "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_status(
        self,
        session_id: str,
        new_status: str,
        *,
        steps: int | None = None,
        cost: float | None = None,
    ) -> None:
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        current = session["status"]
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"invalid transition {current} → {new_status} "
                f"(allowed: {allowed})"
            )
        now = time.time()
        updates = ["status = ?"]
        params: list = [new_status]
        if new_status == "running":
            updates.append("started_at = ?")
            params.append(now)
        if new_status in {"completed", "failed", "stopped"}:
            updates.append("finished_at = ?")
            params.append(now)
        if steps is not None:
            updates.append("steps = ?")
            params.append(steps)
        if cost is not None:
            updates.append("cost = ?")
            params.append(cost)
        params.append(session_id)
        await self._db.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await self._db.commit()

    async def store_result(self, session_id: str, result: dict) -> None:
        await self._db.execute(
            "UPDATE sessions SET result_json = ? WHERE id = ?",
            (json.dumps(result), session_id),
        )
        await self._db.commit()

    async def append_event(
        self,
        session_id: str,
        event_type: str,
        data: dict,
        timestamp: float | None = None,
    ) -> int:
        ts = timestamp or time.time()
        cursor = await self._db.execute(
            """INSERT INTO events (session_id, type, data_json, timestamp)
               VALUES (?, ?, ?, ?)""",
            (session_id, event_type, json.dumps(data), ts),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_events(
        self,
        session_id: str,
        after_id: int = 0,
        limit: int = 1000,
    ) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT id, session_id, type, data_json, timestamp
               FROM events
               WHERE session_id = ? AND id > ?
               ORDER BY id
               LIMIT ?""",
            (session_id, after_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "type": r["type"],
                "data": json.loads(r["data_json"]),
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]
