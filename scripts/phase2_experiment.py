"""Provider-free preparation and analysis for the Phase II six-arm experiment."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ARMS = (
    "stock_raw",
    "prepend_only",
    "action_bound_augmentation",
    "certified_replacement",
    "typed_interface",
    "proactive_map_embedding_control",
)

_TEMPLATE_SENTINEL = "REQUIRED_AT_AUTHORIZED_EXECUTION"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_ARM_BINDING_KEYS = {
    "schema",
    "runner",
    "runner_sha256",
    "agent",
    "mode",
    "provider_calls_per_iteration",
}
_ARM_AGENTS = {
    arm: (
        "eval.miniswe_agent:MiniSweAgent"
        if arm == "stock_raw"
        else "eval.miniswe_agent:MiniSweGtAgent"
    )
    for arm in ARMS
}
HAR9_CLOSEOUT_SCHEMA = "gt.har9.closeout_receipt.v1"
HAR9_REQUIRED_UNITS = frozenset(
    {
        "har5",
        "har6",
        "har7",
        "har8",
        "har9",
        "har10",
        "har11",
        "har12",
        "har14",
        "har29",
        "har30",
        "har35",
        "har36",
        "har37",
        "har38",
        "har41",
        "har42",
        "har48",
        "har59",
        "har60",
        "har61",
    }
)
HAR9_ASSEMBLY_INPUT_SCHEMA = "gt.har9.assembly_inputs.v1"
HAR9_ASSEMBLY_INPUTS = tuple(sorted(HAR9_REQUIRED_UNITS))
HAR9_GROUNDTRUTH_UNITS = frozenset({"har42", "har60", "har61"})
HAR9_REPOSITORY_NAMES = frozenset({"harness", "groundtruth"})


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def build_closeout_receipt(
    *,
    harness_head: str,
    groundtruth_head: str,
    unit_heads: dict[str, str],
    input_receipts: dict[str, str],
    environment_sha256: str,
    unit_repositories: dict[str, str] | None = None,
    provider_calls: int = 0,
    benchmark_runs: int = 0,
    allow_provisional: bool = True,
) -> dict[str, Any]:
    """Assemble a provider-free closeout without authorizing a benchmark."""
    if not _SHA1_RE.fullmatch(harness_head) or not _SHA1_RE.fullmatch(groundtruth_head):
        raise ValueError("closeout repository heads must be full commit SHAs")
    if not unit_heads or any(
        not isinstance(name, str)
        or not name
        or not _SHA1_RE.fullmatch(value)
        for name, value in unit_heads.items()
    ):
        raise ValueError("closeout unit heads must be named full commit SHAs")
    if not set(unit_heads) <= HAR9_REQUIRED_UNITS:
        raise ValueError("closeout unit heads contain an unknown unit")
    if provider_calls != 0 or benchmark_runs != 0:
        raise ValueError("closeout must remain provider-free and benchmark-free")
    if not isinstance(input_receipts, dict) or not input_receipts or any(
        not isinstance(name, str)
        or not name
        or not _SHA256_RE.fullmatch(value)
        for name, value in input_receipts.items()
    ):
        raise ValueError("closeout inputs must be named SHA-256 receipt digests")
    if not set(input_receipts) <= HAR9_REQUIRED_UNITS:
        raise ValueError("closeout inputs contain an unknown unit")
    repositories = unit_repositories or {
        name: ("groundtruth" if name in HAR9_GROUNDTRUTH_UNITS else "harness")
        for name in unit_heads
    }
    if set(repositories) != set(unit_heads) or any(
        not isinstance(name, str) or repo not in HAR9_REPOSITORY_NAMES
        for name, repo in repositories.items()
    ):
        raise ValueError(
            "closeout unit repositories must bind every unit to harness or groundtruth"
        )
    if not isinstance(environment_sha256, str) or (
        environment_sha256 != "UNVERIFIED" and not _SHA256_RE.fullmatch(environment_sha256)
    ):
        raise ValueError("closeout environment identity must be a SHA-256 digest")
    if not allow_provisional:
        missing = sorted(HAR9_REQUIRED_UNITS - set(unit_heads))
        if missing:
            raise ValueError(f"terminal closeout missing unit heads: {', '.join(missing)}")
        missing_receipts = sorted(HAR9_REQUIRED_UNITS - set(input_receipts))
        if missing_receipts:
            raise ValueError(
                "terminal closeout missing input receipts: "
                + ", ".join(missing_receipts)
            )
        if environment_sha256 == "UNVERIFIED":
            raise ValueError("terminal closeout requires a concrete environment digest")
    payload: dict[str, Any] = {
        "schema": HAR9_CLOSEOUT_SCHEMA,
        "harness_head": harness_head,
        "groundtruth_head": groundtruth_head,
        "unit_heads": dict(sorted(unit_heads.items())),
        "unit_repositories": dict(sorted(repositories.items())),
        "input_receipts": dict(sorted(input_receipts.items())),
        "environment_sha256": environment_sha256,
        "results": {"provider_calls": 0, "benchmark_runs": 0},
        "authorization": {
            "status": "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL",
            "benchmark_ready": False,
        },
    }
    payload["bundle_sha256"] = _canonical_sha256(payload)
    return payload


def verify_closeout_receipt(
    receipt: Any,
    *,
    expected_harness_head: str | None = None,
    expected_groundtruth_head: str | None = None,
    expected_unit_heads: dict[str, str] | None = None,
    expected_input_receipts: dict[str, str] | None = None,
    expected_environment_sha256: str | None = None,
    require_terminal: bool = False,
) -> bool:
    """Verify persisted closeout bytes and every identity before assembly."""
    if not isinstance(receipt, dict) or receipt.get("schema") != HAR9_CLOSEOUT_SCHEMA:
        return False
    required = {
        "schema",
        "harness_head",
        "groundtruth_head",
        "unit_heads",
        "unit_repositories",
        "input_receipts",
        "environment_sha256",
        "results",
        "authorization",
        "bundle_sha256",
    }
    if set(receipt) != required:
        return False
    if not _SHA1_RE.fullmatch(receipt["harness_head"]) or not _SHA1_RE.fullmatch(
        receipt["groundtruth_head"]
    ):
        return False
    unit_heads = receipt["unit_heads"]
    unit_repositories = receipt["unit_repositories"]
    input_receipts = receipt["input_receipts"]
    if not isinstance(unit_heads, dict) or not set(unit_heads) <= HAR9_REQUIRED_UNITS or any(
        not isinstance(k, str) or not _SHA1_RE.fullmatch(v)
        for k, v in unit_heads.items()
    ):
        return False
    if (
        not isinstance(unit_repositories, dict)
        or set(unit_repositories) != set(unit_heads)
        or any(repo not in HAR9_REPOSITORY_NAMES for repo in unit_repositories.values())
        or unit_repositories
        != {
            name: ("groundtruth" if name in HAR9_GROUNDTRUTH_UNITS else "harness")
            for name in unit_heads
        }
    ):
        return False
    if (
        not isinstance(input_receipts, dict)
        or not set(input_receipts) <= HAR9_REQUIRED_UNITS
        or any(
            not isinstance(k, str) or not _SHA256_RE.fullmatch(v)
            for k, v in input_receipts.items()
        )
    ):
        return False
    environment = receipt["environment_sha256"]
    if environment != "UNVERIFIED" and not _SHA256_RE.fullmatch(environment):
        return False
    results = receipt["results"]
    authorization = receipt["authorization"]
    if results != {"provider_calls": 0, "benchmark_runs": 0} or not isinstance(authorization, dict):
        return False
    if (
        authorization.get("benchmark_ready") is not False
        or authorization.get("status") != "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL"
    ):
        return False
    if (
        expected_harness_head is not None
        and receipt["harness_head"] != expected_harness_head
    ):
        return False
    if (
        expected_groundtruth_head is not None
        and receipt["groundtruth_head"] != expected_groundtruth_head
    ):
        return False
    if expected_unit_heads is not None and unit_heads != dict(
        sorted(expected_unit_heads.items())
    ):
        return False
    if expected_input_receipts is not None and input_receipts != dict(
        sorted(expected_input_receipts.items())
    ):
        return False
    if expected_environment_sha256 is not None and environment != expected_environment_sha256:
        return False
    if require_terminal and (
        set(unit_heads) != HAR9_REQUIRED_UNITS
        or set(unit_repositories) != HAR9_REQUIRED_UNITS
        or set(input_receipts) != HAR9_REQUIRED_UNITS
        or environment == "UNVERIFIED"
    ):
        return False
    unsigned = dict(receipt)
    unsigned.pop("bundle_sha256", None)
    return receipt["bundle_sha256"] == _canonical_sha256(unsigned)


def build_assembly_input_skeleton(
    *,
    harness_head: str,
    groundtruth_head: str,
    unit_heads: dict[str, str] | None = None,
    input_receipts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a fill-in map without claiming final heads or benchmark readiness."""
    if not _SHA1_RE.fullmatch(harness_head) or not _SHA1_RE.fullmatch(groundtruth_head):
        raise ValueError("assembly input heads must be full commit SHAs")

    observed_heads = unit_heads or {}
    observed_receipts = input_receipts or {}
    if not set(observed_heads) <= HAR9_REQUIRED_UNITS:
        raise ValueError("assembly input contains an unknown unit")
    if not set(observed_receipts) <= HAR9_REQUIRED_UNITS:
        raise ValueError("assembly input contains an unknown receipt")
    if any(not _SHA1_RE.fullmatch(value) for value in observed_heads.values()):
        raise ValueError("assembly unit heads must be full commit SHAs")
    if any(not _SHA256_RE.fullmatch(value) for value in observed_receipts.values()):
        raise ValueError("assembly input receipts must be SHA-256 digests")

    payload: dict[str, Any] = {
        "schema": HAR9_ASSEMBLY_INPUT_SCHEMA,
        "harness_head": harness_head,
        "groundtruth_head": groundtruth_head,
        "unit_heads": {
            name: observed_heads.get(name, "UNVERIFIED")
            for name in HAR9_ASSEMBLY_INPUTS
        },
        "input_receipts": {
            name: observed_receipts.get(name, "UNVERIFIED")
            for name in HAR9_ASSEMBLY_INPUTS
        },
        "state": "PROVISIONAL_INPUTS_PENDING_FINAL_HEADS",
        "pending_units": [
            name for name in HAR9_ASSEMBLY_INPUTS if name not in observed_heads
        ],
        "pending_receipts": [
            name for name in HAR9_ASSEMBLY_INPUTS if name not in observed_receipts
        ],
        "results": {"provider_calls": 0, "benchmark_runs": 0},
        "authorization": {
            "benchmark_ready": False,
            "status": "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL",
        },
    }
    payload["skeleton_sha256"] = _canonical_sha256(payload)
    return payload


