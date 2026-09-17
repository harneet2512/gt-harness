"""End to end on the real objects: contract merge, predicates, gate, journal.

No fake adapter and no fake controller. This exercises the actual
``MiniSweAdapter`` and ``GTSession`` so the chain that matters is proven:
a prompt line the sentence extractor merged away becomes a tracked obligation,
that obligation can be proven by a real command observation, and the submit
decision changes because of it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.gt_session import GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs, merged_plan_contract
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.task_contract import extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates

PROMPT = (
    "Add strict mode to the container loader.\n"
    "\n"
    "Assumptions:\n"
    " build_container must accept a registry argument\n"
    " Scoped loaders run independently; the parent loader is not rebuilt\n"
)


def test_zero_step_limit_is_unlimited_not_exhausted(monkeypatch):
    session = GTSession.__new__(GTSession)
    session._plan_agent = SimpleNamespace(
        config=SimpleNamespace(wall_time_limit_seconds=6600, step_limit=0),
        _start_time=1000, n_calls=301,
    )
    monkeypatch.setattr("time.time", lambda: 6000)
    assert session.plan_gate_budget() == (1600.0, None)
    session._plan_agent.config.step_limit = 300
    assert session.plan_gate_budget() == (1600.0, 0)


def test_installed_miniswe_unlimited_queries_preserve_deadline(monkeypatch):
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.exceptions import TimeExceeded

    # No provider calls: exercise the installed agent's actual query/limit logic.
    model = SimpleNamespace(query=lambda messages: {"role": "assistant", "content": "continue"})
    agent = DefaultAgent(model, SimpleNamespace(), system_template="", instance_template="",
                         step_limit=0, wall_time_limit_seconds=6600)
    monkeypatch.setattr("time.time", lambda: agent._start_time + 5000)
    for _ in range(301):
        agent.query()
    assert agent.n_calls == 301
    session = GTSession.__new__(GTSession)
    session._plan_agent = agent
    assert session.plan_gate_budget() == (1600.0, None)
    monkeypatch.setattr("time.time", lambda: agent._start_time + 6600)
    with pytest.raises(TimeExceeded):
        agent.query()
    assert agent.n_calls == 301


@pytest.fixture
def graph(tmp_path):
    path = tmp_path / "graph.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY, label TEXT, name TEXT,"
            " qualified_name TEXT, file_path TEXT, start_line INTEGER,"
            " end_line INTEGER, signature TEXT, return_type TEXT,"
            " is_exported INTEGER, is_test INTEGER, language TEXT,"
            " parent_id INTEGER, repo_id INTEGER)"
        )
        db.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER,"
            " target_id INTEGER, type TEXT, source_line INTEGER,"
            " source_file TEXT, resolution_method TEXT, confidence REAL,"
            " metadata TEXT, trust_tier TEXT, candidate_count INTEGER,"
            " evidence_type TEXT, verification_status TEXT, repo_id INTEGER)"
        )
        db.execute(
            "CREATE TABLE properties (id INTEGER PRIMARY KEY, node_id INTEGER,"
            " kind TEXT, value TEXT, line INTEGER, confidence REAL,"
            " property_id TEXT, start_line INTEGER, end_line INTEGER,"
            " extractor TEXT, evidence_method TEXT, trust_tier TEXT,"
            " verification_status TEXT, source_revision TEXT, repo_id INTEGER)"
        )
        db.execute(
            "INSERT INTO nodes (id,label,name,qualified_name,file_path,start_line,"
            "signature,is_test,language) VALUES (1,'Function','build_container',"
            "'build_container','src/container.py',10,"
            "'def build_container(registry):',0,'python')"
        )
        db.executemany(
            "INSERT INTO properties (node_id,kind,value,line) VALUES (?,?,?,?)",
            [(1, "param", "registry", 10), (1, "param", "strict:bool opt=False", 10)],
        )
    return str(path)


def _built(tmp_path, graph):
    """Build the whole Phase 0 chain exactly as the runner does."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    contract = extract_task_contract(PROMPT)
    inputs = build_plan_inputs(
        PROMPT, contract=contract, graph_db=graph, repo_root=str(repo),
        source_revision="rev1", graph_revision="g1", capture_baseline=False,
    )
    merged = merged_plan_contract(contract, inputs.ledger, PROMPT)
    compiled = compile_obligation_predicates(merged)
    predicates = tuple(
        Predicate(compiled[obligation.obligation_id].predicate_id, obligation.text)
        for obligation in merged.obligations
        if obligation.obligation_id in compiled
    )
    adapter = MiniSweAdapter(
        task_id="plan-int", state_dir=tmp_path / "state", predicates=predicates,
        contract=merged, repo_root=str(repo), graph_db=graph, issue_text=PROMPT,
    )
    adapter.plan_inputs = inputs
    return adapter, inputs, contract, merged, repo


