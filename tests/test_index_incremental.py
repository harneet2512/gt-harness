"""Blocker 7c: the live path amends the graph instead of rebuilding it.

The producer has had a per-file amend boundary since 25a37a5f and nothing
called it, so every edit paid for a full re-index. These tests pin the two
halves of the fix that can be checked without a real producer: the argv and
capability gate that decide whether an amend is attempted at all, and the
copy-then-publish contract that keeps the parent graph certifiable.

Test doubles follow the tiers already established here: argv assertions with no
process (test_index_graph_quality.py), a fake Python "binary" via the
_resolved_binary_path/_index_command/_binary_certification triple
(test_index_resource_guard.py), and receipt-level lambdas
(test_miniswe_integration.py). The real producer is exercised by the
GT_INDEX_BINARY test at the end, which is skipped off Linux.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from gt_engine import indexer
from gt_engine.miniswe_integration import MiniSweAdapter


@pytest.mark.parametrize("parent_exists", [False, True])
def test_missing_parent_artifacts_refuse_before_producer_capability_probe(tmp_path, monkeypatch, parent_exists):
    adapter = _adapter(tmp_path)
    parent = tmp_path / "absent-parent.db"
    if parent_exists:
        parent.write_bytes(b"not a certified graph")
    def forbidden(*args):
        pytest.fail("An ineligible parent must not pay for producer capability discovery")
    monkeypatch.setattr(indexer, "_producer_supports_amend_capability", forbidden)
    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        adapter.repo_root, layout=adapter.engine_state.layout, parent_graph=parent, changed_paths=("a.py",))
    assert result is None
    assert reason == ("parent_manifest_missing" if parent_exists else "parent_graph_missing")
    assert rows == ()


@pytest.fixture(autouse=True)
def _enable_guarded_test_process_on_windows(monkeypatch):
    """The producer refuses to launch on Windows until a Job Object exists.

    The guard is about descendant teardown, not about the amend, so the tests
    that need a child process stand in a verified kill for it. The real
    refusal is still what runs in production on this platform.
    """
    if os.name == "nt":
        def verified_test_kill(process):
            if process.poll() is None:
                process.kill()
            return True

        monkeypatch.setattr(indexer, "_has_verified_index_process_tree_guard", lambda: True)
        monkeypatch.setattr(indexer, "_kill_index_process_tree", verified_test_kill)


@pytest.fixture(autouse=True)
def _clear_capability_cache():
    indexer._AMEND_CAPABILITY_CACHE.clear()
    yield
    indexer._AMEND_CAPABILITY_CACHE.clear()


# --------------------------------------------------------------------- argv


def test_incremental_command_names_one_file_and_no_walk_bounds():
    """-max-files/-workers/-closure are ignored in incremental mode.

    Passing them would state a bound the producer does not apply: it never
    walks the tree and never recomputes the closure sidecar on this path.
    """
    argv = indexer._incremental_index_command("gt-index", "/repo", "/out/graph.db", "pkg/app.py")

    assert argv == ["gt-index", "-root", "/repo", "-output", "/out/graph.db",
                    "-file", "pkg/app.py"]
    assert "-max-files" not in argv
    assert "-closure=true" not in argv


def test_amendable_extension_mirror_matches_the_producer_registry():
    """The mirror cannot rot into a silent full rebuild on every edit.

    A path whose extension is missing here is skipped, so a drifted mirror
    would quietly stop amending the language the task is written in. The
    producer's registry is the authority, so read it.
    """
    registry = Path(__file__).resolve().parents[1] / "vendor/gt-index-src/internal/specs"
    if not registry.is_dir():
        pytest.skip("vendored producer source not present")
    declared: set[str] = set()
    for source in registry.glob("*.go"):
        if source.name.endswith("_test.go"):
            continue
        for block in re.finditer(r"Extensions:\s*\[\]string\{([^}]*)\}",
                                 source.read_text(encoding="utf-8")):
            declared.update(re.findall(r'"([^"]+)"', block.group(1)))

    assert declared, "no language specs found in the producer registry"
    assert indexer.INCREMENTAL_AMENDABLE_EXTS == frozenset(declared)


# ------------------------------------------------------- the capability gate


def _fake_build_info(path: Path, payload: object) -> None:
    path.write_text(
        "import sys\n"
        "if '-build-info' in sys.argv:\n"
        f"    sys.stdout.write({json.dumps(json.dumps(payload))})\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "capabilities",
    [
        pytest.param(["atomic_graph_publication", "incremental_stale_suppression"],
                     id="certified-binary-capability-list"),
        pytest.param([], id="no-capabilities"),
    ],
)
def test_amend_capability_is_refused_when_not_declared(tmp_path, monkeypatch, capabilities):
    """The certified c3b9f16e accepts -file and destroys the graph doing it.

    Its capability list is otherwise identical to a build that amends
    correctly, so the flag's presence proves nothing and the name must be
    declared explicitly.
    """
    probe = tmp_path / "probe.py"
    _fake_build_info(probe, {"schema": "gt-index.build.v1", "capabilities": capabilities})
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(
        indexer, "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        indexer, "_incremental_index_command",
        lambda binary, root, output, relpath: [binary, str(probe)],
    )
    monkeypatch.setattr(
        indexer, "_index_command",
        lambda binary, root, output: [binary, str(probe), "-build-info"],
    )

    assert indexer._producer_supports_incremental_amend() is False


@pytest.mark.parametrize(
    "script",
    [
        pytest.param("import sys\nsys.stdout.write('not json')\n", id="unparseable"),
        pytest.param("raise SystemExit(2)\n", id="nonzero-exit"),
    ],
)
def test_amend_capability_probe_fails_closed(tmp_path, monkeypatch, script):
    probe = tmp_path / "probe.py"
    probe.write_text(script, encoding="utf-8")
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(
        indexer, "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    real_run = indexer.subprocess.run
    monkeypatch.setattr(
        indexer.subprocess, "run",
        lambda argv, **kwargs: real_run([sys.executable, str(probe)], **kwargs),
    )

    assert indexer._producer_supports_incremental_amend() is False


def test_missing_binary_refuses_without_probing(monkeypatch):
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: "")

    def forbidden(*args, **kwargs):
        pytest.fail("probed a producer that does not exist")

    monkeypatch.setattr(indexer.subprocess, "run", forbidden)
    assert indexer._producer_supports_incremental_amend() is False


# ------------------------------------------------------------ path selection


@pytest.mark.parametrize(
    ("changed", "expected_reason"),
    [
        pytest.param(("tsconfig.json",), "config_input_changed:tsconfig.json", id="config-input"),
        pytest.param(("notes.txt",), "no_amendable_paths", id="nothing-the-producer-parses"),
        pytest.param(tuple(f"pkg/f{n}.py" for n in range(9)),
                     "dirty_paths_exceed_limit:9", id="over-the-crossover"),
    ],
)
def test_paths_the_amend_must_refuse(tmp_path, changed, expected_reason):
    """Each refusal is a correctness answer, not a failure.

    A producer config file refuses because it changes how every OTHER file
    resolves. A file the producer cannot parse is skipped, not refused: it
    contributes no nodes, so it cannot have made the graph stale. A DELETED
    path is neither -- see the deletion test below.
    """
    for ordinal in range(9):
        target = tmp_path / "pkg" / f"f{ordinal}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("text\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}\n", encoding="utf-8")

    amendable, reason = indexer._amendable_paths(tmp_path, changed)

    assert amendable == ()
    assert reason == expected_reason


def test_a_deleted_path_is_amendable_not_refused(tmp_path):
    """Deletions were the single largest cause of full rebuilds.

    Five of eleven on the 2026-09-08 run, because the agent kept creating
    scratch test files and removing them. The producer reconciles a missing
    file's node set to empty rather than erroring, so the engine passes the
    path through.
    """
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")

    amendable, reason = indexer._amendable_paths(
        tmp_path, ("kept.py", "pkg/gone.py"))

    assert reason == ""
    assert amendable == ("kept.py", "pkg/gone.py")


def test_a_rename_is_amendable_as_both_halves(tmp_path):
    """A rename arrives as a deletion beside a creation, in one dirty set."""
    (tmp_path / "new_name.py").write_text("x = 1\n", encoding="utf-8")

    amendable, reason = indexer._amendable_paths(
        tmp_path, ("old_name.py", "new_name.py"))

    assert reason == ""
    assert amendable == ("new_name.py", "old_name.py")


def test_unparseable_paths_are_skipped_not_refused(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.rst").write_text("text\n", encoding="utf-8")

    amendable, reason = indexer._amendable_paths(tmp_path, ("app.py", "README.rst"))

    assert amendable == ("app.py",)
    assert reason == ""


# ------------------------------------------------------------- result parsing


def test_amend_result_line_is_read_for_its_counts():
    line = json.dumps({
        "file": "pkg/app.py", "nodes_replaced": 20, "inserted": 0, "updated": 20,
        "removed": 0, "symbols_reminted": 20, "symbols_removed": 20,
        "short_circuited": False,
    })

    parsed = indexer._parse_incremental_result(f"progress noise\n{line}\n")

    assert parsed["updated"] == 20
    assert parsed["symbols_reminted"] == 20


def test_unreadable_result_line_is_reported_not_assumed():
    """A reindex that reports only that it ran cannot be told apart from one
    that replaced the graph, which is the state the amend fix exists to end."""
    assert indexer._parse_incremental_result("no json here")["result_line"] == "unparsed"


# ------------------------------------------------- the copy-then-amend contract


def _adapter(tmp_path: Path) -> MiniSweAdapter:
    workspace = tmp_path / "repo"
    workspace.mkdir(exist_ok=True)
    return MiniSweAdapter(task_id="task", state_dir=tmp_path / "state",
                          repo_root=workspace, predicates=[])


def _publish_parent(root: Path, layout, monkeypatch) -> Path:
    """Build one real (fake-producer) graph and return its published path."""
    fake = root.parent / "fake-index.py"
    fake.write_text(
        "import sqlite3, sys\n"
        "output = sys.argv[sys.argv.index('-output') + 1]\n"
        "with sqlite3.connect(output) as c:\n"
        "    c.execute('create table project_meta (key text, value text)')\n"
        "    c.execute('create table file_hashes (path text)')\n"
        "    c.execute('create table nodes (id integer, file_path text)')\n"
        "    c.execute(\"insert into nodes values (1, 'app.py')\")\n"
        "    c.execute(\"insert into file_hashes values ('app.py')\")\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(
        indexer, "_index_command",
        lambda binary, r, output: [binary, str(fake), "-root", r, "-output", output],
    )
    monkeypatch.setattr(
        indexer, "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    graph = indexer.ensure_index(str(root), layout=layout)
    assert graph is not None, "parent graph was not published"
    return Path(graph)


def test_batch_amend_uses_one_process_for_multiple_paths_and_config(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    before = parent.read_bytes()
    changed = ("app.py", "two.py", "three.py", "four.py", "pyproject.toml")
    for name in changed:
        (root / name).write_text("# edited\n", encoding="utf-8")
    calls = []
    script = tmp_path / "batch.py"
    script.write_text("import shutil,sys\nshutil.copyfile(sys.argv[-1],sys.argv[1])\n", encoding="utf-8")

    def command(binary, source, output):
        calls.append((source, output))
        return [binary, str(script), output]

    monkeypatch.setattr(indexer, "_producer_supports_amend_capability",
                        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(indexer, "_index_command", command)
    receipt = indexer.refresh_index_files(root, parent, changed, layout=layout, source_revision="batch-2")
    assert receipt.success, receipt.error_type
    assert receipt.build_mode == "incremental"
    assert len(calls) == 1
    assert receipt.incremental_results[0]["mode"] == "batch"
    assert receipt.incremental_results[0]["paths"] == sorted(changed)
    assert parent.read_bytes() == before


def test_parser_cache_location_survives_graph_revision_changes(tmp_path):
    root = tmp_path / "repo"
    state = tmp_path / "state"
    first = indexer._index_launch_environment(1024**3, str(root), state / "revisions" / "one")
    second = indexer._index_launch_environment(1024**3, str(root), state / "revisions" / "two")
    assert first["GT_PARSE_CACHE_ROOT"] == second["GT_PARSE_CACHE_ROOT"] == str((state / "parse-cache").resolve())
    assert "GT_PARSE_CACHE_ROOT" not in indexer._index_launch_environment(1024**3, str(root), root / "state")


def test_amend_publishes_a_new_revision_and_leaves_the_parent_certifiable(
    tmp_path, monkeypatch
):
    """The parent is never opened for writing.

    A published graph is immutable and its manifest pins its exact bytes, so
    amending in place would invalidate the certificate of the graph readers
    hold right now (indexer.py refuses it outright, and certify_graph_artifact
    would fail on graph_sha256). The copy is what gets amended.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    parent_bytes = parent.read_bytes()
    parent_sha = hashlib.sha256(parent_bytes).hexdigest()

    # The edit the amend is asked to absorb.
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    amend = tmp_path / "fake-amend.py"
    amend.write_text(
        "import sqlite3, sys, json\n"
        "output = sys.argv[sys.argv.index('-output') + 1]\n"
        "relpath = sys.argv[sys.argv.index('-file') + 1]\n"
        "with sqlite3.connect(output) as c:\n"
        "    c.execute('insert into nodes values (2, ?)', (relpath,))\n"
        "print(json.dumps({'file': relpath, 'nodes_replaced': 2, 'inserted': 1,\n"
        "                  'updated': 1, 'removed': 0, 'symbols_reminted': 2,\n"
        "                  'symbols_removed': 1, 'short_circuited': False}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_incremental_index_command",
        lambda binary, r, output, relpath: [
            binary, str(amend), "-root", r, "-output", output, "-file", relpath,
        ],
    )

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="rev-2",
    )

    assert receipt.success, receipt.error_type
    assert receipt.build_mode == "incremental", f"reason={receipt.build_mode_reason!r} results={receipt.incremental_results!r}"
    assert receipt.build_mode_reason == ""
    published = Path(receipt.graph_db)

    # A new revision directory, not the parent's.
    assert published != parent
    assert published.parent.parent.name == "revisions"

    # The parent survived byte-for-byte and still certifies.
    assert parent.read_bytes() == parent_bytes
    valid, reason = indexer._certify_published_graph(
        parent, parent.with_suffix(".manifest.json"),
        expected_root=Path(layout.workspace), expected_binary_sha256="b" * 64,
    )
    assert valid, reason

    # The new graph says how it was made, and names what it came from.
    manifest = json.loads(published.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["build_mode"] == "incremental"
    assert manifest["parent_graph_sha256"] == parent_sha
    assert manifest["amended_paths"] == ["app.py"]
    valid, reason = indexer._certify_published_graph(
        published, published.with_suffix(".manifest.json"),
        expected_root=Path(layout.workspace), expected_binary_sha256="b" * 64,
    )
    assert valid, reason

    # The amend actually landed, and its counts were reported.
    with sqlite3.connect(f"file:{published.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2
    assert [row["path"] for row in receipt.incremental_results] == ["app.py"]
    assert receipt.incremental_results[0]["symbols_reminted"] == 2


def test_failed_amend_falls_back_to_a_full_rebuild_and_names_why(tmp_path, monkeypatch):
    """A refused or failed amend is never an error the caller has to handle.

    It is the behaviour that existed before this path, plus a reason. The
    reason is the point: a permanent silent fallback is exactly how the amend
    stayed dead for a whole run while everything looked healthy.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    failing = tmp_path / "fake-amend-fail.py"
    failing.write_text("raise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_incremental_index_command",
        lambda binary, r, output, relpath: [binary, str(failing)],
    )

    calls: list[str] = []
    real_full = indexer.ensure_index_with_receipt
    monkeypatch.setattr(
        indexer, "ensure_index_with_receipt",
        lambda *args, **kwargs: calls.append("full") or real_full(*args, **kwargs),
    )

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="rev-2",
    )

    assert calls == ["full"], "a failed amend must fall back to the full rebuild"
    assert receipt.build_mode == "full"
    assert receipt.build_mode_reason.startswith("amend_failed:")
    assert receipt.success, receipt.error_type


def test_amend_is_refused_when_the_producer_does_not_declare_the_capability(
    tmp_path, monkeypatch
):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: False)

    def forbidden(*args, **kwargs):
        pytest.fail("amended with a producer that never declared it amends")

    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="rev-2",
    )

    assert receipt.build_mode == "full"
    assert receipt.build_mode_reason == "producer_lacks_amend_capability"


def test_amend_is_refused_when_the_parent_cannot_be_certified(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_certify_published_graph",
        lambda *args, **kwargs: (False, "graph_sha256_mismatch"),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("amended onto a graph that could not be certified")

    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="rev-2",
    )

    assert receipt.build_mode == "full"
    assert receipt.build_mode_reason == (
        "incremental_parent_uncertifiable:graph_sha256_mismatch"
    )


def test_reader_created_wal_sidecars_do_not_refuse_the_amend(tmp_path, monkeypatch):
    """A published graph is left in WAL mode, so readers create these files.

    Refusing on their PRESENCE refused every amend a real run would attempt.
    The 2026-09-08 smoke reported it on its first edit -- `mode=full
    reason=parent_graph_has_wal_sidecar` at a cost of 104.6s -- with a 0-byte
    log and a 32 KiB shm, both created by readers, holding nothing.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)

    # Exactly what a reader leaves behind: an empty log and a shm.
    parent.with_name(parent.name + "-wal").write_bytes(b"")
    parent.with_name(parent.name + "-shm").write_bytes(b"\x00" * 32768)

    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")
    amend = tmp_path / "fake-amend-wal.py"
    amend.write_text(
        "import sqlite3, sys, json\n"
        "output = sys.argv[sys.argv.index('-output') + 1]\n"
        "relpath = sys.argv[sys.argv.index('-file') + 1]\n"
        "with sqlite3.connect(output) as c:\n"
        "    c.execute('insert into nodes values (2, ?)', (relpath,))\n"
        "print(json.dumps({'file': relpath, 'inserted': 1, 'updated': 0,\n"
        "                  'removed': 0, 'symbols_reminted': 1,\n"
        "                  'short_circuited': False}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_incremental_index_command",
        lambda binary, r, output, relpath: [
            binary, str(amend), "-root", r, "-output", output, "-file", relpath,
        ],
    )

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="rev-2",
    )

    assert receipt.build_mode == "incremental", receipt.build_mode_reason
    assert receipt.success, receipt.error_type


