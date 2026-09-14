"""Ensure every planned task ships a complete diagnostics trio.

The in-container ``DiagnosticJournal.seal`` (``gt_engine/gt_session.py``)
writes ``diagnostics.json`` + ``diagnostics.txt`` + ``incident-replay.json``
only when the agent session reaches its close path. A trial that ends
through a provider failure, a governor abort, or a runner crash can die
before that seal runs, and even a solved task can lose the trio to a
partial artifact export (run 34801009507: 8 of 20 tasks, including the
solved ``abs-stepped-slices``, carried no diagnostics). The strict
attestation diagnostic then reports ``planned task <id>: missing
diagnostics`` and the evidence chain is broken on exactly the tasks that
need it most.

This helper runs on the host after the official-verifier bind step and
guarantees the trio exists for one task without ever inventing a grade:

* A valid in-container ``diagnostics.json`` is never modified. Only its
  missing siblings are regenerated, byte-deterministically, from the same
  document ``seal()`` renders - the replay digest and fingerprints are pure
  functions of the JSON payload, so repair cannot diverge from it.
* A task with no usable document gets a synthesized trio whose capability
  rows are all ``UNEXERCISED`` (never ``WORKING``) and whose diagnostic
  events only restate what typed evidence already says: the
  ``official-verifier-result.json`` failure class, mapped onto the closed
  ``DiagnosticCode`` set, plus the fact that the in-container seal never
  ran. ``evidence_refs`` digest-bind that event to the real receipt file.
* Nothing here writes ``reward``, ``resolved``, a patch, or a capability
  claim the evidence does not support.

Exit 0 when the trio is present (found, repaired, or synthesized); exit 1
only when the artifact root cannot be made to carry one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from gt_engine.run_diagnostics import (
    CapabilityState,
    DiagnosticCode,
    DiagnosticEvent,
    DiagnosticJournal,
    classify_provider_failure,
)

DOCUMENT_SCHEMA = "gt.diagnostics.v1"

# The five capability rows the in-container close path always reports
# (gt_session.py:2390-2413). A crashed run cannot prove any of them, so the
# synthesized document reports every one as UNEXERCISED rather than
# guessing at a state the evidence cannot support.
_CANONICAL_CAPABILITIES = (
    "capability_negotiation",
    "dense_retrieval",
    "gt_engine_enabled",
    "lsp_promotion",
    "receipt_writer",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_document(path: Path, task_id: str) -> bool:
    payload = _read_json(path)
    return (
        isinstance(payload, dict)
        and payload.get("schema") == DOCUMENT_SCHEMA
        and str(payload.get("task_id") or "") == task_id
    )


def _render_siblings(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Reproduce seal()'s diagnostics.txt and incident-replay.json bytes.

    Both siblings are pure functions of the document payload, so a
    regenerated copy is identical to what the in-container seal would have
    emitted for the same document.
    """

    task_id = str(payload.get("task_id") or "")
    rows = [
        row
        for row in payload.get("diagnostics") or ()
        if isinstance(row, dict)
    ]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    replay = {
        "schema": "gt.incident_replay.v1",
        "task_id": task_id,
        "diagnostics_sha256": hashlib.sha256(canonical).hexdigest(),
        "fingerprints": [str(row.get("fingerprint") or "") for row in rows],
        "evidence_refs": [
            ref for row in rows for ref in (row.get("evidence_refs") or ())
        ],
    }
    lines = [
        f"[GT][{row.get('severity')}][{row.get('code')}] task={task_id} "
        f"phase={row.get('phase')} cause={row.get('normalized_cause')} "
        f"impact={_normalize(row.get('impact'))} "
        f"recovery={_normalize(row.get('recovery'))} "
        f"count={row.get('occurrence_count')}"
        for row in rows
    ]
    if not lines:
        lines = [f"[GT][INFO][HEALTHY] task={task_id}"]
    return "\n".join(lines) + "\n", replay


def _normalize(value: Any) -> str:
    import re

    words = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return words or "unspecified"


