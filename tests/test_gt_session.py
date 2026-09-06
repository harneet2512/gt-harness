from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.gt_session import (
    Assurance,
    GTDecisionCandidate,
    GTMode,
    GTSession,
    GTSessionConfig,
)
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.output_evidence import EvidenceStore
from gt_engine.request_history import load_history_evidence, store_history_evidence


def _adapter(tmp_path):
    return MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=[Predicate("p", "p")]
    )


def test_session_negotiates_degraded_assurance_without_state_dir(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(
        GTSessionConfig(task_id="t", capabilities=("exact_provider_payload",)),
        engine=a,
    )
    assert s.assurance_state is Assurance.DEGRADED
    assert "exact_provider_payload" in s.degraded_notes()[0]


def test_session_empty_capability_declaration_is_degraded(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(
        GTSessionConfig(task_id="t", state_dir=str(tmp_path), capabilities=()),
        engine=a,
    )
    assert s.assurance_state is Assurance.DEGRADED
    assert s.degraded_notes() == ("no host capabilities declared",)
    assert s.mode is GTMode.ADVISORY
    assert s.disabled is False


def test_session_start_and_context_delta(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    s.start()
    batch = s.before_model([{"role": "user", "content": "x"}], iteration=0)
    assert batch.empty  # no contract on a plain adapter


def test_rejected_contract_is_available_on_later_request(tmp_path, monkeypatch):
    from gt_engine.task_contract import extract_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    contract = extract_task_contract("compute() must return an empty list for empty input.")
    compiled = compile_obligation_predicates(contract)
    adapter = MiniSweAdapter(task_id="retry", state_dir=tmp_path, contract=contract,
        predicates=[Predicate(compiled[o.obligation_id].predicate_id, o.text)
                    for o in contract.obligations])
    session = GTSession(GTSessionConfig(task_id="retry", delivery_path="legacy"), engine=adapter)
    admit = adapter.admit_model_visible_delivery
    monkeypatch.setattr(adapter, "admit_model_visible_delivery", lambda **_: False)
    assert session.before_model([], iteration=0).empty
    assert not adapter.contract_shipped
    monkeypatch.setattr(adapter, "admit_model_visible_delivery", admit)
    rendered = session.before_model([], iteration=1).context_additions[0]
    delivery = adapter.bind_provider_payload({
        "messages": [{"role": "user", "content": rendered}]
    })
    session.provider_request_admitted(delivery.delivery_ids)
    assert adapter.contract_shipped


def test_unadmitted_localization_can_retry_after_initial_boundary(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    results = iter(["", "[GT_EVIDENCE:localization]\nsource.py:1"])
    monkeypatch.setattr(adapter, "task_start_localization", lambda **_: next(results))
    session = GTSession(GTSessionConfig(task_id="retry"), engine=adapter)
    assert not session.before_model([], iteration=0).context_additions
    assert session.before_model([], iteration=1).context_additions


def test_session_completion_state_reports_unverified_when_unknown(tmp_path):
    a = _adapter(tmp_path)
    a.start_task()
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is False
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    state = s.completion_state()
    assert state.get("verified", False) is False
    assert state["phase"] == "IMPLEMENT"


def test_degraded_session_cannot_inherit_verified_engine_state(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    monkeypatch.setattr(adapter, "final_state", lambda: {"verified": True, "phase": "SUBMIT"})
    session = GTSession(GTSessionConfig(task_id="t", state_dir=str(tmp_path),
                                      capabilities=("exact_provider_payload",)), engine=adapter)
    session.degrade("before_action", RuntimeError("fixture fault"))
    assert session.completion_state()["verified"] is False


def test_opaque_oversized_localization_is_not_sliced_into_a_claim(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "task_start_localization", lambda **_: "header\n" + "x" * 5000)
    s = GTSession(GTSessionConfig(task_id="t", delivery_path="compiled"), engine=a)

    assert not s.before_model([], iteration=0).context_additions


def test_session_close_records_terminal_event(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    s.close("submitted_unverified")
    rows = [line for line in (tmp_path / "task" / "events.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    assert any('"session_closed"' in r and '"submitted_unverified"' in r for r in rows)


def test_shadow_computes_but_never_delivers_context(tmp_path):
    from gt_engine.task_contract import extract_task_contract

    contract = extract_task_contract("Fix compute() for empty input.")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path,
        predicates=[Predicate("p", "p")], contract=contract,
    )
    s = GTSession(GTSessionConfig(task_id="t", mode=GTMode.SHADOW), engine=a)
    batch = s.before_model([{"role": "user", "content": "x"}], iteration=0)
    assert batch.context_additions == []


def test_session_degrade_is_idempotent_and_fail_open(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    s.degrade("evidence", RuntimeError("boom"))
    s.degrade("later", RuntimeError("ignored duplicate"))
    assert s.disabled is True
    assert s.assurance_state is Assurance.DEGRADED
    assert len([n for n in s.degraded_notes() if "boom" in n]) == 1
    accepted, batch = s.request_submit()
    assert accepted is True
    assert batch.empty


def test_capability_modes_and_kill_switches_are_independent(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    s = GTSession(
        GTSessionConfig(
            task_id="t",
            capability_modes={
                "typed_actions": GTMode.ASSISTIVE,
                "execution_evidence": GTMode.SHADOW,
            },
            disabled_capabilities=("graph_queries",),
        ),
        engine=a,
    )
    assert s.capability_mode("typed_actions") is GTMode.ASSISTIVE
    assert s.capability_model_visible("typed_actions") is True
    assert s.capability_model_visible("execution_evidence") is False
    assert s.capability_active("graph_queries") is False

    monkeypatch.setenv("GT_DISABLED_CAPABILITIES", "typed_actions")
    assert s.capability_active("typed_actions") is False


def test_global_kill_switch_is_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_KILL_SWITCH", "1")
    s = GTSession(GTSessionConfig(task_id="t"), engine=_adapter(tmp_path))
    assert s.disabled is True
    assert s.disabled_stage == "global_kill_switch"


def test_compiled_delivery_places_task_start_evidence_in_first_request_once(
    tmp_path, monkeypatch
):
    a = _adapter(tmp_path)
    calls = []

    def localization(**_):
        calls.append(True)
        return "[GT_EVIDENCE:localization]\nsrc/mod.py"

    monkeypatch.setattr(a, "task_start_localization", localization)
    s = GTSession(
        GTSessionConfig(task_id="t", delivery_path="compiled"), engine=a
    )
    first = s.before_model([{"role": "user", "content": "x"}], iteration=0)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "user", "content": first.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)
    second = s.before_model([{"role": "user", "content": "x"}], iteration=0)
    assert len(first.context_additions) == 1
    assert '"supersession_key":"localization:task"' in first.context_additions[0]
    assert "[GT_EVIDENCE:localization]\nsrc/mod.py" in first.context_additions[0]
    assert second.context_additions == []
    assert len(calls) == 1


def test_wrapped_contract_and_localization_latches_bind_actual_request_bytes(
    tmp_path, monkeypatch,
):
    from gt_engine.task_contract import extract_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    contract = extract_task_contract("compute() must handle an empty list.")
    compiled = compile_obligation_predicates(contract)
    a = MiniSweAdapter(
        task_id="wrapped", state_dir=tmp_path, contract=contract,
        predicates=[
            Predicate(compiled[item.obligation_id].predicate_id, item.text)
            for item in contract.obligations
        ],
    )
    localization_calls = []
    monkeypatch.setattr(
        a,
        "task_start_localization",
        lambda **_: localization_calls.append(1)
        or "[GT_EVIDENCE:localization]\nsrc/mod.py:1",
    )
    s = GTSession(GTSessionConfig(task_id="wrapped"), engine=a)

    batch = s.before_model([], iteration=0)
    assert len(batch.context_additions) == 2
    assert all(item.startswith("[GT_CONTEXT_UNIT] ") for item in batch.context_additions)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "user", "content": "\n\n".join(batch.context_additions)}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    assert a.contract_shipped
    assert s._task_start_shipped
    assert not s._pending_contract_identity
    assert not s._pending_localization_identity
    assert not s.before_model([], iteration=1).context_additions
    assert localization_calls == [1]


def test_legacy_delivery_switch_does_not_emit_duplicate_runtime_localization(
    tmp_path, monkeypatch
):
    a = _adapter(tmp_path)
    monkeypatch.setattr(
        a, "task_start_localization",
        lambda: (_ for _ in ()).throw(AssertionError("duplicate legacy path")),
    )
    s = GTSession(
        GTSessionConfig(task_id="t", delivery_path="legacy"), engine=a
    )
    assert s.before_model([], iteration=0).context_additions == []


def test_prompt_context_addition_is_bounded_and_receipt_visible(
    tmp_path, monkeypatch
):
    from gt_engine.request_history import load_history_evidence

    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "next_contract_delta", lambda **_kwargs: "x" * 2_400)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    batch = s.before_model([{"role": "user", "content": "x"}], iteration=0)

    assert len(batch.context_additions) == 1
    rendered = batch.context_additions[0]
    encoded = rendered.encode("utf-8")
    assert len(encoded) <= 2_000
    assert "[GT_CONTEXT_UNIT_REFERENCE]" in rendered
    prepared_unit = next(
        json.loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
        if '"decision_context_unit_prepared"' in line
    )
    original = load_history_evidence(
        a.engine_state.layout.evidence_root, prepared_unit["artifact_reference"]
    ).decode()
    assert original == "[GT_TASK_CONTRACT]\n" + "x" * 2_400
    a.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    rows = [
        json.loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
    ]
    census = [row for row in rows if row["event"] == "context_addition_delivery"]
    assert len(census) == 1
    assert census[0]["kind"] == "context_contract"
    assert census[0]["lane"] == "prompt"
    assert census[0]["rendered_bytes"] == len(encoded)
    assert census[0]["payload_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_localization_reference_preserves_complete_precompaction_unit(
    tmp_path, monkeypatch,
):
    a = _adapter(tmp_path)
    original = "[GT_EVIDENCE:localization]\n" + "\n".join(
        f"src/module_{index}.py:{index} symbol_{index}" for index in range(120)
    )
    monkeypatch.setattr(a, "task_start_localization", lambda **_: original)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    batch = s.before_model([], iteration=0)

    assert len(batch.context_additions) == 1
    prepared = next(
        json.loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
        if '"decision_context_unit_prepared"' in line
    )
    restored = load_history_evidence(
        a.engine_state.layout.evidence_root, prepared["artifact_reference"]
    ).decode()
    assert restored == original


def test_runtime_allows_current_deltas_beyond_twenty_four_requests(
    tmp_path, monkeypatch
):
    a = _adapter(tmp_path)
    deltas = iter(f"delta-{index}" for index in range(25))
    monkeypatch.setattr(a, "next_contract_delta", lambda **_kwargs: next(deltas))
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    delivered = []
    for index in range(25):
        additions = s.before_model([], iteration=index).context_additions
        delivered.append(additions)
        delivery = a.bind_provider_payload({
            "messages": [{"role": "user", "content": additions[0]}]
        })
        s.provider_request_admitted(delivery.delivery_ids)

    assert all(delivered)
    rows = [
        json.loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
    ]
    assert len([row for row in rows if row["event"] == "context_addition_delivery"]) == 25
    refused = [row for row in rows if row["event"] == "delivery_refused"]
    assert not refused


def test_decision_packet_orders_current_facts_before_weak_history(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    candidates = [
        GTDecisionCandidate("cochange", "cochange_partner", "cochange"),
        GTDecisionCandidate("consequence", "signature_mismatch", "signature"),
        GTDecisionCandidate("location", "localization", "location"),
        GTDecisionCandidate("obligation", "context_delta", "obligation", lane="prompt"),
        GTDecisionCandidate("failure", "covering_red", "failure"),
    ]

    batch = s.admit_decision_packet(candidates, iteration=2, action_index=7)

    assert batch.context_additions == [
        "failure", "obligation", "location", "consequence",
    ]
    assert [item.kind for item in a._pending_provider_deliveries] == [
        "covering_red", "context_delta", "localization", "signature_mismatch",
    ]
    assert "cochange_partner" not in batch.evidence


def test_decision_packet_stages_each_admitted_exposure_independently(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    candidates = [
        GTDecisionCandidate(
            "failure", "covering_red", "failure-key",
            previous_chain_head="0" * 64, next_chain_head="1" * 64,
        ),
        GTDecisionCandidate(
            "location", "localization", "location-key",
            previous_chain_head="1" * 64, next_chain_head="2" * 64,
        ),
    ]

    batch = s.admit_decision_packet(candidates, iteration=2, action_index=7)

    assert batch.context_additions == ["failure", "location"]
    assert {item.dedup_key for item in a._pending_exposures.values()} == {
        "failure-key", "location-key",
    }


def test_decision_packet_uses_typed_outcome_for_execution_evidence_priority(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    batch = s.admit_decision_packet([
        GTDecisionCandidate("history", "cochange_partner", "history"),
        GTDecisionCandidate("location", "localization", "location"),
        GTDecisionCandidate("ordinary execution", "execution_evidence", "pass"),
        GTDecisionCandidate(
            "executed failure", "execution_evidence", "fail", current_failure=True,
        ),
    ], iteration=2, action_index=7)

    assert batch.context_additions == [
        "executed failure", "location", "ordinary execution", "history",
    ]


def test_before_model_reorders_queued_history_behind_current_obligation_and_retries(
    tmp_path, monkeypatch,
):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "next_contract_delta", lambda **_: "current obligation")
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    s.queue_decision_candidates(tuple(
        GTDecisionCandidate(
            f"history-{index}", "cochange_partner", f"history-{index}",
            source_ordinal=index,
        )
        for index in range(4)
    ))

    first = s.before_model([], iteration=0)
    assert "[GT_TASK_CONTRACT]\ncurrent obligation" in first.context_additions[0]
    assert first.context_additions[1:] == ["history-0", "history-1", "history-2"]
    a.discard_pending_provider_deliveries(reason="provider_refused")

    retry = s.before_model([], iteration=0)
    assert retry.context_additions == first.context_additions
    delivery = a.bind_provider_payload({
        "messages": [{"role": "user", "content": "\n".join(retry.context_additions)}]
    })
    s.provider_request_admitted(delivery.delivery_ids)
    assert s._queued_decision_candidates == []


def test_refused_middle_dose_is_removed_from_the_admitted_chain(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    admit = a.admit_model_visible_delivery

    def refuse_location(**kwargs):
        return False if kwargs["kind"] == "localization" else admit(**kwargs)

    monkeypatch.setattr(a, "admit_model_visible_delivery", refuse_location)
    batch = s.admit_decision_packet([
        GTDecisionCandidate(
            "failure", "covering_red", "failure", next_chain_head="candidate-1",
        ),
        GTDecisionCandidate(
            "location", "localization", "location", next_chain_head="candidate-2",
        ),
        GTDecisionCandidate(
            "consequence", "signature_mismatch", "consequence",
            next_chain_head="candidate-3",
        ),
    ], iteration=1, action_index=3)

    assert batch.context_additions == ["failure", "consequence"]
    by_key = {exposure.dedup_key: exposure for exposure in a._pending_exposures.values()}
    assert by_key["consequence"].previous_chain_head == by_key["failure"].next_chain_head


def test_current_claim_replacement_records_explicit_revision_supersession(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    first = GTDecisionCandidate(
        "failure at r1", "covering_red", "failure-r1",
        unit_id="failure-r1", supersession_key="current-failure:test_a",
        source_revision="r1", current_failure=True,
    )
    batch = s.admit_decision_packet([first], iteration=0, action_index=1)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": batch.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    a.repository_revision = "r2"
    second = GTDecisionCandidate(
        "failure at r2", "covering_red", "failure-r2",
        unit_id="failure-r2", supersession_key="current-failure:test_a",
        source_revision="r2", current_failure=True,
    )
    batch = s.admit_decision_packet([second], iteration=1, action_index=2)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": batch.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    rows = [json.loads(line) for line in a.store.path.read_text().splitlines()]
    admitted = [
        row for row in rows if row["event"] == "decision_context_unit_admitted"
    ]
    assert admitted[-1]["unit_id"] == "failure-r2"
    assert admitted[-1]["supersedes"] == ["failure-r1"]
    assert admitted[-1]["source_revision"] == "r2"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("current_kind,current_text,historical_kind,historical_text", [
    ("covering_red", "current FAIL r2", "execution_evidence", "old PASS r1"),
    ("execution_evidence", "current PASS r2", "covering_red", "old FAIL r1"),
])
def test_current_engine_revision_cannot_be_superseded_by_historical_evidence(
    tmp_path, reverse, current_kind, current_text, historical_kind, historical_text,
):
    a = _adapter(tmp_path)
    a.repository_revision = "r2"
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    current = GTDecisionCandidate(
        current_text, current_kind, "current", unit_id="current-r2",
        supersession_key="check:test_a", source_revision="r2",
        current_failure=current_kind == "covering_red",
    )
    historical = GTDecisionCandidate(
        historical_text, historical_kind, "historical", unit_id="historical-r1",
        supersession_key="check:test_a", source_revision="r1",
        current_failure=historical_kind == "covering_red",
    )
    candidates = [historical, current] if reverse else [current, historical]

    batch = s.admit_decision_packet(candidates, iteration=0, action_index=2)

    assert current_text in batch.context_additions[0]
    assert '"historical":false' in batch.context_additions[0]
    historical_unit = next(
        item for item in batch.context_additions
        if '"unit_id":"historical-r1"' in item
    )
    assert '"historical":true' in historical_unit
    assert "[GT_CONTEXT_UNIT_REFERENCE]" in historical_unit
    prepared = [
        json.loads(line) for line in a.store.path.read_text().splitlines()
        if '"decision_context_unit_prepared"' in line
    ]
    historical_row = next(row for row in prepared if row["unit_id"] == "historical-r1")
    assert load_history_evidence(
        a.engine_state.layout.evidence_root, historical_row["artifact_reference"]
    ).decode() == historical_text
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": "\n".join(batch.context_additions)}]
    })
    s.provider_request_admitted(delivery.delivery_ids)
    assert s._active_context_units["check:test_a"]["unit_id"] == "current-r2"


def test_same_revision_claim_cannot_be_replaced_implicitly(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    first = GTDecisionCandidate(
        "first", "localization", "first", unit_id="loc-1",
        supersession_key="localization:task", source_revision="r1",
    )
    batch = s.admit_decision_packet([first], iteration=0, action_index=1)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": batch.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    replacement = GTDecisionCandidate(
        "opposite", "localization", "second", unit_id="loc-2",
        supersession_key="localization:task", source_revision="r1",
    )
    assert s.admit_decision_packet(
        [replacement], iteration=1, action_index=2
    ).context_additions == []


def test_admitted_unit_retains_its_retrieval_reference(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    reference = store_history_evidence(
        EvidenceStore(a.engine_state.layout.evidence_root),
        b"complete original failure evidence",
        kind="decision_evidence",
    )
    candidate = GTDecisionCandidate(
        "[GT_EVIDENCE_REFERENCE:covering_red]", "covering_red", "failure",
        artifact_sha256="a" * 64, artifact_reference=reference,
        unit_id="failure-ref", supersession_key="current-failure:test_a",
        source_revision="r1", current_failure=True,
    )
    batch = s.admit_decision_packet([candidate], iteration=0, action_index=1)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": batch.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    rows = [json.loads(line) for line in a.store.path.read_text().splitlines()]
    admitted = next(
        row for row in rows if row["event"] == "decision_context_unit_admitted"
    )
    assert admitted["artifact_reference"] == reference


def test_nonexistent_or_wrong_root_reference_is_refused(tmp_path):
    a = _adapter(tmp_path)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    reference = {
        "schema": "gt.output_artifact.v1", "root": str(tmp_path / "elsewhere"),
        "sha256": "a" * 64, "total_length": 1, "encoding": "utf-8",
        "kind": "decision_evidence",
        "retrieval_command": f"gt-evidence read {'a' * 64} 0 8192",
    }
    candidate = GTDecisionCandidate(
        "untrusted", "covering_red", "failure", artifact_reference=reference,
        unit_id="bad-ref", supersession_key="current-failure:test_a",
        source_revision="r1", current_failure=True,
    )

    assert not s.admit_decision_packet(
        [candidate], iteration=0, action_index=1
    ).context_additions


def test_later_execution_supersedes_same_revision_outcome(tmp_path):
    a = _adapter(tmp_path)
    a.repository_revision = "unchanged"
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)
    first = GTDecisionCandidate(
        "PASS", "execution_evidence", "pass", action_index=1,
        unit_id="pass-1", supersession_key="execution:command", source_revision="unchanged",
    )
    batch = s.admit_decision_packet([first], iteration=0, action_index=1)
    delivery = a.bind_provider_payload({
        "messages": [{"role": "tool", "content": batch.context_additions[0]}]
    })
    s.provider_request_admitted(delivery.delivery_ids)

    failure = GTDecisionCandidate(
        "FAIL", "execution_evidence", "fail", action_index=2,
        unit_id="fail-2", supersession_key="execution:command", source_revision="unchanged",
        current_failure=True,
    )
    batch = s.admit_decision_packet([failure], iteration=1, action_index=2)

    assert len(batch.context_additions) == 1
    assert '"supersedes":["pass-1"]' in batch.context_additions[0]


def _promoted(edges, *, digest="a" * 64):
    """A published terminal row plus the receipt blob that quantifies it."""
    return (
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r0",
         "artifact_sha256": digest, "artifact_blob": f"lsp_receipts/{digest}.json"},
        {digest: {"verified": edges, "corrected": 0, "deleted": 0}},
    )


def _capability_rows(tmp_path, rows, blobs=None, *, disabled=False,
                     disabled_stage="", mode=None):
    mode = GTMode.ENFORCED if mode is None else mode
    """Drive _mandatory_capability_rows against a journal written by hand.

    Called unbound on purpose. This helper reads only the journal, and the one
    defect it has already shipped (a NameError on a name the module never
    imported) survived a green suite because nothing reached the line. A test
    that constructs a whole session would not have reached it either.
    """
    from types import SimpleNamespace

    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    for digest, payload in (blobs or {}).items():
        blob = tmp_path / "lsp_receipts" / f"{digest}.json"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_text(json.dumps(payload), encoding="utf-8")
    stub = SimpleNamespace(
        _engine=SimpleNamespace(
            store=SimpleNamespace(path=str(journal), root=tmp_path)
        ),
        disabled=disabled,
        disabled_stage=disabled_stage,
        mode=mode,
    )
    return {
        name: (str(state), evidence)
        for name, state, evidence, _ in GTSession._mandatory_capability_rows(stub)
    }


_DENSE_READY = {"event": "dense_index_ready", "query_ready": True}


def test_successful_promotion_is_not_reported_as_a_capability_that_failed(tmp_path):
    """The regression this pins: promotion worked, so nothing may say it did not.

    The first version read lsp-promotion.json, which is sealed by indexer's
    reporting-only start_lsp_promotion and always says promotion_not_scheduled
    on the benchmark path. Every run - including one whose coordinator promoted
    successfully - was therefore named at end of task as a capability that did
    not work. The benchmark tap reports through the journal, so that is the
    only record this may read.
    """
    terminal, blobs = _promoted(3)
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "task_id": "t1",
         "graph_revision": "r0"},
        terminal,
    ], blobs)

    assert rows["lsp_promotion"] == ("WORKING", "terminal_succeeded:published:3_edges")
    assert rows["dense_retrieval"][0] == "WORKING"


def test_promotion_that_never_scheduled_is_failed_not_silent(tmp_path):
    """A tap that never fired is the outcome the end-of-task report exists for."""
    rows = _capability_rows(tmp_path, [_DENSE_READY])

    assert rows["lsp_promotion"] == ("FAILED", "promotion_never_scheduled")


@pytest.mark.parametrize(
    ("status", "expected_state", "expected_evidence"),
    [
        # Adds zero edges, exactly like cancelled: on the axis the report is
        # for - is the highest-precision tier populated - both are degraded.
        # no_op names its own reason: its coordinator disposition is the
        # generic not_publishable bucket, which implies something existed that
        # could not be published, and nothing did.
        ("no_op", "DEGRADED", "terminal_no_op:nothing_promotable"),
        # No language server for a language the graph could have promoted.
        ("unavailable", "FAILED", "terminal_unavailable:d"),
        ("failed", "FAILED", "terminal_failed:d"),
        ("cancelled", "DEGRADED", "terminal_cancelled:d"),
    ],
)
def test_each_terminal_status_maps_to_a_distinguishable_state(
    tmp_path, status, expected_state, expected_evidence
):
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_terminal", "status": status, "disposition": "d"},
    ])

    assert rows["lsp_promotion"] == (expected_state, expected_evidence)


def test_scheduled_without_a_terminal_is_degraded(tmp_path):
    rows = _capability_rows(tmp_path, [
        _DENSE_READY, {"event": "lsp_promotion_scheduled", "task_id": "t1"},
    ])

    assert rows["lsp_promotion"] == ("DEGRADED", "scheduled_no_terminal")


def test_an_unreadable_journal_fails_both_capabilities(tmp_path):
    """"We could not tell" must never render the same as "it worked"."""
    from types import SimpleNamespace

    stub = SimpleNamespace(
        _engine=SimpleNamespace(
            store=SimpleNamespace(path=str(tmp_path / "absent.jsonl"))
        ),
        disabled=False, disabled_stage="", mode=GTMode.ENFORCED,
    )
    rows = {
        name: (str(state), evidence)
        for name, state, evidence, _ in GTSession._mandatory_capability_rows(stub)
    }

    assert rows["lsp_promotion"] == ("FAILED", "promotion_journal_unreadable")
    assert rows["dense_retrieval"] == ("FAILED", "dense_index_receipt_unreadable")


def test_a_superseded_enrichment_does_not_override_the_final_one(tmp_path):
    """poll() journals draining enrichments AFTER the active one.

    graph_coordinator.py:430-433 observes the active enrichment first and the
    draining ones after, so a stale cancelled receipt for a superseded graph
    revision lands later in the journal than the success that superseded it.
    Taking the literal last row would report DEGRADED for a run that promoted -
    the same misreport this whole helper exists to prevent.
    """
    digest = "b" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_scheduled", "graph_revision": "r1"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r1",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
        {"event": "lsp_promotion_terminal", "status": "cancelled",
         "disposition": "obsolete", "input_graph_revision": "r0"},
    ], {digest: {"verified": 1, "corrected": 1, "deleted": 0}})

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:2_edges:last_of_2"
    )


