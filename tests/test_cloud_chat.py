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

import asyncio
import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
import uvicorn
from minisweagent.exceptions import FormatError

from cloud.server import deps
from cloud.server import runner as runner_module
from cloud.server import workspace as workspace_module
from cloud.server.app import create_app
from cloud.server.conversational_agent import ConversationalAgent
from cloud.server.environment import CloudLocalEnvironment
from cloud.server.gt_events import install_gt_action_events
from cloud.server.runner import SessionManager
from cloud.server.workspace import DIFF_PATCH_CAP

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
#: ~760 KB of real patch, comfortably past the 512 KB snapshot cap
BIG_WRITE = "yes 0123456789abcdefghijklmnopqrstuvwxyz | head -20000 > big.txt"


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


#: HAR-84: a GroundTruth typed action. It is NOT a shell command — GT dispatches
#: it through ``execute_typed_action_fail_open`` and never touches
#: ``env.execute`` — so the ``groundtruth`` wheel is the one thing that has to
#: be stubbed here (it ships only in the server image). ``_typed_result``
#: reproduces its real ``gt.compiled_observation.v1`` return shape.
TYPED_LITERAL = "class Command"
TYPED_SCOPE = "pkg"


def _typed_action(cost: float = 0.01) -> dict:
    return {
        "role": "assistant",
        "content": "Asking GroundTruth where VALUE is defined.",
        "extra": {
            "actions": [
                {
                    "tool_name": "groundtruth",
                    "tool_call_id": "call_gt_1",
                    "gt_action": {
                        "kind": "exact_literal_search",
                        "arguments": {
                            "literal": TYPED_LITERAL,
                            "paths": [TYPED_SCOPE],
                        },
                    },
                }
            ],
            "cost": cost,
        },
    }


def _typed_result(action: dict) -> dict:
    """FAKE BOUNDARY: ``execute_typed_action_fail_open`` (the vendored wheel)."""
    arguments = (action.get("gt_action") or {}).get("arguments") or {}
    scopes = list(arguments.get("paths") or [])
    matches = [{"path": "pkg/util.py", "line": 1, "preview": "VALUE = 1"}]
    payload = {
        "schema": "gt.compiled_observation.v1",
        "action_request": {"schema": "gt.action_request.v1"},
        "evidence": {
            "schema": "gt.evidence_artifact.v1",
            "action_id": str(action.get("tool_call_id") or ""),
            "answer": {"scope": scopes, "matches": matches},
            "producer": "groundtruth.deterministic_queries.v1",
            "semantics": "exact",
            "coverage": "complete",
            "omissions": [],
        },
        "direct_answer": matches,
        "decision": {
            "schema": "gt.interception_decision.v1",
            "mode": "REPLACE",
            "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
        },
    }
    output = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "output": output,
        "returncode": 0,
        "exception_info": "",
        "extra": {
            "gt_typed_action": True,
            "compiled_observation_sha256": "c" * 64,
            "interception_decision": "REPLACE",
        },
    }


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
    def __init__(self, seed_repo: Path, workspaces: Path, db_path: Path) -> None:
        self.seed_repo = seed_repo
        self.workspaces = workspaces
        self.db_path = db_path
        #: the server's event loop, captured by ``_patched_create_blocking``
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: httpx.Client = None  # type: ignore[assignment]
        self.script: list[Any] = list(DEFAULT_SCRIPT)
        self.models: dict[str, ScriptedModel] = {}
        self.agents: dict[str, ConversationalAgent] = {}
        self.env_class: type[CloudLocalEnvironment] = CloudLocalEnvironment
        #: mimic `SessionManager._install_gt`: replace `agent.execute_actions`
        #: the way `gt_engine.miniswe_runtime.install_runtime_hooks` does
        self.install_gt_hook = False
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

    # -- server-loop access -----------------------------------------------
    def on_server_loop(self, coro, timeout: float = GATE_TIMEOUT):
        """Await a manager coroutine on the loop that owns the store."""
        assert self.loop is not None, "no session has been created yet"
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def reap(self, timeout: float = GATE_TIMEOUT) -> list[str]:
        """Run exactly one real reaper pass and return the ids it closed."""
        return self.on_server_loop(deps.get_manager().reap_idle_sessions(), timeout)

    def backdate(self, session_id: str, seconds: float) -> None:
        """Make a session look ``seconds`` older than it is.

        A second SQLite connection to the same file, not a fake: the row the
        reaper reads is the row this writes.
        """
        with sqlite3.connect(str(self.db_path), timeout=10) as db:
            db.execute(
                "UPDATE sessions SET updated_at = updated_at - ? WHERE id = ?",
                (seconds, session_id),
            )
            db.commit()

    def updated_at(self, session_id: str) -> float:
        with sqlite3.connect(str(self.db_path), timeout=10) as db:
            row = db.execute(
                "SELECT updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row is not None
        return float(row[0])

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


def _gt_style_execute_actions(agent: ConversationalAgent, message: dict) -> list[dict]:
    """The shape ``gt_engine.miniswe_runtime.install_runtime_hooks`` installs.

    The real hook binds ``environment = agent.env`` at install time and drives
    ``environment.execute(action)`` itself; the agent's own ``execute_actions``
    is kept only as ``_gt_original_execute_actions`` and never called. This
    stand-in reproduces exactly that, so the frames a GT session emits are
    exercised end to end without importing the engine.
    """
    environment = agent.env
    outputs = []
    for action in (message.get("extra", {}) or {}).get("actions", []):
        if isinstance(action, dict) and action.get("tool_name") == "groundtruth":
            # the typed branch: dispatched by GT, never seen by the shell
            outputs.append(_typed_result(action))
            continue
        outputs.append(environment.execute(action))
    return agent.add_messages(
        *agent.model.format_observation_messages(
            message, outputs, agent.get_template_vars()
        )
    )


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
        wall_seconds: int,
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
            wall_seconds=wall_seconds,
            cost_limit=0.0,
            output_path=state_dir / "trajectory.json",
        )
        if harness.install_gt_hook:
            agent._gt_original_execute_actions = agent.execute_actions
            agent.execute_actions = MethodType(_gt_style_execute_actions, agent)
            # exactly what SessionManager._install_gt does, in the same order
            install_gt_action_events(agent)
        harness.agents[session_id] = agent
        return agent

    return _build_agent


