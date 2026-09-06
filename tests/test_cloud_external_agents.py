"""External agents: agents we watch but never run (HAR-84).

An external agent is a **worker we do not execute**. A local Claude Code or
Codex session (or one of *their* subagents) registers itself against a
session, gets a scoped ingest token, and pushes its own events at us; the
server translates each one onto the parent's stream as the frame a worker
would have published, with ``agent_id`` set, so the browser's existing worker
code path draws it. Nothing is ever executed for one: no clone, no sandbox,
no model call, no concurrency slot.

FAKE BOUNDARY (module-wide, single fake): the model provider, exactly as in
``tests/test_cloud_chat.py`` — whose real-server harness this module reuses.
Real: the FastAPI app on a loopback uvicorn port, JWT auth (both credentials:
the user's sign-in and the agent's ingest token, really signed and really
verified), the SQLite store, the event bus and its SSE encoder. The external
agents in this module are *genuinely* external: no code of ours runs for
them, which is the entire point, so there is nothing left to fake.

Run: ``python -m pytest tests/test_cloud_external_agents.py -q`` from the repo
root.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
import pytest

from cloud.server import models as models_module
from cloud.server import runner as runner_module
from tests import test_cloud_chat as chat
from tests.test_cloud_chat import (
    DEFAULT_SCRIPT,
    JWT_SECRET,
    POLL_INTERVAL,
    POLL_TIMEOUT,
    Harness,
    _create_idle,
    _has,
    _read_sse,
    _session,
)

_HARNESS_IMPL = getattr(chat.harness, "__wrapped__", chat.harness)


@pytest.fixture(scope="session")
def seed_repo(tmp_path_factory):
    """One real git repository, cloned by every session in this module."""
    return chat._make_seed_repo(tmp_path_factory.mktemp("seed"))


@pytest.fixture
def harness(seed_repo, tmp_path, monkeypatch):
    """The chat module's real-server harness, verbatim."""
    yield from _HARNESS_IMPL(seed_repo, tmp_path, monkeypatch)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _register(h: Harness, session_id: str, **body: Any):
    payload = {"agent_kind": "claude-code", "label": "Claude Code"}
    payload.update(body)
    return h.client.post(
        f"/api/sessions/{session_id}/external-agents",
        json=payload,
        headers=h.auth,
    )


def _registered(h: Harness, session_id: str, **body: Any) -> dict:
    response = _register(h, session_id, **body)
    assert response.status_code == 201, response.text
    return response.json()


def _ingest_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ingest(h: Harness, agent_id: str, token: str, *events: dict):
    return h.client.post(
        f"/api/external-agents/{agent_id}/events",
        json={"events": list(events)},
        headers=_ingest_headers(token),
    )


def _ingest_ok(h: Harness, agent_id: str, token: str, *events: dict) -> int:
    response = _ingest(h, agent_id, token, *events)
    assert response.status_code == 202, response.text
    return response.json()["accepted"]


def _finish(h: Harness, agent_id: str, token: str, **body: Any):
    return h.client.post(
        f"/api/external-agents/{agent_id}/finish",
        json={"status": "done", **body},
        headers=_ingest_headers(token),
    )


def _agents(h: Harness, session_id: str) -> list[dict]:
    response = h.client.get(f"/api/sessions/{session_id}/agents", headers=h.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _payloads(frames: list[dict], event_type: str) -> list[dict]:
    return [f["payload"]["data"] for f in frames if f["event"] == event_type]


def _wait_row(h: Harness, agent_id: str, predicate) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT
    row: dict = {}
    while time.monotonic() < deadline:
        row = _session(h, agent_id)
        if predicate(row):
            return row
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"external agent {agent_id} never settled; last={row}")


def _mine(frames: list[dict], agent_id: str) -> list[dict]:
    return [f for f in frames if f["payload"]["data"].get("agent_id") == agent_id]


