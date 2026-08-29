"""Machine-auditable contract for the active central GroundTruth engine.

This registry does not activate components.  It binds the executable feature
inventory and lifecycle placement to the component proof gates so reports
cannot confuse a historical module with the active Mini-SWE runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from gt_engine.central_runtime import (
    CENTRAL_FEATURE_BOUNDARIES,
    CENTRAL_FEATURE_IDS,
    CENTRAL_FEATURES,
)
from gt_engine.preflight import PREFLIGHT_FEATURE_PLACEMENT


class ComponentStage(StrEnum):
    PRE_ACTION = "pre_action"
    POST_ACTION = "post_action"
    PROVIDER_BOUNDARY = "provider_boundary"
    CONTROLLER = "controller"
    AUDIT = "audit"


class DefaultVisibility(StrEnum):
    PRIVATE = "private"
    CONDITIONAL_PROVIDER = "conditional_provider"
    PROVIDER_VIEW = "provider_view"


@dataclass(frozen=True, slots=True)
class ComponentContract:
    component_id: str
    stage: ComponentStage
    output: str
    visibility: DefaultVisibility
    proof_gate: str


ACTIVE_GT_COMPONENTS: dict[str, ComponentContract] = {
    item.component_id: item
    for item in (
        ComponentContract(
            "workspace_sensor",
            ComponentStage.POST_ACTION,
            "workspace/source revision and bounded changed paths",
            DefaultVisibility.PRIVATE,
            "source revision and incremental graph tests",
        ),
        ComponentContract(
            "repository_graph",
            ComponentStage.POST_ACTION,
            "certified definitions, references, calls, imports, and file nodes",
            DefaultVisibility.PRIVATE,
            "verify_gt_index_runtime plus graph applicability gate",
        ),
        ComponentContract(
            "hybrid_retrieval",
            ComponentStage.PROVIDER_BOUNDARY,
            "bounded ranked repository evidence or abstention",
            DefaultVisibility.CONDITIONAL_PROVIDER,
            "ARB metrics plus live Snowflake parity witness",
        ),
        ComponentContract(
            "preflight",
            ComponentStage.PRE_ACTION,
            "typed proposal decision and receipt",
            DefaultVisibility.PRIVATE,
            "ordered preflight tests plus active release-profile contract",
        ),
        ComponentContract(
            "postflight_features",
            ComponentStage.POST_ACTION,
            "18 direct-feature receipts and effects",
            DefaultVisibility.CONDITIONAL_PROVIDER,
            "all-18 census and lifecycle applicability audit",
        ),
        ComponentContract(
            "contribution_compiler",
            ComponentStage.PROVIDER_BOUNDARY,
            "one disposition for every evidence/controller contribution",
            DefaultVisibility.PRIVATE,
            "candidate count equals accounted count; duplicate suppression",
        ),
        ComponentContract(
            "provider_delivery",
            ComponentStage.PROVIDER_BOUNDARY,
            "exact next-request evidence with hash and message indices",
            DefaultVisibility.PROVIDER_VIEW,
            "first-eligible/non-predictive/provider-hash audit",
        ),
        ComponentContract(
            "validation_classifier",
            ComponentStage.POST_ACTION,
            "one immutable validation classification per action",
            DefaultVisibility.PRIVATE,
            "terminal-owner and shell-pipeline tests",
        ),
        ComponentContract(
            "completion_controller",
            ComponentStage.CONTROLLER,
            "fail-open completion certificate and optional submit",
            DefaultVisibility.PRIVATE,
            "predicate coverage and outcome-preservation replay",
        ),
        ComponentContract(
            "progress_controller",
            ComponentStage.CONTROLLER,
            "stall/budget state and bounded progress fact",
            DefaultVisibility.CONDITIONAL_PROVIDER,
            "novelty and monotonic budget-risk tests",
        ),
        ComponentContract(
            "context_compactor",
            ComponentStage.PROVIDER_BOUNDARY,
            "bounded provider view preserving assistant reasoning",
            DefaultVisibility.PROVIDER_VIEW,
            "request-budget and exact-prefix preservation tests",
        ),
        ComponentContract(
            "replay_capture",
            ComponentStage.AUDIT,
            "content-addressed provider request/response bundle",
            DefaultVisibility.PRIVATE,
            "bundle hash/completeness verifier",
        ),
        ComponentContract(
            "persistent_execution_state",
            ComponentStage.PROVIDER_BOUNDARY,
            "graph-first living state with one bootstrap and selective material frames",
            DefaultVisibility.CONDITIONAL_PROVIDER,
            "graph-first bootstrap plus materiality and delivery audits",
        ),
        ComponentContract(
            "repository_context_engine",
            ComponentStage.PROVIDER_BOUNDARY,
            "semantic, directed execution, and diff-impact composition over certified evidence",
            DefaultVisibility.CONDITIONAL_PROVIDER,
            "revision, uncertainty, direction, deduplication, delivery, and release audits",
        ),
    )
}


@dataclass(frozen=True, slots=True)
class FeatureComponentContract:
    feature_id: str
    kind: str
    owner: str
    trigger: str
    postflight_only: bool
    required_inputs: tuple[str, ...]
    decision: str
    delivery_contract: str


_FEATURE_ROWS = {str(item["id"]): item for item in CENTRAL_FEATURES}
FEATURE_COMPONENT_CONTRACTS: dict[str, FeatureComponentContract] = {
    feature_id: FeatureComponentContract(
        feature_id=feature_id,
        kind=str(_FEATURE_ROWS[feature_id]["kind"]),
        owner=str(_FEATURE_ROWS[feature_id]["owner"]),
        trigger=(
            "/".join(CENTRAL_FEATURE_BOUNDARIES[feature_id])
            if isinstance(CENTRAL_FEATURE_BOUNDARIES[feature_id], tuple)
            else str(CENTRAL_FEATURE_BOUNDARIES[feature_id])
        ),
        postflight_only=PREFLIGHT_FEATURE_PLACEMENT[feature_id].postflight_only,
        required_inputs=PREFLIGHT_FEATURE_PLACEMENT[feature_id].required_inputs,
        decision=PREFLIGHT_FEATURE_PLACEMENT[feature_id].decision,
        delivery_contract=(
            "postflight_grounded_only"
            if PREFLIGHT_FEATURE_PLACEMENT[feature_id].postflight_only
            else "private_or_first_eligible_grounded"
        ),
    )
    for feature_id in CENTRAL_FEATURE_IDS
    if feature_id in PREFLIGHT_FEATURE_PLACEMENT
}
FEATURE_COMPONENT_CONTRACTS["select_catalog"] = FeatureComponentContract(
    feature_id="select_catalog",
    kind=str(_FEATURE_ROWS["select_catalog"]["kind"]),
    owner="persistent_execution_state",
    trigger="task_start",
    postflight_only=False,
    required_inputs=(
        "complete_revision_bound_catalog",
        "sealed_provider_request",
        "visible_stable_item_ids",
    ),
    decision="select only visible catalog IDs or fail closed",
    delivery_contract="sealed_bootstrap_provider_dispatch",
)


def audit_component_registry() -> dict[str, Any]:
    component_ids = [item.component_id for item in ACTIVE_GT_COMPONENTS.values()]
    duplicate_components = sorted(
        {item for item in component_ids if component_ids.count(item) > 1}
    )
    missing_feature_placements = sorted(
        set(CENTRAL_FEATURE_IDS) - set(FEATURE_COMPONENT_CONTRACTS)
    )
    valid_delivery_contracts = {
        "postflight_grounded_only",
        "private_or_first_eligible_grounded",
        "sealed_bootstrap_provider_dispatch",
    }
    invalid_delivery_contracts = sorted(
        feature_id
        for feature_id, contract in FEATURE_COMPONENT_CONTRACTS.items()
        if contract.delivery_contract not in valid_delivery_contracts
        or not contract.required_inputs
        or not contract.trigger
    )
    ready = not (
        duplicate_components
        or missing_feature_placements
        or invalid_delivery_contracts
        or len(FEATURE_COMPONENT_CONTRACTS) != len(CENTRAL_FEATURE_IDS)
    )
    return {
        "schema": "gt.component_registry.v1",
        "ready": ready,
        "component_count": len(ACTIVE_GT_COMPONENTS),
        "feature_count": len(FEATURE_COMPONENT_CONTRACTS),
        "duplicate_components": duplicate_components,
        "missing_feature_placements": missing_feature_placements,
        "invalid_delivery_contracts": invalid_delivery_contracts,
        "components": [asdict(item) for item in ACTIVE_GT_COMPONENTS.values()],
        "features": [asdict(item) for item in FEATURE_COMPONENT_CONTRACTS.values()],
    }


__all__ = [
    "ACTIVE_GT_COMPONENTS",
    "FEATURE_COMPONENT_CONTRACTS",
    "ComponentContract",
    "ComponentStage",
    "DefaultVisibility",
    "FeatureComponentContract",
    "audit_component_registry",
]
