"""Provider-free operator entry point for canonical product acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_harness.product import run_provider_free_acceptance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fake-provider", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_provider_free_acceptance(args.manifest, output_dir=args.output)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "VERIFIED_PROVIDER_FREE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

