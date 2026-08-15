from __future__ import annotations

import io
import tarfile

import pytest
import zstandard

from scripts.arb_extract import extract_release


def test_extract_release_rejects_path_traversal(tmp_path) -> None:
    archive = tmp_path / "bad.tar.zst"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as bundle:
        info = tarfile.TarInfo("../escape.txt")
        data = b"bad"
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    compressed = zstandard.ZstdCompressor().compress(payload.getvalue())
    archive.write_bytes(compressed)
    with pytest.raises(ValueError, match="unsafe"):
        extract_release(archive, tmp_path / "out")
