"""Measure historical model-visible Groundtruth delivery sizes without replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "gt.delivery_efficiency_measurement.v1"
_PROMPT_PATTERN = re.compile(
    r"(?P<body>\[(?P<tag>GT_TASK_CONTRACT|GT_OBLIGATION_DELTA)\]\n.*?)"
    r"(?=\n\n\[GT_|\Z)",
    re.DOTALL,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _single(root: Path, name: str) -> Path:
    matches = sorted(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"expected_one_{name}:{root}:{len(matches)}")
    return matches[0]


def _events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"event_object_required:{path}")
        rows.append(row)
    return rows


def _prompt_additions(
    state_dir: Path, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    additions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in sorted(
        (row for row in events if row.get("event") == "provider_delivery"),
        key=lambda row: int(row.get("sequence") or 0),
    ):
        request_blob = str(event.get("request_blob") or "")
        if not request_blob:
            continue
        request_path = state_dir / request_blob
        request = json.loads(request_path.read_text(encoding="utf-8"))
        messages = request.get("messages") if isinstance(request, dict) else None
        if not isinstance(messages, list):
            raise ValueError(f"provider_messages_required:{request_path}")
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                continue
            for match in _PROMPT_PATTERN.finditer(content):
                rendered = match.group("body")
                encoded = rendered.encode("utf-8")
                digest = hashlib.sha256(encoded).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                additions.append(
                    {
                        "iteration": int(event.get("iteration") or 0),
                        "kind": (
                            "context_contract"
                            if match.group("tag") == "GT_TASK_CONTRACT"
                            else "context_delta"
                        ),
                        "rendered_bytes": len(encoded),
                        "payload_sha256": digest,
                        "provider_payload_sha256": str(
                            event.get("payload_sha256") or ""
                        ),
                    }
                )
    return additions


def measure_run(run_id: int, root: Path) -> dict[str, Any]:
    events_path = _single(root, "events.jsonl")
    rows = _events(events_path)
    deliveries = []
    for row in rows:
        if row.get("event") != "evidence_delivery":
            continue
        deliveries.append(
            {
                "iteration": int(row.get("iteration") or 0),
                "action_index": int(row.get("action_index") or 0),
                "evidence_type": str(row.get("evidence_type") or ""),
                "rendered_bytes": int(row.get("rendered_bytes") or 0),
                "event_hash": str(row.get("event_hash") or ""),
                "payload_sha256": str(
                    row.get("payload_sha256")
                    or row.get("artifact_sha256")
                    or ""
                ),
            }
        )
    prompt = _prompt_additions(events_path.parent, rows)
    return {
        "run_id": run_id,
        "artifact_root": root.name,
        "events_sha256": _sha256(events_path),
        "sealed_deliveries": deliveries,
        "prompt_context_additions": prompt,
        "sealed_delivery_count": len(deliveries),
        "prompt_context_addition_count": len(prompt),
        "sealed_rendered_bytes": [row["rendered_bytes"] for row in deliveries],
        "prompt_rendered_bytes": [row["rendered_bytes"] for row in prompt],
    }


def measure_cohort(run_id: int, root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for artifact in sorted(path for path in root.iterdir() if path.is_dir()):
        reports = sorted(artifact.rglob("miniswe_report.json"))
        journals = sorted(artifact.rglob("events.jsonl"))
        reported = 0
        if reports:
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            gt = report.get("gt") if isinstance(report, dict) else None
            gt = gt if isinstance(gt, dict) else {}
            reported = int(gt.get("delivered_evidence") or 0)
        journal_count = 0
        if journals:
            journal_count = sum(
                row.get("event") == "evidence_delivery"
                for row in _events(journals[0])
            )
        match = re.search(r"task-(\d+)-", artifact.name)
        rows.append(
            {
                "task_ordinal": int(match.group(1)) if match else 0,
                "reported_delivery_count": reported,
                "journal_delivery_count": journal_count,
                "artifact_present": bool(reports or journals),
            }
        )
    reported_values = [row["reported_delivery_count"] for row in rows]
    journal_values = [row["journal_delivery_count"] for row in rows]
    return {
        "run_id": run_id,
        "task_count": len(rows),
        "tasks": sorted(rows, key=lambda row: row["task_ordinal"]),
        "reported_distribution": {
            "total": sum(reported_values),
            "max": max(reported_values, default=0),
            "p50": statistics.median(reported_values) if reported_values else 0,
        },
        "journal_distribution": {
            "total": sum(journal_values),
            "max": max(journal_values, default=0),
            "p50": statistics.median(journal_values) if journal_values else 0,
        },
    }


def build_measurement(
    runs: list[tuple[int, Path]], cohort: tuple[int, Path]
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "method": "recorded_artifact_read_only_no_replay",
        "runs": [measure_run(run_id, root) for run_id, root in runs],
        "cohort": measure_cohort(*cohort),
    }
    body["measurement_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def _parse_binding(value: str) -> tuple[int, Path]:
    run, separator, path = value.partition("=")
    if not separator or not run.isdigit() or not Path(path).is_dir():
        raise argparse.ArgumentTypeError("expected RUN_ID=EXISTING_DIRECTORY")
    return int(run), Path(path)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(_canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", type=_parse_binding, required=True)
    parser.add_argument("--cohort", type=_parse_binding, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    measurement = build_measurement(args.run, args.cohort)
    _atomic_json(args.output, measurement)
    print(
        json.dumps(
            {
                "schema": measurement["schema"],
                "measurement_sha256": measurement["measurement_sha256"],
                "run_count": len(measurement["runs"]),
                "cohort_task_count": measurement["cohort"]["task_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
