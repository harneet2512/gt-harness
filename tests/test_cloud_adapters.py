"""Tests for the client-side adapters in ``cloud/adapters/``.

No network and no mocking of the transport: a real ``http.server`` on an
ephemeral port is the receiving end, so what is exercised is the same
``urllib`` path that runs on a user's machine.

The Claude Code payload fixtures below are **captured**, not invented. They come
from running ``claude -p`` (2.1.263) against a hook that wrote its stdin to a
file, with the prompt "Use the Explore subagent to tell me what files are in
this directory, then read sample.txt yourself." Content is shortened; every key
and every value shape is as it arrived.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cloud.adapters import gt_cloud_tail
from cloud.adapters.claude_code import gt_cloud_hook, transcript
from cloud.adapters.codex import gt_cloud_codex
from cloud.adapters.gt_cloud_bridge import (
    BREAKER_FAILURE_THRESHOLD,
    HOOK_RETRIES,
    HOOK_TIMEOUT,
    MAX_EVENTS_PER_BATCH,
    Bridge,
    BridgeConfig,
    breaker_is_open,
    extract_paths_from_command,
    record_breaker_failure,
    reset_breaker,
    to_repo_relative,
)

SESSION_ID = "sess-42"
AGENT_ID = "ext-agent-1"
INGEST_TOKEN = "ingest-secret"


# --- the fake server --------------------------------------------------------


class _Recorder:
    """Everything the server saw, and what it should answer with next."""

    def __init__(self) -> None:
        self.registrations: list[dict] = []
        self.batches: list[dict] = []  # {"agent_id": str, "events": [...]}
        self.finishes: list[dict] = []
        self.event_statuses: list[int] = []  # popped per request; 200 when empty
        self.register_status = 201
        self.lock = threading.Lock()
        self.next_agent = 0

    @property
    def events(self) -> list[dict]:
        return [event for batch in self.batches for event in batch["events"]]

    def events_for(self, agent_id: str) -> list[dict]:
        """Only the events posted to one agent's ingest URL - i.e. onto its card."""
        return [
            event
            for batch in self.batches
            if batch["agent_id"] == agent_id
            for event in batch["events"]
        ]

    def next_event_status(self) -> int:
        with self.lock:
            return self.event_statuses.pop(0) if self.event_statuses else 200


def _make_handler(recorder: _Recorder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep pytest output clean
            return

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}

        def _reply(self, status: int, payload: dict) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            body = self._body()
            if self.path.endswith("/external-agents"):
                with recorder.lock:
                    recorder.next_agent += 1
                    agent_id = f"{AGENT_ID}-{recorder.next_agent}"
                    recorder.registrations.append(
                        {"path": self.path, "body": body,
                         "auth": self.headers.get("Authorization"), "agent_id": agent_id}
                    )
                if recorder.register_status != 201:
                    self._reply(recorder.register_status, {"detail": "nope"})
                    return
                self._reply(201, {
                    "agent": {"id": agent_id, "label": body.get("label")},
                    "ingest_token": INGEST_TOKEN,
                    "ingest_url": f"/api/external-agents/{agent_id}/events",
                })
                return
            if self.path.endswith("/events"):
                status = recorder.next_event_status()
                if status == 200:
                    # /api/external-agents/{agent_id}/events - the id in the URL
                    # is what decides which card an event lands on.
                    agent_id = self.path.rsplit("/", 2)[-2]
                    with recorder.lock:
                        recorder.batches.append(
                            {"agent_id": agent_id, "events": list(body.get("events") or [])}
                        )
                self._reply(status, {"ok": status == 200})
                return
            if self.path.endswith("/finish"):
                with recorder.lock:
                    recorder.finishes.append({"body": body,
                                              "auth": self.headers.get("Authorization")})
                self._reply(200, {"ok": True})
                return
            self._reply(404, {"detail": "no route"})

    return Handler


@pytest.fixture
def server():
    recorder = _Recorder()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(recorder))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    recorder.origin = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield recorder
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def config(server, tmp_path):
    return BridgeConfig(
        origin=server.origin,
        session_id=SESSION_ID,
        user_token="user-jwt",
        timeout=3.0,
        flush_interval=0.05,
        queue_max=1000,
        retries=2,
        backoff=0.01,
        state_dir=str(tmp_path / "state"),
    )


def _wait_for(predicate, timeout=5.0):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- registration and streaming ---------------------------------------------


