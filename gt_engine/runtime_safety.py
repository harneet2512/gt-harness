"""Solve-safety and measurement contracts for the active GT runtime.

Mechanical completeness certifies whether a treatment observation is valid.
It is deliberately not an operational permission check: optional GT
intelligence and audit facilities must never prevent the baseline solver from
dispatching a provider request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TreatmentValidity(StrEnum):
    """Analytical validity of GT treatment evidence."""

    VALID = "VALID"
    INVALID = "INVALID"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ActionActor(StrEnum):
    """Actor responsible for one decision or host execution."""

    MODEL_DECISION = "MODEL_DECISION"
    TOOL_ACTION = "TOOL_ACTION"
    CONTROLLER_ACTION = "CONTROLLER_ACTION"
    SUBSTRATE_PROBE = "SUBSTRATE_PROBE"
    HOST_OTHER = "HOST_OTHER"


class MetricState(StrEnum):
    """Whether a reported metric has an interpretable value."""

    MEASURED = "MEASURED"
    NOT_MEASURED = "NOT_MEASURED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ProviderDispatchAssessment:
    """Dispatch decision at the mechanical-certification seam."""

    dispatch_allowed: bool
    treatment_validity: TreatmentValidity
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.provider_dispatch_assessment.v1",
            "dispatch_allowed": self.dispatch_allowed,
            "treatment_validity": self.treatment_validity.value,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class RateMeasurement:
    """A rate together with the counts that make it interpretable."""

    state: MetricState
    numerator: int
    denominator: int
    value: float | None
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.rate_measurement.v1",
            "state": self.state.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class RatioMeasurement:
    """A non-negative ratio or average with an explicit denominator state."""

    state: MetricState
    numerator: float
    denominator: float
    value: float | None
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.ratio_measurement.v1",
            "state": self.state.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class ActionAccounting:
    """Disjoint actor counts with host-execution conservation."""

    counts: Mapping[ActionActor, int]
    host_execution_total: int
    conservation_valid: bool
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.action_accounting.v1",
            "counts": {actor.value: int(self.counts.get(actor, 0)) for actor in ActionActor},
            "host_execution_total": self.host_execution_total,
            "conservation_valid": self.conservation_valid,
            "reason_codes": list(self.reason_codes),
        }


def assess_provider_dispatch(
    mechanical_barrier: Mapping[str, Any] | None,
    *,
    fail_closed: bool = False,
) -> ProviderDispatchAssessment:
    """Allow solving while recording whether the GT treatment is valid.

    Request-budget and deadline checks remain operational safety checks in the
    caller.  This function handles only optional GT certification evidence.
    """

    if mechanical_barrier is None:
        return ProviderDispatchAssessment(
            dispatch_allowed=True,
            treatment_validity=TreatmentValidity.NOT_APPLICABLE,
        )
    failures = tuple(
        dict.fromkeys(str(item) for item in mechanical_barrier.get("failures") or () if item)
    )
    if mechanical_barrier.get("status") == "PASS" and not failures:
        return ProviderDispatchAssessment(
            dispatch_allowed=True,
            treatment_validity=TreatmentValidity.VALID,
        )
    return ProviderDispatchAssessment(
        dispatch_allowed=not fail_closed,
        treatment_validity=TreatmentValidity.INVALID,
        reason_codes=failures or ("mechanical_completeness_not_passed",),
    )


def measure_rate(
    numerator: int,
    denominator: int,
    *,
    precision: int = 6,
) -> RateMeasurement:
    """Return a non-vacuous rate measurement."""

    numerator = int(numerator)
    denominator = int(denominator)
    if numerator < 0 or denominator < 0:
        return RateMeasurement(
            state=MetricState.INVALID,
            numerator=numerator,
            denominator=denominator,
            value=None,
            reason_codes=("negative_count",),
        )
    if numerator > denominator:
        return RateMeasurement(
            state=MetricState.INVALID,
            numerator=numerator,
            denominator=denominator,
            value=None,
            reason_codes=("numerator_exceeds_denominator",),
        )
    if denominator == 0:
        return RateMeasurement(
            state=MetricState.NOT_MEASURED,
            numerator=numerator,
            denominator=denominator,
            value=None,
            reason_codes=("zero_denominator",),
        )
    return RateMeasurement(
        state=MetricState.MEASURED,
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, precision),
    )


def measure_ratio(
    numerator: float,
    denominator: float,
    *,
    precision: int = 6,
) -> RatioMeasurement:
    """Measure an average/ratio without treating zero opportunity as zero."""

    numerator = float(numerator)
    denominator = float(denominator)
    if numerator < 0 or denominator < 0:
        return RatioMeasurement(
            state=MetricState.INVALID,
            numerator=numerator,
            denominator=denominator,
            value=None,
            reason_codes=("negative_value",),
        )
    if denominator == 0:
        return RatioMeasurement(
            state=MetricState.NOT_MEASURED,
            numerator=numerator,
            denominator=denominator,
            value=None,
            reason_codes=("zero_denominator",),
        )
    return RatioMeasurement(
        state=MetricState.MEASURED,
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, precision),
    )


def build_action_accounting(
    *,
    model_decisions: int,
    tool_actions: int,
    controller_actions: int,
    substrate_probes: int,
    actual_environment_execs: int,
) -> ActionAccounting:
    """Build disjoint action counts and prove host execution conservation."""

    supplied = {
        ActionActor.MODEL_DECISION: int(model_decisions),
        ActionActor.TOOL_ACTION: int(tool_actions),
        ActionActor.CONTROLLER_ACTION: int(controller_actions),
        ActionActor.SUBSTRATE_PROBE: int(substrate_probes),
    }
    actual_environment_execs = int(actual_environment_execs)
    reason_codes: list[str] = []
    if actual_environment_execs < 0 or any(value < 0 for value in supplied.values()):
        reason_codes.append("negative_action_count")
    classified_host = sum(
        supplied[actor]
        for actor in (
            ActionActor.TOOL_ACTION,
            ActionActor.CONTROLLER_ACTION,
            ActionActor.SUBSTRATE_PROBE,
        )
    )
    host_other = actual_environment_execs - classified_host
    if host_other < 0:
        reason_codes.append("host_actor_counts_exceed_executions")
    counts = {
        **supplied,
        ActionActor.HOST_OTHER: max(0, host_other),
    }
    return ActionAccounting(
        counts=counts,
        host_execution_total=actual_environment_execs,
        conservation_valid=(
            not reason_codes
            and classified_host + host_other == actual_environment_execs
        ),
        reason_codes=tuple(reason_codes),
    )


__all__ = [
    "ActionAccounting",
    "ActionActor",
    "MetricState",
    "ProviderDispatchAssessment",
    "RateMeasurement",
    "RatioMeasurement",
    "TreatmentValidity",
    "assess_provider_dispatch",
    "build_action_accounting",
    "measure_rate",
    "measure_ratio",
]
