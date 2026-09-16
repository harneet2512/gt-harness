"""Audit slice 4: the graph/index/enrichment lifecycle under adverse timing.

The audit contract is not "does the pruner work in the happy path" -- it is
whether retention, nested derivations, restarts, out-of-order drains,
publication failures, memory windows and interruptions can suppress or
corrupt the evidence a run already paid for, and whether every failure stays
typed and contained.

Sections:

1. Prune containment. ``_prune_superseded_revisions`` is the deletion
   authority; any fault inside it must freeze deletion (retain everything),
   never propagate. A raise inside ``ensure_index``/``refresh_index_files``
   otherwise converts a certified publication into a build failure -- on the
   shipping startup path that is ``BenchmarkGraphRequired`` and a dead run.
2. Nested derivations. lsp-on-lsp and merge-on-lsp chains must certify
   recursively, and the citation scan must protect every revision the chain
   still reads through -- including after a prune.
3. Adapter lifecycle timing: startup index before/after the first edit,
   out-of-order candidate completion, rapid edits against in-flight work,
   publication/disk failure typing, and interruption before publication /
   after admission / during finalization.
4. Cross-boundary reproducers for defects whose owners lived outside this
   slice (``miniswe_integration.py``): the dead-enrichment sweep was
   reference-blind and enrichment graphs could not be pinned. Fixed at the
   owning layer; these tests now assert the corrected contract.
5. D6 end-to-end through the real adapter: refresh -> nested enrichment ->
   pruning -> seal-time certification.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine import indexer
from gt_engine.engine_state import RuntimeLayout
from gt_engine.event_journal import verify_event_journal
from gt_engine.graph_coordinator import FrozenBuildInput, GraphBuildArtifact
from gt_engine.indexer import (
    GRAPH_SCHEMA_VERSION,
    INDEX_RESOURCE_SCHEMA,
    IndexBuildReceipt,
    IndexBuildStatus,
    _graph_phase_metadata,
    _prune_superseded_revisions,
    _sealed_json,
    certify_graph_artifact,
    certify_lsp_candidate,
    certify_scoped_merge,
    prune_graph_revisions,
)
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace, diff_workspace
from gt_engine.scoped_merge import merge_lsp_candidate, merge_receipt
from tests.test_scoped_merge import (
    _build_base,
    _copy,
    _mutate_candidate,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root_sha(workspace: Path) -> str:
    # The exact expression _certify_published_graph derives for the workspace.
    return hashlib.sha256(
        os.path.realpath(workspace).encode("utf-8", "surrogatepass")
    ).hexdigest()


def _revision(root: Path, name: str, *, pinned: bool = False) -> Path:
    """A prune-scheme revision dir: revisions/<name>/graph.db + manifest."""
    path = root / "revisions" / name
    path.mkdir(parents=True)
    (path / "graph.db").write_bytes(f"graph-{name}".encode())
    (path / "graph.manifest.json").write_text(
        json.dumps({"graph_sha256": name}), encoding="utf-8"
    )
    if pinned:
        (path / "pinned.json").write_text(
            json.dumps({"schema": "gt.revision_pin.v1"}), encoding="utf-8"
        )
    return path


def _graph_mtime(revision: Path, when: float) -> None:
    os.utime(revision / "graph.db", (when, when))


def _adapter(tmp_path: Path, *, task_id: str = "audit") -> MiniSweAdapter:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return MiniSweAdapter(
        task_id=task_id,
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
    )


def _journal_rows(adapter: MiniSweAdapter, event: str = "") -> list[dict]:
    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row for row in rows if not event or row.get("event") == event]


# -- certified-graph fixtures (the shape certify_graph_artifact accepts) -----


def _certified_revision(
    layout: RuntimeLayout, workspace: Path, name: str, *,
    source_revision: str = "source-r1", mutate=None,
) -> Path:
    """A flat revisions/<name>/graph.db that passes certify_graph_artifact."""
    directory = layout.graph_root / "revisions" / name
    directory.mkdir(parents=True, exist_ok=True)
    graph = directory / "graph.db"
    _build_base(graph)
    if mutate is not None:
        mutate(graph)
    root_sha = _root_sha(workspace)
    resource = directory / "index-resource.json"
    _sealed_json(
        resource,
        {
            "schema": INDEX_RESOURCE_SCHEMA,
            "identity_scope": "benchmark_bound",
            "task_id": "audit",
            "product_source_sha": "4" * 40,
            "repository_root_sha256": root_sha,
            "source_manifest_sha256": "2" * 64,
            "producer_binary_sha256": "3" * 64,
            "status": "completed",
            "error_code": "",
            "exit_code": 0,
            "memory_evidence": False,
        },
        "evidence_sha256",
    )
    graph_sha = _sha(graph)
    manifest = {
        "schema": GRAPH_SCHEMA_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "identity_scope": "benchmark_bound",
        "task_id": "audit",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": "2" * 64,
        "source_revision": source_revision,
        "graph_revision": graph_sha,
        "graph_sha256": graph_sha,
        "graph_bytes": graph.stat().st_size,
        "index_resource_sha256": _sha(resource),
        "binary_sha256": "3" * 64,
        "binary_certified": True,
        **_graph_phase_metadata(graph),
    }
    graph.with_suffix(".manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return graph


def _lsp_candidate(base: Path, name: str, marker: str) -> Path:
    """An enrichments/<name>/graph.db derived from ``base`` (copy + marker)."""
    graph_root = base.parents[2] if base.parent.parent.name == "revisions" else base.parents[2]
    directory = graph_root / "enrichments" / name
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / "graph.db"
    _copy(base, candidate)
    with closing(sqlite3.connect(candidate)) as db:
        db.execute(f"CREATE TABLE {marker} (id INTEGER PRIMARY KEY)")
        db.execute(f"INSERT INTO {marker} VALUES (1)")
        db.commit()
    return candidate


def _lsp_receipt(base: Path, candidate: Path, *, source_revision: str,
                 snapshot_sha: str) -> dict:
    manifest = json.loads(base.with_suffix(".manifest.json").read_text())
    return {
        "schema": "gt.lsp_promotion_task.v1",
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "source_revision": source_revision,
        "repository_root_sha256": "6" * 64,
        "repository_snapshot_sha256": snapshot_sha,
        "input_graph_revision": manifest["graph_revision"],
        "input_graph_sha256": _sha(base),
        "candidate_path": str(candidate.resolve()),
        "output_graph_sha256": _sha(candidate),
        "verified": 4, "corrected": 2, "selected": 1, "deleted": 0,
    }


def _certify_lsp(layout, workspace, base: Path, candidate: Path, *,
                 source_revision: str = "source-r1",
                 snapshot_sha: str = "7" * 64) -> GraphBuildArtifact:
    return certify_lsp_candidate(
        base, candidate,
        _lsp_receipt(base, candidate, source_revision=source_revision,
                     snapshot_sha=snapshot_sha),
        expected_source_revision=source_revision,
        expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha,
        layout=layout,
        expected_root_sha256=_root_sha(workspace),
        expected_task_id="audit",
        expected_product_source_sha="4" * 40,
    )


def _enrichment_manifest(graph_root: Path, name: str, base_revision: Path) -> Path:
    """A minimal derivation manifest citing a revision through the portable
    manifest-relative reference certify_lsp_candidate actually emits."""
    path = graph_root / "enrichments" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "graph.manifest.json").write_text(
        json.dumps({
            "graph_root": "../..",
            "derivation": {
                "base_manifest": (
                    f"../../revisions/{base_revision.name}/graph.manifest.json"
                ),
                "base_graph": f"../../revisions/{base_revision.name}/graph.db",
                "base_resource": (
                    f"../../revisions/{base_revision.name}/index-resource.json"
                ),
            },
        }),
        encoding="utf-8",
    )
    return path


class _DoneFuture:
    """The runner's _StartupIndex shape: done() + result()."""

    def __init__(self, receipt):
        self._receipt = receipt

    def done(self) -> bool:
        return True

    def result(self):
        return self._receipt


