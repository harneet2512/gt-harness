"""Scripted-agent soak through the real execute_actions hook.

Run 34715686102 task 18 showed the governor aborting a live run after ~54
retrieval-only actions. These tests drive that same path provider-free: the
governor watches the command stream, steers once (twice max), then drops the
churn_abort flag the supervisor polls - and a productive edit resets the
stall instead of aborting a healthy run.
"""
from __future__ import annotations

import json
from pathlib import Path

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks


def _session(adapter):
    return GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=adapter.repo_root,
            state_dir=str(adapter.store.root.parent),
            mode=GTMode.ADVISORY,
        ),
        engine=adapter,
    )


def _configure_fixture_provider(monkeypatch):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100000")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 1)


class FakeEnv:
    """Executes commands lexically: heredoc/edit commands write a real file so
    the workspace snapshot reports a change; everything else is a read."""

    def __init__(self, root: Path):
        self.root = root
        self.n = 0

    def execute(self, action):
        command = action.get("command", "")
        if "cat >" in command or "apply_patch" in command or "write" in command:
            self.n += 1
            (self.root / f"edit_{self.n}.py").write_text("x = 1\n", encoding="utf-8")
            return {"output": "edited", "returncode": 0}
        return {"output": "file contents", "returncode": 0}


class FakeModel:
    model_name = "fixture/model"
    model_kwargs: dict = {}
    tools: list = []

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def _query(self, messages, **kwargs):
        return {"id": "r", "model": self.model_name, "usage": {}}

    def query(self, messages, **kwargs):
        prepared = self._prepare_messages_for_api(messages)
        response = self._query(prepared, **kwargs)
        return {"role": "assistant", "content": "ok",
                "extra": {"actions": [], "response": response}}

    def format_observation_messages(self, message, outputs, template_vars=None):
        return []


class FakeAgent:
    def __init__(self, root: Path):
        self.model = FakeModel()
        self.env = FakeEnv(root)
        self.messages = []

    def execute_actions(self, message):
        return [
            self.env.execute(action)
            for action in (message.get("extra") or {}).get("actions") or ()
        ]

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


def _adapter(tmp_path: Path, task_id: str = "soak") -> MiniSweAdapter:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("value = 1\n", encoding="utf-8")
    return MiniSweAdapter(
        task_id=task_id, repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )


def _drive(agent, commands):
    for command in commands:
        agent.execute_actions({"extra": {"actions": [{"command": command}]}})


def _journal(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _flag(adapter) -> dict | None:
    path = adapter.engine_state.layout.state_root / "churn_abort.json"
    return json.loads(path.read_text()) if path.is_file() else None


def test_retrieval_only_loop_steers_then_aborts_with_flag(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = FakeAgent(tmp_path)
    install_runtime_hooks(agent, _session(adapter))

    # Varied retrieval commands: no repeats >= 15, no verification, no edits.
    # The stall counter is the only governor input that can fire.
    commands = [f"grep -rn 'needle_{i}' src/ | head -20" for i in range(60)]
    _drive(agent, commands)

    governor = adapter.churn_governor
    assert governor.aborted
    flag = _flag(adapter)
    assert flag is not None and flag["schema"] == "gt.churn_abort.v1"
    assert flag["turns_observed"] == governor.turns_observed
    events = _journal(adapter)
    assert any(e["event"] == "churn_abort" for e in events)


def test_identical_command_repeat_aborts_fast(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = FakeAgent(tmp_path)
    install_runtime_hooks(agent, _session(adapter))

    _drive(agent, ["cat src/mod.py"] * 20)

    governor = adapter.churn_governor
    assert governor.aborted
    assert governor.turns_observed <= 20
    assert _flag(adapter) is not None


def test_archaeology_heavy_stall_steers_before_abort(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = FakeAgent(tmp_path)
    install_runtime_hooks(agent, _session(adapter))

    # GT-internal reads dominate the window: stall >= 25 with churn >= 0.4
    # must produce a steer, not an immediate abort.
    commands = [
        "gt-evidence read evidence/item.json",
        "cat gt-state/deliveries/x.json",
    ]
    _drive(agent, [commands[i % 2] for i in range(30)])

    governor = adapter.churn_governor
    assert governor.steers_issued >= 1
    assert not governor.aborted
    events = _journal(adapter)
    assert not any(e["event"] == "churn_abort" for e in events)


def test_productive_edit_resets_the_stall_and_no_abort(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = FakeAgent(tmp_path)
    install_runtime_hooks(agent, _session(adapter))

    # Reads interleaved with real edits and check runs never accumulate a stall.
    commands = []
    for i in range(6):
        commands += [
            f"grep -rn 'needle_{i}' src/ | head -20",
            "cat > src/new.py <<'EOF'\nx = 1\nEOF",
            "pytest -q",
        ]
    _drive(agent, commands)

    governor = adapter.churn_governor
    assert not governor.aborted
    assert governor.stall_turns == 0
    assert _flag(adapter) is None
    assert not any(e["event"] == "churn_abort" for e in _journal(adapter))


def test_probe_exploration_command_stream_does_not_abort(tmp_path, monkeypatch):
    """Recorded stream from run 34801009507, fd-deterministic-multi-key-sorting.

    The real run churn-aborted at stall_turns=50 while the agent was
    methodically probing the Rust API surface: probe crates written under
    /tmp (invisible to the repo diff, so every command arrived with
    productive=False), cargo build/run cycles, and toolchain-source reads.
    The soak harness reproduces exactly that signal environment - FakeEnv
    never mutates the repo, so the runtime passes productive=False for every
    command, identical to the paid run. The fixed governor must not kill it.
    """
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "smoke20_recorded"
         / "fd_churn_abort_commands.json").read_text(encoding="utf-8")
    )
    adapter = _adapter(tmp_path)
    agent = FakeAgent(tmp_path)
    install_runtime_hooks(agent, _session(adapter))

    _drive(agent, fixture["commands"])

    governor = adapter.churn_governor
    assert not governor.aborted
    assert _flag(adapter) is None
    assert not any(e["event"] == "churn_abort" for e in _journal(adapter))
