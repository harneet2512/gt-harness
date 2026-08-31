from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gt_engine.graph_context import (
    GITNEXUS_PINNED_REVISION,
    build_capability_matrix,
    load_capability_matrix,
    persist_capability_matrix,
    verify_capability_matrix,
)

GT_REVISION = subprocess.run(
    ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
).stdout.strip()
GITNEXUS_ROOT = Path(r"D:\gt-harness\.research\gitnexus-current")
GT_ROOT = Path(__file__).parents[1]


def test_capability_matrix_is_source_backed_and_persisted(tmp_path: Path) -> None:
    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=GITNEXUS_ROOT,
    )
    assert matrix["schema"] == "gt.capability_matrix.v2"
    assert matrix["gitnexus_revision"] == GITNEXUS_PINNED_REVISION
    assert verify_capability_matrix(matrix, {"gt": GT_ROOT, "gitnexus": GITNEXUS_ROOT})
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
    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=GITNEXUS_ROOT,
    )
    matrix["cells"][0]["citation"]["sha256"] = "0" * 64
    assert not verify_capability_matrix(matrix, {"gt": GT_ROOT, "gitnexus": GITNEXUS_ROOT})

    matrix = build_capability_matrix(
        GT_ROOT,
        gt_revision=GT_REVISION,
        gitnexus_root=GITNEXUS_ROOT,
    )
    matrix["cells"][0]["citation"]["symbol"] = "invented_symbol"
    assert not verify_capability_matrix(matrix, {"gt": GT_ROOT, "gitnexus": GITNEXUS_ROOT})


def test_checked_in_matrix_artifact_is_verifiable() -> None:
    artifact = GT_ROOT / "gt_finalstand" / "receipts" / "har41_capability_matrix.json"
    matrix = load_capability_matrix(artifact)
    assert matrix["source_revision"] == "8f1bef056e4be9138afe76c253014dc0f2d038af"
    assert verify_capability_matrix(matrix, {"gt": GT_ROOT, "gitnexus": GITNEXUS_ROOT})
