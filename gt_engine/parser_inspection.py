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

_PARSER_IDENTITY_PREFIX = "gt-index/"


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
    parser_identity = ""
    for request, row in zip(items, rows, strict=True):
        expected = request.as_dict()
        if (row.get("schema") != "gt.parser_inspection.v1"
                or row.get("request_id") != request.request_id
                or row.get("content_sha256") != expected["content_sha256"]):
            raise RuntimeError("parser_inspection_response_identity_mismatch")
        identity = row.get("parser_identity")
        if (row.get("parser_identity_complete") is not True
                or not isinstance(identity, str)
                or not identity.startswith(_PARSER_IDENTITY_PREFIX)
                or len(identity) != len(_PARSER_IDENTITY_PREFIX) + 64
                or any(character not in "0123456789abcdef"
                       for character in identity[len(_PARSER_IDENTITY_PREFIX):])):
            raise RuntimeError("parser_inspection_producer_identity_unbound")
        if parser_identity and identity != parser_identity:
            raise RuntimeError("parser_inspection_producer_identity_changed")
        parser_identity = identity
    return rows


__all__ = ["ParserInspectionRequest", "inspect_sources"]
