from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter, ProviderModelMismatch
from gt_engine.request_history import load_provider_request
from gt_engine.run_diagnostics import DiagnosticCode
from gt_engine.runtime_observation import capture_workspace, diff_workspace
from gt_engine.task_contract import Obligation, TaskContract, extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates


def test_verification_recipe_renders_against_the_graph_at_admission(
    tmp_path, monkeypatch
):
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

    # Transaction time now registers the query only - the bytes do not exist
    # until the admission choke point renders them against the current graph.
    assert adapter.prepare_verification_candidate(transaction, snapshot) == ""
    recipe = adapter._pending_verification_recipe
    assert recipe["kind"] == "verification_plan"
    assert recipe["params"]["paths"] == ["src/parser.py"]
    assert captured == {}

    resolved = adapter.resolve_delivery_recipe(recipe)

    assert resolved is not None
    rendered, _render_revision, _metadata, _reference = resolved
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
    # Payload render: the matched line content and matched terms, not an
    # opaque score - the smoke-20 finding was that pointer-only localization
    # was ignored by the model (7/19 deliveries consumed).
    assert "alpha.py:1 ~ needle = 2 | matched needle" in outputs[0]
    assert "score=" not in outputs[0]
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
            file_path="src/semantic.py", start_line=17,
            qualified_name="semantic.handle", name="handle", label="Function",
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
    assert "semantic.handle" in rendered
    assert "Function" in rendered
    assert "semantic match" in rendered
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
    assert "target.py:1 ~ quasar = True | matched quasar" in rendered


