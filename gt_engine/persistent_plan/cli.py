"""Plan inspection and revision requests; only the engine can record evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from gt_harness.canonical_io import atomic_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gt-plan")
    parser.add_argument("operation", choices=("show", "revise", "defer", "bind-check"))
    parser.add_argument("row_id")
    parser.add_argument("--file")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        root = os.environ.get("GT_PLAN_ROOT", "")
        if not root:
            raise ValueError("GT_PLAN_ROOT is not configured")
        root = Path(root)
        state = json.loads((root / "current.json").read_text(encoding="utf-8"))
        row = next((r for r in state["rows"] if r["row_id"] == args.row_id), None)
        if row is None:
            raise ValueError("unknown plan row")
        if args.operation == "show":
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
            return 0
        value = json.loads(Path(args.file).read_text(encoding="utf-8")) if args.file else {}
        if not isinstance(value, dict):
            raise ValueError("request file must contain an object")
        if args.operation == "revise" and set(value) - {"approach", "verification_command", "verification_kind"}:
            raise ValueError("revisions cannot change requirement text or grant evidence")
        if args.operation == "defer" and not args.reason:
            raise ValueError("deferral requires a reason")
        request = {"operation": args.operation, "row_id": args.row_id,
                   "value": value, "reason": args.reason or "",
                   "plan_digest": state["plan_digest"]}
        inbox = root / "requests"
        inbox.mkdir(parents=True, exist_ok=True)
        atomic_json(inbox / (uuid.uuid4().hex + ".json"), request)
        print(json.dumps({"status": "requested", "row_id": args.row_id}))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
