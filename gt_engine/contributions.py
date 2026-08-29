"""Canonical accounting for evidence, controller state, and policy decisions.

Feature producers remain independent.  This module gives the host one typed,
replayable boundary at which every contribution is selected, suppressed, or
kept private exactly once before provider delivery.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ContributionKind(StrEnum):
    EVIDENCE = "evidence"
    CONTROLLER_STATE = "controller_state"
    POLICY_DECISION = "policy_decision"


class ContributionDisposition(StrEnum):
    SELECTED = "selected"
    CONTROLLER_ONLY = "controller_only"
    STALE_SOURCE_REVISION = "stale_source_revision"
    EXPIRED_WINDOW = "expired_window"
    INELIGIBLE_CALL = "ineligible_call"
    DUPLICATE_CLAIM = "duplicate_claim"
    DUPLICATE_TEXT = "duplicate_text"
    UNSAFE_PROVENANCE = "unsafe_provenance"
    VALUE_UNCERTIFIED = "value_uncertified"
    VALUE_REJECTED = "value_rejected"
    BUDGET = "budget"


class ProviderValueClass(StrEnum):
    INSTRUCTION_ENTAILED = "instruction_entailed"
    OBSERVATION_DUPLICATE = "observation_duplicate"
    ACTION_LOCAL_RELATION = "action_local_relation"
    EXECUTION_CONTRADICTION = "execution_contradiction"
    CERTIFIED_PREDECISION_GAP = "certified_predecision_gap"
    AMBIGUOUS_OR_PARTIAL = "ambiguous_or_partial"


class ProviderValueDisposition(StrEnum):
    CONTROLLER_ONLY = "controller_only"
    SAME_OBSERVATION = "same_observation"
    PREDECISION = "predecision"


class ProviderValueCompleteness(StrEnum):
    EXACT = "exact"
    LOWER_BOUND = "lower_bound"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"


# The 18 direct features remain active and auditable, but only the rows
# below can independently establish one of the three provider-value classes.
# Keeping this table here makes the authority boundary exhaustive instead of
# treating every feature that happens to render text as an execution failure.
_FEATURE_EXECUTION_CONTRADICTIONS = frozenset(
    {"syntax_result", "covering_red", "recovery", "submit_refusal"}
)
_FEATURE_RELATION_CANDIDATES = frozenset({"signature_delta", "newfile_precedent"})
_FEATURE_PREDECISION_CANDIDATES = frozenset({"GT_EDIT_CHECK"})
_FEATURE_CONTROLLER_ONLY = frozenset(
    {
        "caller_contract",
        "def_partition",
        "localization",
        "obligations",
        "GT_CERT_DELIVERY",
        "GT_CHANGE_SURFACE",
        "GT_HYPOTHESIS",
        "GT_LOC_RESLOT",
        "GT_PATCH_DELTA",
        "GT_SS_SUBMIT_RED",
        "select_catalog",
    }
)
FEATURE_PROVIDER_VALUE_FEATURE_IDS = frozenset().union(
    _FEATURE_EXECUTION_CONTRADICTIONS,
    _FEATURE_RELATION_CANDIDATES,
    _FEATURE_PREDECISION_CANDIDATES,
    _FEATURE_CONTROLLER_ONLY,
)


@dataclass(frozen=True, slots=True)
class ProviderValueCertificate:
    """Replayable proof that provider-visible evidence can change useful work."""

    claim_id: str
    value_class: ProviderValueClass
    disposition: ProviderValueDisposition
    authority: str
    source_revision: str
    graph_revision: str = ""
    anchors: tuple[str, ...] = ()
    novelty_basis: str = ""
    decision_point: str = ""
    replaces_operation: str = ""
    materiality_reason: str = ""
    completeness: ProviderValueCompleteness = ProviderValueCompleteness.EXACT
    reason_codes: tuple[str, ...] = ()

    @property
    def provider_visible_allowed(self) -> bool:
        if self.disposition is ProviderValueDisposition.CONTROLLER_ONLY:
            return False
        if self.completeness is not ProviderValueCompleteness.EXACT:
            return False
        if self.value_class not in {
            ProviderValueClass.ACTION_LOCAL_RELATION,
            ProviderValueClass.EXECUTION_CONTRADICTION,
            ProviderValueClass.CERTIFIED_PREDECISION_GAP,
        }:
            return False
        return bool(
            self.claim_id
            and self.authority
            and self.source_revision
            and self.anchors
            and self.novelty_basis
            and self.decision_point
            and self.replaces_operation
            and self.materiality_reason
        )

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["value_class"] = self.value_class.value
        row["disposition"] = self.disposition.value
        row["completeness"] = self.completeness.value
        return row


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", "surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_payload(payload: str) -> str:
    return "\n".join(line.rstrip() for line in payload.strip().splitlines())


def _default_token_counter(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(text or ""), re.UNICODE))


def build_provider_value_certificates(
    *,
    surface: str,
    claim_ids: tuple[str, ...],
    fact_ids: tuple[str, ...],
    claim_metadata: tuple[Mapping[str, Any], ...],
    source_revision: str,
    evidence_action: int,
) -> tuple[ProviderValueCertificate, ...]:
    """Classify provider value without granting truth automatic delivery authority."""

    metadata_by_id: dict[str, Mapping[str, Any]] = {}
    for row in claim_metadata:
        for key in ("claim_id", "fact_id", "evidence_id"):
            value = str(row.get(key) or "")
            if value:
                metadata_by_id[value] = row
    required_ids = tuple(dict.fromkeys(claim_ids or fact_ids))
    certificates: list[ProviderValueCertificate] = []
    for claim_id in required_ids:
        row = metadata_by_id.get(claim_id)
        if row is None:
            continue
        reason_codes: tuple[str, ...] = ()
        explicit_class = str(row.get("provider_value_class") or "")
        explicit_disposition = str(row.get("provider_value_disposition") or "")
        explicit_completeness = str(row.get("provider_value_completeness") or "exact")
        feature_id = str(row.get("feature_id") or "")
        if surface == "feature_fact":
            anchors = tuple(
                str(item)
                for item in row.get("provider_value_anchors") or ()
                if str(item)
            )
            completeness = ProviderValueCompleteness.EXACT
            if (
                feature_id in _FEATURE_EXECUTION_CONTRADICTIONS
                and evidence_action > 0
                and anchors
            ):
                value_class = ProviderValueClass.EXECUTION_CONTRADICTION
                disposition = ProviderValueDisposition.SAME_OBSERVATION
                novelty_basis = "new_execution_state_not_represented"
                decision_point = "next_executor_request"
                replaces_operation = "failure_or_validation_rediscovery"
                reason_codes = ("feature_execution_contradiction",)
            elif (
                feature_id in _FEATURE_RELATION_CANDIDATES
                and evidence_action > 0
                and anchors
                and bool(row.get("certified_nonlocal_relation"))
                and bool(row.get("relation") or row.get("relation_endpoint"))
            ):
                value_class = ProviderValueClass.ACTION_LOCAL_RELATION
                disposition = ProviderValueDisposition.SAME_OBSERVATION
                novelty_basis = "certified_nonlocal_relation_absent_from_observation"
                decision_point = "next_executor_request"
                replaces_operation = "repository_relationship_search"
                reason_codes = ("feature_certified_nonlocal_relation",)
            elif (
                feature_id in _FEATURE_PREDECISION_CANDIDATES
                and anchors
                and bool(row.get("certified_predecision_gap"))
            ):
                value_class = ProviderValueClass.CERTIFIED_PREDECISION_GAP
                disposition = ProviderValueDisposition.PREDECISION
                novelty_basis = "certified_decision_gap_absent_from_provider_history"
                decision_point = "next_executor_request"
                replaces_operation = "declared_check_rediscovery"
                reason_codes = ("feature_certified_predecision_gap",)
            else:
                value_class = ProviderValueClass.INSTRUCTION_ENTAILED
                disposition = ProviderValueDisposition.CONTROLLER_ONLY
                novelty_basis = "no_feature_specific_provider_authority"
                decision_point = "none"
                replaces_operation = "none"
                reason_codes = (
                    "feature_controller_only"
                    if feature_id in _FEATURE_CONTROLLER_ONLY
                    or feature_id in _FEATURE_RELATION_CANDIDATES
                    or feature_id in _FEATURE_PREDECISION_CANDIDATES
                    else "unknown_feature_controller_only",
                )
        elif explicit_class and explicit_disposition:
            value_class = ProviderValueClass(explicit_class)
            disposition = ProviderValueDisposition(explicit_disposition)
            completeness = ProviderValueCompleteness(explicit_completeness)
            novelty_basis = str(row.get("provider_value_novelty_basis") or "")
            decision_point = str(row.get("provider_value_decision_point") or "")
            replaces_operation = str(row.get("provider_value_replaces_operation") or "")
            anchors = tuple(
                str(item)
                for item in row.get("provider_value_anchors") or ()
                if str(item)
            )
        else:
            materiality = str(row.get("materiality_reason") or "")
            authority = str(row.get("authority") or "")
            relation = str(row.get("relation") or "")
            provided_anchors = tuple(
                str(item)
                for item in row.get("provider_value_anchors") or ()
                if str(item)
            )
            anchor = str(
                row.get("relation_endpoint")
                or row.get("path")
                or row.get("declared_validation_id")
                or ""
            )
            anchors = provided_anchors or ((anchor,) if anchor else ())
            completeness = ProviderValueCompleteness.EXACT
            if materiality in {
                "current_attributable_failure",
                "declared_validation_status_change",
            }:
                value_class = ProviderValueClass.EXECUTION_CONTRADICTION
                disposition = ProviderValueDisposition.SAME_OBSERVATION
                novelty_basis = "new_execution_state_not_represented"
                decision_point = "next_executor_request"
                replaces_operation = "failure_or_validation_rediscovery"
            elif (
                materiality == "new_unresolved_task_obligation"
                and authority
                in {
                    "certified_relation",
                    "certified_structural",
                    "certified_composition",
                }
                and bool(relation or row.get("constituent_claim_ids"))
            ):
                value_class = ProviderValueClass.ACTION_LOCAL_RELATION
                disposition = (
                    ProviderValueDisposition.SAME_OBSERVATION
                    if evidence_action > 0
                    else ProviderValueDisposition.PREDECISION
                )
                novelty_basis = "certified_nonlocal_obligation_absent_from_observation"
                decision_point = "next_executor_request"
                replaces_operation = "coupled_change_relationship_search"
            elif (
                surface in {"repository_context", "repository_semantic", "repository_process"}
                and evidence_action > 0
                and authority
                in {
                    "certified_relation",
                    "certified_structural",
                    "certified_composition",
                    "compiler_semantic",
                    "lsp_semantic",
                    "parser_structural",
                }
            ):
                value_class = ProviderValueClass.ACTION_LOCAL_RELATION
                disposition = ProviderValueDisposition.SAME_OBSERVATION
                novelty_basis = "nonlocal_relation_absent_from_observation"
                decision_point = "next_executor_request"
                replaces_operation = "repository_relationship_search"
            elif (
                surface == "preemptive_retrieval"
                and evidence_action > 0
                and bool(anchors)
                and str(row.get("support_kind") or "")
                in {"certified_relation", "validation_candidate"}
                and bool(row.get("supporting_channels"))
                and str(row.get("origin") or "") == "preexisting_repository"
                and authority in {"certified_relation", "ranking_support"}
                and bool(materiality)
            ):
                value_class = ProviderValueClass.ACTION_LOCAL_RELATION
                disposition = ProviderValueDisposition.SAME_OBSERVATION
                novelty_basis = "ranked_nonlocal_evidence_absent_from_observation"
                decision_point = "next_executor_request"
                replaces_operation = "repository_search_or_read"
            else:
                value_class = ProviderValueClass.INSTRUCTION_ENTAILED
                disposition = ProviderValueDisposition.CONTROLLER_ONLY
                novelty_basis = "no_counterfactual_replacement_proven"
                decision_point = "none"
                replaces_operation = "none"
        certificates.append(
            ProviderValueCertificate(
                claim_id=claim_id,
                value_class=value_class,
                disposition=disposition,
                authority=str(row.get("authority") or row.get("origin") or "unknown"),
                source_revision=source_revision,
                graph_revision=str(row.get("graph_revision") or ""),
                anchors=anchors,
                novelty_basis=novelty_basis,
                decision_point=decision_point,
                replaces_operation=replaces_operation,
                materiality_reason=str(row.get("materiality_reason") or ""),
                completeness=completeness,
                reason_codes=(
                    reason_codes
                    or tuple(
                        str(item)
                        for item in row.get("provider_value_reason_codes") or ()
                    )
                ),
            )
        )
    return tuple(certificates)


@dataclass(frozen=True, slots=True)
class GTContribution:
    contribution_id: str
    surface: str
    kind: ContributionKind
    payload: str
    payload_hash: str
    claim_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    evidence_action: int
    eligible_call: int
    source_revision: str
    priority: int
    claim_metadata: tuple[dict[str, Any], ...] = ()
    value_certificates: tuple[ProviderValueCertificate, ...] = ()
    lifecycle_required: bool = False

    @classmethod
    def create(
        cls,
        *,
        surface: str,
        kind: ContributionKind,
        payload: str,
        claim_ids: tuple[str, ...] = (),
        fact_ids: tuple[str, ...] = (),
        evidence_action: int,
        eligible_call: int,
        source_revision: str,
        priority: int,
        claim_metadata: tuple[Mapping[str, Any], ...] = (),
        value_certificates: tuple[ProviderValueCertificate, ...] = (),
        lifecycle_required: bool = False,
    ) -> GTContribution:
        normalized = _normalized_payload(payload)
        claims = tuple(dict.fromkeys(str(item) for item in claim_ids if str(item)))
        facts = tuple(dict.fromkeys(str(item) for item in fact_ids if str(item)))
        metadata = tuple(
            {
                str(key): value
                for key, value in dict(row).items()
                if not str(key).startswith("_")
            }
            for row in claim_metadata
        )
        if kind is ContributionKind.EVIDENCE and not (normalized and (claims or facts)):
            raise ValueError("grounded evidence requires payload plus a claim or fact ID")
        identity = {
            "surface": str(surface),
            "kind": kind.value,
            "payload": normalized,
            "claim_ids": claims,
            "fact_ids": facts,
            "evidence_action": max(0, int(evidence_action)),
            "eligible_call": max(1, int(eligible_call)),
            "source_revision": str(source_revision),
            "claim_metadata": metadata,
            "value_certificates": tuple(
                certificate.as_dict() for certificate in value_certificates
            ),
            "lifecycle_required": bool(lifecycle_required),
        }
        return cls(
            contribution_id="gt-contribution-" + _canonical_hash(identity)[:20],
            surface=str(surface),
            kind=kind,
            payload=normalized,
            payload_hash=_canonical_hash(normalized) if normalized else "",
            claim_ids=claims,
            fact_ids=facts,
            evidence_action=max(0, int(evidence_action)),
            eligible_call=max(1, int(eligible_call)),
            source_revision=str(source_revision),
            priority=int(priority),
            claim_metadata=metadata,
            value_certificates=tuple(value_certificates),
            lifecycle_required=bool(lifecycle_required),
        )

    @property
    def provider_value_failures(self) -> tuple[str, ...]:
        if self.kind is not ContributionKind.EVIDENCE or not self.payload:
            return ()
        required_ids = set(self.claim_ids or self.fact_ids)
        certificates = {
            certificate.claim_id: certificate for certificate in self.value_certificates
        }
        failures: list[str] = []
        for claim_id in sorted(required_ids):
            certificate = certificates.get(claim_id)
            if certificate is None:
                failures.append(f"missing_value_certificate:{claim_id}")
            elif not certificate.provider_visible_allowed:
                failures.append(f"provider_value_rejected:{claim_id}")
            elif (
                certificate.source_revision
                and certificate.source_revision != self.source_revision
            ):
                failures.append(f"value_certificate_revision_mismatch:{claim_id}")
        return tuple(failures)

    @property
    def unsafe_provider_origins(self) -> tuple[str, ...]:
        unsafe = {"model_authored", "generated_artifact", "unknown"}
        if self.kind is ContributionKind.EVIDENCE and not self.claim_metadata:
            return ("unknown",)
        if any(not str(row.get("origin") or "") for row in self.claim_metadata):
            return ("unknown",)
        covered_ids = {
            str(value)
            for row in self.claim_metadata
            for value in (
                row.get("claim_id"),
                row.get("fact_id"),
                row.get("evidence_id"),
            )
            if str(value or "")
        }
        required_ids = set(self.claim_ids or self.fact_ids)
        if required_ids - covered_ids:
            return ("unknown",)
        return tuple(
            dict.fromkeys(
                str(row.get("origin") or "")
                for row in self.claim_metadata
                if str(row.get("origin") or "") in unsafe
            )
        )

    @property
    def critical(self) -> bool:
        return bool(self.claim_metadata) and all(
            str(row.get("materiality_reason") or "") in {
                "current_attributable_failure",
                "declared_validation_status_change",
                "new_unresolved_task_obligation",
            }
            for row in self.claim_metadata
        )


@dataclass(frozen=True, slots=True)
class ContributionAccounting:
    contribution_id: str
    surface: str
    disposition: ContributionDisposition
    reason_codes: tuple[str, ...]
    chars: int

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["disposition"] = self.disposition.value
        return row


@dataclass(frozen=True, slots=True)
class CompiledContributions:
    payload: str
    selected_ids: tuple[str, ...]
    accounting: tuple[ContributionAccounting, ...]
    token_count: int = 0
    token_budget: int | None = None
    task_budget_token_count: int = 0
    task_budget_token_limit: int | None = None
    value_certificates: tuple[dict[str, Any], ...] = ()

    @property
    def candidate_count(self) -> int:
        return len(self.accounting)

    @property
    def accounted_count(self) -> int:
        return len(self.accounting)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.contribution_compiler.v1",
            "payload_chars": len(self.payload),
            "payload_tokens": self.token_count,
            "token_budget": self.token_budget,
            # Keep per-call task-budget usage explicit so the release gate can
            # reconcile it with the cumulative task budget. Lifecycle-required
            # context is intentionally excluded from this count.
            "task_budget_tokens": self.task_budget_token_count,
            "task_budget_token_limit": self.task_budget_token_limit,
            "selected_ids": list(self.selected_ids),
            "value_certificates": [dict(row) for row in self.value_certificates],
            "candidate_count": self.candidate_count,
            "accounted_count": self.accounted_count,
            "accounting": [row.as_dict() for row in self.accounting],
        }


@dataclass(slots=True)
class ContributionTaskBudget:
    """Cumulative provider-visible GT budget with a narrow critical reserve."""

    token_budget: int
    critical_reserve_tokens: int = 0
    used_regular_tokens: int = 0
    used_critical_tokens: int = 0

    def __post_init__(self) -> None:
        self.token_budget = max(0, int(self.token_budget))
        self.critical_reserve_tokens = min(
            self.token_budget, max(0, int(self.critical_reserve_tokens))
        )

    @property
    def regular_budget(self) -> int:
        return self.token_budget - self.critical_reserve_tokens

    def available_tokens(self, *, critical: bool) -> int:
        regular_remaining = max(0, self.regular_budget - self.used_regular_tokens)
        if not critical:
            return regular_remaining
        reserve_remaining = max(
            0, self.critical_reserve_tokens - self.used_critical_tokens
        )
        return regular_remaining + reserve_remaining

    def commit(self, tokens: int, *, critical: bool) -> None:
        amount = max(0, int(tokens))
        available = self.available_tokens(critical=critical)
        if amount > available:
            raise ValueError("provider contribution exceeds remaining task budget")
        regular_remaining = max(0, self.regular_budget - self.used_regular_tokens)
        regular_used = min(amount, regular_remaining)
        self.used_regular_tokens += regular_used
        if critical:
            self.used_critical_tokens += amount - regular_used

    @property
    def used_tokens(self) -> int:
        return self.used_regular_tokens + self.used_critical_tokens

    @property
    def exhausted(self) -> bool:
        return self.available_tokens(critical=True) == 0

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "token_budget": self.token_budget,
            "task_budget_tokens": self.used_tokens,
            "task_budget_token_limit": self.token_budget,
            "critical_reserve_tokens": self.critical_reserve_tokens,
            "used_regular_tokens": self.used_regular_tokens,
            "used_critical_tokens": self.used_critical_tokens,
            "used_tokens": self.used_tokens,
            "remaining_regular_tokens": self.available_tokens(critical=False),
            "remaining_total_tokens": self.available_tokens(critical=True),
            "exhausted": self.exhausted,
        }


def compile_contributions(
    contributions: tuple[GTContribution, ...],
    *,
    current_source_revision: str | tuple[str, ...],
    current_call: int,
    budget_chars: int,
    budget_tokens: int | None = None,
    task_budget_tokens: int | None = None,
    token_counter: Callable[[str], int] = _default_token_counter,
    allow_noncritical: bool = True,
) -> CompiledContributions:
    """Select complete contributions deterministically; ambiguity omits text."""

    if isinstance(current_source_revision, str):
        valid_revisions = {current_source_revision}
    else:
        valid_revisions = {str(revision) for revision in current_source_revision if str(revision)}
    decisions: dict[str, ContributionAccounting] = {}
    selected: list[GTContribution] = []
    selected_claims: set[str] = set()
    selected_facts: set[str] = set()
    selected_payload_hashes: set[str] = set()
    used_chars = 0
    limit = max(0, int(budget_chars))
    token_limit = None if budget_tokens is None else max(0, int(budget_tokens))
    task_token_limit = (
        None if task_budget_tokens is None else max(0, int(task_budget_tokens))
    )

    ordered = sorted(
        enumerate(contributions),
        key=lambda item: (item[1].priority, item[0], item[1].contribution_id),
    )
    for _, contribution in ordered:
        disposition = ContributionDisposition.SELECTED
        reasons: tuple[str, ...] = ()
        if contribution.source_revision and contribution.source_revision not in valid_revisions:
            disposition = ContributionDisposition.STALE_SOURCE_REVISION
            reasons = ("source_revision_mismatch",)
        elif contribution.eligible_call < current_call:
            disposition = ContributionDisposition.EXPIRED_WINDOW
            reasons = ("first_eligible_request_passed",)
        elif contribution.eligible_call > current_call:
            disposition = ContributionDisposition.INELIGIBLE_CALL
            reasons = ("future_eligible_call",)
        elif contribution.kind is not ContributionKind.EVIDENCE or not contribution.payload:
            disposition = ContributionDisposition.CONTROLLER_ONLY
            reasons = ("not_provider_text",)
        elif contribution.unsafe_provider_origins:
            disposition = ContributionDisposition.UNSAFE_PROVENANCE
            reasons = tuple(
                f"{origin}_provider_authority"
                for origin in contribution.unsafe_provider_origins
            )
        elif contribution.provider_value_failures:
            missing = any(
                reason.startswith("missing_value_certificate:")
                for reason in contribution.provider_value_failures
            )
            disposition = (
                ContributionDisposition.VALUE_UNCERTIFIED
                if missing
                else ContributionDisposition.VALUE_REJECTED
            )
            reasons = contribution.provider_value_failures
        elif (
            not allow_noncritical
            and not contribution.critical
            and not contribution.lifecycle_required
        ):
            disposition = ContributionDisposition.BUDGET
            reasons = ("task_budget_reserved_for_critical_evidence",)
        elif selected_claims.intersection(contribution.claim_ids) or selected_facts.intersection(
            contribution.fact_ids
        ):
            disposition = ContributionDisposition.DUPLICATE_CLAIM
            reasons = ("claim_or_fact_already_selected",)
        elif contribution.payload_hash in selected_payload_hashes:
            disposition = ContributionDisposition.DUPLICATE_TEXT
            reasons = ("payload_already_selected",)
        else:
            separator_chars = 2 if selected else 0
            required = separator_chars + len(contribution.payload)
            candidate_payload = "\n\n".join(
                (*[item.payload for item in selected], contribution.payload)
            )
            over_token_budget = bool(
                token_limit is not None and token_counter(candidate_payload) > token_limit
            )
            candidate_task_payload = "\n\n".join(
                item.payload
                for item in (*selected, contribution)
                if not item.lifecycle_required
            )
            over_task_budget = bool(
                task_token_limit is not None
                and token_counter(candidate_task_payload) > task_token_limit
            )
            if used_chars + required > limit or over_token_budget or over_task_budget:
                disposition = ContributionDisposition.BUDGET
                reasons = (
                    "complete_contribution_exceeds_task_budget"
                    if over_task_budget
                    else "complete_contribution_exceeds_token_budget"
                    if over_token_budget
                    else "complete_contribution_does_not_fit",
                )
            else:
                selected.append(contribution)
                selected_claims.update(contribution.claim_ids)
                selected_facts.update(contribution.fact_ids)
                selected_payload_hashes.add(contribution.payload_hash)
                used_chars += required
        decisions[contribution.contribution_id] = ContributionAccounting(
            contribution_id=contribution.contribution_id,
            surface=contribution.surface,
            disposition=disposition,
            reason_codes=reasons,
            chars=len(contribution.payload),
        )

    accounting = tuple(decisions[item.contribution_id] for item in contributions)
    payload = "\n\n".join(item.payload for item in selected)
    task_payload = "\n\n".join(
        item.payload for item in selected if not item.lifecycle_required
    )
    return CompiledContributions(
        payload=payload,
        selected_ids=tuple(item.contribution_id for item in selected),
        accounting=accounting,
        token_count=token_counter(payload),
        token_budget=token_limit,
        task_budget_token_count=token_counter(task_payload),
        task_budget_token_limit=task_token_limit,
        value_certificates=tuple(
            {
                "contribution_id": item.contribution_id,
                "surface": item.surface,
                **certificate.as_dict(),
            }
            for item in selected
            for certificate in item.value_certificates
        ),
    )


__all__ = [
    "CompiledContributions",
    "ContributionAccounting",
    "ContributionDisposition",
    "ContributionKind",
    "ContributionTaskBudget",
    "GTContribution",
    "ProviderValueCertificate",
    "ProviderValueClass",
    "ProviderValueCompleteness",
    "ProviderValueDisposition",
    "FEATURE_PROVIDER_VALUE_FEATURE_IDS",
    "build_provider_value_certificates",
    "compile_contributions",
]