def _patched_create_blocking(harness: Harness):
    def _create_blocking(self: SessionManager, session: dict, loop):
        # The only handle a test has on the loop that owns the store, which is
        # where a manager coroutine (a reaper pass) has to be awaited.
        harness.loop = loop
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
    db_path = tmp_path / "cloud.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WORKSPACES_DIR", str(workspaces))
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS", "10")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("CORS_ORIGINS", "")
    # The creation-time model preflight talks to a real provider; the model
    # here is a scripted fake with no route at all. Sessions that need the
    # preflight itself turn it back on (see the G-11 tests).
    monkeypatch.setenv("MODEL_PREFLIGHT", "0")
    monkeypatch.delenv("ALLOWED_GITHUB_LOGINS", raising=False)
    monkeypatch.delenv("WORKSPACES_MIN_FREE_MB", raising=False)
    monkeypatch.delenv("SANDBOX_WORKSPACE_MAX_MB", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_CREATIONS", raising=False)
    # The stream must stay open across turns; a short heartbeat keeps the test
    # reader responsive without changing the production default (15s).
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.2")
    # The idle reaper starts for real (the lifespan wires it), but its own
    # interval never fires inside a test: reaper tests drive one pass by hand
    # so a TTL assertion is a fact, not a sleep.
    monkeypatch.setenv("SESSION_REAP_INTERVAL_SECONDS", "3600")
    monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("TURN_WALL_SECONDS", raising=False)
    # deps is module-global state populated by the lifespan; reset so nothing
    # leaks between tests (monkeypatch restores the originals afterwards).
    monkeypatch.setattr(deps, "_store", None, raising=False)
    monkeypatch.setattr(deps, "_event_bus", None, raising=False)
    monkeypatch.setattr(deps, "_manager", None, raising=False)

    h = Harness(seed, workspaces, db_path)
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


def _diff(h: Harness, session_id: str, **params: Any) -> dict:
    response = h.client.get(
        f"/api/sessions/{session_id}/diff",
        params=params or None,
        headers=h.auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


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
    # /health stays public, and names the build it is serving
    health = harness.client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    # `commit` is the stamp `cloud/deploy.sh` passes as BUILD_SHA; without a
    # build it is the literal "unknown", never missing or empty.
    assert body["commit"] == (os.environ.get("BUILD_SHA") or "unknown")
    assert set(body) == {"status", "commit"}


def test_health_reports_the_stamped_build_commit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-0: a deployment must be identifiable from the outside.

    Round-2 QA ran against a UI image two commits behind the server because
    nothing in the artefacts named a commit. `/health` now does.
    """
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    assert harness.client.get("/health").json() == {
        "status": "ok",
        "commit": "abc1234",
    }
    monkeypatch.delenv("BUILD_SHA")
    assert harness.client.get("/health").json()["commit"] == "unknown"


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
    assert body["gt_error"] is None
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
def test_gt_style_hook_still_streams_tool_frames(harness: Harness) -> None:
    """P0-1 at the manager level: GT replaces ``execute_actions`` wholesale.

    Before the fix the replacement silently dropped every ``tool_call`` /
    ``tool_result`` — which also starved ``_snapshot_diff``, since it keys off
    ``tool_result`` event ids. Emission now lives on the environment, so the
    same stream comes out either way.
    """
    harness.install_gt_hook = True
    session_id = _create_idle(harness)

    accepted = _post_message(harness, session_id, "append a line to the README")
    assert accepted.status_code == 202, accepted.text

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
        "assistant",
        "agent_reply", "turn_finished",
    ]
    calls = [f["payload"]["data"] for f in frames if f["event"] == "tool_call"]
    results = [f["payload"]["data"] for f in frames if f["event"] == "tool_result"]
    assert len(calls) == len(results) == 2
    assert [c["step"] for c in calls] == [1, 2]
    assert [r["step"] for r in results] == [1, 2]
    assert calls[0]["command"] == TOUCH_TRACKED
    assert all(r["returncode"] == 0 for r in results)

    # the agent really ran the commands, through the hook
    assert (harness.workspaces / session_id / "newfile.txt").is_file()

    # ...and the per-step diff snapshot the scrubber reads exists again
    writes = [f for f in frames if f["event"] == "tool_result"]
    snapshot = _diff(harness, session_id, through_event=writes[0]["id"])
    assert snapshot["as_of_event"] == writes[0]["id"]
    assert [f["path"] for f in snapshot["files"]] == ["README.md"]
    assert "patched" in snapshot["patch"], "no per-step diff snapshot was stored"


def test_a_typed_gt_action_becomes_one_gt_action_frame(harness: Harness) -> None:
    """HAR-84: GroundTruth typed actions are first-class on the stream.

    A typed action never reaches ``env.execute``, so it produced no
    ``tool_call``/``tool_result`` and the UI showed a model call with nothing
    under it. It now emits exactly one ``gt_action`` frame, after the
    ``assistant`` frame that asked for it and before the next one, and the turn
    receipt counts it.
    """
    harness.install_gt_hook = True
    harness.set_script([
        _typed_action(),
        _action(TOUCH_TRACKED),
        _reply("VALUE is defined in pkg/util.py."),
    ])
    session_id = _create_idle(harness, gt_mode="advisory")

    accepted = _post_message(harness, session_id, "where is VALUE defined?")
    assert accepted.status_code == 202, accepted.text

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    order = [
        f["event"]
        for f in frames
        if f["event"] in {"assistant", "gt_action", "tool_call", "tool_result"}
    ]
    # the typed action is part of the same model call: no extra assistant frame
    assert order == [
        "assistant", "gt_action",
        "assistant", "tool_call", "tool_result",
        "assistant",
    ]

    gt_frames = [f for f in frames if f["event"] == "gt_action"]
    assert len(gt_frames) == 1
    event = gt_frames[0]["payload"]["data"]
    assert event["kind"] == "exact_literal_search"
    assert event["arguments"] == {
        "literal": TYPED_LITERAL, "paths": [TYPED_SCOPE]
    }
    assert event["scope"] == [TYPED_SCOPE]
    assert event["semantics"] == "exact"
    assert event["coverage"] == "complete"
    assert event["match_count"] == 1
    assert event["returncode"] == 0
    assert event["omissions"] == []
    assert event["reason_codes"] == ["EXACT_COMPLETE_EQUIVALENCE"]
    assert event["step"] == 1
    assert event["duration_ms"] >= 0.0
    assert event["evidence_artifact_id"] == "call_gt_1"
    assert event["turn_id"] == gt_frames[0]["payload"]["data"]["turn_id"]

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert receipts[0]["gt_actions"] == 1
    assert receipts[0]["gt_exact_matches"] == 1

    session = harness.client.get(
        f"/api/sessions/{session_id}", headers=harness.auth
    ).json()
    assert session["gt_actions"] == 1


def test_a_gt_off_turn_reports_no_gt_actions(harness: Harness) -> None:
    session_id = _create_idle(harness)
    assert _post_message(harness, session_id, "append a line").status_code == 202
    _from_turn(_read_sse(harness, session_id, until=_turn_complete))

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert receipts[0]["gt_actions"] == 0
    assert receipts[0]["gt_exact_matches"] == 0


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
    # the third `assistant` is the text-only reply: it is a model call like
    # the other two, so it gets a frame and the live step count never jumps
    assert order == [
        "turn_started",
        "assistant", "tool_call", "tool_result",
        "assistant", "tool_call", "tool_result",
        "assistant",
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
    assert [f["payload"]["data"]["step"] for f in assistants] == [1, 2, 3]
    # the reply frame carries the same n_calls the turn reports, so a client
    # counting assistant frames is never behind at the end of a turn
    assert [f["payload"]["data"].get("is_reply") for f in assistants] == [
        None, None, True
    ]
    assert assistants[-1]["payload"]["data"]["n_calls"] == 3
    assert assistants[-1]["payload"]["data"]["actions"] == []
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
# 7b: per-turn wall-clock budget
# --------------------------------------------------------------------------
def test_wall_budget_interrupts_the_command_and_ends_the_turn(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment (a real ``sleep 30`` in a
    real subprocess, really killed), git.

    Cost is always $0.0 under ``MSWEA_COST_TRACKING=ignore_errors``, so steps
    were the only budget a turn had — and a step is not a unit of time. One
    ``sleep 30`` against a 2 s budget is one step and half a minute.
    """
    monkeypatch.setenv("TURN_WALL_SECONDS", "2")
    harness.set_script([_action("sleep 30"), _reply("Picked back up.")])
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "take your time")
    started = time.monotonic()
    session = _wait_status(harness, session_id, {"idle"})
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"the turn ran {elapsed:.1f}s past a 2s budget"
    assert session["status"] == "idle", "a time limit is an ending, not a failure"
    assert session["total_wall_seconds"] > 0

    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))
    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert reply["finish_reason"] == "time_limit"
    assert reply["content"].startswith(
        "I used the time budget for this turn (0.03 min) without finishing."
    )
    assert "I will run: sleep 30" in reply["content"]
    assert reply["content"].endswith("Say 'continue' to keep going.")

    # the command in flight was really killed, not waited out
    result = frames[_index(frames, "tool_result")]["payload"]["data"]
    assert result["command"] == "sleep 30"
    assert result["returncode"] == 137, "128 + SIGKILL"
    assert result["is_error"] is True

    assert harness.models[session_id].calls == 1, "no step after the deadline"
    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert receipts[0]["finish_reason"] == "time_limit"
    assert receipts[0]["wall_seconds"] >= 2.0

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert messages[-1]["meta"]["finish_reason"] == "time_limit"


