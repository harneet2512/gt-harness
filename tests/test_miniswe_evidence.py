from __future__ import annotations

from groundtruth.runtime.evidence_envelope import EvidenceEnvelope

import gt_engine.miniswe_evidence as me


def test_is_submit_command_detects_real_magic_string():
    assert me.is_submit_command("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
    assert me.is_submit_command(
        "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'"
    )
    assert not me.is_submit_command("echo submit")
    assert not me.is_submit_command("cat submit.txt")
    assert not me.is_submit_command("")


def test_is_submit_command_catches_adjacent_literal_split():
    # The shell joins adjacent string literals, so the contiguous marker can be
    # hidden from a raw substring check: "COMPLETE_TASK_AND_SUBMIT_FINAL_""OUTPUT"
    # concatenates to COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT at the shell level.
    assert me.is_submit_command(
        'echo "COMPLETE_TASK_AND_SUBMIT_FINAL_""OUTPUT"'
    )
    assert me.is_submit_command(
        "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_''OUTPUT'"
    )


def test_classify_event_derives_search_boundary():
    ev = me.classify_event(
        "grep -rn compute src/", "src/mod.py:1:def compute", 0, action_index=1
    )
    assert ev.semantic_events == ("search_result",)
    assert ev.primary_boundary == "search_result"


def test_classify_event_derives_search_boundary_for_broader_heads():
    for cmd in ("git grep -n compute src/", "findstr /s /n compute src",
                "find src -name '*.py' -exec grep -l compute {} \\;"):
        ev = me.classify_event(cmd, "src/mod.py:1:def compute", 0, action_index=1)
        assert ev.primary_boundary == "search_result", cmd
    ev = me.classify_event(
        "rg -n nomatch src/", "", 1, action_index=1
    )
    assert ev.primary_boundary == "failed_search"


def test_classify_event_derives_failed_search():
    ev = me.classify_event(
        "grep -rn nomatch src/", "", 1, action_index=1
    )
    assert ev.primary_boundary == "failed_search"


def test_classify_event_derives_test_boundary():
    ev = me.classify_event(
        "python -m pytest tests/", "1 failed", 1,
        action_index=1, test_outcome="fail",
    )
    assert ev.primary_boundary == "test_result"
    assert ev.test_outcome == "fail"


def test_classify_event_derives_view_boundary():
    ev = me.classify_event(
        "cat src/mod.py", "def compute", 0,
        action_index=1, viewed_files=("src/mod.py",),
    )
    assert ev.primary_boundary == "file_view"


def test_run_evidence_pipeline_one_dose_seals_and_updates_chain():
    env = EvidenceEnvelope(
        producer="test",
        fact_id="compute",
        target="src/mod.py",
        evidence_type="localization",
        payload=("src/mod.py:1:compute",),
        confidence=0.5,
        tier="INFO",
        dedup_key="dedup-1",
    )
    monkeypatched = [env]

    original = me.augment

    def fake_augment(_event, _state):
        return list(monkeypatched)

    me.augment = fake_augment
    try:
        chain: set[str] = set()
        r1 = me.run_evidence_pipeline(
            None,
            me.classify_event("grep -rn compute src/", "hit", 0, action_index=1),
            dedup_chain=chain, chain_head="", episode_id="e", event_id="e:1",
        )
        assert r1.rendered
        assert r1.sealed
        assert r1.chain_head
        assert "dedup-1" in chain
    finally:
        me.augment = original


def test_run_evidence_pipeline_correct_quiet_when_no_envelopes():
    original = me.augment

    def fake_augment(_event, _state):
        return []

    me.augment = fake_augment
    try:
        result = me.run_evidence_pipeline(
            None,
            me.classify_event("grep -rn compute src/", "hit", 0, action_index=1),
            dedup_chain=set(), chain_head="", episode_id="e", event_id="e:1",
        )
        assert result.rendered == ""
        assert result.sealed is False
    finally:
        me.augment = original


def test_run_evidence_pipeline_drops_over_budget_delta():
    env = EvidenceEnvelope(
        producer="test",
        fact_id="big",
        target="src/mod.py",
        evidence_type="localization",
        payload=("x" * 9000,),
        confidence=0.5,
        tier="INFO",
        dedup_key="dedup-big",
    )
    original = me.augment

    def fake_augment(_event, _state):
        return [env]

    me.augment = fake_augment
    try:
        result = me.run_evidence_pipeline(
            None,
            me.classify_event("grep -rn compute src/", "hit", 0, action_index=1),
            dedup_chain=set(), chain_head="", episode_id="e", event_id="e:1",
        )
        assert result.rendered == ""
        assert result.sealed is False
    finally:
        me.augment = original
