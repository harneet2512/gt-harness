"""Read a cumulative token count out of a Claude Code transcript.

Claude Code hooks carry no token counts. The transcript at ``transcript_path``
does: it is JSONL, and each ``{"type": "assistant"}`` record holds
``message.usage``. Measured verbatim on Claude Code 2.1.263::

    {"input_tokens": 8, "cache_creation_input_tokens": 227,
     "cache_read_input_tokens": 39541, "output_tokens": 60,
     "output_tokens_details": {"thinking_tokens": 41}, ...}

**The count is the last assistant record's four totals added together**, not a
sum across records. Each record's `usage` is that one API call's accounting, and
the last call re-reads the whole conversation out of the prompt cache, so
summing every record multiplies the cached prefix by the number of calls. One
record is an under-count of output tokens across the session and an honest count
of what the last call cost; it is the only figure here that is not invented.

A subagent has its own transcript, in a ``subagents/`` folder beside the
parent's, named ``agent-<agent_id>.jsonl`` - verified on disk, and also handed to
``SubagentStop`` directly as ``agent_transcript_path``.

Everything here is defensive by construction. The file is being appended to
while we read it, its last line is routinely half-written, and most of its
records are not assistant records at all (``attachment``, ``queue-operation``,
``last-prompt``, ``user``, ``system``). A parse failure returns ``None``.
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["subagent_transcript_path", "tokens_from_transcript"]

# Reading only the tail bounds the cost on a session whose transcript is tens of
# megabytes. The last assistant record is at the end by definition.
TAIL_BYTES = 512 * 1024

_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def _tail_lines(path: str, limit: int = TAIL_BYTES) -> list[bytes]:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - limit)
        handle.seek(start)
        chunk = handle.read()
    lines = chunk.split(b"\n")
    if start > 0 and lines:
        # The first line is a fragment of whatever record straddled the window.
        lines.pop(0)
    return lines


def tokens_from_transcript(path: Any) -> int | None:
    """Cumulative tokens for the last model call in this transcript, or ``None``.

    ``None`` means "do not report a number": no file, no assistant record in the
    tail, no usage block, or anything unparseable. It never raises.
    """
    if not path:
        return None
    try:
        text_path = str(path)
        if not os.path.isfile(text_path):
            return None
        for raw in reversed(_tail_lines(text_path)):
            if b'"assistant"' not in raw:
                continue
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                continue  # a half-written line at the end of a live file
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            total = 0
            seen = False
            for field in _USAGE_FIELDS:
                value = usage.get(field)
                if isinstance(value, int) and value >= 0:
                    total += value
                    seen = True
            if seen:
                return total
        return None
    except Exception:
        return None


def subagent_transcript_path(transcript_path: Any, agent_id: Any) -> str | None:
    """Where a subagent's own transcript lives, if it is on disk.

    Verified layout: ``<project>/<session_id>/subagents/agent-<agent_id>.jsonl``,
    beside the parent's ``<project>/<session_id>.jsonl``. Returns ``None`` unless
    the file actually exists, so a layout change degrades to "no token count"
    rather than to a wrong one.
    """
    if not transcript_path or not agent_id:
        return None
    try:
        parent = str(transcript_path)
        base, extension = os.path.splitext(parent)
        candidate = os.path.join(base, "subagents", f"agent-{agent_id}{extension or '.jsonl'}")
        return candidate if os.path.isfile(candidate) else None
    except Exception:
        return None
