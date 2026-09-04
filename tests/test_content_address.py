"""Delivery-time content addressing: verify, or name the reason you cannot.

The producer stores ``(file_hash, byte_start, byte_end)`` per code symbol rather
than a copy of the source. These tests pin the three outcomes delivery has to
distinguish, because collapsing any two of them reintroduces exactly the silent
rot the address exists to remove:

* the hash matches   -> the byte range resolves to the exact declaration bytes;
* the file changed   -> a named ``stale_symbol`` carrying BOTH hashes, no bytes;
* no address stored  -> a named ``unaddressed`` state, no crash, no bytes.

The graph fixture mirrors ``tests/test_symbol_contract.py``: a real sqlite file
under ``tmp_path`` (``gt_engine.content_address`` opens graphs read-only through
a ``file:...?mode=ro`` URI, which needs a real path), built from literal rows.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from gt_engine.content_address import (
    ADDRESS_SCHEMA,
    MISSING_FILE,
    OUT_OF_RANGE,
    OUTSIDE_WORKSPACE,
    RESOLVED,
    STALE_SYMBOL,
    UNADDRESSED,
    UNKNOWN_SYMBOL,
    graph_is_addressed,
    resolve_named_symbol,
    symbol_addresses,
)

# The shape the producer writes: `nodes` gained a `file_hash` column alongside
# the `byte_start`/`byte_end` it already had for callsites.
_ADDRESSED_NODES_DDL = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    file_hash TEXT,
    byte_start INTEGER,
    byte_end INTEGER
)
"""

