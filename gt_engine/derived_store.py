"""Versioned GT-owned SQLite sidecar for conservative resolution facts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .resolution_provenance import (
    SCHEMA_VERSION,
    CallCandidate,
    CallsiteRecord,
    SymbolRecord,
)


class StoreIncompatible(RuntimeError):
    """The sidecar is corrupt, stale, or bound to a different authority."""


@dataclass(frozen=True)
class DerivedStoreBinding:
    source_revision: str
    repository_revision: str
    graph_revision: str
    graph_sha256: str
    producer_contract: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "schema_version": str(SCHEMA_VERSION),
            "source_revision": self.source_revision,
            "repository_revision": self.repository_revision,
            "graph_revision": self.graph_revision,
            "graph_sha256": self.graph_sha256,
            "producer_contract": self.producer_contract,
            "complete": "1",
        }


@dataclass(frozen=True)
class DerivedStoreReceipt:
    path: str
    sha256: str
    published: bool
    symbol_count: int
    callsite_count: int
    candidate_count: int


@dataclass(frozen=True)
class DerivedStoreSnapshot:
    binding: DerivedStoreBinding
    symbols: tuple[SymbolRecord, ...]
    callsites: tuple[CallsiteRecord, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS derived_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    stable_id TEXT PRIMARY KEY,
    symbol_ordinal INTEGER NOT NULL UNIQUE,
    native_id TEXT NOT NULL,
    native_kind TEXT NOT NULL,
    normalized_kind TEXT NOT NULL,
    language TEXT NOT NULL,
    path TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    export_status TEXT NOT NULL,
    CHECK(start_line >= 0),
    CHECK(end_line >= start_line)
);

CREATE TABLE IF NOT EXISTS callsites (
    callsite_id TEXT PRIMARY KEY,
    callsite_ordinal INTEGER NOT NULL UNIQUE,
    repository_revision TEXT NOT NULL,
    source_stable_id TEXT NOT NULL REFERENCES symbols(stable_id),
    source_native_id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    callee TEXT NOT NULL,
    language TEXT NOT NULL,
    dispatch_state TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    selected_target_stable_id TEXT,
    selected_target_native_id TEXT,
    mechanism TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    legacy_reported_candidate_count INTEGER,
    legacy_selected_native_target_id TEXT NOT NULL DEFAULT '',
    CHECK(start_line >= 0),
    CHECK(end_line >= start_line),
    CHECK(candidate_count >= 0)
);

CREATE TABLE IF NOT EXISTS call_candidates (
    callsite_id TEXT NOT NULL REFERENCES callsites(callsite_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    target_stable_id TEXT NOT NULL REFERENCES symbols(stable_id),
    target_native_id TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    declared_scope TEXT NOT NULL,
    receiver_type TEXT NOT NULL,
    receiver_origin TEXT NOT NULL,
    receiver_shape TEXT NOT NULL,
    receiver_chain TEXT NOT NULL,
    import_chain TEXT NOT NULL,
    dynamic_dispatch INTEGER NOT NULL,
    export_status TEXT NOT NULL,
    parser_complete INTEGER,
    verification_status TEXT NOT NULL,
    is_selected INTEGER NOT NULL,
    PRIMARY KEY(callsite_id, ordinal),
    UNIQUE(callsite_id, target_stable_id),
    CHECK(ordinal >= 0),
    CHECK(dynamic_dispatch IN (0, 1)),
    CHECK(parser_complete IS NULL OR parser_complete IN (0, 1)),
    CHECK(is_selected IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_symbols_native ON symbols(native_id);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(normalized_kind);
CREATE INDEX IF NOT EXISTS idx_callsites_source ON callsites(source_stable_id);
CREATE INDEX IF NOT EXISTS idx_callsites_path ON callsites(path);
CREATE INDEX IF NOT EXISTS idx_candidates_target ON call_candidates(target_stable_id);
"""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(connection: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _table_columns(connection, table):
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


def migrate_derived_store(path: str | Path) -> Path:
    """Create or additively migrate the sidecar schema without deleting rows."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as connection:
        with connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(_SCHEMA)
            # These additions cover pre-v1 development sidecars. SQLite cannot add
            # table CHECK/FK clauses through ALTER, so reads still run full semantic
            # validation and reject incompatible content.
            additions = {
                "symbols": {
                    "symbol_ordinal": "INTEGER NOT NULL DEFAULT 0",
                    "native_id": "TEXT NOT NULL DEFAULT ''",
                    "native_kind": "TEXT NOT NULL DEFAULT ''",
                    "normalized_kind": "TEXT NOT NULL DEFAULT 'unknown'",
                    "language": "TEXT NOT NULL DEFAULT ''",
                    "path": "TEXT NOT NULL DEFAULT ''",
                    "qualified_name": "TEXT NOT NULL DEFAULT ''",
                    "start_line": "INTEGER NOT NULL DEFAULT 0",
                    "end_line": "INTEGER NOT NULL DEFAULT 0",
                    "export_status": "TEXT NOT NULL DEFAULT 'unknown'",
                },
                "callsites": {
                    "callsite_ordinal": "INTEGER NOT NULL DEFAULT 0",
                    "repository_revision": "TEXT NOT NULL DEFAULT ''",
                    "source_stable_id": "TEXT NOT NULL DEFAULT ''",
                    "source_native_id": "TEXT NOT NULL DEFAULT ''",
                    "path": "TEXT NOT NULL DEFAULT ''",
                    "start_line": "INTEGER NOT NULL DEFAULT 0",
                    "end_line": "INTEGER NOT NULL DEFAULT 0",
                    "callee": "TEXT NOT NULL DEFAULT ''",
                    "language": "TEXT NOT NULL DEFAULT ''",
                    "dispatch_state": "TEXT NOT NULL DEFAULT 'unknown_legacy'",
                    "candidate_count": "INTEGER NOT NULL DEFAULT 0",
                    "selected_target_stable_id": "TEXT",
                    "selected_target_native_id": "TEXT",
                    "mechanism": "TEXT NOT NULL DEFAULT 'unknown_legacy'",
                    "verification_status": "TEXT NOT NULL DEFAULT 'unknown'",
                    "legacy_reported_candidate_count": "INTEGER",
                    "legacy_selected_native_target_id": "TEXT NOT NULL DEFAULT ''",
                },
            }
            for table, columns in additions.items():
                for name, definition in columns.items():
                    _ensure_column(connection, table, name, definition)
            connection.execute(
                "INSERT OR REPLACE INTO derived_metadata(key,value) VALUES(?,?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
    return target


def _validate_records(
    binding: DerivedStoreBinding,
    symbols: tuple[SymbolRecord, ...],
    callsites: tuple[CallsiteRecord, ...],
) -> None:
    if not all(binding.to_metadata().values()):
        raise ValueError("derived-store binding fields must be non-empty")
    if len(binding.graph_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in binding.graph_sha256.lower()
    ):
        raise ValueError("graph_sha256 must be a hexadecimal sha256")
    symbol_ids = [item.stable_id for item in symbols]
    if len(set(symbol_ids)) != len(symbol_ids):
        raise ValueError("symbol stable IDs must be unique")
    symbol_set = set(symbol_ids)
    callsite_ids: set[str] = set()
    for callsite in callsites:
        callsite.validate()
        if callsite.callsite_id in callsite_ids:
            raise ValueError("callsite IDs must be unique")
        callsite_ids.add(callsite.callsite_id)
        if callsite.repository_revision != binding.repository_revision:
            raise ValueError("callsite repository_revision differs from binding")
        if callsite.source_stable_id not in symbol_set:
            raise ValueError("callsite source must be a stored symbol")
        missing = {
            item.target_stable_id
            for item in callsite.candidates
            if item.target_stable_id not in symbol_set
        }
        if missing:
            raise ValueError("candidate target must be a stored symbol")


def _write_database(
    path: Path,
    binding: DerivedStoreBinding,
    symbols: tuple[SymbolRecord, ...],
    callsites: tuple[CallsiteRecord, ...],
) -> None:
    migrate_derived_store(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            connection.execute("DELETE FROM call_candidates")
            connection.execute("DELETE FROM callsites")
            connection.execute("DELETE FROM symbols")
            for key, value in binding.to_metadata().items():
                connection.execute(
                    "INSERT OR REPLACE INTO derived_metadata(key,value) VALUES(?,?)",
                    (key, value),
                )
            for ordinal, symbol in enumerate(symbols):
                row = symbol.to_row()
                connection.execute(
                    """INSERT INTO symbols(
                    stable_id,symbol_ordinal,native_id,native_kind,normalized_kind,
                    language,path,qualified_name,start_line,end_line,export_status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["stable_id"],
                        ordinal,
                        row["native_id"],
                        row["native_kind"],
                        row["normalized_kind"],
                        row["language"],
                        row["path"],
                        row["qualified_name"],
                        row["start_line"],
                        row["end_line"],
                        row["export_status"],
                    ),
                )
            for ordinal, callsite in enumerate(callsites):
                row = callsite.to_row()
                connection.execute(
                    """INSERT INTO callsites(
                    callsite_id,callsite_ordinal,repository_revision,source_stable_id,
                    source_native_id,path,start_line,end_line,callee,language,
                    dispatch_state,candidate_count,selected_target_stable_id,
                    selected_target_native_id,mechanism,verification_status,
                    legacy_reported_candidate_count,legacy_selected_native_target_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["callsite_id"],
                        ordinal,
                        row["repository_revision"],
                        row["source_stable_id"],
                        row["source_native_id"],
                        row["path"],
                        row["start_line"],
                        row["end_line"],
                        row["callee"],
                        row["language"],
                        row["dispatch_state"],
                        row["candidate_count"],
                        row["selected_target_stable_id"],
                        row["selected_target_native_id"],
                        row["mechanism"],
                        row["verification_status"],
                        row["legacy_reported_candidate_count"],
                        row["legacy_selected_native_target_id"],
                    ),
                )
                for candidate in callsite.candidates:
                    item = candidate.to_row()
                    connection.execute(
                        """INSERT INTO call_candidates(
                        callsite_id,ordinal,target_stable_id,target_native_id,mechanism,
                        declared_scope,receiver_type,receiver_origin,receiver_shape,
                        receiver_chain,import_chain,dynamic_dispatch,export_status,
                        parser_complete,verification_status,is_selected
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            callsite.callsite_id,
                            item["ordinal"],
                            item["target_stable_id"],
                            item["target_native_id"],
                            item["mechanism"],
                            item["declared_scope"],
                            item["receiver_type"],
                            item["receiver_origin"],
                            item["receiver_shape"],
                            json.dumps(item["receiver_chain"], separators=(",", ":")),
                            json.dumps(item["import_chain"], separators=(",", ":")),
                            int(item["dynamic_dispatch"]),
                            item["export_status"],
                            None
                            if item["parser_complete"] is None
                            else int(item["parser_complete"]),
                            item["verification_status"],
                            int(item["selected"]),
                        ),
                    )