def test_repeated_failures_before_a_success_are_not_hidden(tmp_path):
    """One success after several failures must not read as an unblemished run."""
    terminal, blobs = _promoted(6)
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "failed",
         "disposition": "factory_exception", "input_graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "failed",
         "disposition": "certification_failed", "input_graph_revision": "r0"},
        terminal,
    ], blobs)

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:6_edges:last_of_3"
    )


def test_a_truncated_final_line_fails_the_dense_capability_closed(tmp_path):
    """The likeliest corruption is a half-written last line, not an absent file.

    Parsing is line by line, so an earlier dense_index_ready row had already
    set WORKING before the truncated line raised. Without an explicit reset the
    row read WORKING with evidence dense_index_receipt_unreadable - a state
    contradicting its own evidence, in the summary a human reads, in exactly
    the case where a process was killed at its budget.
    """
    from types import SimpleNamespace

    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps(_DENSE_READY) + "\n" + '{"event": "lsp_promotion_sched',
        encoding="utf-8",
    )
    stub = SimpleNamespace(
        _engine=SimpleNamespace(store=SimpleNamespace(path=str(journal))),
        disabled=False, disabled_stage="", mode=GTMode.ENFORCED,
    )
    rows = {
        name: (str(state), evidence)
        for name, state, evidence, _ in GTSession._mandatory_capability_rows(stub)
    }

    assert rows["dense_retrieval"] == ("FAILED", "dense_index_receipt_unreadable")
    assert rows["lsp_promotion"] == ("FAILED", "promotion_journal_unreadable")


