"""Conservative, replayable certification for GT trajectory consequences.

Feature producers may observe far more state than should be shown to a model.
This module is the common boundary between a grounded observation and an
active consequence.  It deliberately contains no model call and no learned
confidence score: ambiguity is an abstention.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class GTPolicyMode(StrEnum):
    OFF = "off"
    AUDIT = "audit"
    CERTIFIED_SHADOW = "certified_shadow"
    CERTIFIED_ACTIVE = "certified_active"

    @classmethod
    def parse(cls, value: str | GTPolicyMode) -> GTPolicyMode:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.OFF


class EvidenceAuthority(StrEnum):
    MECHANICAL = "mechanical"
    CERTIFIED_STRUCTURAL = "certified_structural"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"


class OpportunityKind(StrEnum):
    LOCALIZATION_CONTRACTION = "localization_contraction"
    EDIT_CONTRADICTION = "edit_contradiction"
    DECISION_EVIDENCE_GAP = "decision_evidence_gap"
    DECLARED_CHECK_FAILURE = "declared_check_failure"
    REPEATED_FAILURE = "repeated_failure"
    STALE_BATCH = "stale_batch"
    SUBMIT_DEBT = "submit_debt"
    COMPLETION_READY = "completion_ready"
    PROGRESS_STALL = "progress_stall"


class OpportunityDisposition(StrEnum):
    ABSTAIN = "abstain"
    DELIVER_NEXT = "deliver_next"
    RETURN_BEFORE_EXECUTION = "return_before_execution"
    AUTO_SUBMIT = "auto_submit"


@dataclass(frozen=True, slots=True)
class CertifiedOpportunity:
    opportunity_id: str
    kind: OpportunityKind
    authority: EvidenceAuthority
    disposition: OpportunityDisposition
    source_revision: str
    workspace_revision: str
    evidence_ids: tuple[str, ...]
    concrete_anchors: tuple[str, ...]
    absent_from_provider_history: bool
    decision_relevant: bool
    eligible_call: int
    expires_after_call: int
    certified: bool
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        row["authority"] = self.authority.value
        row["disposition"] = self.disposition.value
        return row


_RETURN_KINDS = frozenset(
    {
        OpportunityKind.EDIT_CONTRADICTION,
        OpportunityKind.DECISION_EVIDENCE_GAP,
        OpportunityKind.STALE_BATCH,
        OpportunityKind.SUBMIT_DEBT,
    }
)


def _opportunity_id(
    kind: OpportunityKind,
    source_revision: str,
    evidence_ids: tuple[str, ...],
    anchors: tuple[str, ...],
) -> str:
    material = "\0".join(
        (kind.value, source_revision, *sorted(evidence_ids), *sorted(anchors))
    )
    return "opp-" + hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:20]


def certify_opportunity(
    *,
    kind: OpportunityKind,
    authority: EvidenceAuthority,
    source_revision: str,
    current_source_revision: str,
    workspace_revision: str,
    evidence_ids: tuple[str, ...],
    concrete_anchors: tuple[str, ...],
    absent_from_provider_history: bool,
    decision_relevant: bool,
    eligible_call: int,
    current_call: int,
) -> CertifiedOpportunity:
    """Certify one bounded consequence or return a reasoned abstention.

    Certification is conjunctive.  Numeric retrieval ranks are intentionally
    absent: callers must establish mechanical or certified structural
    authority before reaching this boundary.
    """

    reasons: list[str] = []
    if authority is EvidenceAuthority.HEURISTIC:
        reasons.append("heuristic_evidence")
    elif authority is EvidenceAuthority.UNKNOWN:
        reasons.append("unknown_evidence_authority")
    if not source_revision or source_revision != current_source_revision:
        reasons.append("stale_source_revision")
    if not workspace_revision:
        reasons.append("workspace_revision_missing")
    if not evidence_ids:
        reasons.append("evidence_missing")
    if not concrete_anchors or any(not str(anchor).strip() for anchor in concrete_anchors):
        reasons.append("concrete_anchor_missing")
    if not absent_from_provider_history:
        reasons.append("represented_in_provider_history")
    if not decision_relevant:
        reasons.append("no_decision_need")
    if current_call < eligible_call:
        reasons.append("delivery_window_not_open")
    elif current_call > eligible_call:
        reasons.append("delivery_window_expired")

    certified = not reasons
    if not certified:
        disposition = OpportunityDisposition.ABSTAIN
    elif kind is OpportunityKind.COMPLETION_READY:
        disposition = OpportunityDisposition.AUTO_SUBMIT
    elif kind in _RETURN_KINDS:
        disposition = OpportunityDisposition.RETURN_BEFORE_EXECUTION
    else:
        disposition = OpportunityDisposition.DELIVER_NEXT
    anchors = tuple(str(anchor).strip() for anchor in concrete_anchors if str(anchor).strip())
    ids = tuple(str(item).strip() for item in evidence_ids if str(item).strip())
    return CertifiedOpportunity(
        opportunity_id=_opportunity_id(kind, source_revision, ids, anchors),
        kind=kind,
        authority=authority,
        disposition=disposition,
        source_revision=source_revision,
        workspace_revision=workspace_revision,
        evidence_ids=ids,
        concrete_anchors=anchors,
        absent_from_provider_history=absent_from_provider_history,
        decision_relevant=decision_relevant,
        eligible_call=max(0, int(eligible_call)),
        expires_after_call=max(0, int(eligible_call)),
        certified=certified,
        reason_codes=tuple(dict.fromkeys(reasons or ["certified_opportunity"])),
    )
