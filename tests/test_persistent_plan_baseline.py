"""The pre-edit green baseline: capture, restore, and regression comparison.

Every repository here is built in the test. No benchmark task's tests are read.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gt_engine.persistent_plan.baseline import (
    BASELINE_MAX_SECONDS,
    baseline_budget_seconds,
    compare_to_baseline,
    discover_command,
    run_baseline,
)


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "-m", "base"],
        cwd=root, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A pytest repository with two passing tests and one failing test."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "tests" / "test_green.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_red.py").write_text(
        "def test_already_broken():\n    assert False\n", encoding="utf-8"
    )
    _git_repo(root)
    return root


def _pytest_command() -> tuple[str, ...]:
    # Run the suite through this interpreter so the test never depends on a
    # `pytest` executable being on PATH.
    return (sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider")


def test_nonzero_exit_after_passing_summary_cannot_establish_intact_baseline(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    before = run_baseline(str(tmp_path), budget_seconds=15, command=_pytest_command())
    assert before.captured and before.passed == 1 and before.exit_code == 0
    (tmp_path / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n", encoding="utf-8")
    report = compare_to_baseline(before, str(tmp_path), budget_seconds=15)
    assert report.after.passed == 1
    assert report.after.exit_code == 1
    assert report.status == "unknown"
    assert "nonzero exit" in report.detail


def test_budget_is_a_bounded_fraction_of_the_run():
    assert baseline_budget_seconds(5100) == pytest.approx(BASELINE_MAX_SECONDS)
    assert baseline_budget_seconds(1000) == pytest.approx(30.0)
    assert baseline_budget_seconds(0) > 0
    assert baseline_budget_seconds(None) > 0
    assert baseline_budget_seconds(10_000_000) <= BASELINE_MAX_SECONDS


@pytest.mark.parametrize("complete", [False, None])
def test_incomplete_baseline_capture_never_becomes_green(tmp_path, monkeypatch, complete):
    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n")
    original = CredentialIsolatedLocalEnvironment.execute

    def incomplete(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result["extra"]["capture_complete"] = complete
        return result

    monkeypatch.setattr(CredentialIsolatedLocalEnvironment, "execute", incomplete)
    result = run_baseline(str(tmp_path), budget_seconds=15, command=_pytest_command())
    assert not result.captured
    assert result.status == "spawn_failed"


def test_a_repository_with_no_declared_command_abstains(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    result = run_baseline(str(root), budget_seconds=30)
    assert result.status == "no_test_command"
    assert not result.captured


def test_pytest_config_is_discovered(repo):
    command, basis, confidence = discover_command(str(repo))
    assert command == ("pytest",)
    assert basis.startswith("config:")
    assert confidence == "medium"


def test_capture_records_the_green_and_red_split(repo):
    result = run_baseline(
        str(repo), budget_seconds=120, command=_pytest_command(),
        basis="config:pytest_ini", confidence="medium",
    )
    assert result.captured, result.as_dict()
    assert result.passed == 2
    assert result.failed == 1
    assert result.exit_code != 0
    assert result.output_sha256
    assert result.duration_seconds > 0
    assert "baseline: 2 passing, 1 failing" in result.summary()


def test_an_unfinishable_suite_times_out_without_raising(tmp_path):
    root = tmp_path / "slow"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8"
    )
    _git_repo(root)
    result = run_baseline(
        str(root), budget_seconds=2, command=_pytest_command(),
    )
    assert result.status == "timeout"
    assert not result.captured
    assert "exceeded" in result.detail


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux descendant containment")
def test_baseline_timeout_reaps_grandchildren(tmp_path):
    import os
    import signal

    script = tmp_path / "slow.py"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "Path('child.pid').write_text(str(p.pid))\ntime.sleep(60)\n", encoding="utf-8")
    result = run_baseline(str(tmp_path), budget_seconds=1,
                          command=(sys.executable, str(script)))
    pid = int((tmp_path / "child.pid").read_text())
    try:
        assert result.status == "timeout"
        assert not Path(f"/proc/{pid}").exists(), "baseline left a running descendant"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_suite_that_dirties_the_worktree_is_not_destructively_restored(tmp_path):
    """Automatic checks may not discard source mutations."""
    root = tmp_path / "dirty"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "data.txt").write_text("original\n", encoding="utf-8")
    (root / "tests" / "test_writes.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_writes():\n"
        "    Path(__file__).parent.parent.joinpath('data.txt')"
        ".write_text('mutated\\n', encoding='utf-8')\n"
        "    assert True\n",
        encoding="utf-8",
    )
    _git_repo(root)
    result = run_baseline(str(root), budget_seconds=120, command=_pytest_command())
    assert result.captured
    assert result.restored_paths == ()
    assert (root / "data.txt").read_text(encoding="utf-8") == "mutated\n"


def test_a_runner_with_no_parseable_result_is_not_a_baseline(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _git_repo(root)
    result = run_baseline(
        str(root), budget_seconds=60, command=(sys.executable, "-c", "print('nothing')")
    )
    assert result.status == "no_tests_observed"
    assert not result.captured


def test_an_intact_suite_reports_no_regression(repo):
    baseline = run_baseline(str(repo), budget_seconds=120, command=_pytest_command())
    report = compare_to_baseline(baseline, str(repo), budget_seconds=120)
    assert report.status == "intact"
    assert not report.regressed
    assert report.newly_failing == ()


def test_breaking_a_previously_green_test_is_a_regression(repo):
    baseline = run_baseline(str(repo), budget_seconds=120, command=_pytest_command())
    assert baseline.passed == 2
    (repo / "tests" / "test_green.py").write_text(
        "def test_one():\n    assert False\n\n\ndef test_two():\n    assert True\n",
        encoding="utf-8",
    )
    report = compare_to_baseline(baseline, str(repo), budget_seconds=120)
    assert report.regressed
    assert report.failed_delta > 0
    assert report.passed_delta < 0


def test_an_already_failing_test_is_not_charged_to_the_agent(repo):
    """The suite arrives red; staying red is context, not a regression."""
    baseline = run_baseline(str(repo), budget_seconds=120, command=_pytest_command())
    report = compare_to_baseline(baseline, str(repo), budget_seconds=120)
    assert report.status == "intact"


def test_a_new_failing_test_is_not_reported_as_previously_passing(repo):
    baseline = run_baseline(str(repo), budget_seconds=120, command=_pytest_command())
    (repo / "tests" / "test_new.py").write_text(
        "def test_new_behavior():\n    assert False\n", encoding="utf-8")
    report = compare_to_baseline(baseline, str(repo), budget_seconds=120)
    assert report.newly_failing == ()
    assert report.status == "new_failures_unattributed"
    assert not report.regressed
    assert report.after.failed > baseline.failed


def test_environment_change_cannot_establish_baseline_conservation(repo):
    import os

    initial_env = dict(os.environ, WIDGET_MODE="strict")
    baseline = run_baseline(str(repo), budget_seconds=30, command=_pytest_command(),
                            execution_env=initial_env)
    report = compare_to_baseline(baseline, str(repo), budget_seconds=30,
                                 execution_env=dict(initial_env, WIDGET_MODE="permissive"))
    assert report.status == "unknown"
    assert report.detail == "baseline environment identity changed"
    assert baseline.as_dict()["environment_sha256"]
    assert report.after.environment_sha256 != baseline.environment_sha256
    assert report.as_dict()["after_environment_sha256"] == report.after.environment_sha256


def test_historical_baseline_without_environment_binding_remains_unknown(repo):
    from dataclasses import replace

    baseline = run_baseline(str(repo), budget_seconds=30, command=_pytest_command())
    report = compare_to_baseline(replace(baseline, environment_sha256=""),
                                 str(repo), budget_seconds=30)
    assert report.status == "unknown"
    assert report.detail == "baseline environment identity missing"


def test_plan_inputs_forward_the_task_environment(repo, monkeypatch):
    from gt_engine.persistent_plan import build_plan_inputs
    from gt_engine.persistent_plan.baseline import BaselineResult

    seen = []
    def capture(*args, **kwargs):
        seen.append(kwargs["execution_env"])
        return BaselineResult(status="no_tests_observed")
    monkeypatch.setattr("gt_engine.persistent_plan.run_baseline", capture)
    environment = {"WIDGET_MODE": "strict"}
    build_plan_inputs("The widget must preserve compatibility.", repo_root=str(repo),
                      execution_env=environment)
    assert seen == [environment]


def test_real_agent_baseline_uses_its_task_environment(repo, tmp_path, monkeypatch):
    import hashlib

    from gt_harness.canonical_io import canonical_json_bytes
    from scripts.miniswe_gt_run import build_agent

    monkeypatch.setenv("GT_PERSISTENT_PLAN", "1")
    agent, adapter, session = build_agent(
        task="The widget must preserve compatibility.", model="deepseek-v4-flash",
        cwd=str(repo), state_dir=str(tmp_path / "state"), output=None,
        temperature=1.0, gt_off=False, wall_time_limit_seconds=1000,
    )
    baseline = adapter.plan_inputs.baseline
    assert baseline.captured
    assert agent.env.config.env["GT_PLAN_ROOT"] == str(adapter.store.root / "plan")
    assert baseline.environment_sha256 == hashlib.sha256(
        canonical_json_bytes(agent.env.execution_env())).hexdigest()


def test_comparison_without_a_baseline_never_blocks(tmp_path):
    from gt_engine.persistent_plan.baseline import BaselineResult

    report = compare_to_baseline(
        BaselineResult(status="no_test_command"), str(tmp_path), budget_seconds=10
    )
    assert report.status == "no_baseline"
    assert not report.regressed


def test_an_unparseable_recheck_is_unknown_not_a_block(repo, monkeypatch):
    baseline = run_baseline(str(repo), budget_seconds=120, command=_pytest_command())

    def broken(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr("gt_engine.persistent_plan.baseline._execute_baseline", broken)
    report = compare_to_baseline(baseline, str(repo), budget_seconds=10)
    assert report.status == "unknown"
    assert not report.regressed


def test_a_runner_the_container_lacks_falls_back_to_this_interpreter(repo):
    """A low-confidence guess that cannot spawn must not cost the baseline.

    Measured in production: a repo whose only signal was a tox.ini resolved to
    `tox`, which was absent, so the baseline died with FileNotFoundError. That
    emptied the test command, and every requirement without a covering test
    silently lost its check too.
    """
    result = run_baseline(
        str(repo), budget_seconds=120,
        command=("definitely-not-installed-runner",),
        basis="extension_fallback", confidence="low",
    )
    assert result.captured, result.as_dict()
    assert result.basis == "interpreter_fallback"
    assert result.passed == 2
    assert result.failed == 1


def test_the_fallback_is_attempted_only_once(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    result = run_baseline(
        str(root), budget_seconds=30, command=("definitely-not-installed",),
        basis="extension_fallback", confidence="low",
    )
    # pytest runs, finds no tests, so this is a parse result rather than a
    # second spawn failure; either way it must terminate rather than recurse.
    assert result.status in {"no_tests_observed", "spawn_failed", "captured"}
