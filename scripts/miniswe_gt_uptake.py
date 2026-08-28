#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_engine.uptake_audit import audit_delivery_uptake


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--event-journal", type=Path, required=True)
    parser.add_argument("--run-receipt", type=Path, required=True)
    args = parser.parse_args()
    result = audit_delivery_uptake(
        trajectory_path=args.trajectory,
        event_journal_path=args.event_journal,
        run_receipt_path=args.run_receipt,
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
