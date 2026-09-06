from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter, ProviderModelMismatch
from gt_engine.request_history import load_provider_request
from gt_engine.run_diagnostics import DiagnosticCode
from gt_engine.runtime_observation import capture_workspace
from gt_engine.task_contract import Obligation, TaskContract, extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates


def test_verification_candidate_is_bound_to_pre_edit_graph(tmp_path, monkeypatch):
    graph = tmp_path / "graph.db"
    with sqlite3.connect(graph) as db:
        db.execute("CREATE TABLE resolution_symbols (stable_id TEXT, path TEXT)")
        db.execute(
            "INSERT INTO resolution_symbols VALUES (?, ?)",
            ("symbol:parser", "src/parser.py"),
        )
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="verify-plan",
        state_dir=tmp_path / "state",
        predicates=[],
        contract=extract_task_contract("Fix the parser."),
        repo_root=repo,
        graph_db=str(graph),
    )
    adapter.engine_state.bind_initial_source("before")
    snapshot = adapter.graph_query_snapshot()

    class Check:
        kind = "unit"
        command = ("pytest", "tests/test_parser.py")
        selection_basis = "fact_covering"
        covered_entities = ("symbol:parser",)
        covered_obligations = ()
        expected_cost = "low"
        confidence = "high"
        attribution_requirement = "edit_attributed"
        targets = ("tests/test_parser.py",)
        reason = ""

    class Plan:
        checks = (Check(),)

        def canonical_json(self):
            return '{"checks":[]}'

    captured = {}

    def build(graph_db, repo_root, entities, obligations, **revisions):
        captured.update(
            graph_db=graph_db,
            repo_root=repo_root,
            entities=tuple(entities),
            obligations=tuple(obligations),
            revisions=revisions,
        )
        return Plan()

    import groundtruth.runtime.verification_plan as verification_plan

    monkeypatch.setattr(verification_plan, "build_verification_plan", build)
    transaction = type(
        "Transaction",
        (),
        {
            "transaction_sha256": "tx-1",
            "post_revision": "after",
            "changed_paths": ("src/parser.py",),
        },
    )()

    rendered = adapter.prepare_verification_candidate(transaction, snapshot)

    assert captured == {
        "graph_db": str(graph),
        "repo_root": str(repo),
        "entities": ("symbol:parser",),
        "obligations": (),
        "revisions": {
            "patch_revision": "after",
            "graph_revision": snapshot.graph_revision,
        },
    }
    assert "pytest tests/test_parser.py" in rendered
    assert adapter.consume_verification_candidate()[0] == rendered


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
    assert outputs[0].startswith("[GT_EVIDENCE:localization]")
    assert outputs[0].index("alpha.py:1") < outputs[0].index("beta.py:1")
    assert "score=1 reasons=content_token:needle" in outputs[0]
    blobs = list((tmp_path / "state" / "first" / "localization_advisory").rglob("*"))
    artifact_file = next(path for path in blobs if path.is_file())
    artifact = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact["semantics"] == "advisory"
    assert artifact["coverage"]["complete"] is True
    assert artifact["items"][0]["anchor"] == "alpha.py:1"

    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "first" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    delivery = next(row for row in rows if row["event"] == "delivery_prepared")
    shipped = outputs[0]
    assert delivery["rendered_bytes"] == len(shipped.encode("utf-8"))
    assert delivery["payload_sha256"] == __import__("hashlib").sha256(
        shipped.encode("utf-8")
    ).hexdigest()
    assert not any(row["event"] in {"evidence_delivery", "receipt"} for row in rows)


