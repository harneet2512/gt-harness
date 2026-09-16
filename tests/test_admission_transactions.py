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


def _graph_adapter(tmp_path, *, cochange_rows=()):
    """Adapter bound to a real on-disk graph with a controllable cochanges
    table - the F4 lane-death probe reads the table, not a flag."""
    import sqlite3

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    graph = tmp_path / "graph.db"
    con = sqlite3.connect(graph)
    con.execute("CREATE TABLE cochanges (file_a TEXT, file_b TEXT, count INTEGER)")
    con.execute("CREATE TABLE resolution_symbols (stable_id TEXT, path TEXT)")
    for a, b in cochange_rows:
        con.execute("INSERT INTO cochanges VALUES (?,?,1)", (a, b))
    con.commit()
    con.close()
    (tmp_path / "graph.manifest.json").write_text(
        json.dumps({"graph_revision": "r1"}), encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="admission", state_dir=tmp_path / "state", predicates=[],
        graph_db=str(graph))
    adapter.repo_root = str(repo)
    return adapter


def _cochange_candidate(files=("src/a.py",)):
    return GTDecisionCandidate(
        rendered="", kind="cochange_partner", dedup_key="cochange-unbound",
        supersession_key="cochange_partner:cochange-unbound",
        recipe={"kind": "cochange", "params": {"files": list(files)}},
    )


def _unresolved_rows(adapter):
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if '"delivery_recipe_unresolved"' in line
    ]


def test_dead_cochange_lane_abstains_once_and_stops_queuing(tmp_path):
    """F4 (run 34766499875): a depth-1 checkout has no cochanges table, so the
    lane can never render - 56 identical empty_render rows were the symptom.
    The lane verdict is journaled once, then repeats drop silently and the
    runtime stops queuing the recipe at all."""
    adapter = _graph_adapter(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)

    for files in (("src/a.py",), ("src/b.py",), ("src/c.py",)):
        assert session._resolve_delivery_recipe(
            _cochange_candidate(files)) is None

    rows = _unresolved_rows(adapter)
    assert len(rows) == 1
    assert rows[0]["reason"] == "cochange_history_unavailable"
    assert adapter._cochange_history_dead is True


def test_live_cochange_lane_dedups_empty_renders_per_file_set(tmp_path):
    """A live history table with no partners for these files is a per-query
    answer, not a dead lane: distinct file sets still get their own row."""
    adapter = _graph_adapter(tmp_path, cochange_rows=(("src/x.py", "src/y.py"),))
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)

    assert session._resolve_delivery_recipe(
        _cochange_candidate(("src/a.py",))) is None
    assert session._resolve_delivery_recipe(
        _cochange_candidate(("src/a.py",))) is None
    assert session._resolve_delivery_recipe(
        _cochange_candidate(("src/other.py",))) is None

    rows = _unresolved_rows(adapter)
    assert len(rows) == 2
    assert {row["reason"] for row in rows} == {"no_cochange_partners"}
    assert adapter._cochange_history_dead is False


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
    adapter.issue_text = "compute"
    lines = [f"source{i}.py:1 score=1 reasons=content_token:compute" for i in range(50)]
    candidate = "[GT_EVIDENCE:localization]\n" + "\n".join(lines)
    # The localization recipe resolves through _prepare_task_start_localization
    # at admission; compact_localization still owns the byte bound.
    monkeypatch.setattr(
        adapter, "_prepare_task_start_localization", lambda *_: candidate
    )
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    rendered = session.before_model([], iteration=0).context_additions[0]
    assert len(rendered.encode()) <= 1400
    # Oversized units ship a bounded head/tail view inline; the complete
    # bytes stay audit-side in the CAS. The model never gets a retrieval
    # command - a printed pointer is a fetch chore (run 34656860834).
    assert "[GT_CONTEXT_UNIT]" in rendered
    assert "[GT_CONTEXT_UNIT_REFERENCE]" not in rendered
    assert "gt-evidence read" not in rendered
    assert "elided]" in rendered
    assert "[GT_EVIDENCE:localization]" in rendered
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
    monkeypatch.setattr(
        adapter, "_lexical_task_localization", lambda *_: calls.append(1) or ""
    )
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    session.before_model([], iteration=0)
    session.before_model([], iteration=1)
    assert calls == [1]


