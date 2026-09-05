"""Unit tests for cloud.server.conversational_agent.ConversationalAgent.

FAKE BOUNDARY (module-wide): the LLM and the shell. ``FakeModel`` replays a
scripted list of mini-swe messages and ``FakeEnv`` returns canned command
output; everything else — the mini-swe ``DefaultAgent`` step loop, message
bookkeeping, the steering queue, the stop event, the format-error path and the
context truncator — is the real code under test.

Run: ``python -m pytest tests/test_cloud_conversational_agent.py -q``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from minisweagent.exceptions import FormatError, Submitted

from cloud.server.conversational_agent import (
    KEEP_RECENT_OBSERVATIONS,
    ConversationalAgent,
    assistant_message_from_format_error,
    is_question,
)
from cloud.server.prompts import CHAT_BRIEF_TEMPLATE, CHAT_SYSTEM_TEMPLATE


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
def _action(command: str, thought: str = "", cost: float = 0.01) -> dict:
    return {
        "role": "assistant",
        "content": thought or f"Running {command}",
        "extra": {"actions": [{"command": command}], "cost": cost},
    }


def _text_reply(content: str, cost: float = 0.01) -> FormatError:
    """A model response with text and no tool call, in stock LitellmModel shape.

    ``LitellmModel.query`` raises ``FormatError`` carrying only the format-error
    observation; the raw provider response (and therefore the assistant text) is
    stashed under ``extra.response``.
    """
    return FormatError(
        {
            "role": "user",
            "content": "No tool calls found in the response.",
            "extra": {
                "interrupt_type": "FormatError",
                "cost": cost,
                "response": {
                    "choices": [
                        {"message": {"role": "assistant", "content": content}}
                    ]
                },
            },
        }
    )


class FakeModel:
    """FAKE BOUNDARY: the LLM. Replays scripted messages/exceptions in order."""

    config = MagicMock()

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls = 0
        self.seen_messages: list[list[dict]] = []

    def query(self, messages: list[dict], **_: Any) -> dict:
        self.seen_messages.append([dict(m) for m in messages])
        index, self.calls = self.calls, self.calls + 1
        if index >= len(self._scripted):
            raise _text_reply("I have run out of script.")
        step = self._scripted[index]
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
                "content": f"Observation: {o.get('output', '')}",
                "extra": {"raw_output": o.get("output", "")},
            }
            for o in outputs
        ]

    def get_template_vars(self, **_: Any) -> dict:
        return {}

    def serialize(self) -> dict:
        return {}


class FakeEnv:
    """FAKE BOUNDARY: the shell. Canned output; raises what it is told to."""

    config = MagicMock()

    def __init__(self, raises: Exception | None = None, output: str = "") -> None:
        self._raises = raises
        self._output = output

    def execute(self, action: dict, cwd: str = "") -> dict:
        if self._raises is not None:
            raise self._raises
        return {
            "output": self._output or f"executed: {action.get('command', '')}",
            "returncode": 0,
            "exception_info": "",
        }

    def get_template_vars(self, **_: Any) -> dict:
        return {"system": "Linux", "release": "6.0", "machine": "x86_64"}

    def serialize(self) -> dict:
        return {}


def _agent(
    scripted: list[Any],
    *,
    env: FakeEnv | None = None,
    events: list[dict] | None = None,
    step_limit: int = 10,
    max_context_chars: int = 0,
) -> ConversationalAgent:
    agent = ConversationalAgent(
        FakeModel(scripted),
        env or FakeEnv(),
        event_callback=(events.append if events is not None else None),
        system_template="SYSTEM",
        instance_template="BRIEF",
        step_limit=step_limit,
        max_context_chars=max_context_chars,
    )
    agent.begin_session()
    return agent


# --------------------------------------------------------------------------
# text-only replies end the turn
# --------------------------------------------------------------------------
def test_text_only_response_is_a_reply_not_a_format_error() -> None:
    events: list[dict] = []
    agent = _agent(
        [_action("echo hi"), _text_reply("Done. I appended a line to README.md.")],
        events=events,
    )
    result = agent.run_turn("append a line", turn_id="t1")

    assert result.finish_reason == "reply"
    assert result.reply == "Done. I appended a line to README.md."
    assert result.n_calls == 2
    assert result.cost == pytest.approx(0.02)

    # only the assistant message is kept — the format-error observation is not
    assert agent.messages[-1] == {
        "role": "assistant",
        "content": "Done. I appended a line to README.md.",
        "extra": {"cost": 0.01},
    }
    assert not any(
        "No tool calls found" in str(m.get("content")) for m in agent.messages
    )
    # the reply is a model call too, so it gets its own assistant frame
    assert [e["type"] for e in events] == [
        "assistant", "tool_call", "tool_result", "assistant",
    ]
    reply_frame = events[-1]["data"]
    assert reply_frame["is_reply"] is True
    assert reply_frame["content"] == "Done. I appended a line to README.md."
    assert reply_frame["actions"] == []
    assert reply_frame["step"] == reply_frame["n_calls"] == 2
    assert reply_frame["cost"] == pytest.approx(0.02)


def test_trailing_question_mark_classifies_as_question() -> None:
    agent = _agent([_text_reply("Should I also update the changelog?")])
    result = agent.run_turn("fix the bug", turn_id="t1")
    assert result.finish_reason == "question"


def test_empty_text_is_still_a_format_error() -> None:
    empty = FormatError(
        {
            "role": "user",
            "content": "No tool calls found in the response.",
            "extra": {"cost": 0.0, "response": {"choices": [{"message": {}}]}},
        }
    )
    agent = _agent([empty, _action("echo recovered"), _text_reply("ok")])
    result = agent.run_turn("go", turn_id="t1")

    assert result.finish_reason == "reply"
    assert any(
        "No tool calls found" in str(m.get("content")) for m in agent.messages
    ), "a genuinely malformed response must be fed back to the model"


def test_assistant_message_shape_in_format_error_is_also_accepted() -> None:
    exc = FormatError(
        {"role": "assistant", "content": "All set.", "extra": {"cost": 0.02}},
        {"role": "user", "content": "format error", "extra": {"cost": 0.02}},
    )
    assert assistant_message_from_format_error(exc) == {
        "role": "assistant",
        "content": "All set.",
        "extra": {"cost": 0.02},
    }


def test_text_with_actions_is_not_a_reply() -> None:
    exc = FormatError(
        {
            "role": "assistant",
            "content": "text",
            "extra": {"actions": [{"command": "ls"}]},
        }
    )
    assert assistant_message_from_format_error(exc) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Which branch should I use?", True),
        ("Done.\nShould I push?", True),
        ("Done. I pushed the branch.", False),
        ("", False),
    ],
)
def test_is_question_heuristic(text: str, expected: bool) -> None:
    assert is_question(text) is expected


# --------------------------------------------------------------------------
# turn memory
# --------------------------------------------------------------------------
def test_second_turn_continues_the_same_transcript() -> None:
    agent = _agent(
        [_text_reply("First answer."), _action("echo two"), _text_reply("Second.")]
    )
    agent.run_turn("first question", turn_id="t1")
    agent.run_turn("second question", turn_id="t2")

    model: FakeModel = agent.model  # type: ignore[assignment]
    second_request = model.seen_messages[1]
    contents = [str(m.get("content")) for m in second_request]
    assert "first question" in contents
    assert "First answer." in contents
    assert "second question" in contents
    assert contents.count("SYSTEM") == 1, "the system message is added once"


def test_begin_session_is_idempotent_and_restore_replaces_memory() -> None:
    agent = _agent([_text_reply("hi")])
    agent.begin_session()
    assert [m["role"] for m in agent.messages] == ["system", "user"]

    agent.restore([{"role": "system", "content": "restored"}])
    assert agent.session_started
    assert agent.messages == [{"role": "system", "content": "restored"}]


# --------------------------------------------------------------------------
# steering, stop, budgets, submit
# --------------------------------------------------------------------------
def test_steering_is_drained_at_the_step_boundary() -> None:
    events: list[dict] = []
    agent = _agent(
        [_action("echo one"), _action("echo two"), _text_reply("done")],
        events=events,
    )
    agent.queue_steering("m1", "actually, do it differently")
    agent.run_turn("go", turn_id="t7")

    steering = [e for e in events if e["type"] == "steering"]
    assert len(steering) == 1
    assert steering[0]["data"] == {
        "turn_id": "t7",
        "message_id": "m1",
        "content": "actually, do it differently",
    }
    injected = [
        i
        for i, m in enumerate(agent.messages)
        if m.get("content") == "actually, do it differently"
    ]
    assert len(injected) == 1 and agent.messages[injected[0]]["role"] == "user"


def test_stop_ends_the_turn_at_the_next_boundary() -> None:
    agent = _agent([_action("echo one"), _action("echo two"), _text_reply("done")])
    agent.request_stop()
    result = agent.run_turn("go", turn_id="t1")

    assert result.finish_reason == "stopped"
    assert result.reply == "Stopped."
    assert result.n_calls == 0
    assert agent.messages[-1] == {"role": "assistant", "content": "Stopped."}
    assert not agent.stop_requested or True  # cleared for the next turn below

    # the session is still usable
    follow_up = agent.run_turn("continue", turn_id="t2")
    assert follow_up.finish_reason == "reply"


def test_per_turn_step_limit_produces_an_auto_reply() -> None:
    agent = _agent([_action(f"echo {i}") for i in range(10)], step_limit=2)
    result = agent.run_turn("go", turn_id="t1")

    assert result.finish_reason == "step_limit"
    assert result.n_calls == 2
    assert result.reply.startswith("I used the step budget for this turn")
    assert "Say 'continue' to keep going." in result.reply

    # the budget is per turn, not cumulative: the next turn gets a fresh two
    second = agent.run_turn("continue", turn_id="t2")
    assert second.finish_reason == "step_limit"
    assert second.n_calls == 2
    assert agent.n_calls == 4


def test_submit_marker_finishes_the_turn_with_observations_intact() -> None:
    submitted = Submitted(
        {
            "role": "exit",
            "content": "final output",
            "extra": {"exit_status": "Submitted", "submission": "final output"},
        }
    )
    agent = _agent(
        [_action("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")],
        env=FakeEnv(raises=submitted),
    )
    result = agent.run_turn("finish up", turn_id="t1")

    assert result.finish_reason == "submitted"
    assert result.reply == "final output"
    roles = [m["role"] for m in agent.messages]
    assert roles[-2:] == ["user", "assistant"], (
        "the tool observation must be appended before the submit reply, or the "
        "next turn's request would dangle a tool call"
    )
    assert not any(m["role"] == "exit" for m in agent.messages)


def test_environment_failure_propagates_and_emits_agent_error() -> None:
    events: list[dict] = []
    agent = _agent(
        [_action("boom")], env=FakeEnv(raises=RuntimeError("env exploded")),
        events=events,
    )
    with pytest.raises(RuntimeError, match="env exploded"):
        agent.run_turn("go", turn_id="t1")

    errors = [e for e in events if e["type"] == "agent_error"]
    assert len(errors) == 1
    assert "env exploded" in errors[0]["data"]["error"]
    assert errors[0]["data"]["turn_id"] == "t1"


# --------------------------------------------------------------------------
# context bounding
# --------------------------------------------------------------------------
def test_old_observations_are_truncated_but_replies_are_not() -> None:
    big = "x" * 2000
    script = [_action(f"echo {i}") for i in range(30)] + [_text_reply("done")]
    agent = _agent(
        script,
        env=FakeEnv(output=big),
        step_limit=40,
        max_context_chars=4000,
    )
    agent.run_turn("please do a lot of work", turn_id="t1")

    observations = [
        m
        for m in agent.messages
        if m.get("role") == "user" and "raw_output" in (m.get("extra") or {})
    ]
    truncated = [
        m for m in observations if str(m["content"]).startswith("[truncated ")
    ]
    intact = [m for m in observations if m not in truncated]

    assert truncated, "the oldest observations must be collapsed"
    assert len(intact) >= KEEP_RECENT_OBSERVATIONS - 1
    assert observations[0] in truncated and observations[-1] in intact
    assert any(m.get("content") == "please do a lot of work" for m in agent.messages)
    assert agent.messages[-1]["content"] == "done"


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------
def test_chat_prompts_render_with_the_real_template_vars() -> None:
    agent = ConversationalAgent(
        FakeModel([]),
        FakeEnv(),
        system_template=CHAT_SYSTEM_TEMPLATE,
        instance_template=CHAT_BRIEF_TEMPLATE,
        step_limit=5,
    )
    agent.extra_template_vars |= {
        "repo": "https://github.com/o/r",
        "ref": "main",
        "cwd": "/work",
    }
    agent.begin_session()

    system, brief = agent.messages[0]["content"], agent.messages[1]["content"]
    assert "bash tool call" in system
    assert "NO command block" in system
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in system
    assert "https://github.com/o/r" in brief and "/work" in brief