def _plan_for(inputs, adapter):
    row_ids = [row.row_id for row in inputs.ledger.rows]
    plan = build_plan(
        {
            "rows": [
                {"row_id": row_id, "anchors": [], "verification_kind": "command",
                 "verification_command": "pytest -q"}
                for row_id in row_ids
            ]
        },
        inputs,
    )
    adapter.persistent_plan = plan
    adapter.register_plan_predicates(plan)
    return plan


def test_a_merged_line_becomes_a_tracked_obligation(tmp_path, graph):
    """The line the sentence extractor swallowed is now its own obligation."""
    _adapter, inputs, contract, merged, _repo = _built(tmp_path, graph)
    assert len(merged.obligations) > len(contract.obligations)
    added = {
        item.text
        for item in merged.obligations
        if item.obligation_id.startswith("plan-")
    }
    assert any("parent loader is not rebuilt" in text for text in added)
    assert inputs.counts()["ledger_only_rows"] >= 1


def test_every_merged_obligation_is_compilable_and_therefore_provable(tmp_path, graph):
    """An obligation that cannot compile could block forever without proving."""
    _adapter, _inputs, _contract, merged, _repo = _built(tmp_path, graph)
    compiled = compile_obligation_predicates(merged)
    assert set(compiled) == {item.obligation_id for item in merged.obligations}


def test_plan_rows_map_to_predicates_and_start_unmet(tmp_path, graph):
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    assert adapter.plan_row_predicates, "no plan row mapped to a predicate"
    unmet = adapter.unmet_plan_rows()
    assert unmet, "every row should start without evidence"
    assert set(unmet) <= {row.row_id for row in plan.rows}


def test_unbound_passing_suite_does_not_clear_plan_rows(tmp_path, graph):
    """Passing a suite without requirement bindings is not behavioral proof."""
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    before = adapter.unmet_plan_rows()
    adapter.start_task()
    adapter.evaluate_observation(
        "pytest",
        "collected 3 items\n3 passed in 0.10s",
        returncode=0,
        action_index=1,
    )
    after = adapter.unmet_plan_rows()
    assert after == before


def test_registration_is_refused_after_the_first_edit(tmp_path, graph):
    """A late obligation would retroactively un-verify proven work."""
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    adapter.start_task()
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    adapter.note_edit(["mod.py"])
    assert adapter.workspace_epoch == 1
    assert adapter.register_plan_predicates(plan) == 0