def verify_assembly_input_skeleton(receipt: Any) -> bool:
    """Verify the complete key set and digest before terminal closeout assembly."""
    if not isinstance(receipt, dict) or receipt.get("schema") != HAR9_ASSEMBLY_INPUT_SCHEMA:
        return False
    required = {
        "schema",
        "harness_head",
        "groundtruth_head",
        "unit_heads",
        "input_receipts",
        "state",
        "pending_units",
        "pending_receipts",
        "results",
        "authorization",
        "skeleton_sha256",
    }
    if set(receipt) != required:
        return False
    if not _SHA1_RE.fullmatch(receipt["harness_head"]) or not _SHA1_RE.fullmatch(
        receipt["groundtruth_head"]
    ):
        return False
    unit_heads = receipt["unit_heads"]
    input_receipts = receipt["input_receipts"]
    if not isinstance(unit_heads, dict) or set(unit_heads) != HAR9_REQUIRED_UNITS:
        return False
    if not isinstance(input_receipts, dict) or set(input_receipts) != HAR9_REQUIRED_UNITS:
        return False
    if any(
        value != "UNVERIFIED" and not _SHA1_RE.fullmatch(value)
        for value in unit_heads.values()
    ):
        return False
    if any(
        value != "UNVERIFIED" and not _SHA256_RE.fullmatch(value)
        for value in input_receipts.values()
    ):
        return False
    pending_units = [name for name in HAR9_ASSEMBLY_INPUTS if unit_heads[name] == "UNVERIFIED"]
    pending_receipts = [
        name for name in HAR9_ASSEMBLY_INPUTS if input_receipts[name] == "UNVERIFIED"
    ]
    if receipt["state"] != "PROVISIONAL_INPUTS_PENDING_FINAL_HEADS":
        return False
    if receipt["pending_units"] != pending_units or receipt["pending_receipts"] != pending_receipts:
        return False
    if receipt["results"] != {"provider_calls": 0, "benchmark_runs": 0}:
        return False
    if receipt["authorization"] != {
        "benchmark_ready": False,
        "status": "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL",
    }:
        return False
    unsigned = dict(receipt)
    unsigned.pop("skeleton_sha256", None)
    return receipt["skeleton_sha256"] == _canonical_sha256(unsigned)