def test_a_turn_inside_its_wall_budget_is_untouched(harness: Harness) -> None:
    """The budget is a ceiling, not a schedule: the default path is unchanged."""
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "do the thing")
    session = _wait_status(harness, session_id, {"idle"})

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert receipts[0]["finish_reason"] == "reply"
    # the receipt carries the honest duration whether the budget was hit or not
    assert 0 < receipts[0]["wall_seconds"] < 900
    assert session["total_wall_seconds"] == pytest.approx(
        receipts[0]["wall_seconds"], abs=0.05
    )


def test_wall_seconds_is_validated_like_step_limit(harness: Harness) -> None:
    """Workspace workers are held: this is about the request body, not cloning."""
    harness.hold_worker()
    assert _create(harness, wall_seconds=60).status_code == 201
    assert _create(harness, wall_seconds=3600).status_code == 201
    assert _create(harness, wall_seconds=59).status_code == 422
    assert _create(harness, wall_seconds=3601).status_code == 422


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
# 8a: per-step diff snapshots
# --------------------------------------------------------------------------
def test_diff_through_event_returns_the_snapshot_taken_at_that_write(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus + SSE, agent loop, bash environment, git — every snapshot
    below is a real ``git diff`` taken on the turn worker after a real write.

    The scrubber asks "what did the tree look like at step N"; this is the
    exact answer, not the UI's reconstruction from the files a step touched.
    """
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "make some changes")
    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))

    writes = [f for f in frames if f["event"] == "tool_result"]
    assert [f["payload"]["data"]["command"] for f in writes] == [
        TOUCH_TRACKED,
        MAKE_UNTRACKED,
    ]
    first, second = writes[0]["id"], writes[1]["id"]

    # before any write (the tool_call that preceded it): the empty diff
    early = _diff(harness, session_id, through_event=first - 1)
    assert early["patch"] == "" and early["files"] == []
    assert early["as_of_event"] == 0 and early["approximate"] is False
    assert "truncated" not in early

    at_first = _diff(harness, session_id, through_event=first)
    assert at_first["as_of_event"] == first
    assert at_first["approximate"] is False
    assert [f["path"] for f in at_first["files"]] == ["README.md"]
    assert "patched" in at_first["patch"]
    assert "newfile.txt" not in at_first["patch"]
    assert len(at_first["base_sha"]) == 40

    # an event id between the two writes still resolves to the first snapshot
    between = _diff(harness, session_id, through_event=second - 1)
    assert between["as_of_event"] == first
    assert [f["path"] for f in between["files"]] == ["README.md"]

    at_second = _diff(harness, session_id, through_event=second)
    assert at_second["as_of_event"] == second
    assert sorted(f["path"] for f in at_second["files"]) == [
        "README.md", "newfile.txt"
    ]

    # the last snapshot of the turn is the live diff, and the live diff is
    # unchanged by any of this
    live = _diff(harness, session_id)
    assert at_second["patch"] == live["patch"]
    assert "as_of_event" not in live and "approximate" not in live


def test_reads_do_not_produce_snapshots(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: everything else.

    Only commands the write regex recognises cost a ``git diff``; a `cat` in
    the middle of a turn must not add a snapshot of its own.
    """
    harness.set_script([
        _action("cat README.md"),
        _action(TOUCH_TRACKED),
        _action("cat README.md"),
        _reply("Read, wrote, read."),
    ])
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "look then write")
    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))

    results = [f for f in frames if f["event"] == "tool_result"]
    assert len(results) == 3
    read_before, write, read_after = (f["id"] for f in results)

    assert _diff(harness, session_id, through_event=read_before)["as_of_event"] == 0
    assert _diff(harness, session_id, through_event=write)["as_of_event"] == write
    # the trailing read did not take one, so it still resolves to the write
    assert _diff(harness, session_id, through_event=read_after)["as_of_event"] == write


