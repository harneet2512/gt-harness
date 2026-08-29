from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from groundtruth.runtime.evidence_envelope import HYPOTHESIS, VERIFIED, EvidenceEnvelope

from gt_engine.decision_value import DecisionBoundary, FeatureStage
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter, ProviderModelMismatch
from gt_engine.runtime_observation import capture_workspace
from gt_engine.task_contract import Obligation, TaskContract, extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates


def test_lexical_localization_is_stable_advisory_and_includes_dirty_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "beta.py").write_text("needle = 1\n", encoding="utf-8")
    (repo / "alpha.py").write_text("needle = 2\n", encoding="utf-8")

    outputs = []
    for task_id in ("first", "second"):
        adapter = MiniSweAdapter(
            task_id=task_id,
            state_dir=tmp_path / "state",
            predicates=[],
            repo_root=str(repo),
            issue_text="Find needle behavior",
        )
        outputs.append(adapter.task_start_localization())

    assert outputs[0] == outputs[1]
    assert outputs[0].startswith("[Localized files]")
    assert outputs[0].index("alpha.py:1") < outputs[0].index("beta.py:1")
    assert "score=1 reasons=content_token:needle" in outputs[0]
    blobs = list((tmp_path / "state" / "first" / "localization_advisory").rglob("*"))
    artifact_file = next(path for path in blobs if path.is_file())
    artifact = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact["semantics"] == "advisory"
    assert artifact["coverage"]["complete"] is True
    assert artifact["items"][0]["anchor"] == "alpha.py:1"


def test_lexical_localization_is_quiet_on_no_match(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        issue_text="quasar nebula",
    )
    assert adapter.task_start_localization() == ""


def test_stale_or_unreadable_graph_localization_falls_back_to_lexical(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "target.py").write_text("quasar = True\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        graph_db=str(repo / "missing-graph.db"),
        issue_text="repair quasar",
    )
    rendered = adapter.task_start_localization()
    assert rendered.startswith("[Localized files]")
    assert "target.py:1 score=1 reasons=content_token:quasar" in rendered


def test_existing_stale_graph_is_never_used_for_localization(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "target.py").write_text("quasar = True\n", encoding="utf-8")
    graph = repo / "graph.db"
    graph.write_bytes(b"stale graph sentinel")
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        graph_db=str(graph),
        issue_text="repair quasar",
    )
    adapter.graph_fresh = False

    import gt_engine.miniswe_evidence as evidence

    def reject_stale_graph(*args, **kwargs):
        raise AssertionError("stale graph localization must not execute")

    monkeypatch.setattr(evidence, "run_evidence_pipeline", reject_stale_graph)
    rendered = adapter.task_start_localization()
    assert "target.py:1 score=1 reasons=content_token:quasar" in rendered
    blobs = list((tmp_path / "state" / "task" / "localization_advisory").rglob("*"))
    artifact_file = next(path for path in blobs if path.is_file())
    artifact = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact["omissions"] == ["graph_localization_stale"]


