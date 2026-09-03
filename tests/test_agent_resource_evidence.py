from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import agent_resource_evidence as evidence


def _snapshot(oom: int, oom_kill: int) -> dict[str, object]:
    return {
        "schema": "gt.agent_resource_snapshot.v1",
        "task_id": "task-a",
        "product_source_sha": "a" * 40,
        "cgroup": {
            "schema": "gt.host_cgroup_snapshot.v1",
            "container_id_sha256": "b" * 64,
            "cgroup_path_sha256": "c" * 64,
            "current": 1,
            "max": 2,
            "peak": 1,
            "oom": oom,
            "oom_kill": oom_kill,
        },
    }


def test_host_interval_seals_exact_cgroup_oom(tmp_path: Path) -> None:
    output = tmp_path / "agent-resource.json"
    evidence.write_host_interval(
        output,
        before=_snapshot(3, 4),
        after=_snapshot(4, 5),
        task_id="task-a",
        product_source_sha="a" * 40,
        exit_code=137,
        attestation_key="f" * 64,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    supplied = payload.pop("evidence_sha256")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert supplied == hashlib.sha256(encoded).hexdigest()
    assert payload["attestation_scope"] == "host_agent_adapter"
    assert payload["error_code"] == "GT_AGENT_CGROUP_OOM"
    assert payload["memory_evidence"] is True


def test_exit_137_without_interval_delta_is_unattributed(tmp_path: Path) -> None:
    output = tmp_path / "agent-resource.json"
    evidence.write_host_interval(
        output,
        before=_snapshot(3, 4),
        after=_snapshot(3, 4),
        task_id="task-a",
        product_source_sha="a" * 40,
        exit_code=137,
        attestation_key="f" * 64,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["error_code"] == "GT_AGENT_EXIT_137_UNATTRIBUTED"
    assert payload["memory_evidence"] is False


def test_non_137_exit_cannot_be_mislabeled_as_exit_137(tmp_path: Path) -> None:
    output = tmp_path / "agent-resource.json"
    with pytest.raises(ValueError, match="only defined for exit 137"):
        evidence.write_host_interval(
            output,
            before=_snapshot(3, 4),
            after=_snapshot(3, 4),
            task_id="task-a",
            product_source_sha="a" * 40,
            exit_code=1,
            attestation_key="f" * 64,
        )
    assert not output.exists()


def test_snapshot_capture_rejects_untrusted_cgroup_source() -> None:
    try:
        evidence.capture_snapshot(
            {"oom": 0, "oom_kill": 0}, task_id="task-a", product_source_sha="a" * 40
        )
    except ValueError as exc:
        assert "host cgroup snapshot invalid" in str(exc)
    else:
        raise AssertionError("untrusted cgroup snapshot accepted")
