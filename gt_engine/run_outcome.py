"""Truthful, orthogonal Mini-SWE/Harbor attempt outcomes.

The official verifier, the runner process, the solver lifecycle, and GT are
independent authorities.  This module joins their artifacts without allowing
one success signal (notably reward=1) to erase another failure signal.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ProcessOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    SETUP_ERROR = "SETUP_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_MODEL_MISMATCH = "PROVIDER_MODEL_MISMATCH"
    HARNESS_ERROR = "HARNESS_ERROR"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    INTERRUPTED = "INTERRUPTED"


class SolverOutcome(StrEnum):
    SUBMITTED = "SUBMITTED"
    UNVERIFIED_SUBMISSION = "UNVERIFIED_SUBMISSION"
    EXHAUSTED = "EXHAUSTED"
    DECLINED = "DECLINED"
    STUCK = "STUCK"
    NOT_STARTED = "NOT_STARTED"
    UNKNOWN = "UNKNOWN"


class GtOutcome(StrEnum):
    INACTIVE = "INACTIVE"
    SHADOW_OK = "SHADOW_OK"
    ADVISORY_OK = "ADVISORY_OK"
    DEGRADED_FAIL_OPEN = "DEGRADED_FAIL_OPEN"
    PROVEN_RED_REFUSAL = "PROVEN_RED_REFUSAL"
    GT_ABORTED = "GT_ABORTED"
    INVALID = "INVALID"


class GraderOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class ArtifactIntegrity(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    CONTRADICTORY = "CONTRADICTORY"
    TAMPERED = "TAMPERED"


class ResearchValidity(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class TrialOutcome:
    task_name: str
    reward: float | None
    terminal: str
    runner_report_present: bool
    process_outcome: ProcessOutcome
    solver_outcome: SolverOutcome
    gt_outcome: GtOutcome
    grader_outcome: GraderOutcome
    artifact_integrity: ArtifactIntegrity
    research_validity: ResearchValidity
    derived_label: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SUBMITTED = {"submitted", "submitted_verified", "submitted_unverified"}
_PROVIDER_TERMINALS = {"provider_failed", "provider_model_mismatch"}
_INFRA_TERMINALS = {"internal_error", "timeout"}


def _exception(result: Mapping[str, Any]) -> tuple[str, str]:
    raw = result.get("exception_info") or result.get("agent_error") or {}
    if not isinstance(raw, Mapping):
        return "", str(raw or "")
    return (
        str(raw.get("exception_type") or ""),
        str(raw.get("exception_message") or ""),
    )


def _reward(result: Mapping[str, Any]) -> float | None:
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") if isinstance(verifier, Mapping) else {}
    value = rewards.get("reward") if isinstance(rewards, Mapping) else None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _solver(terminal: str) -> SolverOutcome:
    if terminal in {"submitted", "submitted_verified"}:
        return SolverOutcome.SUBMITTED
    if terminal == "submitted_unverified":
        return SolverOutcome.UNVERIFIED_SUBMISSION
    if terminal == "budget_exhausted":
        return SolverOutcome.EXHAUSTED
    if terminal == "task_failed":
        return SolverOutcome.DECLINED
    if terminal == "stuck":
        return SolverOutcome.STUCK
    if terminal in {"setup_error", "not_started"}:
        return SolverOutcome.NOT_STARTED
    return SolverOutcome.UNKNOWN


def _grader(reward: float | None) -> GraderOutcome:
    if reward is None:
        return GraderOutcome.UNAVAILABLE
    return GraderOutcome.PASS if reward == 1.0 else GraderOutcome.FAIL


def _process(
    terminal: str,
    exception_type: str,
    exception_message: str,
    runner_report: Mapping[str, Any] | None,
) -> ProcessOutcome:
    text = f"{exception_type} {exception_message}".lower()
    if "agenttimeouterror" in text:
        return ProcessOutcome.INTERRUPTED
    if "providermodelmismatch" in text or terminal == "provider_model_mismatch":
        return ProcessOutcome.PROVIDER_MODEL_MISMATCH
    if terminal in _PROVIDER_TERMINALS:
        return ProcessOutcome.PROVIDER_ERROR
    if "sandbox" in exception_type.lower():
        return ProcessOutcome.SANDBOX_ERROR
    if terminal == "setup_error":
        return ProcessOutcome.SETUP_ERROR
    if runner_report is None:
        return ProcessOutcome.HARNESS_ERROR
    runner_exception = str(runner_report.get("exception") or "").lower()
    if "lifecycleerror" in runner_exception:
        return ProcessOutcome.HARNESS_ERROR
    if exception_type == "NonZeroAgentExitCodeError":
        # Solver terminals are supposed to return process success.  A nonzero
        # result here is an exit-contract/harness defect, not model failure.
        return ProcessOutcome.HARNESS_ERROR
    if terminal in _INFRA_TERMINALS:
        return ProcessOutcome.HARNESS_ERROR
    return ProcessOutcome.COMPLETED


def _gt(
    terminal: str,
    process: ProcessOutcome,
    runner_report: Mapping[str, Any] | None,
) -> GtOutcome:
    if not runner_report or "gt" not in runner_report:
        return GtOutcome.INACTIVE
    gt_state = runner_report.get("gt") or {}
    phase = str(gt_state.get("phase") or "") if isinstance(gt_state, Mapping) else ""
    runner_exception = str(runner_report.get("exception") or "")
    if terminal == "stuck" and (
        phase.upper() == "STUCK" or "LifecycleError" in runner_exception
    ):
        return GtOutcome.GT_ABORTED
    if isinstance(gt_state, Mapping) and (
        gt_state.get("degraded")
        or gt_state.get("gt_disabled")
        or str(gt_state.get("assurance") or "").upper() == "DEGRADED"
    ):
        return GtOutcome.DEGRADED_FAIL_OPEN
    mode = str(runner_report.get("gt_mode") or "advisory").lower()
    if mode == "shadow":
        return GtOutcome.SHADOW_OK
    return GtOutcome.ADVISORY_OK


def _label(
    grader: GraderOutcome,
    process: ProcessOutcome,
    solver: SolverOutcome,
    gt: GtOutcome,
    report_present: bool,
) -> str:
    resolved = grader is GraderOutcome.PASS
    if gt is GtOutcome.GT_ABORTED:
        return "GT_ABORTED_RESOLVED" if resolved else "GT_ABORTED_UNRESOLVED"
    if process is ProcessOutcome.INTERRUPTED:
        return "INTERRUPTED_RESOLVED" if resolved else "INTERRUPTED_UNRESOLVED"
    if (
        process is ProcessOutcome.HARNESS_ERROR
        and solver is SolverOutcome.EXHAUSTED
        and report_present
    ):
        return (
            "SALVAGED_RESOLVED_WITH_EXIT_DEFECT"
            if resolved
            else "EXHAUSTED_UNRESOLVED_WITH_EXIT_DEFECT"
        )
    if process is not ProcessOutcome.COMPLETED:
        return "INFRASTRUCTURE_INVALID"
    if solver in {SolverOutcome.SUBMITTED, SolverOutcome.UNVERIFIED_SUBMISSION}:
        return "CLEAN_SUBMITTED_RESOLVED" if resolved else "CLEAN_SUBMITTED_UNRESOLVED"
    if solver is SolverOutcome.EXHAUSTED:
        return "SALVAGED_RESOLVED" if resolved else "CLEAN_EXHAUSTED_UNRESOLVED"
    if solver is SolverOutcome.UNKNOWN:
        return "UNCLASSIFIABLE"
    return "CLEAN_RESOLVED" if resolved else "CLEAN_UNRESOLVED"


def join_trial_outcome(
    result: Mapping[str, Any],
    runner_report: Mapping[str, Any] | None,
    *,
    task_name: str = "",
) -> TrialOutcome:
    """Join Harbor, runner, GT and verifier facts into one immutable outcome."""
    reward = _reward(result)
    terminal = str((runner_report or {}).get("terminal") or "unknown")
    exception_type, exception_message = _exception(result)
    process = _process(terminal, exception_type, exception_message, runner_report)
    solver = _solver(terminal)
    grader = _grader(reward)
    gt = _gt(terminal, process, runner_report)
    reasons: list[str] = []
    integrity = ArtifactIntegrity.COMPLETE
    if runner_report is None:
        integrity = ArtifactIntegrity.INCOMPLETE
        reasons.append("runner_report_missing")
    else:
        reported_exit = runner_report.get("exit_code")
        if reported_exit is None:
            integrity = ArtifactIntegrity.INCOMPLETE
            reasons.append("runner_exit_code_missing")
        if terminal == "unknown":
            integrity = ArtifactIntegrity.INCOMPLETE
            reasons.append("runner_terminal_missing")
    if process is ProcessOutcome.HARNESS_ERROR:
        reasons.append("harness_or_exit_contract_error")
    if gt is GtOutcome.GT_ABORTED:
        reasons.append("gt_aborted_baseline_control_flow")
    if process is ProcessOutcome.INTERRUPTED:
        reasons.append("outer_agent_timeout")
    validity = (
        ResearchValidity.VALID
        if integrity is ArtifactIntegrity.COMPLETE
        and process is ProcessOutcome.COMPLETED
        and gt not in {GtOutcome.GT_ABORTED, GtOutcome.INVALID}
        else ResearchValidity.INVALID
    )
    return TrialOutcome(
        task_name=task_name,
        reward=reward,
        terminal=terminal,
        runner_report_present=runner_report is not None,
        process_outcome=process,
        solver_outcome=solver,
        gt_outcome=gt,
        grader_outcome=grader,
        artifact_integrity=integrity,
        research_validity=validity,
        derived_label=_label(grader, process, solver, gt, runner_report is not None),
        reasons=tuple(reasons),
    )


def summarize_outcomes(outcomes: Iterable[TrialOutcome]) -> dict[str, Any]:
    rows = list(outcomes)
    labels = Counter(row.derived_label for row in rows)
    return {
        "tasks": len(rows),
        "official_resolved": sum(row.grader_outcome is GraderOutcome.PASS for row in rows),
        "clean_resolved": sum(
            row.grader_outcome is GraderOutcome.PASS
            and row.process_outcome is ProcessOutcome.COMPLETED
            for row in rows
        ),
        "clean_submitted_resolved": labels["CLEAN_SUBMITTED_RESOLVED"],
        "salvaged_resolved": labels["SALVAGED_RESOLVED"],
        "interrupted_resolved": labels["INTERRUPTED_RESOLVED"],
        "gt_aborted_resolved": labels["GT_ABORTED_RESOLVED"],
        "infrastructure_invalid": sum(
            row.derived_label == "INFRASTRUCTURE_INVALID" for row in rows
        ),
        "unclassifiable": sum(row.derived_label == "UNCLASSIFIABLE" for row in rows),
        "runner_reports_present": sum(row.runner_report_present for row in rows),
        "research_valid": all(
            row.research_validity is ResearchValidity.VALID for row in rows
        ),
        "labels": dict(sorted(labels.items())),
    }