# A graph built before content addressing: the columns do not exist at all.
_LEGACY_NODES_DDL = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER
)
"""

_SOURCE = (
    "class Repo:\n"
    "    def fetch(self, url):\n"
    "        return url\n"
    "\n"
    "def main():\n"
    "    return Repo()\n"
)

# Byte ranges as tree-sitter reports them for _SOURCE.
_FETCH_START = _SOURCE.index("    def fetch") + 4
_FETCH_END = _SOURCE.index("        return url") + len("        return url")
_FETCH_TEXT = _SOURCE[_FETCH_START:_FETCH_END]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_workspace(root: Path, source: str = _SOURCE) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "repo.py"
    target.write_text(source, encoding="utf-8", newline="")
    return target


def _build_graph(path: Path, *, file_hash: str, addressed: bool = True) -> Path:
    connection = sqlite3.connect(path)
    try:
        if addressed:
            connection.execute(_ADDRESSED_NODES_DDL)
            connection.execute(
                "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
                " start_line, end_line, file_hash, byte_start, byte_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    1, "Method", "fetch", "Repo.fetch", "repo.py", 2, 3,
                    file_hash, _FETCH_START, _FETCH_END,
                ),
            )
        else:
            connection.execute(_LEGACY_NODES_DDL)
            connection.execute(
                "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
                " start_line, end_line) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, "Method", "fetch", "Repo.fetch", "repo.py", 2, 3),
            )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    _write_workspace(tmp_path / "repo")
    return tmp_path / "repo"


@pytest.fixture
def graph(tmp_path: Path) -> Path:
    return _build_graph(tmp_path / "graph.db", file_hash=_digest(_SOURCE))


# ── the hash matches ────────────────────────────────────────────────────────

def test_a_matching_hash_resolves_the_exact_declaration_bytes(
    graph: Path, workspace: Path
):
    result = resolve_named_symbol(graph, str(workspace), "repo.py", "fetch")
    assert result.state == RESOLVED
    assert result.text == _FETCH_TEXT
    assert result.text.startswith("def fetch(self, url):")
    assert result.delivers_bytes is True
    assert result.stored_file_hash == result.actual_file_hash == _digest(_SOURCE)


def test_the_resolved_marker_names_the_byte_range(graph: Path, workspace: Path):
    marker = resolve_named_symbol(
        graph, str(workspace), "repo.py", "fetch"
    ).marker()
    assert marker.startswith(f"[{RESOLVED}] repo.py:Repo.fetch")
    assert f"bytes={_FETCH_START}-{_FETCH_END}" in marker


def test_a_receipt_carries_no_bytes_and_promotes_nothing(
    graph: Path, workspace: Path
):
    receipt = resolve_named_symbol(
        graph, str(workspace), "repo.py", "fetch"
    ).to_receipt()
    assert "text" not in receipt
    assert receipt["schema"] == ADDRESS_SCHEMA
    assert receipt["promotes_trust"] is False


# ── the file changed ────────────────────────────────────────────────────────

def test_a_changed_file_is_stale_and_names_both_hashes(
    graph: Path, workspace: Path
):
    edited = _SOURCE.replace("return url", "return url.strip()")
    _write_workspace(workspace, edited)

    result = resolve_named_symbol(graph, str(workspace), "repo.py", "fetch")
    assert result.state == STALE_SYMBOL
    assert result.is_stale is True
    assert result.stored_file_hash == _digest(_SOURCE)
    assert result.actual_file_hash == _digest(edited)
    assert result.stored_file_hash != result.actual_file_hash


def test_a_stale_symbol_delivers_no_bytes(graph: Path, workspace: Path):
    """The whole point: staleness is reported, never delivered."""
    _write_workspace(workspace, _SOURCE.replace("return url", "return None"))

    result = resolve_named_symbol(graph, str(workspace), "repo.py", "fetch")
    assert result.text == ""
    assert result.delivers_bytes is False


def test_the_stale_marker_carries_both_hashes(graph: Path, workspace: Path):
    edited = _SOURCE.replace("class Repo:", "class Repo(Base):")
    _write_workspace(workspace, edited)

    marker = resolve_named_symbol(
        graph, str(workspace), "repo.py", "fetch"
    ).marker()
    assert marker.startswith(f"[{STALE_SYMBOL}] repo.py:Repo.fetch")
    assert f"stored={_digest(_SOURCE)[:12]}" in marker
    assert f"actual={_digest(edited)[:12]}" in marker


def test_a_whitespace_only_edit_is_still_stale(graph: Path, workspace: Path):
    """Byte identity, not semantic identity. A shifted range is a wrong range."""
    _write_workspace(workspace, _SOURCE.replace("def main():", "\ndef main():"))

    result = resolve_named_symbol(graph, str(workspace), "repo.py", "fetch")
    assert result.state == STALE_SYMBOL


# ── no address stored ───────────────────────────────────────────────────────

def test_a_graph_without_address_columns_reads_as_unaddressed(
    tmp_path: Path, workspace: Path
):
    legacy = _build_graph(
        tmp_path / "legacy.db", file_hash="", addressed=False
    )
    assert graph_is_addressed(legacy) is False

    result = resolve_named_symbol(legacy, str(workspace), "repo.py", "fetch")
    assert result.state == UNADDRESSED
    assert result.text == ""
    assert result.delivers_bytes is False
    assert result.marker() == f"[{UNADDRESSED}] repo.py:Repo.fetch"


def test_a_symbol_with_null_address_columns_reads_as_unaddressed(
    tmp_path: Path, workspace: Path
):
    """The producer writes NULL, not 0, precisely so this stays distinguishable."""
    path = tmp_path / "partial.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(_ADDRESSED_NODES_DDL)
        connection.execute(
            "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
            " start_line, end_line, file_hash, byte_start, byte_end)"
            " VALUES (1, 'Method', 'fetch', 'Repo.fetch', 'repo.py', 2, 3,"
            " NULL, NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    result = resolve_named_symbol(path, str(workspace), "repo.py", "fetch")
    assert result.state == UNADDRESSED


def test_an_unknown_symbol_is_not_confused_with_an_unaddressed_one(
    graph: Path, workspace: Path
):
    result = resolve_named_symbol(graph, str(workspace), "repo.py", "absent")
    assert result.state == UNKNOWN_SYMBOL
    assert result.text == ""


# ── the file is gone, or the address does not fit it ────────────────────────

def test_a_deleted_file_is_named_not_crashed(graph: Path, workspace: Path):
    (workspace / "repo.py").unlink()
    result = resolve_named_symbol(graph, str(workspace), "repo.py", "fetch")
    assert result.state == MISSING_FILE
    assert result.text == ""


def test_a_path_escaping_the_workspace_is_refused(
    tmp_path: Path, workspace: Path
):
    outside = tmp_path / "outside.py"
    outside.write_text(_SOURCE, encoding="utf-8", newline="")
    escaping = _build_graph(tmp_path / "escape.db", file_hash=_digest(_SOURCE))
    connection = sqlite3.connect(escaping)
    try:
        connection.execute("UPDATE nodes SET file_path = '../outside.py'")
        connection.commit()
    finally:
        connection.close()

    result = resolve_named_symbol(
        escaping, str(workspace), "../outside.py", "fetch"
    )
    assert result.state == OUTSIDE_WORKSPACE
    assert result.text == ""


def test_a_range_past_the_end_of_a_matching_file_is_named(
    tmp_path: Path, workspace: Path
):
    """Only reachable if the producer wrote a bad range: still never a slice."""
    path = tmp_path / "overrun.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(_ADDRESSED_NODES_DDL)
        connection.execute(
            "INSERT INTO nodes (id, label, name, qualified_name, file_path,"
            " start_line, end_line, file_hash, byte_start, byte_end)"
            " VALUES (1, 'Method', 'fetch', 'Repo.fetch', 'repo.py', 2, 3,"
            " ?, 0, 100000)",
            (_digest(_SOURCE),),
        )
        connection.commit()
    finally:
        connection.close()

    result = resolve_named_symbol(path, str(workspace), "repo.py", "fetch")
    assert result.state == OUT_OF_RANGE
    assert result.text == ""


# ── lookup surface ──────────────────────────────────────────────────────────

def test_addresses_are_returned_in_node_id_order(graph: Path):
    addresses = symbol_addresses(graph, "repo.py", "fetch")
    assert [a.node_id for a in addresses] == [1]
    assert addresses[0].is_addressed is True
    assert addresses[0].qualified_name == "Repo.fetch"


def test_a_missing_graph_raises_rather_than_reporting_a_clean_result(
    tmp_path: Path, workspace: Path
):
    with pytest.raises(FileNotFoundError):
        resolve_named_symbol(
            tmp_path / "absent.db", str(workspace), "repo.py", "fetch"
        )


# ── the delivery seam ───────────────────────────────────────────────────────

class _Evidence:
    """The shape `GTBridge._render_task_start_orientation` consumes."""

    def __init__(self, file_path: str, symbol: str) -> None:
        self.file_path = file_path
        self.symbol = symbol
        self.claim = "fetch returns the url unchanged"
        self.obligation_ids = ("OB-1",)
        self.intended_action = "inspect the ranked definition"
        self.rank = 1


def _bridge(graph: Path, workspace: Path):
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(workspace), graph_db=str(graph))
    bridge._graph_evidence = (_Evidence("repo.py", "fetch"),)
    return bridge


def test_delivery_annotates_a_verified_symbol_with_its_state(
    graph: Path, workspace: Path
):
    rendered = _bridge(graph, workspace)._render_task_start_orientation()
    assert f"address={RESOLVED}" in rendered
    assert "fetch returns the url unchanged" in rendered


def test_delivery_downgrades_a_stale_symbol_instead_of_shipping_its_claim(
    graph: Path, workspace: Path
):
    edited = _SOURCE.replace("return url", "return url.strip()")
    _write_workspace(workspace, edited)

    rendered = _bridge(graph, workspace)._render_task_start_orientation()
    assert f"[{STALE_SYMBOL}]" in rendered
    assert f"stored={_digest(_SOURCE)[:12]}" in rendered
    assert f"actual={_digest(edited)[:12]}" in rendered
    assert "fetch returns the url unchanged" not in rendered
    assert "action=re-read the file" in rendered


def test_delivery_survives_an_old_graph_without_addresses(
    tmp_path: Path, workspace: Path
):
    legacy = _build_graph(tmp_path / "legacy.db", file_hash="", addressed=False)
    rendered = _bridge(legacy, workspace)._render_task_start_orientation()
    assert f"address={UNADDRESSED}" in rendered
    assert "fetch returns the url unchanged" in rendered
