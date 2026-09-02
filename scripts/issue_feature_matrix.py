#!/usr/bin/env python3
"""Issue the feature proof matrix and bind it atomically."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.feature_matrix import (  # noqa: E402
    build_matrix,
    render_markdown,
    verify_matrix,
)

JSON_OUT = ROOT / "gt_finalstand" / "feature_matrix.json"
MD_OUT = ROOT / "gt_finalstand" / "FEATURE_MATRIX.md"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build matrix without executing bound pytest evidence",
    )
    args = parser.parse_args()
    matrix = build_matrix(repo_root=ROOT, execute=not args.dry_run)
    checkout_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    errors = verify_matrix(
        matrix,
        expected_source_revision=checkout_head,
    )
    if errors:
        for issue in errors:
            print(issue, file=sys.stderr)
        return 1
    encoded = json.dumps(matrix, sort_keys=True, separators=(",", ":"), indent=2)
    _atomic_write(JSON_OUT, (encoded + "\n").encode())
    _atomic_write(MD_OUT, (render_markdown(matrix) + "\n").encode())
    print(JSON_OUT)
    print(MD_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
