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


def test_budget_is_a_bounded_fraction_of_the_run():
    assert baseline_budget_seconds(5100) == pytest.approx(BASELINE_MAX_SECONDS)
    assert baseline_budget_seconds(1000) == pytest.approx(30.0)
    assert baseline_budget_seconds(0) > 0
    assert baseline_budget_seconds(None) > 0
    assert baseline_budget_seconds(10_000_000) <= BASELINE_MAX_SECONDS


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

    monkeypatch.setattr(subprocess, "run", broken)
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
