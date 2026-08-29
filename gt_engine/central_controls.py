"""Consumer and effect contracts for the host-owned central runtime.

Every produced receipt is routed immediately to a registered consumer.  The
consumer declares an effect kind and whether the effect may contribute to the
first model request after its evidence. Most effects stay internal and cost zero
prompt tokens; related novel grounded facts are coalesced into one observation
enrichment. Consumers never block or cancel Mini-SWE actions.

This module holds the operational role for action-bound direct feature IDs.
The bootstrap-bound select_catalog consumer is the persistent-state apply
boundary. A feature without a registered consumer is not operational even
when its producer fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EffectKind(StrEnum):
    CONTRACT_STATE_UPDATE = "CONTRACT_STATE_UPDATE"
    CONTEXT_RESLOT = "CONTEXT_RESLOT"
    IMPACT_SET_UPDATE = "IMPACT_SET_UPDATE"
    VALIDATION_SCHEDULE = "VALIDATION_SCHEDULE"
    AUTO_VALIDATION = "AUTO_VALIDATION"
    FAILURE_STATE_TRANSITION = "FAILURE_STATE_TRANSITION"
    SYNTAX_STATE_UPDATE = "SYNTAX_STATE_UPDATE"
    SUBMIT_RISK_UPDATE = "SUBMIT_RISK_UPDATE"
    CERTIFY_PASS = "CERTIFY_PASS"
    NO_OP_WITH_REASON = "NO_OP_WITH_REASON"


@dataclass(frozen=True, slots=True)
class FeatureEffect:
    """A concrete controller action produced by one feature consumer."""

    feature_id: str
    receipt_id: str
    effect_kind: EffectKind
    effect_action: dict[str, Any]
    required_before_action: int | None
    model_visible: bool
    evidence_action: int
    delivery_status: str = "pending"
    delivery_reason: str = ""
    evidence_call: int = 0
    applied_after_action: int | None = None
    delivered_before_call: int | None = None
    predecided_actions_executed_after_evidence: int = 0
    predecided_actions_cancelled: int = 0
    late: bool = False
    predictive: bool = False
    expiry_call: int | None = None

    def as_dict(self) -> dict[str, Any]:
        applied = self.applied_after_action
        required = self.required_before_action
        return {
            "feature_id": self.feature_id,
            "receipt_id": self.receipt_id,
            "effect_kind": self.effect_kind.value,
            "effect_action": self.effect_action,
            "model_visible": self.model_visible,
            "delivery_status": self.delivery_status,
            "delivery_reason": self.delivery_reason,
            "evidence_action": self.evidence_action,
            "evidence_call": self.evidence_call,
            "applied_after_action": applied,
            "delivered_before_call": self.delivered_before_call,
            "required_before_action": required,
            "predecided_actions_executed_after_evidence": (
                self.predecided_actions_executed_after_evidence
            ),
            "predecided_actions_cancelled": self.predecided_actions_cancelled,
            "late": self.late,
            "predictive": self.predictive,
            "expiry_call": self.expiry_call,
            "evidence_before_effect": applied is not None and applied >= self.evidence_action,
            "effect_before_next_action": required is None or (
                applied is not None and applied <= required
            ),
            "non_late": not self.late,
        }


@dataclass(frozen=True, slots=True)
class ConsumerSpec:
    """Static operational role for one feature consumer."""

    feature_id: str
    effect_kind: EffectKind
    model_visible: bool
    required_before_next_action: bool
    reason: str


CONSUMER_SPECS: dict[str, ConsumerSpec] = {
    "obligations": ConsumerSpec(
        "obligations",
        EffectKind.CONTRACT_STATE_UPDATE,
        False,
        False,
        "parse contract ledger of required outputs, constraints, declared checks",
    ),
    "localization": ConsumerSpec(
        "localization",
        EffectKind.CONTEXT_RESLOT,
        False,
        False,
        "store ranked file/line/symbol anchors and reslot bounded context",
    ),
    "def_partition": ConsumerSpec(
        "def_partition",
        EffectKind.IMPACT_SET_UPDATE,
        False,
        False,
        "separate definition anchors from reference anchors",
    ),
    "caller_contract": ConsumerSpec(
        "caller_contract",
        EffectKind.IMPACT_SET_UPDATE,
        False,
        False,
        "store verified callers and signatures for impact validation",
    ),
    "newfile_precedent": ConsumerSpec(
        "newfile_precedent",
        EffectKind.IMPACT_SET_UPDATE,
        True,
        False,
        "validate new-file placement and registration against a concrete precedent",
    ),
    "covering_red": ConsumerSpec(
        "covering_red",
        EffectKind.FAILURE_STATE_TRANSITION,
        True,
        False,
        "create grounded failure state for the next model decision",
    ),
    "recovery": ConsumerSpec(
        "recovery",
        EffectKind.FAILURE_STATE_TRANSITION,
        True,
        False,
        "select one discriminating alternate action after an exact repeat",
    ),
    "signature_delta": ConsumerSpec(
        "signature_delta",
        EffectKind.VALIDATION_SCHEDULE,
        True,
        False,
        "schedule caller and targeted check selection from a signature delta",
    ),
    "submit_refusal": ConsumerSpec(
        "submit_refusal",
        EffectKind.SUBMIT_RISK_UPDATE,
        True,
        False,
        "record current grounded submission risk without blocking Mini-SWE",
    ),
    "syntax_result": ConsumerSpec(
        "syntax_result",
        EffectKind.SYNTAX_STATE_UPDATE,
        True,
        False,
        "record a fresh syntax result for the next model decision",
    ),
    "GT_CERT_DELIVERY": ConsumerSpec(
        "GT_CERT_DELIVERY",
        EffectKind.CERTIFY_PASS,
        False,
        False,
        "certify contract obligations and fresh checks bound to source revision",
    ),
    "GT_CHANGE_SURFACE": ConsumerSpec(
        "GT_CHANGE_SURFACE",
        EffectKind.IMPACT_SET_UPDATE,
        False,
        False,
        "maintain authored/derived/deliverable/unknown change sets",
    ),
    "GT_EDIT_CHECK": ConsumerSpec(
        "GT_EDIT_CHECK",
        EffectKind.VALIDATION_SCHEDULE,
        True,
        False,
        "source-bound validation scheduling and optional auto-check",
    ),
    "GT_HYPOTHESIS": ConsumerSpec(
        "GT_HYPOTHESIS",
        EffectKind.FAILURE_STATE_TRANSITION,
        False,
        False,
        "deterministic failure state with attempted action and next predicate",
    ),
    "GT_LOC_RESLOT": ConsumerSpec(
        "GT_LOC_RESLOT",
        EffectKind.CONTEXT_RESLOT,
        True,
        False,
        "change the bounded context slot to ranked anchors",
    ),
    "GT_PATCH_DELTA": ConsumerSpec(
        "GT_PATCH_DELTA",
        EffectKind.VALIDATION_SCHEDULE,
        False,
        False,
        "compute changed symbols/paths and select impacted checks",
    ),
    "GT_SS_SUBMIT_RED": ConsumerSpec(
        "GT_SS_SUBMIT_RED",
        EffectKind.SUBMIT_RISK_UPDATE,
        False,
        False,
        "latch source-bound red submission risk without blocking Mini-SWE",
    ),
}


def consumer_spec_for(feature_id: str) -> ConsumerSpec | None:
    return CONSUMER_SPECS.get(feature_id)
