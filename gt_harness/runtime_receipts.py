"""Bind the installed Mini-SWE runtime to durable benchmark receipts.

This module deliberately derives claims from files produced by the running
installed product.  It does not infer a successful treatment from the later
benchmark grade and it refuses inconsistent provider counts or model identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from gt_engine.delivery_budget import (
    DELIVERY_BYTE_LIMITS,
    MAX_BOUNDARY_CLAIMS,
    MAX_TASK_DELIVERIES,
    TOTAL_DELIVERY_BYTE_LIMIT,
    delivery_byte_limit,
)
from gt_engine.graph_utilisation import graph_utilisation

_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_TOTAL_DELIVERY_BYTE_LIMIT = TOTAL_DELIVERY_BYTE_LIMIT


def _validate_delivery_boundaries(deliveries: list[dict]) -> None:
    """Budget each actual next-provider decision, never a task's lifetime."""
    boundaries: dict[int, list[dict]] = {}
    for delivery in deliveries:
        boundaries.setdefault(int(delivery.get("observed_iteration") or 0), []).append(delivery)
    for rows in boundaries.values():
        identities = [str(row.get("delivery_identity") or "") for row in rows]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_delivery_identity")
        if len(rows) > MAX_BOUNDARY_CLAIMS:
            raise ValueError("delivery_boundary_claim_limit_exceeded")
        if sum(int(row.get("context_byte_count") or 0) for row in rows) > TOTAL_DELIVERY_BYTE_LIMIT:
            raise ValueError("delivery_request_budget_exceeded")


def _dense_execution_receipts(events: list[dict]) -> list[dict]:
    return [{"schema": "gt.dense_index_receipt.v1",
             **{key: value for key, value in row.items()
                if key not in {"event", "event_hash", "sequence", "schema"}}}
            for row in events if row.get("event") == "dense_index_ready"]


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    _atomic_bytes(path, payload)


def _single_optional(root: Path, name: str) -> tuple[Path | None, dict[str, Any] | None]:
    paths = sorted(root.rglob(name)) if root.is_dir() else []
    if len(paths) > 1:
        raise ValueError(f"duplicate_runtime_artifact:{name}")
    if not paths:
        return None, None
    return paths[0], _read_object(paths[0])


