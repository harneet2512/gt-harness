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
    ("task_semantic_substrate", ("task_semantic_substrate", "deliveries")),
    ("preemptive_retrieval", ("preemptive_retrieval", "deliveries")),
    ("semantic_evidence", ("semantic_evidence", "deliveries")),
    ("repository_context", ("repository_context", "deliveries")),
    ("relational_context", ("relational_context", "deliveries")),
    ("persistent_execution_state", ("persistent_execution_state", "deliveries")),
    ("guidance", ("guidance_deliveries",)),
    ("repository_frontier", ("repository_intelligence", "frontier_deliveries")),
    ("progress", ("progress", "fact_deliveries")),
    ("completion", ("completion", "deliveries")),
    ("completion_visible", ("completion", "visible_deliveries")),
    ("observed_execution", ("observed_facts", "fact_deliveries")),
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


def _positive_int(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if parsed > 0 else 0


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _claims(row: dict[str, Any]) -> tuple[str, ...]:
    """Return all row identities for reporting and historical totals."""
    values: list[str] = []
    for key in ("claim_ids", "evidence_ids", "effect_ids", "fact_ids"):
        value = row.get(key) or ()
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value if str(item).strip())
    if not values:
        values.extend(_legacy_anchor_claims(row))
    return tuple(dict.fromkeys(values))


