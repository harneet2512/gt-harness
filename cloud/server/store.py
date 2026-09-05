"""SQLite store for cloud chat sessions: sessions, messages, turns, events.

This is a dev tool, so schema evolution is a drop-and-recreate: ``init()``
compares ``PRAGMA user_version`` against ``SCHEMA_VERSION`` and rebuilds every
table when they differ.
"""
from __future__ import annotations

import json
import time
import uuid

import aiosqlite

SCHEMA_VERSION = 5

#: ``stopped`` is a lifecycle *event*, not a persisted status: after a stop the
#: reply is written and the session goes straight back to ``idle``.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "creating": {"idle", "failed", "closed"},
    "idle": {"running", "closed"},
    "running": {"idle", "failed", "closed"},
    "failed": {"closed"},
    "closed": {"closed"},
}

_TABLES = ("diff_snapshots", "events", "turns", "messages", "sessions")

_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    ref TEXT NOT NULL,
    model TEXT NOT NULL,
    gt_mode TEXT NOT NULL DEFAULT 'off',
    gt_status TEXT NOT NULL DEFAULT 'off',
    -- why GT is unavailable, in the indexer's own words; NULL when it is not
    gt_error TEXT,
    status TEXT NOT NULL DEFAULT 'creating',
    -- why the session ended: 'user' (an explicit close), 'expired' (the idle
    -- TTL reaper) or 'failed'. NULL while it is still alive.
    closed_reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_message TEXT,
    turns INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    -- wall-clock seconds summed over finished turns; the only budget signal
    -- that survives MSWEA_COST_TRACKING=ignore_errors pricing every turn at $0
    total_wall_seconds REAL NOT NULL DEFAULT 0.0,
    current_turn_id TEXT,
    workspace_path TEXT,
    base_sha TEXT,
    -- path to the GT indexer's SQLite graph, when one was built
    graph_db TEXT,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE turns (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    n_calls INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    finish_reason TEXT,
    -- wall-clock seconds this turn took, start to finish
    wall_seconds REAL NOT NULL DEFAULT 0.0,
    patch_sha256 TEXT,
    gt_status TEXT NOT NULL DEFAULT 'off',
    model TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE diff_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    -- id of the ``tool_result`` event this snapshot was taken after
    event_id INTEGER NOT NULL,
    turn_id TEXT,
    step INTEGER NOT NULL DEFAULT 0,
    -- sha of the FULL patch, even when the stored text was capped
    patch_sha256 TEXT,
    files_json TEXT NOT NULL DEFAULT '[]',
    patch TEXT NOT NULL DEFAULT '',
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX idx_events_session ON events(session_id, id);
CREATE INDEX idx_diff_snapshots_session
    ON diff_snapshots(session_id, event_id);
CREATE INDEX idx_messages_session ON messages(session_id, seq);
CREATE INDEX idx_turns_session ON turns(session_id, seq);
"""

_SESSION_FIELDS = (
    "gt_status",
    "gt_error",
    "closed_reason",
    "last_message",
    "turns",
    "steps",
    "cost",
    "current_turn_id",
    "workspace_path",
    "base_sha",
    "graph_db",
)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class SessionStore:
    def __init__(self, db_path: str = "cloud_harness.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection = None  # type: ignore[assignment]

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        version = int(row[0]) if row else 0
        if version != SCHEMA_VERSION:
            for table in _TABLES:
                await self._db.execute(f"DROP TABLE IF EXISTS {table}")
            await self._db.executescript(_SCHEMA)
            await self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # -- sessions -------------------------------------------------------------

    async def create_session(
        self,
        *,
        repo: str,
        ref: str,
        model: str,
        gt_mode: str = "off",
        config: dict | None = None,
    ) -> str:
        session_id = new_id()
        now = time.time()
        await self._db.execute(
            """INSERT INTO sessions
               (id, repo, ref, model, gt_mode, gt_status, status,
                created_at, updated_at, config_json)
               VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)""",
            (
                session_id,
                repo,
                ref,
                model,
                gt_mode,
                "off" if gt_mode == "off" else "pending",
                now,
                now,
                json.dumps(config or {}),
            ),
        )
        await self._db.commit()
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def list_sessions(self, limit: int = 100) -> list[dict]:
        cursor = await self._db.execute(
            # rowid breaks ties: two sessions created in the same clock tick
            # would otherwise come back in arbitrary order.
            "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def update_session(self, session_id: str, **fields: object) -> None:
        """Update non-status session fields (validated against the schema)."""
        unknown = set(fields) - set(_SESSION_FIELDS)
        if unknown:
            raise ValueError(f"unknown session fields: {sorted(unknown)}")
        if not fields:
            return
        updates = [f"{name} = ?" for name in fields]
        params: list[object] = list(fields.values())
        updates.append("updated_at = ?")
        params.extend([time.time(), session_id])
        await self._db.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params
        )
        await self._db.commit()

    async def update_status(
        self, session_id: str, new_status: str, **fields: object
    ) -> None:
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        current = str(session["status"])
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"invalid transition {current} -> {new_status} (allowed: {sorted(allowed)})"
            )
        unknown = set(fields) - set(_SESSION_FIELDS)
        if unknown:
            raise ValueError(f"unknown session fields: {sorted(unknown)}")
        updates = ["status = ?", "updated_at = ?"]
        params: list[object] = [new_status, time.time()]
        for name, value in fields.items():
            updates.append(f"{name} = ?")
            params.append(value)
        params.append(session_id)
        await self._db.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params
        )
        await self._db.commit()

    async def bump_totals(
        self,
        session_id: str,
        *,
        turns: int = 0,
        steps: int = 0,
        cost: float = 0.0,
        wall_seconds: float = 0.0,
    ) -> None:
        await self._db.execute(
            """UPDATE sessions
               SET turns = turns + ?, steps = steps + ?, cost = cost + ?,
                   total_wall_seconds = total_wall_seconds + ?, updated_at = ?
               WHERE id = ?""",
            (turns, steps, cost, wall_seconds, time.time(), session_id),
        )
        await self._db.commit()

    async def touch(self, session_id: str) -> None:
        """Mark the session as active *now*, without changing anything else.

        The idle TTL reaper measures idleness from ``updated_at``, so every
        path that is real user or agent activity has to move it. Most already
        do as a side effect of writing a field; ``stop`` writes nothing.
        """
        await self._db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )
        await self._db.commit()

    async def sessions_with_status(self, status: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE status = ?", (status,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def idle_sessions_before(self, cutoff: float) -> list[dict]:
        """Sessions sitting ``idle`` since before ``cutoff`` — the reaper's set.

        Deliberately only ``idle``: a ``running`` session is busy however old
        its row is, and ``creating`` is still cloning.
        """
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE status = 'idle' AND updated_at < ?"
            " ORDER BY updated_at",
            (cutoff,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    # -- messages -------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        turn_id: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        message = {
            "id": new_id(),
            "session_id": session_id,
            "turn_id": turn_id,
            "role": role,
            "content": content,
            "created_at": time.time(),
            "meta": meta or {},
        }
        await self._db.execute(
            """INSERT INTO messages
               (id, session_id, turn_id, role, content, created_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message["id"],
                session_id,
                turn_id,
                role,
                content,
                message["created_at"],
                json.dumps(message["meta"]),
            ),
        )
        await self._db.commit()
        return message

    async def list_messages(self, session_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
        )
        return [_message_row(r) for r in await cursor.fetchall()]

    # -- turns (receipts) -----------------------------------------------------

    async def start_turn(
        self, session_id: str, turn_id: str, *, model: str, gt_status: str
    ) -> dict:
        started_at = time.time()
        await self._db.execute(
            """INSERT INTO turns
               (turn_id, session_id, started_at, gt_status, model)
               VALUES (?, ?, ?, ?, ?)""",
            (turn_id, session_id, started_at, gt_status, model),
        )
        await self._db.commit()
        return {"turn_id": turn_id, "started_at": started_at}

    async def finish_turn(
        self,
        turn_id: str,
        *,
        n_calls: int,
        cost: float,
        finish_reason: str,
        patch_sha256: str | None,
        wall_seconds: float = 0.0,
    ) -> None:
        await self._db.execute(
            """UPDATE turns
               SET finished_at = ?, n_calls = ?, cost = ?, finish_reason = ?,
                   wall_seconds = ?, patch_sha256 = ?
               WHERE turn_id = ?""",
            (
                time.time(),
                n_calls,
                cost,
                finish_reason,
                wall_seconds,
                patch_sha256,
                turn_id,
            ),
        )
        await self._db.commit()

    async def list_turns(self, session_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,)
        )
        return [
            {
                "turn_id": r["turn_id"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "n_calls": r["n_calls"],
                "cost": r["cost"],
                "wall_seconds": r["wall_seconds"],
                "finish_reason": r["finish_reason"] or "",
                "patch_sha256": r["patch_sha256"],
                "gt_status": r["gt_status"],
                "model": r["model"],
            }
            for r in await cursor.fetchall()
        ]

    # -- diff snapshots -------------------------------------------------------

    async def add_diff_snapshot(
        self,
        session_id: str,
        *,
        event_id: int,
        turn_id: str | None,
        step: int,
        patch_sha256: str | None,
        files: list[dict],
        patch: str,
        truncated: bool,
    ) -> int:
        """Record the workspace diff as of ``event_id`` (a ``tool_result``)."""
        cursor = await self._db.execute(
            """INSERT INTO diff_snapshots
               (session_id, event_id, turn_id, step, patch_sha256, files_json,
                patch, truncated, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                int(event_id),
                turn_id,
                int(step),
                patch_sha256,
                json.dumps(files),
                patch,
                1 if truncated else 0,
                time.time(),
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def latest_diff_snapshot(
        self, session_id: str, through_event: int
    ) -> dict | None:
        """The newest snapshot at or before ``through_event``, if there is one."""
        cursor = await self._db.execute(
            """SELECT * FROM diff_snapshots
               WHERE session_id = ? AND event_id <= ?
               ORDER BY event_id DESC, id DESC LIMIT 1""",
            (session_id, int(through_event)),
        )
        row = await cursor.fetchone()
        return _diff_snapshot_row(row) if row is not None else None

    async def count_diff_snapshots(self, session_id: str) -> int:
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM diff_snapshots WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # -- events ---------------------------------------------------------------

    async def append_event(
        self,
        session_id: str,
        event_type: str,
        data: dict,
        timestamp: float | None = None,
        turn_id: str | None = None,
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO events (session_id, turn_id, type, data_json, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session_id,
                turn_id if turn_id is not None else data.get("turn_id"),
                event_type,
                json.dumps(data),
                timestamp or time.time(),
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def get_events(
        self, session_id: str, after_id: int = 0, limit: int = 5000
    ) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT id, session_id, turn_id, type, data_json, timestamp
               FROM events WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?""",
            (session_id, after_id, limit),
        )
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "turn_id": r["turn_id"],
                "type": r["type"],
                "data": json.loads(r["data_json"]),
                "timestamp": r["timestamp"],
            }
            for r in await cursor.fetchall()
        ]


def _diff_snapshot_row(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "event_id": row["event_id"],
        "turn_id": row["turn_id"],
        "step": row["step"],
        "patch_sha256": row["patch_sha256"],
        "files": json.loads(row["files_json"] or "[]"),
        "patch": row["patch"] or "",
        "truncated": bool(row["truncated"]),
        "created_at": row["created_at"],
    }


def _message_row(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "meta": json.loads(row["meta_json"] or "{}"),
    }
