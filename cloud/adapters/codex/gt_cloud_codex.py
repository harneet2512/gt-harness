#!/usr/bin/env python3
"""Follow a Codex session's rollout file and report it to the GT cloud UI.

Codex writes every thread to a JSONL "rollout" file under
``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``
(``CODEX_HOME`` defaults to ``~/.codex``). This module tails that file. It needs
no configuration inside Codex, works on a session that is already running, and
keeps working when Codex changes its tool names - which the hook adapter, which
matches on payload fields rather than tool names, also survives but less surely.

**Subagents come for free, and correctly.** Codex gives every subagent thread its
own rollout file whose first line declares its parentage::

    {"session_id": "...", "id": "01a0743a-a790-...", "parent_thread_id": "01a0738a-...",
     "cwd": "D:\\\\gt-harness", "cli_version": "0.153.3", "thread_source": "subagent",
     "agent_nickname": "Faraday", "agent_path": "/root/dense_cache",
     "source": {"subagent": {"thread_spawn": {"parent_thread_id": "...", "depth": 1,
      "agent_nickname": "Faraday", ...}}}}

So the watcher registers the parent, then registers each new subagent file it
sees with ``parent_agent_id`` set to the agent it registered for
``parent_thread_id``, labelled with ``agent_nickname``. That is a real tree, read
off disk, not inferred.

Shapes below were read from real rollout files written by Codex 0.153.3 (and
0.149.0) on this machine; ``docs/cloud/external-agents.md`` lists what is and is
not captured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

if __package__:
    from ..gt_cloud_bridge import Bridge, BridgeConfig, debug, extract_paths_from_command
    from ..payloads import paths_from_tool_input
else:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gt_cloud_bridge import (  # type: ignore[no-redef]
        Bridge,
        BridgeConfig,
        debug,
        extract_paths_from_command,
    )
    from payloads import paths_from_tool_input  # type: ignore[no-redef]

DEFAULT_POLL_SECONDS = 0.5
MAX_DEPTH = 4
READ_CHUNK = 4 * 1024 * 1024


# --- locating rollout files -------------------------------------------------


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def sessions_dir(root: str | None = None) -> str:
    return os.path.join(root or codex_home(), "sessions")


def list_rollouts(directory: str) -> list[str]:
    """Every rollout file under *directory*, newest last. Never raises."""
    found: list[tuple[float, str]] = []
    try:
        for base, _dirs, files in os.walk(directory):
            for name in files:
                if name.startswith("rollout-") and name.endswith(".jsonl"):
                    path = os.path.join(base, name)
                    try:
                        found.append((os.path.getmtime(path), path))
                    except OSError:
                        continue
    except Exception as exc:
        debug(f"list_rollouts failed for {directory}", exc)
    return [path for _mtime, path in sorted(found)]


def newest_rollout(directory: str) -> str | None:
    rollouts = list_rollouts(directory)
    return rollouts[-1] if rollouts else None


def read_session_meta(path: str) -> dict[str, Any]:
    """The first line's payload: cwd, thread id, parentage, nickname. ``{}`` on failure."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
        record = json.loads(first)
        if record.get("type") != "session_meta":
            return {}
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def spawn_info(meta: dict[str, Any]) -> dict[str, Any]:
    """``source.subagent.thread_spawn`` if this thread is a subagent, else ``{}``."""
    try:
        source = meta.get("source")
        if isinstance(source, dict):
            subagent = source.get("subagent")
            if isinstance(subagent, dict):
                spawn = subagent.get("thread_spawn")
                if isinstance(spawn, dict):
                    return spawn
    except Exception:
        pass
    return {}


# --- mapping one rollout line onto contract events ---------------------------


