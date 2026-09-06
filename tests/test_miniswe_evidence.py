from __future__ import annotations

from groundtruth.runtime.evidence_envelope import EvidenceEnvelope

import gt_engine.miniswe_evidence as me


def test_compound_edit_and_test_preserves_covering_failure_at_delivery(tmp_path, monkeypatch):
    import subprocess
    import sys

    from groundtruth.runtime.gateway import CoveringResult, GatewayState

    monkeypatch.setenv("GT_GATEWAY", "1")
    (tmp_path / "app.py").write_text("value = 2\n")
    execution = subprocess.run(
        [sys.executable, "-c", "assert 2 == 1, 'expected 1 but got 2'"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert execution.returncode != 0
    event = me.classify_event(
        "python -m pytest", execution.stderr, execution.returncode,
        action_index=1, cwd=str(tmp_path), changed_files=("app.py",),
        test_outcome="fail",
        covering=CoveringResult(
            target="app.py", verdict="fail", body_lines=execution.stderr.splitlines(),
            evidence=[("app.py", 1)],
        ),
    )
    assert set(event.semantic_events) >= {"edit_result", "test_result"}
    for native in (False, True):
        state = GatewayState(repo_root=str(tmp_path))
        dedup = set()
        result = me.run_evidence_pipeline(
            state, event, dedup_chain=dedup, chain_head="0" * 64,
            episode_id="compound-fixture", event_id="compound-fixture:1",
            native=native, model_prefix=True, commit=False,
        )
        assert result.sealed
        assert result.envelope.evidence_type == "covering_verdict"
        assert "AssertionError: expected 1 but got 2" in result.rendered
        assert dedup == set(), "candidate rendering must not commit provider exposure"


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


def test_artifact_backed_event_routes_complete_output_into_canonical_gateway(
    tmp_path, monkeypatch
):
    from groundtruth.runtime.adapters.miniswe import StoredToolEvent
    from groundtruth.runtime.gateway import (
        GatewayState,
        _grep_hit_paths_event,
        classify_outcome,
    )

    from gt_engine.output_evidence import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    source.write_bytes(b" " * 20_000 + b"\nsrc/beyond_preview.py:731:def target\n")
    reference = store.publish(source)
    preview = store.preview(reference)
    assert "src/beyond_preview.py" not in preview
    monkeypatch.setattr(
        EvidenceStore,
        "bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("gateway must consume the bounded artifact source")
        ),
    )

    event = me.classify_event(
        "rg -n target .",
        preview,
        0,
        action_index=1,
        output_artifact=reference,
    )
    state = GatewayState(repo_root=str(tmp_path))
    classify_outcome(event, state)

    assert isinstance(event, StoredToolEvent)
    assert event.primary_boundary == "search_result"
    assert state.ledger["target"]["outcomes"] == ["hit"]
    assert _grep_hit_paths_event(event, str(tmp_path)) == {"src/beyond_preview.py"}


def test_artifact_backed_empty_search_ignores_transport_reference_text(tmp_path):
    from groundtruth.runtime.gateway import GatewayState, classify_outcome

    from gt_engine.output_evidence import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    source.write_bytes(b" " * 20_000)
    reference = store.publish(source)
    preview = store.preview(reference)
    assert "GT_OUTPUT_ARTIFACT" in preview

    event = me.classify_event(
        "rg -c missing .",
        preview,
        1,
        action_index=2,
        output_artifact=reference,
    )
    state = GatewayState(repo_root=str(tmp_path))
    classify_outcome(event, state)

    assert event.primary_boundary == "failed_search"
    assert state.ledger["missing"]["outcomes"] == ["zero"]


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


def test_workspace_delta_is_always_an_edit_boundary():
    ev = me.classify_event(
        "python scripts/rewrite.py", "rewritten", 0,
        action_index=1,
        changed_files=("src/mod.py",),
        viewed_files=("scripts/rewrite.py",),
    )
    assert ev.primary_boundary == "edit_result"
    assert ev.semantic_events == ("edit_result", "file_view")


def test_test_and_edit_boundaries_are_both_preserved():
    ev = me.classify_event(
        "npm run build", "built generated source", 0,
        action_index=1,
        changed_files=("src/generated.ts",),
        test_outcome="pass",
    )
    assert ev.primary_boundary == "edit_result"
    assert ev.semantic_events == ("edit_result", "test_result")


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
            commit=True,
        )
        assert r1.rendered
        assert r1.sealed
        assert r1.chain_head
        assert "dedup-1" in chain
    finally:
        me.augment = original


