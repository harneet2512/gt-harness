"""Canonical accounting for evidence, controller state, and policy decisions.

Feature producers remain independent.  This module gives the host one typed,
replayable boundary at which every contribution is selected, suppressed, or
kept private exactly once before provider delivery.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
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
    BUDGET = "budget"


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
    ) -> GTContribution:
        normalized = _normalized_payload(payload)
        claims = tuple(dict.fromkeys(str(item) for item in claim_ids if str(item)))
        facts = tuple(dict.fromkeys(str(item) for item in fact_ids if str(item)))
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
            "selected_ids": list(self.selected_ids),
            "candidate_count": self.candidate_count,
            "accounted_count": self.accounted_count,
            "accounting": [row.as_dict() for row in self.accounting],
        }


def compile_contributions(
    contributions: tuple[GTContribution, ...],
    *,
    current_source_revision: str | tuple[str, ...],
    current_call: int,
    budget_chars: int,
    budget_tokens: int | None = None,
    token_counter: Callable[[str], int] = _default_token_counter,
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
            if used_chars + required > limit or over_token_budget:
                disposition = ContributionDisposition.BUDGET
                reasons = (
                    "complete_contribution_exceeds_token_budget"
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
    return CompiledContributions(
        payload=payload,
        selected_ids=tuple(item.contribution_id for item in selected),
        accounting=accounting,
        token_count=token_counter(payload),
        token_budget=token_limit,
    )


__all__ = [
    "CompiledContributions",
    "ContributionAccounting",
    "ContributionDisposition",
    "ContributionKind",
    "GTContribution",
    "compile_contributions",
]