def test_task_start_uses_independent_dense_graph_retrieval(
    tmp_path, monkeypatch
):
    from gt_engine.retrieval import RankedSymbol, RetrievalSource

    graph = tmp_path / "graph.db"
    graph.write_bytes(b"fixture")
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(model_dir))
    stable_id = "s" * 64
    dense = SimpleNamespace(
        source=RetrievalSource.DENSE,
        available=True,
        ranking=(RankedSymbol(stable_id, 1.0, "semantic"),),
        reason=None,
        detail={"execution_receipt": {"schema": "gt.dense_index_receipt.v1",
                                      "query_ready": True, "index_sha256": "a" * 64}},
    )
    ranking = SimpleNamespace(
        sources=(dense,),
        fused=(RankedSymbol(stable_id, 0.5, "semantic"),),
        provenance={stable_id: SimpleNamespace(
            file_path="src/semantic.py", start_line=17
        )},
        contributing_sources=lambda _stable_id: ("dense",),
        attribution_record=lambda: {
            "schema": "gt.hybrid_retrieval.v1",
            "promotes_trust": False,
        },
    )
    monkeypatch.setattr("gt_engine.retrieval.hybrid_rank", lambda *a, **k: ranking)
    adapter = MiniSweAdapter(
        task_id="semantic",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(tmp_path),
        graph_db=str(graph),
        issue_text="behavior phrased without an identifier",
    )

    rendered = adapter.task_start_localization(commit=False)

    assert "src/semantic.py:17" in rendered
    assert "retrieval:dense" in rendered
    assert adapter.localization_delivery_metadata()["dedup_key"].startswith(
        "semantic-localization:"
    )
    dense_rows = [row for row in map(json.loads, (adapter.store.root / "events.jsonl").read_text().splitlines())
                  if row["event"] == "dense_index_ready"]
    assert len(dense_rows) == 1
    assert dense_rows[0]["index_sha256"] == "a" * 64


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
    assert rendered.startswith("[GT_EVIDENCE:localization]")
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


def test_native_delivery_identity_is_joined_to_immediate_request_and_response(
    tmp_path,
):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    assert a.admit_model_visible_delivery(
        lane="sealed",
        kind="cochange_partner",
        rendered="inspect sibling.py",
        action_index=1,
        iteration=1,
        dedup_key="cochange:sibling.py",
        target="sibling.py",
    )
    identity = hashlib.sha256(b"inspect sibling.py").hexdigest()

    request = a.bind_provider_payload({
        "model": "m",
        "messages": [{"role": "tool", "content": "inspect sibling.py"}],
    })
    a.bind_provider_response({"model": "m", "choices": []})

    assert request.delivery_ids == (identity,)
    rows = [json.loads(line) for line in a.store.path.read_text().splitlines()]
    provider = next(row for row in rows if row["event"] == "provider_delivery")
    response = next(row for row in rows if row["event"] == "provider_response")
    assert provider["delivery_ids"] == [identity]
    assert provider["matches"] == [{
        "delivery_id": identity,
        "rendered_sha256": identity,
    }]
    assert response["delivery_ids"] == [identity]


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
    assert delivery.model_visible_sha256
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    event = [row for row in rows if row["event"] == "provider_delivery"][-1]
    assert load_provider_request(a.store.root, event) == payload
    assert event["request_storage"] == "message_cas"
    assert len(tuple((a.store.root / "provider_messages").glob("*.json"))) == 1
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
    assert any(row["event"] == "recovery_prepared" for row in rows)
    assert not any(row["event"] == "recovery_steer" for row in rows)
    # bounded: at most 2 recovery steers per task
    rendered = a.prepare_recovery_delivery()
    a.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    a.note_edit(["src/mod.py"])
    assert a.note_failure_fingerprint("fp-1", epoch=2) is True   # steer #2
    rendered = a.prepare_recovery_delivery()
    a.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    a.note_edit(["src/mod.py"])
    assert a.note_failure_fingerprint("fp-1", epoch=3) is False  # budget exhausted


def test_same_epoch_repetition_is_not_post_edit_falsification(tmp_path):
    adapter = MiniSweAdapter(task_id="repeat", state_dir=tmp_path, predicates=[])
    assert not adapter.note_failure_fingerprint("fp", epoch=0)
    assert not adapter.note_failure_fingerprint("fp", epoch=0)
    assert not adapter.pending_transient
    assert adapter.note_failure_fingerprint("fp", epoch=1)
    rendered = adapter.prepare_recovery_delivery()
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    assert not adapter.note_failure_fingerprint("fp", epoch=1)
    assert not adapter.pending_transient


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
    # An edit invalidates the old workspace-bound RED to UNKNOWN. UNKNOWN is
    # not silently relabeled GREEN and cannot support verified completion.
    a.note_edit(["src/mod.py"])
    assert a.predicate_status("p").value == "UNKNOWN"
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is True
    assert a.phase == "FINISHED"
    assert a.final_state()["verified"] is False


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
    assert "GT ENFORCED SUBMIT GATE" in directive["content"]
    assert "may continue" in directive["content"]
    assert "compute()" in directive["content"]
    assert "pred-" not in directive["content"]


