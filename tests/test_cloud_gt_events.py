"""Unit tests for cloud.server.gt_events — GT typed actions as stream frames.

FAKE BOUNDARY (module-wide): the LLM, the shell and **the typed-action
executor**. ``FakeTypedExecutor`` stands in for
``gt_engine.miniswe_typed_actions.execute_typed_action_fail_open``, which needs
the vendored ``groundtruth`` wheel (present only in the server image). Its
return value is the real contract — the ``(request, result)`` tuple whose
``result["output"]`` is a canonical ``gt.compiled_observation.v1`` document —
so everything the module under test parses is genuine payload shape.

``FakeGtRuntime`` is the other stand-in: it replays what
``gt_engine.miniswe_runtime.install_runtime_hooks`` installs over
``agent.execute_actions`` — dispatch typed actions through the executor without
ever touching ``env.execute``, then hand ``(message, outputs)`` to
``model.format_observation_messages``. Everything else — the wrappers, the
event builder, the scope normaliser, the per-turn tallies and the mini-swe step
loop — is the real code under test.

Run: ``python -m pytest tests/test_cloud_gt_events.py -q``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cloud.server.gt_events import (
    GT_ACTION_EVENT,
    MAX_ARGUMENT_CHARS,
    MAX_OMISSIONS,
    build_gt_action_event,
    gt_action_events,
    install_gt_action_events,
    is_typed_action,
    match_count,
    producer_scope,
)
from cloud.server.typed_scopes import normalize_typed_action

from .test_cloud_conversational_agent import FakeEnv, _agent, _text_reply


# --------------------------------------------------------------------------
# payload builders — the real gt.compiled_observation.v1 shape
# --------------------------------------------------------------------------
def _observation(
    *,
    semantics: str = "exact",
    coverage: str = "complete",
    scope: list[str] | None = None,
    matches: list[dict] | None = None,
    omissions: list[str] | None = None,
    reason_codes: list[str] | None = None,
    action_id: str = "call_1",
) -> str:
    rows = matches if matches is not None else []
    answer = {"scope": scope if scope is not None else [], "matches": rows}
    return json.dumps(
        {
            "schema": "gt.compiled_observation.v1",
            "action_request": {"schema": "gt.action_request.v1"},
            "evidence": {
                "schema": "gt.evidence_artifact.v1",
                "action_id": action_id,
                "answer": answer,
                "producer": "groundtruth.deterministic_queries.v1",
                "semantics": semantics,
                "coverage": coverage,
                "omissions": omissions or [],
            },
            "direct_answer": rows,
            "decision": {
                "schema": "gt.interception_decision.v1",
                "mode": "REPLACE",
                "reason_codes": reason_codes or ["EXACT_COMPLETE_EQUIVALENCE"],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _match(path: str, line: int, preview: str) -> dict:
    return {"path": path, "line": line, "preview": preview}


def _result(output: str, returncode: int = 0) -> dict:
    return {
        "output": output,
        "returncode": returncode,
        "exception_info": "" if returncode == 0 else "typed evidence incomplete",
        "extra": {
            "gt_typed_action": True,
            "action_request_sha256": "a" * 64,
            "compiled_observation_sha256": "b" * 64,
            "interception_decision": "REPLACE",
        },
    }


def _typed_action(
    kind: str = "exact_literal_search",
    arguments: dict | None = None,
    tool_call_id: str = "call_1",
) -> dict:
    return {
        "tool_name": "groundtruth",
        "tool_call_id": tool_call_id,
        "gt_action": {
            "kind": kind,
            "arguments": arguments
            if arguments is not None
            else {"literal": "class Command", "paths": ["src/click"]},
        },
    }


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class FakeTypedExecutor:
    """FAKE BOUNDARY: ``execute_typed_action_fail_open``.

    The real one lives behind the vendored ``groundtruth`` wheel. This replays
    scripted ``result`` mappings in the same ``(request, result)`` contract.
    """

    def __init__(self, results: list[dict]) -> None:
        self._results = list(results)
        self.seen: list[dict] = []

    def __call__(self, action: dict, **_: Any) -> tuple[None, dict]:
        self.seen.append(action)
        index = len(self.seen) - 1
        if index >= len(self._results):
            return None, _result(_observation(semantics="incomplete"), returncode=2)
        return None, self._results[index]


class FakeGtRuntime:
    """FAKE BOUNDARY: GT's ``execute_actions`` replacement.

    Mirrors ``gt_engine/miniswe_runtime.py`` lines 573-956 in the only two
    respects this module depends on: a typed action is dispatched through the
    executor and **never** reaches ``env.execute``, and the whole batch is
    handed to ``model.format_observation_messages`` before the next model call.
    """

    def __init__(self, agent: Any, executor: FakeTypedExecutor) -> None:
        self._agent = agent
        self._executor = executor

    def install(self) -> None:
        agent, executor = self._agent, self._executor

        def execute_actions(message: dict) -> list[dict]:
            actions = (message.get("extra") or {}).get("actions") or []
            outputs: list[dict] = []
            for action in actions:
                if is_typed_action(action):
                    _request, result = executor(action)
                    outputs.append(result)
                    continue
                outputs.append(agent.env.execute(action))
            formatted = agent.model.format_observation_messages(
                message, outputs, agent.get_template_vars()
            )
            return agent.add_messages(*formatted)

        agent.execute_actions = execute_actions


def _gt_agent(
    scripted: list[Any], results: list[dict], events: list[dict]
) -> tuple[Any, FakeTypedExecutor]:
    agent = _agent(scripted, env=FakeEnv(), events=events)
    executor = FakeTypedExecutor(results)
    FakeGtRuntime(agent, executor).install()
    assert install_gt_action_events(agent) is True
    return agent, executor


def _assistant_with(actions: list[dict], thought: str = "Looking it up") -> dict:
    return {
        "role": "assistant",
        "content": thought,
        "extra": {"actions": actions, "cost": 0.01},
    }


def _events_of(events: list[dict], kind: str) -> list[dict]:
    return [e["data"] for e in events if e["type"] == kind]


# --------------------------------------------------------------------------
# the event payload
# --------------------------------------------------------------------------
def test_event_carries_the_typed_action_and_its_verdict() -> None:
    output = _observation(
        scope=["src/click"],
        matches=[
            _match("src/click/core.py", 959, "class Command:"),
            _match("src/click/core.py", 2119, "class CommandCollection(Group):"),
        ],
    )
    event = build_gt_action_event(
        _typed_action(), _result(output), step=3, duration_ms=12.5
    )

    assert event == {
        "step": 3,
        "kind": "exact_literal_search",
        "arguments": {"literal": "class Command", "paths": ["src/click"]},
        "scope": ["src/click"],
        "returncode": 0,
        "semantics": "exact",
        "coverage": "complete",
        "match_count": 2,
        "omissions": [],
        "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
        "duration_ms": 12.5,
        "evidence_artifact_id": "call_1",
    }


def test_abstention_is_reported_as_an_abstention() -> None:
    output = _observation(
        semantics="incomplete",
        coverage="partial",
        scope=[],
        matches=[],
        omissions=["missing_scope:src/click/**"],
        reason_codes=[
            "SEMANTICS_NOT_EXACT",
            "COVERAGE_NOT_COMPLETE",
            "EVIDENCE_HAS_OMISSIONS",
        ],
    )
    event = build_gt_action_event(
        _typed_action(arguments={"literal": "class Command", "paths": ["src/click/**"]}),
        _result(output, returncode=2),
        step=1,
        duration_ms=4.0,
    )

    assert event["returncode"] == 2
    assert event["semantics"] == "incomplete"
    assert event["coverage"] == "partial"
    assert event["match_count"] == 0
    assert event["omissions"] == ["missing_scope:src/click/**"]
    assert event["reason_codes"][0] == "SEMANTICS_NOT_EXACT"


def test_enum_valued_semantics_and_coverage_are_flattened() -> None:
    output = json.dumps(
        {
            "evidence": {
                "semantics": "EvidenceSemantics.EXACT",
                "coverage": "Coverage.COMPLETE",
                "answer": {"scope": ["src"], "matches": [_match("a.py", 1, "x")]},
            },
            "direct_answer": [_match("a.py", 1, "x")],
            "decision": {"reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"]},
        }
    )
    event = build_gt_action_event(
        _typed_action(), _result(output), step=0, duration_ms=0.0
    )

    assert event["semantics"] == "exact"
    assert event["coverage"] == "complete"


def test_dict_coverage_is_not_mistaken_for_a_verdict() -> None:
    """The compatibility producer puts ``{"scope": [...]}`` under ``coverage``."""
    output = json.dumps(
        {
            "evidence": {
                "semantics": "exact",
                "coverage": {"scope": ["src/click"]},
                "answer": [_match("a.py", 1, "x")],
                "omissions": [],
            },
            "direct_answer": [_match("a.py", 1, "x")],
            "decision": {"reason_codes": ["typed_exact_complete"]},
        }
    )
    event = build_gt_action_event(
        _typed_action(), _result(output), step=0, duration_ms=0.0
    )

    assert event["coverage"] == ""
    assert event["scope"] == ["src/click"]
    assert event["match_count"] == 1


def test_arguments_are_truncated_but_not_dropped() -> None:
    literal = "x" * 500
    event = build_gt_action_event(
        _typed_action(arguments={"literal": literal, "paths": ["src", "y" * 400]}),
        _result(_observation()),
        step=0,
        duration_ms=0.0,
    )

    assert event["arguments"]["literal"] == "x" * MAX_ARGUMENT_CHARS
    assert event["arguments"]["paths"] == ["src", "y" * MAX_ARGUMENT_CHARS]


def test_omissions_are_capped() -> None:
    output = _observation(
        semantics="incomplete",
        omissions=[f"missing_scope:p{n}" for n in range(25)],
    )
    event = build_gt_action_event(
        _typed_action(), _result(output, returncode=2), step=0, duration_ms=0.0
    )

    assert len(event["omissions"]) == MAX_OMISSIONS


def test_unparseable_output_still_produces_a_frame() -> None:
    """A router defect must not cost the UI its record that GT was asked."""
    event = build_gt_action_event(
        _typed_action(),
        {"output": "not json", "returncode": 2, "extra": {}},
        step=2,
        duration_ms=1.0,
    )

    assert event["kind"] == "exact_literal_search"
    assert event["returncode"] == 2
    assert event["semantics"] == ""
    assert event["match_count"] == 0
    assert "evidence_artifact_id" not in event


def test_match_count_reads_every_answer_shape() -> None:
    assert match_count([1, 2, 3]) == 3
    assert match_count({"matches": [1, 2]}) == 2
    assert match_count({"results": [1]}) == 1
    assert match_count({"edge_id": "e1"}) == 1
    assert match_count(None) == 0
    assert match_count("one") == 1


def test_producer_scope_never_echoes_the_request_back() -> None:
    assert producer_scope({"answer": {"scope": ["src/click"]}}, None) == ["src/click"]
    assert producer_scope({"coverage": {"scope": "src"}}, None) == ["src"]
    assert producer_scope({}, {"scope": ["a", "b"]}) == ["a", "b"]
    # nothing echoed: silence, not the paths that were asked for
    assert producer_scope({"answer": None}, None) == []


# --------------------------------------------------------------------------
# the scope-normalised case (HAR-85)
# --------------------------------------------------------------------------
def test_normalized_glob_scope_shows_the_directory_the_producer_searched(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "click").mkdir(parents=True)
    requested = _typed_action(
        arguments={"literal": "class Command", "paths": ["src/click/**"]}
    )
    normalized = normalize_typed_action(requested, tmp_path)
    assert normalized["gt_action"]["arguments"]["paths"] == ["src/click"]

    event = build_gt_action_event(
        normalized,
        _result(
            _observation(
                scope=["src/click"],
                matches=[_match("src/click/core.py", 959, "class Command:")],
            )
        ),
        step=1,
        duration_ms=8.0,
    )

    assert event["arguments"]["paths"] == ["src/click"]
    assert event["scope"] == ["src/click"]
    assert event["semantics"] == "exact"
    assert event["match_count"] == 1


# --------------------------------------------------------------------------
# ordering and installation
# --------------------------------------------------------------------------
def test_gt_action_lands_after_its_assistant_frame_and_before_the_next() -> None:
    events: list[dict] = []
    agent, executor = _gt_agent(
        [
            _assistant_with([_typed_action()]),
            _text_reply("src/click/core.py defines Command."),
        ],
        [
            _result(
                _observation(
                    scope=["src/click"],
                    matches=[_match("src/click/core.py", 959, "class Command:")],
                )
            )
        ],
        events,
    )
    result = agent.run_turn("where is Command?", turn_id="t1")

    types = [e["type"] for e in events]
    assert types.count(GT_ACTION_EVENT) == 1
    index = types.index(GT_ACTION_EVENT)
    # after the assistant frame that requested it …
    assert types[:index].count("assistant") == 1
    # … and before the next model call's frame
    assert "assistant" in types[index + 1 :]
    # the typed action is part of the same model call: no extra assistant frame
    assert types.count("assistant") == result.n_calls == 2
    # and it never reached the shell
    assert not _events_of(events, "tool_call")
    assert len(executor.seen) == 1


def test_frame_reports_the_step_of_the_call_that_asked_for_it() -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [
            _assistant_with([{"command": "ls"}]),
            _assistant_with([_typed_action()]),
            _text_reply("done"),
        ],
        [_result(_observation(scope=["src"], matches=[_match("a.py", 1, "x")]))],
        events,
    )
    agent.run_turn("look", turn_id="t1")

    frame = _events_of(events, GT_ACTION_EVENT)[0]
    assert frame["step"] == 2
    assert frame["turn_id"] == "t1"
    assert frame["duration_ms"] >= 0.0


def test_every_typed_action_in_one_batch_gets_its_own_frame() -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [
            _assistant_with(
                [
                    _typed_action(tool_call_id="call_1"),
                    {"command": "ls"},
                    _typed_action(
                        kind="find_callers",
                        arguments={"symbol": "invoke"},
                        tool_call_id="call_2",
                    ),
                ]
            ),
            _text_reply("done"),
        ],
        [
            _result(_observation(scope=["src"], matches=[_match("a.py", 1, "x")])),
            _result(_observation(action_id="call_2", scope=["src"], matches=[])),
        ],
        events,
    )
    agent.run_turn("look", turn_id="t1")

    frames = _events_of(events, GT_ACTION_EVENT)
    assert [f["kind"] for f in frames] == ["exact_literal_search", "find_callers"]
    # the bash action in the middle still produced its own shell frames
    assert len(_events_of(events, "tool_call")) == 1


def test_turn_tallies_count_only_exact_answers() -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [
            _assistant_with([_typed_action(tool_call_id="call_1")]),
            _assistant_with([_typed_action(tool_call_id="call_2")]),
            _assistant_with([_typed_action(tool_call_id="call_3")]),
            _text_reply("done"),
        ],
        [
            # exact with matches -> an answer
            _result(_observation(scope=["src"], matches=[_match("a.py", 1, "x")])),
            # exact but empty -> a GT action, not an answer
            _result(_observation(scope=["src"], matches=[])),
            # an abstention
            _result(
                _observation(semantics="incomplete", omissions=["missing_scope:q"]),
                returncode=2,
            ),
        ],
        events,
    )
    result = agent.run_turn("look", turn_id="t1")

    assert len(_events_of(events, GT_ACTION_EVENT)) == 3
    assert result.gt_actions == 3
    assert result.gt_exact_matches == 1


def test_tallies_reset_between_turns() -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [
            _assistant_with([_typed_action()]),
            _text_reply("one"),
            _text_reply("two"),
        ],
        [_result(_observation(scope=["src"], matches=[_match("a.py", 1, "x")]))],
        events,
    )
    first = agent.run_turn("look", turn_id="t1")
    second = agent.run_turn("thanks", turn_id="t2")

    assert (first.gt_actions, first.gt_exact_matches) == (1, 1)
    assert (second.gt_actions, second.gt_exact_matches) == (0, 0)


def test_a_bash_only_turn_emits_no_gt_frames() -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [_assistant_with([{"command": "ls"}]), _text_reply("done")], [], events
    )
    result = agent.run_turn("list", turn_id="t1")

    assert not _events_of(events, GT_ACTION_EVENT)
    assert result.gt_actions == 0


def test_installation_is_idempotent() -> None:
    agent = _agent([_text_reply("hi")])
    assert install_gt_action_events(agent) is True
    wrapped = agent.execute_actions
    assert install_gt_action_events(agent) is True
    assert agent.execute_actions is wrapped


def test_installation_declines_a_model_without_a_formatter() -> None:
    """GT returns raw outputs then; there is no seam to hang the frames on."""

    class Bare:
        def execute_actions(self, message: dict) -> list[dict]:
            return []

    bare = Bare()
    bare.model = object()
    assert install_gt_action_events(bare) is False


def test_a_broken_emitter_never_breaks_the_turn(monkeypatch: Any) -> None:
    events: list[dict] = []
    agent, _ = _gt_agent(
        [_assistant_with([_typed_action()]), _text_reply("done")],
        [_result(_observation(scope=["src"], matches=[_match("a.py", 1, "x")]))],
        events,
    )
    monkeypatch.setattr(
        agent,
        "note_gt_action",
        lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = agent.run_turn("look", turn_id="t1")

    assert result.finish_reason == "reply"
    assert not _events_of(events, GT_ACTION_EVENT)


def test_gt_action_events_skips_a_batch_cut_short() -> None:
    message = _assistant_with([_typed_action(), _typed_action(tool_call_id="call_2")])
    frames = gt_action_events(
        message, [_result(_observation())], step=1, duration_ms=1.0
    )

    assert len(frames) == 1


@pytest.mark.parametrize(
    "action",
    [{"command": "ls"}, {"tool_name": "bash", "command": "ls"}, "ls", None],
)
def test_only_groundtruth_tool_calls_are_typed_actions(action: Any) -> None:
    assert is_typed_action(action) is False
