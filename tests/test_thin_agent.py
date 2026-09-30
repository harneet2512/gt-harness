"""GT attached the GitNexus way on the baseline harness (gt_engine.thin_agent)."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import Submitted

from gt_engine.thin_agent import EditProbe, GTAttachedAgent, could_write


@pytest.mark.parametrize("command", [
    "grep -rn foo src/", "cat a.py | head -20", "nl -ba x.py | sed -n '1,40p'", "ls -la && pwd",
    "git diff HEAD", "find . -name '*.py'", "rg foo 2>/dev/null", "gt-context Foo", "cd /app && cat x.py",
])
def test_read_only_commands_are_not_probed(command):
    assert could_write(command) is False


@pytest.mark.parametrize("command", [
    "sed -i 's/a/b/' x.py", "cat > x.py <<'EOF'\nx\nEOF", "python fix.py", "echo x >> y.txt",
    "find . -name '*.pyc' -delete", "git apply p.diff", "pytest -q", "tee out.txt < in", "npm install",
    "sed --in-place=.bak 's/a/b/' x.py", "find . -name x -okdir rm {} ;", "find . -fls out.txt",
])
def test_anything_that_may_write_is_probed(command):
    assert could_write(command) is True


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return tmp_path


def test_edit_probe_reports_before_and_after_in_a_git_checkout(tmp_path):
    root = _git_repo(tmp_path)
    probe = EditProbe(root)
    assert probe.git
    probe.before()
    (root / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (root / "b.py").write_text("x = 1\n", encoding="utf-8")
    changes = probe.after()
    assert changes["a.py"] == ("def f():\n    return 1\n", "def f():\n    return 2\n")
    assert changes["b.py"] == (None, "x = 1\n")
    probe.before()  # a second edit of an already-dirty file is still seen, against its last text
    (root / "a.py").write_text("def f():\n    return 3\n", encoding="utf-8")
    assert probe.after() == {"a.py": ("def f():\n    return 2\n", "def f():\n    return 3\n")}


def test_edit_probe_works_without_git(tmp_path):
    (tmp_path / "sim.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    probe = EditProbe(tmp_path)
    assert not probe.git
    probe.before()
    time.sleep(0.01)
    (tmp_path / "sim.c").write_text("int main(){return 1;}\n", encoding="utf-8")
    assert probe.after() == {"sim.c": ("int main(){return 0;}\n", "int main(){return 1;}\n")}


class _Model:
    """Scripted model: each query returns the next list of commands."""

    def __init__(self, turns):
        self.turns = list(turns)

    def query(self, messages):
        commands = self.turns.pop(0)
        return {"role": "assistant", "content": "t",
                "extra": {"actions": [{"command": c} for c in commands], "cost": 0.0}}

    def format_message(self, **kwargs):
        return dict(kwargs)

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{"role": "user", "content": o["output"]} for o in outputs]

    def get_template_vars(self, **kwargs):
        return {}

    def serialize(self):
        return {}


class _Env:
    def __init__(self, root):
        self.config = SimpleNamespace(cwd=str(root), env={})
        self.ran = []

    def execute(self, action, cwd="", *, timeout=None):
        command = action["command"]
        self.ran.append(command)
        if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in command:
            raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted",
                                                                     "submission": ""}})
        return {"output": f"out:{command}", "returncode": 0, "exception_info": ""}

    def get_template_vars(self, **kwargs):
        return {}

    def serialize(self):
        return {}


class _Delivery:
    def __init__(self, block="", review="", delay=0.0):
        self.block, self.review, self.delay, self.calls = block, review, delay, 0

    def observe_turn(self, commands, outputs, facts):
        self.calls += 1
        time.sleep(self.delay)
        if not self.block:
            return outputs
        return [{**o, "output": f"{o['output']}\n\n{self.block}"} for o in outputs]

    def submit_review_once(self):
        review, self.review = self.review, ""
        return review

    def metrics(self):
        return {"gt_delivery_mode": "attached"}


def _agent(cls, root, turns, **extra):
    kwargs = dict(config_class=AgentConfig, system_template="sys", instance_template="{{task}}",
                  step_limit=10)
    return cls(_Model(turns), _Env(root), **kwargs, **extra)


def _adapter(root):
    return SimpleNamespace(repo_root=str(root), engine_state=SimpleNamespace(graph_path=""),
                           store=SimpleNamespace(append=lambda *a, **k: None),
                           note_edit=lambda paths: None, phase="", begin_implement=lambda: None)


def test_with_nothing_to_add_the_trajectory_is_the_default_agent_s(tmp_path):
    turns = [["ls"], ["grep -rn f ."], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]]
    stock = _agent(DefaultAgent, tmp_path, turns)
    stock.run("task")
    thin = _agent(GTAttachedAgent, tmp_path, turns, delivery=_Delivery(), adapter=_adapter(tmp_path))
    thin.run("task")
    assert thin.messages == stock.messages
    assert (thin.serialize()["info"]["config"]["agent"]
            == stock.serialize()["info"]["config"]["agent"])


def test_gt_blocks_are_appended_to_the_agent_s_own_observation(tmp_path):
    agent = _agent(GTAttachedAgent, tmp_path, [["grep -rn f ."],
                                                 ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=_Delivery(block="[GT] f is called by g"), adapter=_adapter(tmp_path))
    agent.run("task")
    observation = next(m for m in agent.messages if m.get("content", "").startswith("out:grep"))
    assert observation["content"] == "out:grep -rn f .\n\n[GT] f is called by g"


def test_the_first_submit_is_answered_with_the_review_and_the_second_submits(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    agent = _agent(GTAttachedAgent, tmp_path, [[submit], [submit]],
                   delivery=_Delivery(review="[GT] before you submit: 1. x"), adapter=_adapter(tmp_path))
    agent.run("task")
    assert agent.env.ran == [submit]  # the held submit never executed
    assert any(m.get("content") == "[GT] before you submit: 1. x" for m in agent.messages)
    assert agent.messages[-1]["extra"]["exit_status"] == "Submitted"


def test_a_slow_augmenter_cannot_hold_the_step(tmp_path, monkeypatch):
    import gt_engine.thin_agent as thin

    monkeypatch.setattr(thin, "AUGMENT_TIMEOUT_SECONDS", 0.2)
    delivery = _Delivery(block="[GT] late", delay=1.0)
    agent = _agent(GTAttachedAgent, tmp_path, [["ls"], ["ls"], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=delivery, adapter=_adapter(tmp_path))
    started = time.perf_counter()
    agent.run("task")
    assert time.perf_counter() - started < 1.0
    assert not any("[GT] late" in str(m.get("content")) for m in agent.messages)
    assert agent.gt_stats["augment_timeouts"] == 1 and agent.gt_stats["augment_skipped_busy"] >= 1


def test_edits_are_noted_and_passed_to_the_augmenter(tmp_path):
    root = _git_repo(tmp_path)
    noted, seen = [], []

    class Recorder(_Delivery):
        def observe_turn(self, commands, outputs, facts):
            seen.extend(facts)
            return outputs

    class WritingEnv(_Env):
        def execute(self, action, cwd="", *, timeout=None):
            if action["command"].startswith("sed -i"):
                (root / "a.py").write_text("def f():\n    return 9\n", encoding="utf-8")
            return super().execute(action, cwd, timeout=timeout)

    adapter = _adapter(root)
    adapter.note_edit = lambda paths: noted.append(tuple(paths))
    agent = GTAttachedAgent(_Model([["sed -i 's/1/9/' a.py"], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]]),
                            WritingEnv(root), delivery=Recorder(), adapter=adapter,
                            config_class=AgentConfig, system_template="s", instance_template="{{task}}")
    agent.run("task")
    assert noted == [("a.py",)]
    assert seen[0]["changes"] == {"a.py": ("def f():\n    return 1\n", "def f():\n    return 9\n")}
    assert seen[0]["syntax"] and seen[0]["syntax"][0]["valid"] is True


def test_the_gt_off_runner_path_is_the_baseline_s(monkeypatch, tmp_path):
    from minisweagent.models.litellm_model import LitellmModel

    from scripts import miniswe_thin_run as runner

    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    agent, adapter, session = runner.build_agent(
        task="t", model="stealth/space-bunny-alpha", cwd=str(tmp_path), state_dir=str(tmp_path / "s"),
        output=None, temperature=1.0, gt_off=True, step_limit=100)
    assert type(agent) is DefaultAgent and adapter is None and session is None
    assert isinstance(agent.model, LitellmModel) and type(agent.model) is LitellmModel
    assert agent.model.config.model_kwargs == {"temperature": 1.0, "api_base": "https://openrouter.ai/api/v1"}
    assert agent.config.wall_time_limit_seconds == 0 and agent.config.step_limit == 100
    assert type(agent.env).__name__ == "CredentialIsolatedLocalEnvironment"


def test_a_file_dirty_before_the_task_starts_is_not_reported_as_the_agent_s_edit(tmp_path):
    root = _git_repo(tmp_path)
    setup, edited = "def f():\n    return 'setup'\n", "def f():\n    return 'agent'\n"
    (root / "a.py").write_text(setup, encoding="utf-8")  # benchmark setup diff
    probe = EditProbe(root)
    probe.before()
    (root / "a.py").write_text(edited, encoding="utf-8")
    assert probe.after() == {"a.py": (setup, edited)}


def test_actions_after_a_held_submit_in_the_same_turn_do_not_run(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    agent = _agent(GTAttachedAgent, tmp_path, [[submit, "rm -rf src"], [submit]],
                   delivery=_Delivery(review="[GT] before you submit: 1. x"), adapter=_adapter(tmp_path))
    agent.run("task")
    assert agent.env.ran == [submit]
    assert any("not run: it followed the submit" in str(m.get("content")) for m in agent.messages)


def test_the_observation_does_not_wait_for_the_post_turn_refresh(tmp_path, monkeypatch):
    import gt_engine.thin_agent as thin

    monkeypatch.setattr(thin, "AUGMENT_TIMEOUT_SECONDS", 0.5)
    refreshed = []
    adapter = _adapter(tmp_path)
    adapter.graph_fresh = False
    agent = _agent(GTAttachedAgent, tmp_path, [["grep -rn f ."], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=_Delivery(block="[GT] now"), adapter=adapter)

    def slow_refresh():
        time.sleep(1.0)
        refreshed.append(True)

    agent._refresh_after_turn = slow_refresh
    agent.run("task")
    assert any("[GT] now" in str(m.get("content")) for m in agent.messages)  # delivered, not timed out
    assert agent.gt_stats["augment_timeouts"] == 0
    agent.gt_refresher.join(2)
    assert refreshed == [True]


def test_a_turn_during_a_running_amend_still_gets_its_gt_block(tmp_path, monkeypatch):
    import gt_engine.thin_agent as thin

    monkeypatch.setattr(thin, "AUGMENT_TIMEOUT_SECONDS", 0.5)
    adapter = _adapter(tmp_path)
    adapter.graph_fresh = False
    agent = _agent(GTAttachedAgent, tmp_path,
                   [["grep -rn a ."], ["grep -rn b ."], ["grep -rn c ."], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=_Delivery(block="[GT] x"), adapter=adapter)
    agent._refresh_after_turn = lambda: time.sleep(1.5)  # one long amend spans the next turns
    agent.run("task")
    blocks = [m for m in agent.messages if "[GT] x" in str(m.get("content"))]
    assert len(blocks) == 3 and agent.gt_stats["augment_skipped_busy"] == 0


def test_a_passive_read_never_amends_inline_in_the_thin_arm():
    from types import SimpleNamespace

    from gt_engine.tool_server import refresh_if_stale

    calls = []
    adapter = SimpleNamespace(graph_fresh=False, passive_refresh_inline=False, _edit_epoch=1,
                              _last_graph_build_ms=10, engine_state=SimpleNamespace(graph_path="g"),
                              store=SimpleNamespace(append=lambda *a, **k: calls.append(a[0])),
                              refresh_graph=lambda **k: calls.append("amend"))
    session = SimpleNamespace(_engine=adapter, capability_active=lambda name: True)
    refresh_if_stale(session, passive=True)
    assert "amend" not in calls and "passive_refresh_deferred" in calls
    refresh_if_stale(session)  # an explicit gt-* call still pays for currency
    assert "amend" in calls


def test_a_slow_index_build_never_delays_the_agent_s_start(tmp_path, monkeypatch):
    """boa (run 36510165558): a 2,286 s synchronous build ran inside the agent's budget."""
    from types import SimpleNamespace

    import gt_engine.indexer as indexer
    from gt_engine.thin_agent import build_attached_session

    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def slow_build(*args, **kwargs):
        time.sleep(3)
        return SimpleNamespace(success=False, status=SimpleNamespace(value="build_failed"),
                               error_type="slow", error_diagnostic="", graph_db=None)

    monkeypatch.setattr(indexer, "ensure_index_with_receipt", slow_build)
    started = time.perf_counter()
    adapter, _session, delivery, _layout = build_attached_session(
        task="fix f", cwd=str(tmp_path / "."), state_dir=str(tmp_path.parent / f"{tmp_path.name}-state"),
        task_id="t1", wait_seconds=0.2)
    assert time.perf_counter() - started < 2.5
    events = (adapter.store.path).read_text(encoding="utf-8")
    assert "gt_thin_index_background" in events and "gt_thin_index_ready" not in events
    delivery.stop()



