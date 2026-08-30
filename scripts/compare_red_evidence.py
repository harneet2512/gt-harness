"""Join independently captured RED artifacts by canonical body identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "gt.red_evidence.compare.v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare(first: str | Path, second: str | Path) -> dict[str, Any]:
    roots = [Path(first), Path(second)]
    errors: list[str] = []
    bodies: list[bytes] = []
    receipts: list[dict[str, Any]] = []
    for index, root in enumerate(roots, start=1):
        try:
            body = (root / "canonical.txt").read_bytes()
            receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            errors.append(f"runner-{index}:invalid_artifact")
            continue
        bodies.append(body)
        receipts.append(receipt)
        diagnostic = receipt.get("diagnostic") if isinstance(receipt, dict) else None
        if not isinstance(diagnostic, dict) or diagnostic.get("sha256") != _sha256(body):
            errors.append(f"runner-{index}:canonical_hash_mismatch")
        runner = receipt.get("runner") if isinstance(receipt, dict) else None
        if not isinstance(runner, dict) or not all(
            isinstance(runner.get(key), str) and runner[key]
            for key in ("architecture", "image_label", "image_version", "os_release_sha256")
        ):
            errors.append(f"runner-{index}:invalid_identity")
    if len(bodies) == 2 and bodies[0] != bodies[1]:
        errors.append("canonical_body_mismatch")
    if len(receipts) == 2:
        first_tool = receipts[0].get("toolchain", {})
        second_tool = receipts[1].get("toolchain", {})
        if first_tool.get("text") != second_tool.get("text"):
            errors.append("toolchain_version_mismatch")
        if first_tool.get("executable", {}).get("sha256") != second_tool.get("executable", {}).get(
            "sha256"
        ):
            errors.append("toolchain_executable_mismatch")
        runner_keys = ("architecture", "image_label", "image_version", "os_release_sha256")
        first_runner = receipts[0].get("runner", {})
        second_runner = receipts[1].get("runner", {})
        if tuple(first_runner.get(key) for key in runner_keys) == tuple(
            second_runner.get(key) for key in runner_keys
        ):
            errors.append("runner_identities_not_distinct")
        first_runtime = receipts[0].get("capture_runtime", {})
        second_runtime = receipts[1].get("capture_runtime", {})
        if first_runtime.get("python_version") != second_runtime.get("python_version"):
            errors.append("python_version_mismatch")
    digest = _sha256(bodies[0]) if len(bodies) == 2 and bodies[0] == bodies[1] else None
    return {
        "schema": SCHEMA,
        "status": "pass" if not errors and len(bodies) == 2 else "fail",
        "errors": sorted(set(errors)),
        "canonical_sha256": digest,
        "runner_count": len(bodies),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first")
    parser.add_argument("second")
    args = parser.parse_args()
    report = compare(args.first, args.second)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
