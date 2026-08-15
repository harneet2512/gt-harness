"""GroundTruth product identity: a thin deterministic evidence compiler.

GT is not a second coding agent, not a localization product, and not a rich
semantic-requirement graph.  It compiles certified, novel, decision-relevant
evidence for a stock Mini-SWE loop with at most one bootstrap
``select_catalog`` call.

A fact may be provider-visible only if it is CERTIFIED ∧ NOVEL ∧ MATERIAL.
Unknown evidence abstains.  Adjacent but non-material graph relations stay
private controller state.
"""

from __future__ import annotations

RELATION_ALIASES = {
    "asserted_by": "test_assertion",
    "tested_by": "test_assertion",
    "calls_transitive": "verified_closure",
}

PROVIDER_MATERIAL_RELATIONS = frozenset(
    {
        "calls",
        "called_by",
        "test_assertion",
        "verified_closure",
        "task_requirement",
    }
)

NON_MATERIAL_PROVIDER_RELATIONS = frozenset(
    {
        "imports",
        "imported_by",
        "implements",
        "implemented_by",
        "inherits",
        "inherited_by",
        "overrides",
        "overridden_by",
        "references",
        "referenced_by",
    }
)

PROVIDER_MATERIAL_FEATURES = frozenset(
    {
        "covering_red",
        "newfile_precedent",
        "recovery",
        "signature_delta",
        "submit_refusal",
        "syntax_result",
    }
)

PROVIDER_MATERIALITY_REASONS = frozenset(
    {
        "newly_certified_related_file",
        "new_unresolved_task_obligation",
        "related_advisory_obligation",
        "current_attributable_failure",
        "declared_validation_status_change",
    }
)


def provider_material_relation(relation: str) -> str:
    """Return the provider-visible relation, or empty when it must stay private."""

    normalized = str(relation or "").strip().lower()
    normalized = RELATION_ALIASES.get(normalized, normalized)
    if normalized in NON_MATERIAL_PROVIDER_RELATIONS:
        return ""
    if normalized in PROVIDER_MATERIAL_RELATIONS:
        return normalized
    return ""
