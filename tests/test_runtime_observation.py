from __future__ import annotations

import hashlib
import json

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
