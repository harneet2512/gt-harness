"""Scoped-merge salvage: candidate-vs-base mutations, clean-path subset only.

The fixture models the real promotion shape:

- ``B`` the certified base the promotion copied,
- ``C`` the candidate = B + stamped callsites + SELECTED_TARGET edges +
  deleted sibling guesses + a build-metadata stamp,
- ``L`` the live graph = B + an amend that re-derived ``stale.py`` and,
  for the divergence case, silently moved one ``clean.py`` row the stale
  set does not know about.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gt_engine.engine_state import RuntimeLayout
from gt_engine.indexer import (
    GRAPH_SCHEMA_VERSION,
    INDEX_RESOURCE_SCHEMA,
    _graph_phase_metadata,
    _sealed_json,
    certify_graph_artifact,
    certify_scoped_merge,
)
from gt_engine.scoped_merge import merge_lsp_candidate, merge_receipt

SCHEMA = """
CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    label TEXT, name TEXT, qualified_name TEXT,
    file_path TEXT, start_line INTEGER, end_line INTEGER,
    signature TEXT, stable_id TEXT, language TEXT,
    node_type TEXT, candidate_state TEXT,
    callsite_stable_id TEXT, selected_target_id TEXT
);
CREATE TABLE edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER, target_id INTEGER, type TEXT,
    source_file TEXT, source_line INTEGER,
    resolution_method TEXT, confidence REAL,
    derivation_kind TEXT, callsite_stable_id TEXT
);
CREATE TABLE resolution_callsites (
    id INTEGER PRIMARY KEY,
    source_file TEXT, callee TEXT, dispatch_state TEXT,
    candidate_count INTEGER, mechanism TEXT,
    verification_status TEXT, candidate_state TEXT
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE closure (ancestor INTEGER, descendant INTEGER);
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    name, qualified_name, signature, file_path,
    content='nodes', content_rowid='id'
);
"""

NODES = [
    # id, name, file, stable_id, node_type
    (1, "caller_clean", "clean.py", "stable:caller_clean", "function"),
    (2, "caller_stale", "stale.py", "stable:caller_stale", "function"),
    (3, "target", "target.py", "stable:target", "function"),
    (4, "cs_clean", "clean.py", "stable:cs_clean", "callsite"),
    (5, "cs_stale", "stale.py", "stable:cs_stale", "callsite"),
    (6, "diverged", "clean.py", "stable:diverged", "callsite"),
]

EDGES = [
    # id, source, target, type, source_file, method, derivation
    (1, 4, 3, "CALLS", "clean.py", "name_match", "syntax"),
    (2, 5, 3, "CALLS", "stale.py", "name_match", "syntax"),
    (3, 6, 3, "CALLS", "clean.py", "name_match", "syntax"),
]

CALLSITES = [
    (1, "clean.py", "target", "ambiguous", 3, "name_match", "unresolved", "unselected"),
    (2, "stale.py", "target", "ambiguous", 3, "name_match", "unresolved", "unselected"),
    (3, "clean.py", "target", "ambiguous", 3, "name_match", "unresolved", "unselected"),
]


def _build_base(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA)
        for nid, name, file_path, stable, node_type in NODES:
            db.execute(
                "INSERT INTO nodes (id,label,name,qualified_name,file_path,"
                "start_line,end_line,signature,stable_id,language,node_type,"
                "candidate_state,callsite_stable_id,selected_target_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, "Function", name, f"{file_path}:{name}", file_path,
                 1, 10, f"def {name}()", stable, "python", node_type,
                 "unselected" if node_type == "callsite" else None,
                 f"cs:{nid}" if node_type == "callsite" else None, None),
            )
        for eid, src, tgt, etype, sfile, method, deriv in EDGES:
            db.execute(
                "INSERT INTO edges (id,source_id,target_id,type,source_file,"
                "source_line,resolution_method,confidence,derivation_kind,"
                "callsite_stable_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (eid, src, tgt, etype, sfile, 5, method, 0.4, deriv, f"cs:{src}"),
            )
        for row in CALLSITES:
            db.execute(
                "INSERT INTO resolution_callsites (id,source_file,callee,"
                "dispatch_state,candidate_count,mechanism,"
                "verification_status,candidate_state)"
                " VALUES (?,?,?,?,?,?,?,?)", row,
            )
        db.execute("INSERT INTO metadata (key,value) VALUES ('build','base')")
        db.execute("INSERT INTO closure (ancestor,descendant) VALUES (1,3)")
        db.execute("INSERT INTO project_meta (key,value) VALUES ('p','v')")
        # A published graph carries a populated index: external-content FTS5
        # does not index inserts on its own, and the adoption preflight
        # refuses a desynced nodes_fts (docsize must equal nodes).
        db.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        db.commit()


def _copy(src: Path, dst: Path) -> None:
    with closing(sqlite3.connect(src)) as source, closing(
        sqlite3.connect(dst)
    ) as target:
        source.backup(target)


def _mutate_candidate(path: Path) -> None:
    """The promotion's complete mutation surface on the copy."""
    with closing(sqlite3.connect(path)) as db:
        # Clean callsite promoted: stamp + selected target + SELECTED_TARGET.
        db.execute(
            "UPDATE nodes SET candidate_state='selected',"
            " selected_target_id='stable:target' WHERE id=4"
        )
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (10,4,3,'SELECTED_TARGET','clean.py',"
            "5,'lsp',0.99,'lsp','cs:4')"
        )
        # Stale callsite promoted the same way.
        db.execute(
            "UPDATE nodes SET candidate_state='selected',"
            " selected_target_id='stable:target' WHERE id=5"
        )
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (11,5,3,'SELECTED_TARGET','stale.py',"
            "5,'lsp',0.99,'lsp','cs:5')"
        )
        # Diverged callsite promoted (live row will differ from base).
        db.execute(
            "UPDATE nodes SET candidate_state='selected',"
            " selected_target_id='stable:target' WHERE id=6"
        )
        # Sibling name_match guess for the clean callsite deleted.
        db.execute("DELETE FROM edges WHERE id=1")
        # Candidate build bookkeeping - must never merge into live.
        db.execute("UPDATE metadata SET value='candidate' WHERE key='build'")
        db.commit()


