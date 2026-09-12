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


@pytest.mark.parametrize("state_inside", [False, True, "shared"])
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
    state = (repository if state_inside == "shared" else
             repository / "runtime-records" if state_inside else tmp_path / "state")
    internal = state / "fixture-task" if state_inside == "shared" else state
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "internal.json").write_text('{"internal_state": true}', encoding="utf-8")
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


def test_export_omits_generated_cache_and_preserves_source_paths(tmp_path):
    from gt_engine.runtime_observation import capture_workspace
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout
    git("init")
    (repo / "source.py").write_text("value = 1\n")
    cache = repo / "__pycache__"
    cache.mkdir()
    tracked = cache / "tracked.py"
    tracked.write_text("tracked = 1\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=", "commit", "-m", "baseline")
    baseline = git("rev-parse", "HEAD").decode().strip()
    subprocess.run([sys.executable, "-c", "import source"], cwd=repo, check=True,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": ""})
    assert list(cache.glob("*.pyc"))
    (repo / "source.py").write_text("value = 2\n")
    tracked.write_text("tracked = 2\n")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "new.py").write_text("new = 1\n")
    patch = tmp_path / "model.patch"
    supervisor.export_patch(repo, baseline, patch)
    payload = patch.read_bytes()
    assert b".pyc" not in payload
    assert b"a/__pycache__/tracked.py" in payload
    assert b"b/vendor/new.py" in payload
    paths = {row.path for row in capture_workspace(repo).files}
    assert "vendor/new.py" in paths
    assert "__pycache__/tracked.py" in paths


def test_submission_state_distinguishes_commits_from_recovery_bytes(tmp_path):
    import hashlib

    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout
    def commit():
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "core.hooksPath=", "commit", "--allow-empty", "-m", "fixture")
    git("init")
    (repo / "a.py").write_text("x = 1\n")
    git("add", ".")
    commit()
    baseline = git("rev-parse", "HEAD").decode().strip()
    commit()  # A moved HEAD does not establish a nonempty collectible patch.
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 3\n")
    index_before = git("diff", "--cached")
    state = supervisor.submission_patch_state(repo, baseline)
    assert state["status"] == "observed"
    assert state["committed_patch_empty"] is True
    assert state["uncommitted_tracked"] is True
    assert state["untracked_paths"] == ["new.py"]
    assert state["official_collection_observed"] is False
    recovery = tmp_path / "worktree.patch"
    supervisor.export_patch(repo, baseline, recovery)
    assert recovery.stat().st_size > 0
    assert git("diff", "--cached") == index_before
    git("add", ".")
    assert supervisor.submission_patch_state(repo, baseline)["committed_patch_empty"] is True
    commit()
    state = supervisor.submission_patch_state(repo, baseline)
    official = git("diff", "--binary", baseline, "HEAD")
    assert state["committed_patch_sha256"] == hashlib.sha256(official).hexdigest()
    assert state["committed_patch_empty"] is False
    assert state["uncommitted_tracked"] is False
    assert state["untracked_paths"] == []


def test_submission_state_unavailable_is_not_empty_patch(tmp_path):
    state = supervisor.submission_patch_state(tmp_path, "missing")
    assert state["status"] == "unavailable"
    assert "committed_patch_empty" not in state


def test_conserve_failure_rebuilds_gt_section_from_sealed_journal(tmp_path):
    """A killed child never reaches final_state(), so report["gt"] would be
    absent and attestation would read zeros where the journal proves calls
    were spent. The supervisor rebuilds the honest minimum journal-side."""
    import argparse

    state_dir = tmp_path / "gt-state"
    task_state = state_dir / "task-x"
    task_state.mkdir(parents=True)
    events = [
        {"event": "context_addition_delivery", "lane": "prompt",
         "kind": "context_contract", "evidence_type": "context_contract",
         "dedup_key": "prompt-contract-1", "payload_sha256": "a" * 64,
         "iteration": 0, "sequence": 1, "event_hash": "1" * 64},
        {"event": "select_catalog_lifecycle", "sequence": 2,
         "reason": "provider_request_admitted", "event_hash": "2" * 64},
        {"event": "provider_delivery", "sequence": 3, "iteration": 1,
         "request_id": "request-1",
         "resolved_model": "openai/test-model", "event_hash": "3" * 64},
        {"event": "provider_response", "sequence": 4, "request_id": "request-1",
         "usage": {"prompt_tokens": 10, "completion_tokens": 2},
         "event_hash": "4" * 64},
        {"event": "persistent_plan_built", "sequence": 5,
         "finish_reason": "tool_calls", "event_hash": "5" * 64},
        {"event": "provider_delivery", "sequence": 6, "iteration": 2,
         "request_id": "request-2",
         "resolved_model": "openai/test-model", "event_hash": "6" * 64},
        {"event": "provider_response", "sequence": 7, "request_id": "request-2",
         "usage": {"prompt_tokens": 30, "completion_tokens": 4},
         "event_hash": "7" * 64},
    ]
    (task_state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"model": "test-model"}), encoding="utf-8")
    args = argparse.Namespace(
        metrics=str(report_path), state_dir=str(state_dir), cwd=str(tmp_path),
        patch_output="", synthetic_transport=False, gt_off=False,
        gt_mode="advisory", product_receipt="", adapter_receipt="",
        checkpoint_directory="", checkpoint_run_nonce="",
        checkpoint_workspace_sha256="",
    )
    result = supervisor.SupervisedResult(
        reason="deadline_exceeded", returncode=-9, elapsed_seconds=5400.0,
    )

    supervisor.conserve_failure(args, result, "")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["terminal"] == "timeout"
    gt = report["gt"]
    assert gt["reconstructed_from_journal"] is True
    assert gt["verified"] is False
    assert gt["resolved_model"] == "openai/test-model"
    assert gt["select_catalog_bootstrap_calls"] == 1
    assert gt["persistent_plan_bootstrap_calls"] == 1
    assert gt["contract_shipped"] is True
    assert gt["usage"] == {"prompt_tokens": 40, "completion_tokens": 6}
    assert report["gt_mode"] == "advisory"


def test_abort_probe_reason_kills_child_and_is_preserved(tmp_path):
    """The churn flag must terminate the child through the normal seal path
    and surface as the result reason, not as a generic deadline."""
    started = time.monotonic()
    flag = tmp_path / "churn_abort.json"
    ticks = {"n": 0}

    def probe():
        ticks["n"] += 1
        if ticks["n"] >= 3:
            flag.write_text("{}", encoding="utf-8")
            return "churn_abort"
        return None

    result = supervisor.supervise(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        deadline=started + 60.0, termination_grace_seconds=0.1,
        abort_probe=probe,
    )
    assert result.reason == "churn_abort"
    assert result.returncode is not None
    assert time.monotonic() - started < 8


def test_churn_abort_maps_to_typed_terminal(tmp_path):
    import argparse

    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"model": "m"}), encoding="utf-8")
    args = argparse.Namespace(
        metrics=str(report_path), state_dir=str(tmp_path / "gt-state"),
        cwd=str(tmp_path), patch_output="", synthetic_transport=False,
        gt_off=False, gt_mode="advisory", product_receipt="",
        adapter_receipt="", checkpoint_directory="",
        checkpoint_run_nonce="", checkpoint_workspace_sha256="",
    )
    result = supervisor.SupervisedResult(
        reason="churn_abort", returncode=-9, elapsed_seconds=1200.0,
    )
    supervisor.conserve_failure(args, result, "")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["terminal"] == "churn_abort"
    assert report["exit_code"] == 7
    assert report["supervisor"]["reason"] == "churn_abort"