def _events(state_dir: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    paths = sorted(state_dir.rglob("events.jsonl")) if state_dir.is_dir() else []
    if len(paths) > 1:
        raise ValueError("duplicate_runtime_artifact:events.jsonl")
    if not paths:
        return None, []
    rows: list[dict[str, Any]] = []
    for line in paths[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_event_journal_json") from exc
        if not isinstance(row, dict):
            raise ValueError("invalid_event_journal_row")
        rows.append(row)
    return paths[0], rows


def _published_graph(state_dir: Path, events: list[dict[str, Any]]) -> tuple[Path | None, dict | None]:
    publications = [row for row in events if row.get("event") == "graph_publication"]
    if not publications:
        return _single_optional(state_dir, "graph.manifest.json")
    publication = publications[-1]
    digest = str(publication.get("artifact_sha256") or "")
    if not _SHA64.fullmatch(digest):
        raise ValueError("invalid_graph_publication_identity")
    matches = [path for path in state_dir.rglob("graph.manifest.json") if _sha256(path) == digest]
    if len(matches) != 1:
        raise ValueError("graph_publication_manifest_missing_or_ambiguous")
    manifest = _read_object(matches[0])
    if manifest.get("graph_sha256") != publication.get("graph_sha256"):
        raise ValueError("graph_publication_content_mismatch")
    return matches[0], manifest


def _graph_publication_state(events: list[dict[str, Any]]) -> dict:
    publications = [row for row in events if row.get("event") == "graph_publication"]
    snapshots = [row for row in events if row.get("event") == "repository_snapshot"]
    publication = publications[-1] if publications else {}
    snapshot = snapshots[-1] if snapshots else {}
    published_revision = str(publication.get("repository_revision") or "")
    current_revision = str(snapshot.get("repository_revision") or "")
    known = bool(_SHA64.fullmatch(published_revision) and _SHA64.fullmatch(current_revision)
                 and snapshot.get("complete") is True)
    return {
        "schema": "gt.graph_publication_state.v1",
        "manifest_sha256": publication.get("artifact_sha256"),
        "published_source_revision": published_revision,
        "observed_source_revision": current_revision,
        "status": ("current" if published_revision == current_revision else "historical")
        if known else "unknown",
    }


def _delivery_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") not in {"evidence_delivery", "context_addition_delivery"}:
            continue
        event_hash = str(row.get("event_hash") or "")
        artifact_hash = str(row.get("artifact_sha256") or "")
        if not _SHA64.fullmatch(event_hash):
            raise ValueError("invalid_evidence_delivery_event_hash")
        if artifact_hash and not _SHA64.fullmatch(artifact_hash):
            raise ValueError("invalid_evidence_delivery_artifact_hash")
        lane = str(row.get("lane") or "sealed")
        kind = str(row.get("kind") or row.get("evidence_type") or "")
        payload_sha256 = str(row.get("payload_sha256") or "")
        if lane not in {"prompt", "sealed"}:
            raise ValueError("invalid_delivery_lane")
        if lane == "prompt" and (
            kind not in {"context_contract", "context_delta"}
            or not _SHA64.fullmatch(payload_sha256)
        ):
            raise ValueError("invalid_prompt_delivery_kind")
        if lane != "prompt" and payload_sha256 and not _SHA64.fullmatch(payload_sha256):
            raise ValueError("invalid_delivery_payload_hash")
        deliveries.append(
            {
                "event_hash": event_hash,
                "lane": lane,
                "kind": kind,
                "dedup_key": str(row.get("dedup_key") or ""),
                "evidence_type": str(row.get("evidence_type") or ""),
                "artifact_sha256": artifact_hash or None,
                "payload_sha256": payload_sha256 or None,
                "delivery_identity": payload_sha256 or None,
                "action_index": int(row.get("action_index") or 0),
                "observed_iteration": int(row.get("iteration") or 0),
                "rendered_bytes": int(row.get("rendered_bytes") or 0),
                "semantics": str(row.get("semantics") or ""),
                "target": str(row.get("target") or ""),
            }
        )
    return deliveries


def _provider_delivery_receipts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = [
        row for row in events
        if row.get("event") in {"evidence_delivery", "context_addition_delivery"}
        and row.get("dedup_key")
    ]
    provider_rows = [row for row in events if row.get("event") == "provider_delivery"]
    receipts: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") != "receipt" or row.get("transition") != "delivered":
            continue
        iteration = int(row.get("iteration") or 0)
        matching = [
            candidate for candidate in evidence
            if str(candidate.get("dedup_key") or "")
            == str(row.get("dedup_key") or "")
            and int(candidate.get("iteration") or 0) == iteration
            and int(candidate.get("sequence") or 0) < int(row.get("sequence") or 0)
        ]
        if len(matching) != 1:
            raise ValueError("delivery_receipt_evidence_join_failed")
        match = matching[0]
        delivery_identity = str(row.get("payload_hash") or "")
        if delivery_identity != str(
            match.get("delivery_identity") or match.get("payload_sha256") or ""
        ):
            raise ValueError("delivery_receipt_identity_join_failed")
        provider = next(
            (
                candidate
                for candidate in provider_rows
                if int(candidate.get("sequence") or 0) > int(row.get("sequence") or 0)
            ),
            None,
        )
        if provider is None:
            raise ValueError("delivery_receipt_provider_join_failed")
        delivery_ids = provider.get("delivery_ids")
        if (
            not isinstance(delivery_ids, list)
            or any(not isinstance(value, str) for value in delivery_ids)
            or delivery_identity not in delivery_ids
        ):
            raise ValueError("delivery_receipt_provider_join_failed")
        index = len(receipts) + 1
        kind = "repository_start" if index == 1 else "repository_update"
        lane = str(match.get("lane") or "sealed")
        delivery_kind = str(match.get("kind") or match.get("evidence_type") or "")
        context_byte_count = int(match.get("rendered_bytes") or 0)
        byte_limit = delivery_byte_limit(lane=lane, kind=delivery_kind)
        if context_byte_count > byte_limit:
            raise ValueError("delivery_context_budget_exceeded")
        delivered_before_call = int(provider.get("iteration") or 0) if provider else 0
        receipts.append(
            {
                "schema": "gt.provider_delivery.v2",
                "delivery_index": index,
                "kind": kind,
                "lane": lane,
                "delivery_kind": delivery_kind,
                "evidence_type": str(row.get("evidence_type") or ""),
                "dedup_key": str(row.get("dedup_key") or ""),
                "context_sha256": delivery_identity,
                "delivery_identity": delivery_identity,
                **({"artifact_sha256": match["artifact_sha256"]}
                   if match.get("artifact_sha256") else {}),
                "context_byte_count": context_byte_count,
                "byte_limit": byte_limit,
                "event_sequence": int(match.get("sequence") or 0),
                "observed_iteration": iteration,
                "delivered_before_call": delivered_before_call,
                "same_observation": delivered_before_call == iteration + 1,
                "provider_request_id": str(provider.get("request_id") or "") if provider else "",
            }
        )
    return receipts


def _delivery_refusals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_reasons = {
        "boundary_claim_ceiling",
        "request_delivery_byte_ceiling",
        "delivery_byte_ceiling",
        "task_delivery_byte_ceiling",
        "task_delivery_dose_ceiling",
        "task_delivery_storm_backstop",
        "duplicate_delivery_identity",
    }
    refusals: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") != "delivery_refused":
            continue
        reason = str(row.get("reason") or "")
        payload_sha256 = str(row.get("payload_sha256") or "")
        lane = str(row.get("lane") or "")
        if reason not in allowed_reasons:
            raise ValueError("invalid_delivery_refusal_reason")
        if lane not in {"prompt", "sealed"}:
            raise ValueError("invalid_delivery_refusal_lane")
        if not _SHA64.fullmatch(payload_sha256):
            raise ValueError("invalid_delivery_refusal_payload_hash")
        if not _SHA64.fullmatch(str(row.get("event_hash") or "")):
            raise ValueError("invalid_delivery_refusal_event_hash")
        if int(row.get("candidate_ordinal") or 0) < 1:
            raise ValueError("invalid_delivery_refusal_ordinal")
        refusals.append(
            {
                "event_hash": str(row.get("event_hash") or ""),
                "event_sequence": int(row.get("sequence") or 0),
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "dedup_key": str(row.get("dedup_key") or ""),
                "reason": reason,
                "candidate_ordinal": int(row.get("candidate_ordinal") or 0),
                "rendered_bytes": int(row.get("rendered_bytes") or 0),
                "payload_sha256": payload_sha256,
                "delivery_identity": str(row.get("delivery_identity") or payload_sha256),
                "admitted_count": int(row.get("admitted_count") or 0),
                "admitted_bytes": int(row.get("admitted_bytes") or 0),
                "observed_iteration": int(row.get("iteration") or 0),
            }
        )
    return refusals


def _provider_usage(
    events: list[dict[str, Any]], *, attempted_calls: int
) -> dict[str, int | float]:
    responses = [row for row in events if row.get("event") == "provider_response"]
    completed_calls = len(responses)
    if completed_calls > attempted_calls:
        raise ValueError("provider_response_count_exceeds_attempts")
    prompt = completion = cached = 0
    cost = 0.0
    for row in responses:
        usage = row.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("provider_response_usage_missing")
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        cached += int(details.get("cached_tokens") or 0)
        cost += float(usage.get("cost") or 0)
    return {
        "provider_completed_calls": completed_calls,
        "provider_failed_calls": attempted_calls - completed_calls,
        "input_tokens": prompt,
        "output_tokens": completion,
        "cached_tokens": cached,
        "total_cost": round(cost, 12),
    }


def _provider_admissions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and retain every final-boundary provider admission decision."""

    admissions: list[dict[str, Any]] = []
    for row in events:
        if row.get("event") != "provider_admission":
            continue
        event_hash = str(row.get("event_hash") or "")
        status = str(row.get("status") or "")
        reason = str(row.get("reason") or "")
        source = str(row.get("metadata_source") or "")
        if not _SHA64.fullmatch(event_hash):
            raise ValueError("invalid_provider_admission_event_hash")
        context_refusal = (
            status == "refused"
            and reason == "GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE"
        )
        if (
            status not in {"admitted", "refused"}
            or not reason
            or (not source and not context_refusal)
        ):
            raise ValueError("invalid_provider_admission_status")
        numeric: dict[str, int] = {}
        for field in (
            "request_tokens",
            "request_bytes",
            "context_window_tokens",
            "reserved_output_tokens",
            "input_budget_tokens",
        ):
            value = row.get(field)
            if type(value) is not int or value < 0:
                raise ValueError("invalid_provider_admission_measurement")
            numeric[field] = value
        if context_refusal:
            if numeric["input_budget_tokens"] != max(
                0,
                numeric["context_window_tokens"]
                - numeric["reserved_output_tokens"],
            ):
                raise ValueError("invalid_provider_admission_budget")
        else:
            if (
                numeric["context_window_tokens"] - numeric["reserved_output_tokens"]
                != numeric["input_budget_tokens"]
                or numeric["reserved_output_tokens"] < 1
            ):
                raise ValueError("invalid_provider_admission_budget")
            over_budget = numeric["request_tokens"] > numeric["input_budget_tokens"]
            if (status == "admitted" and over_budget) or (
                status == "refused"
                and reason == "GT_PROVIDER_REQUEST_TOO_LARGE"
                and not over_budget
            ):
                raise ValueError("invalid_provider_admission_decision")
        admissions.append(
            {
                "event_hash": event_hash,
                "event_sequence": int(row.get("sequence") or 0),
                "status": status,
                "reason": reason,
                **numeric,
                "metadata_source": source,
            }
        )
    return admissions


def _semantic_graph_deliveries(
    state_dir: Path, events: list[dict[str, Any]], deliveries: list[dict[str, Any]],
    *, task_id: str, product_source_sha: str,
) -> frozenset[str]:
    """Verify graph localization against its source snapshot at admission."""
    from gt_engine.delivery_budget import compact_localization
    from gt_engine.indexer import certify_graph_artifact
    from gt_engine.request_history import load_history_evidence
    from gt_engine.retrieval import render_semantic_localization, source_symbol
    from gt_harness.product import groundtruth_release

    events_path, _ = _events(state_dir)
    if events_path is None:
        return frozenset()
    task_root = events_path.parent

    def blob(namespace: str, digest: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("semantic_localization_blob_identity_invalid")
        path = task_root / namespace / f"{digest}.json"
        if not path.is_file() or _sha256(path) != digest:
            raise ValueError("semantic_localization_blob_integrity_failed")
        return _read_object(path)

    verified = set()
    for delivery in deliveries:
        if not str(delivery.get("dedup_key", "")).startswith("semantic-localization:"):
            continue
        artifact = blob("localization_advisory", str(delivery.get("artifact_sha256") or ""))
        if artifact.get("schema") != "gt.semantic_localization.v1":
            raise ValueError("semantic_localization_schema_invalid")
        preceding = [row for row in events if row.get("event") == "repository_snapshot"
                     and int(row.get("sequence", 0)) < int(delivery["event_sequence"])]
        if not preceding:
            raise ValueError("semantic_localization_source_witness_missing")
        witness = preceding[-1]
        snapshot = blob("repository_snapshots", str(witness.get("snapshot_sha256") or ""))
        revision = artifact.get("source_revision")
        if (not revision or revision != witness.get("repository_revision")
                or revision != snapshot.get("revision") or snapshot.get("complete") is not True):
            raise ValueError("semantic_localization_source_revision_mismatch")
        graph_digest = artifact.get("graph_revision")
        matching = []
        for manifest_path in state_dir.rglob("graph.manifest.json"):
            manifest = _read_object(manifest_path)
            if manifest.get("graph_sha256") == graph_digest:
                graph_path = manifest_path.with_name("graph.db")
                valid, reason = certify_graph_artifact(
                    graph_path, manifest_path,
                    expected_root_sha256=str(snapshot.get("root_sha256") or ""),
                    expected_binary_sha256=groundtruth_release()["producer_sha256"],
                    expected_task_id=task_id, expected_product_source_sha=product_source_sha,
                )
                if not valid:
                    raise ValueError(f"semantic_localization_graph_integrity_failed:{reason}")
                matching.append(graph_path)
        if len(matching) != 1:
            raise ValueError("semantic_localization_certified_graph_missing")
        graph_path = matching[0]
        ranking = artifact.get("ranking", {})
        if ranking.get("graph_content_sha256") != graph_digest:
            raise ValueError("semantic_localization_projection_revision_mismatch")
        items = artifact.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("semantic_localization_items_missing")
        fused = {row["stable_id"]: row for row in ranking.get("fused", [])}
        files = {row["path"]: row["sha256"] for row in snapshot.get("files", [])}
        with sqlite3.connect(f"{graph_path.resolve().as_uri()}?mode=ro", uri=True) as db:
            for item in items:
                candidate = fused.get(item.get("stable_id"), {})
                provenance = candidate.get("provenance", {})
                symbol, source_hash = source_symbol(db, provenance.get("node_id"))
                if (symbol.as_dict() != provenance or symbol.stable_id != item.get("stable_id")
                        or symbol.file_path != item.get("path")
                        or max(1, symbol.start_line) != item.get("line")
                        or item.get("anchor") != f"{symbol.file_path}:{max(1, symbol.start_line)}"
                        or not source_hash or files.get(symbol.file_path) != source_hash
                        or item.get("score") != candidate.get("rrf_score")
                        or item.get("reasons") != [f"retrieval:{value}" for value in
                                                   candidate.get("contributing_sources", [])]):
                    raise ValueError("semantic_localization_primary_source_mismatch")
        rendered = compact_localization(render_semantic_localization(items))
        if not rendered:
            raise ValueError("semantic_localization_admitted_bytes_mismatch")
        delivery_identity = str(delivery.get("delivery_identity") or "")
        delivery_path = task_root / "deliveries" / f"{delivery_identity}.json"
        if (not _SHA64.fullmatch(delivery_identity) or not delivery_path.is_file()
                or _sha256(delivery_path) != delivery_identity):
            raise ValueError("semantic_localization_delivery_blob_integrity_failed")
        delivered = delivery_path.read_text(encoding="utf-8")
        if (len(delivered.encode("utf-8")) != int(delivery.get("context_byte_count") or 0)
                or hashlib.sha256(delivered.encode("utf-8")).hexdigest()
                != delivery.get("context_sha256")):
            raise ValueError("semantic_localization_admitted_bytes_mismatch")
        prefix = "[GT_CONTEXT_UNIT] "
        if not delivered.startswith(prefix) or "\n" not in delivered:
            raise ValueError("semantic_localization_context_wrapper_invalid")
        header, body = delivered.split("\n", 1)
        try:
            metadata = json.loads(header[len(prefix):])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("semantic_localization_context_wrapper_invalid") from exc
        admitted = [
            row for row in events
            if row.get("event") == "decision_context_unit_admitted"
            and row.get("delivery_identity") == delivery_identity
        ]
        if len(admitted) != 1:
            raise ValueError("semantic_localization_context_unit_join_failed")
        context_unit = admitted[0]
        reference = context_unit.get("artifact_reference")
        if not isinstance(reference, dict):
            raise ValueError("semantic_localization_context_reference_missing")
        expected_evidence_root = (task_root / "output_evidence").resolve()
        try:
            layouts = [row for row in events if row.get("event") == "runtime_layout"]
            if layouts:
                if (len(layouts) != 1 or layouts[0].get("layout_schema") != "gt.runtime_layout.v1"
                        or not layouts[0].get("evidence_root")
                        or reference.get("root") != layouts[0]["evidence_root"]):
                    raise ValueError("history_evidence_runtime_root_mismatch")
                # Collection relocates files. Bind the original namespace to
                # its journal witness, then read only this task's collected CAS.
                local_reference = {**reference, "root": str(expected_evidence_root)}
            else:
                local_reference = reference
            referenced = load_history_evidence(expected_evidence_root, local_reference).decode(
                "utf-8", "strict"
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("semantic_localization_context_reference_invalid") from exc
        if referenced != rendered:
            raise ValueError("semantic_localization_primary_bytes_mismatch")
        if (
            not isinstance(metadata, dict)
            or metadata.get("unit_id") != context_unit.get("unit_id")
            or metadata.get("supersession_key")
            != context_unit.get("supersession_key")
            or metadata.get("source_revision") != artifact.get("source_revision")
        ):
            raise ValueError("semantic_localization_context_wrapper_invalid")
        if body == rendered:
            verified.add(delivery_identity)
            continue
        reference_prefix = "[GT_CONTEXT_UNIT_REFERENCE] "
        if not body.startswith(reference_prefix):
            raise ValueError("semantic_localization_admitted_bytes_mismatch")
        try:
            visible_reference = json.loads(body[len(reference_prefix):])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("semantic_localization_context_wrapper_invalid") from exc
        for key in (
            "schema", "sha256", "total_length", "encoding", "kind",
            "retrieval_command",
        ):
            if visible_reference.get(key) != reference.get(key):
                raise ValueError("semantic_localization_context_reference_spliced")
        # A reference proves availability, not that the semantic bytes were in
        # the request. Graph-use credit requires an inline exact canonical unit.
    return frozenset(verified)


def issue_runtime_receipts(
    *,
    report_path: Path,
    trajectory_path: Path,
    state_dir: Path,
    product_receipt_path: Path,
    adapter_receipt_path: Path,
    task_id: str,
    product_source_sha: str,
    treatment: str,
    requested_model: str,
    scaffold_version: str,
    time_budget_seconds: int,
) -> dict[str, Any]:
    """Issue the product and adapter receipts or fail without partial output."""

    if not task_id.strip():
        raise ValueError("task_id_required")
    if not _SHA40.fullmatch(product_source_sha):
        raise ValueError("product_source_sha_invalid")
    if treatment not in {"bare", "groundtruth"}:
        raise ValueError("treatment_invalid")
    if time_budget_seconds < 1:
        raise ValueError("time_budget_seconds_invalid")

    report = _read_object(report_path)
    trajectory = _read_object(trajectory_path)
    info = trajectory.get("info")
    info = info if isinstance(info, dict) else {}
    model_stats = info.get("model_stats")
    model_stats = model_stats if isinstance(model_stats, dict) else {}
    agent_turn_calls = int(model_stats.get("api_calls") or 0)
    gt = report.get("gt")
    gt = gt if isinstance(gt, dict) else {}
    # F10: api_calls is the AGENT's n_calls and never counts a GT-internal
    # bootstrap turn, which bypasses agent.query(). n_calls also bounds the
    # agent's steps and cost, so it must not be inflated. Reconcile instead by
    # adding the separately recorded bootstrap calls, so every count compared
    # below is measured at the same transport boundary. Usage and cost sums are
    # untouched and continue to include the bootstrap: it is real spend.
    bootstrap_calls = int(gt.get("select_catalog_bootstrap_calls") or 0)
    if bootstrap_calls < 0:
        raise ValueError("select_catalog_bootstrap_calls_invalid")
    provider_calls = agent_turn_calls + bootstrap_calls
    terminal_requests = gt.get("terminal_requests")
    if terminal_requests is not None and int(terminal_requests) != provider_calls:
        raise ValueError("provider_call_count_mismatch")

    reported_model = str(gt.get("provider_reported_model") or requested_model)
    effective_model = str(gt.get("resolved_model") or requested_model)
    if reported_model != requested_model:
        raise ValueError("provider_model_mismatch")
    report_model = str(report.get("model") or requested_model)
    if report_model != requested_model:
        raise ValueError("runner_model_mismatch")

    events_path, event_rows = _events(state_dir)
    delivery_events = _delivery_rows(event_rows)
    evidence_events = [row for row in delivery_events if row["lane"] == "sealed"]
    prompt_events = [row for row in delivery_events if row["lane"] == "prompt"]
    refused_deliveries = _delivery_refusals(event_rows)
    deliveries = _provider_delivery_receipts(event_rows)
    if len(deliveries) != len(delivery_events):
        raise ValueError("delivery_receipt_census_mismatch")
    _validate_delivery_boundaries(deliveries)
    delivered_identities = {str(row.get("delivery_identity") or "") for row in delivery_events}
    for refusal in refused_deliveries:
        duplicate = refusal["reason"] == "duplicate_delivery_identity"
        later_delivery = any(
            int(delivery["event_sequence"]) > int(refusal["event_sequence"])
            and delivery["observed_iteration"] == refusal["observed_iteration"]
            and (
                delivery["delivery_identity"] == refusal["delivery_identity"]
                or delivery["dedup_key"] == refusal["dedup_key"]
            )
            for delivery in deliveries
        )
        if later_delivery:
            raise ValueError("refused_then_delivered")
        if any(row["dedup_key"] == refusal["dedup_key"]
               and row["observed_iteration"] == refusal["observed_iteration"]
               for row in delivery_events) and not duplicate:
            raise ValueError("refused_delivery_present")
        if duplicate and refusal["delivery_identity"] not in delivered_identities:
            raise ValueError("invalid_duplicate_delivery_refusal")
    provider_usage = _provider_usage(event_rows, attempted_calls=provider_calls)
    provider_admissions = _provider_admissions(event_rows)
    repro_path, reproduction = _single_optional(state_dir, "reproducibility_manifest.json")
    graph_path, graph = _published_graph(state_dir, event_rows)
    reproduction = reproduction or {}
    graph = graph or {}
    dense_runs = _dense_execution_receipts(event_rows)
    dense_index = (
        dense_runs[-1] if dense_runs
        else {
            "schema": "gt.dense_index_receipt.v1",
            "query_ready": False,
            "reason": "dense_index_receipt_missing",
        }
    )
    provider_receipts = reproduction.get("provider_receipts")
    provider_receipts = provider_receipts if isinstance(provider_receipts, dict) else {}
    manifest_count = provider_receipts.get("request_count")
    if manifest_count is not None and int(manifest_count) != provider_calls:
        raise ValueError("provider_manifest_count_mismatch")

    exit_code = int(report.get("exit_code") or 0)
    terminal = str(report.get("terminal") or "internal_error")
    status = "COMPLETED" if exit_code == 0 else "ERROR"
    if status == "COMPLETED" and (
        len(provider_admissions) != provider_calls
        or any(row["status"] != "admitted" for row in provider_admissions)
    ):
        raise ValueError("provider_admission_count_mismatch")
    usage = gt.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    if int(usage.get("prompt_tokens") or 0) != provider_usage["input_tokens"]:
        raise ValueError("provider_prompt_token_count_mismatch")
    if int(usage.get("completion_tokens") or 0) != provider_usage["output_tokens"]:
        raise ValueError("provider_completion_token_count_mismatch")
    event_journal = reproduction.get("event_journal")
    event_journal = event_journal if isinstance(event_journal, dict) else {}
    if not (
        event_journal.get("valid") is True
        and not event_journal.get("issues")
        and int(event_journal.get("event_count") or 0) == len(event_rows)
        and event_rows
        and event_journal.get("event_head") == event_rows[-1].get("event_hash")
    ):
        raise ValueError("event_journal_conservation_failed")
    treatment_receipt: dict[str, Any] = {
        "schema": "gt.miniswe_treatment_receipt.v1",
        "treatment": treatment,
        "treatment_status": (
            "ACTIVE"
            if treatment == "groundtruth" and report.get("gt_mode") != "off"
            else "INACTIVE"
        ),
        "gt_mode": str(report.get("gt_mode") or "off"),
        "contract_shipped": bool(gt.get("contract_shipped")),
        "verified": bool(gt.get("verified")),
        "unmet_predicates": list(gt.get("unmet_predicates") or []),
        "unverified_predicates": list(gt.get("unverified_predicates") or []),
        "delivery_count": len(deliveries),
        "prompt_delivery_count": len(prompt_events),
        "sealed_delivery_count": len(evidence_events),
        "evidence_items_delivered": len(evidence_events),
        "evidence_event_count": len(evidence_events),
        "evidence_deliveries": evidence_events,
        "prompt_context_deliveries": prompt_events,
        "provider_delivery_receipts": deliveries,
        "refused_deliveries": refused_deliveries,
        "delivery_budget": {
            "schema": "gt.delivery_budget.v2",
            "unit": "utf8_bytes",
            "conversion_from_legacy_tokens": "4_bytes_per_token",
            "sealed_limit": DELIVERY_BYTE_LIMITS["sealed"],
            "prompt_contract_limit": DELIVERY_BYTE_LIMITS["context_contract"],
            "prompt_delta_limit": DELIVERY_BYTE_LIMITS["context_delta"],
            "total_limit": _TOTAL_DELIVERY_BYTE_LIMIT,
            "total_observed": sum(row["context_byte_count"] for row in deliveries),
            "task_delivery_limit": None,
            "boundary_claim_limit": MAX_BOUNDARY_CLAIMS,
            "scope": "provider_decision",
            "admitted_count": len(deliveries),
            "refused_count": len(refused_deliveries),
        },
        "graph_utilisation": graph_utilisation(
            deliveries, cochange_rows=graph.get("cochange_rows"),
            verified_graph_deliveries=_semantic_graph_deliveries(
                state_dir, event_rows, deliveries, task_id=task_id,
                product_source_sha=product_source_sha,
            ),
        ),
        "provider_admissions": provider_admissions,
        "retrieval_mode": "hybrid_required",
        "dense_index_receipt": dense_index,
        "dense_execution_receipts": dense_runs,
        "event_journal": event_journal,
        "completion_state_event_journal": dict(gt.get("event_journal") or {}),
        "provider_identity": {
            "requested": requested_model,
            "resolved": effective_model,
            "reported": reported_model,
            "match": reported_model == requested_model,
        },
        "reproducibility_manifest": reproduction,
        "graph_certification": graph,
        "graph_publication_state": _graph_publication_state(event_rows),
    }
    product: dict[str, Any] = {
        "schema": "gt.run_receipt.v1",
        "synthetic_transport": bool(report.get("synthetic_transport")),
        "task_id": task_id,
        "status": status,
        "terminal": terminal,
        "stop_reason": str(info.get("exit_status") or terminal),
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "provider_calls": provider_calls,
        "agent_turn_calls": agent_turn_calls,
        "select_catalog_bootstrap_calls": bootstrap_calls,
        **provider_usage,
        "research_valid": bool(report.get("research_valid")),
        "treatment_receipt": treatment_receipt,
        "integrity": {
            "report_sha256": _sha256(report_path),
            "trajectory_sha256": _sha256(trajectory_path),
            "events_sha256": _sha256(events_path) if events_path else None,
            "reproducibility_manifest_sha256": (_sha256(repro_path) if repro_path else None),
            "graph_manifest_sha256": _sha256(graph_path) if graph_path else None,
        },
    }
    adapter = {
        "schema": "gt.benchmark_adapter_receipt.v1",
        "synthetic_transport": bool(report.get("synthetic_transport")),
        "task_id": task_id,
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
    }

    trajectory_copy = product_receipt_path.with_name("gt-run.trajectory.json")
    _atomic_bytes(trajectory_copy, trajectory_path.read_bytes())
    _atomic_json(adapter_receipt_path, adapter)
    _atomic_json(product_receipt_path, product)
    return product


def issue_runtime_receipt_failure(
    *,
    report_path: Path,
    trajectory_path: Path,
    product_receipt_path: Path,
    adapter_receipt_path: Path,
    task_id: str,
    product_source_sha: str,
    treatment: str,
    requested_model: str,
    scaffold_version: str,
    time_budget_seconds: int,
    terminal: str,
    exit_code: int,
    error: BaseException,
) -> dict[str, Any]:
    """Write fail-closed receipts without changing the completed task outcome.

    This path makes a receipt-construction defect visible to downstream
    attestation while preserving the agent's native terminal state and exit
    code.  It intentionally makes no treatment, evidence, or research claim.
    """

    report: dict[str, Any] = {}
    trajectory: dict[str, Any] = {}
    try:
        report = _read_object(report_path)
    except ValueError:
        pass
    try:
        trajectory = _read_object(trajectory_path)
    except ValueError:
        pass
    model_identity = report.get("model_identity")
    model_identity = model_identity if isinstance(model_identity, dict) else {}
    gt = report.get("gt")
    gt = gt if isinstance(gt, dict) else {}
    effective_model = str(gt.get("resolved_model") or model_identity.get("resolved") or "") or None
    info = trajectory.get("info")
    info = info if isinstance(info, dict) else {}
    model_stats = info.get("model_stats")
    model_stats = model_stats if isinstance(model_stats, dict) else {}
    provider_calls = model_stats.get("api_calls")
    provider_calls = int(provider_calls) if provider_calls is not None else None

    failure = {
        "code": "runtime_receipt_issuance_failed",
        "type": type(error).__name__,
        "message": str(error),
    }
    integrity = {
        "report_sha256": _sha256(report_path) if report_path.is_file() else None,
        "trajectory_sha256": (_sha256(trajectory_path) if trajectory_path.is_file() else None),
    }
    product: dict[str, Any] = {
        "schema": "gt.run_receipt.v1",
        "task_id": task_id,
        "synthetic_transport": bool(report.get("synthetic_transport")),
        "status": "ERROR",
        "terminal": terminal,
        "stop_reason": terminal,
        "exit_code": exit_code,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "provider_calls": provider_calls,
        "research_valid": False,
        "receipt_issuance": failure,
        "integrity": integrity,
    }
    adapter: dict[str, Any] = {
        "schema": "gt.benchmark_adapter_receipt.v1",
        "task_id": task_id,
        "synthetic_transport": bool(report.get("synthetic_transport")),
        "status": "ERROR",
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": treatment,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "agent_scaffold_version": scaffold_version,
        "product_source_sha": product_source_sha,
        "time_budget_seconds": time_budget_seconds,
        "receipt_issuance": failure,
    }

    if trajectory_path.is_file():
        trajectory_copy = product_receipt_path.with_name("gt-run.trajectory.json")
        _atomic_bytes(trajectory_copy, trajectory_path.read_bytes())
    _atomic_json(adapter_receipt_path, adapter)
    _atomic_json(product_receipt_path, product)
    return product


def verify_runtime_receipt(receipt_path: Path) -> list[str]:
    """Verify a completed receipt against the exact adjacent runtime files."""

    receipt = _read_object(receipt_path)
    errors: list[str] = []
    if receipt.get("synthetic_transport"):
        errors.append("synthetic_transport_not_paid_evidence")
    if receipt.get("schema") != "gt.run_receipt.v1":
        errors.append("product_receipt_schema")
    if receipt.get("status") != "COMPLETED":
        errors.append("product_not_completed")
    if receipt.get("treatment") != "groundtruth":
        errors.append("product_treatment_mismatch")
    if receipt.get("agent_scaffold_version") != "2.4.6":
        errors.append("product_scaffold_version_mismatch")
    try:
        adapter = _read_object(receipt_path.with_name("benchmark-adapter.json"))
        if (adapter.get("schema") != "gt.benchmark_adapter_receipt.v1"
                or adapter.get("product_command") != "gt-miniswe-run"
                or any(adapter.get(key) != receipt.get(key) for key in (
                    "task_id", "treatment", "requested_model", "effective_model",
                    "agent_scaffold_version", "product_source_sha", "time_budget_seconds",
                ))
                or bool(adapter.get("synthetic_transport")) != bool(receipt.get("synthetic_transport"))):
            errors.append("product_adapter_receipt_mismatch")
    except (ValueError, OSError):
        errors.append("product_adapter_receipt_missing_or_invalid")

    trajectory_path = receipt_path.with_name("gt-run.trajectory.json")
    report_path = receipt_path.with_name("miniswe_report.json")
    integrity = receipt.get("integrity")
    integrity = integrity if isinstance(integrity, dict) else {}
    if not trajectory_path.is_file():
        errors.append("product_trajectory_missing")
        trajectory: dict[str, Any] = {}
    else:
        trajectory = _read_object(trajectory_path)
        if integrity.get("trajectory_sha256") != _sha256(trajectory_path):
            errors.append("product_trajectory_digest_mismatch")
    if not report_path.is_file():
        errors.append("product_report_missing")
        report: dict[str, Any] = {}
    else:
        report = _read_object(report_path)
        if integrity.get("report_sha256") != _sha256(report_path):
            errors.append("product_report_digest_mismatch")
    if bool(report.get("synthetic_transport")) != bool(receipt.get("synthetic_transport")):
        errors.append("product_transport_report_mismatch")
    if report.get("synthetic_transport") and "synthetic_transport_not_paid_evidence" not in errors:
        errors.append("synthetic_transport_not_paid_evidence")
    calls = ((trajectory.get("info") or {}).get("model_stats") or {}).get("api_calls")
    if calls is None:
        errors.append("product_provider_calls_missing")
    elif int(calls) != int(
        receipt.get("agent_turn_calls", receipt.get("provider_calls")) or 0
    ):
        # F10: api_calls is the AGENT's turn count. provider_calls also includes
        # GT-internal bootstrap turns, so comparing the two populations directly
        # mismatches whenever a bootstrap fired. Compare like with like; the
        # bootstrap is reconciled separately against admissions and responses.
        errors.append("product_provider_calls_mismatch")
    attempted_calls = int(receipt.get("provider_calls") or 0)
    completed_calls = int(receipt.get("provider_completed_calls") or 0)
    failed_calls = int(receipt.get("provider_failed_calls") or 0)
    if (
        min(attempted_calls, completed_calls, failed_calls) < 0
        or completed_calls + failed_calls != attempted_calls
    ):
        errors.append("product_provider_call_conservation_failed")
    gt_report = report.get("gt") if isinstance(report, dict) else None
    gt_report = gt_report if isinstance(gt_report, dict) else {}
    report_usage = gt_report.get("usage")
    report_usage = report_usage if isinstance(report_usage, dict) else {}
    if int(report_usage.get("prompt_tokens") or 0) != int(receipt.get("input_tokens") or 0):
        errors.append("product_input_token_conservation_failed")
    if int(report_usage.get("completion_tokens") or 0) != int(receipt.get("output_tokens") or 0):
        errors.append("product_output_token_conservation_failed")
    if str(gt_report.get("resolved_model") or receipt.get("requested_model")) != receipt.get(
        "effective_model"
    ):
        errors.append("product_effective_model_report_mismatch")
    state_dir = receipt_path.parent / "gt-state"
    try:
        events_path, runtime_events = _events(state_dir)
    except ValueError as exc:
        errors.append(str(exc))
        events_path, runtime_events = None, []
    if events_path is None:
        errors.append("product_event_journal_missing")
    else:
        if integrity.get("events_sha256") != _sha256(events_path):
            errors.append("product_event_journal_digest_mismatch")
        journal = (receipt.get("treatment_receipt") or {}).get("event_journal") or {}
        if (
            int(journal.get("event_count") or 0) != len(runtime_events)
            or not runtime_events
            or journal.get("event_head") != runtime_events[-1].get("event_hash")
        ):
            errors.append("product_event_journal_conservation_failed")
        try:
            observed_usage = _provider_usage(runtime_events, attempted_calls=attempted_calls)
            observed_admissions = _provider_admissions(runtime_events)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            for field in (
                "provider_completed_calls",
                "provider_failed_calls",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "total_cost",
            ):
                if observed_usage[field] != receipt.get(field):
                    errors.append(f"product_{field}_conservation_failed")
            treatment_admissions = (receipt.get("treatment_receipt") or {}).get(
                "provider_admissions"
            )
            if treatment_admissions != observed_admissions:
                errors.append("treatment_provider_admission_census_mismatch")
            admitted_calls = sum(
                row["status"] == "admitted" for row in observed_admissions
            )
            if admitted_calls != attempted_calls or any(
                row["status"] != "admitted" for row in observed_admissions
            ):
                errors.append("treatment_provider_admission_count_mismatch")

    treatment = receipt.get("treatment_receipt")
    if not isinstance(treatment, dict):
        errors.append("treatment_receipt_missing")
        return errors
    if treatment.get("schema") != "gt.miniswe_treatment_receipt.v1":
        errors.append("treatment_receipt_schema")
    if treatment.get("treatment_status") != "ACTIVE":
        errors.append("treatment_not_active")
    if treatment.get("contract_shipped") is not True:
        errors.append("treatment_contract_not_shipped")
    evidence_events = treatment.get("evidence_deliveries")
    if not isinstance(evidence_events, list):
        errors.append("treatment_deliveries_invalid")
        evidence_events = []
    prompt_events = treatment.get("prompt_context_deliveries")
    if not isinstance(prompt_events, list):
        errors.append("treatment_prompt_deliveries_invalid")
        prompt_events = []
    refused_deliveries = treatment.get("refused_deliveries")
    if not isinstance(refused_deliveries, list):
        errors.append("treatment_refused_deliveries_invalid")
        refused_deliveries = []
    deliveries = treatment.get("provider_delivery_receipts")
    if not isinstance(deliveries, list):
        errors.append("treatment_provider_deliveries_invalid")
        deliveries = []
    budget = treatment.get("delivery_budget")
    budget = budget if isinstance(budget, dict) else {}
    decision_scoped = budget.get("schema") == "gt.delivery_budget.v2"
    delivery_identities = [
        str(row.get("delivery_identity") or row.get("context_sha256") or "")
        for row in deliveries
        if isinstance(row, dict)
    ]
    if len(delivery_identities) != len(deliveries) or (
        not decision_scoped and len(delivery_identities) != len(set(delivery_identities))
    ):
        errors.append("treatment_duplicate_delivery_identity")
    if decision_scoped:
        try:
            _validate_delivery_boundaries(deliveries)
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append("treatment_delivery_boundary_invalid:" + str(exc))
    try:
        observed_delivery_events = _delivery_rows(runtime_events)
        observed_refusals = _delivery_refusals(runtime_events)
        observed_provider_deliveries = _provider_delivery_receipts(runtime_events)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if prompt_events != [row for row in observed_delivery_events if row["lane"] == "prompt"]:
            errors.append("treatment_prompt_delivery_census_mismatch")
        if evidence_events != [row for row in observed_delivery_events if row["lane"] == "sealed"]:
            errors.append("treatment_sealed_delivery_census_mismatch")
        if refused_deliveries != observed_refusals:
            errors.append("treatment_refused_delivery_census_mismatch")
        if deliveries != observed_provider_deliveries:
            errors.append("treatment_provider_delivery_census_mismatch")
    all_delivery_events = [*evidence_events, *prompt_events]
    if int(treatment.get("delivery_count") or 0) != len(deliveries) or len(deliveries) != len(
        all_delivery_events
    ):
        errors.append("treatment_delivery_count_mismatch")
    if int(treatment.get("prompt_delivery_count") or 0) != len(prompt_events):
        errors.append("treatment_prompt_delivery_count_mismatch")
    if int(treatment.get("sealed_delivery_count") or 0) != len(evidence_events):
        errors.append("treatment_sealed_delivery_count_mismatch")
    if not decision_scoped and len(deliveries) > MAX_TASK_DELIVERIES:
        errors.append("treatment_delivery_limit_exceeded")
    core_evidence = int(treatment.get("evidence_items_delivered") or 0)
    if core_evidence < 0 or core_evidence != len(evidence_events):
        errors.append("treatment_evidence_count_invalid")
    event_hashes = [
        str(row.get("event_hash") or "") for row in all_delivery_events if isinstance(row, dict)
    ]
    if len(event_hashes) != len(all_delivery_events) or len(event_hashes) != len(set(event_hashes)):
        errors.append("treatment_delivery_identity_invalid")
    elif any(not _SHA64.fullmatch(value) for value in event_hashes):
        errors.append("treatment_delivery_identity_invalid")
    total_bytes = 0
    delivered_keys: set[str] = set()
    for delivery in deliveries:
        if not isinstance(delivery, dict):
            errors.append("treatment_provider_delivery_invalid")
            continue
        lane = str(delivery.get("lane") or "")
        delivery_kind = str(delivery.get("delivery_kind") or "")
        observed = int(delivery.get("context_byte_count") or 0)
        total_bytes += observed
        delivered_keys.add(str(delivery.get("dedup_key") or ""))
        if delivery.get("same_observation") is not True:
            errors.append("treatment_delivery_late")
        if lane not in {"prompt", "sealed"}:
            errors.append("treatment_delivery_lane_invalid")
        try:
            expected_limit = delivery_byte_limit(lane=lane, kind=delivery_kind)
        except ValueError:
            expected_limit = 0
            errors.append("treatment_delivery_context_kind_invalid")
        if lane == "prompt" and delivery_kind not in {"context_contract", "context_delta"}:
            errors.append("treatment_prompt_context_budget_exceeded")
        if observed > expected_limit or delivery.get("byte_limit") != expected_limit:
            errors.append("treatment_delivery_context_budget_exceeded")
        if not _SHA64.fullmatch(str(delivery.get("context_sha256") or "")):
            errors.append("treatment_delivery_context_digest_invalid")
        if delivery.get("delivery_identity") != delivery.get("context_sha256"):
            errors.append("treatment_delivery_identity_invalid")
    if not decision_scoped and total_bytes > _TOTAL_DELIVERY_BYTE_LIMIT:
        errors.append("treatment_total_context_budget_exceeded")
    allowed_refusals = {
        "boundary_claim_ceiling",
        "request_delivery_byte_ceiling",
        "delivery_byte_ceiling",
        "task_delivery_byte_ceiling",
        "task_delivery_dose_ceiling",
        "task_delivery_storm_backstop",
        "duplicate_delivery_identity",
    }
    for refusal in refused_deliveries:
        if not isinstance(refusal, dict):
            errors.append("treatment_delivery_refusal_invalid")
            continue
        duplicate = refusal.get("reason") == "duplicate_delivery_identity"
        refusal_sequence = int(refusal.get("event_sequence") or 0)
        later_delivery = any(
            isinstance(delivery, dict)
            and int(delivery.get("event_sequence") or 0) > refusal_sequence
            and (not decision_scoped or delivery.get("observed_iteration")
                 == refusal.get("observed_iteration"))
            and (
                delivery.get("delivery_identity") == refusal.get("delivery_identity")
                or delivery.get("dedup_key") == refusal.get("dedup_key")
            )
            for delivery in deliveries
        )
        if later_delivery:
            errors.append("treatment_refused_then_delivered")
        if (
            refusal.get("reason") not in allowed_refusals
            or refusal.get("lane") not in {"prompt", "sealed"}
            or not _SHA64.fullmatch(str(refusal.get("payload_sha256") or ""))
            or refusal_sequence < 1
            or (not duplicate and any(
                isinstance(row, dict) and row.get("dedup_key") == refusal.get("dedup_key")
                and (not decision_scoped or row.get("observed_iteration")
                     == refusal.get("observed_iteration")) for row in deliveries))
            or (
                duplicate
                and str(refusal.get("delivery_identity") or "") not in set(delivery_identities)
            )
        ):
            errors.append("treatment_delivery_refusal_invalid")
    policy_valid = (
        budget.get("task_delivery_limit") is None
        and budget.get("boundary_claim_limit") == MAX_BOUNDARY_CLAIMS
        and budget.get("scope") == "provider_decision"
        and budget.get("total_limit") == TOTAL_DELIVERY_BYTE_LIMIT
    ) if decision_scoped else int(budget.get("task_delivery_limit") or 0) == MAX_TASK_DELIVERIES
    if (
        not policy_valid
        or int(budget.get("admitted_count") or 0) != len(deliveries)
        or int(budget.get("refused_count") or 0) != len(refused_deliveries)
        or int(budget.get("total_observed") or 0) != total_bytes
    ):
        errors.append("treatment_delivery_budget_conservation_failed")
    dense = treatment.get("dense_index_receipt")
    if "dense_execution_receipts" in treatment:
        dense_runs = _dense_execution_receipts(runtime_events)
        if treatment["dense_execution_receipts"] != dense_runs:
            errors.append("treatment_dense_execution_census_mismatch")
        if dense_runs and dense != dense_runs[-1]:
            errors.append("treatment_dense_execution_identity_mismatch")
    if (
        treatment.get("retrieval_mode") != "hybrid_required"
        or not isinstance(dense, dict)
        or (
            dense.get("schema") != "gt.dense_index_receipt.v1"
            or dense.get("query_ready") is not True
        )
    ):
        errors.append("treatment_dense_index_not_ready")
    identity = treatment.get("provider_identity")
    if not isinstance(identity, dict) or identity.get("match") is not True:
        errors.append("treatment_provider_identity_mismatch")
    elif (
        identity.get("requested") != receipt.get("requested_model")
        or identity.get("reported") != receipt.get("requested_model")
        or identity.get("resolved") != receipt.get("effective_model")
    ):
        errors.append("treatment_provider_route_mismatch")
    reproduction = treatment.get("reproducibility_manifest")
    if not isinstance(reproduction, dict) or reproduction.get("research_valid") is not True:
        errors.append("treatment_reproducibility_invalid")
    engine = reproduction.get("engine_integrity") if isinstance(reproduction, dict) else None
    requested_mode = treatment.get("gt_mode")
    if not (
        isinstance(requested_mode, str)
        and requested_mode in {"shadow", "advisory", "assistive", "enforced"}
        and isinstance(engine, dict)
        and engine.get("schema") == "gt.engine_integrity.v1"
        and engine.get("valid") is True
        and engine.get("mode") == requested_mode
        and reproduction.get("gt_mode") == requested_mode
        and engine.get("issues") == []
        and engine.get("disabled_stage") == ""
    ):
        errors.append("treatment_engine_integrity_invalid")
    provider = reproduction.get("provider_receipts") if isinstance(reproduction, dict) else None
    if not isinstance(provider, dict) or provider.get("valid") is not True:
        errors.append("treatment_provider_receipts_invalid")
    elif int(provider.get("request_count") or 0) != int(receipt.get("provider_calls") or 0):
        errors.append("treatment_provider_receipt_count_mismatch")
    graph = treatment.get("graph_certification")
    if not isinstance(graph, dict) or not (
        graph.get("schema") == "gt.graph_certification.v1"
        and graph.get("binary_certified") is True
        and graph.get("sqlite_quick_check") == "ok"
        and _SHA64.fullmatch(str(graph.get("graph_sha256") or ""))
    ):
        errors.append("treatment_graph_certification_invalid")
    try:
        from gt_engine.indexer import certify_graph_artifact
        from gt_harness.product import groundtruth_release

        manifest_path, collected_graph = _published_graph(state_dir, runtime_events)
        if ("graph_publication_state" in treatment
                or any(row.get("event") == "graph_publication" for row in runtime_events)):
            if treatment.get("graph_publication_state") != _graph_publication_state(runtime_events):
                errors.append("treatment_graph_publication_state_mismatch")
        if (manifest_path is None or collected_graph != graph
                or integrity.get("graph_manifest_sha256") != _sha256(manifest_path)):
            errors.append("treatment_graph_manifest_integrity_failed")
        else:
            valid, reason = certify_graph_artifact(
                manifest_path.with_name("graph.db"), manifest_path,
                expected_root_sha256=str(graph.get("repository_root_sha256") or ""),
                expected_binary_sha256=groundtruth_release()["producer_sha256"],
                expected_task_id=str(receipt.get("task_id") or ""),
                expected_product_source_sha=str(receipt.get("product_source_sha") or ""),
            )
            if not valid:
                errors.append(f"treatment_graph_artifact_invalid:{reason}")
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
        errors.append(f"treatment_graph_artifact_invalid:{exc}")
    # A repository that had source to index owes graph-derived evidence: the
    # treatment either used the mechanism under test or it did not. The exemption
    # is for a task that starts with no source at all -- there the graph fills as
    # files are created, and an empty graph is a wait state, not a failure. Keying
    # this on indexed FILES rather than nodes is deliberate: files present with no
    # nodes is a broken index, and must not be exempted as though it were empty.
    utilisation = treatment.get("graph_utilisation")
    utilisation = utilisation if isinstance(utilisation, dict) else {}
    certification = graph if isinstance(graph, dict) else {}
    try:
        graph_deliveries = _semantic_graph_deliveries(
            state_dir, runtime_events, deliveries, task_id=str(receipt.get("task_id") or ""),
            product_source_sha=str(receipt.get("product_source_sha") or ""),
        )
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        errors.append(str(exc))
        graph_deliveries = frozenset()
    expected_utilisation = graph_utilisation(
        deliveries, cochange_rows=certification.get("cochange_rows"),
        verified_graph_deliveries=graph_deliveries,
    )
    if utilisation != expected_utilisation:
        errors.append("treatment_graph_utilisation_mismatch")
    indexed_files = int(certification.get("indexed_file_count") or 0)
    if indexed_files > 0 and not utilisation.get("graph_backed_delivery"):
        errors.append("treatment_graph_evidence_absent")
    return errors


__all__ = [
    "issue_runtime_receipt_failure",
    "issue_runtime_receipts",
    "verify_runtime_receipt",
]