def test_the_gate_keeps_refusing_until_refusals_stop_buying_evidence(tmp_path, graph):
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = GTSession(
        GTSessionConfig(task_id="plan-int", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )

    class _Agent:
        class config:
            wall_time_limit_seconds = 5100
            step_limit = 300

        _start_time = __import__("time").time()
        n_calls = 5

    session._plan_agent = _Agent()
    adapter.start_task()
    adapter.begin_verify()
    adapter.begin_submit()

    assert session.plan_submit_gate() is False
    assert adapter.pending_directives
    assert "GT PLAN GATE" in adapter.pending_directives[0]

    # It keeps refusing while the budget is healthy and nothing has been proven.
    # Refusing exactly once let a measured task submit unproven with 71 minutes
    # and 142 steps still available.
    assert session.plan_submit_gate() is False

    # ...and it never concedes while evidence is missing. The stall count is
    # journaled evidence of a non-compliant submitter, not a limit that ships
    # the defective submission the gate exists to stop.
    from gt_engine.persistent_plan.gate import MAX_REFUSALS_WITHOUT_PROGRESS

    for _ in range(MAX_REFUSALS_WITHOUT_PROGRESS):
        session.plan_submit_gate()
    assert session.plan_submit_gate() is False
    last = [row for row in _journal(adapter) if row["event"] == "plan_gate_decision"][-1]
    assert last["reason"] == "unmet_plan_rows"
    assert last["evidence"]["stalled_refusals"] >= MAX_REFUSALS_WITHOUT_PROGRESS


def test_the_gate_refuses_when_the_budget_is_nearly_gone(tmp_path, graph):
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = GTSession(
        GTSessionConfig(task_id="plan-int", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )

    class _Agent:
        class config:
            wall_time_limit_seconds = 60
            step_limit = 300

        _start_time = __import__("time").time() - 55
        n_calls = 5

    session._plan_agent = _Agent()
    adapter.start_task()
    adapter.begin_verify()
    adapter.begin_submit()
    # ~5s remain: inside the reserve the old policy shipped the dirty submit.
    # The contract refuses; the escape that was available is journaled as
    # evidence instead of spent.
    assert session.plan_submit_gate() is False
    rows = _journal(adapter)
    decision = [row for row in rows if row["event"] == "plan_gate_decision"][-1]
    assert decision["escaped"] == ""
    assert decision["evidence"]["escape_available"] == "time"


def _journal(adapter) -> list[dict]:
    text = Path(adapter.store.path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_gate_rereads_budget_consumed_by_queued_checks(tmp_path, graph, monkeypatch):
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = GTSession(GTSessionConfig(task_id="reserve", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    session._plan_agent = SimpleNamespace(env=object())
    remaining = [603.0]
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (remaining[0], 20))
    allowances = []

    def drain(environment, *, budget_seconds):
        allowances.append(budget_seconds)
        remaining[0] = 599.0

    monkeypatch.setattr(adapter, "drain_plan_checks", drain)
    monkeypatch.setattr(session, "_plan_baseline_check", lambda: ((), "unknown"))
    # The drain still runs inside the reserve -- it can close rows and convert
    # the refusal into a clean accept -- but the submit itself refuses on the
    # unmet rows that remain, and the escape is journaled, not spent.
    assert session.plan_submit_gate() is False
    assert allowances == [30.0]
    event = next(row for row in reversed(_journal(adapter)) if row["event"] == "plan_gate_decision")
    assert event["reason"] == "unmet_plan_rows"
    assert event["remaining_seconds"] == 599.0
    assert event["evidence"]["escape_available"] == "time"
    assert event["completion_proven"] is False


def test_gate_progress_is_applied_before_reached_stall_limit(tmp_path, graph, monkeypatch):
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    session = GTSession(GTSessionConfig(task_id="progress", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (600.0, 20))
    monkeypatch.setattr(session, "_plan_baseline_check", lambda: ((), "unknown"))
    row_ids = tuple(row.row_id for row in plan.rows)
    assert len(row_ids) > 1
    remaining = [row_ids]
    monkeypatch.setattr(adapter, "unmet_plan_rows", lambda: remaining[0])
    for _ in range(3):
        assert session.plan_submit_gate() is False
    assert session._plan_gate_stalled_refusals == 3
    remaining[0] = row_ids[1:]
    assert session.plan_submit_gate() is False
    assert session._plan_gate_stalled_refusals == 1
    assert session.plan_submit_gate() is False
    assert session.plan_submit_gate() is False
    # Progress reset the stall counter once; the stalls after it reach the
    # journaled count again -- and the gate still refuses, because a dirty
    # submit fails the same attestation an unverified run does.
    assert session.plan_submit_gate() is False
    event = next(row for row in reversed(_journal(adapter)) if row["event"] == "plan_gate_decision")
    assert event["reason"] == "unmet_plan_rows"
    assert event["evidence"]["stalled_refusals"] == 3
    assert event["completion_proven"] is False


def test_baseline_recheck_cannot_spend_the_submission_reserve(tmp_path, graph, monkeypatch):
    from dataclasses import replace

    from gt_engine.persistent_plan.baseline import BaselineResult, RegressionReport

    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    adapter.plan_inputs = replace(inputs, baseline=BaselineResult(
        status="captured", command=("pytest",), duration_seconds=60))
    session = GTSession(GTSessionConfig(task_id="reserve", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (602.0, 200))
    budgets = []

    def compare(*args, **kwargs):
        budgets.append(kwargs["budget_seconds"])
        return RegressionReport(status="unknown")

    monkeypatch.setattr("gt_engine.persistent_plan.baseline.compare_to_baseline", compare)
    session._plan_baseline_check()
    assert budgets == [2.0]


@pytest.mark.parametrize("phase", ["VERIFY", "SUBMIT"])
def test_baseline_mutation_at_terminal_boundary_returns_to_implementation(tmp_path, graph, monkeypatch, phase):
    import sys
    from dataclasses import replace

    from gt_engine.event_journal import verify_event_journal
    from gt_engine.persistent_plan.baseline import run_baseline

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process-tree and capture boundary required")
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    source = repo / "widget.py"
    source.write_text("value = 1\n", encoding="utf-8")
    test = repo / "test_widget.py"
    test.write_text("def test_widget(): assert True\n", encoding="utf-8")
    baseline = run_baseline(str(repo), budget_seconds=15,
                            command=(sys.executable, "-m", "pytest", "-v", "test_widget.py"))
    assert baseline.captured and baseline.passed == 1
    adapter.plan_inputs = replace(inputs, baseline=baseline)
    test.write_text("def test_widget():\n    from pathlib import Path\n"
                    "    Path('widget.py').write_text('value = 2\\n')\n", encoding="utf-8")
    session = GTSession(GTSessionConfig(task_id="mutation", repo_root=str(repo), mode="advisory"),
                        engine=adapter)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (1000.0, None))
    adapter.start_task()
    adapter.begin_verify()
    if phase == "SUBMIT":
        adapter.begin_submit()
    regressions, status = session._plan_baseline_check()
    assert regressions == () and status == "unknown"
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    assert adapter.phase == "IMPLEMENT"
    assert verify_event_journal(adapter.store.path).valid


def test_the_journal_records_the_plan_without_a_new_schema(tmp_path, graph):
    """New event names are free; a new schema string is rejected upstream."""
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    adapter.store.append("persistent_plan_built", **adapter.persistent_plan.counts())
    rows = _journal(adapter)
    names = {row["event"] for row in rows}
    assert "persistent_plan_predicates" in names
    assert "persistent_plan_built" in names
    for row in rows:
        assert row["schema"] == "gt.event.v1"


def test_the_plan_never_counts_as_a_delivery(tmp_path, graph):
    """The receipt census counts two row names; the plan must not add to them."""
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    rows = _journal(adapter)
    census = [
        row
        for row in rows
        if row["event"] in {"evidence_delivery", "context_addition_delivery"}
    ]
    assert census == []


def test_final_state_declares_the_plan(tmp_path, graph):
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    adapter.note_persistent_plan_bootstrap()
    state = adapter.final_state()
    assert state["persistent_plan_bootstrap_calls"] == 1
    assert state["persistent_plan_status"] in {"READY", "PARTIAL"}
    assert state["unmet_plan_rows"]


def test_green_predicates_with_unverified_rows_are_not_verified(tmp_path, graph):
    """The gate-one aiomonitor defect, replayed on the real objects.

    Every mapped predicate was GREEN from bound-check evidence, so
    ``unmet_plan_rows`` was empty and ``verified`` minted True -- while the
    row ledger read all-UNVERIFIED at the submission's revision and the gate
    recorded ``completion_proven: false``. The submitted tree was never
    re-checked, so ``verified`` was a stale-evidence claim.
    """
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    adapter.start_task()
    for predicate_id in adapter.predicates:
        adapter.record_receipt(
            predicate_id, "assertion fixture", 0, "ok",
            epoch=adapter.workspace_epoch, status="GREEN", semantic=True,
        )
    adapter.begin_verify()
    adapter.begin_submit()
    adapter.submit_decision()

    state = adapter.final_state()
    assert state["phase"] == "FINISHED"
    assert state["unmet_predicates"] == []
    assert state["verified"] is False
    assert state["unverified_plan_rows"] == [
        row.row_id for row in plan.rows]


def test_current_revision_proven_rows_permit_verified(tmp_path, graph):
    """verified is still reachable -- when the row ledger agrees."""
    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    adapter.start_task()
    adapter._process_row_observations = {
        row.row_id: {"state": "PROVEN", "source_revision": adapter.repository_revision}
        for row in plan.rows
    }
    for predicate_id in adapter.predicates:
        adapter.record_receipt(
            predicate_id, "assertion fixture", 0, "ok",
            epoch=adapter.workspace_epoch, status="GREEN", semantic=True,
        )
    adapter.begin_verify()
    adapter.begin_submit()
    adapter.submit_decision()

    state = adapter.final_state()
    assert state["verified"] is True
    assert state["unverified_plan_rows"] == []


def test_the_gate_is_consulted_before_the_command_runs(tmp_path, graph):
    """A refusal after execution would journal a decision and change nothing.

    ``miniswe_runtime`` refuses to suppress an already-executed action and its
    native ``Submitted`` terminal, and is right to. So the gate must sit on the
    pre-execution branch of ``_run_submit_gate``; this pins that it does.
    """
    import ast
    import inspect

    from gt_engine import miniswe_runtime

    source = inspect.getsource(miniswe_runtime)
    tree = ast.parse(source)
    gate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_submit_gate"
    )
    calls = [
        node
        for node in ast.walk(gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "plan_submit_gate"
    ]
    assert calls, "the plan gate is not consulted in _run_submit_gate"

    # and it must be reached only on the pre-execution branch
    guards = [
        node
        for node in ast.walk(gate)
        if isinstance(node, ast.If)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "plan_submit_gate"
            for inner in ast.walk(node)
        )
    ]
    assert any(
        isinstance(guard.test, ast.Name) and guard.test.id == "pre_execution"
        for guard in guards
    ), "the plan gate must be on the pre_execution branch"


def test_a_refused_submit_leaves_the_lifecycle_editable(tmp_path, graph):
    """After a refusal the agent must be able to keep working."""
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = GTSession(
        GTSessionConfig(task_id="plan-int", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )

    class _Agent:
        class config:
            wall_time_limit_seconds = 5100
            step_limit = 300

        _start_time = __import__("time").time()
        n_calls = 5

    session._plan_agent = _Agent()
    adapter.start_task()
    assert session.plan_submit_gate() is False
    assert adapter.phase == "IMPLEMENT"
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    adapter.note_edit(["mod.py"])


def test_the_carried_snapshot_is_dropped_before_a_submit():
    """A suite run by the submit gate can write; the carry must not survive it.

    capture_workspace ran twice per action, 601 times on one measured task at
    1.08s each. The two describe the same tree, so the post-image is reused as
    the next pre-image -- but a stale pre-image would attribute one action's
    edit to the next, or lose it, and a lost edit means a missed epoch bump.
    """
    import ast
    import inspect

    from gt_engine import miniswe_runtime

    source = inspect.getsource(miniswe_runtime)

    assert "carried_snapshot" in source
    # reused rather than recaptured
    assert "pre_snapshot = (carried_snapshot" in source
    assert "carried_check_generation ==" in source
    # refreshed from every post-image
    assert "post_snapshot if not snapshot_carry_disabled" in source
    # and dropped when GT itself may run the repository's suite
    assert "carried_snapshot = None" in source

    tree = ast.parse(source)
    assigns = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "carried_snapshot"
                for t in node.targets
            )
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "carried_snapshot"
        )
    ]
    # declaration, post-image refresh, and at least one invalidation
    assert len(assigns) >= 3

    nonlocals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Nonlocal) and "carried_snapshot" in node.names
    ]
    assert nonlocals, "the carry must be shared across actions, not per call"


