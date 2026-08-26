#!/usr/bin/env python3
"""Build a role-separated audit oracle from reviewed DeepSWE solution evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.replay_smoke20_localization import ORACLE_SCHEMA

_PATCH_PATH = re.compile(r"^\+\+\+ b/(.+?)\s*$", re.MULTILINE)
_TEST_PATH = re.compile(r"(^|/)(tests?|specs?|fixtures?)(/|\.|_)|(?:test|spec)\.", re.I)
_DOC_PATH = re.compile(r"(^|/)(readme|changelog|docs?)(/|\.|$)|\.(?:md|rst)$", re.I)
_INTEGRATION_BASENAMES = {
    "__init__.py",
    "index.js",
    "index.ts",
    "lib.rs",
    "main.go",
    "main.py",
    "package.json",
}


def _patch_paths(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for path in _PATCH_PATH.findall(text)
            if path != "/dev/null" and not path.startswith("dev/null")
        )
    )


def _role(path: str, *, exists_at_base: bool) -> str:
    if not exists_at_base:
        return "NEW_FILE_PRECEDENT"
    if _TEST_PATH.search(path):
        return "VALIDATION_OR_TEST"
    if _DOC_PATH.search(path):
        return "PUBLIC_SURFACE"
    if Path(path).name.casefold() in _INTEGRATION_BASENAMES:
        return "INTEGRATION_OR_REGISTRATION"
    return "IMPLEMENTATION_OWNER"


def _exists_at_revision(repository: Path, revision: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{revision}:{path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def build_oracle(
    manifest: dict[str, Any],
    benchmark_source: Path,
    repository_root: Path,
    *,
    reviewed_by: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    override_rows = {
        str(row.get("task_id") or ""): row
        for row in (overrides or {}).get("tasks", ())
        if isinstance(row, dict)
    }
    tasks = []
    for row in manifest.get("tasks", ()):
        task_id = str(row["task_id"])
        solution = benchmark_source / str(row["solution"])
        repository = repository_root / task_id
        if not (repository / ".git").exists():
            raise ValueError(f"{task_id}: exact-revision repository is missing: {repository}")
        grouped: dict[str, list[str]] = {}
        for path in _patch_paths(solution.read_text(encoding="utf-8")):
            grouped.setdefault(
                _role(
                    path,
                    exists_at_base=_exists_at_revision(
                        repository,
                        str(row["base_sha"]),
                        path,
                    ),
                ),
                [],
            ).append(path)
        override = override_rows.get(task_id, {})
        if override:
            if str(override.get("base_sha") or "") != str(row["base_sha"]):
                raise ValueError(f"{task_id}: override base_sha mismatch")
            for role, paths in (override.get("add_paths") or {}).items():
                for path in paths:
                    if not _exists_at_revision(repository, str(row["base_sha"]), str(path)):
                        raise ValueError(
                            f"{task_id}: override path does not exist at base revision: {path}"
                        )
                    grouped.setdefault(str(role), []).append(str(path))
        facts = []
        for role, paths in sorted(grouped.items()):
            facts.append(
                {
                    "fact_id": role.casefold().replace("_", "-"),
                    "role": role,
                    "acceptable_paths": list(dict.fromkeys(paths)),
                    # A successful implementation must localize at least one
                    # implementation owner. Other patch roles remain typed
                    # audit evidence and are not falsely declared mandatory.
                    "required": role == "IMPLEMENTATION_OWNER",
                    "evidence": [
                        f"deep-swe@{manifest['benchmark_sha']}:{row['solution']}",
                        f"manual-role-review:{reviewed_by}",
                        *(str(item) for item in override.get("evidence", ()) if str(item).strip()),
                    ],
                }
            )
        tasks.append(
            {
                "task_id": task_id,
                "base_sha": row["base_sha"],
                "review_status": "REVIEWED",
                "facts": facts,
            }
        )
    return {
        "schema": ORACLE_SCHEMA,
        "benchmark_sha": manifest["benchmark_sha"],
        "reviewed_by": reviewed_by,
        "tasks": tasks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--benchmark-source", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--overrides", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    overrides = (
        json.loads(args.overrides.read_text(encoding="utf-8"))
        if args.overrides is not None
        else None
    )
    oracle = build_oracle(
        manifest,
        args.benchmark_source.resolve(),
        args.repository_root.resolve(),
        reviewed_by=str(args.reviewed_by).strip(),
        overrides=overrides,
    )
    args.output.write_text(json.dumps(oracle, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
