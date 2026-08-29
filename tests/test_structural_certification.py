from __future__ import annotations

from gt_engine.hybrid_retrieval import (
    RelationDisposition,
    RelationOrigin,
    RelationUse,
    ResolutionOutcome,
    StructuralLink,
    certify_structural_link,
)


def _link(**overrides) -> StructuralLink:
    values = {
        "source_path": "src/service.py",
        "target_path": "src/repository.py",
        "relation": "CALLS",
        "confidence": 1.0,
        "certified": True,
        "source_symbol": "load_user",
        "target_symbol": "find_user",
        "source_start_line": 10,
        "target_start_line": 20,
        "source_content_sha256": "a" * 64,
        "target_content_sha256": "b" * 64,
        "source_evidence_origin": "preexisting_repository",
        "target_evidence_origin": "preexisting_repository",
        "origin": "program",
        "resolution_outcome": "exact",
        "resolution_method": "lsp_verified",
        "candidate_count": 1,
    }
    values.update(overrides)
    return StructuralLink(**values)


def test_structural_link_normalizes_resolution_authority_to_typed_values():
    link = _link()

    assert link.origin is RelationOrigin.PROGRAM
    assert link.resolution_outcome is ResolutionOutcome.EXACT
    assert link.resolution_evidence.candidate_count == 1


def test_only_exact_unique_repository_relation_certifies_edit_owner():
    exact = certify_structural_link(_link(), RelationUse.EDIT_OWNER)
    ambiguous = certify_structural_link(
        _link(resolution_outcome="ambiguous", candidate_count=2),
        RelationUse.EDIT_OWNER,
    )
    fallback = certify_structural_link(
        _link(resolution_outcome="global_fallback"),
        RelationUse.EDIT_OWNER,
    )

    assert exact.disposition is RelationDisposition.CERTIFIED
    assert ambiguous.disposition is RelationDisposition.INSPECTION_ONLY
    assert fallback.disposition is RelationDisposition.INSPECTION_ONLY
