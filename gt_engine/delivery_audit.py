"""Canonical accounting for every model-visible GT delivery surface.

Receipts historically exposed ``guidance_deliveries`` as the only provider
surface.  The central runtime now has additional, independently receipted
surfaces.  This module is deliberately read-only and schema-tolerant: it
normalises those rows into one audit stream without inferring delivery from
effect counts or later model actions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from gt_engine.thin_compiler import (
    NON_MATERIAL_PROVIDER_RELATIONS,
    PROVIDER_MATERIAL_RELATIONS,
    PROVIDER_MATERIALITY_REASONS,
)

SURFACE_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("preemptive_retrieval", ("preemptive_retrieval", "deliveries")),
    ("persistent_execution_state", ("persistent_execution_state", "deliveries")),
    ("guidance", ("guidance_deliveries",)),
    ("repository_frontier", ("repository_intelligence", "frontier_deliveries")),
    ("progress", ("progress", "fact_deliveries")),
    ("completion", ("completion", "deliveries")),
    ("completion_visible", ("completion", "visible_deliveries")),
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8", "surrogatepass")


def _contexts(receipt: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["call"]): row
        for row in receipt.get("model_call_contexts") or ()
        if isinstance(row, dict) and isinstance(row.get("call"), int)
    }


def _path_value(receipt: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = receipt
    for key in path:
        if not isinstance(value, dict):
            return ()
        value = value.get(key)
    return value if isinstance(value, list) else ()


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _claims(row: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("claim_ids", "evidence_ids", "effect_ids", "fact_ids"):
        value = row.get(key) or ()
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value if str(item).strip())
    if not values:
        # Historical guidance/frontier rows predate claim_ids but contain
        # concrete facts.  Give those anchors a stable accounting identity so
        # old receipts remain auditable without inventing model usage.
        for item in row.get("facts") or ():
            if not isinstance(item, dict):
                continue
            anchor = ":".join(
                str(item.get(key) or "").strip()
                for key in ("path", "line", "symbol", "value")
            ).strip(":")
            if anchor:
                values.append("anchor:" + hashlib.sha256(anchor.encode()).hexdigest()[:16])
        for key in ("claim_anchors", "anchors"):
            value = row.get(key) or ()
            if isinstance(value, (list, tuple)):
                values.extend("anchor:" + str(item) for item in value if str(item).strip())
    return tuple(dict.fromkeys(values))


def _message_indices(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("provider_message_indices")
    if not isinstance(raw, (list, tuple)):
        raw = (row.get("message_index"),)
    return tuple(
        int(item)
        for item in raw
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0
    )


def _identity(surface: str, row: dict[str, Any], claims: tuple[str, ...]) -> str:
    explicit = _first(row, "delivery_id", "frame_id", "provider_delivery_id")
    if explicit:
        return f"id:{explicit}"
    return "row:" + hashlib.sha256(
        _canonical(
            {
                "surface": surface,
                "claims": claims,
                "action": _first(row, "evidence_action", "action"),
                "call": _first(row, "delivered_before_call", "call"),
                "hash": row.get("request_payload_sha256"),
            }
        )
    ).hexdigest()[:24]


def collect_provider_deliveries(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all explicitly receipted model-visible delivery rows.

    Surface names are stable public accounting keys.  Missing optional
    surfaces are an empty stream; private completion certificates and engine
    state are intentionally not counted unless they expose an explicit
    ``deliveries``/``visible_deliveries`` list.
    """

    rows: list[dict[str, Any]] = []
    for surface, path in SURFACE_PATHS:
        for index, raw in enumerate(_path_value(receipt, path), start=1):
            if not isinstance(raw, dict):
                continue
            claims = _claims(raw)
            delivered = _first(raw, "delivered_before_call", "delivered_call", "call")
            eligible = _first(
                raw,
                "first_eligible_call",
                "eligible_call",
                "first_eligible_request",
            )
            provider_hash = str(raw.get("provider_messages_sha256") or "")
            request_hash = str(raw.get("request_payload_sha256") or "")
            selected_evidence = tuple(
                item
                for item in (raw.get("selected_evidence") or ())
                if isinstance(item, dict)
            )
            claim_metadata = tuple(
                item
                for item in (raw.get("claim_metadata") or ())
                if isinstance(item, dict)
            )
            rows.append(
                {
                    "surface": surface,
                    "surface_index": index,
                    "delivery_id": str(raw.get("delivery_id") or ""),
                    "identity": _identity(surface, raw, claims),
                    "feature_id": str(raw.get("feature_id") or ""),
                    "evidence_action": _first(raw, "evidence_action", "action"),
                    "first_eligible_call": eligible,
                    "delivered_before_call": delivered,
                    "request_payload_sha256": request_hash,
                    "provider_messages_sha256": provider_hash,
                    "chars": int(_first(raw, "chars", "payload_chars") or 0),
                    "claim_ids": list(claims),
                    "claim_count": len(claims),
                    "selected_evidence": list(selected_evidence),
                    "claim_metadata": list(claim_metadata),
                    "provider_message_indices": list(_message_indices(raw)),
                    "delivered_before_model_query": bool(
                        _first(raw, "delivered_before_model_query", "delivered_before_query")
                    ),
                    "one_step_late": bool(raw.get("one_step_late")),
                    "predictive": bool(
                        raw.get("predictive")
                        if raw.get("predictive") is not None
                        else not bool(raw.get("not_predictive", False))
                    ),
                    "raw": raw,
                }
            )
    return rows


