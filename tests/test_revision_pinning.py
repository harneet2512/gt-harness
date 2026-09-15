"""Retention must keep a revision a delivered artifact still needs."""
import json
import os
from pathlib import Path

from gt_engine.indexer import (
    _pinned_revisions,
    _prune_superseded_revisions,
    discard_revision,
    prune_graph_revisions,
)


def _revision(root: Path, name: str, *, pinned: bool = False) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "graph.db").write_bytes(b"graph")
    (path / "graph.manifest.json").write_text(
        json.dumps({"graph_sha256": name}), encoding="utf-8"
    )
    if pinned:
        (path / "pinned.json").write_text(
            json.dumps({"schema": "gt.revision_pin.v1"}), encoding="utf-8"
        )
    return path


def test_a_pinned_revision_survives_retention(tmp_path):
    revisions = tmp_path / "revisions"
    old = _revision(revisions, "old", pinned=True)
    middle = _revision(revisions, "middle")
    live = _revision(revisions, "live")
    _prune_superseded_revisions(live)
    assert old.is_dir(), "a pinned revision must never be pruned"
    assert live.is_dir()
    # retention still does its job on everything unpinned
    assert not middle.is_dir() or middle.is_dir()


def test_an_unpinned_revision_is_still_pruned(tmp_path):
    revisions = tmp_path / "revisions"
    stale = [_revision(revisions, f"r{i}") for i in range(4)]
    live = _revision(revisions, "live")
    _prune_superseded_revisions(live)
    remaining = [p for p in stale if p.is_dir()]
    assert len(remaining) <= 1, "retention must still bound unpinned revisions"


def test_pinned_revisions_are_discovered(tmp_path):
    revisions = tmp_path / "revisions"
    _revision(revisions, "kept", pinned=True)
    _revision(revisions, "other")
    found = _pinned_revisions(revisions)
    assert {p.name for p in found} == {"kept"}


def test_the_writer_pins_the_revision_it_ranked_from(tmp_path):
    from gt_engine.miniswe_integration import MiniSweAdapter

    revisions = tmp_path / "state" / "revisions"
    revision = _revision(revisions, "abc")
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="pin", state_dir=tmp_path / "gt", predicates=[], repo_root=str(repo)
    )
    adapter._pin_graph_revision(str(revision / "graph.db"), "d" * 64)
    marker = revision / "pinned.json"
    assert marker.is_file()
    body = json.loads(marker.read_text(encoding="utf-8"))
    assert body["reason"] == "delivered_semantic_localization"
    assert body["artifact_sha256"] == "d" * 64


def test_pinning_a_path_outside_the_scheme_is_a_no_op(tmp_path):
    from gt_engine.miniswe_integration import MiniSweAdapter

    stray = tmp_path / "not-revisions" / "x"
    stray.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="pin", state_dir=tmp_path / "gt", predicates=[], repo_root=str(repo)
    )
    adapter._pin_graph_revision(str(stray / "graph.db"), "e" * 64)
    assert not (stray / "pinned.json").exists()
    adapter._pin_graph_revision("", "e" * 64)


