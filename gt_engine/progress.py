"""Deterministic progress-state tracking for early stall detection."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum


class ActionResultKind(StrEnum):
    SUCCESS = "success"
    SEARCH_NO_MATCH = "search_no_match"
    DIFFERENCE = "difference"
    VALIDATION_PASS = "validation_pass"
    VALIDATION_FAIL = "validation_fail"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    UNKNOWN = "unknown"


def task_information_gain(*, new_read_anchor: bool, diagnostic_gain: bool) -> bool:
    """Whether an observation added task-linked decision evidence.

    A different stdout hash is activity, not information gain. Only a new
    localized task anchor or a new attributable diagnostic advances this edge.
    """

    return bool(new_read_anchor or diagnostic_gain)


def semantic_progress_fingerprint(
    *,
    source_revision: str,
    changed_paths: tuple[str, ...],
    validation_state: str,
    diagnostic_fingerprint: str,
    project_checks: tuple[str, ...],
    validation_debt: bool,
) -> str:
    """Hash decision state, never command novelty or raw model output."""

    material = json.dumps(
        [
            str(source_revision),
            tuple(sorted({str(path).replace("\\", "/") for path in changed_paths if path})),
            str(validation_state or "unknown").lower(),
            str(diagnostic_fingerprint or ""),
            tuple(sorted({" ".join(str(check).split()) for check in project_checks if check})),
            bool(validation_debt),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


def classify_action_result(
    *,
    operation: str,
    executable: str,
    return_code: int | None,
    output: str = "",
) -> ActionResultKind:
    """Interpret executable exit conventions without inferring task success."""

    normalized_operation = str(operation or "").strip().lower()
    normalized_executable = str(executable or "").rsplit("/", 1)[-1].lower()
    if return_code is None:
        return ActionResultKind.UNKNOWN
    host_timeout = bool(
        return_code == -1
        and re.search(
            r"(?:^|\n)RuntimeError: Command timed out after "
            r"\d+(?:\.\d+)? seconds(?:\n|$)",
            str(output),
        )
    )
    if return_code == 124 or host_timeout:
        return ActionResultKind.TIMEOUT
    if normalized_operation == "validate":
        return (
            ActionResultKind.VALIDATION_PASS
            if return_code == 0
            else ActionResultKind.VALIDATION_FAIL
        )
    if normalized_operation == "search" and normalized_executable in {
        "ack",
        "ag",
        "grep",
        "rg",
    }:
        if return_code == 0:
            return ActionResultKind.SUCCESS
        if return_code == 1:
            return ActionResultKind.SEARCH_NO_MATCH
        return ActionResultKind.EXECUTION_ERROR
    if normalized_executable in {"cmp", "diff"}:
        if return_code == 0:
            return ActionResultKind.SUCCESS
        if return_code == 1:
            return ActionResultKind.DIFFERENCE
        return ActionResultKind.EXECUTION_ERROR
    return (
        ActionResultKind.SUCCESS
        if return_code == 0
        else ActionResultKind.EXECUTION_ERROR
    )


@dataclass(frozen=True, slots=True)
class ProgressObservation:
    """Replayable attempt identity and its concrete observed result."""

    attempt_id: str
    observation_id: str
    operation: str
    executable: str
    targets: tuple[str, ...]
    source_revision: str
    result_kind: ActionResultKind
    output_sha256: str
    command_sha256: str
    declared_check_id: str = ""
    diagnostic_fingerprint: str = ""
    observation_gain: bool = False
    task_progress_gain: bool = False
    contradictory: bool = False

    @classmethod
    def create(
        cls,
        *,
        command: str,
        operation: str,
        executable: str,
        targets: tuple[str, ...],
        source_revision: str,
        result_kind: ActionResultKind,
        output: str,
        declared_check_id: str = "",
        diagnostic_fingerprint: str = "",
        observation_gain: bool = False,
        task_progress_gain: bool = False,
        contradictory: bool = False,
    ) -> ProgressObservation:
        normalized_targets = tuple(
            sorted({str(target).replace("\\", "/") for target in targets if target})
        )
        # Operation/target classification intentionally abstains on opaque
        # programs and does not retain search patterns as targets.  Without
        # the exact command identity, distinct searches and experiments
        # therefore collapse into a false repeated-action loop.  Hash the
        # exact bytes rather than persisting its text in private progress
        # state.  Do not whitespace-normalize: whitespace inside a quoted or
        # opaque program can be semantically significant.
        command_sha256 = hashlib.sha256(
            str(command).encode("utf-8", "replace")
        ).hexdigest()
        attempt_material = json.dumps(
            [
                command_sha256,
                str(operation),
                str(executable).rsplit("/", 1)[-1],
                normalized_targets,
                str(source_revision),
                str(declared_check_id),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        attempt_id = hashlib.sha256(attempt_material.encode("utf-8", "replace")).hexdigest()
        output_sha256 = hashlib.sha256(str(output).encode("utf-8", "replace")).hexdigest()
        observation_material = json.dumps(
            [
                attempt_id,
                result_kind.value,
                output_sha256,
                str(diagnostic_fingerprint),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return cls(
            attempt_id=attempt_id,
            observation_id=hashlib.sha256(
                observation_material.encode("utf-8", "replace")
            ).hexdigest(),
            operation=str(operation),
            executable=str(executable).rsplit("/", 1)[-1],
            targets=normalized_targets,
            source_revision=str(source_revision),
            result_kind=result_kind,
            output_sha256=output_sha256,
            command_sha256=command_sha256,
            declared_check_id=str(declared_check_id),
            diagnostic_fingerprint=str(diagnostic_fingerprint),
            observation_gain=bool(observation_gain),
            task_progress_gain=bool(task_progress_gain),
            contradictory=bool(contradictory),
        )


@dataclass(frozen=True)
class ProgressTransition:
    prior: str
    current: str
    reason: str
    streak: int
    signature: str


@dataclass(frozen=True, slots=True)
class StallAggregateFact:
    """Bounded deterministic description of an observed no-progress cycle."""

    fact_id: str
    state: str
    repeated_operation: str
    concrete_targets: tuple[str, ...]
    repeat_count: int
    last_returncode: int | None
    timeout_observed: bool
    source_revision: str
    remaining_calls: int
    remaining_seconds: float | None
    unresolved_anchors: tuple[str, ...]
    evidence_action: int
    eligible_call: int

    @staticmethod
    def _bounded(values: tuple[str, ...], *, count: int = 2, chars: int = 72) -> tuple[str, ...]:
        return tuple(" ".join(str(value).split())[:chars] for value in values[:count] if value)

    @classmethod
    def create(
        cls,
        *,
        state: str,
        repeated_operation: str,
        concrete_targets: tuple[str, ...],
        repeat_count: int,
        last_returncode: int | None,
        timeout_observed: bool,
        source_revision: str,
        remaining_calls: int,
        remaining_seconds: float | None,
        unresolved_anchors: tuple[str, ...],
        evidence_action: int,
        eligible_call: int,
    ) -> StallAggregateFact:
        targets = cls._bounded(concrete_targets)
        anchors = cls._bounded(unresolved_anchors)
        material = json.dumps(
            [
                state,
                repeated_operation,
                targets,
                repeat_count,
                last_returncode,
                timeout_observed,
                source_revision,
                remaining_calls,
                anchors,
                evidence_action,
                eligible_call,
            ],
            separators=(",", ":"),
        )
        return cls(
            fact_id="stall-" + hashlib.sha256(material.encode()).hexdigest()[:20],
            state=str(state),
            repeated_operation=" ".join(str(repeated_operation).split())[:48],
            concrete_targets=targets,
            repeat_count=max(1, int(repeat_count)),
            last_returncode=last_returncode,
            timeout_observed=bool(timeout_observed),
            source_revision=str(source_revision),
            remaining_calls=max(0, int(remaining_calls)),
            remaining_seconds=(
                None if remaining_seconds is None else max(0.0, float(remaining_seconds))
            ),
            unresolved_anchors=anchors,
            evidence_action=max(0, int(evidence_action)),
            eligible_call=max(1, int(eligible_call)),
        )

    def render(self) -> str:
        pieces = [
            f"Execution state {self.state}",
            f"operation={self.repeated_operation or 'unknown'} repeated={self.repeat_count}",
        ]
        if self.concrete_targets:
            pieces.append("targets=" + ",".join(self.concrete_targets))
        if self.last_returncode is not None:
            pieces.append(f"last_rc={self.last_returncode}")
        if self.timeout_observed:
            pieces.append("timeout_observed=true")
        if self.unresolved_anchors:
            pieces.append("unresolved=" + ",".join(self.unresolved_anchors))
        pieces.append(f"remaining_calls={self.remaining_calls}")
        if self.remaining_seconds is not None:
            pieces.append(f"remaining_seconds={int(self.remaining_seconds)}")
        rendered = "; ".join(pieces) + "."
        # Construction bounds make this exceptional; abstain rather than
        # truncate a source-backed fact into a misleading fragment.
        return rendered if len(rendered) <= 320 else ""


class ProgressLedger:
    """Track whether observations add information or mutate task state."""

    def __init__(
        self,
        *,
        stall_threshold: int = 3,
        cycle_threshold: int | None = None,
    ) -> None:
        self.state = "PROGRESS"
        self.stall_threshold = max(2, int(stall_threshold))
        self.cycle_threshold = max(
            self.stall_threshold,
            int(cycle_threshold if cycle_threshold is not None else stall_threshold),
        )
        self._last_signature = ""
        self._repeat_streak = 0
        self._signature_counts: dict[str, int] = {}
        self._history: list[str] = []
        self.same_state_updates_suppressed = 0

    def observe(
        self,
        signature: str,
        *,
        information_gain: bool,
        changed: bool,
        semantic_gain: bool | None = None,
        is_error: bool,
        contradictory: bool | None = None,
    ) -> ProgressTransition | None:
        prior = self.state
        # ``changed`` describes workspace activity and is still useful to the
        # host's stale-batch safety barrier.  It is not proof that the task
        # moved forward: fixture resets and scratch edits are common in long
        # trajectories.  Callers may therefore provide the narrower semantic
        # signal explicitly; legacy callers retain the old behavior.
        if semantic_gain is None:
            semantic_gain = changed
        # Budget risk is a task-state condition, not an observation-novelty
        # condition.  A fresh scratch result or unvalidated patch must not
        # clear it; only a proven semantic gain can recover the controller.
        if prior == "BUDGET_RISK" and not semantic_gain:
            if signature:
                self._signature_counts[signature] = (
                    self._signature_counts.get(signature, 0) + 1
                )
                self._history.append(signature)
                self._history = self._history[-self.cycle_threshold :]
                self._repeat_streak = (
                    self._repeat_streak + 1 if signature == self._last_signature else 1
                )
                self._last_signature = signature
            return None
        if semantic_gain:
            self._signature_counts.clear()
            self._history.clear()
            self._repeat_streak = 0
            self._last_signature = signature
            self.state = "RECOVERED" if prior in {
                "STALLED", "CONTRADICTED", "BUDGET_RISK"
            } else "PROGRESS"
            reason = "material_state_change"
        elif information_gain or not signature:
            if signature:
                self._signature_counts[signature] = self._signature_counts.get(signature, 0) + 1
                self._history.append(signature)
                self._history = self._history[-self.cycle_threshold :]
            self._repeat_streak = 1 if signature else 0
            self._last_signature = signature
            self.state = "RECOVERED" if prior in {
                "STALLED", "CONTRADICTED", "BUDGET_RISK"
            } else "PROGRESS"
            reason = "new_information"
        else:
            count = self._signature_counts.get(signature, 1) + 1
            self._signature_counts[signature] = count
            self._history.append(signature)
            self._history = self._history[-self.cycle_threshold :]
            self._repeat_streak = (
                self._repeat_streak + 1 if signature == self._last_signature else 1
            )
            self._last_signature = signature
            cyclic = bool(
                len(self._history) >= self.cycle_threshold
                and len(set(self._history)) > 1
                and any(
                    all(
                        self._history[index] == self._history[index % period]
                        for index in range(len(self._history))
                    )
                    for period in range(2, min(4, len(self._history)))
                )
            )
            repeated = self._repeat_streak >= self.stall_threshold
            nonconsecutive = count >= self.cycle_threshold
            if repeated or cyclic or nonconsecutive:
                source_contradiction = (
                    bool(is_error)
                    if contradictory is None
                    else bool(contradictory)
                )
                self.state = (
                    "CONTRADICTED" if source_contradiction else "STALLED"
                )
                reason = (
                    "repeated_failure_without_information"
                    if source_contradiction
                    else (
                        "cyclic_actions_without_information"
                        if cyclic and not repeated
                        else "repeated_action_without_information"
                    )
                )
            else:
                self.state = "PROGRESS"
                reason = "no_new_information"
        # Same-state updates are still counted privately above, but they do
        # not create another controller edge or another provider payload.
        if self.state == prior:
            if self.state in {"STALLED", "CONTRADICTED", "BUDGET_RISK"}:
                self.same_state_updates_suppressed += 1
            return None
        return ProgressTransition(
            prior=prior,
            current=self.state,
            reason=reason,
            streak=self._repeat_streak,
            signature=signature,
        )

    def budget_risk(
        self,
        *,
        iteration: int,
        limit: int,
        iteration_risk_ratio: float = 0.8,
        unresolved: bool = False,
        remaining_seconds: float | None = None,
        time_risk_threshold_seconds: float | None = None,
    ) -> ProgressTransition | None:
        risk_ratio = min(1.0, max(0.01, float(iteration_risk_ratio)))
        iteration_risk = limit > 0 and iteration >= max(1, int(limit * risk_ratio))
        time_risk = bool(
            remaining_seconds is not None
            and time_risk_threshold_seconds is not None
            and remaining_seconds <= max(0.0, time_risk_threshold_seconds)
        )
        if not iteration_risk and not time_risk:
            return None
        if self.state == "BUDGET_RISK":
            return None
        if (
            unresolved
            or self.state in {"STALLED", "CONTRADICTED"}
        ):
            prior = self.state
            self.state = "BUDGET_RISK"
            return ProgressTransition(
                prior=prior,
                current=self.state,
                reason=(
                    "unresolved_contract_near_time_limit"
                    if unresolved and time_risk and not iteration_risk
                    else "unresolved_contract_near_iteration_limit"
                    if unresolved
                    else "unresolved_stall_near_time_limit"
                    if time_risk and not iteration_risk
                    else "unresolved_stall_near_iteration_limit"
                ),
                streak=self._repeat_streak,
                signature=self._last_signature,
            )
        return None
