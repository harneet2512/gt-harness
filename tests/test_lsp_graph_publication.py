"""LSP promotion lifecycle on the post-coordinator path.

The coordinator is deleted; the disposition chain it enforced now lives in
``_lsp_terminal_disposition`` and the schedule/poll triggers in
``_maybe_schedule_lsp_promotion``/``_poll_lsp_promotions``. These tests
exercise the adapter directly: what the coordinator's poll proved, the
boundary drain now proves.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from gt_engine.graph_coordinator import FrozenBuildInput, GraphBuildArtifact
from tests.test_scoped_merge import _adapter_with_live, _journal_events


def _request(revision: str, content: bytes = b"x = 1\n") -> FrozenBuildInput:
    return FrozenBuildInput(revision, ("x.py",), (("x.py", content),))


class _Handle:
    def __init__(self, task_id: str, receipt: dict, *, done: bool = True) -> None:
        self.task_id = task_id
        self.receipt = receipt
        self.done = done

    def terminal_receipt(self, *, timeout=None) -> dict:
        assert self.done and timeout == 0
        return dict(self.receipt)


def _scheduler(*handles: _Handle):
    return SimpleNamespace(
        _handles=list(handles), close=lambda wait=False: None
    )


def _receipt(task_id: str, source: str, graph: str, candidate: Path,
             **overrides) -> dict:
    terminal = {
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "task_id": task_id,
        "source_revision": source,
        "input_graph_revision": graph,
        "candidate_path": str(candidate),
        # A promotion that succeeded produced edges. Publication is gated on
        # verified+corrected+selected+deleted > 0, because run 34077224456
        # published four 912MB candidates carrying zero new edges.
        "verified": 12,
        "corrected": 3,
        "selected": 0,
        "deleted": 0,
    }
    terminal.update(overrides)
    return terminal


def _schedule_stub(adapter, monkeypatch, handles):
    """Bypass the freeze + producer seams: return canned handles."""
    requests: list[FrozenBuildInput] = []
    monkeypatch.setattr(
        adapter, "_frozen_graph_input",
        lambda snapshot: _request(adapter.engine_state.source_revision),
    )
    monkeypatch.setattr(
        adapter, "_schedule_lsp_candidate",
        lambda request, base: requests.append(request) or handles.pop(0),
    )
    return requests


def test_promotion_publishes_when_the_base_is_still_adopted(
    tmp_path, monkeypatch
) -> None:
    adapter, live = _adapter_with_live(tmp_path)
    candidate = tmp_path / "candidate.db"
    candidate.write_bytes(b"candidate")
    handle = _Handle(
        "task-1",
        _receipt("task-1", "source-r2", adapter.engine_state.graph_revision,
                 candidate),
    )
    _schedule_stub(adapter, monkeypatch, [handle])
    monkeypatch.setattr(
        adapter, "_certify_lsp_candidate",
        lambda request, base, terminal: GraphBuildArtifact(
            True, str(candidate), "g1+lsp"
        ),
    )
    adapter._lsp_scheduler = _scheduler(handle)
    adapter._latest_workspace_snapshot = SimpleNamespace()
    try:
        adapter._maybe_schedule_lsp_promotion()
        assert adapter._lsp_active == "task-1"
        adapter._poll_lsp_promotions()
        assert adapter.engine_state.graph_path == str(candidate)
        assert adapter.engine_state.graph_revision == "g1+lsp"
        assert adapter._lsp_active is None
        terminal = [
            row for row in _journal_events(adapter)
            if row["event"] == "lsp_promotion_terminal"
        ]
        assert terminal[-1]["disposition"] == "published"
    finally:
        adapter.close_graph_lifecycle()


def test_superseded_promotion_observes_obsolete_and_never_publishes(
    tmp_path, monkeypatch
) -> None:
    adapter, live = _adapter_with_live(tmp_path)
    candidate = tmp_path / "candidate.db"
    candidate.write_bytes(b"candidate")
    base_revision = adapter.engine_state.graph_revision
    handle = _Handle(
        "task-2",
        _receipt("task-2", "source-r2", base_revision, candidate),
    )
    _schedule_stub(adapter, monkeypatch, [handle])
    adapter._lsp_scheduler = _scheduler(handle)
    adapter._latest_workspace_snapshot = SimpleNamespace()
    try:
        adapter._maybe_schedule_lsp_promotion()
        # The authority moves before the promotion drains.
        adapter.engine_state.source_revision = "source-r3"
        adapter._poll_lsp_promotions()
        assert adapter.engine_state.graph_path == str(live)
        terminal = [
            row for row in _journal_events(adapter)
            if row["event"] == "lsp_promotion_terminal"
        ]
        assert terminal[-1]["disposition"] == "obsolete"
        # Refusal deletes the candidate unless salvage claimed it first;
        # either way it is not adopted.
        assert adapter.engine_state.graph_revision == base_revision
    finally:
        adapter.close_graph_lifecycle()


def test_one_active_promotion_and_considered_identities_dedupe(
    tmp_path, monkeypatch
) -> None:
    adapter, live = _adapter_with_live(tmp_path)
    handle = _Handle(
        "task-3",
        _receipt("task-3", "source-r2",
                 adapter.engine_state.graph_revision, tmp_path / "c.db"),
        done=False,
    )
    requests = _schedule_stub(adapter, monkeypatch, [handle])
    adapter._lsp_scheduler = _scheduler(handle)
    adapter._latest_workspace_snapshot = SimpleNamespace()
    try:
        adapter._maybe_schedule_lsp_promotion()
        adapter._maybe_schedule_lsp_promotion()
        assert len(requests) == 1, "one active promotion, never two"
        adapter._lsp_active = None
        adapter._maybe_schedule_lsp_promotion()
        assert len(requests) == 1, "same input identity is already considered"
    finally:
        adapter.close_graph_lifecycle()


def test_failed_and_mismatched_receipts_never_publish(
    tmp_path, monkeypatch
) -> None:
    for overrides, expected in (
        ({"status": "failed", "publishable": False}, "not_publishable"),
        ({"verified": 0, "corrected": 0, "selected": 0, "deleted": 0},
         "no_edge_mutations"),
        ({"input_graph_revision": "other"}, "identity_mismatch"),
    ):
        subdir = tmp_path / expected
        subdir.mkdir()
        adapter, live = _adapter_with_live(subdir)
        candidate = tmp_path / f"{expected}.db"
        candidate.write_bytes(b"candidate")
        terminal = _receipt(
            "task-x", "source-r2", adapter.engine_state.graph_revision,
            candidate, **overrides,
        )
        base = GraphBuildArtifact(
            True, str(live), adapter.engine_state.graph_revision
        )
        request = _request("source-r2")
        try:
            disposition = adapter._lsp_terminal_disposition(
                request, base, terminal
            )
            assert disposition == expected
            assert adapter.engine_state.graph_path == str(live)
        finally:
            adapter.close_graph_lifecycle()


def test_schedule_exception_is_terminal_journaled_data(
    tmp_path, monkeypatch
) -> None:
    adapter, live = _adapter_with_live(tmp_path)
    monkeypatch.setattr(
        adapter, "_frozen_graph_input",
        lambda snapshot: _request(adapter.engine_state.source_revision),
    )

    def unavailable(request, base):
        raise ImportError("installed scheduler unavailable")

    monkeypatch.setattr(adapter, "_schedule_lsp_candidate", unavailable)
    adapter._latest_workspace_snapshot = SimpleNamespace()
    try:
        adapter._maybe_schedule_lsp_promotion()
        assert adapter._lsp_active is None
        terminal = [
            row for row in _journal_events(adapter)
            if row["event"] == "lsp_promotion_terminal"
        ]
        assert terminal[-1]["disposition"] == "schedule_exception"
        assert terminal[-1]["status"] == "failed"
        assert adapter.engine_state.graph_current
    finally:
        adapter.close_graph_lifecycle()


def test_schedule_exception_terminal_preserves_the_reason(
    tmp_path, monkeypatch
) -> None:
    """The receipt must carry the exception's message, not only its type.

    Run 34760986248 journaled 23 rows of ``schedule_exception:ValueError``
    with the message discarded -- an undiagnosable paid failure.
    """
    import json

    adapter, live = _adapter_with_live(tmp_path)
    monkeypatch.setattr(
        adapter, "_frozen_graph_input",
        lambda snapshot: _request(adapter.engine_state.source_revision),
    )

    def mismatch(request, base):
        raise ValueError(
            "lsp_source_input_mismatch:"
            "expected_only=['.pytest_cache/README.md']:materialized_only=[]"
        )

    monkeypatch.setattr(adapter, "_schedule_lsp_candidate", mismatch)
    adapter._latest_workspace_snapshot = SimpleNamespace()
    try:
        adapter._maybe_schedule_lsp_promotion()
        terminal = [
            row for row in _journal_events(adapter)
            if row["event"] == "lsp_promotion_terminal"
        ]
        receipt = json.loads(
            (adapter.store.root / "lsp_receipts"
             / f"{terminal[-1]['artifact_sha256']}.json").read_text()
        )
        assert receipt["reason"].startswith("schedule_exception:ValueError:")
        assert "lsp_source_input_mismatch" in receipt["reason"]
        assert ".pytest_cache/README.md" in receipt["reason"]
    finally:
        adapter.close_graph_lifecycle()
