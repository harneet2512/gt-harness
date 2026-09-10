"""The carried snapshot, against everything that can invalidate it.

`capture_workspace` ran twice per action -- 601 times on one measured task at
1.08s each, 652s of a 6,049s run, the largest single piece of GT's own
bookkeeping. So an action's post-image is reused as the next action's
pre-image. The two describe the same tree only while nothing touches the
worktree in between, and a stale pre-image is not a slow observation but a
wrong one: it attributes one action's edit to the next, or loses it, and a lost
edit is a missed epoch bump and evidence that outlives the code it described.

These tests drive the real `execute_actions` and count real captures. Every
case is one thing that can touch the worktree between two actions: a command's
own surviving background writer, a scope the reaper does not own, an incomplete
capture, an automatic check the engine ran, a typed action, and GT's own
post-edit probe.
"""
from __future__ import annotations

import sys

import pytest

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.task_contract import extract_task_contract

COMPLETE = {"capture_complete": True, "descendant_scope": "linux_subreaper",
            "surviving_descendants": []}


class CarryEnv:
    """A shell whose per-action capture metadata the test controls."""

    def __init__(self, repo, steps):
        self.repo = repo
        self.steps = list(steps)
        self.executed: list[str] = []

    def execution_env(self):
        return {"PATH": "/usr/bin"}

    def execute(self, action, **_kwargs):
        command = action.get("command", "")
        self.executed.append(command)
        extra, mutation = self.steps.pop(0) if self.steps else (dict(COMPLETE), None)
        # A real shell writes. Without that the tree never moves and the carry
        # is trivially correct, which is the one case that proves nothing.
        if mutation is not None:
            mutation(self.repo)
        return {"output": "ok", "returncode": 0, "extra": dict(extra)}


class CarryModel:
    model_name = "fixture/model"
    model_kwargs: dict = {}
    tools: list = []

    def _prepare_messages_for_api(self, messages):
        return list(messages)

    def _query(self, messages, **_kwargs):
        return {"id": "response", "model": self.model_name, "usage": {}}

    def query(self, messages, **kwargs):
        return {"role": "assistant", "content": "ok",
                "extra": {"actions": [], "response": self._query(messages, **kwargs)}}


class CarryAgent:
    def __init__(self, env):
        self.model = CarryModel()
        self.env = env
        self.messages: list[dict] = []

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{"role": "tool", "content": str(out.get("output")),
                 "tool_call_id": f"call-{index}"}
                for index, out in enumerate(outputs)]


def _edit_widget(repo):
    (repo / "widget.py").write_text("def widget():\n    return 2\n", encoding="utf-8")


def _journal(adapter):
    import json

    return [json.loads(line)
            for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]


def _edits_for(adapter, action_index):
    return [row for row in _journal(adapter)
            if row.get("event") == "edit_transaction"
            and row.get("action_index") == action_index]


def _drive(tmp_path, monkeypatch, steps, *, mode=GTMode.ADVISORY, between=None,
           commands=("write widget", "echo two")):
    """Run two actions and report how many real captures happened."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "widget.py").write_text("def widget(): return 1\n", encoding="utf-8")

    adapter = MiniSweAdapter(task_id="carry", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=[],
                             contract=extract_task_contract("The widget must keep its shape."))
    env = CarryEnv(repo, steps)
    agent = CarryAgent(env)
    session = GTSession(
        GTSessionConfig(task_id=adapter.task_id, repo_root=str(repo),
                        state_dir=str(adapter.store.root.parent), mode=mode),
        engine=adapter,
    )
    captures: list[str] = []
    real = rt.capture_workspace

    def counted(root, **kwargs):
        captures.append(str(root))
        return real(root, **kwargs)

    monkeypatch.setattr(rt, "capture_workspace", counted)
    install_runtime_hooks(agent, session)

    agent.execute_actions({"extra": {"actions": [{"command": commands[0]}]}})
    first = len(captures)
    if between is not None:
        between(adapter, repo)
    agent.execute_actions({"extra": {"actions": [{"command": commands[1]}]}})
    return adapter, first, len(captures) - first


def test_a_carried_post_image_still_attributes_the_edit_to_its_own_action(tmp_path, monkeypatch):
    """The whole point, and the thing that must not break while achieving it.

    The second action captures once instead of twice, and the first action's
    write is charged to the first action -- not lost, and not attributed to the
    command that came after it.
    """
    adapter, first, second = _drive(
        tmp_path, monkeypatch, [(dict(COMPLETE), _edit_widget), (dict(COMPLETE), None)]
    )
    assert first == 2
    assert second == 1
    assert any("widget.py" in row["changed_paths"] for row in _edits_for(adapter, 1))
    assert all(not row["changed_paths"] for row in _edits_for(adapter, 2))


@pytest.mark.parametrize("extra", [
    {"capture_complete": True, "descendant_scope": "linux_subreaper",
     "surviving_descendants": [4321]},
    {"capture_complete": True, "descendant_scope": "unknown", "surviving_descendants": []},
    {"capture_complete": False, "descendant_scope": "linux_subreaper",
     "surviving_descendants": []},
])
def test_a_command_that_may_still_be_writing_is_never_carried(tmp_path, monkeypatch, extra):
    """A writer we did not reap, a scope we do not own, a capture we did not finish.

    All three mean the post-image may already be wrong, so the next action pays
    for a real capture rather than inheriting a description of a tree that has
    moved underneath it.
    """
    adapter, first, second = _drive(
        tmp_path, monkeypatch, [(extra, _edit_widget), (dict(COMPLETE), None)]
    )
    assert first == 2
    assert second == 2
    assert all(not row["changed_paths"] for row in _edits_for(adapter, 2))


def test_an_automatic_check_between_actions_invalidates_the_carry(tmp_path, monkeypatch):
    """The engine ran the repository's own command; the tree may have moved."""
    def ran_a_check(adapter, _repo):
        adapter._automatic_check_generation = (
            getattr(adapter, "_automatic_check_generation", 0) + 1
        )

    _adapter, first, second = _drive(
        tmp_path, monkeypatch, [(dict(COMPLETE), _edit_widget), (dict(COMPLETE), None)],
        between=ran_a_check)
    assert first == 2
    assert second == 2


