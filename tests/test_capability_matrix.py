from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from gt_engine.graph_context import (
    GITNEXUS_PINNED_REVISION,
    build_capability_matrix,
    load_capability_matrix,
    persist_capability_matrix,
    verify_capability_matrix,
)

GT_REVISION = "e56c7ef17eaffee36c80ff4dde4f0cd3991c4dcd"
GITNEXUS_ROOT = Path(os.environ["GT_GITNEXUS_ROOT"]) if os.environ.get("GT_GITNEXUS_ROOT") else None
GT_ROOT = Path(__file__).parents[1]


def _pinned_roots() -> dict[str, Path]:
    if GITNEXUS_ROOT is None or not GITNEXUS_ROOT.is_dir():
        pytest.skip("set GT_GITNEXUS_ROOT to the immutable GitNexus checkout")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(GITNEXUS_ROOT),
            "cat-file",
            "-e",
            f"{GITNEXUS_PINNED_REVISION}^{{commit}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("GT_GITNEXUS_ROOT does not contain the reviewed immutable revision")
    return {"gt": GT_ROOT, "gitnexus": GITNEXUS_ROOT}


def test_capability_matrix_is_source_backed_and_persisted(tmp_path: Path) -> None:
    roots = _pinned_roots()
    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=roots["gitnexus"],
    )
    assert matrix["schema"] == "gt.capability_matrix.v2"
    assert matrix["gitnexus_revision"] == GITNEXUS_PINNED_REVISION
    assert verify_capability_matrix(matrix, roots)
    assert all(
        "symbol" in cell["citation"] and "line" in cell["citation"]
        for cell in matrix["cells"]
    )
    assert [cell["capability"] for cell in matrix["cells"]] == sorted(
        cell["capability"] for cell in matrix["cells"]
    )
    artifact = tmp_path / "capability-matrix.json"
    persist_capability_matrix(artifact, matrix)
    assert load_capability_matrix(artifact) == matrix
    assert json.loads(json.dumps(matrix, sort_keys=True)) == matrix


def test_capability_matrix_rejects_mutated_citation_and_symbol(tmp_path: Path) -> None:
    roots = _pinned_roots()
    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=roots["gitnexus"],
    )
    matrix["cells"][0]["citation"]["sha256"] = "0" * 64
    assert not verify_capability_matrix(matrix, roots)

    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=roots["gitnexus"],
    )
    matrix["cells"][0]["citation"]["symbol"] = "invented_symbol"
    assert not verify_capability_matrix(matrix, roots)

    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=roots["gitnexus"],
    )
    matrix["cells"][0]["citation"]["revision"] = "8f1bef056e4be9138afe76c253014dc0f2d038af"
    assert not verify_capability_matrix(matrix, roots)


def test_checked_in_matrix_artifact_is_verifiable() -> None:
    roots = _pinned_roots()
    artifact = GT_ROOT / "gt_finalstand" / "receipts" / "har41_capability_matrix.json"
    matrix = load_capability_matrix(artifact)
    assert matrix["source_revision"] == GT_REVISION
    assert verify_capability_matrix(matrix, roots)
