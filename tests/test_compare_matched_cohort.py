"""Matched-cohort comparison: same tasks, GT-on vs GT-off, tokens in tokens."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_matched_cohort import compare
from scripts.freeze_deepswe_gtoff_trials import freeze_slice


def _trial(task: str, passed: bool, inp: int = 1000, out: int = 100) -> dict:
    return {
        "task_name": task, "passed": passed, "included_in_score": True,
        "n_input_tokens": inp, "n_output_tokens": out,
        "n_cache_tokens": 500, "peak_context_tokens": 900,
        "n_agent_steps": 12,
    }


def _baseline(trials: list[dict]) -> dict:
    tasks = sorted({t["task_name"] for t in trials})
    return {
        "schema": "gt.deepswe_gtoff_trials.v1",
        "model": "deepseek-v4-flash", "harness": "mini-swe-agent",
        "n_tasks": len(tasks), "task_names": tasks, "trials": trials,
    }


def _gton_tree(root: Path, rows: dict[str, dict]) -> None:
    for task_id, row in rows.items():
        agent = root / task_id / "agent"
        agent.mkdir(parents=True)
        (agent / "official-verifier-result.json").write_text(json.dumps({
            "task_id": task_id, "status": "GRADED",
            "reward": 1 if row["solved"] else 0, "solved": row["solved"],
            "failure_class": "graded",
        }))
        if row.get("usage") is not None:
            (agent / "gt-run.json").write_text(json.dumps({"usage": row["usage"]}))


def test_same_task_join_and_token_delta(tmp_path):
    baseline = _baseline([
        _trial("a", True, 1000, 100), _trial("a", False, 3000, 300),
        _trial("b", False, 2000, 200), _trial("b", False, 2000, 200),
    ])
    _gton_tree(tmp_path, {
        "a": {"solved": True, "usage": {"prompt_tokens": 800, "completion_tokens": 50}},
        "b": {"solved": False, "usage": {"prompt_tokens": 1500, "completion_tokens": 90}},
    })
    result = compare(baseline=baseline, task_ids=["a", "b"], gton_root=tmp_path)

    assert result["conservation"]["complete"]
    a, b = result["tasks"]
    assert a["task_name"] == "a"
    assert a["gtoff"]["pass_rate"] == 0.5
    assert a["gtoff"]["mean_total_tokens"] == (1100 + 3300) / 2
    assert a["gton"]["usage"]["total_tokens"] == 850
    assert a["delta_total_tokens"] == 850 - 2200
    cohort = result["cohort"]
    assert cohort["gtoff"]["pass_at_1"] == 0.25        # mean(.5, 0)
    assert cohort["gtoff"]["pass_at_k"] == 0.5        # task a passed once
    assert cohort["gton"]["solve_rate"] == 0.5
    assert cohort["paired"]["n_pairs_with_token_delta"] == 2


def test_missing_and_ungraded_stay_typed(tmp_path):
    baseline = _baseline([_trial("a", True), _trial("a", False)])
    _gton_tree(tmp_path, {
        "a": {"solved": True, "usage": None},   # receipt but no usage file
    })
    result = compare(
        baseline=baseline, task_ids=["a", "missing-task"], gton_root=tmp_path)

    cons = result["conservation"]
    assert not cons["complete"]
    assert cons["missing_gtoff_tasks"] == ["missing-task"]
    assert cons["missing_gton_tasks"] == ["missing-task"]
    a = result["tasks"][0]
    assert a["gton"]["usage"] is None
    assert a["delta_total_tokens"] is None       # never coerced to 0
    assert result["cohort"]["gton"]["n_tasks_with_usage"] == 0


def test_freeze_slice_filters_the_source_model(tmp_path):
    source = tmp_path / "trials.json"
    source.write_text(json.dumps({
        "scope": "deepswe v1.1", "n_trials": 3,
        "rows": [
            {"task_name": "a", "model": "deepseek-v4-flash",
             "harness": "mini-swe-agent", "passed": True},
            {"task_name": "a", "model": "deepseek-v4-pro",
             "harness": "mini-swe-agent", "passed": True},
            {"task_name": "a", "model": "deepseek-v4-flash",
             "harness": "other", "passed": False},
        ],
    }))
    frozen = freeze_slice(source)
    assert frozen["schema"] == "gt.deepswe_gtoff_trials.v1"
    assert len(frozen["trials"]) == 1            # only flash + mini-swe-agent
    assert frozen["trials"][0]["model"] == "deepseek-v4-flash"
    assert frozen["source_sha256"]


def test_frozen_real_slice_covers_the_smoke20(tmp_path):
    """The vendored GT-off slice must contain every smoke20 task."""
    repo = Path(__file__).resolve().parent.parent
    slice_path = repo / "eval" / "deepswe_v4_flash_gtoff_trials.json"
    manifest_path = repo / "eval" / "deepswe_smoke20_v1.json"
    if not slice_path.exists() or not manifest_path.exists():
        pytest.skip("frozen slice or manifest not vendored")
    baseline = json.loads(slice_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gtoff = {str(r["task_name"]) for r in baseline["trials"]}
    assert not [t for t in manifest["task_ids"] if t not in gtoff]
