from __future__ import annotations

import hashlib

import pytest

from scripts.resolve_harbor_budget import resolve_budget


def test_resolver_uses_task_agent_timeout_and_records_source_hash(tmp_path):
    config = tmp_path / "task.toml"
    config.write_text("[agent]\ntimeout_sec = 900\n", encoding="utf-8")

    receipt = resolve_budget(config, multiplier=1.0)

    assert receipt["base_timeout_sec"] == 900.0
    assert receipt["execution_budget_sec"] == 900.0
    assert receipt["timeout_multiplier"] == 1.0
    assert receipt["task_config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert receipt["source"] == "task.toml:[agent].timeout_sec"


def test_resolver_applies_harbor_multiplier_and_optional_cap(tmp_path):
    config = tmp_path / "task.toml"
    config.write_text("[agent]\ntimeout_sec = 600\n", encoding="utf-8")

    receipt = resolve_budget(config, multiplier=2.0, max_timeout_sec=900)

    assert receipt["execution_budget_sec"] == 900.0


def test_resolver_fails_closed_without_explicit_agent_timeout(tmp_path):
    config = tmp_path / "task.toml"
    config.write_text("[verifier]\ntimeout_sec = 60\n", encoding="utf-8")

    with pytest.raises(ValueError, match="agent.timeout_sec"):
        resolve_budget(config, multiplier=1.0)
