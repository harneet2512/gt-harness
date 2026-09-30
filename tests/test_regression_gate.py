"""Regression gate (gt_engine.regression_gate), end to end on real git repositories and real
pytest: only a repository test that passed at the start commit and fails now holds the submit.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gt_engine import regression_gate
from gt_engine.regression_gate import candidate_test_files, check


def _bash() -> str:
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    candidates = [parent / sub / "bash.exe" for parent in Path(git).parents for sub in ("bin", "usr/bin")] if git else []
    found = next((str(p) for p in candidates if p.is_file()), None)
    if found is None:
        pytest.skip("Git bash not found")
    return found


def _execute(extra_path: str = ""):
    """Run a shell command like the agent's environment: bash, with this interpreter as `python`."""
    python_dir = Path(sys.executable).parent.as_posix()
    if os.name == "nt":
        python_dir = "/" + python_dir[0].lower() + python_dir[2:]  # C:/x -> /c/x for Git bash

    def run(cmd: str) -> tuple[str, int]:
        full = f'export PATH="{python_dir}:$PATH"; ' + cmd
        done = subprocess.run([_bash(), "-c", full], capture_output=True, text=True, timeout=300)
        return done.stdout + done.stderr, done.returncode

    return run


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "config.py").write_text("def limit():\n    return 10\n\ndef name():\n    return 'app'\n", encoding="utf-8")
    (root / "tests" / "test_config.py").write_text(
        "from pkg.config import limit, name\n\n"
        "def test_limit():\n    assert limit() == 10\n\n"
        "def test_name():\n    assert name() == 'app'\n\n"
        "def test_already_broken():\n    assert name() == 'other'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return root, head


def _exists(root: Path, baseline: str):
    return lambda path: subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{baseline}:{path}"],
                                       capture_output=True).returncode == 0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    base = tmp_path / "base-copy"
    monkeypatch.setattr(regression_gate, "BASE_COPY", base.as_posix() if os.name != "nt" else
                        "/" + base.as_posix()[0].lower() + base.as_posix()[2:])


