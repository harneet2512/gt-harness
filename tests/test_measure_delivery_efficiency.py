from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.measure_delivery_efficiency import build_measurement


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_measurement_reads_exact_prompt_and_sealed_bytes_and_cohort_distribution(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run" / "artifact"
    state = run_root / "agent" / "gt-state" / "task"
    rendered = "[GT_TASK_CONTRACT]\nrequirement"
    payload = {
        "messages": [{"role": "user", "content": f"task\n\n{rendered}"}]
    }
    request_bytes = json.dumps(payload).encode("utf-8")
    request_digest = hashlib.sha256(request_bytes).hexdigest()
    request = state / "provider_requests" / f"{request_digest}.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_bytes(request_bytes)
    events = [
        {
            "event": "provider_delivery",
            "sequence": 1,
            "iteration": 1,
            "payload_sha256": request_digest,
            "request_blob": f"provider_requests/{request_digest}.json",
        },
        {
            "event": "evidence_delivery",
            "sequence": 2,
            "iteration": 1,
            "action_index": 1,
            "evidence_type": "syntax_result",
            "rendered_bytes": 321,
            "event_hash": "a" * 64,
        },
    ]
    state.mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )

    cohort = tmp_path / "cohort"
    for ordinal, count in enumerate((0, 4, 16), start=1):
        _write(
            cohort
            / f"gt-harness-deepswe20-task-{ordinal}-1"
            / "agent"
            / "miniswe_report.json",
            {"gt": {"delivered_evidence": count}},
        )

    receipt = build_measurement([(1, run_root.parent)], (2, cohort))

    assert receipt["runs"][0]["sealed_rendered_bytes"] == [321]
    assert receipt["runs"][0]["prompt_rendered_bytes"] == [
        len(rendered.encode("utf-8"))
    ]
    assert receipt["cohort"]["reported_distribution"] == {
        "total": 20,
        "max": 16,
        "p50": 4,
    }
    unsigned = {key: value for key, value in receipt.items() if key != "measurement_sha256"}
    expected = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert receipt["measurement_sha256"] == expected
