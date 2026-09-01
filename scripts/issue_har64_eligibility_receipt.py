"""Issue the shipped HAR-64 eligibility receipt through the receipt boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.evidence_router import (  # noqa: E402 - direct-script path bootstrap
    build_eligibility_receipt,
    verify_eligibility_receipt,
)

DEFAULT_OUTPUT = ROOT / "gt_finalstand" / "receipts" / "har64_eligibility_receipt.json"


def issue(output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    """Build and atomically publish a deterministic, source-bound receipt."""
    baseline = {"messages": ["task: inspect the repository"]}
    admitted = "gt-evidence: certified callsite facts"
    final = {"messages": [baseline["messages"][0], admitted]}
    receipt = build_eligibility_receipt(
        decision_id="har64-fixture-decision",
        iteration_id="har64-fixture-iteration",
        claims=[
            {
                "claim_id": "claim-0001",
                "source": "gt.resolution_v2",
                "content": admitted,
                "disposition": "admitted",
                "reason": "task_relevant",
            },
            {
                "claim_id": "claim-0002",
                "source": "gt.legacy_fixture",
                "content": "refused-unrelated-context",
                "disposition": "refused",
                "reason": "outside_task_scope",
            },
        ],
        baseline_request=baseline,
        final_request=final,
    )
    if not verify_eligibility_receipt(receipt):
        raise RuntimeError("eligibility_receipt_unverified")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    receipt = issue(args.output)
    print(json.dumps({"schema": receipt["schema"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
