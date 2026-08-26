"""Emit a receipt proving source, workflow, and built-wheel surface equality."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from gt_harness.product_certification import load_product_surface, validate_product_surface


def _write_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    wheel = args.wheel.resolve()
    errors = validate_product_surface(root, wheel=wheel)
    surface = load_product_surface(root)
    receipt = {
        "schema": "gt.product_surface_verification.v1",
        "status": "PASS" if not errors else "FAIL",
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "python_modules": list(surface.python_modules),
        "dispatchable_workflows": list(surface.dispatchable_workflows),
        "forbidden_modules": list(surface.forbidden_modules),
        "errors": [error.as_dict() for error in errors],
        "provider_calls": 0,
        "provider_credentials_inspected": False,
    }
    _write_atomic(args.output.resolve(), receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
