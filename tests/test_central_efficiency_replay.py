from __future__ import annotations

import json

from scripts.central_efficiency_replay import replay_run


def _write_trial(root, task, *, instruction, command, declared, partial, headroom):
    agent = root / f"{task}__trial" / "agent"
    agent.mkdir(parents=True)
    (agent / "miniswe_trajectory.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": instruction}]}),
        encoding="utf-8",
    )
    (agent / "central_receipt.json").write_text(
        json.dumps(
            {
                "features": {
                    "validation_log": [{"action": 1, "command": command}],
                    "receipts": [
                        {
                            "feature_id": "covering_red",
                            "action": 1,
                            "boundary": "test_result",
                            "model_visible": True,
                        },
                        {
                            "feature_id": "submit_refusal",
                            "action": 1,
                            "boundary": "test_result",
                            "model_visible": True,
                        },
                    ],
                },
                "metrics": {
                    "completion_plan_status": "partial" if partial else "complete",
                    "completion_probe_execs": 3,
                },
                "model_call_contexts": [
                    {
                        "request_budget": {
                            "context_limit_tokens": 1_048_576,
                            "counted_tokens": 1,
                            "conservative_tokens": 1,
                            "effective_tokens": 1,
                            "hard_prompt_limit": 943_718,
                            "remaining_tokens": headroom,
                            "counter_source": "fixture",
                        }
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_archived_replay_projects_custom_failures_private_and_partial_probes_zero(tmp_path):
    _write_trial(
        tmp_path,
        "custom",
        instruction="Demonstrate the behavior.",
        command="python3 /tmp/test_single.py",
        declared=False,
        partial=True,
        headroom=400_000,
    )
    _write_trial(
        tmp_path,
        "declared",
        instruction="Run `pytest -q`.",
        command="pytest -q",
        declared=True,
        partial=False,
        headroom=400_000,
    )

    result = replay_run(tmp_path)

    assert result["invalid_visible_failure_receipts"] == 2
    assert result["invalid_visible_failure_actions"] == 1
    assert result["projected_partial_completion_probe_execs"] == 0
    assert result["avoided_partial_completion_probe_execs"] == 3
    assert result["projected_compaction_epochs"] == 0
    assert result["declared_visible_failure_receipts_preserved"] == 2
    assert all(
        row["minimum_provider_headroom_tokens"] == 400_000
        for row in result["tasks"].values()
    )
    assert all(
        row["provider_budget_evidence"] == "recorded_transformed_request"
        for row in result["tasks"].values()
    )


def test_archived_replay_preserves_unique_observations_below_budget_epoch(tmp_path):
    agent = tmp_path / "large-read__trial" / "agent"
    agent.mkdir(parents=True)
    messages = [{"role": "user", "content": "Inspect the logs."}]
    for index in range(4):
        tool_id = f"call-{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"reasoning-{index}",
                    "extra": {
                        "actions": [
                            {"command": f"cat huge{index}.log", "tool_call_id": tool_id}
                        ]
                    },
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(index) * 30_000,
                },
            ]
        )
    (agent / "miniswe_trajectory.json").write_text(
        json.dumps({"messages": messages}), encoding="utf-8"
    )
    (agent / "central_receipt.json").write_text(
        json.dumps({"features": {}, "metrics": {}, "model_call_contexts": []}),
        encoding="utf-8",
    )

    result = replay_run(tmp_path)
    replay = result["tasks"]["large-read"]["provider_view_replay"]

    # The fourth observation has no following provider call and therefore is
    # correctly outside the replay exposure window.
    assert replay["model_calls_replayed"] == 4
    assert replay["bounded_unique_observations"] == 0
    assert replay["projected_provider_view_chars_avoided"] == 0
    assert replay["assistant_reasoning_chars_removed"] == 0
    assert result["projected_provider_view_chars_avoided"] == 0
    assert result["provider_view_assistant_reasoning_chars_removed"] == 0
    assert result["provider_view_compaction_deferrals"] >= 0
