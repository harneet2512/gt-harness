"""`semantic_parity: False` conflates two findings with opposite meanings.

Both conan and matplotlib report False. They do not mean the same thing, and
the study as shipped could not tell them apart -- the distinction had to be
recomputed by hand from the raw digests, which is exactly how a finding gets
lost.

Measured on the six benchmark-scale repositories:

    conan-io__conan-17132       baseline 34457-34460   candidate 34457-34460
    matplotlib__matplotlib-29431 baseline 49040-49049   candidate 49054-49066

conan's arms wobble across the SAME three edge digests. That is the producer's
own nondeterminism passing through the amend, and it is evidence FOR the
amend's fidelity: it lands on the same states a rebuild does.

matplotlib's arms are DISJOINT. No amend in five ever produced an edge set any
rebuild produced, and the amend is consistently 14-26 edges heavier. That is
not inherited noise; it is the amend and the rebuild disagreeing, on the
largest repository in the corpus and the only one where the amend is faster.

The classification is also what localises the defect. Across all six
repositories the nondeterminism is confined to the EDGES surface -- nodes,
properties and assertions are byte-identical across all ten runs everywhere --
which is a far narrower claim than "the producer is nondeterministic" and a far
more useful one for the parity item this blocks.
"""
from __future__ import annotations

import pytest

from scripts.graph_transition_study import classify_parity


def _rows(baseline: list[dict], candidate: list[dict]) -> list[dict]:
    rows = []
    for ordinal, digest in enumerate(baseline):
        rows.append({"arm": "baseline", "ordinal": ordinal, "digest": digest})
    for ordinal, digest in enumerate(candidate):
        rows.append({"arm": "candidate", "ordinal": ordinal, "digest": digest})
    return rows


def _digest(edges: str, nodes: str = "n1") -> dict:
    return {"nodes": nodes, "edges": edges, "properties": "p1", "assertions": "a1"}


def test_a_repository_that_never_varies_is_reported_as_identical():
    result = classify_parity(_rows([_digest("e1")] * 5, [_digest("e1")] * 5))
    assert result["verdict"] == "identical"
    assert result["unstable_surfaces"] == []


def test_arms_that_wobble_across_the_same_states_are_inherited_noise():
    """The conan shape. The amend lands where rebuilds land."""
    result = classify_parity(_rows(
        [_digest("e1"), _digest("e1"), _digest("e2"), _digest("e3")],
        [_digest("e3"), _digest("e2"), _digest("e1"), _digest("e2")],
    ))
    assert result["verdict"] == "shared_nondeterminism"
    assert result["unstable_surfaces"] == ["edges"]
    assert result["surfaces"]["edges"]["shared"] == 3


def test_arms_with_no_digest_in_common_are_a_divergence_not_noise():
    """The matplotlib shape, and the one that must never be filed as noise."""
    result = classify_parity(_rows(
        [_digest("e1"), _digest("e2")],
        [_digest("e8"), _digest("e9")],
    ))
    assert result["verdict"] == "arms_disjoint"
    assert result["surfaces"]["edges"]["shared"] == 0
    assert result["surfaces"]["edges"]["baseline_distinct"] == 2
    assert result["surfaces"]["edges"]["candidate_distinct"] == 2


def test_a_stable_surface_is_not_reported_as_unstable():
    """The localisation is the point: only edges moved, on every repository."""
    result = classify_parity(_rows(
        [_digest("e1", nodes="n1"), _digest("e2", nodes="n1")],
        [_digest("e1", nodes="n1"), _digest("e2", nodes="n1")],
    ))
    assert result["unstable_surfaces"] == ["edges"]
    assert "nodes" not in result["surfaces"]


def test_one_arm_varying_alone_is_named_for_the_arm_that_varies():
    """A rebuild that is stable while the amend is not accuses the amend."""
    result = classify_parity(_rows([_digest("e1")] * 3,
                                   [_digest("e1"), _digest("e2"), _digest("e3")]))
    assert result["verdict"] == "candidate_only"


def test_an_empty_measurement_does_not_claim_parity():
    """No runs is not agreement, and must not read as it."""
    assert classify_parity([])["verdict"] == "unmeasured"
