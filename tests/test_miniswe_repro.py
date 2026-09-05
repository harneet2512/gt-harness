from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.miniswe_gt_run import (
    MAX_TOOL_OUTPUT_CHARS,
    MINISWE_HISTORY_CHAR_BUDGET,
    CredentialIsolatedLocalEnvironment,
    _compact_miniswe_history,
    _model_and_kwargs,
    _truncate_tool_output,
    _write_model_patch,
)
from scripts.miniswe_repro import (
    ResearchModelMismatch,
    RunReceiptObserver,
    build_reproducibility_manifest,
    write_reproducibility_manifest,
)


def test_muse_route_preserves_the_baseline_xhigh_reasoning_contract(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.invalid/api/v1")

    model, kwargs = _model_and_kwargs("meta/muse-spark-1.2-contributor", 1.0)

    assert model == "openai/meta/muse-spark-1.2-contributor"
    assert kwargs["reasoning"] == {"effort": "xhigh"}


def test_deepseek_route_forwards_relace_only_without_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.invalid/api/v1")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "16384")
    monkeypatch.setenv(
        "GT_PROVIDER_ROUTING_JSON",
        json.dumps(
            {
                "only": ["relace"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        ),
    )

    model, kwargs = _model_and_kwargs("deepseek/deepseek-v4-flash-0731", 1.0)

    assert model == "openai/deepseek/deepseek-v4-flash-0731"
    assert kwargs["max_tokens"] == 16_384
    assert "max_completion_tokens" not in kwargs
    assert kwargs["extra_body"] == {
        "provider": {
            "only": ["relace"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    }


def test_deepseek_openrouter_route_refuses_missing_provider_lock(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.invalid/api/v1")
    monkeypatch.delenv("GT_PROVIDER_ROUTING_JSON", raising=False)

    with pytest.raises(ValueError, match="provider_routing_env_invalid"):
        _model_and_kwargs("deepseek/deepseek-v4-flash-0731", 1.0)


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
    missing_engine = build_reproducibility_manifest(
        event_journal={"valid": True},
        request_receipt={"valid": True, "model_mismatch": False},
        **common,
    )
    assert missing_engine["research_valid"] is False


@pytest.mark.parametrize("mode, receipt, expected", [
    ("off", None, True),
    ("assistive", None, False),
    ("assistive", {"schema": "gt.engine_integrity.v1", "valid": True,
                   "mode": "assistive", "issues": [], "disabled_stage": ""}, True),
    ("assistive", {"schema": "gt.engine_integrity.v1", "valid": True,
                   "mode": "advisory", "issues": [], "disabled_stage": ""}, False),
    ("assistive", {"schema": "gt.engine_integrity.v1", "valid": True,
                   "mode": "assistive", "issues": [], "disabled_stage": "before_action"}, False),
])
def test_manifest_requires_matching_engine_integrity(tmp_path, monkeypatch, mode, receipt, expected):
    monkeypatch.setattr("scripts.miniswe_repro._installed_packages", lambda: {})
    manifest = build_reproducibility_manifest(
        task="fixture", requested_model="model-a", resolved_model="model-a",
        provider_reported_model="model-a", fallback_model="", temperature=0,
        cwd=str(tmp_path), step_limit=1, timeout=1, gt_mode=mode,
        event_journal={"valid": True}, request_receipt={"valid": True},
        engine_integrity=receipt,
    )
    assert manifest["research_valid"] is expected


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


def test_miniswe_tool_output_is_bounded_without_losing_head_or_tail() -> None:
    raw = "HEAD" + ("x" * (MAX_TOOL_OUTPUT_CHARS * 2)) + "TAIL"
    bounded = _truncate_tool_output(raw)
    assert len(bounded) < len(raw)
    assert len(bounded) <= MAX_TOOL_OUTPUT_CHARS + 100
    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")
    assert "truncated" in bounded


def test_miniswe_history_drops_old_tool_payloads_before_quadratic_replay() -> None:
    messages = [{"role": "system", "content": "policy"}]
    for index in range(20):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": f"call-{index}", "function": {"arguments": "{}"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{index}",
                    "content": "x" * MAX_TOOL_OUTPUT_CHARS,
                    "extra": {"raw_output": "x" * MAX_TOOL_OUTPUT_CHARS},
                },
            ]
        )

    _compact_miniswe_history(messages)

    provider_chars = sum(
        len(json.dumps({key: value for key, value in row.items() if key != "extra"}))
        for row in messages
    )
    assert provider_chars <= MINISWE_HISTORY_CHAR_BUDGET
    assert any(
        str(row.get("content", "")).startswith("[truncated") for row in messages
    )
    assert messages[-1]["content"] == "x" * MAX_TOOL_OUTPUT_CHARS
    assert messages[2]["extra"]["raw_output"] == "x" * MAX_TOOL_OUTPUT_CHARS


def test_miniswe_history_preserves_reasoning_metadata_and_tool_arguments() -> None:
    messages = [{"role": "system", "content": "policy"}]
    for index in range(4):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "provider_specific_fields": {
                        "reasoning": "r" * (1_000 if index == 3 else 60_000)
                    },
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps(
                                    {"command": "x" * (1_000 if index == 3 else 60_000)}
                                ),
                            },
                        }
                    ],
                    "extra": {"response": {"reasoning": "r" * 60_000}},
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{index}",
                    "content": "ok",
                    "extra": {"raw_output": "ok"},
                },
            ]
        )

    newest_arguments = messages[-2]["tool_calls"][0]["function"]["arguments"]
    _compact_miniswe_history(messages)

    provider_chars = sum(
        len(json.dumps({key: value for key, value in row.items() if key != "extra"}))
        for row in messages
    )
    # Reasoning-model continuity is a provider contract. Tool output may be
    # compacted, but assistant reasoning and action semantics remain intact
    # even when that means this deterministic pass cannot reach its target.
    assert provider_chars > MINISWE_HISTORY_CHAR_BUDGET
    assert "provider_specific_fields" in messages[1]
    assert messages[1]["tool_calls"][0]["function"]["arguments"] != "{}"
    assert messages[-2]["tool_calls"][0]["function"]["arguments"] == newest_arguments
    assert messages[1]["extra"]["response"]["reasoning"] == "r" * 60_000


