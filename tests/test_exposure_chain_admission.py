import pytest

from gt_engine.miniswe_integration import MiniSweAdapter


def stage(adapter, name, previous, following):
    rendered = f"[GT_EVIDENCE:caller_contract] exact proof {name}"
    adapter.stage_exposure(rendered=rendered, dedup_key=name,
                           previous_chain_head=previous, next_chain_head=following)
    assert adapter.admit_model_visible_delivery(
        lane="sealed", kind="caller_contract", rendered=rendered,
        action_index=1, iteration=adapter.iteration, dedup_key=name,
    )
    return rendered


def test_missing_predecessor_refuses_entire_request_before_any_commit(tmp_path):
    adapter = MiniSweAdapter(task_id="chain", state_dir=tmp_path, predicates=[])
    initial = adapter._chain_head
    first = stage(adapter, "A", initial, "head-a")
    second = stage(adapter, "B", "head-a", "head-b")
    with pytest.raises(ValueError, match="exposure chain"):
        adapter.bind_provider_payload({"messages": [{"role": "user", "content": second}]})
    assert adapter.iteration == 0
    assert not adapter.deliveries
    assert adapter._model_visible_delivery_count == 0
    assert adapter._chain_head == initial
    assert not adapter._dedup_chain
    delivery = adapter.bind_provider_payload({"messages": [{"role": "user", "content": first + second}]})
    assert len(delivery.delivery_ids) == 2
    assert adapter._chain_head == "head-b"
    assert adapter._dedup_chain == {"A", "B"}


def test_late_conflict_does_not_partially_commit_valid_prefix(tmp_path):
    adapter = MiniSweAdapter(task_id="atomic", state_dir=tmp_path, predicates=[])
    initial = adapter._chain_head
    first = stage(adapter, "A", initial, "head-a")
    second = stage(adapter, "B", "unrelated-head", "head-b")
    with pytest.raises(ValueError, match="exposure chain"):
        adapter.bind_provider_payload({"messages": [{"role": "user", "content": first + second}]})
    assert adapter._chain_head == initial
    assert adapter._model_visible_delivery_count == 0
    assert not adapter._dedup_chain


def test_omitted_successor_does_not_block_valid_prefix(tmp_path):
    adapter = MiniSweAdapter(task_id="prefix", state_dir=tmp_path, predicates=[])
    first = stage(adapter, "A", adapter._chain_head, "head-a")
    stage(adapter, "B", "head-a", "head-b")
    delivery = adapter.bind_provider_payload({"messages": [{"role": "user", "content": first}]})
    assert len(delivery.delivery_ids) == 1
    assert adapter._chain_head == "head-a"
    assert adapter._dedup_chain == {"A"}


def test_conflict_preserves_pending_verification_candidate(tmp_path):
    adapter = MiniSweAdapter(task_id="verification", state_dir=tmp_path, predicates=[])
    rendered = "verification proposal"
    adapter._pending_verification_candidate = rendered
    adapter.stage_exposure(rendered=rendered, dedup_key="verify",
                           previous_chain_head=adapter._chain_head,
                           verification_candidate=rendered)
    assert adapter.admit_model_visible_delivery(
        lane="sealed", kind="verification_plan", rendered=rendered,
        action_index=1, iteration=0, dedup_key="verify")
    conflicting = stage(adapter, "B", "absent-predecessor", "head-b")
    with pytest.raises(ValueError, match="exposure chain"):
        adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered + conflicting}]})
    assert adapter.verification_candidate()[0] == rendered
    assert adapter._model_visible_delivery_count == 0
