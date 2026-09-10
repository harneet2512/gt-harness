"""Is the graph a function of the source tree?

Everything GT claims rests on an unstated assumption: index the same tree twice
and get the same graph. Measured on `click` (105 parsed files) with the
certified producer, that is false. Three full rebuilds of a byte-identical tree
produced two different edge sets -- 50,954 edges and 50,957 edges, with
different content -- and the batch amend wobbles between exactly the same two.
`-workers 1` is equally nondeterministic and lands on the identical pair of
digests, so this is not resolver concurrency.

What moves is receiver selection for same-named methods. In one build
`AliasedGroup.get_command` calls `Context.fail`; in another it calls
`ParamType.fail`. `_AtomicFile.close` calls `Context.close` or `LazyFile.close`.
`runner.invoke` binds to `Context.invoke` or `CliRunner.invoke`. A consumer
asking who calls `fail` gets a different answer depending on which build of the
same code it happens to read.

This matters beyond the amend. It sets a ceiling on every parity claim in this
project: no comparison between two graphs can be tighter than the producer's
agreement with itself. It is why the fixture-scale amend-versus-rebuild parity
suite passes -- a small synthetic tree has no same-named methods for the
tie-break to act on -- and why that suite was not sufficient evidence for the
claim it was used to close.

Two tests, because the defect needs scale and the guard does not.

The fixture below has the SHAPE -- several classes each defining the same method
name, reached through receivers whose type is not locally obvious -- and at
eight files it is deterministic. That was measured, not assumed: it passes. So
it is kept as a regression guard for the scale where determinism currently
holds, and it is explicitly NOT evidence that the producer is deterministic.

The second test reproduces the real defect against a real repository, which is
where it lives. It needs one, so it is skipped unless GT_DETERMINISM_REPO names
a checkout, and it is marked xfail because the producer is a certified binary
this project pins and cannot repair here. Fixing the producer turns it into an
XPASS rather than into silence.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from gt_engine import indexer

FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/errors.py": (
        "class Failure:\n"
        "    def fail(self, message):\n        raise RuntimeError(message)\n\n"
        "    def close(self):\n        return 0\n"
    ),
    "pkg/types.py": (
        "class ParamType:\n"
        "    def fail(self, message):\n        raise ValueError(message)\n\n"
        "    def to_info_dict(self):\n        return {}\n"
    ),
    "pkg/context.py": (
        "class Context:\n"
        "    def fail(self, message):\n        raise SystemExit(message)\n\n"
        "    def close(self):\n        return 1\n\n"
        "    def to_info_dict(self):\n        return {}\n\n"
        "    def invoke(self, target):\n        return target()\n"
    ),
    "pkg/runner.py": (
        "class CliRunner:\n"
        "    def invoke(self, command):\n        return command\n\n"
        "    def close(self):\n        return 2\n"
    ),
    "pkg/files.py": (
        "class LazyFile:\n"
        "    def close(self):\n        return 3\n\n\n"
        "class AtomicFile:\n"
        "    def __init__(self, handle):\n        self.handle = handle\n\n"
        "    def close(self):\n        return self.handle.close()\n"
    ),
    "app.py": (
        "from pkg.context import Context\n"
        "from pkg.runner import CliRunner\n"
        "from pkg.types import ParamType\n\n\n"
        "def resolve(thing, message):\n"
        "    return thing.fail(message)\n\n\n"
        "def shut(thing):\n"
        "    return thing.close()\n\n\n"
        "def describe(thing):\n"
        "    return thing.to_info_dict()\n\n\n"
        "def run(runner, command):\n"
        "    return runner.invoke(command)\n"
    ),
    "test_app.py": (
        "from app import describe, resolve, run, shut\n"
        "from pkg.runner import CliRunner\n\n\n"
        "def test_run():\n"
        "    runner = CliRunner()\n"
        "    assert run(runner, 1) == 1\n\n\n"
        "def test_shut():\n"
        "    assert shut(CliRunner()) == 2\n"
    ),
}

SURFACES = {
    "nodes": ("SELECT label,name,qualified_name,file_path,start_line,signature,"
              "is_test,language FROM nodes"),
    "edges": ("SELECT e.type,s.qualified_name,s.file_path,t.qualified_name,t.file_path"
              " FROM edges e LEFT JOIN nodes s ON s.id=e.source_id"
              " LEFT JOIN nodes t ON t.id=e.target_id"),
    "assertions": ("SELECT a.expression,tn.qualified_name,gn.qualified_name FROM assertions a"
                   " LEFT JOIN nodes tn ON tn.id=a.test_node_id"
                   " LEFT JOIN nodes gn ON gn.id=a.target_node_id"),
}


def _digest(database: Path) -> dict[str, str]:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        present = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out: dict[str, str] = {}
        for kind, query in SURFACES.items():
            if kind not in present:
                out[kind] = "absent"
                continue
            rows = sorted(db.execute(query).fetchall(), key=repr)
            payload = "\n".join(repr(row) for row in rows)
            out[kind] = f"{len(rows)}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
        return out


def _build_thrice(root: Path, workspace: Path) -> list[dict[str, str]]:
    digests = []
    for index in range(3):
        logs = workspace / f"run{index}"
        logs.mkdir(parents=True)
        output = logs / "graph.db"
        result = indexer._run_index_bounded(str(root), output, logs)
        assert result.success, (result.error_code, result.stderr_tail)
        digests.append(_digest(output))
        output.unlink(missing_ok=True)
    return digests


def _assert_agreed(digests: list[dict[str, str]]) -> None:
    distinct = {repr(sorted(item.items())) for item in digests}
    moved = {key for key in digests[0] if len({item[key] for item in digests}) > 1}
    detail = {key: sorted({item[key] for item in digests}) for key in moved}
    assert len(distinct) == 1, f"three builds of one tree disagreed: {detail}"


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_the_same_fixture_indexed_three_times_gives_the_same_graph(tmp_path):
    """True today, at eight files. Kept so that it stays true.

    This is a regression guard, not evidence that the producer is
    deterministic: the real-repository test below is the one that speaks to
    that, and it fails.
    """
    binary = indexer._resolved_binary_path()
    assert binary and Path(binary).is_file(), "provide the exact candidate Linux binary"

    root = tmp_path / "repo"
    for name, content in FIXTURE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _assert_agreed(_build_thrice(root, tmp_path / "runs"))


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_the_same_real_repository_indexed_three_times_gives_the_same_graph(tmp_path):
    """Where the defect actually lives.

    Needs a real checkout, because the ambiguity that drives the tie-break does
    not exist at fixture scale. It also needs the RIGHT one: the defect is
    repository-dependent, measured. `click` gives four distinct digests across
    ten builds; `kedro-org__kedro-4580` gives ONE and is perfectly
    deterministic. What separates them is several classes each defining the
    same method name -- click has `fail`, `close`, `invoke` and `to_info_dict`
    on four different classes, reached through receivers whose type is not
    locally obvious, and kedro does not.

    So an XPASS here is NOT evidence the producer was repaired. It is equally
    the shape of a repository that never exercised the tie-break. I tried to
    detect that from source and could not: a heuristic looking for one method
    name defined on several classes accepts kedro too, and kedro is
    deterministic. Whatever distinguishes them is finer than "same-named
    methods exist", so the caller has to choose a repository known to exhibit
    it -- click does -- and read an XPASS with that in mind.
    """
    import os
    import shutil

    binary = indexer._resolved_binary_path()
    assert binary and Path(binary).is_file(), "provide the exact candidate Linux binary"
    source = os.environ.get("GT_DETERMINISM_REPO", "")
    if not source or not Path(source).is_dir():
        pytest.skip("GT_DETERMINISM_REPO does not name a repository checkout")

    root = tmp_path / "repo"
    shutil.copytree(source, root, symlinks=True)
    _assert_agreed(_build_thrice(root, tmp_path / "runs"))