def test_registers_then_streams_with_the_ingest_token(server, config, tmp_path):
    bridge = Bridge(agent_kind="claude-code", label="claude-code · repo",
                    task="fix the parser", cwd=str(tmp_path), state_key="host-session",
                    config=config, background=False)

    assert bridge.start() is True

    assert len(server.registrations) == 1
    registration = server.registrations[0]
    assert registration["path"] == f"/api/sessions/{SESSION_ID}/external-agents"
    assert registration["auth"] == "Bearer user-jwt"
    assert registration["body"] == {
        "agent_kind": "claude-code", "label": "claude-code · repo",
        "task": "fix the parser", "cwd": os.path.normpath(str(tmp_path)),
        "parent_agent_id": None,
    }

    bridge.assistant("renamed the handler")
    bridge.status("working", activity="Editing app.py")
    assert bridge.flush() == 2
    assert [event["type"] for event in server.events] == ["assistant", "status"]
    assert server.events[1]["activity"] == "Editing app.py"

    bridge.finish("done", "all good")
    assert server.finishes[0]["auth"] == f"Bearer {INGEST_TOKEN}"
    assert server.finishes[0]["body"]["status"] == "done"


def test_a_second_process_reuses_the_registration(server, config, tmp_path):
    first = Bridge(label="a", cwd=str(tmp_path), state_key="same-host-session",
                   config=config, background=False)
    assert first.start()
    first.close()

    second = Bridge(label="a", cwd=str(tmp_path), state_key="same-host-session",
                    config=config, background=False)
    assert second.start()

    assert second.reused_registration is True
    assert len(server.registrations) == 1, "one host session must be one card"
    assert second.agent_id == first.agent_id


def test_preauthorised_config_skips_registration(server, tmp_path):
    config = BridgeConfig(origin=server.origin, agent_token=INGEST_TOKEN,
                          agent_id="ext-preauth", backoff=0.01,
                          state_dir=str(tmp_path / "state"))
    bridge = Bridge(label="child", cwd=str(tmp_path), config=config, background=False)

    assert bridge.start() is True
    bridge.status("working")
    bridge.flush()

    assert server.registrations == []
    assert len(server.events) == 1


# --- batching, coalescing and the bounded queue -----------------------------


def test_batches_are_capped_and_coalesced_on_the_background_thread(server, config, tmp_path):
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=True)
    assert bridge.start()
    try:
        for index in range(250):
            bridge.assistant(f"line {index}")
        assert _wait_for(lambda: len(server.events) >= 250, timeout=10)
    finally:
        bridge.close()

    assert all(len(batch["events"]) <= MAX_EVENTS_PER_BATCH for batch in server.batches)
    # 250 events coalesced into a handful of posts, not 250 of them.
    assert len(server.batches) < 20
    assert [event["text"] for event in server.events][:3] == ["line 0", "line 1", "line 2"]


def test_events_arrive_in_order_when_a_flush_races_the_flush_thread(server, config, tmp_path):
    """A concurrent finish() must not overtake the background thread's batch.

    Found on a real Codex session: the token count arrived non-monotonic because
    two batches were in flight at once and the later one won.
    """
    config.flush_interval = 0.01
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=True)
    assert bridge.start()

    for index in range(400):
        bridge.status("working", tokens=index + 1)
        if index % 50 == 0:
            time.sleep(0.005)  # let the flush thread get a batch in flight
    bridge.finish("done", "raced")

    tokens = [event["tokens"] for event in server.events if "tokens" in event]
    assert tokens, "the run must have delivered something to be worth asserting on"
    assert tokens == sorted(tokens), "batches must arrive in the order they were taken"


def test_full_queue_drops_the_oldest_and_reports_the_count(server, config, tmp_path):
    config.queue_max = 10
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()

    for index in range(30):
        bridge.assistant(f"line {index}")

    assert bridge.dropped == 20
    bridge.flush()

    texts = [event.get("text") for event in server.events if event["type"] == "assistant"]
    assert texts == [f"line {index}" for index in range(20, 30)], "the oldest go, not the newest"
    notices = [event for event in server.events if event["type"] == "status"]
    assert len(notices) == 1
    assert "dropped 20 events" in notices[0]["note"]

    bridge.finish("done", "finished")
    assert "dropped 20 events" in server.finishes[0]["body"]["summary"]


def test_an_oversized_event_is_truncated_not_dropped(server, config, tmp_path):
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()

    bridge.tool_result("Bash", ok=True, output="x" * 500_000)
    bridge.flush()

    output = server.events[0]["output"]
    assert len(output) < 5000
    assert "more characters" in output


# --- retries and giving up --------------------------------------------------


def test_retries_on_5xx_then_succeeds(server, config, tmp_path):
    server.event_statuses = [500, 503]
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()

    bridge.assistant("survives a bad gateway")
    assert bridge.flush() == 1

    assert len(server.events) == 1
    assert bridge.dropped == 0


def test_gives_up_quietly_on_4xx(server, config, tmp_path):
    server.event_statuses = [401]
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()

    bridge.assistant("never arrives")
    assert bridge.flush() == 0  # no exception, no retry storm

    assert server.events == []
    assert bridge.dropped == 1
    # A revoked token disables the bridge instead of hammering the server.
    assert bridge.emit({"type": "assistant", "text": "still quiet"}) is False


