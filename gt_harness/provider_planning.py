"""Deterministic, authority-safe provider context selection.

The context compiler owns repository truth.  This module owns the separate
question of which of those proof-carrying claims fit in the provider budget.
It deliberately knows nothing about benchmark task IDs, repositories, or
model behavior.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType


class ClaimAuthority(IntEnum):
    """Proof strength. Retrieval ranking cannot upgrade this value."""

    RANK_SUPPORT = 10
    STRUCTURAL_PROJECTION = 20
    SOURCE_SEMANTIC = 30
    CERTIFIED_RELATION = 40
    EXACT_IDENTITY = 50


class ClaimRole(StrEnum):
    EDIT = "EDIT"
    IMPLEMENTATION_OWNER = "IMPLEMENTATION_OWNER"
    NEW_FILE = "NEW_FILE"
    PUBLIC_SURFACE = "PUBLIC_SURFACE"
    INTEGRATION = "INTEGRATION"
    AFFECTED_TEST = "AFFECTED_TEST"
    VALIDATION = "VALIDATION"
    RELATION = "RELATION"
    PROCESS = "PROCESS"
    IMPACT = "IMPACT"
    SEMANTIC = "SEMANTIC"
    ARCHITECTURE = "ARCHITECTURE"
    AMBIGUITY = "AMBIGUITY"
    INSPECTION = "INSPECTION"
    UNCERTAINTY = "UNCERTAINTY"


class OmissionReason(StrEnum):
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    TOKEN_BUDGET = "TOKEN_BUDGET"
    REDUNDANT_COVERAGE = "REDUNDANT_COVERAGE"
    WEAKER_AUTHORITY = "WEAKER_AUTHORITY"


_ROLE_PRIORITY: Mapping[ClaimRole, int] = MappingProxyType(
    {
        ClaimRole.EDIT: 110,
        ClaimRole.IMPLEMENTATION_OWNER: 100,
        ClaimRole.NEW_FILE: 95,
        ClaimRole.PUBLIC_SURFACE: 90,
        ClaimRole.INTEGRATION: 85,
        ClaimRole.AFFECTED_TEST: 80,
        ClaimRole.RELATION: 78,
        ClaimRole.PROCESS: 75,
        ClaimRole.IMPACT: 70,
        ClaimRole.VALIDATION: 60,
        ClaimRole.AMBIGUITY: 50,
        ClaimRole.ARCHITECTURE: 45,
        ClaimRole.SEMANTIC: 40,
        ClaimRole.INSPECTION: 20,
        ClaimRole.UNCERTAINTY: 10,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderClaim:
    claim_id: str
    role: ClaimRole
    authority: ClaimAuthority
    requirement_ids: tuple[str, ...]
    estimated_tokens: int
    source_revision: str
    graph_revision: str
    # Stable rank assigned by the compiler/serializer before budget planning.
    # Lower is stronger.  This is explicit input data, so the planner remains
    # input-order invariant without throwing away upstream relevance.
    selection_rank: int = 0

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("provider claim ID must not be empty")
        if self.estimated_tokens <= 0:
            raise ValueError("provider claim token cost must be positive")
        if not self.source_revision or not self.graph_revision:
            raise ValueError("provider claim must be bound to source and graph revisions")
        if self.selection_rank < 0:
            raise ValueError("provider claim selection rank must be non-negative")


@dataclass(frozen=True, slots=True)
class OmittedClaim:
    claim_id: str
    reason: OmissionReason


@dataclass(frozen=True, slots=True)
class ProviderPlan:
    selected_claim_ids: tuple[str, ...]
    omitted_claims: tuple[OmittedClaim, ...]
    covered_requirement_ids: tuple[str, ...]
    uncovered_requirement_ids: tuple[str, ...]
    selected_roles: tuple[str, ...]
    estimated_tokens: int
    source_revision: str
    graph_revision: str
    plan_sha256: str

    @property
    def omission_by_claim(self) -> Mapping[str, OmissionReason]:
        return MappingProxyType({item.claim_id: item.reason for item in self.omitted_claims})

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "gt.provider_plan.v1",
            "selected_claim_ids": list(self.selected_claim_ids),
            "omitted_claims": [
                {"claim_id": item.claim_id, "reason": item.reason.value}
                for item in self.omitted_claims
            ],
            "covered_requirement_ids": list(self.covered_requirement_ids),
            "uncovered_requirement_ids": list(self.uncovered_requirement_ids),
            "selected_roles": list(self.selected_roles),
            "estimated_tokens": self.estimated_tokens,
            "source_revision": self.source_revision,
            "graph_revision": self.graph_revision,
            "plan_sha256": self.plan_sha256,
        }

    def as_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


class ProviderContextPlanner:
    """Select claims by proof, requirement coverage, role value, then cost.

    The planner is deliberately input-order invariant.  A provider serializer
    may omit only claims named in ``omitted_claims``; it may never perform a
    second unreceipted ranking or truncation pass.
    """

    @staticmethod
    def _valid(claim: ProviderClaim) -> bool:
        return not (
            claim.role is ClaimRole.EDIT
            and claim.authority is not ClaimAuthority.EXACT_IDENTITY
        )

    def plan(
        self,
        claims: Iterable[ProviderClaim],
        *,
        requirement_ids: Iterable[str],
        token_budget: int,
    ) -> ProviderPlan:
        budget = max(0, int(token_budget))
        rows = tuple(claims)
        ids = [item.claim_id for item in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("provider claim IDs must be unique")
        revisions = {(item.source_revision, item.graph_revision) for item in rows}
        if len(revisions) > 1:
            raise ValueError("provider claims from mixed repository generations")
        source_revision, graph_revision = next(iter(revisions), ("", ""))
        required = tuple(dict.fromkeys(str(item) for item in requirement_ids if str(item)))
        required_set = frozenset(required)
        selected: list[ProviderClaim] = []
        selected_ids: set[str] = set()
        covered: set[str] = set()
        roles: set[ClaimRole] = set()
        omitted: dict[str, OmissionReason] = {}
        remaining = budget

        candidates: list[ProviderClaim] = []
        for claim in rows:
            if not self._valid(claim):
                omitted[claim.claim_id] = OmissionReason.INVALID_AUTHORITY
            else:
                candidates.append(claim)

        while candidates:
            exact_edit_selected = any(
                item.role is ClaimRole.EDIT
                and item.authority is ClaimAuthority.EXACT_IDENTITY
                for item in selected
            )
            decision_selected = any(
                item.authority is not ClaimAuthority.RANK_SUPPORT
                or bool(item.requirement_ids)
                for item in selected
            )
            fitting = [
                item
                for item in candidates
                if item.estimated_tokens <= remaining
                and not (
                    exact_edit_selected
                    and item.role is ClaimRole.IMPLEMENTATION_OWNER
                    and item.authority is ClaimAuthority.RANK_SUPPORT
                )
                and not (
                    decision_selected
                    and item.authority is ClaimAuthority.RANK_SUPPORT
                    and not item.requirement_ids
                )
                and not (
                    decision_selected
                    and item.role
                    in {
                        ClaimRole.IMPLEMENTATION_OWNER,
                        ClaimRole.AMBIGUITY,
                        ClaimRole.INSPECTION,
                    }
                    and bool(
                        {
                            ClaimRole.EDIT,
                            ClaimRole.IMPLEMENTATION_OWNER,
                            ClaimRole.AMBIGUITY,
                            ClaimRole.INSPECTION,
                        }
                        & roles
                    )
                    and required_set.intersection(item.requirement_ids) <= covered
                )
                # A rank-only claim that cannot be bound to a typed task
                # requirement is one heuristic answer to one unresolved
                # decision, not proof of an additional decision.  Deliver at
                # most the strongest representative for a role.  Distinct
                # multi-file work remains expressible through separate typed
                # requirement IDs or stronger structural/exact authority.
                and not (
                    item.authority is ClaimAuthority.RANK_SUPPORT
                    and not required_set.intersection(item.requirement_ids)
                    and any(
                        chosen.role is item.role
                        and chosen.authority is ClaimAuthority.RANK_SUPPORT
                        and not required_set.intersection(chosen.requirement_ids)
                        for chosen in selected
                    )
                )
            ]
            if not fitting:
                break

            def order(item: ProviderClaim) -> tuple[object, ...]:
                new_requirements = required_set.intersection(item.requirement_ids) - covered
                # Repository identity resolution is upstream truth, not a
                # retrieval preference.  When the compiler says a named
                # entity has several exact definitions, that bounded set must
                # reach the agent before a heuristic single-file owner for
                # the same unresolved decision.  A unique exact edit/owner
                # remains stronger than an ambiguity set; ranking evidence
                # can never erase declared identity ambiguity.
                localization_resolution = 0
                if item.authority is ClaimAuthority.EXACT_IDENTITY:
                    if item.role is ClaimRole.EDIT:
                        localization_resolution = 4
                    elif item.role is ClaimRole.IMPLEMENTATION_OWNER:
                        localization_resolution = 3
                    elif item.role is ClaimRole.AMBIGUITY:
                        localization_resolution = 2
                return (
                    -localization_resolution,
                    (
                        item.selection_rank
                        if item.role is ClaimRole.IMPLEMENTATION_OWNER
                        and item.authority is ClaimAuthority.RANK_SUPPORT
                        else 1_000_000
                    ),
                    -int(_ROLE_PRIORITY[item.role] >= 60),
                    -int(item.role not in roles),
                    -_ROLE_PRIORITY[item.role],
                    -int(item.authority),
                    -int(bool(new_requirements)),
                    item.selection_rank,
                    -len(new_requirements),
                    item.estimated_tokens,
                    item.claim_id,
                )

            chosen = min(fitting, key=order)
            candidates.remove(chosen)
            selected.append(chosen)
            selected_ids.add(chosen.claim_id)
            remaining -= chosen.estimated_tokens
            covered.update(required_set.intersection(chosen.requirement_ids))
            roles.add(chosen.role)

        for claim in candidates:
            if claim.claim_id in omitted:
                continue
            if (
                any(
                    item.role is ClaimRole.EDIT
                    and item.authority is ClaimAuthority.EXACT_IDENTITY
                    for item in selected
                )
                and claim.role is ClaimRole.IMPLEMENTATION_OWNER
                and claim.authority is ClaimAuthority.RANK_SUPPORT
            ):
                omitted[claim.claim_id] = OmissionReason.WEAKER_AUTHORITY
            elif (
                claim.authority is ClaimAuthority.RANK_SUPPORT
                and not required_set.intersection(claim.requirement_ids)
                and any(
                    chosen.role is claim.role
                    and chosen.authority is ClaimAuthority.RANK_SUPPORT
                    and not required_set.intersection(chosen.requirement_ids)
                    for chosen in selected
                )
            ):
                omitted[claim.claim_id] = OmissionReason.REDUNDANT_COVERAGE
            elif claim.estimated_tokens > remaining:
                omitted[claim.claim_id] = OmissionReason.TOKEN_BUDGET
            elif required_set.intersection(claim.requirement_ids) <= covered:
                omitted[claim.claim_id] = OmissionReason.REDUNDANT_COVERAGE
            else:
                omitted[claim.claim_id] = OmissionReason.WEAKER_AUTHORITY
        for claim in rows:
            if claim.claim_id not in selected_ids and claim.claim_id not in omitted:
                omitted[claim.claim_id] = OmissionReason.TOKEN_BUDGET

        selected_claim_ids = tuple(item.claim_id for item in selected)
        covered_ids = tuple(item for item in required if item in covered)
        uncovered_ids = tuple(item for item in required if item not in covered)
        selected_roles = tuple(dict.fromkeys(item.role.value for item in selected))
        estimated_tokens = sum(item.estimated_tokens for item in selected)
        material = {
            "selected": selected_claim_ids,
            "omitted": tuple(
                (claim_id, omitted[claim_id].value) for claim_id in sorted(omitted)
            ),
            "covered": covered_ids,
            "uncovered": uncovered_ids,
            "roles": selected_roles,
            "tokens": estimated_tokens,
            "source_revision": source_revision,
            "graph_revision": graph_revision,
        }
        plan_sha256 = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return ProviderPlan(
            selected_claim_ids=selected_claim_ids,
            omitted_claims=tuple(
                OmittedClaim(claim_id, omitted[claim_id]) for claim_id in sorted(omitted)
            ),
            covered_requirement_ids=covered_ids,
            uncovered_requirement_ids=uncovered_ids,
            selected_roles=selected_roles,
            estimated_tokens=estimated_tokens,
            source_revision=source_revision,
            graph_revision=graph_revision,
            plan_sha256=plan_sha256,
        )


__all__ = [
    "ClaimAuthority",
    "ClaimRole",
    "OmissionReason",
    "OmittedClaim",
    "ProviderClaim",
    "ProviderContextPlanner",
    "ProviderPlan",
]
