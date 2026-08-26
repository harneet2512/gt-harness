"""Bind independently graded evaluator facts to a GT run receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class OutcomeBindingError(ValueError):
    """Raised when evaluator evidence cannot prove one run outcome."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeBindingError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise OutcomeBindingError(f"{label} must be a JSON object")
    return dict(value), payload


def _task_name(row: dict[str, Any]) -> str:
    # Harbor 0.20 uses ``task_id`` for a provenance object
    # (git_url/git_commit_id/path).  Stringifying that object used to shadow
    # the actual scalar task_name and made every production result unbindable.
    value: object = row.get("task_name")
    if not isinstance(value, str) or not value.strip():
        task_id = row.get("task_id")
        if isinstance(task_id, str):
            value = task_id
        elif isinstance(task_id, dict):
            value = task_id.get("path") or task_id.get("name") or ""
        else:
            value = ""
    if not isinstance(value, str) or not value.strip():
        value = row.get("trial_name") or ""
    value = str(value)
    if "__" in value and not row.get("task_name"):
        value = value.split("__", 1)[0]
    return value.rstrip("/").rsplit("/", 1)[-1]


def _harbor_outcome(row: dict[str, Any]) -> bool | None:
    rewards = (row.get("verifier_result") or {}).get("rewards")
    if not isinstance(rewards, dict) or not rewards:
        return None
    values = tuple(
        value
        for value in rewards.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    if len(values) != len(rewards):
        return None
    return all(value >= 1 for value in values)


def _derive_outcome(evaluator: dict[str, Any], *, task_id: str) -> tuple[bool, str, dict[str, Any]]:
    candidates = evaluator.get("trial_results")
    rows = (
        tuple(row for row in candidates if isinstance(row, dict))
        if isinstance(candidates, list)
        else (evaluator,)
    )
    matches: list[tuple[bool, dict[str, Any]]] = []
    for row in rows:
        if _task_name(row) != task_id:
            continue
        outcome = _harbor_outcome(row)
        if outcome is not None:
            matches.append((outcome, row))
    if len(matches) == 1:
        return matches[0][0], "harbor", matches[0][1]
    if len(matches) > 1:
        canonical = {_canonical(row) for _, row in matches}
        outcomes = {outcome for outcome, _ in matches}
        if len(canonical) == 1 and len(outcomes) == 1:
            return matches[0][0], "harbor", matches[0][1]
        raise OutcomeBindingError("evaluator contains conflicting matching graded tasks")

    if _task_name(evaluator) == task_id and isinstance(evaluator.get("resolved"), bool):
        return bool(evaluator["resolved"]), "direct", evaluator
    raise OutcomeBindingError("evaluator contains no matching graded task")


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode())
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def bind_evaluator_outcome(
    run_receipt_path: str | Path,
    evaluator_receipt_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Return and persist one run receipt bound to independent grading evidence."""

    run_path = Path(run_receipt_path).resolve()
    evaluator_path = Path(evaluator_receipt_path).resolve()
    destination = Path(output_path).resolve()
    run, run_bytes = _read_object(run_path, label="run receipt")
    evaluator, evaluator_bytes = _read_object(evaluator_path, label="evaluator receipt")
    if run.get("schema") != "gt.run_receipt.v1":
        raise OutcomeBindingError("unsupported run receipt schema")
    run_status = str(run.get("status") or "")
    if run_status not in {"COMPLETED", "ERROR"}:
        raise OutcomeBindingError(f"unsupported run receipt status: {run_status or 'missing'}")
    if isinstance(run.get("resolved"), bool) or run.get("evaluation") is not None:
        raise OutcomeBindingError("run receipt already has an evaluator outcome")
    task_id = str(run.get("task_id") or "").strip()
    if not task_id:
        raise OutcomeBindingError("run receipt has no task_id")
    resolved, evaluator_format, evaluator_row = _derive_outcome(evaluator, task_id=task_id)
    result = dict(run)
    result["resolved"] = resolved
    termination = run.get("termination")
    termination_kind = str(termination.get("kind") or "") if isinstance(termination, dict) else ""
    infrastructure_disposition = (
        "NONE"
        if run_status == "COMPLETED"
        else "PROVIDER_TRANSPORT"
        if termination_kind == "PROVIDER_TRANSPORT"
        else "ORCHESTRATOR_TIMEOUT"
        if termination_kind in {"TIMEOUT", "CANCELLED"}
        else "PRODUCT_ERROR"
    )
    result["evaluation"] = {
        "schema": "gt.evaluation_binding.v1",
        "task_id": task_id,
        "trial_id": str(run.get("trial_id") or ""),
        "resolved": resolved,
        "run_status_at_binding": run_status,
        "evaluator_format": evaluator_format,
        "evaluator_receipt_name": evaluator_path.name,
        "run_receipt_sha256": hashlib.sha256(run_bytes).hexdigest(),
        "evaluator_receipt_sha256": hashlib.sha256(evaluator_bytes).hexdigest(),
        "evaluator_row_sha256": hashlib.sha256(_canonical(evaluator_row)).hexdigest(),
        "infrastructure_disposition": infrastructure_disposition,
        "termination_kind": termination_kind or None,
        "official_verifier_authoritative": True,
    }
    _write_atomic(destination, result)
    return result


def bind_harbor_run_directory(
    harbor_run_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Bind every Harbor trial's GT run receipt to its colocated grader result."""

    root = Path(harbor_run_dir).resolve()
    destination = Path(output_dir).resolve()
    run_paths = sorted(root.rglob("agent/gt-run.json"))
    if not run_paths:
        raise OutcomeBindingError("Harbor run contains no agent/gt-run.json receipts")
    bound: list[dict[str, Any]] = []
    for run_path in run_paths:
        trial_dir = run_path.parent.parent
        evaluator_path = trial_dir / "result.json"
        if not evaluator_path.is_file():
            raise OutcomeBindingError(f"Harbor trial has no evaluator result: {trial_dir.name}")
        run, _ = _read_object(run_path, label="run receipt")
        task_id = str(run.get("task_id") or "").strip()
        trial_id = str(run.get("trial_id") or "").strip()
        if not task_id or not trial_id:
            raise OutcomeBindingError(f"run receipt lacks pair identity: {run_path}")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{task_id}--{trial_id}")
        output_path = destination / f"{safe_name}.json"
        bound.append(bind_evaluator_outcome(run_path, evaluator_path, output_path))
    incomplete = sum(1 for row in bound if row.get("status") != "COMPLETED")
    return {
        "schema": "gt.evaluated_run_collection.v1",
        "status": ("COMPLETE_WITH_INCOMPLETE_RUNS" if incomplete else "COMPLETE"),
        "bound_receipts": len(bound),
        "incomplete_run_receipts": incomplete,
        "pairs": [{"task_id": row["task_id"], "trial_id": row["trial_id"]} for row in bound],
        "output_dir": str(destination),
    }


__all__ = [
    "OutcomeBindingError",
    "bind_evaluator_outcome",
    "bind_harbor_run_directory",
]
