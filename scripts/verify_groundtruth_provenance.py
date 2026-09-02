"""Verify the Groundtruth Route-B lineage exception without provider access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_harness.canonical_io import atomic_json
from gt_harness.groundtruth_provenance import verify_groundtruth_lineage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--groundtruth-checkout", type=Path, required=True)
    parser.add_argument("--review-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_groundtruth_lineage(
        args.manifest,
        groundtruth_checkout=args.groundtruth_checkout,
        review_checkout=args.review_checkout,
    )
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
