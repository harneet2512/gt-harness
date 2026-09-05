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


def clone_repo(repo: str, ref: str, workspace: str) -> str:
    """Clone ``repo`` at ``ref`` into ``workspace``; return the cloned SHA."""
    remove_workspace(workspace)
    Path(workspace).parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, repo, workspace],
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT,
    )
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr.strip()[:2000]}")
    state_dir(workspace).mkdir(parents=True, exist_ok=True)
    return _git_output(workspace, ["rev-parse", "HEAD"])


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


def _git_output(workspace: str, args: list[str]) -> str:
    result = _git(workspace, args)
    return result.stdout.strip() if result.returncode == 0 else ""
