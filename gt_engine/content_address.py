"""Resolve a code symbol's stored content address against the workspace.

The producer stores an *address* for every code symbol -- ``file_hash`` (the
sha256 of the file's bytes at index time) plus the tree-sitter ``byte_start`` /
``byte_end`` of the declaration -- instead of a copy of the source text. The
copy is what goes stale silently: it keeps answering after the file changed and
nothing in the delivered context says so.

This module is the delivery-time half of that design. It re-reads the workspace
file, re-hashes it, and compares:

* the hashes agree -> the byte range IS the snippet, returned verbatim, with no
  re-parse and no line-number arithmetic;
* the hashes disagree -> a named ``stale_symbol`` state carrying BOTH hashes.
  No bytes are returned. Staleness is reported, never delivered;
* the symbol carries no address (a graph built before content addressing, or a
  node the parser could not locate) -> a named ``unaddressed`` state. It is not
  an error and it is not a pass: nothing is verified, so nothing is delivered.

Every state is named and every state is a downgrade or a no-op. Nothing here
raises a claim's trust: a matching hash proves the bytes are the indexed bytes,
which is what the caller already assumed, not new evidence.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

ADDRESS_SCHEMA = "gt.content_address.v1"

# Resolution states. Exactly one is set on every result, and only RESOLVED ever
# carries bytes.
RESOLVED = "resolved"
STALE_SYMBOL = "stale_symbol"
UNADDRESSED = "unaddressed"
UNKNOWN_SYMBOL = "unknown_symbol"
MISSING_FILE = "missing_file"
OUTSIDE_WORKSPACE = "outside_workspace"
UNREADABLE_FILE = "unreadable_file"
OUT_OF_RANGE = "address_out_of_range"

#: The only state whose ``text`` may be shown to a model.
DELIVERABLE_STATES = frozenset({RESOLVED})

# Enough of a digest to name a mismatch in one line without turning the
# orientation block into two hex paragraphs. The full digests stay on the
# receipt.
_SHORT_HASH = 12

_SELECT_ADDRESSED = (
    "SELECT id, label, name, qualified_name, file_path, file_hash,"
    " byte_start, byte_end FROM nodes"
    " WHERE file_path = ? AND name = ? ORDER BY id"
)

# A graph built before content addressing has no address columns at all. It is
# still queried -- so a symbol it holds reads back as `unaddressed` rather than
# as `unknown_symbol` -- with the three address values supplied as empty.
_SELECT_UNADDRESSED = (
    "SELECT id, label, name, qualified_name, file_path, '' AS file_hash,"
    " 0 AS byte_start, 0 AS byte_end FROM nodes"
    " WHERE file_path = ? AND name = ? ORDER BY id"
)

_HAS_COLUMN = "SELECT count(*) FROM pragma_table_info('nodes') WHERE name = ?"


@dataclass(frozen=True)
class SymbolAddress:
    """One symbol's stored address, exactly as the producer wrote it."""

    node_id: int
    label: str
    name: str
    qualified_name: str
    file_path: str
    file_hash: str
    byte_start: int
    byte_end: int

    @property
    def is_addressed(self) -> bool:
        """True only when all three parts are present.

        A partial address is treated as no address. Zero is a legal byte offset,
        so the producer stores NULL for an unaddressed symbol and the absent
        hash is what distinguishes the two.
        """
        return bool(self.file_hash) and self.byte_end > self.byte_start


@dataclass(frozen=True)
class ResolvedSymbol:
    """The outcome of checking one address against the working tree."""

    state: str
    file_path: str
    qualified_name: str
    stored_file_hash: str = ""
    actual_file_hash: str = ""
    byte_start: int = 0
    byte_end: int = 0
    text: str = ""

    @property
    def is_stale(self) -> bool:
        return self.state == STALE_SYMBOL

    @property
    def delivers_bytes(self) -> bool:
        return self.state in DELIVERABLE_STATES and bool(self.text)

    def marker(self) -> str:
        """A one-line, named marker for the delivered context.

        Named rather than free prose so a reader (and a test) can match on the
        state. The stale marker carries both hashes because "this is stale" is
        not actionable on its own -- the pair is what says the working tree
        moved and the graph did not.
        """
        where = f"{self.file_path}:{self.qualified_name or '-'}"
        if self.state == STALE_SYMBOL:
            return (
                f"[{STALE_SYMBOL}] {where}"
                f" stored={self.stored_file_hash[:_SHORT_HASH]}"
                f" actual={self.actual_file_hash[:_SHORT_HASH]}"
                " -- the file changed after indexing; re-read it"
            )
        if self.state == RESOLVED:
            return f"[{RESOLVED}] {where} bytes={self.byte_start}-{self.byte_end}"
        return f"[{self.state}] {where}"

    def to_receipt(self) -> dict[str, object]:
        """Receipt form. Never carries the bytes, and never promotes trust."""
        receipt = asdict(self)
        receipt.pop("text", None)
        receipt["schema"] = ADDRESS_SCHEMA
        receipt["promotes_trust"] = False
        return receipt


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a graph read-only, so a typo can never create or mutate a store."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"graph not found: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _graph_is_addressed(connection: sqlite3.Connection) -> bool:
    """True when this graph's schema can hold an address at all.

    A graph built before content addressing has no ``file_hash`` column. Asking
    for it would raise, and an exception is not a verdict -- the caller needs
    the named ``unaddressed`` state instead.
    """
    for column in ("file_hash", "byte_start", "byte_end"):
        row = connection.execute(_HAS_COLUMN, (column,)).fetchone()
        if not row or int(row[0]) != 1:
            return False
    return True