# ---------------------------------------------------------------------------
# 1. prune containment -- the deletion authority freezes, it never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"text"', "5", "null"])
def test_a_manifest_that_parses_but_is_not_an_object_freezes_pruning(
    tmp_path, payload
):
    """json.loads succeeds on these, but ``.get`` does not exist on them.

    The unreadable-manifest guard catches (OSError, ValueError); a manifest
    that parses to a non-dict slips straight past it into an AttributeError.
    A citation the scan cannot read is unknowable, and unknowable must freeze
    deletion -- never authorise it, and never raise it.
    """
    stale = [_revision(tmp_path / "graph", f"r{i}") for i in range(3)]
    live = _revision(tmp_path / "graph", "live")
    weird = tmp_path / "graph" / "enrichments" / "lsp-weird"
    weird.mkdir(parents=True)
    (weird / "graph.manifest.json").write_text(payload, encoding="utf-8")

    _prune_superseded_revisions(live)  # must not raise

    assert all(path.is_dir() for path in [*stale, live]), (
        "an un-auditable manifest must freeze pruning, not delete or raise"
    )


def test_prune_survives_a_manifest_scan_that_fails_mid_iteration(
    tmp_path, monkeypatch
):
    """Any unexpected fault in the reference scan freezes deletion."""
    stale = [_revision(tmp_path / "graph", f"r{i}") for i in range(3)]
    live = _revision(tmp_path / "graph", "live")

    def boom(parent):
        raise RuntimeError("simulated scan fault")

    monkeypatch.setattr(indexer, "_referenced_revisions", boom)
    _prune_superseded_revisions(live)  # must not raise

    assert all(path.is_dir() for path in [*stale, live]), (
        "a failed reference scan must freeze pruning, not propagate"
    )


def test_ensure_index_returns_the_graph_pruning_could_not_reclaim(
    tmp_path, monkeypatch
):
    """A retention fault is disk pressure; it must not eat the publication.

    Shipping path: ``miniswe_gt_run._initial_index`` calls
    ``ensure_index_with_receipt`` with the default ``reclaim=True``, so the
    prune runs inside ``ensure_index``'s broad except. Today a prune raise
    turns the certified build into ``None`` -- and under benchmark identity
    into ``BenchmarkGraphRequired``, i.e. a dead run after a good build.

    The injected fault is a RuntimeError from the reference scan itself:
    not one of the typed (OSError, ValueError) freeze conditions inside
    it, so only the prune's own containment boundary can catch it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    graph_root = tmp_path / "state" / "graph"
    produced = _revision(graph_root, "produced")
    # An older revision exists so the prune genuinely engages.
    _revision(graph_root, "older")

    def boom(parent):
        raise RuntimeError("simulated scan fault")

    monkeypatch.setattr(indexer, "_referenced_revisions", boom)
    monkeypatch.setattr(
        indexer, "_ensure_index_unlocked",
        lambda *args, **kwargs: str(produced / "graph.db"),
    )
    # ensure_index calls _prune_superseded_revisions(Path(graph).parent):
    # parent is <graph_root>/revisions, so the scan covers graph_root.
    monkeypatch.setattr(
        indexer, "_graph_state_dir", lambda *a, **k: graph_root / "revisions" / "produced"
    )

    graph = indexer.ensure_index(str(repo), state_dir=str(tmp_path / "state"))
    assert graph == str(produced / "graph.db"), (
        "a prune failure must leave the certified publication in the result"
    )


def test_ensure_index_benchmark_identity_survives_a_prune_fault(
    tmp_path, monkeypatch
):
    """The run-termination half: benchmark-bound ensure_index must not turn
    a retention fault into BenchmarkGraphRequired."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("GT_TASK_ID", "audit")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "a" * 40)
    graph_root = tmp_path / "state" / "graph"
    produced = _revision(graph_root, "produced")

    def boom(parent):
        raise RuntimeError("simulated scan fault")

    monkeypatch.setattr(indexer, "_referenced_revisions", boom)
    monkeypatch.setattr(
        indexer, "_ensure_index_unlocked",
        lambda *args, **kwargs: str(produced / "graph.db"),
    )
    monkeypatch.setattr(
        indexer, "_graph_state_dir", lambda *a, **k: produced
    )

    graph = indexer.ensure_index(str(repo), state_dir=str(tmp_path / "state"))
    assert graph == str(produced / "graph.db")


def test_ensure_index_still_raises_when_the_BUILD_failed(
    tmp_path, monkeypatch
):
    """Control: containment is for reclamation, not for build failure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("GT_TASK_ID", "audit")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        indexer, "_ensure_index_unlocked", lambda *args, **kwargs: None
    )
    with pytest.raises(indexer.BenchmarkGraphRequired):
        indexer.ensure_index(str(repo), state_dir=str(tmp_path / "state"))


def test_refresh_index_files_reports_the_amend_pruning_could_not_reclaim(
    tmp_path, monkeypatch
):
    """The incremental half: a published amend is a success even when the
    retention sweep after it faults. Reporting it amend_refused orphans a
    certified revision the journal then says never published."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    layout = RuntimeLayout.resolve(
        workspace=repo, state_root=tmp_path / "state", task_id="audit"
    )
    parent = _certified_revision(layout, repo, "parent")
    published_dir = layout.graph_root / "revisions" / "amended"
    published_dir.mkdir(parents=True)
    published = published_dir / "graph.db"
    with closing(sqlite3.connect(published)) as db:
        db.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
    (published_dir / "graph.manifest.json").write_text(
        json.dumps({"graph_sha256": _sha(published), "graph_revision": _sha(published)}),
        encoding="utf-8",
    )
    def boom(parent):
        raise RuntimeError("simulated scan fault")

    monkeypatch.setattr(indexer, "_referenced_revisions", boom)
    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked",
        lambda *args, **kwargs: (str(published), "", ({"path": "x.py", "updated": 1},)),
    )

    receipt = indexer.refresh_index_files(
        str(repo), parent, ("x.py",), layout=layout
    )
    assert receipt.success, (
        f"published amend misreported after prune fault: {receipt.error_type} "
        f"{receipt.error_diagnostic}"
    )
    assert receipt.build_mode == "incremental"


# ---------------------------------------------------------------------------
# 2. nested derivations + citation protection
# ---------------------------------------------------------------------------