def test_promotion_that_lost_the_publication_race_is_reported_not_erased(tmp_path):
    """An active enrichment can terminate `obsolete`, and it must still speak.

    graph_coordinator.py emits the bare string "obsolete" from TWO places: the
    draining path at :395 and the ACTIVE path at :312, where it fires whenever
    _state.source_revision advanced while the enrichment ran - which it does on
    every action boundary, because record_repository_snapshot rebinds a
    content-addressed revision. Promotion succeeding and losing the race to the
    next rebuild is therefore a common outcome, not an exotic one.

    A previous version dropped every disposition prefixed obsolete, which
    erased exactly these rows and reported `scheduled_no_terminal` - promotion
    never ran - for a run where it ran and succeeded every time. The two point
    at completely different fixes and must never read alike.
    """
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "obsolete", "input_graph_revision": "r0"},
    ])

    assert rows["lsp_promotion"] == ("DEGRADED", "terminal_succeeded:obsolete")


def test_a_certified_candidate_that_lost_the_race_is_degraded_not_working(tmp_path):
    """obsolete_after_certification is the strongest positive evidence there is.

    By that point the candidate certifier has already returned success: the
    enriched graph exists and is certified, and only publish_graph lost the
    race. It is still not WORKING, because the graph the agent used carries
    none of those edges - but it is emphatically not a failure either.
    """
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "obsolete_after_certification",
         "input_graph_revision": "r0"},
    ])

    assert rows["lsp_promotion"] == (
        "DEGRADED", "terminal_succeeded:obsolete_after_certification"
    )


