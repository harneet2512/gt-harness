from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval.miniswe_agent import _REMOTE_LSP_BIN, MiniSweAgent, MiniSweGtAgent


def test_staging_is_optional(monkeypatch):
    """No servers provisioned leaves today's behaviour untouched."""

    monkeypatch.delenv("GT_LSP_BIN_HOST", raising=False)
    assert MiniSweAgent._lsp_bin_host() is None


def test_a_provisioned_directory_resolves(monkeypatch, tmp_path: Path):
    staged = tmp_path / "lsp-bin"
    staged.mkdir()
    (staged / "gopls").write_bytes(b"#!/bin/sh\n")
    (staged / "manifest.json").write_text(json.dumps({
        "schema": "gt.lsp_assets.v1",
        "files": {"gopls": hashlib.sha256(b"#!/bin/sh\n").hexdigest()},
    }))
    monkeypatch.setenv("GT_LSP_BIN_HOST", str(staged))

    assert MiniSweAgent._lsp_bin_host() == staged

    (staged / "gopls").write_bytes(b"changed executable")
    with pytest.raises(ValueError, match="lsp_asset_digest_mismatch"):
        MiniSweAgent._lsp_bin_host()


def test_configured_but_absent_fails_loudly(monkeypatch, tmp_path: Path):
    """Silently skipping a configured directory is how LSP stayed off."""

    monkeypatch.setenv("GT_LSP_BIN_HOST", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        MiniSweAgent._lsp_bin_host()


def test_servers_are_reachable_by_which(tmp_path: Path):
    """Promotion discovers servers with shutil.which, so PATH must carry them."""

    agent = MiniSweGtAgent(logs_dir=tmp_path, model_name="m", task_id="t")
    command = agent._run_command("instruction", "m")

    assert command.startswith(f'PATH="{_REMOTE_LSP_BIN}:$PATH" ')


def test_the_image_path_is_preserved_not_replaced(tmp_path: Path):
    """Prepending wins for the staged names without breaking the task image."""

    agent = MiniSweGtAgent(logs_dir=tmp_path, model_name="m", task_id="t")

    assert ':$PATH"' in agent._run_command("instruction", "m")


def test_staging_lands_under_the_installed_agent_root():
    assert _REMOTE_LSP_BIN.startswith("/installed-agent/")
