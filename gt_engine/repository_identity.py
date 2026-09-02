"""Platform-stable byte identity for files stored as repository text."""

from __future__ import annotations

import hashlib
from pathlib import Path


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
