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
from types import SimpleNamespace

import pytest

from gt_engine import indexer, retrieval
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


def test_batch_headroom_refusal_falls_back_to_the_per_file_amend(tmp_path, monkeypatch):
    """A batch floor the cgroup can never meet must not starve the graph.

    Run 35178222629 journaled 58 batch_amend_floor refusals on one task:
    the floor (~1.7 GB for a ~93k-node parent) sat permanently above the
    ~1.4 GB limit, the adopted graph stayed stale for the tail of the
    run, and refused re-localizations then poisoned the last dense
    receipt -- treatment_dense_index_not_ready on a task the verifier
    passed. The per-file amend's memory scales with the dirty set, not
    the parent, so when the producer declares both lanes and the dirty
    set is amendable it is the lane that still fits.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    parent_bytes = parent.read_bytes()
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability", lambda capability: True)
    # The batch floor priced above any real cgroup limit; the per-file
    # spawn keeps the real _effective_index_memory_limit.
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)

    def forbidden_batch(*args, **kwargs):
        pytest.fail("the refused batch lane spawned a producer anyway")
    monkeypatch.setattr(indexer, "_index_command", forbidden_batch)

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
    assert receipt.build_mode == "incremental"
    assert receipt.incremental_results[0]["path"] == "app.py"
    assert "mode" not in receipt.incremental_results[0]
    assert parent.read_bytes() == parent_bytes
    published = Path(receipt.graph_db)
    with sqlite3.connect(f"file:{published.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2


def test_batch_headroom_refusal_stands_without_the_per_file_lane(tmp_path, monkeypatch):
    """A batch-only producer keeps the memory refusal - it never spawns."""
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend spawned a producer")
    monkeypatch.setattr(indexer, "_index_command", forbidden)
    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None
    assert reason.startswith("GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:")
    assert "need=1000000000000" in reason
    assert "incremental_amend_uncoverable" not in reason
    assert rows == ()


def test_batch_headroom_refusal_names_an_uncoverable_fallback_set(tmp_path, monkeypatch):
    """A dirty set the cheap lane cannot parse keeps the memory refusal.

    Defer stays honest: every bigger lane is bounded by the same cgroup,
    and the suffix records that the fallback was evaluated, not skipped.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)

    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability", lambda capability: True)
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend spawned a producer")
    monkeypatch.setattr(indexer, "_index_command", forbidden)
    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("logo.png",))

    assert result is None
    assert reason.startswith("GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:")
    assert "incremental_amend_uncoverable:no_amendable_paths" in reason
    assert rows == ()


