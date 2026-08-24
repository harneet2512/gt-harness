"""GT-Harness command-line product boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gt_engine.repository_graph_service import (
    SUPPORTED_QUERY_MODES,
    GraphNotReadyError,
    GraphReceipt,
    RepositoryGraphService,
    compute_repository_identity,
    public_graph_receipt,
)
from gt_harness.indexer_setup import ensure_source_indexer, find_go


def _emit(value: object, *, pretty: bool = True) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gt-harness",
        description="Model-agnostic benchmark harness with GroundTruth repository intelligence.",
    )
    parser.add_argument("--version", action="version", version="gt-harness 0.9.0")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Verify runtime, indexer, and product dependencies.")
    doctor.add_argument("--no-build", action="store_true", help="Inspect Go without compiling.")

    graph = sub.add_parser("graph", help="Build, inspect, or query the repository graph.")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)
    for name in ("build", "status"):
        item = graph_sub.add_parser(name)
        item.add_argument("--root", default=".")
        item.add_argument("--state-dir", default=None)
        item.add_argument(
            "--verbose",
            action="store_true",
            help="Emit the complete persisted graph receipt.",
        )
        if name == "build":
            item.add_argument("--force", action="store_true")
            item.add_argument("--timeout", type=float, default=600.0)
    query = graph_sub.add_parser("query")
    query.add_argument("mode", choices=SUPPORTED_QUERY_MODES)
    query.add_argument("symbol")
    query.add_argument("--root", default=".")
    query.add_argument("--state-dir", default=None)
    query.add_argument("--limit", type=int, default=50)
    query.add_argument("--file", default=None, help="Disambiguate a symbol by repository path.")
    query.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum relationship confidence (default: 0.5).",
    )
    query.add_argument("--refresh", action="store_true")

    run = sub.add_parser("run", help="Run the common coding-agent scaffold.")
    run.add_argument("task")
    run.add_argument("--model", required=True, help="Exact model identifier for both arms.")
    run.add_argument("--base-url", default=None)
    run.add_argument("--temperature", type=float, default=None)
    run.add_argument("--max-iterations", type=int, default=30)
    run.add_argument("--time-budget-seconds", type=float, default=None)
    run.add_argument("--treatment", choices=("bare", "groundtruth"), default="bare")
    run.add_argument("--root", default=".")
    run.add_argument("--state-dir", default=None, help="Private graph/runtime state directory.")
    run.add_argument("--run-id", default=None)
    run.add_argument("--task-id", default=None)
    run.add_argument("--trial-id", default="1")
    run.add_argument(
        "--output",
        default=None,
        help="Run receipt path (default: .groundtruth/runs/<run-id>.json).",
    )

    compare = sub.add_parser("compare", help="Compare completed benchmark treatment receipts.")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--treatment", required=True)
    compare.add_argument("--output", default=None)
    outcome = sub.add_parser(
        "record-outcome",
        help="Hash-bind an independently graded evaluator result to one run receipt.",
    )
    outcome.add_argument("--run-receipt", required=True)
    outcome.add_argument("--evaluator-receipt", required=True)
    outcome.add_argument("--output", required=True)
    outcomes = sub.add_parser(
        "record-harbor-outcomes",
        help="Bind all graded Harbor trials to their GT run receipts.",
    )
    outcomes.add_argument("--harbor-run-dir", required=True)
    outcomes.add_argument("--output-dir", required=True)
    certify = sub.add_parser("certify", help="Verify a complete product evidence bundle.")
    certify.add_argument(
        "--receipt-dir",
        required=True,
        help="Directory containing the Codespaces wrapper and required gate receipts.",
    )
    certify.add_argument("--root", default=".", help="Exact product checkout being certified.")
    certify.add_argument(
        "--expected-commit",
        default=None,
        help="Optional full SHA; must equal both the checkout and campaign evidence.",
    )
    certify.add_argument("--output", default=None, help="Optional certification receipt path.")
    return parser


def _doctor(*, build: bool) -> int:
    go = find_go()
    receipt = ensure_source_indexer() if build else None
    checks: dict[str, object] = {
        "schema": "gt.doctor.v1",
        "python": {
            "status": "READY" if sys.version_info >= (3, 12) else "FAILED",
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "git": {
            "status": "READY" if shutil.which("git") else "FAILED",
            "path": shutil.which("git") or "",
        },
        "go": {"status": "READY" if go else "FAILED", "path": str(go or "")},
        "indexer": (
            receipt.as_dict()
            if receipt is not None
            else {"status": "NOT_BUILT", "diagnostic": "--no-build requested"}
        ),
        "provider_credentials_required": False,
        "provider_calls": 0,
    }
    ready = all(
        isinstance(checks[name], dict) and checks[name]["status"] == "READY"
        for name in ("python", "git", "go")
    )
    if receipt is not None:
        ready = ready and receipt.status == "READY"
    checks["status"] = "READY" if ready else "FAILED"
    _emit(checks)
    return 0 if ready else 1


def _graph_receipt_output(
    service: RepositoryGraphService, receipt: GraphReceipt, *, verbose: bool
) -> dict[str, object]:
    value = receipt.as_dict()
    if verbose:
        return value
    return public_graph_receipt(receipt, receipt_path=service.receipt_path)


def _graph(args: argparse.Namespace) -> int:
    service = RepositoryGraphService(args.root, state_dir=args.state_dir)
    if args.graph_command == "build":
        try:
            receipt = service.build(force=args.force, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 - product boundary must fail explicitly
            _emit(
                {
                    "schema": "gt.graph_receipt_error.v1",
                    "status": "FAILED",
                    "query_ready": False,
                    "error_type": type(exc).__name__,
                    "error": " ".join(str(exc).split())[:2000],
                    "repository": str(service.root),
                    "receipt_path": str(service.receipt_path),
                }
            )
            return 1
        _emit(_graph_receipt_output(service, receipt, verbose=args.verbose))
        return 0 if receipt.query_ready else 1
    if args.graph_command == "status":
        receipt = service.status()
        _emit(_graph_receipt_output(service, receipt, verbose=args.verbose))
        return 0 if receipt.query_ready else 1
    if args.refresh and not service.status().query_ready:
        service.build()
    try:
        _emit(
            service.query(
                args.mode,
                args.symbol,
                limit=args.limit,
                file_path=args.file,
                min_confidence=args.min_confidence,
            )
        )
        return 0
    except (GraphNotReadyError, ValueError) as exc:
        _emit({"schema": "gt.graph_query.v1", "status": "FAILED", "error": str(exc)})
        return 1


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        # Harbor mounts /logs back to the host.  Its task process can run as
        # container root, so NamedTemporaryFile's 0600 mode otherwise leaves
        # the GitHub runner unable to bind or upload the final/checkpointed
        # receipt.  The receipt deliberately contains no provider credential.
        os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _receipt_event(event: dict[str, object]) -> dict[str, object]:
    """Convert a live agent event into durable JSON without losing tool calls."""

    def default(value: object) -> object:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump()
        return str(value)

    return json.loads(json.dumps(event, ensure_ascii=False, default=default))


def _run_repository_identity(root: Path) -> dict[str, object]:
    identity = compute_repository_identity(root)
    return {
        "repository": identity.repository,
        "commit_sha": identity.commit_sha,
        "branch": identity.branch,
        "working_tree_state": identity.working_tree_state,
        "source_revision": identity.source_revision,
        "files_discovered": identity.files_discovered,
        "graph_input_files": identity.graph_input_files,
        "source_bytes": identity.source_bytes,
    }


def _run_agent(args: argparse.Namespace) -> int:
    from gt_harness.miniswe_runner import (
        BASH_TOOL,
        build_miniswe_agent,
        run_miniswe_agent,
    )
    from gt_harness.treatments import BareTreatment, GroundTruthTreatment

    root = Path(args.root).resolve()
    temperature = getattr(args, "temperature", None)
    requested_run_id = getattr(args, "run_id", None)
    output_value = getattr(args, "output", None)
    state_dir = getattr(args, "state_dir", None)
    generated_run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    run_id = str(requested_run_id or generated_run_id)
    task_fingerprint = hashlib.sha256(args.task.encode("utf-8")).hexdigest()
    task_id = str(getattr(args, "task_id", None) or f"task-{task_fingerprint[:16]}")
    trial_id = str(getattr(args, "trial_id", "1") or "1")
    base_url = getattr(args, "base_url", None)
    run_configuration: dict[str, object] = {
        "model": args.model,
        "base_url_configured": bool(base_url),
        "base_url_sha256": (
            hashlib.sha256(str(base_url).encode("utf-8")).hexdigest() if base_url else None
        ),
        "temperature": temperature,
        "max_iterations": int(args.max_iterations),
        "time_budget_seconds": args.time_budget_seconds,
        "agent_scaffold": "minisweagent.agents.default.DefaultAgent",
        "agent_scaffold_version": "2.2.8",
        "system_prompt_sha256": None,
        "tool_policy_sha256": _sha256_json(BASH_TOOL),
    }
    repository_start = _run_repository_identity(root)
    output_path = (
        Path(output_value).resolve()
        if output_value
        else root / ".groundtruth" / "runs" / f"{run_id}.json"
    )
    treatment = (
        GroundTruthTreatment(root, state_dir=state_dir)
        if args.treatment == "groundtruth"
        else BareTreatment()
    )
    started = _now()
    started_clock = time.perf_counter()
    checkpoint_events: list[dict[str, object]] = []
    checkpoint_provider_calls = 0
    checkpoint_input_tokens = 0
    checkpoint_output_tokens = 0
    checkpoint_cached_tokens = 0
    checkpoint_repository_end = repository_start
    initial_context = ""

    def write_checkpoint() -> None:
        try:
            treatment_receipt = treatment.finalize(None)
        except Exception as exc:  # noqa: BLE001 - retain progress even if telemetry fails
            treatment_receipt = {
                "schema": "gt.treatment_receipt.v1",
                "treatment": args.treatment,
                "treatment_status": "FAILED",
                "errors": [f"checkpoint_finalize_failed:{type(exc).__name__}"],
            }
        _write_json_atomic(
            output_path,
            {
                "schema": "gt.run_receipt.v1",
                "run_id": run_id,
                "task_id": task_id,
                "task_fingerprint": task_fingerprint,
                "task": args.task,
                "trial_id": trial_id,
                "status": "RUNNING",
                "started": started,
                "completed": None,
                "duration_ms": round(
                    (time.perf_counter() - started_clock) * 1000, 3
                ),
                "repository": str(root),
                **run_configuration,
                "initial_context": initial_context,
                "initial_context_sha256": (
                    hashlib.sha256(initial_context.encode("utf-8")).hexdigest()
                    if initial_context
                    else None
                ),
                "repository_start": repository_start,
                "repository_end": checkpoint_repository_end,
                "treatment": args.treatment,
                "resolved": None,
                "stop_reason": None,
                "provider_calls": checkpoint_provider_calls,
                "input_tokens": checkpoint_input_tokens,
                "output_tokens": checkpoint_output_tokens,
                "cached_tokens": checkpoint_cached_tokens,
                "treatment_receipt": treatment_receipt,
                "treatment_receipt_present": True,
                "transcript": list(checkpoint_events),
            },
        )

    def handle_message(message: dict[str, object]) -> None:
        nonlocal checkpoint_provider_calls
        nonlocal checkpoint_input_tokens
        nonlocal checkpoint_output_tokens
        nonlocal checkpoint_cached_tokens
        nonlocal checkpoint_repository_end
        normalized = _receipt_event(message)
        checkpoint_events.append(normalized)
        role = str(normalized.get("role") or "")
        if role == "assistant":
            checkpoint_provider_calls += 1
            extra = normalized.get("extra")
            response = extra.get("response") if isinstance(extra, dict) else None
            usage = response.get("usage") if isinstance(response, dict) else None
            if isinstance(usage, dict):
                checkpoint_input_tokens += int(usage.get("prompt_tokens") or 0)
                checkpoint_output_tokens += int(usage.get("completion_tokens") or 0)
                details = usage.get("prompt_tokens_details")
                if isinstance(details, dict):
                    checkpoint_cached_tokens += int(details.get("cached_tokens") or 0)
        if role in {"tool", "user"} and normalized.get("extra"):
            try:
                checkpoint_repository_end = _run_repository_identity(root)
            except Exception:  # noqa: BLE001 - next event/final receipt can recover
                pass
        write_checkpoint()

    try:
        agent = build_miniswe_agent(
            model=args.model,
            root=root,
            treatment=treatment,
            base_url=base_url,
            temperature=temperature,
            max_iterations=args.max_iterations,
            time_budget_seconds=args.time_budget_seconds,
            trajectory_path=output_path.with_suffix(".trajectory.json"),
            on_message=handle_message,
        )
        run_configuration["system_prompt_sha256"] = hashlib.sha256(
            (
                agent.config.system_template
                + "\x1f"
                + agent.config.instance_template
            ).encode("utf-8")
        ).hexdigest()
        # Build and freeze the initial GT packet before the first checkpoint.
        # Agent.run invokes prepare again, but GroundTruthTreatment caches the
        # exact packet, so this remains one production delivery.
        if args.treatment == "groundtruth":
            initial_context = treatment.prepare(args.task)
        # A Harbor/CI timeout may kill the process before Agent.run returns.
        # Persist the pair identity and current graph state before any work.
        write_checkpoint()
    except Exception as exc:  # noqa: BLE001 - setup failure must still leave a receipt
        receipt = {
            "schema": "gt.run_receipt.v1",
            "run_id": run_id,
            "task_id": task_id,
            "task_fingerprint": task_fingerprint,
            "trial_id": trial_id,
            "task": args.task,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "started": started,
            "completed": _now(),
            "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
            "repository": str(root),
            **run_configuration,
            "initial_context": initial_context,
            "initial_context_sha256": (
                hashlib.sha256(initial_context.encode("utf-8")).hexdigest()
                if initial_context
                else None
            ),
            "repository_start": repository_start,
            "repository_end": _run_repository_identity(root),
            "treatment": args.treatment,
            "resolved": None,
            "provider_calls": 0,
        }
        _write_json_atomic(output_path, receipt)
        _emit({**receipt, "receipt_path": str(output_path)})
        return 1
    try:
        result = run_miniswe_agent(agent, args.task)
    except Exception as exc:  # noqa: BLE001 - preserve all durable runtime evidence
        try:
            treatment_receipt = treatment.finalize(None)
        except Exception as receipt_exc:  # noqa: BLE001 - preserve the primary error
            treatment_receipt = {
                "schema": "gt.treatment_receipt.v1",
                "treatment": args.treatment,
                "treatment_status": "FAILED",
                "errors": [
                    f"{type(exc).__name__}:{exc}",
                    f"finalize_failed:{type(receipt_exc).__name__}",
                ],
            }
        receipt = {
            "schema": "gt.run_receipt.v1",
            "run_id": run_id,
            "task_id": task_id,
            "task_fingerprint": task_fingerprint,
            "trial_id": trial_id,
            "task": args.task,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "started": started,
            "completed": _now(),
            "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
            "repository": str(root),
            **run_configuration,
            "initial_context": initial_context,
            "initial_context_sha256": (
                hashlib.sha256(initial_context.encode("utf-8")).hexdigest()
                if initial_context
                else None
            ),
            "repository_start": repository_start,
            "repository_end": _run_repository_identity(root),
            "treatment": args.treatment,
            "resolved": None,
            "provider_calls": checkpoint_provider_calls,
            "input_tokens": checkpoint_input_tokens,
            "output_tokens": checkpoint_output_tokens,
            "cached_tokens": checkpoint_cached_tokens,
            "treatment_receipt": treatment_receipt,
            "treatment_receipt_present": True,
            "transcript": [
                *checkpoint_events,
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
                {"type": "treatment_receipt", "receipt": treatment_receipt},
            ],
        }
        _write_json_atomic(output_path, receipt)
        _emit(
            {
                "schema": receipt["schema"],
                "run_id": run_id,
                "status": receipt["status"],
                "error_type": receipt["error_type"],
                "provider_calls": checkpoint_provider_calls,
                "receipt_path": str(output_path),
                "treatment_receipt_present": True,
            }
        )
        return 1
    treatment_receipt = treatment.finalize(result)
    provider_calls = result.iterations
    checkpoint_input_tokens = result.total_input_tokens
    checkpoint_output_tokens = result.total_output_tokens
    completed_normally = result.stop_reason in {"Submitted", "LimitsExceeded"}
    transcript = [*result.transcript, {"type": "treatment_receipt", "receipt": treatment_receipt}]
    receipt = {
        "schema": "gt.run_receipt.v1",
        "run_id": run_id,
        "task_id": task_id,
        "task_fingerprint": task_fingerprint,
        "trial_id": trial_id,
        "task": args.task,
        "status": "COMPLETED" if completed_normally else "ERROR",
        "started": started,
        "completed": _now(),
        "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
        "repository": str(root),
        **run_configuration,
        "initial_context": initial_context,
        "initial_context_sha256": (
            hashlib.sha256(initial_context.encode("utf-8")).hexdigest()
            if initial_context
            else None
        ),
        "repository_start": repository_start,
        "repository_end": _run_repository_identity(root),
        "treatment": args.treatment,
        "resolved": None,
        "stop_reason": result.stop_reason,
        "iterations": result.iterations,
        "provider_calls": provider_calls,
        "input_tokens": result.total_input_tokens,
        "output_tokens": result.total_output_tokens,
        "cached_tokens": result.total_cache_read_tokens,
        "treatment_receipt": treatment_receipt,
        "treatment_receipt_present": True,
        "transcript": transcript,
    }
    _write_json_atomic(output_path, receipt)
    _emit(
        {
            "schema": receipt["schema"],
            "run_id": run_id,
            "status": receipt["status"],
            "stop_reason": result.stop_reason,
            "provider_calls": provider_calls,
            "receipt_path": str(output_path),
            "treatment_receipt_present": True,
        }
    )
    return 0 if completed_normally else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(build=not args.no_build)
    if args.command == "graph":
        return _graph(args)
    if args.command == "record-outcome":
        from gt_harness.outcomes import OutcomeBindingError, bind_evaluator_outcome

        try:
            receipt = bind_evaluator_outcome(
                args.run_receipt,
                args.evaluator_receipt,
                args.output,
            )
        except OutcomeBindingError as exc:
            _emit(
                {
                    "schema": "gt.evaluation_binding_error.v1",
                    "status": "FAILED",
                    "error": str(exc),
                }
            )
            return 1
        _emit(
            {
                "schema": "gt.evaluation_binding.v1",
                "status": "BOUND",
                "task_id": receipt["task_id"],
                "trial_id": receipt["trial_id"],
                "resolved": receipt["resolved"],
                "output": str(Path(args.output).resolve()),
            }
        )
        return 0
    if args.command == "record-harbor-outcomes":
        from gt_harness.outcomes import OutcomeBindingError, bind_harbor_run_directory

        try:
            summary = bind_harbor_run_directory(
                args.harbor_run_dir,
                args.output_dir,
            )
        except OutcomeBindingError as exc:
            _emit(
                {
                    "schema": "gt.evaluated_run_collection.v1",
                    "status": "FAILED",
                    "error": str(exc),
                }
            )
            return 1
        _emit(summary)
        return 0
    if args.command == "run":
        return _run_agent(args)
    if args.command == "compare":
        from gt_harness.comparison import ComparisonError, compare_receipt_paths

        try:
            report = compare_receipt_paths(args.baseline, args.treatment)
        except ComparisonError as exc:
            _emit({"schema": "gt.paired_comparison.v1", "status": "FAILED", "error": str(exc)})
            return 1
        if args.output:
            _write_json_atomic(Path(args.output).resolve(), report)
        _emit(report)
        return 0 if report["status"] == "COMPLETE" else 1
    if args.command == "certify":
        from gt_harness.product_certification import certify_receipt_bundle

        report = certify_receipt_bundle(
            args.receipt_dir,
            repository=args.root,
            expected_commit=args.expected_commit,
        )
        if args.output:
            _write_json_atomic(Path(args.output).resolve(), report)
        _emit(report)
        return 0 if report["status"] == "CERTIFIED_WITH_DECLARED_LIMITATIONS" else 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
