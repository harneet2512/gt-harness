"""Bind Pier/Harbor output to the official benchmark-result receipt."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
from pathlib import Path
from typing import Any

_SHA40 = re.compile(r"[0-9a-f]{40}")
_INSUFFICIENT_BALANCE = re.compile(r"insufficient[ _-]*balance|http\s*402", re.I)
_PROVIDER_EXCEPTIONS = frozenset(
    {
        "APIConnectionError",
        "APIError",
        "ApiRateLimitError",
        "AuthenticationError",
        "BadRequestError",
        "RateLimitError",
    }
)
_EXIT_137 = re.compile(r"(?:exit(?: code)?|return(?: code)?)\s*[:=]?\s*137\b", re.I)
def _valid_self_digest(payload: dict[str, Any], field: str) -> bool:
    supplied = payload.get(field)
    unsigned = dict(payload)
    unsigned.pop(field, None)
    calculated = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return isinstance(supplied, str) and hmac.compare_digest(supplied, calculated)

def conservative_outcomes(
    task_ids: list[str], official_results: dict[str, dict[str, Any]]
) -> dict[str, dict[str, object]]:
    """Represent every planned task exactly once without inventing a grade."""
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate_planned_task")
    unexpected = sorted(set(official_results) - set(task_ids))
    if unexpected:
        raise ValueError(f"unexpected_official_result:{unexpected[0]}")
    outcomes: dict[str, dict[str, object]] = {}
    for task_id in task_ids:
        result = official_results.get(task_id)
        if result is None:
            outcomes[task_id] = {
                "status": "ERROR",
                "graded": False,
                "reward": None,
                "solved": False,
                "failure_class": "missing_result",
                "error_code": "official_verifier_result_missing",
            }
            continue
        reward = result.get("reward")
        graded = (
            result.get("status") == "GRADED"
            and not isinstance(reward, bool)
            and isinstance(reward, (int, float))
            and math.isfinite(reward)
            and reward in (0, 1)
        )
        outcomes[task_id] = {
            "status": "GRADED" if graded else "ERROR",
            "graded": graded,
            "reward": int(reward) if graded else None,
            "solved": bool(graded and reward in (1, 1.0)),
            "failure_class": "graded" if graded else str(result.get("failure_class") or "malformed_result"),
            "error_code": "" if graded else str(result.get("error_code") or "official_verifier_result_malformed"),
        }
    return outcomes


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _reward(payload: dict[str, Any]) -> int | None:
    verifier_result = payload.get("verifier_result")
    verifier_result = verifier_result if isinstance(verifier_result, dict) else {}
    verifier_rewards = verifier_result.get("rewards")
    verifier_rewards = verifier_rewards if isinstance(verifier_rewards, dict) else {}
    direct_rewards = payload.get("rewards")
    direct_rewards = direct_rewards if isinstance(direct_rewards, dict) else {}
    candidates: list[Any] = [
        verifier_rewards.get("reward"),
        direct_rewards.get("reward"),
        payload.get("reward"),
    ]
    stats = payload.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    evals = stats.get("evals") or {}
    if isinstance(evals, dict):
        for evaluation in evals.values():
            if not isinstance(evaluation, dict):
                continue
            metrics = evaluation.get("metrics") or []
            if isinstance(metrics, list):
                candidates.extend(
                    metric.get("reward")
                    for metric in metrics
                    if isinstance(metric, dict)
                )
    rewards = {
        int(value)
        for value in candidates
        if not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value in (0, 1)
    }
    return next(iter(rewards)) if len(rewards) == 1 else None


def _runner_results(
    root: Path,
) -> tuple[Path | None, dict[str, Any], dict[str, Any] | None]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.rglob("result.json")):
        if "agent" in path.parts or "verifier" in path.parts:
            continue
        payload = _read_json(path)
        if payload is not None:
            rows.append((path, payload))
    aggregates = [row for row in rows if "stats" in row[1] and "n_total_trials" in row[1]]
    trials = [row for row in rows if row[1].get("task_name") and row[1].get("trial_name")]
    if not rows:
        return None, {}, None
    if len(aggregates) == 1:
        canonical = aggregates[0]
    elif len(rows) == 1:
        canonical = rows[0]
    else:
        raise ValueError(
            f"expected exactly one canonical runner result, found {len(aggregates)} "
            f"among {len(rows)} result files"
        )
    return canonical[0], canonical[1], trials[0][1] if len(trials) == 1 else None


def _failure_class(
    trial: dict[str, Any] | None,
    *,
    runner_result_present: bool,
    resource_evidence: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not runner_result_present:
        return "setup_failure", "runner_result_missing"
    exception = (trial or {}).get("exception_info") or {}
    exception = exception if isinstance(exception, dict) else {}
    exception_type = str(exception.get("exception_type") or "")
    message = str(exception.get("exception_message") or "")
    if _INSUFFICIENT_BALANCE.search(message):
        return "provider_billing_failure", "provider_insufficient_balance"
    if exception_type in _PROVIDER_EXCEPTIONS:
        return "provider_failure", "provider_request_failed"
    if _EXIT_137.search(message):
        evidence = resource_evidence or {}
        code = str(evidence.get("error_code") or "")
        if evidence.get("memory_evidence") is True and code == "GT_AGENT_CGROUP_OOM":
            return "resource_exhaustion", "agent_cgroup_oom"
        return "process_signal_failure", "process_exit_137_unattributed"
    if exception_type:
        return "setup_failure", "runner_setup_or_execution_failed"
    return "missing_verifier", "official_verifier_missing"


def _resource_evidence(
    root: Path, *, task_id: str, product_source_sha: str
) -> tuple[Path | None, dict[str, Any] | None]:
    valid: list[tuple[Path, dict[str, Any]]] = []
    attestation_key = os.environ.get("GT_RESOURCE_ATTESTATION_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", attestation_key):
        return None, None
    for path in sorted(root.rglob("agent-resource.json")):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != "gt.agent_resource.v1":
            continue
        before = payload.get("cgroup_before")
        after = payload.get("cgroup_after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        counters = (
            before.get("oom"), after.get("oom"),
            before.get("oom_kill"), after.get("oom_kill"),
        )
        if any(type(value) is not int for value in counters):
            continue
        oom_delta = max(0, after["oom"] - before["oom"])
        oom_kill_delta = max(0, after["oom_kill"] - before["oom_kill"])
        unsigned = dict(payload)
        supplied_hmac = unsigned.pop("attestation_hmac_sha256", None)
        unsigned.pop("evidence_sha256", None)
        expected_hmac = hmac.new(
            bytes.fromhex(attestation_key),
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if (
            _valid_self_digest(payload, "evidence_sha256")
            and isinstance(supplied_hmac, str)
            and hmac.compare_digest(supplied_hmac, expected_hmac)
            and payload.get("task_id") == task_id
            and payload.get("product_source_sha") == product_source_sha
            and payload.get("attestation_scope") == "host_agent_adapter"
            and payload.get("exit_code") == 137
            and payload.get("error_code") == "GT_AGENT_CGROUP_OOM"
            and payload.get("memory_evidence") is True
            and payload.get("cgroup_oom_delta") == oom_delta
            and payload.get("cgroup_oom_kill_delta") == oom_kill_delta
            and (oom_delta > 0 or oom_kill_delta > 0)
        ):
            valid.append((path, payload))
    return valid[0] if len(valid) == 1 else (None, None)


def _target_agent_dir(
    root: Path, *, task_id: str, product_paths: list[Path], trial: dict[str, Any] | None
) -> Path:
    if len(product_paths) == 1:
        return product_paths[0].parent
    if trial is not None:
        trial_name = str(trial.get("trial_name") or "")
        matches = [path for path in root.rglob(trial_name) if path.is_dir()]
        if len(matches) == 1:
            target = matches[0] / "agent"
            target.mkdir(parents=True, exist_ok=True)
            return target
    target = root / task_id / "agent"
    target.mkdir(parents=True, exist_ok=True)
    return target


def standardize_result(
    *, root: Path, suite: str, task_id: str, source_sha: str
) -> dict[str, object]:
    if suite not in {"terminal-bench-2", "deepswe"}:
        raise ValueError(f"unsupported runner suite: {suite}")
    if not task_id.strip():
        raise ValueError("task ID must be nonempty")
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("product source SHA must be exactly 40 lowercase hex characters")

    product_paths = sorted(root.rglob("agent/gt-run.json"))
    if len(product_paths) > 1:
        raise ValueError(f"expected at most one GT product receipt, found {len(product_paths)}")
    product = _read_json(product_paths[0]) if product_paths else None
    if product is not None and (
        product.get("schema") != "gt.run_receipt.v1"
        or str(product.get("task_id") or "") != task_id
        or product.get("product_source_sha") != source_sha
    ):
        raise ValueError("GT product receipt identity does not match runner task")

    result_path, runner_result, trial = _runner_results(root)
    if trial is not None:
        runner_task = str(trial.get("task_name") or "").rsplit("/", 1)[-1]
        if runner_task != task_id:
            raise ValueError("runner task identity does not match requested task")
    resource_path, resource_evidence = _resource_evidence(
        root, task_id=task_id, product_source_sha=source_sha
    )
    result_bytes = result_path.read_bytes() if result_path is not None else None
    aggregate_reward = _reward(runner_result)
    trial_reward = _reward(trial or {})
    reward = (
        aggregate_reward
        if aggregate_reward is not None and aggregate_reward == trial_reward
        else None
    )
    failure_class, error_code = (
        ("graded", "")
        if reward is not None
        else _failure_class(
            trial,
            runner_result_present=result_path is not None,
            resource_evidence=resource_evidence,
        )
    )
    receipt: dict[str, object] = {
        "schema": "gt.official_verifier_result.v1",
        "benchmark_suite": suite,
        "task_id": task_id,
        "product_source_sha": source_sha,
        "status": "GRADED" if reward is not None else "ERROR",
        "reward": reward,
        "solved": reward == 1 if reward is not None else None,
        "failure_class": failure_class,
        "error_code": error_code,
        "product_receipt_present": product is not None,
        "runner_result_sha256": (
            hashlib.sha256(result_bytes).hexdigest() if result_bytes is not None else None
        ),
        "runner_result_path": (
            str(result_path.relative_to(root)).replace("\\", "/")
            if result_path is not None
            else None
        ),
        "resource_evidence_path": (
            str(resource_path.relative_to(root)).replace("\\", "/")
            if resource_path is not None
            else None
        ),
        "resource_evidence_sha256": (
            hashlib.sha256(resource_path.read_bytes()).hexdigest()
            if resource_path is not None
            else None
        ),
    }
    target = _target_agent_dir(
        root, task_id=task_id, product_paths=product_paths, trial=trial
    ) / "official-verifier-result.json"
    temporary = target.with_suffix(f"{target.suffix}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(receipt, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", choices=("terminal-bench-2", "deepswe"), required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source-sha", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.task_id.strip() or not _SHA40.fullmatch(args.source_sha):
        print("benchmark identity invalid; official verifier artifact not written")
        return 2
    try:
        receipt = standardize_result(
            root=args.root,
            suite=args.suite,
            task_id=args.task_id,
            source_sha=args.source_sha,
        )
    except Exception as exc:  # noqa: BLE001 - task-level failure must be durable
        product_receipt_present = any(args.root.rglob("agent/gt-run.json"))
        receipt = {
            "schema": "gt.official_verifier_result.v1",
            "benchmark_suite": args.suite,
            "task_id": args.task_id,
            "product_source_sha": args.source_sha,
            "status": "ERROR",
            "reward": None,
            "solved": None,
            "failure_class": "artifact_failure",
            "error_code": "official_verifier_construction_failed",
            "product_receipt_present": product_receipt_present,
            "runner_result_sha256": None,
            "runner_result_path": None,
            "resource_evidence_path": None,
            "resource_evidence_sha256": None,
            "construction_error_type": type(exc).__name__,
        }
        target = args.root / args.task_id / "agent" / "official-verifier-result.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp.{os.getpid()}")
        temporary.write_text(
            json.dumps(receipt, allow_nan=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "GRADED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
