"""Predeclared GT-on experiment assignment and Pareto release gates."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean

BASELINE_TASKS = 89
BASELINE_SOLVED = 66
BASELINE_TOKENS = 242_540_464
BASELINE_ACTIONS = 4_394
BASELINE_ERRORS = 4
MINIMUM_MEAN_SOLVED = 72.0
MAXIMUM_EFFICIENCY_RATIO = 0.85


class ExperimentArm(StrEnum):
    OFF = "off"
    AUDIT = "audit"
    CERTIFIED_CONTEXT = "certified_context"
    CERTIFIED_CONTROLLERS = "certified_controllers"
    CERTIFIED_FULL = "certified_full"


@dataclass(frozen=True, slots=True)
class TrialRecord:
    task: str
    trial: int
    arm: str
    solved: bool
    tokens: float
    actions: float
    errored: bool = False
    calls: float = 0.0
    steps: float = 0.0
    effective_actions: float = 0.0
    wall_time_sec: float = 0.0
    token_cap: float | None = None
    action_cap: float | None = None
    call_cap: float | None = None
    step_cap: float | None = None
    effective_action_cap: float | None = None
    wall_time_cap_sec: float | None = None

    def capped(self, metric: str) -> float:
        value = float(getattr(self, metric))
        if self.solved:
            return value
        cap_name = {
            "tokens": "token_cap",
            "actions": "action_cap",
            "calls": "call_cap",
            "steps": "step_cap",
            "effective_actions": "effective_action_cap",
            "wall_time_sec": "wall_time_cap_sec",
        }[metric]
        cap = getattr(self, cap_name)
        return max(value, float(cap)) if cap is not None else value


@dataclass(frozen=True, slots=True)
class RepeatedReleaseCriteria:
    require_positive_uplift: bool = True
    noninferiority_margin: float = 0.02
    maximum_resource_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class RepeatedReleaseAssessment:
    passed: bool
    failures: tuple[str, ...]
    off_trials: int
    treatment_trials: int
    off_solve_rate: float
    treatment_solve_rate: float
    solve_delta: float
    solve_delta_lower_95: float
    treatment_only_solves: int
    control_only_solves: int
    capped_token_ratio: float
    capped_action_ratio: float
    capped_call_ratio: float
    capped_step_ratio: float
    capped_effective_action_ratio: float
    capped_wall_time_ratio: float
    resource_ratio_upper_95: dict[str, float]


def crossover_arm(task_id: str, round_index: int, *, seed: str) -> ExperimentArm:
    """Return a deterministic ABBA/BAAB crossover assignment.

    Every task receives two OFF and two treatment assignments per four rounds.
    Hashing chooses only the orientation, never the balance.
    """

    if round_index < 0:
        raise ValueError("round_index must be non-negative")
    parity = hashlib.sha256(f"{seed}\0{task_id}".encode()).digest()[0] & 1
    sequence = (
        (ExperimentArm.OFF, ExperimentArm.CERTIFIED_FULL,
         ExperimentArm.CERTIFIED_FULL, ExperimentArm.OFF)
        if parity == 0
        else (ExperimentArm.CERTIFIED_FULL, ExperimentArm.OFF,
              ExperimentArm.OFF, ExperimentArm.CERTIFIED_FULL)
    )
    return sequence[round_index % 4]


@dataclass(frozen=True, slots=True)
class ReleaseAssessment:
    passed: bool
    failures: tuple[str, ...]
    mean_solved: float
    mean_tokens: float
    mean_actions: float
    max_errors_per_run: int
    solve_delta_lower_95: float
    token_ratio_upper_95: float
    action_ratio_upper_95: float


def deterministic_arm(
    task_id: str,
    trial_index: int,
    feature: str,
    feature_version: str,
    seed: str,
) -> str:
    payload = "\0".join(
        (task_id, str(trial_index), feature, feature_version, seed)
    ).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return "treatment" if value & 1 else "shadow"


def select_eligible_panel(
    severity_by_task: Mapping[str, float],
    *,
    minimum: int = 20,
    maximum: int = 30,
) -> tuple[str, ...]:
    if len(severity_by_task) < minimum:
        return ()
    ordered = sorted(severity_by_task, key=lambda task: (-severity_by_task[task], task))
    return tuple(ordered[:maximum])


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile needs at least one value")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(probability * len(ordered))))
    return ordered[index]


def _group_candidate(records: Iterable[TrialRecord]) -> dict[str, list[TrialRecord]]:
    grouped: dict[str, list[TrialRecord]] = {}
    for record in records:
        grouped.setdefault(record.task, []).append(record)
    return grouped


def assess_release(
    baseline: Mapping[str, TrialRecord],
    candidate: Iterable[TrialRecord],
    *,
    bootstrap_samples: int = 100_000,
    seed: int = 20260803,
    runtime_errors: int = 0,
    permanently_blocked_submissions: int = 0,
) -> ReleaseAssessment:
    """Evaluate the frozen-baseline, five-repeat GT-on Pareto contract."""
    if len(baseline) != BASELINE_TASKS:
        raise ValueError(f"baseline must contain exactly {BASELINE_TASKS} tasks")
    grouped = _group_candidate(candidate)
    if set(grouped) != set(baseline):
        raise ValueError("candidate and baseline task sets differ")
    trial_ids = {record.trial for records in grouped.values() for record in records}
    if len(trial_ids) != 5:
        raise ValueError("release gate requires exactly five candidate trials per task")
    if any(
        len(records) != 5 or {record.trial for record in records} != trial_ids
        for records in grouped.values()
    ):
        raise ValueError("every candidate task must contain the same five unique trials")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")

    tasks = sorted(baseline)
    candidate_solve = {
        task: fmean(float(record.solved) for record in grouped[task]) for task in tasks
    }
    candidate_tokens = {
        task: fmean(record.tokens for record in grouped[task]) for task in tasks
    }
    candidate_actions = {
        task: fmean(record.actions for record in grouped[task]) for task in tasks
    }
    mean_solved = sum(candidate_solve.values())
    mean_tokens = sum(candidate_tokens.values())
    mean_actions = sum(candidate_actions.values())
    errors_by_trial = {
        trial: sum(
            1
            for records in grouped.values()
            for record in records
            if record.trial == trial and record.errored
        )
        for trial in trial_ids
    }
    max_errors = max(errors_by_trial.values(), default=0)

    rng = random.Random(seed)
    solve_deltas: list[float] = []
    token_ratios: list[float] = []
    action_ratios: list[float] = []
    for _ in range(bootstrap_samples):
        sample = rng.choices(tasks, k=len(tasks))
        solve_deltas.append(
            fmean(candidate_solve[task] - float(baseline[task].solved) for task in sample)
        )
        baseline_token_sum = sum(baseline[task].tokens for task in sample)
        baseline_action_sum = sum(baseline[task].actions for task in sample)
        token_ratios.append(
            sum(candidate_tokens[task] for task in sample) / baseline_token_sum
        )
        action_ratios.append(
            sum(candidate_actions[task] for task in sample) / baseline_action_sum
        )

    solve_lcb = _percentile(solve_deltas, 0.05)
    token_ucb = _percentile(token_ratios, 0.95)
    action_ucb = _percentile(action_ratios, 0.95)
    failures: list[str] = []
    if mean_solved < MINIMUM_MEAN_SOLVED or solve_lcb <= 0:
        failures.append("solve_rate")
    if mean_tokens > BASELINE_TOKENS * MAXIMUM_EFFICIENCY_RATIO or token_ucb > 0.85:
        failures.append("tokens")
    if mean_actions > BASELINE_ACTIONS * MAXIMUM_EFFICIENCY_RATIO or action_ucb > 0.85:
        failures.append("actions")
    if max_errors > BASELINE_ERRORS:
        failures.append("errors")
    if runtime_errors:
        failures.append("runtime_errors")
    if permanently_blocked_submissions:
        failures.append("blocked_submissions")

    return ReleaseAssessment(
        passed=not failures,
        failures=tuple(failures),
        mean_solved=mean_solved,
        mean_tokens=mean_tokens,
        mean_actions=mean_actions,
        max_errors_per_run=max_errors,
        solve_delta_lower_95=solve_lcb,
        token_ratio_upper_95=token_ucb,
        action_ratio_upper_95=action_ucb,
    )


_RESOURCE_METRICS = (
    "tokens",
    "actions",
    "calls",
    "steps",
    "effective_actions",
    "wall_time_sec",
)


def _arm_records(
    records: Iterable[TrialRecord], arm: ExperimentArm
) -> dict[str, list[TrialRecord]]:
    grouped: dict[str, list[TrialRecord]] = {}
    for record in records:
        if record.arm == arm.value:
            grouped.setdefault(record.task, []).append(record)
    for task_records in grouped.values():
        task_records.sort(key=lambda item: item.trial)
    return grouped


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else float("inf")
    return numerator / denominator


def assess_repeated_release(
    records: Iterable[TrialRecord],
    *,
    criteria: RepeatedReleaseCriteria | None = None,
    bootstrap_samples: int = 100_000,
    seed: int = 20260808,
) -> RepeatedReleaseAssessment:
    """Compare repeated contemporaneous OFF and certified-full task trials.

    Tasks are the top-level sampling unit and repetitions are resampled within
    task.  Failed trajectories consume their declared resource caps so an
    early failure cannot masquerade as an efficiency improvement.
    """

    criteria = criteria or RepeatedReleaseCriteria()
    materialized = tuple(records)
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    off = _arm_records(materialized, ExperimentArm.OFF)
    treatment = _arm_records(materialized, ExperimentArm.CERTIFIED_FULL)
    if not off or set(off) != set(treatment):
        raise ValueError("OFF and certified-full task sets must be non-empty and identical")
    tasks = sorted(off)
    for task in tasks:
        if len(off[task]) < 2 or len(treatment[task]) < 2:
            raise ValueError("release assessment requires at least two trials per arm per task")
        if len(off[task]) != len(treatment[task]):
            raise ValueError("every task must have balanced OFF and certified-full trials")
        if len({item.trial for item in (*off[task], *treatment[task])}) != (
            len(off[task]) + len(treatment[task])
        ):
            raise ValueError("trial identifiers must be unique within each task")
        for record in (*off[task], *treatment[task]):
            if not record.solved and any(
                getattr(record, cap_name) is None
                for cap_name in (
                    "token_cap",
                    "action_cap",
                    "call_cap",
                    "step_cap",
                    "effective_action_cap",
                    "wall_time_cap_sec",
                )
            ):
                raise ValueError("unsolved trials require every resource cap")

    off_trials = sum(len(rows) for rows in off.values())
    treatment_trials = sum(len(rows) for rows in treatment.values())
    off_solve_rate = fmean(float(row.solved) for rows in off.values() for row in rows)
    treatment_solve_rate = fmean(
        float(row.solved) for rows in treatment.values() for row in rows
    )
    solve_delta = treatment_solve_rate - off_solve_rate

    treatment_only = control_only = 0
    for task in tasks:
        control_solves = sum(row.solved for row in off[task])
        treatment_solves = sum(row.solved for row in treatment[task])
        treatment_only += max(0, treatment_solves - control_solves)
        control_only += max(0, control_solves - treatment_solves)

    totals: dict[str, tuple[float, float]] = {}
    for metric in _RESOURCE_METRICS:
        off_total = sum(row.capped(metric) for rows in off.values() for row in rows)
        treatment_total = sum(
            row.capped(metric) for rows in treatment.values() for row in rows
        )
        totals[metric] = (off_total, treatment_total)

    rng = random.Random(seed)
    solve_deltas: list[float] = []
    ratio_samples: dict[str, list[float]] = {metric: [] for metric in _RESOURCE_METRICS}
    for _ in range(bootstrap_samples):
        sampled_tasks = rng.choices(tasks, k=len(tasks))
        sampled_off: list[TrialRecord] = []
        sampled_treatment: list[TrialRecord] = []
        for task in sampled_tasks:
            sampled_off.extend(rng.choices(off[task], k=len(off[task])))
            sampled_treatment.extend(
                rng.choices(treatment[task], k=len(treatment[task]))
            )
        solve_deltas.append(
            fmean(float(row.solved) for row in sampled_treatment)
            - fmean(float(row.solved) for row in sampled_off)
        )
        for metric in _RESOURCE_METRICS:
            ratio_samples[metric].append(
                _ratio(
                    sum(row.capped(metric) for row in sampled_treatment),
                    sum(row.capped(metric) for row in sampled_off),
                )
            )

    solve_lcb = _percentile(solve_deltas, 0.05)
    ratio_ucb = {
        metric: _percentile(samples, 0.95)
        for metric, samples in ratio_samples.items()
    }
    point_ratios = {
        metric: _ratio(treatment_total, off_total)
        for metric, (off_total, treatment_total) in totals.items()
    }
    failures: list[str] = []
    if criteria.require_positive_uplift:
        if solve_lcb <= 0:
            failures.append("solve_rate")
    elif solve_lcb < -abs(float(criteria.noninferiority_margin)):
        failures.append("solve_rate")
    for metric, upper in ratio_ucb.items():
        if upper > float(criteria.maximum_resource_ratio):
            failures.append(metric)

    return RepeatedReleaseAssessment(
        passed=not failures,
        failures=tuple(failures),
        off_trials=off_trials,
        treatment_trials=treatment_trials,
        off_solve_rate=off_solve_rate,
        treatment_solve_rate=treatment_solve_rate,
        solve_delta=solve_delta,
        solve_delta_lower_95=solve_lcb,
        treatment_only_solves=treatment_only,
        control_only_solves=control_only,
        capped_token_ratio=point_ratios["tokens"],
        capped_action_ratio=point_ratios["actions"],
        capped_call_ratio=point_ratios["calls"],
        capped_step_ratio=point_ratios["steps"],
        capped_effective_action_ratio=point_ratios["effective_actions"],
        capped_wall_time_ratio=point_ratios["wall_time_sec"],
        resource_ratio_upper_95=ratio_ucb,
    )
