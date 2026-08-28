"""Run the frozen decision-value corpus through the production GT boundaries.

This is a provider-free mechanics proof.  It uses the real indexer, graph
lease, task-start evidence pipeline, provider request binding, uptake binding,
execution validation, and v2 finalizer.  The scripted consumer performs one
read-only search selected from the delivered owner claim; it does not solve a
benchmark task and cannot support a solve-rate claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.bridge import apply_profile_env  # noqa: E402
from gt_engine.decision_value_corpus import load_decision_value_corpus  # noqa: E402
from gt_engine.engine.runner import _ensure_gateway_flags  # noqa: E402
from gt_engine.indexer import ensure_index_with_receipt  # noqa: E402
from gt_engine.miniswe_integration import MiniSweAdapter  # noqa: E402
from gt_engine.run_receipt_v2 import RunReceiptFinalizer  # noqa: E402
from gt_engine.runtime_observation import capture_workspace  # noqa: E402
from scripts.miniswe_gt_run import _record_adapter_run_receipts  # noqa: E402

_MODEL = "provider-free-scripted-consumer"


def _owner_claim(adapter: MiniSweAdapter) -> dict:
    for lifecycle in adapter.feature_lifecycle_receipts():
        for claim in lifecycle.get("claims") or ():
            if claim.get("role") == "edit_owner":
                return dict(claim)
    raise RuntimeError("production pipeline emitted no certified edit-owner claim")


def _run_case(case, output_root: Path) -> dict:
    case_root = output_root / case.case_id
    state_root = case_root / "state"
    index_receipt = ensure_index_with_receipt(
        str(case.repository),
        state_dir=str(state_root),
    )
    if not index_receipt.available or not index_receipt.schema_valid:
        raise RuntimeError(f"{case.case_id}: graph index is unavailable or invalid")
    adapter = MiniSweAdapter(
        task_id=case.case_id,
        state_dir=state_root,
        predicates=(),
        repo_root=str(case.repository),
        graph_db=index_receipt.graph_db,
        issue_text=case.task,
        requested_model=_MODEL,
        resolved_model=_MODEL,
    )
    adapter.initial_index_receipt = index_receipt
    snapshot = capture_workspace(
        case.repository,
        excluded_roots=(state_root,),
    )
    if snapshot.revision != case.repository_revision:
        raise RuntimeError(
            f"{case.case_id}: frozen repository revision mismatch: "
            f"expected {case.repository_revision}, observed {snapshot.revision}"
        )
    adapter.record_repository_snapshot(snapshot, boundary="repository_start")
    localization = adapter.task_start_localization()
    if not localization:
        raise RuntimeError(f"{case.case_id}: no certifiable task-start delivery")
    delivery = adapter.bind_provider_payload(
        {
            "model": _MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": f"{case.task}\n\n{localization}",
                }
            ],
        },
        model_visible_additions=(localization,),
    )
    claim = _owner_claim(adapter)
    symbol = str(claim.get("symbol_identity") or "").rsplit(":", 1)[-1]
    if not symbol:
        raise RuntimeError(f"{case.case_id}: owner claim has no symbol identity")
    command = f"rg -n --fixed-strings {symbol} ."
    adapter.bind_provider_response(
        {
            "id": f"provider-free:{case.case_id}",
            "model": _MODEL,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        model=_MODEL,
        next_actions=({"command": command},),
    )
    result = subprocess.run(
        ["rg", "-n", "--fixed-strings", symbol, "."],
        cwd=case.repository,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout + result.stderr
    adapter.validate_consumed_lifecycles(
        command,
        output=output,
        returncode=result.returncode,
    )
    lifecycle = next(
        row
        for row in adapter.feature_lifecycle_receipts()
        if row.get("feature_id") == "implementation_owner"
    )
    if lifecycle.get("stage") != "VALIDATED":
        raise RuntimeError(
            f"{case.case_id}: owner lifecycle ended at {lifecycle.get('stage')}"
        )

    receipt_path = case_root / "gt-run-receipt.json"
    finalizer = RunReceiptFinalizer(
        receipt_path,
        task_id=case.case_id,
        requested_model=_MODEL,
    )
    finalizer.record_provider_usage(
        calls=0,
        input_tokens=0,
        output_tokens=0,
        duration_ms=0,
    )
    _record_adapter_run_receipts(finalizer, adapter)
    receipt = finalizer.finalize(
        terminal="provider_free_fixture_complete",
        infrastructure_classification="COMPLETED",
        trajectory={
            "messages": [
                {
                    "role": "assistant",
                    "extra": {
                        "actions": [
                            {
                                "command": command,
                                "operation": "inspect",
                                "targets": [
                                    evidence["path"]
                                    for evidence in claim.get("source_evidence") or ()
                                ],
                            }
                        ]
                    },
                }
            ]
        },
    )
    return {
        "case_id": case.case_id,
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "repository_revision": snapshot.revision,
        "graph_revision": delivery.graph_revision,
        "owner": claim["symbol_identity"],
        "lifecycle_stage": lifecycle["stage"],
        "provider_calls": receipt["provider_usage"]["calls"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    corpus_path = Path(args.corpus).resolve()
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    apply_profile_env()
    _ensure_gateway_flags()
    os.environ["GT_GATEWAY_NATIVE"] = "1"
    corpus = load_decision_value_corpus(corpus_path)
    rows = [_run_case(case, output_root) for case in corpus.cases]
    manifest = {
        "schema": "gt.decision_value_fixture_pilot.v1",
        "corpus": str(corpus_path),
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "provider_calls": sum(int(row["provider_calls"]) for row in rows),
        "cases": rows,
    }
    manifest_path = output_root / "pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
