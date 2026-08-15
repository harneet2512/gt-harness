from __future__ import annotations

from scripts.decision_point_control import _anchors, _commands


def test_commands_extracts_normalized_and_provider_shapes():
    assert _commands({"extra": {"actions": [{"command": "sed app.py"}]}}) == [
        "sed app.py"
    ]
    assert _commands(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"command":"cat app.py"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ) == ["cat app.py"]


def test_payload_anchor_extraction_is_bounded_to_paths_and_symbols():
    anchors = _anchors("src/app.py:12; symbol=handle_request; confidence=0.9")
    assert "src/app.py" in anchors
    assert "handle_request" in anchors
    assert "0.9" not in anchors
