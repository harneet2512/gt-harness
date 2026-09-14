"""Behavioral-program check channel.

The DeepSWE fd task bound 39 acceptance checks that were shell *programs* -
``tmp=$(mktemp -d); cd "$tmp"; touch a.txt; test "$(fd --sort)" = "a.txt"`` -
not test-runner argv. Every one failed CheckSpec admission (shell expansion,
non-runner heads) and sat ``plan_check_binding_pending`` forever; the gate
conceded and the task closed ``submitted_unverified`` while the official
verifier ran those exact programs.

The honest channel binds the program text itself as the spec and replays it
verbatim through the task environment - the same verdict the verifier gets,
bound to repository revision and environment. Exit status is the assertion
contract (``test``, ``diff -q``, ``grep -q``, ``cmp`` all encode it there).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.miniswe_integration import MiniSweAdapter

FD_PROGRAM = (
    'tmp=$(mktemp -d); cd "$tmp"; touch a.txt b.txt; '
    'test "$(fd --sort)" = "a.txt\nb.txt"'
)
DIFF_PROGRAM = 'fd --hidden > got.txt; diff -u expected.txt got.txt'
TEST_PROGRAM = 'test "$(fd --exclude .git)" = "visible.txt"'


def _adapter(tmp_path: Path) -> MiniSweAdapter:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("value = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="fd", repo_root=str(tmp_path),
        state_dir=tmp_path / "state", predicates=[], issue_text="fd task",
    )
    return adapter


def _plan(adapter, programs):
    """Stub the persistent plan with rows carrying shell-program checks."""
    rows = [
        SimpleNamespace(
            row_id=f"row-{index}",
            text=f"fd acceptance check {index}",
            verification_command=program,
        )
        for index, program in enumerate(programs)
    ]
    adapter.persistent_plan = SimpleNamespace(rows=rows)
    # The render layer (inputs.ledger/canonical_json) is not what these tests
    # exercise; the journal rows are the auditable evidence.
    adapter.publish_plan_state = lambda: None
    return adapter.persistent_plan


def _journal(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ProgramEnv:
    """Executes command strings as shell programs; records what ran."""

    def __init__(self, root: Path, returncode: int = 0, output: str = ""):
        self.root = root
        self.returncode = returncode
        self.output = output
        self.ran: list[str] = []

    def execution_env(self):
        return self

    def execute(self, action, cwd=None, timeout=None):
        command = action["command"]
        assert "argv" not in action or not action.get("argv"), (
            "a program check must execute verbatim, not as argv"
        )
        self.ran.append(command)
        return {
            "output": self.output,
            "returncode": self.returncode,
            "extra": {"environment_sha256": "env1", "capture_complete": True},
        }


def test_a_shell_program_row_binds_as_a_program_check(tmp_path):
    adapter = _adapter(tmp_path)
    _plan(adapter, [FD_PROGRAM])
    adapter.bind_initial_plan_checks()

    events = _journal(adapter)
    bound = [e for e in events if e["event"] == "plan_program_check_bound"]
    assert bound, "fd's shell program produced no program-check binding"
    assert bound[0]["program"] == FD_PROGRAM
    assert bound[0]["requirement_ids"] == ["row-0"]
    assert not [
        e for e in events
        if e["event"] == "plan_check_binding_pending"
        and e.get("row_id") == "row-0"
    ], "a bound program check must not stay pending"


def test_drained_program_check_passes_and_proves_its_row(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    adapter = _adapter(tmp_path)
    plan = _plan(adapter, [FD_PROGRAM])
    adapter.bind_initial_plan_checks()
    env = ProgramEnv(tmp_path)

    adapter.drain_plan_checks(env)

    assert env.ran == [FD_PROGRAM], "the drain must replay the program verbatim"
    assert adapter.plan_row_state("row-0") == "CHECK_PASSED"
    observed = [
        e for e in _journal(adapter) if e["event"] == "plan_check_observed"
    ]
    assert observed and observed[0]["binding_basis"] == "program_spec"


def test_drained_program_check_fails_typed(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    adapter = _adapter(tmp_path)
    _plan(adapter, [FD_PROGRAM])
    adapter.bind_initial_plan_checks()

    adapter.drain_plan_checks(ProgramEnv(tmp_path, returncode=1))

    assert adapter.plan_row_state("row-0") == "CHECK_FAILED"


def test_agent_running_the_program_verbatim_discharges_it(tmp_path):
    """The agent executing the bound program itself is the same evidence."""
    from gt_engine.runtime_observation import capture_workspace

    adapter = _adapter(tmp_path)
    _plan(adapter, [TEST_PROGRAM])
    adapter.bind_initial_plan_checks()

    before = capture_workspace(
        adapter.repo_root, excluded_roots=(adapter.store.root,)
    )
    result = {
        "output": "", "returncode": 0,
        "extra": {"environment_sha256": "env1", "capture_complete": True},
    }
    after = capture_workspace(
        adapter.repo_root, excluded_roots=(adapter.store.root,)
    )
    # The runtime records the post-command snapshot; that is what advances
    # adapter.repository_revision to the revision the observation cites.
    adapter.record_repository_snapshot(after, boundary="after_agent_check")
    adapter.observe_plan_checks(
        TEST_PROGRAM, result, before, after, ProgramEnv(tmp_path),
    )

    assert adapter.plan_row_state("row-0") == "CHECK_PASSED"


def test_explicit_program_bind_through_the_cli_channel(tmp_path):
    adapter = _adapter(tmp_path)
    _plan(adapter, [DIFF_PROGRAM])
    check_id = adapter.bind_plan_check(
        {"program": DIFF_PROGRAM, "requirement_ids": ["row-0"]}
    )
    assert check_id
    assert adapter._program_check_specs[check_id].program == DIFF_PROGRAM


def test_a_program_without_an_assertion_verdict_never_binds(tmp_path):
    """``pytest x | tail -1``'s exit status is tail's, not the suite's -
    binding it would manufacture CHECK_PASSED. It stays pending."""
    adapter = _adapter(tmp_path)
    _plan(adapter, [
        "pytest tests/test_a.py | tail -1",   # vacuous: tail owns rc
        "fd --hidden | sort | head -5",       # no assertion at all
        "cd /tmp && cd /var",                 # nothing to execute
    ])
    adapter.bind_initial_plan_checks()

    events = _journal(adapter)
    assert not [e for e in events if e["event"] == "plan_program_check_bound"]
    pending = [e for e in events if e["event"] == "plan_check_binding_pending"]
    assert len(pending) == 3
    assert sum(
        "program_has_no_assertion" in str(e.get("reason")) for e in pending
    ) == 2
