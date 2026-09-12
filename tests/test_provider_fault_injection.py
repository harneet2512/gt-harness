"""Provider fault injection at the real query() seam.

Paid run 34715686102 proved this class was only exercised with money on the
line: a FormatError on "aiomonitor-task-snapshots-diff" tripped the secret
canary inside the provider_failure receipt and turned a typed provider
failure into internal_error. Every transport misbehavior must instead land
as a typed diagnostic + provider_failure journal row while the session and
the agent loop stay intact. Discovery of this class is provider-free.
"""
from __future__ import annotations

import json

import pytest

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.provider_limits import (
    ProviderAdmission,
    ProviderContextWindowUnavailable,
    ProviderRequestTooLarge,
)


def _session(adapter, mode=GTMode.ADVISORY):
    return GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=adapter.repo_root,
            state_dir=str(adapter.store.root.parent),
            mode=mode,
        ),
        engine=adapter,
    )


def _configure_fixture_provider(monkeypatch):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100000")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 1)


class FakeEnv:
    def execute(self, action):
        return {"output": "ok", "returncode": 0}


class FakeAgent:
    def __init__(self, model):
        self.model = model
        self.env = FakeEnv()
        self.messages = []

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


class FaultyModel:
    """A mini-swe-shaped model whose transport raises the injected fault."""

    model_name = "fixture/model"
    model_kwargs: dict = {}
    tools: list = []

    def __init__(self, fault: BaseException):
        self.fault = fault

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def _query(self, messages, **kwargs):
        raise self.fault

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


def _events(adapter):
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _http_error(status: int, body: str = "provider error") -> Exception:
    return type("HTTPError", (Exception,), {"status_code": status})(body)


@pytest.mark.parametrize(
    ("fault", "code", "retryable"),
    [
        (type("FormatError", (Exception,), {})(""), "GT_PROVIDER_MALFORMED_RESPONSE", False),
        (_http_error(429), "GT_PROVIDER_RATE_LIMIT", True),
        (_http_error(402), "GT_PROVIDER_BILLING", False),
        (_http_error(400), "GT_PROVIDER_BAD_REQUEST", False),
        (TimeoutError("timed out"), "GT_PROVIDER_TIMEOUT", True),
        (ConnectionError("connection reset"), "GT_PROVIDER_DISCONNECT", True),
        (
            ProviderRequestTooLarge(ProviderAdmission(
                request_tokens=200, request_bytes=400, context_window_tokens=100,
                reserved_output_tokens=10, input_budget_tokens=90,
                metadata_source="fixture",
            )),
            "GT_PROVIDER_REQUEST_TOO_LARGE", False,
        ),
        (
            ProviderContextWindowUnavailable(
                "no window", request_tokens=1, request_bytes=2,
                context_window_tokens=100, reserved_output_tokens=10,
                metadata_source="fixture",
            ),
            "GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE",
            False,
        ),
    ],
)
def test_provider_faults_record_typed_diagnostics_without_degrading(
    tmp_path, monkeypatch, fault, code, retryable
):
    _configure_fixture_provider(monkeypatch)
    adapter = MiniSweAdapter(
        task_id="fault-matrix", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )
    session = _session(adapter)
    agent = FakeAgent(FaultyModel(fault))
    install_runtime_hooks(agent, session)

    with pytest.raises(type(fault)):
        agent.model.query([{"role": "user", "content": "x"}])

    events = _events(adapter)
    failure = [e for e in events if e["event"] == "provider_failure"]
    assert failure, "provider failure was not journaled"
    assert failure[-1]["error_type"] == type(fault).__name__
    assert not any(e["event"] == "gt_degraded_fail_open" for e in events), (
        "a provider fault must not degrade the session via the receipt path"
    )
    assert not session.disabled

    adapter.diagnostics.seal()
    diagnostics = json.loads(
        (adapter.store.root / "diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["diagnostics"][0]["code"] == code
    assert diagnostics["diagnostics"][0]["retryable"] is retryable


def test_key_shaped_task_id_survives_provider_failure_receipt(tmp_path, monkeypatch):
    """Regression for run 34715686102 task 7: the task id
    "aiomonitor-task-snapshots-diff" embeds "sk-snapshots-diff", which the
    secret canary read as a key. The provider_failure receipt raised
    ValueError -> gt_degraded_fail_open -> internal_error exit 5."""
    _configure_fixture_provider(monkeypatch)
    adapter = MiniSweAdapter(
        task_id="aiomonitor-task-snapshots-diff", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )
    session = _session(adapter)
    fault = type("FormatError", (Exception,), {})("")
    agent = FakeAgent(FaultyModel(fault))
    install_runtime_hooks(agent, session)

    with pytest.raises(type(fault)):
        agent.model.query([{"role": "user", "content": "x"}])

    events = _events(adapter)
    assert any(e["event"] == "provider_failure" for e in events)
    assert not any(e["event"] == "gt_degraded_fail_open" for e in events)
    assert not session.disabled
    adapter.diagnostics.seal()
    diagnostics = json.loads(
        (adapter.store.root / "diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["diagnostics"][0]["code"] == "GT_PROVIDER_MALFORMED_RESPONSE"


def test_secret_echoing_provider_error_is_redacted_in_journal(tmp_path, monkeypatch):
    """A provider error body can echo request headers. The journal is
    uploaded evidence: credential-shaped material must be masked there."""
    _configure_fixture_provider(monkeypatch)
    adapter = MiniSweAdapter(
        task_id="redaction", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )
    fault = type(
        "HTTPError", (Exception,), {"status_code": 401}
    )("401 unauthorized: Bearer sk-or-v1-abcdef1234567890 API_KEY=sk-zzz999888777")
    agent = FakeAgent(FaultyModel(fault))
    install_runtime_hooks(agent, _session(adapter))

    with pytest.raises(type(fault)):
        agent.model.query([{"role": "user", "content": "x"}])

    failure = next(e for e in _events(adapter) if e["event"] == "provider_failure")
    assert "sk-or-v1" not in failure["error"]
    assert "sk-zzz" not in failure["error"]
    assert "Bearer" not in failure["error"]
    assert "[redacted]" in failure["error"]


def test_receipt_path_failure_degrades_once_and_stays_fail_open(tmp_path, monkeypatch):
    """If the receipt writer itself fails, GT must degrade exactly once and
    the provider error still propagates - the fault is recorded, not hidden."""
    _configure_fixture_provider(monkeypatch)
    adapter = MiniSweAdapter(
        task_id="degrade-once", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="task",
    )
    session = _session(adapter)
    agent = FakeAgent(FaultyModel(TimeoutError("timed out")))
    install_runtime_hooks(agent, session)

    def broken_record(event):
        raise RuntimeError("disk full")

    monkeypatch.setattr(adapter.diagnostics, "record", broken_record)

    with pytest.raises(TimeoutError):
        agent.model.query([{"role": "user", "content": "x"}])

    events = _events(adapter)
    assert any(e["event"] == "provider_failure" for e in events)
    degraded = [e for e in events if e["event"] == "gt_degraded_fail_open"]
    assert degraded and degraded[-1]["stage"] == "provider_failure_receipt"
    assert session.disabled