def test_search_drift_refires_shipped_localization_with_drift_terms(
    tmp_path, monkeypatch
):
    adapter = adapter_for(tmp_path)
    adapter.issue_text = "compute"
    queries = []

    def fake_prepare(query):
        queries.append(query)
        hit = "src/batch.py:7" if "compute_batch" in query else "src/mod.py:1"
        return f"[GT_EVIDENCE:localization]\n{hit} score=1"

    monkeypatch.setattr(adapter, "_prepare_task_start_localization", fake_prepare)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)

    batch = session.before_model([], iteration=0)
    assert queries == ["compute"]
    adapter.bind_provider_payload(
        {"messages": [{"role": "user", "content": "\n".join(batch.context_additions)}]}
    )
    ids = tuple(
        hashlib.sha256(item.encode()).hexdigest()
        for item in batch.context_additions
    )
    session.provider_request_admitted(ids)
    assert session._task_start_shipped

    # Unchanged revision and no drift: a shipped localization stays shipped.
    session.before_model([], iteration=1)
    assert len(queries) == 1

    adapter.note_search_drift("grep -rn compute_batch src/")
    batch = session.before_model([], iteration=2)
    assert len(queries) == 2
    assert "compute_batch" in queries[1]
    assert batch.context_additions


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


def test_localization_delivery_dedups_by_content_per_episode(tmp_path):
    """Ranked localization is refused when the same content re-fires, but new
    ranked content may re-deliver (capped) because the agent's own searches
    can move the information need. One localization per DECISION still holds
    so two never queue in parallel."""
    adapter = adapter_for(tmp_path)

    def offer(iteration, text, key):
        return adapter.admit_model_visible_delivery(
            lane="sealed", kind="localization", rendered=text,
            action_index=iteration, iteration=iteration, dedup_key=key,
        )

    assert offer(0, "ranked rows v1", "loc-a")
    # A second localization inside the same decision is refused before it
    # can queue as a parallel pending delivery.
    assert not offer(0, "ranked rows v2", "loc-b")
    adapter.bind_provider_payload({
        "messages": [{"role": "tool", "content": "ranked rows v1"}]
    })
    # The identical bytes re-firing on a later decision is refused.
    assert not offer(1, "ranked rows v1", "loc-a")
    # New ranked content is a new delivery, not a re-fire.
    assert offer(1, "ranked rows v2", "loc-b")

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len([
        row for row in rows
        if row.get("event") == "evidence_delivery"
        and row.get("evidence_type") == "localization"
    ]) == 1
    assert len([
        row for row in rows
        if row.get("event") == "delivery_refused"
        and row.get("reason") == "localization_fire_once"
    ]) == 2


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


def test_cochange_ceiling_is_reachable_within_one_decision(tmp_path):
    """The ceiling is NOT dead, and this pins why it is not.

    I argued on HAR-87 that the per-decision reset made cochange_task_ceiling
    unreachable, on the assumption that a decision contains exactly one bind
    and so the committed count is always zero during admissions. That
    assumption is wrong: a decision can bind more than once, the count is
    cleared at the DECISION boundary rather than at bind, and two committed
    partners inside one decision close further cochange within it.

    Without this test the ceiling looks dead to anyone who repeats my
    reasoning, and the next reader deletes it.
    """
    adapter = adapter_for(tmp_path)

    def offer(iteration, text):
        return adapter.admit_model_visible_delivery(
            lane="sealed", kind="cochange_partner", rendered=text,
            action_index=iteration, iteration=iteration, dedup_key=text,
        )

    assert offer(0, "p1")
    assert offer(0, "p2")
    adapter.bind_provider_payload({"messages": [{"role": "tool", "content": "p1 p2"}]})
    assert adapter._cochange_delivery_count == 2

    # Same decision, and now the ceiling binds.
    assert not offer(0, "p3")
    assert _refusals(adapter, "cochange_task_ceiling")

    # A new decision clears it, which is the ruled behaviour.
    assert offer(1, "p4")