def test_an_unreachable_server_never_raises(tmp_path):
    config = BridgeConfig(origin="http://127.0.0.1:1", session_id=SESSION_ID,
                          user_token="t", timeout=0.3, retries=0, backoff=0.01,
                          state_dir=str(tmp_path / "state"))
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)

    assert bridge.start() is False
    assert bridge.assistant("dropped on the floor") is False
    assert bridge.finish("done") is False


def test_registration_failure_disables_the_bridge(server, config, tmp_path):
    server.register_status = 403
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)

    assert bridge.start() is False
    assert bridge.enabled is False


# --- the circuit breaker ----------------------------------------------------
#
# The point of all of this: when the deployment is down, a local session must
# stop paying a timeout on every single tool call.


def _down_config(tmp_path, **overrides):
    """A config pointed at a closed port, so every request fails fast."""
    settings = dict(
        origin="http://127.0.0.1:1", session_id=SESSION_ID, user_token="t",
        timeout=0.25, retries=0, backoff=0.01, state_dir=str(tmp_path / "state"),
    )
    settings.update(overrides)
    return BridgeConfig(**settings)


def test_three_failures_open_the_breaker(tmp_path):
    config = _down_config(tmp_path)

    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        assert Bridge(label="a", cwd=str(tmp_path), config=config, background=False).start() is False
        assert breaker_is_open(config) is False, "one or two failures is not a verdict"

    assert Bridge(label="a", cwd=str(tmp_path), config=config, background=False).start() is False
    assert breaker_is_open(config) is True


def test_an_open_breaker_makes_no_request_at_all(server, config, tmp_path):
    """The next invocation must not touch the network, even though it would work."""
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        record_breaker_failure(config)
    assert breaker_is_open(config) is True

    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start() is False
    assert bridge.breaker_open is True

    assert server.registrations == [], "a live server must still receive nothing"
    assert server.events == []


def test_the_breaker_closes_after_its_window(server, config, tmp_path):
    config.breaker_seconds = 0.3
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        record_breaker_failure(config)
    assert breaker_is_open(config) is True

    time.sleep(0.35)

    assert breaker_is_open(config) is False, "the window expires and the next call probes"
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start() is True, "the probe succeeds against a live server"
    assert breaker_is_open(config) is False, "success closes the breaker"


def test_a_failing_probe_reopens_the_breaker_immediately(tmp_path):
    config = _down_config(tmp_path, breaker_seconds=0.3)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        record_breaker_failure(config)
    time.sleep(0.35)
    assert breaker_is_open(config) is False

    Bridge(label="a", cwd=str(tmp_path), config=config, background=False).start()

    assert breaker_is_open(config) is True, "one failed probe is enough; do not start over"


def test_a_401_opens_the_breaker_at_once_and_for_longer(server, config, tmp_path):
    server.event_statuses = [401]
    config.breaker_seconds = 10.0
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start() is True
    assert breaker_is_open(config) is False

    bridge.assistant("never arrives")
    bridge.flush()

    assert breaker_is_open(config) is True, "a revoked token is not worth retrying"


def test_a_plain_400_does_not_open_the_breaker(server, config, tmp_path):
    """A malformed batch is our bug; the deployment is fine and still reachable."""
    server.event_statuses = [400]
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start() is True

    bridge.assistant("rejected")
    bridge.flush()

    assert breaker_is_open(config) is False


def test_a_corrupt_breaker_file_never_raises(config, tmp_path):
    from cloud.adapters.gt_cloud_bridge import _breaker_path

    path = _breaker_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for blob in ("", "{ half written", "[]", "null", "\x00\x01\x02"):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(blob)
        assert breaker_is_open(config) is False, f"{blob!r} must read as no memory"
        record_breaker_failure(config)  # must not raise either

    reset_breaker(config)
    assert breaker_is_open(config) is False


def test_an_unwritable_state_dir_never_raises(tmp_path):
    """A state directory we cannot write is a lost cache, not a failed hook."""
    blocker = tmp_path / "blocked"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    config = _down_config(tmp_path, state_dir=str(blocker / "state"))

    assert breaker_is_open(config) is False
    record_breaker_failure(config)
    reset_breaker(config)
    assert Bridge(label="a", cwd=str(tmp_path), config=config, background=False).start() is False


def test_hook_mode_takes_the_tight_budget():
    hook = BridgeConfig.from_env({"GT_CLOUD_ORIGIN": "http://x"}, hook_mode=True)
    tailer = BridgeConfig.from_env({"GT_CLOUD_ORIGIN": "http://x"})

    assert (hook.timeout, hook.retries) == (HOOK_TIMEOUT, HOOK_RETRIES)
    assert tailer.timeout > hook.timeout and tailer.retries > hook.retries
    # The ceiling is a cap, not a default: configuration may lower it, never raise it.
    raised = BridgeConfig.from_env(
        {"GT_CLOUD_ORIGIN": "http://x", "GT_CLOUD_TIMEOUT": "30"}, hook_mode=True
    )
    assert raised.timeout == HOOK_TIMEOUT


