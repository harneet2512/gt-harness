#!/usr/bin/env python3
"""Provider-free, role-aware localization audit over a pinned DeepSWE cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
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


_COMPILER_INPUTS = (
    "pyproject.toml",
    "eval/benchmark_product_contract.json",
    "gt_engine/task_contract.py",
    "gt_engine/hybrid_repository.py",
    "gt_engine/hybrid_retrieval.py",
    "gt_engine/repository_context_compiler.py",
    "gt_engine/repository_architecture.py",
    "gt_engine/graph_db_projection.py",
    "gt_engine/semantic_graph.py",
    "gt_engine/dense_semantic_index.py",
    "gt_harness/treatments.py",
    "gt_harness/provider_planning.py",
    "gt_harness/localization_truth.py",
    "scripts/replay_smoke20_localization.py",
)


def _tracked_source_fingerprint(root: Path, relative_paths: tuple[str, ...]) -> str:
    """Hash canonical Git objects while still detecting substantive local edits.

    A clean checkout of one commit must have one compiler identity on every OS.
    Reading worktree bytes violated that invariant because Git may materialize
    LF blobs as CRLF. For clean paths we therefore bind to their committed blob
    IDs. A real staged, unstaged, deleted, or untracked compiler input is bound
    to normalized worktree content with an explicit ``DIRTY`` marker so a local
    modification can never masquerade as the frozen compiler.
    """

    hasher = hashlib.sha256()
    hasher.update(b"gt.compiler_fingerprint.v2\0")
    for relative in relative_paths:
        path = root / relative
        hasher.update(relative.encode("utf-8"))
        tracked = (
            subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                text=True,
                encoding="utf-8",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
        unstaged_clean = (
            subprocess.run(
                ["git", "-C", str(root), "diff", "--quiet", "--", relative],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
        staged_clean = (
            subprocess.run(
                ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", relative],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
        if tracked and unstaged_clean and staged_clean:
            blob_oid = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", f"HEAD:{relative}"],
                text=True,
                encoding="utf-8",
                stderr=subprocess.STDOUT,
            ).strip()
            hasher.update(b"\0CLEAN\0" + blob_oid.encode("ascii") + b"\0")
            continue
        content = path.read_bytes() if path.is_file() else b"<missing>"
        content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        hasher.update(b"\0DIRTY\0" + hashlib.sha256(content).digest() + b"\0")
    return hasher.hexdigest()


def _compiler_fingerprint() -> str:
    """Bind reports to every implementation that can change delivered facts."""

    root = Path(__file__).resolve().parents[1]
    return _tracked_source_fingerprint(root, _COMPILER_INPUTS)


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
    delivery_receipt = delivery_receipts[0] if delivery_receipts else {}
    delivered = (
        delivered_roles_from_provider_receipt(delivery_receipt) if delivery_receipt else {}
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
        "provider_plan": delivery_receipt.get("provider_plan"),
        "provider_claim_ledger": delivery_receipt.get("provider_claim_ledger", []),
        "provider_omitted_metadata": delivery_receipt.get("omitted_metadata", []),
        "provider_visible_feature_counts": delivery_receipt.get(
            "provider_visible_feature_counts"
        ),
        "score": score.as_dict(),
        "token_count": receipt.get("initial_context_token_count"),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _exception_receipt(exc: BaseException) -> str:
    """Preserve the causal chain needed to reproduce a failed product path."""

    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__, chain=True)
    ).rstrip()


def _isolated_case_worker(
    output: Path,
    task_id: str,
    row: dict[str, Any],
    oracle: LocalizationOracleTask,
    state_root: Path,
    benchmark_source: Path | None,
    repository_root: Path | None,
    retrieval_mode: str,
    dense_model_dir: Path | None,
) -> None:
    """Run one repository treatment behind a native-process boundary."""

    if dense_model_dir is not None:
        os.environ["GT_DENSE_MODEL_DIR"] = str(dense_model_dir.resolve())
    try:
        payload: dict[str, Any] = {
            "status": "OK",
            "result": run_case(
                task_id,
                row,
                oracle,
                state_root,
                benchmark_source=benchmark_source,
                repository_root=repository_root,
                retrieval_mode=retrieval_mode,
            ),
        }
    except BaseException as exc:  # noqa: BLE001 - preserve worker failure evidence
        payload = {"status": "FAIL", "failure": _exception_receipt(exc)}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def _run_case_isolated(
    task_id: str,
    row: dict[str, Any],
    oracle: LocalizationOracleTask,
    state_root: Path,
    *,
    benchmark_source: Path | None,
    repository_root: Path | None,
    retrieval_mode: str,
    dense_model_dir: Path | None,
    worker_timeout_seconds: float = 900.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return a task result or an explicit Python/native worker failure."""

    worker_key = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
    output = state_root / ".worker-results" / f"{worker_key}.json"
    output.unlink(missing_ok=True)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_isolated_case_worker,
        args=(
            output,
            task_id,
            row,
            oracle,
            state_root,
            benchmark_source,
            repository_root,
            retrieval_mode,
            dense_model_dir,
        ),
        name=f"gt-localization-{worker_key}",
    )
    process.start()
    timeout = max(1.0, float(worker_timeout_seconds))
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(min(10.0, timeout))
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(1.0)
        return None, f"isolated_worker_timeout:seconds={timeout:g}"
    if process.exitcode != 0:
        signal = -process.exitcode if process.exitcode and process.exitcode < 0 else None
        return None, (
            f"isolated_worker_exit:exit_code={process.exitcode}"
            + (f":signal={signal}" if signal is not None else "")
        )
    if not output.is_file():
        return None, "isolated_worker_receipt_missing"
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"isolated_worker_receipt_invalid:{type(exc).__name__}:{exc}"
    if payload.get("status") != "OK":
        return None, str(payload.get("failure") or "isolated_worker_failed_without_reason")
    result = payload.get("result")
    if not isinstance(result, dict):
        return None, "isolated_worker_result_invalid"
    return result, None


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
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Bounded concurrent isolated workers (default: 2).",
    )
    parser.add_argument(
        "--worker-timeout-seconds",
        type=float,
        default=1200.0,
        help="Hard timeout for each isolated worker (default: 1200 seconds).",
    )
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

    results_by_task: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    max_workers = max(1, min(int(args.max_workers), len(selected) or 1))

    def run_row(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        task_id = str(row["task_id"])
        result, failure = _run_case_isolated(
            task_id,
            row,
            oracle[task_id],
            args.state_root,
            benchmark_source=args.benchmark_source,
            repository_root=args.repository_root,
            retrieval_mode=args.retrieval_mode,
            dense_model_dir=args.dense_model_dir,
            worker_timeout_seconds=args.worker_timeout_seconds,
        )
        return task_id, result, failure

    # The thread pool only bounds how many isolated native processes are
    # active. Reassemble results in manifest order so scheduling cannot alter
    # the receipt or its digest.
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="gt-localization"
    ) as pool:
        futures = [pool.submit(run_row, row) for row in selected]
        for future in as_completed(futures):
            task_id, result, failure = future.result()
            if result is not None:
                results_by_task[task_id] = result
                print(f"{task_id}: OK", file=sys.stderr)
            else:
                reason = failure or "isolated_worker_failed_without_reason"
                failures.append(f"{task_id}: {reason}")
                print(f"{task_id}: FAIL\n{reason}", file=sys.stderr, flush=True)

    results = [
        results_by_task[task_id]
        for task_id in (str(row["task_id"]) for row in selected)
        if task_id in results_by_task
    ]
    failures.sort()

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
    implementation_facts = sum(int(row.get("facts") or 0) for row in implementation_rows)
    implementation_facts_covered = sum(
        int(row.get("facts_covered") or 0) for row in implementation_rows
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
            round(implementation_facts_covered / implementation_facts, 4)
            if implementation_facts
            else None
        ),
        "implementation_path_recall": (
            round(implementation_hits / implementation_acceptable, 4)
            if implementation_acceptable
            else None
        ),
        "implementation_facts": implementation_facts,
        "implementation_facts_covered": implementation_facts_covered,
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
