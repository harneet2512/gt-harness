"""Verify the frozen HAR-81 run 19/20/21 delivery-content fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_harness.canonical_io import atomic_json
from gt_harness.recorded_content import measure_recorded_content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure_recorded_content(args.fixture)
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