def test_a_down_deployment_costs_the_hook_almost_nothing_once_the_breaker_opens(tmp_path):
    """The whole review finding, as one measurement."""
    config = _down_config(tmp_path, timeout=1.5)
    payload = dict(PRE_TOOL_USE_READ)

    started = time.monotonic()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        gt_cloud_hook.handle(payload, config)
    opened = time.monotonic()
    assert breaker_is_open(config) is True

    resumed = time.monotonic()
    for _ in range(20):
        gt_cloud_hook.handle(payload, config)
    per_call = (time.monotonic() - resumed) / 20

    assert per_call < 0.05, f"a broken deployment still cost {per_call:.3f}s per tool call"
    assert opened - started < 12, "opening the breaker must not take long either"


# --- paths ------------------------------------------------------------------


def test_absolute_path_inside_cwd_becomes_relative(tmp_path):
    inside = os.path.join(str(tmp_path), "src", "app.py")
    assert to_repo_relative(inside, str(tmp_path)) == "src/app.py"


def test_absolute_path_outside_cwd_is_dropped(tmp_path):
    outside = os.path.join(os.path.dirname(str(tmp_path)), "elsewhere", "secret.env")
    assert to_repo_relative(outside, str(tmp_path)) is None


@pytest.mark.parametrize(
    "path", ["../outside.py", "src/../../outside.py", "", None, "."]
)
def test_escaping_and_empty_paths_are_dropped(path, tmp_path):
    assert to_repo_relative(path, str(tmp_path)) is None


def test_windows_style_paths_convert(tmp_path):
    if os.sep == "\\":
        assert to_repo_relative(str(tmp_path) + "\\src\\a.py", str(tmp_path)) == "src/a.py"
        assert to_repo_relative("C:\\other\\a.py", "D:\\repo") is None
    else:  # the drive-letter case only exists on Windows
        assert to_repo_relative("/tmp/other/a.py", "/repo") is None