def test_lexical_hybrid_rerank_runs_while_the_graph_is_stale(tmp_path, monkeypatch):
    """graph_snapshot_not_current was a precondition refusal, not a dense run.

    Run 35178222629's journal: mid-task amend refusals left the adopted
    graph stale, so every later re-localization journaled
    dense_index_ready query_ready=false reason=graph_snapshot_not_current
    -- and last-row-wins at the product receipt read it as
    treatment_dense_index_not_ready on a task the verifier passed. The
    re-rank embeds the lexical candidates' own texts; it does not need
    the graph, so it still runs and its receipt measures the index it
    actually touched, stamped with the revision the engine last held.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "target.py").write_text(
        "def frobnicate():\n    return 1\n", encoding="utf-8"
    )
    monkeypatch.setenv("GT_RETRIEVAL_MODE", "hybrid_required")
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "model"))
    adapter = MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=str(repo),
        issue_text="repair frobnicate",
    )
    adapter.engine_state.graph_path = str(repo / "graph.db")
    adapter.engine_state.graph_revision = "stale-revision-abc"
    adapter.engine_state.mark_graph_failed()

    captured: dict = {}

    def fake_rank_documents(**kwargs):
        captured.update(kwargs)
        return (["target.py"], {
            "schema": "gt.dense_index_receipt.v1",
            "query_ready": True,
            "graph_revision": kwargs["graph_revision"],
            "reason": None,
        })

    monkeypatch.setattr(
        "gt_engine.dense_runtime.rank_documents", fake_rank_documents)

    rendered = adapter._lexical_task_localization("repair frobnicate")

    assert "target.py" in rendered
    assert captured["graph_revision"] == "stale-revision-abc"
    rows = [
        json.loads(line)
        for line in (adapter.store.root / "events.jsonl").read_text().splitlines()
    ]
    dense = [row for row in rows if row.get("event") == "dense_index_ready"]
    assert len(dense) == 1
    assert dense[0]["query_ready"] is True
    assert "graph_snapshot_not_current" not in str(dense)


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
    assert "target.py:1 ~ quasar = True | matched quasar" in rendered
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
    (tmp_path / "output.json").write_text("{}", encoding="utf-8")
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
        repo_root=str(tmp_path),
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


def test_bootstrap_response_binds_its_own_request_not_the_prior_agents(tmp_path):
    """GT-internal calls never bind_provider_payload, so _latest_delivery is
    the previous agent request. Without an explicit request_id the bootstrap
    response borrows that identity: it claims the agent's deliveries and marks
    the wrong request terminal, collapsing the terminal census (smoke-20
    task-7: terminal_requests 17 vs provider_calls 19)."""
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    delivery = a.bind_provider_payload({
        "messages": [{"role": "user", "content": "task"}],
    })
    bootstrap_id = "task-gt-internal-select-catalog"
    a.bind_provider_response(
        {"model": "m", "choices": []},
        usage={"prompt_tokens": 4, "completion_tokens": 1},
        request_id=bootstrap_id,
    )
    assert a.terminal_confirmed(bootstrap_id)
    assert not a.terminal_confirmed(delivery.request_id)
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    response = next(row for row in rows if row["event"] == "provider_response")
    assert response["request_id"] == bootstrap_id
    assert response["delivery_ids"] == []

    a.bind_provider_failure(
        TimeoutError("provider deadline"), request_id=bootstrap_id
    )
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    failure = next(row for row in rows if row["event"] == "provider_failure")
    assert failure["request_id"] == bootstrap_id


def test_provider_failure_journals_the_format_error_reason(tmp_path):
    """F8 (run 34766499875): ``InterruptAgentFlow`` exceptions call
    ``Exception.__init__`` with no args, so ``str(exc)`` is "" and the
    provider_failure row carried an empty error. GT's own format errors keep
    the raw reason in ``gt_error_detail``; foreign ones fall back to their
    message payload."""
    from gt_engine.miniswe_typed_actions import _format_error

    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a.start_task()
    a.bind_provider_failure(
        _format_error("{{ error }}", "Unknown tool 'wrench'."),
        request_id="req-1",
    )
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    failure = next(row for row in rows if row["event"] == "provider_failure")
    assert failure["error_type"] == "FormatError"
    assert "Unknown tool 'wrench'" in failure["error"]

    from minisweagent.exceptions import InterruptAgentFlow

    class Foreign(InterruptAgentFlow):
        pass

    a.bind_provider_failure(
        Foreign({"role": "user", "content": "foreign template body",
                 "extra": {"interrupt_type": "Foreign"}}),
        request_id="req-2",
    )
    rows = [json.loads(x) for x in a.store.path.read_text().splitlines()]
    failure = [row for row in rows if row["event"] == "provider_failure"][-1]
    assert failure["error"] == "foreign template body"


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
    # **_kwargs on purpose. A stub whose signature lags the real one has
    # already cost two red commits here, and the boundary reaches this
    # function through the recovery build as well as directly.
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **_kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT,
            graph_db=str(rebuilt),
            graph_revision="b" * 64,
            analysis_state="complete",
        ),
    )
    # The parent has no manifest, so the boundary amend refuses with a
    # parent-loss reason and the recovery build runs inline -- the graph is
    # current at the end of the same refresh_graph call.
    assert a.refresh_graph() is True
    assert a.graph_fresh is True
    assert a.graph_db == str(rebuilt)
    assert a.gateway_state().graph_db == str(rebuilt)


def test_a_snapshot_delta_is_the_dirty_enumeration_the_overlay_needs(tmp_path):
    """A snapshot advances the source revision without writing overlay
    entries, so without the diff the graph goes stale with an empty dirty
    set and the boundary amend reads it as a stale marking and returns.
    Rehearsal 34750641705: a side-effect file changed between snapshots,
    the revision moved 59bbfbc0 -> b16d33e6, and nothing ever resynced.
    """
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
    a.record_repository_snapshot(capture_workspace(repo), boundary="task_start")
    assert a.graph_fresh is True

    # The side effect lands outside any transaction: only the next witness
    # sees it, and its diff against the previous witness is the enumeration.
    (repo / "side.py").write_text("x = 2\n", encoding="utf-8")
    a.record_repository_snapshot(capture_workspace(repo), boundary="after_action")

    assert a.graph_fresh is False
    assert a.engine_state.query_snapshot().masked_paths == ("side.py",)


def test_identical_tree_with_same_basis_rebadges_instead_of_rebuilding(tmp_path):
    """Only git history moved: the file tree is provably identical to the
    adopted graph's basis tree, so the certified graph carries the new
    revision directly. Rebuilding identical input is pure waste; leaving
    it stale is a lie.
    """
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
    first = capture_workspace(repo)
    a.record_repository_snapshot(first, boundary="task_start")

    moved = replace(first, revision="f" * 40)
    a.record_repository_snapshot(moved, boundary="after_action")

    assert a.graph_fresh is True
    assert a.engine_state.graph_source_revision == "f" * 40
    lines = Path(a.store.path).read_text(encoding="utf-8").splitlines()
    assert any(
        json.loads(line).get("event") == "graph_rebadged" for line in lines
    )


def test_identical_tree_with_unwitnessed_basis_recovers_not_rebadges(
    monkeypatch, tmp_path,
):
    """The same empty diff against a graph whose basis is a tree no witness
    proves identical is an unenumerated advance: the dirty set is
    unknowable, so the only honest resync is the whole-tree recovery build.
    """
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
    first = capture_workspace(repo)
    a.record_repository_snapshot(first, boundary="task_start")

    # A graph adopted on a basis no recorded snapshot witnessed.
    a.engine_state.graph_source_revision = "0" * 40
    moved = replace(first, revision="f" * 40)
    a.record_repository_snapshot(moved, boundary="after_action")

    assert a.graph_fresh is False
    assert "source_advance_unenumerated" in (
        a.engine_state.query_snapshot().omissions
    )

    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **_kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT,
            graph_db=str(rebuilt),
            graph_revision="b" * 64,
            analysis_state="complete",
        ),
    )
    assert a.refresh_graph() is True
    assert a.graph_fresh is True


def _build_mode_rows(adapter) -> list[dict]:
    lines = Path(adapter.store.path).read_text(encoding="utf-8").splitlines()
    return [
        row for line in lines
        if (row := json.loads(line)).get("event")
        in {"graph_sync_amend", "graph_boundary_amend", "graph_recovery"}
    ]


def _edited_adapter(tmp_path, graph_bytes: bytes = b"old"):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(graph_bytes)
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )
    adapter.start_task()
    adapter.record_repository_snapshot(capture_workspace(repo), boundary="after_action")
    adapter.note_edit(["mod.py"])
    return adapter, repo, graph


def test_an_edit_takes_the_amend_path_and_the_journal_says_which(monkeypatch, tmp_path):
    """The row that proves the incremental path ran.

    Without it an amend and a full rebuild are indistinguishable in the
    journal, and the last time that mattered a caller_coverage improvement was
    credited to producer code that had never executed.
    """
    adapter, _repo, graph = _edited_adapter(tmp_path)
    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")

    seen: dict[str, object] = {}

    def fake_amend(root, *, parent_graph, changed_paths, **kwargs):
        seen["parent"] = str(parent_graph)
        seen["changed_paths"] = tuple(changed_paths)
        return str(rebuilt), "", ({"path": "mod.py", "updated": 1,
                                  "symbols_reminted": 3},)

    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked", fake_amend)
    monkeypatch.setattr(
        "gt_engine.indexer._receipt_for_published_graph",
        lambda graph, **kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT_CORE_ONLY, graph_db=graph,
            graph_revision="c" * 64, analysis_state="not_run",
            build_mode="incremental",
            source_revision=str(kwargs.get("source_revision") or ""),
            incremental_results=({"path": "mod.py", "updated": 1,
                                  "symbols_reminted": 3},),
        ),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("a full rebuild ran while an amend was available")

    monkeypatch.setattr("gt_engine.indexer.ensure_index_with_receipt", forbidden)

    # The boundary amends inline: one call, graph current at return.
    assert adapter.refresh_graph() is True

    # The amend was handed the published graph and the paths the edit dirtied.
    assert seen["parent"] == str(graph)
    assert seen["changed_paths"] == ("mod.py",)

    row = _build_mode_rows(adapter)[-1]
    assert row["event"] == "graph_boundary_amend"
    assert row["build_mode"] == "incremental"
    assert row["build_mode_reason"] == ""
    assert row["dirty_paths"] == ["mod.py"]
    assert row["amended"] == [{"path": "mod.py", "updated": 1, "symbols_reminted": 3}]
    assert row["analysis_state"] == "not_run"
    assert isinstance(row["elapsed_ms"], int)


def test_an_amend_refusal_is_named_and_never_rebuilds(monkeypatch, tmp_path):
    """A permanent silent fallback is how the amend path stayed dead.

    The real incremental producer runs here, refuses because no producer
    declares the amend capability, and must NOT fall back: a rebuild cannot
    restore amend capability, so the honest outcome is the named refusal with
    the stale-marked parent kept. ensure_index_with_receipt is forbidden to
    prove the full path is unreachable from a refusal.
    """
    adapter, _repo, _graph = _edited_adapter(tmp_path)
    _graph.with_suffix(".manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "gt_engine.indexer._producer_supports_amend_capability", lambda capability: False)

    def forbidden(*args, **kwargs):
        pytest.fail("a refused amend fell back to a full rebuild")

    monkeypatch.setattr("gt_engine.indexer.ensure_index_with_receipt", forbidden)

    assert adapter.refresh_graph() is False
    # Stays stale-marked: the refusal is an answer, and the next boundary
    # retries the amend - the graph is never silently rebuilt behind it.
    assert adapter.refresh_graph() is False

    lines = Path(adapter.store.path).read_text(encoding="utf-8").splitlines()
    refused = [
        row for line in lines
        if (row := json.loads(line)).get("event") == "graph_boundary_amend_refused"
    ]
    assert refused[-1]["reason"] == "producer_lacks_amend_capability"
    assert refused[-1]["dirty_paths"] == ["mod.py"]


class _StubEnrichmentHandle:
    """A scheduled promotion that has not finished, so polling stays a no-op."""

    task_id = "stub-promotion"

    def __init__(self) -> None:
        self.cancelled = False

    @property
    def done(self) -> bool:
        return False

    def cancel(self) -> bool:
        self.cancelled = True
        return True

    def terminal_receipt(self, *, timeout=None):  # pragma: no cover - never done
        raise AssertionError("terminal_receipt on an unfinished stub handle")


def test_enrichment_is_offered_on_a_rebuilt_current_graph(monkeypatch, tmp_path):
    """Every adopted graph is offered for LSP promotion, through the real path.

    _maybe_schedule_lsp_promotion runs at the end of refresh_graph on both
    kinds of boundary: the one that just adopted a graph and the
    already-current early return every later boundary takes. Nothing else
    proved the offer survives the route the benchmark actually takes - an
    edit, an inline rebuild, and the next refresh_graph. This drives it.
    """
    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
    from gt_engine.runtime_observation import capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )

    offers = []

    def stub_factory(request, base):
        offers.append((request.source_revision, base.graph_revision))
        return _StubEnrichmentHandle()

    # Bound before the first boundary, because refresh_graph calls this
    # attribute as the promotion factory after adopting.
    monkeypatch.setattr(a, "_schedule_lsp_candidate", stub_factory)

    a.start_task()
    a.record_repository_snapshot(capture_workspace(repo), boundary="after_action")
    a.note_edit(["mod.py"])

    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **_kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT,
            graph_db=str(rebuilt),
            graph_revision="b" * 64,
            analysis_state="complete",
        ),
    )

    # The recovery build lands inline and the offer fires at the same
    # boundary; the second call takes the already-current early return, the
    # branch every later boundary takes and the one the offer was missing.
    assert a.refresh_graph() is True
    assert a.refresh_graph() is True

    # The rebuilt graph - not the pre-edit one - is what gets promoted.
    assert offers == [(a.engine_state.source_revision, "b" * 64)]


def test_a_repeated_boundary_does_not_re_offer_the_same_graph(monkeypatch, tmp_path):
    """The offer is per published graph, not per boundary.

    Scheduling a promotion materialises the whole workspace to a temporary
    directory and hashes every byte of it twice, on the owner thread. A graph
    that has not moved must not pay that again at each action.
    """
    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
    from gt_engine.runtime_observation import capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )
    offers = []
    monkeypatch.setattr(
        a, "_schedule_lsp_candidate",
        lambda request, base: offers.append(base.graph_revision)
        or _StubEnrichmentHandle(),
    )
    a.start_task()
    a.record_repository_snapshot(capture_workspace(repo), boundary="after_action")
    a.note_edit(["mod.py"])
    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **_kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT, graph_db=str(rebuilt),
            graph_revision="b" * 64, analysis_state="complete",
        ),
    )
    assert a.refresh_graph() is True
    assert a.refresh_graph() is True
    assert a.refresh_graph() is True

    # One offer for the one published graph, however many boundaries pass.
    assert offers == ["b" * 64]


class _PendingEnrichmentHandle(_StubEnrichmentHandle):
    """A promotion still in flight: done only once the next boundary drains it.

    A real leg runs ~30-60s while boundaries keep landing; modelling it done
    at schedule time lets the same boundary drain it against an unmoved
    engine, which never reproduces the obsolete race.
    """

    def __init__(self, task_id: str, receipt: dict) -> None:
        super().__init__()
        self.task_id = task_id
        self._receipt = receipt
        self._released = False

    def release(self) -> None:
        self._released = True

    @property
    def done(self) -> bool:
        return self._released

    def terminal_receipt(self, *, timeout=None):
        return dict(self._receipt)


def test_lsp_promotion_backs_off_while_revisions_churn(monkeypatch, tmp_path):
    """A leg that loses the revision race must not respawn instantly.

    smoke20 bandit-taint: the graph revision re-minted every ~35-60s while a
    cold promotion leg ran ~30-60s, so 15 of 36 legs landed obsolete - each
    holding an LSP server resident through its doomed run while gt_index
    starved for headroom. Obsolescence itself is correct (stale evidence
    must never publish); the defect is scheduling a fresh leg whose expected
    latency exceeds the observed revision half-life. Consecutive obsolete
    dispositions must back off scheduling; a leg that keeps up resets it.
    """
    import itertools

    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
    from gt_engine.runtime_observation import capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )

    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    offers: list[str] = []
    in_flight: list[_PendingEnrichmentHandle] = []
    seq = itertools.count(1)
    scheduler = type("_Scheduler", (), {"_handles": []})()
    a._lsp_scheduler = scheduler

    def factory(request, base):
        task_id = f"leg-{next(seq)}"
        offers.append(base.graph_revision)
        handle = _PendingEnrichmentHandle(task_id, {
            "terminal": True, "status": "succeeded", "publishable": True,
            "verified": 3, "corrected": 0, "selected": 0, "deleted": 0,
            "source_revision": request.source_revision,
            "input_graph_revision": base.graph_revision,
            "candidate_path": "", "task_id": task_id,
        })
        scheduler._handles.append(handle)
        in_flight.append(handle)
        return handle

    monkeypatch.setattr(a, "_schedule_lsp_candidate", factory)

    revisions = itertools.cycle(["a" * 64, "b" * 64, "c" * 64, "d" * 64])

    def build(root, **_kwargs):
        revision = next(revisions)
        path = tmp_path / f"rebuilt-{revision[:1]}.db"
        path.write_bytes(b"new")
        return IndexBuildReceipt(
            IndexBuildStatus.BUILT, graph_db=str(path),
            graph_revision=revision, analysis_state="complete",
        )

    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt", build
    )

    a.start_task()
    a.record_repository_snapshot(
        capture_workspace(repo), boundary="after_action"
    )

    def churn():
        """One boundary: finish in-flight legs, then adopt the next revision."""
        for handle in in_flight:
            handle.release()
        a.note_edit(["mod.py"])
        assert a.refresh_graph() is True

    # Leg-1 schedules on rev A while it is the live graph, then loses the
    # race when rev B adopts at the next boundary.
    churn()
    assert offers == ["a" * 64]
    churn()  # leg-1 drains obsolete as rev B lands
    # Without backoff every further boundary re-offers the fresh revision to
    # another doomed leg (the bandit-taint hamster wheel: 15/36 obsolete).
    churn()
    assert offers == ["a" * 64], (
        "a superseded leg must back off, not respawn on the next revision"
    )

    # The window expires; the next boundary promotes the then-current graph.
    clock["now"] += 301.0
    churn()
    assert offers[-1] != "a" * 64
    assert len(offers) == 2


def test_lsp_churn_backoff_escalates_and_resets(monkeypatch, tmp_path):
    """The defer window doubles per lost race and clears when a leg keeps up.

    Producer-side outcomes (failed builds, certifier faults, identity
    mismatches) are not race evidence - they must leave the window alone so
    a leg that died for its own reasons neither widens nor clears the
    workspace's churn signal.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )
    clock = {"now": 5000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    note = a._note_lsp_churn_outcome
    note("obsolete")
    assert a._lsp_churn_streak == 1
    assert a._lsp_churn_defer_until == 5000.0 + 60.0
    note("obsolete_after_certification")
    assert a._lsp_churn_streak == 2
    assert a._lsp_churn_defer_until == 5000.0 + 120.0

    # Producer-side dispositions are not race outcomes: window untouched.
    for neutral in ("not_publishable", "certification_failed",
                    "certifier_exception", "identity_mismatch",
                    "schedule_exception"):
        note(neutral)
        assert a._lsp_churn_streak == 2
        assert a._lsp_churn_defer_until == 5000.0 + 120.0

    # A leg that kept up proves the race is winnable: reset.
    note("published")
    assert a._lsp_churn_streak == 0
    assert a._lsp_churn_defer_until == 0.0


def test_lsp_promotion_defers_after_index_memory_refusal(monkeypatch, tmp_path):
    """A headroom-refused index must not race a fresh LSP leg for memory.

    smoke20 bandit-taint: 16 graph_recovery_failed rows, all
    GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT, while promotion legs kept
    resident ~1GB servers alive. Spawning a leg the moment a build lands
    after a refusal re-pressures the cgroup the guard just protected - the
    next recovery starves again. A memory refusal must defer LSP
    scheduling until the pressure has had room to clear.
    """
    import itertools

    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
    from gt_engine.runtime_observation import capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"old")
    a = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(graph),
    )

    clock = {"now": 2000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    offers: list[str] = []
    scheduler = type("_Scheduler", (), {"_handles": []})()
    a._lsp_scheduler = scheduler
    seq = itertools.count(1)

    def factory(request, base):
        offers.append(base.graph_revision)
        handle = _PendingEnrichmentHandle(f"leg-{next(seq)}", {})
        scheduler._handles.append(handle)
        return handle

    monkeypatch.setattr(a, "_schedule_lsp_candidate", factory)

    receipts = iter([
        IndexBuildReceipt(
            IndexBuildStatus.BUILD_FAILED,
            error_type="GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT",
            error_diagnostic="memory_headroom_refused",
        ),
        IndexBuildReceipt(
            IndexBuildStatus.BUILT, graph_db=str(tmp_path / "r-a.db"),
            graph_revision="a" * 64, analysis_state="complete",
        ),
        IndexBuildReceipt(
            IndexBuildStatus.BUILT, graph_db=str(tmp_path / "r-b.db"),
            graph_revision="b" * 64, analysis_state="complete",
        ),
    ])
    (tmp_path / "r-a.db").write_bytes(b"new")
    (tmp_path / "r-b.db").write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **_kwargs: next(receipts),
    )

    a.start_task()
    a.record_repository_snapshot(
        capture_workspace(repo), boundary="after_action"
    )

    # The refused build leaves the graph stale, and the retry inside the
    # window defers too: respawning a heavier build into the pressure the
    # refusal just measured is how the live storm formed.
    a.note_edit(["mod.py"])
    assert a.refresh_graph() is False
    a.note_edit(["mod.py"])
    assert a.refresh_graph() is False

    clock["now"] += 121.0
    a.note_edit(["mod.py"])
    assert a.refresh_graph() is True
    # The window drained with the build, so the landed adoption offers.
    assert offers == ["a" * 64]

    clock["now"] += 301.0
    a.note_edit(["mod.py"])
    assert a.refresh_graph() is True
    # One promotion in flight at a time: the fresher adoption waits its turn.
    assert offers == ["a" * 64]