def test_first_refusal_allows_one_corrective_then_honest_unverified_submit(tmp_path):
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
    assert a.submit_decision() is True
    assert a.phase == "FINISHED"
    assert a.final_state()["verified"] is False


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


def test_unknown_obligation_refuses_completion_without_verified_claim(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                       predicates=[Predicate("p", "p")])
    a.start_task()
    a.begin_verify()
    a.begin_submit()
    assert a.submit_decision() is False
    state = a.final_state()
    assert state["phase"] == "IMPLEMENT"
    assert state.get("verified", False) is False


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
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )
    a.start_task()
    assert a.gateway_state().graph_db == str(graph)
    snapshot = capture_workspace(repo)
    a.record_repository_snapshot(snapshot, boundary="after_action")
    a.note_edit(["mod.py"])
    assert a.graph_fresh is False
    assert a.gateway_state().graph_db is None

    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, state_dir=None, source_revision="", layout=None: IndexBuildReceipt(
            IndexBuildStatus.BUILT,
            graph_db=str(rebuilt),
            graph_revision="b" * 64,
            analysis_state="complete",
        ),
    )
    assert a.refresh_graph() is False
    assert a._graph_coordinator.wait_idle(timeout=3)
    assert a.refresh_graph() is True
    assert a.graph_fresh is True
    assert a.graph_db == str(rebuilt)
    assert a.gateway_state().graph_db == str(rebuilt)


@pytest.mark.parametrize("config_name", ["tsconfig.json", "package.json", "go.mod", "Cargo.toml", ".gitignore"])
def test_frozen_input_and_reuse_key_include_resolver_configuration(tmp_path, config_name):
    from gt_engine.indexer import source_manifest_digest
    from gt_engine.runtime_observation import capture_workspace
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "a.ts").write_text("export const a = 1\n", encoding="utf-8")
    config = repository / config_name
    config.write_text("before", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="configuration", state_dir=tmp_path / "state",
                             repo_root=str(repository), predicates=[])
    before = source_manifest_digest(repository)
    frozen = adapter._frozen_graph_input(capture_workspace(repository))
    assert (config_name, b"before") in frozen.files
    config.write_text("after", encoding="utf-8")
    assert source_manifest_digest(repository) != before


def test_frozen_graph_input_ignores_only_known_non_source_omissions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = capture_workspace(repo)
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
    )

    unrelated = replace(
        snapshot,
        complete=False,
        omissions=("unreadable:asset.bin",),
    )
    request = adapter._frozen_graph_input(unrelated)
    assert request.files == (("mod.py", (repo / "mod.py").read_bytes()),)

    source_missing = replace(
        snapshot,
        complete=False,
        omissions=("unreadable:other.py",),
    )
    with pytest.raises(ValueError, match="frozen_source_incomplete"):
        adapter._frozen_graph_input(source_missing)


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


def test_submit_refreshes_stale_graph_and_refuses_when_refresh_fails(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        graph_db=str(graph),
    )
    a.start_task()
    a.note_edit(["mod.py"])
    monkeypatch.setattr("gt_engine.indexer.ensure_index", lambda *a, **k: None)
    a.begin_verify()
    a.begin_submit()

    assert a.submit_decision() is False
    assert a.phase == "IMPLEMENT"
    assert a.graph_fresh is False
    assert any(
        event.code is DiagnosticCode.GT_GRAPH_REFRESH_FAILED
        and event.phase == "submit"
        for event in a.diagnostics._events
    )


def test_advisory_submit_does_not_rebuild_stale_graph(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    a = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        graph_db=str(tmp_path / "graph.db"),
    )
    a.start_task()
    a.note_edit(["mod.py"])
    phases = []

    def refresh_graph(*, phase="graph_query"):
        phases.append(phase)
        a.graph_fresh = True
        return True

    monkeypatch.setattr(a, "refresh_graph", refresh_graph)
    a.begin_verify()
    a.begin_submit()

    assert a.advisory_submit_decision() is True
    assert phases == []
    assert a.graph_fresh is False
    rows = [
        json.loads(line)
        for line in a.store.path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row["event"] == "graph_refresh_deferred"
        and row.get("phase") == "submit_advisory"
        and row.get("reason") == "advisory_submit_cannot_consume_refresh"
        for row in rows
    )


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
