from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment
from scripts.miniswe_repro import (
    ResearchModelMismatch,
    RunReceiptObserver,
    build_reproducibility_manifest,
    write_reproducibility_manifest,
)


class FakeModel:
    model_name = "openai/deepseek-v4-flash"
    model_kwargs = {"temperature": 1.0, "api_base": "https://gateway.invalid"}

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in row.items() if k != "extra"} for row in messages]

    def query(self, messages, **kwargs):
        self._prepare_messages_for_api(messages)
        return {
            "role": "assistant",
            "content": "ok",
            "extra": {
                "response": {
                    "id": "resp-1",
                    "model": "deepseek-v4-flash",
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }
            },
        }


def test_neutral_receipt_observer_commits_final_request_and_response(tmp_path):
    model = FakeModel()
    observer = RunReceiptObserver(
        tmp_path, requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    observer.install(model)
    model.query([{"role": "user", "content": "exact message"}])
    assert observer.request_count == 1
    assert observer.provider_reported_model == "deepseek-v4-flash"
    assert observer.receipt()["valid"] is True
    rows = [json.loads(line) for line in observer.events_path.read_text(
        encoding="utf-8"
    ).splitlines()]
    assert [row["event"] for row in rows] == [
        "provider_request", "provider_response"
    ]
    request = rows[0]
    assert request["messages_sha256"]
    assert request["request_sha256"]
    assert request["request_id_kind"] == "local_correlation"
    assert rows[1]["provider_response_id"] == "resp-1"
    assert rows[1]["latency_ms"] >= 0
    assert (tmp_path / request["request_blob"]).is_file()


def test_neutral_observer_fails_loudly_on_model_substitution(tmp_path):
    class WrongModel(FakeModel):
        def query(self, messages, **kwargs):
            result = super().query(messages, **kwargs)
            result["extra"]["response"]["model"] = "fallback-model"
            return result

    model = WrongModel()
    observer = RunReceiptObserver(
        tmp_path, requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    observer.install(model)
    with pytest.raises(ResearchModelMismatch):
        model.query([{"role": "user", "content": "task"}])
    assert observer.model_mismatch is True


def test_neutral_observer_records_provider_failure_symmetrically(tmp_path):
    class FailedModel(FakeModel):
        def query(self, messages, **kwargs):
            self._prepare_messages_for_api(messages)
            raise TimeoutError("provider timeout")

    model = FailedModel()
    observer = RunReceiptObserver(
        tmp_path, requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    observer.install(model)
    with pytest.raises(TimeoutError):
        model.query([{"role": "user", "content": "task"}])
    rows = [json.loads(line) for line in observer.events_path.read_text(
        encoding="utf-8"
    ).splitlines()]
    assert rows[-1]["event"] == "provider_failure"
    assert rows[-1]["request_id"] == rows[0]["request_id"]
    assert observer.receipt()["valid"] is True


def test_neutral_observer_marks_missing_terminal_or_corrupt_blob_invalid(tmp_path):
    model = FakeModel()
    observer = RunReceiptObserver(
        tmp_path,
        requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    observer.install(model)
    model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    receipt = observer.receipt()
    assert receipt["valid"] is False
    assert any("lacks terminal" in issue for issue in receipt["issues"])

    row = json.loads(observer.events_path.read_text(encoding="utf-8").splitlines()[0])
    (tmp_path / row["request_blob"]).write_text("tampered", encoding="utf-8")
    receipt = observer.receipt()
    assert any("blob hash mismatch" in issue for issue in receipt["issues"])


def test_reproducibility_manifest_redacts_secrets_and_anchors_events(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENAI_API_KEY", "never-write-this-secret")
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"pinned-binary")
    common = dict(
        task="fix it",
        requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
        provider_reported_model="deepseek-v4-flash",
        fallback_model="",
        temperature=1.0,
        cwd=str(tmp_path),
        step_limit=100,
        timeout=30,
        event_journal={
            "event_count": 4,
            "event_head": "a" * 64,
            "valid": True,
        },
        request_receipt={
            "request_count": 2,
            "events_sha256": "b" * 64,
            "valid": True,
        },
        binary_paths=[str(binary)],
        source_paths=[str(binary)],
    )
    on = build_reproducibility_manifest(gt_mode="advisory", **common)
    off = build_reproducibility_manifest(gt_mode="off", **common)
    encoded = json.dumps(on, sort_keys=True)
    assert "never-write-this-secret" not in encoded
    assert on["model"]["requested"] == "deepseek-v4-flash"
    assert on["event_journal"]["event_head"] == "a" * 64
    assert on["binaries"][0]["sha256"]
    assert on["runner_sources"][0]["sha256"]
    assert on["comparison_fingerprint"] == off["comparison_fingerprint"]
    path = write_reproducibility_manifest(tmp_path / "manifest.json", on)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "gt.repro.v1"


def test_manifest_is_invalid_when_any_receipt_layer_is_invalid(tmp_path):
    common = dict(
        task="fix it",
        requested_model="model-a",
        resolved_model="openai/model-a",
        provider_reported_model="model-a",
        fallback_model="",
        temperature=1.0,
        cwd=str(tmp_path),
        step_limit=100,
        timeout=30,
        gt_mode="advisory",
        binary_paths=[],
    )
    bad_provider = build_reproducibility_manifest(
        event_journal={"valid": True},
        request_receipt={"valid": False, "model_mismatch": False},
        **common,
    )
    bad_journal = build_reproducibility_manifest(
        event_journal={"valid": False},
        request_receipt={"valid": True, "model_mismatch": False},
        **common,
    )
    assert bad_provider["research_valid"] is False
    assert bad_journal["research_valid"] is False


def test_agent_shell_environment_excludes_host_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "host-provider-secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "host-gcp-secret.json")
    monkeypatch.setenv("TASK_SAFE_VALUE", "visible")
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=5)
    child = env.execution_env()
    assert child["TASK_SAFE_VALUE"] == "visible"
    assert "OPENAI_API_KEY" not in child
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in child
    template = env.get_template_vars()
    assert "OPENAI_API_KEY" not in template
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in template


@pytest.mark.skipif(sys.platform != "linux", reason="Linux procfs boundary")
def test_model_shell_cannot_read_provider_key_from_parent_procfs(tmp_path):
    canary = "gt-provider-procfs-canary"
    parent_code = r'''
import os
import subprocess
import sys
from scripts.miniswe_gt_run import _harden_process_secret_boundary

_harden_process_secret_boundary()
child_env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
child_code = r"""
import os
try:
    payload = open(f"/proc/{os.getppid()}/environ", "rb").read()
except OSError:
    print("BLOCKED")
else:
    print("LEAK" if b"gt-provider-procfs-canary" in payload else "READABLE")
"""
result = subprocess.run(
    [sys.executable, "-c", child_code],
    env=child_env,
    text=True,
    capture_output=True,
    check=True,
)
print(result.stdout.strip())
'''
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = canary

    result = subprocess.run(
        [sys.executable, "-c", parent_code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "BLOCKED"
    assert canary not in result.stdout
