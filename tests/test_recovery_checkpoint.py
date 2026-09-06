import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gt_engine.engine_state import RuntimeLayout
from gt_engine.runtime_observation import capture_workspace
from scripts import miniswe_supervisor as supervisor_module
from scripts.miniswe_checkpoint import (
    INTEGRITY_SCOPE,
    capture_checkpoint,
    read_checkpoint,
    workspace_identity,
)
from scripts.miniswe_supervisor import supervise


def _fixture(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "GT Test", "GIT_COMMITTER_NAME": "GT Test",
           "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_EMAIL": "test@example.invalid"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, env=env,
                              capture_output=True, check=True).stdout

    git("init")
    (root / "source.py").write_text("value = 1\n")
    git("add", ".")
    git("-c", "core.hooksPath=", "commit", "-m", "baseline")
    baseline = git("rev-parse", "HEAD").decode().strip()
    layout = RuntimeLayout.resolve(workspace=root, state_root=root / "state", task_id="task")
    run_nonce = "0123456789abcdef0123456789abcdef"
    request = {"workspace": str(root), "baseline": baseline,
               "run_nonce": run_nonce,
               "workspace_sha256": workspace_identity(root),
               "directory": str(layout.task_root / "recovery" / run_nonce),
               "excluded_roots": [str(path) for path in layout.excluded_roots],
               "deadline": time.monotonic() + 30, "interval_seconds": 0.1,
               "synthetic_transport": True}
    return root, layout, request


def test_checkpoint_is_durable_integrity_bound_and_not_source_churn(tmp_path):
    root, layout, request = _fixture(tmp_path)
    (root / "source.py").write_text("value = 2\n")
    before = capture_workspace(root, excluded_roots=layout.excluded_roots)
    capture_checkpoint(request)
    first, payload = read_checkpoint(
        Path(request["directory"]), request["baseline"],
        run_nonce=request["run_nonce"], workspace_sha256=request["workspace_sha256"],
    )
    assert b"+value = 2" in payload
    assert b"state/" not in payload
    assert not first["verified"] and not first["terminal"]
    assert not first["code_current"] and not first["official_score"]
    assert first["integrity_scope"] == INTEGRITY_SCOPE
    assert first["synthetic_transport"]
    capture_checkpoint(request)
    after = capture_workspace(root, excluded_roots=layout.excluded_roots)
    assert before.revision == after.revision
    assert first["patch_sha256"] == hashlib.sha256(payload).hexdigest()
    (Path(request["directory"]) / "patches" / first["patch_sha256"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity_invalid"):
        read_checkpoint(
            Path(request["directory"]), request["baseline"],
            run_nonce=request["run_nonce"],
            workspace_sha256=request["workspace_sha256"],
        )


def test_checkpoint_rejects_same_baseline_from_another_attempt(tmp_path):
    root, _layout, request = _fixture(tmp_path)
    (root / "source.py").write_text("value = 2\n")
    capture_checkpoint(request)
    other_nonce = "fedcba9876543210fedcba9876543210"
    other_directory = Path(request["directory"]).parent / other_nonce

    with pytest.raises((FileNotFoundError, ValueError)):
        read_checkpoint(
            other_directory, request["baseline"], run_nonce=other_nonce,
            workspace_sha256=request["workspace_sha256"],
        )
    with pytest.raises(ValueError, match="identity_invalid"):
        read_checkpoint(
            Path(request["directory"]), request["baseline"], run_nonce=other_nonce,
            workspace_sha256=request["workspace_sha256"],
        )


def test_checkpoint_helper_failure_is_an_explicit_degradation(tmp_path):
    status_path = tmp_path / "checkpoint-helper.json"
    result = supervise(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        deadline=time.monotonic() + 5,
        checkpoint_command=[sys.executable, "-c", "raise SystemExit(7)"],
        checkpoint_status_path=status_path,
    )

    status = json.loads(status_path.read_bytes())
    assert result.reason == "exited"
    assert result.checkpoint_status == "degraded"
    assert result.checkpoint_returncode == 7
    assert status["status"] == "degraded"
    assert status["candidate_patch_only"] is True
    assert status["verified"] is False and status["official_score"] is False


def test_checkpoint_teardown_failure_does_not_skip_signal_restoration(
    tmp_path, monkeypatch
):
    class BrokenCheckpoint:
        pid = 999_999_999

        @staticmethod
        def poll():
            return None

        @staticmethod
        def wait(timeout=None):
            raise subprocess.TimeoutExpired("checkpoint", timeout)

    real_popen = subprocess.Popen
    real_signal_worker = supervisor_module._signal_worker

    def popen(command, *args, **kwargs):
        if command == ["broken-checkpoint"]:
            return BrokenCheckpoint()
        return real_popen(command, *args, **kwargs)

    def signal_worker(process, *, force):
        if isinstance(process, BrokenCheckpoint):
            return
        return real_signal_worker(process, force=force)

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", popen)
    monkeypatch.setattr(supervisor_module, "_signal_worker", signal_worker)
    previous = signal.getsignal(signal.SIGTERM)
    status_path = tmp_path / "checkpoint-helper.json"

    result = supervise(
        [sys.executable, "-c", "pass"], deadline=time.monotonic() + 5,
        checkpoint_command=["broken-checkpoint"],
        checkpoint_status_path=status_path,
    )

    assert result.checkpoint_status == "teardown_failed"
    assert result.checkpoint_error_type == "TimeoutExpired"
    assert signal.getsignal(signal.SIGTERM) is previous
    assert json.loads(status_path.read_bytes())["status"] == "teardown_failed"


@pytest.mark.skipif(os.name != "posix", reason="installed isolated Linux worker required")
def test_checkpoint_survives_real_agent_deadline_during_active_command(tmp_path):
    root, layout, request = _fixture(tmp_path)
    directory = Path(request["directory"])
    directory.mkdir(parents=True)
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request))
    result = supervise(
        [sys.executable, "-c", "import pathlib,sys,time; "
         "pathlib.Path(sys.argv[1]).write_text('value = 3\\n'); time.sleep(60)",
         str(root / "source.py")],
        deadline=time.monotonic() + 3, termination_grace_seconds=0.1,
        checkpoint_command=[sys.executable, "-I", "-m", "scripts.miniswe_checkpoint",
                            str(request_path)],
    )
    assert result.reason == "deadline_exceeded"
    receipt, patch = read_checkpoint(
        directory, request["baseline"], run_nonce=request["run_nonce"],
        workspace_sha256=request["workspace_sha256"],
    )
    assert b"+value = 3" in patch
    assert not receipt["terminal"]
    assert not receipt["verified"]
    # A later edit was never checkpointed and must not be invented on recovery.
    (root / "source.py").write_text("value = 4\n")
    _, recovered = read_checkpoint(
        directory, request["baseline"], run_nonce=request["run_nonce"],
        workspace_sha256=request["workspace_sha256"],
    )
    assert b"+value = 4" not in recovered
