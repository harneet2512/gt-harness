"""Scripted provider-failure rehearsal through the real wrapped query.

The online failure modes — rate-limit mid-run, transport timeout, disconnect,
billing refusal, malformed response — must each produce the typed journal row
and the correctly-classified diagnostic while the exception still propagates
to the caller that owns retry policy. A stub model driven through
``install_runtime_hooks`` exercises exactly the production wrapper.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.run_diagnostics import DiagnosticCode


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


class _ScriptedFailure(Exception):
    """A carrier exception shaped like the provider SDK's error classes."""

    def __init__(self, message="", status_code=None, code=""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RateLimitError(_ScriptedFailure):
    pass


class APITimeoutError(_ScriptedFailure):
    pass


class APIConnectionError(_ScriptedFailure):
    pass


class BadRequestError(_ScriptedFailure):
    pass


class ScriptedModel:
    """_query raises whatever the script holds; None means succeed."""

    model_name = "fixture/model"
    model_kwargs: dict = {}
    tools: list = []

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def _query(self, messages, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, BaseException):
            raise item
        return {"id": "r", "model": self.model_name, "usage": {"total_tokens": 5}}

    def query(self, messages, **kwargs):
        prepared = self._prepare_messages_for_api(messages)
        response = self._query(prepared, **kwargs)
        return {
            "role": "assistant",
            "content": "ok",
            "extra": {"actions": [], "response": response},
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        return []


class ScriptedEnv:
    def __init__(self, root: Path):
        self.root = root

    def execute(self, action):
        return {"output": "ok", "returncode": 0}


class ScriptedAgent:
    def __init__(self, root: Path, model):
        self.model = model
        self.env = ScriptedEnv(root)
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


def _adapter(tmp_path: Path) -> MiniSweAdapter:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("value = 1\n", encoding="utf-8")
    return MiniSweAdapter(
        task_id="rehearsal", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )


def _journal(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _diagnostic_events(adapter) -> list:
    return list(adapter.diagnostics._events)


def _drive_query(agent):
    """Invoke the wrapped provider query exactly as the agent loop does."""
    return agent.model.query([{"role": "user", "content": "go"}])


_FAILURE_SCENARIOS = [
    (
        "rate_limit_429",
        RateLimitError("rate limit exceeded", status_code=429),
        DiagnosticCode.GT_PROVIDER_RATE_LIMIT,
        True,
    ),
    (
        "transport_timeout",
        APITimeoutError("request timed out"),
        DiagnosticCode.GT_PROVIDER_TIMEOUT,
        True,
    ),
    (
        "mid_stream_disconnect",
        APIConnectionError("connection reset by peer"),
        DiagnosticCode.GT_PROVIDER_DISCONNECT,
        True,
    ),
    (
        "billing_refusal",
        _ScriptedFailure("insufficient credits", status_code=402),
        DiagnosticCode.GT_PROVIDER_BILLING,
        False,
    ),
    (
        "bad_request",
        BadRequestError("invalid request: bad param", status_code=400),
        DiagnosticCode.GT_PROVIDER_BAD_REQUEST,
        False,
    ),
    (
        "unclassifiable",
        _ScriptedFailure("???"),
        DiagnosticCode.GT_PROVIDER_MALFORMED_RESPONSE,
        False,
    ),
]


@pytest.mark.parametrize(
    "scenario,exc,want_code,want_retryable",
    _FAILURE_SCENARIOS,
    ids=[s[0] for s in _FAILURE_SCENARIOS],
)
def test_scripted_provider_failure_binds_typed_receipt(
    tmp_path, monkeypatch, scenario, exc, want_code, want_retryable
):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(tmp_path, ScriptedModel([exc]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(type(exc)):
        _drive_query(agent)

    # The typed journal row exists with the real error type.
    failures = [e for e in _journal(adapter) if e["event"] == "provider_failure"]
    assert failures, f"{scenario}: no provider_failure journal row"
    assert failures[-1]["error_type"] == type(exc).__name__

    # The diagnostic carries the right code and retryability.
    events = [
        e for e in _diagnostic_events(adapter) if e.subsystem == "provider"
    ]
    assert events, f"{scenario}: no provider diagnostic recorded"
    assert events[-1].code is want_code, (
        f"{scenario}: {events[-1].code} != {want_code}"
    )
    assert events[-1].retryable is want_retryable


def test_provider_recovers_and_binds_response_after_failure(tmp_path, monkeypatch):
    """A transient failure followed by a healthy response must leave both
    rows: the failure receipt AND the bound response — never a silent gap."""
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(
        tmp_path,
        ScriptedModel([APITimeoutError("timed out"), None]),
    )
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(APITimeoutError):
        _drive_query(agent)
    message = _drive_query(agent)
    assert message["role"] == "assistant"

    events = _journal(adapter)
    assert any(e["event"] == "provider_failure" for e in events)
    assert any(
        e["event"] in ("provider_response", "provider_call") for e in events
    ), "recovery produced no provider response binding"


def test_a_failing_query_does_not_count_as_agent_work(tmp_path, monkeypatch):
    """A provider failure is transport truth — it must not feed the churn
    governor's command stream or the verify-failure streak."""
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(
        tmp_path, ScriptedModel([RateLimitError("rl", status_code=429)])
    )
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    governor = adapter.churn_governor
    assert governor.turns_observed == 0
    assert governor.verify_fail_streak == 0
