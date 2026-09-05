from __future__ import annotations

import hashlib
import json

from gt_engine.gt_session import Assurance, GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter


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
    assert first.context_additions == [
        "[GT_EVIDENCE:localization]\nsrc/mod.py"
    ]
    assert second.context_additions == []
    assert len(calls) == 1


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
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "next_contract_delta", lambda **_kwargs: "x" * 2_400)
    s = GTSession(GTSessionConfig(task_id="t"), engine=a)

    batch = s.before_model([{"role": "user", "content": "x"}], iteration=0)

    assert len(batch.context_additions) == 1
    rendered = batch.context_additions[0]
    encoded = rendered.encode("utf-8")
    assert len(encoded) <= 1_400
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