def _text_of(content: Any) -> str:
    """Join the text blocks of a Codex message. Blocks are ``{"type","text"}``."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _command_of(item: dict[str, Any]) -> str | None:
    command = item.get("command")
    if isinstance(command, list) and command:
        return " ".join(str(part) for part in command)
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None


def _parsed_paths(item: dict[str, Any]) -> list[str]:
    """Codex pre-parses shell commands into ``parsed_cmd`` entries with a ``path``."""
    out: list[str] = []
    parsed = item.get("parsed_cmd")
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                out.append(entry["path"])
    return out


def _activity_for_command(command: str | None) -> str:
    if not command:
        return "Running a command"
    words = command.split()
    # argv[0] is the absolute interpreter path on Windows; name the program.
    program = os.path.basename(words[0].replace("\\", "/")) if words else "a command"
    for word in words[1:]:
        if not word.startswith("-"):
            return f"Running {program} {word}"[:80]
    return f"Running {program}"[:80]


def map_item(item: dict[str, Any], cwd: Any) -> list[dict[str, Any]]:
    """Map one ``item_completed`` item onto zero or more contract events."""
    kind = item.get("type")
    if kind == "CommandExecution":
        command = _command_of(item)
        files = _parsed_paths(item) or extract_paths_from_command(command, cwd)
        activity = _activity_for_command(command)
        ok = item.get("status") in (None, "completed")
        return [
            {"type": "tool_call", "name": "exec", "command": command,
             "files": files, "activity": activity},
            {"type": "tool_result", "name": "exec", "ok": ok,
             "output": item.get("stdout"), "files": files},
        ]
    if kind == "FileChange":
        files = paths_from_tool_input({"changes": item.get("changes")})
        target = os.path.basename(files[0].replace("\\", "/")) if files else "files"
        return [
            {"type": "tool_call", "name": "apply_patch", "command": None,
             "files": files, "activity": f"Editing {target}"},
            {"type": "tool_result", "name": "apply_patch", "ok": True,
             "output": None, "files": files},
        ]
    if kind in ("AgentMessage", "Plan"):
        text = item.get("text") if kind == "Plan" else _text_of(item.get("content"))
        return [{"type": "assistant", "text": text}] if text else []
    if kind == "SubAgentActivity":
        note = f"subagent {item.get('agent_path') or ''} {item.get('kind') or ''}".strip()
        return [{"type": "status", "state": "working", "note": note}]
    # UserMessage is the human's own text; Reasoning is the model's private
    # scratchpad. Neither belongs on a card that shows what the agent is doing.
    return []


def _map_legacy(kind: str, payload: dict[str, Any], cwd: Any) -> list[dict[str, Any]]:
    """Events that appear only when ``session_meta.history_mode`` is ``legacy``.

    Codex has two history modes and they surface different records. In
    ``paginated`` mode everything arrives as ``item_completed``; in ``legacy``
    mode - the default, and 340 of the 786 rollout files on the machine this was
    written on - there is no ``item_completed`` and the same information arrives
    as its own ``event_msg`` types. Both are read.
    """
    if kind == "agent_message":
        text = payload.get("message")
        return [{"type": "assistant", "text": text}] if text else []
    if kind == "patch_apply_end":
        changes = payload.get("changes")
        files = paths_from_tool_input({"changes": changes})
        target = os.path.basename(files[0].replace("\\", "/")) if files else "files"
        ok = bool(payload.get("success", True))
        return [
            {"type": "tool_call", "name": "apply_patch", "command": None,
             "files": files, "activity": f"Editing {target}"},
            {"type": "tool_result", "name": "apply_patch", "ok": ok,
             "output": payload.get("stderr") if not ok else payload.get("stdout"),
             "files": files},
        ]
    if kind == "sub_agent_activity":
        note = f"subagent {payload.get('agent_path') or ''} {payload.get('kind') or ''}".strip()
        return [{"type": "status", "state": "working", "note": note}]
    return []


def map_rollout_line(record: Any, cwd: Any = None) -> list[dict[str, Any]]:
    """Map one parsed rollout line onto contract events. Never raises.

    Only ``event_msg`` lines are read. They are Codex's own normalised view of
    the session - the same items its UI renders - which is far steadier than the
    raw ``response_item`` tool-call records underneath them.
    """
    try:
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            return []
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return []
        kind = payload.get("type")
        if kind == "item_completed":
            item = payload.get("item")
            return map_item(item, cwd) if isinstance(item, dict) else []
        if kind == "token_count":
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if isinstance(total, int):
                return [{"type": "status", "state": "working", "tokens": total}]
            return []
        if kind == "task_started":
            return [{"type": "status", "state": "working", "activity": "Working"}]
        if kind == "task_complete":
            return [{"type": "status", "state": "idle", "activity": "Waiting for input"}]
        if kind == "turn_aborted":
            return [{"type": "status", "state": "idle", "note": str(payload.get("reason") or "aborted")}]
        return _map_legacy(str(kind), payload, cwd)
    except Exception as exc:
        debug("map_rollout_line failed", exc)
        return []


# --- tailing ----------------------------------------------------------------


class RolloutTailer:
    """Follows one rollout file, forwarding what it learns to one Bridge."""

    def __init__(self, path: str, bridge: Bridge, cwd: Any) -> None:
        self.path = path
        self.bridge = bridge
        self.cwd = cwd
        self.offset = 0
        self._buffer = ""
        self.events = 0

    def poll(self) -> int:
        """Read whatever has been appended. Returns the number of events emitted."""
        emitted = 0
        try:
            size = os.path.getsize(self.path)
            if size < self.offset:
                # Truncated or replaced: start over rather than read garbage.
                self.offset = 0
                self._buffer = ""
            if size == self.offset:
                return 0
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(min(READ_CHUNK, size - self.offset))
            self.offset += len(chunk)
            self._buffer += chunk.decode("utf-8", errors="replace")
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                emitted += self._consume(line)
        except FileNotFoundError:
            return 0
        except Exception as exc:
            debug(f"tailer poll failed for {self.path}", exc)
        return emitted

    def _consume(self, line: str) -> int:
        if not line.strip():
            return 0
        try:
            record = json.loads(line)
        except Exception:
            # A malformed or half-written line is skipped, not fatal.
            return 0
        count = 0
        for event in map_rollout_line(record, self.cwd):
            if self.bridge.emit(event):
                count += 1
        self.events += count
        return count


# --- the watcher ------------------------------------------------------------


class CodexWatcher:
    """Tails a root rollout file and every subagent rollout spawned under it."""

    def __init__(
        self,
        root_path: str,
        directory: str,
        config: BridgeConfig | None = None,
        follow_subagents: bool = True,
    ) -> None:
        self.root_path = root_path
        self.directory = directory
        self.config = config or BridgeConfig.from_env()
        self.follow_subagents = follow_subagents
        self.tailers: dict[str, RolloutTailer] = {}
        self.agent_ids: dict[str, str] = {}  # codex thread id -> our external agent id
        self.seen_files: set[str] = set()
        self._stop = threading.Event()

    def _label(self, meta: dict[str, Any], path: str) -> str:
        nickname = meta.get("agent_nickname")
        agent_path = meta.get("agent_path")
        if nickname:
            return f"{nickname} · {str(agent_path or '').lstrip('/') or 'subagent'}"[:200]
        cwd = meta.get("cwd") or ""
        base = os.path.basename(str(cwd).replace("\\", "/").rstrip("/")) or "codex"
        return f"codex · {base}"

    def attach(self, path: str) -> RolloutTailer | None:
        """Register an external agent for one rollout file and start tailing it."""
        if path in self.tailers:
            return self.tailers[path]
        meta = read_session_meta(path)
        if not meta:
            return None
        thread_id = str(meta.get("id") or meta.get("session_id") or path)
        spawn = spawn_info(meta)
        parent_thread = spawn.get("parent_thread_id") or meta.get("parent_thread_id")
        parent_agent = self.agent_ids.get(str(parent_thread)) if parent_thread else None
        if meta.get("thread_source") == "subagent" and not parent_agent:
            # Its parent is not one of ours; showing it at the root would lie
            # about the tree, so leave it alone.
            debug(f"skipping subagent rollout with unknown parent: {path}")
            return None
        depth = spawn.get("depth")
        if isinstance(depth, int) and depth > MAX_DEPTH:
            return None
        bridge = Bridge(
            agent_kind="codex",
            label=self._label(meta, path),
            task=str(spawn.get("agent_path") or "") or None,
            cwd=meta.get("cwd"),
            parent_agent_id=parent_agent,
            state_key=f"codex:{thread_id}",
            config=self.config,
        )
        if not bridge.start():
            return None
        self.agent_ids[thread_id] = str(bridge.agent_id)
        tailer = RolloutTailer(path, bridge, meta.get("cwd"))
        self.tailers[path] = tailer
        self.seen_files.add(path)
        return tailer

    def discover(self) -> list[str]:
        """New rollout files that are subagents of a thread we already follow."""
        if not self.follow_subagents:
            return []
        new: list[str] = []
        for path in list_rollouts(self.directory):
            if path in self.seen_files:
                continue
            self.seen_files.add(path)
            meta = read_session_meta(path)
            if meta.get("thread_source") != "subagent":
                continue
            spawn = spawn_info(meta)
            parent = str(spawn.get("parent_thread_id") or meta.get("parent_thread_id") or "")
            if parent in self.agent_ids:
                new.append(path)
        return new

    def poll_once(self) -> int:
        emitted = 0
        for path in self.discover():
            self.attach(path)
        for tailer in list(self.tailers.values()):
            emitted += tailer.poll()
        return emitted

    def run(self, poll: float = DEFAULT_POLL_SECONDS, deadline: float | None = None) -> int:
        """Poll until interrupted or *deadline* seconds elapse. Returns events sent."""
        total = 0
        limit = None if deadline is None else time.monotonic() + deadline
        try:
            while not self._stop.is_set():
                total += self.poll_once()
                if limit is not None and time.monotonic() >= limit:
                    break
                self._stop.wait(poll)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            debug("watcher run failed", exc)
        return total

    def stop(self) -> None:
        self._stop.set()

    def finish(self, status: str = "done", summary: str | None = None) -> None:
        # Children first, so a card never outlives its parent on the screen.
        for tailer in sorted(self.tailers.values(), key=lambda t: t.path, reverse=True):
            tailer.bridge.finish(status, summary)


# --- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="rollout file to follow (default: the newest one)")
    parser.add_argument("--sessions-dir", help="override $CODEX_HOME/sessions")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="drain to EOF and exit")
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="replay the file from its first line instead of following from the end",
    )
    parser.add_argument("--no-subagents", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    directory = args.sessions_dir or sessions_dir()
    path = args.file or newest_rollout(directory)
    if not path or not os.path.isfile(path):
        print(f"no Codex rollout file found under {directory}", file=sys.stderr)
        return 1
    watcher = CodexWatcher(path, directory, follow_subagents=not args.no_subagents)
    tailer = watcher.attach(path)
    if tailer is None:
        print(
            "could not start: set GT_CLOUD_ORIGIN, GT_CLOUD_SESSION and GT_CLOUD_TOKEN "
            "(GT_CLOUD_DEBUG=1 logs why)",
            file=sys.stderr,
        )
        return 2
    if not args.from_start:
        # Follow from the end: a session that has been running for an hour should
        # not replay an hour of tool calls into a live card.
        tailer.offset = os.path.getsize(path)
    try:
        if args.once:
            watcher.poll_once()
        else:
            watcher.run(poll=args.poll)
    finally:
        watcher.finish("done", "codex adapter stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
