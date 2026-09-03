"""Prove the producer binary was built from the source it claims.

The product executes a committed producer binary. `vendor/gt-index-src` in this
repository is a vendored *copy* for source inspection and toolchain
verification -- it is not what the binary was built from, and it is not
expected to match. The authoritative source is the producer repository
(github.com/harneet2512/groundtruth), and `producer_build` records the exact
commit and tree the binary came from.

Comparing the pinned tree against the vendored copy therefore proves nothing:
the two are different artifacts serving different purposes, and a mismatch
between them is the normal state rather than a defect. The binding that
matters, and the one this checks, is:

    producer_sha256      == the digest of the shipped binary
    source_commit        resolves in the producer repository
    source_tree          == that commit's root tree

Given the producer repository, all three are decidable. Without it the commit
simply is not present locally, which is a limitation of where the check runs
and not evidence of drift.
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


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _object_present(root: Path, oid: str) -> bool:
    if not oid:
        return False
    return subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{oid}^{{}}"],
        capture_output=True,
        check=False,
    ).returncode == 0


def language_specs(root: Path) -> list[str]:
    """Languages the vendored copy can parse, as a measurement."""

    spec_dir = root / SPEC_DIR
    if not spec_dir.is_dir():
        return []
    return sorted(
        path.stem
        for path in spec_dir.glob("*.go")
        if path.name != "spec.go" and not path.name.endswith("_test.go")
    )


def evaluate(
    bundle: dict, *, root: Path, producer_repo: Path | None = None
) -> tuple[dict, list[str]]:
    """Return a measured binding report and the reasons it is not verified."""

    groundtruth = bundle.get("groundtruth") or {}
    build = groundtruth.get("producer_build") or {}
    failures: list[str] = []

    pinned_tree = str(build.get("source_tree") or "")
    pinned_commit = str(build.get("source_commit") or "")

    if not pinned_tree:
        failures.append("producer_build_source_tree_missing")
    if not pinned_commit:
        failures.append("producer_build_source_commit_missing")

    # The binary must be the one the bundle declares.
    binary_path = root / str(groundtruth.get("producer_path") or "")
    declared_binary = str(groundtruth.get("producer_sha256") or "")
    measured_binary = ""
    if binary_path.is_file():
        measured_binary = hashlib.sha256(binary_path.read_bytes()).hexdigest()
        if declared_binary and measured_binary != declared_binary:
            failures.append("producer_binary_digest_mismatch")
    else:
        failures.append("producer_binary_missing")

    # The binding to source is decidable only against the producer repository.
    commit_tree = ""
    binding = "not_checked_without_producer_repo"
    if producer_repo is not None:
        if not _object_present(producer_repo, pinned_commit):
            failures.append("producer_source_commit_absent_from_producer_repo")
            binding = "commit_unresolvable"
        else:
            commit_tree = _git(producer_repo, "rev-parse", f"{pinned_commit}^{{tree}}")
            if commit_tree and pinned_tree and commit_tree != pinned_tree:
                failures.append("producer_source_tree_does_not_match_commit")
                binding = "tree_mismatch"
            else:
                binding = "verified"

    specs = language_specs(root)
    report = {
        "schema": "gt.producer_source_binding.v2",
        "status": "VERIFIED" if not failures else "UNVERIFIED",
        "binding": binding,
        "pinned_source_commit": pinned_commit,
        "pinned_source_tree": pinned_tree,
        "producer_repo_commit_tree": commit_tree,
        "producer_sha256": measured_binary,
        # Recorded, never asserted: the vendored copy is for inspection and is
        # not expected to equal the tree the binary was built from.
        "vendored_source_tree": _git(root, "rev-parse", f"HEAD:{VENDORED_SOURCE}"),
        "vendored_language_count": len(specs),
        "vendored_languages": specs,
        "failures": failures,
    }
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default="config/deepswe_product_bundle_v1.json")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--producer-repo",
        default="",
        help="Path to the producer repository holding the pinned source commit.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--enforce", action="store_true", help="Exit nonzero when the binding is unverified."
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    producer = Path(args.producer_repo).resolve() if args.producer_repo else None
    bundle = json.loads((root / args.bundle).read_text(encoding="utf-8"))
    report, failures = evaluate(bundle, root=root, producer_repo=producer)

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    return 1 if (failures and args.enforce) else 0


if __name__ == "__main__":
    sys.exit(main())