def test_agreeing_terminals_do_not_raise_a_count_alarm(tmp_path):
    """Every published graph gets an enrichment, so N terminals is normal.

    A bare count fired on every clean multi-edit run, which trains a reader to
    ignore the field. The suffix now marks disagreement, not volume.
    """
    digest = "c" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_scheduled", "graph_revision": "r1"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "obsolete", "input_graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r1",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
    ], {digest: {"verified": 4, "corrected": 0, "deleted": 0}})

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:4_edges"
    )


def test_a_failure_that_never_reached_the_scheduler_is_not_outranked(tmp_path):
    """factory_exception terminals carry no scheduled row, and must rank newest.

    _schedule_lsp_candidate raises lsp_base_uncertified, unsafe_lsp_source_path
    and lsp_source_input_mismatch BEFORE it journals lsp_promotion_scheduled,
    and consider_enrichment converts any such raise into a factory_exception
    terminal. So the rows with no scheduled row are exactly the failures.
    Ranking an unknown revision oldest let a healthier earlier revision outrank
    the news that the factory had started throwing on every graph - a run that
    broke halfway through reported the state it had before it broke.
    """
    terminal, blobs = _promoted(5)
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        terminal,
        {"event": "lsp_promotion_terminal", "status": "failed",
         "disposition": "factory_exception", "input_graph_revision": "r1"},
    ], blobs)

    assert rows["lsp_promotion"] == (
        "FAILED", "terminal_failed:factory_exception:last_of_2"
    )


