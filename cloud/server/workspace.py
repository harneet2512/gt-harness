"""Session workspaces: the clone, its cumulative diff, and the transcript file.

Everything here is blocking git/filesystem work, called from the manager's
worker threads. Nothing in this module knows about sessions, events or HTTP.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

STATE_DIRNAME = ".gt_state"
TRANSCRIPT_NAME = "transcript.json"
TRAJECTORY_NAME = "trajectory.json"
CLONE_TIMEOUT = 300
GIT_TIMEOUT = 60
#: `du -sm` over a big tree is not instant; it runs on the turn worker
DU_TIMEOUT = 30

#: a full commit id, which ``git clone --branch`` cannot resolve
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
#: an abbreviated one, only trusted as a *fallback* after a clone failed
_ABBREV_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")

#: free space below which a new session is refused outright (MB)
DEFAULT_MIN_FREE_MB = 2048
#: how big one session's workspace may get before its turn is cut off (MB)
DEFAULT_WORKSPACE_MAX_MB = 2048

#: git's own words -> the product's, so a failure does not leak the host path
#: or ask the user about a username they were never going to be prompted for
_CLONE_ERRORS = (
    (
        "could not read username",
        "repository not found, or it is private and the server has no "
        "credentials for it",
    ),
    (
        "repository not found",
        "repository not found, or it is private and the server has no "
        "credentials for it",
    ),
    ("not found in upstream origin", "ref not found in the repository"),
    # what GitHub says when a fetch-by-SHA names a commit it does not have
    ("not our ref", "ref not found in the repository"),
    ("couldn't find remote ref", "ref not found in the repository"),
    ("remote branch", "ref not found in the repository"),
    ("could not resolve host", "the repository host could not be reached"),
    ("permission denied", "the server is not allowed to read this repository"),
)

#: git's own words when a 3-way apply did not merge a path cleanly. `git apply
#: --3way --check` exits **0** for a patch it can only apply with conflict
#: markers, so the text is the only signal there is.
_APPLY_CONFLICTS = (
    re.compile(r"Applied patch to '(?P<path>.+?)' with conflicts"),
    re.compile(r"error: patch failed: (?P<path>.+?):\d+"),
    re.compile(
        r"error: (?P<path>.+?): (?:patch does not apply|does not match index"
        r"|already exists in working directory|No such file or directory)"
    ),
)

#: `git diff --name-status` codes we map to the API's file statuses
_STATUS_NAMES = {"A": "added", "D": "deleted"}
#: harness scratch is never part of a patch
_PATHSPEC = [".", f":(exclude){STATE_DIRNAME}"]

#: biggest patch text a stored diff snapshot may carry, in bytes
DIFF_PATCH_CAP = 512 * 1024

#: Commands that plausibly wrote something.
#:
#: PORTED VERBATIM from ``cloud/ui/src/trail.ts`` (``export const WRITES``).
#: THE TWO MUST STAY IN SYNC: the UI paints a step as an edit with this test,
#: and the server takes a per-step diff snapshot with it, so a divergence
#: would give the scrubber ticks with no snapshot behind them (or the other
#: way round). Change one, change the other, and update both tests.
_WRITES = re.compile(
    r"(^|[\s;&|(])(tee|patch|mv|cp|rm|mkdir|touch|truncate|install)\s"
    r"|>>?[^&]"
    r"|sed\s+-[a-z]*i"
    r"|perl\s+-[a-z]*i"
    r"|git\s+(apply|checkout|restore|revert|mv|rm)"
    r"|apply_patch"
    r"|python3?\s+-\s*<<"
    # `python -c "open(...,'w').write(...)"` is how a model edits a file when
    # it does not want to fight heredoc quoting. Without this the write is
    # invisible: no diff snapshot, no edit tick, and the live diff panel never
    # refreshes (observed on the live codespace, HAR-84 round 2).
    r"|python3?\s+-c\b"
)


def looks_like_write(command: str) -> bool:
    """True when ``command`` is the kind of thing that changes the tree.

    Deliberately generous — a false positive costs one extra ``git diff``, a
    false negative costs a missing snapshot.
    """
    return bool(command) and _WRITES.search(command) is not None


def cap_diff(diff: dict) -> tuple[str, list[dict], bool]:
    """Bound a diff for storage: ``(patch, files, truncated)``.

    Past :data:`DIFF_PATCH_CAP` the combined patch is cut at a byte boundary
    and the per-file bodies are dropped — they are the same bytes again, and
    a truncated snapshot is a summary, not a patch anyone can apply.
    """
    patch = str(diff.get("patch") or "")
    files = [dict(f) for f in (diff.get("files") or [])]
    encoded = patch.encode("utf-8")
    if len(encoded) <= DIFF_PATCH_CAP:
        return patch, files, False
    for entry in files:
        entry["patch"] = ""
    return encoded[:DIFF_PATCH_CAP].decode("utf-8", errors="ignore"), files, True


def workspaces_root() -> str:
    return os.environ.get("WORKSPACES_DIR", "./workspaces")


def workspace_path(session_id: str) -> str:
    return str(Path(workspaces_root()) / session_id)


def state_dir(workspace: str) -> Path:
    return Path(workspace, STATE_DIRNAME)


def remove_workspace(path: str) -> None:
    """``rmtree`` that survives git's read-only pack files (Windows)."""

    def _retry(func: Any, target: str, _exc: Any) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:  # noqa: BLE001 - best effort, the caller ignores it
            pass

    if not path or not Path(path).exists():
        return
    try:
        shutil.rmtree(path, onexc=_retry)
    except Exception:  # noqa: BLE001
        shutil.rmtree(path, ignore_errors=True)


