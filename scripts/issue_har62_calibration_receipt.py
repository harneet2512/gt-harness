"""Issue the HAR-62 report from the checked-in capability machinery."""

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
        default=Path("gt_finalstand/receipts/har62_trust_calibration_report_v2.json"),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    from gt_engine.trust_calibration_report import (  # noqa: PLC0415
        collect_from_shipped_machinery,
        emit_trust_calibration_receipt,
    )
    output = args.output if args.output.is_absolute() else root / args.output
    observations = collect_from_shipped_machinery(root)
    report = emit_trust_calibration_receipt(observations, output)
    print({"schema": report["schema"], "observations": len(observations), "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
