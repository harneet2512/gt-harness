#!/usr/bin/env python3
"""Fail closed unless a workflow and frozen runtime are release-authorized.

The dispatch commit may follow the frozen runtime only through the two
content-addressed release files. The frozen prediction verifier owns that
ancestry and changed-path proof; this guard owns authorization and workflow
allow-list checks.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from scripts.verify_frozen_outcome_prediction import verify_release_manifest


def audit_release_workflow(
    manifest: dict,
    *,
    workflow: str,
    runtime_sha: str,
) -> list[str]:
    failures: list[str] = []
    if manifest.get("schema") not in {"gt.release_manifest.v1", "gt.release_manifest.v2"}:
        failures.append("release_manifest_schema_invalid")
    if manifest.get("benchmark_authorized") is not True:
        failures.append("benchmark_not_authorized")
    allowed = {str(item) for item in manifest.get("authorized_workflows") or ()}
    if workflow not in allowed:
        failures.append("workflow_not_authorized")
    expected_sha = str(manifest.get("runtime_commit") or "")
    if re.fullmatch(r"[0-9a-f]{40}", runtime_sha) is None:
        failures.append("runtime_sha_invalid")
    if runtime_sha != expected_sha:
        failures.append("runtime_sha_not_active_release")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="eval/release/active_release.json")
    parser.add_argument("--workflow", required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--runtime-sha")
    identity.add_argument("--current-sha")
    args = parser.parse_args()
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity_failures: list[str] = []
    runtime_sha = str(args.runtime_sha or manifest.get("runtime_commit") or "")
    if args.current_sha:
        try:
            verify_release_manifest(
                manifest_path=manifest_path,
                current_commit=args.current_sha,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            identity_failures.append(f"release_identity_invalid:{exc}")
    failures = audit_release_workflow(
        manifest,
        workflow=args.workflow,
        runtime_sha=runtime_sha,
    )
    failures = identity_failures + failures
    print(json.dumps({"status": "PASS" if not failures else "BLOCKED", "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
