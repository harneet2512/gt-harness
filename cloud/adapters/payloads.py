"""Pull a command and a set of file paths out of an agent's tool payload.

Both the hook (which sees Claude Code's and Codex's ``tool_input`` /
``tool_response``) and the Codex rollout tailer (which sees Codex's own item
records) need the same two answers: *what command is this*, and *which files
does it touch*. Neither tool documents a payload schema per tool that we can
rely on across versions, so everything here reads a field **if it is present**
and falls back rather than asserting a shape.

Nothing in this module converts a path to repo-relative form. It returns raw
paths exactly as the agent reported them — absolute, native separators — and
:func:`cloud.adapters.gt_cloud_bridge.to_repo_relative` does the conversion at
the point of emission, which is also the point that drops anything outside the
working directory.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "command_from_tool_input",
    "paths_from_patch_text",
    "paths_from_tool_input",
]

# Keys observed to hold a single file path. `file_path` is Claude Code's
# documented spelling for Write/Edit/Read; the rest are defensive.
_PATH_KEYS = (
    "file_path",
    "filePath",
    "notebook_path",
    "notebookPath",
    "path",
    "target_file",
    "abs_path",
)

# Keys observed to hold a list of paths.
_PATH_LIST_KEYS = ("file_paths", "filePaths", "paths", "files")

# Keys observed to hold a shell command.
_COMMAND_KEYS = ("command", "cmd", "commandLine", "script", "shell_command")

# Nested containers worth walking one level into.
_NESTED_KEYS = ("edits", "changes", "operations", "patches", "items")

_PATCH_MARKERS = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)

_MAX_PATHS = 64


def _add(out: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip() and len(out) < _MAX_PATHS:
        text = value.strip()
        if text not in out:
            out.append(text)


def paths_from_patch_text(text: Any) -> list[str]:
    """Read the file headers out of an ``apply_patch`` envelope.

    Codex's ``apply_patch`` tool takes a patch whose file headers are
    ``*** Add File: <path>`` and friends. Anything that is not one of those
    headers is ignored, so a diff body mentioning a path does not produce one.
    """
    out: list[str] = []
    if not isinstance(text, str):
        return out
    for line in text.splitlines():
        stripped = line.strip()
        for marker in _PATCH_MARKERS:
            if stripped.startswith(marker):
                _add(out, stripped[len(marker):].strip())
                break
    return out


def _walk_container(container: Any, out: list[str]) -> None:
    """Walk one level into an edits list or a changes map."""
    if isinstance(container, dict):
        # Codex's FileChange / patch_apply_end shape: the keys are the paths.
        for key, value in container.items():
            if isinstance(key, str) and ("/" in key or "\\" in key or "." in key):
                _add(out, key)
            if isinstance(value, dict):
                for path_key in _PATH_KEYS:
                    _add(out, value.get(path_key))
    elif isinstance(container, list):
        for entry in container:
            if isinstance(entry, str):
                _add(out, entry)
            elif isinstance(entry, dict):
                for path_key in _PATH_KEYS:
                    _add(out, entry.get(path_key))


def paths_from_tool_input(payload: Any) -> list[str]:
    """Every file path this tool payload names, best effort, order preserved.

    Handles, in order: the single-path keys, the list-of-paths keys, a nested
    ``edits`` list or ``changes`` map, and an ``apply_patch`` envelope in
    ``input`` / ``patch``. A payload that names nothing gives back ``[]``; it is
    never an error for a tool to touch no files.
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    try:
        for key in _PATH_KEYS:
            _add(out, payload.get(key))
        for key in _PATH_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                for entry in value:
                    _add(out, entry)
        for key in _NESTED_KEYS:
            if key in payload:
                _walk_container(payload.get(key), out)
        for key in ("input", "patch", "diff"):
            for path in paths_from_patch_text(payload.get(key)):
                _add(out, path)
    except Exception:
        # A malformed payload is a payload with no paths, not a failed hook.
        return out
    return out


def command_from_tool_input(payload: Any) -> str | None:
    """The shell command this payload runs, as one string, or ``None``.

    Accepts a plain string (Claude Code's ``Bash``/``PowerShell`` ``command``)
    and an argv list (Codex's ``CommandExecution.command``). An ``apply_patch``
    envelope is deliberately *not* treated as a command: its body is a patch, it
    is often enormous, and the files it names are already reported separately.
    """
    if not isinstance(payload, dict):
        return None
    try:
        for key in _COMMAND_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                parts = [str(item) for item in value if isinstance(item, (str, int, float))]
                if parts:
                    return " ".join(parts)
        query = payload.get("pattern") or payload.get("query")
        if isinstance(query, str) and query.strip():
            return query.strip()
    except Exception:
        return None
    return None


def json_loads_or_none(text: Any) -> Any:
    """``json.loads`` that answers ``None`` instead of raising. Used on tool arguments."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