def test_adapter_external_state_and_provider_binding(tmp_path):
    a = MiniSweAdapter(
        task_id="task-1",
        state_dir=tmp_path,
        predicates=[Predicate("syntax", "syntax")],
    )
    a.start_task()
    a.begin_verify()
    a.record_receipt("syntax", "python -m py_compile x.py", 0, "ok", epoch=0,
                     semantic=True)
    a.begin_submit()
    payload = a.bind_provider_payload({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": a.provider_suffix()}],
    })
    assert payload.request_id.startswith("task-1-")
    assert payload.payload_sha256
    assert a.submit_decision() is True
    rows = [json.loads(x) for x in (tmp_path / "task-1" / "events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "provider_delivery" for row in rows)
    assert any(row["event"] == "state" for row in rows)


def test_adapter_rejects_provider_payload_without_messages(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    try:
        a.bind_provider_payload({"model": "deepseek-v4-flash"})
    except ValueError as exc:
        assert "messages" in str(exc)
    else:
        raise AssertionError("missing provider messages was accepted")


def test_verified_envelope_reaches_validated_lifecycle_through_provider_boundary(
    tmp_path,
):
    repo = tmp_path / "repo"
    source = repo / "src" / "owner.py"
    source.parent.mkdir(parents=True)
    source.write_text("def resolve_identity():\n    return 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
    )
    adapter.repository_revision = "repo-r1"
    envelope = EvidenceEnvelope.build(
        producer="fixture",
        fact_id="resolve_identity",
        target="src/owner.py",
        evidence_type="localization",
        payload=("resolve_identity is defined in src/owner.py",),
        provenance=(("src/owner.py", 1),),
        confidence=1.0,
        tier=VERIFIED,
    )

    rendered = adapter.compile_certified_envelope(
        envelope,
        boundary=DecisionBoundary.REPOSITORY_START,
    )
    assert rendered.startswith("[inspection dependencies]")
    delivery = adapter.bind_provider_payload(
        {"messages": [{"role": "user", "content": f"task\n\n{rendered}"}]},
        model_visible_additions=(rendered,),
    )
    adapter.bind_provider_response(
        {"id": "response-1", "model": ""},
        next_actions=({"command": "rg resolve_identity"},),
    )
    adapter.validate_consumed_lifecycles(
        "rg resolve_identity",
        output="execution outcome unavailable",
        returncode=None,
    )
    assert adapter.feature_lifecycle_receipts()[0]["stage"] == FeatureStage.DELIVERED
    assert adapter.feature_lifecycle_receipts()[0]["uptake"] == "ACTION_CONSISTENT"
    adapter.validate_consumed_lifecycles(
        "rg resolve_identity",
        output="def resolve_identity():",
        returncode=0,
    )

    receipts = adapter.feature_lifecycle_receipts()
    assert delivery.suffix == rendered
    assert len(receipts) == 1
    assert receipts[0]["stage"] == FeatureStage.VALIDATED
    assert receipts[0]["model_visible_bytes_hex"] == rendered.encode().hex()
    assert receipts[0]["resulting_agent_action"] == "rg resolve_identity"

    from gt_engine.run_receipt_v2 import RunReceiptFinalizer
    from scripts.miniswe_gt_run import _record_adapter_run_receipts

    finalizer = RunReceiptFinalizer(
        tmp_path / "gt-run-receipt.json",
        task_id="task",
        requested_model="fixture-model",
    )
    _record_adapter_run_receipts(finalizer, adapter)
    run_receipt = finalizer.finalize(
        terminal="fixture_complete",
        infrastructure_classification="NONE",
        trajectory={"messages": []},
    )
    assert run_receipt["deliveries"][0]["model_visible_bytes_hex"] == rendered.encode().hex()
    assert run_receipt["feature_lifecycle_transitions"][0]["stage"] == "VALIDATED"


def test_unverifiable_envelope_abstains_and_is_not_model_visible(tmp_path):
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(tmp_path),
    )
    adapter.repository_revision = "repo-r1"
    envelope = EvidenceEnvelope.build(
        producer="fixture",
        fact_id="guess",
        target="missing.py",
        evidence_type="localization",
        payload=("maybe missing.py",),
        provenance=(("missing.py", 1),),
        confidence=0.5,
    )

    rendered = adapter.compile_certified_envelope(
        envelope,
        boundary=DecisionBoundary.REPOSITORY_START,
    )

    assert rendered == ""
    assert adapter.feature_lifecycle_receipts()[0]["stage"] == FeatureStage.ABSTAINED


def test_exact_current_graph_symbol_upgrades_ranked_candidate_to_edit_owner(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "src" / "owner.py"
    source.parent.mkdir(parents=True)
    source.write_text("def resolve_identity():\n    return 1\n", encoding="utf-8")
    display = repo / "src" / "display.py"
    display.write_text("def display_identity():\n    return 1\n", encoding="utf-8")
    graph_db = tmp_path / "graph.db"
    graph_revision = "b" * 64
    with sqlite3.connect(graph_db) as connection:
        connection.execute("CREATE TABLE project_meta(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO project_meta(key,value) VALUES('post_revision',?)",
            (graph_revision,),
        )
        connection.execute(
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY,label TEXT,name TEXT,"
            "qualified_name TEXT,file_path TEXT,start_line INTEGER,language TEXT)"
        )
        connection.execute(
            "INSERT INTO nodes(label,name,qualified_name,file_path,start_line,language) "
            "VALUES('Function','resolve_identity','resolve_identity','src/owner.py',1,'python')"
        )
        connection.execute(
            "INSERT INTO nodes(label,name,qualified_name,file_path,start_line,language) "
            "VALUES('Function','display_identity','display_identity','src/display.py',1,'python')"
        )
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        graph_db=str(graph_db),
        issue_text=(
            "Update resolve_identity while preserving display formatting behavior."
        ),
    )
    adapter.record_repository_snapshot(
        capture_workspace(repo),
        boundary="repository_start",
    )
    envelope = EvidenceEnvelope.build(
        producer="ranked_localization",
        fact_id="display_identity",
        target="src/display.py",
        evidence_type="localization",
        payload=(
            "src/display.py:1:display_identity",
            "src/owner.py:1:resolve_identity",
        ),
        provenance=(("src/display.py", 1), ("src/owner.py", 1)),
        confidence=0.5,
        tier=HYPOTHESIS,
        graph_revision=graph_revision,
    )

    rendered = adapter.compile_certified_envelope(
        envelope,
        boundary=DecisionBoundary.REPOSITORY_START,
    )
    adapter.bind_provider_payload(
        {"messages": [{"role": "user", "content": rendered}]},
        model_visible_additions=(rendered,),
    )
    adapter.bind_provider_response(
        {"id": "response-1", "model": ""},
        next_actions=({"command": "rg resolve_identity"},),
    )
    adapter.validate_consumed_lifecycles(
        "rg resolve_identity",
        output="src/owner.py:1:def resolve_identity():",
        returncode=0,
    )

    receipt = adapter.feature_lifecycle_receipts()[0]
    claims = receipt["claims"]
    claim = claims[0]
    assert rendered.startswith("[edit owners]")
    assert receipt["feature_id"] == "implementation_owner"
    assert receipt["stage"] == FeatureStage.VALIDATED
    assert len(claims) == 2
    assert claim["claim_id"] == "symbol:python:src/owner.py:resolve_identity"
    assert claim["text"] == "resolve_identity is defined at src/owner.py:1"
    assert claim["symbol_identity"] == "python:src/owner.py:resolve_identity"
    assert claim["relationship"] == "graph_node_identity"


def test_envelope_without_repository_snapshot_abstains_instead_of_crashing(tmp_path):
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(tmp_path),
    )
    envelope = EvidenceEnvelope.build(
        producer="fixture",
        fact_id="guess",
        target="owner.py",
        evidence_type="localization",
        payload=("owner.py may be relevant",),
        provenance=(("owner.py", 1),),
        confidence=0.5,
        tier=VERIFIED,
    )

    assert (
        adapter.compile_certified_envelope(
            envelope,
            boundary=DecisionBoundary.REPOSITORY_START,
        )
        == ""
    )
    receipt = adapter.feature_lifecycle_receipts()[0]
    assert receipt["stage"] == FeatureStage.ABSTAINED
    assert receipt["repository_revision"] == "repository-unavailable"


@pytest.mark.parametrize(
    ("language", "relative", "content", "symbol"),
    (
        ("python", "src/owner.py", "def resolve_identity():\n    return 1\n", "resolve_identity"),
        ("typescript", "src/owner.ts", "export function resolveIdentity() {}\n", "resolveIdentity"),
        ("go", "src/owner.go", "package src\nfunc ResolveIdentity() {}\n", "ResolveIdentity"),
        ("rust", "src/owner.rs", "fn resolve_identity() {}\n", "resolve_identity"),
    ),
)
def test_structured_target_selection_is_the_only_edit_owner_authority(
    tmp_path, language, relative, content, symbol
):
    repo = tmp_path / "repo"
    source = repo / relative
    source.parent.mkdir(parents=True)
    source.write_text(content, encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
    )
    adapter.repository_revision = "repo-r1"
    semantics = SimpleNamespace(
        roles=(SimpleNamespace(value="TARGET_IDENTITY"),),
        decision_context=SimpleNamespace(value="SOURCE_TARGET_SELECTION"),
        authority=5,
        claim=f"{symbol} is the implementation owner",
        actionable_consequence=f"edit {relative}:{symbol}",
    )
    envelope = EvidenceEnvelope.build(
        producer="fixture",
        fact_id=f"{language}:{relative}:{symbol}",
        target=relative,
        evidence_type="localization",
        payload=("legacy payload is not the authority",),
        provenance=((relative, 1),),
        confidence=1.0,
        tier=VERIFIED,
        canonical_semantics=semantics,
    )

    rendered = adapter.compile_certified_envelope(
        envelope,
        boundary=DecisionBoundary.REPOSITORY_START,
    )

    receipt = adapter.feature_lifecycle_receipts()[0]
    assert rendered.startswith("[edit owners]")
    assert receipt["claims"][0]["role"] == "edit_owner"
    assert receipt["claims"][0]["text"] == semantics.claim


def test_adapter_evaluates_semantic_predicates_from_real_observation(tmp_path):
    contract = TaskContract(
        "ARTIFACT",
        (Obligation("obl-1", "Create output.json artifact.", "test"),),
    )
    predicate_id = next(
        iter(compile_obligation_predicates(contract).values())
    ).predicate_id
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate(predicate_id, "output.json exists")],
        contract=contract,
    )
    a.start_task()
    a.begin_verify()
    receipts = a.evaluate_observation(
        "test -f output.json", "output.json exists", returncode=0, action_index=1
    )
    assert receipts == (predicate_id,)
    assert a.predicate_status(predicate_id).value == "GREEN"


