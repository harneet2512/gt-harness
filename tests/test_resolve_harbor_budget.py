from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.resolve_harbor_budget import (
    SUPERVISOR_GRACE_SECONDS,
    TASK_CONFIG_IDENTITY,
    canonical_task_config_bytes,
    resolve_budget,
)


def test_supervisor_reserve_covers_observed_container_startup_and_finalization() -> None:
    # Both ends measured on run 34305004976: 100s from step start to the journal
    # opening, and 68s from session close to step end. 168s observed. The
    # reserve must cover that with margin, and must not exceed it so far that it
    # hands back minutes of the benchmark's own budget -- seven tasks in run
    # 34312022821 hit our line rather than the benchmark's 5400.
    assert SUPERVISOR_GRACE_SECONDS >= 200
    assert SUPERVISOR_GRACE_SECONDS <= 300


def test_task_config_identity_is_checkout_line_ending_independent(
    tmp_path: Path,
) -> None:
    canonical = b'[agent]\ntimeout_sec = 300\n\n[metadata]\nlanguage = "go"\n'
    windows_checkout = canonical.replace(b"\n", b"\r\n")
    expected = hashlib.sha256(canonical).hexdigest()

    linux_path = tmp_path / "linux.toml"
    windows_path = tmp_path / "windows.toml"
    linux_path.write_bytes(canonical)
    windows_path.write_bytes(windows_checkout)

    linux = resolve_budget(linux_path, multiplier=1.0)
    windows = resolve_budget(windows_path, multiplier=1.0)
    assert linux["task_config_identity"] == TASK_CONFIG_IDENTITY
    assert windows["task_config_identity"] == TASK_CONFIG_IDENTITY
    assert linux["task_config_sha256"] == expected
    assert windows["task_config_sha256"] == expected


def test_task_config_identity_rejects_ambiguous_bare_carriage_return() -> None:
    with pytest.raises(ValueError, match="bare CR"):
        canonical_task_config_bytes(b"[agent]\rtimeout_sec = 300\n")


def test_the_overhead_extension_is_added_and_declared(tmp_path):
    """GT's pre-agent work is paid for openly, never folded into the base.

    Measured on run 34360947973: 247s of graph build and 702s of planning ran
    before the agent's first step, and all seven tasks then reached the deadline
    with the agent still working. The benchmark's number bounds the agent, so
    the setup is added on top and the deviation is stated.
    """
    from scripts.resolve_harbor_budget import (
        GT_OVERHEAD_EXTENSION_SECONDS,
        resolve_budget,
    )

    config = tmp_path / "task.toml"
    config.write_text("[agent]\ntimeout_sec = 5400.0\n", encoding="utf-8")

    plain = resolve_budget(config, multiplier=1.0)
    assert plain["execution_budget_sec"] == 5400.0
    assert plain["gt_overhead_extension_sec"] == 0.0
    assert plain["deviates_from_benchmark_budget"] is False

    extended = resolve_budget(
        config, multiplier=1.0,
        overhead_extension_sec=GT_OVERHEAD_EXTENSION_SECONDS,
    )
    assert extended["benchmark_budget_sec"] == 5400.0
    assert extended["gt_overhead_extension_sec"] == float(
        GT_OVERHEAD_EXTENSION_SECONDS
    )
    assert extended["execution_budget_sec"] == 5400.0 + GT_OVERHEAD_EXTENSION_SECONDS
    assert extended["deviates_from_benchmark_budget"] is True
    # the benchmark's own base is never rewritten
    assert extended["base_timeout_sec"] == 5400.0


def test_the_extension_lands_on_top_of_the_stage_cap(tmp_path):
    """The cap keeps tasks on one rail; the extension is the same for each."""
    from scripts.resolve_harbor_budget import resolve_budget

    config = tmp_path / "task.toml"
    config.write_text("[agent]\ntimeout_sec = 5400.0\n", encoding="utf-8")

    capped = resolve_budget(
        config, multiplier=2.0, max_timeout_sec=5400.0,
        overhead_extension_sec=600.0,
    )
    # the cap bit first, so the benchmark portion is the rail, not 10800
    assert capped["benchmark_budget_sec"] == 5400.0
    assert capped["execution_budget_sec"] == 6000.0


def test_a_negative_extension_is_refused(tmp_path):
    from scripts.resolve_harbor_budget import resolve_budget

    config = tmp_path / "task.toml"
    config.write_text("[agent]\ntimeout_sec = 5400.0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_budget(config, multiplier=1.0, overhead_extension_sec=-1.0)
