from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.phase2_single_witness import (
    analyze_single_witness,
    build_baseline_receipt,
)

TASK = "fix-code-vulnerability"
CHECKSUM = "13c4e35adbd7e55707f273aabd8f4108672f0fb790c96af543fbcbdcc977b119"
FINGERPRINT = "fp_a18b46594c_prod0820_fp8_kvcache_20260402"


def _trajectory(*, gt: bool, calls: int, fingerprint: str = FINGERPRINT) -> dict:
    first = {
        "role": "assistant",
        "extra": {
            "actions": [{"command": "grep -rn vulnerable ."}],
            "response": {"system_fingerprint": fingerprint},
        },
    }
    messages = [
        {"role": "system", "content": "system\nGT advisory" if gt else "system"},
        {"role": "user", "content": "task"},
        first,
        {"role": "tool", "content": "<output>hit</output>", "extra": {"raw_output": "hit"}},
        {
            "role": "assistant",
            "extra": {"actions": [{"command": "sed -i 's/a/b/' bottle.py"}]},
        },
    ]
    return {
        "info": {
            "mini_version": "2.2.8",
            "model_stats": {"api_calls": calls},
            "config": {
                "agent": {"step_limit": 100, "cost_limit": 3.0},
                "model": {
                    "model_name": "openai/deepseek-v4-flash",
                    "model_kwargs": {"temperature": 1.0},
                },
            },
            "gt": gt,
        },
        "messages": messages,
        "trajectory_format": "mini-swe-agent-1.1",
    }


def _baseline_root(tmp_path: Path) -> Path:
    root = tmp_path / "baseline"
    root.mkdir()
    trajectory = _trajectory(gt=False, calls=33)
    (root / f"{TASK}_trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )
    merged = {
        "trial_results": [
            {
                "id": "baseline-trial",
                "task_name": TASK,
                "task_checksum": CHECKSUM,
                "task_id": {
                    "git_commit_id": "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
                },
                "config": {
                    "agent_timeout_multiplier": 1.0,
                    "agent": {
                        "name": "eval.miniswe_agent:MiniSweAgent",
                        "model_name": "deepseek-v4-flash",
                    },
                },
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        ]
    }
    (root / "merged.json").write_text(json.dumps(merged), encoding="utf-8")
    (root / "SUMMARY.md").write_text(
        "Branch/commit: `miniswe-gtoff-20260731` @ `62c06c1`\n"
        "GHA run: `30665246698`\n",
        encoding="utf-8",
    )
    return root


def _candidate_root(tmp_path: Path, *, checksum: str = CHECKSUM) -> Path:
    root = tmp_path / "candidate"
    trial = root / f"{TASK}__candidate"
    (trial / "agent").mkdir(parents=True)
    trajectory = _trajectory(gt=True, calls=20)
    (trial / "agent" / "miniswe_trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )
    result = {
        "id": "candidate-trial",
        "task_name": TASK,
        "task_checksum": checksum,
        "task_id": {
            "git_commit_id": "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
        },
        "config": {
            "agent_timeout_multiplier": 1.0,
            "agent": {
                "name": "eval.miniswe_agent:MiniSweGtAgent",
                "model_name": "deepseek-v4-flash",
            },
        },
        "verifier_result": {"rewards": {"reward": 1.0}},
    }
    (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return root


def test_imports_exact_local_baseline_and_binds_bytes(tmp_path: Path) -> None:
    root = _baseline_root(tmp_path)
    receipt = build_baseline_receipt(root, TASK)
    assert receipt["schema"] == "gt.phase2.single_witness_baseline.v1"
    assert receipt["task_checksum"] == CHECKSUM
    assert receipt["mini_swe_version"] == "2.2.8"
    assert receipt["system_fingerprint"] == FINGERPRINT
    assert receipt["task_prompt_sha256"]
    assert receipt["system_prompt_sha256"]
    assert receipt["reward"] == 1.0
    assert receipt["gt_enabled"] is False
    assert receipt["baseline_github_actions_run_id"] == "30665246698"
    assert receipt["baseline_harness_commit"] == "62c06c1"
    assert receipt["trajectory_sha256"] == hashlib.sha256(
        (root / f"{TASK}_trajectory.json").read_bytes()
    ).hexdigest()


def test_single_witness_is_descriptive_and_manifest_matched(tmp_path: Path) -> None:
    baseline = build_baseline_receipt(_baseline_root(tmp_path), TASK)
    result = analyze_single_witness(baseline, _candidate_root(tmp_path))
    assert result["schema"] == "gt.phase2.single_witness_analysis.v1"
    assert result["manifest_identical"] is True
    assert result["matched_tasks"] == 1
    assert result["candidate"]["reward"] == 1.0
    assert (
        result["candidate"]["system_prompt_sha256"]
        != result["baseline"]["system_prompt_sha256"]
    )
    assert result["deltas"]["api_calls"] == -13
    assert result["inferential_claim"] is False
    assert "mcnemar" not in result
    assert "confidence_interval" not in json.dumps(result)


def test_single_witness_rejects_task_checksum_drift(tmp_path: Path) -> None:
    baseline = build_baseline_receipt(_baseline_root(tmp_path), TASK)
    with pytest.raises(ValueError, match="task_checksum"):
        analyze_single_witness(baseline, _candidate_root(tmp_path, checksum="0" * 64))


def test_single_witness_rejects_provider_fingerprint_drift(tmp_path: Path) -> None:
    baseline = build_baseline_receipt(_baseline_root(tmp_path), TASK)
    root = _candidate_root(tmp_path)
    trajectory_path = next(root.glob("*/agent/miniswe_trajectory.json"))
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["messages"][2]["extra"]["response"]["system_fingerprint"] = "different"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")
    with pytest.raises(ValueError, match="system_fingerprint"):
        analyze_single_witness(baseline, root)
