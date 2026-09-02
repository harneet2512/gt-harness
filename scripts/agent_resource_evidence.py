"""Seal cgroup evidence around the exact Harbor agent process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from gt_engine.indexer import _cgroup_snapshot

_SHA40 = re.compile(r"[0-9a-f]{40}")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_sealed(path: Path, payload: dict[str, object]) -> None:
    sealed = dict(payload)
    sealed["evidence_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_canonical(sealed) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_sealed(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("resource evidence must be an object")
    supplied = payload.pop("evidence_sha256", None)
    if supplied != hashlib.sha256(_canonical(payload)).hexdigest():
        raise ValueError("resource evidence seal mismatch")
    return payload


def record_before(path: Path, *, task_id: str, product_source_sha: str) -> None:
    if not task_id or not _SHA40.fullmatch(product_source_sha):
        raise ValueError("agent resource identity invalid")
    _write_sealed(
        path,
        {
            "schema": "gt.agent_resource_before.v1",
            "task_id": task_id,
            "product_source_sha": product_source_sha,
            "cgroup": _cgroup_snapshot(),
        },
    )


def record_after(
    before_path: Path,
    output_path: Path,
    *,
    task_id: str,
    product_source_sha: str,
    exit_code: int,
) -> None:
    before = _read_sealed(before_path)
    if (
        before.get("schema") != "gt.agent_resource_before.v1"
        or before.get("task_id") != task_id
        or before.get("product_source_sha") != product_source_sha
    ):
        raise ValueError("agent resource interval identity mismatch")
    first = before.get("cgroup")
    if not isinstance(first, dict):
        raise ValueError("agent resource before snapshot invalid")
    after = _cgroup_snapshot()

    def delta(name: str) -> int:
        old = first.get(name)
        new = after.get(name)
        return max(0, new - old) if type(old) is int and type(new) is int else 0

    oom_delta = delta("oom")
    oom_kill_delta = delta("oom_kill")
    memory_evidence = exit_code == 137 and (oom_delta > 0 or oom_kill_delta > 0)
    _write_sealed(
        output_path,
        {
            "schema": "gt.agent_resource.v1",
            "task_id": task_id,
            "product_source_sha": product_source_sha,
            "exit_code": exit_code,
            "memory_evidence": memory_evidence,
            "error_code": (
                "GT_AGENT_CGROUP_OOM" if memory_evidence else "GT_AGENT_EXIT_137_UNATTRIBUTED"
            ),
            "cgroup_before": first,
            "cgroup_after": after,
            "cgroup_oom_delta": oom_delta,
            "cgroup_oom_kill_delta": oom_kill_delta,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--product-source-sha", required=True)
    parser.add_argument("--exit-code", type=int)
    args = parser.parse_args()
    if args.phase == "before":
        if args.output is not None:
            args.output.unlink(missing_ok=True)
        record_before(
            args.before,
            task_id=args.task_id,
            product_source_sha=args.product_source_sha,
        )
    else:
        if args.output is None or args.exit_code is None:
            parser.error("after phase requires --output and --exit-code")
        record_after(
            args.before,
            args.output,
            task_id=args.task_id,
            product_source_sha=args.product_source_sha,
            exit_code=args.exit_code,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
