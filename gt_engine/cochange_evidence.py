"""Co-change priors: evolutionary coupling delivered as advisory evidence.

``cochanges`` has been in the graph schema since the graph was first written,
and ``cochange_prior`` -- one of the four :data:`GRAPH_BACKED_FEATURES` -- has
never fired, because it was dead at two layers at once: the engine had no
emitter for it, and no capability pack allowed it. This module is the emitter.

WHAT THE TABLE ACTUALLY HOLDS
-----------------------------
The producer schema (``gt-index/internal/store/sqlite.go``) is exactly three
columns::

    file_a TEXT NOT NULL
    file_b TEXT NOT NULL
    count  INTEGER NOT NULL DEFAULT 1
    PRIMARY KEY(file_a, file_b)

``count`` **is** the pair support: the producer's ``cochange.Persist`` writes
``Pair.Support`` into it and records ConfidenceAToB, ConfidenceBToA, CommitsA
and CommitsB as a KNOWN LOSS with nowhere to go. There is likewise no column
for the extraction window. This module therefore reports ``support`` and
``count`` as the one stored quantity they are, and reports the window as
``unrecorded`` unless a caller passes a recorded one. It does not reconstruct
a window, a confidence or a recency the graph does not contain.

A PRIOR IS NEVER A RESOLUTION
-----------------------------
Evolutionary coupling is known not to be congruent with structural coupling:
two files that change together need not refer to each other. Every rendered
line is marked ``status=prior_not_resolution``, is staged ``advisory``, and is
built from a read of ``cochanges`` alone -- this module issues no query against
``edges``, ``nodes`` or any resolution table, so a co-change prior structurally
cannot promote a candidate edge to a verified one.

Correct-or-quiet: no graph, no table, no rows, no repository-relative file, or
no partner all yield ``()``/``""``. An empty ``cochanges`` table -- which is
what every depth-1 fixture clone has -- is silence, never an error.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from gt_engine.miniswe_covering import _repo_relative

COCHANGE_EVIDENCE_TYPE = "cochange_partner"
COCHANGE_FEATURE = "cochange_prior"

# The window is a property of the extraction run, not of the table. Until a
# co-change receipt is published alongside the graph there is nothing honest to
# print but the absence itself.
COCHANGE_WINDOW_UNRECORDED = "unrecorded"

# Dose bounds. A prior is the weakest signal GT delivers, so it gets the
# smallest footprint: a handful of partners for a couple of files, inside the
# same 600-byte envelope the new-file precedent uses.
MAX_PARTNERS_PER_FILE = 3
MAX_FILES_PER_DOSE = 2
COCHANGE_DOSE_BYTE_LIMIT = 600

# One statement, one table. Named as a constant so that any future query
# against another surface is a visible diff rather than a silent widening of
# what a "prior" is allowed to read.
_PARTNER_SQL = (
    "SELECT file_a, file_b, count FROM cochanges "
    "WHERE (file_a = ? OR file_b = ?) AND file_a <> file_b "
    "ORDER BY count DESC, file_a ASC, file_b ASC LIMIT ?"
)
_COUNT_SQL = "SELECT COUNT(*) FROM cochanges"

__all__ = [
    "COCHANGE_DOSE_BYTE_LIMIT",
    "COCHANGE_EVIDENCE_TYPE",
    "COCHANGE_FEATURE",
    "COCHANGE_WINDOW_UNRECORDED",
    "MAX_FILES_PER_DOSE",
    "MAX_PARTNERS_PER_FILE",
    "CochangePartner",
    "cochange_partners",
    "cochange_prior_dose",
    "cochange_row_count",
    "render_cochange_prior",
    "run_cochange_prior",
]


@dataclass(frozen=True)
class CochangePartner:
    """One ``cochanges`` row, read from the perspective of ``file_path``."""

    file_path: str
    partner: str
    count: int
    support: int
    window: str
    provenance: str
    commits_file: int | None = None
    commits_partner: int | None = None
    confidence: float | None = None


def _connect(graph_db: str) -> sqlite3.Connection | None:
    """Open the graph read-only, or return None. A missing graph is quiet."""
    if not graph_db or not os.path.isfile(graph_db):
        return None
    try:
        return sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return None


def cochange_row_count(graph_db: str) -> int:
    """Rows in ``cochanges``, failing closed to zero.

    Zero is the honest answer for a graph with no table, an unreadable graph
    and a graph built from a depth-1 clone alike: in all three cases no prior
    can be grounded, and the enforcement gate must not count one.
    """
    con = _connect(graph_db)
    if con is None:
        return 0
    try:
        return int(con.execute(_COUNT_SQL).fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError):
        return 0
    finally:
        con.close()


def cochange_partners(
    graph_db: str,
    file_path: str,
    *,
    limit: int = MAX_PARTNERS_PER_FILE,
    window: str | None = None,
) -> tuple[CochangePartner, ...]:
    """The recorded co-change companions of one repository-relative file.

    The primary key is an ordered pair but the coupling relation is not, so
    both columns are searched and the stored key order is preserved in the
    provenance: a reader can go back to the exact row from either end.
    """
    normalized = str(file_path or "").replace("\\", "/")
    if not normalized or limit <= 0:
        return ()
    con = _connect(graph_db)
    if con is None:
        return ()
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(cochanges)")}
        has_directional = {
            "commits_a", "commits_b", "confidence_a_to_b", "confidence_b_to_a"
        }.issubset(columns)
        sql = (
            "SELECT file_a, file_b, count, commits_a, commits_b, "
            "confidence_a_to_b, confidence_b_to_a FROM cochanges "
            if has_directional else "SELECT file_a, file_b, count FROM cochanges "
        ) + (
            "WHERE (file_a = ? OR file_b = ?) AND file_a <> file_b "
            "ORDER BY count DESC, file_a ASC, file_b ASC LIMIT ?"
        )
        rows = con.execute(sql, (normalized, normalized, int(limit))).fetchall()
        resolved_window = str(window or "")
        if not resolved_window:
            try:
                meta = dict(
                    con.execute(
                        "SELECT key,value FROM project_meta WHERE key IN (?,?)",
                        (
                            "derived_cochange_window_start",
                            "derived_cochange_window_end",
                        ),
                    )
                )
                start = str(meta.get("derived_cochange_window_start") or "")
                end = str(meta.get("derived_cochange_window_end") or "")
                resolved_window = f"{start}..{end}" if start and end else ""
            except sqlite3.Error:
                resolved_window = ""
    except sqlite3.Error:
        return ()
    finally:
        con.close()
    out: list[CochangePartner] = []
    for row in rows:
        file_a, file_b, count = row[:3]
        partner = str(file_b) if str(file_a) == normalized else str(file_a)
        support = int(count or 0)
        commits_file = commits_partner = None
        confidence = None
        if len(row) == 7:
            commits_a, commits_b, confidence_a_to_b, confidence_b_to_a = row[3:]
            from_a = str(file_a) == normalized
            raw_file = commits_a if from_a else commits_b
            raw_partner = commits_b if from_a else commits_a
            raw_confidence = confidence_a_to_b if from_a else confidence_b_to_a
            if (
                raw_file is not None
                and raw_partner is not None
                and raw_confidence is not None
                and int(raw_file) > 0
                and int(raw_partner) > 0
            ):
                commits_file = int(raw_file)
                commits_partner = int(raw_partner)
                confidence = float(raw_confidence)
        out.append(
            CochangePartner(
                file_path=normalized,
                partner=partner,
                # One stored column, reported under both names it answers to.
                count=support,
                support=support,
                window=resolved_window or COCHANGE_WINDOW_UNRECORDED,
                provenance=f"cochanges(file_a={file_a},file_b={file_b})",
                commits_file=commits_file,
                commits_partner=commits_partner,
                confidence=confidence,
            )
        )
    # SQL orders the LIMITed selection by stored key; presentation orders by
    # the quantity a reader compares, with the partner path as the tie-break.
    out.sort(key=lambda item: (-item.support, item.partner))
    return tuple(out)


def render_cochange_prior(
    partners: tuple[CochangePartner, ...], *, revision: str = ""
) -> str:
    """Render partners as one line each, each line naming its own row."""
    return "\n".join(
        f"{item.file_path}: co-change prior"
        + (f" revision={revision}" if revision else "")
        + f" partner={item.partner}"
        f" count={item.count} support={item.support}"
        + (
            f" confidence={item.confidence:.8f}"
            f" commits_file={item.commits_file} commits_partner={item.commits_partner}"
            if item.confidence is not None
            and item.commits_file is not None
            and item.commits_partner is not None
            else " confidence=unrecorded"
        )
        + f" window={item.window} provenance={item.provenance}"
        + " status=prior_not_resolution"
        for item in partners
    )


def run_cochange_prior(
    adapter,
    files: tuple[str, ...],
    *,
    limit_files: int = MAX_FILES_PER_DOSE,
    limit_partners: int = MAX_PARTNERS_PER_FILE,
) -> str:
    """``cochange_prior`` for the files an action viewed or edited.

    Mirrors :func:`gt_engine.miniswe_covering.run_newfile_precedent`: bounded,
    LLM-free, correct-or-quiet, and returning rendered bytes for the seam to
    dose. A file outside the repository root is dropped, not queried.
    """
    if getattr(adapter, "graph_fresh", True) is False:
        return ""
    graph_db = str(getattr(adapter, "graph_db", "") or "")
    if not graph_db or not files:
        return ""
    repo_root = str(getattr(adapter, "repo_root", "") or "")
    revision = str(getattr(adapter, "repository_revision", "") or "unknown")
    window = str(getattr(adapter, "cochange_window", "") or "") or None
    lines: list[str] = []
    seen: set[str] = set()
    for raw in files:
        if len(seen) >= max(1, limit_files):
            break
        relative = _repo_relative(raw, repo_root) if repo_root else None
        if not relative or relative in seen:
            continue
        seen.add(relative)
        partners = cochange_partners(
            graph_db, relative, limit=limit_partners, window=window
        )
        rendered = render_cochange_prior(partners, revision=revision)
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)


def _whole_lines_within(rendered: str, limit: int) -> str:
    """Keep as many complete lines as fit. A half-line is a half-claim.

    Byte-truncating the render would leave a partial provenance -- a row key cut
    mid-path -- which reads as a malformed fact rather than as a dropped one.
    """
    kept: list[str] = []
    used = 0
    for line in rendered.splitlines():
        cost = len(line.encode("utf-8")) + (1 if kept else 0)
        if used + cost > limit:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept)


def cochange_prior_dose(adapter, files: tuple[str, ...]) -> str:
    """Stage and render one advisory co-change dose, or return ``""``.

    The dose is staged ``advisory`` and carries the evidence tag the trajectory
    census reads, so a delivered prior is attributable without heuristics.
    Nothing is staged unless a complete line survives the byte ceiling.
    """
    rendered = _whole_lines_within(
        run_cochange_prior(adapter, files), COCHANGE_DOSE_BYTE_LIMIT
    )
    if not rendered:
        return ""
    stage = getattr(adapter, "stage_model_visible_delivery", None)
    if callable(stage):
        stage(
            kind=COCHANGE_EVIDENCE_TYPE,
            dedup_key=(
                f"cochange-{files[0]}-"
                f"{getattr(adapter, 'repository_revision', '') or 'unknown'}"
            ),
            target=files[0],
            semantics="advisory",
        )
    return f"[GT_EVIDENCE:{COCHANGE_EVIDENCE_TYPE}]\n{rendered}"
