#!/usr/bin/env python3
"""Issue the feature proof matrix and bind it atomically."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.feature_matrix import (  # noqa: E402
    SCHEMA,
    build_matrix,
    digest_body,
    render_markdown,
    verify_matrix,
)
from gt_harness.canonical_io import atomic_json, atomic_write  # noqa: E402

JSON_OUT = ROOT / "gt_finalstand" / "feature_matrix.json"
MD_OUT = ROOT / "gt_finalstand" / "FEATURE_MATRIX.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build matrix without executing bound pytest evidence",
    )
    parser.add_argument("--output", type=Path, default=JSON_OUT)
    parser.add_argument("--markdown-output", type=Path, default=MD_OUT)
    args = parser.parse_args()
    checkout_head = ""
    try:
        checkout_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        matrix = build_matrix(repo_root=ROOT, execute=not args.dry_run)
    except Exception as exc:
        # A failed issuer must not leave an older proof in the output slot.
        matrix = {
            "schema": SCHEMA,
            "source_revision": checkout_head,
            "generated_at": datetime.now(UTC).isoformat(),
            "identity_count": 0,
            "rows": [],
            "issuance_error": {"type": type(exc).__name__},
        }
        matrix["matrix_digest_sha256"] = digest_body(matrix, field="matrix_digest_sha256")
    atomic_json(args.output, matrix)
    atomic_write(args.markdown_output, (render_markdown(matrix) + "\n").encode())
    errors = verify_matrix(
        matrix,
        expected_source_revision=checkout_head,
        require_witnessed=not args.dry_run,
    )
    if errors:
        for issue in errors:
            print(issue, file=sys.stderr)
        return 1
    print(args.output)
    print(args.markdown_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