def _mutate_live(path: Path) -> None:
    """The amend: stale.py re-derived, one clean.py row silently moved."""
    with closing(sqlite3.connect(path)) as db:
        # stale.py re-parse: same rowids, different content.
        db.execute(
            "UPDATE nodes SET signature='def caller_stale(v2)', end_line=12"
            " WHERE id=2"
        )
        db.execute(
            "UPDATE nodes SET signature='cs-moved', end_line=12 WHERE id=5"
        )
        db.execute(
            "UPDATE edges SET source_line=9, confidence=0.4 WHERE id=2"
        )
        db.execute(
            "UPDATE resolution_callsites SET callee='target_v2' WHERE id=2"
        )
        # The diverged case: clean.py row changed live, not in stale set.
        db.execute(
            "UPDATE nodes SET signature='def diverged(v2)' WHERE id=6"
        )
        db.commit()


def _three_graphs(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "base.db"
    cand = tmp_path / "cand.db"
    live = tmp_path / "live.db"
    _build_base(base)
    _copy(base, cand)
    _mutate_candidate(cand)
    _copy(base, live)
    _mutate_live(live)
    return base, cand, live


def _rows(db_path: Path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro",
                                 uri=True)) as db:
        return db.execute(sql).fetchall()


def test_clean_mutations_apply_stale_and_diverged_skip(tmp_path):
    base, cand, live = _three_graphs(tmp_path)
    out = tmp_path / "merged.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"},
    )

    # Clean callsite promoted through the merge.
    assert _rows(out, "SELECT candidate_state,selected_target_id FROM nodes"
                      " WHERE id=4") == [("selected", "stable:target")]
    # Its SELECTED_TARGET edge inserted with refs intact.
    assert _rows(out, "SELECT source_id,target_id,resolution_method FROM edges"
                      " WHERE id=10") == [(4, 3, "lsp")]
    # The sibling guess the candidate deleted is gone.
    assert _rows(out, "SELECT id FROM edges WHERE id=1") == []

    # Stale-file mutations skipped: callsite 5 keeps live's moved row.
    assert _rows(out, "SELECT candidate_state,signature FROM nodes"
                      " WHERE id=5") == [("unselected", "cs-moved")]
    assert _rows(out, "SELECT id FROM edges WHERE id=11") == []

    # Diverged clean-path row: live no longer equals base -> not overwritten.
    assert _rows(out, "SELECT candidate_state,signature FROM nodes"
                      " WHERE id=6") == [("unselected", "def diverged(v2)")]

    # Candidate build metadata never merges.
    assert _rows(out, "SELECT value FROM metadata WHERE key='build'") == \
        [("base",)]

    assert result.inserted == 1 and result.deleted == 1 and result.updated == 1
    assert result.skipped_stale >= 2  # stale callsite update + stale edge
    assert result.skipped_diverged == 1  # node 6
    assert result.applied == 3
    # The nodes table moved, so its external-content FTS was rebuilt.
    assert result.fts_rebuilt == 1


