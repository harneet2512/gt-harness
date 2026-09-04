"""Tests for cloud.server.store.SessionStore (chat schema).

FAKE BOUNDARY: none — this exercises a real SQLite database end to end.
"""
from __future__ import annotations

import json

import aiosqlite
import pytest
import pytest_asyncio

from cloud.server.store import SCHEMA_VERSION, SessionStore

REPO = "https://github.com/owner/repo"


@pytest_asyncio.fixture
async def store():
    s = SessionStore(":memory:")
    await s.init()
    yield s
    await s.close()


async def _session(store: SessionStore, **overrides) -> str:
    body = {"repo": REPO, "ref": "main", "model": "deepseek/deepseek-v4-flash"}
    body.update(overrides)
    return await store.create_session(**body)


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_init_stamps_the_schema_version(store: SessionStore) -> None:
    cursor = await store._db.execute("PRAGMA user_version")
    assert (await cursor.fetchone())[0] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_init_drops_and_recreates_a_stale_schema(tmp_path) -> None:
    db_path = str(tmp_path / "old.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, task TEXT)")
        await db.execute("INSERT INTO sessions VALUES ('old', 'legacy row')")
        await db.commit()

    store = SessionStore(db_path)
    await store.init()
    try:
        assert await store.get_session("old") is None
        session_id = await _session(store)
        assert (await store.get_session(session_id))["status"] == "creating"
    finally:
        await store.close()


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_session_starts_in_creating(store: SessionStore) -> None:
    session_id = await _session(store, ref="feature", gt_mode="advisory")
    session = await store.get_session(session_id)

    assert len(session_id) == 12
    assert session["repo"] == REPO
    assert session["ref"] == "feature"
    assert session["gt_mode"] == "advisory"
    assert session["gt_status"] == "pending", "GT is pending until the index builds"
    assert session["status"] == "creating"
    assert session["turns"] == 0 and session["steps"] == 0 and session["cost"] == 0.0
    assert session["current_turn_id"] is None
    assert session["created_at"] > 0 and session["updated_at"] > 0
    assert json.loads(session["config_json"]) == {}


@pytest.mark.asyncio
async def test_gt_off_sessions_report_gt_status_off(store: SessionStore) -> None:
    session_id = await _session(store, gt_mode="off")
    assert (await store.get_session(session_id))["gt_status"] == "off"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown(store: SessionStore) -> None:
    assert await store.get_session("nonexistent") is None


@pytest.mark.asyncio
async def test_list_sessions_newest_first(store: SessionStore) -> None:
    first = await _session(store)
    second = await _session(store)
    assert [s["id"] for s in await store.list_sessions()] == [second, first]


@pytest.mark.asyncio
async def test_state_machine_allows_the_chat_lifecycle(store: SessionStore) -> None:
    session_id = await _session(store)
    await store.update_status(session_id, "idle", workspace_path="/w", base_sha="abc")
    await store.update_status(session_id, "running", current_turn_id="t1")
    await store.update_status(session_id, "idle", current_turn_id=None)
    await store.update_status(session_id, "running", current_turn_id="t2")
    await store.update_status(session_id, "closed")

    session = await store.get_session(session_id)
    assert session["status"] == "closed"
    assert session["workspace_path"] == "/w"
    assert session["base_sha"] == "abc"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ([], "running"),          # creating -> running
        (["idle"], "failed"),     # idle -> failed
        (["idle", "closed"], "idle"),
        ([], "stopped"),          # stopped is an event, never a status
    ],
)
async def test_state_machine_rejects_invalid_transitions(
    store: SessionStore, path: list[str], bad: str
) -> None:
    session_id = await _session(store)
    for status in path:
        await store.update_status(session_id, status)
    with pytest.raises(ValueError, match="invalid transition"):
        await store.update_status(session_id, bad)


@pytest.mark.asyncio
async def test_update_session_rejects_unknown_fields(store: SessionStore) -> None:
    session_id = await _session(store)
    with pytest.raises(ValueError, match="unknown session fields"):
        await store.update_session(session_id, task="there is no task any more")