def test_a_published_graph_with_no_promoted_edges_is_not_working(tmp_path):
    """Publication is necessary and not sufficient.

    A receipt can return succeeded with verified/corrected/deleted all zero:
    edge_mutations is 0, the closure is never rebuilt, and the candidate is a
    semantically identical copy of the base that certifies and publishes
    cleanly. WORKING there would reproduce the original complaint - an empty
    highest-precision tier described as healthy - inside the reporter written
    to catch it.
    """
    terminal, blobs = _promoted(0)
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        terminal,
    ], blobs)

    assert rows["lsp_promotion"] == (
        "DEGRADED", "terminal_succeeded:published:0_edges"
    )


def test_an_unreadable_receipt_blob_is_not_reported_as_success(tmp_path):
    """"We could not tell" and "it worked" must not look alike here either."""
    terminal, _ = _promoted(7)
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        terminal,
    ])

    assert rows["lsp_promotion"] == (
        "DEGRADED", "terminal_succeeded:published:yield_unknown"
    )


def test_a_run_where_gt_switched_itself_off_says_so(tmp_path):
    """The least visible failure in the system, made visible.

    degrade() writes gt_degraded_fail_open and disables GT for the rest of the
    run. Nothing anywhere read that event - no gate, no receipt, no report, not
    even a test - so a run whose observer stopped observing partway through
    finished looking like any other, and every GT claim after that point came
    from a component that had already given up.
    """
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "gt_degraded_fail_open", "stage": "before_action",
         "error_type": "SQLiteError", "error": "database is locked"},
    ], disabled=True, disabled_stage="before_action")

    assert rows["gt_engine_enabled"] == (
        "FAILED", "disabled_at_before_action:SQLiteError"
    )


