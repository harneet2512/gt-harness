from __future__ import annotations

import hashlib
import json

import pytest
from groundtruth.runtime.evidence_envelope import EvidenceEnvelope

from gt_engine import miniswe_evidence
from gt_engine.gt_session import GTDecisionCandidate, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter


def adapter_for(tmp_path):
    return MiniSweAdapter(task_id="admission", state_dir=tmp_path, predicates=[])


def admit(adapter, iteration, text):
    return adapter.admit_model_visible_delivery(lane="sealed", kind="syntax_result",
        rendered=text, action_index=0, iteration=iteration, dedup_key=text)


def test_later_current_facts_are_not_starved_by_task_lifetime_budget(tmp_path):
    adapter = adapter_for(tmp_path)
    for iteration in range(40):
        assert admit(adapter, iteration, f"{iteration}:" + "x" * 1300)


def test_each_boundary_accepts_at_most_four_new_claims(tmp_path):
    adapter = adapter_for(tmp_path)
    assert [admit(adapter, 0, str(i)) for i in range(5)] == [True] * 4 + [False]
    assert admit(adapter, 1, "later relevant failure")


def test_failed_blob_write_consumes_no_admission_state(tmp_path, monkeypatch):
    adapter = adapter_for(tmp_path)
    write = adapter.store.put_blob
    def fail(*args, **kwargs):
        raise OSError("fixture storage failure")
    monkeypatch.setattr(adapter.store, "put_blob", fail)
    with pytest.raises(OSError):
        admit(adapter, 0, "current failure")
    assert not adapter._pending_provider_deliveries
    assert adapter._model_visible_delivery_count == 0
    monkeypatch.setattr(adapter.store, "put_blob", write)
    assert admit(adapter, 0, "current failure")


def test_localization_receipt_matches_final_structurally_compacted_bytes(tmp_path, monkeypatch):
    from gt_engine.request_history import load_history_evidence

    adapter = adapter_for(tmp_path)
    lines = [f"source{i}.py:1 score=1 reasons=content_token:compute" for i in range(50)]
    candidate = "[GT_EVIDENCE:localization]\n" + "\n".join(lines)
    monkeypatch.setattr(adapter, "task_start_localization", lambda **_: candidate)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    rendered = session.before_model([], iteration=0).context_additions[0]
    assert len(rendered.encode()) <= 1400
    assert "[GT_CONTEXT_UNIT_REFERENCE]" in rendered
    prepared = next(
        json.loads(line)
        for line in adapter.store.path.read_text().splitlines()
        if '"decision_context_unit_prepared"' in line
    )
    complete = load_history_evidence(
        adapter.engine_state.layout.evidence_root, prepared["artifact_reference"]
    ).decode()
    assert complete.startswith("[GT_EVIDENCE:localization]\n")
    assert all(line in lines for line in complete.splitlines()[1:])
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    receipts = [row for row in rows if row["event"] == "evidence_delivery"]
    assert len(receipts) == 1
    assert receipts[0]["payload_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()


def test_no_match_localization_does_not_rescan_unchanged_workspace(tmp_path, monkeypatch):
    adapter = adapter_for(tmp_path)
    adapter.issue_text = "compute"
    calls = []
    monkeypatch.setattr(adapter, "_lexical_task_localization", lambda: calls.append(1) or "")
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    session.before_model([], iteration=0)
    session.before_model([], iteration=1)
    assert calls == [1]


def test_localization_preview_does_not_seal_or_admit(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "compute.py").write_text("def compute(): return 1\n")
    adapter = adapter_for(tmp_path / "state")
    adapter.repo_root = str(source)
    adapter.issue_text = "compute"
    rendered = adapter.task_start_localization(commit=False)
    assert "compute.py:1" in rendered
    assert not adapter._pending_provider_deliveries
    assert not adapter._dedup_chain
    assert adapter.task_start_localization(commit=False) == rendered


def test_cochange_history_never_blocks_a_later_decision(tmp_path):
    adapter = adapter_for(tmp_path)

    def offer(iteration, text):
        return adapter.admit_model_visible_delivery(
            lane="sealed",
            kind="cochange_partner",
            rendered=text,
            action_index=iteration,
            iteration=iteration,
            dedup_key=text,
        )

    assert offer(1, "first")
    adapter.discard_pending_provider_deliveries(reason="provider_refused")
    assert offer(2, "first retry")
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "first retry"}]
    })
    assert offer(3, "second")
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "second"}]
    })
    assert offer(4, "third")
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "third"}]
    })

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    assert len([
        row for row in rows
        if row.get("event") == "evidence_delivery"
        and row.get("kind") == "cochange_partner"
    ]) == 3
    assert not [
        row for row in rows
        if row.get("event") == "delivery_refused"
        and row.get("reason") == "cochange_task_ceiling"
    ]


