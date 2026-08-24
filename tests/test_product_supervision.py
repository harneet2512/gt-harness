from __future__ import annotations

import json
from pathlib import Path

from gt_harness.supervision import finalize_nonterminal_receipt


def test_supervisor_converts_running_checkpoint_to_evidenced_error(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "gt-run.json"
    trajectory_path = tmp_path / "gt-run.trajectory.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "gt.run_receipt.v1",
                "status": "RUNNING",
                "started": "2026-08-24T01:00:00Z",
                "completed": None,
                "duration_ms": 100.0,
                "provider_calls": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "cached_tokens": 3,
                "transcript": [{"role": "assistant", "content": "preserve me"}],
                "treatment_receipt": {
                    "treatment_status": "ACTIVE",
                    "errors": [],
                    "provider_calls": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    trajectory_path.write_text(
        json.dumps(
            {
                "info": {"model_stats": {"api_calls": 2}},
                "messages": [
                    {
                        "role": "assistant",
                        "extra": {
                            "response": {
                                "usage": {
                                    "prompt_tokens": 20,
                                    "completion_tokens": 4,
                                    "prompt_tokens_details": {"cached_tokens": 6},
                                }
                            }
                        },
                    },
                    {"role": "tool", "content": "observed"},
                ],
            }
        ),
        encoding="utf-8",
    )

    changed = finalize_nonterminal_receipt(
        receipt_path,
        trajectory_path=trajectory_path,
        return_code=137,
        supervisor="harbor_adapter",
    )

    assert changed is True
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "ERROR"
    assert receipt["error_type"] == "ProductProcessExitError"
    assert receipt["error"] == "SUPERVISOR:product_process_exit_137"
    assert receipt["provider_calls"] == 2
    assert receipt["input_tokens"] == 20
    assert receipt["output_tokens"] == 4
    assert receipt["cached_tokens"] == 6
    assert receipt["transcript"][0]["content"] == "preserve me"
    assert receipt["transcript"][-1]["type"] == "supervisor_error"
    assert receipt["treatment_receipt"]["treatment_status"] == "FAILED"
    assert receipt["supervisor_finalization"]["return_code"] == 137


def test_supervisor_never_rewrites_a_terminal_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "gt-run.json"
    original = {"schema": "gt.run_receipt.v1", "status": "COMPLETED"}
    receipt_path.write_text(json.dumps(original), encoding="utf-8")

    assert (
        finalize_nonterminal_receipt(
            receipt_path,
            trajectory_path=tmp_path / "missing.json",
            return_code=137,
            supervisor="harbor_adapter",
        )
        is False
    )
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == original
