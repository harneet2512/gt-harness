"""Tests for cloud.server.store.SessionStore."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from cloud.server.store import SessionStore


@pytest_asyncio.fixture
async def store():
    s = SessionStore(":memory:")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_session_returns_id(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/owner/repo",
        ref="main",
        task="Fix the bug",
        model="deepseek/deepseek-v4-flash",
    )
    assert isinstance(sid, str)
    assert len(sid) == 12


@pytest.mark.asyncio
async def test_get_session_returns_data(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/owner/repo",
        ref="feature-branch",
        task="Add tests",
        model="gpt-4",
        gt_mode="engine",
    )
    session = await store.get_session(sid)
    assert session is not None
    assert session["id"] == sid
    assert session["repo"] == "https://github.com/owner/repo"
    assert session["ref"] == "feature-branch"
    assert session["task"] == "Add tests"
    assert session["model"] == "gpt-4"
    assert session["gt_mode"] == "engine"
    assert session["status"] == "pending"
    assert session["created_at"] > 0


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown(store: SessionStore) -> None:
    assert await store.get_session("nonexistent") is None


@pytest.mark.asyncio
async def test_list_sessions_newest_first(store: SessionStore) -> None:
    sid1 = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="first", model="m",
    )
    sid2 = await store.create_session(
        repo="https://github.com/b/b", ref="main", task="second", model="m",
    )
    sessions = await store.list_sessions()
    assert len(sessions) == 2
    assert sessions[0]["id"] == sid2
    assert sessions[1]["id"] == sid1


@pytest.mark.asyncio
async def test_update_status_pending_to_running(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    await store.update_status(sid, "running")
    session = await store.get_session(sid)
    assert session["status"] == "running"
    assert session["started_at"] is not None


@pytest.mark.asyncio
async def test_update_status_running_to_completed(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    await store.update_status(sid, "running")
    await store.update_status(sid, "completed", steps=5, cost=0.12)
    session = await store.get_session(sid)
    assert session["status"] == "completed"
    assert session["finished_at"] is not None
    assert session["steps"] == 5
    assert session["cost"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_update_status_rejects_invalid_transition(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    await store.update_status(sid, "running")
    await store.update_status(sid, "completed")
    with pytest.raises(ValueError, match="invalid transition"):
        await store.update_status(sid, "running")


@pytest.mark.asyncio
async def test_update_status_rejects_pending_to_completed(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    with pytest.raises(ValueError, match="invalid transition"):
        await store.update_status(sid, "completed")


@pytest.mark.asyncio
async def test_append_and_get_events(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    eid1 = await store.append_event(sid, "assistant", {"content": "hello"}, 100.0)
    eid2 = await store.append_event(sid, "tool_result", {"output": "ok"}, 101.0)

    events = await store.get_events(sid)
    assert len(events) == 2
    assert events[0]["type"] == "assistant"
    assert events[0]["data"] == {"content": "hello"}
    assert events[0]["timestamp"] == 100.0
    assert events[1]["type"] == "tool_result"
    assert events[1]["data"] == {"output": "ok"}

    events_after = await store.get_events(sid, after_id=eid1)
    assert len(events_after) == 1
    assert events_after[0]["id"] == eid2


@pytest.mark.asyncio
async def test_store_result(store: SessionStore) -> None:
    sid = await store.create_session(
        repo="https://github.com/a/a", ref="main", task="t", model="m",
    )
    result = {"patch": "diff --git a/f.py", "receipt": {"n_calls": 3}}
    await store.store_result(sid, result)
    session = await store.get_session(sid)
    assert session["result_json"] is not None
    loaded = json.loads(session["result_json"])
    assert loaded["patch"] == "diff --git a/f.py"
    assert loaded["receipt"]["n_calls"] == 3
