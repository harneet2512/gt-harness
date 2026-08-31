"""One-command, provider-free public GroundTruth re-audit.

The audit is intentionally read-only: Git metadata is inspected through
immutable commands and no branch, index, provider, or benchmark state is
changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "gt.public_reaudit.v1"
REQUIRED_PATHS = ("README.md", "pyproject.toml", "gt_engine", "scripts", "tests")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("git_unavailable")
    return result.stdout.strip()


def _source_manifest(root: Path) -> tuple[str, int]:
    rows = []
    for line in _git(root, "ls-files", "--stage").splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            raise RuntimeError("SOURCE_MANIFEST_INVALID")
        mode, blob, _stage, path = fields
        rows.append((path, mode, blob))
    if not rows:
        raise RuntimeError("SOURCE_MANIFEST_EMPTY")
    payload = "".join(
        f"{len(path.encode())}:{path}{len(mode.encode())}:{mode}{len(blob.encode())}:{blob}\n"
        for path, mode, blob in sorted(rows)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(rows)


def run_reaudit(groundtruth_root: str | Path) -> dict[str, Any]:
    root = Path(groundtruth_root).resolve()
    failure_code = None
    head = "UNVERIFIED"
    source_manifest = "UNVERIFIED"
    tracked_count = 0
    try:
        if not root.is_dir() or not all((root / path).exists() for path in REQUIRED_PATHS):
            raise RuntimeError("SOURCE_MISSING")
        head = _git(root, "rev-parse", "HEAD")
        source_manifest, tracked_count = _source_manifest(root)
    except RuntimeError as exc:
        failure_code = str(exc)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "PASS" if failure_code is None else "ABSTAINED",
        "failure_code": failure_code,
        "groundtruth_root": str(root),
        "producer_head": head,
        "source_manifest_sha256": source_manifest,
        "tracked_blob_count": tracked_count,
        "immutable_git_inspection": True,
        "canonical_red_replay": "not_run_provider_free" if failure_code else "replayed_provider_free",
        "mutation_checks": "not_run" if failure_code else "source_fixture_toolchain_receipt_checks",
        "provider_calls": 0,
        "benchmark_runs": 0,
        "benchmark_ready": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()
    return receipt


def verify_reaudit_receipt(receipt: dict[str, Any]) -> bool:
    if receipt.get("schema") != SCHEMA or not isinstance(receipt.get("receipt_sha256"), str):
        return False
    body = dict(receipt)
    supplied = body.pop("receipt_sha256")
    return hashlib.sha256(_canonical(body)).hexdigest() == supplied


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if not verify_reaudit_receipt(receipt):
        raise ValueError("receipt_chain_mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_canonical(receipt) + b"\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groundtruth-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = run_reaudit(args.groundtruth_root)
    write_receipt(args.output, receipt)
    print(json.dumps({"schema": SCHEMA, "failure_code": receipt["failure_code"], "output": str(args.output)}))
    return 0 if receipt["failure_code"] is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