def test_the_bridge_converts_paths_before_sending(server, config, tmp_path):
    bridge = Bridge(label="a", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()
    outside = os.path.join(os.path.dirname(str(tmp_path)), "outside.py")

    bridge.tool_call("Edit", files=[os.path.join(str(tmp_path), "src", "app.py"), outside])
    bridge.flush()

    assert server.events[0]["files"] == ["src/app.py"], "no absolute path may reach the server"


def test_command_paths_are_a_hint_and_stay_inside_the_repo(tmp_path):
    (tmp_path / "src").mkdir()
    found = extract_paths_from_command("grep -r needle src/app.py /etc/passwd", str(tmp_path))
    assert found == ["src/app.py"]


# --- captured Claude Code hook payloads -------------------------------------

HOOK_SESSION_ID = "ce365208-f156-4a0a-85e2-51d9ccf5f94d"
SUBAGENT_ID = "ad3891f2b37c94b26"


def _payload(**overrides):
    base = {
        "session_id": HOOK_SESSION_ID,
        "transcript_path": "/home/u/.claude/projects/p/ce365208.jsonl",
        "cwd": "/home/u/project",
        "permission_mode": "default",
        "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
    }
    base.update(overrides)
    return base


PRE_TOOL_USE_READ = _payload(
    hook_event_name="PreToolUse",
    tool_name="Read",
    tool_input={"file_path": "/home/u/project/sample.txt"},
    tool_use_id="toolu_01C9bZdwGZbJFei3u4Je4BCV",
)

POST_TOOL_USE_READ = _payload(
    hook_event_name="PostToolUse",
    tool_name="Read",
    tool_input={"file_path": "/home/u/project/sample.txt"},
    tool_response={"type": "text", "file": {"filePath": "/home/u/project/sample.txt",
                                            "content": "hello world\n", "numLines": 2}},
    tool_use_id="toolu_01C9bZdwGZbJFei3u4Je4BCV",
    duration_ms=12,
)

PRE_TOOL_USE_AGENT = _payload(
    hook_event_name="PreToolUse",
    tool_name="Agent",
    tool_input={"description": "List files in current directory",
                "prompt": "List all files in the current working directory.",
                "subagent_type": "Explore"},
    tool_use_id="toolu_01NbSyxQm764ZTEQMQDeTkd9",
)

SUBAGENT_START = _payload(
    hook_event_name="SubagentStart", agent_id=SUBAGENT_ID, agent_type="Explore"
)

SUBAGENT_GLOB = _payload(
    hook_event_name="PreToolUse",
    agent_id=SUBAGENT_ID,
    agent_type="Explore",
    tool_name="Glob",
    tool_input={"pattern": "*"},
    tool_use_id="toolu_01MFMrANrZG3h284pFRCMeX8",
)

SUBAGENT_STOP = _payload(
    hook_event_name="SubagentStop",
    agent_id=SUBAGENT_ID,
    agent_type="Explore",
    agent_transcript_path="/home/u/.claude/projects/p/ce365208/subagents/agent-ad3891.jsonl",
    last_assistant_message="Found 1 file: sample.txt",
    stop_hook_active=False,
    background_tasks=[],
    session_crons=[],
)

STOP = _payload(
    hook_event_name="Stop",
    last_assistant_message="The file contains a single line.",
    stop_hook_active=False,
    background_tasks=[],
    session_crons=[],
)


@pytest.fixture
def hook_config(server, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/home/u/project")
    monkeypatch.delenv("GT_CLOUD_AGENT_KIND", raising=False)
    return BridgeConfig(origin=server.origin, session_id=SESSION_ID, user_token="user-jwt",
                        timeout=3.0, flush_interval=0.05, retries=0, backoff=0.01,
                        state_dir=str(tmp_path / "state"))


def test_hook_reports_a_tool_call_end_to_end(server, hook_config):
    gt_cloud_hook.handle(PRE_TOOL_USE_READ, hook_config)

    assert len(server.registrations) == 1
    assert server.registrations[0]["body"]["agent_kind"] == "claude-code"
    call = next(event for event in server.events if event["type"] == "tool_call")
    assert call["name"] == "Read"
    assert call["files"] == ["sample.txt"], "the absolute path is made repo-relative"
    assert call["activity"] == "Reading sample.txt"


def test_hook_reads_paths_out_of_the_tool_response(server, hook_config):
    gt_cloud_hook.handle(POST_TOOL_USE_READ, hook_config)

    result = next(event for event in server.events if event["type"] == "tool_result")
    assert result["ok"] is True
    assert result["files"] == ["sample.txt"]


def test_a_subagent_registers_with_its_parent_agent_id(server, hook_config):
    gt_cloud_hook.handle(PRE_TOOL_USE_READ, hook_config)  # registers the main agent
    parent_id = server.registrations[0]["agent_id"]

    gt_cloud_hook.handle(PRE_TOOL_USE_AGENT, hook_config)  # parks the description
    gt_cloud_hook.handle(SUBAGENT_START, hook_config)

    assert len(server.registrations) == 2
    child = server.registrations[1]["body"]
    assert child["parent_agent_id"] == parent_id
    assert child["label"] == "List files in current directory", "labelled from the Agent call"


def test_a_subagents_tool_call_lands_on_the_child_not_the_parent(server, hook_config):
    gt_cloud_hook.handle(PRE_TOOL_USE_READ, hook_config)
    gt_cloud_hook.handle(SUBAGENT_START, hook_config)
    parent_id = server.registrations[0]["agent_id"]
    child_id = server.registrations[1]["agent_id"]
    server.batches.clear()

    gt_cloud_hook.handle(SUBAGENT_GLOB, hook_config)

    assert len(server.registrations) == 2, "an existing child is reused, not re-registered"
    child_events = server.events_for(child_id)
    assert [event["name"] for event in child_events if event["type"] == "tool_call"] == ["Glob"]
    assert server.events_for(parent_id) == [], "the parent's card must not show the child's work"


def test_subagent_stop_finishes_the_child(server, hook_config):
    gt_cloud_hook.handle(PRE_TOOL_USE_READ, hook_config)
    gt_cloud_hook.handle(SUBAGENT_START, hook_config)

    gt_cloud_hook.handle(SUBAGENT_STOP, hook_config)

    assert len(server.finishes) == 1
    assert server.finishes[0]["body"]["summary"] == "Found 1 file: sample.txt"


def test_stop_reports_the_reply_and_goes_idle(server, hook_config):
    gt_cloud_hook.handle(STOP, hook_config)

    kinds = [event["type"] for event in server.events]
    assert "assistant" in kinds
    statuses = [event for event in server.events if event["type"] == "status"]
    assert statuses[-1]["state"] == "idle"
    assert server.finishes == [], "a finished turn is not a finished session"


def test_hook_ignores_events_it_does_not_map(server, hook_config):
    assert gt_cloud_hook.handle(_payload(hook_event_name="PreCompact"), hook_config) == "PreCompact"
    assert server.registrations == []


def test_hook_never_reports_a_token_count_it_did_not_measure(server, hook_config):
    gt_cloud_hook.handle(POST_TOOL_USE_READ, hook_config)

    for event in server.events:
        assert "tokens" not in event, "the fixture transcript does not exist; omit, do not guess"


def test_transcript_tokens_sum_the_last_assistant_record(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 1, "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3, "output_tokens": 4}}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 8, "cache_creation_input_tokens": 227,
            "cache_read_input_tokens": 39541, "output_tokens": 60}}}) + "\n"
        + '{"type": "assistant", "message": {"usage": {"input_to',  # half-written
        encoding="utf-8",
    )

    assert transcript.tokens_from_transcript(str(path)) == 8 + 227 + 39541 + 60