def test_a_log_holding_frames_is_carried_onto_the_copy(tmp_path, monkeypatch):
    """A non-empty log is copied so no committed frame is lost.

    The parent is still never written to, so its certificate stands.
    """
    parent = tmp_path / "graph.db"
    with sqlite3.connect(parent) as connection:
        connection.execute("create table t (x integer)")
        connection.execute("insert into t values (1)")
    log = parent.with_name(parent.name + "-wal")
    log.write_bytes(b"frames-not-yet-folded-in")
    parent_bytes = parent.read_bytes()

    candidate = tmp_path / "candidate.db"
    indexer._copy_graph_for_amend(parent, candidate)

    assert candidate.read_bytes() == parent_bytes
    assert candidate.with_name(candidate.name + "-wal").read_bytes() == log.read_bytes()
    # Untouched, as the immutability contract requires.
    assert parent.read_bytes() == parent_bytes


def test_an_empty_log_is_not_copied(tmp_path):
    parent = tmp_path / "graph.db"
    parent.write_bytes(b"database")
    parent.with_name(parent.name + "-wal").write_bytes(b"")

    candidate = tmp_path / "candidate.db"
    indexer._copy_graph_for_amend(parent, candidate)

    assert candidate.read_bytes() == b"database"
    assert not candidate.with_name(candidate.name + "-wal").exists()


