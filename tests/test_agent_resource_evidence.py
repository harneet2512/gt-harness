from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from scripts import agent_resource_evidence as evidence


def test_exact_agent_interval_seals_cgroup_oom(tmp_path: Path, monkeypatch) -> None:
    snapshots = iter(
        [
            {"current": 1, "max": 2, "peak": 1, "oom": 3, "oom_kill": 4},
            {"current": 1, "max": 2, "peak": 2, "oom": 4, "oom_kill": 5},
        ]
    )
    monkeypatch.setattr(evidence, "_cgroup_snapshot", lambda: next(snapshots))
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"

    evidence.record_before(before, task_id="task-a", product_source_sha="a" * 40)
    evidence.record_after(
        before,
        after,
        task_id="task-a",
        product_source_sha="a" * 40,
        exit_code=137,
    )

    payload = json.loads(after.read_text(encoding="utf-8"))
    supplied = payload.pop("evidence_sha256")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert supplied == hashlib.sha256(encoded).hexdigest()
    assert payload["error_code"] == "GT_AGENT_CGROUP_OOM"
    assert payload["memory_evidence"] is True


def test_exit_137_without_interval_delta_is_unattributed(tmp_path: Path, monkeypatch) -> None:
    snapshot = {"current": 1, "max": 2, "peak": 1, "oom": 3, "oom_kill": 4}
    monkeypatch.setattr(evidence, "_cgroup_snapshot", lambda: dict(snapshot))
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    evidence.record_before(before, task_id="task-a", product_source_sha="a" * 40)
    evidence.record_after(
        before,
        after,
        task_id="task-a",
        product_source_sha="a" * 40,
        exit_code=137,
    )

    payload = json.loads(after.read_text(encoding="utf-8"))
    assert payload["error_code"] == "GT_AGENT_EXIT_137_UNATTRIBUTED"
    assert payload["memory_evidence"] is False


def test_before_phase_removes_stale_completed_interval(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    after.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        evidence,
        "_cgroup_snapshot",
        lambda: {"current": 1, "max": 2, "peak": 1, "oom": 0, "oom_kill": 0},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent_resource_evidence",
            "--phase",
            "before",
            "--before",
            str(before),
            "--output",
            str(after),
            "--task-id",
            "task-a",
            "--product-source-sha",
            "a" * 40,
        ],
    )

    assert evidence.main() == 0
    assert before.is_file()
    assert not after.exists()