def test_a_broken_existing_test_is_a_regression(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")

    result = check(root.as_posix(), base, ["pkg/config.py"], [], _execute(), _exists(root, base))

    assert result.regressions == ["tests/test_config.py::test_limit"]
    assert "test_limit" in result.message() and "do not edit or delete" in result.message()


def test_a_test_already_failing_at_the_start_is_not_a_regression(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit():\n    return 10\n\ndef name():\n    return 'app'\n# edit\n", encoding="utf-8")

    result = check(root.as_posix(), base, ["pkg/config.py"], [], _execute(), _exists(root, base))

    assert "tests/test_config.py::test_already_broken" in result.failing_now
    assert result.regressions == [] and result.message() == ""
    assert result.skipped == "failures_predate_the_change"


def test_test_files_the_agent_edited_are_not_used(tmp_path):
    root, base = _repo(tmp_path)
    assert candidate_test_files(["pkg/config.py", "tests/test_config.py"], [], _exists(root, base)) == []


def test_new_test_files_do_not_count(tmp_path):
    root, base = _repo(tmp_path)
    (root / "tests" / "test_new.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")
    assert candidate_test_files(["pkg/config.py"], ["tests/test_new.py"], _exists(root, base)) == ["tests/test_config.py"]


def test_no_start_commit_or_no_related_tests_never_holds(tmp_path):
    root, base = _repo(tmp_path)
    assert check(root.as_posix(), "", ["pkg/config.py"], [], _execute(), _exists(root, base)).skipped == "no_start_commit"
    assert check(root.as_posix(), base, ["pkg/other.py"], [], _execute(), _exists(root, base)).skipped == "no_related_existing_tests"


def test_an_environment_without_pytest_fails_open(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")

    def no_pytest(cmd):
        return "/usr/bin/python: No module named pytest\n", 1

    result = check(root.as_posix(), base, ["pkg/config.py"], [], no_pytest, _exists(root, base))
    assert result.regressions == [] and result.skipped == "tests_did_not_run"


def test_the_agent_tree_is_untouched(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")
    before = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout

    check(root.as_posix(), base, ["pkg/config.py"], [], _execute(), _exists(root, base))

    after = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert after == before and "return 20" in (root / "pkg" / "config.py").read_text(encoding="utf-8")


def test_any_internal_error_fails_open(tmp_path):
    def boom(cmd):
        raise RuntimeError("container went away")

    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("x = 1\n", encoding="utf-8")
    result = check(root.as_posix(), base, ["pkg/config.py"], [], boom, _exists(root, base))
    assert result.regressions == [] and result.skipped.startswith("error:")


# -- v2: discovery, runners, test ids, timeouts --------------------------------------------

def _referencing(root: Path, baseline: str):
    """What the thin agent passes: test files at the start commit that mention a stem."""
    import re as _re

    def find(stems):
        if not stems:
            return []
        pattern = "|".join(_re.escape(s) for s in stems)
        done = subprocess.run(["git", "-C", str(root), "grep", "-l", "-E", rf"\b({pattern})\b", baseline, "--",
                               ":(glob)**/test_*.py", ":(glob)**/*_test.go", ":(glob)**/*.test.*"],
                              capture_output=True, text=True)
        return [line.split(":", 1)[1] for line in done.stdout.splitlines() if ":" in line]

    return find


def test_a_test_file_not_named_after_the_module_is_found_by_reference(tmp_path):
    root, base = _repo(tmp_path)
    (root / "tests" / "test_behaviour.py").write_text(
        "from pkg import config\n\ndef test_limit_is_ten():\n    assert config.limit() == 10\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "more")
    base = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (root / "tests" / "test_config.py").unlink()  # only the differently named file relates
    _git(root, "rm", "-q", "--cached", "tests/test_config.py")
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")

    result = check(root.as_posix(), base, ["pkg/config.py", "tests/test_config.py"], [], _execute(),
                   _exists(root, base), referencing=_referencing(root, base))

    assert "tests/test_behaviour.py" in result.candidates
    assert result.regressions == ["tests/test_behaviour.py::test_limit_is_ten"]


def test_reachable_test_ids_run_instead_of_whole_files(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")
    seen = []
    real = _execute()

    def spy(cmd):
        seen.append(cmd)
        return real(cmd)

    result = check(root.as_posix(), base, ["pkg/config.py"], ["tests/test_config.py::test_limit"], spy, _exists(root, base))
    assert result.regressions == ["tests/test_config.py::test_limit"]
    assert "tests/test_config.py::test_limit" in seen[0] and "test_already_broken" not in seen[0]


def test_a_timed_out_suite_stops_the_gate_after_one_run(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("x = 1\n", encoding="utf-8")
    calls = []

    def slow(cmd):
        calls.append(cmd)
        return "....\nGT_RC=124\n", 0

    result = check(root.as_posix(), base, ["pkg/config.py"], [], slow, _exists(root, base))
    assert result.skipped == "timeout" and len(calls) == 1 and result.regressions == []


def test_go_runs_per_package_and_compares_with_the_start(tmp_path):
    root, base = _repo(tmp_path)
    outputs = iter([
        "--- FAIL: TestParse (0.00s)\n--- FAIL: TestOld (0.00s)\nFAIL\tex.com/p/parser\nGT_RC=1\n",   # now
        "GT_BASE_READY\n",                                                                          # copy
        "--- FAIL: TestOld (0.00s)\nFAIL\tex.com/p/parser\nGT_RC=1\n",                              # start
    ])
    cmds = []

    def fake(cmd):
        cmds.append(cmd)
        return next(outputs), 0

    result = check(root.as_posix(), base, ["parser/parse.go"], [], fake, lambda p: False,
                   go_has_tests=lambda pkg: pkg == "parser")
    assert result.regressions == ["TestParse"]
    assert "go test" in cmds[0] and "./parser" in cmds[0] and "-run" in cmds[2]


def test_js_runner_detection():
    from gt_engine.regression_gate import js_runner

    assert js_runner('{"scripts": {"test": "vitest run"}}') == "vitest"
    assert js_runner('{"devDependencies": {"jest": "^29"}}') == "jest"
    assert js_runner('{"scripts": {"test": "mocha"}}') == ""
    assert js_runner("not json") == ""


def test_js_file_level_regression_with_vitest(tmp_path):
    root, base = _repo(tmp_path)
    outputs = iter([
        " FAIL  src/grid.test.ts > layout > wraps\n Test Files  1 failed (1)\nGT_RC=1\n",   # now
        "GT_BASE_READY\n",
        " Test Files  1 passed (1)\nGT_RC=0\n",                                          # start
    ])
    cmds = []

    def fake(cmd):
        cmds.append(cmd)
        return next(outputs), 0

    result = check(root.as_posix(), base, ["src/grid.ts"], [], fake, lambda p: p == "src/grid.test.ts",
                   package_json='{"devDependencies": {"vitest": "^2"}}')
    assert result.regressions == ["src/grid.test.ts"]
    assert "vitest run" in cmds[0] and "node_modules" in cmds[1]


def test_js_without_a_known_runner_fails_open(tmp_path):
    root, base = _repo(tmp_path)
    result = check(root.as_posix(), base, ["src/grid.ts"], [], lambda c: ("", 0), lambda p: p == "src/grid.test.ts",
                   package_json='{"scripts": {"test": "mocha"}}')
    assert result.skipped == "no_supported_runner" and result.regressions == []


# -- the real chain: GTAttachedAgent -> git grep discovery -> real pytest -> start copy ------

def test_the_thin_agent_holds_a_real_regression_end_to_end(tmp_path):
    """Everything real except the model: the agent's first submit runs the repository's own
    test (found by git grep, not by name), sees it fail, confirms it passed at the start
    commit, and holds the submit naming it. The second submit goes through."""
    from types import SimpleNamespace

    from minisweagent.agents.default import AgentConfig
    from minisweagent.exceptions import Submitted

    from gt_engine.thin_agent import GTAttachedAgent

    root, base = _repo(tmp_path)
    (root / "tests" / "test_behaviour.py").write_text(
        "from pkg import config\n\ndef test_limit_is_ten():\n    assert config.limit() == 10\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "more")
    base = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (root / "pkg" / "config.py").write_text("def limit():\n    return 20\n\ndef name():\n    return 'app'\n", encoding="utf-8")
    run = _execute()

    class Env:
        def __init__(self):
            self.config = SimpleNamespace(cwd=str(root), env={})

        def execute(self, action, cwd="", *, timeout=None):
            command = action["command"]
            if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in command:
                raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}})
            output, rc = run(command)
            return {"output": output, "returncode": rc, "exception_info": ""}

        def get_template_vars(self, **kwargs):
            return {}

        def serialize(self):
            return {}

    class Model:
        def __init__(self):
            self.turns = [["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"], ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]]

        def query(self, messages):
            return {"role": "assistant", "content": "t",
                    "extra": {"actions": [{"command": c} for c in self.turns.pop(0)], "cost": 0.0}}

        def format_message(self, **kwargs):
            return dict(kwargs)

        def format_observation_messages(self, message, outputs, template_vars=None):
            return [{"role": "user", "content": o["output"]} for o in outputs]

        def get_template_vars(self, **kwargs):
            return {}

        def serialize(self):
            return {}

    events = []
    adapter = SimpleNamespace(repo_root=str(root), engine_state=SimpleNamespace(graph_path=""),
                              store=SimpleNamespace(append=lambda event, **row: events.append((event, row))),
                              note_edit=lambda paths: None, phase="", begin_implement=lambda: None)
    delivery = SimpleNamespace(session=SimpleNamespace(), review_baseline=base,
                               observe_turn=lambda commands, outputs, facts: outputs,
                               submit_review_once=lambda: "")
    agent = GTAttachedAgent(Model(), Env(), delivery=delivery, adapter=adapter, config_class=AgentConfig,
                            system_template="sys", instance_template="{{task}}", step_limit=10)
    agent.run("task")

    held = [m for m in agent.messages if "[GT] regression check" in str(m.get("content"))]
    assert len(held) == 1, [str(m.get("content"))[:200] for m in agent.messages]
    assert "tests/test_behaviour.py::test_limit_is_ten" in str(held[0]["content"])
    check_rows = [row for event, row in events if event == "gt_regression_check"]
    assert check_rows and check_rows[0]["regressions"] and check_rows[0]["runners"] == ["pytest"]
    assert agent.messages[-1]["role"] == "exit"  # the second submit went through


# -- v3: /bin/sh exit codes, per-package JS runners --------------------------------------

def test_timeout_is_detected_under_posix_sh(tmp_path):
    """The agent's executor runs /bin/sh (no PIPESTATUS): v2 read a 90 s timeout as
    'tests did not run' (skrub, kcp-go in run 36683688726)."""
    import shutil as _shutil

    sh = _shutil.which("sh") or _bash()
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("x = 1\n", encoding="utf-8")
    calls = []

    def run_sh(cmd):
        calls.append(cmd)
        py = Path(sys.executable).as_posix()
        cmd = cmd.replace("timeout 90 python -m pytest", f"timeout 1 {py} -c 'import time; time.sleep(5)'")
        done = subprocess.run([sh, "-c", cmd], capture_output=True, text=True, timeout=60)
        return done.stdout + done.stderr, done.returncode

    result = check(root.as_posix(), base, ["pkg/config.py"], [], run_sh, _exists(root, base))
    assert "PIPESTATUS" not in calls[0]
    assert result.skipped == "timeout", result


def test_js_runner_comes_from_the_nearest_package_json():
    from gt_engine.regression_gate import js_packages

    files = {"package.json": '{"private": true, "workspaces": ["packages/*"]}',
             "drizzle-orm/package.json": '{"devDependencies": {"vitest": "^1"}}',
             "packages/core/package.json": '{"scripts": {"test": "deno test"}}'}
    groups = js_packages(["drizzle-orm/tests/sql.test.ts", "packages/core/src/a.test.ts"], lambda p: files.get(p, ""))
    assert groups == {"drizzle-orm": ("vitest", ["tests/sql.test.ts"])}  # deno package: no runner, left out


def test_a_monorepo_js_regression_runs_in_its_package(tmp_path):
    root, base = _repo(tmp_path)
    outputs = iter([
        "GT_RC=1\n FAIL  tests/sql.test.ts > window > rank\n Test Files  1 failed (1)\n",   # now
        "GT_BASE_READY\n",
        "GT_RC=0\n Test Files  1 passed (1)\n",                                              # start
    ])
    cmds = []

    def fake(cmd):
        cmds.append(cmd)
        return next(outputs), 0

    files = {"drizzle-orm/package.json": '{"devDependencies": {"vitest": "^1"}}'}
    result = check(root.as_posix(), base, ["drizzle-orm/src/sql.ts"], ["drizzle-orm/tests/sql.test.ts"], fake,
                   lambda p: p == "drizzle-orm/tests/sql.test.ts", package_json=lambda p: files.get(p, ""))
    assert result.regressions == ["drizzle-orm/tests/sql.test.ts"]
    assert f"{root.as_posix()}/drizzle-orm" in cmds[0] and "vitest run" in cmds[0]
    assert "drizzle-orm/node_modules" in cmds[1]


# -- v4: import/build failures are regressions; go -short/-timeout; output tails ---------

def test_a_change_that_breaks_an_import_is_a_regression(tmp_path):
    """numba/skrub in run 36690124216 read 'tests_did_not_run' in seconds: pytest collection
    errors ("1 error") were not recognised. A file that imported at the start and does not
    now is exactly a regression."""
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("def limit(:\n    return 10\n", encoding="utf-8")  # syntax error

    result = check(root.as_posix(), base, ["pkg/config.py"], [], _execute(), _exists(root, base))

    assert result.regressions == ["tests/test_config.py (does not import)"], result
    assert "does not import" in result.message()


def test_a_go_package_that_stops_building_is_a_regression(tmp_path):
    root, base = _repo(tmp_path)
    outputs = iter([
        "GT_RC=1\n# ex.com/p/parser\nparser/parse.go:3:1: syntax error\nFAIL\tex.com/p/parser [build failed]\n",
        "GT_BASE_READY\n",
        "GT_RC=0\nok  \tex.com/p/parser\t0.01s [no tests to run]\n",
    ])
    cmds = []

    def fake(cmd):
        cmds.append(cmd)
        return next(outputs), 0

    result = check(root.as_posix(), base, ["parser/parse.go"], [], fake, lambda p: False,
                   go_has_tests=lambda pkg: pkg == "parser")
    assert result.regressions == ["ex.com/p/parser (does not build)"]
    assert "-short" in cmds[0] and "-timeout 80s" in cmds[0]


def test_go_failures_found_before_a_timeout_are_still_checked(tmp_path):
    root, base = _repo(tmp_path)
    outputs = iter([
        "GT_RC=1\n--- FAIL: TestParse (0.01s)\npanic: test timed out after 80s\nFAIL\tex.com/p/parser\t80.0s\n",
        "GT_BASE_READY\n",
        "GT_RC=0\nok  \tex.com/p/parser\t0.02s\n",
    ])
    result = check(root.as_posix(), base, ["parser/parse.go"], [], lambda c: (next(outputs), 0), lambda p: False,
                   go_has_tests=lambda pkg: pkg == "parser")
    assert result.regressions == ["TestParse"]


def test_no_verdict_keeps_an_output_tail(tmp_path):
    root, base = _repo(tmp_path)
    (root / "pkg" / "config.py").write_text("x = 1\n", encoding="utf-8")
    result = check(root.as_posix(), base, ["pkg/config.py"], [],
                   lambda c: ("GT_RC=4\nERROR: usage: pytest [options] -- unrecognized arguments: --no-header\n", 0),
                   _exists(root, base))
    assert result.skipped == "tests_did_not_run" and "unrecognized arguments" in result.note
