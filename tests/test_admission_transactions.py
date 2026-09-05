from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.gt_session import GTSession, GTSessionConfig
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
    adapter = adapter_for(tmp_path)
    lines = [f"source{i}.py:1 score=1 reasons=content_token:compute" for i in range(50)]
    candidate = "[GT_EVIDENCE:localization]\n" + "\n".join(lines)
    monkeypatch.setattr(adapter, "task_start_localization", lambda **_: candidate)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    rendered = session.before_model([], iteration=0).context_additions[0]
    assert len(rendered.encode()) <= 1400
    assert all(line in lines for line in rendered.splitlines()[1:])
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


def test_cochange_quota_counts_only_final_provider_deliveries(tmp_path):
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
    assert not offer(4, "third")

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    refusal = [row for row in rows if row.get("event") == "delivery_refused"][-1]
    assert refusal["reason"] == "cochange_task_ceiling"


def test_provider_refusal_allows_identical_delivery_retry(tmp_path):
    adapter = adapter_for(tmp_path)
    assert admit(adapter, 0, "retry these exact bytes")
    adapter.discard_pending_provider_deliveries(reason="provider_refused")
    assert admit(adapter, 1, "retry these exact bytes")
