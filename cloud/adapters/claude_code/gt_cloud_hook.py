#!/usr/bin/env python3
"""Report a Claude Code (or Codex) session to the GT cloud UI, from its hooks.

Claude Code and Codex both run *hooks*: a configured command receives one JSON
object on stdin at a point in the session's lifecycle. This script is that
command. It reads the object, maps it onto the cloud's event contract and hands
it to :class:`~cloud.adapters.gt_cloud_bridge.Bridge`.

**One script serves both tools.** Codex 0.153.3 embeds JSON Schemas for its own
hook payloads (titled ``pre-tool-use.command.input``, ``subagent-stop.command.input``
and so on) whose fields are the same as Claude Code's, plus ``turn_id`` and
``model``. See ``docs/cloud/external-agents.md`` for how that was verified and
where the two diverge.

The mapping, verified by running Claude Code 2.1.263 against a capturing hook
(see the docs for the captured payloads):

===================  ==========================================================
``SessionStart``     register the main agent
``UserPromptSubmit`` the first prompt becomes the agent's ``task``
``PreToolUse``       one ``tool_call``; on ``Agent`` it also records the spawn
``SubagentStart``    register a nested agent, ``parent_agent_id`` = the main one
``PostToolUse``      one ``tool_result``, ``ok: true``
``PostToolUseFailure`` one ``tool_result``, ``ok: false``
``SubagentStop``     finish the nested agent
``Stop``             the turn's reply, then ``status: idle``
``SessionEnd``       finish the main agent
===================  ==========================================================

**Attribution is exact, not guessed.** A tool event that fires inside a subagent
carries that subagent's ``agent_id``, and it is the same id that ``SubagentStart``
and ``SubagentStop`` carry. So a ``Glob`` run by an ``Explore`` subagent is
posted to the ``Explore`` card, never to the parent's.

This script exits 0 on every path, prints nothing, and bounds its own runtime.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

if __package__:
    from ..gt_cloud_bridge import (
        Bridge,
        BridgeConfig,
        debug,
        extract_paths_from_command,
        read_registration,
        truncate,
    )
    from ..payloads import command_from_tool_input, paths_from_tool_input
    from .transcript import subagent_transcript_path, tokens_from_transcript
else:  # running as a plain script: `python .../gt_cloud_hook.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gt_cloud_bridge import (  # type: ignore[no-redef]
        Bridge,
        BridgeConfig,
        debug,
        extract_paths_from_command,
        read_registration,
        truncate,
    )
    from payloads import command_from_tool_input, paths_from_tool_input  # type: ignore[no-redef]

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from transcript import (  # type: ignore[no-redef]
        subagent_transcript_path,
        tokens_from_transcript,
    )

# The subagent-spawning tool is named `Agent` on Claude Code 2.1.263. `Task` is
# its older spelling. Codex names the same tool `spawn_agent` and ships `Agent`
# as a matcher alias for it, so both spellings can reach a hook.
SUBAGENT_TOOLS = ("Agent", "Task", "spawn_agent")

# A hook runs inside somebody's tool call. With one attempt at 1.5 s and at most
# two bridges opened per invocation (the agent and, on an Agent call, its child),
# a completely unreachable deployment costs at most this - and then the circuit
# breaker opens and the cost falls to one file read.
DEFAULT_HOOK_DEADLINE = 3.0
MAX_PENDING_SPAWNS = 20
STDIN_LIMIT = 4 * 1024 * 1024


# --- the one-line activity description --------------------------------------


def _basename(path: Any) -> str:
    try:
        return os.path.basename(str(path).replace("\\", "/").rstrip("/")) or str(path)
    except Exception:
        return str(path)


def _command_phrase(command: str | None) -> str:
    """Name the program a shell command runs, not the command line itself."""
    if not command:
        return "Running a command"
    words = command.strip().split()
    if not words:
        return "Running a command"
    program = _basename(words[0])
    if len(words) > 1 and not words[1].startswith("-"):
        return f"Running {program} {words[1]}"[:80]
    return f"Running {program}"[:80]


def describe_activity(tool_name: str, tool_input: Any) -> str:
    """A short human phrase for what the agent is doing right now.

    Derived from the tool and its target, in the voice of a fleet list —
    "Editing cloud/server/routes.py", not the raw command line.
    """
    data = tool_input if isinstance(tool_input, dict) else {}
    paths = paths_from_tool_input(data)
    target = _basename(paths[0]) if paths else None
    if tool_name in SUBAGENT_TOOLS:
        kind = str(data.get("subagent_type") or "subagent")
        what = str(data.get("description") or "a task")
        return f"Delegating to {kind}: {what}"[:200]
    verbs = {
        "Read": "Reading",
        "NotebookEdit": "Editing",
        "Edit": "Editing",
        "MultiEdit": "Editing",
        "Write": "Writing",
    }
    if tool_name in verbs and target:
        return f"{verbs[tool_name]} {target}"
    if tool_name in ("Bash", "PowerShell", "shell", "exec", "shell_command"):
        return _command_phrase(command_from_tool_input(data))
    if tool_name in ("Grep", "Glob", "Search"):
        needle = data.get("pattern") or data.get("query") or ""
        return f"Searching for {str(needle)[:60]}" if needle else "Searching the repository"
    if tool_name == "WebSearch":
        return "Searching the web"
    if tool_name == "WebFetch":
        return f"Fetching {str(data.get('url') or '')[:80]}".strip()
    if tool_name in ("TodoWrite", "update_plan"):
        return "Updating the plan"
    if tool_name in ("apply_patch",) and target:
        return f"Patching {target}"
    return f"Running {tool_name}"[:200]


# --- pending subagent spawns ------------------------------------------------
#
# `PreToolUse` on the `Agent` tool carries the human description of the task;
# `SubagentStart`, which fires next, carries the id but only the agent *type*.
# Nothing links the two, so the description is parked here and claimed by the
# next SubagentStart of the same type. Two subagents of the same type spawned in
# one batch can therefore swap descriptions - an approximation, documented as one.


def _pending_path(config: BridgeConfig, session_id: str) -> str:
    safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "-_")[:64]
    return os.path.join(config.resolved_state_dir(), f"spawn-{safe or 'unknown'}.json")


def _push_pending_spawn(config: BridgeConfig, session_id: str, tool_input: Any) -> None:
    try:
        data = tool_input if isinstance(tool_input, dict) else {}
        entry = {
            "subagent_type": str(data.get("subagent_type") or ""),
            "description": str(data.get("description") or "")[:200],
            "ts": time.time(),
        }
        path = _pending_path(config, session_id)
        try:
            with open(path, encoding="utf-8") as handle:
                pending = json.load(handle)
            if not isinstance(pending, list):
                pending = []
        except Exception:
            pending = []
        pending.append(entry)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(pending[-MAX_PENDING_SPAWNS:], handle)
    except Exception as exc:
        debug("push_pending_spawn failed", exc)


def _pop_pending_spawn(config: BridgeConfig, session_id: str, agent_type: str) -> str | None:
    try:
        path = _pending_path(config, session_id)
        with open(path, encoding="utf-8") as handle:
            pending = json.load(handle)
        if not isinstance(pending, list):
            return None
        for index, entry in enumerate(pending):
            if isinstance(entry, dict) and entry.get("subagent_type") == agent_type:
                pending.pop(index)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(pending, handle)
                return str(entry.get("description") or "") or None
        return None
    except Exception:
        return None


# --- bridges ----------------------------------------------------------------


class HookSession:
    """Resolves the right :class:`Bridge` for one hook invocation."""

    def __init__(self, payload: dict[str, Any], config: BridgeConfig | None = None) -> None:
        self.payload = payload
        self.config = config or BridgeConfig.from_env(hook_mode=True)
        self.session_id = str(payload.get("session_id") or "unknown")
        # CLAUDE_PROJECT_DIR is the directory the session started in; `cwd` can
        # move during a session, and paths must stay relative to one root.
        self.cwd = (
            os.environ.get("CLAUDE_PROJECT_DIR")
            or payload.get("cwd")
            or os.getcwd()
        )
        self.agent_id = payload.get("agent_id") or None
        self.agent_type = str(payload.get("agent_type") or "subagent")
        self._bridges: list[Bridge] = []

    # The state key is what makes a fresh hook process reuse one registration.
    def _key(self, agent_id: str | None) -> str:
        return f"{self.session_id}:{agent_id}" if agent_id else self.session_id

    def main_agent_id(self) -> str | None:
        record = read_registration(self.config, self._key(None))
        return str(record["agent_id"]) if record else None

    def open(self, agent_id: str | None, label: str, task: str | None = None) -> Bridge | None:
        """Register (or reuse) the bridge for this agent. ``None`` if disabled."""
        try:
            parent = self.main_agent_id() if agent_id else None
            bridge = Bridge(
                agent_kind=os.environ.get("GT_CLOUD_AGENT_KIND", "claude-code"),
                label=label,
                task=task,
                cwd=self.cwd,
                parent_agent_id=parent,
                state_key=self._key(agent_id),
                config=self.config,
                background=False,  # a hook process is short-lived: flush explicitly
            )
            if not bridge.start():
                return None
            self._bridges.append(bridge)
            return bridge
        except Exception as exc:
            debug("HookSession.open failed", exc)
            return None

    def current(self) -> Bridge | None:
        """The agent this hook fired inside: the subagent when there is one.

        No ``agent_id`` means the main session. An ``agent_id`` means the hook
        fired inside that subagent - measured on Claude Code 2.1.263, where a
        ``Glob`` run by an ``Explore`` subagent arrives with that subagent's id.
        """
        if not self.agent_id:
            return self.open(None, self._main_label())
        label = self.agent_type
        # Claim a parked description only when the child still needs registering.
        # Doing it on every tool call would steal the label off its siblings.
        if read_registration(self.config, self._key(self.agent_id)) is None:
            label = _pop_pending_spawn(self.config, self.session_id, self.agent_type) or label
        return self.open(self.agent_id, label)

    def _main_label(self) -> str:
        return f"claude-code · {_basename(self.cwd)}"

    def tokens(self) -> int | None:
        """Cumulative tokens for the agent this hook fired inside, or ``None``.

        Hook payloads carry no usage, so this reads the tail of the right
        transcript: the subagent's own when we are inside one, the session's
        otherwise. A child's transcript is either handed to us directly by
        ``SubagentStop`` or derived from the parent's path and checked on disk.
        """
        if self.agent_id:
            path = self.payload.get("agent_transcript_path") or subagent_transcript_path(
                self.payload.get("transcript_path"), self.agent_id
            )
        else:
            path = self.payload.get("transcript_path")
        return tokens_from_transcript(path)

    def flush_all(self, deadline: float) -> None:
        for bridge in self._bridges:
            bridge.flush(deadline=deadline)
            bridge.close()


# --- event handlers ---------------------------------------------------------


def _files_for(payload: dict[str, Any], cwd: Any) -> list[str]:
    """Every path this tool event names: from its input, its response, its command."""
    paths = paths_from_tool_input(payload.get("tool_input"))
    paths += paths_from_tool_input(payload.get("tool_response"))
    response = payload.get("tool_response")
    if isinstance(response, dict):
        # Observed shapes: Glob -> {"filenames": [...]}, Read -> {"file": {"filePath": ...}}
        for name in response.get("filenames") or []:
            if isinstance(name, str):
                paths.append(name)
        nested = response.get("file")
        if isinstance(nested, dict):
            paths += paths_from_tool_input(nested)
    if not paths:
        paths = extract_paths_from_command(command_from_tool_input(payload.get("tool_input")), cwd)
    return paths


def handle(payload: dict[str, Any], config: BridgeConfig | None = None) -> str:
    """Map one hook payload onto the event contract. Returns the event handled."""
    event = str(payload.get("hook_event_name") or "")
    session = HookSession(payload, config)
    deadline = _deadline()

    try:
        if event == "SessionStart":
            bridge = session.open(None, session._main_label())
            if bridge:
                bridge.status("working", note=f"session {payload.get('source') or 'started'}",
                              activity="Starting up")
        elif event == "UserPromptSubmit":
            prompt = truncate(payload.get("prompt"), 2000)
            bridge = session.open(None, session._main_label(), task=prompt)
            if bridge:
                bridge.status("working", activity="Thinking")
        elif event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            _handle_tool(session, payload, event)
        elif event == "SubagentStart":
            label = _pop_pending_spawn(session.config, session.session_id, session.agent_type)
            bridge = session.open(session.agent_id, label or session.agent_type)
            if bridge:
                bridge.status("working", note=f"{session.agent_type} started",
                              activity=label or f"Running {session.agent_type}")
        elif event == "SubagentStop":
            _finish_subagent(session, payload)
            return event
        elif event == "Stop":
            bridge = session.open(None, session._main_label())
            if bridge:
                reply = payload.get("last_assistant_message")
                if reply:
                    bridge.assistant(reply)
                bridge.status("idle", activity="Waiting for input", tokens=session.tokens())
        elif event == "SessionEnd":
            _finish_main(session, payload)
            return event
        else:
            debug(f"ignoring hook event {event!r}")
    finally:
        session.flush_all(deadline)
    return event


def _handle_tool(session: HookSession, payload: dict[str, Any], event: str) -> None:
    tool_name = str(payload.get("tool_name") or "tool")
    tool_input = payload.get("tool_input")
    if event == "PreToolUse" and tool_name in SUBAGENT_TOOLS:
        _push_pending_spawn(session.config, session.session_id, tool_input)
    bridge = session.current()
    if not bridge:
        return
    activity = describe_activity(tool_name, tool_input)
    files = _files_for(payload, session.cwd)
    if event == "PreToolUse":
        bridge.tool_call(
            tool_name,
            command=command_from_tool_input(tool_input),
            files=files,
            activity=activity,
        )
        return
    ok = event == "PostToolUse"
    output = payload.get("tool_response") if ok else payload.get("tool_error")
    bridge.tool_result(tool_name, ok=ok, output=output, files=files)
    # The live counter the fleet list shows. Omitted when the transcript has no
    # usage yet, which is the normal case for the first tool call of a session.
    bridge.status("working", activity=activity, tokens=session.tokens())
    if tool_name in SUBAGENT_TOOLS and ok:
        _finish_completed_subagent(session, payload.get("tool_response"))


def _finish_completed_subagent(session: HookSession, response: Any) -> None:
    """A *foreground* Agent call returns the subagent's result; close its card.

    A backgrounded subagent - the default since Claude Code v2.1.198 - returns
    ``status: "async_launched"`` here and finishes at ``SubagentStop`` instead.
    """
    if not isinstance(response, dict) or response.get("status") != "completed":
        return
    agent_id = response.get("agentId")
    if not agent_id:
        return
    bridge = session.open(str(agent_id), session.agent_type)
    if not bridge:
        return
    blocks = response.get("content")
    summary = None
    if isinstance(blocks, list):
        summary = " ".join(
            str(block.get("text", "")) for block in blocks if isinstance(block, dict)
        ).strip()
    tokens = response.get("totalTokens")
    if tokens is not None:
        bridge.status("done", activity="Finished", tokens=tokens)
    bridge.finish("done", summary or "subagent finished")


def _finish_subagent(session: HookSession, payload: dict[str, Any]) -> None:
    bridge = session.open(session.agent_id, session.agent_type)
    if bridge:
        bridge.status("done", activity="Finished", tokens=session.tokens())
        bridge.finish("done", payload.get("last_assistant_message"))


def _finish_main(session: HookSession, payload: dict[str, Any]) -> None:
    bridge = session.open(None, session._main_label())
    if bridge:
        bridge.status("done", activity="Finished", tokens=session.tokens())
        bridge.finish("done", f"session ended ({payload.get('reason') or 'other'})")


# --- entry point ------------------------------------------------------------


def _deadline() -> float:
    try:
        raw = os.environ.get("GT_CLOUD_HOOK_DEADLINE", "").strip()
        return max(0.5, float(raw)) if raw else DEFAULT_HOOK_DEADLINE
    except Exception:
        return DEFAULT_HOOK_DEADLINE


def _start_watchdog() -> None:
    """Guarantee the hook returns, even if something below us hangs.

    Every network call already has its own timeout, so this should never fire.
    It exists because a hook that hangs stalls the user's agent, and "should
    never" is not a guarantee. It exits **0**: a watchdog must not turn a slow
    report into a failed tool call.
    """
    if os.environ.get("GT_CLOUD_HOOK_NO_WATCHDOG", "").strip() == "1":
        return
    limit = _deadline() + 1.5

    def _bite() -> None:
        time.sleep(limit)
        debug(f"hook watchdog fired after {limit}s; exiting 0")
        sys.stderr.flush()
        os._exit(0)

    threading.Thread(target=_bite, name="gt-cloud-hook-watchdog", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    """Read the hook payload on stdin and report it. Always returns 0.

    Exit code 0 with empty stdout is "no decision" for every Claude Code and
    Codex hook event, so this script can never block a tool call, reject a
    prompt or fail a session.
    """
    del argv
    try:
        _start_watchdog()
        raw = sys.stdin.read(STDIN_LIMIT)
        payload = json.loads(raw) if raw.strip() else {}
        if isinstance(payload, dict):
            handle(payload)
        else:
            debug("hook payload was not a JSON object")
    except Exception as exc:
        debug("hook failed", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
