"""Prove the vendored indexer source is the source of the indexer that runs.

The product executes a committed producer binary, not the binary CI builds from
``vendor/gt-index-src``.  CI compiles that source only to prove it compiles, so
nothing has ever compared the two.  When they drift, the repository holds a
source tree that is never executed and a binary that cannot be rebuilt from
anything present -- and graph capability added to the source silently fails to
reach production.

This check makes that drift loud.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

VENDORED_SOURCE = "vendor/gt-index-src"
SPEC_DIR = Path(VENDORED_SOURCE) / "internal" / "specs"


def _git_tree(path: str, *, root: Path) -> str:
    """Return the git tree hash of a tracked directory at HEAD."""

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _object_present(oid: str, *, root: Path) -> bool:
    if not oid:
        return False
    return subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{oid}^{{}}"],
        capture_output=True,
        check=False,
    ).returncode == 0


def language_specs(root: Path) -> list[str]:
    """Languages the vendored source can parse, as a measurement."""

    spec_dir = root / SPEC_DIR
    if not spec_dir.is_dir():
        return []
    skip = {"spec.go"}
    return sorted(
        path.stem
        for path in spec_dir.glob("*.go")
        if path.name not in skip and not path.name.endswith("_test.go")
    )


def evaluate(bundle: dict, *, root: Path) -> tuple[dict, list[str]]:
    """Return a measured binding report and the reasons it is not verified."""

    groundtruth = bundle.get("groundtruth") or {}
    build = groundtruth.get("producer_build") or {}
    failures: list[str] = []

    pinned_tree = str(build.get("source_tree") or "")
    pinned_commit = str(build.get("source_commit") or "")
    actual_tree = _git_tree(VENDORED_SOURCE, root=root)

    if not pinned_tree:
        failures.append("producer_build_source_tree_missing")
    elif not actual_tree:
        failures.append("vendored_source_not_tracked")
    elif pinned_tree != actual_tree:
        failures.append("vendored_source_tree_mismatch")

    if pinned_commit and not _object_present(pinned_commit, root=root):
        failures.append("producer_source_commit_absent_from_repository")

    binary_path = root / str(groundtruth.get("producer_path") or "")
    declared_binary = str(groundtruth.get("producer_sha256") or "")
    measured_binary = ""
    if binary_path.is_file():
        measured_binary = hashlib.sha256(binary_path.read_bytes()).hexdigest()
        if declared_binary and measured_binary != declared_binary:
            failures.append("producer_binary_digest_mismatch")
    else:
        failures.append("producer_binary_missing")

    specs = language_specs(root)
    report = {
        "schema": "gt.producer_source_binding.v1",
        "status": "VERIFIED" if not failures else "UNVERIFIED",
        "pinned_source_tree": pinned_tree,
        "actual_source_tree": actual_tree,
        "pinned_source_commit": pinned_commit,
        "producer_sha256": measured_binary,
        "vendored_language_count": len(specs),
        "vendored_languages": specs,
        "failures": failures,
    }
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default="config/deepswe_product_bundle_v1.json")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit nonzero when the binding is unverified.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    bundle = json.loads((root / args.bundle).read_text(encoding="utf-8"))
    report, failures = evaluate(bundle, root=root)

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    if failures and args.enforce:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