def _memory_killed_amend(spawns: list, code: str = "GT_INDEX_MEMORY_GUARD_TRIGGERED"):
    """A producer spawn that dies in Pass 1, like the live gate-one run."""

    def killed(root, *, parent_graph, changed_paths, **kwargs):
        spawns.append(tuple(changed_paths))
        return None, f"amend_failed:{code}:exit=-9:stderr=Pass 1: discovering files", ()

    return killed


def test_a_memory_killed_amend_defers_the_next_spawn(monkeypatch, tmp_path):
    """A guard-killed amend sets the defer window; boundaries stop spawning.

    Run 34849119441 (the paid gate-one): nineteen amend refusals clustered
    in bursts - five dead gt-index spawns inside ~60 journal events at one
    point - every one killed in Pass 1 while resident LSP legs held the
    cgroup. Each refusal was journaled and nothing consumed it: the memory
    defer machinery was fed only by build receipts, so the next serving
    boundary spawned straight back into the same pressure.
    """
    adapter, _repo, _graph = _edited_adapter(tmp_path)
    clock = {"now": 9000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )
    spawns: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked",
        _memory_killed_amend(spawns),
    )

    assert adapter.refresh_graph() is False
    assert adapter._index_memory_defer_until > clock["now"]

    adapter.refresh_graph()
    adapter.refresh_graph()
    assert len(spawns) == 1, "boundaries inside the window must not respawn"

    clock["now"] += 121.0
    adapter.refresh_graph()
    assert len(spawns) == 2


