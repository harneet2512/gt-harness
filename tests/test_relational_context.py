from __future__ import annotations

import pytest

from gt_engine.hybrid_retrieval import (
    EvidenceAuthority,
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalChannel,
)
from gt_engine.hybrid_retrieval import (
    StructuralLink as _StructuralLink,
)
from gt_engine.relational_context import (
    ContextOpportunity,
    EpistemicStatus,
    EvidenceSnapshot,
    RelationalContextComposer,
    RelationalContextStatus,
)

REVISION = "source-1"


def StructuralLink(*args, **kwargs):
    kwargs.setdefault("origin", "program")
    kwargs.setdefault("resolution_outcome", "exact")
    kwargs.setdefault("source_start_line", 1)
    kwargs.setdefault("target_start_line", 1)
    kwargs.setdefault("source_content_sha256", "a" * 64)
    kwargs.setdefault("target_content_sha256", "b" * 64)
    kwargs.setdefault("source_evidence_origin", "preexisting_repository")
    kwargs.setdefault("target_evidence_origin", "preexisting_repository")
    kwargs.setdefault("candidate_count", 1)
    return _StructuralLink(*args, **kwargs)


def _candidate(path: str, symbol: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        path=path,
        start_line=1,
        end_line=3,
        symbol=symbol,
        text=f"def {symbol}(): ...",
        channel=RetrievalChannel.STRUCTURAL,
        channel_rank=1,
        relation="calls",
        provenance=("structural_certified",),
        source_revision=REVISION,
        authority=EvidenceAuthority.CERTIFIED_RELATION,
    )


def _retrieval(*candidates: RetrievalCandidate) -> HybridRetrievalResult:
    return HybridRetrievalResult(
        ranked_files=(),
        ranked_spans=candidates,
        selected_context=candidates,
        abstained=not candidates,
        reason_codes=() if candidates else ("no_supported_context",),
        channel_receipts=(),
        latency_ms=0.0,
        query_hash="query-1",
        token_budget=256,
        selected_token_count=0,
    )


def test_composer_returns_deterministic_certified_process_and_affected_test() -> None:
    links = (
        StructuralLink(
            "src/entry.py",
            "src/core.py",
            "calls",
            certified=True,
            source_symbol="run",
            target_symbol="work",
        ),
        StructuralLink(
            "src/core.py",
            "tests/test_core.py",
            "asserted_by",
            certified=True,
            source_symbol="work",
            target_symbol="test_work",
        ),
    )
    snapshot = EvidenceSnapshot(
        retrieval=_retrieval(_candidate("src/core.py", "work")),
        structural_links=links,
        source_revision=REVISION,
        graph_revision="graph-1",
    )
    opportunity = ContextOpportunity(
        kind="post_read_search",
        evidence_action=2,
        eligible_call=3,
        source_revision=REVISION,
        graph_revision="graph-1",
        anchors=("src/core.py",),
    )

    first = RelationalContextComposer().compose(opportunity, snapshot)
    second = RelationalContextComposer().compose(opportunity, snapshot)

    assert first == second
    assert first.status is RelationalContextStatus.DELIVER
    assert first.epistemic_status is EpistemicStatus.LOWER_BOUND
    assert first.processes
    assert "src/entry.py#run --calls--> src/core.py#work" in first.rendered_text
    assert "src/core.py#work --asserted_by--> tests/test_core.py#test_work" in first.rendered_text
    assert first.claim_ids


def test_composer_refuses_uncertified_ambiguous_and_external_links() -> None:
    links = (
        StructuralLink(
            "src/core.py",
            "src/guess.py",
            "calls",
            certified=False,
            provenance=("global_fallback",),
        ),
        StructuralLink(
            "src/core.py",
            "src/ambiguous.py",
            "calls",
            certified=True,
            provenance=("resolution:ambiguous",),
        ),
        StructuralLink(
            "src/core.py",
            "site-packages/vendor.py",
            "calls",
            certified=True,
            provenance=("origin:external",),
        ),
    )
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(_candidate("src/core.py", "work")),
            structural_links=links,
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert set(result.reason_codes) >= {
        "no_certified_process",
        "ambiguous_edge_rejected",
        "external_edge_rejected",
        "uncertified_edge_rejected",
    }