def test_the_fail_open_row_never_carries_the_exception_message(tmp_path):
    """capability() asserts its evidence is secret-free.

    The journal row carries the exception text, which is unbounded run content
    and can hold anything the failing component was handling. Only the stage
    and the exception type name reach the report.
    """
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "gt_degraded_fail_open", "stage": "submit_gate",
         "error_type": "ValueError",
         "error": "token sk-do-not-leak-this failed to parse"},
    ], disabled=True, disabled_stage="submit_gate")

    assert "sk-do-not-leak-this" not in rows["gt_engine_enabled"][1]
    assert rows["gt_engine_enabled"] == ("FAILED", "disabled_at_submit_gate:ValueError")


def test_gt_disabled_without_a_journal_row_is_still_reported(tmp_path):
    """degrade() wraps its own append in try/except.

    The state sink may be the very component that failed, so GT can be disabled
    with no row to show for it. Reading only the journal would report that run
    as healthy - the one reading that must never happen here.
    """
    rows = _capability_rows(
        tmp_path, [_DENSE_READY], disabled=True, disabled_stage="record_action"
    )

    assert rows["gt_engine_enabled"] == (
        "FAILED", "disabled_at_record_action:unrecorded"
    )


def test_a_run_that_kept_gt_on_reports_it_working(tmp_path):
    rows = _capability_rows(tmp_path, [_DENSE_READY])

    assert rows["gt_engine_enabled"] == ("WORKING", "no_fail_open_recorded")