def test_a_memory_killed_amend_is_a_consequential_warning(monkeypatch, tmp_path):
    """A resource-bound attempt failure is retried, not reported terminal.

    The same run carried five GT_GRAPH_REFRESH_FAILED rows typed
    retryable=False primary ERROR - deterministic-failure classification
    applied to a guard kill that defer+retry exists for. A memory-family
    refusal keeps the journal row but moves to the consequential channel:
    the terminal verdict belongs to the capability row (stale at seal),
    not to one killed attempt.
    """
    adapter, _repo, _graph = _edited_adapter(tmp_path)
    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked",
        _memory_killed_amend([]),
    )

    assert adapter.refresh_graph() is False

    events = [
        event for event in adapter.diagnostics._events
        if event.code is DiagnosticCode.GT_GRAPH_REFRESH_FAILED
    ]
    assert len(events) == 1
    assert events[0].severity == "WARNING"
    assert events[0].classification == "consequential"
    assert events[0].retryable is True
    # A transient resource outcome is not proof an amend can never land:
    # it must not advance the deterministic-failure escalation streak.
    assert adapter._amend_failure_streak == {}


def test_a_deterministic_amend_failure_still_escalates_to_recovery(
    monkeypatch, tmp_path
):
    """Non-memory amend_failed on the same parent keeps the rebuild ladder.

    PROCESS_FAILED is not a typed memory outcome: it may be a real
    producer defect on immutable parent bytes, so the streak semantics
    must survive - two consecutive failures buy the recovery build that
    publishes a fresh base. The spawn defer window bounds HOW OFTEN the
    boundary retries; it does not count as a failure itself.
    """
    from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus

    adapter, _repo, _graph = _edited_adapter(tmp_path)
    clock = {"now": 4000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )
    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked",
        _memory_killed_amend([], code="GT_INDEX_PROCESS_FAILED"),
    )
    recoveries: list[int] = []
    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")
    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt",
        lambda root, **kwargs: recoveries.append(1) or IndexBuildReceipt(
            IndexBuildStatus.BUILT, graph_db=str(rebuilt),
            graph_revision="b" * 64, analysis_state="complete",
        ),
    )

    adapter.refresh_graph()  # attempt 1: streak 1
    clock["now"] += 61.0   # past the spawn-defer window
    adapter.refresh_graph()  # attempt 2: streak 2 -> escalate
    assert recoveries == [1]

    # The rebuild's failure typing is unchanged: still a primary ERROR.
    events = [
        event for event in adapter.diagnostics._events
        if event.code is DiagnosticCode.GT_GRAPH_REFRESH_FAILED
    ]
    assert events and all(
        event.severity == "ERROR" and event.classification == "primary"
        for event in events
    )


