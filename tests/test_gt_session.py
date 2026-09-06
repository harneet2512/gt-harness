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


def _capability_rows(tmp_path, rows):
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
    stub = SimpleNamespace(
        _engine=SimpleNamespace(store=SimpleNamespace(path=str(journal)))
    )
    return {
        name: (str(state), evidence)
        for name, state, evidence in GTSession._mandatory_capability_rows(stub)
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
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_scheduled", "task_id": "t1"},
        {"event": "lsp_promotion_terminal", "status": "succeeded",
         "disposition": "published"},
    ])

    assert rows["lsp_promotion"] == ("WORKING", "terminal_succeeded:published")
    assert rows["dense_retrieval"][0] == "WORKING"


def test_promotion_that_never_scheduled_is_failed_not_silent(tmp_path):
    """A tap that never fired is the outcome the end-of-task report exists for."""
    rows = _capability_rows(tmp_path, [_DENSE_READY])

    assert rows["lsp_promotion"] == ("FAILED", "promotion_never_scheduled")


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        # Promotion ran and had nothing to promote: the capability worked and
        # the empty edge tier is a property of the repository.
        ("no_op", "WORKING"),
        # No language server for a language the graph could have promoted.
        ("unavailable", "FAILED"),
        ("failed", "FAILED"),
        ("cancelled", "DEGRADED"),
    ],
)
def test_each_terminal_status_maps_to_a_distinguishable_state(
    tmp_path, status, expected_state
):
    rows = _capability_rows(tmp_path, [
        _DENSE_READY,
        {"event": "lsp_promotion_terminal", "status": status, "disposition": "d"},
    ])

    assert rows["lsp_promotion"] == (expected_state, f"terminal_{status}:d")


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
        )
    )
    rows = {
        name: (str(state), evidence)
        for name, state, evidence in GTSession._mandatory_capability_rows(stub)
    }

    assert rows["lsp_promotion"] == ("FAILED", "promotion_journal_unreadable")
    assert rows["dense_retrieval"] == ("FAILED", "dense_index_receipt_unreadable")