@pytest.mark.parametrize("per_file_lane", [False, True])
def test_batch_headroom_refusal_records_the_cgroup_it_read(
    tmp_path, monkeypatch, per_file_lane
):
    """``limit=`` alone cannot tell a full cgroup from a misread one.

    Run 35262214538 journaled nine GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT
    refusals whose cause carried limit=0, 2326528, 7020544 and 12804096
    against need=178438144. More runner memory, a smaller batch and a
    deferred amend are all consistent with that, so no operator could pick
    the remedy. The max/current the limit was derived from decide it in one
    run, on both refusal branches - the bare one and the one that also
    reports an uncoverable per-file fallback.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text(
        "def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(
        indexer, "_producer_supports_incremental_amend", lambda: per_file_lane)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)
    monkeypatch.setattr(
        indexer, "_cgroup_snapshot",
        lambda **_kwargs: {
            "current": 900_000_000, "max": 1_000_000_000,
            "peak": None, "oom": None, "oom_kill": None,
            "cgroup_version": 2, "limit_state": "limited",
            "headroom_basis": "max_and_current", "source": "proc_self_cgroup",
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend spawned a producer")
    monkeypatch.setattr(indexer, "_index_command", forbidden)
    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    # A path the per-file lane cannot parse, so the ``per_file_lane=True``
    # branch reaches the second refusal instead of falling back to it.
    changed = ("logo.png",) if per_file_lane else ("app.py",)
    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=changed)

    assert result is None and rows == ()
    assert reason.startswith(
        "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:")
    assert "cgroup_max=1000000000current=900000000" in reason
    assert "basis=max_and_current" in reason
    assert ("incremental_amend_uncoverable" in reason) is per_file_lane


def test_batch_headroom_refusal_says_when_the_headroom_was_estimated(
    tmp_path, monkeypatch
):
    """An estimated ceiling must not read like a measured one.

    With the usage file unreadable the budget is derived from ``max`` alone.
    That is still evidence, but it is not a measurement, and an operator
    sizing a runner off ``limit=`` needs to know which one they are holding.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text(
        "def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: False)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)
    monkeypatch.setattr(
        indexer, "_cgroup_snapshot",
        lambda **_kwargs: {
            "current": None, "max": 1_000_000_000,
            "peak": None, "oom": None, "oom_kill": None,
            "cgroup_version": 2, "limit_state": "limited",
            "headroom_basis": "max_only", "source": "proc_self_cgroup",
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend spawned a producer")
    monkeypatch.setattr(indexer, "_index_command", forbidden)
    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    _result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert "cgroup_max=1000000000current=None" in reason
    assert "basis=max_only" in reason
    # The estimate is half the cgroup, not zero: the refusal is the floor's,
    # not a phantom "no memory at all".
    assert "limit=500000000" in reason


@pytest.mark.parametrize(
    ("limit_state", "basis"),
    [("unlimited", "uncapped"), ("unreadable", "no_ceiling_evidence")],
)
def test_batch_headroom_refusal_separates_no_ceiling_from_no_evidence(
    tmp_path, monkeypatch, limit_state, basis
):
    """Same budget, opposite facts - and the refusal is the only place it shows.

    Both states deliberately spend the flat 4 GiB cap: a Windows box and a
    host with no cgroup read as ``unreadable``, and lowering their budget
    would refuse legitimate local amends over a fact about the HOST rather
    than about memory. But an operator holding ``limit=4294967296`` needs to
    know whether the guard SAW an uncapped controller or saw nothing at all -
    one remedy is a cgroup that needs a limit, the other is a reader that
    needs a host it can read. ``uncapped`` used to name both.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text(
        "def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: False)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    # A floor above the flat cap: the only lane left is the refusal, which is
    # what carries the basis.
    monkeypatch.setattr(indexer, "_batch_amend_memory_floor", lambda *a: 10**12)
    monkeypatch.setattr(
        indexer, "_cgroup_snapshot",
        lambda **_kwargs: {
            "current": None, "max": None, "peak": None, "oom": None,
            "oom_kill": None, "cgroup_version": None,
            "limit_state": limit_state,
            # Derived, not hand-written: the point of the test is that the
            # producer of this string tells the two states apart.
            "headroom_basis": indexer._headroom_basis(limit_state, None),
            "source": "proc_self_cgroup",
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend spawned a producer")
    monkeypatch.setattr(indexer, "_index_command", forbidden)
    monkeypatch.setattr(indexer, "_incremental_index_command", forbidden)

    _result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert f"basis={basis}" in reason
    assert "cgroup_max=Nonecurrent=None" in reason
    assert f"limit={indexer._INDEX_RSS_LIMIT_BYTES}" in reason


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

    # A failed amend is a named refusal, not a from-scratch rebuild: the
    # certified parent still exists and the next trigger retries the amend.
    assert calls == [], "a refused amend must never fall back to a full rebuild"
    assert receipt.build_mode == "amend_refused"
    assert receipt.build_mode_reason.startswith("amend_failed:")
    assert not receipt.success


def test_a_failed_build_names_the_producers_own_error():
    """The same blindness on the path that runs first.

    TB2 run 35560215706, write-compressor: eight
    ``index_unavailable(error="nonzero_exit")`` rows while the sealed evidence
    beside them held the whole cause - a C file whose partial syntax tree was
    counted as a parse failure, making 0 of 1 files "parsed", so the producer
    refused a graph it had already extracted four functions into. Nothing in
    the journal named a file, a parser or a policy, and the run read as an
    unindexable repository for the rest of the episode.
    """
    evidence = {
        "status": "nonzero_exit",
        "exit_code": 1,
        "stderr_tail": (
            "Pass 2: parsing 1 files (2 workers)...\n"
            "  Parsed 0/1 files in 1ms (1 parse failures, 100.0%)\n"
            "  [WARN] parse failures (first 1):\n"
            "    - decomp.c: parser-incomplete syntax tree\n"
            "2026/09/21 05:48:40 INDEX FAILED: 0/1 files parsed - graph would "
            "be empty (sample: [decomp.c: parser-incomplete syntax tree])\n"
        ),
    }

    diagnostic = indexer._build_failure_diagnostic(evidence)

    assert diagnostic.startswith("nonzero_exit:exit=1")
    assert "INDEX FAILED: 0/1 files parsed" in diagnostic
    # One journal row per event, and the caller truncates at 300.
    assert "\n" not in diagnostic
    assert len(diagnostic) <= 300


def test_a_build_failure_without_evidence_still_says_so():
    """An absent seal is its own diagnosis, not a missing one."""
    assert indexer._build_failure_diagnostic(None) == (
        "gt-index failed without valid sealed evidence"
    )


def test_a_failed_amend_names_the_producers_own_error(tmp_path, monkeypatch):
    """``amend_failed:GT_INDEX_PROCESS_FAILED`` alone was a blind receipt.

    One paid run journaled 601 of them while the Go error that named the
    cause -- "batch parent parser row differs from its inventory:
    aiomonitor/types.py" -- sat unread in stderr_tail. The reason now
    carries the exit code and a bounded, single-line stderr tail so the
    refusal receipt names what actually failed.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability", lambda capability: False)
    monkeypatch.setattr(
        indexer, "_run_index_bounded",
        lambda *args, **kwargs: indexer.IndexProcessResult(
            success=False, status="nonzero_exit",
            error_code="GT_INDEX_PROCESS_FAILED", exit_code=1, elapsed_ms=37,
            stderr_tail=(
                "panic: batch parent parser row differs from its inventory: "
                "aiomonitor/types.py\n  goroutine 1 [running]:\n"),
        ),
    )

    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None
    assert reason.startswith("amend_failed:GT_INDEX_PROCESS_FAILED")
    assert "exit=1" in reason
    assert "batch parent parser row differs from its inventory" in reason
    # The journal is one row per event: the tail is folded to a single line.
    assert "\n" not in reason
    assert len(reason) <= 200
    assert rows and rows[0]["status"] == "nonzero_exit"


def test_amend_failure_reason_keeps_the_stderr_tail_end(tmp_path, monkeypatch):
    """The fatal line is the LAST thing the producer writes, never the first.

    stderr_tail is already the final 4 KiB of output; taking its head showed
    the Pass-1 banner while the actual "batch parent parser row differs from
    its inventory" sat unread at the end - run 34888711134 died four times on
    an enriched parent and the journal could only say "Found 609 source
    files". Keep the tail's end so the refusal names the cause.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text(
        "def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability", lambda capability: False)
    monkeypatch.setattr(
        indexer, "_run_index_bounded",
        lambda *args, **kwargs: indexer.IndexProcessResult(
            success=False, status="nonzero_exit",
            error_code="GT_INDEX_PROCESS_FAILED", exit_code=1, elapsed_ms=37,
            stderr_tail=(
                "Pass 1: discovering files in /testbed...\n"
                "  Found 609 source files\n"
                "  toml: 100 files\n  yaml: 53 files\n"
                "Pass 2: parsing 609 files (2 workers)...\n"
                "2026/09/14 20:21:39 batch insert nodes: batch parent parser "
                "row differs from its inventory: dynaconf/base.py\n"),
        ),
    )

    result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None
    assert reason.startswith("amend_failed:GT_INDEX_PROCESS_FAILED")
    assert "exit=1" in reason
    # The end of the tail carries the cause; the banner it starts with does not.
    assert "batch parent parser row differs from its inventory" in reason
    assert "Pass 1" not in reason


def test_a_failed_amend_without_stderr_still_names_exit(tmp_path, monkeypatch):
    """The diagnostic degrades, it does not pad: no stderr, no stderr key."""
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    # The edit moves the reuse key, or the existing revision short-circuits
    # as reusable before the producer is ever asked to amend.
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability", lambda capability: False)
    monkeypatch.setattr(
        indexer, "_run_index_bounded",
        lambda *args, **kwargs: indexer.IndexProcessResult(
            success=False, status="timeout", error_code="GT_INDEX_TIMEOUT",
            exit_code=-9, elapsed_ms=60000,
        ),
    )

    result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None
    assert reason == "amend_failed:GT_INDEX_TIMEOUT:exit=-9"


def test_a_failed_amend_persists_the_process_evidence(tmp_path, monkeypatch):
    """A refused amend leaves the same evidence a failed full build does.

    Run 34888711134 journaled four amend refusals whose only diagnostic was a
    truncated reason; the stderr tail, exit code and cgroup deltas that would
    have named the defect were dropped. The failed attempt now writes the
    same ``index-failure-resource.json`` + ``graph.failure.json`` receipts
    the full-build path writes.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text(
        "def one(): pass\ndef two(): pass\n", encoding="utf-8")

    stderr_tail = (
        "Pass 1: discovering files in /testbed...\n"
        "batch insert nodes: batch parent parser row differs from its "
        "inventory: dynaconf/base.py\n")
    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: True)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(
        indexer, "_run_index_bounded",
        lambda *args, **kwargs: indexer.IndexProcessResult(
            success=False, status="nonzero_exit",
            error_code="GT_INDEX_PROCESS_FAILED", exit_code=1, elapsed_ms=37,
            stderr_tail=stderr_tail,
            stderr_bytes=len(stderr_tail),
            stderr_sha256=hashlib.sha256(
                stderr_tail.encode("utf-8")).hexdigest(),
            cgroup_memory_current_after=123456,
            cgroup_oom_delta=0, cgroup_oom_kill_delta=0,
        ),
    )

    result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None and reason.startswith("amend_failed:")

    resource = list(tmp_path.rglob("index-failure-resource.json"))
    failure = list(tmp_path.rglob("graph.failure.json"))
    assert resource, "failed amend left no index-failure-resource receipt"
    assert failure, "failed amend left no graph.failure receipt"

    resource_doc = json.loads(resource[0].read_text(encoding="utf-8"))
    assert resource_doc["exit_code"] == 1
    assert resource_doc["stderr_tail"] == stderr_tail
    assert resource_doc["stderr_sha256"] == hashlib.sha256(
        stderr_tail.encode("utf-8")).hexdigest()
    assert resource_doc["cgroup_memory_current_after"] == 123456
    assert resource_doc["build_attempt_count"] == 1

    failure_doc = json.loads(failure[0].read_text(encoding="utf-8"))
    assert failure_doc["schema"] == "gt.graph_failure.v1"
    assert failure_doc["error_code"] == "GT_INDEX_PROCESS_FAILED"
    # The failure receipt binds the resource receipt by content, not by path.
    assert failure_doc["resource_evidence_sha256"] == hashlib.sha256(
        resource[0].read_bytes()).hexdigest()


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

    assert receipt.build_mode == "amend_refused"
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

    assert receipt.build_mode == "amend_refused"
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


# ---------------------------------------------------------------- write path




def _txn(post: str, paths: tuple[str, ...], complete: bool = True):
    return SimpleNamespace(
        post_revision=post, pre_revision="rev0",
        changed_paths=tuple(paths), complete=complete, omissions=(),
        transaction_sha256=f"txn-{post}", action_id=1,
        canonical_bytes=lambda: b"txn",
        changes=[
            SimpleNamespace(path=p, operation="modify", before_sha256="",
                            after_sha256="", after=b"x")
            for p in paths
        ],
    )


def _adopted_parent(adapter, tmp_path) -> Path:
    adapter.engine_state.bind_initial_source("rev0")
    parent = tmp_path / "graph.db"
    parent.write_bytes(b"parent-bytes")
    adapter.engine_state.publish_graph(
        graph_path=str(parent), graph_revision="g0", source_revision="rev0")
    adapter.graph_db = str(parent)
    return parent


def _fake_receipt(graph, **kwargs):
    return SimpleNamespace(
        success=True, graph_db=graph, graph_revision="g-next",
        source_revision=kwargs.get("source_revision", ""),
        embedding_state="skipped", error_type="", error_diagnostic="")


def test_edit_transaction_sync_amends_and_graph_stays_current(tmp_path, monkeypatch):
    """The invariant the whole phase exists for: graph_current is true THROUGH
    the edit, not stale until a worker lands a rebuild behind it."""
    adapter = _adapter(tmp_path)
    parent = _adopted_parent(adapter, tmp_path)
    assert adapter.engine_state.graph_current

    calls = {}
    def fake_amend(root, *, layout, parent_graph, changed_paths,
                   excluded_roots, diagnostics=None, source_revision=""):
        calls["paths"] = changed_paths
        calls["parent"] = str(parent_graph)
        calls["source_revision"] = source_revision
        return str(parent), "", ({"path": "a.py", "status": "ok"},)
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", fake_amend)
    monkeypatch.setattr(indexer, "_receipt_for_published_graph", _fake_receipt)

    adapter.record_edit_transaction(_txn("rev1", ("a.py",)))

    assert calls["paths"] == ("a.py",)
    assert calls["parent"] == str(parent)
    # The amend is built for the transaction's post revision; the indexer
    # hands it to gt-index as -source-revision when the producer declares it.
    assert calls["source_revision"] == "rev1"
    assert adapter.engine_state.graph_current
    assert adapter.graph_db == str(parent)
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    assert any(row.get("event") == "graph_sync_amend" and row.get("adopted")
               for row in rows)


def test_sync_amend_dirty_set_covers_earlier_unpatched_edits(tmp_path, monkeypatch):
    """A refused amend leaves its paths dirty; the next publish must cover
    them or the overlay clears over changes no graph ever saw."""
    adapter = _adapter(tmp_path)
    parent = _adopted_parent(adapter, tmp_path)
    state = {"fail": True}
    calls = {}

    def flaky(root, *, layout, parent_graph, changed_paths,
              excluded_roots, diagnostics=None, source_revision=""):
        calls["paths"] = changed_paths
        if state["fail"]:
            return None, "amend_failed:boom", ()
        return str(parent), "", ()
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", flaky)
    monkeypatch.setattr(indexer, "_receipt_for_published_graph", _fake_receipt)

    clock = {"now": 5000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    adapter.record_edit_transaction(_txn("rev0.5", ("b.py",)))
    assert calls["paths"] == ("b.py",)
    assert not adapter.engine_state.graph_current
    state["fail"] = False

    # The refused spawn opened the amend defer window; the retry lands on
    # the next transaction after it drains, still covering both edits.
    clock["now"] += 61.0
    adapter.record_edit_transaction(_txn("rev1", ("a.py",)))
    assert calls["paths"] == ("a.py", "b.py")
    assert adapter.engine_state.graph_current


def test_sync_amend_skips_when_unaccounted_changes_exist(tmp_path, monkeypatch):
    """Unenumerated omissions mean changes nobody enumerated; publishing
    would clear them on top of a graph that never covered them. A lone
    transaction_bytes_unavailable is different -- every dirty path is
    still enumerated -- and does not block the amend."""
    adapter = _adapter(tmp_path)
    _adopted_parent(adapter, tmp_path)
    incomplete = _txn("rev0.5", ("b.py",), complete=False)
    incomplete.omissions = ("unenumerated_paths",)
    adapter.record_edit_transaction(incomplete)

    calls = {}
    def forbidden(root, *, layout, parent_graph, changed_paths,
                  excluded_roots, diagnostics=None, source_revision=""):
        calls["paths"] = changed_paths
        return None, "", ()
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", forbidden)

    adapter.record_edit_transaction(_txn("rev1", ("a.py",)))
    assert "paths" not in calls


def test_boundary_amend_covers_byte_unrecorded_paths(tmp_path, monkeypatch):
    """mark_paths_dirty's byte-unavailable marking still enumerates every
    dirty path; the boundary amend covers them because the producer
    re-reads live bytes rather than trusting a transaction record."""
    adapter = _adapter(tmp_path)
    parent = _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("b.py",), revision="rev0.5")

    calls = {}
    def fake_amend(root, *, layout, parent_graph, changed_paths, **kwargs):
        calls["paths"] = changed_paths
        return str(parent), "", ()
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", fake_amend)
    monkeypatch.setattr(indexer, "_receipt_for_published_graph", _fake_receipt)

    assert adapter.refresh_graph() is True
    assert calls["paths"] == ("b.py",)
    assert adapter.engine_state.graph_current


def test_sync_amend_refusal_is_journaled_and_parent_kept(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    parent = _adopted_parent(adapter, tmp_path)

    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked",
        lambda *a, **k: (None, "amend_failed:disk_full", ()))

    adapter.record_edit_transaction(_txn("rev1", ("a.py",)))
    assert adapter.engine_state.graph_path == str(parent)
    assert not adapter.engine_state.graph_current
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    refused = [row for row in rows if row.get("event") == "graph_sync_amend_refused"]
    assert refused and "disk_full" in refused[0].get("reason", "")


# ------------------------------------------- deterministic-failure escalation
#
# A producer nonzero-exit (``amend_failed:*``) against a certified parent is
# deterministic: the parent's bytes are immutable, so the same amend on them
# can only fail the same way again. The serving boundary therefore escalates
# a bounded streak on the SAME parent to the recovery build that publishes a
# fresh base -- the failure mode this removes is the run that journaled 601
# refusal rows while its adopted graph stayed ~95% stale.


def _journal_rows(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text().splitlines()
    ]


def test_repeated_amend_failure_on_one_parent_escalates_to_a_recovery_build(
    tmp_path, monkeypatch
):
    """The second amend_failed on identical parent bytes buys a fresh base.

    The first refusal stays journaled-only -- it could still indict the
    dirty set rather than the chain's base. The second on the same immutable
    parent is the proof no amend can land, so the boundary journals the
    escalation and runs the one build that publishes a parent the chain can
    amend from again.
    """
    adapter = _adapter(tmp_path)
    parent = _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("a.py",), revision="rev1")

    amends: list[str] = []

    def failed(root, *, layout, parent_graph, changed_paths, **kwargs):
        amends.append(str(parent_graph))
        return None, ("amend_failed:GT_INDEX_PROCESS_FAILED:exit=1:"
                      "stderr=batch parent parser row differs"), ()
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", failed)

    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    builds: list[str] = []

    def fake_build(root, **kwargs):
        builds.append(str(root))
        return indexer.IndexBuildReceipt(
            indexer.IndexBuildStatus.BUILT, graph_db=str(rebuilt),
            graph_revision="g-recovery", source_revision="rev1",
        )
    monkeypatch.setattr(indexer, "ensure_index_with_receipt", fake_build)

    clock = {"now": 5000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    # First refusal on these parent bytes: journaled, not escalated.
    assert adapter.refresh_graph() is False
    assert builds == []

    # Second consecutive failure on the same parent - once the spawn window
    # the first dead producer opened has drained: the chain cannot heal
    # itself, so the recovery build runs inline and publishes a new parent.
    clock["now"] += 61.0
    assert adapter.refresh_graph() is True
    assert builds == [adapter.repo_root]
    assert amends == [str(parent), str(parent)]
    assert adapter.engine_state.graph_current
    assert adapter.engine_state.graph_path == str(rebuilt)

    rows = _journal_rows(adapter)
    events = [row.get("event") for row in rows]
    refused = [row for row in rows if row.get("event") == "graph_boundary_amend_refused"]
    escalated = [row for row in rows if row.get("event") == "graph_amend_escalated"]
    recovery = [row for row in rows if row.get("event") == "graph_recovery"]
    assert len(refused) == 2 and len(escalated) == 1
    # The refusal is still journaled; the escalation row lands after the
    # last refusal and before the recovery row it bought.
    last_refusal = max(
        index for index, event in enumerate(events)
        if event == "graph_boundary_amend_refused"
    )
    assert events.index("graph_amend_escalated") == last_refusal + 1
    assert events.index("graph_recovery") > events.index("graph_amend_escalated")
    assert escalated[0]["parent_graph"] == str(parent)
    assert escalated[0]["streak"] == MiniSweAdapter.AMEND_FAILURE_ESCALATION_STREAK
    assert "GT_INDEX_PROCESS_FAILED" in escalated[0]["reason"]
    assert recovery[0]["adopted"] is True


def test_a_first_amend_failure_never_escalates(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("a.py",), revision="rev1")

    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked",
        lambda *a, **k: (None, "amend_failed:GT_INDEX_PROCESS_FAILED:exit=1", ()))

    def forbidden(*args, **kwargs):
        pytest.fail("a first amend_failed refusal ran a recovery build")
    monkeypatch.setattr(indexer, "ensure_index_with_receipt", forbidden)

    assert adapter.refresh_graph() is False
    rows = _journal_rows(adapter)
    assert any(row.get("event") == "graph_boundary_amend_refused" for row in rows)
    assert not any(row.get("event") == "graph_amend_escalated" for row in rows)


@pytest.mark.parametrize("reason", [
    "producer_lacks_amend_capability",
    "no_amendable_paths",
    "changed_path_outside_repository",
    "incremental_publication_failed",
])
def test_refusals_a_rebuild_cannot_fix_never_escalate(
    tmp_path, monkeypatch, reason
):
    """A rebuild grants no capability and moves no path into the repository.

    These refusals stay journaled-only forever: escalating them would
    journal a recovery that fixes nothing at the price of a full build.
    """
    adapter = _adapter(tmp_path)
    _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("a.py",), revision="rev1")

    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked",
        lambda *a, **k: (None, reason, ()))

    def forbidden(*args, **kwargs):
        pytest.fail(f"{reason} escalated to a recovery build")
    monkeypatch.setattr(indexer, "ensure_index_with_receipt", forbidden)

    for _ in range(MiniSweAdapter.AMEND_FAILURE_ESCALATION_STREAK + 1):
        assert adapter.refresh_graph() is False

    rows = _journal_rows(adapter)
    refused = [row for row in rows if row.get("event") == "graph_boundary_amend_refused"]
    assert len(refused) == MiniSweAdapter.AMEND_FAILURE_ESCALATION_STREAK + 1
    assert not any(row.get("event") == "graph_amend_escalated" for row in rows)