def _numeric_equals(value: Any, expected: float) -> bool:
    try:
        return float(value) == expected
    except (TypeError, ValueError):
        return False


def _validate_smoke_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    tasks = manifest.get("tasks")
    if manifest.get("schema") != "gt.tb2.deepseek.smoke10.v1":
        errors.append("canonical smoke manifest schema mismatch")
    if not isinstance(tasks, list) or len(tasks) != 10:
        errors.append("canonical smoke must contain exactly ten unique tasks")
    elif any(not isinstance(task, str) or not task.strip() for task in tasks):
        errors.append("canonical smoke task identities must be nonempty strings")
    elif len(set(tasks)) != 10:
        errors.append("canonical smoke must contain exactly ten unique tasks")
    if manifest.get("dataset") != "terminal-bench/terminal-bench-2":
        errors.append("canonical smoke dataset mismatch")
    if manifest.get("model") != "deepseek-v4-flash":
        errors.append("canonical smoke model mismatch")
    if not _numeric_equals(manifest.get("temperature"), 1.0):
        errors.append("canonical smoke temperature mismatch")
    if not _numeric_equals(manifest.get("timeout_multiplier"), 1.0):
        errors.append("canonical smoke timeout multiplier mismatch")
    concurrency = manifest.get("concurrency")
    if not isinstance(concurrency, int) or concurrency < 1:
        errors.append("canonical smoke concurrency must be a positive integer")
    return errors


