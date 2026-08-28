#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.decision_value_observations import observations_from_receipts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="CASE_ID=RECEIPT",
        help="bind one independent corpus case id to one finalized v2 receipt",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for value in args.run:
        case_id, separator, raw_path = value.partition("=")
        if not separator or not case_id or not raw_path:
            parser.error("--run requires CASE_ID=RECEIPT")
        path = Path(raw_path)
        rows.append((case_id, json.loads(path.read_text(encoding="utf-8"))))
    observations = observations_from_receipts(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(observations, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
