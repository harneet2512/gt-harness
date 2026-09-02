"""Provider-free content attestation for recorded Groundtruth deliveries."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _render(fact: dict[str, Any]) -> str:
    kind = fact.get("kind")
    tag = f"[GT_EVIDENCE:{kind}]"
    if kind == "localization":
        lines = [tag]
        for row in fact["ranked_targets"]:
            lines.append(
                f"{row['path']}:{row['line']} score={row['score']} "
                f"reasons={','.join(row['reasons'])}"
            )
        return "\n".join(lines)
    if kind == "caller_contract_view":
        lines = [
            tag,
            f"{fact['subject']}() has {fact['caller_count']} production caller(s) "
            f"across {fact['file_count']} file(s)",
        ]
        lines.extend(
            f"{row['path']}:{row['line']}: note: {row['symbol']} - "
            "verify your change is consistent here"
            for row in fact["callers"]
        )
        return "\n".join(lines) + "\n"
    if kind == "new_file_destination":
        lines = [tag]
        for row in fact["precedents"]:
            lines.append(
                f"{row['path']}: advisory precedent revision={row['revision']}; "
                f"reason={','.join(row['reasons'])}; "
                f"inspect={', '.join(row['inspect'])}"
            )
        if fact.get("truncated"):
            lines.append("...(truncated by GT)")
        return "\n".join(lines)
    if kind == "trace_frame":
        return f"{tag}\n{fact['path']}:{fact['line']}\n"
    raise ValueError(f"unsupported recorded evidence kind: {kind!r}")


def _claim(fact: dict[str, Any]) -> str:
    kind = fact["kind"]
    if kind == "localization":
        targets = ",".join(
            f"{row['path']}:{row['line']}" for row in fact["ranked_targets"]
        )
        return f"ranked_repository_targets:{targets}"
    if kind == "caller_contract_view":
        return (
            f"production_callers:{fact['subject']}:{fact['caller_count']}:"
            f"{fact['file_count']}"
        )
    if kind == "new_file_destination":
        first = fact["precedents"][0]
        return f"new_file_precedents:{fact['target']}:{first['revision']}"
    if kind == "trace_frame":
        return f"failure_trace_frame:{fact['path']}:{fact['line']}"
    raise ValueError(f"unsupported recorded evidence kind: {kind!r}")


def _consequence(fact: dict[str, Any]) -> str:
    return {
        "localization": "inspect_ranked_targets_before_editing",
        "caller_contract_view": "preserve_production_caller_compatibility",
        "new_file_destination": "inspect_repository_precedents_before_adding_file",
        "trace_frame": "inspect_recorded_failure_location",
    }[fact["kind"]]


def _sealed(result: dict[str, Any]) -> dict[str, Any]:
    body = dict(result)
    body.pop("packet_digest_sha256", None)
    result["packet_digest_sha256"] = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return result


def verify_recorded_content(fixture: dict[str, Any]) -> dict[str, Any]:
    """Re-derive every recorded payload and fail closed on any inconsistency."""
    failures: list[dict[str, Any]] = []
    fixture_failures: list[str] = []
    if fixture.get("schema") != "gt.recorded_content_fixture.v1":
        fixture_failures.append("fixture_schema_invalid")
    counts = {
        "delivery_count": 0,
        "rederived_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "target_match_count": 0,
        "trigger_match_count": 0,
        "claim_match_count": 0,
        "consequence_match_count": 0,
    }
    states = {
        str(state.get("graph_state_id")): state
        for state in fixture.get("graph_states", [])
        if isinstance(state, dict)
    }
    if len(states) != len(fixture.get("graph_states", [])):
        fixture_failures.append("graph_state_identity_invalid")
    seen_deliveries: set[str] = set()
    seen_events: set[str] = set()
    actual_run_ids = [str(run.get("run_id") or "") for run in fixture.get("runs", [])]
    if actual_run_ids != fixture.get("expected_run_ids"):
        fixture_failures.append("recorded_run_set_mismatch")
    for run in fixture.get("runs", []):
        run_id = str(run.get("run_id") or "")
        if any(
            not isinstance(run.get(field), str)
            or not _SHA256.fullmatch(run[field])
            for field in ("events_sha256", "graph_manifest_sha256")
        ):
            fixture_failures.append(f"recording_digest_invalid:{run_id}")
        if run.get("recorded_shipped_delivery_count") != len(run.get("deliveries", [])):
            fixture_failures.append(f"recorded_delivery_count_mismatch:{run_id}")
        for delivery in run.get("deliveries", []):
            counts["delivery_count"] += 1
            delivery_id = str(delivery.get("delivery_id") or "")
            reasons: list[str] = []
            if not delivery_id or delivery_id in seen_deliveries:
                reasons.append("delivery_identity_invalid")
            seen_deliveries.add(delivery_id)
            source_event = delivery.get("source_event_hash")
            provider_request = delivery.get("provider_request_sha256")
            if (
                not isinstance(source_event, str)
                or not _SHA256.fullmatch(source_event)
                or source_event in seen_events
                or not isinstance(provider_request, str)
                or not _SHA256.fullmatch(provider_request)
            ):
                reasons.append("recording_provenance_invalid")
            if isinstance(source_event, str):
                seen_events.add(source_event)
            state = states.get(str(delivery.get("graph_state_id") or ""))
            fact: dict[str, Any] | None = None
            if state is None:
                reasons.append("graph_state_missing")
            else:
                fact = next(
                    (
                        row for row in state.get("facts", [])
                        if row.get("fact_id") == delivery.get("fact_id")
                    ),
                    None,
                )
            if fact is None:
                reasons.append("graph_fact_missing")
            else:
                try:
                    rendered = _render(fact)
                    counts["rederived_count"] += 1
                    actual_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                    expected_hash = delivery.get("recorded_payload_sha256")
                    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(
                        expected_hash
                    ):
                        reasons.append("recorded_payload_hash_invalid")
                    elif actual_hash != expected_hash:
                        reasons.append("payload_hash_mismatch")
                    if delivery.get("recorded_payload_bytes") != len(
                        rendered.encode("utf-8")
                    ):
                        reasons.append("payload_byte_count_mismatch")
                    target = str(delivery.get("envelope", {}).get("target") or "")
                    targets = {
                        str(row.get("path") or "") for row in state.get("targets", [])
                    }
                    if target != str(fact.get("target") or "") or target not in targets:
                        reasons.append("target_not_in_recorded_graph")
                    else:
                        counts["target_match_count"] += 1
                    recorded_trigger = str(
                        delivery.get("recorded_trigger_class") or ""
                    )
                    envelope_trigger = str(
                        delivery.get("envelope", {}).get("trigger_class") or ""
                    )
                    if not recorded_trigger or not (
                        recorded_trigger == envelope_trigger == fact.get("trigger_class")
                    ):
                        reasons.append("trigger_class_mismatch")
                    else:
                        counts["trigger_match_count"] += 1
                    if delivery.get("envelope", {}).get("canonical_claim") != _claim(fact):
                        reasons.append("canonical_claim_mismatch")
                    else:
                        counts["claim_match_count"] += 1
                    if delivery.get("envelope", {}).get("consequence") != _consequence(fact):
                        reasons.append("consequence_mismatch")
                    else:
                        counts["consequence_match_count"] += 1
                except (KeyError, TypeError, ValueError):
                    reasons.append("graph_fact_malformed")
            if reasons:
                counts["mismatch_count"] += 1
                failures.append(
                    {
                        "run_id": run_id,
                        "delivery_id": delivery_id,
                        "reason_codes": sorted(set(reasons)),
                    }
                )
            else:
                counts["match_count"] += 1
    if fixture_failures:
        failures.append(
            {
                "run_id": "fixture",
                "delivery_id": "fixture",
                "reason_codes": sorted(set(fixture_failures)),
            }
        )
    result: dict[str, Any] = {
        "schema": "gt.recorded_content_measurement.v1",
        "status": "PASS" if not failures else "FAIL",
        "provider_calls": 0,
        "run_count": len(fixture.get("runs", [])),
        **counts,
        "failures": failures,
    }
    return _sealed(result)


def measure_recorded_content(fixture: dict[str, Any]) -> dict[str, Any]:
    """Verify the cohort and prove a deterministic mutation is rejected."""
    result = verify_recorded_content(fixture)
    mutated = copy.deepcopy(fixture)
    first = mutated["runs"][0]["deliveries"][0]
    first["recorded_payload_sha256"] = "0" * 64
    mutation = verify_recorded_content(mutated)
    result.pop("packet_digest_sha256", None)
    result["mutation_case"] = {
        "mutation": "recorded_payload_sha256_zeroed",
        "outcome": "FAIL_AS_DESIGNED" if mutation["status"] == "FAIL" else "FAILED_OPEN",
        "mismatch_count": mutation["mismatch_count"],
    }
    if result["mutation_case"]["outcome"] != "FAIL_AS_DESIGNED":
        result["status"] = "FAIL"
    return _sealed(result)