def test_gt_disabled_by_configuration_is_not_reported_as_a_fault(tmp_path):
    """A baseline arm arrives at close having done exactly what it was asked.

    Calling that FAILED would cry wolf on every control run and train the
    reader to skip the row on the treatment runs where it carries the signal.
    """
    rows = _capability_rows(
        tmp_path, [_DENSE_READY], disabled=True, disabled_stage="off",
        mode=GTMode.OFF,
    )

    assert rows["gt_engine_enabled"] == (
        "UNEXERCISED", "gt_disabled_by_configuration:off"
    )


def test_the_kill_switch_on_a_treatment_arm_is_a_required_failure(tmp_path):
    """The one case no other artifact in the pipeline can see.

    GT_KILL_SWITCH disables GT regardless of mode, but miniswe_gt_run computes
    gt_active as `not gt_off and gt_mode != "off"` and deliberately excludes
    the kill switch - "a kill switch may preserve native execution but cannot
    relabel ON as OFF". So the run reports gt_mode enforced, treatment
    groundtruth and treatment_status ACTIVE, and treatment_not_active never
    fires, while GT did nothing at all. This row is the only artifact that
    knows.

    Grouping it with the OFF arm because both land in CONFIGURED_OFF_STAGES
    marked it not-required, which made smoke_stage's prior-gate check skip it
    and let a run with zero GT behaviour past every gate. That constant names
    the strings __init__ can produce; it does not classify whether GT was
    required.
    """
    rows = _capability_rows(
        tmp_path, [_DENSE_READY], disabled=True,
        disabled_stage="global_kill_switch", mode=GTMode.ENFORCED,
    )
    required = _capability_required(
        tmp_path, [_DENSE_READY], disabled=True,
        disabled_stage="global_kill_switch", mode=GTMode.ENFORCED,
    )

    assert rows["gt_engine_enabled"] == (
        "FAILED", "gt_disabled_by_kill_switch:enforced"
    )
    assert required["gt_engine_enabled"] is True


def test_the_kill_switch_on_an_off_arm_is_still_not_required(tmp_path):
    """Off either way: the kill switch adds nothing to a run already asked off."""
    required = _capability_required(
        tmp_path, [_DENSE_READY], disabled=True,
        disabled_stage="global_kill_switch", mode=GTMode.OFF,
    )

    assert required["gt_engine_enabled"] is False


def test_an_early_failure_does_not_outrank_later_successes(tmp_path):
    """The mirror of the buried-failure case, which neither earlier rule handled.

    The FIRST enrichment offer is made on the initial graph, which the
    coordinator did not build, so _schedule_lsp_candidate certifies a manifest
    it did not produce and can raise lsp_base_uncertified there while every
    later graph carries a manifest the coordinator made itself. Ranking an
    unscheduled terminal above everything reported FAILED for a run that threw
    once and then published cleanly for the rest of the task; ranking it below
    everything buried a failure that started late. The rank is stamped during
    the parse, so it means "newer than everything scheduled so far".
    """
    digest = "d" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_terminal", "status": "failed",
         "disposition": "factory_exception", "input_graph_revision": "r0"},
        {"event": "lsp_promotion_scheduled", "graph_revision": "r1"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r1",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
    ], {digest: {"verified": 9, "corrected": 0, "deleted": 0}})

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:9_edges:last_of_2"
    )


def test_tombstones_alone_are_not_a_populated_tier(tmp_path):
    """`deleted` is a window-miss tombstone, not a promotion.

    verified and corrected stamp resolution_method='lsp' at confidence 1.0;
    deleted stamps 'lsp_window_miss' at confidence 0.0 and trust tier
    SPECULATIVE, which the closure excludes from traversal. Summing it made a
    receipt of verified=0, corrected=0, deleted=40 report WORKING on a graph
    whose lsp tier was empty and forty of whose edges had just been demoted.
    """
    digest = "e" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r0",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
    ], {digest: {"verified": 0, "corrected": 0, "deleted": 40}})

    assert rows["lsp_promotion"] == (
        "DEGRADED", "terminal_succeeded:published:0_edges:40_tombstoned"
    )


def test_promoted_edges_and_tombstones_are_both_reported(tmp_path):
    """Tombstoning is real precision work and stays visible - just not as yield."""
    digest = "f" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r0",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
    ], {digest: {"verified": 7, "corrected": 5, "deleted": 3}})

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:12_edges:3_tombstoned"
    )