def _cursor_session(tmp_path, graph, repo, adapter):
    session = GTSession(
        GTSessionConfig(task_id="cursor", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )
    # Advisory mode is already model-visible; assert it rather than assume it,
    # because a cursor that never reaches the model is the failure under test.
    assert session.model_visible
    return session


def test_the_cursor_is_delivered_to_the_tail_by_the_real_session(tmp_path, graph):
    """The steering line has to arrive, not merely render.

    Everything else about the cursor is tested on the renderer in isolation. The
    thing that decides whether an agent ever sees it is this path: the session
    building a candidate, admitting it through the delivery lane, and returning
    it as a context addition on a real ``before_model``.
    """
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    session = _cursor_session(tmp_path, graph, repo, adapter)

    batch = session.before_model([{"role": "user", "content": "task"}], 1)
    cursor = [text for text in batch.context_additions if "[GT_PLAN_CURSOR]" in text]
    assert len(cursor) == 1
    assert any(row.row_id in cursor[0] for row in plan.rows)
    assert "pytest -q" in cursor[0]


def test_an_unchanged_world_does_not_repeat_the_cursor(tmp_path, graph):
    """Fired on change, not on a clock: a second identical turn adds nothing."""
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = _cursor_session(tmp_path, graph, repo, adapter)

    assert any("[GT_PLAN_CURSOR]" in text
               for text in session.before_model([{"role": "user", "content": "t"}], 1).context_additions)
    again = session.before_model([{"role": "user", "content": "t"}], 2)
    assert not any("[GT_PLAN_CURSOR]" in text for text in again.context_additions)


def test_a_moved_row_state_re_emits_the_cursor_superseding_the_last(tmp_path, graph):
    """One cursor at the tail, replaced -- never a growing pile of them."""
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)
    session = _cursor_session(tmp_path, graph, repo, adapter)
    session.before_model([{"role": "user", "content": "t"}], 1)

    remaining = [row.row_id for row in plan.rows][1:]
    assert remaining
    adapter.unmet_plan_rows = lambda: tuple(remaining)
    moved = session.before_model([{"role": "user", "content": "t"}], 2)
    cursor = [text for text in moved.context_additions if "[GT_PLAN_CURSOR]" in text]
    assert len(cursor) == 1
    assert remaining[0] in cursor[0]

    rows = _journal(adapter)
    prepared = [row for row in rows
                if row["event"] == "decision_context_unit_prepared"
                and row.get("supersession_key") == "plan_cursor:task"]
    assert len(prepared) == 2
    assert prepared[0]["unit_id"] != prepared[1]["unit_id"]


