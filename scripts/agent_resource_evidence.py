"""Capture cgroup snapshots for host-owned agent resource attestation."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path

from gt_engine.indexer import _cgroup_snapshot

_SHA40 = re.compile(r"[0-9a-f]{40}")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def capture_snapshot(*, task_id: str, product_source_sha: str) -> dict[str, object]:
    if not task_id or not _SHA40.fullmatch(product_source_sha):
        raise ValueError("agent resource identity invalid")
    return {
        "schema": "gt.agent_resource_snapshot.v1",
        "task_id": task_id,
        "product_source_sha": product_source_sha,
        "cgroup": _cgroup_snapshot(),
    }


def parse_snapshot(
    encoded: str, *, task_id: str, product_source_sha: str
) -> dict[str, object]:
    payload = json.loads(encoded)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "gt.agent_resource_snapshot.v1"
        or payload.get("task_id") != task_id
        or payload.get("product_source_sha") != product_source_sha
        or not isinstance(payload.get("cgroup"), dict)
    ):
        raise ValueError("agent resource snapshot identity invalid")
    return payload


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--product-source-sha", required=True)
    args = parser.parse_args()
    snapshot = capture_snapshot(
        task_id=args.task_id, product_source_sha=args.product_source_sha
    )
    print(_canonical(snapshot).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