def test_superseded_context_units_collapse_to_pointers(tmp_path):
    from gt_engine.miniswe_runtime import collapse_superseded_context_units

    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)

    def ship(candidate):
        batch = session.admit_decision_packet([candidate], iteration=0,
                                            action_index=0)
        session.provider_request_admitted(tuple(
            hashlib.sha256(item.encode()).hexdigest()
            for item in batch.context_additions
        ))
        return batch

    first = ship(GTDecisionCandidate(
        rendered="contract v1 obligations body", kind="context_delta",
        dedup_key="prompt:v1", lane="prompt",
        unit_id="unit-1", supersession_key="obligations:task",
    ))
    assert first.context_additions
    messages = [
        {"role": "assistant", "content": "I noted contract v1 obligations body"},
        {"role": "user", "content": "turn\n\n" + first.context_additions[0]},
    ]

    second = ship(GTDecisionCandidate(
        rendered="contract v2 obligations body", kind="context_delta",
        dedup_key="prompt:v2", lane="prompt",
        unit_id="unit-2", supersession_key="obligations:task",
        supersedes=("unit-1",),
    ))
    assert second.context_additions

    collapse_superseded_context_units(session, adapter, messages)
    agent_text, history_text = messages[0]["content"], messages[1]["content"]
    # Agent-authored text that merely mentions the same words is untouched.
    assert "contract v1 obligations body" in agent_text
    # The injected GT block is replaced by a tagged pointer.
    assert "contract v1 obligations body" not in history_text
    assert '[GT_CONTEXT_UNIT] {' in history_text
    pointer = json.loads(history_text.split("[GT_CONTEXT_UNIT] ", 1)[1])
    assert pointer["unit_id"] == "unit-1"
    assert pointer["superseded"] is True
    assert pointer["superseded_by"] == "unit-2"
    rows = [json.loads(line)
            for line in adapter.store.path.read_text().splitlines()]
    assert any(row["event"] == "context_unit_collapsed"
               and row["unit_id"] == "unit-1" for row in rows)
    # The drain is one-shot: a second collapse is a no-op.
    collapse_superseded_context_units(session, adapter, messages)
    assert history_text == messages[1]["content"]


def _ship_context_unit(session, candidate, *, iteration):
    """Admit one candidate, then commit it as a delivered provider request.

    A context unit's delivery identity is the sha256 of its exact wire bytes -
    the same identity provider_request_admitted commits on transport return.
    """
    batch = session.admit_decision_packet(
        [candidate], iteration=iteration, action_index=iteration
    )
    session.provider_request_admitted(tuple(
        hashlib.sha256(item.encode()).hexdigest()
        for item in batch.context_additions
    ))
    return batch


def test_overbudget_live_units_collapse_oldest_first(tmp_path, monkeypatch):
    """The history-axis bound: live unit bytes are capped across lanes, not
    just per lane. The oldest-admitted units demote to pointers until the
    survivors fit; the newest stays verbatim."""
    from gt_engine.miniswe_runtime import collapse_superseded_context_units

    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    batches = [
        _ship_context_unit(session, GTDecisionCandidate(
            rendered=f"claim body {index} " + "x" * 300,
            kind="syntax_result", dedup_key=f"key-{index}",
            unit_id=f"unit-{index}", supersession_key=f"check:lane_{index}",
        ), iteration=index)
        for index in range(3)
    ]
    additions = [batch.context_additions[0] for batch in batches]
    # Exactly the newest unit's worth of bytes may stay live.
    monkeypatch.setenv(
        "GT_HISTORY_CONTEXT_UNIT_LIVE_BYTES",
        str(len(additions[2].encode("utf-8"))),
    )
    adapter.iteration = 3
    messages = [{"role": "user", "content": "\n\n".join(additions)}]

    collapse_superseded_context_units(session, adapter, messages)

    parts = messages[0]["content"].split("\n\n")
    assert len(parts) == 3
    for part, unit_id in zip(parts[:2], ("unit-0", "unit-1"), strict=True):
        assert part.startswith("[GT_CONTEXT_UNIT] ")
        pointer = json.loads(part.split("[GT_CONTEXT_UNIT] ", 1)[1])
        assert pointer["unit_id"] == unit_id
        assert pointer["superseded"] is True
        assert pointer["superseded_by"] == "history_budget"
        # The pointer still resolves the full bytes through the evidence CAS.
        assert pointer["artifact_sha256"]
    # The newest unit rides verbatim - demotion never reaches it.
    assert parts[2] == additions[2]
    assert set(session._context_unit_rendered) == {"unit-2"}
    rows = [json.loads(line)
            for line in adapter.store.path.read_text().splitlines()]
    collapsed = [row for row in rows if row["event"] == "context_unit_collapsed"]
    assert [row["unit_id"] for row in collapsed] == ["unit-0", "unit-1"]
    assert all(row["reason"] == "history_budget" for row in collapsed)


