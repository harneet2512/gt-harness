"""Persistent, source-bound facts materialized only for an active decision.

The central runtime previously selected guidance from only the receipts created
since the last model call.  A lower-priority fact that lost arbitration was
therefore destroyed.  This module separates evidence lifetime from delivery
time: claims persist until their source revision becomes stale, while explicit
decision needs determine whether a claim belongs in a provider request.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any


class SemanticClaimKind(StrEnum):
    LOCALIZATION = "localization"
    IMPACT = "impact"
    FAILURE = "failure"
    RECOVERY = "recovery"
    VALIDATION = "validation"
    SUBMISSION = "submission"


class DecisionNeedKind(StrEnum):
    LOCALIZE_TASK = "localize_task"
    REPAIR_IMPACT = "repair_impact"
    REPAIR_FAILURE = "repair_failure"
    RECOVER_FAILURE = "recover_failure"
    VALIDATE_CHANGE = "validate_change"
    SUBMIT_SAFELY = "submit_safely"


_NEED_PRIORITY = {
    DecisionNeedKind.REPAIR_FAILURE: 0,
    DecisionNeedKind.RECOVER_FAILURE: 1,
    DecisionNeedKind.REPAIR_IMPACT: 2,
    DecisionNeedKind.VALIDATE_CHANGE: 3,
    DecisionNeedKind.LOCALIZE_TASK: 4,
    DecisionNeedKind.SUBMIT_SAFELY: 5,
}

_CLAIM_PRIORITY = {
    SemanticClaimKind.FAILURE: 0,
    SemanticClaimKind.RECOVERY: 1,
    SemanticClaimKind.IMPACT: 2,
    SemanticClaimKind.VALIDATION: 3,
    SemanticClaimKind.LOCALIZATION: 4,
    SemanticClaimKind.SUBMISSION: 5,
}


@dataclass(frozen=True, slots=True)
class SemanticClaim:
    claim_id: str
    feature_id: str
    kind: SemanticClaimKind
    fact: str
    anchors: tuple[str, ...]
    source_revision: str
    evidence_action: int
    workspace_revision: str = ""
    evidence_hash: str = ""
    active: bool = True
    invalidated_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        return row


@dataclass(frozen=True, slots=True)
class DecisionNeed:
    need_id: str
    kind: DecisionNeedKind
    source_revision: str
    created_after_action: int
    required_claim_kinds: tuple[SemanticClaimKind, ...]
    anchors: tuple[str, ...] = ()
    open: bool = True
    resolution: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        row["required_claim_kinds"] = [item.value for item in self.required_claim_kinds]
        return row


@dataclass(frozen=True, slots=True)
class DecisionFrame:
    frame_id: str
    need_id: str
    need_kind: DecisionNeedKind
    claim_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]
    text: str
    source_revision: str
    evidence_actions: tuple[int, ...]
    materialized_for_call: int

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["need_kind"] = self.need_kind.value
        return row


class SemanticDecisionEngine:
    """Deterministic claim store and decision-time frame materializer."""

    def __init__(self, *, max_frame_chars: int = 320) -> None:
        self.max_frame_chars = max(80, int(max_frame_chars))
        self._claims: dict[str, SemanticClaim] = {}
        self._needs: dict[str, DecisionNeed] = {}
        self._frames: list[DecisionFrame] = []
        self._exposures: set[str] = set()

    @staticmethod
    def _digest(*parts: object) -> str:
        material = "\0".join(str(part) for part in parts)
        return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:20]

    def upsert_claim(
        self,
        *,
        feature_id: str,
        kind: SemanticClaimKind,
        fact: str,
        anchors: tuple[str, ...],
        source_revision: str,
        evidence_action: int,
        workspace_revision: str = "",
        evidence_hash: str = "",
    ) -> SemanticClaim | None:
        cleaned_fact = " ".join(str(fact or "").split())
        cleaned_anchors = tuple(
            dict.fromkeys(" ".join(str(anchor or "").split()) for anchor in anchors if anchor)
        )
        if not feature_id or not cleaned_fact or not cleaned_anchors or not source_revision:
            return None
        digest = evidence_hash or self._digest(cleaned_fact, cleaned_anchors)
        claim_id = "claim-" + self._digest(feature_id, kind.value, source_revision, digest)
        existing = self._claims.get(claim_id)
        claim = SemanticClaim(
            claim_id=claim_id,
            feature_id=feature_id,
            kind=kind,
            fact=cleaned_fact,
            anchors=cleaned_anchors,
            source_revision=source_revision,
            evidence_action=(
                min(existing.evidence_action, max(0, int(evidence_action)))
                if existing is not None and existing.active
                else max(0, int(evidence_action))
            ),
            workspace_revision=(
                existing.workspace_revision
                if existing is not None and existing.active
                else workspace_revision
            ),
            evidence_hash=digest,
        )
        self._claims[claim_id] = claim
        return claim

    def find_claim(
        self,
        *,
        feature_id: str,
        kind: SemanticClaimKind,
        fact: str,
        anchors: tuple[str, ...],
        source_revision: str,
    ) -> SemanticClaim | None:
        """Return the existing claim for canonical evidence, if any.

        This is deliberately the same canonicalization used by
        :meth:`upsert_claim`; callers use it to distinguish a new fact from a
        repeated fact so delivery accounting can mark the latter explicitly
        suppressed rather than claiming it was model-visible.
        """
        cleaned_fact = " ".join(str(fact or "").split())
        cleaned_anchors = tuple(
            dict.fromkeys(" ".join(str(anchor or "").split()) for anchor in anchors if anchor)
        )
        if not feature_id or not cleaned_fact or not cleaned_anchors or not source_revision:
            return None
        digest = self._digest(cleaned_fact, cleaned_anchors)
        claim_id = "claim-" + self._digest(feature_id, kind.value, source_revision, digest)
        return self._claims.get(claim_id)

    def open_need(
        self,
        *,
        kind: DecisionNeedKind,
        source_revision: str,
        created_after_action: int,
        required_claim_kinds: tuple[SemanticClaimKind, ...],
        anchors: tuple[str, ...] = (),
    ) -> DecisionNeed:
        cleaned_anchors = tuple(dict.fromkeys(str(item) for item in anchors if item))
        need_id = "need-" + self._digest(
            kind.value,
            source_revision,
            max(0, int(created_after_action)),
            tuple(item.value for item in required_claim_kinds),
            cleaned_anchors,
        )
        existing = self._needs.get(need_id)
        if existing is not None:
            return existing
        need = DecisionNeed(
            need_id=need_id,
            kind=kind,
            source_revision=source_revision,
            created_after_action=max(0, int(created_after_action)),
            required_claim_kinds=tuple(required_claim_kinds),
            anchors=cleaned_anchors,
        )
        self._needs[need_id] = need
        return need

    def resolve_need(self, need_id: str, *, resolution: str) -> None:
        need = self._needs.get(need_id)
        if need is None or not need.open:
            return
        self._needs[need_id] = DecisionNeed(
            need_id=need.need_id,
            kind=need.kind,
            source_revision=need.source_revision,
            created_after_action=need.created_after_action,
            required_claim_kinds=need.required_claim_kinds,
            anchors=need.anchors,
            open=False,
            resolution=str(resolution or "resolved"),
        )

    def resolve_open_needs_by_kind(
        self, kind: DecisionNeedKind, *, resolution: str
    ) -> tuple[str, ...]:
        """Resolve every currently open need of one lifecycle kind."""

        resolved: list[str] = []
        for need_id, need in tuple(self._needs.items()):
            if need.open and need.kind is kind:
                self.resolve_need(need_id, resolution=resolution)
                # A deliberately disabled delivery window must close both
                # halves of the semantic lifecycle.  Leaving its claim active
                # would make the receipt look like an unresolved decision even
                # though the host explicitly suppressed the surface.
                for claim_id, claim in tuple(self._claims.items()):
                    if (
                        claim.active
                        and claim.source_revision == need.source_revision
                        and claim.evidence_action == need.created_after_action
                        and claim.kind in need.required_claim_kinds
                    ):
                        self._claims[claim_id] = replace(
                            claim,
                            active=False,
                            invalidated_reason=str(resolution or "need_resolved"),
                        )
                resolved.append(need_id)
        return tuple(resolved)

    def invalidate_other_revisions(self, source_revision: str) -> None:
        """Invalidate stale claims/needs after authored source changes."""
        for claim_id, claim in tuple(self._claims.items()):
            if not claim.active or claim.source_revision == source_revision:
                continue
            self._claims[claim_id] = SemanticClaim(
                claim_id=claim.claim_id,
                feature_id=claim.feature_id,
                kind=claim.kind,
                fact=claim.fact,
                anchors=claim.anchors,
                source_revision=claim.source_revision,
                evidence_action=claim.evidence_action,
                workspace_revision=claim.workspace_revision,
                evidence_hash=claim.evidence_hash,
                active=False,
                invalidated_reason="source_revision_changed",
            )
        for need_id, need in tuple(self._needs.items()):
            if need.open and need.source_revision != source_revision:
                self.resolve_need(need_id, resolution="source_revision_changed")

    @staticmethod
    def _anchor_overlap(need: DecisionNeed, claim: SemanticClaim) -> int:
        if not need.anchors:
            return 0
        need_tokens = {item.lower() for item in need.anchors}
        claim_text = " ".join((claim.fact, *claim.anchors)).lower()
        return sum(token in claim_text for token in need_tokens)

    def materialize(self, *, call: int, source_revision: str) -> DecisionFrame | None:
        open_needs = sorted(
            (
                need
                for need in self._needs.values()
                if need.open
                and need.source_revision == source_revision
                and need.created_after_action < call
            ),
            key=lambda need: (
                _NEED_PRIORITY[need.kind],
                need.created_after_action,
                need.need_id,
            ),
        )
        selected_claims: list[SemanticClaim] = []
        selected_need: DecisionNeed | None = None
        facts: list[str] = []
        for need in open_needs:
            candidates = [
                claim
                for claim in self._claims.values()
                if claim.active
                and claim.source_revision == source_revision
                and claim.kind in need.required_claim_kinds
                # Provider-visible evidence has one deterministic delivery
                # window: the first model call after its evidence action.
                # Older claims remain available as controller state, but must
                # never leak into a later frame after losing arbitration.
                and claim.evidence_action == call - 1
                and need.created_after_action == call - 1
                and claim.claim_id not in self._exposures
                and claim.claim_id not in {item.claim_id for item in selected_claims}
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda claim: (
                    _CLAIM_PRIORITY[claim.kind],
                    -self._anchor_overlap(need, claim),
                    claim.evidence_action,
                    claim.claim_id,
                )
            )
            selected = candidates[0]
            candidate_text = " ".join((*facts, selected.fact))
            if facts and len(candidate_text) > self.max_frame_chars:
                continue
            if not facts and len(candidate_text) > self.max_frame_chars:
                # A clipped diagnostic can change its meaning.  Keep the
                # source-bound claim private when the complete fact does not
                # fit; a later call may not expose it because the one-call
                # evidence window still applies.
                continue
            selected_need = selected_need or need
            selected_claims.append(selected)
            facts.append(selected.fact)
            if len(selected_claims) >= 3:
                break
        if not selected_claims or selected_need is None:
            return None
        text = " ".join(facts)
        frame_id = "frame-" + self._digest(
            selected_need.need_id,
            tuple(item.claim_id for item in selected_claims),
            call,
            text,
        )
        frame = DecisionFrame(
            frame_id=frame_id,
            need_id=selected_need.need_id,
            need_kind=selected_need.kind,
            claim_ids=tuple(item.claim_id for item in selected_claims),
            feature_ids=tuple(item.feature_id for item in selected_claims),
            text=text,
            source_revision=source_revision,
            evidence_actions=tuple(item.evidence_action for item in selected_claims),
            materialized_for_call=max(1, int(call)),
        )
        self._frames.append(frame)
        self._exposures.update(item.claim_id for item in selected_claims)
        return frame

    def claim(self, claim_id: str) -> SemanticClaim | None:
        return self._claims.get(claim_id)

    def summary(self) -> dict[str, Any]:
        return {
            "claims": [item.as_dict() for item in self._claims.values()],
            "needs": [item.as_dict() for item in self._needs.values()],
            "frames": [item.as_dict() for item in self._frames],
            "active_claims": sum(item.active for item in self._claims.values()),
            "open_needs": sum(item.open for item in self._needs.values()),
        }
