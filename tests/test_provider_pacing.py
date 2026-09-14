"""Provider retry pacing inside the Mini-SWE retry loop.

A rate-limited attempt must be paced before it propagates to the tenacity
retry: the server-declared ``Retry-After`` when present, else a bounded
jittered delay so a 20-way cohort cannot retry on synchronized schedules.
Each paced attempt is journaled and diagnosed as consequential evidence; the
terminal classification still belongs to the outer query wrapper. Non-retryable
failures are never paced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import gt_engine.miniswe_runtime as rt
from gt_engine import provider_pacing as pacing
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
    def __init__(self, message="", status_code=None, code="", headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.headers = headers


class RateLimitError(_ScriptedFailure):
    pass


class APITimeoutError(_ScriptedFailure):
    pass


class BillingError(_ScriptedFailure):
    pass


class ContextWindowExceededError(_ScriptedFailure):
    pass


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


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
            self.env.execute(action) for action in (message.get("extra") or {}).get("actions") or ()
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
        task_id="pacing",
        repo_root=str(tmp_path),
        state_dir=tmp_path / "state",
        predicates=[],
        issue_text="task",
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
    return agent.model.query([{"role": "user", "content": "go"}])


@pytest.fixture
def recorded_sleep(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing, "_sleep", calls.append)
    return calls


# --- Retry-After parsing -------------------------------------------------


def test_retry_after_numeric_seconds():
    exc = RateLimitError("rate limited", status_code=429)
    exc.response = _FakeResponse({"retry-after": "17"})
    assert pacing.retry_after_seconds(exc) == 17.0


def test_retry_after_http_date():
    exc = RateLimitError("rate limited", status_code=429)
    when = (datetime.now(UTC) + timedelta(seconds=42)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    exc.response = _FakeResponse({"Retry-After": when})
    declared = pacing.retry_after_seconds(exc)
    assert declared is not None
    assert 0.0 <= declared <= 43.0


def test_retry_after_absent_or_malformed():
    exc = RateLimitError("rate limited", status_code=429)
    assert pacing.retry_after_seconds(exc) is None
    exc.response = _FakeResponse({"retry-after": "not-a-time"})
    assert pacing.retry_after_seconds(exc) is None


# --- Policy decision ------------------------------------------------------


def test_decision_retryable_draws_jitter(monkeypatch):
    monkeypatch.setattr(pacing, "_uniform", lambda lo, hi: 12.5)
    policy = pacing.PacingPolicy(jitter_max_seconds=45, retry_after_cap_seconds=120)
    decision = policy.decide(RateLimitError("rate limit", status_code=429))
    assert decision.retryable is True
    assert decision.code is DiagnosticCode.GT_PROVIDER_RATE_LIMIT
    assert decision.delay_seconds == 12.5
    assert decision.basis == "jitter"


def test_decision_retry_after_capped():
    exc = RateLimitError("rate limited", status_code=429)
    exc.response = _FakeResponse({"retry-after": "300"})
    policy = pacing.PacingPolicy(jitter_max_seconds=45, retry_after_cap_seconds=120)
    decision = policy.decide(exc)
    assert decision.retryable is True
    assert decision.delay_seconds == 120.0
    assert decision.basis == "retry_after"


@pytest.mark.parametrize(
    "exc",
    [
        BillingError("insufficient balance", status_code=402),
        _ScriptedFailure("invalid request", status_code=400),
        ContextWindowExceededError("context window exceeded"),
        _ScriptedFailure("resource exhausted"),
        _ScriptedFailure("???"),
    ],
    ids=["billing", "bad_request", "context_window", "resource", "malformed"],
)
def test_decision_non_retryable_is_never_paced(exc):
    policy = pacing.PacingPolicy(jitter_max_seconds=45, retry_after_cap_seconds=120)
    decision = policy.decide(exc)
    assert decision.retryable is False
    assert decision.delay_seconds == 0.0


def test_decision_jitter_disabled_yields_zero_delay():
    policy = pacing.PacingPolicy(jitter_max_seconds=0, retry_after_cap_seconds=120)
    decision = policy.decide(RateLimitError("rate limit", status_code=429))
    assert decision.retryable is True
    assert decision.delay_seconds == 0.0
    assert decision.basis == "jitter_disabled"


def test_policy_env_resolution_and_invalid_values(monkeypatch):
    monkeypatch.delenv(pacing.JITTER_MAX_ENV, raising=False)
    monkeypatch.delenv(pacing.RETRY_AFTER_CAP_ENV, raising=False)
    policy = pacing.PacingPolicy.from_env()
    assert policy.jitter_max_seconds == pacing.DEFAULT_JITTER_MAX_SECONDS
    monkeypatch.setenv(pacing.JITTER_MAX_ENV, "9.5")
    monkeypatch.setenv(pacing.RETRY_AFTER_CAP_ENV, "30")
    policy = pacing.PacingPolicy.from_env()
    assert policy.jitter_max_seconds == 9.5
    assert policy.retry_after_cap_seconds == 30.0
    monkeypatch.setenv(pacing.JITTER_MAX_ENV, "not-a-number")
    with pytest.raises(ValueError, match="provider_pacing_env_invalid"):
        pacing.PacingPolicy.from_env()


# --- Through the real wrapped transport ----------------------------------


def test_retryable_attempt_is_paced_and_journaled(tmp_path, monkeypatch, recorded_sleep):
    _configure_fixture_provider(monkeypatch)
    monkeypatch.setattr(pacing, "_uniform", lambda lo, hi: 12.5)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(tmp_path, ScriptedModel([RateLimitError("rate limit", status_code=429)]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    assert recorded_sleep == [12.5]
    paced = [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    assert len(paced) == 1
    assert paced[0]["code"] == DiagnosticCode.GT_PROVIDER_RATE_LIMIT.value
    assert paced[0]["basis"] == "jitter"
    assert paced[0]["delay_seconds"] == pytest.approx(12.5)

    events = [e for e in _diagnostic_events(adapter) if e.phase == "provider_retry_pacing"]
    assert len(events) == 1
    assert events[0].code is DiagnosticCode.GT_PROVIDER_RATE_LIMIT
    assert events[0].severity == "WARNING"
    assert events[0].classification == "consequential"


def test_retry_then_success_binds_response(tmp_path, monkeypatch, recorded_sleep):
    """A paced 429 followed by a healthy attempt: the response still binds."""
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(
        tmp_path, ScriptedModel([RateLimitError("rate limit", status_code=429), None])
    )
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)
    message = _drive_query(agent)
    assert message["role"] == "assistant"
    assert recorded_sleep, "paced attempt slept no delay"

    events = _journal(adapter)
    paced_index = next(i for i, e in enumerate(events) if e["event"] == "provider_retry_paced")
    response_index = next(i for i, e in enumerate(events) if e["event"] == "provider_response")
    assert paced_index < response_index


def test_paced_attempt_precedes_terminal_failure_row(tmp_path, monkeypatch, recorded_sleep):
    """Event ordering: the paced attempt is journaled BEFORE the terminal
    provider_failure row the outer wrapper records for the exhausted query."""
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(tmp_path, ScriptedModel([RateLimitError("rate limit", status_code=429)]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    events = _journal(adapter)
    names = [e["event"] for e in events]
    assert names.index("provider_retry_paced") < names.index("provider_failure")

    paced_diag = next(e for e in _diagnostic_events(adapter) if e.phase == "provider_retry_pacing")
    terminal_diag = next(e for e in _diagnostic_events(adapter) if e.phase == "provider_transport")
    assert terminal_diag.classification == "primary"
    assert terminal_diag.retryable is True
    assert paced_diag.event_sequence < terminal_diag.event_sequence


def test_non_retryable_failure_is_never_paced(tmp_path, monkeypatch, recorded_sleep):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(
        tmp_path, ScriptedModel([BillingError("insufficient balance", status_code=402)])
    )
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(BillingError):
        _drive_query(agent)

    assert recorded_sleep == []
    assert not [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    events = [e for e in _diagnostic_events(adapter) if e.subsystem == "provider"]
    assert events[-1].code is DiagnosticCode.GT_PROVIDER_BILLING
    assert events[-1].classification == "primary"


def test_retry_after_header_overrides_jitter(tmp_path, monkeypatch, recorded_sleep):
    _configure_fixture_provider(monkeypatch)
    monkeypatch.setattr(pacing, "_uniform", lambda lo, hi: 12.5)
    exc = RateLimitError("rate limited", status_code=429)
    exc.response = _FakeResponse({"retry-after": "9"})
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(tmp_path, ScriptedModel([exc]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    assert recorded_sleep == [9.0]
    paced = [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    assert paced[0]["basis"] == "retry_after"
    assert paced[0]["delay_seconds"] == pytest.approx(9.0)


def test_jitter_disable_env_still_journals_but_sleeps_not(tmp_path, monkeypatch, recorded_sleep):
    _configure_fixture_provider(monkeypatch)
    monkeypatch.setenv(pacing.JITTER_MAX_ENV, "0")
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(tmp_path, ScriptedModel([RateLimitError("rate limit", status_code=429)]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    assert recorded_sleep == []
    paced = [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    assert len(paced) == 1
    assert paced[0]["basis"] == "jitter_disabled"


def test_twenty_way_pacing_decorrelates(tmp_path, monkeypatch):
    """Twenty simulated cohort jobs must not pace on one synchronized delay:
    injected jitter draws distinct values per attempt."""
    _configure_fixture_provider(monkeypatch)
    draws = iter(3.0 + index * 1.7 for index in range(40))
    monkeypatch.setattr(pacing, "_uniform", lambda lo, hi: next(draws))
    adapter = _adapter(tmp_path)
    agent = ScriptedAgent(
        tmp_path,
        ScriptedModel([RateLimitError("rate limit", status_code=429)] * 20),
    )
    install_runtime_hooks(agent, _session(adapter))

    sleeps: list[float] = []
    monkeypatch.setattr(pacing, "_sleep", sleeps.append)
    for _ in range(20):
        with pytest.raises(RateLimitError):
            _drive_query(agent)

    assert len(sleeps) == 20
    assert len(set(sleeps)) == 20, "cohort pacing produced synchronized delays"
    paced = [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    assert len(paced) == 20


def test_pacing_bookkeeping_fault_never_disables_session(tmp_path, monkeypatch):
    """A broken diagnostics sink must not let pacing silence the terminal
    provider_failure row: pacing takes the non-disabling fault channel."""
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    session = _session(adapter)
    exc = RateLimitError("rate limit", status_code=429)
    agent = ScriptedAgent(tmp_path, ScriptedModel([exc]))
    install_runtime_hooks(agent, session)

    def broken_record(event):
        raise RuntimeError("disk full")

    monkeypatch.setattr(adapter.diagnostics, "record", broken_record)

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    # The terminal receipt path legitimately degrades the observer when its
    # own record fails; the contract is that PACING never causes that
    # disable and never eats the terminal provider_failure journal row.
    if session.disabled:
        assert session.disabled_stage == "provider_failure_receipt"
    events = _journal(adapter)
    assert any(e["event"] == "provider_retry_paced" for e in events)
    assert any(e["event"] == "provider_failure" for e in events)


def test_paced_row_redacts_secret_shaped_error(tmp_path, monkeypatch, recorded_sleep):
    _configure_fixture_provider(monkeypatch)
    adapter = _adapter(tmp_path)
    exc = RateLimitError(
        "rate limit exceeded for key sk-live9x_secret_token_value",
        status_code=429,
    )
    agent = ScriptedAgent(tmp_path, ScriptedModel([exc]))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(RateLimitError):
        _drive_query(agent)

    paced = [e for e in _journal(adapter) if e["event"] == "provider_retry_paced"]
    assert len(paced) == 1
    assert "sk-live9x" not in json.dumps(paced[0])
    assert "[redacted]" in paced[0]["error"]