def test_current_iteration_unit_rides_the_wire_before_budget_collapse(
    tmp_path, monkeypatch
):
    """A unit admitted in the iteration being prepared has not provably
    ridden the wire; the budget may not pull it from under the model."""
    from gt_engine.miniswe_runtime import collapse_superseded_context_units

    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    old = _ship_context_unit(session, GTDecisionCandidate(
        rendered="old claim bytes " + "o" * 200, kind="syntax_result",
        dedup_key="key-old", unit_id="unit-old", supersession_key="check:old",
    ), iteration=0)
    fresh = _ship_context_unit(session, GTDecisionCandidate(
        rendered="fresh claim bytes " + "f" * 200, kind="syntax_result",
        dedup_key="key-fresh", unit_id="unit-fresh",
        supersession_key="check:fresh",
    ), iteration=2)
    additions = [old.context_additions[0], fresh.context_additions[0]]
    # A budget below EITHER unit still cannot reach the current one.
    monkeypatch.setenv("GT_HISTORY_CONTEXT_UNIT_LIVE_BYTES", "1")
    adapter.iteration = 2
    messages = [{"role": "user", "content": "\n\n".join(additions)}]

    collapse_superseded_context_units(session, adapter, messages)

    content = messages[0]["content"]
    assert "old claim bytes" not in content
    assert '"superseded_by":"history_budget"' in content
    assert additions[1] in content
    assert "unit-old" not in session._context_unit_rendered
    assert "unit-fresh" in session._context_unit_rendered


@pytest.mark.parametrize("key", ["obligations:task", "plan_cursor:task"])
def test_contract_and_cursor_lanes_stay_verbatim_under_budget(
    tmp_path, monkeypatch, key
):
    """The current contract and the plan cursor organize the NEXT decision;
    they stay verbatim no matter how hard the budget bites."""
    from gt_engine.miniswe_runtime import collapse_superseded_context_units

    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    protected = _ship_context_unit(session, GTDecisionCandidate(
        rendered="current steering " + "s" * 200, kind="context_delta",
        dedup_key=f"prompt:{key}", lane="prompt", unit_id="unit-protected",
        supersession_key=key,
    ), iteration=0)
    ordinary = _ship_context_unit(session, GTDecisionCandidate(
        rendered="ordinary claim " + "o" * 200, kind="syntax_result",
        dedup_key="key-ord", unit_id="unit-ord", supersession_key="check:ord",
    ), iteration=1)
    additions = [protected.context_additions[0], ordinary.context_additions[0]]
    monkeypatch.setenv("GT_HISTORY_CONTEXT_UNIT_LIVE_BYTES", "1")
    adapter.iteration = 2
    messages = [{"role": "user", "content": "\n\n".join(additions)}]

    collapse_superseded_context_units(session, adapter, messages)

    content = messages[0]["content"]
    # The protected lane is the OLDEST admission here and still survives.
    assert additions[0] in content
    assert "ordinary claim" not in content
    assert set(session._context_unit_rendered) == {"unit-protected"}


def test_units_under_the_history_budget_are_never_touched(
    tmp_path, monkeypatch
):
    """Under the default bound nothing changes; a zero bound disables the
    mechanism outright rather than collapsing everything."""
    from gt_engine.miniswe_runtime import collapse_superseded_context_units

    adapter = adapter_for(tmp_path)
    session = GTSession(GTSessionConfig(task_id="admission"), engine=adapter)
    additions = [
        _ship_context_unit(session, GTDecisionCandidate(
            rendered=f"small claim {index}", kind="syntax_result",
            dedup_key=f"key-{index}", unit_id=f"unit-{index}",
            supersession_key=f"check:lane_{index}",
        ), iteration=index).context_additions[0]
        for index in range(2)
    ]
    adapter.iteration = 3
    before = "history\n\n" + "\n\n".join(additions)
    messages = [{"role": "user", "content": before}]

    collapse_superseded_context_units(session, adapter, messages)

    assert messages[0]["content"] == before
    assert set(session._context_unit_rendered) == {"unit-0", "unit-1"}
    rows = [json.loads(line)
            for line in adapter.store.path.read_text().splitlines()]
    assert not [row for row in rows if row["event"] == "context_unit_collapsed"]

    # GT_HISTORY_CONTEXT_UNIT_LIVE_BYTES=0 means "no bound", not "no bytes".
    monkeypatch.setenv("GT_HISTORY_CONTEXT_UNIT_LIVE_BYTES", "0")
    collapse_superseded_context_units(session, adapter, messages)
    assert messages[0]["content"] == before
    assert set(session._context_unit_rendered) == {"unit-0", "unit-1"}