def _deep_chain(tmp_path: Path):
    """revisions/rev1 -> enrichments/lsp-a -> enrichments/lsp-b -> merge-m.

    Three derivation hops, two phases, bases living in BOTH derivation
    namespaces. Returns everything later assertions need.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    layout = RuntimeLayout.resolve(
        workspace=repo, state_root=tmp_path / "state", task_id="audit"
    )
    rev1 = _certified_revision(layout, repo, "rev1")

    lsp_a = _lsp_candidate(rev1, "lsp-aaaa", "lsp_edges_a")
    art_a = _certify_lsp(layout, repo, rev1, lsp_a, snapshot_sha="7" * 64)
    assert art_a.success, art_a.error

    # lsp-on-lsp: the second leg's base is the first leg's published output.
    lsp_b = _lsp_candidate(lsp_a, "lsp-bbbb", "lsp_edges_b")
    art_b = _certify_lsp(layout, repo, lsp_a, lsp_b, snapshot_sha="8" * 64)
    assert art_b.success, art_b.error
    assert json.loads(
        lsp_b.with_suffix(".manifest.json").read_text()
    )["derivation"]["phase"] == "lsp"

    # merge-on-lsp: a scoped salvage whose live graph is lsp-b. The merge's
    # own inputs (base + candidate) are the promotion pair it salvaged.
    promo_base = tmp_path / "promo-base.db"
    _build_base(promo_base)
    promo_cand = tmp_path / "promo-cand.db"
    _copy(promo_base, promo_cand)
    _mutate_candidate(promo_cand)
    merged_dir = layout.graph_root / "enrichments" / "merge-m"
    merged_dir.mkdir(parents=True)
    merged = merged_dir / "graph.db"
    result = merge_lsp_candidate(
        base_graph=promo_base, candidate_graph=promo_cand,
        live_graph=lsp_b, out_path=merged, stale_paths=set(),
    )
    assert result.applied > 0, "the salvage must carry real mutations"
    payload = merge_receipt(
        base_graph=promo_base, candidate_graph=promo_cand,
        live_graph=lsp_b, out_path=merged, source_revision="source-r1",
        stale_paths=set(), result=result, closure_rebuilt=True,
    )
    art_m = certify_scoped_merge(
        lsp_b, merged, payload,
        expected_source_revision="source-r1",
        expected_repository_root_sha256="6" * 64,
        layout=layout,
        expected_root_sha256=_root_sha(repo),
        expected_task_id="audit",
        expected_product_source_sha="4" * 40,
    )
    assert art_m.success, art_m.error
    return layout, repo, rev1, lsp_a, lsp_b, merged


def test_nested_chain_certifies_and_survives_a_later_prune(tmp_path):
    """D6's core: a three-hop chain must revalidate after retention runs.

    lsp-a cites rev1; lsp-b cites lsp-a; merge-m cites lsp-b. Pruning a
    newer live revision must keep rev1 -- the only path the citation scan
    has to it is the enrichment manifest run 35016130850 proved invisible.
    """
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)

    # A later adoption makes rev1 superseded: live moves to a new revision.
    rev2 = _certified_revision(layout, repo, "rev2")
    _graph_mtime(rev1.parent, 1000)
    _graph_mtime(rev2.parent, 4000)
    # Two uncited siblings: the newest is the one retained superseded slot,
    # the older must be reclaimed -- which proves the prune actually ran.
    expendable = [
        _certified_revision(layout, repo, f"expendable-{i}") for i in range(2)
    ]
    _graph_mtime(expendable[0].parent, 2000)
    _graph_mtime(expendable[1].parent, 2100)

    # Drop transient read handles before pruning: on Windows an open sqlite
    # handle inside a revision dir blocks os.replace, which would mask a
    # missing protection as a silent retain rather than a delete.
    gc.collect()
    prune_graph_revisions(rev2.parent)

    assert not expendable[0].parent.is_dir(), (
        "an uncited superseded revision must be reclaimed -- otherwise the "
        "protection assertions below are vacuous"
    )
    assert expendable[1].parent.is_dir(), "the retained superseded slot stays"
    assert rev1.parent.is_dir(), (
        "the chain's root revision is cited by enrichments/lsp-a -- "
        "losing it is the 35016130850 receipt failure"
    )
    # Seal-time certification: the whole chain revalidates recursively.
    valid, reason = certify_graph_artifact(
        merged, merged.with_suffix(".manifest.json"),
        expected_root_sha256=_root_sha(repo),
        expected_source_revision="source-r1",
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert valid, reason


def test_the_chain_break_is_typed_not_raised(tmp_path):
    """A pruned/corrupt base must never become an unhandled raise."""
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)

    # Simulate the defect: the middle of the chain disappears.
    lsp_a.with_suffix(".manifest.json").unlink()

    valid, reason = certify_graph_artifact(
        merged, merged.with_suffix(".manifest.json"),
        expected_root_sha256=_root_sha(repo),
        expected_source_revision="source-r1",
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert not valid
    assert reason.startswith("derivation_base"), reason


def test_a_foreign_phase_base_is_still_refused(tmp_path):
    """The nested relaxation admits lsp/lsp_scoped_merge phases only."""
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)
    base_manifest = json.loads(lsp_b.with_suffix(".manifest.json").read_text())
    base_manifest["derivation"]["phase"] = "exotic_enrichment"
    lsp_b.with_suffix(".manifest.json").write_text(json.dumps(base_manifest))

    candidate = _lsp_candidate(lsp_b, "lsp-cccc", "lsp_edges_c")
    result = certify_lsp_candidate(
        lsp_b, candidate,
        _lsp_receipt(lsp_b, candidate, source_revision="source-r1",
                     snapshot_sha="9" * 64),
        expected_source_revision="source-r1",
        expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256="9" * 64,
        layout=layout,
        expected_root_sha256=_root_sha(repo),
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert not result.success
    # The base's own certificate refuses an unknown phase before the
    # nested-phase rule is even consulted; either typed refusal is correct,
    # what matters is that the chain never certifies a foreign phase.
    assert result.error in {
        "lsp_nested_derivation_forbidden",
        "lsp_base_invalid:derivation_unknown",
    }, result.error


def test_a_revision_manifest_can_also_protect_its_base(tmp_path):
    """Revision-to-revision derivations read through ../<base>/ references."""
    root = tmp_path / "graph"
    base = _revision(root, "base")
    stale = _revision(root, "stale")
    live = _revision(root, "live")
    child = root / "revisions" / "child"
    child.mkdir(parents=True)
    (child / "graph.db").write_bytes(b"child")
    (child / "graph.manifest.json").write_text(json.dumps({
        "derivation": {
            "base_graph": "../base/graph.db",
            "base_manifest": "../base/graph.manifest.json",
        }
    }), encoding="utf-8")
    _graph_mtime(base, 1000)
    _graph_mtime(stale, 2000)
    _graph_mtime(child, 3000)
    _graph_mtime(live, 4000)

    _prune_superseded_revisions(live)

    assert not stale.is_dir(), "the prune ran: the uncited sibling is gone"
    assert base.is_dir(), "a revision-to-revision citation is load-bearing"


def test_every_surviving_pruner_state_still_certifies(tmp_path):
    """Seal-time certification across the states the pruner can leave:

    live revision, retained superseded sibling, pinned revision, and the
    frozen-everything outcome of an un-auditable manifest.
    """
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)
    root_sha = _root_sha(repo)
    pinned = _certified_revision(layout, repo, "pinned")
    (pinned.parent / "pinned.json").write_text(
        json.dumps({"schema": "gt.revision_pin.v1"}), encoding="utf-8"
    )
    extra = _certified_revision(layout, repo, "extra")
    live = _certified_revision(layout, repo, "live")
    _graph_mtime(rev1.parent, 1000)
    _graph_mtime(pinned.parent, 2000)
    _graph_mtime(extra.parent, 3000)
    _graph_mtime(live.parent, 4000)

    prune_graph_revisions(live.parent)

    for graph in (rev1, pinned, live):
        valid, reason = certify_graph_artifact(
            graph, graph.with_suffix(".manifest.json"),
            expected_root_sha256=root_sha,
            expected_source_revision="source-r1",
            expected_task_id="audit", expected_product_source_sha="4" * 40,
        )
        assert valid, f"{graph.parent.name}: {reason}"

    # The corrupt-manifest freeze state: nothing is deleted, everything
    # still certifies. `witness` is created AFTER the first prune so it is
    # the oldest unprotected sibling -- without the freeze it must die.
    witness = _certified_revision(layout, repo, "witness")
    _graph_mtime(witness.parent, 1500)
    corrupt = layout.graph_root / "enrichments" / "lsp-corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "graph.manifest.json").write_bytes(b"\xff\xfe not json")
    gc.collect()
    prune_graph_revisions(live.parent)
    assert witness.parent.is_dir(), (
        "freeze state retains even expendable revisions"
    )
    valid, reason = certify_graph_artifact(
        extra, extra.with_suffix(".manifest.json"),
        expected_root_sha256=root_sha,
        expected_source_revision="source-r1",
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert valid, reason


# -- Layer-4: the invariants are mutation-sensitive ---------------------------


def test_mutation_a_revisions_only_scan_loses_the_cited_base(tmp_path):
    """Sensitivity proof for the enrichment citation fix.

    Reinstating the 35016130850 scan (revisions manifests only) must lose
    the cited base; if it does not, the protection tests above are vacuous.
    """
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)
    rev2 = _certified_revision(layout, repo, "rev2")
    # Enough unpinned siblings that retention reaches rev1's slot: with only
    # one superseded revision the retained slot hides the mutation.
    expendable = [
        _certified_revision(layout, repo, f"expendable-{i}") for i in range(2)
    ]
    _graph_mtime(rev1.parent, 1000)
    for index, extra in enumerate(expendable):
        _graph_mtime(extra.parent, 2000 + index * 100)
    _graph_mtime(rev2.parent, 4000)

    def blind_scan(parent: Path) -> set[Path]:
        referenced: set[Path] = set()
        for manifest in parent.glob("*/graph.manifest.json"):
            try:
                derivation = json.loads(
                    manifest.read_text(encoding="utf-8")).get("derivation")
            except (OSError, ValueError):
                continue
            if not isinstance(derivation, dict):
                continue
            for key in ("base_graph", "base_manifest", "base_resource",
                        "terminal_receipt"):
                reference = str(derivation.get(key) or "")
                if not reference or "/" not in reference:
                    continue
                candidate = (manifest.parent / reference).resolve()
                for ancestor in (candidate, *candidate.parents):
                    if ancestor.parent == parent.resolve():
                        referenced.add(ancestor)
                        break
        return referenced

    monkeypatch_target = indexer._referenced_revisions
    indexer._referenced_revisions = blind_scan
    try:
        gc.collect()  # clear transient handles so the delete is observable
        prune_graph_revisions(rev2.parent)
    finally:
        indexer._referenced_revisions = monkeypatch_target

    assert not rev1.parent.is_dir(), (
        "under the mutated scan the cited base must die -- otherwise the "
        "protection tests cannot see the fix they claim to cover"
    )
    valid, reason = certify_graph_artifact(
        merged, merged.with_suffix(".manifest.json"),
        expected_root_sha256=_root_sha(repo),
        expected_source_revision="source-r1",
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert not valid and reason.startswith("derivation_base"), reason


def test_mutation_dropping_the_freeze_deletes_under_corruption(tmp_path):
    """Sensitivity proof for the unreadable-manifest freeze."""
    root = tmp_path / "graph"
    stale = [_revision(root, f"r{i}") for i in range(3)]
    live = _revision(root, "live")
    corrupt = root / "enrichments" / "lsp-bad"
    corrupt.mkdir(parents=True)
    (corrupt / "graph.manifest.json").write_bytes(b"\xff\xfe not json")

    def no_freeze(parent: Path) -> set[Path]:
        referenced: set[Path] = set()
        for manifest in parent.parent.rglob("graph.manifest.json"):
            try:
                derivation = json.loads(
                    manifest.read_text(encoding="utf-8")).get("derivation")
            except (OSError, ValueError):
                continue  # mutation: unreadable means "no citations"
            if isinstance(derivation, dict):
                for key in ("base_graph", "base_manifest"):
                    reference = str(derivation.get(key) or "")
                    if "/" in reference:
                        candidate = (manifest.parent / reference).resolve()
                        for ancestor in (candidate, *candidate.parents):
                            if ancestor.parent == parent.resolve():
                                referenced.add(ancestor)
                                break
        return referenced

    monkeypatch_target = indexer._referenced_revisions
    indexer._referenced_revisions = no_freeze
    try:
        _prune_superseded_revisions(live)
    finally:
        indexer._referenced_revisions = monkeypatch_target

    assert sum(path.is_dir() for path in stale) <= 1, (
        "under the mutation, retention must visibly evict -- a test that "
        "still passes without the freeze is not testing the freeze"
    )


# ---------------------------------------------------------------------------
# 3. adapter lifecycle timing
# ---------------------------------------------------------------------------


class _Handle:
    """A promotion leg whose completion the test controls."""

    def __init__(self, task_id: str, receipt: dict, *, done: bool = True):
        self.task_id = task_id
        self.receipt = receipt
        self.done = done

    def release(self):
        self.done = True

    def terminal_receipt(self, *, timeout=None):
        return dict(self.receipt)


def _terminal(task_id: str, request: FrozenBuildInput,
              base: GraphBuildArtifact, candidate: Path, **overrides) -> dict:
    receipt = {
        "schema": "gt.lsp_promotion_task.v1",
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "task_id": task_id,
        "candidate_path": str(candidate),
        "source_revision": request.source_revision,
        "input_graph_revision": base.graph_revision,
        "verified": 5, "corrected": 1, "selected": 0, "deleted": 0,
    }
    receipt.update(overrides)
    return receipt


def _lsp_scheduler(*handles):
    return SimpleNamespace(
        _handles=list(handles), close=lambda wait=False: None
    )


def test_out_of_order_completion_dispositions_against_current_state(
    tmp_path, monkeypatch
):
    """Two finished legs drain in handle order; each is judged against the
    graph the authority names NOW, not the order it finished in.

    The leg scheduled second is first in the drain list and wins; the leg
    that actually finished first drains obsolete against the graph the
    newer leg just published.
    """
    adapter = _adapter(tmp_path)
    live = tmp_path / "live.db"
    live.write_bytes(b"live-graph")
    (tmp_path / "live.manifest.json").write_text(
        json.dumps({"graph_sha256": _sha(live)}), encoding="utf-8"
    )
    adapter.engine_state.graph_path = str(live)
    adapter.engine_state.graph_revision = "rev-old"
    adapter.engine_state.source_revision = "src-2"
    adapter.engine_state.graph_source_revision = "src-2"
    adapter.engine_state._graph_usable = True

    old_candidate = tmp_path / "cand-old.db"
    old_candidate.write_bytes(b"cand-old")
    new_candidate = tmp_path / "cand-new.db"
    new_candidate.write_bytes(b"cand-new")

    req_old = FrozenBuildInput("src-2", ("x.py",), (("x.py", b"x"),))
    base_old = GraphBuildArtifact(True, str(live), "rev-old")
    req_new = FrozenBuildInput("src-2", ("x.py",), (("x.py", b"x"),))
    base_new = GraphBuildArtifact(True, str(live), "rev-new")

    # The newer leg's base must BE the authority's graph when it drains.
    adapter.engine_state.graph_revision = "rev-new"
    handle_new = _Handle(
        "leg-new", _terminal("leg-new", req_new, base_new, new_candidate)
    )
    handle_old = _Handle(
        "leg-old", _terminal("leg-old", req_old, base_old, old_candidate)
    )
    # Newer first in the handle list even though leg-old "finished" first.
    adapter._lsp_scheduler = _lsp_scheduler(handle_new, handle_old)
    adapter._lsp_bases["leg-new"] = (req_new, base_new)
    adapter._lsp_bases["leg-old"] = (req_old, base_old)
    adapter._lsp_epochs["leg-old"] = adapter._edit_epoch

    monkeypatch.setattr(
        adapter, "_certify_lsp_candidate",
        lambda request, base, terminal: GraphBuildArtifact(
            True, str(terminal["candidate_path"]), "rev-adopted"
        ),
    )
    try:
        adapter._poll_lsp_promotions()
        terminals = _journal_rows(adapter, "lsp_promotion_terminal")
        by_task = {
            json.loads(
                (adapter.store.root / "lsp_receipts"
                 / f"{row['artifact_sha256']}.json").read_text()
            ).get("task_id"): row["disposition"]
            for row in terminals
        }
        assert by_task["leg-new"] == "published"
        assert by_task["leg-old"] == "obsolete"
        assert adapter.engine_state.graph_path == str(new_candidate)
    finally:
        adapter.close_graph_lifecycle()


def test_certifier_exception_is_typed_and_the_graph_untouched(
    tmp_path, monkeypatch
):
    """Disk-full inside certification: OSError from _sealed_json reaches the
    disposition chain, which must type it -- never propagate it."""
    adapter = _adapter(tmp_path)
    live = tmp_path / "live.db"
    live.write_bytes(b"live-graph")
    (tmp_path / "live.manifest.json").write_text(
        json.dumps({"graph_sha256": _sha(live)}), encoding="utf-8"
    )
    adapter.engine_state.graph_path = str(live)
    adapter.engine_state.graph_revision = "rev-1"
    adapter.engine_state.source_revision = "src-1"
    adapter.engine_state.graph_source_revision = "src-1"
    adapter.engine_state._graph_usable = True

    candidate = tmp_path / "cand.db"
    candidate.write_bytes(b"cand")
    request = FrozenBuildInput("src-1", ("x.py",), (("x.py", b"x"),))
    base = GraphBuildArtifact(True, str(live), "rev-1")
    handle = _Handle("leg-x", _terminal("leg-x", request, base, candidate))
    adapter._lsp_scheduler = _lsp_scheduler(handle)
    adapter._lsp_bases["leg-x"] = (request, base)
    adapter._lsp_epochs["leg-x"] = adapter._edit_epoch

    def enospc(request, base, terminal):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(adapter, "_certify_lsp_candidate", enospc)
    try:
        adapter._poll_lsp_promotions()
        terminals = _journal_rows(adapter, "lsp_promotion_terminal")
        assert terminals[-1]["disposition"] == "certifier_exception"
        assert adapter.engine_state.graph_path == str(live)
        assert not candidate.exists(), "a refused candidate must not leak"
    finally:
        adapter.close_graph_lifecycle()


def _startup_receipt(adapter, graph: Path, source_revision: str):
    return IndexBuildReceipt(
        IndexBuildStatus.BUILT,
        graph_db=str(graph),
        graph_revision=_sha(graph),
        source_revision=source_revision,
    )


def test_startup_index_landing_before_any_edit_adopts(tmp_path, monkeypatch):
    """Index completes before the first edit: adopted, journaled, leg offered."""
    adapter = _adapter(tmp_path)
    repo = Path(adapter.repo_root)
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(repo), boundary="task_start"
    )
    produced = _revision(tmp_path / "graph", "boot")
    monkeypatch.setattr(
        adapter, "_schedule_lsp_candidate",
        lambda request, base: SimpleNamespace(task_id="leg-boot"),
    )
    adapter._startup_index = _DoneFuture(
        _startup_receipt(adapter, produced / "graph.db",
                         adapter.engine_state.source_revision)
    )
    try:
        adapter._poll_startup_index()
        ready = _journal_rows(adapter, "initial_index_ready")
        assert ready and ready[-1]["adopted"] is True
        assert adapter.engine_state.graph_path == str(produced / "graph.db")
        assert adapter._unadopted_graph == ("", "")
    finally:
        adapter.close_graph_lifecycle()


def test_startup_index_landing_after_an_edit_becomes_the_amend_parent(
    tmp_path, monkeypatch
):
    """Index completes after the first edit: superseded at admission, kept
    as the certified fallback parent, and PROTECTED from the next prune --
    the restart-resume shape where an older dependency is still required.
    """
    adapter = _adapter(tmp_path)
    repo = Path(adapter.repo_root)
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(repo), boundary="task_start"
    )
    pre = capture_workspace(repo)
    # The edit lands while the build is still in flight.
    (repo / "mod.py").write_text("value = 2\n", encoding="utf-8")
    post = capture_workspace(repo)
    adapter.record_edit_transaction(
        diff_workspace(pre, post, action_id=1, command="edit mod.py")
    )

    produced = _revision(tmp_path / "graph", "boot")
    adapter._startup_index = _DoneFuture(
        _startup_receipt(adapter, produced / "graph.db",
                         adapter.engine_state.graph_source_revision or "stale")
    )
    # The receipt predates the edit: its source_revision is the task-start one.
    adapter._startup_index = _DoneFuture(
        IndexBuildReceipt(
            IndexBuildStatus.BUILT,
            graph_db=str(produced / "graph.db"),
            graph_revision=_sha(produced / "graph.db"),
            source_revision=str(pre.revision),
        )
    )
    monkeypatch.setattr(
        adapter, "_schedule_lsp_candidate",
        lambda request, base: SimpleNamespace(task_id="leg-next"),
    )
    try:
        adapter._poll_startup_index()
        ready = _journal_rows(adapter, "initial_index_ready")
        assert ready and ready[-1]["adopted"] is False
        assert adapter._unadopted_graph[0] == str(produced / "graph.db")

        # The boundary amend builds ON the unadopted fallback parent.
        seen_parents: list[str] = []

        def amend(root, *, parent_graph, changed_paths, **kwargs):
            seen_parents.append(str(parent_graph))
            amended = _revision(tmp_path / "graph", "amended-1")
            return str(amended / "graph.db"), "", (
                {"path": "mod.py", "updated": 1},
            )

        monkeypatch.setattr(
            indexer, "_ensure_index_incremental_unlocked", amend
        )
        monkeypatch.setattr(
            indexer, "_receipt_for_published_graph",
            lambda graph_path, **kwargs: IndexBuildReceipt(
                IndexBuildStatus.BUILT_CORE_ONLY, graph_db=graph_path,
                graph_revision=_sha(Path(graph_path)),
                build_mode="incremental",
                source_revision=str(kwargs.get("source_revision") or ""),
            ),
        )
        adapter.refresh_graph(phase="audit")

        assert seen_parents == [str(produced / "graph.db")], (
            "the superseded startup build is the amend's certified parent"
        )
        assert "amended-1" in str(adapter.engine_state.graph_path)
        # The prune the adoption ran protected the fallback parent.
        assert produced.is_dir(), (
            "a live amend chain's base must survive the adoption prune"
        )
    finally:
        adapter.close_graph_lifecycle()


def test_rapid_edits_while_a_leg_is_in_flight_stage_the_exact_stale_set(
    tmp_path, monkeypatch
):
    """Rapid edits vs a pending leg: the salvage's stale set is exactly the
    paths edited after the schedule epoch -- no more, no less."""
    adapter = _adapter(tmp_path)
    repo = Path(adapter.repo_root)
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x = 1\n", encoding="utf-8")
    live = tmp_path / "live.db"
    live.write_bytes(b"live-graph")
    (tmp_path / "live.manifest.json").write_text(
        json.dumps({"graph_sha256": _sha(live)}), encoding="utf-8"
    )
    adapter.engine_state.graph_path = str(live)
    adapter.engine_state.graph_revision = "rev-1"
    adapter.engine_state.source_revision = "src-1"
    adapter.engine_state.graph_source_revision = "src-1"
    adapter.engine_state._graph_usable = True

    request = FrozenBuildInput("src-1", ("x.py",), (("x.py", b"x"),))
    base = GraphBuildArtifact(True, str(live), "rev-1")
    # The staged-candidate wal_checkpoint opens the file for real: a bare
    # blob is not a database and the salvage would refuse unreadable.
    candidate = tmp_path / "cand.db"
    with closing(sqlite3.connect(candidate)) as db:
        db.execute("CREATE TABLE mutations (k)")
        db.commit()
    handle = _Handle(
        "leg-r", _terminal("leg-r", request, base, candidate)
    )
    adapter._lsp_scheduler = _lsp_scheduler(handle)
    adapter._lsp_bases["leg-r"] = (request, base)
    adapter._lsp_epochs["leg-r"] = adapter._edit_epoch
    adapter._lsp_requests["leg-r"] = SimpleNamespace(
        repository_root=str(repo), source_revision="src-1",
        graph_revision="rev-1", candidate_path=str(candidate),
        repository_snapshot_sha256="0" * 64,
    )

    # Each record_edit_transaction fires the synchronous amend inline; a
    # real adopt per edit is what keeps graph_current true for the drain.
    def amend(root, *, parent_graph, changed_paths, **kwargs):
        amended = _revision(tmp_path / "graph",
                            f"amended-{len(changed_paths)}-{len(list(amends))}")
        amends.append(amended)
        return str(amended / "graph.db"), "", (
            {"path": path, "updated": 1} for path in changed_paths
        )

    amends: list[Path] = []
    monkeypatch.setattr(
        indexer, "_ensure_index_incremental_unlocked", amend
    )
    monkeypatch.setattr(
        indexer, "_receipt_for_published_graph",
        lambda graph_path, **kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT_CORE_ONLY, graph_db=graph_path,
            graph_revision=_sha(Path(graph_path)),
            build_mode="incremental",
            source_revision=str(kwargs.get("source_revision") or ""),
        ),
    )

    # Three rapid edits while the leg runs: two enumerated, one not.
    pre = capture_workspace(repo)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    post = capture_workspace(repo)
    adapter.record_edit_transaction(
        diff_workspace(pre, post, action_id=1, command="edit a.py")
    )
    pre = post
    (repo / "b.py").write_text("x = 2\n", encoding="utf-8")
    post = capture_workspace(repo)
    adapter.record_edit_transaction(
        diff_workspace(pre, post, action_id=2, command="edit b.py")
    )
    # c.py never touched -> not stale.

    monkeypatch.setattr(
        adapter, "_certify_lsp_candidate",
        lambda request, base, terminal: GraphBuildArtifact(
            True, str(terminal["candidate_path"]), "rev-adopted"
        ),
    )
    try:
        adapter._poll_lsp_promotions()
        scheduled = _journal_rows(adapter, "lsp_salvage_scheduled")
        assert scheduled, "an obsolete leg with clean coverage must salvage"
        assert scheduled[-1]["stale_path_count"] == 2, (
            "the stale set is exactly the post-schedule edits"
        )
        staged = candidate.with_name(candidate.name + ".salvage")
        assert staged.is_file(), "staging renames the candidate for the merge"
        assert not candidate.exists()
    finally:
        adapter.close_graph_lifecycle()


def test_an_unenumerated_edit_during_the_leg_blocks_salvage(
    tmp_path, monkeypatch
):
    """An edit the transaction layer could not enumerate makes 'untouched
    since' unknowable: salvage refuses rather than merge on faith."""
    adapter = _adapter(tmp_path)
    repo = Path(adapter.repo_root)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    live = tmp_path / "live.db"
    live.write_bytes(b"live-graph")
    (tmp_path / "live.manifest.json").write_text(
        json.dumps({"graph_sha256": _sha(live)}), encoding="utf-8"
    )
    adapter.engine_state.graph_path = str(live)
    adapter.engine_state.graph_revision = "rev-1"
    adapter.engine_state.source_revision = "src-1"
    adapter.engine_state.graph_source_revision = "src-1"
    adapter.engine_state._graph_usable = True

    request = FrozenBuildInput("src-1", ("x.py",), (("x.py", b"x"),))
    base = GraphBuildArtifact(True, str(live), "rev-1")
    candidate = tmp_path / "cand.db"
    candidate.write_bytes(b"cand")
    handle = _Handle(
        "leg-u", _terminal("leg-u", request, base, candidate)
    )
    adapter._lsp_scheduler = _lsp_scheduler(handle)
    adapter._lsp_bases["leg-u"] = (request, base)
    adapter._lsp_epochs["leg-u"] = adapter._edit_epoch
    adapter._lsp_requests["leg-u"] = SimpleNamespace(
        repository_root=str(repo), source_revision="src-1",
        graph_revision="rev-1", candidate_path=str(candidate),
    )

    # The unenumerated edit: a real transaction through the real intake,
    # marked incomplete -- record_edit_transaction poisons the stale-set
    # epoch for every enrichment scheduled before it.
    pre = capture_workspace(repo)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    post = capture_workspace(repo)
    transaction = diff_workspace(pre, post, action_id=1, command="edit a.py")
    transaction = type(transaction)(
        **{**transaction.__dict__, "complete": False,
           "omissions": ("partial_capture",)}
    )
    adapter.record_edit_transaction(transaction)

    adapter.engine_state.graph_revision = "rev-2"
    try:
        adapter._poll_lsp_promotions()
        refused = _journal_rows(adapter, "lsp_salvage_refused")
        assert refused and refused[-1]["reason"] == "unenumerated_edit", (
            "an unknowable stale set must refuse the merge"
        )
    finally:
        adapter.close_graph_lifecycle()


