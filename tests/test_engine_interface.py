"""ENGINE interface contract + correct-time delivery gates.

The interface must deliver each fact bound to its action, in the same
observation, in order, before the next model call — and never before the first
action (no prediction). A pure pass-through renders the raw alone; a
fact-bearing observation leads with the fact. These gates run the real
`engine_execute_actions` seam with fakes so the contract is tested, not just
the pure functions.
"""
from __future__ import annotations

import json
import os

import pytest

from groundtruth.runtime.gateway import GatewayState
from gt_engine.engine.contracts import Decision
from gt_engine.engine.runner import engine_execute_actions
from gt_engine.gt_session import GTMode


class FakeEnv:
    def __init__(self):
        self.ran = []

    def execute(self, action):
        cmd = str(action.get("command") or action.get("cmd") or "")
        self.ran.append(cmd)
        if "pytest" in cmd:
            return {"output": "tests/test_a.py:4: in test_x\n"
                              "    app_function()\nsrc/app.py:12: in app_function\n"
                              "    assert x == 1\nE   AssertionError\n1 failed",
                    "returncode": 1}
        if "x.py" in cmd:
            return {"output": "X_CONTENT\n", "returncode": 0}
        if "y.py" in cmd:
            return {"output": "Y_CONTENT\n", "returncode": 0}
        return {"output": "ok", "returncode": 0}


class FakeStore:
    def __init__(self):
        self.events = []

    def append(self, event, **payload):
        self.events.append({"event": event, **payload})


class FakeAdapter:
    repository_revision = "rev-1"
    graph_db = None
    graph_fresh = True
    global_action = 0
    contract = None
    iteration = 0
    blocking_reasons = ("active failure",)
    _dedup_chain = set()
    _latest_delivery = None
    _engine_search_history = {}
    _delivered_evidence_types = set()
    _engine_failure_history = {}

    def __init__(self, repo_root="."):
        self.repo_root = repo_root
        self.store = FakeStore()

    def gateway_state(self):
        return GatewayState(repo_root=self.repo_root)

    def evaluate_observation(self, *a, **k):
        return None

    def evaluate_failing_observation(self, *a, **k):
        return None

    def blocking_obligation_texts(self):
        return tuple(self.blocking_reasons)

    def next_contract_delta(self, max_chars=1200):
        return ""


class FakeSession:
    mode = GTMode.ENGINE
    disabled = False
    model_visible = True

    def can_enforce(self):
        return True

    def capability_active(self, name):
        return True

    def capability_model_visible(self, name):
        return True

    def allows_live_probes(self):
        return False

    def degrade(self, stage, error):
        self.disabled = True


class FakeModel:
    def __init__(self):
        self.observations = []

    def format_observation_messages(self, message, outputs, template_vars):
        formatted = []
        for out in outputs:
            formatted.append({"role": "tool", "content": str(out.get("output") or "")})
        self.observations = formatted
        return formatted


class FakeAgent:
    def __init__(self):
        self.sent = []

    def get_template_vars(self):
        return {}

    def add_messages(self, *messages):
        self.sent = list(messages)
        return self.sent


def _run(actions, session=None):
    import tempfile

    adapter = FakeAdapter(repo_root=tempfile.mkdtemp())
    model = FakeModel()
    agent = FakeAgent()
    env = FakeEnv()
    engine_execute_actions(
        agent,
        {"extra": {"actions": actions}},
        session=session or FakeSession(),
        adapter=adapter,
        model=model,
        environment=env,
        original_execute=None,
    )
    return agent, model, adapter, env


def test_one_observation_per_action_in_order():
    os.environ.setdefault("GT_GATEWAY", "1")
    agent, model, adapter, env = _run([
        {"command": "cat src/x.py", "tool_call_id": "c1"},
        {"command": "ls", "tool_call_id": "c2"},
        {"command": "cat src/y.py", "tool_call_id": "c3"},
    ])
    assert len(model.observations) == 3
    # one tool observation per action, in order, bound to that action
    assert "X_CONTENT" in model.observations[0]["content"]
    assert "ok" in model.observations[1]["content"]  # ls
    assert "Y_CONTENT" in model.observations[2]["content"]
    # no wrapper on pure pass-through reads (minimal interface, raw is the answer)
    assert "<result" not in model.observations[0]["content"]


