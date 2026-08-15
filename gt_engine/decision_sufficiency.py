"""Fail-closed decision-sufficiency policy for pre-action repository evidence.

The compiler does not execute, suppress, rewrite, or return an action.  It only
certifies whether a host *may* consider returning one mutation to the model.
Every ambiguity degrades to PASS.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from gt_engine.hybrid_retrieval import (
    EvidenceAuthority,
    HybridRetrievalResult,
    RetrievalCandidate,
)
from gt_engine.preflight import ActionOperation, MutationCertainty, ProposedAction

_MUTATION_OPERATIONS = frozenset(
    {ActionOperation.EDIT, ActionOperation.CREATE, ActionOperation.DELETE}
)
_INCOMPLETE_RETRIEVAL_REASONS = frozenset(
    {
        "context_budget",
        "no_complete_evidence",
        "stale_candidates_rejected",
    }
)
_COCHANGE_MARKERS = ("cochange", "co-change", "git_cochange")
_DECISION_RELEVANT_STRUCTURAL_RELATIONS = frozenset(
    {
        "calls",
        "inverse:calls",
        "asserted_by",
        "inverse:asserted_by",
    }
)


class DecisionSufficiencyDisposition(StrEnum):
    PASS = "pass"
    RETURN_ELIGIBLE = "return_eligible"


def _canonical_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if value.startswith("/app/"):
        value = value[len("/app/") :]
    return value.rstrip("/").lower()


def _bounded_token_count(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(text or ""), re.UNICODE))


@dataclass(frozen=True, slots=True)
class ProviderVisibleState:
    """Exact claim inventory for the request that selected the action."""

    selecting_request_hash: str
    source_revision: str
    graph_revision: str = ""
    selecting_request_claim_ids: tuple[str, ...] = ()
    retained_history_claim_ids: tuple[str, ...] = ()
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selecting_request_claim_ids",
            tuple(dict.fromkeys(str(item) for item in self.selecting_request_claim_ids if item)),
        )
        object.__setattr__(
            self,
            "retained_history_claim_ids",
            tuple(dict.fromkeys(str(item) for item in self.retained_history_claim_ids if item)),
        )

    @property
    def visible_claim_ids(self) -> frozenset[str]:
        return frozenset(
            (*self.selecting_request_claim_ids, *self.retained_history_claim_ids)
        )


@dataclass(frozen=True, slots=True)
class DecisionEvidenceClaim:
    claim_id: str
    decision_claim_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str
    relation: str
    text: str
    provenance: tuple[str, ...]
    source_revision: str
    support_kind: str
    target_path: str
    token_count: int


@dataclass(frozen=True, slots=True)
class DecisionEvidenceBundle:
    action_id: str
    cycle_id: str
    target_path: str
    source_revision: str
    graph_revision: str
    retrieval_query_hash: str
    selecting_request_hash: str
    claims: tuple[DecisionEvidenceClaim, ...]
    token_count: int
    char_count: int
    complete: bool


@dataclass(frozen=True, slots=True)
class DecisionSufficiency:
    disposition: DecisionSufficiencyDisposition
    return_eligible: bool
    reason_codes: tuple[str, ...]
    bundle: DecisionEvidenceBundle | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["disposition"] = self.disposition.value
        return row


def _pass(*reason_codes: str) -> DecisionSufficiency:
    return DecisionSufficiency(
        disposition=DecisionSufficiencyDisposition.PASS,
        return_eligible=False,
        reason_codes=tuple(dict.fromkeys(reason_codes or ("insufficient_evidence",))),
    )


def _action_target(proposed: ProposedAction) -> tuple[str | None, str | None]:
    targets = tuple(
        dict.fromkeys(
            path
            for target in proposed.targets
            if (path := _canonical_path(target.path))
        )
    )
    if len(targets) != 1:
        return None, "non_unique_action_target"
    return targets[0], None


def _target_anchor(candidate: RetrievalCandidate, target_path: str) -> bool:
    expected = f"action_target:{target_path}"
    return any(
        str(item).strip().lower().replace("\\", "/") == expected
        for item in candidate.provenance
    )


def _support_kind(
    candidate: RetrievalCandidate,
    target_path: str,
) -> str | None:
    provenance = {str(item).strip().lower() for item in candidate.provenance}
    if (
        "delivery_support:certified_relation" not in provenance
        or candidate.authority is not EvidenceAuthority.CERTIFIED_RELATION
    ):
        return None
    relation_material = " ".join(
        (str(candidate.relation or ""), *sorted(provenance))
    ).lower()
    if any(marker in relation_material for marker in _COCHANGE_MARKERS):
        return None
    if (
        "support_channel:structural" in provenance
        and "structural_certified" in provenance
        and str(candidate.relation or "").strip().lower()
        in _DECISION_RELEVANT_STRUCTURAL_RELATIONS
        and _target_anchor(candidate, target_path)
        and any(
            item.startswith(("edge_endpoint_symbol:", "edge_endpoint_start:"))
            for item in provenance
        )
    ):
        return "certified_structural"
    return None


def _material_to_target(candidate: RetrievalCandidate, target_path: str) -> bool:
    return (
        _canonical_path(candidate.path) == target_path
        or _target_anchor(candidate, target_path)
    )


def _complete_claim(
    candidate: RetrievalCandidate,
    *,
    target_path: str,
    support_kind: str,
    operation: ActionOperation,
) -> DecisionEvidenceClaim | None:
    text = str(candidate.text or "").strip()
    start = candidate.start_line
    end = candidate.end_line
    if not text or start is None or end is None or start < 1 or end < start:
        return None
    token_count = _bounded_token_count(text)
    if token_count < 1:
        return None
    return DecisionEvidenceClaim(
        claim_id=candidate.claim_hash,
        decision_claim_id=hashlib.sha256(
            "\x00".join(
                (
                    candidate.content_claim_id,
                    operation.value,
                    target_path,
                    support_kind,
                )
            ).encode("utf-8")
        ).hexdigest(),
        path=candidate.path,
        start_line=start,
        end_line=end,
        symbol=str(candidate.symbol or ""),
        relation=str(candidate.relation or ""),
        text=text,
        provenance=candidate.provenance,
        source_revision=candidate.source_revision,
        support_kind=support_kind,
        target_path=target_path,
        token_count=token_count,
    )


def compile_decision_sufficiency(
    proposed: ProposedAction,
    retrieval: HybridRetrievalResult,
    visible: ProviderVisibleState,
    *,
    current_source_revision: str,
    current_graph_revision: str | None = None,
    max_evidence_tokens: int = 1_200,
    max_evidence_chars: int = 480,
    max_evidence_claims: int = 1,
) -> DecisionSufficiency:
    """Certify bounded reconsideration eligibility for one proposed mutation."""

    if proposed.operation not in _MUTATION_OPERATIONS or not proposed.mutates_workspace:
        return _pass("non_mutation_operation")
    if (
        proposed.mutation_certainty is not MutationCertainty.PROVEN_MUTATING
        or proposed.parser_confidence < 0.95
        or proposed.parse_coverage < 1.0
        or proposed.has_unknown_segments
        or proposed.has_opaque_segments
    ):
        return _pass("action_parse_not_mechanically_complete")
    target_path, target_error = _action_target(proposed)
    if target_error or target_path is None:
        return _pass(target_error or "non_unique_action_target")
    current_revision = str(current_source_revision or "")
    current_graph = str(current_graph_revision or current_revision)
    if (
        not current_revision
        or proposed.source_revision != current_revision
        or visible.source_revision != current_revision
    ):
        return _pass("source_revision_mismatch")
    if visible.graph_revision not in {"", current_graph}:
        return _pass("graph_revision_mismatch")
    if not visible.complete or not visible.selecting_request_hash:
        return _pass("provider_visibility_incomplete")
    if (
        max_evidence_tokens < 1
        or max_evidence_chars < 1
        or max_evidence_claims < 1
        or retrieval.token_budget < 1
        or retrieval.selected_token_count < 1
        or retrieval.abstained
        or not retrieval.selected_context
        or bool(_INCOMPLETE_RETRIEVAL_REASONS & set(retrieval.reason_codes))
    ):
        return _pass("retrieval_evidence_incomplete")
    if any(
        candidate.source_revision != current_graph
        for candidate in retrieval.selected_context
    ):
        return _pass("graph_revision_mismatch")

    material = tuple(
        candidate
        for candidate in retrieval.selected_context
        if _material_to_target(candidate, target_path)
    )
    if not material:
        return _pass("no_exact_target_material")

    certified: list[DecisionEvidenceClaim] = []
    incomplete_certified = False
    structurally_anchored_but_not_decision_relevant = False
    structurally_relevant_but_unaligned = False
    for candidate in material:
        support_kind = _support_kind(candidate, target_path)
        if support_kind is None:
            provenance = {str(item).strip().lower() for item in candidate.provenance}
            if (
                _target_anchor(candidate, target_path)
                and "support_channel:structural" in provenance
                and "structural_certified" in provenance
                and str(candidate.relation or "").strip().lower()
                in _DECISION_RELEVANT_STRUCTURAL_RELATIONS
                and not any(
                    item.startswith(("edge_endpoint_symbol:", "edge_endpoint_start:"))
                    for item in provenance
                )
            ):
                structurally_relevant_but_unaligned = True
            if (
                _target_anchor(candidate, target_path)
                and "support_channel:structural" in provenance
                and "structural_certified" in provenance
                and str(candidate.relation or "").strip().lower()
                not in _DECISION_RELEVANT_STRUCTURAL_RELATIONS
                and not any(
                    marker
                    in " ".join(
                        (str(candidate.relation or ""), *sorted(provenance))
                    ).lower()
                    for marker in _COCHANGE_MARKERS
                )
            ):
                structurally_anchored_but_not_decision_relevant = True
            continue
        claim = _complete_claim(
            candidate,
            target_path=target_path,
            support_kind=support_kind,
            operation=proposed.operation,
        )
        if claim is None:
            incomplete_certified = True
            continue
        certified.append(claim)
    if not certified:
        return _pass(
            "certified_evidence_incomplete"
            if incomplete_certified
            else (
                "structural_span_not_edge_aligned"
                if structurally_relevant_but_unaligned
                else (
                    "no_decision_relevant_evidence"
                    if structurally_anchored_but_not_decision_relevant
                    else "no_certified_mechanical_evidence"
                )
            )
        )

    claim_ids = tuple(claim.claim_id for claim in certified)
    if len(set(claim_ids)) != len(claim_ids):
        return _pass("duplicate_evidence_claim")
    if len(certified) > max_evidence_claims:
        return _pass("evidence_claim_limit")
    visible_claims = visible.visible_claim_ids
    if any(claim.claim_id in visible_claims for claim in certified):
        return _pass("evidence_already_provider_visible")

    computed_tokens = sum(claim.token_count for claim in certified)
    computed_chars = sum(len(claim.text) for claim in certified)
    measured_tokens = max(computed_tokens, retrieval.selected_token_count)
    effective_budget = min(max_evidence_tokens, retrieval.token_budget)
    if measured_tokens > effective_budget:
        return _pass("evidence_token_budget")
    if computed_chars > max_evidence_chars:
        return _pass("evidence_character_budget")

    bundle = DecisionEvidenceBundle(
        action_id=proposed.action_id,
        cycle_id=proposed.cycle_id,
        target_path=target_path,
        source_revision=current_revision,
        graph_revision=current_graph,
        retrieval_query_hash=retrieval.query_hash,
        selecting_request_hash=visible.selecting_request_hash,
        claims=tuple(certified),
        token_count=measured_tokens,
        char_count=computed_chars,
        complete=True,
    )
    return DecisionSufficiency(
        disposition=DecisionSufficiencyDisposition.RETURN_ELIGIBLE,
        return_eligible=True,
        reason_codes=("certified_missing_decision_evidence",),
        bundle=bundle,
        confidence=1.0,
    )


__all__ = [
    "DecisionEvidenceBundle",
    "DecisionEvidenceClaim",
    "DecisionSufficiency",
    "DecisionSufficiencyDisposition",
    "ProviderVisibleState",
    "compile_decision_sufficiency",
]
