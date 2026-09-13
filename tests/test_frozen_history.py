import os
import subprocess
from pathlib import Path

import pytest

from gt_engine.indexer import _SKIP_DIRS, _source_paths, is_producer_input
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace


def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    env = os.environ | {"GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                        "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], env=env,
                              check=True, capture_output=True).stdout.decode().strip()

    git("init", "-q")
    (root / "one.py").write_text("def one(): return 1\n")
    git("add", ".")
    git("-c", "core.hooksPath=", "commit", "-qm", "initial")
    return root, git


def test_history_only_change_updates_frozen_producer_identity(tmp_path):
    root, git = repository(tmp_path)
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "state",
                             repo_root=root, predicates=[])
    before = capture_workspace(root)
    first = adapter._frozen_graph_input(before)
    git("-c", "core.hooksPath=", "commit", "--allow-empty", "-qm", "history boundary")
    after = capture_workspace(root)
    second = adapter._frozen_graph_input(after)
    assert before.files == after.files
    assert before.revision != after.revision
    assert first.history != second.history
    assert second.history.head == git("rev-parse", "HEAD")


def test_frozen_history_materializes_requested_commit_after_head_moves(tmp_path):
    from gt_engine.indexer import _freeze_history
    from gt_engine.repository_identity import repository_history

    root, git = repository(tmp_path)
    revision = git("rev-parse", "HEAD")
    history = repository_history(root)
    git("-c", "core.hooksPath=", "commit", "--allow-empty", "-qm", "later")
    frozen = tmp_path / "frozen"
    _freeze_history(root, frozen, history)
    observed = subprocess.run(["git", "-C", str(frozen), "rev-parse", "HEAD"],
                              capture_output=True, check=True).stdout.decode().strip()
    assert observed == revision
    assert not (frozen / "one.py").exists()


def test_frozen_history_survives_source_pruning(tmp_path):
    from gt_engine.indexer import _freeze_history
    from gt_engine.repository_identity import repository_history

    root, git = repository(tmp_path)
    branch = git("symbolic-ref", "--short", "HEAD")
    history = repository_history(root)
    frozen = tmp_path / "frozen"
    _freeze_history(root, frozen, history)
    git("checkout", "--orphan", "replacement")
    git("-c", "core.hooksPath=", "commit", "-qm", "replacement history")
    git("branch", "-D", branch)
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "--prune=now")
    retained = subprocess.run(["git", "-C", str(frozen), "log", "--format=%H"],
                              check=True, capture_output=True).stdout.decode().splitlines()
    assert retained == [history.head]
    assert not (frozen / ".git/objects/info/alternates").exists()


def test_deepening_changes_identity_and_freezes_original_boundary(tmp_path):
    from gt_engine.indexer import _freeze_history, compute_index_reuse_key

    root, git = repository(tmp_path)
    for ordinal in range(2):
        git("-c", "core.hooksPath=", "commit", "--allow-empty", "-qm", f"history {ordinal}")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--quiet", "--no-local", "--depth=1", str(root), str(shallow)],
                   check=True, capture_output=True)
    before = capture_workspace(shallow)
    old_key = compute_index_reuse_key(shallow)
    subprocess.run(["git", "-C", str(shallow), "fetch", "--unshallow"],
                   check=True, capture_output=True)
    after = capture_workspace(shallow)
    assert before.files == after.files
    assert before.revision != after.revision
    assert old_key != compute_index_reuse_key(shallow)
    assert before.history.head == after.history.head
    assert before.history.shallow and not after.history.shallow
    frozen = tmp_path / "frozen"
    _freeze_history(shallow, frozen, before.history)
    count = subprocess.run(["git", "-C", str(frozen), "rev-list", "--count", "HEAD"],
                           check=True, capture_output=True).stdout.strip()
    assert count == b"1"


@pytest.mark.skipif(os.name != "posix" or not os.environ.get("GT_INDEX_BINARY"),
                    reason="installed Linux producer required")
def test_producer_retains_real_cochange_history(tmp_path):
    import sqlite3

    from gt_engine.indexer import ensure_index_with_receipt

    root, git = repository(tmp_path)
    for version in range(3):
        for name in ("one", "two"):
            (root / f"{name}.py").write_text(f"def {name}(): return {version}\n")
        git("add", ".")
        git("-c", "core.hooksPath=", "commit", "-qm", f"paired change {version}")
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "state",
                             repo_root=root, predicates=[])
    result = ensure_index_with_receipt(root, layout=adapter.engine_state.layout)
    assert result.success, result.error_type
    with sqlite3.connect(result.graph_db) as connection:
        rows = connection.execute("SELECT * FROM cochanges").fetchall()
    assert rows, "the build discarded eligible Git co-change history"


def test_source_manifest_domain_matches_workspace_snapshot(tmp_path):
    """_source_paths enumerates exactly the files the freeze can reproduce.

    The manifest digest and the frozen build input must name the same file
    set: paths Git reports. .pytest_cache/{README.md,.gitignore} are
    producer-input names on disk but runtime artifacts; a gitignored
    local_settings.py is producer-input but invisible to the snapshot. When
    the domains diverged, every LSP schedule failed
    lsp_source_input_mismatch (run 34760986248, 23/23 attempts).
    """
    root, git = repository(tmp_path)
    cache = root / ".pytest_cache"
    cache.mkdir()
    (cache / "README.md").write_text("# pytest cache\n")
    (cache / ".gitignore").write_text("# Created by pytest automatically.\n*\n")
    (root / "local_settings.py").write_text("SECRET = 1\n")
    (root / ".gitignore").write_text("local_settings.py\n")
    (root / "scratch.py").write_text("# untracked but visible\n")

    snapshot = capture_workspace(root)
    want = {
        item.path for item in snapshot.files
        if item.kind == "file" and is_producer_input(item.path)
        and not any(part in _SKIP_DIRS for part in Path(item.path).parts)
    }
    have = {
        path.relative_to(root).as_posix()
        for path in _source_paths(root)
    }
    assert have == want
    assert "scratch.py" in have
    assert ".gitignore" in have
    assert not any(".pytest_cache" in p or p == "local_settings.py" for p in have)


def test_source_manifest_falls_back_to_walk_outside_git(tmp_path):
    """A non-git root keeps the filesystem walk, with the snapshot's
    runtime-artifact pruning so the freeze domain still matches."""
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def a(): return 1\n")
    cache = root / ".pytest_cache"
    cache.mkdir()
    (cache / "README.md").write_text("# pytest cache\n")
    have = {
        path.relative_to(root).as_posix()
        for path in _source_paths(root)
    }
    assert have == {"src/a.py"}