# --------------------------------------------------------------------------
# 1: registration
# --------------------------------------------------------------------------
def test_registering_an_external_agent_runs_nothing(harness: Harness) -> None:
    """A row, a token and a URL — and not one line of agent execution."""
    parent_id = _create_idle(harness)
    before = harness.client.get("/api/sessions", headers=harness.auth).json()

    body = _registered(
        harness,
        parent_id,
        agent_kind="claude-code",
        label="Claude Code (local)",
        task="refactor the parser",
        cwd="/home/me/work/repo",
    )

    agent = body["agent"]
    assert agent["role"] == "external"
    assert agent["parent_id"] == parent_id
    assert agent["status"] == "running"
    assert agent["agent_kind"] == "claude-code"
    assert agent["label"] == "Claude Code (local)"
    assert agent["task"] == "refactor the parser"
    assert agent["external_cwd"] == "/home/me/work/repo"
    assert agent["gt_mode"] == "off" and agent["gt_status"] == "off"
    assert agent["parent_agent_id"] is None
    assert body["ingest_token"] and body["ingest_url"].endswith(
        f"/api/external-agents/{agent['id']}/events"
    )
    # nothing was spent: no model call, and no turn slot taken
    assert harness.models == {}, "no agent was built for an external agent"
    assert harness.agents == {}
    manager = chat.deps.get_manager()
    assert manager.running_count == 0 and manager.running_worker_count == 0
    # the only new session is the agent's own row
    after = harness.client.get("/api/sessions", headers=harness.auth).json()
    assert len(after) == len(before) + 1

    claims = jwt.decode(body["ingest_token"], JWT_SECRET, algorithms=["HS256"])
    assert claims["scope"] == "ingest"
    assert claims["aid"] == agent["id"] and claims["sid"] == parent_id
    # a week, not a day: an external agent may be pushing events for days
    assert claims["exp"] - claims["iat"] == runner_module_ingest_ttl()


def runner_module_ingest_ttl() -> int:
    from cloud.server.auth import ingest_ttl_seconds

    return ingest_ttl_seconds()


def test_the_agent_kind_is_a_bounded_slug(harness: Harness) -> None:
    parent_id = _create_idle(harness)

    assert _register(harness, parent_id, agent_kind="Claude-Code").status_code == 201
    assert _register(harness, parent_id, agent_kind="a b").status_code == 422
    assert _register(harness, parent_id, agent_kind="../x").status_code == 422
    assert _register(harness, parent_id, agent_kind="x" * 33).status_code == 422
    assert _register(harness, parent_id, label="   ").status_code == 422

    # ...and the one that was accepted was lowercased on the way in
    assert _agents(harness, parent_id)[0]["agent_kind"] == "claude-code"


