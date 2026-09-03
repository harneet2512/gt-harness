"""Capture cgroup snapshots for host-owned agent resource attestation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path

_SHA40 = re.compile(r"[0-9a-f]{40}")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def capture_snapshot(
    cgroup: dict[str, object], *, task_id: str, product_source_sha: str
) -> dict[str, object]:
    if not task_id or not _SHA40.fullmatch(product_source_sha):
        raise ValueError("agent resource identity invalid")
    if (
        cgroup.get("schema") != "gt.host_cgroup_snapshot.v1"
        or not re.fullmatch(r"[0-9a-f]{64}", str(cgroup.get("container_id_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(cgroup.get("cgroup_path_sha256") or ""))
    ):
        raise ValueError("host cgroup snapshot invalid")
    return {
        "schema": "gt.agent_resource_snapshot.v1",
        "task_id": task_id,
        "product_source_sha": product_source_sha,
        "cgroup": cgroup,
    }


def write_host_interval(
    path: Path,
    *,
    before: dict[str, object],
    after: dict[str, object],
    task_id: str,
    product_source_sha: str,
    exit_code: int,
    attestation_key: str,
) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", attestation_key):
        raise ValueError("host attestation key invalid")
    for snapshot in (before, after):
        if (
            snapshot.get("schema") != "gt.agent_resource_snapshot.v1"
            or snapshot.get("task_id") != task_id
            or snapshot.get("product_source_sha") != product_source_sha
            or not isinstance(snapshot.get("cgroup"), dict)
        ):
            raise ValueError("agent resource interval identity mismatch")
    first = before["cgroup"]
    last = after["cgroup"]
    assert isinstance(first, dict) and isinstance(last, dict)
    for field in ("container_id_sha256", "cgroup_path_sha256"):
        if first.get(field) != last.get(field):
            raise ValueError("agent resource cgroup identity changed")

    def delta(name: str) -> int:
        old = first.get(name)
        new = last.get(name)
        return max(0, new - old) if type(old) is int and type(new) is int else 0

    oom_delta = delta("oom")
    oom_kill_delta = delta("oom_kill")
    memory_evidence = exit_code == 137 and (oom_delta > 0 or oom_kill_delta > 0)
    payload: dict[str, object] = {
        "schema": "gt.agent_resource.v1",
        "attestation_scope": "host_agent_adapter",
        "task_id": task_id,
        "product_source_sha": product_source_sha,
        "exit_code": exit_code,
        "memory_evidence": memory_evidence,
        "error_code": (
            "GT_AGENT_CGROUP_OOM" if memory_evidence else "GT_AGENT_EXIT_137_UNATTRIBUTED"
        ),
        "cgroup_before": first,
        "cgroup_after": last,
        "cgroup_oom_delta": oom_delta,
        "cgroup_oom_kill_delta": oom_kill_delta,
    }
    payload["attestation_hmac_sha256"] = hmac.new(
        bytes.fromhex(attestation_key), _canonical(payload), hashlib.sha256
    ).hexdigest()
    payload["evidence_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_canonical(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
