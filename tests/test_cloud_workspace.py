"""Workspace helpers against a real git repository (no fakes)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cloud.server.workspace import STATE_DIRNAME, list_tree


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_bytes(b"hello\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_bytes(b"print('x')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_list_tree_reports_tracked_files_with_sizes(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    files = list_tree(str(repo))
    assert [f["path"] for f in files] == ["README", "src/app.py"]
    assert files[0]["size"] == len("hello\n")


def test_list_tree_includes_untracked_but_not_harness_state(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    (repo / STATE_DIRNAME).mkdir()
    (repo / STATE_DIRNAME / "transcript.json").write_text("{}", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (repo / "ignored.log").write_text("noise\n", encoding="utf-8")
    paths = [f["path"] for f in list_tree(str(repo))]
    assert "new.txt" in paths
    assert ".gitignore" in paths
    assert "ignored.log" not in paths
    assert not any(p.startswith(STATE_DIRNAME) for p in paths)