def test_a_slow_snapshot_disables_the_rest_of_the_turn(
    harness: Harness, monkeypatch
) -> None:
    """FAKE BOUNDARY: model provider (LLM), plus the snapshot time budget,
    lowered to zero so the first real ``compute_diff`` overruns it. Real:
    everything else.

    Snapshots are a convenience; on a tree where the diff is expensive they
    must get out of the agent's way and say so, not slow every step.
    """
    monkeypatch.setattr(runner_module, "DIFF_SNAPSHOT_BUDGET_SECONDS", 0.0)
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "make some changes")
    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))

    disabled = frames[
        _index(frames, "lifecycle", status="diff_snapshots_disabled")
    ]["payload"]["data"]
    assert "budget" in disabled["reason"]

    writes = [f for f in frames if f["event"] == "tool_result"]
    # the one that blew the budget was still stored; the next one was skipped
    assert _diff(
        harness, session_id, through_event=writes[0]["id"]
    )["as_of_event"] == writes[0]["id"]
    assert _diff(
        harness, session_id, through_event=writes[1]["id"]
    )["as_of_event"] == writes[0]["id"]


def test_a_snapshot_patch_over_the_cap_is_truncated(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: everything else — bash really
    writes ~760 KB into the clone and git really diffs it."""
    harness.set_script([_action(BIG_WRITE), _reply("Wrote a big file.")])
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "write a big file")
    frames = _from_turn(_read_sse(harness, session_id, until=_turn_complete))

    write = [f for f in frames if f["event"] == "tool_result"][0]
    snapshot = _diff(harness, session_id, through_event=write["id"])
    assert snapshot["truncated"] is True
    assert len(snapshot["patch"].encode("utf-8")) <= DIFF_PATCH_CAP
    assert [f["path"] for f in snapshot["files"]] == ["big.txt"]
    # per-file bodies are dropped once the combined patch is already cut
    assert snapshot["files"][0]["patch"] == ""
    assert snapshot["files"][0]["additions"] == 20000

    live = _diff(harness, session_id)
    assert "truncated" not in live
    assert len(live["patch"].encode("utf-8")) > DIFF_PATCH_CAP


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
# 9b: idle-session TTL reaper
# --------------------------------------------------------------------------
def test_an_expired_idle_session_is_closed_exactly_like_close(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment, git — and a real reaper
    pass, awaited on the server's own event loop.

    A workspace is a full repo clone. Without a TTL it lives until someone
    remembers to press close, so host disk grows with every session anyone
    ever opened and never shrinks.
    """
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "do the thing")
    _wait_status(harness, session_id, {"idle"})
    workspace = harness.workspaces / session_id
    assert workspace.is_dir()

    harness.backdate(session_id, 7200)
    assert harness.reap() == [session_id]

    session = _session(harness, session_id)
    assert session["status"] == "closed"
    assert session["closed_reason"] == "expired"
    assert not workspace.exists(), "the reaper closes exactly like /close"

    rejected = _post_message(harness, session_id, "hello?")
    assert rejected.status_code == 409
    assert "session is closed" in rejected.json()["detail"]

    frames = _read_sse(harness, session_id, until=lambda _f: False)
    assert frames[-1]["event"] == "lifecycle"
    assert frames[-1]["payload"]["data"] == {"status": "closed", "reason": "expired"}

    # a second pass has nothing left to do
    assert harness.reap() == []


