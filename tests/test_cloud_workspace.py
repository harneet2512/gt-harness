"""Workspace helpers against a real git repository (no fakes)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud.server import workspace as workspace_module
from cloud.server.workspace import (
    _WRITES,
    DIFF_PATCH_CAP,
    STATE_DIRNAME,
    cap_diff,
    clone_repo,
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
        # observed on the live codespace: the model edited src/click/core.py
        # with an inline script and the write went unrecorded (HAR-84 round 2)
        'python3 -c "open(\'f.py\', \'w\').write(text)"',
        'python -c "import pathlib; pathlib.Path(\'f\').write_text(\'x\')"',
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


# --------------------------------------------------------------------------
# HAR-84 G-06: `ref` is documented as "branch, tag, or SHA" — make a SHA work
# --------------------------------------------------------------------------
def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_clone_by_branch_still_takes_the_ordinary_path(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    workspace = tmp_path / "ws"

    sha = clone_repo(str(repo), "master", str(workspace)) or clone_repo(
        str(repo), _default_branch(repo), str(workspace)
    )

    assert sha == _head_sha(repo)
    assert (workspace / "README").is_file()


def _default_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_full_sha_ref_clones_instead_of_failing(tmp_path: Path) -> None:
    """`git clone --depth 1 --branch <sha>` cannot resolve a commit id.

    Before this the documented "or SHA" always produced
    *Remote branch <sha> not found in upstream origin* and a failed session.
    """
    repo = _seed_repo(tmp_path)
    sha = _head_sha(repo)
    workspace = tmp_path / "ws-sha"

    cloned = clone_repo(str(repo), sha, str(workspace))

    assert cloned == sha
    # (line endings are git's business on Windows; the content is not)
    assert (workspace / "README").read_text(encoding="utf-8").strip() == "hello"
    assert (workspace / STATE_DIRNAME).is_dir()


def test_an_unknown_ref_is_still_a_failure_with_no_host_path_in_it(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    workspace = tmp_path / "ws-bad"

    with pytest.raises(RuntimeError) as caught:
        clone_repo(str(repo), "no-such-branch", str(workspace))

    message = str(caught.value)
    assert "ref not found in the repository" in message
    # HAR-84 G-22: git's own text names the workspace path; the product's does not.
    assert str(workspace) not in message


def test_a_private_repo_failure_is_not_git_asking_for_a_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = workspace_module.clone_error_message(
        "fatal: could not read Username for 'https://github.com': No such device "
        "or address\nCloning into '/srv/gt-workspaces/abc123'..."
    )
    assert "private" in message
    assert "Username" not in message
    assert "/srv/gt-workspaces" not in message


# --------------------------------------------------------------------------
# HAR-84 G-07: disk floor and per-session quota
# --------------------------------------------------------------------------
def test_creation_is_refused_below_the_free_space_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_MIN_FREE_MB", "2048")
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=0, used=0, free=100 * 1024 * 1024),
    )

    with pytest.raises(RuntimeError) as caught:
        workspace_module.ensure_free_space()

    assert "100 MB free" in str(caught.value)
    assert "2048 MB required" in str(caught.value)


def test_creation_is_allowed_above_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_MIN_FREE_MB", "2048")
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=0, used=0, free=8 * 1024 * 1024 * 1024),
    )

    workspace_module.ensure_free_space()


def test_the_floor_can_be_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_MIN_FREE_MB", "0")
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=0, used=0, free=1),
    )

    workspace_module.ensure_free_space()


def test_workspace_mb_measures_a_real_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "big.bin").write_bytes(b"\0" * (3 * 1024 * 1024))

    assert workspace_module.workspace_mb(str(workspace)) >= 3
    assert workspace_module.workspace_mb(str(tmp_path / "nope")) == 0


def test_a_failed_clone_leaves_nothing_on_disk(tmp_path: Path) -> None:
    """The session row never gets a workspace_path, so close() cannot clean up.

    The fetch-by-SHA path creates the directory itself (`git init`), so without
    this a failed SHA clone left a 1 MB orphan under the workspaces root
    forever (found on the codespace during the HAR-84 fix verification).
    """
    repo = _seed_repo(tmp_path)
    workspace = tmp_path / "ws-orphan"

    for ref in ["no-such-branch", "0" * 40]:
        with pytest.raises(RuntimeError):
            clone_repo(str(repo), ref, str(workspace))
        assert not workspace.exists(), f"{ref} left {workspace} behind"
