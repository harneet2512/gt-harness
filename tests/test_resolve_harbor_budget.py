from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.resolve_harbor_budget import (
    TASK_CONFIG_IDENTITY,
    canonical_task_config_bytes,
    resolve_budget,
)


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
