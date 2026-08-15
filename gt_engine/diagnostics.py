"""Typed, repository-bound anchors extracted from execution diagnostics.

The extractor recognizes only mechanically located stack/compiler frames and
then intersects them with the current workspace manifest.  It does not infer a
file or symbol from error prose.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiagnosticAnchor:
    path: str
    line: int
    column: int | None = None
    symbol: str = ""
    kind: str = "compiler_location"


_PYTHON_FRAME = re.compile(
    r'^\s*File\s+["\'](?P<path>[^"\']+)["\'],\s*line\s+(?P<line>\d+)'
    r'(?:,\s*in\s+(?P<symbol>[^\s,]+))?\s*$',
)
_JAVASCRIPT_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<symbol>[^\s(]+)\s+\()?"
    r"(?P<path>(?:[A-Za-z]:)?[^():\s][^():]*):(?P<line>\d+):(?P<column>\d+)\)?\s*$"
)
_GENERIC_LOCATION = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?(?:[^\s:\"'()]+[/\\])?[^\s:\"'()]+\.[A-Za-z0-9]{1,12})"
    r":(?P<line>\d+)(?::(?P<column>\d+))?(?::|\b)"
)


def _canonical_path(path: str, *, cwd: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    normalized_cwd = str(cwd or "").strip().replace("\\", "/").rstrip("/")
    if normalized_cwd and value.startswith(normalized_cwd + "/"):
        value = value[len(normalized_cwd) + 1 :]
    elif value.startswith("/app/"):
        value = value[len("/app/") :]
    while value.startswith("./"):
        value = value[2:]
    value = posixpath.normpath(value)
    if value in {"", ".", ".."} or value.startswith("../") or value.startswith("/"):
        return ""
    return value


def extract_diagnostic_anchors(
    output: str,
    *,
    repository_paths: tuple[str, ...],
    cwd: str = "/app",
    limit: int = 8,
) -> tuple[DiagnosticAnchor, ...]:
    """Return unique current-repository locations in diagnostic order."""

    maximum = max(0, int(limit))
    if maximum == 0:
        return ()
    verified = {
        canonical.lower(): canonical
        for path in repository_paths
        if (canonical := _canonical_path(path, cwd=cwd))
    }
    anchors: list[DiagnosticAnchor] = []
    seen: set[tuple[str, int, int | None, str]] = set()
    for line_text in str(output or "").splitlines():
        match = _PYTHON_FRAME.match(line_text)
        kind = "python_traceback"
        if match is None:
            match = _JAVASCRIPT_FRAME.match(line_text)
            kind = "javascript_stack"
        if match is None:
            match = _GENERIC_LOCATION.search(line_text)
            kind = "compiler_location"
        if match is None:
            continue
        path = _canonical_path(match.group("path"), cwd=cwd)
        canonical = verified.get(path.lower())
        if not canonical:
            continue
        line = int(match.group("line"))
        if line < 1:
            continue
        column_text = match.groupdict().get("column")
        column = int(column_text) if column_text else None
        symbol = str(match.groupdict().get("symbol") or "").strip()
        key = (canonical.lower(), line, column, symbol)
        if key in seen:
            continue
        seen.add(key)
        anchors.append(
            DiagnosticAnchor(
                path=canonical,
                line=line,
                column=column,
                symbol=symbol,
                kind=kind,
            )
        )
        if len(anchors) >= maximum:
            break
    return tuple(anchors)


__all__ = ["DiagnosticAnchor", "extract_diagnostic_anchors"]
