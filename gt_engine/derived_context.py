"""Community membership and process participation from the derived tables.

The Go producer (``vendor/gt-index-src``) derives and persists two layers the
Python consumer never read: the ``communities``/``community_members`` Leiden
partition of repository files, and the ``processes``/``process_steps``
test-witnessed interprocedural slices. This module is the read side -- the
same role :mod:`gt_engine.cochange_evidence` plays for ``cochanges``.

WHAT THE TABLES ACTUALLY HOLD
-----------------------------
``community_members.member`` is a *file path* (``member_kind='file'``;
``internal/community/persist.go``). A symbol reaches its community through
``nodes.file_path``, never through a stable id. ``communities.cohesion`` is
nullable on purpose: an unmeasurable held-out cohesion is stored as NULL with
``cohesion_reason`` explaining it -- never 0.0, which would read as a measured
total failure.

``process_steps.stable_id`` is the producer's *effective* stable id,
``COALESCE(NULLIF(nodes.stable_id,''), resolution_symbols.stable_id)`` with
``resolution_symbols.native_id = nodes.id`` (``internal/process/process.go``
``readStableIDs``). On a real graph every Function/Method/Class node has
``nodes.stable_id`` NULL, so the join MUST go through that same expression or
it names nothing. A process has no name column; its readable identity is the
entry symbol's ``resolution_symbols.qualified_name`` (the assertion target),
falling back to the stored ``entry_stable_id``.

THE FRESHNESS GATE
------------------
Both layers are published under ``project_meta`` keys
``derived_community_state`` / ``derived_process_state`` (and the count markers
``derived_community_count``/``derived_community_members``/
``derived_process_count``/``derived_process_steps``). ``ok`` is the only state
that admits rows. Everything else -- ``not_run``, ``disabled_by_operator``,
the packages' own reason constants, every failure state, and an absent key --
means the table contents are not a published partition and are not served.
When a count marker is recorded it must equal the live row count, the same
contract ``_closure_is_fresh`` applies to ``closure_count``: a mismatch is
staleness evidence, reported as ``count_mismatch`` rather than served.

Correct-or-quiet throughout: a missing graph, missing tables, an absent key,
a degraded state or a query fault all yield typed empty output, never a
fabricated partition and never an exception a caller has to catch.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DERIVED_STATE_OK = "ok"
STATE_UNRECORDED = "unrecorded"
STATE_TABLE_ABSENT = "table_absent"
STATE_COUNT_MISMATCH = "count_mismatch"
STATE_GRAPH_ABSENT = "graph_absent"

COMMUNITY_STATE_KEY = "derived_community_state"
PROCESS_STATE_KEY = "derived_process_state"

MAX_PROCESSES_PER_SYMBOL = 8

__all__ = [
    "COMMUNITY_STATE_KEY",
    "DERIVED_STATE_OK",
    "MAX_PROCESSES_PER_SYMBOL",
    "PROCESS_STATE_KEY",
    "STATE_COUNT_MISMATCH",
    "STATE_GRAPH_ABSENT",
    "STATE_TABLE_ABSENT",
    "STATE_UNRECORDED",
    "CommunityMembership",
    "DerivedContext",
    "ProcessParticipation",
    "derived_layer_states",
    "symbol_derived_context",
]


@dataclass(frozen=True)
class CommunityMembership:
    """One ``community_members`` row resolved through ``communities``."""

    community_id: str
    name: str
    cohesion: float | None
    cohesion_reason: str
    member_count: int
    provenance: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cohesion": self.cohesion,
            "member_count": self.member_count,
        }


@dataclass(frozen=True)
class ProcessParticipation:
    """One ``process_steps`` row: this symbol is a step in a witnessed path."""

    process_id: str
    name: str
    kind: str
    step_index: int
    step_count: int
    trust_floor: str
    provenance: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "step_count": self.step_count,
            "step_index": self.step_index,
        }


@dataclass(frozen=True)
class DerivedContext:
    """What the derived layers recorded about one queried symbol.

    ``states`` names each layer's admission state verbatim so a reader can
    always tell "published, not a member" from "never published". The typed
    fields stay empty in every state but ``ok``.
    """

    community: CommunityMembership | None
    processes: tuple[ProcessParticipation, ...]
    states: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "community": (
                self.community.as_dict() if self.community is not None else None
            ),
            "processes": [item.as_dict() for item in self.processes],
            "derived_states": dict(self.states),
        }


def _open(
    db: str | Path | sqlite3.Connection,
) -> tuple[sqlite3.Connection | None, bool]:
    """Open the graph read-only. A passed-in connection is never closed."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    path = os.fspath(db)
    if not path or not os.path.isfile(path):
        return None, False
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True), True
    except (sqlite3.Error, OSError):
        return None, False


