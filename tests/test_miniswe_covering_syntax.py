"""Post-edit syntax probe coverage for the certified multi-language surface.

``run_syntax_probe`` must reach the same certified parser boundary
(``gt-index -inspect-jsonl`` via ``parser_inspection.inspect_sources``) that
``compile_transaction_artifacts`` uses for ``.py .pyi .go .ts .tsx .js .jsx
.rs`` edits. The boundary is faked at the client seam here; request/response
identity binding has dedicated tests in ``test_parser_inspection.py``.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from gt_engine import miniswe_covering as covering


def _adapter(repo):
    return SimpleNamespace(repo_root=str(repo))


def _inspect_rows(*, complete, diagnostics=(), language="go"):
    def inspect(requests):
        return tuple({
            "schema": "gt.parser_inspection.v1",
            "request_id": request.request_id,
            "content_sha256": hashlib.sha256(request.content).hexdigest(),
            "language": language,
            "parser_identity": "gt-index/" + "a" * 64,
            "parser_identity_complete": True,
            "complete": complete,
            "diagnostics": list(diagnostics),
            "declarations": [],
        } for request in requests)
    return inspect


@pytest.mark.parametrize(
    "filename",
    ["src/a.go", "src/a.ts", "src/a.tsx", "src/a.js", "src/a.rs"],
)
def test_syntax_probe_reports_broken_edit_on_certified_nonpython_surface(
    tmp_path, monkeypatch, filename
):
    """A broken .go/.ts/.tsx/.js/.rs edit produces a syntax-probe observation.

    Before the fix the probe filtered ``f.endswith(".py")`` and these edits
    produced no evidence at all.
    """
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"this is not valid source code (((")
    seen = []

    def inspect(requests):
        rows = _inspect_rows(
            complete=False, diagnostics=["syntax_tree_incomplete"]
        )(requests)
        seen.extend(request.path for request in requests)
        return rows

    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources", inspect
    )
    result = covering.run_syntax_probe(_adapter(tmp_path), (filename,))
    assert seen == [filename]
    assert f"{filename}: syntax error" in result
    assert "syntax_tree_incomplete" in result


def test_syntax_probe_stays_quiet_on_clean_certified_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    target = tmp_path / "src" / "a.go"
    target.parent.mkdir(parents=True)
    target.write_text("package a\nfunc A() {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources",
        _inspect_rows(complete=True),
    )
    assert covering.run_syntax_probe(_adapter(tmp_path), ("src/a.go",)) == ""


def test_syntax_probe_abstains_on_producer_request_fault(tmp_path, monkeypatch):
    """An incomplete row that is not a parse failure is not syntax evidence."""
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    target = tmp_path / "src" / "a.go"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"package a\n")
    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources",
        _inspect_rows(complete=False, diagnostics=["content_too_large"]),
    )
    assert covering.run_syntax_probe(_adapter(tmp_path), ("src/a.go",)) == ""


def test_syntax_probe_falls_back_to_py_compile_when_boundary_unavailable(
    tmp_path, monkeypatch
):
    """No certified parser -> .py keeps the compile probe, others stay quiet."""
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "src" / "a.go").write_bytes(b"package a\nfunc BROKEN(((\n")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("parser_inspection_binary_unavailable")

    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources", unavailable
    )
    result = covering.run_syntax_probe(
        _adapter(tmp_path), ("src/a.py", "src/a.go")
    )
    assert "src/a.py: syntax error" in result
    assert "src/a.go" not in result


def test_syntax_probe_stays_quiet_without_verify_execute(tmp_path, monkeypatch):
    monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
    target = tmp_path / "src" / "a.go"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken ((")
    seen = []
    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources",
        lambda requests: seen.extend(requests) or (),
    )
    assert covering.run_syntax_probe(_adapter(tmp_path), ("src/a.go",)) == ""
    assert seen == []


def test_syntax_probe_stays_quiet_on_uncertified_extension(
    tmp_path, monkeypatch
):
    """Extensions outside the certified parser surface are not probed."""
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "a.java").write_bytes(b"class BROKEN (((\n")
    seen = []
    monkeypatch.setattr(
        "gt_engine.parser_inspection.inspect_sources",
        lambda requests: seen.extend(requests) or (),
    )
    assert covering.run_syntax_probe(_adapter(tmp_path), ("src/a.java",)) == ""
    assert seen == []
