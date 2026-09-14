"""Freeze the official DeepSWE GT-off trial slice for matched-cohort comparison.

The live artifact ``/artifacts/v1.1/trials.json`` carries every published
trial (31,617 rows). The GT-off arm of a matched comparison is the
``deepseek-v4-flash`` + ``mini-swe-agent`` slice - 452 trials across 113
tasks - frozen locally with provenance so comparison runs never depend on a
remote fetch and never silently re-baseline. Download the source once:

    curl -s https://deepswe.datacurve.ai/artifacts/v1.1/trials.json -o trials.json
    python scripts/freeze_deepswe_gtoff_trials.py \
        --source trials.json --output eval/deepswe_v4_flash_gtoff_trials.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from gt_harness.canonical_io import atomic_json, canonical_json_bytes

MODEL = "deepseek-v4-flash"
HARNESS = "mini-swe-agent"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_slice(source: Path, *, model: str = MODEL, harness: str = HARNESS) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("trials artifact has no rows array")
    trials = [
        row for row in rows
        if row.get("model") == model and row.get("harness") == harness
    ]
    if not trials:
        raise ValueError(f"no trials for {model} + {harness}")
    tasks = sorted({str(row["task_name"]) for row in trials})
    result: dict[str, Any] = {
        "schema": "gt.deepswe_gtoff_trials.v1",
        "source_locator": "https://deepswe.datacurve.ai/artifacts/v1.1/trials.json",
        "source_scope": str(payload.get("scope") or ""),
        "source_n_trials": payload.get("n_trials"),
        "source_sha256": _sha256_file(source),
        "model": model,
        "harness": harness,
        "n_tasks": len(tasks),
        "task_names": tasks,
        "trials": trials,
    }
    result["content_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--harness", default=HARNESS)
    args = parser.parse_args()
    atomic_json(args.output, freeze_slice(args.source, model=args.model, harness=args.harness))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
