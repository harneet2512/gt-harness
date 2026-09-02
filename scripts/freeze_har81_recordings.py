"""Freeze compact, byte-faithful A21 evidence from retained HAR-82 run artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from gt_harness.canonical_io import atomic_json

RUN_IDS = ("33563173631", "33565965241", "33567358689")
TRIGGERS = {
    "localization": "task_start",
    "caller_contract_view": "graph_retrieval",
    "new_file_destination": "repository_new_file",
    "trace_frame": "test_failure",
}
CONSEQUENCES = {
    "localization": "inspect_ranked_targets_before_editing",
    "caller_contract_view": "preserve_production_caller_compatibility",
    "new_file_destination": "inspect_repository_precedents_before_adding_file",
    "trace_frame": "inspect_recorded_failure_location",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _claim(kind: str, target: str, body: str) -> str:
    lines = body.splitlines()
    if kind == "localization":
        anchors = [line.split(" score=", 1)[0] for line in lines if " score=" in line]
        return "ranked_repository_targets:" + ",".join(anchors)
    if kind == "caller_contract_view":
        subject = lines[0].split("() has ", 1)[0]
        caller_count = lines[0].split("() has ", 1)[1].split(" production", 1)[0]
        file_count = lines[0].split(" across ", 1)[1].split(" file", 1)[0]
        return f"production_callers:{subject}:{caller_count}:{file_count}"
    if kind == "new_file_destination":
        revision = lines[0].split("revision=", 1)[1].split(";", 1)[0]
        return f"new_file_precedents:{target}:{revision}"
    if kind == "trace_frame":
        path, line = lines[0].rsplit(":", 1)
        return f"failure_trace_frame:{path}:{line}"
    raise ValueError(f"unsupported delivery kind: {kind}")


def _shipped_body(request: dict[str, Any], kind: str) -> tuple[int, str]:
    marker = f"[GT_EVIDENCE:{kind}]"
    for index in range(len(request.get("messages", [])) - 1, -1, -1):
        content = request["messages"][index].get("content")
        if not isinstance(content, str) or marker not in content:
            continue
        body = content[content.rfind(marker) + len(marker) :]
        if body.startswith("\n"):
            body = body[1:]
        for terminator in ("\n</gt-facts>", "\n\nGroundtruth", "\n\n<"):
            if terminator in body:
                body = body.split(terminator, 1)[0]
        return index, body
    raise ValueError(f"provider request does not contain {marker}")


def freeze(source: Path, output: Path) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for run_id in RUN_IDS:
        run_root = source / run_id
        events_path = next(run_root.rglob("events.jsonl"))
        state = events_path.parent
        graph_db = next(run_root.rglob("graph.db"))
        graph_manifest = graph_db.with_name("graph.manifest.json")
        provider_events = state / "provider_events.jsonl"
        reproduction = json.loads(
            (state / "reproducibility_manifest.json").read_text(encoding="utf-8")
        )
        events_bytes = events_path.read_bytes()
        event_rows = [json.loads(line) for line in events_bytes.splitlines() if line]
        run_output = output / "recorded_runs" / run_id
        _atomic_bytes(run_output / "events.jsonl", events_bytes)
        _atomic_bytes(run_output / "provider_events.jsonl", provider_events.read_bytes())
        _atomic_bytes(run_output / "graph.db.gz", gzip.compress(graph_db.read_bytes(), mtime=0))
        _atomic_bytes(run_output / "graph.manifest.json", graph_manifest.read_bytes())
        deliveries: list[dict[str, Any]] = []
        receipts = [
            row
            for row in event_rows
            if row.get("event") == "receipt" and row.get("transition") == "delivered"
        ]
        for ordinal, receipt in enumerate(receipts):
            kind = str(receipt["evidence_type"])
            delivery = next(
                row
                for row in event_rows
                if row.get("event") in {"evidence_delivery", "context_addition_delivery"}
                and row.get("iteration") == receipt.get("iteration")
                and row.get("evidence_type") == kind
            )
            provider = next(
                row
                for row in event_rows
                if row.get("event") == "provider_delivery"
                and int(row["sequence"]) > int(receipt["sequence"])
            )
            request_source = state / provider["request_blob"]
            request_bytes = request_source.read_bytes()
            if _sha256(request_bytes) != provider["payload_sha256"]:
                raise ValueError(f"provider request digest mismatch in run {run_id}")
            request = json.loads(request_bytes)
            message_index, body = _shipped_body(request, kind)
            request_target = run_output / "provider_requests" / request_source.name
            _atomic_bytes(request_target, request_bytes)
            artifact_path = ""
            if kind == "localization":
                artifact_sha = str(delivery["artifact_sha256"])
                artifact_source = state / "localization_advisory" / f"{artifact_sha}.json"
                artifact_target = run_output / "localization_advisory" / artifact_source.name
                _atomic_bytes(artifact_target, artifact_source.read_bytes())
                artifact_path = artifact_target.relative_to(output).as_posix()
            target = str(receipt.get("target") or delivery.get("target") or "")
            deliveries.append(
                {
                    "delivery_id": f"{run_id}:{ordinal}:{kind}",
                    "kind": kind,
                    "target": target,
                    "event_sequence": delivery["sequence"],
                    "event_hash": delivery["event_hash"],
                    "recorded_rendered_bytes": delivery["rendered_bytes"],
                    "receipt_sequence": receipt["sequence"],
                    "receipt_event_hash": receipt["event_hash"],
                    "receipt_payload_sha256": receipt["payload_hash"],
                    "provider_event_sequence": provider["sequence"],
                    "provider_event_hash": provider["event_hash"],
                    "provider_request_sha256": provider["payload_sha256"],
                    "provider_request_path": request_target.relative_to(output).as_posix(),
                    "provider_message_index": message_index,
                    "shipped_payload_sha256": _sha256(body.encode("utf-8")),
                    "shipped_payload_bytes": len(body.encode("utf-8")),
                    "artifact_path": artifact_path,
                    "trigger_class": TRIGGERS[kind],
                    "canonical_claim": _claim(kind, target, body),
                    "consequence": CONSEQUENCES[kind],
                }
            )
        runs.append(
            {
                "run_id": run_id,
                "events_path": (run_output / "events.jsonl").relative_to(output).as_posix(),
                "events_sha256": _sha256(events_bytes),
                "event_count": reproduction["event_journal"]["event_count"],
                "event_head": reproduction["event_journal"]["event_head"],
                "provider_events_path": (run_output / "provider_events.jsonl")
                .relative_to(output)
                .as_posix(),
                "provider_events_sha256": _sha256(provider_events.read_bytes()),
                "graph_db_gzip_path": (run_output / "graph.db.gz").relative_to(output).as_posix(),
                "graph_db_sha256": _sha256(graph_db.read_bytes()),
                "graph_manifest_path": (run_output / "graph.manifest.json")
                .relative_to(output)
                .as_posix(),
                "graph_manifest_sha256": _sha256(graph_manifest.read_bytes()),
                "deliveries": deliveries,
            }
        )
    result = {
        "schema": "gt.recorded_content_fixture.v2",
        "artifact_source": "HAR-82",
        "source_locator": str(source),
        "provider_calls_required": 0,
        "expected_run_ids": list(RUN_IDS),
        "runs": runs,
    }
    atomic_json(output / "content_recordings.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
