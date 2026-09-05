"""HAR-86: the GT working-tree snapshot must ignore the harness state dir.

FAKE BOUNDARY: none. A real git repository is created on disk with real
files, and ``gt_engine.miniswe_typed_actions._snapshot_authority`` — the
function whose output becomes ``RepositorySnapshot.working_tree_sha256`` —
is called on it directly.

Why it matters: GT writes receipts and the trajectory into its state
directory *while a turn is running*. If those bytes are part of the working
tree identity, a typed action taken mid-turn is compared against a tree GT
itself moved, and the producer reports ``repository_revision_mismatch`` +
``working_tree_sha256_mismatch`` for changes the agent never made.

Run: ``python -m pytest tests/test_gt_snapshot_state_dir.py -q``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gt_engine.miniswe_typed_actions import (
    STATE_DIRNAME,
    _snapshot_authority,
    _snapshot_excluded,
)


def _git(*args: str, cwd: Path) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {args} failed:\n{proc.stdout}\n{proc.stderr}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one committed file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "harness@example.invalid", cwd=root)
    _git("config", "user.name", "HAR-86 harness", cwd=root)
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("-c", "commit.gpgsign=false", "commit", "-m", "init", cwd=root)
    return root


def test_state_dir_writes_do_not_change_the_snapshot_authority(repo: Path) -> None:
    """GT writing its own scratch is not a working-tree change."""
    before = _snapshot_authority(repo)
    assert before[2] is True, "a quiet repository is a complete snapshot"
    assert [path for path, _ in before[1]] == ["a.py"]

    state = repo / STATE_DIRNAME
    state.mkdir()
    (state / "trajectory.json").write_text('{"messages": []}', encoding="utf-8")
    (state / "receipts").mkdir()
    (state / "receipts" / "turn-1.json").write_text("{}", encoding="utf-8")

    after = _snapshot_authority(repo)
    assert after == before, "state-dir writes must not move the working tree"


def test_a_real_edit_still_changes_the_snapshot_authority(repo: Path) -> None:
    """The exclusion is narrow: a tracked file still moves the identity."""
    (repo / STATE_DIRNAME).mkdir()
    (repo / STATE_DIRNAME / "trajectory.json").write_text("{}", encoding="utf-8")
    before = _snapshot_authority(repo)

    (repo / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = _snapshot_authority(repo)

    assert after[0] != before[0], "a tracked edit must change the tree hash"
    assert after[1] != before[1], "and the file manifest with it"
    assert dict(after[1])["a.py"] != dict(before[1])["a.py"]


def test_an_untracked_file_outside_the_state_dir_is_still_hashed(repo: Path) -> None:
    before = _snapshot_authority(repo)
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")
    after = _snapshot_authority(repo)
    assert after[0] != before[0]
    assert [path for path, _ in after[1]] == ["a.py", "new.txt"]


def test_an_explicitly_configured_state_dir_is_the_one_excluded(repo: Path) -> None:
    """A harness that names its scratch dir gets that one excluded, not `.gt_state`."""
    scratch = repo / ".harness"
    scratch.mkdir()
    baseline = _snapshot_authority(repo, str(scratch))

    (scratch / "notes.json").write_text("{}", encoding="utf-8")
    assert _snapshot_authority(repo, str(scratch)) == baseline

    # ...and with no configuration the same write is visible, because the
    # fallback only knows about `.gt_state`.
    assert _snapshot_authority(repo)[0] != _snapshot_authority(repo, str(scratch))[0]


def test_git_is_always_excluded_and_a_state_dir_outside_the_repo_is_a_no_op(
    repo: Path, tmp_path: Path
) -> None:
    assert ".git" in _snapshot_excluded(repo)
    assert _snapshot_excluded(repo) == (".git", STATE_DIRNAME)
    assert _snapshot_excluded(repo, str(scratch := tmp_path / "outside")) == (".git",)
    # nothing under `outside` can be reached by a walk rooted at the repo
    scratch.mkdir()
    (scratch / "x.json").write_text("{}", encoding="utf-8")
    assert _snapshot_authority(repo, str(scratch))[2] is True


def test_gt_state_dir_environment_variable_is_honoured(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GT_STATE_DIR`` is what the indexer reads; the snapshot follows it."""
    monkeypatch.setenv("GT_STATE_DIR", str(repo / ".scratch"))
    assert _snapshot_excluded(repo) == (".git", ".scratch")

    (repo / ".scratch").mkdir()
    before = _snapshot_authority(repo)
    (repo / ".scratch" / "index.db").write_bytes(b"\x00\x01")
    assert _snapshot_authority(repo) == before
