from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from scripts import miniswe_supervisor as supervisor


def test_hung_child_is_reaped_with_its_last_written_artifact(tmp_path):
    marker = tmp_path / "last-action"
    started = time.monotonic()
    result = supervisor.supervise(
        [sys.executable, "-c",
         "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ack'); time.sleep(60)",
         str(marker)],
        deadline=started + 1.0, termination_grace_seconds=0.1,
    )
    assert result.reason == "deadline_exceeded"
    assert result.returncode is not None
    assert marker.read_text() == "ack"
    assert time.monotonic() - started < 8


def test_expired_budget_does_not_start_child(tmp_path):
    marker = tmp_path / "must-not-exist"
    result = supervisor.supervise(
        [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
        deadline=time.monotonic() - 1, termination_grace_seconds=0.1,
    )
    assert result.reason == "deadline_exceeded"
    assert result.returncode is None
    assert not marker.exists()


def test_normal_child_exit_is_not_reclassified():
    result = supervisor.supervise(
        [sys.executable, "-c", "raise SystemExit(6)"],
        deadline=time.monotonic() + 5, termination_grace_seconds=0.1,
    )
    assert result.reason == "exited"
    assert result.returncode == 6


@pytest.mark.parametrize("state_inside", [False, True])
def test_actual_cli_expired_startup_conserves_patch_and_error_receipts(tmp_path, state_inside):
    repository = tmp_path / "repo"
    repository.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "GT Test", "GIT_COMMITTER_NAME": "GT Test",
           "GIT_AUTHOR_EMAIL": "gt-test@example.invalid",
           "GIT_COMMITTER_EMAIL": "gt-test@example.invalid"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=repository, env=env,
                              capture_output=True, check=True).stdout

    git("init")
    source = repository / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("-c", "core.hooksPath=", "commit", "-m", "fixture")
    source.write_text("x = 2\n", encoding="utf-8")
    index_before = git("diff", "--cached")
    output = tmp_path / "artifacts"
    state = repository / "runtime-records" if state_inside else tmp_path / "state"
    state.mkdir()
    (state / "internal.json").write_text('{"internal_state": true}', encoding="utf-8")
    result = subprocess.run([
        sys.executable, "-m", "scripts.miniswe_supervisor",
        "--task", "fixture", "--model", "fixture/model", "--task-id", "fixture-task",
        "--cwd", str(repository), "--state-dir", str(state),
        "--time-budget-seconds", "0", "--metrics", str(output / "report.json"),
        "--patch-output", str(output / "model.patch"),
        "--product-receipt", str(output / "product.json"),
        "--adapter-receipt", str(output / "adapter.json"),
    ], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 3, result.stderr
    report = json.loads((output / "report.json").read_bytes())
    assert report["terminal"] == "timeout"
    assert report["supervisor"]["child_returncode"] is None
    assert "+x = 2" in (output / "model.patch").read_text()
    assert "internal_state" not in (output / "model.patch").read_text()
    assert "runtime-records/" not in (output / "model.patch").read_text()
    assert git("diff", "--cached") == index_before
    receipt = json.loads((output / "product.json").read_bytes())
    assert receipt["status"] == "ERROR"
    assert receipt["research_valid"] is False
    assert receipt["provider_calls"] is None
    assert receipt["integrity"]["trajectory_sha256"] is None
def test_public_console_entrypoint_uses_deadline_owner():
    import tomllib
    from pathlib import Path

    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["gt-miniswe-run"] == "scripts.miniswe_supervisor:main"
