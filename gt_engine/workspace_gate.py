"""Which workspaces get GT's code-repository guidance.

GT's task workflow (``gt-query`` first, ``gt-impact``, write tests) and its
submit review assume a code repository under git. Campaign 2026-09-29
(same model, space-bunny-alpha) applied both to Terminal-Bench 2.0 workspaces
that are an ISO image, a CSV or one C file: the review, which diffs against
git, flagged every task literal as "[not in your changes]" (qemu-startup
rep4), and TB2 agent timeouts ran at 29% against the baseline's 18%.

A workspace qualifies when it is a git work tree tracking at least
``MIN_SOURCE_FILES`` source files. The gate fails OPEN: only git's own "not a
git repository" answer, or a repository with too few sources, turns the
guidance off. Any other git failure (a container repo owned by another user,
a timeout, no git binary) keeps GT fully on. The ``gt-*`` tools stay
available either way.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

MIN_SOURCE_FILES = 20
_GIT_TIMEOUT_SECONDS = 15
_NOT_A_REPOSITORY = "not a git repository"
# Program source only: the source languages gt-index parses
# (indexer.SOURCE_EXTS) minus config, docs, markup, SQL and shell scripts, which
# the indexer also reads but which do not make a workspace a code repository.
CODE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".svelte", ".vue",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".sc", ".groovy", ".clj",
    ".c", ".h", ".cc", ".hh", ".cpp", ".cxx", ".hpp", ".hxx", ".m", ".mm",
    ".cs", ".rb", ".rake", ".php", ".swift", ".dart", ".zig", ".lua",
    ".ex", ".exs", ".erl", ".hs", ".ml", ".mli", ".elm",
})
# Inherited repository overrides would make git describe another repository
# than the workspace (a leaked GIT_DIR once rewrote the real repo's remotes).
_GIT_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                  "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE")

REPOSITORY = "git"
NO_REPOSITORY = "none"
UNKNOWN = "unknown"


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    # safe.directory: container repos are often owned by root while the agent
    # runs as another user, and git then refuses every command.
    # LC_ALL=C: _NOT_A_REPOSITORY is matched against git's English message; a
    # localized container would otherwise read UNKNOWN and keep GT on everywhere.
    env = {k: v for k, v in os.environ.items() if k not in _GIT_OVERRIDES}
    try:
        return subprocess.run(["git", "-c", "safe.directory=*", "-C", cwd, *args],
                              capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT_SECONDS, errors="replace",
                              env={**env, "LC_ALL": "C", "LANGUAGE": ""})
    except (OSError, subprocess.SubprocessError):
        return None


def repository_state(cwd: str) -> str:
    """``git``, ``none`` (git itself says there is no repository) or ``unknown``."""
    result = _git(cwd, "rev-parse", "--is-inside-work-tree")
    if result is not None and result.returncode == 0 and result.stdout.strip() == "true":
        return REPOSITORY
    if result is not None and _NOT_A_REPOSITORY in result.stderr:
        return NO_REPOSITORY
    # git could not answer (no git binary - most TB2 images - a timeout, or a
    # refusal): with no .git anywhere from cwd up there is no repository either
    # way; with one, stay UNKNOWN and fail open.
    return UNKNOWN if _has_git_entry(cwd) else NO_REPOSITORY


def _has_git_entry(cwd: str) -> bool:
    """A `.git` directory or file (worktrees, submodules) in cwd or any parent."""
    try:
        here = Path(cwd).resolve()
    except OSError:
        return True  # cannot tell: fail open
    return any((parent / ".git").exists() for parent in (here, *here.parents))


def lacks_repository(cwd: str) -> bool:
    """True only when git confirms there is no repository here."""
    return repository_state(cwd) == NO_REPOSITORY


def has_start_commit(cwd: str) -> bool:
    """A git repository with a commit to diff against: what the submit review
    needs. False on no repository, no git, a refused repository or an empty
    `git init` - every case where a diff would read as "nothing changed"."""
    if repository_state(cwd) != REPOSITORY:
        return False
    result = _git(cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    return result is not None and result.returncode == 0


def source_files(cwd: str) -> int | None:
    """Source files in the repository, tracked or untracked-but-not-ignored
    (a `git init` whose sources were never committed is still a code repo);
    ``:/`` = from the top, ``-z`` = unquoted paths. None when git cannot answer."""
    result = _git(cwd, "ls-files", "-z", "--full-name", "--cached", "--others",
                  "--exclude-standard", "--", ":/")
    if result is None or result.returncode != 0:
        return None
    return sum(1 for path in result.stdout.split("\0")
               if path and PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS)


def gate_report(cwd: str) -> dict[str, object]:
    state = repository_state(cwd)
    if state == NO_REPOSITORY:
        return {"code_workspace": False, "repository": state, "source_files": 0}
    count = source_files(cwd) if state == REPOSITORY else None
    code = count is None or count >= MIN_SOURCE_FILES  # unknown: fail open
    return {"code_workspace": code, "repository": state, "source_files": count}


def is_code_workspace(cwd: str) -> bool:
    return bool(gate_report(cwd)["code_workspace"])


__all__ = ["CODE_EXTENSIONS", "MIN_SOURCE_FILES", "gate_report", "has_start_commit",
           "is_code_workspace", "lacks_repository", "repository_state", "source_files"]