def test_transcript_tokens_are_none_when_unmeasurable(tmp_path):
    assert transcript.tokens_from_transcript(None) is None
    assert transcript.tokens_from_transcript(str(tmp_path / "missing.jsonl")) is None
    empty = tmp_path / "e.jsonl"
    empty.write_text('{"type": "user"}\nnot json at all\n', encoding="utf-8")
    assert transcript.tokens_from_transcript(str(empty)) is None


# --- the hook as a subprocess: the host agent's exit code -------------------


HOOK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(gt_cloud_hook.__file__))),
    "claude_code", "gt_cloud_hook.py",
)


def _run_hook(payload: dict, env_overrides: dict, timeout: float = 60) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("GT_CLOUD_ORIGIN", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=timeout,
    )


@pytest.mark.parametrize(
    "name, env",
    [
        ("unconfigured", {}),
        ("unreachable server", {"GT_CLOUD_ORIGIN": "http://127.0.0.1:1",
                                "GT_CLOUD_SESSION": SESSION_ID, "GT_CLOUD_TOKEN": "t"}),
        ("nonsense origin", {"GT_CLOUD_ORIGIN": "not a url",
                             "GT_CLOUD_SESSION": SESSION_ID, "GT_CLOUD_TOKEN": "t"}),
    ],
)
def test_the_hook_exits_zero_and_stays_silent_whatever_happens(name, env, tmp_path):
    env = dict(env, GT_CLOUD_STATE_DIR=str(tmp_path / f"state-{abs(hash(name))}"))

    result = _run_hook(PRE_TOOL_USE_READ, env)

    assert result.returncode == 0, f"{name} must not fail the host agent's tool call"
    assert result.stdout.strip() == "", "stdout is a control channel; the hook makes no decisions"


def test_the_hook_survives_garbage_on_stdin(tmp_path):
    env = {"GT_CLOUD_STATE_DIR": str(tmp_path / "state")}
    for blob in ("", "not json", "[1, 2, 3]", "null"):
        result = subprocess.run(
            [sys.executable, HOOK_SCRIPT], input=blob, capture_output=True, text=True,
            env=dict(os.environ, **env), timeout=60,
        )
        assert result.returncode == 0, f"input {blob!r} must not fail the hook"


def test_the_hook_reports_through_a_real_subprocess(server, tmp_path):
    env = {
        "GT_CLOUD_ORIGIN": server.origin,
        "GT_CLOUD_SESSION": SESSION_ID,
        "GT_CLOUD_TOKEN": "user-jwt",
        "GT_CLOUD_STATE_DIR": str(tmp_path / "state"),
        "CLAUDE_PROJECT_DIR": "/home/u/project",
    }

    result = _run_hook(PRE_TOOL_USE_READ, env)

    assert result.returncode == 0
    assert len(server.registrations) == 1
    assert any(event["type"] == "tool_call" for event in server.events)


# --- the generic JSONL tailer -----------------------------------------------


def test_tailer_accepts_contract_lines_and_survives_malformed_ones(server, config, tmp_path):
    bridge = Bridge(label="generic", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()
    tailer = gt_cloud_tail.JsonlTailer(bridge)

    lines = [
        json.dumps({"type": "assistant", "text": "hello"}),
        "{not json at all",
        "",
        json.dumps({"type": "nonsense", "text": "ignored"}),
        json.dumps({"type": "tool_call", "name": "Edit",
                    "files": [os.path.join(str(tmp_path), "a.py")]}),
        json.dumps([1, 2, 3]),
        json.dumps({"type": "status", "state": "done"}),
    ]
    for line in lines:
        tailer.feed_line(line)
    bridge.flush()

    assert tailer.stats.accepted == 3
    assert tailer.stats.malformed == 1
    assert tailer.stats.skipped == 2
    assert [event["type"] for event in server.events] == ["assistant", "tool_call", "status"]
    assert server.events[1]["files"] == ["a.py"]


def test_tailer_map_renames_fields(server, config, tmp_path):
    aliases = gt_cloud_tail.parse_map("name=tool,command=cmd, bad-entry ,=x,y=")
    assert aliases == {"name": "tool", "command": "cmd"}

    bridge = Bridge(label="generic", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()
    tailer = gt_cloud_tail.JsonlTailer(bridge, aliases)

    tailer.feed_line(json.dumps({"type": "tool_call", "tool": "Bash", "cmd": "pytest -q"}))
    bridge.flush()

    assert server.events[0]["name"] == "Bash"
    assert server.events[0]["command"] == "pytest -q"


def test_tailer_follows_a_file_as_it_grows(server, config, tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text(json.dumps({"type": "assistant", "text": "one"}) + "\n", encoding="utf-8")
    bridge = Bridge(label="generic", cwd=str(tmp_path), config=config, background=False)
    assert bridge.start()
    tailer = gt_cloud_tail.JsonlTailer(bridge)

    assert tailer.poll_file(str(path)) == 1
    assert tailer.poll_file(str(path)) == 0
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "assistant", "text": "two"}))  # no trailing newline
    assert tailer.poll_file(str(path)) == 0, "a half-written line is held over, not parsed"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n")
    assert tailer.poll_file(str(path)) == 1

    bridge.flush()
    assert [event["text"] for event in server.events] == ["one", "two"]