def test_an_abstained_plan_delivers_no_cursor(tmp_path, graph):
    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    adapter.persistent_plan = build_plan(None, inputs)
    adapter.persistent_plan.rows = ()
    session = _cursor_session(tmp_path, graph, repo, adapter)
    batch = session.before_model([{"role": "user", "content": "t"}], 1)
    assert not any("[GT_PLAN_CURSOR]" in text for text in batch.context_additions)


def test_final_state_carries_the_gates_completion_verdict(tmp_path, graph, monkeypatch):
    """The gate's receipt has to reach the terminal, or it decides nothing.

    `persistent_plan/gate.py` computes `completion_proven` against the
    sighted-baseline whitelist and `GTSession.plan_submit_gate` journals it as
    a `plan_gate_decision` row -- and it had no non-test consumer. The run then
    named its terminal `submitted_verified` from `verified` alone, which reads
    predicate status and the row ledger and never looks at the baseline. This
    is the wire: the last decision comes back off the journal onto the final
    state, and the runner's terminal is computed from both facts.
    """
    from scripts.miniswe_gt_run import _submission_terminal

    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    _plan_for(inputs, adapter)
    session = GTSession(
        GTSessionConfig(task_id="verdict", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (600.0, 20))
    # A baseline that never produced a conservation verdict: the exact blind
    # shape the whitelist exists to catch.
    monkeypatch.setattr(session, "_plan_baseline_check", lambda: ((), "unknown"))
    monkeypatch.setattr(adapter, "unmet_plan_rows", lambda: ())
    adapter.start_task()
    adapter.begin_verify()
    adapter.begin_submit()

    assert session.plan_submit_gate() is True
    decision = next(
        row for row in reversed(_journal(adapter))
        if row["event"] == "plan_gate_decision"
    )
    assert decision["completion_proven"] is False
    assert decision["baseline_status"] == "unknown"

    state = adapter.final_state()

    assert state["completion_proven"] is False
    assert state["baseline_status"] == "unknown"
    # Every predicate green is not proof of completion when the baseline was
    # blind, so the terminal must not say verified.
    assert _submission_terminal({**state, "verified": True}) == "submitted_unverified"


def test_final_state_reports_no_gate_verdict_when_no_gate_ran(tmp_path, graph):
    """Absence stays absent: a plan-off run must not be relabelled."""
    adapter, _inputs, _contract, _merged, _repo = _built(tmp_path, graph)

    state = adapter.final_state()

    assert state["completion_proven"] is None
    assert state["baseline_status"] == ""


def test_plan_built_after_first_edit_is_typed_post_edit(tmp_path, graph):
    """A plan built after the agent started editing may not claim otherwise.

    `_finalize_startup` builds the plan whenever the async initial index lands,
    with no barrier against the agent editing first - and it routinely does:
    dynaconf 34996816912 recorded the first edit at +157.0 s and
    `persistent_plan_built` at +299.5 s; gitingest 34907273607 at +34.2 s and
    +65.8 s. `PlanInputs.anchors_are_current` then read unknown as current
    (`observed_source_revision` equals `source_revision` at capture, because
    the capture IS the post-edit tree), so `render_plan_block` shipped "Built
    before implementation began ... Every anchor and check below was validated
    against that capture" describing a tree the agent had already changed.

    No barrier is added: delaying the first edit to wait for an index is a
    worse trade than an honest label. The build stays where it is and says
    what it is.
    """
    from gt_engine.persistent_plan.render import render_plan_block

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    contract = extract_task_contract(PROMPT)
    inputs = build_plan_inputs(
        PROMPT, contract=contract, graph_db=graph, repo_root=str(repo),
        source_revision="rev-after-edit", graph_revision="g1",
        capture_baseline=False,
        # What `_finalize_startup` observed on the adapter before it built:
        # two journaled edit transactions, the first of which produced
        # rev-first-edit and touched src/container.py.
        edits_before_build=2,
        first_edit_revision="rev-first-edit",
        pre_build_edited_paths=("src/container.py",),
    )

    assert inputs.built_after_first_edit is True
    assert inputs.first_edit_revision == "rev-first-edit"
    assert inputs.edits_before_build == 2
    # Anchors captured from an already-edited tree are not current, whatever
    # the revision comparison says.
    assert inputs.anchors_are_current is False

    counts = inputs.counts()
    assert counts["built_after_first_edit"] is True
    assert counts["edits_before_plan_build"] == 2
    assert counts["first_edit_revision"] == "rev-first-edit"

    merged = merged_plan_contract(contract, inputs.ledger, PROMPT)
    compiled = compile_obligation_predicates(merged)
    predicates = tuple(
        Predicate(compiled[obligation.obligation_id].predicate_id, obligation.text)
        for obligation in merged.obligations
        if obligation.obligation_id in compiled
    )
    adapter = MiniSweAdapter(
        task_id="post-edit", state_dir=tmp_path / "state", predicates=predicates,
        contract=merged, repo_root=str(repo), graph_db=graph, issue_text=PROMPT,
    )
    adapter.plan_inputs = inputs
    plan = _plan_for(inputs, adapter)

    block = render_plan_block(plan)

    assert "Built before implementation began" not in block
    assert "Context captured before implementation began" not in block
    assert "BUILT AFTER 2 EDIT" in block.upper()
    assert "rev-first-edit" in block
    # Name the anchors the pre-build edits could have moved, not just the fact
    # that some exist: a warning that cannot be acted on is a warning nobody
    # reads.
    assert "src/container.py" in block

    adapter.store.append("persistent_plan_built", **plan.counts())
    row = next(
        item for item in reversed(_journal(adapter))
        if item["event"] == "persistent_plan_built"
    )
    assert row["built_after_first_edit"] is True
    assert row["edits_before_plan_build"] == 2
    assert row["first_edit_revision"] == "rev-first-edit"


def test_plan_built_before_any_edit_keeps_its_claim(tmp_path, graph):
    """The honest pre-edit case must keep saying so."""
    from gt_engine.persistent_plan.render import render_plan_block

    adapter, inputs, _contract, _merged, _repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)

    assert inputs.built_after_first_edit is False
    assert inputs.anchors_are_current is True
    assert inputs.counts()["built_after_first_edit"] is False

    block = render_plan_block(plan)

    assert "Built before implementation began" in block
    assert "BUILT AFTER" not in block.upper()