def test_merge_refuses_to_overwrite_an_existing_output(tmp_path):
    base, cand, live = _three_graphs(tmp_path)
    out = tmp_path / "merged.db"
    out.write_bytes(b"occupied")
    with pytest.raises(FileExistsError, match="scoped_merge_output_exists"):
        merge_lsp_candidate(
            base_graph=base, candidate_graph=cand, live_graph=live,
            out_path=out, stale_paths=set(),
        )
    assert out.read_bytes() == b"occupied"


def test_receipt_pins_all_four_graphs_and_counts(tmp_path):
    base, cand, live = _three_graphs(tmp_path)
    out = tmp_path / "merged.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"},
    )
    receipt = merge_receipt(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, source_revision="rev-live", stale_paths={"stale.py"},
        result=result,
    )
    assert receipt["schema"] == "gt.scoped_merge_receipt.v1"
    assert receipt["source_revision"] == "rev-live"
    assert receipt["stale_path_count"] == 1
    assert receipt["applied"] == 3
    assert len(receipt["input_base_graph_sha256"]) == 64
    assert len(receipt["input_candidate_graph_sha256"]) == 64
    assert len(receipt["input_live_graph_sha256"]) == 64
    assert len(receipt["output_graph_sha256"]) == 64
    json.dumps(receipt)  # the receipt must serialize


def test_inserted_edge_with_diverged_target_refuses(tmp_path):
    base, cand, live = _three_graphs(tmp_path)
    with closing(sqlite3.connect(cand)) as db:
        # A SELECTED_TARGET edge whose target is the diverged node (6).
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (12,4,6,'SELECTED_TARGET','clean.py',"
            "5,'lsp',0.99,'lsp','cs:4')"
        )
        db.commit()
    out = tmp_path / "merged.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths=set(),
    )
    # The clean.py callsite is clean by path, but its target node diverged:
    # the insert must not bind a live row that is not the honest version.
    assert _rows(out, "SELECT id FROM edges WHERE id=12") == []
    assert result.skipped_diverged >= 1


def test_inserted_row_already_live_verbatim_does_not_crash(tmp_path):
    """Smoke20 bandit-taint: lsp_salvage failed IntegrityError UNIQUE
    constraint failed: edges.id (x2). A sibling salvage published the same
    lineage row while this candidate was in flight - live already holds the
    identical row at the same id. The merge must treat it as already applied,
    not INSERT it again."""
    base, cand, live = _three_graphs(tmp_path)
    with closing(sqlite3.connect(live)) as db:
        # The sibling salvage landed the identical edge the candidate adds.
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (10,4,3,'SELECTED_TARGET','clean.py',"
            "5,'lsp',0.99,'lsp','cs:4')"
        )
        db.commit()
    out = tmp_path / "merged.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"},
    )
    # The row is live verbatim - one edge total, no duplicate.
    assert _rows(out, "SELECT source_id,target_id,resolution_method FROM edges"
                      " WHERE id=10") == [(4, 3, "lsp")]
    assert _rows(out, "SELECT COUNT(*) FROM edges WHERE id=10") == [(1,)]


def test_remapped_insert_id_is_seen_by_later_candidate_rows(tmp_path):
    """merged_rows is a snapshot taken before the merge. Live holds a
    different-lineage row at id 13, so the candidate's row 13 remaps to the
    next free id (14); the candidate's own row 14 then collides with the row
    the merge itself just placed there."""
    base, cand, live = _three_graphs(tmp_path)
    with closing(sqlite3.connect(cand)) as db:
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (13,4,3,'SELECTED_TARGET','clean.py',"
            "5,'lsp',0.99,'lsp','cs:4')"
        )
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (14,4,3,'CALLS','clean.py',"
            "6,'lsp',0.9,'lsp','cs:4')"
        )
        db.commit()
    with closing(sqlite3.connect(live)) as db:
        # Same id, different lineage - forces candidate row 13 to remap to 14.
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (13,4,3,'CALLS','clean.py',"
            "6,'import',0.5,'syntax','cs:4')"
        )
        db.commit()
    out = tmp_path / "merged.db"
    merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"},
    )
    # The live-lineage row at 13 survives; all candidate rows landed, every
    # id distinct.
    assert _rows(out, "SELECT resolution_method FROM edges WHERE id=13") == \
        [("import",)]
    assert _rows(out, "SELECT COUNT(*) FROM edges WHERE type='SELECTED_TARGET'"
                      " AND source_file='clean.py'") == [(2,)]
    ids = [r[0] for r in _rows(out, "SELECT id FROM edges")]
    assert len(ids) == len(set(ids))