# --- the Codex rollout tailer -----------------------------------------------

CODEX_CWD = "/home/u/project"


def _rollout_meta(thread_id: str, parent: str | None = None, nickname: str | None = None) -> dict:
    payload = {
        "session_id": parent or thread_id, "id": thread_id, "cwd": CODEX_CWD,
        "originator": "codex-tui", "cli_version": "0.153.3", "model_provider": "openai",
        "thread_source": "subagent" if parent else "user",
    }
    if parent:
        payload["parent_thread_id"] = parent
        payload["agent_nickname"] = nickname
        payload["agent_path"] = "/root/dense_cache"
        payload["source"] = {"subagent": {"thread_spawn": {
            "parent_thread_id": parent, "depth": 1, "agent_path": "/root/dense_cache",
            "agent_nickname": nickname, "agent_role": None}}}
    return {"timestamp": "2026-09-06T00:59:56.347Z", "ordinal": 0,
            "type": "session_meta", "payload": payload}


def _item(item: dict) -> dict:
    return {"timestamp": "2026-09-06T01:00:00Z", "type": "event_msg",
            "payload": {"type": "item_completed", "thread_id": "t", "turn_id": "u", "item": item}}


def test_codex_maps_a_command_execution(tmp_path):
    events = gt_cloud_codex.map_rollout_line(_item({
        "type": "CommandExecution", "id": "exec-1",
        "command": ["C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                    "-Command", "Get-Content README.md"],
        "cwd": "file:///home/u/project", "status": "completed", "stdout": "text",
        "parsed_cmd": [{"type": "read", "cmd": "Get-Content README.md",
                        "name": "README.md", "path": "/home/u/project/README.md"}],
    }), CODEX_CWD)

    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["name"] == "exec"
    assert events[0]["files"] == ["/home/u/project/README.md"]
    # The flag is skipped and the program named: a phrase, not the command line.
    assert events[0]["activity"] == "Running powershell.exe Get-Content"
    assert events[1]["ok"] is True


def test_codex_maps_a_file_change_to_its_paths():
    events = gt_cloud_codex.map_rollout_line(_item({
        "type": "FileChange", "id": "exec-2",
        "changes": {"/home/u/project/tests/test_a.py": {"type": "update", "unified_diff": "@@"}},
    }), CODEX_CWD)

    assert events[0]["files"] == ["/home/u/project/tests/test_a.py"]
    assert events[0]["activity"] == "Editing test_a.py"


def test_codex_maps_messages_and_tokens_but_not_private_reasoning():
    assert gt_cloud_codex.map_rollout_line(_item(
        {"type": "AgentMessage", "content": [{"type": "Text", "text": "done"}]}), CODEX_CWD
    ) == [{"type": "assistant", "text": "done"}]

    assert gt_cloud_codex.map_rollout_line(_item({"type": "Reasoning", "text": "hmm"})) == []
    assert gt_cloud_codex.map_rollout_line(_item({"type": "UserMessage", "content": []})) == []

    tokens = gt_cloud_codex.map_rollout_line({
        "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 22525, "output_tokens": 202,
                                  "total_tokens": 22727}}}})
    assert tokens == [{"type": "status", "state": "working", "tokens": 22727}]


def _legacy(payload: dict) -> dict:
    return {"timestamp": "2026-09-06T01:00:00Z", "type": "event_msg", "payload": payload}


def test_codex_reads_legacy_history_mode_events():
    """`legacy` is Codex's default history mode and emits no `item_completed`."""
    assert gt_cloud_codex.map_rollout_line(
        _legacy({"type": "agent_message", "message": "done", "phase": "commentary"}), CODEX_CWD
    ) == [{"type": "assistant", "text": "done"}]

    events = gt_cloud_codex.map_rollout_line(_legacy({
        "type": "patch_apply_end", "call_id": "exec-1", "success": True,
        "stdout": "Success. Updated the following files:\nA /home/u/project/a.py\n", "stderr": "",
        "changes": {"/home/u/project/a.py": {"type": "add", "content": "x"}},
    }), CODEX_CWD)
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["files"] == ["/home/u/project/a.py"]
    assert events[0]["activity"] == "Editing a.py"
    assert events[1]["ok"] is True

    failed = gt_cloud_codex.map_rollout_line(_legacy({
        "type": "patch_apply_end", "success": False, "stderr": "no such file", "changes": {},
    }), CODEX_CWD)
    assert failed[1]["ok"] is False