# -- interruptions -------------------------------------------------------------


def test_interruption_before_publication_seals_clean(tmp_path, monkeypatch):
    """A leg still in flight at close: bounded drain, journaled stall, and
    no post-seal journal writes -- the conservation invariant holds."""
    from tests.test_workload_simulation import _Sim

    sim = _Sim(monkeypatch, tmp_path)
    sim.boundary()
    assert sim.in_flight, "the scenario needs a leg in flight"
    # Never released: the leg is still running when the run ends.
    sim.adapter.close_graph_lifecycle()

    convergence = sim.journal("lsp_seal_convergence")
    assert convergence and convergence[-1]["outcome"] == "drain_incomplete", (
        f"a stalled leg must seal as a typed gap: {convergence}"
    )
    drained = sim.journal("lsp_promotion_drained")
    assert drained and drained[-1]["stalled"] >= 1, drained
    terminals = sim.journal("lsp_promotion_terminal")
    assert not any(
        row.get("input_graph_revision") == sim.adapter.engine_state.graph_revision
        and row.get("disposition") == "published" for row in terminals
    ), "a stalled leg cannot publish"
    verification = verify_event_journal(sim.adapter.store.path)
    assert verification.valid, verification.issues


def test_interruption_after_admission_keeps_the_delivered_pin(tmp_path):
    """Admitted, delivered, then superseded: the pin survives every prune
    and the delivered graph still certifies at seal."""
    layout, repo, rev1, lsp_a, lsp_b, merged = _deep_chain(tmp_path)
    root_sha = _root_sha(repo)

    adapter = _adapter(tmp_path / "run")
    # A delivered advisory pins the revision it was ranked from.
    adapter._pin_graph_revision(str(rev1), "d" * 64)
    assert (rev1.parent / "pinned.json").is_file()

    later = _certified_revision(layout, repo, "later")
    _graph_mtime(rev1.parent, 1000)
    _graph_mtime(later.parent, 4000)
    prune_graph_revisions(later.parent)

    assert rev1.parent.is_dir()
    valid, reason = certify_graph_artifact(
        rev1, rev1.with_suffix(".manifest.json"),
        expected_root_sha256=root_sha,
        expected_source_revision="source-r1",
        expected_task_id="audit", expected_product_source_sha="4" * 40,
    )
    assert valid, reason
    adapter.close_graph_lifecycle()