def test_run_evidence_pipeline_preserves_ranked_multidose_and_per_dose_chain():
    envelopes = [
        EvidenceEnvelope(
            producer="test", fact_id=f"fact-{index}", target=f"src/{index}.py",
            evidence_type=kind, payload=(f"evidence-{index}",), confidence=0.5,
            tier=tier, dedup_key=f"dedup-{index}",
        )
        for index, (kind, tier) in enumerate((
            ("cochange_partner", "HYPOTHESIS"),
            ("localization", "INFO"),
            ("covering_red", "VERIFIED"),
        ))
    ]
    original = me.augment
    me.augment = lambda _event, _state: list(envelopes)
    try:
        result = me.run_evidence_pipeline(
            None,
            me.classify_event(
                "python -m pytest", "1 failed", 1, action_index=3,
                test_outcome="fail",
            ),
            dedup_chain=set(), chain_head="", episode_id="e", event_id="e:3",
        )
        assert {dose.envelope.evidence_type for dose in result.doses} == {
            "covering_red", "localization", "cochange_partner",
        }
        assert result.doses[0].previous_chain_head == ""
        assert result.doses[1].previous_chain_head == result.doses[0].chain_head
        assert result.doses[2].previous_chain_head == result.doses[1].chain_head
    finally:
        me.augment = original


def test_run_evidence_pipeline_lifetime_delivery_does_not_starve_current_repeat():
    env = EvidenceEnvelope(
        producer="test", fact_id="failure", target="src/mod.py",
        evidence_type="covering_red", payload=("still failing",), confidence=1.0,
        tier="VERIFIED", dedup_key="same-current-failure",
    )
    original = me.augment
    me.augment = lambda _event, _state: [env]
    try:
        lifetime_chain = {env.dedup_key}
        result = me.run_evidence_pipeline(
            None,
            me.classify_event(
                "python -m pytest", "1 failed", 1, action_index=4,
                test_outcome="fail",
            ),
            dedup_chain=lifetime_chain, chain_head="1" * 64,
            episode_id="e", event_id="e:4",
        )
        assert [dose.envelope.dedup_key for dose in result.doses] == [env.dedup_key]
        assert lifetime_chain == {env.dedup_key}
    finally:
        me.augment = original


def test_over_budget_candidate_does_not_starve_smaller_candidate():
    oversized = EvidenceEnvelope(
        producer="test", fact_id="large", target="src/large.py",
        evidence_type="covering_red", payload=("x" * 9000,), confidence=1.0,
        tier="VERIFIED", dedup_key="large",
    )
    smaller = EvidenceEnvelope(
        producer="test", fact_id="small", target="src/small.py",
        evidence_type="localization", payload=("src/small.py:1",), confidence=0.5,
        tier="INFO", dedup_key="small",
    )
    original = me.augment
    me.augment = lambda _event, _state: [oversized, smaller]
    try:
        result = me.run_evidence_pipeline(
            None,
            me.classify_event("rg small", "src/small.py:1", 0, action_index=5),
            dedup_chain=set(), chain_head="", episode_id="e", event_id="e:5",
        )
        assert [dose.envelope.dedup_key for dose in result.doses] == ["small"]
    finally:
        me.augment = original


def test_over_budget_candidate_is_replaced_by_retrievable_whole_unit(tmp_path):
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.request_history import load_history_evidence

    env = EvidenceEnvelope(
        producer="test", fact_id="large", target="src/large.py",
        evidence_type="covering_red", payload=("x" * 9000,), confidence=1.0,
        tier="VERIFIED", dedup_key="large",
    )
    original = me.augment
    me.augment = lambda _event, _state: [env]
    try:
        store = EvidenceStore(tmp_path / "evidence")
        result = me.run_evidence_pipeline(
            None,
            me.classify_event(
                "python -m pytest", "1 failed", 1, action_index=6,
                test_outcome="fail",
            ),
            dedup_chain=set(), chain_head="", episode_id="e", event_id="e:6",
            model_prefix=True, artifact_store=store,
        )

        assert len(result.doses) == 1
        dose = result.doses[0]
        assert dose.rendered.startswith("[GT_EVIDENCE_REFERENCE:covering_red]\n")
        assert "gt-evidence read " in dose.rendered
        assert dose.artifact_reference is not None
        complete = load_history_evidence(store.root, dose.artifact_reference).decode()
        assert complete.startswith("[GT_EVIDENCE:covering_red]\n")
        assert "x" * 9000 in complete
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
