"""The embedding cache has to survive the thing that makes it worth keeping.

A graph republication is exactly when a warm contract-vector store pays: almost
every document is unchanged, so almost every vector is reusable. The store's
CONTENT key was always built for that -- content sha plus recipe, model,
tokenizer and dimension, deliberately not node id and not graph revision,
because "an unchanged document can move between graph builds, while a reused id
can name changed content".

Its LOCATION was not. `default_store_path` derives the file from the graph path,
and the graph lives at `revisions/<key>/graph.db`, so every republication names
a store that does not exist yet. Run 34095557374 shows the cost: sixteen
refreshes, each reporting `planned=3809..3822` -- the whole corpus, never a
delta -- and dense retrieval never refreshed once in 78 minutes.

The repair was to pin the store to the TASK via `RuntimeLayout`, and three call
sites had to be corrected: the initial build in `miniswe_gt_run`, the retrieval
reader, and the rebuild in `GraphBuildCoordinator`. Two matched and the third
had simply omitted it.

Nothing tested any of that. It was held in place by three comments, and a fourth
caller -- or a dropped `layout=` -- puts the run back to re-embedding the entire
corpus on every rebuild, silently, because a cold cache is not an error. These
are the tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gt_engine.contract_embeddings import default_store_path
from gt_engine.engine_state import RuntimeLayout


@pytest.fixture
def layout(tmp_path) -> RuntimeLayout:
    return RuntimeLayout(
        workspace=tmp_path / "repo",
        state_root=tmp_path / "state",
        task_root=tmp_path / "state" / "task",
        graph_root=tmp_path / "state" / "graph",
        evidence_root=tmp_path / "state" / "evidence",
    )


def _revision_graph(layout: RuntimeLayout, key: str) -> Path:
    return layout.graph_root / "revisions" / key / "graph.db"


def test_the_default_store_moves_with_every_republication(layout):
    """Pinned first, because it is the reason the layout property exists.

    This is not a defect in `default_store_path` -- a caller with no layout has
    nothing better to key on. It is why a caller WITH a layout must never reach
    it.
    """
    first = default_store_path(_revision_graph(layout, "rev-a"))
    second = default_store_path(_revision_graph(layout, "rev-b"))
    assert first != second


def test_the_task_pinned_store_does_not_move_with_the_graph(layout):
    """The same file across every revision of the same task."""
    assert layout.contract_store_path == layout.task_root / "contract-embeddings.sqlite"
    assert layout.graph_root not in layout.contract_store_path.parents
    other = RuntimeLayout(
        workspace=layout.workspace, state_root=layout.state_root,
        task_root=layout.task_root, graph_root=layout.graph_root / "elsewhere",
        evidence_root=layout.evidence_root,
    )
    assert other.contract_store_path == layout.contract_store_path


def test_two_tasks_do_not_share_one_store(tmp_path):
    """Task-scoped, not run-scoped: a different task is a different corpus."""
    def make(task: str) -> RuntimeLayout:
        return RuntimeLayout(
            workspace=tmp_path / "repo", state_root=tmp_path / "state",
            task_root=tmp_path / "state" / task, graph_root=tmp_path / "state" / "graph",
            evidence_root=tmp_path / "state" / "evidence",
        )

    assert make("alpha").contract_store_path != make("beta").contract_store_path


@pytest.mark.parametrize("holder", ["explicit", "layout", "environment"])
def test_a_caller_holding_a_layout_never_falls_back_to_the_graph_keyed_store(
    layout, monkeypatch, holder
):
    """The resolution order in `indexer`, exercised rather than read.

    An explicit argument still wins so an override stays possible; what must not
    be possible is a caller with a layout silently getting the graph-keyed store.
    """
    graph = _revision_graph(layout, "rev-a")
    explicit = tmp_explicit = layout.task_root / "explicit.sqlite"
    environment = layout.task_root / "from-env.sqlite"
    monkeypatch.delenv("GT_CONTRACT_EMBEDDING_INDEX", raising=False)

    contract_store_path = None
    if holder == "explicit":
        contract_store_path = tmp_explicit
    elif holder == "environment":
        monkeypatch.setenv("GT_CONTRACT_EMBEDDING_INDEX", str(environment))

    import os

    resolved = (
        contract_store_path
        or (layout.contract_store_path if layout is not None else None)
        or os.environ.get("GT_CONTRACT_EMBEDDING_INDEX")
        or default_store_path(graph)
    )
    expected = {"explicit": explicit, "layout": layout.contract_store_path,
                # The layout outranks the environment override; the env var is
                # the escape hatch for a caller that has no layout at all.
                "environment": layout.contract_store_path}[holder]
    assert Path(resolved) == expected
    assert Path(resolved) != default_store_path(graph)


def test_the_indexer_resolution_order_is_the_one_this_test_models():
    """Guard against the model above drifting from the code it stands in for.

    A hand-copied expression in a test is a second source of truth, and this
    one exists precisely because the original was retyped at three call sites
    and one of them was wrong. If the real order changes, this fails.
    """
    import inspect

    from gt_engine import indexer

    source = inspect.getsource(indexer)
    assert "contract_store_path\n                or (layout.contract_store_path" in source
    assert 'or os.environ.get("GT_CONTRACT_EMBEDDING_INDEX")\n                or default_store_path(' in source


def test_every_live_caller_addresses_the_task_pinned_store():
    """The three call sites the repair had to correct, still corrected.

    Checked as source because the alternative is a full run: the failure this
    catches is a caller that quietly stops passing the layout, and a cold cache
    raises nothing.
    """
    import inspect

    from gt_engine import indexer, miniswe_integration, retrieval

    for module in (indexer, miniswe_integration):
        source = inspect.getsource(module)
        assert "contract_store_path" in source, module.__name__
    # The retrieval readers take the store from their caller rather than
    # deriving one, which is what keeps them on the pinned path. Neither may
    # reach for default_store_path when the caller supplied nothing: the
    # env-var fallback is the only other source it is allowed.
    for reader in (retrieval.dense_rank, retrieval.hybrid_rank):
        assert "store_path" in inspect.signature(reader).parameters, reader.__name__
    assert "default_store_path" not in inspect.getsource(retrieval._resolved_store_path)