def _legacy_anchor_claims(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    # Historical guidance/frontier rows predate claim_ids but contain
    # concrete facts. Give those anchors a stable accounting identity so old
    # receipts remain auditable without inventing model usage.
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
    return values


def _provider_claims(row: dict[str, Any]) -> tuple[str, ...]:
    """Return identities that participate in duplicate provider detection.

    Underlying fact/effect IDs are included only for legacy rows without an
    explicit claim ID. A new claim can legitimately describe a changed value
    for the same stable fact ID.
    """
    values: list[str] = []
    for key in ("claim_ids", "evidence_ids", "effect_ids", "fact_ids"):
        value = row.get(key) or ()
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value if str(item).strip())
        if values:
            break
    if not values:
        values.extend(_legacy_anchor_claims(row))
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
                    # A receipt may carry lower-level fact/effect IDs in
                    # addition to its explicit provider claim IDs.  Those
                    # identities remain useful for aggregate accounting, but
                    # only the first explicit identity family is the delivered
                    # semantic claim surface and therefore needs a matching
                    # provider-value/support certificate.
                    "provider_claim_ids": list(_provider_claims(raw)),
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
    contribution_runtime = receipt.get("contribution_compiler") or {}
    value_contract_active = (
        contribution_runtime.get("provider_value_contract") == "gt.provider_value.v1"
    )
    value_certificates: dict[tuple[int, str], list[dict[str, Any]]] = {}
    if value_contract_active:
        for compilation in contribution_runtime.get("calls") or ():
            if not isinstance(compilation, dict):
                continue
            call = _positive_int(compilation.get("call"))
            for certificate in compilation.get("value_certificates") or ():
                if not isinstance(certificate, dict):
                    continue
                claim_id = str(certificate.get("claim_id") or "")
                if call and claim_id:
                    value_certificates.setdefault((call, claim_id), []).append(certificate)
    for index, row in enumerate(rows, start=1):
        delivery_claim_ids = tuple(row["provider_claim_ids"])
        identity = row["identity"]
        duplicate_identity = identity in identities
        claim_overlap = seen_claims.intersection(_provider_claims(row["raw"]))
        permitted_state_refresh = False
        if duplicate_identity:
            failures.append(f"{task}:duplicate_provider_delivery:{identity}")
        if claim_overlap and not permitted_state_refresh:
            failures.append(
                f"{task}:duplicate_provider_claim:{','.join(sorted(claim_overlap))}"
            )
        identities.add(identity)
        seen_claims.update(_provider_claims(row["raw"]))
        duplicate = duplicate_identity or bool(claim_overlap and not permitted_state_refresh)
        row["persistent_state_refresh"] = permitted_state_refresh
        delivered = row["delivered_before_call"]
        eligible = row["first_eligible_call"]
        context = contexts.get(delivered) if isinstance(delivered, int) else None
        row_value_certificates: list[dict[str, Any]] = []
        if value_contract_active and isinstance(delivered, int):
            for claim_id in _provider_claims(row["raw"]):
                matches = value_certificates.get((delivered, claim_id), [])
                if len(matches) != 1:
                    failures.append(
                        f"{task}:provider_value_certificate_count:{index}:{claim_id}:{len(matches)}"
                    )
                    continue
                certificate = matches[0]
                row_value_certificates.append(certificate)
                allowed = bool(
                    certificate.get("value_class")
                    in {
                        "action_local_relation",
                        "execution_contradiction",
                        "certified_predecision_gap",
                    }
                    and certificate.get("disposition")
                    in {"same_observation", "predecision"}
                    and certificate.get("completeness") == "exact"
                    and str(certificate.get("authority") or "")
                    and str(certificate.get("source_revision") or "")
                    and bool(certificate.get("anchors"))
                    and str(certificate.get("novelty_basis") or "")
                    and str(certificate.get("decision_point") or "")
                    and str(certificate.get("replaces_operation") or "")
                    and str(certificate.get("materiality_reason") or "")
                )
                if not allowed:
                    failures.append(
                        f"{task}:provider_value_certificate_rejected:{index}:{claim_id}"
                    )
        row["value_certificates"] = row_value_certificates
        if value_contract_active:
            relationship = str(
                row["raw"].get("exploration_relationship") or "unreported"
            )
            replacement_status = (
                "replacement_opportunity"
                if relationship == "context_used_without_prior_exploration"
                else "exploration_added_or_not_replaced"
                if relationship
                in {
                    "context_accompanied_exploration",
                    "context_followed_exploration",
                    "context_unmatched_after_exploration",
                }
                else "not_observed"
            )
            row["raw"]["provider_value_certificates"] = [
                dict(certificate) for certificate in row_value_certificates
            ]
            row["raw"]["exploration_replacement_receipt"] = {
                "schema": "gt.exploration_replacement.v1",
                "expected_replaced_operations": list(
                    dict.fromkeys(
                        str(certificate.get("replaces_operation") or "")
                        for certificate in row_value_certificates
                        if str(certificate.get("replaces_operation") or "")
                    )
                ),
                "first_followup_operation": str(
                    row["raw"].get("first_followup_operation") or ""
                ),
                "semantic_utilization": str(
                    row["raw"].get("semantic_utilization") or "unreported"
                ),
                "exploration_actions_before_use": int(
                    row["raw"].get("exploration_actions_before_use") or 0
                ),
                "exploration_relationship": relationship,
                "replacement_status": replacement_status,
                "reasoning_uptake": "not_captured_by_delivery_receipt",
                "causal_claim_allowed": False,
                "causal_claim_requires_matched_trajectory_or_ablation": True,
            }
        completed_actions = (
            context.get("completed_action_count_before_call")
            if isinstance(context, dict)
            else None
        )
        if isinstance(completed_actions, int) and isinstance(
            row.get("evidence_action"), int
        ):
            # Calls and actions are different ordinal domains.  The provider
            # call receipt records the exact number of completed actions at
            # dispatch, so use that instead of trusting producer-local flags.
            row["predictive"] = row["evidence_action"] > completed_actions
        if context is None:
            failures.append(f"{task}:delivery_call_context_missing:{index}")
        dispatch_status = str((context or {}).get("dispatch_status") or "")
        transport_started = dispatch_status in {
            "invoked",
            "response_received",
            "response_error",
        }
        exposure_confirmed = dispatch_status == "response_received"
        dispatch_valid = bool(context and exposure_confirmed)
        if context is not None and not transport_started:
            failures.append(f"{task}:delivery_request_not_dispatched:{index}")
        elif context is not None and not exposure_confirmed:
            failures.append(f"{task}:delivery_provider_response_missing:{index}")
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
        unsafe_origins = {
            str(item.get("origin") or "")
            for item in (*row["selected_evidence"], *row["claim_metadata"])
            if str(item.get("origin") or "")
            in {"model_authored", "generated_artifact", "unknown"}
        }
        if unsafe_origins:
            semantic_support_valid = False
            failures.append(
                f"{task}:delivery_unsafe_provider_origin:{index}:"
                + ",".join(sorted(unsafe_origins))
            )
        if row["surface"] == "task_semantic_substrate":
            metadata_by_claim = {
                str(item.get("claim_id") or ""): item
                for item in row["claim_metadata"]
                if str(item.get("claim_id") or "")
            }
            allowed_kinds = {
                "binary_format",
                "secret_location",
                "required_check",
                "project_check",
            }
            semantic_support_valid = bool(delivery_claim_ids) and all(
                claim_id in metadata_by_claim
                and str(metadata_by_claim[claim_id].get("kind") or "")
                in allowed_kinds
                and str(metadata_by_claim[claim_id].get("origin") or "")
                in {"preexisting_repository", "external_runtime"}
                and str(metadata_by_claim[claim_id].get("authority") or "")
                == "deterministic_task_semantics"
                and str(metadata_by_claim[claim_id].get("materiality_reason") or "")
                in {"task_decisive_evidence", "new_unresolved_task_obligation"}
                and str(metadata_by_claim[claim_id].get("source_revision") or "")
                and bool(metadata_by_claim[claim_id].get("gap_text"))
                and bool(
                    metadata_by_claim[claim_id].get("path")
                    or metadata_by_claim[claim_id].get("provider_value_anchors")
                )
                for claim_id in delivery_claim_ids
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:task_semantic_delivery_support_missing:{index}"
                )
        elif row["surface"] == "preemptive_retrieval":
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
        elif row["surface"] == "relational_context":
            raw = row["raw"]
            processes = tuple(
                item for item in raw.get("processes") or () if isinstance(item, dict)
            )
            process_id_rows = tuple(
                str(item.get("process_id") or "") for item in processes
            )
            claim_ids = delivery_claim_ids
            semantic_support_valid = bool(
                processes
                and claim_ids
                and all(process_id_rows)
                and len(process_id_rows) == len(set(process_id_rows))
                and len(claim_ids) == len(set(claim_ids))
                and set(process_id_rows) == set(claim_ids)
            )
            semantic_support_valid = bool(
                semantic_support_valid
                and str(raw.get("epistemic_status") or "") == "lower_bound"
                and str(raw.get("source_revision") or "")
                and str(raw.get("graph_revision") or "")
                and all(str(item.get("rendered") or "").strip() for item in processes)
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:relational_delivery_semantic_support_missing:{index}"
                )
        elif row["surface"] == "semantic_evidence":
            raw = row["raw"]
            items = tuple(item for item in raw.get("items") or () if isinstance(item, dict))
            item_claim_ids = tuple(str(item.get("claim_id") or "") for item in items)
            semantic_support_valid = bool(
                items
                and delivery_claim_ids
                and all(item_claim_ids)
                and len(item_claim_ids) == len(set(item_claim_ids))
                and set(item_claim_ids) == set(delivery_claim_ids)
                and str(raw.get("source_revision") or "")
                and str(raw.get("graph_revision") or "")
                and all(
                    str(item.get("path") or "").strip()
                    and _positive_int(item.get("line")) > 0
                    and str(item.get("kind") or "")
                    in {"definition", "property", "caller", "reference", "test"}
                    and str(item.get("source_revision") or "")
                    == str(raw.get("source_revision") or "")
                    for item in items
                )
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:semantic_evidence_delivery_support_missing:{index}"
                )
        elif row["surface"] == "repository_context":
            raw = row["raw"]
            projection = raw.get("projection") or {}
            metadata_by_claim = {
                str(item.get("claim_id") or ""): item
                for item in row["claim_metadata"]
                if str(item.get("claim_id") or "")
            }
            semantic = projection.get("semantic_evidence") or {}
            semantic_ids = {
                str(item.get("claim_id") or "")
                for item in semantic.get("items") or ()
                if isinstance(item, dict)
            }
            semantic_graph = projection.get("semantic_graph") or {}
            semantic_graph_facts = tuple(
                item
                for item in semantic_graph.get("facts") or ()
                if isinstance(item, dict)
            )
            semantic_graph_ids = {
                str(item.get("claim_id") or "") for item in semantic_graph_facts
            }
            semantic_graph_receipt = semantic_graph.get("receipt") or {}
            delivered_semantic_graph_ids = semantic_graph_ids & set(delivery_claim_ids)
            semantic_graph_support_valid = bool(
                not delivered_semantic_graph_ids
                or (
                    isinstance(semantic_graph_receipt, dict)
                    and str(semantic_graph_receipt.get("builder_version") or "")
                    == "python-semantic-graph-v1"
                    and str(semantic_graph_receipt.get("source_revision") or "")
                    == str(raw.get("source_revision") or "")
                    and int(semantic_graph_receipt.get("documents_attempted") or 0) > 0
                    and int(semantic_graph_receipt.get("documents_indexed") or 0) > 0
                    and all(
                        str(item.get("claim_id") or "")
                        and str(item.get("path") or "")
                        and _positive_int(item.get("start_line")) > 0
                        and str(item.get("kind") or "")
                        in {
                            "value_flow",
                            "return_flow",
                            "call_argument_flow",
                            "shape_constraint",
                            "control_dependency",
                        }
                        and str(item.get("source_revision") or "")
                        == str(raw.get("source_revision") or "")
                        and bool(item.get("evidence"))
                        and bool(item.get("provenance"))
                        and str(
                            metadata_by_claim[
                                str(item.get("claim_id") or "")
                            ].get("authority")
                            or ""
                        )
                        == "deterministic_derived"
                        for item in semantic_graph_facts
                        if str(item.get("claim_id") or "")
                        in delivered_semantic_graph_ids
                    )
                )
            )
            if not semantic_graph_support_valid:
                failures.append(
                    f"{task}:repository_context_semantic_graph_support_invalid:{index}"
                )
            execution_ids = {
                str(item.get("view_id") or "")
                for item in projection.get("execution_views") or ()
                if isinstance(item, dict)
            }
            execution_views = tuple(
                item
                for item in projection.get("execution_views") or ()
                if isinstance(item, dict)
            )
            process_coverage = projection.get("process_coverage") or {}
            if execution_views:
                coverage_valid = bool(
                    isinstance(process_coverage, dict)
                    and str(process_coverage.get("profile_id") or "")
                    == "gt.certified_process.v1"
                    and int(process_coverage.get("max_depth") or 0) > 0
                    and int(process_coverage.get("max_branching") or 0) > 0
                    and int(process_coverage.get("max_execution_views") or 0) > 0
                    and int(process_coverage.get("returned_views") or 0)
                    == len(execution_views)
                    and int(process_coverage.get("candidate_views") or 0)
                    >= len(execution_views)
                    and int(process_coverage.get("lower_bound") or 0) == 1
                    and all(
                        int(process_coverage.get(key) or 0) >= 0
                        for key in (
                            "entries_considered",
                            "paths_considered",
                            "branch_truncated",
                            "depth_truncated",
                            "cycle_terminated",
                            "deduplicated_paths",
                            "omitted_for_view_limit",
                            "rejected_edges",
                        )
                    )
                )
                if not coverage_valid:
                    failures.append(
                        f"{task}:repository_context_process_coverage_invalid:{index}"
                    )
            impact_ids = {
                str(item.get("claim_id") or "")
                for item in projection.get("impact_facts") or ()
                if isinstance(item, dict)
            }
            diagnostic_ids = {
                str(item.get("claim_id") or "")
                for item in projection.get("diagnostic_facts") or ()
                if isinstance(item, dict)
            }
            validation_ids = {
                str(item.get("claim_id") or "")
                for item in projection.get("validation_facts") or ()
                if isinstance(item, dict)
            }
            impact_by_id = {
                str(item.get("claim_id") or ""): item
                for item in projection.get("impact_facts") or ()
                if isinstance(item, dict) and str(item.get("claim_id") or "")
            }
            validation_by_id = {
                str(item.get("claim_id") or ""): item
                for item in projection.get("validation_facts") or ()
                if isinstance(item, dict) and str(item.get("claim_id") or "")
            }
            coupled_items = tuple(
                item
                for item in projection.get("coupled_obligations") or ()
                if isinstance(item, dict)
            )
            coupled_obligation_ids = {
                str(item.get("claim_id") or "")
                for item in coupled_items
            }
            convention_items = tuple(
                item
                for item in projection.get("resolved_conventions") or ()
                if isinstance(item, dict)
            )
            convention_ids = {
                str(item.get("claim_id") or "") for item in convention_items
            }
            supported_ids = (
                semantic_ids
                | semantic_graph_ids
                | execution_ids
                | impact_ids
                | diagnostic_ids
                | validation_ids
                | coupled_obligation_ids
                | convention_ids
            ) - {""}
            coupled_support_failures: list[str] = []
            for item in coupled_items:
                claim_id = str(item.get("claim_id") or "")
                if claim_id not in delivery_claim_ids:
                    continue
                changed = item.get("changed") or {}
                dependent_paths = tuple(
                    str(path)
                    for path in item.get("dependent_paths") or ()
                    if str(path)
                )
                test_paths = tuple(
                    str(path)
                    for path in item.get("test_paths") or ()
                    if str(path)
                )
                declared_check = str(item.get("declared_check") or "")
                constituents = {
                    str(value)
                    for value in item.get("constituent_claim_ids") or ()
                    if str(value)
                }
                metadata = metadata_by_claim.get(claim_id) or {}
                metadata_constituents = {
                    str(value)
                    for value in metadata.get("constituent_claim_ids") or ()
                    if str(value)
                }
                dependency_facts = tuple(
                    impact_by_id[value]
                    for value in constituents
                    if value in impact_by_id
                    and str(impact_by_id[value].get("kind") or "")
                    in {"caller", "api_consumer", "re_export"}
                )
                test_facts = tuple(
                    impact_by_id[value]
                    for value in constituents
                    if value in impact_by_id
                    and str(impact_by_id[value].get("kind") or "") == "test"
                )
                validation_facts = tuple(
                    validation_by_id[value]
                    for value in constituents
                    if value in validation_by_id
                )
                dependency_ids = {
                    str(fact.get("claim_id") or "") for fact in dependency_facts
                }
                test_ids = {str(fact.get("claim_id") or "") for fact in test_facts}
                validation_ids = {
                    str(fact.get("claim_id") or "") for fact in validation_facts
                }

                def endpoint_matches(
                    value: object,
                    expected_path: str = str(changed.get("path") or ""),
                    expected_symbol: str = str(changed.get("symbol") or ""),
                ) -> bool:
                    endpoint = value if isinstance(value, dict) else {}
                    return bool(
                        str(endpoint.get("path") or "") == expected_path
                        and str(endpoint.get("symbol") or "")
                        == expected_symbol
                    )

                support_valid = bool(
                    claim_id
                    and isinstance(changed, dict)
                    and str(changed.get("path") or "")
                    and str(changed.get("symbol") or "")
                    and _positive_int(changed.get("line")) > 0
                    and dependent_paths
                    and test_paths
                    and declared_check
                    and constituents
                    and constituents == metadata_constituents
                    and dependency_ids
                    and test_ids
                    and validation_ids
                    and constituents == dependency_ids | test_ids | validation_ids
                    and len(dependent_paths) == len(set(dependent_paths))
                    and len(test_paths) == len(set(test_paths))
                    and item.get("blocking") is False
                    and metadata.get("blocking") is False
                    and str(metadata.get("authority") or "")
                    == "certified_composition"
                    and all(
                        endpoint_matches(fact.get("target"))
                        and str((fact.get("source") or {}).get("path") or "")
                        in dependent_paths
                        and bool(fact.get("provenance"))
                        and str(fact.get("authority") or "")
                        == "certified_structural"
                        for fact in dependency_facts
                    )
                    and {
                        str((fact.get("source") or {}).get("path") or "")
                        for fact in dependency_facts
                    }
                    == set(dependent_paths)
                    and all(
                        endpoint_matches(fact.get("source"))
                        and str((fact.get("target") or {}).get("path") or "")
                        in test_paths
                        and str(fact.get("relation") or "").upper()
                        in {"ASSERTED_BY", "TESTED_BY"}
                        and bool(fact.get("provenance"))
                        and str(fact.get("authority") or "")
                        == "certified_structural"
                        for fact in test_facts
                    )
                    and {
                        str((fact.get("target") or {}).get("path") or "")
                        for fact in test_facts
                    }
                    == set(test_paths)
                    and all(
                        str(fact.get("command") or "") == declared_check
                        and str(fact.get("impacted_path") or "") in test_paths
                        and str(fact.get("authority") or "")
                        == "declared_validation"
                        for fact in validation_facts
                    )
                    and {
                        str(fact.get("impacted_path") or "")
                        for fact in validation_facts
                    }
                    <= set(test_paths)
                )
                if not support_valid:
                    coupled_support_failures.append(claim_id or "missing_claim_id")
            if coupled_support_failures:
                failures.extend(
                    f"{task}:repository_context_coupled_support_invalid:{index}:{claim_id}"
                    for claim_id in coupled_support_failures
                )
            convention_support_failures: list[str] = []
            for item in convention_items:
                claim_id = str(item.get("claim_id") or "")
                if claim_id not in delivery_claim_ids:
                    continue
                subject = item.get("subject") or {}
                callers = tuple(
                    str(value) for value in item.get("callers") or () if str(value)
                )
                tests = tuple(
                    str(value) for value in item.get("tests") or () if str(value)
                )
                constituents = tuple(
                    str(value)
                    for value in item.get("constituent_claim_ids") or ()
                    if str(value)
                )
                metadata = metadata_by_claim.get(claim_id) or {}
                metadata_constituents = tuple(
                    str(value)
                    for value in metadata.get("constituent_claim_ids") or ()
                    if str(value)
                )
                anchors = {
                    str(value)
                    for value in metadata.get("provider_value_anchors") or ()
                    if str(value)
                }
                subject_rendered = (
                    f"{subject.get('path')}#{subject.get('symbol')}"
                    if subject.get("path") and subject.get("symbol")
                    else ""
                )
                support_valid = bool(
                    claim_id
                    and subject_rendered
                    and _positive_int(subject.get("line")) > 0
                    and str(item.get("signature") or "")
                    and str(item.get("resolved_type") or "")
                    and callers
                    and tests
                    and len(constituents) >= 3
                    and len(constituents) == len(set(constituents))
                    and constituents == metadata_constituents
                    and str(metadata.get("authority") or "")
                    == "certified_composition"
                    and str(metadata.get("signature") or "")
                    == str(item.get("signature") or "")
                    and str(metadata.get("resolved_type") or "")
                    == str(item.get("resolved_type") or "")
                    and {subject_rendered, *callers, *tests} <= anchors
                )
                if not support_valid:
                    convention_support_failures.append(claim_id or "missing_claim_id")
            if convention_support_failures:
                failures.extend(
                    f"{task}:repository_context_convention_support_invalid:{index}:{claim_id}"
                    for claim_id in convention_support_failures
                )
            semantic_support_valid = bool(
                delivery_claim_ids
                and set(delivery_claim_ids) <= supported_ids
                and str(raw.get("source_revision") or "")
                and str(raw.get("graph_revision") or "")
                and str(projection.get("source_revision") or "")
                == str(raw.get("source_revision") or "")
                and str(projection.get("graph_revision") or "")
                == str(raw.get("graph_revision") or "")
                and set(delivery_claim_ids) <= set(metadata_by_claim)
                and all(
                    str(metadata_by_claim[claim_id].get("origin") or "")
                    in {"preexisting_repository", "execution_observation"}
                    and str(metadata_by_claim[claim_id].get("authority") or "")
                    and str(
                        metadata_by_claim[claim_id].get("materiality_reason") or ""
                    )
                    and str(metadata_by_claim[claim_id].get("source_revision") or "")
                    for claim_id in delivery_claim_ids
                )
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:repository_context_delivery_support_missing:{index}"
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
            semantic_support_valid = bool(delivery_claim_ids) and all(
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
                    "deterministic_derived",
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
                for claim_id in delivery_claim_ids
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:persistent_delivery_semantic_authority_invalid:{index}"
                )
        elif row["surface"] == "guidance":
            raw = row["raw"]
            feature_id = str(raw.get("feature_id") or "")
            anchors = tuple(
                str(value) for value in raw.get("claim_anchors") or () if str(value)
            )
            evidence_action = _positive_int(raw.get("evidence_action"))
            certified_relation = bool(raw.get("certified_nonlocal_relation")) and bool(
                raw.get("relation") or raw.get("relation_endpoint")
            )
            certified_gap = bool(raw.get("certified_predecision_gap"))
            expected_value = (
                "execution_contradiction"
                if feature_id
                in {"syntax_result", "covering_red", "recovery", "submit_refusal"}
                and evidence_action
                and anchors
                else "action_local_relation"
                if feature_id in {"signature_delta", "newfile_precedent"}
                and evidence_action
                and anchors
                and certified_relation
                else "certified_predecision_gap"
                if feature_id == "GT_EDIT_CHECK" and anchors and certified_gap
                else ""
            )
            semantic_support_valid = bool(
                delivery_claim_ids
                and expected_value
            )
            if not semantic_support_valid:
                failures.append(f"{task}:guidance_delivery_support_missing:{index}")
        elif row["surface"] == "repository_frontier":
            raw = row["raw"]
            facts = tuple(
                item for item in raw.get("facts") or () if isinstance(item, dict)
            )
            facts_by_claim = {
                str(item.get("claim_id") or ""): item
                for item in facts
                if str(item.get("claim_id") or "")
            }
            semantic_support_valid = bool(
                delivery_claim_ids
                and set(delivery_claim_ids) == set(facts_by_claim)
                and str(raw.get("source_revision") or "")
                and str(raw.get("graph_revision") or "")
                and all(
                    str(facts_by_claim[claim_id].get("path") or "")
                    and str(facts_by_claim[claim_id].get("source_revision") or "")
                    == str(raw.get("source_revision") or "")
                    and str(facts_by_claim[claim_id].get("graph_revision") or "")
                    == str(raw.get("graph_revision") or "")
                    and isinstance(facts_by_claim[claim_id].get("provenance"), dict)
                    and str(
                        (facts_by_claim[claim_id].get("provenance") or {}).get("origin")
                        or ""
                    )
                    in {"task_start", "observed_external"}
                    for claim_id in delivery_claim_ids
                )
            )
            if not semantic_support_valid:
                failures.append(
                    f"{task}:repository_frontier_delivery_support_missing:{index}"
                )
        row["timing_valid"] = timing_valid
        row["transport_started"] = transport_started
        row["provider_exposure_confirmed"] = exposure_confirmed
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

    exposed_rows = [row for row in rows if row.get("provider_exposure_confirmed")]
    totals: dict[str, Any] = {
        "attempted_delivery_count": len(rows),
        "delivery_count": len(exposed_rows),
        "visible_chars": sum(row["chars"] for row in exposed_rows),
        "claim_count": sum(row["claim_count"] for row in exposed_rows),
        "timely_count": sum(bool(row.get("timing_valid")) for row in rows),
        "late_count": sum(bool(row.get("one_step_late")) for row in rows),
        "predictive_count": sum(bool(row.get("predictive")) for row in rows),
        "duplicate_count": sum(bool(row.get("duplicate")) for row in rows),
        "surfaces": {},
    }
    for surface in sorted({row["surface"] for row in rows}):
        selected = [row for row in rows if row["surface"] == surface]
        exposed = [row for row in selected if row.get("provider_exposure_confirmed")]
        totals["surfaces"][surface] = {
            "attempted_delivery_count": len(selected),
            "delivery_count": len(exposed),
            "visible_chars": sum(row["chars"] for row in exposed),
            "claim_count": sum(row["claim_count"] for row in exposed),
            "timely_count": sum(bool(row.get("timing_valid")) for row in selected),
            "late_count": sum(bool(row.get("one_step_late")) for row in selected),
            "predictive_count": sum(bool(row.get("predictive")) for row in selected),
        }
    return rows, failures, totals


__all__ = ["SURFACE_PATHS", "audit_provider_deliveries", "collect_provider_deliveries"]
