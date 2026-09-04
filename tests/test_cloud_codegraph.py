"""File relation graph (HAR-84) against real files in a real git repository.

FAKE BOUNDARY: none for the import graph — every test writes real source files
into a real ``git init`` repository, lists them through the production
``workspace.list_tree``, and parses them with the production code. The only
constructed artefact is the GT graph database, which is a real SQLite file
written with the schema ``gt_engine`` builds.

Run: ``python -m pytest tests/test_cloud_codegraph.py -q`` from the repo root.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from cloud.server import codegraph
from cloud.server.codegraph import MAX_NODES, build_graph, build_import_graph
from cloud.server.workspace import list_tree


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _repo(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    """A committed git repository containing exactly ``files``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, body in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            # newline="" so byte sizes are the same on Windows as on POSIX
            target.write_text(body, encoding="utf-8", newline="")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@example.invalid"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"),
    ):
        subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        )
    return repo


def _edges(repo: Path) -> set[tuple[str, str]]:
    return {
        (e["source"], e["target"]) for e in build_import_graph(str(repo), list_tree(str(repo)))
    }


def _weights(repo: Path) -> dict[tuple[str, str], int]:
    return {
        (e["source"], e["target"]): e["weight"]
        for e in build_import_graph(str(repo), list_tree(str(repo)))
    }


# --------------------------------------------------------------------------
# 1: Python
# --------------------------------------------------------------------------
def test_python_absolute_imports_resolve_to_modules_and_packages(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "main.py": "import pkg.util\nfrom pkg import helper\nimport os\n",
        "pkg/__init__.py": "",
        "pkg/util.py": "VALUE = 1\n",
        "pkg/helper.py": "VALUE = 2\n",
    })
    assert _edges(repo) == {
        ("main.py", "pkg/util.py"),
        # ``from pkg import helper``: the package itself and the submodule
        ("main.py", "pkg/__init__.py"),
        ("main.py", "pkg/helper.py"),
    }


def test_python_resolves_a_src_layout_and_ignores_third_party(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "src/app/cli.py": "import app.core\nimport requests\nfrom app.core import run\n",
        "src/app/__init__.py": "",
        "src/app/core.py": "def run():\n    pass\n",
    })
    assert _edges(repo) == {("src/app/cli.py", "src/app/core.py")}


def test_python_relative_imports_resolve_against_the_importers_package(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/shared.py": "",
        "pkg/sub/__init__.py": "",
        "pkg/sub/leaf.py": "",
        "pkg/sub/mod.py": (
            "from . import leaf\n"
            "from ..shared import thing\n"
            "from .leaf import other\n"
        ),
    })
    assert _edges(repo) == {
        ("pkg/sub/mod.py", "pkg/sub/leaf.py"),
        ("pkg/sub/mod.py", "pkg/shared.py"),
        ("pkg/sub/mod.py", "pkg/sub/__init__.py"),
    }


def test_python_import_of_self_is_not_an_edge(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"solo.py": "import solo\nfrom solo import x\n"})
    assert build_import_graph(str(repo), list_tree(str(repo))) == []


def test_repeated_imports_of_the_same_file_count_into_weight(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "a.py": "import target\n\ndef late():\n    import target\n",
        "target.py": "",
    })
    assert _weights(repo) == {("a.py", "target.py"): 2}


def test_unparsable_python_yields_no_edges_and_does_not_raise(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "broken.py": "import target\nthis is (not python\n",
        "target.py": "",
    })
    assert _edges(repo) == set()


# --------------------------------------------------------------------------
# 2: JavaScript / TypeScript
# --------------------------------------------------------------------------
def test_typescript_relative_specifiers_resolve_by_extension_and_index(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "src/app.ts": (
            "import { a } from './lib/a';\n"
            "import './side.css';\n"
            "import b from './widgets';\n"
            "const c = require('../root.js');\n"
            "export { d } from './lib/d.tsx';\n"
            "import react from 'react';\n"
            "import scoped from '@scope/pkg';\n"
        ),
        "src/lib/a.ts": "export const a = 1;\n",
        "src/lib/d.tsx": "export const d = 2;\n",
        "src/widgets/index.tsx": "export default 1;\n",
        "root.js": "module.exports = 1;\n",
    })
    assert _edges(repo) == {
        ("src/app.ts", "src/lib/a.ts"),
        ("src/app.ts", "src/lib/d.tsx"),
        ("src/app.ts", "src/widgets/index.tsx"),
        ("src/app.ts", "root.js"),
    }


def test_bare_package_specifiers_never_become_edges(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a.js": "import x from 'lodash';\nconst y = require('lodash');\n",
        # a real file whose path would match the bare specifier if we were sloppy
        "lodash.js": "module.exports = {};\n",
    })
    assert _edges(repo) == set()


