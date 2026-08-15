#!/usr/bin/env python3
"""Prepare gold-free ARB V2 inputs for the benchmark-only GT adapter.

This script reads official ARB samples, projects only the query signal that a
runtime retriever is allowed to see, and writes one redacted JSONL file per
release.  It never writes gold, fix commits, patches, evaluator labels, or
expected paths to the adapter input.  The original ARB release remains the
offline evaluation source and is joined only after prediction generation.

The script intentionally does not materialize repositories or build indexes.
The adapter must run against a lossless checkout at each ``(repo,
base_commit)`` pair; ARB's chunk corpus is a candidate/evaluation corpus and
is not a replacement for the production graph workspace.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PRIMARY_RELEASES = (
    "v2_code2test",
    "v2_comment2context",
    "v2_trace2code",
    "v2_edit2ripple",
    "v2_abstention",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _query(row: dict[str, Any]) -> dict[str, Any]:
    query = row.get("query")
    if not isinstance(query, dict):
        raise ValueError(f"{row.get('id')}: query must be an object")
    return query


def _join(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _projection(row: dict[str, Any]) -> tuple[str, list[str]]:
    """Return the official query signal and explicitly given paths only."""

    task_type = _text(row.get("task_type"))
    query = _query(row)
    if task_type == "code2test":
        instruction = _join(
            [
                _text(query.get("pr_title")),
                _text(query.get("pr_body")),
                _text(query.get("changed_file_summary")),
            ]
        )
        active_paths = [_text(query.get("changed_file"))]
    elif task_type == "comment2context":
        instruction = _join(
            [
                _text(query.get("pr_title")),
                _text(query.get("review_comment")),
                _text(query.get("diff_hunk_context")),
            ]
        )
        active_paths = [_text(query.get("given_file") or query.get("path"))]
    elif task_type == "trace2code":
        instruction = _join(
            [
                _text(query.get("command")),
                _text(query.get("failure_excerpt")),
                _text(query.get("run_strategy")),
                _text(query.get("source_type")),
            ]
        )
        active_paths = []
    elif task_type == "edit2ripple":
        instruction = _join([_text(query.get("intent")), _text(query.get("anchor_diff"))])
        active_paths = [_text(query.get("anchor_file"))]
    elif task_type == "abstention":
        instruction = _text(query.get("text"))
        active_paths = []
    else:
        raise ValueError(f"{row.get('id')}: unsupported ARB task_type {task_type!r}")

    instruction = instruction.strip()
    if not instruction:
        raise ValueError(f"{row.get('id')}: projected instruction is empty")
    return instruction, [path.replace("\\", "/") for path in active_paths if path]


def project_row(row: dict[str, Any]) -> dict[str, Any]:
    instruction, active_paths = _projection(row)
    return {
        "sample_id": _text(row.get("id")),
        "repository": _text(row.get("repo")),
        "base_commit": _text(row.get("base_commit")),
        "task_type": _text(row.get("task_type")),
        "instruction": instruction,
        "active_paths": active_paths,
    }


def project_release(input_path: Path, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with input_path.open(encoding="utf-8") as source, output_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as target:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{input_path}:{line_number}: expected object")
            projected = project_row(row)
            if (
                not projected["sample_id"]
                or not projected["repository"]
                or not projected["base_commit"]
            ):
                raise ValueError(f"{input_path}:{line_number}: missing sample identity")
            target.write(json.dumps(projected, sort_keys=True, separators=(",", ":")))
            target.write("\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--release", action="append", choices=PRIMARY_RELEASES)
    args = parser.parse_args()
    releases = args.release or list(PRIMARY_RELEASES)
    total = 0
    for release in releases:
        input_path = args.data_dir / "benchmark" / release / "samples.jsonl"
        output_path = args.out_dir / f"{release}.redacted.jsonl"
        if not input_path.exists():
            raise FileNotFoundError(input_path)
        count = project_release(input_path, output_path)
        print(f"{release}: {count} redacted rows -> {output_path}")
        total += count
    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
