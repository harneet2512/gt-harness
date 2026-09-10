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

    # ...but it concedes rather than run the task into the deadline with no
    # submission at all, once refusals have stopped buying evidence.
    from gt_engine.persistent_plan.gate import MAX_REFUSALS_WITHOUT_PROGRESS

    for _ in range(MAX_REFUSALS_WITHOUT_PROGRESS):
        session.plan_submit_gate()
    assert session.plan_submit_gate() is True


def test_the_gate_escapes_when_the_budget_is_nearly_gone(tmp_path, graph):
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
    assert session.plan_submit_gate() is True
    rows = _journal(adapter)
    decision = [row for row in rows if row["event"] == "plan_gate_decision"][-1]
    assert decision["escaped"] == "time"


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
    assert session.plan_submit_gate() is True
    assert allowances == [3.0]
    event = next(row for row in reversed(_journal(adapter)) if row["event"] == "plan_gate_decision")
    assert event["reason"] == "budget_escape"
    assert event["remaining_seconds"] == 599.0
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
    assert session.plan_submit_gate() is True
    event = next(row for row in reversed(_journal(adapter)) if row["event"] == "plan_gate_decision")
    assert event["reason"] == "refusals_without_progress"
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
