"""Endpoint-level tests for the cloud coding agent HTTP API (HAR-84).

FAKE BOUNDARY (module-wide, single fake):
    Faked: the model provider (LLM). ``ScriptedModel`` replaces the LiteLLM
    client and nothing else, so no network call is made.
    Real: the FastAPI app (via lifespan), the SQLite ``SessionStore``, the
    ``EventBus`` + SSE encoder, the ``SteerableAgent`` loop, the bash
    environment (``minisweagent.environments.local.LocalEnvironment``, real
    subprocesses on the real filesystem), and git (a real repository is
    created on disk, really cloned, and the patch is a real ``git diff``).

    The only other test seam is scheduling, not behaviour:
    ``_GitCloneRedirector`` rewrites the *remote* of the ``git clone`` argv to
    a local seed repository (the HTTP body still carries a real
    ``https://github.com/...`` URL so route validation is exercised), and a
    thin wrapper around ``SessionRunner._run_blocking`` lets a test hold the
    background worker at its entry point so pre-run assertions are not racy.
    Both wrappers run the real code they wrap.

Run: ``python -m pytest tests/test_cloud_routes.py -q`` from the repo root.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloud.server import deps
from cloud.server import runner as runner_module
from cloud.server.app import create_app
from cloud.server.runner import SessionRunner
from cloud.server.steerable_agent import SteerableAgent

# Hard timeouts everywhere: a stuck run must fail fast, never hang CI.
GATE_TIMEOUT = 20.0
POLL_TIMEOUT = 20.0
POLL_INTERVAL = 0.02
TERMINAL_STATUSES = {"completed", "failed", "stopped"}

REPO_URL = "https://github.com/example/repo"
MODEL_NAME = "scripted-fake/no-network"
STEER_TEXT = "STEERING: stop editing README and touch STEERED.txt instead"

SYSTEM_TEMPLATE = "You are a test coding agent."
INSTANCE_TEMPLATE = "Task: {{task}}"

# Modifies a *tracked* file, which is what `git diff` (used by
# SessionRunner._extract_patch) can actually see, and additionally creates an
# untracked file (see test_patch_includes_new_untracked_files).
TOUCH_TRACKED = "echo patched >> README.md"
MAKE_UNTRACKED = "echo brand-new > newfile.txt"


# --------------------------------------------------------------------------
# scripted model + real environment
# --------------------------------------------------------------------------
def _action(command: str, cost: float = 0.01) -> dict:
    return {
        "role": "assistant",
        "content": f"I will run: {command}",
        "extra": {"actions": [{"command": command}], "cost": cost},
    }


def _exit_message(submission: str = "done") -> dict:
    return {
        "role": "exit",
        "content": "Submitted",
        "extra": {"exit_status": "Submitted", "submission": submission, "cost": 0.0},
    }


DEFAULT_SCRIPT: list[dict] = [
    _action(f"{TOUCH_TRACKED} && {MAKE_UNTRACKED}"),
    _exit_message(),
]


class ScriptedModel:
    """FAKE BOUNDARY: the LLM provider. Replays a fixed list of mini-swe messages.

    Records every message list it is handed (``seen_messages``) so a test can
    prove what the agent actually sent upstream, and can block on a
    ``threading.Event`` before a chosen call so a test can act mid-run.
    """

    def __init__(
        self,
        steps: list[dict],
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
            return {
                "role": "exit",
                "content": "LimitsExceeded",
                "extra": {"exit_status": "LimitsExceeded", "submission": ""},
            }
        return self._steps[self.calls - 1]

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
                "extra": {},
            }
            for o in outputs
        ]

    def get_template_vars(self, **_: Any) -> dict:
        return {}

    def serialize(self) -> dict:
        return {"info": {"config": {"model": {"model_name": "scripted-fake"}}}}


def _make_environment(cwd: str):
    """Real bash execution inside the cloned workdir (no container fake)."""
    try:  # the live-run engineer may land cloud/server/environment.py
        from cloud.server.environment import CloudLocalEnvironment  # type: ignore
    except Exception:
        from minisweagent.environments.local import (
            LocalEnvironment,
            LocalEnvironmentConfig,
        )

        return LocalEnvironment(
            config_class=LocalEnvironmentConfig, cwd=cwd, timeout=30
        )
    return CloudLocalEnvironment(cwd=cwd, timeout=30)


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
    _git("add", "README.md", cwd=repo)
    _git("-c", "commit.gpgsign=false", "commit", "-m", "init", cwd=repo)
    return repo


class _GitCloneRedirector:
    """Stands in for ``cloud.server.runner.subprocess``.

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
_ORIGINAL_RUN_BLOCKING = SessionRunner._run_blocking


