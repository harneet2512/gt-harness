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


def test_write_manifest_makes_a_staged_tree_verifiable(tmp_path):
    """Paid run 34046802932 errored in setup because nothing wrote this file.

    The staging step provisioned the servers and set GT_LSP_BIN_HOST, but
    verify_lsp_assets opens root/manifest.json unconditionally, so the agent
    install raised FileNotFoundError before any model call.
    """
    from gt_harness.lsp_assets import verify_lsp_assets, write_lsp_manifest

    (tmp_path / "node-runtime/bin").mkdir(parents=True)
    (tmp_path / "gopls").write_bytes(b"gopls-binary")
    (tmp_path / "node-runtime/bin/node").write_bytes(b"node-binary")

    with pytest.raises(FileNotFoundError):
        verify_lsp_assets(tmp_path)

    result = write_lsp_manifest(tmp_path)
    assert result["verified"] is True
    assert result["file_count"] == 2
    assert verify_lsp_assets(
        tmp_path, expected_manifest_sha256=result["manifest_sha256"]
    )["verified"] is True


def test_written_manifest_fails_the_census_when_the_tree_moves(tmp_path):
    """The generator must not paper over a later change to the tree."""
    from gt_harness.lsp_assets import verify_lsp_assets, write_lsp_manifest

    (tmp_path / "gopls").write_bytes(b"gopls-binary")
    write_lsp_manifest(tmp_path)

    (tmp_path / "unlisted").write_bytes(b"added after the manifest")
    with pytest.raises(ValueError, match="lsp_asset_file_census_mismatch"):
        verify_lsp_assets(tmp_path)
