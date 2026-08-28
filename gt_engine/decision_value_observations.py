"""Export untrusted corpus observations from finalized production receipts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .run_receipt_v2 import is_complete_run_receipt

_VISIBLE_STAGES = frozenset({"DELIVERED", "CONSUMED", "VALIDATED", "CONTRADICTED"})


def observation_from_run_receipt(
    case_id: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Project only provider-visible certified claims from one v2 receipt."""

    row = dict(receipt)
    if not case_id:
        raise ValueError("case_id is required")
    if not is_complete_run_receipt(row):
        raise ValueError("run receipt is incomplete or invalid")
    deliveries = list(row.get("deliveries") or ())
    repository_revisions = [
        str(delivery.get("repository_revision") or "")
        for delivery in deliveries
        if str(delivery.get("repository_revision") or "")
    ]
    repository_revision = repository_revisions[0] if repository_revisions else ""
    if repository_revisions and len(set(repository_revisions)) != 1:
        # A corpus case binds one immutable repository revision. Edited run
        # revisions cannot be collapsed into a single owner-ranking oracle row.
        repository_revision = str(
            row.get("initial_repository_revision") or repository_revisions[0]
        )

    facts: list[dict[str, Any]] = []
    ranked_owners: list[str] = []
    seen_claims: set[str] = set()
    for lifecycle in row.get("feature_lifecycle_transitions") or ():
        if str(lifecycle.get("stage") or "") not in _VISIBLE_STAGES:
            continue
        visible_hex = str(lifecycle.get("model_visible_bytes_hex") or "")
        if not visible_hex:
            continue
        for claim in lifecycle.get("claims") or ():
            claim_id = str(claim.get("claim_id") or "")
            if not claim_id or claim_id in seen_claims:
                continue
            seen_claims.add(claim_id)
            facts.append(
                {
                    "claim_id": claim_id,
                    "fact": str(claim.get("text") or ""),
                    "source_evidence": [
                        {
                            "path": str(evidence.get("path") or ""),
                            "start_line": int(evidence.get("start_line") or 0),
                            "end_line": int(evidence.get("end_line") or 0),
                            "content_sha256": str(
                                evidence.get("content_sha256") or ""
                            ),
                        }
                        for evidence in claim.get("source_evidence") or ()
                    ],
                }
            )
            role = str(getattr(claim.get("role"), "value", claim.get("role") or ""))
            if role != "edit_owner":
                continue
            owner_candidates = [str(claim.get("symbol_identity") or "")]
            owner_candidates.extend(
                str(evidence.get("path") or "")
                for evidence in claim.get("source_evidence") or ()
            )
            for owner in owner_candidates:
                if owner and owner not in ranked_owners:
                    ranked_owners.append(owner)
    return {
        "schema": "gt.decision_value_observation.v1",
        "case_id": case_id,
        "repository_revision": repository_revision,
        "ranked_owners": ranked_owners,
        "certified_facts": facts,
    }


def observations_from_receipts(
    rows: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[dict[str, Any], ...]:
    return tuple(observation_from_run_receipt(case_id, receipt) for case_id, receipt in rows)


__all__ = ["observation_from_run_receipt", "observations_from_receipts"]
