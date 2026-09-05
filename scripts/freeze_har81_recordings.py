"""Freeze compact, byte-faithful A21 evidence from retained HAR-82 run artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from gt_engine.request_history import load_provider_request
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
RENDERER_SOURCE_PATHS = (
    "scripts/miniswe_gt_run.py",
    "gt_engine/miniswe_integration.py",
    "gt_engine/miniswe_runtime.py",
    "config/deepswe_product_bundle_v1.json",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _git(source: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(source), *arguments])


def _git_blob_sha1(payload: bytes) -> str:
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git identity


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


def freeze(source: Path, output: Path, harness_repository: Path) -> dict[str, Any]:
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
        trial_root = state.parents[2]
        trial_config_source = trial_root / "config.json"
        run_receipt_source = state.parents[1] / "gt-run.json"
        if not run_receipt_source.is_file():
            run_receipt_source = state.parents[1] / "official-verifier-result.json"
        trial_config_bytes = trial_config_source.read_bytes()
        run_receipt_bytes = run_receipt_source.read_bytes()
        reproduction_bytes = (state / "reproducibility_manifest.json").read_bytes()
        trial_config = json.loads(trial_config_bytes)
        source_commit = str(trial_config["agent"]["kwargs"]["product_source_sha"])
        source_tree = _git(harness_repository, "rev-parse", f"{source_commit}^{{tree}}").decode().strip()
        events_bytes = events_path.read_bytes()
        event_rows = [json.loads(line) for line in events_bytes.splitlines() if line]
        receipts = [
            row
            for row in event_rows
            if row.get("event") == "receipt" and row.get("transition") == "delivered"
        ]
        has_trace = any(row.get("evidence_type") == "trace_frame" for row in receipts)
        trajectory_source = state.parents[1] / "miniswe_trajectory.json"
        trajectory_bytes = trajectory_source.read_bytes() if has_trace else b""
        run_output = output / "recorded_runs" / run_id
        _atomic_bytes(run_output / "events.jsonl", events_bytes)
        _atomic_bytes(run_output / "provider_events.jsonl", provider_events.read_bytes())
        _atomic_bytes(run_output / "graph.db.gz", gzip.compress(graph_db.read_bytes(), mtime=0))
        _atomic_bytes(run_output / "graph.manifest.json", graph_manifest.read_bytes())
        trajectory_target = run_output / "miniswe_trajectory.json"
        if has_trace:
            _atomic_bytes(trajectory_target, trajectory_bytes)
        else:
            trajectory_target.unlink(missing_ok=True)
        config_target = run_output / "provenance" / "trial-config.json"
        run_receipt_target = run_output / "provenance" / run_receipt_source.name
        reproduction_target = run_output / "provenance" / "reproducibility_manifest.json"
        _atomic_bytes(config_target, trial_config_bytes)
        _atomic_bytes(run_receipt_target, run_receipt_bytes)
        _atomic_bytes(reproduction_target, reproduction_bytes)
        source_files: list[dict[str, Any]] = []
        for repository_path in RENDERER_SOURCE_PATHS:
            source_bytes = _git(
                harness_repository, "show", f"{source_commit}:{repository_path}"
            )
            source_target = run_output / "renderer_sources" / source_commit / repository_path
            _atomic_bytes(source_target, source_bytes)
            source_files.append(
                {
                    "repository_path": repository_path,
                    "path": source_target.relative_to(output).as_posix(),
                    "bytes": len(source_bytes),
                    "sha256": _sha256(source_bytes),
                    "git_blob_sha1": _git_blob_sha1(source_bytes),
                }
            )
        deliveries: list[dict[str, Any]] = []
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
            request = load_provider_request(state, provider)
            request_bytes = json.dumps(
                request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if hashlib.sha256(request_bytes).hexdigest() != provider["payload_sha256"]:
                raise ValueError(f"provider request digest mismatch in run {run_id}")
            message_index, body = _shipped_body(request, kind)
            request_target = (
                run_output / "provider_requests" / f"{provider['payload_sha256']}.json"
            )
            _atomic_bytes(request_target, request_bytes)
            artifact_path = ""
            derivation_path = ""
            derivation_sha256 = ""
            if kind == "localization":
                artifact_sha = str(delivery["artifact_sha256"])
                artifact_source = state / "localization_advisory" / f"{artifact_sha}.json"
                artifact_target = run_output / "localization_advisory" / artifact_source.name
                _atomic_bytes(artifact_target, artifact_source.read_bytes())
                artifact_path = artifact_target.relative_to(output).as_posix()
            elif kind == "new_file_destination":
                transaction_sha = str(
                    receipt.get("transaction_sha256")
                    or delivery.get("transaction_sha256")
                    or ""
                )
                edit = next(
                    row
                    for row in event_rows
                    if row.get("event") == "edit_transaction"
                    and row.get("transaction_sha256") == transaction_sha
                )
                snapshot = max(
                    (
                        row
                        for row in event_rows
                        if row.get("event") == "repository_snapshot"
                        and row.get("boundary") == "after_action"
                        and row.get("repository_revision") == edit.get("post_revision")
                        and int(row["sequence"]) < int(edit["sequence"])
                    ),
                    key=lambda row: int(row["sequence"]),
                )
                snapshot_source = (
                    state
                    / "repository_snapshots"
                    / f"{snapshot['snapshot_sha256']}.json"
                )
                snapshot_bytes = snapshot_source.read_bytes()
                snapshot_target = run_output / "repository_snapshots" / snapshot_source.name
                _atomic_bytes(snapshot_target, snapshot_bytes)
                derivation_path = snapshot_target.relative_to(output).as_posix()
                derivation_sha256 = _sha256(snapshot_bytes)
                delivery_time_snapshot_sequence = int(snapshot["sequence"])
                edit_transaction_sequence = int(edit["sequence"])
            elif kind == "trace_frame":
                derivation_path = (
                    run_output / "miniswe_trajectory.json"
                ).relative_to(output).as_posix()
                derivation_sha256 = _sha256(trajectory_bytes)
            target = str(receipt.get("target") or delivery.get("target") or "")
            delivery_record = {
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
                    "derivation_path": derivation_path,
                    "derivation_sha256": derivation_sha256,
                    "trigger_class": TRIGGERS[kind],
                    "canonical_claim": _claim(kind, target, body),
                    "consequence": CONSEQUENCES[kind],
                }
            if kind == "new_file_destination":
                delivery_record.update(
                    {
                        "delivery_time_snapshot_sequence": delivery_time_snapshot_sequence,
                        "edit_transaction_sequence": edit_transaction_sequence,
                    }
                )
            deliveries.append(delivery_record)
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
                "renderer_provenance": {
                    "source_commit": source_commit,
                    "source_tree": source_tree,
                    "product_config_path": config_target.relative_to(output).as_posix(),
                    "product_config_sha256": _sha256(trial_config_bytes),
                    "run_receipt_path": run_receipt_target.relative_to(output).as_posix(),
                    "run_receipt_sha256": _sha256(run_receipt_bytes),
                    "reproducibility_manifest_path": reproduction_target.relative_to(output).as_posix(),
                    "reproducibility_manifest_sha256": _sha256(reproduction_bytes),
                    "source_files": source_files,
                },
                "deliveries": deliveries,
            }
        )
    result = {
        "schema": "gt.recorded_content_fixture.v3",
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
    parser.add_argument(
        "--harness-repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.source, args.output, args.harness_repository)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