def graph_is_addressed(db_path: str | Path) -> bool:
    """Whether ``db_path`` carries the address columns."""
    connection = _connect(db_path)
    try:
        return _graph_is_addressed(connection)
    finally:
        connection.close()


def symbol_addresses(
    db_path: str | Path, file_path: str, name: str
) -> tuple[SymbolAddress, ...]:
    """Every stored address for ``name`` in ``file_path``, in node-id order.

    More than one is normal (overloads, a class and its constructor); they all
    share the file's hash, so the staleness verdict does not depend on which.
    """
    connection = _connect(db_path)
    try:
        select = (
            _SELECT_ADDRESSED
            if _graph_is_addressed(connection)
            else _SELECT_UNADDRESSED
        )
        rows = connection.execute(select, (str(file_path), str(name))).fetchall()
    finally:
        connection.close()
    return tuple(
        SymbolAddress(
            node_id=int(row["id"]),
            label=str(row["label"] or ""),
            name=str(row["name"] or ""),
            qualified_name=str(row["qualified_name"] or row["name"] or ""),
            file_path=str(row["file_path"] or "").replace("\\", "/"),
            file_hash=str(row["file_hash"] or ""),
            byte_start=int(row["byte_start"] or 0),
            byte_end=int(row["byte_end"] or 0),
        )
        for row in rows
    )


def _confined_abs(repo_root: str, rel: str) -> str | None:
    """Absolute path for a repo-relative target, confined INSIDE the repo.

    Same confinement the bridge already applies before reading a workspace file
    (``GTBridge._confined_abs``): an address is producer data, and producer data
    must not be able to name a path outside the workspace.
    """
    try:
        root = os.path.realpath(repo_root)
        target = os.path.realpath(os.path.join(root, rel))
        if os.path.commonpath([root, target]) != root:
            return None
        return target
    except (OSError, ValueError):
        return None


def resolve(
    address: SymbolAddress,
    repo_root: str,
    *,
    hash_cache: dict[str, str] | None = None,
) -> ResolvedSymbol:
    """Check one address against the working tree and name the outcome.

    ``hash_cache`` lets one render hash each file once; it is keyed by the
    absolute path and holds only digests.
    """
    base = {
        "file_path": address.file_path,
        "qualified_name": address.qualified_name,
        "stored_file_hash": address.file_hash,
        "byte_start": address.byte_start,
        "byte_end": address.byte_end,
    }
    if not address.is_addressed:
        return ResolvedSymbol(state=UNADDRESSED, **base)

    target = _confined_abs(repo_root, address.file_path)
    if target is None:
        # The stored path leaves the workspace. Producer data must not be able
        # to name a file outside it, and the refusal is named rather than
        # folded into "missing" so the two are not read as the same fault.
        return ResolvedSymbol(state=OUTSIDE_WORKSPACE, **base)
    if not os.path.isfile(target):
        return ResolvedSymbol(state=MISSING_FILE, **base)

    cached = hash_cache.get(target) if hash_cache is not None else None
    try:
        blob = Path(target).read_bytes()
    except OSError:
        return ResolvedSymbol(state=UNREADABLE_FILE, **base)
    actual = cached or hashlib.sha256(blob).hexdigest()
    if hash_cache is not None:
        hash_cache[target] = actual

    if actual != address.file_hash:
        return ResolvedSymbol(state=STALE_SYMBOL, actual_file_hash=actual, **base)
    if address.byte_end > len(blob):
        return ResolvedSymbol(
            state=OUT_OF_RANGE, actual_file_hash=actual, **base
        )
    snippet = blob[address.byte_start:address.byte_end].decode("utf-8", "replace")
    return ResolvedSymbol(state=RESOLVED, actual_file_hash=actual, text=snippet, **base)


def resolve_named_symbol(
    db_path: str | Path,
    repo_root: str,
    file_path: str,
    name: str,
    *,
    hash_cache: dict[str, str] | None = None,
) -> ResolvedSymbol:
    """Resolve the first stored address for ``file_path``:``name``.

    A symbol the graph does not carry is ``unknown_symbol`` -- distinct from a
    symbol it carries without an address, because the two call for different
    repairs (a stale graph slice versus an old producer).
    """
    normalized = str(file_path).replace("\\", "/")
    addresses = symbol_addresses(db_path, normalized, name)
    if not addresses:
        return ResolvedSymbol(
            state=UNKNOWN_SYMBOL, file_path=normalized, qualified_name=str(name)
        )
    return resolve(addresses[0], repo_root, hash_cache=hash_cache)
