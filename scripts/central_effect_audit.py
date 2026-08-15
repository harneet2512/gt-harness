"""Audit additive GT effect provenance in one or more central receipts.

This is intentionally report-only. It never changes routing, prompts, action
execution, or controller policy.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


TERMINAL_DISPOSITIONS = {
    "provider_payload",
    "existing_engine_actuation",
    "engine_internal_state",
    "audit_only",
    "coalesced",
    "unused",
}


def audit(path: Path) -> dict[str, object]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    features = receipt.get("features", {})
    trace_present = "effect_trace" in features
    rows = features.get("effect_trace", [])
    dispositions = Counter(str(row.get("disposition")) for row in rows)
    unknown = [
        row.get("effect_id")
        for row in rows
        if row.get("disposition") not in TERMINAL_DISPOSITIONS
    ]
    missing_ids = [row.get("feature_id") for row in rows if not row.get("effect_id")]
    return {
        "receipt": str(path),
        "effect_trace_rows": len(rows),
        "dispositions": dict(sorted(dispositions.items())),
        "provider_payload_effects": dispositions.get("provider_payload", 0),
        "existing_engine_actuation_effects": dispositions.get(
            "existing_engine_actuation", 0
        ),
        "engine_internal_state_effects": dispositions.get("engine_internal_state", 0),
        "audit_only_effects": dispositions.get("audit_only", 0),
        "unknown_effects": unknown,
        "missing_effect_ids": missing_ids,
        "trace_present": trace_present,
        "valid": trace_present and bool(rows) and not unknown and not missing_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipts", nargs="+", type=Path)
    args = parser.parse_args()
    valid = True
    for path in args.receipts:
        result = audit(path)
        print(json.dumps(result, indent=2, sort_keys=True))
        valid = valid and bool(result["valid"])
    print("EFFECT_TRACE_VALID" if valid else "EFFECT_TRACE_INVALID")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
