"""Auditable accounting for every host-side environment execution."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class HostExecCategory(StrEnum):
    MODEL_ACTION = "model_action"
    WORKSPACE_MANIFEST = "workspace_manifest"
    WORKSPACE_HASH = "workspace_hash"
    WORKSPACE_CAPTURE = "workspace_capture"
    SYNTAX_PROBE = "syntax_probe"
    COMPLETION_PROBE = "completion_probe"
    PROJECT_VALIDATION_PROBE = "project_validation_probe"
    RED_TEST_PROBE = "red_test_probe"
    AUTO_SUBMIT = "auto_submit"
    SYSTEM_INFORMATION = "system_information"
    REPOSITORY_TRANSFER = "repository_transfer"


_TASK_ACTION_CATEGORIES = frozenset(HostExecCategory) - {
    HostExecCategory.SYSTEM_INFORMATION
}
_SENSOR_CATEGORIES = frozenset(
    {
        HostExecCategory.WORKSPACE_MANIFEST,
        HostExecCategory.WORKSPACE_HASH,
        HostExecCategory.WORKSPACE_CAPTURE,
    }
)
_CONTROLLER_INTERVENTION_CATEGORIES = frozenset(
    {
        HostExecCategory.SYNTAX_PROBE,
        HostExecCategory.COMPLETION_PROBE,
        HostExecCategory.PROJECT_VALIDATION_PROBE,
        HostExecCategory.RED_TEST_PROBE,
        HostExecCategory.AUTO_SUBMIT,
    }
)
_SUBSTRATE_CATEGORIES = frozenset(
    {*_SENSOR_CATEGORIES, HostExecCategory.REPOSITORY_TRANSFER}
)


@dataclass(frozen=True, slots=True)
class HostExecReceipt:
    category: HostExecCategory
    command: str
    action_id: int
    source_revision: str
    duration_ms: float
    timeout_sec: float | None
    return_code: int | None
    output_bytes: int
    executed: bool
    cache_hit: bool
    exception_type: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["category"] = self.category.value
        return row


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 6)


class HostExecutionRecorder:
    def __init__(self) -> None:
        self.receipts: list[HostExecReceipt] = []

    async def exec(
        self,
        environment: Any,
        command: str,
        *,
        category: HostExecCategory,
        action_id: int = 0,
        source_revision: str = "",
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()
        timeout = kwargs.get("timeout_sec")
        try:
            result = await environment.exec(command, **kwargs)
        except Exception as exc:
            self.receipts.append(
                HostExecReceipt(
                    category=category,
                    command=command,
                    action_id=max(0, int(action_id)),
                    source_revision=source_revision,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    timeout_sec=None if timeout is None else float(timeout),
                    return_code=None,
                    output_bytes=0,
                    executed=True,
                    cache_hit=False,
                    exception_type=type(exc).__name__,
                )
            )
            raise
        output_bytes = len(
            ((getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")).encode(
                "utf-8", "replace"
            )
        )
        self.receipts.append(
            HostExecReceipt(
                category=category,
                command=command,
                action_id=max(0, int(action_id)),
                source_revision=source_revision,
                duration_ms=(time.perf_counter() - started) * 1000,
                timeout_sec=None if timeout is None else float(timeout),
                return_code=int(getattr(result, "return_code", 0)),
                output_bytes=output_bytes,
                executed=True,
                cache_hit=False,
            )
        )
        return result

    def record_cache_hit(
        self,
        *,
        category: HostExecCategory,
        command: str,
        action_id: int = 0,
        source_revision: str = "",
    ) -> None:
        self.receipts.append(
            HostExecReceipt(
                category=category,
                command=command,
                action_id=max(0, int(action_id)),
                source_revision=source_revision,
                duration_ms=0.0,
                timeout_sec=None,
                return_code=None,
                output_bytes=0,
                executed=False,
                cache_hit=True,
            )
        )

    def summary(self) -> dict[str, Any]:
        executed = [row for row in self.receipts if row.executed]
        cached = [row for row in self.receipts if row.cache_hit]
        category_counts = {
            category.value: sum(row.executed and row.category is category for row in self.receipts)
            for category in HostExecCategory
        }
        latencies = {
            category.value: {
                "total_ms": round(
                    sum(row.duration_ms for row in executed if row.category is category),
                    6,
                ),
                "p50_ms": _percentile(
                    [row.duration_ms for row in executed if row.category is category], 0.50
                ),
                "p95_ms": _percentile(
                    [row.duration_ms for row in executed if row.category is category], 0.95
                ),
            }
            for category in HostExecCategory
        }
        return {
            "actual_environment_execs": len(executed),
            "model_actions": category_counts[HostExecCategory.MODEL_ACTION.value],
            "decision_actions": category_counts[HostExecCategory.MODEL_ACTION.value],
            "controller_environment_execs": sum(
                row.category is not HostExecCategory.MODEL_ACTION for row in executed
            ),
            "harness_overhead_execs": sum(
                row.category is not HostExecCategory.MODEL_ACTION for row in executed
            ),
            "controller_intervention_execs": sum(
                row.category in _CONTROLLER_INTERVENTION_CATEGORIES for row in executed
            ),
            "substrate_environment_execs": sum(
                row.category in _SUBSTRATE_CATEGORIES for row in executed
            ),
            "controller_cached_reads": len(cached),
            "effective_task_actions": sum(
                row.category in _TASK_ACTION_CATEGORIES for row in executed
            ),
            "sensor_environment_execs": sum(
                row.category in _SENSOR_CATEGORIES for row in executed
            ),
            "category_counts": category_counts,
            "category_latency": latencies,
            "receipts": [row.as_dict() for row in self.receipts],
        }