class Harness:
    def __init__(self, seed_repo: Path) -> None:
        self.seed_repo = seed_repo
        self.client: TestClient = None  # type: ignore[assignment]
        self.script: list[dict] = list(DEFAULT_SCRIPT)
        self.models: dict[str, ScriptedModel] = {}
        self.agents: dict[str, SteerableAgent] = {}
        self._gate_call = 0
        self.model_gate = threading.Event()
        self.model_gate.set()
        self.start_gate = threading.Event()
        self.start_gate.set()
        self.aborted = False

    # -- test controls ----------------------------------------------------
    def set_script(self, script: list[dict]) -> None:
        self.script = list(script)

    def block_model_at(self, call_index: int) -> None:
        """Hold the agent inside model call ``call_index`` (1-based)."""
        self._gate_call = call_index
        self.model_gate.clear()

    def release_model(self) -> None:
        self.model_gate.set()

    def hold_worker(self) -> None:
        """Keep launched sessions parked at the runner entry point (status pending)."""
        self.start_gate.clear()

    def release_worker(self) -> None:
        self.start_gate.set()

    def new_model(self, session_id: str) -> ScriptedModel:
        model = ScriptedModel(
            self.script, gate=self.model_gate, gate_call=self._gate_call
        )
        self.models[session_id] = model
        return model

    def wait_blocked(self, session_id: str, timeout: float = GATE_TIMEOUT) -> ScriptedModel:
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
        try:
            runner = deps.get_runner()
        except Exception:
            return
        deadline = time.monotonic() + 10.0
        while runner._running_count > 0 and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)


def _patched_build_agent(harness: Harness):
    def _build_agent(
        self: SessionRunner,
        *,
        task: str,
        model: str,
        cwd: str,
        gt_mode: str,
        step_limit: int,
        temperature: float,
        session_id: str,
        loop,
    ) -> SteerableAgent:
        state_dir = Path(cwd) / ".gt_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        agent = SteerableAgent(
            harness.new_model(session_id),
            _make_environment(cwd),
            event_callback=lambda event: self._emit_sync(loop, session_id, event),
            system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE,
            step_limit=step_limit,
            output_path=state_dir / "trajectory.json",
        )
        harness.agents[session_id] = agent
        return agent

    return _build_agent


def _patched_run_blocking(harness: Harness):
    def _run_blocking(self: SessionRunner, session_id: str, **kwargs):
        if not harness.start_gate.wait(GATE_TIMEOUT) or harness.aborted:
            return None
        return _ORIGINAL_RUN_BLOCKING(self, session_id, **kwargs)

    return _run_blocking


