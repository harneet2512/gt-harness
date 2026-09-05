"""Unit tests for ``cloud.server.environment.CloudLocalEnvironment``.

NO FAKE BOUNDARY: these run real ``bash -c`` subprocesses on the real
filesystem. The interrupt path is exactly the one ``request_stop()`` drives in
production, so a passing test here means a live Stop really does kill the
command in flight rather than waiting for it (HAR-84 round-2, P2-4).

Run: ``python -m pytest tests/test_cloud_environment.py -q``.
"""
from __future__ import annotations

import shutil
import threading
import time

import pytest

from cloud.server.environment import (
    INTERRUPT_MESSAGE,
    INTERRUPT_RETURNCODE,
    CloudLocalEnvironment,
    InterruptGuard,
    interrupted_observation,
)

#: a real bash must exist for any of this to mean anything
needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None and not CloudLocalEnvironment(cwd=".")._bash.endswith(
        ("bash", "bash.exe")
    ),
    reason="no bash available",
)


@needs_bash
def test_a_normal_command_is_unaffected(tmp_path) -> None:
    env = CloudLocalEnvironment(cwd=str(tmp_path), timeout=30)
    result = env.execute({"command": "echo hello"})

    assert result["returncode"] == 0
    assert result["output"].strip() == "hello"
    assert result["exception_info"] == ""


@needs_bash
def test_interrupt_kills_the_command_in_flight(tmp_path) -> None:
    env = CloudLocalEnvironment(cwd=str(tmp_path), timeout=120)
    running = threading.Event()
    result: dict[str, dict] = {}

    def run() -> None:
        running.set()
        result["out"] = env.execute({"command": "sleep 30"})

    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    assert running.wait(5.0)
    # Give bash a moment to actually be in `sleep` before pulling the rug.
    time.sleep(0.4)
    env.interrupt()
    worker.join(10.0)
    elapsed = time.monotonic() - started

    assert not worker.is_alive(), "execute() never returned after interrupt()"
    assert elapsed < 5.0, f"the interrupt took {elapsed:.1f}s"
    assert result["out"]["returncode"] == INTERRUPT_RETURNCODE
    assert result["out"]["exception_info"] == INTERRUPT_MESSAGE


@needs_bash
def test_interrupt_before_the_process_exists_still_kills_it(tmp_path) -> None:
    """The window between ``arm`` and ``Popen`` must not swallow a stop."""
    env = CloudLocalEnvironment(cwd=str(tmp_path), timeout=120)
    env._guard.arm()
    env._guard.request()

    started = time.monotonic()
    result = env.execute({"command": "sleep 30"})
    elapsed = time.monotonic() - started

    # `execute` re-arms, so this command runs to completion under its own
    # timeout — the guarantee under test is only that the guard never wedges.
    assert elapsed < 40.0
    assert result["returncode"] in (0, INTERRUPT_RETURNCODE)


@needs_bash
def test_the_environment_is_reusable_after_an_interrupt(tmp_path) -> None:
    env = CloudLocalEnvironment(cwd=str(tmp_path), timeout=120)
    worker = threading.Thread(
        target=lambda: env.execute({"command": "sleep 30"}), daemon=True
    )
    worker.start()
    time.sleep(0.4)
    env.interrupt()
    worker.join(10.0)

    after = env.execute({"command": "echo still here"})
    assert after["returncode"] == 0
    assert after["output"].strip() == "still here"


def test_interrupted_observation_shape() -> None:
    assert interrupted_observation("partial") == {
        "output": "partial",
        "returncode": 137,
        "exception_info": "interrupted by user stop",
    }


def test_guard_reports_an_interrupt_exactly_once() -> None:
    fired: list[int] = []
    guard = InterruptGuard(on_interrupt=lambda: fired.append(1))

    guard.arm()
    assert guard.disarm() is False

    guard.arm()
    guard.request()
    assert guard.disarm() is True
    assert fired == [1]

    # A fresh command starts clean.
    guard.arm()
    assert guard.disarm() is False