def test_a_background_writer_seen_by_an_automatic_check_invalidates_the_carry(
    tmp_path, monkeypatch
):
    def saw_a_writer(adapter, _repo):
        adapter._background_writers_seen = True

    _adapter, first, second = _drive(
        tmp_path, monkeypatch, [(dict(COMPLETE), _edit_widget), (dict(COMPLETE), None)],
        between=saw_a_writer)
    assert first == 2
    assert second == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="Linux process-tree and capture boundary required")
def test_gt_s_own_post_edit_probe_invalidates_the_carry(tmp_path, monkeypatch):
    """GT runs `py_compile` against the worktree AFTER taking the post-image.

    The probe writes `__pycache__` beside the source it compiles. That write
    lands after the carry was taken, so the next action inherits a description
    of the tree as it stood before GT's own subprocess touched it, and the
    resulting diff charges GT's bytecode to the agent's next command -- a
    phantom edit, a spurious epoch bump, and evidence invalidated for nothing.
    The module's own comment says the carry is dropped wherever GT may run a
    subprocess against the worktree. This is one of those places.
    """
    monkeypatch.setenv("GT_ALLOW_LIVE_PROBES", "1")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    probes: list[tuple[str, ...]] = []

    from gt_engine import miniswe_covering

    original = miniswe_covering.run_syntax_probe

    def probing(adapter, changed_files):
        probes.append(tuple(changed_files))
        return original(adapter, changed_files)

    monkeypatch.setattr(miniswe_covering, "run_syntax_probe", probing)

    adapter, first, second = _drive(
        tmp_path, monkeypatch,
        [(dict(COMPLETE), _edit_widget), (dict(COMPLETE), None)],
        mode=GTMode.ASSISTIVE,
    )
    assert probes, "the fixture did not reach GT's own live probe"
    written = list((tmp_path / "repo").rglob("__pycache__/*.pyc"))
    assert written, "the probe did not write into the worktree"
    assert first == 2
    assert second == 2, "the carry survived GT's own subprocess against the worktree"
    charged = [path for row in _edits_for(adapter, 2) for path in row["changed_paths"]]
    assert not charged, f"GT's own bytecode was charged to the agent: {charged}"


def test_a_typed_query_between_actions_does_not_force_a_recapture(tmp_path, monkeypatch):
    """A read-only query is not a reason to pay 1.08s again.

    Typed actions never reach the shell and the only subprocess in that path is
    `git rev-parse HEAD`, which writes nothing. Dropping the carry for them
    would charge every query the cost this carry exists to avoid, so the
    negative witness matters as much as the positive ones above.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widget.py").write_text("def widget(): return 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="typed-carry", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=[],
                             contract=extract_task_contract("The widget must keep its shape."))
    env = CarryEnv(repo, [(dict(COMPLETE), _edit_widget), (dict(COMPLETE), None)])
    agent = CarryAgent(env)
    session = GTSession(
        GTSessionConfig(task_id=adapter.task_id, repo_root=str(repo),
                        state_dir=str(adapter.store.root.parent), mode=GTMode.ADVISORY),
        engine=adapter,
    )
    captures: list[str] = []
    real = rt.capture_workspace
    monkeypatch.setattr(rt, "capture_workspace",
                        lambda root, **kw: (captures.append(str(root)), real(root, **kw))[1])
    install_runtime_hooks(agent, session)

    agent.execute_actions({"extra": {"actions": [{"command": "write widget"}]}})
    first = len(captures)
    agent.execute_actions({"extra": {"actions": [{
        "tool_name": "groundtruth",
        "gt_action": {"kind": "exact_literal_search",
                      "arguments": {"literal": "widget", "paths": ["widget.py"]}},
    }]}})
    typed = len(captures) - first
    agent.execute_actions({"extra": {"actions": [{"command": "echo two"}]}})
    assert first == 2
    assert typed == 0, "a read-only typed query captured the workspace"
    assert len(captures) - first - typed == 1, "the carry did not survive a typed query"
    assert env.executed == ["write widget", "echo two"]