def min_free_mb() -> int:
    """Free space under the workspaces root below which creation is refused."""
    return _int_env("WORKSPACES_MIN_FREE_MB", DEFAULT_MIN_FREE_MB)


def workspace_max_mb() -> int:
    """Per-session workspace cap in MB. ``0`` disables the quota."""
    return _int_env("SANDBOX_WORKSPACE_MAX_MB", DEFAULT_WORKSPACE_MAX_MB)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def free_mb(path: str) -> int:
    """Free megabytes on the filesystem holding ``path`` (its nearest parent).

    ``-1`` when the question cannot be answered, which callers treat as "do
    not block on it": a broken statvfs must not stop the product.
    """
    probe = Path(path or ".")
    for candidate in [probe, *probe.parents]:
        if candidate.exists():
            try:
                return int(shutil.disk_usage(str(candidate)).free // (1024 * 1024))
            except OSError:
                return -1
    return -1


def ensure_free_space(root: str | None = None) -> None:
    """Refuse to start a session that would fill the host (HAR-84 G-07a).

    The workspaces directory is a bind mount of a *shared* filesystem — the
    SQLite database, the docker images and every other session's clone live on
    it too — so the last free gigabyte is not this session's to spend.
    """
    floor = min_free_mb()
    if floor <= 0:
        return
    root = root or workspaces_root()
    free = free_mb(root)
    if 0 <= free < floor:
        raise RuntimeError(
            f"not enough free disk to start a session: {free} MB free under the "
            f"workspaces directory, {floor} MB required "
            f"(WORKSPACES_MIN_FREE_MB)"
        )


def workspace_mb(workspace: str) -> int:
    """Size of a workspace in MB. ``du -sm`` where there is one, else a walk."""
    if not workspace or not Path(workspace).is_dir():
        return 0
    try:
        result = subprocess.run(
            ["du", "-sm", workspace],
            capture_output=True,
            text=True,
            timeout=DU_TIMEOUT,
        )
        if result.returncode == 0:
            first = result.stdout.split(maxsplit=1)
            if first and first[0].isdigit():
                return int(first[0])
    except Exception:  # noqa: BLE001 - no `du` (Windows), or a slow tree
        pass
    return _walk_mb(workspace)


def _walk_mb(workspace: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(workspace):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return int(total // (1024 * 1024))


def clone_repo(repo: str, ref: str, workspace: str) -> str:
    """Clone ``repo`` at ``ref`` into ``workspace``; return the cloned SHA.

    ``ref`` is documented as "branch, tag, or SHA", but ``git clone --depth 1
    --branch <sha>`` cannot resolve a commit id — it only takes a *name*. A
    full SHA therefore goes down the fetch path from the start, and anything
    else falls back to it when the clone fails and the ref still looks like a
    commit (HAR-84 G-06).
    """
    remove_workspace(workspace)
    Path(workspace).parent.mkdir(parents=True, exist_ok=True)
    try:
        if _FULL_SHA.match(ref):
            return _clone_at_sha(repo, ref, workspace)
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, "--", repo, workspace],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
        )
        if clone.returncode != 0:
            if _ABBREV_SHA.match(ref):
                return _clone_at_sha(repo, ref, workspace)
            raise RuntimeError(clone_error_message(clone.stderr or clone.stdout or ""))
        state_dir(workspace).mkdir(parents=True, exist_ok=True)
        return _git_output(workspace, ["rev-parse", "HEAD"])
    except BaseException:
        # A clone that failed leaves nothing behind. `close()` cannot do it:
        # the session row never got a workspace_path, so the directory the
        # fetch path created would sit on the disk forever.
        remove_workspace(workspace)
        raise


def _clone_at_sha(repo: str, ref: str, workspace: str) -> str:
    """``init`` + ``fetch --depth 1 <sha>`` + ``checkout FETCH_HEAD``."""
    remove_workspace(workspace)
    Path(workspace).mkdir(parents=True, exist_ok=True)
    steps = (
        ["init", "--quiet"],
        ["remote", "add", "origin", "--", repo],
        ["fetch", "--depth", "1", "origin", ref],
        ["checkout", "--quiet", "FETCH_HEAD"],
    )
    for args in steps:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError(
                clone_error_message(result.stderr or result.stdout or "")
            )
    state_dir(workspace).mkdir(parents=True, exist_ok=True)
    return _git_output(workspace, ["rev-parse", "HEAD"])


def clone_error_message(stderr: str) -> str:
    """A clone failure in the product's words, with no host path in it.

    git's own text names ``/srv/gt-workspaces/<session id>`` and asks about a
    username nobody was going to be prompted for (HAR-84 G-22).
    """
    text = " ".join((stderr or "").split())
    lowered = text.lower()
    for needle, message in _CLONE_ERRORS:
        if needle in lowered:
            return f"could not clone the repository: {message}"
    return f"could not clone the repository: {_strip_paths(text)[:500]}"


def _strip_paths(text: str) -> str:
    """Drop absolute paths (and the workspaces root) out of an error string."""
    root = workspaces_root()
    if root:
        text = text.replace(str(Path(root)), "<workspace>").replace(root, "<workspace>")
    return re.sub(r"(?<![\w])(?:[A-Za-z]:)?[/\\][\w./\\-]{6,}", "<path>", text)


def compute_diff(workspace: str, base_sha: str) -> dict:
    """Cumulative diff of the workspace, including files the agent created.

    A plain ``git diff`` misses untracked files, so everything is marked
    intent-to-add first. Harness scratch (``.gt_state/``) is excluded so the
    trajectory never leaks into a patch.
    """
    empty: dict[str, Any] = {"patch": "", "files": [], "base_sha": base_sha}
    if not workspace or not Path(workspace).is_dir():
        return empty
    try:
        _git(workspace, ["add", "-A", "-N", "--", *_PATHSPEC])
        patch = _git(workspace, ["diff", "--", *_PATHSPEC]).stdout
        numstat = _git(workspace, ["diff", "--numstat", "--", *_PATHSPEC]).stdout
        namestatus = _git(
            workspace, ["diff", "--name-status", "--", *_PATHSPEC]
        ).stdout
    except Exception:  # noqa: BLE001 - a broken workspace reports no diff
        return empty

    statuses: dict[str, str] = {}
    for line in namestatus.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        statuses[fields[-1]] = _STATUS_NAMES.get(fields[0][:1], "modified")

    per_file = split_patch_by_file(patch)
    files = []
    for line in numstat.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        path = fields[-1]
        files.append({
            "path": path,
            "status": statuses.get(path, "modified"),
            "additions": int(fields[0]) if fields[0].isdigit() else 0,
            "deletions": int(fields[1]) if fields[1].isdigit() else 0,
            "patch": per_file.get(path, ""),
        })
    return {"patch": patch, "files": files, "base_sha": base_sha}


def apply_patch(workspace: str, patch: str) -> tuple[bool, list[str]]:
    """3-way merge ``patch`` into ``workspace``. ``(applied, conflicting paths)``.

    Either the whole patch lands or the workspace is exactly as it was — a
    half-applied tree with conflict markers in it is never a result anybody
    asked for.

    Two things make that true. ``git apply --3way`` implies ``--index``, so it
    refuses every path whose worktree differs from the index — which, after
    ``compute_diff``'s ``add -N``, is *every* file the session has edited. So
    the parent's own work is staged first (index == worktree), the patch is
    checked, applied, and the index is put back the way ``compute_diff`` leaves
    it (``reset`` + ``add -N``), which changes no file on disk. And because
    ``--3way --check`` exits 0 for a patch it would apply *with conflict
    markers*, the pre-staged tree is recorded with ``write-tree`` and restored
    with ``read-tree -u --reset`` if the real apply still ends in conflicts.
    """
    if not patch.strip():
        return True, []
    if not patch.endswith("\n"):
        patch += "\n"
    if not workspace or not Path(workspace).is_dir():
        return False, []
    try:
        _git(workspace, ["add", "-A", "--", *_PATHSPEC])
        tree = _git_output(workspace, ["write-tree"])
        check = _git_input(workspace, ["apply", "--3way", "--check", "-"], patch)
        conflicts = _conflict_paths(check)
        if check.returncode != 0 or conflicts:
            return False, conflicts
        if not tree:
            # No restore point, so the real apply is not attempted: a failure
            # would leave conflict markers nobody asked for.
            return False, []
        applied = _git_input(workspace, ["apply", "--3way", "-"], patch)
        conflicts = _conflict_paths(applied)
        if applied.returncode != 0 or conflicts:
            _git(workspace, ["read-tree", "-u", "--reset", tree])
            return False, conflicts
        return True, []
    finally:
        # back to what compute_diff expects: index at HEAD, untracked files
        # marked intent-to-add. Neither command touches a file on disk.
        _git(workspace, ["reset", "-q"])
        _git(workspace, ["add", "-A", "-N", "--", *_PATHSPEC])


def _conflict_paths(result: subprocess.CompletedProcess) -> list[str]:
    """The paths git named as unmergeable, in the order it named them."""
    paths: list[str] = []
    for line in f"{result.stderr or ''}\n{result.stdout or ''}".splitlines():
        for pattern in _APPLY_CONFLICTS:
            match = pattern.search(line)
            if match is None:
                continue
            path = match.group("path")
            if path not in paths:
                paths.append(path)
            break
    return paths


def list_tree(workspace: str) -> list[dict]:
    """Every tracked or untracked (non-ignored) file with its size in bytes."""
    listing = _git_output(
        workspace, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    files: list[dict] = []
    for rel in listing.split("\0"):
        if not rel or rel.startswith(f"{STATE_DIRNAME}/") or rel.startswith(".git/"):
            continue
        try:
            size = os.path.getsize(os.path.join(workspace, rel))
        except OSError:
            continue
        files.append({"path": rel.replace("\\", "/"), "size": size})
    files.sort(key=lambda f: f["path"])
    return files


def split_patch_by_file(patch: str) -> dict[str, str]:
    """Split a combined diff into one ``diff --git`` block per path."""
    blocks: dict[str, str] = {}
    path = ""
    current: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if path:
                blocks[path] = "".join(current)
            current = [line]
            _, _, tail = line.partition(" b/")
            path = tail.rstrip("\r\n")
            continue
        if path:
            current.append(line)
    if path:
        blocks[path] = "".join(current)
    return blocks


def load_transcript(workspace: str) -> list[dict]:
    """The persisted agent memory, or ``[]`` if there is none to trust."""
    path = state_dir(workspace) / TRANSCRIPT_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt transcript starts a fresh brief
        return []
    messages = data.get("messages") if isinstance(data, dict) else data
    return messages if isinstance(messages, list) else []


def save_transcript(workspace: str, messages: list[dict]) -> None:
    try:
        directory = state_dir(workspace)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / TRANSCRIPT_NAME).write_text(
            json.dumps({"messages": messages}, default=str), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - persistence is best effort
        pass


def _git(workspace: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )


def _git_input(
    workspace: str, args: list[str], text: str
) -> subprocess.CompletedProcess:
    """``_git`` with something on stdin (a patch)."""
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        input=text,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )


def _git_output(workspace: str, args: list[str]) -> str:
    result = _git(workspace, args)
    return result.stdout.strip() if result.returncode == 0 else ""
