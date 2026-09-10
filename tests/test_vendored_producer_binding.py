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

That divergence was never root-caused: the binary was built 2026-09-10T04:16
from a tree whose hashed file set matched neither the commit it named nor the
vendored copy — consistent with a dirty or extra-file build. The resolved state
is a fresh certified build (d6fdf93d, determinism fix) whose fingerprint recipe
hashes the build worktree itself, so the vendored tree was synced to the exact
worktree bytes the recipe hashed — CRLF included, preserved by `.gitattributes
* -text`. All four identities now agree: build-info git_commit == SOURCE-COMMIT
== d6fdf93d, and source_fingerprint == 77d8bf97 over both the vendored tree and
the producer worktree it was built from.
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

# The producer's own recipe, from scripts/swebench/build_gt_index_linux.sh:73.
# Relative paths are part of the digest so a rename is an identity change while
# the checkout location is not.
FINGERPRINT_SUFFIXES = (".go", ".c", ".cc", ".cpp", ".h", ".hpp", ".s")
FINGERPRINT_NAMES = ("go.mod", "go.sum")


def _declared() -> dict:
    return json.loads(BUILD_INFO.read_text(encoding="utf-8"))


def _source_fingerprint(root: Path) -> str:
    """Reproduce `sha256sum <files> | sha256sum` without a shell.

    coreutils prints "<digest>  <path>\\n" per file, then the outer pass hashes
    that text. Both details matter: two spaces, and the paths exactly as `find`
    emitted them under `LC_ALL=C sort`.
    """
    entries = [
        path for path in root.rglob("*")
        if path.is_file() and (path.suffix in FINGERPRINT_SUFFIXES
                               or path.name in FINGERPRINT_NAMES)
    ]
    lines = sorted(f"./{path.relative_to(root).as_posix()}" for path in entries)
    inner = "".join(
        f"{hashlib.sha256((root / name[2:]).read_bytes()).hexdigest()}  {name}\n"
        for name in lines
    )
    return hashlib.sha256(inner.encode("utf-8")).hexdigest()


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
    """This test reimplements a shell pipeline; guard the reimplementation.

    Run the real pipeline when a POSIX shell with coreutils is available and
    require the pure-Python version to agree. Without that, a bug here would
    silently redefine what "binding" means.
    """
    if sys.platform.startswith("win") or not SOURCE.is_dir():
        pytest.skip("needs a POSIX shell with GNU coreutils")
    pipeline = (
        "find . -type f \\( -name '*.go' -o -name '*.c' -o -name '*.cc' "
        "-o -name '*.cpp' -o -name '*.h' -o -name '*.hpp' -o -name '*.s' "
        "-o -name 'go.mod' -o -name 'go.sum' \\) -print0 | LC_ALL=C sort -z "
        "| xargs -0 sha256sum | sha256sum | awk '{print $1}'"
    )
    completed = subprocess.run(["sh", "-c", pipeline], cwd=SOURCE,
                               capture_output=True, text=True)
    if completed.returncode != 0:
        pytest.skip(f"shell pipeline unavailable: {completed.stderr.strip()[:80]}")
    assert completed.stdout.strip() == _source_fingerprint(SOURCE)