def test_miniswe_history_preserves_every_result_in_latest_multi_tool_turn() -> None:
    messages = [
        {"role": "system", "content": "policy"},
        {
            "role": "assistant",
            "provider_specific_fields": {"reasoning": "r" * 2_000},
            "tool_calls": [
                {"id": "old", "function": {"name": "bash", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "old", "content": "x" * 80_000},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "new-1", "function": {"name": "bash", "arguments": "{}"}},
                {"id": "new-2", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "new-1", "content": "first-current-result"},
        {"role": "tool", "tool_call_id": "new-2", "content": "second-current-result"},
    ]

    _compact_miniswe_history(messages, char_budget=1_000)

    assert messages[2]["content"].startswith("[truncated")
    assert messages[4]["content"] == "first-current-result"
    assert messages[5]["content"] == "second-current-result"


def test_environment_bounds_model_output_but_preserves_exact_raw_output(tmp_path) -> None:
    raw = "HEAD" + ("x" * (MAX_TOOL_OUTPUT_CHARS * 2)) + "TAIL"
    command = (
        f'{sys.executable} -c "print(\'HEAD\' + \'x\' * '
        f'{MAX_TOOL_OUTPUT_CHARS * 2} + \'TAIL\', end=\'\')"'
    )
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=5)

    result = env.execute({"command": command})

    assert len(result["output"]) < len(raw)
    assert result["extra"]["raw_output"] == raw


def test_model_patch_exports_committed_tracked_and_untracked_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = dict(os.environ)
    git_env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=git_env)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, env=git_env)
    subprocess.run(
        ["git", "commit", "-qm", "base"], cwd=repo, check=True, env=git_env
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    output = tmp_path / "artifacts" / "model.patch"
    index_before = (repo / ".git" / "index").read_bytes()

    _write_model_patch(repo, baseline, output)

    patch_text = output.read_text(encoding="utf-8")
    assert "tracked.txt" in patch_text
    assert "+changed" in patch_text
    assert "new.txt" in patch_text
    assert "+new" in patch_text
    assert (repo / ".git" / "index").read_bytes() == index_before


@pytest.mark.skipif(sys.platform != "linux", reason="Linux procfs boundary")
def test_model_shell_cannot_read_provider_key_from_parent_procfs(tmp_path):
    canary = "gt-provider-procfs-canary"
    parent_code = r'''
import os
import subprocess
import sys
from gt_harness.process_boundary import harden_process_secret_boundary

harden_process_secret_boundary()
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
