from __future__ import annotations

import pytest
from harbor.environments.base import ExecResult

from gt_engine.host_execution import (
    HostExecCategory,
    HostExecutionRecorder,
)


class _Environment:
    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        if command == "explode":
            raise RuntimeError("boom")
        return ExecResult(stdout="ok", stderr="warn", return_code=3)


@pytest.mark.asyncio
async def test_host_execution_records_actual_dispatch_and_output_bytes():
    recorder = HostExecutionRecorder()

    result = await recorder.exec(
        _Environment(),
        "pytest -q",
        category=HostExecCategory.MODEL_ACTION,
        action_id=7,
        source_revision="s2",
        cwd="/app",
        env={},
        timeout_sec=30,
    )

    assert result.return_code == 3
    row = recorder.receipts[0]
    assert row.executed is True
    assert row.cache_hit is False
    assert row.category is HostExecCategory.MODEL_ACTION
    assert row.output_bytes == len(b"okwarn")
    assert row.timeout_sec == 30.0


@pytest.mark.asyncio
async def test_host_execution_records_exception_before_reraising():
    recorder = HostExecutionRecorder()

    with pytest.raises(RuntimeError, match="boom"):
        await recorder.exec(
            _Environment(),
            "explode",
            category=HostExecCategory.COMPLETION_PROBE,
            cwd="/app",
            env={},
            timeout_sec=2,
        )

    assert recorder.receipts[0].exception_type == "RuntimeError"
    assert recorder.receipts[0].executed is True


def test_cache_hit_is_not_counted_as_environment_execution():
    recorder = HostExecutionRecorder()
    recorder.record_cache_hit(
        category=HostExecCategory.COMPLETION_PROBE,
        command="test -s output.jsonl",
        action_id=9,
        source_revision="s3",
    )

    summary = recorder.summary()
    assert summary["actual_environment_execs"] == 0
    assert summary["controller_cached_reads"] == 1
    assert summary["effective_task_actions"] == 0


@pytest.mark.asyncio
async def test_effective_task_actions_include_sensor_overhead_but_not_system_info():
    recorder = HostExecutionRecorder()
    environment = _Environment()

    await recorder.exec(
        environment,
        "find . -type f",
        category=HostExecCategory.WORKSPACE_MANIFEST,
    )
    await recorder.exec(
        environment,
        "uname -s",
        category=HostExecCategory.SYSTEM_INFORMATION,
    )

    summary = recorder.summary()
    assert summary["actual_environment_execs"] == 2
    assert summary["sensor_environment_execs"] == 1
    assert summary["effective_task_actions"] == 1
    assert summary["decision_actions"] == 0
    assert summary["harness_overhead_execs"] == 2
    assert summary["substrate_environment_execs"] == 1
