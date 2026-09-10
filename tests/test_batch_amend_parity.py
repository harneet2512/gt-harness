"""Amend versus rebuild, on the mutations that actually change resolution.

The batch amendment exists so an edit does not pay for a whole re-index: on
arktype a full build was ~115s against an edit interval near 50s, which never
converges. It is only worth having if the graph it produces means the same
thing as the graph a full build would have produced. "Same" is checked here on
the facts consumers read, compared as CONTENT rather than as row ids, because
row ids are an implementation detail the amend is explicitly allowed to
preserve and a rebuild is free to renumber.

The mutations are chosen for what they do to the resolver, not for what they do
to the text. Adding a definition creates a new resolution target. Deleting one
removes a target other files were resolved against. Renaming does both at once
and is the case a naive amend gets wrong in the most expensive way -- the old
name keeps resolving. Changing an import moves the resolution, inheritance
moves it up a class chain, and a second same-named definition turns a unique
resolution into an ambiguous one.

The parent must survive all of it byte for byte: the amend copies, it never
edits in place, and a consumer holding the parent open is entitled to the graph
it opened.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from gt_engine import indexer

# The tables a consumer reads. Ordered by content and never by row id: the
# amend retains parent ids on purpose, and a from-scratch build has no reason
# to choose the same numbers.
FACTS = {
    "nodes": (
        "SELECT label,name,qualified_name,file_path,start_line,signature,is_test,language"
        " FROM nodes"
    ),
    "edges": (
        "SELECT e.type, s.qualified_name, s.file_path, t.qualified_name, t.file_path"
        " FROM edges e LEFT JOIN nodes s ON s.id=e.source_id"
        " LEFT JOIN nodes t ON t.id=e.target_id"
    ),
    "properties": (
        "SELECT p.kind,p.value,n.qualified_name,n.file_path"
        " FROM properties p JOIN nodes n ON n.id=p.node_id"
    ),
    "assertions": (
        "SELECT a.expression, tn.qualified_name, gn.qualified_name"
        " FROM assertions a LEFT JOIN nodes tn ON tn.id=a.test_node_id"
        " LEFT JOIN nodes gn ON gn.id=a.target_node_id"
    ),
    "resolution_symbols": (
        "SELECT path,qualified_name,normalized_kind,native_kind,language,export_status"
        " FROM resolution_symbols"
    ),
    "resolution_callsites": (
        "SELECT source_file,callee,dispatch_state,candidate_count,mechanism,"
        "verification_status FROM resolution_callsites"
    ),
    "resolution_candidates": (
        "SELECT c.ordinal, s.qualified_name, s.path FROM resolution_candidates c"
        " LEFT JOIN resolution_symbols s ON s.stable_id=c.target_stable_id"
    ),
    "closure": "SELECT COUNT(*) FROM closure",
    "cochanges": "SELECT COUNT(*) FROM cochanges",
    "file_hashes": "SELECT file_path,content_hash,language FROM file_hashes",
}

BASE = {
    "pkg/__init__.py": "",
    "pkg/base.py": "class Base:\n    def save(self):\n        return 1\n",
    "pkg/child.py": (
        "from pkg.base import Base\n\n\n"
        "class Child(Base):\n"
        "    def run(self):\n        return self.save()\n"
    ),
    "pkg/other.py": "def helper():\n    return 0\n\n\ndef spare():\n    return 3\n",
    "app.py": (
        "from pkg.child import Child\n"
        "from pkg.other import helper\n\n\n"
        "def main():\n    return Child().run() + helper()\n"
    ),
    "test_app.py": (
        "from app import main\n\n\n"
        "def test_main():\n    assert main() == 1\n"
    ),
}

# (name, {path: new content or None to delete}, paths handed to the amend)
CASES = [
    pytest.param(
        "add_definition",
        {"pkg/other.py": ("def helper():\n    return 0\n\n\ndef spare():\n    return 3\n"
                          "\n\ndef extra():\n    return 2\n")},
        ("pkg/other.py",),
        id="add",
    ),
    pytest.param(
        "delete_definition",
        {"pkg/other.py": "def helper():\n    return 0\n"},
        ("pkg/other.py",),
        id="delete_body",
    ),
    pytest.param(
        "rename_definition",
        {
            "pkg/other.py": "def assistant():\n    return 0\n\n\ndef spare():\n    return 3\n",
            "app.py": (
                "from pkg.child import Child\n"
                "from pkg.other import assistant\n\n\n"
                "def main():\n    return Child().run() + assistant()\n"
            ),
        },
        ("pkg/other.py", "app.py"),
        id="rename",
    ),
    pytest.param(
        "change_import",
        {
            "app.py": (
                "from pkg.child import Child\n"
                "from pkg.base import Base\n\n\n"
                "def main():\n    return Child().run() + Base().save()\n"
            ),
        },
        ("app.py",),
        id="import",
    ),
    pytest.param(
        "move_inheritance",
        {
            "pkg/mid.py": "from pkg.base import Base\n\n\nclass Mid(Base):\n    pass\n",
            "pkg/child.py": (
                "from pkg.mid import Mid\n\n\n"
                "class Child(Mid):\n"
                "    def run(self):\n        return self.save()\n"
            ),
        },
        ("pkg/mid.py", "pkg/child.py"),
        id="inheritance",
    ),
    pytest.param(
        "introduce_ambiguity",
        {
            "pkg/base.py": (
                "class Base:\n    def save(self):\n        return 1\n\n\n"
                "class Other:\n    def save(self):\n        return 9\n"
            ),
        },
        ("pkg/base.py",),
        id="ambiguity",
    ),
    pytest.param(
        "new_resolution_target",
        {
            "pkg/child.py": (
                "from pkg.base import Base\n"
                "from pkg.other import helper\n\n\n"
                "class Child(Base):\n"
                "    def run(self):\n        return self.save() + helper()\n"
            ),
        },
        ("pkg/child.py",),
        id="new_target",
    ),
    pytest.param(
        "delete_file",
        {"pkg/other.py": None,
         "app.py": (
             "from pkg.child import Child\n\n\n"
             "def main():\n    return Child().run()\n"
         )},
        ("pkg/other.py", "app.py"),
        id="delete_file",
    ),
]


def _write(root: Path, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = root / name
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _facts(database: Path) -> dict[str, list]:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            kind: sorted(db.execute(query).fetchall(), key=repr)
            for kind, query in FACTS.items()
            if kind in tables
        }


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


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
@pytest.mark.parametrize(("case", "mutation", "_changed"), CASES)
def test_an_amended_graph_says_what_a_rebuilt_graph_says(tmp_path, case, mutation, _changed):
    """Same tree, two routes, one meaning.

    Anything the amend retains that the rebuild would not have produced is a
    stale fact outliving the code it described, and anything the rebuild
    produces that the amend drops is a fact a consumer will not find. Both are
    failures of the same claim, so the comparison is an equality rather than a
    containment.
    """
    binary = indexer._resolved_binary_path()
    assert binary and Path(binary).is_file(), "provide the exact candidate Linux binary"

    root = tmp_path / "repo"
    root.mkdir()
    _write(root, BASE)
    state = tmp_path / "state"
    parent = _build(root, state, "parent")
    parent_digest = hashlib.sha256(parent.read_bytes()).hexdigest()

    _write(root, mutation)
    amended = _build(root, state, f"amended_{case}", parent)
    rebuilt = _build(root, state, f"rebuilt_{case}")

    amended_facts = _facts(amended)
    rebuilt_facts = _facts(rebuilt)
    assert set(amended_facts) == set(rebuilt_facts)
    for kind in sorted(amended_facts):
        assert amended_facts[kind] == rebuilt_facts[kind], kind

    # The amend copies; it never edits in place. A consumer holding the parent
    # open is entitled to the graph it opened.
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == parent_digest

    with closing(sqlite3.connect(f"file:{amended.as_posix()}?mode=ro", uri=True)) as db:
        assert db.execute(
            "SELECT value FROM project_meta WHERE key='analysis_state'"
        ).fetchone()[0] == "complete"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_the_mutation_matrix_actually_changes_the_graph(tmp_path):
    """A parity test over mutations that change nothing proves nothing.

    Every case must move at least one fact away from the parent, or the
    equality above is satisfied by a graph nobody touched.
    """
    binary = indexer._resolved_binary_path()
    assert binary and Path(binary).is_file(), "provide the exact candidate Linux binary"

    inert: list[str] = []
    for parameters in CASES:
        case, mutation, _changed = parameters.values
        root = tmp_path / case
        root.mkdir()
        _write(root, BASE)
        state = tmp_path / f"state_{case}"
        before = _facts(_build(root, state, "parent"))
        _write(root, mutation)
        after = _facts(_build(root, state, "mutated"))
        if before == after:
            inert.append(case)
    assert not inert, f"these mutations left the graph unchanged: {inert}"