def test_interruption_during_finalization_cannot_append_post_seal(
    tmp_path, monkeypatch
):
    """A salvage merge still running at close finishes into a sealed run:
    its outcome row never lands (the drain is over), and the journal still
    verifies -- post-seal writes are dropped, not smuggled."""
    from tests.test_workload_simulation import _Sim

    sim = _Sim(monkeypatch, tmp_path)
    # The drain loop's sleep is a real sleep; with the clock frozen the
    # 2000-iteration cap is what ends it -- make the spin cheap.
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.sleep", lambda _s: None
    )

    blocker = threading.Event()
    finished = threading.Event()

    def blocking_salvage(*, base_graph_path, staged_candidate,
                         stale_paths, scheduled):
        def work():
            blocker.wait(10.0)
            # Post-seal the worker still writes its merge artifact --
            # inert on disk, never journaled.
            out = sim.tmp_path / "post-seal-merge.db"
            out.write_bytes(b"merged-after-seal")
            finished.set()
            return {"outcome": "merged", "graph_path": str(out),
                    "graph_revision": "r-x", "live_graph_path": "",
                    "source_revision": ""}
        return work

    monkeypatch.setattr(sim.adapter, "_salvage_work", blocking_salvage)

    sim.boundary()
    sim.edit("mod.py")
    sim.finish_legs()
    # The obsolete drain stages + enqueues the salvage.
    sim.provider_wait()
    scheduled = sim.journal("lsp_salvage_scheduled")
    assert scheduled, "the scenario needs a salvage in flight"

    # Launch it, then close while it is still running.
    sim.adapter.provider_wait_begin()
    assert sim.adapter._wait_scheduler is not None
    deadline = 100
    while deadline and not sim.adapter._wait_scheduler._running:
        import time as _t
        _t.sleep(0.005)
        deadline -= 1
    assert sim.adapter._wait_scheduler._running, "salvage must be running"

    sim.adapter.close_graph_lifecycle()

    rows_at_seal = len(_journal_rows(sim.adapter))
    blocker.set()
    assert finished.wait(10.0), "the worker finishes after seal"
    assert len(_journal_rows(sim.adapter)) == rows_at_seal, (
        "a worker finishing post-seal must not append"
    )
    verification = verify_event_journal(sim.adapter.store.path)
    assert verification.valid, verification.issues
    # The typed gap is journaled: scheduled without outcome is honest.
    assert sim.journal("lsp_salvage") == [] or all(
        row.get("outcome") for row in sim.journal("lsp_salvage")
    )


