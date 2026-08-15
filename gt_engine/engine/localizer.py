"""Deterministic graph-backed localizer (the 'depth' GT was missing).

The gateway's `_localize` is None in production (the embedding-backed localizer
is an isolated comparison control), so `_produce_ranked_localization` always
abstained — localization could NEVER fire despite a populated graph. This module
implements the deterministic replacement: extract the issue's significant
identifiers, match them against the graph's FTS5 node index, and return ranked
candidate files. The engine injects it as `gateway._localize` at startup.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

_STOPWORDS = {
    "the", "and", "for", "are", "with", "that", "this", "you", "should",
    "must", "from", "have", "not", "was", "will", "can", "all", "into",
    "out", "its", "has", "but", "any", "your", "our", "their", "them",
    "fix", "add", "make", "need", "want", "issue", "task", "function",
    "when", "where", "what", "using", "used", "use", "does", "doesn",
}


def significant_issue_tokens(issue_text: str, max_tokens: int = 10) -> list[str]:
    """Extract the issue's distinctive identifiers for FTS5 matching.

    CamelCase splits (issue names/symbols), identifiers, and file paths.
    Stopwords and short tokens are dropped.
    """
    tokens: set[str] = set()
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{3,}", issue_text or ""):
        tokens.add(m.group(0))
    for m in re.finditer(r"([a-z0-9]+)([A-Z][a-z]+)", issue_text or ""):
        tokens.add(m.group(1))
        tokens.add(m.group(2))
    for m in re.finditer(r"[\w./-]+\.(?:py|js|ts|go|rs|c|h|rb|java|scm)", issue_text or ""):
        path = m.group(0)
        tokens.add(path)
        basename = path.rsplit("/", 1)[-1]
        tokens.add(basename)
        tokens.add(basename.rsplit(".", 1)[0])  # bottle.py -> bottle
    filtered = [
        t for t in tokens
        if t.lower() not in _STOPWORDS and len(t) >= 3
    ]
    # rank file basenames and symbol-like identifiers first (highest FTS5
    # signal); generic issue-narrative words last.
    def _rank(t):
        has_ext = "." in t.rsplit("/", 1)[-1]
        is_symbol = any(c.isupper() for c in t[1:]) or "_" in t
        return (not (has_ext or is_symbol), t.lower())

    ordered = sorted(filtered, key=_rank)
    return ordered[:max_tokens]


def _sql_escape(value: str) -> str:
    return (value or "").replace("'", "''").replace("%", "").replace("_", "\\_")


@dataclass
class _Candidate:
    file_path: str = ""
    symbol: str = ""
    line: int = 1


@dataclass
class _LocalizeResult:
    candidates: list[Any] = field(default_factory=list)
    anchor_symbols: list[str] = field(default_factory=list)


def deterministic_localize(issue_text: str, graph_db: str, repo_root: str) -> Any:
    """Rank candidate files by matching the issue's identifiers against the
    graph's FTS5 node index. Correct-or-quiet: no tokens / no graph / no FTS
    match -> empty candidates (never a fabricated answer)."""
    tokens = significant_issue_tokens(issue_text)
    if not tokens or not graph_db or not os.path.isfile(graph_db):
        return _LocalizeResult()
    try:
        con = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
        try:
            raw_rows: list[tuple[str, str]] = []
            try:
                match = " OR ".join(f'"{t}"' for t in tokens[:8])
                raw_rows = con.execute(
                    "SELECT file_path, name FROM nodes_fts "
                    "WHERE nodes_fts MATCH ? LIMIT 60",
                    (match,),
                ).fetchall()
            except sqlite3.Error:
                raw_rows = []  # no FTS table; use the LIKE fallback below
            if not raw_rows:
                # FTS5 fallback: match the file basename literally against the
                # nodes' file_path (robust when the issue phrases a path FTS5
                # tokenizes away, e.g. "/app/bottle.py").
                like = " OR ".join(
                    f"file_path LIKE '%{_sql_escape(t.rsplit('/', 1)[-1])}%'"
                    for t in tokens[:6]
                )
                try:
                    raw_rows = con.execute(
                        f"SELECT file_path, name FROM nodes WHERE {like} LIMIT 60"
                    ).fetchall()
                except sqlite3.Error:
                    raw_rows = []
            # resolve line + is_test from the nodes table (nodes_fts has no line)
            resolved = []
            for file_path, name in raw_rows:
                line_row = con.execute(
                    "SELECT start_line, is_test FROM nodes "
                    "WHERE name = ? AND file_path = ? LIMIT 1",
                    (name, file_path),
                ).fetchone()
                line, is_test = (line_row if line_row else (1, 0))
                resolved.append(
                    (str(file_path), int(line or 1), str(name or ""), int(is_test or 0))
                )
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return _LocalizeResult()
    counts: dict[str, list[tuple[str, int, str, int]]] = {}
    anchors: set[str] = set()
    for file_path, line, name, is_test in resolved:
        key = str(file_path or "").lower()
        if not key:
            continue
        counts.setdefault(key, []).append(
            (str(file_path), int(line or 1), str(name or ""), int(is_test or 0))
        )
        anchors.add(str(name or ""))
    ranked = sorted(
        counts.items(),
        key=lambda kv: (-len(kv[1]), min(r[3] for r in kv[1])),
    )
    candidates: list[_Candidate] = []
    seen: set[str] = set()
    for _key, entries in ranked:
        entries = sorted(entries, key=lambda r: (r[3], r[1]))  # non-test first
        for file_path, line, name, _is_test in entries:
            if file_path not in seen:
                seen.add(file_path)
                candidates.append(
                    _Candidate(file_path=file_path, symbol=name or "", line=line)
                )
                break
        if len(candidates) >= 8:
            break
    return _LocalizeResult(
        candidates=candidates,
        anchor_symbols=sorted(a for a in anchors if a),
    )
