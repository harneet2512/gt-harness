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
    from gt_engine.why_this_edge import WhyThisEdgeStore  # noqa: PLC0415

    facts = {
        "edge_id": "har63-fixture-edge",
        "callsite_id": "har63-fixture-callsite",
        "edge_kind": "SELECTED_TARGET",
        "target_id": "har63-fixture-target",
        "dispatch_state": "unique",
        "candidate_count": 1,
        "candidates": [{"target_id": "har63-fixture-target", "flow_witnesses": ["har63-flow"]}],
        "flow_witnesses": {"har63-fixture-target": ["har63-flow"]},
        "source_revision": "har63-fixture-source",
        "graph_revision": "har63-fixture-graph",
        "completion_identity": "har63-fixture-build",
        "complete": True,
    }
    output = args.output if args.output.is_absolute() else root / args.output
    receipt = WhyThisEdgeStore(output).publish(facts)
    print({"schema": receipt["schema"], "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
