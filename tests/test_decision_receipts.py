import pytest

from gt_harness.runtime_receipts import _dense_execution_receipts, _validate_delivery_boundaries


def test_current_fact_can_recur_beyond_legacy_task_limit():
    rows = [{"observed_iteration": iteration, "delivery_identity": "a" * 64,
             "context_byte_count": 1000} for iteration in range(30)]
    _validate_delivery_boundaries(rows)
    # The same fact twice in one decision remains a duplicate.
    rows.append(dict(rows[-1]))
    with pytest.raises(ValueError, match="duplicate_delivery_identity"):
        _validate_delivery_boundaries(rows)


def test_boundary_claim_and_byte_limits_remain_enforced():
    rows = [{"observed_iteration": 1, "delivery_identity": str(index),
             "context_byte_count": 1} for index in range(5)]
    with pytest.raises(ValueError, match="boundary_claim_limit"):
        _validate_delivery_boundaries(rows)
    rows = [{"observed_iteration": 1, "delivery_identity": str(index),
             "context_byte_count": 3000} for index in range(4)]
    with pytest.raises(ValueError, match="request_budget"):
        _validate_delivery_boundaries(rows)


def test_repeated_dense_execution_retains_each_recipe_and_source_identity():
    events = [{"event": "dense_index_ready", "query_ready": ready,
               "recipe_id": "cls-query-prefix-512-l2.v1", "source_revision": revision,
               "environment_sha256": "a" * 64, "query_sha256": revision}
              for ready, revision in ((True, "1" * 64), (False, "2" * 64))]
    receipts = _dense_execution_receipts(events)
    assert len(receipts) == 2
    assert receipts[0]["query_ready"] is True
    assert receipts[-1]["query_ready"] is False
    assert receipts[0]["source_revision"] != receipts[-1]["source_revision"]
    assert all(row["environment_sha256"] == "a" * 64 for row in receipts)
    assert all(row["recipe_id"] == "cls-query-prefix-512-l2.v1" for row in receipts)
