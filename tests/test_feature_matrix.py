from __future__ import annotations

import copy
import json

import pytest

from gt_engine.attribution import DIRECT_FEATURES
from gt_engine.feature_matrix import (
    SCHEMA,
    build_cell,
    build_matrix,
    digest_body,
    render_markdown,
    verify_matrix,
)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "gt_engine.feature_matrix._git_head",
        lambda _root: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    return tmp_path


def test_feature_matrix_schema_and_coverage(repo_root):
    matrix = build_matrix(repo_root=repo_root, execute=False)
    assert matrix["schema"] == SCHEMA
    assert matrix["identity_count"] == len(DIRECT_FEATURES)
    assert {row["identity"] for row in matrix["rows"]} == set(DIRECT_FEATURES)
    assert not verify_matrix(matrix)


def test_cell_digest_rejects_tamper(repo_root):
    matrix = build_matrix(repo_root=repo_root, execute=False)
    mutated = copy.deepcopy(matrix)
    mutated["rows"][0]["disposition"] = "TAMPERED"
    assert verify_matrix(mutated)


def test_matrix_digest_rejects_tamper(repo_root):
    matrix = build_matrix(repo_root=repo_root, execute=False)
    mutated = copy.deepcopy(matrix)
    mutated["identity_count"] = 0
    assert verify_matrix(mutated)


def test_render_markdown_includes_identities(repo_root):
    matrix = build_matrix(repo_root=repo_root, execute=False)
    rendered = render_markdown(matrix)
    for identity in DIRECT_FEATURES:
        assert identity in rendered


def test_build_cell_without_binding_is_not_run(repo_root):
    # Force empty binding path via monkeypatch on module constant
    import gt_engine.feature_matrix as fm

    original = fm.FEATURE_EVIDENCE.pop("caller_contract", None)
    try:
        cell = build_cell("caller_contract", repo_root=repo_root, execute=False)
        assert cell["disposition"] == "not_run"
        assert cell["evidence"]["reason"] == "no_evidence_binding"
        assert digest_body(cell, field="cell_digest_sha256") == cell["cell_digest_sha256"]
    finally:
        if original is not None:
            fm.FEATURE_EVIDENCE["caller_contract"] = original


def test_issue_and_verify_round_trip(repo_root):
    matrix = build_matrix(repo_root=repo_root, execute=False)
    encoded = json.dumps(matrix, sort_keys=True, separators=(",", ":"))
    roundtrip = json.loads(encoded)
    assert not verify_matrix(roundtrip)
