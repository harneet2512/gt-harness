"""Deterministic, content-safe attribution records for the GT engine.

The delivery ledger proves that bytes were sealed. This trace answers the
different questions needed for mechanism attribution: what boundary GT saw,
why a decision stayed quiet, and which delivered evidence was present in the
request that produced a model response.

Raw prompts, tool output, model text, and provider payloads are never persisted
here. Content-bearing events store only a byte hash and character count.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

DIRECT_FEATURES: dict[str, dict[str, Any]] = {
    "caller_contract": {
        "kind": "FACT", "boundaries": ("file_view", "edit_result"),
        "producer": "contract_map",
        "trigger": "a viewed or signature-edited callable has verified callers",
        "intended_action": "update or inspect proven callers",
    },
    "cochange_prior": {
        "kind": "FACT", "boundaries": ("file_view", "edit_result"),
        "producer": "curation",
        "trigger": (
            "the indexed revision contains a verified co-change companion "
            "for the viewed or edited file"
        ),
        "intended_action": "inspect or update the proven companion file",
    },
    "covering_red": {
        "kind": "FACT", "boundaries": ("edit_result", "submit"),
        "producer": "covering_runner",
        "trigger": "an executed covering test fails because of an edited source file",
        "intended_action": "repair an attributable covering-test regression",
    },
    "def_partition": {
        "kind": "FACT", "boundaries": ("search_result",),
        "producer": "post_search",
        "trigger": "a symbol search contains definitions and references that can be partitioned",
        "intended_action": "distinguish definitions from references",
    },
    "localization": {
        "kind": "FACT", "boundaries": ("task_start", "search_result"),
        "producer": "v1r_brief",
        "trigger": "the indexed task or search has ranked relevant source locations",
        "intended_action": "inspect ranked relevant source locations",
    },
    "newfile_precedent": {
        "kind": "FACT", "boundaries": ("search_result", "edit_result"),
        "producer": "change_surface",
        "trigger": (
            "repeated failed search or a new file exposes a verified "
            "sibling/registry precedent"
        ),
        "intended_action": "follow a verified repository precedent for a new file",
    },
    "obligations": {
        "kind": "FACT", "boundaries": ("task_start",),
        "producer": "spec",
        "trigger": "issue text yields non-empty, evidence-backed implementation obligations",
        "intended_action": "satisfy issue-derived requirements",
    },
    "recovery": {
        "kind": "FACT", "boundaries": ("test_result", "tool_result"),
        "producer": "governor",
        "trigger": (
            "the same test failure recurs after an intervening edit, a fresh "
            "attributable required RED persists near the iteration limit, or "
            "a repository action repeats without information gain"
        ),
        "intended_action": (
            "change hypothesis after falsification or repair the observed "
            "required RED before further exploration"
        ),
    },
    "signature_delta": {
        "kind": "FACT", "boundaries": ("edit_result",),
        "producer": "patch_delta",
        "trigger": "a before/after edit changes a callable signature with verified call sites",
        "intended_action": "repair call sites affected by a signature change",
    },
    "submit_refusal": {
        "kind": "FACT", "boundaries": ("submit",),
        "producer": "submit_gate",
        "trigger": "submission is attempted with unresolved positive failing evidence",
        "intended_action": "resolve positive failing evidence before submission",
    },
    "syntax_result": {
        "kind": "FACT", "boundaries": ("edit_result", "submit"),
        "producer": "edit_check",
        "trigger": "an executed syntax/compiler check fails on an edited source file",
        "intended_action": "repair an executed syntax failure",
    },
    "GT_CERT_DELIVERY": {
        "kind": "CAP", "boundaries": ("submit",),
        "trigger": "a completion certificate owns a submit-refusal delivery",
        "intended_action": "name the evidence state of the completion decision",
    },
    "GT_CHANGE_SURFACE": {
        "kind": "CAP", "boundaries": ("search_result",),
        "trigger": "the change-surface producer yields a new-file precedent",
        "intended_action": "identify the proven change surface",
    },
    "GT_EDIT_CHECK": {
        "kind": "CAP", "boundaries": ("edit_result", "submit"),
        "trigger": "the edit checker executes, or yields a syntax-result delivery on failure",
        "intended_action": "validate edited code with deterministic checks",
    },
    "GT_HYPOTHESIS": {
        "kind": "CAP", "boundaries": ("test_result", "tool_result"),
        "trigger": (
            "the recovery governor yields a proven falsification or bounded "
            "near-budget RED intervention"
        ),
        "intended_action": "track repeated failures across edits",
    },
    "GT_LOC_RESLOT": {
        "kind": "CAP", "boundaries": ("task_start", "search_result"),
        "trigger": (
            "ranked localization is placed into the task-start or next "
            "search-result model request"
        ),
        "intended_action": "reslot a ranked localization result into the request",
    },
    "GT_PATCH_DELTA": {
        "kind": "CAP", "boundaries": ("edit_result",),
        "trigger": "the patch-delta producer yields a signature delta",
        "intended_action": "derive evidence from the actual before/after patch",
    },
    "GT_SS_SUBMIT_RED": {
        "kind": "CAP", "boundaries": ("submit",),
        "trigger": "the submit gate yields refusal for an observed unresolved RED check",
        "intended_action": "refuse once after an observed unresolved test failure",
    },
    "select_catalog": {
        "kind": "CAP", "boundaries": ("task_start",),
        "producer": "persistent_execution_state",
        "trigger": "a versioned bootstrap catalog is constructed and offered to the model",
        "intended_action": "select and order existing catalog IDs for the next execution focus",
    },
}

CAPABILITY_OWNERS: dict[str, str] = {
    "GT_CHANGE_SURFACE": "newfile_precedent",
    "GT_PATCH_DELTA": "signature_delta",
    "GT_LOC_RESLOT": "localization",
    "GT_SS_SUBMIT_RED": "submit_refusal",
    "GT_EDIT_CHECK": "syntax_result",
    "GT_HYPOTHESIS": "recovery",
    "GT_CERT_DELIVERY": "submit_refusal",
}

_EVIDENCE_FEATURES = {
    "caller_contract": "caller_contract",
    "caller_contract_search": "caller_contract",
    "caller_contract_view": "caller_contract",
    "covering_red": "covering_red",
    "covering_verdict": "covering_red",
    "cochange_partner": "cochange_prior",
    "def_partition": "def_partition",
    "def_ref_partition": "def_partition",
    "name_fold": "def_partition",
    "wrong_surface": "def_partition",
    "body_concept": "def_partition",
    "localization": "localization",
    "brief_localization": "localization",
    "ranked_localization": "localization",
    "trace_frame": "localization",
    "new_file_destination": "newfile_precedent",
    "newfile_precedent": "newfile_precedent",
    "obligations": "obligations",
    "obligation_unexercised": "obligations",
    "recovery": "recovery",
    "coherence_collapse": "recovery",
    "caller_break": "caller_contract",
    "companion_surface": "signature_delta",
    "signature_mismatch": "signature_delta",
    "signature_delta": "signature_delta",
    "submit_refusal": "submit_refusal",
    "syntax_result": "syntax_result",
    "select_catalog": "select_catalog",
}


def feature_for_evidence(evidence_type: str | None) -> str | None:
    """Map a concrete envelope type to its 17-feature census identity."""
    value = str(evidence_type or "")
    if value.startswith(("missing_role:", "missing_role_postcreate:")):
        return "newfile_precedent"
    return _EVIDENCE_FEATURES.get(value)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


class AttributionTrace:
    """Append-only, hash-chained JSONL trace.

    Writes are correct-or-quiet: tracing can never break the engine path.
    """

    def __init__(
        self,
        path_provider: Callable[[], Path | str | None],
        *,
        trace_id: str | None = None,
    ) -> None:
        self._path_provider = path_provider
        self.trace_id = trace_id or uuid.uuid4().hex
        self.sequence = 0
        self.previous_hash = ""
        self.rows: list[dict[str, Any]] = []

    def record(
        self,
        event_type: str,
        *,
        action_index: int,
        boundary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        row: dict[str, Any] = {
            "version": "gt.attribution.v1",
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "event_type": str(event_type),
            "action_index": int(action_index),
            "boundary": str(boundary),
            "payload": dict(payload or {}),
            "previous_hash": self.previous_hash,
        }
        row["row_hash"] = hashlib.sha256(_canonical_bytes(row)).hexdigest()
        self.previous_hash = row["row_hash"]
        self.rows.append(row)
        try:
            path_value = self._path_provider()
            if path_value:
                path = Path(path_value)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(
                        row, sort_keys=True, ensure_ascii=False,
                    ) + "\n")
                    handle.flush()
        except Exception:  # noqa: BLE001 - telemetry must never break execution
            pass
        return row

    def record_content(
        self,
        event_type: str,
        *,
        content: str | None,
        action_index: int,
        boundary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = content or ""
        safe_payload = dict(payload or {})
        safe_payload["content_chars"] = len(text)
        safe_payload["content_sha256"] = hashlib.sha256(
            text.encode("utf-8", "surrogatepass"),
        ).hexdigest()
        return self.record(
            event_type,
            action_index=action_index,
            boundary=boundary,
            payload=safe_payload,
        )


def census_trace_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the rich summary in stable registry order for the submit row."""
    summary = summarize_features(rows)
    return [
        {"feature_id": feature_id, **summary[feature_id]}
        for feature_id in DIRECT_FEATURES
    ]


