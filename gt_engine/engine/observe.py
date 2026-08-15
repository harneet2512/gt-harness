"""Canonical observation compiler (IE-04).

Compiles exactly ONE observation per selected action with deterministic
ordering:

1. action identity and decision; 2. raw result or declared replacement;
3. FACT-backed deterministic evidence; 4. stable source anchors and witnesses;
5. freshness and semantic qualification; 6. ambiguity and omission
declarations; 7. fallback or incompleteness notice; 8. receipt identifier.

Evidence-delta projection references unchanged facts by id/hash instead of
re-dumping them. Token reduction is secondary to decision relevance; raw
output remains exact where required and is always retained in replay storage.
"""
from __future__ import annotations

from typing import Mapping

from .contracts import (
    ActionRequest,
    CanonicalObservation,
    Decision,
    EvidenceArtifact,
    InterceptionDecision,
)


def project_evidence_delta(
    evidence: tuple[EvidenceArtifact, ...],
    referenced: Mapping[str, str],
) -> tuple[EvidenceArtifact, ...]:
    """Project evidence down to deltas against previously delivered facts.

    ``referenced`` maps ``artifact_id -> content hash`` of artifacts already
    delivered this episode. An artifact whose content hash is unchanged is
    emitted as a lightweight reference (``{"ref": artifact_id, "hash": ...}``)
    instead of its full content. Never called to decide anything; projection
    is presentation only.
    """
    projected: list[EvidenceArtifact] = []
    for artifact in evidence:
        prior = referenced.get(artifact.artifact_id)
        current = artifact.hash()
        if prior is not None and prior == current:
            projected.append(
                EvidenceArtifact(
                    artifact_id=artifact.artifact_id,
                    owner=artifact.owner,
                    semantics=artifact.semantics,
                    content={"ref": artifact.artifact_id, "hash": current},
                    anchors=artifact.anchors,
                    witnesses=artifact.witnesses,
                    producer=artifact.producer,
                    producer_version=artifact.producer_version,
                    freshness_revision=artifact.freshness_revision,
                    coverage=artifact.coverage,
                    ambiguity=artifact.ambiguity,
                    omissions=artifact.omissions,
                    configuration_digest=artifact.configuration_digest,
                    raw_fallback=artifact.raw_fallback,
                    model_visible=artifact.model_visible,
                    schema=artifact.schema,
                )
            )
        else:
            projected.append(artifact)
    return tuple(projected)


def compile_observation(
    request: ActionRequest,
    decision: InterceptionDecision,
    *,
    raw_result: str = "",
    raw_exact: bool = True,
    replaced: str = "",
    evidence: tuple[EvidenceArtifact, ...] = (),
    anchors: tuple[str, ...] = (),
    witnesses: tuple[str, ...] = (),
    freshness_qualification: str = "",
    ambiguity: tuple[str, ...] = (),
    omissions: tuple[str, ...] = (),
    fallback_notice: str = "",
    receipt_id: str = "",
    referenced: Mapping[str, str] | None = None,
) -> CanonicalObservation:
    """Build the canonical observation for one selected action.

    Invariants:
    - A REPLACE/REWRITE observation carries the declared replacement bytes and
      marks raw_exact=False.
    - A PASS_THROUGH/AUGMENT observation carries the exact raw bytes when
      ``raw_exact`` (raw-required observations retain exact raw bytes).
    - Incomplete evidence never suppresses raw acquisition: SUPPRESS is the
      only decision that may omit raw, and only with certified equivalence.
    """
    projected = (
        project_evidence_delta(evidence, referenced)
        if referenced is not None
        else evidence
    )
    if decision.decision in (Decision.REPLACE, Decision.REWRITE):
        return CanonicalObservation(
            action_request=request,
            decision=decision,
            replaced=replaced,
            evidence=projected,
            anchors=anchors,
            witnesses=witnesses,
            freshness_qualification=freshness_qualification,
            ambiguity=ambiguity,
            omissions=omissions,
            fallback_notice=fallback_notice,
            receipt_id=receipt_id,
            raw_exact=False,
        )
    if decision.decision == Decision.SUPPRESS:
        return CanonicalObservation(
            action_request=request,
            decision=decision,
            raw_exact=False,
            evidence=projected,
            anchors=anchors,
            witnesses=witnesses,
            freshness_qualification=freshness_qualification,
            ambiguity=ambiguity,
            omissions=omissions,
            fallback_notice=fallback_notice,
            receipt_id=receipt_id,
        )
    return CanonicalObservation(
        action_request=request,
        decision=decision,
        raw_result=raw_result,
        raw_exact=raw_exact,
        evidence=projected,
        anchors=anchors,
        witnesses=witnesses,
        freshness_qualification=freshness_qualification,
        ambiguity=ambiguity,
        omissions=omissions,
        fallback_notice=fallback_notice,
        receipt_id=receipt_id,
    )
