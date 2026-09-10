"""What happens to a fact the producer cannot re-derive, when the graph is amended.

An LSP promotion writes edges into the published graph that the producer's own
resolution never emits -- that is the whole point of promotion, and
`certify_lsp_candidate` exists to admit exactly those. The published enrichment
then becomes `engine_state.graph_path` (graph_coordinator publishes the certified
candidate), so it is the PARENT the next edit amends from.

That puts two desirable properties in direct conflict, and only one can hold:

  * RETAIN the promoted edges -> LSP bindings survive an edit, and
    amend-versus-rebuild digest parity is broken BY CONSTRUCTION, because no
    rebuild can produce them. `tests/test_batch_amend_parity.py` compares the two
    for EQUALITY across eight mutations and would fail on every one.
  * DROP them -> parity holds, and every promotion is lost on the first edit
    after it.

MEASURED, on the certified producer: it drops them. Both edges injected below
disappear from the amended graph, including the one whose endpoints are in a file
the edit never touched. The amend retains parser NODES (the study measures
`parser_nodes_retained` in the thousands) but re-resolves every EDGE -- both arms
run `resolver_passes: 1` -- so a fact that resolution cannot reproduce cannot
survive, wherever it lives.

So "preserve LSP bindings" is NOT what the system does, and this file exists so
that is written down as a measurement rather than assumed either way. It is also
the reason the parity suite can demand equality at all: the amend carries forward
nothing a rebuild would not derive.

This is a characterisation test. If the producer ever starts retaining derived
facts across an amend, this fails FIRST and points at the parity suite, which
would otherwise start failing on all eight mutations with no explanation.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from gt_engine import indexer

PARENT_SOURCE = {
    "alpha.py": "def a_one():\n    return 1\n\n\ndef a_two():\n    return a_one()\n",
    "beta.py": "def b_one():\n    return 2\n\n\ndef b_two():\n    return b_one()\n",
}
# One edit, confined to alpha.py. beta.py is byte-identical afterwards.
EDITED_SOURCE = dict(PARENT_SOURCE,
                     **{"alpha.py": "def a_one():\n    return 11\n\n\n"
                                    "def a_two():\n    return a_one()\n"})

PROMOTION_MARKER = "test_promoted_binding"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")


def _build(root: Path, state: Path, name: str, parent: Path | None = None) -> Path:
    logs = state / name
    logs.mkdir(parents=True)
    output = logs / "graph.db"
    factory = None if parent is None else (
        lambda binary, source, target: indexer._index_command(binary, source, target)
        + ["-amend-parent", str(parent)]
    )
    result = indexer._run_index_bounded(str(root), output, logs, command_factory=factory)
    assert result.success, (name, result.error_code, result.stderr_tail)
    return output


def _node_id(database: Path, name: str, filename: str) -> int:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        row = db.execute(
            "SELECT id FROM nodes WHERE name=? AND file_path LIKE ?",
            (name, f"%{filename}"),
        ).fetchone()
    assert row is not None, f"{name} in {filename} is not in the graph"
    return int(row[0])


def _inject_promotion(database: Path, source: int, target: int, marker: str) -> None:
    """An edge no re-derivation produces, standing in for a promoted binding.

    `a_one` does not call `a_two`; the edge is deliberately not a fact about the
    source, so its survival is a statement about RETENTION and nothing else.
    """
    with closing(sqlite3.connect(database)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(edges)")}
        fields: dict[str, object] = {
            "source_id": source, "target_id": target, "type": "CALLS",
            "resolution_method": marker,
        }
        for optional, value in (("confidence", 0.99), ("evidence_type", marker),
                                ("derivation_kind", "lsp"), ("trust_tier", "CERTIFIED")):
            if optional in columns:
                fields[optional] = value
        db.execute(
            f"INSERT INTO edges ({','.join(fields)}) "
            f"VALUES ({','.join('?' for _ in fields)})",
            tuple(fields.values()),
        )
        db.commit()


def _count(database: Path, marker: str) -> int:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        return int(db.execute(
            "SELECT count(*) FROM edges WHERE resolution_method=?", (marker,)
        ).fetchone()[0])


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_an_amend_drops_a_promoted_edge_even_in_an_untouched_file(tmp_path):
    """The measurement the LSP-preservation question turns on.

    Two promoted edges are injected: one between definitions in the file the
    edit changes, one between definitions in a file it does not touch. If
    retention were path-scoped, the second would survive. It does not.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    _write(repo, PARENT_SOURCE)
    parent = _build(repo, state, "parent")

    _inject_promotion(parent, _node_id(parent, "a_one", "alpha.py"),
                      _node_id(parent, "a_two", "alpha.py"), PROMOTION_MARKER)
    _inject_promotion(parent, _node_id(parent, "b_one", "beta.py"),
                      _node_id(parent, "b_two", "beta.py"), PROMOTION_MARKER)
    assert _count(parent, PROMOTION_MARKER) == 2, "the fixture did not inject"

    _write(repo, EDITED_SOURCE)
    amended = _build(repo, state, "amended", parent=parent)

    assert _count(amended, PROMOTION_MARKER) == 0, (
        "the amend retained a fact no rebuild can produce; amend-versus-rebuild "
        "parity in tests/test_batch_amend_parity.py cannot hold if this is true"
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_the_parent_graph_still_carries_the_promotion_afterwards(tmp_path):
    """The amend must not reach back and rewrite what it built from.

    A published graph is immutable and its manifest pins its bytes, so a parent
    that lost rows during an amend would invalidate the certificate of every
    reader already holding it. This is the half that makes the drop above a
    RE-DERIVATION result rather than a mutation of the parent.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    _write(repo, PARENT_SOURCE)
    parent = _build(repo, state, "parent")
    _inject_promotion(parent, _node_id(parent, "b_one", "beta.py"),
                      _node_id(parent, "b_two", "beta.py"), PROMOTION_MARKER)
    before = parent.read_bytes()

    _write(repo, EDITED_SOURCE)
    _build(repo, state, "amended", parent=parent)

    assert _count(parent, PROMOTION_MARKER) == 1
    assert parent.read_bytes() == before, "the amend mutated its own parent"


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_a_rebuild_does_not_produce_the_promoted_edge_either(tmp_path):
    """Guard the premise: the injected edge must be unreproducible.

    If resolution happened to emit this edge on its own, both assertions above
    would pass for the wrong reason and the file would be measuring nothing.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    _write(repo, EDITED_SOURCE)
    rebuilt = _build(repo, state, "rebuilt")
    assert _count(rebuilt, PROMOTION_MARKER) == 0
