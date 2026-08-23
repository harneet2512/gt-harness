"""Deterministic parser for line-oriented coding-agent transcripts.

The parser is scaffold-neutral.  Every non-blank line is either consumed by
an explicitly recognized structure or retained in ``unparsed``; audit callers
therefore cannot turn an unknown output shape into a false green result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PANEL_TOP_RE = re.compile(r"^\s*╭─+(?: (.+?) )?─*╮\s*$")
_PANEL_BOTTOM_RE = re.compile(r"^\s*╰─+╯\s*$")
_PANEL_CONTENT_RE = re.compile(r"^\s*│ ?(.*?) ?│\s*$")
_STOP_RE = re.compile(
    r"^stop:\s*(?P<reason>\S+)\s+iterations=(?P<iters>\d+)\s+in=(?P<in>\d+)"
    r"\s+out=(?P<out>\d+)\s+cache_read=(?P<cache>\d+)\s*$"
)
_STATS_RE = re.compile(r"^iter=(?P<iter>\d+)\s+in=(?P<in>\d+)\s+out=(?P<out>\d+)\s*$")
_SETUP_ERROR_RE = re.compile(r"^setup error:")
_GT_L1_RE = re.compile(r"^\[GT L1\] ")
_GRAPH_BUILD_DIAGNOSTIC_RE = re.compile(
    r"(?i)^(?:GroundTruth:\s+gt-index failed:.*|Found \d+ source files,?\s*|"
    r"Pass [12]:|python:\s+\d+ files,?\s*|Parsed \d+/\d+ files.*|"
    r".*INDEX FAILED:.*|.*workers\)\.*)$"
)
_KNOWN_PANEL_TITLES = {
    "assistant",
    "tool_call",
    "tool_result",
    "tool_result (error)",
    "final",
}


@dataclass(frozen=True, slots=True)
class Panel:
    title: str
    lines: tuple[str, ...]
    start_line: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(slots=True)
class Transcript:
    panels: list[Panel] = field(default_factory=list)
    stop: dict[str, int | str] | None = None
    stats: list[dict[str, int]] = field(default_factory=list)
    setup_error: str | None = None
    gt_l1_lines: int = 0
    run_receipts: list[dict] = field(default_factory=list)
    unparsed: list[tuple[int, str]] = field(default_factory=list)
    unparsed_structures: list[str] = field(default_factory=list)


def _extract_run_receipts(text: str) -> tuple[str, list[dict]]:
    lines = text.splitlines()
    cleaned = list(lines)
    receipts: list[dict] = []
    ignored: set[int] = set()
    decoder = json.JSONDecoder()
    for start, line in enumerate(lines):
        if start in ignored or line.strip() != "{":
            continue
        segment = "\n".join(lines[start:])
        try:
            value, end = decoder.raw_decode(segment)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("schema") != "gt.run_receipt.v1":
            continue
        consumed = segment[:end].count("\n") + 1
        receipts.append(value)
        for index in range(start, min(len(lines), start + consumed)):
            ignored.add(index)
            cleaned[index] = ""
    return "\n".join(cleaned), receipts


def parse_transcript(text: str) -> Transcript:
    """Parse known transcript structures and retain every unknown line."""
    normalized, receipts = _extract_run_receipts(_ANSI_RE.sub("", text))
    transcript = Transcript(run_receipts=receipts)
    panel_title: str | None = None
    panel_lines: list[str] = []
    panel_start = 0

    def close_panel(*, truncated: bool = False) -> None:
        nonlocal panel_title, panel_lines, panel_start
        if panel_title is None:
            return
        transcript.panels.append(Panel(panel_title, tuple(panel_lines), panel_start))
        if truncated:
            transcript.unparsed_structures.append(
                f"line {panel_start}: panel '{panel_title}' not closed"
            )
        panel_title = None
        panel_lines = []
        panel_start = 0

    for line_number, raw in enumerate(normalized.splitlines(), start=1):
        line = raw.rstrip("\r")
        if panel_title is not None:
            if _PANEL_BOTTOM_RE.match(line):
                close_panel()
                continue
            content = _PANEL_CONTENT_RE.match(line)
            if content:
                panel_lines.append(content.group(1).rstrip())
                continue
            if _PANEL_TOP_RE.match(line):
                close_panel(truncated=True)
            else:
                transcript.unparsed.append((line_number, line))
                continue
        top = _PANEL_TOP_RE.match(line)
        if top:
            panel_title = (top.group(1) or "").strip()
            panel_start = line_number
            if panel_title not in _KNOWN_PANEL_TITLES:
                transcript.unparsed_structures.append(
                    f"line {line_number}: unknown panel title '{panel_title}'"
                )
            continue
        stripped = line.strip()
        if not stripped:
            continue
        stop = _STOP_RE.match(stripped)
        if stop:
            transcript.stop = {
                "reason": stop.group("reason"),
                "iterations": int(stop.group("iters")),
                "in_tokens": int(stop.group("in")),
                "out_tokens": int(stop.group("out")),
                "cache_read": int(stop.group("cache")),
            }
            continue
        stats = _STATS_RE.match(stripped)
        if stats:
            transcript.stats.append(
                {
                    "iteration": int(stats.group("iter")),
                    "in_tokens": int(stats.group("in")),
                    "out_tokens": int(stats.group("out")),
                }
            )
            continue
        if _SETUP_ERROR_RE.match(stripped):
            transcript.setup_error = stripped
            continue
        if _GT_L1_RE.match(line):
            transcript.gt_l1_lines += 1
            continue
        if _GRAPH_BUILD_DIAGNOSTIC_RE.match(stripped):
            continue
        transcript.unparsed.append((line_number, line))
    if panel_title is not None:
        close_panel(truncated=True)
        transcript.unparsed_structures[-1] += " (EOF)"
    return transcript


__all__ = ["Panel", "Transcript", "parse_transcript"]
