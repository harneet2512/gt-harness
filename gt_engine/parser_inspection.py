"""Content-addressed client for gt-index's pure JSONL parser mode."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .indexer import _index_child_environment

_LANGUAGE = {
    ".py": "python", ".pyi": "python", ".go": "go", ".ts": "typescript",
    ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".rs": "rust",
}


@dataclass(frozen=True, slots=True)
class ParserInspectionRequest:
    request_id: str
    path: str
    content: bytes
    is_test: bool = False

    def as_dict(self) -> dict:
        language = _LANGUAGE.get(Path(self.path).suffix.lower(), "")
        return {
            "request_id": self.request_id,
            "language": language,
            "path": self.path.replace("\\", "/"),
            "content_sha256": hashlib.sha256(self.content).hexdigest(),
            "content_base64": base64.b64encode(self.content).decode("ascii"),
            "is_test": self.is_test,
        }


def inspect_sources(requests: Iterable[ParserInspectionRequest], *,
                    binary: str | None = None, timeout: float = 15) -> tuple[dict, ...]:
    items = tuple(requests)
    if not items:
        return ()
    # Observation enrichment must never trigger producer download or build.
    # Product startup pins GT_INDEX_BINARY; absence is typed unavailable.
    executable = binary or os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index")
    if not executable:
        raise RuntimeError("parser_inspection_binary_unavailable")
    payload = b"".join(
        json.dumps(item.as_dict(), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for item in items
    )
    process = subprocess.run(
        [executable, "-inspect-jsonl"], input=payload, capture_output=True,
        timeout=timeout, env=_index_child_environment(256 * 1024 * 1024), check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("parser_inspection_process_failed")
    try:
        rows = tuple(json.loads(line) for line in process.stdout.splitlines() if line.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("parser_inspection_response_invalid") from exc
    if len(rows) != len(items):
        raise RuntimeError("parser_inspection_response_count_mismatch")
    for request, row in zip(items, rows, strict=True):
        expected = request.as_dict()
        if (row.get("schema") != "gt.parser_inspection.v1"
                or row.get("request_id") != request.request_id
                or row.get("content_sha256") != expected["content_sha256"]):
            raise RuntimeError("parser_inspection_response_identity_mismatch")
    return rows


__all__ = ["ParserInspectionRequest", "inspect_sources"]
