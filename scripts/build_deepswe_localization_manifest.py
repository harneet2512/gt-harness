#!/usr/bin/env python3
"""Build a portable, exact-revision localization corpus from pinned DeepSWE."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

SCHEMA = "gt.deepswe_localization_manifest.v2"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


def _load_overrides(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "gt.deepswe_revision_overrides.v1":
        raise ValueError("invalid revision override schema")
    return {str(key): str(sha) for key, sha in value.get("revisions", {}).items()}


def _exact_revision(task_id: str, value: str, overrides: dict[str, str]) -> str:
    candidate = value if len(value) == 40 else overrides.get(task_id, "")
    if len(candidate) != 40 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"{task_id}: unresolved non-exact base revision {value!r}")
    if not candidate.startswith(value):
        raise ValueError(f"{task_id}: override {candidate} does not extend {value}")
    return candidate


def build_manifest(
    source: Path,
    cohort: dict[str, Any],
    overrides: dict[str, str],
) -> dict[str, Any]:
    expected_source_sha = str(cohort.get("benchmark_sha") or "")
    actual_source_sha = _git(source, "rev-parse", "HEAD")
    if actual_source_sha != expected_source_sha:
        raise ValueError(
            f"DeepSWE source mismatch: expected {expected_source_sha}, got {actual_source_sha}"
        )
    rows = []
    for task_id in cohort.get("task_ids", ()):
        task_dir = source / "tasks" / str(task_id)
        task_toml = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        metadata = task_toml["metadata"]
        rows.append(
            {
                "task_id": str(task_id),
                "language": str(metadata["language"]),
                "repository_url": str(metadata["repository_url"]),
                "base_sha": _exact_revision(
                    str(task_id), str(metadata["base_commit_hash"]), overrides
                ),
                "instruction": f"tasks/{task_id}/instruction.md",
                "solution": f"tasks/{task_id}/solution/solution.patch",
            }
        )
    task_order = "\n".join(str(row["task_id"]) for row in rows)
    return {
        "schema": SCHEMA,
        "benchmark_repository": "https://github.com/datacurve-ai/deep-swe.git",
        "benchmark_sha": actual_source_sha,
        "task_order_sha256": hashlib.sha256(task_order.encode("utf-8")).hexdigest(),
        "tasks": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--revision-overrides", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text(encoding="utf-8"))
    manifest = build_manifest(
        args.source.resolve(), cohort, _load_overrides(args.revision_overrides)
    )
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
