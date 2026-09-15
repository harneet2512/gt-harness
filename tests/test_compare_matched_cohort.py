"""Matched-cohort comparison: same tasks, GT-on vs GT-off, tokens in tokens."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_matched_cohort import compare
from scripts.freeze_deepswe_gtoff_trials import freeze_slice


def _trial(task: str, passed: bool, inp: int = 1000, out: int = 100,
           cache: int = 500) -> dict:
    return {
        "task_name": task, "passed": passed, "included_in_score": True,
        "n_input_tokens": inp, "n_output_tokens": out,
        "n_cache_tokens": cache, "peak_context_tokens": 900,
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
    """Write a GT-on run tree; ``receipt`` is the literal gt-run.json body."""
    for task_id, row in rows.items():
        agent = root / task_id / "agent"
        agent.mkdir(parents=True)
        (agent / "official-verifier-result.json").write_text(json.dumps({
            "task_id": task_id, "status": "GRADED",
            "reward": 1 if row["solved"] else 0, "solved": row["solved"],
            "failure_class": "graded",
        }))
        if row.get("receipt") is not None:
            (agent / "gt-run.json").write_text(json.dumps(row["receipt"]))


# The real product receipt: top-level token counters, no ``usage`` object.
_REAL_RECEIPTS = {
    "a": {"input_tokens": 800, "output_tokens": 50, "cached_tokens": 600,
          "total_cost": 0.01},
    "b": {"input_tokens": 1500, "output_tokens": 90, "cached_tokens": 1000,
          "total_cost": 0.02},
}
# The legacy shape the comparator used to assume.
_LEGACY_RECEIPTS = {
    "a": {"usage": {"prompt_tokens": 800, "completion_tokens": 50,
                    "prompt_cache_hit_tokens": 600}},
    "b": {"usage": {"prompt_tokens": 1500, "completion_tokens": 90,
                    "prompt_cache_hit_tokens": 1000}},
}


def _two_task_baseline() -> dict:
    return _baseline([
        _trial("a", True, 1000, 100), _trial("a", False, 3000, 300),
        _trial("b", False, 2000, 200), _trial("b", False, 2000, 200),
    ])


def _assert_two_task_result(result: dict) -> None:
    """Assertions shared by the run-tree and attestation sources."""
    assert result["conservation"]["complete"]
    a, b = result["tasks"]
    assert a["task_name"] == "a"
    assert a["gtoff"]["pass_rate"] == 0.5
    assert a["gtoff"]["mean_total_tokens"] == (1100 + 3300) / 2
    # uncached = input - cache + output, per trial, then mean
    assert a["gtoff"]["mean_uncached_tokens"] == (600 + 2800) / 2
    assert a["gton"]["usage"]["total_tokens"] == 850
    assert a["gton"]["usage"]["cached_tokens"] == 600
    assert a["gton"]["usage"]["uncached_tokens"] == 800 - 600 + 50
    assert a["delta_total_tokens"] == 850 - 2200
    assert a["delta_uncached_tokens"] == 250 - 1700
    assert b["gton"]["usage"]["uncached_tokens"] == 1500 - 1000 + 90
    assert b["delta_uncached_tokens"] == 590 - 1700

    cohort = result["cohort"]
    assert cohort["gtoff"]["pass_at_1"] == 0.25        # mean(.5, 0)
    assert cohort["gtoff"]["pass_at_k"] == 0.5        # task a passed once
    assert cohort["gtoff"]["mean_uncached_tokens"] == 1700
    assert cohort["gton"]["solve_rate"] == 0.5
    assert cohort["gton"]["mean_total_tokens"] == (850 + 1590) / 2
    assert cohort["gton"]["mean_uncached_tokens"] == (250 + 590) / 2
    assert cohort["paired"]["n_pairs_with_token_delta"] == 2
    assert cohort["paired"]["mean_delta_uncached_tokens"] == (-1450 + -1110) / 2
    assert cohort["paired"]["per_task_uncached_tokens"]["a"] == {
        "gtoff_mean": 1700, "gton": 250,
    }


def test_same_task_join_and_token_delta(tmp_path):
    _gton_tree(tmp_path, {
        "a": {"solved": True, "receipt": _REAL_RECEIPTS["a"]},
        "b": {"solved": False, "receipt": _REAL_RECEIPTS["b"]},
    })
    result = compare(
        baseline=_two_task_baseline(), task_ids=["a", "b"], gton_root=tmp_path)
    assert result["gton_source"] == "run_tree"
    _assert_two_task_result(result)


def test_legacy_usage_object_still_reads(tmp_path):
    """Older receipts carried a nested ``usage`` dict - keep reading them."""
    _gton_tree(tmp_path, {
        "a": {"solved": True, "receipt": _LEGACY_RECEIPTS["a"]},
        "b": {"solved": False, "receipt": _LEGACY_RECEIPTS["b"]},
    })
    result = compare(
        baseline=_two_task_baseline(), task_ids=["a", "b"], gton_root=tmp_path)
    _assert_two_task_result(result)


def _attestation(rows: list[dict], outcomes: dict) -> dict:
    return {
        "schema": "gt.deepswe_gt_harness_attestation.v1",
        "task_count": len(rows), "product_rows": rows, "outcomes": outcomes,
    }


def test_attestation_source_matches_the_run_tree():
    attestation = _attestation(
        [dict(_REAL_RECEIPTS["a"], task="a", status="COMPLETED", verified=False),
         dict(_REAL_RECEIPTS["b"], task="b", status="COMPLETED", verified=False)],
        {
            "a": {"solved": True, "status": "GRADED", "failure_class": "graded",
                  "graded": True, "reward": 1},
            "b": {"solved": False, "status": "GRADED", "failure_class": "graded",
                  "graded": True, "reward": 0},
        },
    )
    result = compare(baseline=_two_task_baseline(), task_ids=["a", "b"],
                     gton_attestation=attestation)
    assert result["gton_source"] == "attestation"
    _assert_two_task_result(result)


def test_attestation_errored_task_is_ungraded_not_failed():
    attestation = _attestation(
        [dict(_REAL_RECEIPTS["a"], task="a"), dict(_REAL_RECEIPTS["b"], task="b")],
        {
            "a": {"solved": True, "status": "GRADED", "failure_class": "graded",
                  "graded": True, "reward": 1},
            "b": {"solved": False, "status": "ERROR",
                  "failure_class": "provider_failure", "graded": False,
                  "reward": 0},
        },
    )
    result = compare(baseline=_two_task_baseline(), task_ids=["a", "b"],
                     gton_attestation=attestation)
    assert result["conservation"]["gton_ungraded_tasks"] == ["b"]
    assert result["cohort"]["gton"]["n_graded"] == 1
    assert result["cohort"]["gton"]["n_solved"] == 1
    assert result["tasks"][1]["gton"]["solved"] is None
    assert result["tasks"][1]["gton"]["failure_class"] == "provider_failure"
    # usage is still reported for the errored task
    assert result["cohort"]["gton"]["n_tasks_with_usage"] == 2


def test_exactly_one_gton_source_is_required(tmp_path):
    baseline = _two_task_baseline()
    with pytest.raises(ValueError):
        compare(baseline=baseline, task_ids=["a"])
    with pytest.raises(ValueError):
        compare(baseline=baseline, task_ids=["a"], gton_root=tmp_path,
                gton_attestation=_attestation([], {}))


def test_missing_and_ungraded_stay_typed(tmp_path):
    baseline = _baseline([_trial("a", True), _trial("a", False)])
    _gton_tree(tmp_path, {
        "a": {"solved": True, "receipt": None},   # receipt but no usage file
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
    assert a["delta_uncached_tokens"] is None
    assert result["cohort"]["gton"]["n_tasks_with_usage"] == 0
    assert result["cohort"]["gton"]["mean_uncached_tokens"] is None


def test_unknown_cache_leaves_uncached_typed_none(tmp_path):
    _gton_tree(tmp_path, {
        "a": {"solved": True,
              "receipt": {"input_tokens": 800, "output_tokens": 50}},
    })
    result = compare(baseline=_baseline([_trial("a", True)]),
                     task_ids=["a"], gton_root=tmp_path)
    usage = result["tasks"][0]["gton"]["usage"]
    assert usage["total_tokens"] == 850
    assert usage["cached_tokens"] is None
    assert usage["uncached_tokens"] is None      # not 850, not 0
    assert result["tasks"][0]["delta_uncached_tokens"] is None


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
