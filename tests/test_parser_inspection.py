from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from gt_engine.parser_inspection import ParserInspectionRequest, inspect_sources


def test_client_binds_request_and_content_identity(monkeypatch):
    request = ParserInspectionRequest("r1", "src/a.ts", b"export function a() {}")
    def run(command, **kwargs):
        sent = json.loads(kwargs["input"])
        assert command == ["producer", "-inspect-jsonl"]
        assert sent["language"] == "typescript"
        row = {"schema": "gt.parser_inspection.v1", "request_id": "r1",
               "content_sha256": hashlib.sha256(request.content).hexdigest(),
               "complete": True, "declarations": []}
        return SimpleNamespace(returncode=0, stdout=json.dumps(row).encode(), stderr=b"")
    monkeypatch.setattr("gt_engine.parser_inspection.subprocess.run", run)
    assert inspect_sources([request], binary="producer")[0]["complete"]


def test_client_rejects_response_for_different_bytes(monkeypatch):
    request = ParserInspectionRequest("r1", "a.py", b"def a(): pass")
    monkeypatch.setattr("gt_engine.parser_inspection.subprocess.run", lambda *a, **k:
        SimpleNamespace(returncode=0, stdout=json.dumps({
            "schema": "gt.parser_inspection.v1", "request_id": "r1",
            "content_sha256": "0" * 64}).encode(), stderr=b""))
    with pytest.raises(RuntimeError, match="identity_mismatch"):
        inspect_sources([request], binary="producer")
