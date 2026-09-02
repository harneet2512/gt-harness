"""Provider-free content attestation over retained HAR-81 run artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
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


def _cap_evidence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...(truncated by GT)"


def _new_file_body(
    snapshot: dict[str, Any],
    created_files: list[str],
    revision: str,
    *,
    max_chars: int | None = 600,
) -> str:
    paths = [str(row.get("path") or "") for row in snapshot.get("files", [])]
    lines: list[str] = []
    for relative in created_files[:3]:
        suffix = os.path.splitext(relative)[1]
        if not relative or not suffix:
            continue
        directory = os.path.dirname(relative).replace("\\", "/")
        name = os.path.basename(relative)
        siblings = sorted(
            os.path.basename(path)
            for path in paths
            if os.path.dirname(path).replace("\\", "/") == directory
            and os.path.splitext(path)[1] == suffix
            and os.path.basename(path) != name
        )[:4]
        if siblings:
            lines.append(
                f"{relative}: advisory precedent revision={revision}; "
                "reason=same_directory,same_extension; inspect="
                + ", ".join(f"{directory}/{item}".lstrip("/") for item in siblings)
            )
    body = "\n".join(lines)
    return _cap_evidence(body, max_chars) if max_chars is not None else body


def _git_blob_sha1(payload: bytes) -> str:
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git identity


def _git(repository: Path, *arguments: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise ValueError(f"git_command_failed:{arguments[0]}")
    return process.stdout


def _source_repository() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".git").exists():
            return candidate
    raise ValueError("renderer_source_repository_unavailable")


def _verify_renderer_provenance(
    root: Path, run: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Bind retained runtime bytes to the exact product source revision."""

    reasons: list[str] = []
    provenance = run.get("renderer_provenance")
    if not isinstance(provenance, dict):
        return False, ["renderer_provenance_missing"]
    try:
        config_bytes = _safe_path(root, provenance["product_config_path"]).read_bytes()
        run_bytes = _safe_path(root, provenance["run_receipt_path"]).read_bytes()
        repro_bytes = _safe_path(root, provenance["reproducibility_manifest_path"]).read_bytes()
        if _sha256(config_bytes) != provenance["product_config_sha256"]:
            reasons.append("product_config_digest_mismatch")
        if _sha256(run_bytes) != provenance["run_receipt_sha256"]:
            reasons.append("run_receipt_digest_mismatch")
        if _sha256(repro_bytes) != provenance["reproducibility_manifest_sha256"]:
            reasons.append("reproducibility_manifest_digest_mismatch")
        config = json.loads(config_bytes)
        run_receipt = json.loads(run_bytes)
        repro = json.loads(repro_bytes)
        source_commit = provenance["source_commit"]
        source_repository = _source_repository()
        observed_tree = _git(
            source_repository, "rev-parse", f"{source_commit}^{{tree}}"
        ).decode("ascii").strip()
        if observed_tree != provenance["source_tree"]:
            reasons.append("renderer_source_tree_mismatch")
        configured_commit = config["agent"]["kwargs"]["product_source_sha"]
        if configured_commit != source_commit or run_receipt.get("product_source_sha") != source_commit:
            reasons.append("renderer_source_revision_mismatch")
        integrity = run_receipt.get("integrity") or {}
        if integrity and integrity.get("reproducibility_manifest_sha256") != _sha256(repro_bytes):
            reasons.append("run_to_reproducibility_digest_mismatch")
        source_files = provenance.get("source_files")
        if not isinstance(source_files, list) or not source_files:
            reasons.append("renderer_source_closure_missing")
            source_files = []
        by_repository_path: dict[str, dict[str, Any]] = {}
        for record in source_files:
            source_bytes = _safe_path(root, record["path"]).read_bytes()
            if (
                len(source_bytes) != record["bytes"]
                or _sha256(source_bytes) != record["sha256"]
                or _git_blob_sha1(source_bytes) != record["git_blob_sha1"]
            ):
                reasons.append("renderer_source_digest_mismatch")
            repository_path = str(record["repository_path"])
            committed_blob = _git(
                source_repository, "rev-parse", f"{source_commit}:{repository_path}"
            ).decode("ascii").strip()
            committed_bytes = _git(
                source_repository, "cat-file", "blob", committed_blob
            )
            if (
                committed_blob != record["git_blob_sha1"]
                or committed_bytes != source_bytes
            ):
                reasons.append("renderer_source_git_blob_mismatch")
            by_repository_path[repository_path] = record
        runner_record = by_repository_path.get("scripts/miniswe_gt_run.py")
        installed_runner = next(
            (
                row
                for row in repro.get("runner_sources", [])
                if str(row.get("path") or "").endswith("/scripts/miniswe_gt_run.py")
            ),
            None,
        )
        if (
            runner_record is None
            or installed_runner is None
            or installed_runner.get("sha256") != runner_record.get("sha256")
            or installed_runner.get("bytes") != runner_record.get("bytes")
        ):
            reasons.append("installed_renderer_source_mismatch")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        reasons.append("renderer_provenance_invalid")
    return not reasons, sorted(set(reasons))