def test_a_stage_this_build_does_not_define_is_not_echoed(tmp_path):
    """The journal is inside the task container and the agent can write to it.

    --state-dir /logs/agent/gt-state puts events.jsonl where the benchmarked
    agent's shell can append to it, so neither field read back from a row is
    trusted as text. A stage this build never passes to degrade() is not a
    stage, and an error_type that is not an identifier is not a type name.
    Clamping on read means the evidence string can only contain values this
    codebase authored - which settles the question of what could be smuggled
    into it rather than arguing about which patterns a denylist catches.
    """
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "gt_degraded_fail_open",
         "stage": "sk-not-a-real-stage-and-not-a-token-either",
         "error_type": "Bearer smuggled", "error": "x"},
    ], disabled=True, disabled_stage="before_action")

    assert rows["gt_engine_enabled"] == (
        "FAILED", "disabled_at_unrecognized_stage:unrecognized_error"
    )


def _capability_required(tmp_path, rows, **kwargs):
    """The required flag alone, which decides CI severity and the gate."""
    from types import SimpleNamespace

    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        "".join(json.dumps(row) + chr(10) for row in rows), encoding="utf-8"
    )
    stub = SimpleNamespace(
        _engine=SimpleNamespace(
            store=SimpleNamespace(path=str(journal), root=tmp_path)
        ),
        disabled=kwargs.get("disabled", False),
        disabled_stage=kwargs.get("disabled_stage", ""),
        mode=kwargs.get("mode", GTMode.ENFORCED),
    )
    return {
        name: required
        for name, _s, _e, required in GTSession._mandatory_capability_rows(stub)
    }


def test_a_capability_asked_to_be_off_is_not_a_required_one(tmp_path):
    """capability() defaults required=True, and that default was the root cause.

    It made a GT-off control arm raise a CI error annotation, and in
    smoke_stage's prior-gate check a hard ValueError blaming verification
    rather than naming configuration. GT running is not a requirement of a run
    configured not to run it - a property of the row, not something each
    consumer should re-derive from the state.
    """
    off = _capability_required(
        tmp_path, [_DENSE_READY], disabled=True, disabled_stage="off",
        mode=GTMode.OFF,
    )
    on = _capability_required(tmp_path, [_DENSE_READY])

    assert off["gt_engine_enabled"] is False
    assert on["gt_engine_enabled"] is True
    # The two that are mandatory stay mandatory in both arms.
    assert off["dense_retrieval"] is True and off["lsp_promotion"] is True


def test_terminal_order_survives_a_journal_with_missing_sequences(tmp_path):
    """Ordering must not depend on a field that some rows may lack.

    ExternalStateStore.__init__ explicitly contemplates a mixed journal and
    hands it to a verifier rather than blessing it. Ranking on the row's own
    `sequence` needed a default for rows without one, and zero sorts such a row
    oldest - so a genuinely newest terminal lacking the field lost to every row
    that had it. That is the same shape as the -1 and len(schedule_order)
    defaults this function has already shipped twice. Parse position is always
    present, so there is no default to get wrong.
    """
    digest = "9" * 64
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "graph_revision": "r0",
         "sequence": 2},
        {"event": "lsp_promotion_terminal", "status": "failed",
         "disposition": "certification_failed", "input_graph_revision": "r0",
         "sequence": 3},
        # Newest, and carries no sequence at all.
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published", "input_graph_revision": "r0",
         "artifact_blob": f"lsp_receipts/{digest}.json"},
    ], {digest: {"verified": 2, "corrected": 0, "deleted": 0}})

    assert rows["lsp_promotion"] == (
        "WORKING", "terminal_succeeded:published:2_edges:last_of_2"
    )


def test_the_degrade_stage_constant_matches_the_actual_call_sites(tmp_path):
    """Automate the hand-check that already caught two invented stages.

    Nothing referenced _DEGRADE_STAGES outside the module, so adding a
    degrade() call site with a new stage would have made the reporter say
    unrecognized_stage on a real fault - a quiet failure, on the fault path.
    """
    import ast as _ast
    from pathlib import Path as _Path

    from gt_engine.gt_session import CONFIGURED_OFF_STAGES, _DEGRADE_STAGES

    root = _Path(__file__).resolve().parent.parent
    found = set()
    test_found = set()
    scanned = 0
    for directory in ("gt_engine", "scripts", "eval", "tests"):
        for path in (root / directory).rglob("*.py"):
            if "fixtures" in path.parts:
                continue
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                # A file that stops parsing drops its call sites silently while
                # the rest keep `found` non-empty, so the count is asserted
                # below rather than trusting that the scan saw everything.
                continue
            scanned += 1
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name != "degrade" or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, _ast.Constant) and isinstance(first.value, str):
                    if "tests" in path.parts:
                        test_found.add(first.value)
                    else:
                        found.add(first.value)

    assert scanned > 100, f"only {scanned} files parsed - the scan lost files"
    assert found, "no degrade() call sites found - the scan itself is broken"
    assert found == set(_DEGRADE_STAGES) - set(CONFIGURED_OFF_STAGES)
    # tests/ is scanned so the count above cannot silently shrink, but its
    # stages are deliberately NOT asserted to be a subset. degrade() accepts
    # any string and several tests exercise the mechanism itself with
    # throwaway stages (evidence, later, injected, fixture) - forcing those to
    # use production stage names would make them assert less, not more.
    # The clamp is the structural guard instead: an unknown stage renders
    # unrecognized_stage, so a REPORTER test written against an invented stage
    # now fails on its own assertion rather than passing quietly. That is what
    # caught before_model and submit, and it needs no scan to keep working.
    assert test_found, "tests exercise degrade() and the scan saw none of it"
