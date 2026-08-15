from __future__ import annotations

from gt_engine.miniswe_receipt import (
    RECEIPT_DELIVERED,
    RECEIPT_RESOLVED_STATE,
    both_sides_dose_check,
    promote_receipts,
)


def _trajectory(target: str, acted: bool) -> list[dict]:
    """A synthetic trajectory: task-start localization, a view action that may
    target the ranked file, then a submit."""
    msgs = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "ok",
         "extra": {"actions": [{"command": "cat src/mod.py", "tool_call_id": "c1"}]}},
        {"role": "tool", "content": (
            f"<gt-facts>\n{target}:1:compute\n</gt-facts>\n<returncode>0</returncode>"
        )},
        {"role": "assistant", "content": "viewed",
         "extra": {"actions": [{"command": (
             f"grep -rn compute {target}" if acted else "python -m pytest tests/ -q"
         ), "tool_call_id": "c2"}]}},
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "assistant", "content": "submit",
         "extra": {"actions": [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                                "tool_call_id": "c3"}]}},
    ]
    return msgs


def test_promote_delivered_to_acted_and_resolved():
    receipts = [{
        "schema": "gt_receipt.v1", "transition": "delivered", "layer": "miniswe",
        "evidence_type": "localization", "dedup_key": "loc-1", "target": "src/mod.py",
        "action_index": 1, "iteration": 1,
    }]
    msgs = _trajectory("src/mod.py", acted=True)
    promoted, census = promote_receipts(receipts, msgs, terminal_finished=True)
    assert len(promoted) == 1
    r = promoted[0]
    assert r.referenced and r.acted and r.resolved_state
    assert r.transition == RECEIPT_RESOLVED_STATE
    entry = census["localization"]
    assert entry == {"delivered": 1, "referenced": 1, "acted": 1, "resolved_state": 1}


def test_promote_stays_delivered_when_never_used():
    receipts = [{
        "schema": "gt_receipt.v1", "transition": "delivered", "layer": "miniswe",
        "evidence_type": "localization", "dedup_key": "loc-2", "target": "src/other.py",
        "action_index": 1, "iteration": 1,
    }]
    msgs = _trajectory("src/mod.py", acted=False)
    promoted, _ = promote_receipts(receipts, msgs, terminal_finished=False)
    assert promoted[0].transition == RECEIPT_DELIVERED
    assert not promoted[0].acted


def test_dose_check_catches_seal_without_observation_block():
    receipts = [{
        "schema": "gt_receipt.v1", "transition": "delivered", "layer": "miniswe",
        "evidence_type": "localization", "dedup_key": "loc-3", "target": "src/mod.py",
        "action_index": 1, "iteration": 1,
    }]
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "t"},
            {"role": "assistant", "content": "a", "extra": {"actions": [{"command": "x"}]}},
            {"role": "tool", "content": "<returncode>0</returncode>"}]
    ok, issues = both_sides_dose_check(receipts, msgs)
    assert not ok
    assert any("no agent-side observation block" in i for i in issues)
