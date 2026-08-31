"""Issue a deterministic producer-owned HAR-63 explanation receipt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("gt_finalstand/receipts/har63_why_this_edge_receipt.json"),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    from gt_engine.why_this_edge import (  # noqa: PLC0415
        WhyThisEdgeStore,
        harvest_resolution_substrate,
    )

    rows = harvest_resolution_substrate(root)
    output = args.output if args.output.is_absolute() else root / args.output
    receipts = WhyThisEdgeStore(output).publish_substrate(rows)
    print({"schema": receipts[-1]["schema"], "records": len(receipts), "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