def verify_trace_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Return deterministic integrity errors for already parsed trace rows."""
    issues: list[str] = []
    previous_hash = ""
    trace_id: str | None = None
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"row {position}: not an object")
            continue
        if row.get("sequence") != position:
            issues.append(f"row {position}: sequence mismatch")
        if trace_id is None:
            trace_id = str(row.get("trace_id", ""))
        elif str(row.get("trace_id", "")) != trace_id:
            issues.append(f"row {position}: trace_id mismatch")
        if str(row.get("previous_hash", "")) != previous_hash:
            issues.append(f"row {position}: previous_hash mismatch")
        claimed_hash = str(row.get("row_hash", ""))
        unhashed = {key: value for key, value in row.items() if key != "row_hash"}
        expected_hash = hashlib.sha256(_canonical_bytes(unhashed)).hexdigest()
        if claimed_hash != expected_hash:
            issues.append(f"row {position}: row_hash mismatch")
        previous_hash = claimed_hash
    return issues


def verify_lifecycle_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Verify every sealed delivery reached one provider request and response.

    This checks provenance and timing only. It does not infer that the model's
    behavior was caused by the delivery.
    """
    materialized = [dict(row) for row in rows]
    deliveries: dict[str, dict[str, Any]] = {}
    provider_rows: list[dict[str, Any]] = []
    response_rows: list[dict[str, Any]] = []
    for position, row in enumerate(materialized, 1):
        row.setdefault("sequence", position)
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = str(row.get("event_type") or "")
        if (
            event_type == "decision.committed"
            and payload.get("decision") == "delivered"
        ):
            delivery_id = str(payload.get("delivery_id") or "")
            if delivery_id:
                deliveries[delivery_id] = row
        elif event_type == "provider.request":
            provider_rows.append(row)
        elif event_type == "model.response":
            response_rows.append(row)

    issues: list[str] = []
    for delivery_id, delivery in deliveries.items():
        delivery_payload = delivery.get("payload", {})
        delivery_sequence = int(delivery.get("sequence") or 0)
        providers = [
            row for row in provider_rows
            if int(row.get("sequence") or 0) > delivery_sequence
        ]
        provider = providers[0] if providers else None
        if provider is None:
            issues.append(
                f"delivery {delivery_id}: missing provider-final request receipt"
            )
        else:
            provider_payload = provider.get("payload", {})
            provider_delivery_ids = {
                str(value)
                for value in provider_payload.get("delivery_ids", ())
            }
            if delivery_id not in provider_delivery_ids:
                issues.append(
                    f"delivery {delivery_id}: missing from immediate "
                    "provider-final request"
                )
            matches = [
                item for item in provider_payload.get("matches", ())
                if isinstance(item, dict)
                and str(item.get("delivery_id") or "") == delivery_id
            ]
            sealed_hash = str(
                delivery_payload.get("rendered_bytes_hash") or ""
            )
            if not matches:
                issues.append(
                    f"delivery {delivery_id}: provider byte match missing"
                )
            elif sealed_hash and not any(
                str(item.get("rendered_sha256") or "") == sealed_hash
                for item in matches
            ):
                issues.append(
                    f"delivery {delivery_id}: provider receipt hash does not "
                    "match sealed bytes"
                )

        provider_sequence = (
            int(provider.get("sequence") or 0) if provider is not None
            else delivery_sequence
        )
        responses = [
            row for row in response_rows
            if int(row.get("sequence") or 0) > provider_sequence
        ]
        response = responses[0] if responses else None
        if response is None:
            issues.append(f"delivery {delivery_id}: missing linked model response")
        elif provider is not None:
            response_delivery_ids = {
                str(value)
                for value in response.get("payload", {}).get(
                    "delivery_ids", ()
                )
            }
            if delivery_id not in response_delivery_ids:
                issues.append(
                    f"delivery {delivery_id}: missing from immediate "
                    "model response"
                )
            provider_iteration = int(
                provider.get("payload", {}).get("iteration") or 0
            )
            response_iteration = int(
                response.get("payload", {}).get("iteration") or 0
            )
            if response_iteration != provider_iteration:
                issues.append(
                    f"delivery {delivery_id}: response iteration "
                    f"{response_iteration} != provider iteration "
                    f"{provider_iteration}"
                )
    return issues


