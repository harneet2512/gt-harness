"""Endpoint-level tests for the cloud coding agent chat API (HAR-84).

FAKE BOUNDARY (module-wide, single fake):
    Faked: the model provider (LLM). ``ScriptedModel`` replaces the LiteLLM
    client and nothing else, so no network call is made.
    Real: the FastAPI app (via lifespan), JWT auth, the SQLite store, the
    ``EventBus`` + SSE encoder, the ``ConversationalAgent`` turn loop, the bash
    environment (``CloudLocalEnvironment``, real subprocesses on the real
    filesystem), and git (a real repository is created on disk, really cloned,
    and the diff is a real ``git diff``).

    The only other test seams are scheduling, not behaviour:
    ``_GitCloneRedirector`` rewrites the *remote* of the ``git clone`` argv to a
    local seed repository (the HTTP body still carries a real
    ``https://github.com/...`` URL so route validation is exercised), and a thin
    wrapper around ``SessionManager._create_blocking`` lets a test hold the
    workspace worker at its entry point so pre-clone assertions are not racy.
    Both wrappers run the real code they wrap.

    The app is served by a real uvicorn server on a loopback port rather than
    Starlette's ``TestClient``: ``TestClient`` buffers a whole response before
    returning, so a stream that deliberately stays open across turns — the
    behaviour under test — would deadlock it.

Run: ``python -m pytest tests/test_cloud_chat.py -q`` from the repo root.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import uvicorn
from minisweagent.exceptions import FormatError

from cloud.server import deps
from cloud.server import workspace as workspace_module
from cloud.server.app import create_app
from cloud.server.conversational_agent import ConversationalAgent
from cloud.server.environment import CloudLocalEnvironment
from cloud.server.runner import SessionManager

# Hard timeouts everywhere: a stuck run must fail fast, never hang CI.
GATE_TIMEOUT = 10.0
POLL_TIMEOUT = 15.0
POLL_INTERVAL = 0.01
SSE_TIMEOUT = 15.0

JWT_SECRET = "test-jwt-secret"
REPO_URL = "https://github.com/example/repo"
MODEL_NAME = "scripted-fake/no-network"
STEER_TEXT = "actually, write STEERED.txt instead"

SYSTEM_TEMPLATE = "You are a test coding agent."
INSTANCE_TEMPLATE = "Workspace ready."

TOUCH_TRACKED = "echo patched >> README.md"
MAKE_UNTRACKED = "echo brand-new > newfile.txt"


# --------------------------------------------------------------------------
# scripted model
# --------------------------------------------------------------------------
def _action(command: str, cost: float = 0.01) -> dict:
    return {
        "role": "assistant",
        "content": f"I will run: {command}",
        "extra": {"actions": [{"command": command}], "cost": cost},
    }


def _reply(content: str, cost: float = 0.01) -> FormatError:
    """A text-only model response, in stock ``LitellmModel`` FormatError shape."""
    return FormatError(
        {
            "role": "user",
            "content": "No tool calls found in the response.",
            "extra": {
                "interrupt_type": "FormatError",
                "cost": cost,
                "response": {
                    "choices": [{"message": {"role": "assistant", "content": content}}]
                },
            },
        }
    )


DEFAULT_SCRIPT: list[Any] = [
    _action(TOUCH_TRACKED),
    _action(MAKE_UNTRACKED),
    _reply("Done. I appended to README.md and added newfile.txt."),
]


class ScriptedModel:
    """FAKE BOUNDARY: the LLM provider. Replays a fixed list of responses.

    Records every message list it is handed (``seen_messages``) so a test can
    prove what the agent actually sent upstream, and can block on a
    ``threading.Event`` before a chosen call so a test can act mid-turn.
    """

    def __init__(
        self,
        steps: list[Any],
        *,
        gate: threading.Event | None = None,
        gate_call: int = 0,
    ) -> None:
        self._steps = list(steps)
        self._gate = gate
        self._gate_call = gate_call
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.blocked = threading.Event()

    def query(self, messages: list[dict], **_: Any) -> dict:
        self.calls += 1
        if self._gate is not None and self.calls == self._gate_call:
            self.blocked.set()
            if not self._gate.wait(GATE_TIMEOUT):
                raise AssertionError("model gate was never released")
        self.seen_messages.append([dict(m) for m in messages])
        if self.calls > len(self._steps):
            raise _reply("The script is exhausted; nothing left to do.")
        step = self._steps[self.calls - 1]
        if isinstance(step, BaseException):
            raise step
        return step

    def format_message(
        self, role: str = "", content: str = "", extra: dict | None = None, **_: Any
    ) -> dict:
        message: dict[str, Any] = {"role": role, "content": content}
        if extra is not None:
            message["extra"] = extra
        return message

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        return [
            {
                "role": "user",
                "content": f"Observation (rc={o.get('returncode')}):\n{o.get('output', '')}",
                "extra": {"raw_output": o.get("output", "")},
            }
            for o in outputs
        ]

    def get_template_vars(self, **_: Any) -> dict:
        return {}

    def serialize(self) -> dict:
        return {"info": {"config": {"model": {"model_name": "scripted-fake"}}}}


class ExplodingEnvironment(CloudLocalEnvironment):
    """Real environment whose ``execute`` raises — the infra-failure path."""

    def execute(self, action: dict, cwd: str = "", **_: Any) -> dict:
        raise RuntimeError("environment exploded")


# --------------------------------------------------------------------------
# real git seed repo + clone redirector
# --------------------------------------------------------------------------
def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {args} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _make_seed_repo(root: Path) -> Path:
    repo = root / "seed-repo"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "harness@example.invalid", cwd=repo)
    _git("config", "user.name", "HAR-84 harness", cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    # a real (tiny) Python package, so /graph has an import edge to find
    (repo / "app.py").write_text("import pkg.util\n", encoding="utf-8")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("-c", "commit.gpgsign=false", "commit", "-m", "init", cwd=repo)
    return repo


class _GitCloneRedirector:
    """Stands in for ``cloud.server.workspace.subprocess``.

    Only ``git clone`` is redirected (remote URL -> local seed repo); every
    other call, including the ``git diff`` that produces the patch, goes
    straight to the real ``subprocess.run``.
    """

    def __init__(self, seed_repo: Path) -> None:
        self._seed_repo = seed_repo
        self.clone_calls: list[tuple[str, ...]] = []

    def run(self, argv, **kwargs):  # noqa: ANN001
        if isinstance(argv, (list, tuple)) and list(argv[:2]) == ["git", "clone"]:
            args = list(argv)
            self.clone_calls.append(tuple(args))
            workdir = args[-1]
            ref = args[args.index("--branch") + 1] if "--branch" in args else "main"
            return subprocess.run(
                ["git", "clone", "--branch", ref, str(self._seed_repo), workdir],
                **kwargs,
            )
        return subprocess.run(argv, **kwargs)

    def __getattr__(self, name: str):
        return getattr(subprocess, name)


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
_ORIGINAL_CREATE_BLOCKING = SessionManager._create_blocking


class Harness:
    def __init__(self, seed_repo: Path, workspaces: Path) -> None:
        self.seed_repo = seed_repo
        self.workspaces = workspaces
        self.client: httpx.Client = None  # type: ignore[assignment]
        self.script: list[Any] = list(DEFAULT_SCRIPT)
        self.models: dict[str, ScriptedModel] = {}
        self.agents: dict[str, ConversationalAgent] = {}
        self.env_class: type[CloudLocalEnvironment] = CloudLocalEnvironment
        self._gate_call = 0
        self.model_gate = threading.Event()
        self.model_gate.set()
        self.start_gate = threading.Event()
        self.start_gate.set()
        self.aborted = False
        self.token = jwt.encode(
            {"sub": "1", "login": "tester", "exp": int(time.time()) + 3600},
            JWT_SECRET,
            algorithm="HS256",
        )

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    # -- test controls ----------------------------------------------------
    def set_script(self, script: list[Any]) -> None:
        self.script = list(script)

    def block_model_at(self, call_index: int) -> None:
        self._gate_call = call_index
        self.model_gate.clear()

    def release_model(self) -> None:
        self.model_gate.set()

    def hold_worker(self) -> None:
        self.start_gate.clear()

    def release_worker(self) -> None:
        self.start_gate.set()

    def new_model(self, session_id: str) -> ScriptedModel:
        model = ScriptedModel(
            self.script, gate=self.model_gate, gate_call=self._gate_call
        )
        self.models[session_id] = model
        return model

    def wait_blocked(self, session_id: str, timeout: float = GATE_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            model = self.models.get(session_id)
            if model is not None and model.blocked.wait(0.02):
                return model
            time.sleep(POLL_INTERVAL)
        raise AssertionError(f"model for session {session_id} never reached its gate")

    def shutdown(self) -> None:
        self.aborted = True
        self.start_gate.set()
        self.model_gate.set()
        for agent in self.agents.values():
            agent.request_stop()
        try:
            manager = deps.get_manager()
        except Exception:
            return
        deadline = time.monotonic() + 15.0
        while manager.running_count > 0 and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)


def _patched_build_agent(harness: Harness):
    def _build_agent(
        self: SessionManager,
        *,
        session_id: str,
        repo: str,
        ref: str,
        model: str,
        cwd: str,
        gt_mode: str,
        # SANDBOX_MODE=local in these tests, so this is always None; the
        # parameter exists so the fake keeps matching the real signature.
        sandbox: str | None = None,
        step_limit: int,
        temperature: float,
        issue_text: str,
        loop,
    ) -> ConversationalAgent:
        state_dir = Path(cwd) / ".gt_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        agent = ConversationalAgent(
            harness.new_model(session_id),
            harness.env_class(cwd=cwd, timeout=30),
            event_callback=lambda event: self._publish(loop, session_id, event),
            system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE,
            step_limit=step_limit,
            cost_limit=0.0,
            output_path=state_dir / "trajectory.json",
        )
        harness.agents[session_id] = agent
        return agent

    return _build_agent


def _patched_create_blocking(harness: Harness):
    def _create_blocking(self: SessionManager, session: dict, loop):
        if not harness.start_gate.wait(GATE_TIMEOUT) or harness.aborted:
            return None
        return _ORIGINAL_CREATE_BLOCKING(self, session, loop)

    return _create_blocking


@pytest.fixture(scope="session")
def seed_repo(tmp_path_factory) -> Path:
    """One real git repository, cloned by every session in the module."""
    return _make_seed_repo(tmp_path_factory.mktemp("seed"))


@pytest.fixture
def harness(seed_repo, tmp_path, monkeypatch):
    seed = seed_repo
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cloud.db"))
    monkeypatch.setenv("WORKSPACES_DIR", str(workspaces))
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS", "10")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("CORS_ORIGINS", "")
    # The stream must stay open across turns; a short heartbeat keeps the test
    # reader responsive without changing the production default (15s).
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.2")
    # deps is module-global state populated by the lifespan; reset so nothing
    # leaks between tests (monkeypatch restores the originals afterwards).
    monkeypatch.setattr(deps, "_store", None, raising=False)
    monkeypatch.setattr(deps, "_event_bus", None, raising=False)
    monkeypatch.setattr(deps, "_manager", None, raising=False)

    h = Harness(seed, workspaces)
    monkeypatch.setattr(workspace_module, "subprocess", _GitCloneRedirector(seed))
    monkeypatch.setattr(SessionManager, "_build_agent", _patched_build_agent(h))
    monkeypatch.setattr(
        SessionManager, "_create_blocking", _patched_create_blocking(h)
    )

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(), host="127.0.0.1", port=0, log_level="error", lifespan="on"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
    assert server.started, "uvicorn never came up"
    port = server.servers[0].sockets[0].getsockname()[1]

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
        h.client = client
        try:
            yield h
        finally:
            h.shutdown()
            server.should_exit = True
            thread.join(timeout=5.0)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=5.0)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _create(h: Harness, **overrides: Any):
    body = {
        "repo": REPO_URL,
        "ref": "main",
        "model": MODEL_NAME,
        "gt_mode": "off",
        "step_limit": 10,
    }
    body.update(overrides)
    return h.client.post("/api/sessions", json=body, headers=h.auth)


def _create_idle(h: Harness, **overrides: Any) -> str:
    response = _create(h, **overrides)
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    _wait_status(h, session_id, {"idle"})
    return session_id


def _session(h: Harness, session_id: str) -> dict:
    response = h.client.get(f"/api/sessions/{session_id}", headers=h.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _wait_status(
    h: Harness, session_id: str, statuses: set[str], timeout: float = POLL_TIMEOUT
) -> dict:
    deadline = time.monotonic() + timeout
    session: dict = {}
    while time.monotonic() < deadline:
        session = _session(h, session_id)
        if session["status"] in statuses:
            return session
        time.sleep(POLL_INTERVAL)
    raise AssertionError(
        f"session {session_id} never reached {sorted(statuses)}; "
        f"last={session.get('status')!r}"
    )


def _post_message(h: Harness, session_id: str, content: str):
    return h.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": content},
        headers=h.auth,
    )


def _parse_block(block: str) -> dict | None:
    frame: dict[str, Any] = {}
    for line in block.split("\n"):
        key, _, value = line.partition(": ")
        if key == "id":
            frame["id"] = int(value)
        elif key == "event":
            frame["event"] = value
        elif key == "data":
            frame["payload"] = json.loads(value)
    return frame if "event" in frame else None


def _read_sse(
    h: Harness,
    session_id: str,
    *,
    until,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = SSE_TIMEOUT,
) -> list[dict]:
    """Read the live stream until ``until(frames)`` or the server closes it."""
    frames: list[dict] = []
    deadline = time.monotonic() + timeout
    request_headers = {**h.auth, **(headers or {})}
    with h.client.stream(
        "GET",
        f"/api/sessions/{session_id}/events",
        params=params,
        headers=request_headers,
    ) as response:
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream")
        buffer = ""
        if until(frames):
            return frames
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                frame = _parse_block(block.strip("\n"))
                if frame is not None:
                    frames.append(frame)
            if until(frames):
                return frames
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"SSE stream never satisfied the predicate; saw {_types(frames)}"
                )
    return frames


def _has(event_type: str, **data_match: Any):
    def predicate(frames: list[dict]) -> bool:
        return any(_matches(f, event_type, data_match) for f in frames)

    return predicate


def _matches(frame: dict, event_type: str, data_match: dict) -> bool:
    if frame["event"] != event_type:
        return False
    data = frame["payload"]["data"]
    return all(data.get(k) == v for k, v in data_match.items())


def _turn_complete(frames: list[dict]) -> bool:
    """A turn is over once ``turn_finished`` is followed by ``lifecycle: idle``.

    The session's own creation ``idle`` comes first on the stream, so a bare
    "wait for idle" predicate would stop reading before the turn even starts.
    """
    for i, frame in enumerate(frames):
        if frame["event"] != "turn_finished":
            continue
        return any(
            _matches(f, "lifecycle", {"status": "idle"}) for f in frames[i:]
        )
    return False


def _from_turn(frames: list[dict]) -> list[dict]:
    """Frames from the first ``turn_started`` onwards."""
    return frames[_index(frames, "turn_started"):]


def _types(frames: list[dict]) -> list[str]:
    return [f["event"] for f in frames]


def _index(frames: list[dict], event_type: str, **data_match: Any) -> int:
    for i, frame in enumerate(frames):
        if _matches(frame, event_type, data_match):
            return i
    raise AssertionError(
        f"no {event_type} frame matching {data_match} in {_types(frames)}"
    )


# --------------------------------------------------------------------------
# 1: auth
# --------------------------------------------------------------------------
def test_api_requires_authentication(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    assert harness.client.get("/api/sessions").status_code == 401
    assert harness.client.post("/api/sessions", json={}).status_code == 401
    assert (
        harness.client.get(
            "/api/sessions", headers={"Authorization": "Bearer not-a-jwt"}
        ).status_code
        == 401
    )
    wrong_secret = jwt.encode(
        {"sub": "1", "exp": int(time.time()) + 60}, "other-secret", algorithm="HS256"
    )
    assert (
        harness.client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {wrong_secret}"}
        ).status_code
        == 401
    )

    # ...and the bearer token the other tests use does work
    assert harness.client.get("/api/sessions", headers=harness.auth).status_code == 200
    # /health stays public
    assert harness.client.get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------------------
# 2: session creation
# --------------------------------------------------------------------------
def test_create_session_clones_then_goes_idle(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git — the repo is really
    cloned. The workspace worker is held at its entry point so the freshly
    created row is observed before cloning starts."""
    harness.hold_worker()

    response = _create(harness)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "creating"
    assert body["repo"] == REPO_URL
    assert body["ref"] == "main"
    assert body["model"] == MODEL_NAME
    assert body["gt_mode"] == "off"
    assert body["gt_status"] == "off"
    assert body["turns"] == 0 and body["steps"] == 0 and body["cost"] == 0.0
    assert body["last_message"] is None
    assert body["current_turn_id"] is None
    assert isinstance(body["created_at"], float)
    assert _session(harness, body["id"])["status"] == "creating"

    harness.release_worker()
    _wait_status(harness, body["id"], {"idle"})

    frames = _read_sse(harness, body["id"], until=_has("lifecycle", status="idle"))
    lifecycle = [
        f["payload"]["data"]["status"] for f in frames if f["event"] == "lifecycle"
    ]
    assert lifecycle[:3] == ["creating", "cloning", "idle"]

    # the clone really happened, against the https URL from the request body
    clone_argv = workspace_module.subprocess.clone_calls[0]
    assert clone_argv[:2] == ("git", "clone") and REPO_URL in clone_argv
    assert (harness.workspaces / body["id"] / "README.md").is_file()


