"""Issue the provider-free, producer-bound HAR-70 validation receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Permit both ``python -m scripts...`` and the documented direct script form.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.har70_framework_validation import LANGUAGES, SCHEMA, verify_har70_receipt


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("git_unavailable")
    return result.stdout.strip()


def issue(groundtruth_root: Path, output: Path) -> dict[str, Any]:
    root = groundtruth_root.resolve()
    if not (root / "gt-index" / "go.mod").is_file():
        raise RuntimeError("groundtruth_source_missing")
    producer_commit = git(root, "rev-parse", "HEAD")
    producer_tree = git(root, "rev-parse", "HEAD^{tree}")
    command = ["go", "run", "./cmd/gt-index", "-framework-validation"]
    result = subprocess.run(command, cwd=root / "gt-index", capture_output=True, text=True, check=False)
    stdout_digest = hashlib.sha256(result.stdout.encode()).hexdigest()
    stderr_digest = hashlib.sha256(result.stderr.encode()).hexdigest()
    if result.returncode != 0:
        raise RuntimeError("framework_validation_failed")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("framework_validation_invalid_json") from exc
    if report.get("schema") != "gt.framework_resolution_validation.v1":
        raise RuntimeError("framework_validation_schema")
    rows = report.get("rows")
    if not isinstance(rows, list) or {row.get("language") for row in rows} != set(LANGUAGES):
        raise RuntimeError("framework_validation_language_coverage")
    if any(row.get("certified_pairs_after", 0) <= row.get("certified_pairs_before", 0) for row in rows):
        raise RuntimeError("framework_validation_no_pair_increase")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "PASS",
        "producer": {
            "repository": "github.com/harneet2512/groundtruth",
            "root": str(root),
            "commit": producer_commit,
            "tree": producer_tree,
        },
        "manifest_languages": list(LANGUAGES),
        "languages": rows,
        "validation": {
            "command": command,
            "exit_code": result.returncode,
            "stdout_sha256": stdout_digest,
            "stderr_sha256": stderr_digest,
            "digest_sha256": report.get("validation_digest_sha256"),
        },
        "provider_calls": 0,
        "benchmark_runs": 0,
        "benchmark_ready": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    ok, reason = verify_har70_receipt(receipt)
    if not ok:
        raise RuntimeError(f"receipt_self_verify:{reason}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(canonical(receipt) + b"\n")
    os.replace(temporary, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groundtruth-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = issue(args.groundtruth_root, args.output)
    print(json.dumps({"schema": SCHEMA, "status": receipt["status"], "receipt_sha256": receipt["receipt_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
