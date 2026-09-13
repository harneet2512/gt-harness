"""Tests for scripts/smoke_task_metrics.py - per-task metrics extraction.

Covers finding-7 fixes:
  * ``gt_delivered_bytes`` counts delivery events once - ``delivery_prepared``
    is staging, not delivery, so prepared+delivered must not double-count.
  * ``collect_task_rows`` emits one row per task identity; artifact trees
    that carry the same task under several wrapper dirs (run 34715686102
    produced 22 agent/ dirs for 20 tasks) collapse deterministically to
    the most complete copy.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import smoke_task_metrics as stm


def _write_task(root: Path, parent: str, dirname: str, *,
                task_id: str, input_tokens: int = 10,
                events: list | None = None) -> Path:
    task_dir = root / parent / dirname
    agent = task_dir / "agent"
    agent.mkdir(parents=True)
    (agent / "gt-run.json").write_text(json.dumps({
        "task_id": task_id,
        "status": "success",
        "input_tokens": input_tokens,
        "cached_tokens": 0,
        "output_tokens": 5,
        "provider_calls": 1,
        "provider_completed_calls": 1,
        "provider_failed_calls": 0,
        "delivery_count": 1,
        "treatment_receipt": {"status": "success", "verified": True,
                              "gt_mode": "on"},
    }), encoding="utf-8")
    if events is not None:
        state = agent / "gt-state" / "state-1"
        state.mkdir(parents=True)
        (state / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8")
    return task_dir


def test_delivered_bytes_not_double_counted_with_prepared(tmp_path):
    """Preparation stages the same rendered_bytes as delivery; the delivered
    total must count each byte once, at the delivery event of record."""
    task_dir = _write_task(
        tmp_path, "tasks", "alpha-task__t1", task_id="alpha-task",
        events=[
            {"event": "delivery_prepared", "rendered_bytes": 120},
            {"event": "evidence_delivery", "rendered_bytes": 120},
            {"event": "context_addition_delivery", "rendered_bytes": 40},
            {"event": "delivery_refused", "rendered_bytes": 30},
        ])
    row = stm.task_metrics(task_dir)
    # old behavior summed prepared+delivered: 120+120+40 = 280
    assert row["gt_delivered_bytes"] == 160
    assert row["gt_prepared_bytes"] == 120
    assert row["gt_refused_bytes"] == 30


def test_prepared_only_bytes_do_not_count_as_delivered(tmp_path):
    task_dir = _write_task(
        tmp_path, "tasks", "alpha-task__t1", task_id="alpha-task",
        events=[{"event": "delivery_prepared", "rendered_bytes": 77}])
    row = stm.task_metrics(task_dir)
    assert row["gt_delivered_bytes"] == 0
    assert row["gt_prepared_bytes"] == 77


def test_collect_task_rows_dedupes_same_task_across_dirs(tmp_path):
    """22 agent/ dirs for 20 tasks -> one row per task identity, keeping
    the most complete copy; equal completeness breaks ties on the
    lexically-first task dir (deterministic, no timestamps)."""
    events = [{"event": "evidence_delivery", "rendered_bytes": 100}]
    # canonical wrapper, complete journal, distinguishes itself by tokens
    _write_task(tmp_path, "gt-harness-deepswe20-task-7-1", "alpha-task__t1",
                task_id="alpha-task", input_tokens=111, events=events)
    # duplicate of the same task under a second wrapper (same completeness)
    _write_task(tmp_path, "task7", "alpha-task__t1",
                task_id="alpha-task", input_tokens=222, events=events)
    # third copy with no journal at all -> strictly less complete
    _write_task(tmp_path, "zz-copy", "alpha-task__t1",
                task_id="alpha-task", input_tokens=333, events=None)
    _write_task(tmp_path, "gt-harness-deepswe20-task-8-1", "beta-task__t1",
                task_id="beta-task", input_tokens=50, events=events)

    rows = stm.collect_task_rows(tmp_path)

    assert [r["task"] for r in rows] == ["alpha-task", "beta-task"]
    alpha = rows[0]
    # journal-carrying copies beat the journal-less one; the
    # lexically-first wrapper wins the remaining tie
    assert alpha["input_tokens"] == 111
    assert alpha["gt_delivered_bytes"] == 100


def test_collect_task_rows_uses_task_id_not_truncated_dirname(tmp_path):
    """Dir names truncate the task id (``<task>__XXXXX``); the canonical
    identity comes from gt-run.json task_id."""
    _write_task(tmp_path, "w1",
                "claude-code-by-agents-recursive__t1",
                task_id="claude-code-by-agents-recursive-delegation",
                events=[])
    rows = stm.collect_task_rows(tmp_path)
    assert [r["task"] for r in rows] == [
        "claude-code-by-agents-recursive-delegation"]


def test_collect_task_rows_single_copy_unchanged(tmp_path):
    events = [{"event": "evidence_delivery", "rendered_bytes": 5}]
    _write_task(tmp_path, "w", "solo-task__t1", task_id="solo-task",
                events=events)
    rows = stm.collect_task_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["task"] == "solo-task"
    assert rows[0]["gt_delivered_bytes"] == 5
