"""Provider-wait scheduler: whole-graph work launches while the host blocks."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from gt_engine.provider_wait import ProviderWaitScheduler


def test_work_launches_at_window_begin_and_drains() -> None:
    scheduler = ProviderWaitScheduler()
    ran: list[str] = []
    scheduler.enqueue("job:a", lambda: ran.append("a") or {"n": 1})
    scheduler.enqueue("job:b", lambda: ran.append("b") or {"n": 2})
    # Pending work does not run before the window opens.
    assert ran == []
    launched = scheduler.begin_window()
    assert sorted(launched) == ["job:a", "job:b"]
    deadline = time.monotonic() + 5
    while scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.01)
    results = scheduler.drain()
    assert len(results) == 2
    assert all(status == "ok" for _, status, _ in results)
    assert sorted(ran) == ["a", "b"]
    scheduler.close()


def test_pending_same_name_is_replaced_not_duplicated() -> None:
    scheduler = ProviderWaitScheduler()
    ran: list[str] = []
    scheduler.enqueue("dense_refresh:r0", lambda: ran.append("r0") or {})
    assert scheduler.enqueue("dense_refresh:r0", lambda: ran.append("r1") or {}) == "replaced"
    scheduler.begin_window()
    deadline = time.monotonic() + 5
    while scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.01)
    scheduler.close()
    assert ran == ["r1"]


def test_newest_revision_drops_stale_pending_refreshes() -> None:
    """Under churn the queue would fill with refreshes whose output is keyed
    to already-superseded graph files -- pure spend ahead of the live job."""
    scheduler = ProviderWaitScheduler()
    scheduler.enqueue("dense_refresh:r1", lambda: {})
    scheduler.enqueue("lsp_salvage:t1", lambda: {})
    dropped = scheduler.drop_pending_family("dense_refresh:", "dense_refresh:r2")
    assert dropped == ["dense_refresh:r1"]
    scheduler.enqueue("dense_refresh:r2", lambda: {})
    assert scheduler.pending_names() == ("dense_refresh:r2", "lsp_salvage:t1")
    scheduler.close()


def test_work_failure_is_data_not_exception() -> None:
    scheduler = ProviderWaitScheduler()

    def boom():
        raise ValueError("onnx missing")

    scheduler.enqueue("dense_refresh:r0", boom)
    scheduler.begin_window()
    deadline = time.monotonic() + 5
    while scheduler.pending_names() and time.monotonic() < deadline:
        time.sleep(0.01)
    results = scheduler.drain()
    scheduler.close()
    assert results and results[0][0] == "dense_refresh:r0"
    assert results[0][1] == "error"
    assert "ValueError" in results[0][2]


def test_close_drops_pending_without_joining() -> None:
    scheduler = ProviderWaitScheduler()
    gate = threading.Event()
    scheduler.enqueue("slow", lambda: gate.wait(2) or {})
    scheduler.begin_window()
    scheduler.enqueue("queued", lambda: {})
    started = time.monotonic()
    scheduler.close(wait=False)
    assert time.monotonic() - started < 1.0
    gate.set()


# ---------------------------------------------------------------------------
# Adapter wiring: the dense refresh is the first whole-graph product.
# ---------------------------------------------------------------------------


def _adapter(tmp_path: Path):
    from gt_engine.miniswe_integration import MiniSweAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    return MiniSweAdapter(
        task_id="t-wait",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=repo,
    )


def test_wait_window_enqueues_dense_refresh_once_per_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter(tmp_path)
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "model"))
    adapter.engine_state.graph_path = str(tmp_path / "graph.db")
    adapter.engine_state.graph_revision = "rev-a"

    calls: list[str] = []
    monkeypatch.setattr(
        adapter,
        "_contract_store_path",
        lambda graph_path: tmp_path / "store.sqlite",
    )

    class FakeStore:
        def __init__(self, path):
            calls.append(str(path))

        def refresh(self, graph_path, *, embed_fn, length_fn=None, **_):
            return {"embedded": 3, "unchanged": 1, "deleted": 0, "documents_after": 4}

        def close(self):
            pass

    import gt_engine.contract_embeddings as ce

    monkeypatch.setattr(ce, "ContractEmbeddingStore", FakeStore)
    monkeypatch.setattr(ce, "onnx_embedder", lambda root: (lambda texts: [[0.0]] * len(texts)))
    monkeypatch.setattr(ce, "onnx_token_lengths", lambda root: (lambda texts: [1] * len(texts)))

    adapter.provider_wait_begin()
    adapter.provider_wait_end()
    deadline = time.monotonic() + 5
    while adapter._dense_warmed_revision != "rev-a" and time.monotonic() < deadline:
        adapter.provider_wait_end()
        time.sleep(0.02)
    assert adapter._dense_warmed_revision == "rev-a"
    # A second window over the same revision enqueues nothing.
    adapter.provider_wait_begin()
    assert adapter._wait_scheduler.pending_names() == ()
    adapter._wait_scheduler.close()


def test_wait_window_refresh_drain_emits_dense_index_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed refresh is real readiness evidence even though no dense
    query ran: the receipt must record it, measured -- quick_check over a
    real store file plus the refresh's own documents_after -- not assumed
    and not missing (smoke-20 katex/testem read dense_index_receipt_missing)."""
    import json
    import sqlite3

    adapter = _adapter(tmp_path)
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "model"))
    adapter.engine_state.graph_path = str(tmp_path / "graph.db")
    adapter.engine_state.graph_revision = "rev-c"

    store_path = tmp_path / "store.sqlite"
    sqlite3.connect(store_path).close()  # a real, quick_check-ok file
    monkeypatch.setattr(
        adapter, "_contract_store_path", lambda graph_path: store_path
    )

    class FakeStore:
        def __init__(self, path):
            pass

        def refresh(self, graph_path, *, embed_fn, length_fn=None, **_):
            return {
                "embedded": 3, "unchanged": 1, "deleted": 0,
                "documents_after": 4, "index_sha256": "ab" * 32,
                "source_revision": "src-rev-c",
            }

        def close(self):
            pass

    import gt_engine.contract_embeddings as ce

    monkeypatch.setattr(ce, "ContractEmbeddingStore", FakeStore)
    monkeypatch.setattr(
        ce, "onnx_embedder", lambda root: (lambda texts: [[0.0]] * len(texts))
    )
    monkeypatch.setattr(
        ce, "onnx_token_lengths", lambda root: (lambda texts: [1] * len(texts))
    )

    adapter.provider_wait_begin()
    adapter.provider_wait_end()
    deadline = time.monotonic() + 5
    while adapter._dense_warmed_revision != "rev-c" and time.monotonic() < deadline:
        adapter.provider_wait_end()
        time.sleep(0.02)
    assert adapter._dense_warmed_revision == "rev-c"

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text().splitlines()
    ]
    ready = [row for row in rows if row["event"] == "dense_index_ready"]
    assert len(ready) == 1
    assert ready[0]["query_ready"] is True
    assert ready[0]["reason"] is None
    assert ready[0]["vector_source"] == "dense_wait_refresh"
    assert ready[0]["graph_revision"] == "rev-c"
    assert ready[0]["source_revision"] == "src-rev-c"
    assert ready[0]["document_count"] == 4
    assert ready[0]["index_sha256"] == "ab" * 32
    assert ready[0]["sqlite_quick_check"] == "ok"
    adapter._wait_scheduler.close()