def test_provider_suffix_is_stable_until_state_changes(tmp_path):
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate("p", "predicate")],
    )
    a.start_task()
    first = a.provider_suffix()
    second = a.provider_suffix()
    assert first == second
    a.note_edit(["x.py"])
    assert a.provider_suffix() != first


def test_provider_control_delta_is_empty_when_state_is_unchanged(tmp_path):
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate("p", "predicate")],
    )
    a.start_task()
    assert a.next_provider_suffix()
    assert a.next_provider_suffix() == ""
    a.note_edit(["x.py"])
    assert a.next_provider_suffix()


def test_contract_shipped_once_then_delta_only_on_state_change(tmp_path):
    contract = extract_task_contract(
        "Fix compute() to return 0.0 on empty lists. Add a health endpoint to server.py."
    )
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate(pid, text) for pid, text in _predicates(contract)],
        contract=contract,
    )
    a.start_task()
    full = a.next_contract_delta()
    assert full.startswith("Requirements to satisfy")
    assert "compute" in full
    assert a.next_contract_delta() == ""  # unchanged state -> no re-dose
    # a failing check on a matching obligation changes state -> delta reappears
    for obligation in contract.obligations:
        for item in a._compiled_predicates.values():
            if item.obligation_id == obligation.obligation_id:
                a.record_receipt(item.predicate_id, "pytest", 1, "1 failed",
                                 epoch=0, status="RED", semantic=True)
    delta = a.next_contract_delta()
    assert delta  # obligation state changed -> a delta is owed
    assert "GT retained" not in delta  # delta names remaining obligations


