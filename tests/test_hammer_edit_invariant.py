"""Hammer-edit invariant: the adopted graph always names the live source.

Run 34701523365's failure was a reclamation race: an adopted parent was
pruned while still named, later builds lost their parent and fell into
full rebuilds that never landed. The invariant asserted here is the one
that incident violated and the redesign claims to uphold:

- after every edit, whatever ``engine_state.graph_path`` names is a file
  that exists and whose ``graph_source_revision`` equals
  ``source_revision`` - or ``graph_current`` is honestly false;
- a build that finishes against a superseded revision can never publish;
- reclamation can never delete a revision the authority still names;
- the dirty window is visible as ``CURRENT_PARTIAL`` with the dirty set
  masked, never as a silent stale read.

These tests substitute the producer with a deterministic fake amender -
the producer's own amend is covered by the installed-Linux suite; what is
under test is the coordination plane (epochs, CAS, reclaim protection)
that the incident proved broken.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.engine_state import QueryCompleteness
from gt_engine.indexer import (
    GRAPH_SCHEMA_VERSION,
    INDEX_RESOURCE_SCHEMA,
    _graph_phase_metadata,
    _sealed_json,
)
from tests.test_scoped_merge import (
    _adapter_with_live,
    _build_base,
    _journal_events,
)


def _change(path: str, content: bytes) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        operation="modify",
        before_sha256=hashlib.sha256(b"before").hexdigest(),
        after_sha256=hashlib.sha256(content).hexdigest(),
        before=b"before",
        after=content,
    )


def _transaction(post_revision: str, paths: list[str], *,
                 action_id: int = 1, complete: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        canonical_bytes=lambda: post_revision.encode(),
        post_revision=post_revision,
        pre_revision=f"pre-{post_revision}",
        changed_paths=list(paths),
        changes=[_change(path, f"content:{post_revision}".encode())
                 for path in paths],
        complete=complete,
        omissions=[],
        transaction_sha256=hashlib.sha256(post_revision.encode()).hexdigest(),
        action_id=action_id,
    )


def _certify_revision(graph_db: Path, *, source_revision: str,
                      repo_root: Path) -> None:
    """Write the manifest + sealed resource the receipt path requires."""
    root_sha = hashlib.sha256(
        str(repo_root.resolve()).encode()).hexdigest()
    resource = graph_db.parent / "index-resource.json"
    _sealed_json(resource, {
        "schema": INDEX_RESOURCE_SCHEMA,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": "2" * 64,
        "producer_binary_sha256": "3" * 64,
        "status": "completed", "error_code": "", "exit_code": 0,
        "memory_evidence": False,
    }, "evidence_sha256")
    graph_sha = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    manifest = {
        "schema": GRAPH_SCHEMA_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": "2" * 64,
        "source_revision": source_revision,
        "graph_revision": graph_sha,
        "graph_sha256": graph_sha,
        "graph_bytes": graph_db.stat().st_size,
        "index_resource_sha256": hashlib.sha256(
            resource.read_bytes()).hexdigest(),
        "binary_sha256": "3" * 64,
        "binary_certified": True,
        **_graph_phase_metadata(graph_db),
    }
    graph_db.with_suffix(".manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def fake_amend(monkeypatch):
    """Deterministic producer stand-in for the sync amend path.

    Each call copies the parent into a fresh revisions dir, stamps one
    marker row so the revision is distinct, certifies it, and returns the
    publish tuple the real function returns. The serial counter doubles
    as a call log.
    """
    from gt_engine import indexer

    calls: list[tuple[str, tuple[str, ...]]] = []

    def amend(root, *, layout, parent_graph, changed_paths,
              excluded_roots=(), diagnostics=None):
        calls.append((str(parent_graph), tuple(changed_paths)))
        n = len(calls)
        dest = layout.graph_root / "revisions" / f"amend-{n}"
        dest.mkdir(parents=True)
        out = dest / "graph.db"
        out.write_bytes(Path(parent_graph).read_bytes())
        with closing(sqlite3.connect(out)) as db:
            db.execute(
                "INSERT INTO project_meta (key,value) VALUES (?,?)",
                (f"amend-{n}", "x"),
            )
            db.commit()
        _certify_revision(out, source_revision=f"r{n + 1}",
                          repo_root=Path(root))
        return (
            str(out),
            "",
            tuple({"path": p, "amended": True} for p in changed_paths),
        )

    monkeypatch.setattr(indexer, "_ensure_index_incremental_unlocked", amend)
    return calls


def test_rapid_sequential_edits_stay_current(tmp_path, fake_amend):
    adapter, live = _adapter_with_live(tmp_path)
    try:
        for index in range(1, 16):
            revision = f"r{index + 1}"
            adapter.record_edit_transaction(
                _transaction(revision, [f"file{index}.py"],
                             action_id=index)
            )
            assert adapter.engine_state.graph_current, (
                f"graph went stale after edit {index}"
            )
            assert adapter.engine_state.graph_source_revision == revision
            assert adapter.engine_state.source_revision == revision
            assert Path(adapter.engine_state.graph_path).is_file(), (
                f"adopted graph missing after edit {index}"
            )
        events = _journal_events(adapter)
        refusals = [
            row for row in events
            if row.get("event") == "graph_sync_amend_refused"
        ]
        amends = [
            row for row in events if row.get("event") == "graph_sync_amend"
        ]
        assert refusals == []
        assert len(amends) == 15
        assert all(row.get("adopted") for row in amends)
        assert len(fake_amend) == 15
    finally:
        adapter.close_graph_lifecycle()


def test_superseded_produced_graph_never_publishes_and_named_survives(
    tmp_path, fake_amend, monkeypatch
):
    """The arktype invariant on the adoption path.

    A receipt produced against r2 arrives after an edit moved the source
    to r3 and the sync amend already published it. publish_graph refuses
    the stale artifact, the adopted r3 graph survives, and the refused
    produce is retained as the certified fallback parent - reclaim only
    ever deletes what no authority names.
    """
    adapter, live = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    produced_dir = layout.graph_root / "revisions" / "stale-build"
    produced_dir.mkdir(parents=True)
    produced = produced_dir / "graph.db"
    produced.write_bytes(live.read_bytes())

    try:
        adapter.record_edit_transaction(
            _transaction("r3", ["file1.py"], action_id=2)
        )
        assert adapter.engine_state.graph_current
        adopted_path = adapter.engine_state.graph_path
        assert "amend-" in adopted_path

        stale = SimpleNamespace(
            success=True, graph_db=str(produced), graph_revision="rev-stale",
            source_revision="source-r2", build_mode="incremental",
            build_mode_reason="", embedding_state="skipped",
        )
        adapter._adopt_graph_receipt(stale, event="graph_boundary_amend")

        assert adapter.engine_state.graph_path == adopted_path
        assert Path(adopted_path).is_file()
        assert adapter.engine_state.graph_current
        assert adapter.engine_state.graph_source_revision == "r3"
        # The refused produce survives as the named fallback parent; the
        # next boundary amend can build on it rather than recovering
        # from scratch.
        assert produced.exists()
        assert adapter._unadopted_graph == (str(produced), "rev-stale")
    finally:
        adapter.close_graph_lifecycle()


def test_dirty_window_is_partial_and_masked_never_stale_silent(
    tmp_path, fake_amend
):
    """An over-cap edit cannot sync: the graph is honestly PARTIAL, the
    dirty set is masked, and no query sees stale rows as current."""
    adapter, live = _adapter_with_live(tmp_path)
    try:
        many = [f"dir/file{i}.py" for i in range(20)]
        adapter.record_edit_transaction(
            _transaction("r2", many, action_id=3)
        )
        assert not adapter.engine_state.graph_current
        snapshot = adapter.engine_state.query_snapshot()
        assert snapshot.completeness is QueryCompleteness.CURRENT_PARTIAL
        assert snapshot.graph_path == ""
        assert set(snapshot.masked_paths) == set(many)
        assert len(fake_amend) == 0, (
            "the over-cap edit must not pretend a synchronous amend"
        )
        # The named graph is still the pre-edit artifact and still exists.
        assert Path(adapter.engine_state.graph_path).is_file()

        # The serving boundary completes the deferred amend inline: the
        # dirty window closes before any consumer can observe the graph
        # behind the source. This is the post-coordinator contract --
        # staleness is a boundary, not a queue position.
        assert adapter.refresh_graph(phase="test") is True
        assert adapter.engine_state.graph_current
        assert len(fake_amend) == 1
        assert set(fake_amend[0][1]) == set(many)
        boundary = [
            row for row in _journal_events(adapter)
            if row.get("event") == "graph_boundary_amend"
        ]
        assert boundary and boundary[-1]["adopted"] is True
    finally:
        adapter.close_graph_lifecycle()


def test_salvage_superseded_mid_flight_stays_unpublished(
    tmp_path, fake_amend, monkeypatch
):
    """A salvage merge that completes after an edit moved the live graph
    is journaled as superseded and the adopted graph is untouched."""
    from gt_engine.graph_coordinator import GraphBuildArtifact
    from tests.test_scoped_merge import _terminal

    monkeypatch.setattr(
        "groundtruth.resolve._rebuild_closure", lambda db_path: True
    )

    adapter, live = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    base = tmp_path / "base.db"
    _build_base(base)
    cand_dir = layout.graph_root / "enrichments" / "lsp-hammer"
    cand_dir.mkdir(parents=True)
    cand = cand_dir / "graph.db"
    cand.write_bytes(base.read_bytes())
    with closing(sqlite3.connect(cand)) as db:
        db.execute(
            "UPDATE nodes SET candidate_state='selected' WHERE id=4"
        )
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (90,4,3,'SELECTED_TARGET','clean.py',"
            "5,'lsp',1.0,'lsp','cs:4')"
        )
        db.commit()

    adapter._edit_epoch = 4
    adapter._lsp_epochs["task-h"] = 3
    adapter._lsp_requests["task-h"] = SimpleNamespace(
        candidate_path=str(cand),
        repository_root=str(tmp_path / "repo"),
        source_revision="source-r1",
        graph_revision="rev-base",
    )
    adapter._record_lsp_terminal(
        SimpleNamespace(source_revision="source-r1"),
        GraphBuildArtifact(True, str(base), "rev-base"),
        _terminal("task-h", cand),
        "obsolete",
    )
    assert "lsp_salvage:task-h" in adapter._wait_scheduler.pending_names()

    adapter.provider_wait_begin()
    # The hammer edit lands while the merge is queued or running: the
    # drain-side CAS is the only thing standing between the merged
    # artifact and the authority slot.
    adapter.record_edit_transaction(
        _transaction("r3", ["other.py"], action_id=9)
    )
    deadline = time.monotonic() + 15
    while adapter._wait_scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.02)
    adapter.provider_wait_end()
    adapter._wait_scheduler.close()

    assert adapter.engine_state.graph_current
    assert adapter.engine_state.graph_source_revision == "r3"
    assert Path(adapter.engine_state.graph_path).is_file()
    outcomes = [
        row for row in _journal_events(adapter)
        if row.get("event") == "lsp_salvage"
    ]
    assert outcomes, "the salvage result was never journaled"
    assert outcomes[-1]["outcome"] in {
        "superseded", "live_not_current", "candidate_missing",
        "input_missing",
    }, outcomes[-1]
    adapter.close_graph_lifecycle()


def test_unenumerated_edit_blocks_sync_amend_and_recovers_at_boundary(
    tmp_path, fake_amend, monkeypatch
):
    """An incomplete transaction poisons the sync amend: the dirty set is
    unknowable, so nothing publishes on faith. The serving boundary
    resyncs with the one build whose coverage does not depend on the
    dirty set -- a whole-tree recovery -- and journals the reason."""
    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus

    adapter, live = _adapter_with_live(tmp_path)
    try:
        incomplete = _transaction("r3", ["a.py"], action_id=4,
                                  complete=False)
        incomplete.omissions = ["unenumerated_paths"]
        adapter.record_edit_transaction(incomplete)
        assert not adapter.engine_state.graph_current
        assert len(fake_amend) == 0
        snapshot = adapter.engine_state.query_snapshot()
        assert snapshot.completeness is QueryCompleteness.CURRENT_PARTIAL
        assert snapshot.omissions

        recovered = tmp_path / "recovered.db"
        recovered.write_bytes(b"recovered")
        monkeypatch.setattr(
            "gt_engine.indexer.ensure_index_with_receipt",
            lambda root, **kwargs: IndexBuildReceipt(
                IndexBuildStatus.BUILT, graph_db=str(recovered),
                graph_revision="d" * 64, analysis_state="complete",
                source_revision="r3",
            ),
        )
        assert adapter.refresh_graph(phase="test") is True
        assert adapter.engine_state.graph_current
        resync = [
            row for row in _journal_events(adapter)
            if row.get("event") == "graph_resync_incomplete"
        ]
        assert resync and "unenumerated_paths" in resync[-1]["omissions"]
        recovery = [
            row for row in _journal_events(adapter)
            if row.get("event") == "graph_recovery"
        ]
        assert recovery and recovery[-1]["adopted"]
        assert len(fake_amend) == 0
    finally:
        adapter.close_graph_lifecycle()


def test_arktype_incident_sequence_produces_no_rebuild_storm(
    tmp_path, fake_amend
):
    """Replay the 277-transaction edit sequence from run 34701523365.

    In the incident this sequence produced 363 scheduled builds, 646
    producer invocations and ~82 minutes of rebuilds that never landed:
    every asynchronous result arrived already superseded, and reclamation
    deleted the parent the next build still named.

    Every one of the 277 transactions dirtied at most 11 paths - inside
    the synchronous amend cap - so on the write-path design the identical
    sequence is 86 deltas applied inside the edit boundary and zero
    asynchronous builds at all. The graph never leaves the authority
    slot empty and reclamation has nothing to race.
    """
    fixture = (
        Path(__file__).parent / "fixtures"
        / "arktype_34701523365_edit_sequence.json"
    )
    edits = json.loads(fixture.read_text(encoding="utf-8"))["edits"]
    assert len(edits) == 277

    adapter, live = _adapter_with_live(tmp_path)
    # The incident's revisions are content hashes: a transaction that
    # moved no bytes repeats the prior revision, so the graph stays
    # current across the 191 no-change actions exactly as it did live.
    # Bind the adopted graph to the sequence's first revision - the
    # task-start state the incident itself began from.
    first = edits[0]["post"]
    adapter.engine_state.source_revision = first
    adapter.engine_state.graph_source_revision = first
    try:
        for index, edit in enumerate(edits):
            adapter.record_edit_transaction(
                _transaction(
                    str(edit["post"]), list(edit["paths"]),
                    action_id=index + 1, complete=bool(edit["complete"]),
                )
            )
            assert adapter.engine_state.graph_current, (
                f"graph stale after replayed edit {index + 1} "
                f"({len(edit['paths'])} paths)"
            )
            assert Path(adapter.engine_state.graph_path).is_file(), (
                f"adopted graph missing after replayed edit {index + 1}"
            )
            assert (
                adapter.engine_state.graph_source_revision
                == adapter.engine_state.source_revision
            )
        events = _journal_events(adapter)
        assert [
            row for row in events
            if row.get("event") == "graph_refresh_scheduled"
        ] == [], "the incident's rebuild storm must not exist here"
        assert [
            row for row in events
            if row.get("event") == "graph_sync_amend_refused"
        ] == []
        amends = [
            row for row in events if row.get("event") == "graph_sync_amend"
        ]
        path_edits = sum(1 for edit in edits if edit["paths"])
        assert len(amends) == path_edits == 86
        assert len(fake_amend) == 86
        assert all(row.get("adopted") for row in amends)
        superseded = [
            row for row in events
            if "superseded" in json.dumps(row)
        ]
        assert superseded == [], (
            "nothing in this design can arrive already superseded"
        )
    finally:
        adapter.close_graph_lifecycle()