def test_a_recovery_build_defers_while_the_memory_window_is_open(
    monkeypatch, tmp_path
):
    """Escalating to a heavier build under measured pressure is worse.

    A full recovery build spawns the same bounded producer over the whole
    tree - strictly more allocation than the amend that just died. While
    the memory window is open the rebuild defers; the boundary refuses
    honestly and the trigger survives to the next boundary.
    """
    adapter, _repo, _graph = _edited_adapter(tmp_path)
    clock = {"now": 7000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )
    adapter._index_memory_defer_until = clock["now"] + 120.0

    def forbidden(root, **kwargs):
        pytest.fail("a recovery build spawned inside the memory window")

    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt", forbidden
    )
    adapter._recovery_build_inline(phase="native_action")

    rows = [
        row for row in _journal_rows(adapter)
        if row.get("event") == "graph_recovery_deferred"
    ]
    assert rows and rows[-1]["reason"] == "index_memory_backoff"


def test_the_transaction_amend_also_feeds_and_obeys_the_window(
    monkeypatch, tmp_path
):
    """The transaction-boundary amend is the other spawn site.

    Run 34849119441's graph_sync_amend_refused rows are this path: a
    memory kill there never reached the defer machinery at all - it
    journaled and returned. It must feed the same window and skip its own
    spawn while the window is open.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(tmp_path / "graph.db"),
    )
    (tmp_path / "graph.db").write_bytes(b"old")
    adapter.start_task()
    before = capture_workspace(repo)
    adapter.record_repository_snapshot(before, boundary="task_start")

    clock = {"now": 3000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )
    spawns: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked",
        _memory_killed_amend(spawns),
    )

    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    tx = diff_workspace(
        before, capture_workspace(repo), action_id=1, command="edit a.py"
    )
    adapter.record_edit_transaction(tx)
    assert len(spawns) == 1
    assert adapter._index_memory_defer_until > clock["now"]

    # The next edit's synchronous amend is inside the window: no spawn.
    before = capture_workspace(repo)
    (repo / "a.py").write_text("a = 3\n", encoding="utf-8")
    tx = diff_workspace(
        before, capture_workspace(repo), action_id=2, command="edit a.py"
    )
    adapter.record_edit_transaction(tx)
    assert len(spawns) == 1


def test_sustained_churn_under_memory_pressure_storms_once_per_window(
    monkeypatch, tmp_path
):
    """The live storm, replayed deterministically: boundaries keep coming,
    the cgroup stays pressured, and spawns stay bounded.

    Run 34849119441 journaled nineteen amend refusals across the run with
    five dead spawns inside ~60 events - one per serving boundary, because
    nothing slowed the retry. Here forty boundaries fire inside ~200
    seconds of measured pressure: the defer machinery must turn that into
    a handful of attempts, all consequential warnings, zero escalations,
    and a clean recovery the moment pressure clears.
    """
    adapter, _repo, graph = _edited_adapter(tmp_path)
    clock = {"now": 10000.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    pressure_until = clock["now"] + 200.0
    spawns: list[float] = []
    rebuilt = tmp_path / "rebuilt.db"
    rebuilt.write_bytes(b"new")

    def pressure_amend(root, *, parent_graph, changed_paths, **kwargs):
        spawns.append(clock["now"])
        if clock["now"] < pressure_until:
            return None, (
                "amend_failed:GT_INDEX_MEMORY_GUARD_TRIGGERED:exit=-9:"
                "stderr=Pass 1: discovering files"
            ), ()
        return str(rebuilt), "", ({"path": "mod.py", "updated": 1},)

    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked", pressure_amend
    )
    monkeypatch.setattr(
        "gt_engine.indexer._receipt_for_published_graph",
        lambda graph_path, **kwargs: IndexBuildReceipt(
            IndexBuildStatus.BUILT_CORE_ONLY, graph_db=graph_path,
            graph_revision="d" * 64, analysis_state="not_run",
            build_mode="incremental",
            source_revision=str(kwargs.get("source_revision") or ""),
        ),
    )

    def forbidden_recovery(root, **kwargs):
        pytest.fail("a recovery build spawned while the storm was bounded")

    monkeypatch.setattr(
        "gt_engine.indexer.ensure_index_with_receipt", forbidden_recovery
    )

    # Forty serving boundaries inside the pressure window, ~5s apart: the
    # cadence the paid run actually produced.
    for _ in range(40):
        adapter.note_edit(["mod.py"])
        adapter.refresh_graph()
        clock["now"] += 5.0

    # ~200s of pressure with a 120s memory window re-armed by each kill:
    # a handful of attempts, not one per boundary. The live run did one
    # per boundary.
    assert 1 < len(spawns) <= 5
    assert adapter._amend_failure_streak == {}, (
        "resource-bound outcomes must not feed the deterministic ladder"
    )
    assert all(
        event.severity == "WARNING"
        and event.classification == "consequential"
        and event.retryable
        for event in adapter.diagnostics._events
        if event.code is DiagnosticCode.GT_GRAPH_REFRESH_FAILED
    )

    # Pressure clears and the last kill's window drains; the next
    # boundary's amend lands and the graph is current again - the
    # deferral cost bounded staleness, not an outage.
    clock["now"] += 60.0
    adapter.note_edit(["mod.py"])
    assert adapter.refresh_graph() is True
    assert adapter.engine_state.graph_current is True

    events = {
        row.get("event") for row in _journal_rows(adapter)
    }
    assert "index_memory_backoff" in events
    assert "graph_amend_deferred" in events


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


def test_frozen_graph_input_skips_producer_pruned_trees(tmp_path):
    """Smoke-20: a git-tracked .vuepress/dist bundle entered the snapshot via
    the unfiltered git path and froze every abs-repo rebuild (942 events).
    The producer's walk prunes _SKIP_DIRS by name at any depth, so a path
    under one is never producer input."""
    from gt_engine.runtime_observation import FileState, capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = capture_workspace(repo)
    bundle = FileState(
        path="docs/src/.vuepress/dist/assets/js/12.5188bbc0.js",
        kind="file", sha256="0" * 64, size=2_000_000, captured=None,
    )
    snapshot = replace(snapshot, files=(*snapshot.files, bundle), complete=False)
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state",
        predicates=[], repo_root=str(repo),
    )
    request = adapter._frozen_graph_input(snapshot)
    assert request.files == (("mod.py", (repo / "mod.py").read_bytes()),)


def test_frozen_graph_input_verifies_hash_only_witness_bytes(tmp_path):
    """A producer-input file above the capture cap is a hash-only witness;
    a live read that still hashes to the snapshot digest satisfies the freeze."""
    from gt_engine.runtime_observation import FileState, capture_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    big = repo / "big.js"
    big.write_text("const x = 1;\n", encoding="utf-8")
    snapshot = capture_workspace(repo)
    original = next(f for f in snapshot.files if f.path == "big.js")
    uncaptured = replace(original, captured=None)
    snapshot = replace(
        snapshot,
        files=tuple(uncaptured if f.path == "big.js" else f
                    for f in snapshot.files),
    )
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state",
        predicates=[], repo_root=str(repo),
    )
    request = adapter._frozen_graph_input(snapshot)
    assert ("big.js", big.read_bytes()) in request.files

    # Same file, different live content -> digest mismatch -> honest miss.
    big.write_text("const x = 2;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen_source_incomplete"):
        adapter._frozen_graph_input(snapshot)


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


def test_churn_steer_names_pending_check_command(tmp_path):
    from gt_engine.persistent_plan.checks import CheckSpec

    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a._check_specs = {
        "check-1": CheckSpec(
            check_id="check-1",
            argv=("pytest", "-q", "tests/test_parser.py"),
            cwd=".",
            protocol="pytest",
            requirement_ids=("row-1",),
        )
    }
    a._pending_check_ids = {"check-1"}
    text = a.build_churn_steer(25)
    assert "pytest -q tests/test_parser.py" in text
    assert "GT_CHURN_STEER" in text
    assert "check-1" not in text


def test_churn_steer_names_unmet_requirement_without_internal_ids(tmp_path):
    from gt_engine.persistent_plan import PlanRow

    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a._check_specs = {}
    a._pending_check_ids = set()
    a.persistent_plan = SimpleNamespace(
        rows=[
            PlanRow(
                row_id="row-7",
                text="Preserve multi-column array spans in the renderer.",
                verification_command="npm test -- --grep spans",
            )
        ]
    )
    a.unmet_plan_rows = lambda: ("row-7",)
    text = a.build_churn_steer(30)
    assert "Preserve multi-column array spans" in text
    assert "npm test -- --grep spans" in text
    assert "row-7" not in text


def test_churn_steer_falls_back_to_plain_redirect(tmp_path):
    a = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    a._check_specs = {}
    a._pending_check_ids = set()
    text = a.build_churn_steer(25)
    assert "Return to the task now" in text
    assert "terminates this run" in text


class _FakeStartupFuture:
    def __init__(self, receipt=None, exc=None):
        self._receipt = receipt
        self._exc = exc

    def done(self):
        return True

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._receipt


def _receipt(**kwargs):
    defaults = dict(
        success=True, graph_db="graph.db", graph_revision="g1",
        source_revision="rev0", elapsed_ms=5,
        analysis_state="complete", embedding_state="ready",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _journal_rows(adapter):
    return [
        json.loads(line)
        for line in adapter.store.path.read_text().splitlines()
        if line.strip()
    ]


def test_note_edit_skips_invalidation_when_the_edit_is_already_adopted(
    monkeypatch, tmp_path
):
    """A synchronous amend publishes before note_edit runs; re-dirtying the
    just-adopted engine would journal an invalidation after the publication
    that closed it and force a deduplicated second amend -- the
    `ended_graph_dark` tail in run 34754150319."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db="graph.db",
    )
    adapter.start_task()
    adapter.begin_implement()
    before = capture_workspace(repo)
    adapter.record_repository_snapshot(before, boundary="task_start")
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    transaction = diff_workspace(
        before, capture_workspace(repo), action_id=1, command="edit a.py"
    )
    # The producer stands in for the boundary amend: adopting the
    # transaction's revision is what publish_graph does after a real amend.
    monkeypatch.setattr(
        adapter, "_sync_amend_graph",
        lambda tx: adapter.engine_state.publish_graph(
            graph_path="graph.db", graph_revision="g2",
            source_revision=str(tx.post_revision),
        ),
    )
    adapter.record_edit_transaction(transaction)
    assert adapter.engine_state.graph_current

    adapter.note_edit(["a.py"])

    rows = _journal_rows(adapter)
    assert not any(row.get("event") == "graph_invalidated" for row in rows)
    assert adapter.engine_state.graph_current
    assert adapter.graph_stale_since_revision == ""


