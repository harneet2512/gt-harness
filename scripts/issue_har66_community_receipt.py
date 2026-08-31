"""Issue a deterministic HAR-66 community certificate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path,
        default=Path("gt_finalstand/receipts/har66_community_certificate_v2.json"),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    from gt_engine.repository_intelligence import (  # noqa: PLC0415
        CommunityEdge,
        build_leiden_communities,
        emit_community_receipt,
    )
    run = build_leiden_communities(
        nodes=("har66-a", "har66-b", "har66-c"),
        edges=(
            CommunityEdge("har66-a", "har66-b", "CALL", "verified", "har66-e1"),
            CommunityEdge("har66-b", "har66-c", "CALL", "candidate", "har66-e2"),
        ),
        revision="har66-fixture-revision", seed=2512,
    )
    output = args.output if args.output.is_absolute() else root / args.output
    receipt = emit_community_receipt(run, output)
    print({"schema": receipt["schema"], "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
