"""Tests for cloud.server.steerable_agent.SteerableAgent."""
from __future__ import annotations

import queue
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from cloud.server.steerable_agent import SteerableAgent


class FakeModel:
    """Scripted model that returns a sequence of responses then an exit."""

    config = MagicMock()

    def __init__(self, scripted: list[dict]) -> None:
        self._scripted = list(scripted)
        self._call_idx = 0

    def query(self, messages: list[dict]) -> dict:
        if self._call_idx >= len(self._scripted):
            return {
                "role": "exit",
                "content": "LimitsExceeded",
                "extra": {"exit_status": "LimitsExceeded", "submission": ""},
            }
        msg = self._scripted[self._call_idx]
        self._call_idx += 1
        return msg

    def format_message(self, role: str = "", content: str = "", extra: dict | None = None, **kwargs: Any) -> dict:
        msg: dict[str, Any] = {"role": role, "content": content}
        if extra is not None:
            msg["extra"] = extra
        return msg

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        return [
            {"role": "user", "content": f"Output: {o.get('output', '')}", "extra": {}}
            for o in outputs
        ]

    def get_template_vars(self, **kwargs: Any) -> dict:
        return {}

    def serialize(self) -> dict:
        return {}


class FakeEnv:
    """Fake environment that returns fixed output for each command."""

    config = MagicMock()

    def execute(self, action: dict, cwd: str = "") -> dict:
        return {
            "output": f"executed: {action.get('command', '')}",
            "returncode": 0,
            "exception_info": "",
        }

    def get_template_vars(self, **kwargs: Any) -> dict:
        return {}

    def serialize(self) -> dict:
        return {}


def _action_step(command: str) -> dict:
    return {
        "role": "assistant",
        "content": f"Running {command}",
        "extra": {
            "actions": [{"command": command}],
            "cost": 0.01,
        },
    }


def _exit_step() -> dict:
    return {
        "role": "exit",
        "content": "Submitted",
        "extra": {
            "exit_status": "Submitted",
            "submission": "done",
            "cost": 0.0,
        },
    }


def _build_agent(
    scripted: list[dict],
    event_callback: Any = None,
    step_limit: int = 10,
) -> SteerableAgent:
    model = FakeModel(scripted)
    env = FakeEnv()
    return SteerableAgent(
        model,
        env,
        event_callback=event_callback,
        system_template="You are a test agent. Task: {{ task }}",
        instance_template="Task: {{ task }}",
        step_limit=step_limit,
    )


def test_basic_run_completes() -> None:
    agent = _build_agent([_action_step("echo hello"), _exit_step()])
    result = agent.run("do the task")
    assert result["exit_status"] == "Submitted"
    assert agent.n_calls == 2


def test_steering_message_injected_at_step_boundary() -> None:
    events: list[dict] = []
    step_count = 0

    def on_event(event: dict) -> None:
        nonlocal step_count
        events.append(event)
        if event.get("type") == "tool_result" and step_count == 0:
            step_count += 1

    agent = _build_agent(
        [_action_step("echo step1"), _action_step("echo step2"), _exit_step()],
        event_callback=on_event,
    )

    agent._steering_queue.put("change approach: use pytest instead")

    result = agent.run("do the task")

    steering_events = [e for e in events if e.get("type") == "steering"]
    assert len(steering_events) == 1
    assert "change approach" in steering_events[0]["content"]

    user_messages = [
        m for m in agent.messages
        if m.get("role") == "user" and "change approach" in m.get("content", "")
    ]
    assert len(user_messages) == 1


def test_stop_terminates_with_user_stopped() -> None:
    events: list[dict] = []

    def on_event(event: dict) -> None:
        events.append(event)

    agent = _build_agent(
        [_action_step("echo step1"), _action_step("echo step2"), _exit_step()],
        event_callback=on_event,
    )

    agent._stop_event.set()

    result = agent.run("do the task")
    assert result["exit_status"] == "UserStopped"

    lifecycle_events = [
        e for e in events
        if e.get("type") == "lifecycle" and e.get("status") == "stopped"
    ]
    assert len(lifecycle_events) == 1


def test_stop_mid_run() -> None:
    call_count = 0
    original_query: Any = None

    def on_event(event: dict) -> None:
        nonlocal call_count
        if event.get("type") == "tool_result":
            call_count += 1
            if call_count >= 1:
                agent._stop_event.set()

    agent = _build_agent(
        [
            _action_step("echo step1"),
            _action_step("echo step2"),
            _action_step("echo step3"),
            _exit_step(),
        ],
        event_callback=on_event,
    )

    result = agent.run("do the task")
    assert result["exit_status"] == "UserStopped"
    assert agent.n_calls == 1


def test_event_callback_fires_for_assistant_and_tool_result() -> None:
    events: list[dict] = []

    def on_event(event: dict) -> None:
        events.append(event)

    agent = _build_agent(
        [_action_step("ls"), _exit_step()],
        event_callback=on_event,
    )
    agent.run("do it")

    event_types = [e["type"] for e in events]
    assert "lifecycle" in event_types
    assert "assistant" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types

    assistant_idx = next(i for i, e in enumerate(events) if e["type"] == "assistant")
    tool_call_idx = next(i for i, e in enumerate(events) if e["type"] == "tool_call")
    tool_result_idx = next(i for i, e in enumerate(events) if e["type"] == "tool_result")

    assert assistant_idx < tool_call_idx < tool_result_idx


def test_event_callback_not_called_when_none() -> None:
    agent = _build_agent([_exit_step()])
    result = agent.run("do it")
    assert result["exit_status"] == "Submitted"


def test_step_limit_respected() -> None:
    events: list[dict] = []
    agent = _build_agent(
        [_action_step(f"echo step{i}") for i in range(20)],
        event_callback=lambda e: events.append(e),
        step_limit=3,
    )
    result = agent.run("do it")
    assert result["exit_status"] == "LimitsExceeded"
    assert agent.n_calls <= 3