def test_a_user_close_is_recorded_as_such(harness: Harness) -> None:
    """The other half of the reason: `closed` alone does not say who did it."""
    session_id = _create_idle(harness)
    closed = harness.client.post(
        f"/api/sessions/{session_id}/close", headers=harness.auth
    )
    assert closed.status_code == 200
    assert closed.json()["closed_reason"] == "user"

    frames = _read_sse(harness, session_id, until=lambda _f: False)
    assert frames[-1]["payload"]["data"] == {"status": "closed", "reason": "user"}


def test_a_running_session_is_never_reaped(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Age is not idleness: a turn that has been working for hours is working."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    harness.set_script([_action("echo one"), _reply("Done.")])
    session_id = _create_idle(harness)
    harness.block_model_at(1)
    _post_message(harness, session_id, "work on it")
    harness.wait_blocked(session_id)
    assert _session(harness, session_id)["status"] == "running"

    harness.backdate(session_id, 7200)
    assert harness.reap() == [], "a running session is not a candidate"
    assert _session(harness, session_id)["status"] == "running"
    assert (harness.workspaces / session_id).is_dir()

    harness.release_model()
    session = _wait_status(harness, session_id, {"idle"})
    assert session["closed_reason"] is None


def test_ttl_zero_disables_the_reaper(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "0")
    session_id = _create_idle(harness)
    harness.backdate(session_id, 10_000_000)

    assert harness.reap() == []
    assert _session(harness, session_id)["status"] == "idle"
    assert (harness.workspaces / session_id).is_dir()


def test_activity_inside_the_ttl_keeps_a_session_alive(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`updated_at` has to move on activity, or the TTL measures age."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    session_id = _create_idle(harness)
    harness.backdate(session_id, 7200)
    stale = harness.updated_at(session_id)

    _post_message(harness, session_id, "still here")
    _wait_status(harness, session_id, {"idle"})
    assert harness.updated_at(session_id) > stale

    assert harness.reap() == []
    assert _session(harness, session_id)["status"] == "idle"
    assert (harness.workspaces / session_id).is_dir()


def test_stop_counts_as_activity(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/stop` writes no session field of its own, so it has to touch the row."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    harness.set_script([_action("echo one"), _reply("Picked back up.")])
    session_id = _create_idle(harness)
    harness.block_model_at(1)
    _post_message(harness, session_id, "work on it")
    harness.wait_blocked(session_id)

    harness.backdate(session_id, 7200)
    stale = harness.updated_at(session_id)
    assert (
        harness.client.post(
            f"/api/sessions/{session_id}/stop", headers=harness.auth
        ).status_code
        == 202
    )
    assert harness.updated_at(session_id) > stale

    harness.release_model()
    _wait_status(harness, session_id, {"idle"})


def test_recover_reaps_expired_sessions_on_startup(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server that was down for a week must not come back holding the disk."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    expired = _create_idle(harness)
    live = _create_idle(harness)
    harness.backdate(expired, 7200)

    harness.on_server_loop(deps.get_manager().recover(), POLL_TIMEOUT)

    assert _session(harness, expired)["closed_reason"] == "expired"
    assert not (harness.workspaces / expired).exists()
    assert _session(harness, live)["status"] == "idle"
    assert (harness.workspaces / live).is_dir()


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
def test_environment_failure_ends_the_turn_and_keeps_the_session(
    harness: Harness,
) -> None:
    """HAR-84 G-04: a crash inside a turn ends the TURN, not the session.

    FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, JWT auth, SQLite
    store, event bus, agent loop, bash environment (a subclass that raises),
    git.

    The conversation, the clone and the transcript are all still there, so
    writing off the session over one bad turn destroyed recoverable work.
    """
    harness.env_class = ExplodingEnvironment
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "break something")
    frames = _read_sse(
        harness, session_id, until=_has("turn_finished", finish_reason="error")
    )
    assert "error" not in _types(frames), (
        "the event type must be agent_error — `error` collides with "
        "EventSource's native error event"
    )
    error = frames[_index(frames, "agent_error")]["payload"]["data"]
    assert "environment exploded" in error["error"]
    assert error["turn_id"]

    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert reply["finish_reason"] == "error"
    assert reply["content"].startswith("This turn failed:")
    assert "environment exploded" in reply["content"]

    # Back to idle, and the receipt says how the turn ended.
    session = _wait_status(harness, session_id, {"idle"})
    assert session["status"] == "idle"
    assert session["current_turn_id"] is None
    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    assert [r["finish_reason"] for r in receipts] == ["error"]
    assert receipts[0]["finished_at"] is not None

    # And the session still takes messages: only creation failures kill one.
    harness.env_class = CloudLocalEnvironment
    assert _post_message(harness, session_id, "again?").status_code == 202


# --------------------------------------------------------------------------
# 11b: GT degradation
# --------------------------------------------------------------------------
def test_gt_index_failure_persists_its_reason_on_the_session(
    harness: Harness, monkeypatch
) -> None:
    """FAKE BOUNDARY: model provider (LLM), plus the GT indexer, which is made
    to raise the way a real build failure does. Real: FastAPI app, JWT auth,
    SQLite store, event bus, workspace creation, git.

    GT degrading must not fail the session, and the reason must outlive the
    ``gt_unavailable`` event — after a reload the row is all the UI has.
    """
    import gt_engine.indexer as indexer

    def _fail(*_args, **_kwargs):
        raise RuntimeError("index status build_failed: nonzero_exit")

    monkeypatch.setattr(indexer, "ensure_index_with_receipt", _fail)

    session_id = _create_idle(harness, gt_mode="advisory")
    session = _session(harness, session_id)
    assert session["status"] == "idle"
    assert session["gt_status"] == "unavailable"
    assert "nonzero_exit" in session["gt_error"]

    # and it is on the listing too, not just the detail route
    listed = harness.client.get("/api/sessions", headers=harness.auth).json()
    assert listed[0]["gt_error"] == session["gt_error"]

    frames = _read_sse(
        harness, session_id, until=_has("lifecycle", status="idle")
    )
    unavailable = frames[_index(frames, "lifecycle", status="gt_unavailable")]
    assert unavailable["payload"]["data"]["error"] == session["gt_error"]


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


# --------------------------------------------------------------------------
# HAR-84 G-02 / G-13: gt_mode is a validated Literal, not free text
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["off", "advisory", "assistive", "enforced"])
def test_every_offered_gt_mode_is_accepted(harness: Harness, mode: str) -> None:
    assert _create(harness, gt_mode=mode).status_code == 201


@pytest.mark.parametrize("mode", ["banana", "engine", "ENGINE", "", "shadow"])
def test_an_unknown_gt_mode_is_rejected_at_creation(
    harness: Harness, mode: str
) -> None:
    """HAR-84 G-02: "engine" was never a GTMode member.

    It built an index, advertised gt_ready, and then raised
    ValueError: 'engine' is not a valid GTMode on the first turn of every
    session that asked for it. An unknown mode is a 422 now.
    """
    response = _create(harness, gt_mode=mode)
    assert response.status_code == 422, response.text
    assert ("body", "gt_mode") in {tuple(e["loc"]) for e in response.json()["detail"]}


# --------------------------------------------------------------------------
# HAR-84 G-11 / G-12: creation and message validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ref", ["", "   ", "\t", "main\n", "ma\x00in", "--upload-pack=x"]
)
def test_a_malformed_ref_is_rejected(harness: Harness, ref: str) -> None:
    response = _create(harness, ref=ref)
    assert response.status_code == 422, f"{ref!r} -> {response.status_code}"


def test_a_blank_model_is_rejected(harness: Harness) -> None:
    assert _create(harness, model="").status_code == 422
    assert _create(harness, model="   ").status_code == 422


def test_an_unavailable_model_is_a_400_at_creation_not_a_dead_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to buy a clone, a sandbox and a 250 s first turn before failing."""
    monkeypatch.setenv("MODEL_PREFLIGHT", "1")

    def _refuse(model: str) -> None:
        raise runner_module.ModelUnavailable(
            f"BadRequestError: {model} is not a valid model ID"
        )

    monkeypatch.setattr(runner_module, "_preflight_blocking", _refuse)

    response = _create(harness, model="not/a/real/model-xyz")

    assert response.status_code == 400, response.text
    assert "model not available" in response.json()["detail"]
    # Nothing was built for it.
    assert harness.client.get("/api/sessions", headers=harness.auth).json() == []


def test_a_working_model_passes_the_preflight(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_PREFLIGHT", "1")
    seen: list[str] = []
    monkeypatch.setattr(runner_module, "_preflight_blocking", seen.append)

    assert _create(harness).status_code == 201
    assert seen == [MODEL_NAME]


@pytest.mark.parametrize("content", ["   ", "\n\t "])
def test_a_whitespace_only_message_never_starts_a_turn(
    harness: Harness, content: str
) -> None:
    """HAR-84 G-12: "" was 422 but "   " was 202 and burned two model calls."""
    session_id = _create_idle(harness)

    response = _post_message(harness, session_id, content)

    assert response.status_code == 422, response.text
    assert _session(harness, session_id)["status"] == "idle"
    assert harness.models.get(session_id) is None


# --------------------------------------------------------------------------
# HAR-84 G-09: turn_started carries the prompt, so every tab can render it
# --------------------------------------------------------------------------
def test_turn_started_carries_the_users_own_message(harness: Harness) -> None:
    """A second tab showed the turn and the reply but never the question."""
    session_id = _create_idle(harness)
    posted = _post_message(harness, session_id, "TABSYNC please").json()

    frames = _read_sse(harness, session_id, until=_has("turn_started"))
    started = frames[_index(frames, "turn_started")]["payload"]["data"]

    assert started["content"] == "TABSYNC please"
    assert started["role"] == "user"
    assert started["message_id"] == posted["message"]["id"]
    assert started["turn_id"]


# --------------------------------------------------------------------------
# HAR-84 G-17: a malformed resume token is an error, not a full replay
# --------------------------------------------------------------------------
def test_a_malformed_last_event_id_is_rejected(harness: Harness) -> None:
    session_id = _create_idle(harness)
    for bad in ["not-a-number", "12abc", "-4"]:
        response = harness.client.get(
            f"/api/sessions/{session_id}/events",
            headers={**harness.auth, "Last-Event-ID": bad},
        )
        assert response.status_code == 400, f"{bad} -> {response.status_code}"


# --------------------------------------------------------------------------
# HAR-84 G-07: the disk floor and the per-session quota
# --------------------------------------------------------------------------
def test_a_session_is_refused_when_the_host_is_nearly_full(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_MIN_FREE_MB", "2048")
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=0, used=0, free=64 * 1024 * 1024),
    )
    response = _create(harness)
    assert response.status_code == 201
    session_id = response.json()["id"]

    session = _wait_status(harness, session_id, {"failed"})

    assert session["closed_reason"] == "failed"
    frames = _read_sse(harness, session_id, until=_has("lifecycle", status="failed"))
    error = frames[_index(frames, "lifecycle", status="failed")]["payload"]["data"]
    assert "not enough free disk" in error["error"]
    assert "2048 MB required" in error["error"]
    # Nothing was cloned.
    assert not (harness.workspaces / session_id).exists()


def test_a_workspace_over_its_quota_ends_the_turn(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAR-84 G-07b: one dd took the host from 82% to 92% full, uncapped."""
    monkeypatch.setenv("SANDBOX_WORKSPACE_MAX_MB", "1")
    harness.set_script(
        [
            _action("yes 0123456789abcdefghijklmnopqrstuvwxyz | head -60000 > fat.txt"),
            _reply("should never be reached"),
        ]
    )
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "fill the disk")
    frames = _read_sse(
        harness, session_id, until=_has("turn_finished", finish_reason="error")
    )

    quota = frames[_index(frames, "lifecycle", status="quota_exceeded")]["payload"]
    assert "workspace quota exceeded" in quota["data"]["reason"]
    reply = frames[_index(frames, "agent_reply")]["payload"]["data"]
    assert "workspace quota exceeded" in reply["content"]
    assert reply["finish_reason"] == "error"
    # The session survives the turn that overran.
    assert _wait_status(harness, session_id, {"idle"})["status"] == "idle"
    assert harness.models[session_id].calls == 1, "the next model call never happened"


def test_a_workspace_inside_its_quota_is_untouched(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_WORKSPACE_MAX_MB", "512")
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "do the thing")
    frames = _read_sse(harness, session_id, until=_turn_complete)

    statuses = [
        f["payload"]["data"].get("status") for f in frames if f["event"] == "lifecycle"
    ]
    assert "quota_exceeded" not in statuses
    finished = frames[_index(frames, "turn_finished")]["payload"]["data"]
    assert finished["finish_reason"] == "reply"


# --------------------------------------------------------------------------
# HAR-84 G-08: a restart-interrupted turn ends on the wire too
# --------------------------------------------------------------------------
def test_recover_ends_an_interrupted_turn_on_the_wire(harness: Harness) -> None:
    """HAR-84 G-08/E-02/E-03.

    The turn used to have no turn_finished, no note on the stream, and a
    receipt left with finish_reason "" and finished_at null forever.
    """
    harness.block_model_at(1)
    session_id = _create_idle(harness)
    _post_message(harness, session_id, "start something long")
    harness.wait_blocked(session_id)
    turn_id = _session(harness, session_id)["current_turn_id"]
    assert turn_id

    # The store is what survives a restart; recover() runs against exactly it.
    harness.on_server_loop(deps.get_manager().recover(), POLL_TIMEOUT)

    session = _session(harness, session_id)
    assert session["status"] == "idle"
    assert session["current_turn_id"] is None

    frames = _read_sse(harness, session_id, until=_has("system_note"))
    finished = frames[_index(frames, "turn_finished")]["payload"]["data"]
    assert finished["turn_id"] == turn_id
    assert finished["finish_reason"] == "interrupted"
    note = frames[_index(frames, "system_note")]["payload"]["data"]
    assert note["content"] == runner_module.RESTART_NOTICE
    assert note["turn_id"] == turn_id
    assert note["message_id"]

    receipts = harness.client.get(
        f"/api/sessions/{session_id}/receipts", headers=harness.auth
    ).json()
    receipt = next(r for r in receipts if r["turn_id"] == turn_id)
    assert receipt["finish_reason"] == "interrupted"
    assert receipt["finished_at"] is not None

    messages = harness.client.get(
        f"/api/sessions/{session_id}/messages", headers=harness.auth
    ).json()
    assert any(
        m["role"] == "system" and m["content"] == runner_module.RESTART_NOTICE
        for m in messages
    )
    harness.release_model()


# --------------------------------------------------------------------------
# HAR-84 G-21: creation takes a slot too
# --------------------------------------------------------------------------
def test_session_creation_is_bounded(harness: Harness) -> None:
    """4 simultaneous creations all succeeded: 4 clones, 4 sandboxes, 4 indexes.

    MAX_CONCURRENT_SESSIONS only ever gated *turns*.
    """
    harness.hold_worker()
    try:
        accepted = [_create(harness) for _ in range(3)]
        assert [r.status_code for r in accepted] == [201, 201, 201]

        refused = _create(harness)

        assert refused.status_code == 429, refused.text
        assert "creations" in refused.json()["detail"]
    finally:
        harness.release_worker()
    for response in accepted:
        _wait_status(harness, response.json()["id"], {"idle"})


def test_a_dd_style_filler_is_still_caught_within_a_bounded_number_of_steps(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dd if=/dev/zero of=big` matches none of the write verbs (HAR-84 G-07).

    The audit's repro was a *single-command* turn, so any stride at all would
    have missed it: the workspace is measured after every command.
    """
    monkeypatch.setenv("SANDBOX_WORKSPACE_MAX_MB", "1")
    harness.set_script(
        [
            _action("dd if=/dev/zero of=fat.bin bs=1M count=3 2>/dev/null"),
            _reply("should never be reached"),
        ]
    )
    session_id = _create_idle(harness)

    _post_message(harness, session_id, "fill the disk without a redirect")
    frames = _read_sse(
        harness, session_id, until=_has("turn_finished", finish_reason="error")
    )

    quota = frames[_index(frames, "lifecycle", status="quota_exceeded")]["payload"]
    assert "workspace quota exceeded" in quota["data"]["reason"]
    assert _wait_status(harness, session_id, {"idle"})["status"] == "idle"
    assert harness.models[session_id].calls == 1, "the turn stopped at that command"