def _predicates(contract):
    from gt_engine.verification_contract import compile_obligation_predicates

    compiled = compile_obligation_predicates(contract)
    return [
        (compiled[obligation.obligation_id].predicate_id, obligation.text)
        for obligation in contract.obligations
    ]


def test_failing_executable_check_marks_predicate_red(tmp_path):
    from gt_engine.verification_contract import compile_obligation_predicates

    contract = extract_task_contract(
        "compute() must pass the pytest suite."
    )
    compiled = compile_obligation_predicates(contract)
    predicate_id = next(iter(compiled.values())).predicate_id
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate(predicate_id, "compute passes the pytest suite")],
        contract=contract,
    )
    a.start_task()
    a.begin_verify()
    red = a.evaluate_failing_observation(
        "python -m pytest tests/test_compute.py -q",
        "tests/test_compute.py::test_compute FAILED - compute([]) raised ZeroDivisionError",
        returncode=1, action_index=1,
    )
    assert predicate_id in red
    assert a.predicate_status(predicate_id).value == "RED"


def test_response_binding_records_usage_and_marks_terminal(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    payload = a.bind_provider_payload({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "task"}],
    })
    assert not a.terminal_confirmed(payload.request_id)
    a.bind_provider_response(
        {"model": "deepseek-v4-flash", "choices": []},
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    assert a.terminal_confirmed(payload.request_id)
    rows = [json.loads(x) for x in (tmp_path / "task" / "events.jsonl").read_text().splitlines()]
    response_rows = [row for row in rows if row["event"] == "provider_response"]
    assert response_rows and response_rows[-1]["usage"]["prompt_tokens"] == 10


def test_provider_request_commits_exact_logical_payload_and_model_identity(tmp_path):
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=[],
        requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    a.start_task()
    payload = {
        "model": "openai/deepseek-v4-flash",
        "model_kwargs": {"temperature": 1.0, "api_base": "https://gateway.invalid"},
        "tools": [{"type": "function", "function": {"name": "bash"}}],
        "messages": [{"role": "user", "content": "exact final bytes"}],
    }
    delivery = a.bind_provider_payload(payload)
    blob = a.store.root / "provider_requests" / f"{delivery.payload_sha256}.json"
    assert blob.is_file()
    assert delivery.model_visible_sha256
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    event = [row for row in rows if row["event"] == "provider_delivery"][-1]
    assert event["requested_model"] == "deepseek-v4-flash"
    assert event["resolved_model"] == "openai/deepseek-v4-flash"
    assert event["model_visible_sha256"] == delivery.model_visible_sha256


def test_unexpected_provider_model_mismatch_is_recorded_and_raises(tmp_path):
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=[],
        requested_model="deepseek-v4-flash",
        resolved_model="openai/deepseek-v4-flash",
    )
    a.start_task()
    delivery = a.bind_provider_payload({
        "model": "openai/deepseek-v4-flash",
        "messages": [{"role": "user", "content": "task"}],
    })
    try:
        a.bind_provider_response({"model": "fallback-model", "choices": []})
    except ProviderModelMismatch as exc:
        assert "fallback-model" in str(exc)
    else:
        raise AssertionError("unexpected provider model was accepted")
    assert a.terminal_confirmed(delivery.request_id)
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    event = [row for row in rows if row["event"] == "provider_response"][-1]
    assert event["model_mismatch"] is True


