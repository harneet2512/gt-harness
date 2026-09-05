from __future__ import annotations

import json
import sys

from scripts import issue_feature_matrix as issuer


def test_red_matrix_replaces_stale_output_before_nonzero_exit(tmp_path, monkeypatch):
    output = tmp_path / "matrix.json"
    markdown = tmp_path / "matrix.md"
    output.write_text('{"source_revision":"stale"}', encoding="utf-8")
    matrix = {"source_revision": "current", "rows": [], "identity_count": 0}
    monkeypatch.setattr(issuer, "build_matrix", lambda **_: matrix)
    monkeypatch.setattr(issuer, "verify_matrix", lambda *args, **kwargs: ["missing positive witness"])
    monkeypatch.setattr(sys, "argv", ["issue_feature_matrix", "--output", str(output),
                                     "--markdown-output", str(markdown)])
    assert issuer.main() == 1
    assert json.loads(output.read_text(encoding="utf-8"))["source_revision"] == "current"
    assert markdown.is_file()


def test_issuer_exception_leaves_fresh_error_artifact(tmp_path, monkeypatch):
    output = tmp_path / "matrix.json"
    markdown = tmp_path / "matrix.md"
    monkeypatch.setattr(issuer, "build_matrix", lambda **_: (_ for _ in ()).throw(OSError("fixture")))
    monkeypatch.setattr(sys, "argv", ["issue_feature_matrix", "--output", str(output),
                                     "--markdown-output", str(markdown)])
    assert issuer.main() == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["source_revision"]
    assert result["issuance_error"]["type"] == "OSError"
    assert result["rows"] == []


def test_git_failure_cannot_leave_stale_matrix(tmp_path, monkeypatch):
    output = tmp_path / "matrix.json"
    output.write_text('{"source_revision":"stale"}', encoding="utf-8")
    monkeypatch.setattr(issuer.subprocess, "run", lambda *args, **kwargs:
                        (_ for _ in ()).throw(OSError("fixture")))
    monkeypatch.setattr(sys, "argv", ["issue_feature_matrix", "--output", str(output),
                                     "--markdown-output", str(tmp_path / "matrix.md")])
    assert issuer.main() == 1
    result = json.loads(output.read_bytes())
    assert result["source_revision"] == ""
    assert result["issuance_error"]["type"] == "OSError"
