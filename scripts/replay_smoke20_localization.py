#!/usr/bin/env python3
"""Provider-free, role-aware localization audit over a pinned DeepSWE cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from gt_harness.localization_truth import (
    LocalizationOracleTask,
    delivered_roles_from_packet,
    delivered_roles_from_provider_receipt,
    score_localization,
)
from gt_harness.treatments import GroundTruthTreatment

REPORT_SCHEMA = "gt.localization_truth_report.v2"
ORACLE_SCHEMA = "gt.localization_truth_oracle.v2"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compiler_fingerprint() -> str:
    """Bind reports to every implementation that can change delivered facts."""

    root = Path(__file__).resolve().parents[1]
    hasher = hashlib.sha256()
    for relative in (
        "eval/benchmark_product_contract.json",
        "gt_engine/task_contract.py",
        "gt_engine/hybrid_repository.py",
        "gt_engine/hybrid_retrieval.py",
        "gt_engine/repository_context_compiler.py",
        "gt_engine/graph_db_projection.py",
        "gt_engine/semantic_graph.py",
        "gt_engine/dense_semantic_index.py",
        "gt_harness/treatments.py",
        "gt_harness/localization_truth.py",
        "scripts/replay_smoke20_localization.py",
    ):
        path = root / relative
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0" + path.read_bytes() + b"\0")
    return hasher.hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        encoding="utf-8",
        stderr=subprocess.STDOUT,
    ).strip()


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _repository_for(
    task_id: str,
    row: dict[str, Any],
    repository_root: Path | None,
) -> Path:
    explicit = str(row.get("repository") or "").strip()
    if explicit:
        repository = Path(explicit).resolve()
    else:
        if repository_root is None:
            raise ValueError(f"{task_id}: portable manifest requires --repository-root")
        repository = (repository_root / task_id).resolve()
        url = str(row.get("repository_url") or "").strip()
        if not url:
            raise ValueError(f"{task_id}: repository_url missing")
        if not repository.exists():
            repository.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    "--filter=blob:none",
                    url,
                    str(repository),
                ],
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
    expected = str(row.get("base_sha") or "")
    if len(expected) != 40:
        raise ValueError(f"{task_id}: base_sha must be an exact 40-character SHA")
    try:
        _run_git(repository, "cat-file", "-e", f"{expected}^{{commit}}")
    except subprocess.CalledProcessError:
        _run_git(repository, "fetch", "--depth=1", "origin", expected)
    _run_git(repository, "checkout", "--detach", "--force", expected)
    _run_git(repository, "clean", "-ffd")
    head = _git(repository, "rev-parse", "HEAD")
    if head != expected:
        raise RuntimeError(
            f"{task_id}: repository revision mismatch: expected {expected}, got {head}"
        )
    return repository


def _load_oracle(path: Path) -> dict[str, LocalizationOracleTask]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != ORACLE_SCHEMA:
        raise ValueError(f"oracle schema must be {ORACLE_SCHEMA}")
    rows = tuple(LocalizationOracleTask.from_dict(row) for row in value.get("tasks", ()))
    result = {row.task_id: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("oracle contains duplicate task IDs")
    return result


def _resolve_source_path(source_root: Path | None, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if source_root is None:
        raise ValueError(f"relative benchmark path requires --benchmark-source: {value}")
    return (source_root / path).resolve()


def run_case(
    task_id: str,
    row: dict[str, Any],
    oracle: LocalizationOracleTask,
    state_root: Path,
    *,
    benchmark_source: Path | None,
    repository_root: Path | None,
    retrieval_mode: str,
) -> dict[str, Any]:
    repository = _repository_for(task_id, row, repository_root)
    expected_sha = str(row["base_sha"])
    if oracle.base_sha != expected_sha:
        raise ValueError(
            f"{task_id}: oracle SHA {oracle.base_sha} does not match manifest {expected_sha}"
        )
    instruction = _resolve_source_path(benchmark_source, str(row.get("instruction") or ""))
    task_text = instruction.read_text(encoding="utf-8")

    treatment = GroundTruthTreatment(
        repository,
        state_dir=state_root / task_id / "state",
        retrieval_mode=retrieval_mode,
    )
    context = treatment.prepare(task_text)
    receipt = treatment.finalize(None)
    compile_receipts = receipt.get("context_compile_receipts") or []
    if not compile_receipts:
        raise RuntimeError(f"{task_id}: typed compiler receipt missing")
    compile_receipt = compile_receipts[0]
    packet = compile_receipt.get("packet") or {}
    if compile_receipt.get("source_revision") != receipt.get("source_revision"):
        raise RuntimeError(f"{task_id}: compiler/source revision mismatch")
    compiled = delivered_roles_from_packet(packet)
    compiled_score = score_localization(oracle, compiled)
    delivery_receipts = receipt.get("provider_delivery_receipts") or []
    delivered = (
        delivered_roles_from_provider_receipt(delivery_receipts[0]) if delivery_receipts else {}
    )
    score = score_localization(oracle, delivered)
    compiled_evidence = {
        field: [
            {
                "path": item.get("path"),
                "symbol": item.get("symbol"),
                "decision_reason": item.get("decision_reason"),
                "facet_ids": list(item.get("facet_ids") or ()),
            }
            for item in packet.get(field, ())
            if isinstance(item, dict)
        ]
        for field in (
            "primary_edit_targets",
            "inspection_implementation_owners",
            "inspection_candidates",
            "inspection_public_surface",
            "inspection_integration",
        )
    }
    return {
        "task_id": task_id,
        "commit": _git(repository, "rev-parse", "HEAD"),
        "source_revision": receipt.get("source_revision"),
        "treatment_status": receipt.get("treatment_status"),
        "initial_delivery_disposition": receipt.get("initial_delivery_disposition"),
        "graph_available": receipt.get("graph_available"),
        "retrieval_mode": receipt.get("retrieval_mode"),
        "dense_query_ready": bool((receipt.get("dense_index_receipt") or {}).get("query_ready")),
        "context_schema_v7": 'schema="gt.agent_context.v7"' in context or not context,
        "typed_compile_receipt": True,
        "compiled_roles": {role: list(paths) for role, paths in compiled.items()},
        "compiled_evidence": compiled_evidence,
        "compiled_score": compiled_score.as_dict(),
        "delivered_roles": {role: list(paths) for role, paths in delivered.items()},
        "score": score.as_dict(),
        "token_count": receipt.get("initial_context_token_count"),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--benchmark-source", type=Path, default=None)
    parser.add_argument("--repository-root", type=Path, default=None)
    parser.add_argument("--tasks", type=Path, default=None)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid_required", "hybrid_if_available", "sparse_only"),
        default="hybrid_required",
    )
    parser.add_argument("--dense-model-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.dense_model_dir is not None:
        os.environ["GT_DENSE_MODEL_DIR"] = str(args.dense_model_dir.resolve())
    manifest_value = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_rows = (
        manifest_value.get("tasks", ()) if isinstance(manifest_value, dict) else manifest_value
    )
    oracle = _load_oracle(args.oracle)
    only_tasks = None
    if args.tasks is not None:
        only_tasks = frozenset(
            line.strip()
            for line in args.tasks.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if args.task_id:
        requested = frozenset(str(task_id).strip() for task_id in args.task_id)
        only_tasks = requested if only_tasks is None else only_tasks & requested
    selected = [
        row for row in manifest_rows if only_tasks is None or str(row.get("task_id")) in only_tasks
    ]
    selected_ids = {str(row.get("task_id")) for row in selected}
    oracle_ids = set(oracle)
    missing_oracles = sorted(selected_ids - oracle_ids)
    extra_oracles = sorted(oracle_ids - {str(row.get("task_id")) for row in manifest_rows})

    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for row in selected:
        task_id = str(row["task_id"])
        try:
            results.append(
                run_case(
                    task_id,
                    row,
                    oracle[task_id],
                    args.state_root,
                    benchmark_source=args.benchmark_source,
                    repository_root=args.repository_root,
                    retrieval_mode=args.retrieval_mode,
                )
            )
            print(f"{task_id}: OK", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - report every task failure
            failures.append(f"{task_id}: {exc}")
            print(f"{task_id}: FAIL {exc}", file=sys.stderr)

    exact_precisions = [
        float(result["score"]["exact_edit_precision"])
        for result in results
        if result["score"]["exact_edit_precision"] is not None
    ]
    coverages = [
        float(result["score"]["required_facet_coverage"])
        for result in results
        if result["score"]["required_facet_coverage"] is not None
    ]
    ambiguity_recalls = [
        float(result["score"]["ambiguity_candidate_recall"])
        for result in results
        if result["score"]["ambiguity_candidate_recall"] is not None
    ]
    implementation_rows = [
        result["score"]["role_metrics"].get("IMPLEMENTATION_OWNER", {}) for result in results
    ]
    implementation_delivered = sum(int(row.get("delivered") or 0) for row in implementation_rows)
    implementation_hits = sum(int(row.get("hits") or 0) for row in implementation_rows)
    implementation_acceptable = sum(
        int(row.get("acceptable") or 0) for row in implementation_rows
    )
    summary = {
        "schema": REPORT_SCHEMA,
        "compiler_fingerprint": _compiler_fingerprint(),
        "manifest_sha256": _sha256_file(args.manifest),
        "oracle_sha256": _sha256_file(args.oracle),
        "retrieval_mode": args.retrieval_mode,
        "cases_expected": len(selected),
        "cases_run": len(results),
        "case_failures": failures,
        "missing_oracle_tasks": missing_oracles,
        "extra_oracle_tasks": extra_oracles,
        "mean_exact_edit_precision": _mean(exact_precisions),
        "mean_required_facet_coverage": _mean(coverages),
        "mean_ambiguity_candidate_recall": _mean(ambiguity_recalls),
        "implementation_role_precision": (
            round(implementation_hits / implementation_delivered, 4)
            if implementation_delivered
            else None
        ),
        "implementation_role_recall": (
            round(implementation_hits / implementation_acceptable, 4)
            if implementation_acceptable
            else None
        ),
        "implementation_role_hits": implementation_hits,
        "implementation_role_delivered": implementation_delivered,
        "tasks_with_false_edit_authority": [
            result["task_id"] for result in results if result["score"]["false_edit_authority"]
        ],
        "tasks_below_half_required_coverage": [
            result["task_id"]
            for result in results
            if result["score"]["required_facet_coverage"] is not None
            and float(result["score"]["required_facet_coverage"]) < 0.5
        ],
        "treatment_failures": [
            {"task": result["task_id"], "status": result["treatment_status"]}
            for result in results
            if result["treatment_status"] != "ACTIVE"
        ],
        "dense_not_ready_tasks": [
            result["task_id"]
            for result in results
            if args.retrieval_mode == "hybrid_required" and not result["dense_query_ready"]
        ],
    }
    report_status = (
        "PASS"
        if not failures
        and len(results) == len(selected)
        and not missing_oracles
        and not extra_oracles
        and not summary["treatment_failures"]
        and not summary["dense_not_ready_tasks"]
        else "FAIL"
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(
            {
                "schema": REPORT_SCHEMA,
                "status": report_status,
                "provider_calls": 0,
                "provider_credentials_inspected": False,
                "summary": summary,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
