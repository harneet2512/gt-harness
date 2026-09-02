"""Provider-free content attestation over retained HAR-81 run artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from gt_engine.event_journal import GENESIS_HASH, event_hash
from gt_harness.canonical_io import canonical_json_bytes

_TRIGGERS = {
    "localization": "task_start",
    "caller_contract_view": "graph_retrieval",
    "new_file_destination": "repository_new_file",
    "trace_frame": "test_failure",
}
_CONSEQUENCES = {
    "localization": "inspect_ranked_targets_before_editing",
    "caller_contract_view": "preserve_production_caller_compatibility",
    "new_file_destination": "inspect_repository_precedents_before_adding_file",
    "trace_frame": "inspect_recorded_failure_location",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sealed(result: dict[str, Any]) -> dict[str, Any]:
    body = dict(result)
    body.pop("packet_digest_sha256", None)
    result["packet_digest_sha256"] = _sha256(canonical_json_bytes(body))
    return result


def _safe_path(root: Path, relative: object) -> Path:
    candidate = (root / str(relative)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("recording_path_escape")
    return candidate


def _read_jsonl(payload: bytes) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("recording_event_not_object")
    return rows


def _verify_event_chain(
    rows: list[dict[str, Any]], *, expected_count: int, expected_head: str
) -> bool:
    parent = GENESIS_HASH
    for sequence, row in enumerate(rows, start=1):
        if (
            row.get("sequence") != sequence
            or row.get("parent_hash") != parent
            or row.get("event_hash") != event_hash(row)
        ):
            return False
        parent = str(row["event_hash"])
    return len(rows) == expected_count and parent == expected_head


def _shipped_body(request: dict[str, Any], kind: str, message_index: int) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list) or not 0 <= message_index < len(messages):
        raise ValueError("provider_message_index_invalid")
    content = messages[message_index].get("content")
    marker = f"[GT_EVIDENCE:{kind}]"
    if not isinstance(content, str) or marker not in content:
        raise ValueError("provider_payload_marker_missing")
    body = content[content.rfind(marker) + len(marker) :]
    if body.startswith("\n"):
        body = body[1:]
    for terminator in ("\n</gt-facts>", "\n\nGroundtruth", "\n\n<"):
        if terminator in body:
            body = body.split(terminator, 1)[0]
    return body


def _localization_body(artifact: dict[str, Any]) -> str:
    return "\n".join(
        f"{row['anchor']} score={row['score']} reasons={','.join(row['reasons'])}"
        for row in artifact["items"]
    )


def _caller_body(connection: sqlite3.Connection, subject: str) -> str:
    rows = connection.execute(
        """
        SELECT source.file_path, edge.source_line, source.name
        FROM edges AS edge
        JOIN nodes AS source ON source.id = edge.source_id
        JOIN nodes AS target ON target.id = edge.target_id
        WHERE target.name = ? AND edge.type = 'CALLS' AND source.is_test = 0
        ORDER BY source.file_path, edge.source_line, source.name
        """,
        (subject,),
    ).fetchall()
    files = {str(row[0]) for row in rows}
    lines = [f"{subject}() has {len(rows)} production caller(s) across {len(files)} file(s)"]
    lines.extend(
        f"{path}:{line}: note: {symbol} - verify your change is consistent here"
        for path, line, symbol in rows
    )
    return "\n".join(lines) + "\n"


def _structured_passthrough_body(kind: str, shipped: str) -> str:
    lines = shipped.splitlines()
    if kind == "trace_frame":
        if len(lines) != 1:
            raise ValueError("trace_frame_malformed")
        path, line = lines[0].rsplit(":", 1)
        int(line)
        return f"{path}:{line}\n"
    if kind == "new_file_destination":
        precedent_lines = [line for line in lines if line != "...(truncated by GT)"]
        if not precedent_lines or any(
            ": advisory precedent revision=" not in line
            or "; reason=" not in line
            or "; inspect=" not in line
            for line in precedent_lines
        ):
            raise ValueError("new_file_destination_malformed")
        return "\n".join(lines)
    raise ValueError(f"unsupported passthrough kind: {kind}")


def _claim(kind: str, target: str, body: str) -> str:
    lines = body.splitlines()
    if kind == "localization":
        anchors = [line.split(" score=", 1)[0] for line in lines]
        return "ranked_repository_targets:" + ",".join(anchors)
    if kind == "caller_contract_view":
        subject = lines[0].split("() has ", 1)[0]
        caller_count = lines[0].split("() has ", 1)[1].split(" production", 1)[0]
        file_count = lines[0].split(" across ", 1)[1].split(" file", 1)[0]
        return f"production_callers:{subject}:{caller_count}:{file_count}"
    if kind == "new_file_destination":
        revision = lines[0].split("revision=", 1)[1].split(";", 1)[0]
        return f"new_file_precedents:{target}:{revision}"
    path, line = lines[0].rsplit(":", 1)
    return f"failure_trace_frame:{path}:{line}"


def _graph_paths(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT file_path FROM file_hashes UNION SELECT file_path FROM nodes"
        )
    }


def verify_recorded_content(fixture_path: str | Path) -> dict[str, Any]:
    """Re-derive all shipped payloads from byte-retained event/graph artifacts."""
    fixture_file = Path(fixture_path).resolve()
    root = fixture_file.parent
    fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    counts = {
        "delivery_count": 0,
        "rederived_count": 0,
        "payload_match_count": 0,
        "provider_request_match_count": 0,
        "target_match_count": 0,
        "trigger_match_count": 0,
        "claim_match_count": 0,
        "consequence_match_count": 0,
        "attestation_match_count": 0,
        "mismatch_count": 0,
    }
    if fixture.get("schema") != "gt.recorded_content_fixture.v2":
        failures.append(
            {
                "run_id": "fixture",
                "delivery_id": "fixture",
                "reason_codes": ["fixture_schema_invalid"],
            }
        )
    run_ids = [str(run.get("run_id") or "") for run in fixture.get("runs", [])]
    if run_ids != fixture.get("expected_run_ids"):
        failures.append(
            {
                "run_id": "fixture",
                "delivery_id": "fixture",
                "reason_codes": ["recorded_run_set_mismatch"],
            }
        )
    for run in fixture.get("runs", []):
        run_id = str(run.get("run_id") or "")
        run_failures: list[str] = []
        try:
            events_bytes = _safe_path(root, run["events_path"]).read_bytes()
            if _sha256(events_bytes) != run["events_sha256"]:
                run_failures.append("events_digest_mismatch")
            events = _read_jsonl(events_bytes)
            if not _verify_event_chain(
                events,
                expected_count=int(run["event_count"]),
                expected_head=str(run["event_head"]),
            ):
                run_failures.append("event_stream_chain_invalid")
            provider_events = _safe_path(root, run["provider_events_path"]).read_bytes()
            if _sha256(provider_events) != run["provider_events_sha256"]:
                run_failures.append("provider_events_digest_mismatch")
            graph_gzip = _safe_path(root, run["graph_db_gzip_path"]).read_bytes()
            graph_bytes = gzip.decompress(graph_gzip)
            if _sha256(graph_bytes) != run["graph_db_sha256"]:
                run_failures.append("graph_digest_mismatch")
            graph_manifest = _safe_path(root, run["graph_manifest_path"]).read_bytes()
            if _sha256(graph_manifest) != run["graph_manifest_sha256"]:
                run_failures.append("graph_manifest_digest_mismatch")
        except (KeyError, OSError, ValueError, gzip.BadGzipFile, json.JSONDecodeError):
            run_failures.append("recorded_artifact_invalid")
            events = []
            graph_bytes = b""
        if run_failures:
            failures.append(
                {
                    "run_id": run_id,
                    "delivery_id": "run",
                    "reason_codes": sorted(set(run_failures)),
                }
            )
        if not graph_bytes:
            counts["delivery_count"] += len(run.get("deliveries", []))
            counts["mismatch_count"] += len(run.get("deliveries", []))
            continue
        with tempfile.TemporaryDirectory() as graph_directory:
            graph_path = Path(graph_directory) / "graph.db"
            graph_path.write_bytes(graph_bytes)
            connection = sqlite3.connect(graph_path)
            try:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    failures.append(
                        {
                            "run_id": run_id,
                            "delivery_id": "graph",
                            "reason_codes": ["graph_quick_check_failed"],
                        }
                    )
                graph_paths = _graph_paths(connection)
                by_sequence = {int(row.get("sequence") or 0): row for row in events}
                for delivery in run.get("deliveries", []):
                    counts["delivery_count"] += 1
                    reasons: list[str] = []
                    delivery_id = str(delivery.get("delivery_id") or "")
                    kind = str(delivery.get("kind") or "")
                    target = str(delivery.get("target") or "")
                    event = by_sequence.get(int(delivery.get("event_sequence") or 0), {})
                    receipt = by_sequence.get(int(delivery.get("receipt_sequence") or 0), {})
                    provider = by_sequence.get(
                        int(delivery.get("provider_event_sequence") or 0), {}
                    )
                    if (
                        event.get("event_hash") != delivery.get("event_hash")
                        or event.get("evidence_type") != kind
                        or receipt.get("event_hash") != delivery.get("receipt_event_hash")
                        or provider.get("event_hash") != delivery.get("provider_event_hash")
                    ):
                        reasons.append("event_stream_delivery_mismatch")
                    try:
                        request_bytes = _safe_path(
                            root, delivery["provider_request_path"]
                        ).read_bytes()
                        if _sha256(request_bytes) != delivery.get(
                            "provider_request_sha256"
                        ) or provider.get("payload_sha256") != delivery.get(
                            "provider_request_sha256"
                        ):
                            reasons.append("provider_request_digest_mismatch")
                        else:
                            counts["provider_request_match_count"] += 1
                        request = json.loads(request_bytes)
                        shipped = _shipped_body(
                            request, kind, int(delivery["provider_message_index"])
                        )
                    except (KeyError, OSError, ValueError, json.JSONDecodeError):
                        reasons.append("provider_request_invalid")
                        shipped = ""
                    try:
                        if kind == "localization":
                            artifact_bytes = _safe_path(
                                root, delivery["artifact_path"]
                            ).read_bytes()
                            artifact = json.loads(artifact_bytes)
                            artifact_name = Path(delivery["artifact_path"]).stem
                            if _sha256(artifact_bytes) != artifact_name:
                                reasons.append("payload_artifact_digest_mismatch")
                            rederived = _localization_body(artifact)
                        elif kind == "caller_contract_view":
                            subject = shipped.split("() has ", 1)[0]
                            rederived = _caller_body(connection, subject)
                        else:
                            rederived = _structured_passthrough_body(kind, shipped)
                        counts["rederived_count"] += 1
                    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                        reasons.append("payload_rederivation_failed")
                        rederived = ""
                    shipped_bytes = shipped.encode("utf-8")
                    if (
                        rederived == shipped
                        and _sha256(shipped_bytes) == delivery.get("shipped_payload_sha256")
                        and len(shipped_bytes) == delivery.get("shipped_payload_bytes")
                    ):
                        counts["payload_match_count"] += 1
                    else:
                        reasons.append("shipped_payload_mismatch")
                    target_paths = {target}
                    if kind == "new_file_destination":
                        target_paths = {
                            line.split(": advisory precedent", 1)[0]
                            for line in rederived.splitlines()
                        }
                    if target_paths and target_paths <= graph_paths:
                        counts["target_match_count"] += 1
                    else:
                        reasons.append("target_not_in_recorded_graph")
                    if delivery.get("trigger_class") == _TRIGGERS.get(kind):
                        counts["trigger_match_count"] += 1
                    else:
                        reasons.append("trigger_class_mismatch")
                    try:
                        claim = _claim(kind, target, rederived)
                    except (IndexError, ValueError):
                        claim = ""
                    if delivery.get("canonical_claim") == claim:
                        counts["claim_match_count"] += 1
                    else:
                        reasons.append("canonical_claim_mismatch")
                    if delivery.get("consequence") == _CONSEQUENCES.get(kind):
                        counts["consequence_match_count"] += 1
                    else:
                        reasons.append("consequence_mismatch")
                    if receipt.get("payload_hash") != _sha256(shipped_bytes):
                        reasons.append("receipt_payload_hash_mismatch")
                    if event.get("rendered_bytes") != len(shipped_bytes):
                        reasons.append("recorded_rendered_bytes_mismatch")
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
                        counts["attestation_match_count"] += 1
            finally:
                connection.close()
    result: dict[str, Any] = {
        "schema": "gt.recorded_content_measurement.v2",
        "status": "PASS" if not failures else "FAIL",
        "provider_calls": 0,
        "artifact_source": fixture.get("artifact_source"),
        "run_count": len(fixture.get("runs", [])),
        **counts,
        "failures": failures,
    }
    return _sealed(result)


def _mutate(path: Path, surface: str) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    run = fixture["runs"][0]
    delivery = run["deliveries"][0]
    relative = {
        "payload": delivery["artifact_path"],
        "graph": run["graph_db_gzip_path"],
        "event_stream": run["events_path"],
        "provider_request": delivery["provider_request_path"],
    }[surface]
    target = _safe_path(path.parent, relative)
    payload = bytearray(target.read_bytes())
    payload[len(payload) // 2] ^= 1
    target.write_bytes(payload)


def measure_recorded_content(fixture_path: str | Path) -> dict[str, Any]:
    """Verify retained artifacts and reproduce four independent tamper failures."""
    fixture_file = Path(fixture_path).resolve()
    result = verify_recorded_content(fixture_file)
    baseline_reasons = {
        reason for failure in result["failures"] for reason in failure["reason_codes"]
    }
    outcomes: dict[str, str] = {}
    expected = {
        "payload": {"payload_artifact_digest_mismatch", "payload_rederivation_failed"},
        "graph": {"recorded_artifact_invalid", "graph_digest_mismatch"},
        "event_stream": {"events_digest_mismatch", "event_stream_chain_invalid"},
        "provider_request": {
            "provider_request_digest_mismatch",
            "provider_request_invalid",
        },
    }
    for surface in expected:
        with tempfile.TemporaryDirectory() as temporary:
            copied_root = Path(temporary) / "fixture"
            shutil.copytree(fixture_file.parent, copied_root)
            copied_fixture = copied_root / fixture_file.name
            _mutate(copied_fixture, surface)
            mutated = verify_recorded_content(copied_fixture)
            mutated_reasons = {
                reason for failure in mutated["failures"] for reason in failure["reason_codes"]
            }
            detected = bool((mutated_reasons - baseline_reasons) & expected[surface])
            outcomes[surface] = "FAIL_AS_DESIGNED" if detected else "FAILED_OPEN"
    result.pop("packet_digest_sha256", None)
    result["mutation_cases"] = outcomes
    if any(outcome != "FAIL_AS_DESIGNED" for outcome in outcomes.values()):
        result["status"] = "FAIL"
    return _sealed(result)