def test_each_post_turn_refresh_leaves_a_receipt(tmp_path, monkeypatch):
    """gt-index runs at nice 19 beside the agent's builds (PR #50): a starved
    amend must show in receipts as a long refresh, with whether nice applied."""
    import gt_engine.tool_server as tool_server

    events = []
    adapter = _adapter(tmp_path)
    adapter.graph_fresh = False
    adapter.store = SimpleNamespace(append=lambda event, **row: events.append((event, row)))
    delivery = _Delivery()
    delivery.session = SimpleNamespace()
    agent = _agent(GTAttachedAgent, tmp_path, [["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=delivery, adapter=adapter)

    def amend(_session, **_kw):
        time.sleep(0.15)  # above REFRESH_RECEIPT_MIN_SECONDS: real indexing work
        adapter.graph_fresh = True

    monkeypatch.setattr(tool_server, "refresh_if_stale", amend)
    agent._refresh_after_turn()

    receipts = [row for event, row in events if event == "gt_thin_refresh"]
    assert len(receipts) == 1
    assert receipts[0]["seconds"] >= 0.15 and receipts[0]["graph_fresh"] is True
    assert isinstance(receipts[0]["low_priority"], bool)
    assert agent.gt_stats["refresh_max_seconds"] >= 0.15


def test_low_priority_active_reports_the_env_switch(monkeypatch):
    from gt_engine import indexer

    monkeypatch.setenv(indexer.INDEX_NICE_ENV, "0")
    assert indexer.low_priority_active() is False


def test_a_no_op_refresh_leaves_no_receipt(tmp_path, monkeypatch):
    """Nothing to index (one FASTA): the refresher returns at once every turn;
    validation run 36665598281 logged 89 such zero-second receipts on one task."""
    import gt_engine.tool_server as tool_server

    events = []
    adapter = _adapter(tmp_path)
    adapter.graph_fresh = False
    adapter.store = SimpleNamespace(append=lambda event, **row: events.append(event))
    delivery = _Delivery()
    delivery.session = SimpleNamespace()
    agent = _agent(GTAttachedAgent, tmp_path, [["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]],
                   delivery=delivery, adapter=adapter)
    monkeypatch.setattr(tool_server, "refresh_if_stale", lambda _session, **_kw: None)

    agent._refresh_after_turn()

    assert "gt_thin_refresh" not in events


def _regression_agent(tmp_path, monkeypatch, found):
    from gt_engine import regression_gate

    events = []
    adapter = _adapter(tmp_path)
    adapter.store = SimpleNamespace(append=lambda event, **row: events.append((event, row)))
    delivery = _Delivery()
    delivery.session = SimpleNamespace()
    delivery.review_baseline = "abc123"
    submit = ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]
    agent = _agent(GTAttachedAgent, tmp_path, [submit, submit], delivery=delivery, adapter=adapter)

    def fake_check(root, baseline, changed, reachable, execute, exists_at_start, **_kw):
        return regression_gate.RegressionResult(regressions=list(found), failing_now=list(found),
                                                candidates=["tests/test_config.py"], seconds=1.5)

    monkeypatch.setattr(regression_gate, "check", fake_check)
    monkeypatch.setattr("gt_engine.submit_review.changed_files_since", lambda root, baseline: ["pkg/config.py"])
    return agent, events


def test_a_regression_holds_the_first_submit_once(tmp_path, monkeypatch):
    agent, events = _regression_agent(tmp_path, monkeypatch, ["tests/test_config.py::test_limit"])
    agent.run("task")
    held = [m for m in agent.messages if "[GT] regression check" in str(m.get("content"))]
    assert len(held) == 1 and "tests/test_config.py::test_limit" in str(held[0]["content"])
    assert agent.gt_stats["regression_held"] == 1
    checks = [row for event, row in events if event == "gt_regression_check"]
    assert len(checks) == 1 and checks[0]["regressions"] == ["tests/test_config.py::test_limit"]


def test_no_regression_means_no_hold(tmp_path, monkeypatch):
    agent, events = _regression_agent(tmp_path, monkeypatch, [])
    agent.run("task")
    assert not any("[GT] regression check" in str(m.get("content")) for m in agent.messages)
    assert agent.gt_stats.get("regression_held", 0) == 0


def test_the_regression_gate_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_REGRESSION_GATE", "0")
    agent, events = _regression_agent(tmp_path, monkeypatch, ["tests/test_config.py::test_limit"])
    agent.run("task")
    assert not any("[GT] regression check" in str(m.get("content")) for m in agent.messages)
    assert not any(event == "gt_regression_check" for event, _ in events)