def test_provider_failure_has_terminal_receipt(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    delivery = a.bind_provider_payload({
        "messages": [{"role": "user", "content": "task"}],
    })
    a.bind_provider_failure(TimeoutError("provider deadline"))
    assert a.terminal_confirmed(delivery.request_id)
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    assert any(row["event"] == "provider_failure" for row in rows)


def test_recovery_steer_scheduled_on_recurring_failure_after_edit(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    assert a.note_failure_fingerprint("fp-1", epoch=0) is False
    a.note_edit(["src/mod.py"])  # epoch -> 1
    assert a.note_failure_fingerprint("fp-1", epoch=1) is True
    assert a.pending_transient
    assert "GT_RECOVERY" in a.pending_transient
    rows = [json.loads(x) for x in (tmp_path / "task" / "events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "recovery_steer" for row in rows)
    # bounded: at most 2 recovery steers per task
    a.pending_transient = ""
    a.note_edit(["src/mod.py"])
    assert a.note_failure_fingerprint("fp-1", epoch=2) is True   # steer #2
    a.pending_transient = ""
    a.note_edit(["src/mod.py"])
    assert a.note_failure_fingerprint("fp-1", epoch=3) is False  # budget exhausted


def test_submit_refused_on_verified_red(tmp_path):
    from gt_engine.miniswe_controller import Predicate

    a = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                       predicates=[Predicate("p", "p")])
    a.start_task()
    a.begin_verify()
    a.record_receipt("p", "pytest", 1, "1 failed", epoch=0,
                     status="RED", semantic=True)
    a.begin_submit()
    assert a.submit_decision() is False
    assert a.phase == "IMPLEMENT"
    # An edit invalidates the old workspace-bound RED to UNKNOWN.  UNKNOWN is
    # nonblocking; it is not silently relabeled GREEN.
    a.note_edit(["src/mod.py"])
    assert a.predicate_status("p").value == "UNKNOWN"
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is True
    assert a.phase == "FINISHED"


def test_refusal_names_obligation_text_not_opaque_predicate_id(tmp_path):
    from gt_engine.miniswe_runtime import _refusal_directive

    contract = extract_task_contract(
        "Fix compute() in src/mod.py so it returns 0.0 for an empty list."
    )
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path,
        predicates=[Predicate(pid, text) for pid, text in _predicates(contract)],
        contract=contract,
    )
    a.start_task()
    directive = _refusal_directive(a)
    assert directive["role"] == "user"
    assert "Submission not executed" in directive["content"]
    assert "may continue" in directive["content"]
    # Deep-audit D2: the refusal must NEVER frame the harness or offer a bypass
    # (round-8: that text sent the model reading gt_engine/ source).
    assert "GT ENFORCED" not in directive["content"]
    assert "harness" not in directive["content"].lower()
    assert "advisory" not in directive["content"].lower()
    assert "bypass" not in directive["content"].lower()
    assert "compute()" in directive["content"]
    assert "pred-" not in directive["content"]


def test_repeated_refusal_never_escalates_to_stuck(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                       predicates=[Predicate("p", "p")])
    a.start_task()
    a.begin_verify()
    a.record_receipt("p", "pytest", 1, "1 failed", epoch=0, status="RED",
                     semantic=True)
    a.begin_submit()
    assert a.submit_decision() is False        # refusal #1 -> IMPLEMENT
    assert a.phase == "IMPLEMENT"
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is False
    assert a.phase == "IMPLEMENT"
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is False
    assert a.phase == "IMPLEMENT"


def test_task_mode_compiles_service_and_build_predicates():
    from gt_engine.task_contract import Obligation, TaskContract, TaskMode
    from gt_engine.verification_contract import compile_obligation_predicates

    service = TaskContract(
        role="code_behavior",
        obligations=(Obligation("obl-s", "The server must expose a /health endpoint.", "t"),),
        task_mode=TaskMode.SERVICE,
    )
    compiled = compile_obligation_predicates(service)
    assert compiled["obl-s"].kind == "service_probe"

    build = TaskContract(
        role="code_behavior",
        obligations=(Obligation("obl-b", "The package must build via make.", "t"),),
        task_mode=TaskMode.BUILD_INSTALL,
    )
    compiled = compile_obligation_predicates(build)
    assert compiled["obl-b"].kind == "build_install"


def test_final_state_reports_verified_only_when_all_obligations_green(tmp_path):

    a = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                       predicates=[Predicate("p", "p")])
    a.start_task()
    a.begin_verify()
    a.record_receipt("p", "pytest", 0, "1 passed", epoch=0, semantic=True)
    a.begin_submit()
    assert a.submit_decision() is True
    state = a.final_state()
    assert state["verified"] is True
    assert state["unverified_predicates"] == []


def test_final_state_reports_unverified_when_any_obligation_unknown(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                       predicates=[Predicate("p", "p")])
    a.start_task()
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is True
    state = a.final_state()
    # T2.2: UNKNOWN obligations must NOT be reported as verified.
    assert state["verified"] is False
    assert state["unverified_predicates"] == ["p"]


def test_edit_invalidates_stale_red_without_fabricating_green(tmp_path):
    """An early RED becomes UNKNOWN after edit; unrelated PASS is not GREEN."""
    contract = TaskContract(
        "code_behavior",
        (Obligation("obl-1", "The terminal must support Ctrl-C handling.", "t"),),
    )
    compiled = compile_obligation_predicates(contract)
    predicate_id = compiled["obl-1"].predicate_id
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path,
        predicates=[Predicate(predicate_id, "Ctrl-C handling")],
        contract=contract,
    )
    a.start_task()
    a.begin_verify()
    # early failing run -> RED (the real gton13 path: python3 test.py rc=1)
    a.record_receipt(
        predicate_id, "cd /app && PYTHONPATH=/app python3 /tmp/test_terminal.py",
        1, "Traceback: AttributeError", epoch=a.workspace_epoch,
        status="RED", semantic=True,
    )
    assert a.predicate_status(predicate_id).value == "RED"
    a.begin_implement()
    a.note_edit(["headless_terminal.py"])
    assert a.predicate_status(predicate_id).value == "UNKNOWN"
    a.begin_verify()
    a.evaluate_observation(
        "cd /tmp && python3 /tmp/test_full.py 2>&1 | tail -6",
        "PASS: Ctrl-C handling works\nAll 13 tests passed",
        returncode=0, action_index=2,
    )
    assert a.predicate_status(predicate_id).value == "UNKNOWN"