# --------------------------------------------------------------------------
# 3: a turn
# --------------------------------------------------------------------------
def test_message_runs_a_turn_and_streams_it(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus + SSE, agent loop, bash environment, git — the agent's
    shell commands really run in the clone."""
    session_id = _create_idle(harness)

    accepted = _post_message(harness, session_id, "append a line to the README")
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["delivery"] == "turn_started"
    assert body["message"]["role"] == "user"
    assert body["message"]["content"] == "append a line to the README"
    assert body["message"]["session_id"] == session_id

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    order = [
        f["event"]
        for f in frames
        if f["event"]
        in {"turn_started", "assistant", "tool_call", "tool_result",
            "agent_reply", "turn_finished"}
    ]
    assert order == [
        "turn_started",
        "assistant", "tool_call", "tool_result",
        "assistant", "tool_call", "tool_result",
        "agent_reply", "turn_finished",
    ]
    assert _index(frames, "turn_finished") < _index(
        frames, "lifecycle", status="idle"
    )

    turn_id = frames[_index(frames, "turn_started")]["payload"]["data"]["turn_id"]
    assert all(
        f["payload"]["data"].get("turn_id") == turn_id
        for f in frames
        if f["event"] in {"assistant", "tool_call", "tool_result", "agent_reply"}
    )
    assistants = [f for f in frames if f["event"] == "assistant"]
    assert [f["payload"]["data"]["step"] for f in assistants] == [1, 2]
    tool_calls = [f for f in frames if f["event"] == "tool_call"]
    assert [f["payload"]["data"]["step"] for f in tool_calls] == [1, 2]
    assert tool_calls[0]["payload"]["data"]["command"] == TOUCH_TRACKED

    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert reply["finish_reason"] == "reply"
    assert reply["n_calls"] == 3
    assert reply["content"].startswith("Done.")
    assert reply["patch_sha256"] and len(reply["patch_sha256"]) == 64
    assert sorted(reply["files_changed"]) == ["README.md", "newfile.txt"]
    finished = frames[_index(frames, "turn_finished")]["payload"]["data"]
    assert finished["patch_sha256"] == reply["patch_sha256"]
    assert finished["files_changed"] == reply["files_changed"]

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert [m["role"] for m in messages] == ["user", "agent"]
    assert messages[1]["turn_id"] == turn_id
    assert messages[1]["meta"]["finish_reason"] == "reply"
    assert messages[1]["meta"]["n_calls"] == 3
    assert messages[1]["meta"]["patch_sha256"] == reply["patch_sha256"]

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert len(receipts) == 1
    assert receipts[0]["turn_id"] == turn_id
    assert receipts[0]["n_calls"] == 3
    assert receipts[0]["finish_reason"] == "reply"
    assert receipts[0]["gt_status"] == "off"
    assert receipts[0]["model"] == MODEL_NAME
    assert receipts[0]["finished_at"] >= receipts[0]["started_at"]
    assert receipts[0]["cost"] == pytest.approx(0.03)

    session = _session(harness, session_id)
    assert session["status"] == "idle"
    assert session["turns"] == 1 and session["steps"] == 3
    assert session["cost"] == pytest.approx(0.03)
    assert session["current_turn_id"] is None
    assert session["last_message"].startswith("Done.")


# --------------------------------------------------------------------------
# 4: the transcript survives across turns
# --------------------------------------------------------------------------
def test_second_turn_continues_the_same_transcript(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    harness.set_script([
        _action("echo first"),
        _reply("First answer: I ran echo first."),
        _action("echo second"),
        _reply("Second answer."),
    ])
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "first question")
    _wait_status(harness, session_id, {"idle"})
    _post_message(harness, session_id, "second question")
    _wait_status(harness, session_id, {"idle"})

    model = harness.models[session_id]
    assert model.calls == 4
    third_request = model.seen_messages[2]  # first model call of turn 2
    contents = [str(m.get("content")) for m in third_request]
    assert "first question" in contents
    assert "First answer: I ran echo first." in contents
    assert "second question" in contents
    assert contents.count(SYSTEM_TEMPLATE) == 1

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert [m["role"] for m in messages] == ["user", "agent", "user", "agent"]

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert [r["n_calls"] for r in receipts] == [2, 2]
    assert _session(harness, session_id)["turns"] == 2


# --------------------------------------------------------------------------
# 5: steering a running turn
# --------------------------------------------------------------------------
def test_message_during_running_turn_is_delivered_as_steering(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git.

    The agent is parked inside its FIRST model call (the queue is drained at the
    top of the loop, i.e. before each query), so the POST lands strictly between
    step 1 and step 2.
    """
    harness.set_script([
        _action("echo one"),
        _action("touch STEERED.txt"),
        _reply("Steered and done."),
    ])
    session_id = _create_idle(harness)
    harness.block_model_at(1)

    first = _post_message(harness, session_id, "start working")
    assert first.json()["delivery"] == "turn_started"
    model = harness.wait_blocked(session_id)
    assert _session(harness, session_id)["status"] == "running"

    steered = _post_message(harness, session_id, STEER_TEXT)
    assert steered.status_code == 202, steered.text
    assert steered.json()["delivery"] == "queued_for_running_turn"
    steer_message_id = steered.json()["message"]["id"]

    harness.release_model()
    _wait_status(harness, session_id, {"idle"})

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    steering = frames[_index(frames, "steering")]["payload"]["data"]
    assert steering["content"] == STEER_TEXT
    assert steering["message_id"] == steer_message_id
    assistants = [i for i, f in enumerate(frames) if f["event"] == "assistant"]
    assert assistants[0] < _index(frames, "steering") < assistants[1]

    # the model actually saw it on its next request
    second_request = model.seen_messages[1]
    assert any(m.get("content") == STEER_TEXT for m in second_request)
    assert not any(m.get("content") == STEER_TEXT for m in model.seen_messages[0])

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert [m["role"] for m in messages] == ["user", "user", "agent"]
    assert messages[1]["content"] == STEER_TEXT
    assert messages[1]["turn_id"] is not None


# --------------------------------------------------------------------------
# 6: stop
# --------------------------------------------------------------------------
def test_stop_ends_the_turn_and_leaves_the_session_usable(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    harness.set_script([
        _action("echo one"),
        _action("echo never runs"),
        _reply("Picked back up."),
    ])
    session_id = _create_idle(harness)
    harness.block_model_at(1)

    _post_message(harness, session_id, "do a lot of work")
    model = harness.wait_blocked(session_id)

    stopped = harness.client.post(
        f"/api/sessions/{session_id}/stop", headers=harness.auth
    )
    assert stopped.status_code == 202, stopped.text

    harness.release_model()
    session = _wait_status(harness, session_id, {"idle"})
    assert session["status"] == "idle", "stopped is an event, not a status"
    assert model.calls == 1, "the loop must not start another step after stop"

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert reply["finish_reason"] == "stopped"
    assert reply["content"] == "Stopped."
    assert _index(frames, "lifecycle", status="stopped") < _index(
        frames, "lifecycle", status="idle"
    )

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert receipts[0]["finish_reason"] == "stopped"
    assert receipts[0]["n_calls"] == 1

    # the session is still usable
    again = _post_message(harness, session_id, "keep going")
    assert again.status_code == 202
    assert again.json()["delivery"] == "turn_started"
    _wait_status(harness, session_id, {"idle"})
    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert [r["finish_reason"] for r in receipts] == ["stopped", "reply"]

    # a stop with no running turn is a 409
    assert (
        harness.client.post(
            f"/api/sessions/{session_id}/stop", headers=harness.auth
        ).status_code
        == 409
    )


# --------------------------------------------------------------------------
# 7: per-turn step limit
# --------------------------------------------------------------------------
def test_step_limit_auto_replies_and_returns_to_idle(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    harness.set_script([_action(f"echo step-{i}") for i in range(10)])
    session_id = _create_idle(harness, step_limit=2)

    _post_message(harness, session_id, "work forever")
    session = _wait_status(harness, session_id, {"idle"})
    assert session["steps"] == 2

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert reply["finish_reason"] == "step_limit"
    assert reply["content"].startswith("I used the step budget for this turn")
    assert "Say 'continue' to keep going." in reply["content"]
    assert harness.models[session_id].calls == 2

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert messages[-1]["meta"]["finish_reason"] == "step_limit"


# --------------------------------------------------------------------------
# 8: diff
# --------------------------------------------------------------------------
def test_diff_reports_created_and_modified_files(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git — the patch below is a
    real ``git diff`` of a real clone that real bash commands modified.

    Regression guard: a plain ``git diff`` silently drops files the agent
    created, and the harness's own scratch must never leak into a patch.
    """
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "make some changes")
    _wait_status(harness, session_id, {"idle"})

    response = harness.client.get(
        f"/api/sessions/{session_id}/diff", headers=harness.auth
    )
    assert response.status_code == 200, response.text
    diff = response.json()

    assert "diff --git" in diff["patch"]
    assert "newfile.txt" in diff["patch"] and "brand-new" in diff["patch"]
    assert "+patched" in diff["patch"].replace("\r", "")
    assert len(diff["base_sha"]) == 40

    by_path = {f["path"]: f for f in diff["files"]}
    assert set(by_path) == {"README.md", "newfile.txt"}
    assert by_path["newfile.txt"]["status"] == "added"
    assert by_path["newfile.txt"]["additions"] == 1
    assert by_path["README.md"]["status"] == "modified"
    assert by_path["newfile.txt"]["patch"].startswith("diff --git")
    assert "brand-new" in by_path["newfile.txt"]["patch"]
    assert "README" not in by_path["newfile.txt"]["patch"]

    assert ".gt_state" not in diff["patch"]
    assert "trajectory.json" not in diff["patch"]
    assert not any(".gt_state" in f["path"] for f in diff["files"])


# --------------------------------------------------------------------------
# 8b: file relation graph
# --------------------------------------------------------------------------
def test_graph_nodes_match_the_tree_and_carry_the_seeded_import(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git — the graph is parsed
    out of the real files of a real clone."""
    session_id = _create_idle(harness)

    tree = harness.client.get(
        f"/api/sessions/{session_id}/tree", headers=harness.auth
    ).json()
    response = harness.client.get(
        f"/api/sessions/{session_id}/graph", headers=harness.auth
    )
    assert response.status_code == 200, response.text
    graph = response.json()

    assert [n["path"] for n in graph["nodes"]] == [f["path"] for f in tree["files"]]
    assert all(n["id"] == n["path"] for n in graph["nodes"])
    assert graph["base_sha"] == tree["base_sha"] and len(graph["base_sha"]) == 40
    assert graph["gt"] is False, "this session is gt_mode=off"
    assert "truncated" not in graph, "optional field, absent below the cap"

    by_path = {n["path"]: n for n in graph["nodes"]}
    assert by_path["app.py"]["lang"] == "py" and by_path["app.py"]["dir"] == ""
    assert by_path["pkg/util.py"]["dir"] == "pkg"
    assert by_path["README.md"]["lang"] == "md"

    assert {
        (e["source"], e["target"], e["kind"], e["weight"]) for e in graph["edges"]
    } == {("app.py", "pkg/util.py", "import", 1)}

    # cached, and stable across calls
    assert harness.client.get(
        f"/api/sessions/{session_id}/graph", headers=harness.auth
    ).json() == graph

    # a file the agent writes shows up on the next call (the cache is keyed to
    # the tree, not to the session)
    harness.set_script([_action("echo x > brand_new.py"), _reply("Added a file.")])
    _post_message(harness, session_id, "add a file")
    _wait_status(harness, session_id, {"idle"})
    refreshed = harness.client.get(
        f"/api/sessions/{session_id}/graph", headers=harness.auth
    ).json()
    assert "brand_new.py" in {n["path"] for n in refreshed["nodes"]}
    assert {n["path"] for n in refreshed["nodes"]} == {
        f["path"]
        for f in harness.client.get(
            f"/api/sessions/{session_id}/tree", headers=harness.auth
        ).json()["files"]
    }


# --------------------------------------------------------------------------
# 9: close
# --------------------------------------------------------------------------
def test_close_removes_the_workspace_and_rejects_new_messages(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "do the thing")
    _wait_status(harness, session_id, {"idle"})
    workspace = harness.workspaces / session_id
    assert workspace.is_dir()

    closed = harness.client.post(
        f"/api/sessions/{session_id}/close", headers=harness.auth
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert not workspace.exists()

    assert _post_message(harness, session_id, "hello?").status_code == 409

    # idempotent
    again = harness.client.post(
        f"/api/sessions/{session_id}/close", headers=harness.auth
    )
    assert again.status_code == 200
    assert again.json()["status"] == "closed"
    assert _session(harness, session_id)["status"] == "closed"

    # the stream of a closed session replays and self-terminates
    frames = _read_sse(harness, session_id, until=lambda _f: False)
    assert frames[-1]["event"] == "lifecycle"
    assert frames[-1]["payload"]["data"]["status"] == "closed"


# --------------------------------------------------------------------------
# 10: replay
# --------------------------------------------------------------------------
def test_last_event_id_header_replays_like_after_id(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus + SSE, agent loop, bash environment, git."""
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "do the thing")
    _wait_status(harness, session_id, {"idle"})
    harness.client.post(f"/api/sessions/{session_id}/close", headers=harness.auth)

    everything = _read_sse(harness, session_id, until=lambda _f: False)
    ids = [f["id"] for f in everything]
    assert len(ids) >= 8
    assert ids == sorted(set(ids))

    cutoff = ids[len(ids) // 2]
    by_param = _read_sse(
        harness, session_id, until=lambda _f: False, params={"after_id": cutoff}
    )
    by_header = _read_sse(
        harness,
        session_id,
        until=lambda _f: False,
        headers={"Last-Event-ID": str(cutoff)},
    )

    assert [f["id"] for f in by_param] == [i for i in ids if i > cutoff]
    assert by_header == by_param
    assert by_param == everything[len(everything) - len(by_param):]
    assert _read_sse(
        harness, session_id, until=lambda _f: False, params={"after_id": ids[-1]}
    ) == []


# --------------------------------------------------------------------------
# 11: infrastructure failure
# --------------------------------------------------------------------------
def test_environment_failure_emits_agent_error_and_fails_the_session(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment (a subclass that raises),
    git."""
    harness.env_class = ExplodingEnvironment
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "break something")
    session = _wait_status(harness, session_id, {"failed"})
    assert session["status"] == "failed"

    frames = _read_sse(harness, session_id, until=_has("lifecycle", status="failed"))
    assert "error" not in _types(frames), (
        "the event type must be agent_error — `error` collides with "
        "EventSource's native error event"
    )
    error = frames[_index(frames, "agent_error")]["payload"]["data"]
    assert "environment exploded" in error["error"]
    assert error["turn_id"]

    assert _post_message(harness, session_id, "again?").status_code == 409


# --------------------------------------------------------------------------
# 12: listing and validation
# --------------------------------------------------------------------------
def test_listing_is_newest_first(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git. Workspace workers are
    held so the listing is asserted against a stable set of rows."""
    assert harness.client.get("/api/sessions", headers=harness.auth).json() == []

    harness.hold_worker()
    created = []
    for i in range(3):
        response = _create(harness, ref="main", model=f"model-{i}")
        assert response.status_code == 201
        created.append(response.json()["id"])
        time.sleep(0.02)

    listed = harness.client.get("/api/sessions", headers=harness.auth).json()
    assert [s["id"] for s in listed] == list(reversed(created))
    assert [s["model"] for s in listed] == ["model-2", "model-1", "model-0"]
    assert all(s["status"] == "creating" for s in listed)
    timestamps = [s["created_at"] for s in listed]
    assert timestamps == sorted(timestamps, reverse=True)


def test_validation_and_404s(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    for bad in [
        "https://gitlab.com/example/repo",
        "git@github.com:example/repo.git",
        "http://github.com/example/repo",
        "https://github.com/example",
        "https://github.com/example/repo/tree/main",
    ]:
        response = _create(harness, repo=bad)
        assert response.status_code == 400, f"{bad} -> {response.status_code}"
        assert "GitHub HTTPS URL" in response.json()["detail"]

    missing = harness.client.post(
        "/api/sessions", json={"repo": REPO_URL}, headers=harness.auth
    )
    assert missing.status_code == 422
    assert ("body", "model") in {tuple(e["loc"]) for e in missing.json()["detail"]}

    for path in ["", "/messages", "/diff", "/tree", "/graph", "/receipts", "/events"]:
        assert (
            harness.client.get(
                f"/api/sessions/nope{path}", headers=harness.auth
            ).status_code
            == 404
        ), path
    for path in ["/messages", "/stop", "/close"]:
        assert (
            harness.client.post(
                f"/api/sessions/nope{path}",
                json={"content": "hi"},
                headers=harness.auth,
            ).status_code
            == 404
        ), path

    session_id = _create_idle(harness)
    empty = harness.client.post(
        f"/api/sessions/{session_id}/messages", json={}, headers=harness.auth
    )
    assert empty.status_code == 422
    assert ("body", "content") in {tuple(e["loc"]) for e in empty.json()["detail"]}
    assert (
        harness.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": ""},
            headers=harness.auth,
        ).status_code
        == 422
    )


def test_messages_are_rejected_while_the_workspace_is_being_created(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git."""
    harness.hold_worker()
    session_id = _create(harness).json()["id"]
    assert _session(harness, session_id)["status"] == "creating"

    rejected = _post_message(harness, session_id, "too early")
    assert rejected.status_code == 409
    assert "creating" in rejected.json()["detail"]
    assert (
        harness.client.post(
            f"/api/sessions/{session_id}/stop", headers=harness.auth
        ).status_code
        == 409
    )