def _repair_siblings(document_path: Path) -> list[str]:
    """Write only the siblings that are missing; never overwrite evidence."""

    payload = _read_json(document_path)
    if payload is None:
        return []
    text, replay = _render_siblings(payload)
    written: list[str] = []
    text_path = document_path.with_name("diagnostics.txt")
    replay_path = document_path.with_name("incident-replay.json")
    if not text_path.is_file():
        text_path.write_text(text, encoding="utf-8")
        written.append(str(text_path))
    if not replay_path.is_file():
        replay_path.write_text(
            json.dumps(replay, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(str(replay_path))
    return written


def _agent_dir(root: Path, task_id: str) -> Path:
    """The directory receipts live in; diagnostics must sit beside them.

    Receipts are matched by their declared task_id first - sorting order is
    not task identity, and writing another task's trio into a sibling trial's
    agent dir overwrites real evidence.
    """

    receipts = sorted(root.rglob("agent/official-verifier-result.json"))
    for path in receipts:
        payload = _read_json(path)
        if (
            payload is not None
            and str(payload.get("task_id") or "") == task_id
        ):
            return path.parent
    # No parsed receipt: fall back to the trial-directory name (<task>__*)
    # or a task-named directory before inventing one.
    for path in receipts:
        parts = path.parts
        if any(
            part == task_id or part.split("__", 1)[0] == task_id
            for part in parts
        ):
            return path.parent
    for candidate in sorted(root.rglob(task_id)):
        if candidate.is_dir():
            target = candidate / "agent"
            target.mkdir(parents=True, exist_ok=True)
            return target
    target = root / task_id / "agent"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _trial_exception(root: Path, task_id: str) -> dict[str, Any]:
    """exception_info from the trial-level result.json, when it survived."""

    for path in sorted(root.rglob("result.json")):
        if "agent" in path.parts or "verifier" in path.parts:
            continue
        row = _read_json(path)
        if row is None:
            continue
        task_name = str(row.get("task_name") or "").rsplit("/", 1)[-1]
        if task_name == task_id and row.get("trial_name"):
            info = row.get("exception_info")
            if isinstance(info, dict):
                return info
    return {}


def _provider_code(info: dict[str, Any]) -> DiagnosticCode:
    """Classify the recorded provider exception through the canonical map."""

    exc_type = str(info.get("exception_type") or "Exception")
    exc = type(exc_type, (Exception,), {})(
        str(info.get("exception_message") or "")
    )
    code, _retryable = classify_provider_failure(exc)
    return code


def _failure_event(
    receipt: dict[str, Any],
    info: dict[str, Any],
    task_id: str,
    evidence_refs: tuple[dict[str, str], ...],
) -> DiagnosticEvent:
    """Map the typed receipt failure onto the closed diagnostic code set.

    Every branch restates a classification ``standardize_benchmark_result``
    already wrote into the official-verifier receipt - nothing is upgraded,
    downgraded, or invented.
    """

    failure_class = str(receipt.get("failure_class") or "")
    error_code = str(receipt.get("error_code") or "")
    cause = error_code or failure_class or "unknown_trial_failure"
    subsystem, capability, retryable = "runtime", "agent_runtime", False
    if failure_class == "provider_billing_failure":
        code = DiagnosticCode.GT_PROVIDER_BILLING
        subsystem, capability = "provider", "provider_gateway"
    elif failure_class == "provider_failure":
        code = (
            _provider_code(info)
            if info
            else DiagnosticCode.GT_PROVIDER_MALFORMED_RESPONSE
        )
        subsystem, capability, retryable = "provider", "provider_gateway", True
    elif failure_class == "governor_abort":
        code = DiagnosticCode.GT_CAPABILITY_DEGRADED
        subsystem, capability, retryable = "supervisor", "agent_runtime", True
    elif failure_class == "budget_exhausted":
        code = DiagnosticCode.GT_RESOURCE_EXHAUSTED
    elif failure_class in {"resource_exhaustion", "process_signal_failure"}:
        code = DiagnosticCode.GT_RESOURCE_EXHAUSTED
    elif failure_class == "missing_verifier":
        code = DiagnosticCode.GT_VERIFIER_FAILED
        subsystem, capability = "verifier", "official_verifier"
    elif failure_class == "missing_result":
        code = DiagnosticCode.GT_RECEIPT_MISSING
        subsystem, capability = "runner", "receipt_writer"
    elif failure_class in {"artifact_failure", "malformed_result"}:
        code = DiagnosticCode.GT_RECEIPT_INVALID
        subsystem, capability = "runner", "receipt_writer"
    else:
        code = DiagnosticCode.GT_CAPABILITY_DEGRADED
    return DiagnosticEvent.create(
        code=code,
        severity="ERROR",
        phase=(
            "agent_loop"
            if subsystem in {"runtime", "supervisor", "provider"}
            else subsystem
        ),
        subsystem=subsystem,
        capability=capability,
        task_id=task_id,
        classification="primary",
        cause=cause,
        impact="trial_completed_without_grade",
        recovery="inspect_typed_receipt_and_trial_artifacts",
        retryable=retryable,
        event_sequence=0,
        identities={"emitter": "emit_task_diagnostics"},
        evidence_refs=evidence_refs,
    )


def _seal_absent_event(task_id: str, *, primary: bool) -> DiagnosticEvent:
    return DiagnosticEvent.create(
        code=DiagnosticCode.GT_RECEIPT_MISSING,
        severity="WARNING",
        phase="finalize",
        subsystem="diagnostics",
        capability="receipt_writer",
        task_id=task_id,
        classification="primary" if primary else "consequential",
        cause="in_container_diagnostics_seal_absent",
        impact="in_container_diagnostic_state_unverifiable",
        recovery="host_synthesized_diagnostics_from_typed_receipt",
        retryable=False,
        event_sequence=0,
        identities={"emitter": "emit_task_diagnostics"},
    )


def _synthesize(root: Path, task_id: str) -> Path:
    agent_dir = _agent_dir(root, task_id)
    target_dir = agent_dir
    if (target_dir / "diagnostics.json").exists():
        # A document exists here but failed validation - it is corruption
        # evidence, not something to overwrite. Drop the synthesized trio
        # one level down, where the auditor still counts a valid document
        # for the task while the malformed one stays visible.
        target_dir = agent_dir / "gt-state" / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = agent_dir / "official-verifier-result.json"
    receipt = _read_json(receipt_path) or {}
    # Evidence refs are digest-verified by the auditor and resolved relative
    # to the diagnostics document's own directory; an absent receipt gets no
    # ref at all rather than a malformed one.
    evidence_refs: tuple[dict[str, str], ...] = ()
    if receipt_path.is_file():
        relative = os.path.relpath(receipt_path, target_dir).replace("\\", "/")
        evidence_refs = (
            {
                "path": relative,
                "sha256": hashlib.sha256(
                    receipt_path.read_bytes()
                ).hexdigest(),
            },
        )

    journal = DiagnosticJournal(target_dir, task_id=task_id)
    # required=True because these are the product's mandatory capabilities;
    # UNEXERCISED because a run that never reached its seal cannot prove any
    # of them. That keeps the row loud without claiming work that did not
    # verifiably happen.
    for name in _CANONICAL_CAPABILITIES:
        journal.capability(
            name,
            CapabilityState.UNEXERCISED,
            "in_container_seal_absent_host_synthesized",
            required=True,
        )
    if receipt.get("status") == "ERROR":
        journal.record(
            _failure_event(
                receipt,
                _trial_exception(root, task_id),
                task_id,
                evidence_refs,
            )
        )
        journal.record(_seal_absent_event(task_id, primary=False))
    else:
        journal.record(_seal_absent_event(task_id, primary=True))
    journal.seal()
    return target_dir / "diagnostics.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    task_id = args.task_id.strip()
    if not task_id:
        print(
            "emit_task_diagnostics: task ID must be nonempty", file=sys.stderr
        )
        return 2

    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    documents = [
        path
        for path in sorted(root.rglob("diagnostics.json"))
        if _valid_document(path, task_id)
    ]
    try:
        if documents:
            for path in documents:
                for written in _repair_siblings(path):
                    print(
                        "emit_task_diagnostics: repaired missing sibling "
                        f"{written}"
                    )
            print(
                f"emit_task_diagnostics: in-container diagnostics present for "
                f"{task_id} ({len(documents)} document(s)); trio verified"
            )
            return 0
        synthesized = _synthesize(root, task_id)
    except Exception as exc:  # noqa: BLE001 - a failed emit must be loud
        print(
            f"emit_task_diagnostics: could not ensure diagnostics for "
            f"{task_id}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(
        f"emit_task_diagnostics: synthesized honest diagnostics for {task_id} "
        f"at {synthesized} (in-container seal absent)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