def test_wait_window_survives_refresh_failure_and_bounds_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter(tmp_path)
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "model"))
    adapter.engine_state.graph_path = str(tmp_path / "graph.db")
    adapter.engine_state.graph_revision = "rev-b"
    monkeypatch.setattr(adapter, "_contract_store_path", lambda gp: tmp_path / "s.sqlite")

    class BadStore:
        def __init__(self, path):
            pass

        def refresh(self, *a, **k):
            raise OSError("store locked")

        def close(self):
            pass

    import gt_engine.contract_embeddings as ce

    monkeypatch.setattr(ce, "ContractEmbeddingStore", BadStore)
    monkeypatch.setattr(ce, "onnx_embedder", lambda root: (lambda texts: []))
    monkeypatch.setattr(ce, "onnx_token_lengths", lambda root: (lambda texts: []))

    for _ in range(adapter.DENSE_WAIT_MAX_FAILURES + 1):
        adapter.provider_wait_begin()
        deadline = time.monotonic() + 5
        while adapter._wait_scheduler.pending_names() and time.monotonic() < deadline:
            time.sleep(0.02)
        adapter.provider_wait_end()
    assert adapter._dense_wait_failures["rev-b"] >= adapter.DENSE_WAIT_MAX_FAILURES
    adapter.provider_wait_begin()
    assert adapter._wait_scheduler.pending_names() == ()
    adapter._wait_scheduler.close()


def test_epoch_map_records_transaction_paths(tmp_path: Path) -> None:
    from types import SimpleNamespace

    adapter = _adapter(tmp_path)

    def txn(post: str, paths: tuple[str, ...], complete: bool = True):
        return SimpleNamespace(
            post_revision=post, pre_revision="r0",
            changed_paths=tuple(paths), complete=complete,
            omissions=() if complete else ("unenumerated",),
            transaction_sha256=f"txn-{post}", action_id=1,
            canonical_bytes=lambda: b"txn",
            changes=[
                SimpleNamespace(path=p, operation="modify", before_sha256="",
                                after_sha256="", after=b"x")
                for p in paths
            ],
        )

    adapter.record_edit_transaction(txn("r1", ("src/a.py", "src/b.py")))
    assert adapter._path_edit_epochs["src/a.py"] == 1
    assert adapter._path_edit_epochs["src/b.py"] == 1
    adapter.record_edit_transaction(txn("r2", ("src/c.py",), complete=False))
    assert adapter._incomplete_edit_epoch == 2