def feature_provider_iterations(
    rows: Iterable[dict[str, Any]],
) -> dict[str, list[int]]:
    """Join independently attributed features to exact provider iterations."""
    materialized = [dict(row) for row in rows]
    delivery_features: dict[str, set[str]] = {}
    for row in materialized:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = str(row.get("event_type") or "")
        decision = str(payload.get("decision") or "")
        delivery_id = str(payload.get("delivery_id") or "")
        feature_id = str(payload.get("feature_id") or "")
        if not delivery_id or not feature_id:
            continue
        if event_type == "decision.committed" and decision == "delivered":
            delivery_features.setdefault(delivery_id, set()).add(feature_id)
        elif event_type in {"feature.applied", "capability.applied"} and (
            decision == "APPLIED"
        ):
            delivery_features.setdefault(delivery_id, set()).add(feature_id)

    timings: dict[str, set[int]] = {}
    for row in materialized:
        if row.get("event_type") != "provider.request":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        iteration = int(payload.get("iteration") or 0)
        if iteration <= 0:
            continue
        for delivery_id in payload.get("delivery_ids", ()):
            for feature_id in delivery_features.get(str(delivery_id), ()):
                timings.setdefault(feature_id, set()).add(iteration)
    return {
        feature_id: sorted(iterations)
        for feature_id, iterations in sorted(timings.items())
    }