def test_generic_passing_test_file_does_not_clear_unrelated_behavior_reds(tmp_path):
    contract = TaskContract(
        "code_behavior",
        (
            Obligation("obl-1", "The terminal must support Ctrl-C handling.", "t"),
            Obligation("obl-2", "The terminal must source bash startup files.", "t"),
        ),
    )
    compiled = compile_obligation_predicates(contract)
    predicates = [
        Predicate(compiled[key].predicate_id, key) for key in ("obl-1", "obl-2")
    ]
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=predicates, contract=contract
    )
    a.start_task()
    a.begin_verify()
    for predicate in predicates:
        a.record_receipt(
            predicate.predicate_id,
            "python3 /tmp/test_terminal.py",
            1,
            "FAILED",
            epoch=0,
            status="RED",
            semantic=True,
        )
    a.evaluate_observation(
        "python3 /tmp/test_unrelated.py",
        "PASS: unrelated formatting works\nAll 1 tests passed",
        returncode=0,
        action_index=2,
    )
    assert [a.predicate_status(p.predicate_id).value for p in predicates] == [
        "RED",
        "RED",
    ]


def test_graph_full_rebuild_fallback_restores_freshness(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )
    a.start_task()
    a.repository_revision = "a" * 64
    a.note_edit(["mod.py"])
    assert a.graph_fresh is False

    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index", lambda root, state_dir=None: str(rebuilt)
    )
    assert a.refresh_graph() is True
    assert a.graph_fresh is True
    assert a.graph_db == str(rebuilt)


def test_graph_rebuild_failure_keeps_graph_stale(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(tmp_path / "graph.db"),
    )
    a.start_task()
    a.note_edit(["mod.py"])
    monkeypatch.setattr("gt_engine.indexer.ensure_index", lambda *a, **k: None)
    assert a.refresh_graph() is False
    assert a.graph_fresh is False


def test_provider_receipt_binds_exact_response_and_immediate_next_action(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    delivery = a.bind_provider_payload({
        "model": "m", "messages": [{"role": "user", "content": "go"}],
    })
    response = {"id": "resp-1", "model": "m", "choices": [{"index": 0}]}
    action = {"tool_name": "bash", "command": "rg needle .", "tool_call_id": "c1"}
    a.bind_provider_response(response, model="m", next_actions=(action,))

    rows = [
        __import__("json").loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
    ]
    row = next(item for item in rows if item["event"] == "provider_response")
    encoded = __import__("json").dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert row["request_id"] == delivery.request_id
    assert row["response_sha256"] == __import__("hashlib").sha256(encoded).hexdigest()
    assert (a.store.root / row["response_blob"]).read_bytes() == encoded
    assert row["provider_response_id"] == "resp-1"
    assert row["immediate_next_actions"][0]["tool_name"] == "bash"