def test_pure_read_has_no_gt_bytes_before_first_action():
    agent, model, adapter, env = _run([
        {"command": "cat src/x.py", "tool_call_id": "c1"},
    ])
    # no prediction: nothing before the action; the observation is the raw
    assert len(model.observations) == 1
    assert "X_CONTENT" in model.observations[0]["content"]
    assert "<result" not in model.observations[0]["content"]


def test_test_failure_delivers_covering_in_same_observation():
    os.environ.setdefault("GT_GATEWAY", "1")
    agent, model, adapter, env = _run([
        {"command": "pytest tests/test_a.py", "tool_call_id": "t1"},
    ])
    content = model.observations[0]["content"]
    assert "<result" in content  # fact-bearing observation leads with the fact
    assert 'decision="augment"' in content or 'decision="pass_through"' in content
    # the fact is bound to the action in the same observation
    assert "t1" in content or "call" in content


def test_submit_blocked_produces_suppress_and_refusal(monkeypatch):
    """When the submit gate denies, the engine must emit a SUPPRESS observation
    + a refusal directive (never execute the submit), so the model keeps
    working instead of finishing with unmet obligations/RED."""
    import gt_engine.miniswe_runtime as rt

    def deny_gate(session, command):
        return False

    monkeypatch.setattr(rt, "_run_submit_gate", deny_gate)
    agent, model, adapter, env = _run([
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
         "tool_call_id": "s1"},
    ])
    content = model.observations[0]["content"]
    assert 'decision="suppress"' in content
    # the refusal directive was added (agent.add_messages got a user message)
    assert any(
        isinstance(m, dict) and m.get("role") == "user" and "Submission not executed" in str(m.get("content") or "")
        for m in agent.sent
    )


def test_repeated_empty_search_emits_stop_signal():
    """A repeated empty search must emit the certified STOP signal so the model
    stops wasting calls."""
    os.environ.setdefault("GT_GATEWAY", "1")
    from gt_engine.engine.runner import _stop_signal_fact

    class Adapter:
        repository_revision = "rev-1"

    a = Adapter()
    assert _stop_signal_fact(command="grep -r foo .", raw="", returncode=1,
                             adapter=a) is None  # first run: record only
    stop = _stop_signal_fact(command="grep -r foo .", raw="", returncode=1,
                             adapter=a)
    assert stop is not None
    assert stop.owner == "localization"
    assert "no matches" in stop.content["notice"]
    # a non-search command never emits
    assert _stop_signal_fact(command="cat x", raw="", returncode=0,
                             adapter=Adapter()) is None


def test_submit_command_detected():
    from gt_engine.engine.runner import _is_submit_command

    assert _is_submit_command('echo "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"')
    assert _is_submit_command("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
    assert not _is_submit_command("cat src/x.py")


def test_submitted_signal_propagates_not_degrade():
    """Mini-SWE's end-of-run Submitted must propagate out of the engine (the
    seam re-raises it) instead of being treated as an engine failure."""

    class RaisingEnv:
        def execute(self, action):
            from minisweagent.exceptions import Submitted

            raise Submitted()

    agent = FakeAgent()
    model = FakeModel()
    adapter = FakeAdapter()
    session = FakeSession()
    with pytest.raises(Exception):
        engine_execute_actions(
            agent,
            {"extra": {"actions": [{"command": "echo done", "tool_call_id": "s1"}]}},
            session=session, adapter=adapter, model=model, environment=RaisingEnv(),
            original_execute=None,
        )
    assert not session.disabled  # a real failure, not a degrade


def test_engine_delivery_events_recorded_and_journal_valid(tmp_path):
    from gt_engine.miniswe_integration import ExternalStateStore
    from gt_engine.event_journal import verify_event_journal

    os.environ.setdefault("GT_GATEWAY", "1")
    adapter = FakeAdapter()
    adapter.store = ExternalStateStore(tmp_path, "task-i")
    model = FakeModel()
    agent = FakeAgent()
    env = FakeEnv()
    engine_execute_actions(
        agent,
        {"extra": {"actions": [{"command": "cat src/x.py", "tool_call_id": "c1"}]}},
        session=FakeSession(), adapter=adapter, model=model, environment=env,
        original_execute=None,
    )
    receipt = adapter.store.receipt()
    verification = verify_event_journal(
        adapter.store.path,
        event_count=receipt["event_count"],
        event_head=receipt["event_head"],
    )
    assert verification.valid, verification.issues
    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    deliveries = [e for e in rows if e.get("event") == "engine_delivery"]
    assert len(deliveries) == 1
    assert deliveries[0]["decision"] in {d.value for d in Decision}
