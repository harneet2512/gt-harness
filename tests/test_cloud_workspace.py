"""Workspace helpers against a real git repository (no fakes)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from cloud.server import workspace as workspace_module
from cloud.server.workspace import (
    _WRITES,
    DIFF_PATCH_CAP,
    STATE_DIRNAME,
    cap_diff,
    list_tree,
    looks_like_write,
)


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


# --------------------------------------------------------------------------
# write detection + snapshot capping
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        "echo patched >> README.md",
        "echo brand-new > newfile.txt",
        "touch a.txt",
        "rm -rf build",
        "mkdir -p src/new",
        "sed -i 's/a/b/' f.py",
        "perl -pi -e 's/a/b/' f.py",
        "git apply /tmp/p.diff",
        "git checkout -- src",
        "apply_patch <<'EOF'",
        "python3 - <<'EOF'",
        "cat x.py | tee y.py",
        "cd src && mv a.py b.py",
    ],
)
def test_write_commands_are_recognised(command: str) -> None:
    assert looks_like_write(command)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "ls -la",
        "cat README.md",
        "grep -rn needle src",
        "python -m pytest -q",
        "git status",
        "git log --oneline -5",
        "make 2>&1",
    ],
)
def test_read_commands_are_not_writes(command: str) -> None:
    assert not looks_like_write(command)


def test_the_write_regex_is_identical_to_the_one_the_ui_uses() -> None:
    """The scrubber's ticks and the server's snapshots must agree.

    ``cloud/ui/src/trail.ts`` decides whether a step reads as an edit; this
    module decides whether that step gets a stored diff. A divergence gives
    the UI ticks with nothing behind them, so the two literals are compared
    directly rather than trusted to a comment.
    """
    repo_root = Path(workspace_module.__file__).parents[2]
    source = (repo_root / "cloud" / "ui" / "src" / "trail.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r"export const WRITES\s*=\s*\n?\s*/(.+)/;", source)
    assert match, "could not find `export const WRITES` in cloud/ui/src/trail.ts"
    assert match.group(1) == _WRITES.pattern


def test_cap_diff_leaves_a_small_patch_alone() -> None:
    diff = {
        "patch": "diff --git a/a b/a\n+x\n",
        "files": [{"path": "a", "patch": "diff --git a/a b/a\n+x\n"}],
    }
    patch, files, truncated = cap_diff(diff)
    assert truncated is False
    assert patch == diff["patch"]
    assert files[0]["patch"] == diff["files"][0]["patch"]


def test_cap_diff_truncates_past_the_cap_and_drops_per_file_bodies() -> None:
    body = "x" * (DIFF_PATCH_CAP + 4096)
    diff = {"patch": body, "files": [{"path": "big.txt", "patch": body}]}
    patch, files, truncated = cap_diff(diff)
    assert truncated is True
    assert len(patch.encode("utf-8")) == DIFF_PATCH_CAP
    assert files[0]["patch"] == ""
    assert files[0]["path"] == "big.txt"
    # the caller's dicts are untouched
    assert diff["files"][0]["patch"] == body


def test_cap_diff_never_splits_a_multibyte_character() -> None:
    patch = "é" * DIFF_PATCH_CAP  # 2 bytes each, so the cut lands mid-character
    capped, _files, truncated = cap_diff({"patch": patch, "files": []})
    assert truncated is True
    assert len(capped.encode("utf-8")) <= DIFF_PATCH_CAP
    assert "�" not in capped