def verify_sdlc_timing_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Verify edit lifecycle checkpoints bracket the actual tool dispatch."""
    materialized = [dict(row) for row in rows]
    for position, row in enumerate(materialized, 1):
        row.setdefault("sequence", position)
    checkpoints = [
        row
        for row in materialized
        if row.get("event_type") == "lifecycle.checkpoint"
        and isinstance(row.get("payload"), dict)
    ]
    issues: list[str] = []
    for observation in materialized:
        if observation.get("event_type") != "observation.received":
            continue
        payload = observation.get("payload")
        if (
            not isinstance(payload, dict)
            or not payload.get("changed_files")
        ):
            continue
        action_index = int(observation.get("action_index") or 0)
        sequence = int(observation.get("sequence") or 0)
        pre = [
            row
            for row in checkpoints
            if row.get("boundary") == "pre_edit"
            and int(
                row.get("payload", {}).get(
                    "proposed_action_index"
                ) or 0
            ) == action_index
        ]
        if not pre:
            issues.append(
                f"edit action {action_index}: missing pre_edit checkpoint"
            )
        elif not any(int(row.get("sequence") or 0) < sequence for row in pre):
            issues.append(
                f"edit action {action_index}: pre_edit occurs after dispatch"
            )
        post = [
            row
            for row in checkpoints
            if row.get("boundary") == "post_edit"
            and int(row.get("action_index") or 0) == action_index
            and int(row.get("sequence") or 0) > sequence
        ]
        if not post:
            issues.append(
                f"edit action {action_index}: missing post_edit checkpoint"
            )
    return issues


def summarize_features(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build a conservative 17-feature projection from attribution events.

    Exact-byte exposure and response linkage are observable. Semantic
    consumption and causal benefit are intentionally not inferred here.
    """
    materialized = [dict(row) for row in rows]
    summary: dict[str, dict[str, Any]] = {
        feature_id: {
            "kind": spec["kind"],
            "status": "INELIGIBLE",
            "reasons": ["no_trigger_observed"],
            "deliveries": [],
            "exposed": False,
            "response_observed": False,
            "exposure_source": "",
            "action_observed": False,
            "action_consistent": False,
        }
        for feature_id, spec in DIRECT_FEATURES.items()
    }

    priority = {
        "INELIGIBLE": 0,
        "TRIGGERED_DARK": 1,
        "SUPPRESSED_WITH_REASON": 2,
        "DELIVERED_UNEXPOSED": 3,
        "EXPOSED": 4,
        "WITNESSED": 5,
        # A witnessed delivery cannot excuse a broken causal trace elsewhere
        # in the same feature. Audit integrity outranks lifecycle success.
        "TELEMETRY_FAULT": 6,
    }

    def update(feature_id: str, status: str, reason: str = "") -> None:
        if feature_id not in summary:
            return
        previous_status = summary[feature_id]["status"]
        if priority[status] > priority[previous_status]:
            summary[feature_id]["status"] = status
            summary[feature_id]["reasons"] = []
        elif priority[status] == priority[previous_status]:
            summary[feature_id]["status"] = status
        if reason:
            if summary[feature_id]["reasons"] == ["no_trigger_observed"]:
                summary[feature_id]["reasons"] = []
            if reason not in summary[feature_id]["reasons"]:
                summary[feature_id]["reasons"].append(reason)

    delivery_to_features: dict[str, list[str]] = {}
    exposed_ids: set[str] = set()
    response_ids: set[str] = set()
    action_by_delivery: dict[str, str] = {}
    provider_receipts_present = any(
        row.get("event_type") == "provider.request" for row in materialized
    )
    producer_terminal_ids = {
        str(row.get("payload", {}).get("invocation_id") or "")
        for row in materialized
        if row.get("event_type") == "producer.invocation"
        and row.get("payload", {}).get("outcome") != "entered"
    }
    for row in materialized:
        event_type = str(row.get("event_type") or "")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "provider.request":
            exposed_ids.update(str(item) for item in payload.get("delivery_ids", ()))
            continue
        if event_type == "model.request":
            if not provider_receipts_present:
                exposed_ids.update(
                    str(item) for item in payload.get("delivery_ids", ())
                )
            continue
        if event_type == "model.response":
            response_ids.update(str(item) for item in payload.get("delivery_ids", ()))
            continue
        if event_type == "response.action":
            delivery_id = str(payload.get("delivery_id") or "")
            if delivery_id:
                # A sealed block remains in later conversation history, so
                # subsequent responses can mention the same delivery id. The
                # first linked response is the causal boundary verified by
                # ``verify_lifecycle_rows``; never overwrite it with a later
                # carried-context action.
                action_by_delivery.setdefault(
                    delivery_id,
                    str(payload.get("classification") or ""),
                )
            continue
        if event_type == "capability.applied":
            feature_id = str(payload.get("feature_id") or "")
            fact_id = str(payload.get("fact_id") or "")
            decision = str(payload.get("decision") or "")
            delivery_id = str(payload.get("delivery_id") or "")
            if fact_id and CAPABILITY_OWNERS.get(feature_id) != fact_id:
                update(feature_id, "TELEMETRY_FAULT", "capability_owner_mismatch")
            elif decision == "APPLIED":
                if delivery_id:
                    update(
                        feature_id,
                        "DELIVERED_UNEXPOSED",
                        "capability_applied",
                    )
                    delivery_to_features.setdefault(delivery_id, []).append(
                        feature_id
                    )
                    summary[feature_id]["deliveries"].append(delivery_id)
                else:
                    update(feature_id, "WITNESSED", "capability_applied")
            elif decision in {"SUPPRESSED", "DROPPED"}:
                update(
                    feature_id,
                    "SUPPRESSED_WITH_REASON",
                    str(payload.get("reason") or "capability_suppressed"),
                )
            elif decision == "FAULT":
                update(
                    feature_id,
                    "TELEMETRY_FAULT",
                    str(payload.get("reason") or "capability_fault"),
                )
            continue
        if event_type == "feature.applied":
            feature_id = str(payload.get("feature_id") or "")
            decision = str(payload.get("decision") or "")
            delivery_id = str(payload.get("delivery_id") or "")
            if feature_id not in DIRECT_FEATURES:
                continue
            if decision == "APPLIED":
                if delivery_id:
                    update(
                        feature_id,
                        "DELIVERED_UNEXPOSED",
                        str(payload.get("reason") or "feature_applied"),
                    )
                    delivery_to_features.setdefault(delivery_id, []).append(
                        feature_id
                    )
                    summary[feature_id]["deliveries"].append(delivery_id)
                else:
                    update(feature_id, "WITNESSED", "feature_applied")
            elif decision in {"SUPPRESSED", "DROPPED"}:
                update(
                    feature_id,
                    "SUPPRESSED_WITH_REASON",
                    str(payload.get("reason") or "feature_suppressed"),
                )
            elif decision == "FAULT":
                update(
                    feature_id,
                    "TELEMETRY_FAULT",
                    str(payload.get("reason") or "feature_fault"),
                )
            continue
        if event_type == "decision.committed":
            evidence_type = str(payload.get("evidence_type") or "")
            feature_id = str(
                payload.get("feature_id") or feature_for_evidence(evidence_type) or ""
            )
            decision = str(payload.get("decision") or "")
            reason = str(payload.get("reason") or "")
            if decision == "delivered" and feature_id:
                delivery_id = str(payload.get("delivery_id") or "")
                update(
                    feature_id,
                    "DELIVERED_UNEXPOSED",
                    reason or "sealed_and_delivered",
                )
                if delivery_id:
                    delivery_to_features.setdefault(delivery_id, []).append(
                        feature_id
                    )
                    summary[feature_id]["deliveries"].append(delivery_id)
            elif decision == "suppressed" and feature_id:
                update(feature_id, "SUPPRESSED_WITH_REASON", reason or "suppressed")
            continue
        if event_type == "feature.evaluated":
            feature_id = str(payload.get("feature_id") or "")
            if bool(payload.get("eligible")):
                outcome = str(payload.get("outcome") or "")
                update(
                    feature_id,
                    "TRIGGERED_DARK",
                    outcome or "producer_abstained",
                )
            else:
                update(
                    feature_id,
                    "INELIGIBLE",
                    str(payload.get("outcome") or "trigger_not_satisfied"),
                )
            continue
        if event_type == "producer.invocation":
            outcome = str(payload.get("outcome") or "")
            feature_ids = {
                mapped
                for mapped in (
                    feature_for_evidence(str(item))
                    for item in payload.get("evidence_types", ())
                )
                if mapped
            }
            if outcome == "entered":
                invocation_id = str(payload.get("invocation_id") or "")
                if invocation_id not in producer_terminal_ids:
                    for feature_id in feature_ids:
                        update(
                            feature_id,
                            "TELEMETRY_FAULT",
                            "producer_terminal_missing",
                        )
                continue
            reasons = payload.get("abstention_reasons", ())
            categories = {
                str(item.get("category") or "")
                for item in reasons
                if isinstance(item, dict)
            }
            reason_names = [
                str(item.get("reason") or "")
                for item in reasons
                if isinstance(item, dict) and str(item.get("reason") or "")
            ]
            for feature_id in feature_ids:
                if outcome == "returned_fact":
                    update(feature_id, "TRIGGERED_DARK", "candidate_returned")
                elif "instrumentation_gap" in categories or outcome == "fault":
                    update(
                        feature_id,
                        "TELEMETRY_FAULT",
                        reason_names[0] if reason_names else "producer_audit_fault",
                    )
                elif categories & {
                    "authority",
                    "registry",
                    "dedup",
                    "cooldown",
                    "suppression",
                }:
                    for reason in reason_names or ["authority_suppressed"]:
                        update(feature_id, "SUPPRESSED_WITH_REASON", reason)
                elif categories and categories <= {
                    "correct_quiet", "dependency_failure"
                }:
                    for reason in reason_names or ["required_input_absent"]:
                        update(feature_id, "INELIGIBLE", reason)
                else:
                    for reason in reason_names or ["producer_abstained"]:
                        update(feature_id, "TRIGGERED_DARK", reason)
            continue
        if event_type == "control.decision":
            feature_id = str(payload.get("feature_id") or "")
            decision = str(payload.get("decision") or "")
            reason = str(payload.get("reason") or decision or "control_evaluated")
            routed_feature = (
                feature_for_evidence(
                    str(payload.get("evidence_type") or "")
                )
                if feature_id == "GT_ROLE_DRIVEN_COALITION"
                else None
            )
            if decision == "APPLIED":
                update(feature_id, "WITNESSED", reason)
            elif decision in {"SUPPRESSED", "DROPPED"}:
                update(feature_id, "SUPPRESSED_WITH_REASON", reason)
                if routed_feature:
                    update(
                        routed_feature,
                        "SUPPRESSED_WITH_REASON",
                        reason,
                    )
            elif decision == "ERROR":
                update(feature_id, "TELEMETRY_FAULT", reason)
            elif feature_id:
                update(feature_id, "INELIGIBLE", reason)

    consistent_actions = {
        "target_referenced",
        "repair_or_verify_action",
        "inspect_or_search_action",
        "action_taken",
    }
    for delivery_id, feature_ids in delivery_to_features.items():
        for feature_id in feature_ids:
            summary[feature_id]["exposed"] = (
                summary[feature_id]["exposed"] or delivery_id in exposed_ids
            )
            if delivery_id in exposed_ids:
                summary[feature_id]["exposure_source"] = (
                    "provider.request"
                    if provider_receipts_present else "model.request_legacy"
                )
            summary[feature_id]["response_observed"] = (
                summary[feature_id]["response_observed"]
                or delivery_id in response_ids
            )
            action = action_by_delivery.get(delivery_id, "")
            if action:
                summary[feature_id]["action_observed"] = True
                summary[feature_id]["action_consistent"] = (
                    summary[feature_id]["action_consistent"]
                    or action in consistent_actions
                )
            if delivery_id in response_ids:
                update(
                    feature_id,
                    "WITNESSED",
                    "capability_applied"
                    if summary[feature_id]["kind"] == "CAP" else "",
                )
            elif delivery_id in exposed_ids:
                update(feature_id, "EXPOSED")
    return summary