def _declared_runner_modes(path: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return ()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PHASE2_SUPPORTED_MODES"
                   for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            return ()
        if isinstance(value, (list, tuple, set)) and all(
            isinstance(item, str) for item in value
        ):
            return tuple(value)
    return ()


def _validate_arm_bindings(
    bindings: Any, repository_root: Path
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    sanitized: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not isinstance(bindings, dict):
        return sanitized, list(ARMS), ["arm bindings must be an object"]
    unexpected_arms = sorted(set(bindings) - set(ARMS))
    if unexpected_arms:
        errors.append(f"unexpected arm bindings: {', '.join(unexpected_arms)}")
    unbound = [arm for arm in ARMS if arm not in bindings]
    root = repository_root.resolve()
    for arm in ARMS:
        binding = bindings.get(arm)
        if binding is None:
            continue
        if not isinstance(binding, dict):
            errors.append(f"{arm}: binding must be an object")
            continue
        extra = sorted(set(binding) - _ARM_BINDING_KEYS)
        missing = sorted(_ARM_BINDING_KEYS - set(binding))
        if extra:
            errors.append(f"{arm}: unsupported keys: {', '.join(extra)}")
            continue
        if missing:
            errors.append(f"{arm}: missing keys: {', '.join(missing)}")
            continue
        runner = binding.get("runner")
        if (
            not isinstance(runner, str)
            or not runner.endswith(".py")
            or "\\" in runner
            or runner.startswith("/")
            or ".." in Path(runner).parts
        ):
            errors.append(f"{arm}: runner must be a repository-relative Python path")
            continue
        runner_path = (root / runner).resolve()
        try:
            runner_path.relative_to(root)
        except ValueError:
            errors.append(f"{arm}: runner escapes repository root")
            continue
        runner_hash = binding.get("runner_sha256")
        if not runner_path.is_file():
            errors.append(f"{arm}: runner file is missing")
            continue
        if not isinstance(runner_hash, str) or not _SHA256_RE.fullmatch(runner_hash):
            errors.append(f"{arm}: runner_sha256 is invalid")
            continue
        if hashlib.sha256(runner_path.read_bytes()).hexdigest() != runner_hash:
            errors.append(f"{arm}: runner_sha256 mismatch")
            continue
        if binding.get("schema") != "gt.phase2.arm_binding.v1":
            errors.append(f"{arm}: binding schema mismatch")
            continue
        if binding.get("agent") != _ARM_AGENTS[arm]:
            errors.append(f"{arm}: Mini-SWE agent identity mismatch")
            continue
        if binding.get("mode") != arm:
            errors.append(f"{arm}: mode must equal the canonical arm")
            continue
        if binding.get("provider_calls_per_iteration") != 1:
            errors.append(f"{arm}: provider calls per iteration must equal one")
            continue
        if arm not in _declared_runner_modes(runner_path):
            errors.append(f"{arm}: runner does not statically declare mode support")
            continue
        sanitized[arm] = {key: binding[key] for key in sorted(_ARM_BINDING_KEYS)}
    return sanitized, unbound, errors


def build_execution_plan(
    manifest: dict[str, Any],
    task_manifest: dict[str, Any],
    arm_bindings: dict[str, Any] | None = None,
    *,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    """Compile the six-arm ten-task matrix without executing any trial.

    The plan is intentionally incapable of granting authorization or creating
    provider receipts.  It records missing executable arm bindings and frozen
    identities as blockers instead of turning a template into fake readiness.
    """
    arm_bindings = arm_bindings or {}
    repository_root = repository_root or Path(__file__).resolve().parents[1]
    manifest_errors = validate_manifest(manifest)
    smoke_errors = _validate_smoke_manifest(task_manifest)
    frozen = manifest.get("frozen") if isinstance(manifest.get("frozen"), dict) else {}
    template_values = {
        "model",
        "prompt_sha256",
        "task_manifest_sha256",
        "environment_sha256",
        "budget_sha256",
    }
    manifest_is_template = bool(manifest.get("template_only")) or any(
        frozen.get(key) == _TEMPLATE_SENTINEL for key in template_values
    )
    task_manifest_hash = _canonical_sha256(task_manifest)
    if not manifest_is_template:
        for key in template_values - {"model"}:
            if not isinstance(frozen.get(key), str) or not _SHA256_RE.fullmatch(frozen[key]):
                manifest_errors.append(f"invalid frozen identity: {key}")
        if frozen.get("model") != task_manifest.get("model"):
            manifest_errors.append("frozen model differs from task manifest")
        if frozen.get("task_manifest_sha256") != task_manifest_hash:
            manifest_errors.append("frozen task manifest hash mismatch")
        for key in ("missing_run_policy", "multiplicity_policy"):
            if manifest.get(key) in (None, "", _TEMPLATE_SENTINEL):
                manifest_errors.append(f"missing concrete policy: {key}")
    sanitized_bindings, unbound_arms, binding_errors = _validate_arm_bindings(
        arm_bindings, repository_root
    )
    blockers: list[str] = []
    if manifest_errors:
        blockers.append("invalid_experiment_manifest")
    if smoke_errors:
        blockers.append("invalid_smoke_manifest")
    if manifest_is_template:
        blockers.append("template_manifest_not_frozen")
    if unbound_arms:
        blockers.append("arm_executors_not_bound")
    if binding_errors:
        blockers.append("arm_executors_invalid")

    trials: list[dict[str, Any]] = []
    tasks = task_manifest.get("tasks") if not smoke_errors else []
    experiment_manifest_hash = _canonical_sha256(manifest)
    for task_index, task_id in enumerate(tasks):
        matched_pair_id = hashlib.sha256(
            f"gt.phase2.pair.v1\0{experiment_manifest_hash}\0{task_manifest_hash}\0{task_id}".encode()
        ).hexdigest()
        for arm_index, arm in enumerate(ARMS):
            trial_id = hashlib.sha256(
                f"gt.phase2.trial.v1\0{matched_pair_id}\0{arm}".encode()
            ).hexdigest()
            trials.append(
                {
                    "ordinal": len(trials),
                    "task_index": task_index,
                    "arm_index": arm_index,
                    "task_id": task_id,
                    "matched_pair_id": matched_pair_id,
                    "arm": arm,
                    "trial_id": trial_id,
                    "runner_binding": sanitized_bindings.get(arm),
                    "execution_state": "PLANNED",
                    "provider_execution_required": True,
                }
            )
    return {
        "schema": "gt.phase2.execution_plan.v1",
        "ok": not manifest_errors and not smoke_errors and not binding_errors,
        "executed": False,
        "provider_calls": 0,
        "authorization_receipt": None,
        "provider_receipt_root_sha256": None,
        "experiment_manifest_sha256": experiment_manifest_hash,
        "task_manifest_sha256": task_manifest_hash,
        "task_count": len(tasks),
        "trial_count": len(trials),
        "arms": list(ARMS),
        "unbound_arms": unbound_arms,
        "ready_for_authorized_execution": not blockers,
        "blockers": blockers,
        "validation_errors": manifest_errors + smoke_errors,
        "binding_errors": binding_errors,
        "trials": trials,
    }


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != "gt.phase2.experiment_manifest.v1":
        errors.append("manifest schema mismatch")
    if tuple(manifest.get("arms", ())) != ARMS:
        errors.append("six arms are missing or out of canonical order")
    frozen = manifest.get("frozen", {})
    for key in (
        "model",
        "prompt_sha256",
        "task_manifest_sha256",
        "environment_sha256",
        "budget_sha256",
    ):
        if not frozen.get(key):
            errors.append(f"missing frozen identity: {key}")
    if manifest.get("model_calls_per_iteration") != 1:
        errors.append("model_calls_per_iteration must equal one")
    if manifest.get("primary_endpoint") != "independently_verified_solve_rate":
        errors.append("primary endpoint mismatch")
    if manifest.get("authorization_required") is not True:
        errors.append("provider-cost authorization gate is missing")
    return errors


def dry_run(manifest: dict[str, Any]) -> dict[str, Any]:
    errors = validate_manifest(manifest)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema": "gt.phase2.dry_run.v1",
        "ok": not errors,
        "executed": False,
        "provider_calls": 0,
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        "planned_arms": list(ARMS),
        "errors": errors,
    }


def _mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2**n)
    return min(1.0, 2 * tail)


def _bootstrap_delta(values: list[float], seed: int = 20260801) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    draws = []
    for _ in range(2000):
        sample = [values[rng.randrange(len(values))] for _ in values]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    return draws[49], draws[1949]


def analyze(
    rows: list[dict[str, str]], execution_receipt: dict[str, Any]
) -> dict[str, Any]:
    if (
        execution_receipt.get("schema") != "gt.phase2.execution_receipt.v1"
        or execution_receipt.get("authorized") is not True
        or not execution_receipt.get("provider_receipt_root_sha256")
        or not execution_receipt.get("manifest_sha256")
    ):
        raise ValueError("authorized provider-bound execution receipt is required")
    by_task: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        arm = row.get("arm", "")
        if arm not in ARMS:
            raise ValueError(f"unknown arm: {arm}")
        key = row.get("matched_pair_id") or row.get("task_id")
        if not key or arm in by_task[key]:
            raise ValueError("missing or duplicate task-arm identity")
        if row.get("solved") not in {"0", "1"}:
            raise ValueError("solved must be independently verified as 0 or 1")
        if not row.get("verified_by"):
            raise ValueError("independent verifier identity is required")
        for metric in (
            "exploration_actions",
            "raw_bytes_consumed",
            "false_interventions",
            "stale_incomplete_incidents",
        ):
            try:
                if float(row.get(metric, "")) < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(f"invalid nonnegative metric {metric}") from exc
        by_task[key][arm] = row
    if not by_task:
        raise ValueError("no matched task identities")
    expected_arms = set(ARMS)
    for key, pair in by_task.items():
        if set(pair) != expected_arms:
            raise ValueError(f"matched identity {key} does not contain all six arms")
    if execution_receipt.get("task_count") != len(by_task):
        raise ValueError("execution receipt task count differs from result pairs")
    comparisons: dict[str, Any] = {}
    for arm in ARMS[1:]:
        paired = [pair for pair in by_task.values() if "stock_raw" in pair and arm in pair]
        b = sum(
            int(pair["stock_raw"]["solved"]) == 1
            and int(pair[arm]["solved"]) == 0
            for pair in paired
        )
        c = sum(
            int(pair["stock_raw"]["solved"]) == 0
            and int(pair[arm]["solved"]) == 1
            for pair in paired
        )
        solve_delta = [
            int(pair[arm]["solved"]) - int(pair["stock_raw"]["solved"])
            for pair in paired
        ]
        exploration_delta = [
            float(pair[arm]["exploration_actions"])
            - float(pair["stock_raw"]["exploration_actions"])
            for pair in paired
        ]
        comparisons[arm] = {
            "paired_tasks": len(paired),
            "solve_rate_delta": sum(solve_delta) / len(paired) if paired else 0.0,
            "solve_rate_delta_ci95": _bootstrap_delta([float(x) for x in solve_delta]),
            "exploration_delta": (
                sum(exploration_delta) / len(paired) if paired else 0.0
            ),
            "exploration_delta_ci95": _bootstrap_delta(exploration_delta),
            "mcnemar": {"stock_only": b, "candidate_only": c, "p_exact": _mcnemar_exact(b, c)},
        }
    return {
        "schema": "gt.phase2.analysis.v1",
        "paid_run": True,
        "execution_receipt_sha256": hashlib.sha256(
            json.dumps(execution_receipt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "primary_endpoint": "independently_verified_solve_rate",
        "matched_identities": len(by_task),
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--manifest", type=Path, required=True)
    dry.add_argument("--out", type=Path, required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--task-manifest", type=Path, required=True)
    plan.add_argument("--arm-bindings", type=Path)
    plan.add_argument(
        "--inspect",
        action="store_true",
        help="emit a structurally valid blocked plan without treating it as execution-ready",
    )
    plan.add_argument("--out", type=Path, required=True)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--results", type=Path, required=True)
    analysis.add_argument("--execution-receipt", type=Path, required=True)
    analysis.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "dry-run":
        result = dry_run(json.loads(args.manifest.read_text(encoding="utf-8")))
    elif args.command == "plan":
        bindings = (
            json.loads(args.arm_bindings.read_text(encoding="utf-8"))
            if args.arm_bindings
            else None
        )
        result = build_execution_plan(
            json.loads(args.manifest.read_text(encoding="utf-8")),
            json.loads(args.task_manifest.read_text(encoding="utf-8")),
            bindings,
        )
    else:
        with args.results.open(encoding="utf-8", newline="") as stream:
            result = analyze(
                list(csv.DictReader(stream)),
                json.loads(args.execution_receipt.read_text(encoding="utf-8")),
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "plan":
        if not result["ok"]:
            return 1
        if not result["ready_for_authorized_execution"] and not args.inspect:
            return 2
        return 0
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