# --------------------------------------------------------------------------
# 3: Go and Rust
# --------------------------------------------------------------------------
def test_go_same_module_imports_edge_to_every_file_of_the_package(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "go.mod": "module example.com/proj\n\ngo 1.22\n",
        "main.go": (
            'package main\n\n'
            'import (\n'
            '\t"fmt"\n'
            '\t"example.com/proj/internal/store"\n'
            '\t"github.com/other/dep"\n'
            ')\n'
        ),
        "internal/store/store.go": "package store\n",
        "internal/store/extra.go": "package store\n",
    })
    assert _edges(repo) == {
        ("main.go", "internal/store/store.go"),
        ("main.go", "internal/store/extra.go"),
    }


def test_rust_mod_and_use_crate_resolve_best_effort(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "src/lib.rs": "pub mod engine;\nmod util;\nuse crate::engine::run;\n",
        "src/util.rs": "",
        "src/engine/mod.rs": "use crate::util;\n",
    })
    assert _weights(repo) == {
        # ``pub mod engine;`` and ``use crate::engine::run`` both land here
        ("src/lib.rs", "src/engine/mod.rs"): 2,
        ("src/lib.rs", "src/util.rs"): 1,
        ("src/engine/mod.rs", "src/util.rs"): 1,
    }


# --------------------------------------------------------------------------
# 4: file handling
# --------------------------------------------------------------------------
def test_binary_files_are_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "blob.py": b"import target\n\x00\x01\x02binary payload\n",
        "target.py": "",
    })
    assert build_import_graph(str(repo), list_tree(str(repo))) == []


# --------------------------------------------------------------------------
# 5: build_graph — nodes, gt flag, truncation
# --------------------------------------------------------------------------
def test_nodes_mirror_the_tree_with_lang_and_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "README": "hello\n",
        "main.py": "import pkg.util\n",
        "pkg/__init__.py": "",
        "pkg/util.py": "",
        "web/App.tsx": "export default 1;\n",
    })
    files = list_tree(str(repo))
    graph = build_graph(str(repo), files)

    assert [n["path"] for n in graph["nodes"]] == [f["path"] for f in files]
    assert all(n["id"] == n["path"] for n in graph["nodes"])
    by_path = {n["path"]: n for n in graph["nodes"]}
    assert by_path["README"]["lang"] == "" and by_path["README"]["dir"] == ""
    assert by_path["main.py"]["lang"] == "py" and by_path["main.py"]["dir"] == ""
    assert by_path["web/App.tsx"]["lang"] == "tsx"
    assert by_path["web/App.tsx"]["dir"] == "web"
    assert by_path["pkg/util.py"]["dir"] == "pkg"
    assert by_path["README"]["size"] == len("hello\n")
    assert graph["gt"] is False
    assert "truncated" not in graph
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        ("main.py", "pkg/util.py")
    }
    assert all(e["kind"] == "import" for e in graph["edges"])


def test_a_huge_tree_is_capped_to_the_busiest_files_by_degree(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path, {
        "hub.py": "".join(f"import leaf{i}\n" for i in range(3)),
        **{f"leaf{i}.py": "" for i in range(3)},
        **{f"lonely{i}.py": "" for i in range(3)},
    })
    files = list_tree(str(repo))
    assert "truncated" not in build_graph(str(repo), files)

    monkeypatch.setattr(codegraph, "MAX_NODES", 4)
    capped = build_graph(str(repo), files)
    assert capped["truncated"] is True
    # hub (degree 3) and its three leaves (degree 1) beat the isolated files
    assert [n["path"] for n in capped["nodes"]] == [
        "hub.py", "leaf0.py", "leaf1.py", "leaf2.py"
    ]
    kept = {n["path"] for n in capped["nodes"]}
    assert all(e["source"] in kept and e["target"] in kept for e in capped["edges"])


def test_a_huge_tree_is_capped_to_max_nodes(tmp_path: Path) -> None:
    repo = tmp_path / "wide"
    repo.mkdir()
    files = [{"path": f"f{i:05d}.py", "size": 0} for i in range(MAX_NODES + 10)]
    graph = build_graph(str(repo), files)
    assert graph["truncated"] is True
    assert len(graph["nodes"]) == MAX_NODES
    kept = {n["path"] for n in graph["nodes"]}
    assert all(e["source"] in kept and e["target"] in kept for e in graph["edges"])