def test_finalize_startup_observes_the_edits_that_beat_the_index(tmp_path, graph):
    """The count and revision the post-edit label is built from are real.

    `_finalize_startup` cannot know whether the agent edited first unless it
    asks, and the adapter is the only thing that knows: it increments
    `_edit_epoch`, fills `_path_edit_epochs`, and journals one
    `edit_transaction` row per transaction. This is the read that turns those
    into the plan's provenance, so the label cannot drift from the journal.
    """
    from gt_engine.runtime_observation import capture_workspace, diff_workspace
    from scripts.miniswe_gt_run import _edits_observed_so_far

    adapter, _inputs, _contract, _merged, repo = _built(tmp_path, graph)

    # Nothing edited yet: the pre-edit label is still the honest one.
    assert _edits_observed_so_far(adapter) == (0, "", ())

    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    adapter.record_repository_snapshot(
        capture_workspace(repo), boundary="task_start"
    )
    before = capture_workspace(repo)
    (repo / "mod.py").write_text("value = 2\n", encoding="utf-8")
    after = capture_workspace(repo)
    transaction = diff_workspace(before, after, action_id=1, command="edit mod.py")
    adapter.record_edit_transaction(transaction)

    edits, first_revision, paths = _edits_observed_so_far(adapter)

    assert edits == 1
    assert first_revision == str(transaction.post_revision)
    assert "mod.py" in paths
    journaled = [
        row for row in _journal(adapter) if row["event"] == "edit_transaction"
    ]
    assert first_revision == journaled[0]["post_revision"]


