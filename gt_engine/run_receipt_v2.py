"""Always-emitted, failure-safe production run receipt (v2)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_RECEIPT_SCHEMA = "gt.run_receipt.v2"


def is_complete_run_receipt(row: dict[str, Any]) -> bool:
    """Recognize a finalized production receipt, not a schema-only stub."""

    required_text = (
        "task_id",
        "requested_model",
        "started_at",
        "finished_at",
        "terminal",
        "infrastructure_classification",
    )
    required_numbers = (
        "duration_ms",
        "iteration_count",
        "graph_build_count",
        "graph_refresh_count",
        "graph_duration_ms",
        "artifact_size_bytes",
    )
    if row.get("schema") != RUN_RECEIPT_SCHEMA:
        return False
    if any(not str(row.get(key) or "").strip() for key in required_text):
        return False
    if any(
        not isinstance(row.get(key), (int, float)) or float(row[key]) < 0
        for key in required_numbers
    ):
        return False
    if int(row.get("artifact_size_bytes") or 0) <= 0:
        return False
    if any(
        not isinstance(row.get(key), list)
        for key in ("graph_builds", "deliveries", "feature_lifecycle_transitions")
    ):
        return False
    usage = row.get("provider_usage")
    if not isinstance(usage, dict):
        return False
    return all(
        isinstance(usage.get(key), (int, float)) and float(usage[key]) >= 0
        for key in ("calls", "input_tokens", "output_tokens", "duration_ms")
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _actions(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for action in (message.get("extra") or {}).get("actions") or ():
            if isinstance(action, dict):
                rows.append(dict(action))
    return rows


def _trajectory_metrics(trajectory: dict[str, Any]) -> dict[str, Any]:
    messages = list(trajectory.get("messages") or ())
    actions = _actions(messages)
    inspected: list[str] = []
    edited: list[str] = []
    reverted = 0
    for action in actions:
        command = str(action.get("command") or "")
        targets = [str(item) for item in action.get("targets") or () if str(item)]
        operation = str(action.get("operation") or "").lower()
        if operation in {"read", "search", "inspect"}:
            inspected.extend(targets)
        if operation in {"edit", "create", "delete", "rename"}:
            edited.extend(targets)
            if "revert" in command.lower() or "checkout --" in command.lower():
                reverted += 1
    distinct_edits = list(dict.fromkeys(edited))
    target_switches = sum(
        left != right for left, right in zip(distinct_edits, distinct_edits[1:], strict=False)
    )
    first_edit = distinct_edits[0] if distinct_edits else ""
    unrelated_before = (
        sum(path != first_edit for path in inspected) if first_edit else len(inspected)
    )
    return {
        "iteration_count": sum(message.get("role") == "assistant" for message in messages),
        "first_correct_target_inspection": "",
        "first_correct_edit": first_edit,
        "unrelated_inspections_before_first_correct_edit": unrelated_before,
        "target_switches": target_switches,
        "reverted_edits": reverted,
        "failure_to_relevant_action_latency": None,
        "affected_test_coverage": None,
        "public_surface_coverage": None,
    }


class RunReceiptFinalizer:
    def __init__(
        self,
        output: str | Path,
        *,
        task_id: str,
        requested_model: str,
        started_at: str | None = None,
    ) -> None:
        self.output = Path(output)
        self.task_id = task_id
        self.requested_model = requested_model
        self.started_at = started_at or datetime.now(UTC).isoformat()
        self._clock_started = time.perf_counter()
        self.provider_usage = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": 0.0,
        }
        self.graph_builds: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.feature_lifecycles: list[dict[str, Any]] = []
        self.initial_repository_revision = ""
        self.final_repository_revision = ""
        self._finalized = False

    @property
    def finalized(self) -> bool:
        return self._finalized

    def record_provider_usage(
        self,
        *,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float = 0.0,
    ) -> None:
        self.provider_usage = {
            "calls": max(0, int(calls)),
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
            "duration_ms": max(0.0, float(duration_ms)),
        }

    def record_graph_build(
        self,
        *,
        kind: str,
        repository_revision: str,
        graph_revision: str,
        duration_ms: float,
        success: bool,
        workspace_revision: str = "",
        mode: str = "",
    ) -> None:
        self.graph_builds.append(
            {
                "kind": kind,
                "repository_revision": repository_revision,
                "graph_revision": graph_revision,
                "duration_ms": max(0.0, float(duration_ms)),
                "success": bool(success),
                "workspace_revision": workspace_revision,
                "mode": mode,
            }
        )

    def record_delivery(self, receipt: dict[str, Any]) -> None:
        self.deliveries.append(dict(receipt))

    def record_feature_lifecycle(self, receipt: dict[str, Any]) -> None:
        self.feature_lifecycles.append(dict(receipt))

    def record_repository_identity(
        self,
        *,
        initial_repository_revision: str,
        final_repository_revision: str,
    ) -> None:
        self.initial_repository_revision = str(initial_repository_revision or "")
        self.final_repository_revision = str(final_repository_revision or "")

    def finalize(
        self,
        *,
        terminal: str,
        infrastructure_classification: str,
        exception: BaseException | None = None,
        trajectory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._finalized and self.output.is_file():
            return json.loads(self.output.read_text(encoding="utf-8"))
        metrics = _trajectory_metrics(trajectory or {})
        graph_revisions = sorted(
            {
                str(row.get("graph_revision") or "")
                for row in self.deliveries
                if str(row.get("graph_revision") or "")
            }
        )
        delivery_tokens = sum(
            max(0, int(row.get("delivery_tokens") or 0)) for row in self.deliveries
        )
        payload: dict[str, Any] = {
            "schema": RUN_RECEIPT_SCHEMA,
            "task_id": self.task_id,
            "requested_model": self.requested_model,
            "started_at": self.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "terminal": terminal,
            "infrastructure_classification": infrastructure_classification,
            "exception": (
                {"type": type(exception).__name__, "message": str(exception)[:1000]}
                if exception is not None
                else None
            ),
            "duration_ms": round((time.perf_counter() - self._clock_started) * 1000, 3),
            "provider_usage": dict(self.provider_usage),
            **metrics,
            "graph_build_count": sum(row["kind"] == "initial" for row in self.graph_builds),
            "graph_refresh_count": sum(row["kind"] != "initial" for row in self.graph_builds),
            "graph_duration_ms": round(
                sum(float(row["duration_ms"]) for row in self.graph_builds), 3
            ),
            "graph_builds": list(self.graph_builds),
            "initial_repository_revision": self.initial_repository_revision,
            "final_repository_revision": self.final_repository_revision,
            "graph_revisions_used_for_deliveries": graph_revisions,
            "feature_lifecycle_transitions": list(self.feature_lifecycles),
            "deliveries": list(self.deliveries),
            "delivery_tokens": delivery_tokens,
            "delivery_roles": sorted(
                {str(row.get("role") or "") for row in self.deliveries if row.get("role")}
            ),
            "delivery_uptake": sum(
                bool(row.get("resulting_agent_action")) for row in self.deliveries
            ),
            "delivery_contradictions": sum(
                str(row.get("stage") or "") == "CONTRADICTED" for row in self.feature_lifecycles
            ),
            "artifact_size_bytes": 0,
        }
        # Include the exact final file size.  Updating a decimal field can
        # change its own width, so converge before the atomic publication.
        for _ in range(4):
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            if payload["artifact_size_bytes"] == len(encoded):
                break
            payload["artifact_size_bytes"] = len(encoded)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        _atomic_write(self.output, encoded)
        self._finalized = True
        return payload


@dataclass(frozen=True, slots=True)
class RunReceiptLoadResult:
    valid: bool
    receipts: tuple[dict[str, Any], ...]
    missing_paths: tuple[str, ...]
    invalid_paths: tuple[str, ...]
    aggregate: dict[str, Any] | None


def load_run_receipts(paths: Iterable[str | Path]) -> RunReceiptLoadResult:
    receipts: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            missing.append(str(path))
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            invalid.append(str(path))
            continue
        if not is_complete_run_receipt(row):
            invalid.append(str(path))
            continue
        receipts.append(row)
    valid = not missing and not invalid
    aggregate = None
    if valid:
        aggregate = {
            "runs": len(receipts),
            "provider_calls": sum(
                int((row.get("provider_usage") or {}).get("calls") or 0)
                for row in receipts
            ),
            "input_tokens": sum(
                int((row.get("provider_usage") or {}).get("input_tokens") or 0)
                for row in receipts
            ),
            "output_tokens": sum(
                int((row.get("provider_usage") or {}).get("output_tokens") or 0)
                for row in receipts
            ),
        }
    return RunReceiptLoadResult(
        valid=valid,
        receipts=tuple(receipts),
        missing_paths=tuple(missing),
        invalid_paths=tuple(invalid),
        aggregate=aggregate,
    )


__all__ = [
    "RUN_RECEIPT_SCHEMA",
    "RunReceiptFinalizer",
    "RunReceiptLoadResult",
    "is_complete_run_receipt",
    "load_run_receipts",
]