def test_close_under_memory_pressure_is_a_typed_gap_not_a_crash(
    tmp_path, monkeypatch
):
    """Pressure held to seal: convergence is honestly skipped, the journal
    verifies, and nothing escalates a heavier build inside the window."""
    from tests.test_workload_simulation import _Sim

    sim = _Sim(monkeypatch, tmp_path)
    sim.pressure = True
    for _ in range(8):
        sim.edit("mod.py", advance=15.0)
        sim.boundary()
    sim.adapter.close_graph_lifecycle()

    assert sim.build_spawns == [], "a recovery build fired inside pressure"
    events = sim.refresh_events()
    assert all(
        event.severity == "WARNING" and event.retryable for event in events
    )
    verification = verify_event_journal(sim.adapter.store.path)
    assert verification.valid, verification.issues


# ---------------------------------------------------------------------------
# 4. cross-boundary reproducers (owner: miniswe_integration.py)
# ---------------------------------------------------------------------------


def _old_enrichment(namespace: Path, name: str, *,
                    manifest: dict | None = None) -> Path:
    directory = namespace / name
    directory.mkdir(parents=True)
    (directory / "graph.db").write_bytes(f"g-{name}".encode())
    (directory / "graph.manifest.json").write_text(
        json.dumps(manifest or {"graph_sha256": name}), encoding="utf-8"
    )
    return directory


