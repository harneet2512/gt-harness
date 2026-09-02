"""Extract the immutable HAR-82 Muse baseline slice without provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from gt_harness.canonical_io import atomic_json, canonical_json_bytes

EXPECTED_SOURCE_SHA256 = {
    "SUMMARY.json": "d39cc6b6fc4c1827d4aad635cc91cbb3a18ec96ca51b4beec915cd1b89b89036",
    "muse-spark-1.2_per-task-comparison.json": (
        "48336a5a102242cbf9cb7a01030543f31ba28bed02d3a0a87415345cc05fd3fa"
    ),
    "muse-spark-1.2_trials-all-452.json": (
        "ea4f001474d37eeae1fde4ee9020f2a8834588db6fb7d33b053c75c46a1e5d02"
    ),
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_slice(source: Path, manifest_path: Path) -> dict[str, Any]:
    for name, expected in EXPECTED_SOURCE_SHA256.items():
        observed = _sha256_file(source / name)
        if observed != expected:
            raise ValueError(f"HAR-82 source digest mismatch for {name}: {observed}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    comparison = json.loads(
        (source / "muse-spark-1.2_per-task-comparison.json").read_text(encoding="utf-8")
    )
    by_task = {str(row["task_name"]): row for row in comparison["tasks"]}
    task_ids = [str(task_id) for task_id in manifest["task_ids"]]
    missing = [task_id for task_id in task_ids if task_id not in by_task]
    if missing:
        raise ValueError(f"HAR-82 baseline is missing smoke tasks: {missing}")
    tasks = [by_task[task_id] for task_id in task_ids]
    if any(
        row.get("aggregate", {}).get("trials") != 4 or len(row.get("trials", [])) != 4
        for row in tasks
    ):
        raise ValueError("HAR-82 smoke slice does not contain four trials per task")
    result: dict[str, Any] = {
        "schema": "gt.deepswe_muse_baseline.v1",
        "source_locator": "HAR-82",
        "source_directory_name": "muse-spark-1.2_DeepSWE_v1.1",
        "source_sha256": EXPECTED_SOURCE_SHA256,
        "benchmark_release": "v1.1",
        "declared_model": "meta/muse-spark-1.2-contributor",
        "effective_route": "openai/meta/muse-spark-1.2-contributor",
        "provider": "openrouter",
        "source_model": "muse-spark-1-2",
        "source_config": "mini_swe_agent_muse_spark_1_2_xhigh",
        "task_order_sha256": manifest["task_order_sha256"],
        "tasks": tasks,
    }
    result["content_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    atomic_json(args.output, build_slice(args.source, args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