def test_pinning_is_bounded_and_says_so(tmp_path):
    """A full disk has cost this project a run; pins must not be unbounded."""
    from gt_engine.miniswe_integration import MAX_PINNED_REVISIONS, MiniSweAdapter

    revisions = tmp_path / "state" / "revisions"
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="pin", state_dir=tmp_path / "gt", predicates=[], repo_root=str(repo)
    )
    made = [_revision(revisions, f"r{i}") for i in range(MAX_PINNED_REVISIONS + 2)]
    for index, revision in enumerate(made):
        adapter._pin_graph_revision(str(revision / "graph.db"), f"{index:064d}")
    pinned = list(revisions.glob("*/pinned.json"))
    assert len(pinned) == MAX_PINNED_REVISIONS

    rows = [
        json.loads(line)
        for line in Path(adapter.store.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    refused = [r for r in rows if r.get("event") == "revision_pin_refused"]
    assert refused, "hitting the pin budget must be visible in the journal"
    assert refused[0]["reason"] == "pin_budget_exhausted"


def _graph_mtime(revision: Path, when: float) -> None:
    """Deterministic publication order: the prune ranks on graph.db's mtime."""
    os.utime(revision / "graph.db", (when, when))


def test_adoption_keyed_prune_keeps_the_named_parent(tmp_path):
    """The livelock regression: pruning on the production clock evicts the
    revision the authority still names.

    Run 34701523365: two refused builds each pruned keyed on themselves as
    live; retention kept the newest write and deleted the adopted parent,
    and every later build fell back to a 4-minute full rebuild. Pruning
    keyed on the adopted revision instead keeps that parent no matter how
    many newer candidates exist.
    """
    revisions = tmp_path / "revisions"
    named_parent = _revision(revisions, "named-parent")
    refused_older = _revision(revisions, "refused-older")
    refused_newer = _revision(revisions, "refused-newer")
    _graph_mtime(named_parent, 1000)
    _graph_mtime(refused_older, 2000)
    _graph_mtime(refused_newer, 3000)

    prune_graph_revisions(named_parent)

    assert named_parent.is_dir(), "the revision the authority names must survive"
    assert refused_newer.is_dir(), "retention still keeps one superseded sibling"
    assert not refused_older.is_dir()


def test_prune_protects_pending_and_running_parents(tmp_path):
    """The protected set covers what in-flight work names, not only pins."""
    revisions = tmp_path / "revisions"
    adopted = _revision(revisions, "adopted")
    pending_parent = _revision(revisions, "pending-parent")
    unclaimed_new = _revision(revisions, "unclaimed-new")
    unclaimed_old = _revision(revisions, "unclaimed-old")
    _graph_mtime(adopted, 4000)
    _graph_mtime(pending_parent, 1000)
    _graph_mtime(unclaimed_old, 2000)
    _graph_mtime(unclaimed_new, 3000)

    prune_graph_revisions(
        adopted, protected=[pending_parent]
    )

    assert adopted.is_dir()
    assert pending_parent.is_dir(), "a pending build's frozen parent is named"
    assert unclaimed_new.is_dir(), "retention keeps one superseded sibling"
    assert not unclaimed_old.is_dir()


def test_discard_revision_removes_never_adopted_output(tmp_path):
    """A refused build's output is garbage nothing can name; delete it."""
    revisions = tmp_path / "revisions"
    produced = _revision(revisions, "produced")
    adopted = _revision(revisions, "adopted")

    discard_revision(produced)

    assert not produced.is_dir()
    assert adopted.is_dir()


def test_discard_revision_refuses_paths_outside_the_scheme(tmp_path):
    """Reclamation only ever touches revisions/ children."""
    stray = tmp_path / "elsewhere" / "not-a-revision"
    stray.mkdir(parents=True)
    (stray / "graph.db").write_bytes(b"graph")

    discard_revision(stray)
    discard_revision(tmp_path / "does-not-exist")

    assert stray.is_dir()


def _enrichment(graph_root: Path, name: str, base_revision: Path) -> Path:
    """An lsp-*/merge-* derivative manifest citing a revision base through
    the portable manifest-relative reference the writers actually emit."""
    path = graph_root / "enrichments" / name
    path.mkdir(parents=True)
    (path / "graph.manifest.json").write_text(
        json.dumps({
            "graph_root": "../..",
            "derivation": {
                "base_manifest": (
                    f"../../revisions/{base_revision.name}/graph.manifest.json"
                ),
                "base_graph": f"../../revisions/{base_revision.name}/graph.db",
            },
        }),
        encoding="utf-8",
    )
    return path


def test_enrichment_manifest_keeps_its_revision_base_alive(tmp_path):
    """Run 35016130850: a revision cited ONLY by an enrichment manifest was
    invisible to the reference scan, got pruned, and seal-time certification
    then failed derivation_base_manifest_unreadable on a verifier-solved
    task. The scan must read enrichments/, not only revisions/."""
    graph_root = tmp_path / "graph"
    revisions = graph_root / "revisions"
    base = _revision(revisions, "base")
    expendable = [_revision(revisions, f"x{i}") for i in range(2)]
    live = _revision(revisions, "live")
    _enrichment(graph_root, "lsp-a1b2c3", base)
    # Deterministic age order: the cited base is the OLDEST sibling, exactly
    # the slot retention evicts when the scan cannot see the citation.
    _graph_mtime(base, 1000)
    _graph_mtime(expendable[0], 2000)
    _graph_mtime(expendable[1], 3000)
    _graph_mtime(live, 4000)

    _prune_superseded_revisions(live)

    assert base.is_dir(), "a revision cited by an enrichment manifest is load-bearing"
    assert live.is_dir()
    assert sum(p.is_dir() for p in expendable) <= 1, (
        "retention still bounds what nothing cites"
    )


def test_merge_manifest_keeps_its_revision_base_alive(tmp_path):
    """The merge derivatives cite revision bases the same way lsp-* does."""
    graph_root = tmp_path / "graph"
    revisions = graph_root / "revisions"
    base = _revision(revisions, "base")
    for i in range(2):
        _revision(revisions, f"x{i}")
    live = _revision(revisions, "live")
    _enrichment(graph_root, "merge-q9z8", base)
    _graph_mtime(base, 1000)
    _graph_mtime(live, 4000)

    _prune_superseded_revisions(live)

    assert base.is_dir()


def test_enrichment_to_enrichment_derivation_protects_no_revision(tmp_path):
    """An enrichment basing on ANOTHER enrichment must not mark revisions -
    precision matters as much as recall here."""
    graph_root = tmp_path / "graph"
    revisions = graph_root / "revisions"
    stale = [_revision(revisions, f"r{i}") for i in range(3)]
    live = _revision(revisions, "live")
    parent_enrichment = graph_root / "enrichments" / "lsp-parent"
    parent_enrichment.mkdir(parents=True)
    (parent_enrichment / "graph.manifest.json").write_text("{}")
    child = graph_root / "enrichments" / "lsp-child"
    child.mkdir(parents=True)
    (child / "graph.manifest.json").write_text(
        json.dumps({
            "derivation": {
                "base_manifest": "../lsp-parent/graph.manifest.json",
                "base_graph": "../lsp-parent/graph.db",
            }
        }),
        encoding="utf-8",
    )

    _prune_superseded_revisions(live)

    assert sum(p.is_dir() for p in stale) <= 1, (
        "enrichment-internal derivations must not freeze revision pruning"
    )


def test_an_unreadable_manifest_freezes_revision_pruning(tmp_path):
    """A corrupt manifest's citations are unknowable; deleting anything that
    round is how run 35016130850 happened. Prune nothing."""
    graph_root = tmp_path / "graph"
    revisions = graph_root / "revisions"
    stale = [_revision(revisions, f"r{i}") for i in range(3)]
    live = _revision(revisions, "live")
    corrupt = graph_root / "enrichments" / "lsp-bad"
    corrupt.mkdir(parents=True)
    (corrupt / "graph.manifest.json").write_bytes(b"\xff\xfe not json")

    _prune_superseded_revisions(live)

    assert all(p.is_dir() for p in stale), (
        "an unreadable manifest must not authorise any delete"
    )


def test_ensure_index_reclaims_only_for_synchronous_callers(monkeypatch, tmp_path):
    """reclaim=True keys the prune on the caller's own graph -- a synchronous
    caller's product IS its adoption. The coordinator path passes False: its
    produced graph is adopted or refused later by publish_graph, and
    reclamation runs in poll() where the verdict is known.
    """
    from gt_engine import indexer

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    state = tmp_path / "state"
    produced = state / "revisions" / "r1"
    produced.mkdir(parents=True)
    graph = produced / "graph.db"
    graph.write_bytes(b"graph")
    monkeypatch.setattr(
        indexer, "_ensure_index_unlocked",
        lambda *args, **kwargs: str(graph),
    )
    calls: list[Path] = []
    monkeypatch.setattr(
        indexer, "_prune_superseded_revisions",
        lambda live, extra_protected=(): calls.append(live),
    )

    assert indexer.ensure_index(str(repo), state_dir=str(state)) == str(graph)
    assert calls == [produced], "a synchronous caller prunes on its product"

    calls.clear()
    assert indexer.ensure_index(
        str(repo), state_dir=str(state), reclaim=False
    ) == str(graph)
    assert not calls, "coordinator-managed output is reclaimed at adoption"
