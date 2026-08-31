from __future__ import annotations

import hashlib
import json

from gt_engine.graph_context import build_capability_matrix, verify_capability_matrix


def _entry(capability: str, state: str, path: str, blob: bytes) -> dict[str, object]:
    return {
        "capability": capability,
        "state": state,
        "citation": {"path": path, "sha256": hashlib.sha256(blob).hexdigest()},
    }


def test_capability_matrix_is_sorted_and_source_bound() -> None:
    source = {"gt_engine/indexer.py": b"producer", "README.md": b"comparison"}
    matrix = build_capability_matrix(
        [_entry("graph", "implemented", "gt_engine/indexer.py", source["gt_engine/indexer.py"])],
        [_entry("graph", "evidenced", "README.md", source["README.md"])],
        source_revision="a" * 40,
        gitnexus_revision="7e993ab8972386294fb96bf14a8665d0b5325397",
    )
    assert matrix["schema"] == "gt.capability_matrix.v1"
    assert verify_capability_matrix(matrix, source)
    assert [cell["tool"] for cell in matrix["cells"]] == ["gitnexus", "gt"]
    assert json.loads(json.dumps(matrix, sort_keys=True)) == matrix


def test_capability_matrix_rejects_mutated_citation_bytes() -> None:
    blob = b"producer"
    matrix = build_capability_matrix(
        [_entry("graph", "implemented", "gt_engine/indexer.py", blob)],
        [],
        source_revision="a" * 40,
        gitnexus_revision="7e993ab8972386294fb96bf14a8665d0b5325397",
    )
    assert not verify_capability_matrix(matrix, {"gt_engine/indexer.py": b"mutated"})
