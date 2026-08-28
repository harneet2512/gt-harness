"""Decision-value contracts for certifiable, boundary-scoped GT evidence.

This module keeps candidate generation separate from certification and model
delivery.  A lifecycle receipt is evidence of work only when it reaches the
state justified by independently checkable fields; merely producing a
candidate is deliberately not success.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

CERTIFIABLE_FEATURES = (
    "implementation_owner",
    "ambiguous_identity",
    "inspection_files",
    "public_surface",
    "impact",
    "affected_tests",
    "processes",
    "supporting_files",
    "new_file_proposals",
    "failure_analysis",
    "verification",
)

_TRIGGER_FIELDS = {
    "implementation_owner": "implementation_owners",
    "ambiguous_identity": "ambiguous_identities",
    "inspection_files": "inspection_files",
    "public_surface": "public_surface",
    "impact": "impact",
    "affected_tests": "affected_tests",
    "processes": "processes",
    "supporting_files": "supporting_files",
    "new_file_proposals": "new_file_proposals",
    "failure_analysis": "failure_analysis",
    "verification": "verification",
}


class FeatureStage(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANDIDATE = "CANDIDATE"
    CERTIFIED = "CERTIFIED"
    DELIVERED = "DELIVERED"
    CONSUMED = "CONSUMED"
    VALIDATED = "VALIDATED"
    CONTRADICTED = "CONTRADICTED"
    ABSTAINED = "ABSTAINED"


class DecisionBoundary(StrEnum):
    REPOSITORY_START = "REPOSITORY_START"
    IDENTITY_AMBIGUITY = "IDENTITY_AMBIGUITY"
    PRE_EDIT = "PRE_EDIT"
    POST_EDIT_GRAPH_DELTA = "POST_EDIT_GRAPH_DELTA"
    FAILURE_OBSERVATION = "FAILURE_OBSERVATION"
    VERIFICATION_SELECTION = "VERIFICATION_SELECTION"
    PRE_SUBMIT = "PRE_SUBMIT"


class ClaimRole(StrEnum):
    EDIT_OWNER = "edit_owner"
    INSPECTION_DEPENDENCY = "inspection_dependency"
    PUBLIC_SURFACE = "public_surface"
    AFFECTED_TEST = "affected_test"
    VALIDATION_COMMAND = "validation_command"
    UNRESOLVED_IDENTITY = "unresolved_identity"


@dataclass(frozen=True, slots=True)
class FeatureTriggerContext:
    event_id: str
    repository_revision: str
    graph_revision: str
    implementation_owners: tuple[str, ...] = ()
    ambiguous_identities: tuple[str, ...] = ()
    inspection_files: tuple[str, ...] = ()
    public_surface: tuple[str, ...] = ()
    impact: tuple[str, ...] = ()
    affected_tests: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    supporting_files: tuple[str, ...] = ()
    new_file_proposals: tuple[str, ...] = ()
    failure_analysis: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()


def evaluate_feature_triggers(
    context: FeatureTriggerContext,
) -> dict[str, FeatureLifecycle]:
    """Evaluate every production feature from typed, language-neutral facts."""

    lifecycles: dict[str, FeatureLifecycle] = {}
    for feature_id in CERTIFIABLE_FEATURES:
        values = tuple(getattr(context, _TRIGGER_FIELDS[feature_id]))
        trigger = f"{context.event_id}:{feature_id}"
        if values:
            lifecycles[feature_id] = FeatureLifecycle.candidate(
                feature_id,
                triggering_event=trigger,
                repository_revision=context.repository_revision,
                graph_revision=context.graph_revision,
            )
        else:
            lifecycles[feature_id] = FeatureLifecycle.not_applicable(
                feature_id,
                triggering_event=trigger,
                repository_revision=context.repository_revision,
                graph_revision=context.graph_revision,
                reason="typed trigger inputs absent at this decision boundary",
            )
    return lifecycles


_SECTION_NAMES = {
    ClaimRole.EDIT_OWNER: "edit owners",
    ClaimRole.INSPECTION_DEPENDENCY: "inspection dependencies",
    ClaimRole.PUBLIC_SURFACE: "public surface",
    ClaimRole.AFFECTED_TEST: "affected tests",
    ClaimRole.VALIDATION_COMMAND: "validation commands",
    ClaimRole.UNRESOLVED_IDENTITY: "explicitly unresolved identities",
}


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    path: str
    start_line: int
    end_line: int
    content_sha256: str
    excerpt: str = ""

    def valid(self) -> bool:
        return bool(
            self.path
            and self.start_line >= 0
            and self.end_line >= self.start_line
            and len(self.content_sha256) == 64
            and all(character in "0123456789abcdef" for character in self.content_sha256)
        )


@dataclass(frozen=True, slots=True)
class DecisionClaim:
    claim_id: str
    text: str
    role: ClaimRole
    requirement_id: str
    repository_revision: str
    graph_revision: str
    source_evidence: tuple[SourceEvidence, ...]
    action: str = ""
    prevents: str = ""
    symbol_identity: str = ""
    relationship: str = ""
    competing_identities: tuple[str, ...] = ()
    disambiguation_action: str = ""
    semantic_similarity: float = 0.0
    exact_identifier_match: bool = False
    graph_distance: int | None = None
    authoritative_edge: bool = False
    evidence_quality: float = 0.0
    certified: bool = True
    retrieval_truncated: bool = False

    def source_supported(self) -> bool:
        return bool(self.source_evidence) and all(item.valid() for item in self.source_evidence)

    def owner_authorized(self) -> bool:
        if self.role is not ClaimRole.EDIT_OWNER:
            return True
        return bool(self.symbol_identity or self.relationship)

    def ambiguity_actionable(self) -> bool:
        if self.role is not ClaimRole.UNRESOLVED_IDENTITY:
            return True
        return len(set(self.competing_identities)) >= 2 and bool(self.disambiguation_action)

    def concrete(self) -> bool:
        return bool(self.action or self.prevents or self.disambiguation_action)

    def novelty_key(self) -> str:
        payload = {
            "role": self.role.value,
            "requirement_id": self.requirement_id,
            "text": " ".join(self.text.split()),
            "action": " ".join(self.action.split()),
            "prevents": " ".join(self.prevents.split()),
            "identities": sorted(self.competing_identities),
            "disambiguation": " ".join(self.disambiguation_action.split()),
            "evidence": [
                (item.path, item.start_line, item.end_line, item.content_sha256)
                for item in self.source_evidence
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureTransition:
    from_stage: FeatureStage | None
    to_stage: FeatureStage
    reason: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "from": self.from_stage.value if self.from_stage is not None else None,
            "to": self.to_stage.value,
            "reason": self.reason,
        }


@dataclass(slots=True)
class FeatureLifecycle:
    feature_id: str
    triggering_event: str
    repository_revision: str
    graph_revision: str
    stage: FeatureStage
    transitions: list[FeatureTransition] = field(default_factory=list)
    claims: tuple[DecisionClaim, ...] = ()
    decision_boundary: DecisionBoundary | None = None
    model_visible_bytes: bytes = b""
    resulting_agent_action: str = ""
    validation_result: str = ""
    terminal_reason: str = ""

    @classmethod
    def candidate(
        cls,
        feature_id: str,
        *,
        triggering_event: str,
        repository_revision: str,
        graph_revision: str,
    ) -> FeatureLifecycle:
        instance = cls(
            feature_id=feature_id,
            triggering_event=triggering_event,
            repository_revision=repository_revision,
            graph_revision=graph_revision,
            stage=FeatureStage.CANDIDATE,
        )
        instance.transitions.append(
            FeatureTransition(None, FeatureStage.CANDIDATE, "deterministic trigger matched")
        )
        instance._validate_identity()
        return instance

    @classmethod
    def not_applicable(
        cls,
        feature_id: str,
        *,
        triggering_event: str,
        repository_revision: str,
        graph_revision: str,
        reason: str,
    ) -> FeatureLifecycle:
        if not reason:
            raise ValueError("NOT_APPLICABLE requires a reason")
        instance = cls(
            feature_id=feature_id,
            triggering_event=triggering_event,
            repository_revision=repository_revision,
            graph_revision=graph_revision,
            stage=FeatureStage.NOT_APPLICABLE,
            terminal_reason=reason,
        )
        instance.transitions.append(FeatureTransition(None, FeatureStage.NOT_APPLICABLE, reason))
        instance._validate_identity()
        return instance

    def _validate_identity(self) -> None:
        if not self.feature_id or not self.triggering_event or not self.repository_revision:
            raise ValueError("feature, trigger, and repository revision are required")
        if not self.graph_revision:
            raise ValueError("graph revision (or explicit not-applicable identity) is required")

    def _move(self, expected: FeatureStage, target: FeatureStage, reason: str) -> None:
        if self.stage is not expected:
            raise ValueError(f"{target.value} requires {expected.value}; found {self.stage.value}")
        self.transitions.append(FeatureTransition(self.stage, target, reason))
        self.stage = target

    def certify(
        self,
        *,
        claims: tuple[DecisionClaim, ...],
        decision_boundary: DecisionBoundary,
    ) -> None:
        if not claims:
            raise ValueError("CERTIFIED requires at least one claim")
        for claim in claims:
            if not claim.certified or not claim.source_supported():
                raise ValueError(f"claim {claim.claim_id} is not independently source-certified")
            if claim.repository_revision != self.repository_revision:
                raise ValueError(f"claim {claim.claim_id} repository revision is stale")
            if claim.graph_revision != self.graph_revision:
                raise ValueError(f"claim {claim.claim_id} graph revision is stale")
            if not claim.owner_authorized() or not claim.ambiguity_actionable():
                raise ValueError(f"claim {claim.claim_id} lacks decision authority")
        self._move(FeatureStage.CANDIDATE, FeatureStage.CERTIFIED, "source claims certified")
        self.claims = claims
        self.decision_boundary = decision_boundary

    def deliver(self, *, boundary: DecisionBoundary, model_visible_bytes: bytes) -> None:
        if self.stage is not FeatureStage.CERTIFIED:
            raise ValueError(
                f"DELIVERED requires CERTIFIED; found {self.stage.value}"
            )
        if boundary is not self.decision_boundary:
            raise ValueError("delivery boundary differs from certification boundary")
        if not model_visible_bytes:
            raise ValueError("DELIVERED requires exact non-empty model-visible bytes")
        self._move(FeatureStage.CERTIFIED, FeatureStage.DELIVERED, "exact bytes exposed")
        self.model_visible_bytes = bytes(model_visible_bytes)

    def consume(self, *, resulting_agent_action: str) -> None:
        if not resulting_agent_action:
            raise ValueError("CONSUMED requires a resulting agent action")
        self._move(FeatureStage.DELIVERED, FeatureStage.CONSUMED, "later action used delivery")
        self.resulting_agent_action = resulting_agent_action

    def validate(self, *, validation: str, contradicted: bool) -> None:
        if not validation:
            raise ValueError("validation or contradiction evidence is required")
        target = FeatureStage.CONTRADICTED if contradicted else FeatureStage.VALIDATED
        self._move(FeatureStage.CONSUMED, target, validation)
        self.validation_result = validation

    def abstain(self, reason: str) -> None:
        if not reason:
            raise ValueError("ABSTAINED requires a reason")
        self._move(FeatureStage.CANDIDATE, FeatureStage.ABSTAINED, reason)
        self.terminal_reason = reason

    def receipt(self) -> dict[str, Any]:
        payload_hash = (
            hashlib.sha256(self.model_visible_bytes).hexdigest()
            if self.model_visible_bytes
            else ""
        )
        return {
            "schema": "gt.feature_lifecycle.v1",
            "feature_id": self.feature_id,
            "stage": self.stage.value,
            "triggering_event": self.triggering_event,
            "repository_revision": self.repository_revision,
            "graph_revision": self.graph_revision,
            "decision_boundary": self.decision_boundary.value if self.decision_boundary else "",
            "claims": [asdict(claim) for claim in self.claims],
            "model_visible_bytes_hex": self.model_visible_bytes.hex(),
            "model_visible_bytes_sha256": payload_hash,
            "resulting_agent_action": self.resulting_agent_action,
            "validation_result": self.validation_result,
            "terminal_reason": self.terminal_reason,
            "transitions": [item.as_dict() for item in self.transitions],
        }


@dataclass(frozen=True, slots=True)
class CompiledDelivery:
    boundary: DecisionBoundary
    repository_revision: str
    graph_revision: str
    claims: tuple[DecisionClaim, ...]
    model_visible_bytes: bytes
    model_visible_bytes_sha256: str
    coverage_complete: bool


class DecisionDeliveryCompiler:
    """Select independently certified, current, novel claims at real boundaries."""

    def __init__(self) -> None:
        self._delivered_keys: set[str] = set()

    @staticmethod
    def _score(claim: DecisionClaim) -> float:
        distance = 0.0 if claim.graph_distance is None else 1.0 / (1.0 + claim.graph_distance)
        return (
            max(0.0, min(1.0, claim.semantic_similarity)) * 0.30
            + float(claim.exact_identifier_match) * 0.22
            + distance * 0.15
            + float(claim.authoritative_edge) * 0.13
            + max(0.0, min(1.0, claim.evidence_quality)) * 0.20
        )

    @staticmethod
    def _render_claim(claim: DecisionClaim) -> str:
        evidence = ", ".join(
            f"{item.path}:{item.start_line}-{item.end_line}#{item.content_sha256[:12]}"
            for item in claim.source_evidence
        )
        suffixes = []
        if claim.action:
            suffixes.append(f"action: {claim.action}")
        if claim.prevents:
            suffixes.append(f"prevents: {claim.prevents}")
        if claim.competing_identities:
            suffixes.append("identities: " + " vs ".join(claim.competing_identities))
        if claim.disambiguation_action:
            suffixes.append("disambiguate: " + claim.disambiguation_action)
        return f"- {claim.text} [source: {evidence}]" + (
            " [" + "; ".join(suffixes) + "]" if suffixes else ""
        )

    def compile(
        self,
        *,
        boundary: DecisionBoundary,
        repository_revision: str,
        graph_revision: str,
        unmet_requirement_ids: tuple[str, ...],
        claims: tuple[DecisionClaim, ...],
        max_claims: int = 12,
    ) -> CompiledDelivery | None:
        if boundary not in DecisionBoundary:
            raise ValueError("unsupported decision boundary")
        unmet = set(unmet_requirement_ids)
        eligible = [
            claim
            for claim in claims
            if claim.certified
            and claim.repository_revision == repository_revision
            and claim.graph_revision == graph_revision
            and claim.requirement_id in unmet
            and claim.source_supported()
            and claim.concrete()
            and claim.owner_authorized()
            and claim.ambiguity_actionable()
            and claim.novelty_key() not in self._delivered_keys
        ]
        if not eligible:
            return None

        # Rank first, then round-robin roles so one high-volume role cannot
        # crowd all other unmet decision surfaces from the bounded delivery.
        by_role: dict[ClaimRole, list[DecisionClaim]] = {}
        for claim in sorted(
            eligible,
            key=lambda item: (-self._score(item), item.requirement_id, item.claim_id),
        ):
            by_role.setdefault(claim.role, []).append(claim)
        selected: list[DecisionClaim] = []
        roles = [role for role in ClaimRole if role in by_role]
        while roles and len(selected) < max_claims:
            next_roles: list[ClaimRole] = []
            for role in roles:
                bucket = by_role[role]
                if bucket and len(selected) < max_claims:
                    selected.append(bucket.pop(0))
                if bucket:
                    next_roles.append(role)
            roles = next_roles

        rendered: list[str] = []
        for role in ClaimRole:
            section = [claim for claim in selected if claim.role is role]
            if not section:
                continue
            rendered.append(f"[{_SECTION_NAMES[role]}]")
            rendered.extend(self._render_claim(claim) for claim in section)
        payload = ("\n".join(rendered) + "\n").encode("utf-8")
        for claim in selected:
            self._delivered_keys.add(claim.novelty_key())
        return CompiledDelivery(
            boundary=boundary,
            repository_revision=repository_revision,
            graph_revision=graph_revision,
            claims=tuple(selected),
            model_visible_bytes=payload,
            model_visible_bytes_sha256=hashlib.sha256(payload).hexdigest(),
            coverage_complete=not any(claim.retrieval_truncated for claim in selected),
        )


__all__ = [
    "CERTIFIABLE_FEATURES",
    "ClaimRole",
    "CompiledDelivery",
    "DecisionBoundary",
    "DecisionClaim",
    "DecisionDeliveryCompiler",
    "FeatureLifecycle",
    "FeatureStage",
    "FeatureTriggerContext",
    "SourceEvidence",
    "evaluate_feature_triggers",
]
