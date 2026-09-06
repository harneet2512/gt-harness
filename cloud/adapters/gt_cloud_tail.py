#!/usr/bin/env python3
"""Stream any agent's JSONL output into the GT cloud UI.

This is the honest fallback. When a tool has no hooks and writes no transcript we
can parse, it can usually be made to write JSON lines - and this reads them.

Each input line is one JSON object. A line already shaped like the event
contract passes straight through::

    {"type": "tool_call", "name": "Edit", "files": ["src/app.py"]}
    {"type": "assistant", "text": "Renamed the handler."}
    {"type": "status", "state": "done"}

A line shaped like something else is renamed with ``--map``, which takes
``contract_field=source_field`` pairs::

    gt_cloud_tail.py --file agent.jsonl --map "name=tool,command=cmd,text=message"

Only renaming is supported, deliberately. A mapping language that could compute
values would be a way to smuggle logic into a config file, and the failure mode
of a wrong expression is a card full of plausible nonsense. If a rename is not
enough, write four lines of Python that prints the contract shape and pipe it in.

Reads a file (following it as it grows) or stdin. Unknown, malformed and empty
lines are counted and skipped; the tailer does not stop on them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

if __package__:
    from .gt_cloud_bridge import Bridge, BridgeConfig, debug
else:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gt_cloud_bridge import Bridge, BridgeConfig, debug  # type: ignore[no-redef]

DEFAULT_POLL_SECONDS = 0.5
CONTRACT_TYPES = ("assistant", "tool_call", "tool_result", "status")


class Stats:
    """What the tailer did, for the operator and for the tests."""

    def __init__(self) -> None:
        self.accepted = 0
        self.skipped = 0
        self.malformed = 0

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"Stats(accepted={self.accepted}, skipped={self.skipped}, malformed={self.malformed})"


def parse_map(spec: str | None) -> dict[str, str]:
    """``"name=tool,files=paths"`` becomes ``{"name": "tool", "files": "paths"}``.

    A malformed pair is dropped with a debug line rather than failing the run:
    the alternative is an adapter that refuses to report anything because of a
    stray comma in a config file.
    """
    aliases: dict[str, str] = {}
    if not spec:
        return aliases
    for pair in str(spec).split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            debug(f"ignoring --map entry without '=': {pair!r}")
            continue
        destination, _, source = pair.partition("=")
        destination, source = destination.strip(), source.strip()
        if destination and source:
            aliases[destination] = source
        else:
            debug(f"ignoring empty --map entry: {pair!r}")
    return aliases


def normalise(
    record: Any, aliases: dict[str, str] | None = None, default_type: str | None = None
) -> dict[str, Any] | None:
    """Rename fields and check the ``type``. ``None`` means "skip this line".

    The Bridge validates and truncates every field afterwards, so this only has
    to get the line into the right shape - not to trust it.
    """
    if not isinstance(record, dict):
        return None
    out = dict(record)
    for destination, source in (aliases or {}).items():
        if source in record and destination not in record:
            out[destination] = record[source]
    kind = out.get("type") or default_type
    if kind not in CONTRACT_TYPES:
        return None
    out["type"] = kind
    return out


class JsonlTailer:
    """Feeds JSON lines from a file or a stream into one Bridge."""

    def __init__(
        self,
        bridge: Bridge,
        aliases: dict[str, str] | None = None,
        default_type: str | None = None,
    ) -> None:
        self.bridge = bridge
        self.aliases = aliases or {}
        self.default_type = default_type
        self.stats = Stats()
        self._buffer = ""
        self.offset = 0

    def feed_line(self, line: str) -> bool:
        """Handle one raw line. Returns whether it became an event."""
        if not line.strip():
            return False
        try:
            record = json.loads(line)
        except Exception:
            self.stats.malformed += 1
            return False
        event = normalise(record, self.aliases, self.default_type)
        if event is None:
            self.stats.skipped += 1
            return False
        if self.bridge.emit(event):
            self.stats.accepted += 1
            return True
        self.stats.skipped += 1
        return False

    def feed_text(self, text: str) -> int:
        """Handle a chunk that may end mid-line; the remainder is held over."""
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        return sum(1 for line in lines if self.feed_line(line))

    def poll_file(self, path: str) -> int:
        """Read whatever has been appended to *path* since the last call."""
        try:
            size = os.path.getsize(path)
            if size < self.offset:
                self.offset, self._buffer = 0, ""
            if size == self.offset:
                return 0
            with open(path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(size - self.offset)
            self.offset += len(chunk)
            return self.feed_text(chunk.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            return 0
        except Exception as exc:
            debug(f"poll_file failed for {path}", exc)
            return 0

    def drain_stream(self, stream: Any) -> int:
        """Read a stream to EOF, one line at a time, without buffering it all."""
        count = 0
        try:
            for line in stream:
                if self.feed_line(line):
                    count += 1
        except Exception as exc:
            debug("drain_stream failed", exc)
        return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream JSONL agent events to the GT cloud UI.")
    parser.add_argument("--file", help="JSONL file to follow; omit or use '-' for stdin")
    parser.add_argument("--label", default="external agent", help="name shown on the card")
    parser.add_argument("--task", default=None, help="one-line description of the work")
    parser.add_argument("--cwd", default=None, help="repository root that file paths are under")
    parser.add_argument("--kind", default="other", choices=["claude-code", "codex", "other"])
    parser.add_argument("--parent-agent-id", default=None, help="nest under this external agent")
    parser.add_argument("--map", dest="field_map", default=None, help="contract=source pairs")
    parser.add_argument("--default-type", default=None, choices=list(CONTRACT_TYPES))
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="drain to EOF and exit")
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="replay the file from its first line instead of following from the end",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = args.cwd or os.getcwd()
    bridge = Bridge(
        agent_kind=args.kind,
        label=args.label,
        task=args.task,
        cwd=cwd,
        parent_agent_id=args.parent_agent_id,
        state_key="",  # a tailer is one process, one card: no registration reuse
        config=BridgeConfig.from_env(),
    )
    if not bridge.start():
        print(
            "could not register: set GT_CLOUD_ORIGIN, GT_CLOUD_SESSION and GT_CLOUD_TOKEN "
            "(GT_CLOUD_DEBUG=1 logs why)",
            file=sys.stderr,
        )
        return 2
    tailer = JsonlTailer(bridge, parse_map(args.field_map), args.default_type)
    status = "done"
    try:
        bridge.status("working", activity="Streaming")
        if not args.file or args.file == "-":
            tailer.drain_stream(sys.stdin)
        elif args.once:
            tailer.poll_file(args.file)
        else:
            if not args.from_start and os.path.isfile(args.file):
                tailer.offset = os.path.getsize(args.file)
            while True:
                tailer.poll_file(args.file)
                time.sleep(max(0.05, args.poll))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        debug("tail failed", exc)
        status = "error"
    finally:
        stats = tailer.stats
        bridge.finish(
            status,
            f"{stats.accepted} events, {stats.skipped} skipped, {stats.malformed} malformed",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