@pytest.fixture
def harness(tmp_path, monkeypatch):
    seed = _make_seed_repo(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cloud.db"))
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS", "10")
    # deps is module-global state populated by the lifespan; reset so nothing
    # leaks between tests (monkeypatch restores the originals afterwards).
    monkeypatch.setattr(deps, "_store", None, raising=False)
    monkeypatch.setattr(deps, "_event_bus", None, raising=False)
    monkeypatch.setattr(deps, "_runner", None, raising=False)

    h = Harness(seed)
    monkeypatch.setattr(runner_module, "subprocess", _GitCloneRedirector(seed))
    monkeypatch.setattr(SessionRunner, "_build_agent", _patched_build_agent(h))
    monkeypatch.setattr(SessionRunner, "_run_blocking", _patched_run_blocking(h))

    with TestClient(create_app()) as client:
        h.client = client
        try:
            yield h
        finally:
            h.shutdown()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _create_session(client: TestClient, **overrides: Any):
    body = {
        "repo": REPO_URL,
        "ref": "main",
        "task": "Append a line to the README",
        "model": MODEL_NAME,
        "gt_mode": "off",
        "step_limit": 10,
    }
    body.update(overrides)
    return client.post("/api/sessions", json=body)


def _create_ok(client: TestClient, **overrides: Any) -> str:
    response = _create_session(client, **overrides)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _session(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_status(
    client: TestClient,
    session_id: str,
    statuses: set[str],
    timeout: float = POLL_TIMEOUT,
) -> dict:
    deadline = time.monotonic() + timeout
    session = {}
    while time.monotonic() < deadline:
        session = _session(client, session_id)
        if session["status"] in statuses:
            return session
        time.sleep(POLL_INTERVAL)
    raise AssertionError(
        f"session {session_id} never reached {sorted(statuses)}; "
        f"last={session.get('status')!r}"
    )


def _wait_for_subscriber(session_id: str, timeout: float = GATE_TIMEOUT) -> None:
    """Wait until the SSE generator has registered its live queue on the bus."""
    bus = deps.get_event_bus()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bus._queues.get(session_id):
            return
        time.sleep(POLL_INTERVAL)
    raise AssertionError("SSE subscriber never registered on the event bus")


def _parse_sse(text: str) -> list[dict]:
    frames = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        frame: dict[str, Any] = {}
        for line in block.split("\n"):
            key, _, value = line.partition(": ")
            if key == "id":
                frame["id"] = int(value)
            elif key == "event":
                frame["event"] = value
            elif key == "data":
                frame["payload"] = json.loads(value)
        frames.append(frame)
    return frames


def _fetch_events(client: TestClient, session_id: str, after_id: int = 0) -> list[dict]:
    """Read the SSE stream of an already-terminal session (it must self-close)."""
    params = {"after_id": after_id} if after_id else None
    response = client.get(f"/api/sessions/{session_id}/events", params=params)
    assert response.status_code == 200, response.text
    return _parse_sse(response.text)


def _types(frames: list[dict]) -> list[str]:
    return [f["event"] for f in frames]


def _first_index(frames: list[dict], event_type: str, **data_match: Any) -> int:
    for i, frame in enumerate(frames):
        if frame["event"] != event_type:
            continue
        data = frame["payload"]["data"]
        if all(data.get(k) == v for k, v in data_match.items()):
            return i
    raise AssertionError(
        f"no {event_type} frame matching {data_match} in {_types(frames)}"
    )


# --------------------------------------------------------------------------
# 1-4: POST /sessions contract + GET /sessions/{id}
# --------------------------------------------------------------------------
def test_post_sessions_creates_pending_session_and_returns_201(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git. The background worker is held
    at its entry point so the freshly created row is observed before it starts."""
    harness.hold_worker()

    response = _create_session(harness.client, task="Do the thing")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["id"]
    assert body["status"] == "pending"
    assert body["repo"] == REPO_URL
    assert body["ref"] == "main"
    assert body["task"] == "Do the thing"
    assert body["model"] == MODEL_NAME
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["steps"] == 0

    assert _session(harness.client, body["id"])["status"] == "pending"


def test_post_sessions_rejects_non_github_url_with_400(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    for bad in [
        "https://gitlab.com/example/repo",
        "git@github.com:example/repo.git",
        "http://github.com/example/repo",
        "https://github.com/example",
        "https://github.com/example/repo/tree/main",
    ]:
        response = _create_session(harness.client, repo=bad)
        assert response.status_code == 400, f"{bad} -> {response.status_code}"
        assert "GitHub HTTPS URL" in response.json()["detail"]

    assert harness.client.get("/api/sessions").json() == []


def test_post_sessions_rejects_missing_task_with_422(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    response = harness.client.post("/api/sessions", json={"repo": REPO_URL})
    assert response.status_code == 422, response.text
    missing = {tuple(e["loc"]) for e in response.json()["detail"]}
    assert ("body", "task") in missing

    response = harness.client.post("/api/sessions", json={"task": "no repo"})
    assert response.status_code == 422
    assert ("body", "repo") in {
        tuple(e["loc"]) for e in response.json()["detail"]
    }


def test_get_session_404_for_unknown_id(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    response = harness.client.get("/api/sessions/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"

    assert harness.client.get("/api/sessions/does-not-exist/result").status_code == 404
    assert harness.client.get("/api/sessions/does-not-exist/events").status_code == 404
    assert (
        harness.client.post(
            "/api/sessions/does-not-exist/steer", json={"content": "hi"}
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------
# 5: job lifecycle
# --------------------------------------------------------------------------
def test_session_lifecycle_pending_running_completed(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git — the repo is really cloned
    and the agent's shell commands really run."""
    harness.block_model_at(1)
    session_id = _create_ok(harness.client)

    harness.wait_blocked(session_id)
    running = _session(harness.client, session_id)
    assert running["status"] == "running"
    assert running["started_at"] is not None
    assert running["finished_at"] is None

    harness.release_model()
    done = _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    assert done["status"] == "completed"
    assert done["finished_at"] is not None
    assert done["finished_at"] >= done["started_at"] >= done["created_at"]
    assert done["steps"] > 0
    assert done["steps"] == len(DEFAULT_SCRIPT)
    assert done["cost"] > 0

    # the clone really happened, against the real https URL from the request body
    clone_argv = runner_module.subprocess.clone_calls[0]
    assert clone_argv[:2] == ("git", "clone")
    assert REPO_URL in clone_argv


# --------------------------------------------------------------------------
# 6-7: SSE
# --------------------------------------------------------------------------
def test_sse_stream_orders_events_and_terminates(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git.

    The subscriber attaches while the agent is parked in its first model call,
    so every assistant/tool event below is delivered live through the bus queue
    (none of them existed at subscribe time), and the stream must close itself.
    """
    harness.block_model_at(1)
    session_id = _create_ok(harness.client)
    harness.wait_blocked(session_id)

    captured: dict[str, Any] = {}

    def _consume() -> None:
        with harness.client.stream(
            "GET", f"/api/sessions/{session_id}/events"
        ) as response:
            captured["status_code"] = response.status_code
            captured["content_type"] = response.headers.get("content-type", "")
            captured["text"] = "".join(response.iter_text())

    reader = threading.Thread(target=_consume, daemon=True)
    reader.start()

    _wait_for_subscriber(session_id)
    harness.release_model()

    reader.join(timeout=POLL_TIMEOUT)
    assert not reader.is_alive(), "SSE stream never terminated"

    assert captured["status_code"] == 200
    assert captured["content_type"].startswith("text/event-stream")

    frames = _parse_sse(captured["text"])
    ids = [f["id"] for f in frames]
    assert ids == sorted(set(ids)) and len(ids) == len(set(ids)), ids

    assistant = _first_index(frames, "assistant")
    tool_call = _first_index(frames, "tool_call")
    tool_result = _first_index(frames, "tool_result")
    lifecycle_running = _first_index(frames, "lifecycle", status="running")
    completed = _first_index(frames, "lifecycle", status="completed")
    assert lifecycle_running < assistant < tool_call < tool_result < completed

    assert frames[-1]["event"] == "lifecycle"
    assert frames[-1]["payload"]["data"]["status"] == "completed"
    assert frames[tool_call]["payload"]["data"]["command"].startswith("echo patched")
    assert frames[tool_result]["payload"]["data"]["returncode"] == 0
    assert frames[tool_result]["payload"]["data"]["is_error"] is False
    for frame in frames:
        assert frame["payload"]["id"] == frame["id"]
        assert frame["payload"]["timestamp"] > 0

    _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)


def test_sse_after_id_replays_only_newer_events(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    session_id = _create_ok(harness.client)
    _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    everything = _fetch_events(harness.client, session_id)
    assert len(everything) >= 6
    all_ids = [f["id"] for f in everything]

    cutoff = all_ids[len(all_ids) // 2]
    tail = _fetch_events(harness.client, session_id, after_id=cutoff)

    assert [f["id"] for f in tail] == [i for i in all_ids if i > cutoff]
    assert all(f["id"] > cutoff for f in tail)
    assert tail == everything[len(everything) - len(tail):]

    # replaying past the end yields nothing at all
    assert _fetch_events(harness.client, session_id, after_id=all_ids[-1]) == []


# --------------------------------------------------------------------------
# 8: result retrieval
# --------------------------------------------------------------------------
def test_result_409_while_running_then_returns_patch_and_receipt(
    harness: Harness,
) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git — the patch below is a real
    `git diff` of a real clone that a real bash command modified."""
    harness.block_model_at(1)
    session_id = _create_ok(harness.client)
    harness.wait_blocked(session_id)

    running = harness.client.get(f"/api/sessions/{session_id}/result")
    assert running.status_code == 409
    assert running.json()["detail"] == "session not finished yet"

    harness.release_model()
    _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    response = harness.client.get(f"/api/sessions/{session_id}/result")
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["id"] == session_id
    assert result["terminal_outcome"] == "submitted"

    patch = result["patch"]
    assert patch, "patch must not be empty"
    assert "diff --git" in patch
    assert "README.md" in patch
    assert "+patched" in patch.replace("\r", "")

    receipt = result["receipt"]
    assert receipt["n_calls"] == len(DEFAULT_SCRIPT)
    assert receipt["exit_status"] == "Submitted"
    assert receipt["terminal_outcome"] == "submitted"
    assert receipt["submission"] == "done"
    assert receipt["cost"] > 0

    messages = result["trajectory"]["messages"]
    assert [m["role"] for m in messages[:2]] == ["system", "user"]
    assert messages[-1]["role"] == "exit"


def test_patch_includes_new_untracked_files(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git.

    Regression guard: a plain `git diff` silently drops files the agent
    created, which would make a "write the new module" session return an empty
    patch. The harness's own scratch (`.gt_state/trajectory.json`) must stay
    out of the patch.
    """
    harness.set_script([_action(MAKE_UNTRACKED), _exit_message()])
    session_id = _create_ok(harness.client)
    _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    result = harness.client.get(f"/api/sessions/{session_id}/result").json()
    patch = result["patch"]
    assert patch, "a newly created file must appear in the patch"
    assert "newfile.txt" in patch
    assert "brand-new" in patch
    assert ".gt_state" not in patch
    assert "trajectory.json" not in patch


# --------------------------------------------------------------------------
# 9: the steering proof
# --------------------------------------------------------------------------
def test_steer_message_is_drained_at_step_boundary(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git.

    Proof that steering is load-bearing, not decorative. The agent is parked
    inside its FIRST model call (SteerableAgent drains the queue at the top of
    the loop, i.e. *before* each query, so gating call #1 is what puts the
    POST /steer strictly between step 1 and step 2). While it is parked the
    test POSTs a steering message; after release the loop must drain it at the
    step boundary, hand it to the model as a user turn, and emit a `steering`
    event before the next assistant event.
    """
    harness.set_script(
        [_action(TOUCH_TRACKED), _action("echo second step"), _exit_message()]
    )
    harness.block_model_at(1)
    session_id = _create_ok(harness.client)
    model = harness.wait_blocked(session_id)

    assert _session(harness.client, session_id)["status"] == "running"
    steered = harness.client.post(
        f"/api/sessions/{session_id}/steer", json={"content": STEER_TEXT}
    )
    assert steered.status_code == 202, steered.text
    assert steered.json() == {"status": "queued"}

    harness.release_model()
    _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    # --- 1. it landed in the trajectory at a step boundary ---------------
    messages = harness.client.get(f"/api/sessions/{session_id}/result").json()[
        "trajectory"
    ]["messages"]
    roles = [m["role"] for m in messages]
    assistant_idx = [i for i, r in enumerate(roles) if r == "assistant"]
    assert len(assistant_idx) >= 2, roles
    steer_idx = [
        i for i, m in enumerate(messages) if m.get("content") == STEER_TEXT
    ]
    assert len(steer_idx) == 1, "steering message must be injected exactly once"
    steer_at = steer_idx[0]

    assert messages[steer_at]["role"] == "user"
    observation_at = assistant_idx[0] + 1
    assert messages[observation_at]["content"].startswith("Observation")
    # first assistant -> its observation -> steering -> second assistant
    assert assistant_idx[0] < observation_at < steer_at < assistant_idx[1]

    # --- 2. the model actually saw it on its next request ----------------
    assert model.calls >= 2
    second_request = model.seen_messages[1]
    assert any(m.get("content") == STEER_TEXT for m in second_request), (
        "the steering text never reached the model's second request"
    )
    assert not any(
        m.get("content") == STEER_TEXT for m in model.seen_messages[0]
    ), "steering leaked into the first request"

    # --- 3. it is observable on the SSE stream, before the next assistant --
    frames = _fetch_events(harness.client, session_id)
    steering_frames = [i for i, f in enumerate(frames) if f["event"] == "steering"]
    assistant_frames = [i for i, f in enumerate(frames) if f["event"] == "assistant"]
    assert len(steering_frames) == 1
    assert frames[steering_frames[0]]["payload"]["data"]["content"] == STEER_TEXT
    assert assistant_frames[0] < steering_frames[0] < assistant_frames[1]


# --------------------------------------------------------------------------
# 10-11: stop / steer guards
# --------------------------------------------------------------------------
def test_stop_terminates_session_with_stopped_status(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    harness.set_script(
        [_action(TOUCH_TRACKED), _action("echo never runs"), _exit_message()]
    )
    harness.block_model_at(1)
    session_id = _create_ok(harness.client)
    model = harness.wait_blocked(session_id)

    stopped = harness.client.post(f"/api/sessions/{session_id}/stop")
    assert stopped.status_code == 202, stopped.text
    assert stopped.json() == {"status": "stopping"}

    harness.release_model()
    session = _wait_for_status(harness.client, session_id, TERMINAL_STATUSES)

    assert session["status"] == "stopped"
    assert session["finished_at"] is not None
    assert model.calls == 1, "the loop must not start another step after stop"
    assert session["steps"] == 1

    result = harness.client.get(f"/api/sessions/{session_id}/result").json()
    assert result["terminal_outcome"] == "user_stopped"
    assert result["receipt"]["terminal_outcome"] == "user_stopped"
    assert result["receipt"]["exit_status"] == "UserStopped"

    frames = _fetch_events(harness.client, session_id)
    stop_at = _first_index(frames, "lifecycle", status="stopped")
    assert stop_at == len(frames) - 1, "the stop event must be the last one"
    terminal_statuses = [
        f["payload"]["data"].get("status")
        for f in frames
        if f["event"] == "lifecycle"
        and f["payload"]["data"].get("status") in TERMINAL_STATUSES
    ]
    assert terminal_statuses == ["stopped"], (
        f"a stopped session must not also report completed: {terminal_statuses}"
    )

    # a stopped session accepts neither steering nor a second stop
    assert (
        harness.client.post(
            f"/api/sessions/{session_id}/steer", json={"content": "too late"}
        ).status_code
        == 409
    )
    assert harness.client.post(f"/api/sessions/{session_id}/stop").status_code == 409


def test_steer_409_when_session_not_running(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git."""
    harness.hold_worker()
    pending_id = _create_ok(harness.client)
    assert _session(harness.client, pending_id)["status"] == "pending"

    response = harness.client.post(
        f"/api/sessions/{pending_id}/steer", json={"content": "too early"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "session is not running"

    harness.release_worker()
    _wait_for_status(harness.client, pending_id, TERMINAL_STATUSES)

    response = harness.client.post(
        f"/api/sessions/{pending_id}/steer", json={"content": "too late"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "session is not running"

    # and the body itself is still validated
    assert (
        harness.client.post(
            f"/api/sessions/{pending_id}/steer", json={"content": ""}
        ).status_code
        == 422
    )


# --------------------------------------------------------------------------
# 12: listing
# --------------------------------------------------------------------------
def test_list_sessions_newest_first(harness: Harness) -> None:
    """FAKE BOUNDARY: model provider (LLM). Real: FastAPI app, SQLite store,
    event bus, agent loop, bash environment, git. Workers are held at their
    entry point so the listing is asserted against a stable set of rows."""
    assert harness.client.get("/api/sessions").json() == []

    harness.hold_worker()
    created = []
    for i in range(3):
        created.append(_create_ok(harness.client, task=f"task {i}"))
        time.sleep(0.02)

    response = harness.client.get("/api/sessions")
    assert response.status_code == 200
    listed = response.json()

    assert [s["id"] for s in listed] == list(reversed(created))
    assert [s["task"] for s in listed] == ["task 2", "task 1", "task 0"]
    timestamps = [s["created_at"] for s in listed]
    assert timestamps == sorted(timestamps, reverse=True)
    assert all(s["status"] == "pending" for s in listed)
