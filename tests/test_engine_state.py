from __future__ import annotations

from types import SimpleNamespace

from gt_engine.engine_state import EngineState, QueryCompleteness


def change(path, operation, before, after):
    return SimpleNamespace(path=path, operation=operation, before_sha256=before,
                           after_sha256=after, before=None, after=b"new" if after else None)


def transaction(*changes, complete=True):
    return SimpleNamespace(post_revision="work-2", changes=changes,
                           complete=complete, omissions=() if complete else ("unreadable:x",))


def test_overlay_masks_deleted_and_superseded_base_paths():
    state = EngineState(graph_path="base.db", graph_revision="graph-1",
                        source_revision="work-1")
    state.apply_transaction(transaction(
        change("gone.py", "delete", "a" * 64, None),
        change("changed.py", "modify", "b" * 64, "c" * 64),
    ))
    view = state.query_snapshot()
    assert view.completeness is QueryCompleteness.CURRENT_PARTIAL
    assert view.graph_path == ""
    assert view.masked_paths == ("changed.py", "gone.py")
    assert view.overlay["gone.py"].operation == "delete"


def test_incomplete_transaction_never_supports_absence_claims():
    state = EngineState(graph_path="base.db", graph_revision="graph-1",
                        source_revision="work-1")
    state.apply_transaction(transaction(change("x.py", "modify", "a", "b"),
                                        complete=False))
    assert not state.query_snapshot().absence_claims_allowed


def test_publishing_current_graph_clears_only_included_overlay():
    state = EngineState(graph_path="base.db", graph_revision="graph-1",
                        source_revision="work-1")
    state.apply_transaction(transaction(change("x.py", "modify", "a", "b")))
    assert state.publish_graph(graph_path="next.db", graph_revision="graph-2",
                               source_revision="work-2")
    view = state.query_snapshot()
    assert view.completeness is QueryCompleteness.CURRENT_COMPLETE
    assert view.graph_path == "next.db"
    assert not view.overlay


def test_obsolete_graph_publication_cannot_clear_newer_overlay():
    state = EngineState(graph_path="base.db", graph_revision="graph-1",
                        source_revision="work-1")
    state.apply_transaction(transaction(change("x.py", "modify", "a", "b")))
    assert not state.publish_graph(graph_path="old.db", graph_revision="old",
                                   source_revision="work-1")
    assert state.query_snapshot().masked_paths == ("x.py",)