def _validate_database(path: Path) -> None:
    try:
        with closing(
            sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        ) as connection:
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]).lower() != "ok":
                raise StoreIncompatible("SQLite quick_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise StoreIncompatible("SQLite foreign_key_check failed")
            mismatch = connection.execute(
                """SELECT c.callsite_id FROM callsites c
                LEFT JOIN (
                    SELECT callsite_id, COUNT(*) AS n FROM call_candidates GROUP BY callsite_id
                ) x ON x.callsite_id=c.callsite_id
                WHERE c.candidate_count != COALESCE(x.n,0) LIMIT 1"""
            ).fetchone()
            if mismatch:
                raise StoreIncompatible("candidate_count conservation failed")
            bad_selection = connection.execute(
                """SELECT c.callsite_id FROM callsites c
                WHERE c.selected_target_stable_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM call_candidates x
                    WHERE x.callsite_id=c.callsite_id
                    AND x.target_stable_id=c.selected_target_stable_id
                    AND x.is_selected=1
                ) LIMIT 1"""
            ).fetchone()
            if bad_selection:
                raise StoreIncompatible("selected target is not a retained selected candidate")
            if connection.execute(
                """SELECT callsite_id FROM call_candidates
                GROUP BY callsite_id HAVING SUM(is_selected) > 1 LIMIT 1"""
            ).fetchone():
                raise StoreIncompatible("multiple selected candidates")
    except sqlite3.Error as exc:
        raise StoreIncompatible(f"derived store unreadable: {exc}") from exc


def build_derived_store(
    path: str | Path,
    binding: DerivedStoreBinding,
    symbols: Iterable[SymbolRecord],
    callsites: Iterable[CallsiteRecord],
) -> DerivedStoreReceipt:
    """Build, validate, and atomically publish one complete sidecar file."""
    target = Path(path)
    symbol_rows = tuple(symbols)
    callsite_rows = tuple(callsites)
    _validate_records(binding, symbol_rows, callsite_rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    temporary.unlink(missing_ok=True)
    try:
        _write_database(temporary, binding, symbol_rows, callsite_rows)
        _validate_database(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    payload = target.read_bytes()
    return DerivedStoreReceipt(
        path=str(target),
        sha256=hashlib.sha256(payload).hexdigest(),
        published=True,
        symbol_count=len(symbol_rows),
        callsite_count=len(callsite_rows),
        candidate_count=sum(item.candidate_count for item in callsite_rows),
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(key): str(value)
            for key, value in connection.execute("SELECT key,value FROM derived_metadata")
        }
    except sqlite3.Error as exc:
        raise StoreIncompatible(f"derived metadata unreadable: {exc}") from exc


def _binding_from_metadata(metadata: Mapping[str, str]) -> DerivedStoreBinding:
    if metadata.get("schema_version") != str(SCHEMA_VERSION):
        raise StoreIncompatible("schema_version mismatch")
    if metadata.get("complete") != "1":
        raise StoreIncompatible("derived store is not complete")
    try:
        return DerivedStoreBinding(
            source_revision=metadata["source_revision"],
            repository_revision=metadata["repository_revision"],
            graph_revision=metadata["graph_revision"],
            graph_sha256=metadata["graph_sha256"],
            producer_contract=metadata["producer_contract"],
        )
    except KeyError as exc:
        raise StoreIncompatible(f"missing binding field: {exc.args[0]}") from exc


def load_derived_store(path: str | Path, *, expected: DerivedStoreBinding) -> DerivedStoreSnapshot:
    target = Path(path)
    if not target.is_file():
        raise StoreIncompatible("derived store is missing")
    _validate_database(target)
    with closing(
        sqlite3.connect(f"file:{target.resolve().as_posix()}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        actual = _binding_from_metadata(_metadata(connection))
        for field in actual.__dataclass_fields__:
            if getattr(actual, field) != getattr(expected, field):
                raise StoreIncompatible(f"{field} mismatch")
        symbols = tuple(
            SymbolRecord.from_row(dict(row))
            for row in connection.execute("SELECT * FROM symbols ORDER BY symbol_ordinal")
        )
        callsites: list[CallsiteRecord] = []
        for row in connection.execute("SELECT * FROM callsites ORDER BY callsite_ordinal"):
            candidates = tuple(
                CallCandidate.from_row(dict(candidate))
                for candidate in connection.execute(
                    """SELECT target_stable_id,target_native_id,ordinal,mechanism,
                    declared_scope,receiver_type,receiver_origin,receiver_shape,
                    receiver_chain,import_chain,dynamic_dispatch,export_status,
                    parser_complete,verification_status,is_selected AS selected
                    FROM call_candidates WHERE callsite_id=? ORDER BY ordinal""",
                    (row["callsite_id"],),
                )
            )
            try:
                callsites.append(CallsiteRecord.from_row(dict(row), candidates=candidates))
            except ValueError as exc:
                raise StoreIncompatible(str(exc)) from exc
    return DerivedStoreSnapshot(actual, symbols, tuple(callsites))


__all__ = [
    "DerivedStoreBinding",
    "DerivedStoreReceipt",
    "DerivedStoreSnapshot",
    "StoreIncompatible",
    "build_derived_store",
    "load_derived_store",
    "migrate_derived_store",
]