def test_no_mutation_means_no_publish_worthy_delta(tmp_path):
    base = tmp_path / "base.db"
    _build_base(base)
    cand = tmp_path / "cand.db"
    _copy(base, cand)  # promotion that changed nothing mergeable
    live = tmp_path / "live.db"
    _copy(base, live)
    out = tmp_path / "merged.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths=set(),
    )
    assert result.applied == 0
    # Output is a byte-identical copy of live.
    assert out.read_bytes() == live.read_bytes()


def _certified_live(tmp_path: Path):
    """A certified serving graph: revisions/source-r2/core/graph.db plus
    the manifest and sealed resource certification requires."""
    import hashlib

    workspace = tmp_path / "repo"
    workspace.mkdir(exist_ok=True)
    layout = RuntimeLayout.resolve(
        workspace=workspace, state_root=tmp_path / "state", task_id="task"
    )
    directory = layout.graph_root / "revisions" / "source-r2" / "core"
    directory.mkdir(parents=True)
    live = directory / "graph.db"
    _build_base(live)
    _mutate_live(live)
    root_sha = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    resource = directory / "index-resource.json"
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
    live_sha = hashlib.sha256(live.read_bytes()).hexdigest()
    manifest = {
        "schema": GRAPH_SCHEMA_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": "2" * 64,
        "source_revision": "source-r2",
        "graph_revision": live_sha,
        "graph_sha256": live_sha,
        "graph_bytes": live.stat().st_size,
        "index_resource_sha256": hashlib.sha256(
            resource.read_bytes()).hexdigest(),
        "binary_sha256": "3" * 64,
        "binary_certified": True,
        **_graph_phase_metadata(live),
    }
    live.with_suffix(".manifest.json").write_text(json.dumps(manifest))
    return layout, live, root_sha