def test_the_streak_does_not_follow_the_graph_to_a_new_parent(
    tmp_path, monkeypatch
):
    """Consecutive is per parent bytes, not per adapter.

    A refusal against a different parent belongs to a different chain: the
    streak the old parent earned cannot carry onto the graph that replaced
    it.
    """
    adapter = _adapter(tmp_path)
    first = _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("a.py",), revision="rev1")

    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked",
        lambda *a, **k: (None, "amend_failed:GT_INDEX_PROCESS_FAILED:exit=1", ()))

    def forbidden(*args, **kwargs):
        pytest.fail("a first refusal on a NEW parent escalated an old streak")
    monkeypatch.setattr(indexer, "ensure_index_with_receipt", forbidden)

    clock = {"now": 5000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    assert adapter.refresh_graph() is False  # streak {old parent: 1}

    # A new parent lands by other means -- anything the authority names.
    other = tmp_path / "other.db"
    other.write_bytes(b"other")
    adapter.engine_state.publish_graph(
        graph_path=str(other), graph_revision="g1", source_revision="rev1")
    adapter.engine_state.mark_paths_dirty(("b.py",), revision="rev2")

    # A first refusal on the new parent is streak 1, not the old parent's 2.
    clock["now"] += 61.0  # past the spawn window the first refusal opened
    assert adapter.refresh_graph() is False
    rows = _journal_rows(adapter)
    assert not any(row.get("event") == "graph_amend_escalated" for row in rows)
    refused = [row for row in rows if row.get("event") == "graph_boundary_amend_refused"]
    assert [row["parent_graph"] for row in refused] == [str(first), str(other)]


def test_a_successful_amend_resets_the_streak(tmp_path, monkeypatch):
    """An amend that lands proves the chain still heals: the count restarts."""
    adapter = _adapter(tmp_path)
    _adopted_parent(adapter, tmp_path)
    adapter.engine_state.mark_paths_dirty(("a.py",), revision="rev1")

    published = tmp_path / "published.db"
    published.write_bytes(b"amended")
    state = {"fail": True}

    def flaky(root, *, layout, parent_graph, changed_paths, **kwargs):
        if state["fail"]:
            return None, "amend_failed:GT_INDEX_PROCESS_FAILED:exit=1", ()
        return str(published), "", ({"path": "a.py", "status": "completed"},)
    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", flaky)
    monkeypatch.setattr(indexer, "_receipt_for_published_graph", _fake_receipt)

    def forbidden(*args, **kwargs):
        pytest.fail("a post-success first refusal escalated an old streak")
    monkeypatch.setattr(indexer, "ensure_index_with_receipt", forbidden)

    clock = {"now": 5000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    assert adapter.refresh_graph() is False   # streak 1 on the old parent
    state["fail"] = False
    # The overlay is still dirty from the refusal; the next boundary past
    # the spawn window amends it.
    clock["now"] += 61.0
    assert adapter.refresh_graph() is True    # landed: streak cleared
    assert adapter.engine_state.graph_current

    state["fail"] = True
    adapter.engine_state.mark_paths_dirty(("b.py",), revision="rev2")
    assert adapter.refresh_graph() is False   # streak 1 again -- not 2
    rows = _journal_rows(adapter)
    assert not any(row.get("event") == "graph_amend_escalated" for row in rows)


# ------------------------------------------------- batch amend memory floor


def _batch_parent_manifest(parent: Path, nodes: int) -> None:
    manifest = parent.with_suffix(".manifest.json")
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["indexed_node_count"] = nodes
    manifest.write_text(json.dumps(doc), encoding="utf-8")


def _floor_for(tmp_path: Path, nodes: int, *, graph_nodes: int = 0) -> int:
    # A fresh pair per call: sqlite keeps the file open on Windows long enough
    # that reusing one name across a parametrised table fails on unlink.
    stem = f"parent-{nodes}-{graph_nodes}"
    graph = tmp_path / f"{stem}.db"
    con = sqlite3.connect(graph)
    try:
        con.execute("create table if not exists nodes (id integer)")
        con.execute("delete from nodes")
        con.executemany(
            "insert into nodes values (?)", [(i,) for i in range(graph_nodes)])
        con.commit()
    finally:
        con.close()
    manifest = tmp_path / f"{stem}.manifest.json"
    manifest.write_text(
        json.dumps({"indexed_node_count": nodes} if nodes else {}), encoding="utf-8")
    return indexer._batch_amend_memory_floor(graph, manifest)


def test_batch_amend_floor_scales_with_the_parent_graph(tmp_path, monkeypatch):
    """The floor is the measured pipeline envelope: base + per-node slope.

    The base is no longer flat. It was calibrated on a 15,600-node parent and
    charged in full to every parent, so an 11-node one was priced at 170 MiB -
    run 35262214538 refused nine amends at need=178438144 against a cgroup
    whose whole remaining headroom was smaller than that base alone. Below the
    calibration parent the base is prorated, with a floor that stops the
    prediction collapsing to nothing.
    """
    assert _floor_for(tmp_path, 93_600) == (
        indexer._AMEND_MEMORY_FLOOR_BASE_BYTES
        + 93_600 * indexer._AMEND_MEMORY_FLOOR_PER_NODE_BYTES
    )
    assert _floor_for(tmp_path, 93_600) > 1024 * 1024 * 1024  # >1 GiB, unchanged

    # Manifest without the field falls back to counting the graph itself. The
    # base is prorated there too: 50 nodes is far below the calibration
    # parent, so the minimum carries it.
    assert _floor_for(tmp_path, 0, graph_nodes=50) == (
        indexer._AMEND_MEMORY_FLOOR_MIN_BYTES
        + 50 * indexer._AMEND_MEMORY_FLOOR_PER_NODE_BYTES
    )


@pytest.mark.parametrize(
    ("nodes", "expected_base"),
    [
        (0, "min"),        # unreadable parent scale
        (11, "min"),       # the parent run 35262214538 priced at 170 MiB
        (1_000, "min"),
        (15_600, "full"),  # the calibration parent itself
        (40_000, "full"),  # above calibration the base stops growing
    ],
)
def test_batch_amend_floor_prorates_the_base_below_the_calibration_parent(
    tmp_path, nodes, expected_base
):
    """Five points on the curve, including the one the live run refused.

    ``nodes=0`` is an unreadable parent, ``11`` is the parent whose 170 MiB
    price produced need=178438144 in run 35262214538, ``15600`` is the
    measured calibration parent and ``40000`` proves the base is capped there
    rather than extrapolated past the measurement.
    """
    base = (
        indexer._AMEND_MEMORY_FLOOR_MIN_BYTES if expected_base == "min"
        else indexer._AMEND_MEMORY_FLOOR_BASE_BYTES
    )
    expected = base + nodes * indexer._AMEND_MEMORY_FLOOR_PER_NODE_BYTES
    assert _floor_for(tmp_path, nodes) == expected
    # Monotonic, and never below the launch floor a refused build already uses.
    assert expected >= indexer._AMEND_MEMORY_FLOOR_MIN_BYTES


def test_batch_amend_floor_is_monotonic_and_continuous_at_the_calibration_knee(
    tmp_path,
):
    """A prorated base must not make a bigger parent cheaper than a smaller one.

    The knee is where the prorated base reaches the minimum: at 16 KiB/node
    the per-node term alone would otherwise let the curve step backwards.
    """
    floors = [_floor_for(tmp_path, nodes)
              for nodes in (0, 11, 1_000, 5_800, 5_900, 15_599, 15_600, 40_000)]
    assert floors == sorted(floors)
    assert len(set(floors)) == len(floors), "the curve must be strictly rising"


def test_batch_amend_floor_min_is_not_below_the_launch_floor(tmp_path):
    """The smallest predicted amend must still clear the launch floor.

    ``_run_index_bounded`` refuses any build under 64 MiB. A floor beneath
    that would predict a producer can run in memory the guard would not even
    launch it with, so the two constants are pinned equal here on purpose.
    """
    assert indexer._AMEND_MEMORY_FLOOR_MIN_BYTES == 64 * 1024 * 1024
    assert _floor_for(tmp_path, 0) >= indexer._AMEND_MEMORY_FLOOR_MIN_BYTES


def test_batch_amend_refuses_before_spawn_below_the_floor(tmp_path, monkeypatch):
    """Gate-one shipped two mid-flight SIGKILLs on single-file dirty sets.

    The fix is not a kinder kill: it is refusing before launch. The reason is
    the typed scheduling code, NOT ``amend_failed:*`` — no producer ran, so
    nothing died, and the strict gate's producer-death count stays honest.
    """
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: False)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(
        indexer, "_effective_index_memory_limit", lambda snapshot: 32 * 1024 * 1024)

    def forbidden(*args, **kwargs):
        pytest.fail("an amend whose floor exceeds the limit must not spawn a producer")
    monkeypatch.setattr(indexer, "_run_index_bounded", forbidden)

    result, reason, rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert result is None
    assert reason.startswith(
        "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:")
    assert not reason.startswith("amend_failed:")
    assert rows == ()


def test_batch_amend_above_the_floor_still_launches(tmp_path, monkeypatch):
    """The floor refuses only what would die; headroom launches as before."""
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(indexer, "_producer_supports_incremental_amend", lambda: False)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability == indexer.BATCH_AMEND_CAPABILITY)
    monkeypatch.setattr(
        indexer, "_effective_index_memory_limit",
        lambda snapshot: 8 * 1024 * 1024 * 1024)

    spawned: list[str] = []

    def fake_run(*args, **kwargs):
        spawned.append("ran")
        return indexer.IndexProcessResult(
            success=False, status="nonzero_exit",
            error_code="GT_INDEX_PROCESS_FAILED", exit_code=1, elapsed_ms=5)
    monkeypatch.setattr(indexer, "_run_index_bounded", fake_run)

    result, reason, _rows = indexer._ensure_index_incremental_unlocked(
        str(root), layout=layout, parent_graph=parent, changed_paths=("app.py",))

    assert spawned == ["ran"], "a headroom-satisfying amend must still launch"
    assert result is None and reason.startswith("amend_failed:GT_INDEX_PROCESS_FAILED")


