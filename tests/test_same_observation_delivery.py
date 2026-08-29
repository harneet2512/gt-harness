from gt_engine.provider_view import inject_same_observation


def test_post_action_evidence_never_falls_back_to_synthetic_user_message():
    source = [{"role": "assistant", "content": "reasoning"}]

    injection = inject_same_observation(
        source, "certified fact", allow_initial_context=False
    )

    assert list(injection.messages) == source
    assert injection.message_index is None
    assert injection.disposition == "no_matching_tool_observation"


def test_post_action_evidence_is_attached_to_exact_latest_tool_observation():
    source = [
        {"role": "tool", "content": "old"},
        {"role": "assistant", "content": "next"},
        {"role": "tool", "content": "current"},
    ]

    injection = inject_same_observation(
        source, "certified fact", allow_initial_context=False
    )

    assert injection.message_index == 2
    assert injection.messages[2]["content"] == "current\n\ncertified fact"
    assert source[2]["content"] == "current"
