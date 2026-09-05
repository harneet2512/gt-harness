from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from gt_engine.runtime_observation import (
    capture_workspace,
    certify_observation_equivalence,
    compile_execution_evidence,
    compile_transaction_artifacts,
    diff_workspace,
)


def test_workspace_revision_changes_and_multifile_transaction_is_canonical(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    before = capture_workspace(tmp_path)
    (tmp_path / "a.py").write_text("a = 2\n", encoding="utf-8")
    (tmp_path / "b.py").unlink()
    (tmp_path / "c.py").write_text("c = 3\n", encoding="utf-8")
    after = capture_workspace(tmp_path)

    transaction = diff_workspace(before, after, action_id=7, command="edit both")

    assert transaction.changed_paths == ("a.py", "b.py", "c.py")
    assert [change.operation for change in transaction.changes] == [
        "modify", "delete", "create",
    ]
    assert transaction.pre_revision == before.revision
    assert transaction.post_revision == after.revision
    assert transaction.transaction_sha256 == hashlib.sha256(
        transaction.canonical_bytes(include_transaction_hash=False)
    ).hexdigest()


def test_workspace_snapshot_excludes_gitignored_generated_output(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("generated-out/\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    generated = tmp_path / "generated-out"
    generated.mkdir()
    (generated / "source.js").write_text("compiled\n", encoding="utf-8")

    snapshot = capture_workspace(tmp_path)

    paths = {item.path for item in snapshot.files}
    assert ".gitignore" in paths
    assert "source.py" in paths
    assert "generated-out/source.js" not in paths


def test_workspace_revision_uses_repository_text_bytes(tmp_path):
    source = tmp_path / "module.py"
    source.write_bytes(b"first\nsecond\n")
    lf = capture_workspace(tmp_path)

    source.write_bytes(b"first\r\nsecond\r\n")
    crlf = capture_workspace(tmp_path)
    assert crlf.revision == lf.revision
    assert [item.mapping() for item in crlf.files] == [
        item.mapping() for item in lf.files
    ]

    source.write_bytes(b"first\r\nchanged\r\n")
    changed = capture_workspace(tmp_path)
    assert changed.revision != lf.revision


def test_workspace_revision_keeps_binary_crlf_byte_exact(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"\x00first\r\n")
    crlf = capture_workspace(tmp_path)

    source.write_bytes(b"\x00first\n")
    lf = capture_workspace(tmp_path)
    assert crlf.revision != lf.revision


def test_execution_evidence_preserves_exact_raw_bytes_and_structure():
    raw = "tests/test_mod.py::test_x FAILED\r\n1 failed\r\n"
    artifact = compile_execution_evidence(
        command="python -m pytest tests/test_mod.py -q",
        output=raw,
        returncode=1,
        action_id=3,
        repository_revision="a" * 64,
    )

    payload = json.loads(artifact.canonical_bytes())
    assert payload["kind"] == "test"
    assert payload["outcome"] == "fail"
    assert payload["raw_output_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert artifact.raw_output == raw.encode()
    assert payload["returncode"] == 1


def test_non_build_or_test_command_is_not_structured():
    assert compile_execution_evidence(
        command="rg needle .",
        output="x.py:1:needle",
        returncode=0,
        action_id=1,
        repository_revision="b" * 64,
    ) is None


@pytest.mark.parametrize(("command", "output", "returncode", "expected"), [
    ("pytest -q | tee test.log", "1 failed", 0, "unknown"),
    ("npm test; echo done", "Tests: 1 failed", 0, "unknown"),
    ("pytest -q", "no tests ran", 0, "executed_no_tests"),
    ("pytest -q", "", 0, "unknown"),
    ("pytest -q", "1 passed", None, "unknown"),
    ("pytest -q", "1 passed", 0, "pass"),
    ("pytest -q", "1 failed", 1, "fail"),
    ("pytest -q", "ModuleNotFoundError: no module named missing", 1, "env_fail"),
    ("cargo test", "running 0 tests\ntest result: ok. 0 passed", 0, "executed_no_tests"),
    ("go test ./...", "ok example.com/test 0.013s", 0, "pass"),
    ("pytest -q", "", 124, "timeout"),
    ("pytest -q", "", -9, "interrupted"),
])
def test_execution_result_does_not_invent_test_success(command, output, returncode, expected):
    evidence = compile_execution_evidence(command=command, output=output,
        returncode=returncode, action_id=1, repository_revision="r1")
    assert evidence is not None
    assert evidence.outcome == expected


def test_transaction_artifacts_bind_patch_syntax_and_recorded_callers(tmp_path):
    import sqlite3

    (tmp_path / "target.py").write_text("def helper(x):\n    return x\n", encoding="utf-8")
    before = capture_workspace(tmp_path)
    (tmp_path / "target.py").write_text(
        "def helper(x, y):\n    return x + y\n", encoding="utf-8"
    )
    after = capture_workspace(tmp_path)
    transaction = diff_workspace(before, after, action_id=2, command="edit")
    graph = tmp_path / "graph.db"
    with sqlite3.connect(graph) as connection:
        connection.executescript(
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY,name TEXT,file_path TEXT);"
            "CREATE TABLE edges(source_id INTEGER,target_id INTEGER,type TEXT,"
            "source_line INTEGER);"
            "INSERT INTO nodes VALUES(1,'caller','use.py');"
            "INSERT INTO nodes VALUES(2,'helper','target.py');"
            "INSERT INTO edges VALUES(1,2,'CALLS',7);"
        )

    artifact = compile_transaction_artifacts(transaction, graph_db=graph)

    assert artifact["transaction_sha256"] == transaction.transaction_sha256
    assert artifact["syntax"][0]["path"] == "target.py"
    assert artifact["syntax"][0]["status"] == "exact"
    assert artifact["syntax"][0]["valid"] is True
    assert "-def helper(x):" in artifact["patches"][0]["patch"]
    assert bytes.fromhex(artifact["patches"][0]["postimage_hex"]) == (
        tmp_path / "target.py"
    ).read_bytes()
    assert artifact["patches"][0]["truncated"] is False
    assert artifact["callers"][0]["caller"] == "caller"
    assert artifact["callers"][0]["target"] == "helper"


def test_observation_equivalence_rejects_raw_sentinel_leak():
    sentinel = b"UNIQUE_RAW_SENTINEL_7281"
    clean = certify_observation_equivalence(
        raw_output=b"raw " + sentinel,
        final_observation=b'{"answer":42}',
        expected_observation=b'{"answer":42}',
        sentinel=sentinel,
    )
    leaked = certify_observation_equivalence(
        raw_output=b"raw " + sentinel,
        final_observation=b'{"answer":"UNIQUE_RAW_SENTINEL_7281"}',
        expected_observation=b'{"answer":"UNIQUE_RAW_SENTINEL_7281"}',
        sentinel=sentinel,
    )
    assert clean["replacement_certified"] is True
    assert leaked["replacement_certified"] is False
    assert leaked["sentinel_absent"] is False


def test_every_changed_file_gets_revision_bound_syntax_status_and_exact_postimage(
    tmp_path,
):
    (tmp_path / "valid.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"\x00old")
    before = capture_workspace(tmp_path)
    (tmp_path / "valid.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"\x00new\xff")
    (tmp_path / "removed.js").write_text("let x = 1;", encoding="utf-8")
    middle = capture_workspace(tmp_path)
    (tmp_path / "removed.js").unlink()
    after = capture_workspace(tmp_path)
    # Use a transaction covering modification + deletion.
    transaction = diff_workspace(middle, after, action_id=9, command="delete")
    artifact = compile_transaction_artifacts(transaction)
    assert artifact["syntax"] == [{
        "path": "removed.js",
        "language": "unknown",
        "status": "not_applicable_deleted",
        "post_revision": transaction.post_revision,
    }]
    patch = artifact["patches"][0]
    assert patch["postimage_hex"] is None
    assert patch["after_sha256"] is None

    modified = diff_workspace(before, middle, action_id=8, command="modify")
    modified_artifact = compile_transaction_artifacts(modified)
    statuses = {row["path"]: row["status"] for row in modified_artifact["syntax"]}
    assert statuses == {
        "asset.bin": "unsupported",
        "removed.js": "unsupported",
        "valid.py": "exact",
    }
    expected_postimages = {change.path: change.after for change in modified.changes}
    for row in modified_artifact["patches"]:
        assert bytes.fromhex(row["postimage_hex"]) == expected_postimages[row["path"]]


def test_python_signature_delta_distinguishes_body_and_signature_edits(tmp_path):
    target = tmp_path / "api.py"
    target.write_text("def compute(value: int) -> int:\n    return value\n", encoding="utf-8")
    before = capture_workspace(tmp_path)
    target.write_text("def compute(value: int) -> int:\n    return value + 1\n", encoding="utf-8")
    body_after = capture_workspace(tmp_path)
    body_artifact = compile_transaction_artifacts(
        diff_workspace(before, body_after, action_id=1, command="body edit")
    )
    assert body_artifact["signatures"][0]["status"] == "exact"
    assert body_artifact["signatures"][0]["changed"] == []

    target.write_text(
        "def compute(value: int, scale: int = 1) -> int:\n    return value * scale\n",
        encoding="utf-8",
    )
    signature_after = capture_workspace(tmp_path)
    signature_artifact = compile_transaction_artifacts(
        diff_workspace(body_after, signature_after, action_id=2, command="signature edit")
    )
    assert signature_artifact["signatures"][0]["changed"] == ["compute"]


def test_parser_signature_delta_handles_create_and_delete(tmp_path, monkeypatch):
    removed = tmp_path / "removed.go"
    removed.write_text("package p\nfunc Removed() {}\n", encoding="utf-8")
    before = capture_workspace(tmp_path)
    removed.unlink()
    (tmp_path / "added.go").write_text(
        "package p\nfunc Added(value int) {}\n", encoding="utf-8"
    )
    after = capture_workspace(tmp_path)

    def inspect(requests):
        rows = []
        for request in requests:
            name = "Added" if b"Added" in request.content else "Removed"
            rows.append({
                "schema": "gt.parser_inspection.v1",
                "request_id": request.request_id,
                "content_sha256": hashlib.sha256(request.content).hexdigest(),
                "language": "go",
                "parser_identity": "fixture/parser",
                "complete": True,
                "diagnostics": [],
                "declarations": [{
                    "name": name,
                    "qualified_name": name,
                    "signature": name,
                }],
            })
        return tuple(rows)

    monkeypatch.setattr("gt_engine.parser_inspection.inspect_sources", inspect)
    artifact = compile_transaction_artifacts(
        diff_workspace(before, after, action_id=3, command="replace API")
    )
    signatures = {row["path"]: row for row in artifact["signatures"]}
    assert signatures["added.go"]["added"] == ["Added"]
    assert signatures["added.go"]["removed"] == []
    assert signatures["removed.go"]["added"] == []
    assert signatures["removed.go"]["removed"] == ["Removed"]


def test_parser_signature_delta_rejects_duplicate_qualified_names(
    tmp_path, monkeypatch
):
    target = tmp_path / "api.ts"
    target.write_text("function f(value: string): void {}\n", encoding="utf-8")
    before = capture_workspace(tmp_path)
    target.write_text("function f(value: number): void {}\n", encoding="utf-8")
    after = capture_workspace(tmp_path)

    def inspect(requests):
        return tuple({
            "schema": "gt.parser_inspection.v1",
            "request_id": request.request_id,
            "content_sha256": hashlib.sha256(request.content).hexdigest(),
            "language": "typescript",
            "parser_identity": "fixture/parser",
            "complete": True,
            "diagnostics": [],
            "declarations": [
                {"qualified_name": "f", "signature": "first"},
                {"qualified_name": "f", "signature": "second"},
            ],
        } for request in requests)

    monkeypatch.setattr("gt_engine.parser_inspection.inspect_sources", inspect)
    artifact = compile_transaction_artifacts(
        diff_workspace(before, after, action_id=4, command="edit overload")
    )
    assert artifact["signatures"][0]["status"] == (
        "unavailable_ambiguous_declaration_identity"
    )