# --------------------------------------------------------------------------
# 6: GT edges
#
# The schema below is the one the GT indexer's Go producer creates:
# ``nodes`` (symbols, NOT a ``symbols`` table) and ``edges`` with
# ``source_id``/``target_id``/``type`` — vendor/gt-index-src/internal/store/
# sqlite.go:208 (nodes) and :231 (edges). ``nodes.file_path`` is repo-root
# relative with forward slashes (internal/walker/walker.go:99). Only the
# columns this endpoint reads are recreated here.
# --------------------------------------------------------------------------
_GT_SCHEMA = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL
);
CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES nodes(id),
    target_id INTEGER NOT NULL REFERENCES nodes(id),
    type TEXT NOT NULL,
    confidence REAL DEFAULT 0.0
);
"""


def _graph_db(path: Path, symbols: dict[str, str], edges: list[tuple]) -> str:
    """A real SQLite graph: ``symbols`` maps symbol name -> file_path."""
    connection = sqlite3.connect(str(path))
    with connection:
        connection.executescript(_GT_SCHEMA)
        connection.executemany(
            "INSERT INTO nodes (name, file_path, label, language) "
            "VALUES (?, ?, 'Function', 'python')",
            list(symbols.items()),
        )
        ids = {
            name: node_id
            for node_id, name in connection.execute("SELECT id, name FROM nodes")
        }
        connection.executemany(
            "INSERT INTO edges (source_id, target_id, type) VALUES (?, ?, ?)",
            [(ids[s], ids[t], kind) for s, t, kind in edges],
        )
    connection.close()
    return str(path)


def test_gt_symbol_edges_are_aggregated_to_file_level(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a.py": "import b\n",
        "b.py": "",
        "c.py": "",
    })
    db = _graph_db(
        tmp_path / "graph.db",
        {
            "a_one": "a.py", "a_two": "a.py",
            "b_one": "b.py", "b_two": "b.py",
            "c_one": "c.py",
        },
        [
            ("a_one", "b_one", "CALLS"),
            ("a_two", "b_two", "CALLS"),   # collapses with the one above
            ("a_one", "b_two", "API_CALL"),  # also a call edge
            ("a_one", "c_one", "IMPORTS"),
            ("b_one", "c_one", "EXTENDS"),
            ("b_one", "c_one", "READS"),
            ("a_one", "a_two", "CALLS"),   # same file: a self-edge, dropped
            ("a_one", "b_one", "CONTAINS"),  # structural, never an edge here
        ],
    )
    graph = build_graph(str(repo), list_tree(str(repo)), db)

    assert graph["gt"] is True
    gt_edges = {
        (e["source"], e["target"], e["kind"]): e["weight"]
        for e in graph["edges"]
        if e["kind"] != "import"
    }
    assert gt_edges == {
        ("a.py", "b.py", "gt_call"): 3,
        ("a.py", "c.py", "gt_import"): 1,
        ("b.py", "c.py", "gt_ref"): 2,
    }
    # the static import edge is still there alongside them
    assert {
        (e["source"], e["target"]) for e in graph["edges"] if e["kind"] == "import"
    } == {("a.py", "b.py")}


def test_gt_paths_are_normalised_and_unknown_files_are_dropped(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {"pkg/a.py": "", "b.py": ""})
    db = _graph_db(
        tmp_path / "graph.db",
        {
            # a backslash path, a workspace-prefixed absolute path, a "./"
            # prefix, and a file that is not in the tree at all
            "back": "pkg\\a.py",
            "absolute": f"{repo.resolve().as_posix()}/b.py",
            "dotted": "./b.py",
            "gone": "vanished/old.py",
        },
        [
            ("back", "absolute", "CALLS"),
            ("back", "dotted", "CALLS"),
            ("back", "gone", "CALLS"),
        ],
    )
    graph = build_graph(str(repo), list_tree(str(repo)), db)

    assert graph["gt"] is True
    assert [(e["source"], e["target"], e["kind"], e["weight"]) for e in graph["edges"]] == [
        ("pkg/a.py", "b.py", "gt_call", 2)
    ]


def test_a_corrupt_graph_db_fails_open_to_import_edges_only(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {"a.py": "import b\n", "b.py": ""})
    corrupt = tmp_path / "graph.db"
    corrupt.write_bytes(b"this is definitely not a SQLite database\n" * 32)

    assert build_import_graph(str(repo), list_tree(str(repo))) != []
    graph = build_graph(str(repo), list_tree(str(repo)), str(corrupt))
    assert graph["gt"] is False
    assert all(e["kind"] == "import" for e in graph["edges"])
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {("a.py", "b.py")}


def test_a_missing_graph_db_and_a_schemaless_one_both_fail_open(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {"a.py": "import b\n", "b.py": ""})
    files = list_tree(str(repo))

    missing = build_graph(str(repo), files, str(tmp_path / "nope.db"))
    assert missing["gt"] is False and len(missing["edges"]) == 1

    empty_db = tmp_path / "empty.db"
    sqlite3.connect(str(empty_db)).close()
    wrong_schema = build_graph(str(repo), files, str(empty_db))
    assert wrong_schema["gt"] is False and len(wrong_schema["edges"]) == 1

    # and no graph database at all is simply gt: false, not an error
    assert build_graph(str(repo), files, None)["gt"] is False