def _merge_into_certified(tmp_path: Path):
    import hashlib

    layout, live, root_sha = _certified_live(tmp_path)
    base = tmp_path / "base.db"
    _build_base(base)
    cand = tmp_path / "cand.db"
    _copy(base, cand)
    _mutate_candidate(cand)
    out_dir = layout.graph_root / "enrichments" / "merge-1"
    out_dir.mkdir(parents=True)
    out = out_dir / "graph.db"
    result = merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"},
    )
    payload = merge_receipt(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, source_revision="source-r2",
        stale_paths={"stale.py"}, result=result,
        closure_rebuilt=True,
    )
    artifact = certify_scoped_merge(
        live, out, payload,
        expected_source_revision="source-r2",
        expected_repository_root_sha256="6" * 64,
        layout=layout,
        expected_root_sha256=root_sha,
        expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    return layout, live, base, cand, out, root_sha, artifact


def test_scoped_merge_certifies_as_chained_derivation(tmp_path):
    import hashlib

    layout, live, base, cand, out, root_sha, artifact = (
        _merge_into_certified(tmp_path)
    )
    assert artifact.success, artifact.error
    manifest = json.loads(out.with_suffix(".manifest.json").read_text())
    derivation = manifest["derivation"]
    assert derivation["phase"] == "lsp_scoped_merge"
    assert derivation["base_graph_sha256"] == hashlib.sha256(
        live.read_bytes()).hexdigest()
    assert derivation["merge_candidate_sha256"] == hashlib.sha256(
        cand.read_bytes()).hexdigest()
    assert derivation["merge_base_graph_sha256"] == hashlib.sha256(
        base.read_bytes()).hexdigest()
    # The salvage candidate is named by sha only - lineage resolves
    # without the file remaining on disk.
    assert "merge_candidate" not in {
        key for key in derivation if key.endswith("_path")
    }
    valid, reason = certify_graph_artifact(
        out, out.with_suffix(".manifest.json"),
        expected_root_sha256=root_sha,
        expected_source_revision="source-r2",
        expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    assert valid, reason


def test_merged_graph_can_be_the_base_of_the_next_merge(tmp_path):
    """The nested-derivation relaxation: a merged serving graph is itself
    a valid derivation base, so salvage chains instead of dying once."""
    layout, live, base, cand, out, root_sha, artifact = (
        _merge_into_certified(tmp_path)
    )
    assert artifact.success, artifact.error
    # Second merge onto the merged graph as the live base. The second
    # promotion's base IS the first merge's output - a real candidate is
    # a copy of whatever it was scheduled on.
    cand2 = tmp_path / "cand2.db"
    _copy(out, cand2)
    with closing(sqlite3.connect(cand2)) as db:
        # The callsite whose mutation skipped_diverged in the first merge
        # gets promoted this round, plus one more SELECTED_TARGET.
        db.execute(
            "UPDATE nodes SET candidate_state='selected',"
            " selected_target_id='stable:target' WHERE id=6"
        )
        db.execute(
            "INSERT INTO edges (id,source_id,target_id,type,source_file,"
            "source_line,resolution_method,confidence,derivation_kind,"
            "callsite_stable_id) VALUES (20,6,3,'SELECTED_TARGET','clean.py',"
            "5,'lsp',0.99,'lsp','cs:6')"
        )
        db.commit()
    out_dir = layout.graph_root / "enrichments" / "merge-2"
    out_dir.mkdir(parents=True)
    out2 = out_dir / "graph.db"
    result2 = merge_lsp_candidate(
        base_graph=out, candidate_graph=cand2, live_graph=out,
        out_path=out2, stale_paths=set(),
    )
    assert result2.applied == 2  # node-6 update + edge-20 insert
    payload2 = merge_receipt(
        base_graph=out, candidate_graph=cand2, live_graph=out,
        out_path=out2, source_revision="source-r2",
        stale_paths=set(), result=result2,
        closure_rebuilt=True,
    )
    artifact2 = certify_scoped_merge(
        out, out2, payload2,
        expected_source_revision="source-r2",
        expected_repository_root_sha256="6" * 64,
        layout=layout,
        expected_root_sha256=root_sha,
        expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    assert artifact2.success, artifact2.error
    manifest2 = json.loads(out2.with_suffix(".manifest.json").read_text())
    assert manifest2["derivation"]["phase"] == "lsp_scoped_merge"
    # Its base is itself a derived graph - the chain the relaxation permits.
    base_manifest = json.loads(out.with_suffix(".manifest.json").read_text())
    assert base_manifest["derivation"]["phase"] == "lsp_scoped_merge"
    valid, reason = certify_graph_artifact(
        out2, out2.with_suffix(".manifest.json"),
        expected_root_sha256=root_sha,
        expected_source_revision="source-r2",
        expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    assert valid, reason


def test_scoped_merge_manifest_tampering_is_caught(tmp_path):
    layout, live, base, cand, out, root_sha, artifact = (
        _merge_into_certified(tmp_path)
    )
    assert artifact.success, artifact.error
    manifest_path = out.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["derivation"]["merge_candidate_sha256"] = "9" * 64
    manifest_path.write_text(json.dumps(manifest))
    valid, reason = certify_graph_artifact(
        out, manifest_path,
        expected_root_sha256=root_sha,
        expected_source_revision="source-r2",
        expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    assert not valid
    assert "receipt" in reason or "identity" in reason


# ---------------------------------------------------------------------------
# Adapter integration: terminal receipt -> staged candidate -> provider-wait
# merge -> CAS publish.
# ---------------------------------------------------------------------------


def _adapter_with_live(tmp_path: Path):
    import hashlib

    from gt_engine.miniswe_integration import MiniSweAdapter

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=repo,
    )
    layout = adapter.engine_state.layout
    live_dir = layout.graph_root / "revisions" / "source-r2" / "core"
    live_dir.mkdir(parents=True, exist_ok=True)
    live = live_dir / "graph.db"
    _build_base(live)
    _mutate_live(live)
    root_sha = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()
    resource = live_dir / "index-resource.json"
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
    live_sha = hashlib.sha256(live.read_bytes()).hexdigest()
    manifest = {
        "schema": GRAPH_SCHEMA_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": "2" * 64,
        "source_revision": "source-r2",
        "graph_revision": live_sha,
        "graph_sha256": live_sha,
        "graph_bytes": live.stat().st_size,
        "index_resource_sha256": hashlib.sha256(
            resource.read_bytes()).hexdigest(),
        "binary_sha256": "3" * 64,
        "binary_certified": True,
        **_graph_phase_metadata(live),
    }
    live.with_suffix(".manifest.json").write_text(json.dumps(manifest))
    adapter.engine_state.graph_path = str(live)
    adapter.engine_state.graph_revision = live_sha
    adapter.engine_state.source_revision = "source-r2"
    adapter.engine_state.graph_source_revision = "source-r2"
    adapter.engine_state._graph_usable = True
    return adapter, live


def _terminal(task_id: str, candidate: Path, **overrides):
    receipt = {
        "schema": "gt.lsp_promotion_task.v1",
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "task_id": task_id,
        "candidate_path": str(candidate),
        "source_revision": "source-r1",
        "selected": 2,
    }
    receipt.update(overrides)
    return receipt


def _journal_events(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text().splitlines()
        if line.strip()
    ]


def test_superseded_candidate_salvages_and_publishes(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from gt_engine.graph_coordinator import GraphBuildArtifact

    # The wheel's closure rebuild shells out to the gt-index binary; the
    # merge/certify/publish path under test does not depend on it.
    monkeypatch.setattr(
        "groundtruth.resolve._rebuild_closure", lambda db_path: True
    )

    adapter, live = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    base = tmp_path / "base.db"
    _build_base(base)
    cand_dir = layout.graph_root / "enrichments" / "lsp-9"
    cand_dir.mkdir(parents=True)
    cand = cand_dir / "graph.db"
    _copy(base, cand)
    _mutate_candidate(cand)

    adapter._edit_epoch = 4
    adapter._lsp_epochs["task-9"] = 3
    adapter._path_edit_epochs["stale.py"] = 4
    adapter._lsp_requests["task-9"] = SimpleNamespace(
        candidate_path=str(cand),
        repository_root=str(tmp_path / "repo"),
        source_revision="source-r1",
        graph_revision="rev-base",
    )
    adapter._record_lsp_terminal(
        SimpleNamespace(source_revision="source-r1"),
        GraphBuildArtifact(True, str(base), "rev-base"),
        _terminal("task-9", cand),
        "obsolete",
    )

    # The candidate was claimed: renamed aside, WAL folded, job enqueued.
    assert not cand.exists()
    staged = cand_dir / "graph.db.salvage"
    assert staged.is_file()
    assert "lsp_salvage:task-9" in adapter._wait_scheduler.pending_names()

    adapter.provider_wait_begin()
    deadline = time.monotonic() + 15
    while adapter._wait_scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.02)
    adapter.provider_wait_end()
    adapter._wait_scheduler.close()

    published = Path(adapter.engine_state.graph_path)
    assert published != live
    assert published.parent.parent.name == "enrichments"
    # Clean callsite promoted through the merge; stale and diverged rows not.
    assert _rows(published,
                 "SELECT candidate_state FROM nodes WHERE id=4") == \
        [("selected",)]
    assert _rows(published,
                 "SELECT candidate_state FROM nodes WHERE id=5") == \
        [("unselected",)]
    assert _rows(published,
                 "SELECT candidate_state FROM nodes WHERE id=6") == \
        [("unselected",)]
    # The serving manifest is a scoped-merge derivation.
    manifest = json.loads(published.with_suffix(".manifest.json").read_text())
    assert manifest["derivation"]["phase"] == "lsp_scoped_merge"
    # Journal records the whole salvage honestly.
    events = {row.get("event"): row for row in _journal_events(adapter)}
    assert events["lsp_salvage_scheduled"]["stale_path_count"] == 1
    salvage = events["lsp_salvage"]
    assert salvage["outcome"] == "published"
    assert salvage["applied"] == 3
    assert salvage["skipped_diverged"] == 1


def test_salvage_refuses_when_an_edit_is_unenumerated(tmp_path):
    from types import SimpleNamespace

    from gt_engine.graph_coordinator import GraphBuildArtifact

    adapter, _ = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    base = tmp_path / "base.db"
    _build_base(base)
    cand_dir = layout.graph_root / "enrichments" / "lsp-7"
    cand_dir.mkdir(parents=True)
    cand = cand_dir / "graph.db"
    _copy(base, cand)
    _mutate_candidate(cand)

    adapter._edit_epoch = 5
    adapter._lsp_epochs["task-7"] = 3
    adapter._incomplete_edit_epoch = 5  # an edit whose paths are unknowable
    adapter._lsp_requests["task-7"] = SimpleNamespace(
        candidate_path=str(cand),
        repository_root=str(tmp_path / "repo"),
        source_revision="source-r1",
        graph_revision="rev-base",
    )
    adapter._record_lsp_terminal(
        SimpleNamespace(source_revision="source-r1"),
        GraphBuildArtifact(True, str(base), "rev-base"),
        _terminal("task-7", cand),
        "obsolete",
    )

    # Refusal is typed and nothing was staged.
    assert cand.is_file()
    assert adapter._wait_scheduler is None or (
        "lsp_salvage:task-7" not in adapter._wait_scheduler.pending_names()
    )
    events = [row for row in _journal_events(adapter)
              if row.get("event") == "lsp_salvage_refused"]
    assert events and events[0]["reason"] == "unenumerated_edit"


def test_salvage_refuses_when_live_graph_is_partial(tmp_path):
    from types import SimpleNamespace

    from gt_engine.graph_coordinator import GraphBuildArtifact

    adapter, _ = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    base = tmp_path / "base.db"
    _build_base(base)
    cand_dir = layout.graph_root / "enrichments" / "lsp-5"
    cand_dir.mkdir(parents=True)
    cand = cand_dir / "graph.db"
    _copy(base, cand)
    _mutate_candidate(cand)

    adapter._edit_epoch = 4
    adapter._lsp_epochs["task-5"] = 3
    adapter._path_edit_epochs["stale.py"] = 4
    # A dirty overlay makes the live graph partial: publishing a merge
    # over it would serve masked paths as if they were current.
    adapter.engine_state.mark_paths_dirty(("stale.py",), revision="source-r2")
    adapter._lsp_requests["task-5"] = SimpleNamespace(
        candidate_path=str(cand),
        repository_root=str(tmp_path / "repo"),
        source_revision="source-r1",
        graph_revision="rev-base",
    )
    adapter._record_lsp_terminal(
        SimpleNamespace(source_revision="source-r1"),
        GraphBuildArtifact(True, str(base), "rev-base"),
        _terminal("task-5", cand),
        "obsolete",
    )

    assert cand.is_file()
    events = [row for row in _journal_events(adapter)
              if row.get("event") == "lsp_salvage_refused"]
    assert events and events[0]["reason"] == "live_not_current"


def test_salvage_reports_closure_rebuild_failure(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from gt_engine.graph_coordinator import GraphBuildArtifact

    monkeypatch.setattr(
        "groundtruth.resolve._rebuild_closure", lambda db_path: False
    )
    adapter, live = _adapter_with_live(tmp_path)
    layout = adapter.engine_state.layout
    base = tmp_path / "base.db"
    _build_base(base)
    cand_dir = layout.graph_root / "enrichments" / "lsp-3"
    cand_dir.mkdir(parents=True)
    cand = cand_dir / "graph.db"
    _copy(base, cand)
    _mutate_candidate(cand)

    adapter._edit_epoch = 4
    adapter._lsp_epochs["task-3"] = 3
    adapter._path_edit_epochs["stale.py"] = 4
    adapter._lsp_requests["task-3"] = SimpleNamespace(
        candidate_path=str(cand),
        repository_root=str(tmp_path / "repo"),
        source_revision="source-r1",
        graph_revision="rev-base",
    )
    adapter._record_lsp_terminal(
        SimpleNamespace(source_revision="source-r1"),
        GraphBuildArtifact(True, str(base), "rev-base"),
        _terminal("task-3", cand),
        "obsolete",
    )
    adapter.provider_wait_begin()
    deadline = time.monotonic() + 15
    while adapter._wait_scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.02)
    adapter.provider_wait_end()
    adapter._wait_scheduler.close()

    assert Path(adapter.engine_state.graph_path) == live
    events = [row for row in _journal_events(adapter)
              if row.get("event") == "lsp_salvage"]
    assert events and events[0]["outcome"] == "closure_rebuild_failed"


# ------------------------------------------- parser bookkeeping consistency
#
# The producer's batch amend trusts parser_*_inventory as the identity->rowid
# map it minted. A merge that mutates a row without maintaining the inventory
# manufactures a corrupt parent: run 34888711134's merge-9bdqrbry applied 441
# in-place node UPDATEs (LSP-enriched signatures), left the un-enriched
# identities pointing at them, and every later batch amend died on
# "batch parent parser row differs from its inventory".


def _row_digest(db: sqlite3.Connection, table: str, rowid: int) -> str:
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (rowid,)).fetchone()
    return hashlib.sha256(repr(tuple(row)).encode("utf-8")).hexdigest()


def _mint_parser_inventory(db: sqlite3.Connection) -> None:
    """(Re)mint bookkeeping so each entry describes its row's content.

    Identities/digests are row-content digests, matching the producer's own
    invariant: the stored row must equal the row the entry was minted for.
    """
    db.executescript(
        "CREATE TABLE IF NOT EXISTS parser_node_inventory"
        " (identity TEXT PRIMARY KEY, node_id INTEGER NOT NULL UNIQUE);"
        "CREATE TABLE IF NOT EXISTS parser_edge_inventory"
        " (edge_id INTEGER PRIMARY KEY, content_sha256 TEXT NOT NULL);"
    )
    db.execute("DELETE FROM parser_node_inventory")
    db.execute("DELETE FROM parser_edge_inventory")
    for (nid,) in db.execute("SELECT id FROM nodes").fetchall():
        db.execute(
            "INSERT INTO parser_node_inventory(identity,node_id) VALUES(?,?)",
            (_row_digest(db, "nodes", nid), nid))
    for (eid,) in db.execute("SELECT id FROM edges").fetchall():
        db.execute(
            "INSERT INTO parser_edge_inventory(edge_id,content_sha256)"
            " VALUES(?,?)", (eid, _row_digest(db, "edges", eid)))


def _inventoried_graphs(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "base.db"
    cand = tmp_path / "cand.db"
    live = tmp_path / "live.db"
    _build_base(base)
    with closing(sqlite3.connect(base)) as db:
        _mint_parser_inventory(db)
        db.commit()

    # live = base + a real amend on stale.py; an amend re-mints bookkeeping
    # for the rows it touched, so the whole inventory is re-minted.
    _copy(base, live)
    _mutate_live(live)
    with closing(sqlite3.connect(live)) as db:
        _mint_parser_inventory(db)
        db.commit()

    # cand = base + the promotion: type enrichment of clean and stale rows
    # (each mutated row is evicted, exactly like groundtruth.resolve does),
    # a new inventoried node, and deleted inventoried edges.
    _copy(base, cand)
    with closing(sqlite3.connect(cand)) as db:
        db.execute(
            "UPDATE nodes SET signature='def caller_clean() -> int' WHERE id=1")
        db.execute("DELETE FROM parser_node_inventory WHERE node_id=1")
        db.execute(
            "UPDATE nodes SET signature='def caller_stale() -> int' WHERE id=2")
        db.execute("DELETE FROM parser_node_inventory WHERE node_id=2")
        db.execute(
            "INSERT INTO nodes (id,label,name,qualified_name,file_path,"
            "start_line,end_line,signature,stable_id,language,node_type,"
            "candidate_state,callsite_stable_id,selected_target_id) VALUES"
            " (7,'Function','enriched','clean.py:enriched','clean.py',20,30,"
            " 'def enriched() -> str','stable:enriched','python','function',"
            " NULL,NULL,NULL)")
        db.execute(
            "INSERT INTO parser_node_inventory(identity,node_id) VALUES(?,?)",
            (_row_digest(db, "nodes", 7), 7))
        db.execute("DELETE FROM edges WHERE id=1")
        db.execute("DELETE FROM parser_edge_inventory WHERE edge_id=1")
        db.execute("DELETE FROM edges WHERE id=2")
        db.execute("DELETE FROM parser_edge_inventory WHERE edge_id=2")
        # The inserted node must reach the index, or docsize parity (6 != 7)
        # trips the same refusal a dead index generation trips in production.
        db.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        db.commit()
    return base, cand, live


def _assert_inventory_consistent(path: Path) -> None:
    """Every surviving entry describes the row it points at (or fails)."""
    with closing(sqlite3.connect(path)) as db:
        for identity, nid in db.execute(
                "SELECT identity,node_id FROM parser_node_inventory"):
            assert _row_digest(db, "nodes", nid) == identity, (
                f"node inventory entry aliases row {nid}")
        for eid, digest in db.execute(
                "SELECT edge_id,content_sha256 FROM parser_edge_inventory"):
            assert _row_digest(db, "edges", eid) == digest, (
                f"edge inventory entry aliases row {eid}")


def test_merge_never_manufactures_a_stale_parser_inventory(tmp_path):
    base, cand, live = _inventoried_graphs(tmp_path)
    out = tmp_path / "merged.db"
    merge_lsp_candidate(
        base_graph=base, candidate_graph=cand, live_graph=live,
        out_path=out, stale_paths={"stale.py"})

    _assert_inventory_consistent(out)

    with closing(sqlite3.connect(
            f"file:{out.as_posix()}?mode=ro", uri=True)) as db:
        # The enriched clean-path row merged and its stale live entry is gone:
        # a later amend must re-derive it, not alias the old identity.
        assert db.execute(
            "SELECT signature FROM nodes WHERE id=1").fetchone() == \
            ("def caller_clean() -> int",)
        assert db.execute(
            "SELECT 1 FROM parser_node_inventory WHERE node_id=1"
        ).fetchone() is None
        # The candidate-only inventoried row was adopted under its merged id.
        adopted = db.execute(
            "SELECT n.id FROM parser_node_inventory i JOIN nodes n"
            " ON n.id=i.node_id WHERE n.name='enriched'").fetchall()
        assert len(adopted) == 1
        # The stale-path update was skipped: live's row and entry stand.
        assert db.execute(
            "SELECT signature FROM nodes WHERE id=2").fetchone() == \
            ("def caller_stale(v2)",)
        assert db.execute(
            "SELECT 1 FROM parser_node_inventory WHERE node_id=2"
        ).fetchone() is not None
        # The clean-path deleted edge and its entry are both gone; the
        # stale-path delete was skipped and live's entry survives.
        assert db.execute(
            "SELECT 1 FROM edges WHERE id=1").fetchone() is None
        assert db.execute(
            "SELECT 1 FROM parser_edge_inventory WHERE edge_id=1"
        ).fetchone() is None
        assert db.execute(
            "SELECT 1 FROM parser_edge_inventory WHERE edge_id=2"
        ).fetchone() is not None
