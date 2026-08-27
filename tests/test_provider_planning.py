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
    selection_rank: int = 0,
) -> ProviderClaim:
    return ProviderClaim(
        claim_id=claim_id,
        role=role,
        authority=authority,
        requirement_ids=requirements,
        estimated_tokens=tokens,
        source_revision="source-revision",
        graph_revision="graph-revision",
        selection_rank=selection_rank,
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


def test_planner_preserves_compiler_rank_before_serialized_cost() -> None:
    """A cheap lower-ranked row must not replace the compiler's best fact."""

    claims = (
        _claim(
            "compiler-first",
            role=ClaimRole.EDIT,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=20,
            selection_rank=0,
        ),
        _claim(
            "cheap-but-second",
            role=ClaimRole.EDIT,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=5,
            selection_rank=1,
        ),
    )

    plans = {
        ProviderContextPlanner().plan(
            order,
            requirement_ids=("requirement",),
            token_budget=20,
        ).selected_claim_ids
        for order in permutations(claims)
    }

    assert plans == {("compiler-first",)}


def test_exact_ambiguity_beats_rank_only_owner_for_same_requirement() -> None:
    claims = (
        _claim(
            "weak-owner",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=("requirement",),
            tokens=10,
        ),
        _claim(
            "exact-ambiguity",
            role=ClaimRole.AMBIGUITY,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=10,
        ),
    )

    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement",),
        token_budget=10,
    )

    assert plan.selected_claim_ids == ("exact-ambiguity",)


def test_unique_exact_owner_beats_ambiguity_for_same_requirement() -> None:
    claims = (
        _claim(
            "exact-owner",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=10,
        ),
        _claim(
            "exact-ambiguity",
            role=ClaimRole.AMBIGUITY,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=10,
        ),
    )

    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement",),
        token_budget=10,
    )

    assert plan.selected_claim_ids == ("exact-owner",)


def test_rank_only_owner_delivery_is_a_bounded_two_candidate_set() -> None:
    claims = tuple(
        _claim(
            f"owner-{index}",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=("requirement",),
            tokens=10,
            selection_rank=index,
        )
        for index in range(3)
    )

    for ordering in permutations(claims):
        plan = ProviderContextPlanner().plan(
            ordering,
            requirement_ids=("requirement",),
            token_budget=30,
        )

        assert plan.selected_claim_ids == ("owner-0", "owner-1")
        assert plan.omission_by_claim["owner-2"] is OmissionReason.REDUNDANT_COVERAGE


def test_planner_delivers_localization_before_downstream_relation_detail() -> None:
    claims = (
        _claim(
            "semantic-owner",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=("requirement",),
            tokens=10,
        ),
        _claim(
            "relation",
            role=ClaimRole.RELATION,
            authority=ClaimAuthority.CERTIFIED_RELATION,
            requirements=("requirement",),
            tokens=10,
        ),
    )

    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement",),
        token_budget=10,
    )

    assert plan.selected_claim_ids == ("semantic-owner",)


def test_planner_drops_redundant_localization_family_noise() -> None:
    claims = (
        _claim(
            "exact-edit",
            role=ClaimRole.EDIT,
            authority=ClaimAuthority.EXACT_IDENTITY,
            requirements=("requirement",),
            tokens=10,
        ),
        _claim(
            "semantic-owner",
            role=ClaimRole.IMPLEMENTATION_OWNER,
            authority=ClaimAuthority.RANK_SUPPORT,
            requirements=("requirement",),
            tokens=10,
        ),
    )

    plan = ProviderContextPlanner().plan(
        claims,
        requirement_ids=("requirement",),
        token_budget=20,
    )

    assert plan.selected_claim_ids == ("exact-edit",)
    assert plan.omission_by_claim["semantic-owner"] is OmissionReason.REDUNDANT_COVERAGE
