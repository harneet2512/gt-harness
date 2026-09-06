"""Platform-stable byte identity for files stored as repository text."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


RUNTIME_GENERATED_DIRS = frozenset({
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


def is_untracked_runtime_artifact(path: str) -> bool:
    """Recognize generated caches, never arbitrary source/dependency directories.

    Call only for paths established as untracked by Git. Tracked files remain
    observable even when they live inside a cache directory.
    """
    return any(part in RUNTIME_GENERATED_DIRS
               for part in path.replace("\\", "/").split("/")[:-1])


@dataclass(frozen=True, slots=True)
class RepositoryHistory:
    head: str = ""
    shallow: tuple[str, ...] = ()

    def mapping(self) -> dict:
        return {"head": self.head, "shallow": list(self.shallow)}


def repository_history(root: Path) -> RepositoryHistory:
    """Identify HEAD and its available ancestry, excluding enclosing repositories."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD", "--git-path", "shallow"],
            capture_output=True, text=True, check=True, timeout=8,
        )
        top, head, shallow_path = result.stdout.strip().splitlines()
        if Path(top).resolve() != root.resolve():
            return RepositoryHistory()
        path = root / shallow_path
        shallow = tuple(sorted(path.read_text().splitlines())) if path.exists() else ()
        return RepositoryHistory(head, shallow)
    except (OSError, ValueError, subprocess.SubprocessError):
        return RepositoryHistory()


def canonical_repository_bytes(payload: bytes) -> bytes:
    """Return Git-style LF bytes for UTF-8 text and exact bytes otherwise."""

    if b"\r\n" not in payload or b"\x00" in payload:
        return payload
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    return payload.replace(b"\r\n", b"\n")


def repository_file_sha256(path: Path) -> str:
    """Hash a checkout file independently of Git's CRLF materialization."""

    return hashlib.sha256(canonical_repository_bytes(path.read_bytes())).hexdigest()


def matches_repository_file_sha256(path: Path, expected: str) -> bool:
    """Accept either exact artifact bytes or their canonical checkout form."""

    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() == expected:
        return True
    return hashlib.sha256(canonical_repository_bytes(payload)).hexdigest() == expected
