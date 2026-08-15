from gt_engine.preflight import adapt_proposed_action
from gt_engine.trajectory_utilization import SemanticUse, SemanticUtilizationTracker


def _action(command: str, call: int, index: int = 0, batch_size: int = 1):
    return adapt_proposed_action(
        {"command": command, "tool_call_id": f"a-{call}-{index}"},
        source_revision="rev-1",
        workspace_revision="rev-1",
        model_call=call,
        batch_index=index,
        batch_size=batch_size,
    )


def test_semantic_tracker_counts_later_typed_target_use():
    delivery = {
        "delivery_id": "d1",
        "claim_anchors": ["src/models.py:12:Model"],
        "revision": "rev-1",
    }
    tracker = SemanticUtilizationTracker(max_calls=3, max_actions=6)
    tracker.register(delivery, call=1, source_revision="rev-1")
    tracker.observe(call=1, actions=(_action("ls -la", 1),), source_revision="rev-1")
    tracker.observe(
        call=2,
        actions=(_action("sed -n '1,80p' src/models.py", 2),),
        source_revision="rev-1",
    )
    assert delivery["semantic_utilization"] == SemanticUse.DEFERRED.value
    assert delivery["semantic_use_reason_codes"] == ["typed_target_path", "read"]


def test_semantic_tracker_sees_later_action_in_same_batch():
    delivery = {
        "delivery_id": "d2",
        "claim_anchors": ["src/models.py:12:Model"],
        "revision": "rev-1",
    }
    tracker = SemanticUtilizationTracker()
    tracker.register(delivery, call=1, source_revision="rev-1")
    tracker.observe(
        call=1,
        actions=(
            _action("pwd", 1, 0, 2),
            _action("cat src/models.py", 1, 1, 2),
        ),
        source_revision="rev-1",
    )
    assert delivery["semantic_utilization"] == SemanticUse.SAME_RESPONSE.value
    assert delivery["semantic_use_action_index"] == 1


def test_semantic_tracker_does_not_claim_use_after_source_revision_changes():
    delivery = {
        "delivery_id": "d3",
        "claim_anchors": ["src/models.py:12:Model"],
        "revision": "rev-1",
    }
    tracker = SemanticUtilizationTracker(max_calls=3)
    tracker.register(delivery, call=1, source_revision="rev-1")
    tracker.observe(
        call=2,
        actions=(_action("cat src/models.py", 2),),
        source_revision="rev-2",
    )
    assert delivery["semantic_utilization"] == SemanticUse.STALE_SOURCE.value


def test_semantic_tracker_uses_source_revision_not_workspace_revision():
    # Guidance receipts also carry a workspace ``revision``.  A cache/pyc or
    # other workspace-only update must not invalidate a source-bound fact.
    delivery = {
        "delivery_id": "d-source",
        "claim_anchors": ["src/models.py:12:Model"],
        "revision": "workspace-after-edit",
        "source_revision": "source-rev-1",
    }
    tracker = SemanticUtilizationTracker(max_calls=3)
    tracker.register(delivery, call=1, source_revision="source-rev-1")
    tracker.observe(
        call=2,
        actions=(_action("cat src/models.py", 2),),
        source_revision="source-rev-1",
    )
    assert delivery["semantic_utilization"] == SemanticUse.DEFERRED.value


def test_semantic_tracker_marks_unmatched_without_calling_it_ignored():
    delivery = {
        "delivery_id": "d4",
        "claim_anchors": ["src/models.py:12:Model"],
        "revision": "rev-1",
    }
    tracker = SemanticUtilizationTracker(max_calls=2, max_actions=3)
    tracker.register(delivery, call=1, source_revision="rev-1")
    tracker.observe(call=1, actions=(_action("pwd", 1),), source_revision="rev-1")
    tracker.observe(call=2, actions=(_action("echo done", 2),), source_revision="rev-1")
    assert delivery["semantic_utilization"] == SemanticUse.NO_MATCH.value
    assert "bounded_window_expired" in delivery["semantic_use_reason_codes"]