def test_the_ingest_url_follows_the_public_base_url(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL is handed to a process on somebody's laptop, not to us."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agents.example.test/")
    parent_id = _create_idle(harness)

    body = _registered(harness, parent_id)

    assert body["ingest_url"] == (
        f"https://agents.example.test/api/external-agents/{body['agent']['id']}/events"
    )


def test_a_closed_session_cannot_host_an_external_agent(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    harness.client.post(f"/api/sessions/{parent_id}/close", headers=harness.auth)

    response = _register(harness, parent_id, label="too late")

    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]


# --------------------------------------------------------------------------
# 2: the ingest token is the whole boundary
# --------------------------------------------------------------------------
def test_the_ingest_token_works_and_a_user_jwt_does_not(harness: Harness) -> None:
    """Same secret, different scope — so the scope check is the boundary."""
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    assert _ingest(harness, agent_id, token, {"type": "assistant", "text": "hi"}) \
        .status_code == 202

    # the signed-in user's own JWT is refused here
    refused = harness.client.post(
        f"/api/external-agents/{agent_id}/events",
        json={"events": [{"type": "assistant", "text": "hi"}]},
        headers=harness.auth,
    )
    assert refused.status_code == 401, refused.text
    assert "ingest token" in refused.json()["detail"]


def test_an_ingest_token_cannot_read_sessions(harness: Harness) -> None:
    """The dangerous direction: a token handed to an adapter is not a sign-in."""
    parent_id = _create_idle(harness)
    token = _registered(harness, parent_id)["ingest_token"]

    listed = harness.client.get(
        "/api/sessions", headers=_ingest_headers(token)
    )
    single = harness.client.get(
        f"/api/sessions/{parent_id}", headers=_ingest_headers(token)
    )

    assert listed.status_code == 401, listed.text
    assert single.status_code == 401, single.text
    assert "ingest token" in listed.json()["detail"]


def test_a_token_for_another_agent_is_refused(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    first = _registered(harness, parent_id, label="one")
    second = _registered(harness, parent_id, label="two")

    crossed = _ingest(
        harness,
        second["agent"]["id"],
        first["ingest_token"],
        {"type": "assistant", "text": "not mine"},
    )

    assert crossed.status_code == 401, crossed.text
    assert "different agent" in crossed.json()["detail"]


def test_a_token_for_an_agent_that_is_gone_is_a_404(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    _registered(harness, parent_id)
    ghost = "deadbeefcafe"
    token = jwt.encode(
        {
            "aid": ghost,
            "sid": parent_id,
            "scope": "ingest",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    response = _ingest(harness, ghost, token, {"type": "assistant", "text": "?"})

    assert response.status_code == 404, response.text


def test_a_closed_agent_refuses_events(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]
    harness.client.post(
        f"/api/sessions/{parent_id}/agents/{agent_id}/close", headers=harness.auth
    )

    response = _ingest(harness, agent_id, token, {"type": "assistant", "text": "?"})

    assert response.status_code == 409, response.text


# --------------------------------------------------------------------------
# 3: events reach the parent's stream as worker frames
# --------------------------------------------------------------------------
def test_events_land_on_the_parents_stream_with_agent_id_and_files(
    harness: Harness,
) -> None:
    """The whole design: no new frame types, only a new source of them."""
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id, cwd="/home/me/work/repo")
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    accepted = _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "assistant", "text": "I will read the parser."},
        {
            "type": "tool_call",
            "name": "Read",
            "files": ["/home/me/work/repo/pkg/util.py", "app.py"],
        },
        {
            "type": "tool_result",
            "name": "Read",
            "ok": True,
            "output": "VALUE = 1",
            "files": ["pkg/util.py"],
        },
    )
    assert accepted == 3

    frames = _read_sse(
        harness, parent_id, until=_has("tool_result", agent_id=agent_id)
    )
    mine = _mine(frames, agent_id)
    assert {f["event"] for f in mine} == {"assistant", "tool_call", "tool_result"}

    assistant = _payloads(mine, "assistant")[0]
    assert assistant["content"] == "I will read the parser."

    call = _payloads(mine, "tool_call")[0]
    assert call["tool_name"] == "Read"
    # the cwd prefix is stripped, so both paths are repo-relative labels
    assert call["files"] == ["pkg/util.py", "app.py"]
    # no command was sent, so one was synthesised into the field a worker's
    # tool_call uses — the UI's existing renderer has something to show
    assert call["command"] == "Read pkg/util.py app.py"

    result = _payloads(mine, "tool_result")[0]
    assert result["ok"] is True and result["is_error"] is False
    assert result["files"] == ["pkg/util.py"]
    assert result["output"] == "VALUE = 1"

    # ...and the same frames are on the agent's OWN stream, untagged
    own = _read_sse(harness, agent_id, until=_has("tool_result"))
    assert all("agent_id" not in f["payload"]["data"] for f in own)


def test_a_client_command_is_used_verbatim(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "tool_call", "name": "Bash", "command": "pytest -q tests/"},
    )

    frames = _read_sse(harness, parent_id, until=_has("tool_call", agent_id=agent_id))
    call = _payloads(_mine(frames, agent_id), "tool_call")[0]
    assert call["command"] == "pytest -q tests/"
    assert call["files"] == []


def test_only_an_error_status_interrupts_the_stream(harness: Harness) -> None:
    """`agent_activity` is not a frame the UI knows; an error is worth a note."""
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "note": "thinking"},
        {"type": "status", "state": "error", "note": "the build broke"},
    )

    frames = _read_sse(harness, parent_id, until=_has("system_note", agent_id=agent_id))
    notes = [n for n in _payloads(frames, "system_note") if n.get("agent_id")]
    assert len(notes) == 1, "only the error state published a note"
    assert "the build broke" in notes[0]["content"]


