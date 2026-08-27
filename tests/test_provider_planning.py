from __future__ import annotations

from itertools import permutations

from gt_harness.provider_planning import (
    ClaimAuthority,
    ClaimRole,
    OmissionReason,
    ProviderClaim,
    ProviderContextPlanner,
)


def _claim(
    claim_id: str,
    *,
    role: ClaimRole,
    authority: ClaimAuthority,
    requirements: tuple[str, ...],
    tokens: int,
) -> ProviderClaim:
    return ProviderClaim(
        claim_id=claim_id,
        role=role,
        authority=authority,
        requirement_ids=requirements,
        estimated_tokens=tokens,
        source_revision="source-revision",
        graph_revision="graph-revision",
    )


def test_planner_covers_distinct_requirements_before_rank_only_noise() -> None:
    claims = (
        _claim(
            "owner-one",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement-one",),
            tokens=20,
        ),
        _claim(
            "owner-two",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.CERTIFIED_RELATION,
            requirements=("requirement-two",),
            tokens=20,
        ),
        _claim(
            "dense-noise",
            role=ClaimRole.INSPECTION,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=(),
            tokens=5,
        ),
    )

    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement-one", "requirement-two"),
        token_budget=40,
    )

    assert plan.selected_claim_ids == ("owner-one", "owner-two")
    assert plan.covered_requirement_ids == ("requirement-one", "requirement-two")
    assert plan.uncovered_requirement_ids == ()
    assert plan.omission_by_claim["dense-noise"] is OmissionReason.TOKEN_BUDGET


def test_rank_support_can_never_become_edit_authority() -> None:
    plan = ProviderContextPlanner().plan(
        (
            _claim(
                "unsafe-edit",
                role=ClaimRole.EDIT,
                authority=ClaimAuthority.RANK_SUPPORT,
                requirements=("requirement",),
                tokens=1,
            ),
        ),
        requirement_ids=("requirement",),
        token_budget=100,
    )

    assert plan.selected_claim_ids == ()
    assert plan.omission_by_claim["unsafe-edit"] is OmissionReason.INVALID_AUTHORITY
    assert plan.uncovered_requirement_ids == ("requirement",)


def test_planner_is_input_order_invariant_and_receipts_every_omission() -> None:
    claims = (
        _claim(
            "exact",
            role=ClaimRole.EDIT,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=15,
        ),
        _claim(
            "relation",
            role=ClaimRole.PROCESS,
            authority=ClaimAuthority.CERTIFIED_RELATION,
            requirements=("requirement",),
            tokens=10,
        ),
        _claim(
            "inspection",
            role=ClaimRole.INSPECTION,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=(),
            tokens=5,
        ),
    )

    plans = {
        ProviderContextPlanner().plan(
            order,
            requirement_ids=("requirement",),
            token_budget=20,
        ).as_json()
        for order in permutations(claims)
    }

    assert len(plans) == 1
    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement",),
        token_budget=20,
    )
    assert set(plan.selected_claim_ids) | set(plan.omission_by_claim) == {
        claim.claim_id for claim in claims
    }