def test_provider_refusal_allows_identical_delivery_retry(tmp_path):
    adapter = adapter_for(tmp_path)
    assert admit(adapter, 0, "retry these exact bytes")
    adapter.discard_pending_provider_deliveries(reason="provider_refused")
    assert admit(adapter, 1, "retry these exact bytes")


def test_identical_current_fact_can_recur_on_a_later_decision(tmp_path):
    adapter = adapter_for(tmp_path)
    rendered = "same current failure"
    assert admit(adapter, 0, rendered)
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": rendered}]
    })

    assert admit(adapter, 1, rendered)


def test_multidose_request_retains_per_fact_delivery_provenance(tmp_path, monkeypatch):
    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    envelopes = [
        EvidenceEnvelope(
            producer="test", fact_id="failure", target="src/a.py",
            evidence_type="covering_red", payload=("executed failure",),
            confidence=1.0, tier="VERIFIED", dedup_key="failure-key",
        ),
        EvidenceEnvelope(
            producer="test", fact_id="location", target="src/b.py",
            evidence_type="localization", payload=("src/b.py:2",),
            confidence=0.5, tier="INFO", dedup_key="location-key",
        ),
    ]
    monkeypatch.setattr(miniswe_evidence, "augment", lambda *_: envelopes)
    result = miniswe_evidence.run_evidence_pipeline(
        adapter.gateway_state(),
        miniswe_evidence.classify_event(
            "python -m pytest", "1 failed", 1, action_index=1,
            test_outcome="fail",
        ),
        dedup_chain=set(), chain_head="", episode_id="admission",
        event_id="admission:1",
    )
    candidates = tuple(
        GTDecisionCandidate(
            rendered=dose.rendered,
            kind=dose.envelope.evidence_type,
            dedup_key=dose.envelope.dedup_key,
            target=dose.envelope.target,
            previous_chain_head=dose.previous_chain_head,
            next_chain_head=dose.chain_head,
            source_ordinal=ordinal,
            current_failure=dose.envelope.evidence_type == "covering_red",
            artifact_sha256=(
                dose.artifact_reference["sha256"] if dose.artifact_reference else ""
            ),
            artifact_reference=dose.artifact_reference,
        )
        for ordinal, dose in enumerate(result.doses)
    )
    batch = session.admit_decision_packet(candidates, iteration=0, action_index=1)
    delivery = adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "\n".join(batch.context_additions)}]
    })

    assert len(delivery.delivery_ids) == 2
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    delivered = [row for row in rows if row["event"] == "evidence_delivery"]
    assert [row["dedup_key"] for row in delivered] == ["failure-key", "location-key"]
    assert len({row["payload_sha256"] for row in delivered}) == 2
    assert adapter._dedup_chain == {"failure-key", "location-key"}


# The two ceilings are per DECISION, not per run. Clearing them at a decision
# boundary is only correct if they still BIND inside one - otherwise the fix
# for "GT stops contributing" becomes "GT has no ceilings at all".


def _refusals(adapter, reason):
    return [
        row for row in (
            json.loads(line)
            for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        )
        if row.get("event") == "delivery_refused" and row.get("reason") == reason
    ]


def test_duplicate_identity_still_refused_within_one_decision(tmp_path):
    adapter = adapter_for(tmp_path)
    rendered = "same current failure"
    assert admit(adapter, 0, rendered)
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": rendered}]
    })
    # A later decision may re-offer it - that is the point of the reset.
    assert admit(adapter, 1, rendered)
    # Inside one decision the same bytes are idempotent, not duplicated:
    # accepted again, but they do not become a second pending delivery.
    pending = len(adapter._pending_provider_deliveries)
    assert admit(adapter, 1, rendered)
    assert len(adapter._pending_provider_deliveries) == pending


def test_boundary_ceiling_governs_cochange_within_one_decision(tmp_path):
    """What actually limits a decision, now that the run-scoped count is gone.

    An earlier version of this test asserted a two-per-decision cochange
    limit. That threshold was invented here, not in the product: it
    contradicted test_before_model_reorders_queued_history_behind_current_
    obligation_and_retries, which delivers THREE cochange partners in one
    decision and was green. The real intra-decision limit is
    MAX_BOUNDARY_CLAIMS, shared by every kind.
    """
    adapter = adapter_for(tmp_path)

    def offer(iteration, text):
        return adapter.admit_model_visible_delivery(
            lane="sealed", kind="cochange_partner", rendered=text,
            action_index=iteration, iteration=iteration, dedup_key=text,
        )

    assert [offer(0, f"partner {index}") for index in range(5)] == [True] * 4 + [False]
    assert _refusals(adapter, "boundary_claim_ceiling")
    assert not _refusals(adapter, "cochange_task_ceiling")

    # A new decision restores the whole allowance.
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "partner 0"}]
    })
    assert [offer(1, f"later {index}") for index in range(5)] == [True] * 4 + [False]
