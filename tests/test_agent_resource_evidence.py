from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import agent_resource_evidence as evidence


def _snapshot(oom: int, oom_kill: int) -> dict[str, object]:
    return {
        "schema": "gt.agent_resource_snapshot.v1",
        "task_id": "task-a",
        "product_source_sha": "a" * 40,
        "cgroup": {"current": 1, "max": 2, "peak": 1,
                   "oom": oom, "oom_kill": oom_kill},
    }


def test_host_interval_seals_exact_cgroup_oom(tmp_path: Path) -> None:
    output = tmp_path / "agent-resource.json"
    evidence.write_host_interval(
        output, before=_snapshot(3, 4), after=_snapshot(4, 5),
        task_id="task-a", product_source_sha="a" * 40, exit_code=137,
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
        output, before=_snapshot(3, 4), after=_snapshot(3, 4),
        task_id="task-a", product_source_sha="a" * 40, exit_code=137,
        attestation_key="f" * 64,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["error_code"] == "GT_AGENT_EXIT_137_UNATTRIBUTED"
    assert payload["memory_evidence"] is False


def test_snapshot_parser_rejects_foreign_identity() -> None:
    encoded = json.dumps(_snapshot(0, 0))
    try:
        evidence.parse_snapshot(
            encoded, task_id="foreign", product_source_sha="a" * 40
        )
    except ValueError as exc:
        assert "identity invalid" in str(exc)
    else:
        raise AssertionError("foreign snapshot identity accepted")