def test_xbound_sweep_preserves_the_live_chains_cited_enrichment_base(
    tmp_path, monkeypatch
):
    """Restart while an older dependency is still required by the live graph.

    The live graph is an adopted enrichment (lsp-b) whose derivation cites
    enrichments/lsp-a. Both predate this process. The sweep protects only
    the live directory itself -- it deletes the base the live manifest still
    reads through, which is the 35016130850 failure one namespace up.
    """
    adapter = _adapter(tmp_path)
    namespace = adapter.engine_state.layout.graph_root / "enrichments"
    lsp_a = _old_enrichment(namespace, "lsp-aaaa")
    lsp_b = _old_enrichment(
        namespace, "lsp-bbbb",
        manifest={
            "graph_root": "../..",
            "derivation": {
                "phase": "lsp",
                "base_graph": "../lsp-aaaa/graph.db",
                "base_manifest": "../lsp-aaaa/graph.manifest.json",
                "base_resource": "../lsp-aaaa/index-resource.json",
            },
        },
    )
    # The adopted enrichment is the live graph.
    adapter.engine_state.graph_path = str(lsp_b / "graph.db")
    old = adapter._PROCESS_START - 1000
    os.utime(lsp_a, (old, old))
    os.utime(lsp_b, (old, old))

    adapter._sweep_dead_enrichments(namespace)

    assert lsp_b.is_dir(), "the live enrichment is protected by name"
    assert lsp_a.is_dir(), (
        "lsp-b's manifest still cites lsp-a; deleting it breaks the live "
        "graph's derivation chain (derivation_base_manifest_unreadable)"
    )
    adapter.close_graph_lifecycle()


