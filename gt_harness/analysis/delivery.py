"""Validate the exact deterministic evidence exposed to a benchmark agent."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any


class DeliveryAuditError(ValueError):
    """Raised when a receipt is not structurally auditable."""


_HEX64 = re.compile(r"[0-9a-f]{64}")
_READY = frozenset({"READY", "READY_WITH_DECLARED_LIMITATIONS"})


def _normalized_relative_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        return ""
    return candidate.as_posix()


def _valid_hash(value: object) -> bool:
    return bool(_HEX64.fullmatch(str(value or "")))


def audit_treatment_delivery(
    treatment_receipt: dict[str, Any],
    *,
    initial_context: str | None = None,
    repository_end: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit GT delivery without inferring anything about hidden model reasoning.

    The result is data, not an exception-based PASS. Structurally malformed
    inputs raise ``DeliveryAuditError``; well-formed but invalid treatments
    return ``status=FAIL`` with stable reason codes.
    """

    if not isinstance(treatment_receipt, dict):
        raise DeliveryAuditError("treatment receipt must be an object")
    if treatment_receipt.get("schema") != "gt.treatment_receipt.v4":
        raise DeliveryAuditError("treatment receipt schema must be gt.treatment_receipt.v4")
    if treatment_receipt.get("treatment") != "groundtruth":
        raise DeliveryAuditError("delivery audit requires a groundtruth treatment")

    failures: list[str] = []
    if treatment_receipt.get("treatment_status") != "ACTIVE":
        failures.append("treatment_not_active")
    if treatment_receipt.get("graph_available") is not True:
        failures.append("graph_unavailable")
    if treatment_receipt.get("graph_status") not in _READY:
        failures.append("graph_not_ready")
    if treatment_receipt.get("delivery_reconciliation") != "PASS":
        failures.append("claim_reconciliation_failed")

    if repository_end is not None:
        if treatment_receipt.get("graph_commit_sha") != repository_end.get("commit_sha"):
            failures.append("graph_commit_mismatch")
        if treatment_receipt.get("source_revision") != repository_end.get("source_revision"):
            failures.append("graph_source_revision_mismatch")

    raw_deliveries = treatment_receipt.get("provider_delivery_receipts")
    if not isinstance(raw_deliveries, list):
        raise DeliveryAuditError("provider_delivery_receipts must be a list")
    deliveries = [row for row in raw_deliveries if isinstance(row, dict)]
    if len(deliveries) != len(raw_deliveries):
        raise DeliveryAuditError("every provider delivery receipt must be an object")
    declared_count = int(treatment_receipt.get("delivery_count") or 0)
    if declared_count != len(deliveries):
        failures.append("delivery_count_mismatch")
    if not deliveries:
        failures.append("evidence_not_delivered")

    seen_claims: set[str] = set()
    prior_call = 0
    delivered_paths: set[str] = set()
    delivery_rows: list[dict[str, Any]] = []
    for expected_index, delivery in enumerate(deliveries, start=1):
        row_failures: list[str] = []
        if delivery.get("schema") != "gt.provider_delivery.v2":
            row_failures.append("provider_delivery_schema")
        if int(delivery.get("delivery_index") or 0) != expected_index:
            row_failures.append("delivery_index_noncontiguous")
        kind = str(delivery.get("kind") or "")
        if kind not in {"repository_start", "repository_update"}:
            row_failures.append("delivery_kind_invalid")
        if expected_index == 1 and kind != "repository_start":
            row_failures.append("first_delivery_not_repository_start")
        call = int(delivery.get("delivered_before_call") or 0)
        if call < 1 or call < prior_call:
            row_failures.append("delivery_timing_invalid")
        prior_call = call
        if not _valid_hash(delivery.get("context_sha256")):
            row_failures.append("context_hash_invalid")
        if int(delivery.get("context_token_count") or 0) <= 0:
            row_failures.append("context_tokens_missing")
        if int(delivery.get("context_char_count") or 0) <= 0:
            row_failures.append("context_chars_missing")
        claims = delivery.get("serialized_claim_ids")
        if not isinstance(claims, list) or not all(
            isinstance(item, str) and item for item in claims
        ):
            row_failures.append("serialized_claims_invalid")
            claims = []
        duplicate_claims = seen_claims.intersection(claims)
        if duplicate_claims:
            row_failures.append("claim_redelivered")
        seen_claims.update(claims)

        role_paths = delivery.get("provider_visible_role_paths")
        if not isinstance(role_paths, dict):
            row_failures.append("provider_role_paths_invalid")
            role_paths = {}
        normalized_roles: dict[str, list[str]] = {}
        for role, values in role_paths.items():
            if not isinstance(values, list):
                row_failures.append("provider_role_paths_invalid")
                continue
            normalized: list[str] = []
            for value in values:
                path = _normalized_relative_path(value)
                if not path:
                    row_failures.append("provider_path_not_relative")
                    continue
                normalized.append(path)
                delivered_paths.add(path)
            normalized_roles[str(role)] = list(dict.fromkeys(normalized))

        if expected_index == 1 and initial_context is not None:
            actual = hashlib.sha256(initial_context.encode("utf-8")).hexdigest()
            if actual != delivery.get("context_sha256"):
                row_failures.append("initial_context_hash_mismatch")
        failures.extend(f"delivery_{expected_index}:{reason}" for reason in row_failures)
        delivery_rows.append(
            {
                "delivery_index": expected_index,
                "kind": kind,
                "delivered_before_call": call,
                "claim_count": len(claims),
                "role_paths": normalized_roles,
                "status": "PASS" if not row_failures else "FAIL",
                "failures": row_failures,
            }
        )

    declared_claims = {
        str(item) for item in treatment_receipt.get("delivered_claim_ids", []) if str(item)
    }
    if declared_claims != seen_claims:
        failures.append("delivered_claim_set_mismatch")
    if int(treatment_receipt.get("evidence_items_delivered") or 0) != len(seen_claims):
        failures.append("evidence_item_count_mismatch")

    return {
        "schema": "gt.delivery_audit.v1",
        "status": "PASS" if not failures else "FAIL",
        "delivery_count": len(deliveries),
        "claim_count": len(seen_claims),
        "delivered_path_count": len(delivered_paths),
        "delivered_paths": sorted(delivered_paths),
        "deliveries": delivery_rows,
        "failures": list(dict.fromkeys(failures)),
    }