def _tables(con: sqlite3.Connection) -> set[str]:
    try:
        return {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
    except sqlite3.Error:
        return set()


def _project_meta(con: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(key): str(value)
            for key, value in con.execute("SELECT key,value FROM project_meta")
        }
    except sqlite3.Error:
        return {}


def _layer_state(
    con: sqlite3.Connection,
    tables: set[str],
    meta: Mapping[str, str],
    *,
    state_key: str,
    required_tables: tuple[str, ...],
    count_markers: tuple[tuple[str, str], ...],
) -> str:
    """One layer's admission state, verbatim or a named degraded condition.

    The recorded state is the admission ticket: anything but ``ok`` --
    including no record at all -- is returned as recorded and serves no rows.
    Only after ``ok`` do the structural checks run, mirroring the
    ``closure_count`` contract: a recorded count that fails to parse or
    disagrees with the live table is staleness evidence, not data.
    """
    recorded = str(meta.get(state_key) or "").strip()
    if recorded != DERIVED_STATE_OK:
        return recorded or STATE_UNRECORDED
    if any(table not in tables for table in required_tables):
        return STATE_TABLE_ABSENT
    for meta_key, table in count_markers:
        raw = meta.get(meta_key)
        if raw is None:
            # A producer that predates the count markers still published a
            # state; absence of a marker is not, by itself, staleness evidence.
            continue
        try:
            recorded_count = int(str(raw).strip())
        except (TypeError, ValueError):
            return STATE_COUNT_MISMATCH
        try:
            live = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except sqlite3.Error:
            return STATE_COUNT_MISMATCH
        if live != recorded_count:
            return STATE_COUNT_MISMATCH
    return DERIVED_STATE_OK


def derived_layer_states(
    con: sqlite3.Connection, tables: set[str] | None = None
) -> dict[str, str]:
    """The recorded admission state of each derived layer.

    ``community``/``process`` map to ``ok``, the producer's recorded state
    verbatim (``not_run``, ``disabled_by_operator``, a package reason, a
    failure state), or a reader-named degraded condition:
    ``unrecorded``/``table_absent``/``count_mismatch``.
    """
    if tables is None:
        tables = _tables(con)
    meta = _project_meta(con) if "project_meta" in tables else {}
    return {
        "community": _layer_state(
            con,
            tables,
            meta,
            state_key=COMMUNITY_STATE_KEY,
            required_tables=("communities", "community_members"),
            count_markers=(
                ("derived_community_count", "communities"),
                ("derived_community_members", "community_members"),
            ),
        ),
        "process": _layer_state(
            con,
            tables,
            meta,
            state_key=PROCESS_STATE_KEY,
            required_tables=("processes", "process_steps"),
            count_markers=(
                ("derived_process_count", "processes"),
                ("derived_process_steps", "process_steps"),
            ),
        ),
    }


_COMMUNITY_FOR_FILE_SQL = (
    "SELECT c.id, c.label, c.cohesion, c.cohesion_reason, c.member_count "
    "FROM community_members cm "
    "JOIN communities c ON c.id = cm.community_id "
    "WHERE cm.member = ? AND cm.member_kind = 'file' "
    "ORDER BY c.member_count DESC, c.id LIMIT 1"
)


def _community_for_file(
    con: sqlite3.Connection, file_path: str
) -> CommunityMembership | None:
    try:
        row = con.execute(_COMMUNITY_FOR_FILE_SQL, (file_path,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    community_id, label, cohesion, reason, member_count = row
    return CommunityMembership(
        community_id=str(community_id),
        name=str(label),
        cohesion=None if cohesion is None else float(cohesion),
        cohesion_reason=str(reason or ""),
        member_count=int(member_count or 0),
        provenance=f"community_members(community_id={community_id},member={file_path})",
    )


def _processes_for_stable_ids(
    con: sqlite3.Connection,
    stable_ids: tuple[str, ...],
    tables: set[str],
    *,
    limit: int,
) -> tuple[ProcessParticipation, ...]:
    slots = ",".join("?" for _ in stable_ids)
    # The readable process name is its entry symbol's qualified name. The
    # resolution table can legitimately be absent on a core-only graph, so
    # the join is conditional and the fallback is the stored stable id.
    if "resolution_symbols" in tables:
        name_expr = "COALESCE(re.qualified_name, p.entry_stable_id)"
        name_join = (
            "LEFT JOIN resolution_symbols re ON re.stable_id = p.entry_stable_id "
        )
    else:
        name_expr = "p.entry_stable_id"
        name_join = ""
    sql = (
        "SELECT ps.process_id, ps.ordinal, p.kind, p.trust_floor, "
        "(SELECT COUNT(*) FROM process_steps s WHERE s.process_id = ps.process_id), "
        f"{name_expr} "
        "FROM process_steps ps "
        "JOIN processes p ON p.id = ps.process_id "
        f"{name_join}"
        f"WHERE ps.stable_id IN ({slots}) "
        "ORDER BY ps.ordinal, ps.process_id LIMIT ?"
    )
    try:
        rows = con.execute(sql, (*stable_ids, int(limit))).fetchall()
    except sqlite3.Error:
        return ()
    out: list[ProcessParticipation] = []
    for process_id, ordinal, kind, trust_floor, step_count, name in rows:
        out.append(
            ProcessParticipation(
                process_id=str(process_id),
                name=str(name or process_id),
                kind=str(kind or ""),
                step_index=int(ordinal or 0),
                step_count=int(step_count or 0),
                trust_floor=str(trust_floor or ""),
                provenance=(
                    f"process_steps(process_id={process_id},ordinal={ordinal})"
                ),
            )
        )
    return tuple(out)


def _node_identity(
    con: sqlite3.Connection, node_id: int, tables: set[str]
) -> tuple[str, str]:
    """``(file_path, effective_stable_id)`` for a node, or ``("", "")``.

    The stable id uses the producer's own expression -- ``nodes.stable_id``
    when stamped, else the ``resolution_symbols`` id joined on
    ``native_id = nodes.id`` -- because ``process_steps.stable_id`` was minted
    by exactly that rule.
    """
    if "nodes" not in tables:
        return "", ""
    try:
        if "resolution_symbols" in tables:
            row = con.execute(
                "SELECT n.file_path,"
                "COALESCE(NULLIF(n.stable_id,''),rs.stable_id,'') "
                "FROM nodes n "
                "LEFT JOIN resolution_symbols rs "
                "ON CAST(rs.native_id AS INTEGER) = n.id "
                "WHERE n.id = ?",
                (int(node_id),),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT n.file_path,COALESCE(NULLIF(n.stable_id,''),'') "
                "FROM nodes n WHERE n.id = ?",
                (int(node_id),),
            ).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return "", ""
    if row is None:
        return "", ""
    return str(row[0] or "").replace("\\", "/"), str(row[1] or "")


def symbol_derived_context(
    db: str | Path | sqlite3.Connection,
    *,
    node_id: int | None = None,
    stable_id: str | None = None,
    file_path: str | None = None,
    limit: int = MAX_PROCESSES_PER_SYMBOL,
) -> DerivedContext:
    """Community and process participation for one queried symbol.

    Any subset of ``node_id``/``stable_id``/``file_path`` identifies the
    symbol; a ``node_id`` additionally resolves the producer's effective
    stable id so a NULL-stamped source symbol still joins ``process_steps``.
    Each layer is independently gated on its recorded ``derived_*_state``:
    anything but ``ok`` leaves the typed fields empty and reports the state.
    """
    con, owned = _open(db)
    if con is None:
        return DerivedContext(
            None,
            (),
            {"community": STATE_GRAPH_ABSENT, "process": STATE_GRAPH_ABSENT},
        )
    try:
        tables = _tables(con)
        states = derived_layer_states(con, tables)
        resolved_file = str(file_path or "").replace("\\", "/")
        stable_ids: list[str] = []
        if stable_id:
            stable_ids.append(str(stable_id))
        if node_id is not None:
            node_file, effective = _node_identity(con, int(node_id), tables)
            if not resolved_file:
                resolved_file = node_file
            if effective and effective not in stable_ids:
                stable_ids.append(effective)
        community = None
        if states["community"] == DERIVED_STATE_OK and resolved_file:
            community = _community_for_file(con, resolved_file)
        processes: tuple[ProcessParticipation, ...] = ()
        if states["process"] == DERIVED_STATE_OK and stable_ids:
            processes = _processes_for_stable_ids(
                con, tuple(stable_ids), tables, limit=max(1, int(limit))
            )
        return DerivedContext(community, processes, states)
    finally:
        if owned:
            con.close()
