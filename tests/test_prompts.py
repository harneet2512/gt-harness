
from nano.prompts import GT_PROMPT_SUFFIX, SYSTEM_PROMPT, count_tokens_approx


def test_system_prompt_under_1000_tokens():
    assert count_tokens_approx(SYSTEM_PROMPT) < 1000, (
        f"system prompt = ~{count_tokens_approx(SYSTEM_PROMPT)} tokens, "
        f"hard cap is 1000 (spec §3.5)"
    )


def test_system_prompt_mentions_three_tools():
    s = SYSTEM_PROMPT.lower()
    assert "bash" in s
    assert "read_file" in s
    assert "edit_file" in s


def test_system_prompt_no_filler_phrases():
    forbidden = ["you are an expert", "i'd be happy", "as an ai"]
    s = SYSTEM_PROMPT.lower()
    for phrase in forbidden:
        assert phrase not in s, f"filler phrase present: {phrase!r}"


def test_gt_prompt_forbids_harness_reverse_engineering():
    prompt = GT_PROMPT_SUFFIX.lower()
    assert "/installed-agent" in prompt
    assert ".gt" in prompt
    assert "never inspect" in prompt
    assert "work only on the user's task" in prompt
