#!/usr/bin/env python3
"""Verify the feature proof matrix digests and coverage."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.attribution import DIRECT_FEATURES  # noqa: E402
from gt_engine.feature_matrix import verify_matrix  # noqa: E402

DEFAULT_MATRIX = ROOT / "gt_finalstand" / "feature_matrix.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "matrix",
        nargs="?",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Path to gt.feature_matrix.v2 JSON",
    )
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
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
        require_witnessed=True,
    )
    if errors:
        for issue in errors:
            print(issue, file=sys.stderr)
        return 1
    rows = matrix.get("rows") or []
    witnessed = sum(1 for row in rows if row.get("disposition") == "WITNESSED")
    print(
        f"OK: {len(rows)}/{len(DIRECT_FEATURES)} identities; "
        f"{witnessed} witnessed cells"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