@pytest.mark.skipif(
    not os.environ.get("GT_INDEX_BINARY"),
    reason="real producer binary required (set GT_INDEX_BINARY)",
)
def test_a_freshly_produced_graph_passes_preflight_and_ranks(tmp_path):
    """The producer→consumer contract on a real built artifact.

    Gate-one (35056493769) shipped a graph whose nodes_fts carried a dead
    index generation (docsize 190,953 vs nodes 95,644); under Python's
    SQLite, bm25() returned NULL for terms whose doclist exceeded the
    claimed row count and lexical_rank crashed float(None). The producer
    now rebuilds and verifies the index at the publication boundary. This
    test runs the real producer and asserts the consumer-visible
    invariants end to end: adoption preflight passes, docsize parity
    holds, every matched row scores a finite bm25, and the consumer ranks.
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
    build = indexer.subprocess.run(
        indexer._index_command(binary, str(root), str(graph)),
        check=False, capture_output=True)
    assert build.returncode == 0, build.stderr

    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok, f"a freshly produced graph must pass adoption preflight: {reason}"

    with sqlite3.connect(graph) as con:
        nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        docsize = con.execute(
            "SELECT COUNT(*) FROM nodes_fts_docsize").fetchone()[0]
        assert docsize == nodes, (
            f"index desynced at publication: docsize={docsize} nodes={nodes}")
        null_scores = con.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT bm25(nodes_fts) AS s FROM nodes_fts"
            "  WHERE nodes_fts MATCH 'save OR run'"
            ") WHERE s IS NULL").fetchone()[0]
        assert null_scores == 0

    ranking = retrieval.lexical_rank(graph, "save OR run", k=10)
    assert ranking.available
    names = {entry.snippet for entry in ranking.ranking}
    assert {"save", "run"} <= names


# ------------------------------------------------- graph source-revision flag
#
# Cross-stream contract: the harness passes the workspace revision it builds
# for as gt-index -source-revision on full builds, batch amends and -file
# incrementals, but only to a producer that declares source_revision_meta_v1;
# an older producer would reject the unknown flag.


def _revision_recording_producer(path: Path) -> None:
    """A fake gt-index that persists -source-revision the way W2's producer does."""
    path.write_text(
        "import sqlite3, sys\n"
        "output = sys.argv[sys.argv.index('-output') + 1]\n"
        "with sqlite3.connect(output) as c:\n"
        "    c.execute('create table if not exists project_meta (key text, value text)')\n"
        "    c.execute('create table if not exists file_hashes (path text)')\n"
        "    c.execute('create table if not exists nodes (id integer, file_path text)')\n"
        "    c.execute(\"insert into nodes values (1, 'app.py')\")\n"
        "    c.execute(\"insert into file_hashes values ('app.py')\")\n"
        "    if '-source-revision' in sys.argv:\n"
        "        value = sys.argv[sys.argv.index('-source-revision') + 1]\n"
        "        c.execute(\"insert into project_meta values ('source_revision', ?)\", (value,))\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("declared", [True, False])
def test_source_revision_flag_follows_the_declared_capability(monkeypatch, declared):
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: declared and capability == indexer.SOURCE_REVISION_CAPABILITY,
    )
    argv = indexer._source_revision_argv("rev-7")
    assert argv == (["-source-revision", "rev-7"] if declared else [])
    assert indexer._source_revision_argv("") == []