def test_note_edit_journals_invalidation_when_graph_is_stale(tmp_path):
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=[], graph_db="graph.db"
    )
    adapter.start_task()
    adapter.begin_implement()
    adapter.engine_state.bind_initial_source("rev0")
    adapter.engine_state.publish_graph(
        graph_path="graph.db", graph_revision="g1", source_revision="rev0"
    )
    adapter.engine_state.source_revision = "rev1"
    adapter.repository_revision = "rev1"
    assert not adapter.engine_state.graph_current

    adapter.note_edit(["a.py"])

    rows = _journal_rows(adapter)
    invalidated = [row for row in rows if row.get("event") == "graph_invalidated"]
    assert len(invalidated) == 1
    assert invalidated[0]["paths"] == ["a.py"]
    assert invalidated[0]["repository_revision"] == "rev1"
    assert adapter.graph_stale_since_revision == "rev1"


def test_note_edit_invalidation_for_paths_outside_the_adopted_transaction(
    monkeypatch, tmp_path
):
    """The adopted skip must be keyed to the transaction's own paths.

    When the workspace diff cannot enumerate changes the runtime falls back
    to _capture_edit_after's shell-intent paths -- a transaction with no
    enumerated changes never reaches record_edit_transaction, so its
    heuristic paths were never amended into anything. Skipping the
    invalidation there would leave the graph claiming currency over an
    unenumerated edit.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db="graph.db",
    )
    adapter.start_task()
    adapter.begin_implement()
    before = capture_workspace(repo)
    adapter.record_repository_snapshot(before, boundary="task_start")
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    transaction = diff_workspace(
        before, capture_workspace(repo), action_id=1, command="edit a.py"
    )
    monkeypatch.setattr(
        adapter, "_sync_amend_graph",
        lambda tx: adapter.engine_state.publish_graph(
            graph_path="graph.db", graph_revision="g2",
            source_revision=str(tx.post_revision),
        ),
    )
    adapter.record_edit_transaction(transaction)
    assert adapter.engine_state.graph_current

    # Same epoch, but a superset of what the adopted transaction amended:
    # the heuristic tail must still invalidate.
    adapter.note_edit(["a.py", "heuristic-extra.py"])

    rows = _journal_rows(adapter)
    invalidated = [row for row in rows if row.get("event") == "graph_invalidated"]
    assert len(invalidated) == 1
    assert set(invalidated[0]["paths"]) == {"a.py", "heuristic-extra.py"}


def test_poll_startup_index_adopts_a_landed_graph(tmp_path):
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    adapter.engine_state.bind_initial_source("rev0")
    finalized = []
    adapter._startup_index = _FakeStartupFuture(_receipt())
    adapter._startup_finalize = lambda receipt: finalized.append(receipt.graph_db)

    adapter._poll_startup_index()

    assert adapter.graph_db == "graph.db"
    assert adapter.engine_state.graph_current
    assert adapter._startup_index is None
    assert finalized == ["graph.db"]
    events = [row.get("event") for row in _journal_rows(adapter)]
    assert "initial_index_ready" in events


def test_poll_startup_index_keeps_a_superseded_graph_as_amend_parent(tmp_path):
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    adapter.engine_state.bind_initial_source("rev0")
    adapter.engine_state.mark_paths_dirty(("src/a.py",), revision="rev1")
    adapter._startup_index = _FakeStartupFuture(
        _receipt(source_revision="rev0")
    )

    adapter._poll_startup_index()

    assert adapter.graph_db is None
    assert not adapter.engine_state.graph_current
    assert adapter._unadopted_graph == ("graph.db", "g1")
    ready = [row for row in _journal_rows(adapter)
             if row.get("event") == "initial_index_ready"]
    assert ready and ready[0].get("adopted") is False


def test_poll_startup_index_journals_failure_without_abort(tmp_path):
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    adapter.engine_state.bind_initial_source("rev0")
    adapter._startup_index = _FakeStartupFuture(exc=RuntimeError("boom"))

    adapter._poll_startup_index()

    rows = _journal_rows(adapter)
    events = [row.get("event") for row in rows]
    assert "index_unavailable" in events
    assert "startup_abort" not in events


def test_poll_startup_index_signals_abort_on_required_graph(tmp_path):
    class BenchmarkGraphRequired(Exception):
        pass

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    adapter.engine_state.bind_initial_source("rev0")
    adapter._startup_index = _FakeStartupFuture(
        exc=BenchmarkGraphRequired("graph required")
    )

    adapter._poll_startup_index()

    rows = _journal_rows(adapter)
    abort = [row for row in rows if row.get("event") == "startup_abort"]
    assert abort and "benchmark_graph_required" in abort[0].get("reason", "")
    flag = adapter.engine_state.layout.state_root / "startup_abort.json"
    assert flag.exists()
    assert "benchmark_graph_required" in json.loads(flag.read_text())["reason"]


def test_adopt_startup_plan_registers_new_predicates_as_unknown(tmp_path):
    contract = extract_task_contract("Fix the parser.")
    adapter = MiniSweAdapter(
        task_id="task", state_dir=tmp_path, predicates=[], contract=contract,
    )
    from gt_engine.miniswe_controller import Predicate
    from gt_engine.persistent_plan import PlanInputs

    inputs = SimpleNamespace(ledger=(), as_dict=lambda: {}, counts=lambda: {})
    merged_predicates = (Predicate("pred-new", "new row"),)
    merged_contract = SimpleNamespace(obligations=())

    class FakeCompiled:
        predicate_id = "pred-new"
        obligation_id = "ob-1"
        kind = "k"
        scope = ()

    import gt_engine.miniswe_integration as mi
    original = mi.compile_obligation_predicates
    mi.compile_obligation_predicates = lambda c: {"ob-1": FakeCompiled()}
    try:
        adapter.adopt_startup_plan(inputs, merged_contract, merged_predicates)
    finally:
        mi.compile_obligation_predicates = original

    assert adapter.plan_inputs is inputs
    assert "pred-new" in adapter.predicates
    assert adapter._status["pred-new"].value == "UNKNOWN"
    rows = _journal_rows(adapter)
    assert any(row.get("event") == "contract.predicate_compiled"
               and row.get("predicate_id") == "pred-new" for row in rows)


def test_unbound_check_stays_pending_and_retries_on_new_revision(tmp_path):
    """A check bound before its test file exists must not be discarded.

    The drain used to drop specs whose test source did not resolve - so a
    bound check whose test the agent had not written yet could never run.
    It now stays pending, journals once, skips cheaply while the revision is
    unchanged, and retries (then executes) once the workspace moves.
    """
    os.environ["GT_VERIFY_EXECUTE"] = "1"
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "lib.py").write_text("x = 1\n", encoding="utf-8")
        adapter = MiniSweAdapter(
            task_id="task", state_dir=tmp_path / "state",
            repo_root=str(repo), predicates=[],
        )
        adapter.engine_state.bind_initial_source("rev0")
        adapter.repository_revision = "rev0"
        from gt_engine.persistent_plan.checks import CheckSpec

        spec = CheckSpec.from_dict(
            {"argv": ["pytest", "tests/test_new.py"], "cwd": ".",
             "requirement_ids": ["r1"]},
            str(repo),
        )
        adapter._check_specs = {spec.check_id: spec}
        adapter._pending_check_ids = {spec.check_id}
        calls = []

        class Env:
            def execution_env(self):
                return lambda *a, **k: None

            def execute(self, payload, **kwargs):
                calls.append(payload)
                return {"returncode": 0, "output": "1 passed",
                        "extra": {"capture_complete": True}}

        adapter.drain_plan_checks(Env())
        # Unbound: still pending, journaled once, nothing executed.
        assert spec.check_id in adapter._pending_check_ids
        assert not calls
        rows = _journal_rows(adapter)
        assert sum(
            1 for row in rows
            if row.get("event") == "plan_check_binding_pending"
            and row.get("reason") == "test_source_not_bound"
        ) == 1
        # Same revision: deterministic-identical outcome -> skip, no re-journal.
        adapter.drain_plan_checks(Env())
        assert sum(
            1 for row in _journal_rows(adapter)
            if row.get("event") == "plan_check_binding_pending"
        ) == 1
        # The test file arrives: new revision, the spec retries and executes.
        (repo / "tests").mkdir()
        (repo / "tests" / "test_new.py").write_text(
            "def test_x():\n    assert True\n", encoding="utf-8"
        )
        adapter.engine_state.mark_paths_dirty(("tests/test_new.py",), revision="rev1")
        adapter.repository_revision = "rev1"
        adapter.drain_plan_checks(Env())
        assert calls, "the rebound spec never executed"
        assert spec.check_id not in adapter._pending_check_ids
    finally:
        os.environ.pop("GT_VERIFY_EXECUTE", None)


def test_a_headroom_refused_amend_opens_the_cgroup_window_only(monkeypatch, tmp_path):
    """A pre-launch headroom refusal defers without pretending a producer died.

    Gate-one (35056493769) SIGKILLed two single-file batch amends mid-flight.
    The floor refusal returns before spawn, so the reason is the bare typed
    code — not ``amend_failed:*`` — and only the cgroup window opens: the
    spawn window exists for dead producers, and none ran.
    """
    adapter, _repo, _graph = _edited_adapter(tmp_path)
    clock = {"now": 4200.0}
    monkeypatch.setattr(
        "gt_engine.miniswe_integration.time.monotonic", lambda: clock["now"]
    )

    def headroom_refusal(root, *, parent_graph, changed_paths, **kwargs):
        return (None,
                "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:"
                "limit=100need=200", ())
    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked", headroom_refusal)

    assert adapter.refresh_graph() is False
    assert adapter._index_memory_defer_until > clock["now"], (
        "measured pressure must open the cgroup window")
    assert adapter._graph_amend_defer_until == 0.0, (
        "no producer spawned — the spawn window must not open")

    rows = _journal_rows(adapter)
    refused = [r for r in rows if r.get("event") in {
        "graph_sync_amend_refused", "graph_boundary_amend_refused"}]
    assert refused and "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT" in refused[-1]["reason"]
    assert "amend_failed" not in refused[-1]["reason"]
    assert any(r.get("event") == "index_memory_backoff" for r in rows)

    # Boundaries inside the window defer without re-measuring; none journal
    # a second refusal.
    adapter.refresh_graph()
    assert sum(1 for r in _journal_rows(adapter)
               if r.get("event") in {
                   "graph_sync_amend_refused", "graph_boundary_amend_refused"}) == 1