@pytest.mark.parametrize(
    ("provenance", "expected_reason"),
    (
        ("origin:builtin", "builtin_edge_rejected"),
        ("origin:stdlib", "stdlib_edge_rejected"),
        ("origin:third_party", "third_party_edge_rejected"),
        ("origin:framework", "framework_edge_rejected"),
        ("resolution:dynamic", "dynamic_edge_rejected"),
        ("resolution:reexport_unproven", "unproven_reexport_edge_rejected"),
        ("resolution:unresolved_receiver", "unresolved_edge_rejected"),
    ),
)
def test_composer_makes_non_program_or_unproven_origins_terminal_unknown(
    provenance: str,
    expected_reason: str,
) -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(_candidate("src/core.py", "work")),
            structural_links=(
                StructuralLink(
                    "src/core.py",
                    "src/guess.py",
                    "calls",
                    certified=True,
                    provenance=(provenance,),
                ),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert result.processes == ()
    assert expected_reason in result.reason_codes


def test_composer_uses_typed_origin_even_without_provenance_marker() -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(
                StructuralLink(
                    "src/core.py",
                    "src/external.py",
                    "calls",
                    certified=True,
                    origin="external",
                    provenance=(),
                ),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert "external_edge_rejected" in result.reason_codes


def test_composer_rejects_certified_edge_without_content_bound_identity() -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(
                _StructuralLink(
                    "src/core.py",
                    "src/other.py",
                    "calls",
                    certified=True,
                    origin="program",
                    resolution_outcome="exact",
                ),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert "incomplete_edge_identity_rejected" in result.reason_codes


def test_composer_abstains_for_stale_or_non_event_context() -> None:
    composer = RelationalContextComposer()
    snapshot = EvidenceSnapshot(
        retrieval=_retrieval(_candidate("src/core.py", "work")),
        structural_links=(),
        source_revision=REVISION,
        graph_revision="graph-1",
    )

    stale = composer.compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision="source-2",
            graph_revision="graph-2",
            anchors=("src/core.py",),
        ),
        snapshot,
    )
    generic = composer.compose(
        ContextOpportunity(
            kind="post_other",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        snapshot,
    )

    assert stale.status is RelationalContextStatus.ABSTAIN
    assert stale.reason_codes == ("stale_evidence_snapshot",)
    assert generic.status is RelationalContextStatus.ABSTAIN
    assert generic.reason_codes == ("ineligible_opportunity",)


def test_composer_omits_whole_process_when_token_budget_cannot_fit() -> None:
    links = (
        StructuralLink(
            "src/entry.py",
            "src/core.py",
            "calls",
            certified=True,
            source_symbol="run",
            target_symbol="work",
        ),
    )
    result = RelationalContextComposer(max_tokens=4).compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(_candidate("src/core.py", "work")),
            structural_links=links,
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert result.reason_codes == ("process_token_budget",)
    assert result.rendered_text == ""


def test_process_claim_identity_depends_on_semantic_process_not_current_anchor() -> None:
    link = StructuralLink(
        "src/entry.py",
        "src/core.py",
        "calls",
        certified=True,
        source_symbol="run",
        target_symbol="work",
    )
    snapshot = EvidenceSnapshot(
        retrieval=_retrieval(),
        structural_links=(link,),
        source_revision=REVISION,
        graph_revision="graph-1",
    )
    composer = RelationalContextComposer()

    from_entry = composer.compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/entry.py",),
        ),
        snapshot,
    )
    from_core = composer.compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=2,
            eligible_call=3,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        snapshot,
    )

    assert from_entry.claim_ids == from_core.claim_ids
    duplicate = composer.compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=2,
            eligible_call=3,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(link,),
            source_revision=REVISION,
            graph_revision="graph-1",
            delivered_claim_ids=from_entry.claim_ids,
        ),
    )
    assert duplicate.status is RelationalContextStatus.ABSTAIN
    assert duplicate.reason_codes == ("duplicate_relational_claim",)


def test_rejected_edge_count_counts_edges_not_distinct_reason_codes() -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_mutation",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/core.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(
                StructuralLink(
                    "src/core.py",
                    "src/guess_a.py",
                    "calls",
                    certified=False,
                ),
                StructuralLink(
                    "src/core.py",
                    "src/guess_b.py",
                    "calls",
                    certified=False,
                ),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert result.status is RelationalContextStatus.ABSTAIN
    assert result.rejected_edge_count == 2


def test_process_claim_identity_changes_when_endpoint_content_changes() -> None:
    def compose(source_hash: str):
        return RelationalContextComposer().compose(
            ContextOpportunity(
                kind="post_read_search",
                evidence_action=1,
                eligible_call=2,
                source_revision=REVISION,
                graph_revision="graph-1",
                anchors=("src/entry.py",),
            ),
            EvidenceSnapshot(
                retrieval=_retrieval(),
                structural_links=(
                    StructuralLink(
                        "src/entry.py",
                        "src/core.py",
                        "calls",
                        certified=True,
                        source_symbol="run",
                        target_symbol="work",
                        source_content_sha256=source_hash,
                        target_content_sha256="c" * 64,
                    ),
                ),
                source_revision=REVISION,
                graph_revision="graph-1",
            ),
        )

    assert compose("a" * 64).claim_ids != compose("b" * 64).claim_ids


def test_case_distinct_repository_paths_do_not_share_adjacency() -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/Foo.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(
                StructuralLink("src/Foo.py", "src/upper.py", "calls", certified=True),
                StructuralLink("src/foo.py", "src/lower.py", "calls", certified=True),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    assert "src/upper.py" in result.rendered_text
    assert "src/lower.py" not in result.rendered_text


def test_inverse_relation_swaps_all_endpoint_claim_metadata() -> None:
    result = RelationalContextComposer().compose(
        ContextOpportunity(
            kind="post_read_search",
            evidence_action=1,
            eligible_call=2,
            source_revision=REVISION,
            graph_revision="graph-1",
            anchors=("src/callee.py",),
        ),
        EvidenceSnapshot(
            retrieval=_retrieval(),
            structural_links=(
                StructuralLink(
                    "src/caller.py",
                    "src/callee.py",
                    "inverse:calls",
                    certified=True,
                    source_symbol="caller",
                    source_start_line=11,
                    source_content_sha256="a" * 64,
                    target_symbol="callee",
                    target_start_line=22,
                    target_content_sha256="b" * 64,
                ),
            ),
            source_revision=REVISION,
            graph_revision="graph-1",
        ),
    )

    step = result.processes[0].steps[0]
    assert step.source_path == "src/callee.py"
    assert step.source_symbol == "callee"
    assert step.source_start_line == 22
    assert step.source_content_sha256 == "b" * 64
    assert step.target_path == "src/caller.py"
    assert step.target_symbol == "caller"
    assert step.target_start_line == 11
    assert step.target_content_sha256 == "a" * 64