def audit_provider_deliveries(
    receipt: dict[str, Any], *, task: str = "task"
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Validate hashes/timing/claims and return surface-separated totals."""

    contexts = _contexts(receipt)
    failures: list[str] = []
    identities: set[str] = set()
    seen_claims: set[str] = set()
    rows = collect_provider_deliveries(receipt)
    for index, row in enumerate(rows, start=1):
        identity = row["identity"]
        duplicate_identity = identity in identities
        claim_overlap = seen_claims.intersection(row["claim_ids"])
        permitted_state_refresh = False
        if duplicate_identity:
            failures.append(f"{task}:duplicate_provider_delivery:{identity}")
        if claim_overlap and not permitted_state_refresh:
            failures.append(
                f"{task}:duplicate_provider_claim:{','.join(sorted(claim_overlap))}"
            )
        identities.add(identity)
        seen_claims.update(row["claim_ids"])
        duplicate = duplicate_identity or bool(claim_overlap and not permitted_state_refresh)
        row["persistent_state_refresh"] = permitted_state_refresh
        delivered = row["delivered_before_call"]
        eligible = row["first_eligible_call"]
        context = contexts.get(delivered) if isinstance(delivered, int) else None
        if context is None:
            failures.append(f"{task}:delivery_call_context_missing:{index}")
        dispatch_valid = bool(
            context
            and str(context.get("dispatch_status") or "")
            in {"invoked", "response_received", "response_error"}
        )
        if context is not None and not dispatch_valid:
            failures.append(f"{task}:delivery_request_not_dispatched:{index}")
        request_hash = row["request_payload_sha256"]
        if not request_hash:
            failures.append(f"{task}:delivery_missing_provider_request_hash:{index}")
        if not row["provider_messages_sha256"] and context:
            row["provider_messages_sha256"] = str(context.get("provider_messages_sha256") or "")
        if not row["provider_messages_sha256"]:
            failures.append(f"{task}:delivery_missing_provider_messages_hash:{index}")
        if context and request_hash != str(context.get("request_payload_sha256") or ""):
            failures.append(f"{task}:delivery_request_hash_context_mismatch:{index}")
        if context and row["provider_messages_sha256"] != str(
            context.get("provider_messages_sha256") or ""
        ):
            failures.append(f"{task}:delivery_provider_hash_context_mismatch:{index}")
        message_indices = row["provider_message_indices"]
        message_index_valid = False
        if not message_indices:
            failures.append(f"{task}:delivery_message_index_missing:{index}")
        elif context:
            provider_message_count = int(context.get("provider_message_count") or 0)
            changed_indices = {
                int(item)
                for item in context.get("provider_changed_message_indices") or ()
                if isinstance(item, int) and not isinstance(item, bool) and item >= 0
            }
            in_range = bool(provider_message_count) and all(
                item < provider_message_count for item in message_indices
            )
            gt_changed = bool(changed_indices) and all(
                item in changed_indices for item in message_indices
            )
            if not in_range:
                failures.append(f"{task}:delivery_message_index_out_of_range:{index}")
            if not gt_changed:
                failures.append(f"{task}:delivery_message_index_not_gt_changed:{index}")
            message_index_valid = in_range and gt_changed
        timing_valid = (
            isinstance(eligible, int)
            and isinstance(delivered, int)
            and eligible == delivered
            and row["delivered_before_model_query"]
            and not row["one_step_late"]
            and not row["predictive"]
        )
        semantic_support_valid = True
        if row["surface"] == "preemptive_retrieval":
            selected_evidence = row["selected_evidence"]
            semantic_support_valid = bool(selected_evidence) and all(
                str(item.get("path") or "").strip()
                and str(item.get("support_kind") or "")
                in {"certified_relation", "validation_candidate"}
                and bool(item.get("supporting_channels"))
                and str(item.get("origin") or "") == "preexisting_repository"
                and str(item.get("authority") or "")
                in {"certified_relation", "ranking_support"}
                and item.get("novel_to_provider_view") is True
                and item.get("known_to_model") is False
                and bool(str(item.get("materiality_reason") or ""))
                and bool(str(item.get("source_revision") or ""))
                for item in selected_evidence
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:preemptive_delivery_semantic_support_missing:{index}"
                )
        elif row["surface"] == "persistent_execution_state":
            claim_metadata = row["claim_metadata"]
            metadata_by_claim = {
                str(item.get("claim_id") or ""): item for item in claim_metadata
            }
            allowed_materiality = set(PROVIDER_MATERIALITY_REASONS)
            non_material_relations = set(NON_MATERIAL_PROVIDER_RELATIONS)
            material_relations = set(PROVIDER_MATERIAL_RELATIONS)
            relation_required_reasons = {
                "newly_certified_related_file",
                "related_advisory_obligation",
            }
            semantic_support_valid = bool(row["claim_ids"]) and all(
                claim_id in metadata_by_claim
                and str(metadata_by_claim[claim_id].get("origin") or "")
                in {
                    "preexisting_repository",
                    "task_deliverable",
                    "external_runtime",
                }
                and str(metadata_by_claim[claim_id].get("authority") or "")
                in {
                    "identity_only",
                    "certified_relation",
                    "execution_observation",
                }
                and metadata_by_claim[claim_id].get("novel_to_provider_view") is True
                and metadata_by_claim[claim_id].get("known_to_model") is False
                and str(metadata_by_claim[claim_id].get("materiality_reason") or "")
                in allowed_materiality
                and str(metadata_by_claim[claim_id].get("relation") or "").strip().lower()
                not in non_material_relations
                and (
                    str(metadata_by_claim[claim_id].get("materiality_reason") or "")
                    not in relation_required_reasons
                    or str(metadata_by_claim[claim_id].get("relation") or "")
                    .strip()
                    .lower()
                    in material_relations
                )
                and bool(str(metadata_by_claim[claim_id].get("source_revision") or ""))
                and (
                    str(metadata_by_claim[claim_id].get("materiality_reason") or "")
                    != "declared_validation_status_change"
                    or bool(
                        str(
                            metadata_by_claim[claim_id].get("declared_validation_id")
                            or ""
                        )
                    )
                )
                for claim_id in row["claim_ids"]
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:persistent_delivery_semantic_authority_invalid:{index}"
                )
        row["timing_valid"] = timing_valid
        row["dispatch_valid"] = dispatch_valid
        row["semantic_support_valid"] = semantic_support_valid
        row["message_index_valid"] = message_index_valid
        row["hash_valid"] = bool(
            context
            and request_hash
            and row["provider_messages_sha256"]
            and request_hash == str(context.get("request_payload_sha256") or "")
            and row["provider_messages_sha256"]
            == str(context.get("provider_messages_sha256") or "")
        )
        row["duplicate"] = duplicate
        row["deterministic_status"] = (
            "VALID"
            if timing_valid
            and dispatch_valid
            and row["hash_valid"]
            and message_index_valid
            and row["claim_count"] > 0
            and semantic_support_valid
            else "INVALID"
        )
        if not timing_valid:
            failures.append(
                f"{task}:delivery_timing_invalid:{index}:{row['surface']}"
            )
        if row["claim_count"] == 0:
            failures.append(f"{task}:delivery_without_claims:{index}:{row['surface']}")

    totals: dict[str, Any] = {
        "delivery_count": len(rows),
        "visible_chars": sum(row["chars"] for row in rows),
        "claim_count": sum(row["claim_count"] for row in rows),
        "timely_count": sum(bool(row.get("timing_valid")) for row in rows),
        "late_count": sum(bool(row.get("one_step_late")) for row in rows),
        "predictive_count": sum(bool(row.get("predictive")) for row in rows),
        "duplicate_count": sum(bool(row.get("duplicate")) for row in rows),
        "surfaces": {},
    }
    for surface in sorted({row["surface"] for row in rows}):
        selected = [row for row in rows if row["surface"] == surface]
        totals["surfaces"][surface] = {
            "delivery_count": len(selected),
            "visible_chars": sum(row["chars"] for row in selected),
            "claim_count": sum(row["claim_count"] for row in selected),
            "timely_count": sum(bool(row.get("timing_valid")) for row in selected),
            "late_count": sum(bool(row.get("one_step_late")) for row in selected),
            "predictive_count": sum(bool(row.get("predictive")) for row in selected),
        }
    return rows, failures, totals


__all__ = ["SURFACE_PATHS", "audit_provider_deliveries", "collect_provider_deliveries"]
