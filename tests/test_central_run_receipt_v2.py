from __future__ import annotations

import hashlib
import json

from gt_engine.central_run_receipt_v2 import finalize_central_run_receipt
from gt_engine.run_receipt_v2 import RunReceiptFinalizer, is_complete_run_receipt


def test_central_projection_preserves_graph_and_exact_delivery_identity(tmp_path):
    visible = b"edit owners:\n- src/identity.py#resolve_identity"
    repository_revision = "r" * 64
    graph_revision = "g" * 64
    central = {
        "source_revision": repository_revision,
        "calls": 1,
        "metrics": {"api_calls": 1, "input_tokens": 10, "output_tokens": 2},
        "repository_work_receipts": [
            {
                "kind": "initial_index",
                "status": "source_backed",
                "source_revision": repository_revision,
                "graph_revision": graph_revision,
                "duration_ms": 4.5,
            }
        ],
        "source_file_digests": {"src/identity.py": "c" * 64},
        "semantic_evidence": {
            "deliveries": [
                {
                    "delivery_id": "semantic-evidence-1",
                    "source_revision": repository_revision,
                    "graph_revision": graph_revision,
                    "request_payload_sha256": "p" * 64,
                    "model_visible_bytes_hex": visible.hex(),
                    "model_visible_bytes_sha256": hashlib.sha256(visible).hexdigest(),
                    "items": [
                        {
                            "kind": "definition",
                            "path": "src/identity.py",
                            "line": 7,
                            "symbol": "resolve_identity",
                            "language": "python",
                            "content_sha256": "c" * 64,
                            "semantic_certainty": 1.0,
                            "retrieval_relevance": 1.0,
                        }
                    ],
                }
            ]
        },
    }
    trajectory = {
        "info": {"exit_status": "Submitted"},
        "messages": [{"role": "assistant", "extra": {"actions": []}}],
    }
    (tmp_path / "central_receipt.json").write_text(
        json.dumps(central), encoding="utf-8"
    )
    (tmp_path / "miniswe_trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )
    finalizer = RunReceiptFinalizer(
        tmp_path / "gt-run-receipt.json",
        task_id="fixture",
        requested_model="fixture/model",
    )

    receipt = finalize_central_run_receipt(finalizer, logs_dir=tmp_path)

    assert is_complete_run_receipt(receipt)
    assert receipt["graph_build_count"] == 1
    assert receipt["initial_repository_revision"] == repository_revision
    assert receipt["deliveries"][0]["graph_revision"] == graph_revision
    assert receipt["graph_revisions_used_for_deliveries"] == [graph_revision]
    assert bytes.fromhex(receipt["deliveries"][0]["model_visible_bytes_hex"]) == visible
    lifecycle = receipt["feature_lifecycle_transitions"][0]
    assert lifecycle["stage"] == "DELIVERED"
    assert lifecycle["feature_id"] == "implementation_owner"
    assert lifecycle["claims"][0]["symbol_identity"] == (
        "python:src/identity.py:resolve_identity"
    )
