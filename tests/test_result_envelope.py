import pytest

from gt_engine.result_envelope import HonestyEnvelope, envelope_for_result, envelope_from_mapping


def test_complete_requires_known_true_total():
    row = envelope_for_result(
        source_revision="src", workspace_revision="ws", payload=[1, 2],
        returned_count=2, true_total=2,
    )
    assert row["schema"] == "gt.honesty_envelope.v1"
    assert row["completeness"] == "complete"


def test_truncated_requires_returned_below_true_total():
    row = envelope_for_result(
        source_revision="src", workspace_revision="ws", payload=[1],
        returned_count=1, true_total=2,
    )
    assert row["completeness"] == "truncated"


def test_legacy_mapping_is_conservative():
    row = envelope_from_mapping({"source_revision": "old", "payload": []})
    assert row.completeness == "legacy_unknown"


def test_truncated_equal_total_is_rejected():
    with pytest.raises(ValueError):
        HonestyEnvelope(
            source_revision="src", workspace_revision="ws", payload=[1],
            completeness="truncated", returned_count=1, true_total=1,
        )
