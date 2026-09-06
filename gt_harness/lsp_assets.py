"""Validate the exact language-server asset tree before and after upload."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def verify_lsp_assets(root: Path, *, expected_manifest_sha256: str = "") -> dict:
    root = root.resolve()
    raw = (root / "manifest.json").read_bytes()
    identity = hashlib.sha256(raw).hexdigest()
    if expected_manifest_sha256 and identity != expected_manifest_sha256:
        raise ValueError("lsp_asset_manifest_mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema") != "gt.lsp_assets.v1":
        raise ValueError("lsp_asset_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("lsp_asset_files_missing")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("lsp_asset_symlink")
        if path.is_file() and path != root / "manifest.json":
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files):
        raise ValueError("lsp_asset_file_census_mismatch")
    for relative, digest in files.items():
        path = root / relative
        if root not in path.resolve().parents or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("lsp_asset_identity_invalid")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("lsp_asset_digest_mismatch")
    return {"schema": "gt.lsp_asset_validation.v1", "manifest_sha256": identity,
            "file_count": len(files), "verified": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(verify_lsp_assets(
        args.root, expected_manifest_sha256=args.expected_manifest_sha256,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