@pytest.mark.asyncio
async def test_bump_totals_accumulates(store: SessionStore) -> None:
    session_id = await _session(store)
    await store.bump_totals(session_id, turns=1, steps=3, cost=0.02)
    await store.bump_totals(session_id, turns=1, steps=2, cost=0.01)
    session = await store.get_session(session_id)
    assert session["turns"] == 2
    assert session["steps"] == 5
    assert session["cost"] == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_sessions_with_status_finds_interrupted_runs(
    store: SessionStore,
) -> None:
    running = await _session(store)
    await store.update_status(running, "idle")
    await store.update_status(running, "running", current_turn_id="t1")
    await _session(store)

    found = await store.sessions_with_status("running")
    assert [s["id"] for s in found] == [running]
    assert len(await store.sessions_with_status("creating")) == 1


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_messages_keep_insertion_order_and_meta(store: SessionStore) -> None:
    session_id = await _session(store)
    user = await store.add_message(session_id, role="user", content="do it")
    agent = await store.add_message(
        session_id,
        role="agent",
        content="done",
        turn_id="t1",
        meta={"finish_reason": "reply", "n_calls": 3, "files_changed": ["a.py"]},
    )

    messages = await store.list_messages(session_id)
    assert [m["id"] for m in messages] == [user["id"], agent["id"]]
    assert [m["role"] for m in messages] == ["user", "agent"]
    assert messages[0]["turn_id"] is None
    assert messages[1]["turn_id"] == "t1"
    assert messages[1]["meta"]["finish_reason"] == "reply"
    assert messages[1]["meta"]["files_changed"] == ["a.py"]
    assert messages[0]["created_at"] <= messages[1]["created_at"]


@pytest.mark.asyncio
async def test_messages_are_scoped_to_their_session(store: SessionStore) -> None:
    one, two = await _session(store), await _session(store)
    await store.add_message(one, role="user", content="mine")
    assert await store.list_messages(two) == []


# --------------------------------------------------------------------------
# turns (receipts)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_turn_receipts_round_trip(store: SessionStore) -> None:
    session_id = await _session(store)
    await store.start_turn(session_id, "t1", model="m", gt_status="off")
    await store.finish_turn(
        "t1", n_calls=3, cost=0.03, finish_reason="reply", patch_sha256="deadbeef"
    )
    await store.start_turn(session_id, "t2", model="m", gt_status="off")

    receipts = await store.list_turns(session_id)
    assert [r["turn_id"] for r in receipts] == ["t1", "t2"]
    assert receipts[0]["n_calls"] == 3
    assert receipts[0]["cost"] == pytest.approx(0.03)
    assert receipts[0]["finish_reason"] == "reply"
    assert receipts[0]["patch_sha256"] == "deadbeef"
    assert receipts[0]["finished_at"] >= receipts[0]["started_at"]
    assert receipts[1]["finished_at"] is None, "an open turn has no finish time"


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_events_are_ordered_and_carry_turn_id(store: SessionStore) -> None:
    session_id = await _session(store)
    first = await store.append_event(session_id, "turn_started", {"turn_id": "t1"})
    second = await store.append_event(
        session_id, "assistant", {"turn_id": "t1", "content": "hi"}, 101.0
    )
    await store.append_event(session_id, "lifecycle", {"status": "idle"})

    events = await store.get_events(session_id)
    assert [e["id"] for e in events] == [first, second, first + 2]
    assert events[0]["turn_id"] == "t1", "turn_id is lifted out of the payload"
    assert events[1]["timestamp"] == 101.0
    assert events[2]["turn_id"] is None
    assert events[1]["data"] == {"turn_id": "t1", "content": "hi"}

    tail = await store.get_events(session_id, after_id=first)
    assert [e["id"] for e in tail] == [second, first + 2]
    assert await store.get_events(session_id, after_id=first + 2) == []