def test_an_unmapped_red_predicate_still_blocks_the_gate(tmp_path, graph, monkeypatch):
    """The hole the row census cannot see, on the real objects.

    ``unmet_plan_rows`` censuses rows; ``evaluate_failing_observation``
    reddens any contract obligation a failing check lexically matches, and
    ``_link_obligations`` never promised every obligation a plan row. An
    obligation bound to no row mints a predicate that can go RED without a
    single row reporting it. The gate has to refuse on it directly, under
    its own journaled reason -- otherwise a submission ships over live
    failing evidence.
    """
    from dataclasses import replace

    from gt_engine.miniswe_controller import PredicateStatus
    from gt_engine.task_contract import Obligation
    from gt_engine.verification_contract import compile_obligation_predicates

    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    plan = _plan_for(inputs, adapter)

    # An obligation no ledger row links to: it joins the contract and the
    # predicate set exactly as register_plan_predicates/adopt_startup_plan
    # register derived obligations, but no row's candidates ever name it.
    orphan = Obligation(
        obligation_id="obl-orphan",
        text="the tokenizer handles orphan tokens correctly",
        source="persistent_plan",
    )
    adapter.contract = replace(
        adapter.contract, obligations=adapter.contract.obligations + (orphan,)
    )
    adapter._compiled_predicates = compile_obligation_predicates(adapter.contract)
    adapter._predicate_by_obligation = {
        item.obligation_id: item.predicate_id
        for item in adapter._compiled_predicates.values()
    }
    adapter._obligation_by_predicate = {
        value: key for key, value in adapter._predicate_by_obligation.items()
    }
    orphan_pid = adapter._predicate_by_obligation["obl-orphan"]
    adapter.predicates[orphan_pid] = Predicate(orphan_pid, orphan.text)
    adapter._status[orphan_pid] = PredicateStatus.UNKNOWN
    assert orphan_pid not in {
        key for keys in adapter.plan_row_predicates.values() for key in keys
    }

    # Every row reads PROVEN on the current tree, so the row census is clean
    # and stays clean -- the orphan predicate is not any row's business.
    adapter._process_row_observations = {
        row.row_id: {
            "state": "PROVEN",
            "source_revision": adapter.repository_revision,
        }
        for row in plan.rows
    }

    session = GTSession(
        GTSessionConfig(task_id="unmapped", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )
    session._plan_agent = SimpleNamespace(env=None)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (600.0, 20))

    adapter.start_task()
    reddened = adapter.evaluate_failing_observation(
        "pytest -q tests/test_tokenizer.py",
        "FAILED tests/test_tokenizer.py::test_orphan_tokens - "
        "orphan tokens not handled",
        returncode=1,
        action_index=1,
    )
    assert reddened == (orphan_pid,)
    assert adapter.predicate_status(orphan_pid) is PredicateStatus.RED
    # The premise: the row census genuinely cannot see this failure.
    assert adapter.unmet_plan_rows() == ()

    assert session.plan_submit_gate() is False
    event = next(
        row for row in reversed(_journal(adapter))
        if row["event"] == "plan_gate_decision"
    )
    assert event["reason"] == "unresolved_predicates"
    assert event["unresolved_predicates"] == [orphan_pid]
    # The refusal names the obligation, not the opaque predicate id.
    assert "the tokenizer handles orphan tokens correctly" in adapter.pending_directives[-1]


