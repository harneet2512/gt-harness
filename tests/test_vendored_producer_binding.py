"""Does the vendored producer source belong to the certified binary?

The paid workflow builds `vendor/gt-index-src` cleanly, throws the result away,
and runs the certified binary instead. Its own comment says why that is only a
verification if the two correspond: "for the whole of HAR-87 they did not: the
vendored tree carried ZERO occurrences of node_type, candidate_state,
resolution_v2 or completeness_fact while the pinned binary emits all of them. A
green check over source that is not running is worse than no check, and it
produced three confidently wrong conclusions in this ticket."

So the workflow binds them two ways: `SOURCE-COMMIT` against the binary's
declared `git_commit`, and a content digest against its declared
`source_fingerprint`. Both live only in the PAID workflow
(`deepswe_gt_harness_product_p0731.yaml`), which means the binding is currently
checked only by a dispatch that costs money, and only after it has already
started.

These tests move both halves onto the free suite. They previously failed,
measured rather than assumed:

    build-info git_commit         efa70e52d4e85bff2364b0038ceca2306de218b0
    vendor/.../SOURCE-COMMIT      193b9d93b0650721be6120ae519ab3a1d8e8137f

    build-info source_fingerprint f7fca174b866cc8c8b0f826aa649aa24f47d718b872c8dae45d0dabd71e5a7a8
    vendored tree, same recipe    4f612d4cdf487a22469765fd23f3500429c10ffea0677860785324c3e2e81551

That divergence's root cause is now established, in two parts. First, the
recipe hashed worktree BYTES, and `sha256sum`'s mode marker differs by host
(`hash *path` on MSYS vs `hash  path` on Linux). Second, and deeper: fourteen
producer files carry LF blobs but CRLF worktrees under autocrlf, so the same
recipe on a Linux clone could never reproduce the stamp at all. The recipe now
fingerprints the commit's git OBJECTS (`git ls-tree` blob shas — canonical on
every platform) and refuses a dirty `gt-index` worktree outright. The vendored
tree carries producer blob bytes exactly, so the same object computation runs
over it without a git binary at all: sha1("blob <len>\\0" + content).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "vendor"
BUILD_INFO = VENDOR / "gt-index-linux-amd64.build-info.json"
SOURCE = VENDOR / "gt-index-src"

# The producer's own recipe filter, from
# scripts/swebench/build_gt_index_linux.sh: every checked-in compiler input.
FINGERPRINT_SUFFIXES = (".go", ".c", ".cc", ".cpp", ".h", ".hpp", ".s")
FINGERPRINT_NAMES = ("go.mod", "go.sum")
_OVERLAY = {"SOURCE-COMMIT", ".gitattributes"}


def _declared() -> dict:
    return json.loads(BUILD_INFO.read_text(encoding="utf-8"))


def _blob_sha(data: bytes) -> str:
    """git's blob identity: sha1 of `blob <len>\\0` + content, no git needed."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _source_fingerprint(root: Path) -> str:
    """Reproduce `git ls-tree -r HEAD -- <dir> | <filter> | sha256sum`.

    The producer's recipe reads commit objects because worktree bytes are not
    canonical (autocrlf writes CRLF where the blob is LF). The vendored tree
    stores those same blob bytes, so the blob sha is computable in place. All
    fingerprinted entries are mode 100644 on the producer side.
    """
    lines = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in _OVERLAY:
            continue
        if path.suffix in FINGERPRINT_SUFFIXES or path.name in FINGERPRINT_NAMES:
            rel = path.relative_to(root).as_posix()
            lines.append(f"100644 blob {_blob_sha(path.read_bytes())}\t{rel}")
    lines.sort()
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


@pytest.mark.skipif(not BUILD_INFO.is_file(), reason="no vendored producer build info")
def test_the_vendored_source_names_the_commit_the_binary_was_built_from():
    """Cheap half of the binding: one file against one field."""
    marker = SOURCE / "SOURCE-COMMIT"
    if not marker.is_file():
        pytest.skip("no vendored SOURCE-COMMIT to compare")
    assert marker.read_text(encoding="utf-8").strip() == _declared()["git_commit"], (
        "the vendored producer source names a different commit than the "
        "certified binary was built from"
    )


@pytest.mark.skipif(not BUILD_INFO.is_file(), reason="no vendored producer build info")
def test_the_vendored_source_reproduces_the_binarys_declared_fingerprint():
    """The half that a stale SOURCE-COMMIT cannot fake.

    A commit marker binds by assertion. This binds by content, so a source
    change without a rebuild fails here even if the marker is updated by hand.
    """
    if not SOURCE.is_dir():
        pytest.skip("no vendored producer source to fingerprint")
    assert _source_fingerprint(SOURCE) == _declared()["source_fingerprint"]


@pytest.mark.skipif(not BUILD_INFO.is_file(), reason="no vendored producer build info")
def test_the_fingerprint_recipe_matches_the_producers_own():
    """Guard the reimplementation against the real `git ls-tree` stream.

    When a git binary can resolve this repository, compute the object
    fingerprint the recipe's own way and require agreement. On mounted
    worktrees whose .git pointer does not resolve in-container the check skips
    rather than fakes agreement.
    """
    if not SOURCE.is_dir():
        pytest.skip("no vendored producer source to fingerprint")
    pipeline = (
        "git ls-tree -r HEAD -- vendor/gt-index-src "
        "| sed 's|\\tvendor/gt-index-src/|\\t|' | LC_ALL=C sort "
        "| grep -E '\\.(go|c|cc|cpp|h|hpp|s)$|go\\.(mod|sum)[[:space:]]*$' "
        "| sha256sum"
    )
    completed = subprocess.run(["sh", "-c", pipeline], cwd=REPO,
                               capture_output=True, text=True)
    if completed.returncode != 0:
        pytest.skip(f"git ls-tree unavailable: {completed.stderr.strip()[:80]}")
    assert completed.stdout.strip() == _source_fingerprint(SOURCE)
