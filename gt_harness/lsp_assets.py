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


def write_lsp_manifest(root: Path) -> dict:
    """Record the staged tree so verify_lsp_assets has something to check.

    The staging step provisions the servers; without this the manifest never
    exists and every agent install fails on its absence, which is what
    happened to paid run 34046802932 - the task errored in setup before any
    model call. Generator and verifier live together so the census rule they
    share cannot drift: every file under root except the manifest itself is
    listed, so an unlisted addition or a silent removal fails the census.
    """
    root = root.resolve()
    manifest_path = root / "manifest.json"
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("lsp_asset_symlink")
        if path.is_file() and path != manifest_path:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files[path.relative_to(root).as_posix()] = digest
    if not files:
        raise ValueError("lsp_asset_files_missing")
    manifest_path.write_bytes(
        json.dumps({"schema": "gt.lsp_assets.v1", "files": files},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return verify_lsp_assets(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write", action="store_true",
                        help="generate manifest.json for the staged tree")
    parser.add_argument("--expected-manifest-sha256", default="")
    args = parser.parse_args()
    if args.write:
        print(json.dumps(write_lsp_manifest(args.root), sort_keys=True))
        return 0
    if not args.expected_manifest_sha256:
        parser.error("--expected-manifest-sha256 is required without --write")
    print(json.dumps(verify_lsp_assets(
        args.root, expected_manifest_sha256=args.expected_manifest_sha256,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