# ------------------------------------------------------------- real producer


def test_batch_summary_preserves_work_counters():
    summary = {"build_mode": "batch", "files": 5, "parser_nodes_retained": 7,
               "parser_nodes_inserted": 3, "parse_cache_hits": 4,
               "parse_cache_misses": 1, "resolver_passes": 1}
    assert indexer._parse_incremental_result(json.dumps(summary)) == summary


@pytest.mark.skipif(
    os.name != "posix" or not os.environ.get("GT_INDEX_BINARY"),
    reason="installed Linux producer required",
)
def test_real_producer_amend_reminds_the_edited_files_symbols(tmp_path):
    """The end-to-end claim: an amend keeps the graph and re-mints identity.

    resolution_symbols used to be cleared wholesale by any -file run, which
    left gt_engine.contract deriving a different id form for every symbol in
    the repository and the verification planner finding no entities at all.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "base.py").write_text(
        "class Base:\n    def save(self):\n        return 1\n", encoding="utf-8")
    (root / "pkg" / "child.py").write_text(
        "from pkg.base import Base\n\n\nclass Child(Base):\n"
        "    def run(self):\n        return self.save()\n", encoding="utf-8")

    graph = tmp_path / "graph.db"
    binary = os.environ["GT_INDEX_BINARY"]
    assert indexer.subprocess.run(
        indexer._index_command(binary, str(root), str(graph)), check=False,
        capture_output=True,
    ).returncode == 0

    with sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True) as connection:
        before = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert before > 0

    (root / "pkg" / "child.py").write_text(
        "from pkg.base import Base\n\n\nclass Child(Base):\n"
        "    def run(self):\n        return self.save()\n\n"
        "    def extra(self):\n        return 0\n", encoding="utf-8")
    build_info = json.loads(indexer.subprocess.run(
        [binary, "-build-info"], check=True, capture_output=True, text=True,
    ).stdout)
    batch = indexer.BATCH_AMEND_CAPABILITY in build_info["capabilities"]
    candidate = tmp_path / "candidate.db" if batch else graph
    command = (
        indexer._index_command(binary, str(root), str(candidate)) + ["-amend-parent", str(graph)]
        if batch else indexer._incremental_index_command(
            binary, str(root), str(graph), "pkg/child.py")
    )
    amend = indexer.subprocess.run(
        command,
        check=False, capture_output=True,
    )
    assert amend.returncode == 0, amend.stderr

    result = indexer._parse_incremental_result(amend.stdout.decode("utf-8", "replace"))
    if batch:
        assert result["build_mode"] == "batch"
        assert result["parser_nodes_retained"] > 0
        assert result["parser_nodes_inserted"] > 0
        assert result["resolver_passes"] == 1
    else:
        assert result["short_circuited"] is False
        assert int(result["symbols_reminted"]) > 0

    with sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True) as connection:
        after = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        reminted = connection.execute(
            "SELECT COUNT(*) FROM resolution_symbols WHERE path = 'pkg/child.py'"
        ).fetchone()[0]
        elsewhere = connection.execute(
            "SELECT COUNT(*) FROM resolution_symbols WHERE path != 'pkg/child.py'"
        ).fetchone()[0]

    # The graph was amended, not replaced: 25a37a5f's whole point.
    assert after >= before
    assert reminted > 0, "the amended file lost its symbol identity"
    assert elsewhere > 0, "an amend cleared symbols for files it never touched"
