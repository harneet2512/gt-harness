#!/usr/bin/env python3
"""Extract an official ARB tar.zst release without a system zstd binary."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path, PurePosixPath

import zstandard


def _verify_sha256(archive: Path, sidecar: Path | None) -> None:
    if sidecar is None:
        return
    expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest().lower()
    if digest != expected:
        raise ValueError(f"checksum mismatch for {archive.name}: {digest} != {expected}")


def extract_release(
    archive: str | Path,
    destination: str | Path,
    *,
    sidecar: str | Path | None = None,
) -> int:
    source = Path(archive).resolve()
    target = Path(destination).resolve()
    _verify_sha256(source, Path(sidecar).resolve() if sidecar else None)
    extracted = 0
    target.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as raw:
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        with tarfile.open(fileobj=reader, mode="r|") as bundle:
            for member in bundle:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"unsafe ARB archive member: {member.name}")
                output = (target / Path(*relative.parts)).resolve()
                try:
                    output.relative_to(target)
                except ValueError as exc:
                    raise ValueError(f"archive member escapes destination: {member.name}") from exc
                if member.issym() or member.islnk() or not (member.isdir() or member.isreg()):
                    raise ValueError(f"unsupported ARB archive member: {member.name}")
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValueError(f"missing archive payload: {member.name}")
                with output.open("wb") as handle:
                    while chunk := stream.read(1024 * 1024):
                        handle.write(chunk)
                extracted += 1
    return extracted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--sha256", type=Path)
    args = parser.parse_args()
    print(extract_release(args.archive, args.destination, sidecar=args.sha256))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