def _trace_body(trajectory: dict[str, Any], message_index: int, target: str) -> str:
    messages = trajectory.get("messages")
    if not isinstance(messages, list) or not 0 <= message_index < len(messages):
        raise ValueError("trajectory_message_index_invalid")
    content = messages[message_index].get("content")
    if not isinstance(content, str) or messages[message_index].get("role") != "tool":
        raise ValueError("trajectory_tool_observation_invalid")
    native_output = content.split("</gt-facts>", 1)[-1]
    match = re.search(rf"(?m){re.escape(target)}:(\d+)(?::\d+)?", native_output)
    if match is None:
        raise ValueError("trace_target_absent_from_tool_observation")
    return f"{target}:{match.group(1)}\n"


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
        "renderer_provenance_match_count": 0,
        "historical_adjudication_count": 0,
        "mismatch_count": 0,
    }
    adjudications: list[dict[str, Any]] = []
    if fixture.get("schema") != "gt.recorded_content_fixture.v3":
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
        provenance_ok, provenance_reasons = _verify_renderer_provenance(root, run)
        if provenance_ok:
            counts["renderer_provenance_match_count"] += 1
        else:
            run_failures.extend(provenance_reasons)
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
                        elif kind == "new_file_destination":
                            derivation_bytes = _safe_path(
                                root, delivery["derivation_path"]
                            ).read_bytes()
                            if _sha256(derivation_bytes) != delivery.get(
                                "derivation_sha256"
                            ):
                                reasons.append("derivation_artifact_digest_mismatch")
                            snapshot = json.loads(derivation_bytes)
                            transaction_sha = str(
                                receipt.get("transaction_sha256")
                                or event.get("transaction_sha256")
                                or ""
                            )
                            edit = next(
                                row
                                for row in events
                                if row.get("event") == "edit_transaction"
                                and row.get("transaction_sha256") == transaction_sha
                            )
                            if snapshot.get("revision") != edit.get("post_revision"):
                                reasons.append("snapshot_revision_mismatch")
                            full_rederived = _new_file_body(
                                snapshot,
                                [str(path) for path in edit.get("changed_paths", [])],
                                str(edit.get("post_revision") or "unknown"),
                                max_chars=None,
                            )
                            rederived = _cap_evidence(full_rederived, 600)
                        elif kind == "trace_frame":
                            derivation_bytes = _safe_path(
                                root, delivery["derivation_path"]
                            ).read_bytes()
                            if _sha256(derivation_bytes) != delivery.get(
                                "derivation_sha256"
                            ):
                                reasons.append("derivation_artifact_digest_mismatch")
                            trajectory = json.loads(derivation_bytes)
                            rederived = _trace_body(
                                trajectory,
                                int(delivery["provider_message_index"]),
                                target,
                            )
                        else:
                            raise ValueError(f"unsupported delivery kind: {kind}")
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
                    target_proven = bool(target_paths and target_paths <= graph_paths)
                    if kind == "new_file_destination" and not target_proven:
                        try:
                            snapshot_event = by_sequence.get(
                                int(delivery["delivery_time_snapshot_sequence"]), {}
                            )
                            snapshot_bytes = _safe_path(
                                root, delivery["derivation_path"]
                            ).read_bytes()
                            delivery_snapshot = json.loads(snapshot_bytes)
                            snapshot_paths = {
                                str(row.get("path") or "")
                                for row in delivery_snapshot.get("files", [])
                            }
                            edit_sequence = int(delivery["edit_transaction_sequence"])
                            if not (
                                snapshot_event.get("event") == "repository_snapshot"
                                and snapshot_event.get("boundary") == "after_action"
                                and snapshot_event.get("snapshot_sha256")
                                == delivery.get("derivation_sha256")
                                and int(snapshot_event.get("sequence") or 0) < edit_sequence
                                < int(delivery.get("event_sequence") or 0)
                            ):
                                reasons.append("delivery_time_snapshot_order_invalid")
                            elif not target_paths <= snapshot_paths:
                                reasons.append("target_not_in_delivery_time_snapshot")
                            elif not provenance_ok:
                                reasons.append("new_file_adjudication_provenance_invalid")
                            else:
                                target_proven = True
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            reasons.append("delivery_time_snapshot_invalid")
                    if target_proven:
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
                    receipt_matches = receipt.get("payload_hash") == _sha256(shipped_bytes)
                    rendered_count_matches = event.get("rendered_bytes") == len(shipped_bytes)
                    adjudication: dict[str, Any] | None = None
                    if kind == "localization" and provenance_ok:
                        artifact_bytes = _safe_path(root, delivery["artifact_path"]).read_bytes()
                        if (
                            event.get("artifact_sha256") == _sha256(artifact_bytes)
                            and event.get("rendered_bytes") == len(artifact_bytes)
                            and receipt_matches
                        ):
                            rendered_count_matches = True
                            adjudication = {
                                "run_id": run_id,
                                "delivery_id": delivery_id,
                                "kind": kind,
                                "disposition": (
                                    "artifact_json_count_proven_by_renderer_source"
                                ),
                                "historical_event_byte_semantics": "artifact_json_utf8",
                                "model_payload_byte_semantics": "rendered_localization_utf8",
                            }
                    elif kind == "new_file_destination" and provenance_ok:
                        full_bytes = full_rederived.encode("utf-8")
                        if (
                            receipt.get("payload_hash") == _sha256(full_bytes)
                            and event.get("rendered_bytes") == len(full_bytes)
                            and shipped == _cap_evidence(full_rederived, 600)
                            and target_proven
                        ):
                            receipt_matches = True
                            rendered_count_matches = True
                            adjudication = {
                                "run_id": run_id,
                                "delivery_id": delivery_id,
                                "kind": kind,
                                "disposition": "legacy_precap_telemetry_defect_repaired",
                                "historical_event_byte_semantics": "uncapped_precedent_utf8",
                                "model_payload_byte_semantics": "capped_precedent_utf8",
                            }
                    if adjudication is not None:
                        adjudications.append(adjudication)
                        counts["historical_adjudication_count"] += 1
                    if not receipt_matches:
                        reasons.append("receipt_payload_hash_mismatch")
                    if not rendered_count_matches:
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
        "schema": "gt.recorded_content_measurement.v3",
        "status": "PASS" if not failures else "FAIL",
        "provider_calls": 0,
        "artifact_source": fixture.get("artifact_source"),
        "run_count": len(fixture.get("runs", [])),
        **counts,
        "adjudications": adjudications,
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
