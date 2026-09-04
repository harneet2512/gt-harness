"""Session workspaces: the clone, its cumulative diff, and the transcript file.

Everything here is blocking git/filesystem work, called from the manager's
worker threads. Nothing in this module knows about sessions, events or HTTP.
"""
from __future__ import annotations

import json
import os
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