@pytest.mark.parametrize("declared", [True, False])
def test_full_build_persists_the_workspace_revision_only_when_declared(
    tmp_path, monkeypatch, declared
):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    fake = tmp_path / "fake-index.py"
    _revision_recording_producer(fake)
    argvs: list[list[str]] = []

    def command(binary, r, output):
        argv = [binary, str(fake), "-root", r, "-output", output]
        argvs.append(argv)
        return argv

    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(indexer, "_index_command", command)
    monkeypatch.setattr(
        indexer, "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: declared and capability == indexer.SOURCE_REVISION_CAPABILITY,
    )

    receipt = indexer.ensure_index_with_receipt(
        root, layout=adapter.engine_state.layout, source_revision="ws-rev-1",
    )

    assert receipt.success, receipt.error_type
    with sqlite3.connect(f"file:{Path(receipt.graph_db).as_posix()}?mode=ro", uri=True) as c:
        stored = dict(c.execute("SELECT key, value FROM project_meta").fetchall())
    if declared:
        assert stored.get("source_revision") == "ws-rev-1"
    else:
        assert "source_revision" not in stored
        assert all("-source-revision" not in argv for argv in argvs)


def test_batch_amend_passes_the_workspace_revision(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")
    calls: list[list[str]] = []
    script = tmp_path / "batch.py"
    script.write_text(
        "import shutil,sys\nshutil.copyfile(sys.argv[sys.argv.index('-amend-parent') + 1], sys.argv[1])\n",
        encoding="utf-8",
    )

    def command(binary, source, output):
        return [binary, str(script), output]

    real_popen = indexer.subprocess.Popen

    def recording_popen(argv, *args, **kwargs):
        if str(script) in [str(item) for item in argv]:
            calls.append([str(item) for item in argv])
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(indexer.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability in {
            indexer.BATCH_AMEND_CAPABILITY, indexer.SOURCE_REVISION_CAPABILITY,
        },
    )
    monkeypatch.setattr(indexer, "_index_command", command)

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="ws-rev-2",
    )

    assert receipt.success, receipt.error_type
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("-source-revision") + 1] == "ws-rev-2"