def test_codex_mapping_never_raises_on_junk():
    for junk in (None, [], "text", {"type": "event_msg"}, {"type": "event_msg", "payload": 3},
                 _item({}), {"type": "response_item", "payload": {"type": "function_call"}}):
        assert gt_cloud_codex.map_rollout_line(junk) == []


def test_codex_watcher_nests_a_subagent_rollout(server, config, tmp_path):
    directory = tmp_path / "sessions" / "2026" / "09" / "06"
    directory.mkdir(parents=True)
    root = directory / "rollout-2026-09-06T00-00-00-parent.jsonl"
    root.write_text(json.dumps(_rollout_meta("thread-parent")) + "\n", encoding="utf-8")

    watcher = gt_cloud_codex.CodexWatcher(str(root), str(tmp_path / "sessions"), config)
    assert watcher.attach(str(root)) is not None
    parent_agent_id = server.registrations[0]["agent_id"]

    child = directory / "rollout-2026-09-06T00-01-00-child.jsonl"
    child.write_text(
        json.dumps(_rollout_meta("thread-child", parent="thread-parent", nickname="Faraday"))
        + "\n" + json.dumps(_item({"type": "AgentMessage",
                                   "content": [{"type": "Text", "text": "child working"}]})) + "\n",
        encoding="utf-8",
    )
    watcher.poll_once()

    assert len(server.registrations) == 2
    child_body = server.registrations[1]["body"]
    assert child_body["parent_agent_id"] == parent_agent_id
    assert child_body["agent_kind"] == "codex"
    assert child_body["label"].startswith("Faraday")

    watcher.finish("done", "stopped")
    assert len(server.finishes) == 2


def test_codex_watcher_ignores_a_subagent_of_an_unknown_parent(server, config, tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    orphan = directory / "rollout-2026-09-06T00-02-00-orphan.jsonl"
    orphan.write_text(
        json.dumps(_rollout_meta("thread-orphan", parent="somebody-elses-thread", nickname="Ohm")),
        encoding="utf-8",
    )

    watcher = gt_cloud_codex.CodexWatcher(str(orphan), str(directory), config)

    assert watcher.attach(str(orphan)) is None
    assert server.registrations == []


def test_codex_tailer_streams_appended_lines(server, config, tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    path = directory / "rollout-2026-09-06T00-00-00-a.jsonl"
    path.write_text(json.dumps(_rollout_meta("thread-a")) + "\n", encoding="utf-8")
    watcher = gt_cloud_codex.CodexWatcher(str(path), str(directory), config)
    tailer = watcher.attach(str(path))
    assert tailer is not None

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_item({"type": "AgentMessage",
                                       "content": [{"type": "Text", "text": "hi"}]})) + "\n")
        handle.write("{ broken\n")
    assert watcher.poll_once() == 1

    tailer.bridge.flush()
    assert [event["text"] for event in server.events] == ["hi"]


# ---------------------------------------------------------------------------
# HAR-84: defects a live Claude Code run found that the suite did not
# ---------------------------------------------------------------------------
def test_a_finish_summary_is_capped_after_the_dropped_note_is_added(
    server, config, tmp_path
):
    """The note used to push the payload one line past the server's cap.

    ``finish`` is the only thing that settles a card, so the 422 that came
    back left the agent reading ``running`` for ever with its activity stuck
    on "Finished". Observed on a real run before the cap moved.
    """
    from cloud.adapters.gt_cloud_bridge import MAX_TEXT_CHARS

    bridge = Bridge(
        agent_kind="claude-code", label="capped", cwd=str(tmp_path),
        state_key="cap-session", config=config, background=False,
    )
    assert bridge.start() is True
    bridge.dropped = 7

    bridge.finish("done", "x" * MAX_TEXT_CHARS)

    assert server.finishes, "finish was never posted"
    summary = server.finishes[-1]["body"]["summary"]
    assert len(summary) <= MAX_TEXT_CHARS
    assert "dropped 7 events" in summary


def test_the_parent_hook_never_registers_a_card_for_a_subagent(
    server, hook_config, tmp_path
):
    """``open_existing`` settles a card; it must never invent one.

    A completed foreground ``Agent`` call is reported in the **parent's**
    hook, where the subagent's own label is unknown. Registering from there
    produced a second, empty card named after the parent's agent type beside
    the child's real one — one subagent, two rows, seen live.
    """
    from cloud.adapters.claude_code.gt_cloud_hook import HookSession

    session = HookSession(_payload(hook_event_name="Stop"), hook_config)
    before = len(server.registrations)

    assert session.open_existing("never-registered-id") is None

    assert len(server.registrations) == before
