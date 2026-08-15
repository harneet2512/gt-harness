from gt_engine.replay import build_iteration_replay


def _row(sequence, event_type, action_index, payload):
    return {
        "sequence": sequence,
        "event_type": event_type,
        "action_index": action_index,
        "boundary": "test",
        "payload": payload,
    }


def test_replay_joins_request_response_and_following_tool_outcomes():
    rows = [
        _row(1, "provider.request", 0, {
            "iteration": 1,
            "payload_chars": 1200,
            "active_message_chars": 900,
            "raw_message_chars": 2500,
            "delivery_ids": ["d1"],
            "checkpoint_sha256": "cp1",
            "compacted": True,
            "omitted_message_count": 4,
        }),
        _row(2, "model.response", 0, {
            "iteration": 1,
            "delivery_ids": ["d1"],
            "input_tokens": 300,
            "output_tokens": 20,
            "cache_read_tokens": 100,
            "tool_calls": [{"id": "t1", "name": "bash"}],
        }),
        _row(3, "tool.outcome_classified", 1, {
            "tool_call_id": "t1",
            "classification": "useful_red",
            "information_gain": True,
            "active_delivery_ids": ["d1"],
        }),
        _row(4, "provider.request", 1, {
            "iteration": 2,
            "payload_chars": 1400,
            "active_message_chars": 1000,
            "raw_message_chars": 4000,
            "delivery_ids": [],
            "checkpoint_sha256": "cp2",
            "compacted": True,
            "omitted_message_count": 6,
        }),
        _row(5, "model.response", 1, {
            "iteration": 2,
            "delivery_ids": [],
            "input_tokens": 350,
            "output_tokens": 10,
            "cache_read_tokens": 120,
            "tool_calls": [],
        }),
    ]

    replay = build_iteration_replay(rows)

    assert replay["accounted_input_tokens"] == 650
    assert replay["iteration_count"] == 2
    first = replay["iterations"][0]
    assert first["iteration"] == 1
    assert first["request_payload_chars"] == 1200
    assert first["raw_message_chars"] == 2500
    assert first["delivery_ids"] == ["d1"]
    assert first["tool_outcomes"] == ["useful_red"]
    assert first["useful_observation_count"] == 1
    assert first["compacted"] is True
    assert first["omitted_message_count"] == 4


def test_replay_reports_missing_or_duplicate_iteration_receipts():
    rows = [
        _row(1, "provider.request", 0, {
            "iteration": 1, "payload_chars": 10,
        }),
        _row(2, "provider.request", 0, {
            "iteration": 1, "payload_chars": 11,
        }),
        _row(3, "model.response", 0, {
            "iteration": 2, "input_tokens": 4,
        }),
    ]

    replay = build_iteration_replay(rows)

    assert "iteration 1: duplicate provider.request receipts" in replay["issues"]
    assert "iteration 1: missing model.response receipt" in replay["issues"]
    assert "iteration 2: missing provider.request receipt" in replay["issues"]