def test_tool_calls_move_the_steps_counter_and_the_clock(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]
    created = body["agent"]["updated_at"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "tool_call", "name": "Edit", "files": ["a.py"]},
        {"type": "tool_call", "name": "Edit", "files": ["b.py"]},
        {"type": "assistant", "text": "done both"},
    )

    row = _wait_row(harness, agent_id, lambda r: r["steps"] == 2)
    assert row["updated_at"] > created, "idleness is measured from updated_at"
    assert row["last_message"] == "done both"


# --------------------------------------------------------------------------
# 4: the fleet list's live columns
# --------------------------------------------------------------------------
def test_activity_and_tokens_track_the_agent_and_never_go_backwards(
    harness: Harness,
) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]
    assert body["agent"]["activity"] is None
    assert body["agent"]["tokens"] is None, "a token count is never synthesised"

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "activity": "reading", "tokens": 1200},
    )
    assert _wait_row(harness, agent_id, lambda r: r["tokens"] == 1200)["activity"] == (
        "reading"
    )

    # a tool call can set the line without a second event
    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "tool_call", "name": "Edit", "activity": "editing pkg/util.py"},
    )
    assert _wait_row(harness, agent_id, lambda r: r["activity"] == "editing pkg/util.py")

    # a client that restarts its own counter must not count the fleet down
    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "tokens": 5},
    )
    time.sleep(0.05)
    assert _session(harness, agent_id)["tokens"] == 1200

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "tokens": 4000},
    )
    assert _wait_row(harness, agent_id, lambda r: r["tokens"] == 4000)


def test_activity_is_bounded(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "activity": "z" * 500},
    )

    row = _wait_row(harness, agent_id, lambda r: bool(r["activity"]))
    assert len(row["activity"]) == models_module.MAX_ACTIVITY_CHARS


# --------------------------------------------------------------------------
# 5: caps, truncation and path hygiene
# --------------------------------------------------------------------------
def test_an_over_long_batch_is_a_413(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    too_many = _ingest(
        harness,
        agent_id,
        token,
        *[{"type": "assistant", "text": "x"} for _ in range(101)],
    )
    too_big = _ingest(
        harness,
        agent_id,
        token,
        *[{"type": "assistant", "text": "y" * 5_000} for _ in range(100)],
    )

    assert too_many.status_code == 413, too_many.text
    assert too_big.status_code == 413, too_big.text
    # ...and exactly the caps are still accepted
    assert _ingest_ok(
        harness,
        agent_id,
        token,
        *[{"type": "assistant", "text": "x"} for _ in range(100)],
    ) == 100


def test_over_long_text_is_truncated_not_rejected(harness: Harness) -> None:
    """A clipped line beats a 422 that loses the whole batch."""
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "assistant", "text": "a" * 25_000},
        {"type": "tool_result", "name": "Bash", "ok": False, "output": "b" * 20_000},
    )

    frames = _read_sse(
        harness, parent_id, until=_has("tool_result", agent_id=agent_id)
    )
    mine = _mine(frames, agent_id)
    assert len(_payloads(mine, "assistant")[0]["content"]) == (
        models_module.MAX_INGEST_TEXT_CHARS
    )
    result = _payloads(mine, "tool_result")[0]
    assert len(result["output"]) == models_module.MAX_INGEST_OUTPUT_CHARS
    assert result["ok"] is False and result["is_error"] is True