def test_xbound_sweep_preserves_a_previously_adopted_enrichment(tmp_path):
    """A published enrichment superseded before restart: a delivered
    semantic-localization advisory can still name its graph_sha, and
    verify_runtime_receipt demands exactly one surviving certified graph
    matching it. Protection is the delivery-time pin -- _pin_graph_revision
    now accepts enrichments/ paths -- and the sweep honours pinned.json even
    when the directory is neither live nor fresh. An unpinned superseded
    sibling is genuinely dead and is swept."""
    adapter = _adapter(tmp_path)
    namespace = adapter.engine_state.layout.graph_root / "enrichments"
    adopted_earlier = _old_enrichment(namespace, "lsp-dead")
    orphan = _old_enrichment(namespace, "lsp-orphan")
    live_revision = _revision(
        adapter.engine_state.layout.graph_root, "live"
    )
    adapter.engine_state.graph_path = str(live_revision / "graph.db")
    adapter._pin_graph_revision(
        str(adopted_earlier / "graph.db"), "d" * 64
    )
    old = adapter._PROCESS_START - 1000
    os.utime(adopted_earlier, (old, old))
    os.utime(orphan, (old, old))

    adapter._sweep_dead_enrichments(namespace)

    assert adopted_earlier.is_dir(), (
        "an enrichment a delivered advisory names is receipted evidence, "
        "not a dead candidate"
    )
    assert not orphan.exists(), (
        "the protection is not vacuous: the unpinned orphan was swept"
    )
    adapter.close_graph_lifecycle()


def test_xbound_a_delivered_enrichment_graph_can_be_pinned(tmp_path):
    """The companion hole is closed: the pin writer honours enrichments/
    paths, so an advisory delivered against an adopted enrichment is pinned
    at delivery and the restart sweep preserves it."""
    adapter = _adapter(tmp_path)
    namespace = adapter.engine_state.layout.graph_root / "enrichments"
    enrichment = _old_enrichment(namespace, "lsp-live")
    adapter._pin_graph_revision(str(enrichment / "graph.db"), "e" * 64)
    assert (enrichment / "pinned.json").is_file(), (
        "a delivered artifact's graph must be pinnable wherever it lives"
    )
    adapter.close_graph_lifecycle()


# ---------------------------------------------------------------------------
# 5. D6 end-to-end through the real adapter
# ---------------------------------------------------------------------------


class _SimRevisions:
    """_Sim variant whose produced graphs live under the real revision
    scheme, so adoption actually drives the pruner."""

    def __init__(self, monkeypatch, tmp_path):
        from tests.test_workload_simulation import _Sim

        self._sim = _Sim(monkeypatch, tmp_path)
        self.adapter = self._sim.adapter
        self.layout = self.adapter.engine_state.layout
        self._seq = 0
        sim = self._sim

        def new_graph(tag, *, parent_sha=None, build_mode=None):
            self._seq += 1
            directory = self.layout.graph_root / "revisions" / f"{tag}-{self._seq}"
            directory.mkdir(parents=True, exist_ok=True)
            graph = directory / "graph.db"
            graph.write_bytes(f"{tag}-graph-{self._seq}".encode())
            return str(graph), sim._publish_manifest(
                graph, parent_sha=parent_sha, build_mode=build_mode
            )

        sim._new_graph = new_graph


def test_d6_refresh_nested_enrichment_prune_and_seal_certifies(
    tmp_path, monkeypatch
):
    """The assigned scenario D6 end to end through the real adapter:

    refresh (startup build superseded, then an amend adopted) ->
    nested enrichment (a salvage merge published onto the live graph,
    then the seal-time leg published on top of the merge -- merge-on-lsp
    at the journal level) -> pruning (the adoption prune keeps the
    fallback parent and the delivered pin) -> final certification (the
    journal verifies and the tier reads WORKING).
    """
    from gt_engine.miniswe_integration import GraphBuildArtifact as GBA
    from tests.test_workload_simulation import _Sim

    sim = _Sim(monkeypatch, tmp_path)

    def certify(request, base, terminal):
        candidate = Path(str(terminal["candidate_path"]))
        return GBA(
            True, str(candidate),
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )

    monkeypatch.setattr(sim.adapter, "_certify_lsp_candidate", certify)

    # A leg scheduled from inside close finishes there.
    schedule = sim.adapter._schedule_lsp_candidate

    def releasing(request, base):
        handle = schedule(request, base)
        handle.release()
        return handle

    monkeypatch.setattr(
        sim.adapter, "_schedule_lsp_candidate", releasing
    )

    # refresh -> leg scheduled -> edit races it -> obsolete -> salvage.
    sim.boundary()
    sim.edit("mod.py")
    sim.finish_legs()
    sim.provider_wait()   # drains obsolete, stages salvage
    sim.provider_wait()   # launches + drains the merge, publishes it
    sim.boundary()
    salvages = sim.journal("lsp_salvage")
    assert salvages and salvages[-1].get("outcome") == "published", (
        f"D6 needs the nested enrichment adopted: {salvages}"
    )
    merged_revision = salvages[-1]["graph_revision"]

    # seal-time: the merged (enrichment) graph gets the leg nothing can
    # obsolete -- the second derivation hop.
    sim.adapter.close_graph_lifecycle()

    scheduled = sim.journal("lsp_promotion_scheduled")
    assert any(
        row.get("graph_revision") == merged_revision for row in scheduled
    ), f"seal never offered the merged graph a leg: {scheduled}"
    terminals = sim.journal("lsp_promotion_terminal")
    assert terminals[-1].get("disposition") == "published", terminals[-1]

    verification = verify_event_journal(sim.adapter.store.path)
    assert verification.valid, verification.issues

    caps = sim.capabilities()
    assert caps["lsp_promotion"][0] == "WORKING", caps["lsp_promotion"]