def test_file_incremental_passes_the_workspace_revision(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    root = Path(adapter.repo_root)
    (root / "app.py").write_text("def one(): pass\n", encoding="utf-8")
    layout = adapter.engine_state.layout
    parent = _publish_parent(root, layout, monkeypatch)
    (root / "app.py").write_text("def one(): pass\ndef two(): pass\n", encoding="utf-8")
    amend = tmp_path / "fake-amend.py"
    amend.write_text(
        "import json, sqlite3, sys\n"
        "output = sys.argv[sys.argv.index('-output') + 1]\n"
        "relpath = sys.argv[sys.argv.index('-file') + 1]\n"
        "revision = sys.argv[sys.argv.index('-source-revision') + 1]\n"
        "with sqlite3.connect(output) as c:\n"
        "    c.execute(\"insert into project_meta values ('source_revision', ?)\", (revision,))\n"
        "print(json.dumps({'file': relpath, 'nodes_replaced': 1, 'inserted': 0,\n"
        "                  'updated': 1, 'removed': 0, 'symbols_reminted': 1,\n"
        "                  'symbols_removed': 0, 'short_circuited': False}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        indexer, "_incremental_index_command",
        lambda binary, r, output, relpath: [
            binary, str(amend), "-root", r, "-output", output, "-file", relpath,
        ],
    )
    monkeypatch.setattr(
        indexer, "_producer_supports_amend_capability",
        lambda capability: capability in {
            indexer.AMEND_CAPABILITY, indexer.SOURCE_REVISION_CAPABILITY,
        },
    )

    receipt = indexer.refresh_index_files(
        root, parent, ("app.py",), layout=layout, source_revision="ws-rev-3",
    )

    assert receipt.success, receipt.error_type
    with sqlite3.connect(f"file:{Path(receipt.graph_db).as_posix()}?mode=ro", uri=True) as c:
        stored = dict(c.execute("SELECT key, value FROM project_meta").fetchall())
    assert stored.get("source_revision") == "ws-rev-3"