def test_a_missing_plan_does_not_disarm_the_gate(tmp_path, graph, monkeypatch):
    """Run 35168421439 (cyclotruc): the plan bootstrap died on a provider
    timeout, ``persistent_plan_unavailable`` fired, and ``plan_submit_gate``
    early-returned on the absent plan before the unresolved-predicate
    channel was ever read -- the submit shipped over live RED evidence and
    attestation read ``submitted_unverified``. With no plan the gate must
    still consult RED predicates and still journal the consult."""
    from dataclasses import replace

    from gt_engine.miniswe_controller import PredicateStatus
    from gt_engine.task_contract import Obligation
    from gt_engine.verification_contract import compile_obligation_predicates

    adapter, inputs, _contract, _merged, repo = _built(tmp_path, graph)
    # No plan is ever installed: adapter.persistent_plan stays None, the
    # bootstrap-failure shape. The orphan RED predicate is the blocking
    # evidence that remains.
    orphan = Obligation(
        obligation_id="obl-orphan",
        text="the tokenizer handles orphan tokens correctly",
        source="persistent_plan",
    )
    adapter.contract = replace(
        adapter.contract, obligations=adapter.contract.obligations + (orphan,)
    )
    adapter._compiled_predicates = compile_obligation_predicates(adapter.contract)
    adapter._predicate_by_obligation = {
        item.obligation_id: item.predicate_id
        for item in adapter._compiled_predicates.values()
    }
    adapter._obligation_by_predicate = {
        value: key for key, value in adapter._predicate_by_obligation.items()
    }
    orphan_pid = adapter._predicate_by_obligation["obl-orphan"]
    adapter.predicates[orphan_pid] = Predicate(orphan_pid, orphan.text)
    adapter._status[orphan_pid] = PredicateStatus.UNKNOWN
    assert adapter.persistent_plan is None

    session = GTSession(
        GTSessionConfig(task_id="noplan", repo_root=str(repo), mode="advisory"),
        engine=adapter,
    )
    session._plan_agent = SimpleNamespace(env=None)
    monkeypatch.setattr(session, "plan_gate_budget", lambda: (600.0, 20))

    adapter.start_task()
    reddened = adapter.evaluate_failing_observation(
        "pytest -q tests/test_tokenizer.py",
        "FAILED tests/test_tokenizer.py::test_orphan_tokens - "
        "orphan tokens not handled",
        returncode=1,
        action_index=1,
    )
    assert reddened == (orphan_pid,)
    assert adapter.predicate_status(orphan_pid) is PredicateStatus.RED

    assert session.plan_submit_gate() is False
    event = next(
        row for row in reversed(_journal(adapter))
        if row["event"] == "plan_gate_decision"
    )
    assert event["accepted"] is False
    assert event["reason"] == "unresolved_predicates"
    assert event["unresolved_predicates"] == [orphan_pid]
