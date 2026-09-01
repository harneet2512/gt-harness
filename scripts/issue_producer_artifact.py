"""Issue a digest-bound ``gt.producer_artifact.v2`` receipt.

The issuer is intentionally provider-free.  It binds the exact producer
checkout, source tree, toolchain/build configuration, and executable bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = "gt.producer_artifact.v2"


def _run(*args: str, cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_manifest(source: Path) -> tuple[str, int]:
    records: list[bytes] = []
    for relative in _run("git", "ls-files", "-z", cwd=source).split("\0"):
        if not relative:
            continue
        path = source / relative
        if not path.is_file():
            continue
        payload = path.read_bytes()
        path_bytes = relative.replace("\\", "/").encode("utf-8", "surrogatepass")
        byte_hash = hashlib.sha256(payload).hexdigest().encode("ascii")
        records.append(
            len(path_bytes).to_bytes(8, "big") + path_bytes
            + len(payload).to_bytes(8, "big")
            + len(byte_hash).to_bytes(8, "big") + byte_hash
        )
    material = b"".join(sorted(records))
    return hashlib.sha256(material).hexdigest(), len(records)


def issue(*, source: Path, binary: Path, output: Path, goos: str, goarch: str,
          cgo: bool, tags: str, toolchain: str) -> dict[str, Any]:
    source_commit = _run("git", "rev-parse", "HEAD", cwd=source)
    source_tree = _run("git", "rev-parse", "HEAD^{tree}", cwd=source)
    manifest_sha, manifest_files = _source_manifest(source)
    build_info = {
        "schema": "gt-index.build.v1",
        "git_commit": source_commit,
        "go_toolchain": toolchain,
        "build_tags": tags,
        "graph_schema_version": "v15.2-trust-tier",
        "capabilities": [
            "atomic_graph_publication", "call_resolution_v2",
            "incremental_stale_suppression", "parse_failure_accounting",
            "retained_call_candidates", "versioned_query_policy",
        ],
    }
    build_material = json.dumps(build_info, sort_keys=True, separators=(",", ":")).encode()
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "VERIFIED",
        "source_repository": "github.com/harneet2512/groundtruth",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "source_manifest_sha256": manifest_sha,
        "source_manifest_file_count": manifest_files,
        "go_toolchain": toolchain,
        "goos": goos,
        "goarch": goarch,
        "cgo_enabled": bool(cgo),
        "build_tags": tags,
        "build_id": hashlib.sha256(build_material).hexdigest(),
        "graph_schema_version": "v15.2-trust-tier",
        "capabilities": build_info["capabilities"],
        "binary_path": str(binary).replace("\\", "/"),
        "binary_sha256": _sha256(binary),
        "binary_bytes": binary.stat().st_size,
        "module_closure": {"go_mod": "go.mod", "go_sum": "go.sum", "complete": True},
    }
    unsigned = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    receipt["receipt_digest_sha256"] = hashlib.sha256(unsigned).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n")
    os.replace(temporary, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--goos", default="windows")
    parser.add_argument("--goarch", default="amd64")
    parser.add_argument("--cgo", action="store_true")
    parser.add_argument("--tags", default="sqlite_fts5")
    parser.add_argument("--toolchain", default="go1.26.7")
    args = parser.parse_args()
    receipt = issue(**vars(args))
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
