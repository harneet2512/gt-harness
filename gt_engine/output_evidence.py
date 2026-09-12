"""Byte-preserving command artifacts and the installed Bash retrieval operation."""
from __future__ import annotations

import argparse
import base64
import codecs
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

PAGE_BYTES = 8192

# Model-visible preview bound. Sized for the provider window, not for
# prompt-economy: a source file or test log under ~64KB arrives complete;
# only genuinely large outputs are head+tail truncated.
PREVIEW_CHARS = 65_536
PREVIEW_HEAD_CHARS = 49_152
PREVIEW_TAIL_CHARS = 16_384


class EvidenceStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid artifact ID")
        path = self.root / digest
        if path.is_symlink():
            raise ValueError("artifact symlink forbidden")
        return path

    @staticmethod
    def _identity(stream):
        digest = hashlib.sha256()
        size = 0
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        encoding = "utf-8"
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
            if encoding == "utf-8":
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError:
                    encoding = "base64"
        if encoding == "utf-8":
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                encoding = "base64"
        return digest.hexdigest(), size, encoding

    def publish(self, spool: Path) -> dict:
        # Never publish an inode inherited by a subprocess. Even a detached
        # writer cannot mutate this separately owned, finite byte snapshot.
        with spool.open("rb") as source, tempfile.NamedTemporaryFile(
            dir=self.root, prefix="snapshot-", delete=False,
        ) as frozen:
            remaining = os.fstat(source.fileno()).st_size
            while remaining:
                chunk = source.read(min(remaining, 65536))
                if not chunk:
                    raise ValueError("capture shortened during publication")
                frozen.write(chunk)
                remaining -= len(chunk)
            frozen.flush()
            os.fsync(frozen.fileno())
        frozen_path = Path(frozen.name)
        with frozen_path.open("rb") as stream:
            digest, size, encoding = self._identity(stream)
        destination = self.path(digest)
        if destination.exists():
            self.read(digest, 0, 1)
            frozen_path.unlink()
        else:
            os.replace(frozen_path, destination)
        try:
            spool.unlink()
        except PermissionError:
            pass  # Windows may retain an inherited handle; it is never the CAS inode.
        if os.name == "posix":
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {"schema": "gt.output_artifact.v1", "root": str(self.root),
                "sha256": digest, "total_length": size, "encoding": encoding}

    def read(self, digest: str, offset: int, length: int) -> dict:
        if offset < 0 or not 1 <= length <= PAGE_BYTES:
            raise ValueError("offset must be nonnegative; length must be 1..8192 bytes")
        with self.path(digest).open("rb") as stream:
            actual, total, disposition = self._identity(stream)
            if actual != digest:
                raise ValueError("artifact digest mismatch")
            if offset > total:
                raise ValueError("offset exceeds artifact length")
            stream.seek(offset)
            raw = stream.read(length)
        end = offset + len(raw)
        row = {"sha256": digest, "total_length": total, "offset": offset,
               "end_offset": end, "returned_length": len(raw),
               "continuation_offset": end if end < total else None,
               "artifact_encoding": disposition}
        try:
            row.update(encoding="utf-8", text=raw.decode("utf-8"))
        except UnicodeDecodeError:
            row.update(encoding="base64", base64=base64.b64encode(raw).decode("ascii"))
        return row

    def bytes(self, digest: str) -> bytes:
        # Legacy analyzers require a complete value. Capture and provider history
        # never buffer it; this compatibility boundary validates the same blob.
        with self.path(digest).open("rb") as stream:
            actual, _, _ = self._identity(stream)
            if actual != digest:
                raise ValueError("artifact digest mismatch")
            stream.seek(0)
            return stream.read()

    def iter_bytes(
        self,
        digest: str,
        *,
        chunk_size: int = 65536,
        expected_length: int | None = None,
        expected_encoding: str | None = None,
    ) -> Iterator[bytes]:
        """Yield bounded bytes and verify the same open handle at exhaustion.

        Consumers must exhaust the iterator before publishing a derived result.
        Digest and metadata failures are deliberately reported after the final
        chunk has been hashed, while the file descriptor remains open.
        """
        if not 1 <= chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be 1..1048576 bytes")
        digest_state = hashlib.sha256()
        size = 0
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        disposition = "utf-8"
        with self.path(digest).open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest_state.update(chunk)
                size += len(chunk)
                if disposition == "utf-8":
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError:
                        disposition = "base64"
                yield chunk
            if disposition == "utf-8":
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    disposition = "base64"
            if digest_state.hexdigest() != digest:
                raise ValueError("artifact digest mismatch")
            if expected_length is not None and size != expected_length:
                raise ValueError("artifact length metadata mismatch")
            if expected_encoding is not None and disposition != expected_encoding:
                raise ValueError("artifact encoding metadata mismatch")

    def preview(self, reference: dict, *, retrieval_result: bool = False) -> str:
        if retrieval_result and reference["total_length"] <= PAGE_BYTES * 6 + 2048:
            return self.bytes(reference["sha256"]).decode("utf-8", "strict")
        # Model-visible truncation. A pointer here is not free: resolving it
        # costs the agent a whole turn per page, and on a long task the
        # fetch-chores crowded out the task itself (run 34656860834 spent
        # ~54% of its commands dereferencing GT artifacts instead of
        # implementing). Head+tail keeps the two ends agents actually read —
        # the start of a file and the tail of a test log — and the elision
        # note invites the agent-native recovery (re-run with sed/head/tail
        # ranges) rather than a GT-internal fetch protocol. Complete bytes
        # stay in the artifact for the canonical analyzers either way.
        text = self.bytes(reference["sha256"]).decode("utf-8", "replace")
        if len(text) <= PREVIEW_HEAD_CHARS + PREVIEW_TAIL_CHARS:
            return text
        head = text[: PREVIEW_HEAD_CHARS]
        tail = text[-PREVIEW_TAIL_CHARS:]
        elided = len(text) - len(head) - len(tail)
        return (head + f"\n[output truncated: {elided} of "
                f"{len(text)} chars elided; use sed -n/head/tail for other ranges]\n"
                + tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read")
    read.add_argument("artifact_id")
    read.add_argument("offset", type=int)
    read.add_argument("length", type=int)
    args = parser.parse_args()
    try:
        root = os.environ.get("GT_EVIDENCE_ROOT")
        if not root:
            raise ValueError("GT_EVIDENCE_ROOT is not configured for this task")
        page = EvidenceStore(root).read(args.artifact_id, args.offset, args.length)
        print(json.dumps(page, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