def test_untrusted_paths_are_scrubbed_into_display_labels(
    harness: Harness,
) -> None:
    """These become graph labels. Nothing on this server opens one."""
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id, cwd="/home/me/work/repo")
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {
            "type": "tool_call",
            "name": "Edit",
            "files": [
                "/etc/passwd",
                "../../etc/shadow",
                "pkg/../../../etc/hosts",
                "C:/Windows/System32/config",
                "x" * 600,
                "/home/me/work/repo/pkg/util.py",
                "./app.py",
                "app.py",
                "pkg/util.py",
            ],
        },
    )

    frames = _read_sse(harness, parent_id, until=_has("tool_call", agent_id=agent_id))
    files = _payloads(_mine(frames, agent_id), "tool_call")[0]["files"]
    # absolute, traversing, over-long and duplicate paths are all gone
    assert files == ["pkg/util.py", "app.py"]


def test_at_most_fifty_files_survive_one_event(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    _ingest_ok(
        harness,
        agent_id,
        token,
        {
            "type": "tool_call",
            "name": "Grep",
            "files": [f"src/f{i}.py" for i in range(80)],
        },
    )

    frames = _read_sse(harness, parent_id, until=_has("tool_call", agent_id=agent_id))
    files = _payloads(_mine(frames, agent_id), "tool_call")[0]["files"]
    assert len(files) == models_module.MAX_INGEST_FILES
    assert files[0] == "src/f0.py"


def test_over_the_rate_limit_the_surplus_is_dropped_not_five_hundredth(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 on a batch makes a chatty adapter retry and cost more."""
    monkeypatch.setenv("MAX_INGEST_EVENTS_PER_MINUTE", "5")
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    first = _ingest_ok(
        harness,
        agent_id,
        token,
        *[{"type": "assistant", "text": f"line {i}"} for i in range(8)],
    )
    second = _ingest_ok(
        harness, agent_id, token, {"type": "assistant", "text": "one more"}
    )

    assert first == 5, "the batch was accepted; the surplus was dropped"
    assert second == 0
    assert runner_module.DEFAULT_MAX_INGEST_EVENTS_PER_MINUTE == 600


# --------------------------------------------------------------------------
# 6: nesting, listing, finishing, closing
# --------------------------------------------------------------------------
def test_a_subagent_registers_as_its_own_agent(harness: Harness) -> None:
    """No subagent event type: nesting is a row with a second edge."""
    parent_id = _create_idle(harness)
    lead = _registered(harness, parent_id, label="Claude Code")["agent"]

    child = _registered(
        harness,
        parent_id,
        agent_kind="claude-code",
        label="Explore subagent",
        parent_agent_id=lead["id"],
    )["agent"]

    assert child["parent_agent_id"] == lead["id"]
    # parent_id still names the owning SESSION, not the parent agent
    assert child["parent_id"] == parent_id

    frames = _read_sse(
        harness, parent_id, until=_has("agent_spawned", worker_id=child["id"])
    )
    spawned = [
        s for s in _payloads(frames, "agent_spawned") if s["worker_id"] == child["id"]
    ][0]
    assert spawned["external"] is True
    assert spawned["agent_kind"] == "claude-code"
    assert spawned["label"] == "Explore subagent"
    assert spawned["parent_agent_id"] == lead["id"]


def test_a_bogus_parent_agent_is_refused(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    other_id = _create_idle(harness)
    stranger = _registered(harness, other_id, label="somebody else's")["agent"]

    missing = _register(harness, parent_id, parent_agent_id="deadbeefcafe")
    crossed = _register(harness, parent_id, parent_agent_id=stranger["id"])

    assert missing.status_code == 400, missing.text
    assert crossed.status_code == 400, crossed.text


def test_the_agents_list_carries_external_agents_in_order(
    harness: Harness,
) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    first = _registered(harness, parent_id, label="first")["agent"]
    second = _registered(harness, parent_id, agent_kind="codex", label="second")[
        "agent"
    ]
    third = _registered(
        harness, parent_id, label="third", parent_agent_id=second["id"]
    )["agent"]

    listed = _agents(harness, parent_id)

    assert [a["id"] for a in listed] == [first["id"], second["id"], third["id"]]
    assert [a["created_at"] for a in listed] == sorted(
        a["created_at"] for a in listed
    )
    assert [a["role"] for a in listed] == ["external"] * 3
    assert [a["agent_kind"] for a in listed] == ["claude-code", "codex", "claude-code"]
    assert [a["label"] for a in listed] == ["first", "second", "third"]
    assert listed[2]["parent_agent_id"] == second["id"]


def test_finishing_reports_and_closes_on_the_parents_stream(
    harness: Harness,
) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]
    _ingest_ok(
        harness,
        agent_id,
        token,
        {"type": "status", "state": "working", "activity": "wrapping up", "tokens": 900},
    )
    _wait_row(harness, agent_id, lambda r: r["tokens"] == 900)

    response = _finish(harness, agent_id, token, summary="Rewrote the parser.")

    assert response.status_code == 200, response.text
    row = response.json()
    assert row["status"] == "idle"
    # the outcome is said in the vocabulary every worker frame already uses,
    # and the external-only word rides beside it on the report frame
    assert row["report"]["finish_reason"] == "reply"
    assert row["report"]["reply_excerpt"] == "Rewrote the parser."

    frames = _read_sse(
        harness, parent_id, until=_has("agent_closed", worker_id=agent_id)
    )
    types = [f["event"] for f in frames if f["payload"]["data"].get("worker_id") == agent_id]
    assert types.index("agent_report") < types.index("agent_closed")
    report = _payloads(frames, "agent_report")[0]
    assert report["finish_reason"] == "reply" and report["status"] == "done"
    assert report["content"] == "Rewrote the parser."
    assert report["activity"] == "wrapping up" and report["tokens"] == 900
    assert report["external"] is True

    # a stream is not a record: the parent's messages carry it after a reload
    messages = harness.client.get(
        f"/api/sessions/{parent_id}/messages", headers=harness.auth
    ).json()
    assert any(m["meta"].get("agent_id") == agent_id for m in messages)


def test_finishing_with_an_error_fails_the_row(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    body = _registered(harness, parent_id)
    agent_id, token = body["agent"]["id"], body["ingest_token"]

    response = _finish(
        harness, agent_id, token, status="error", summary="it broke"
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["report"]["finish_reason"] == "error"
    assert response.json()["report"]["reply_excerpt"] == "it broke"


def test_closing_the_session_closes_its_external_agents(harness: Harness) -> None:
    parent_id = _create_idle(harness)
    first = _registered(harness, parent_id, label="one")["agent"]
    second = _registered(harness, parent_id, label="two")["agent"]

    response = harness.client.post(
        f"/api/sessions/{parent_id}/close", headers=harness.auth
    )

    assert response.status_code == 200, response.text
    listed = _agents(harness, parent_id)
    assert [a["id"] for a in listed] == [first["id"], second["id"]]
    assert [a["status"] for a in listed] == ["closed", "closed"]
    assert all(a["closed_reason"] == "user" for a in listed)


def test_an_external_agent_never_runs_a_turn(harness: Harness) -> None:
    """It is a worker we do not execute — there is nothing to message."""
    parent_id = _create_idle(harness)
    agent_id = _registered(harness, parent_id)["agent"]["id"]

    response = harness.client.post(
        f"/api/sessions/{agent_id}/messages",
        json={"content": "do something"},
        headers=harness.auth,
    )

    assert response.status_code == 409, response.text
    assert "does not run turns" in response.json()["detail"]
    assert harness.models == {} and harness.agents == {}
