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

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("provider claim ID must not be empty")
        if self.estimated_tokens <= 0:
            raise ValueError("provider claim token cost must be positive")
        if not self.source_revision or not self.graph_revision:
            raise ValueError("provider claim must be bound to source and graph revisions")


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
                    decision_selected
                    and item.authority is ClaimAuthority.RANK_SUPPORT
                    and not item.requirement_ids
                )
            ]
            if not fitting:
                break

            def order(item: ProviderClaim) -> tuple[object, ...]:
                new_requirements = required_set.intersection(item.requirement_ids) - covered
                return (
                    -int(bool(new_requirements)),
                    -len(new_requirements),
                    -int(_ROLE_PRIORITY[item.role] >= 60),
                    -int(item.role not in roles),
                    -_ROLE_PRIORITY[item.role],
                    -int(item.authority),
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
            if claim.estimated_tokens > remaining:
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
