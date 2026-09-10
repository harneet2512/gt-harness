"""Existing versus proposed: what the plan names that the repository already has.

A plan row names two different kinds of thing and they carry two different
warranties. A SYMBOL that resolved in the graph is a fact about the code at the
captured revision. A CHECK is a command, and the files it names may not exist
yet -- ``pytest tests/test_strict_mode.py`` is a perfectly good plan and a
completely absent file.

Rendering both the same way is the defect these tests pin. The planning call is
free to return ``verification_kind: existing_test`` for a path the repository
does not contain, and nothing checked it; the block then told the agent an
acceptance test existed. An agent that runs it gets a collection error rather
than a red test and cannot tell which of the two it is looking at.
"""
from __future__ import annotations

import sqlite3

import pytest

from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.persistent_plan.render import render_plan_block

PROMPT = (
    "Add a strict mode to the container loader.\n"
    "\n"
    "Assumptions:\n"
    " build_container must accept a registry argument\n"
    " The loader must document its retry behaviour for operators\n"
)


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
        db.executemany(
            "INSERT INTO nodes (id,label,name,qualified_name,file_path,"
            "start_line,signature,is_test,language) VALUES (?,?,?,?,?,?,?,0,?)",
            [(1, "Function", "build_container", "build_container",
              "src/container.py", 10, "def build_container(registry):", "python")],
        )
    return str(path)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_container.py").write_text(
        "def test_container(): assert True\n", encoding="utf-8"
    )
    return root


def _inputs(graph, repo):
    return build_plan_inputs(PROMPT, graph_db=graph, repo_root=str(repo),
                             source_revision="src1", graph_revision="g1",
                             capture_baseline=False)


def _row_id(inputs, needle: str) -> str:
    for row in inputs.ledger.rows:
        if needle in row.text:
            return row.row_id
    raise AssertionError(f"no ledger row containing {needle!r}")


def _plan(graph, repo, **row):
    inputs = _inputs(graph, repo)
    row_id = row.pop("row_id", None) or _row_id(inputs, "build_container")
    payload = {"rows": [{"row_id": row_id, "anchors": [1], **row}]}
    return build_plan(payload, inputs, repo_root=str(repo)), row_id


def test_a_check_naming_an_absent_file_is_recorded_as_proposed(graph, repo):
    plan, row_id = _plan(graph, repo, verification_kind="new_test",
                         verification_command="pytest tests/test_strict_mode.py")
    row = plan.row(row_id)
    assert row.check_basis == "proposed"
    assert row.check_missing_paths == ("tests/test_strict_mode.py",)


def test_a_declared_existing_test_over_an_absent_file_is_corrected(graph, repo):
    plan, row_id = _plan(graph, repo, verification_kind="existing_test",
                         verification_command="pytest tests/test_strict_mode.py")
    row = plan.row(row_id)
    assert row.check_basis == "proposed"
    # The correction is one-directional. A plan may not claim a test exists when
    # the repository does not contain the file that would hold it.
    assert row.verification_kind == "new_test"


def test_a_check_naming_only_present_files_is_recorded_as_existing(graph, repo):
    plan, row_id = _plan(graph, repo, verification_kind="existing_test",
                         verification_command="pytest tests/test_container.py")
    row = plan.row(row_id)
    assert row.check_basis == "existing"
    assert row.check_missing_paths == ()
    assert row.verification_kind == "existing_test"


def test_a_new_case_inside_an_existing_file_stays_a_new_test(graph, repo):
    plan, row_id = _plan(graph, repo, verification_kind="new_test",
                         verification_command="pytest tests/test_container.py::test_strict")
    row = plan.row(row_id)
    # The file is there, so nothing must be written before the command can run,
    # but the case inside it is still new and the plan said so.
    assert row.check_basis == "existing"
    assert row.verification_kind == "new_test"


@pytest.mark.parametrize("command", ["pytest", "pytest -k strict", "go test ./..."])
def test_a_command_naming_no_file_claims_neither_existing_nor_proposed(graph, repo, command):
    plan, row_id = _plan(graph, repo, verification_kind="command",
                         verification_command=command)
    assert plan.row(row_id).check_basis == "unnamed"


def test_a_row_with_no_resolved_symbol_is_recorded_as_unmapped(graph, repo):
    inputs = _inputs(graph, repo)
    anchored = _row_id(inputs, "build_container")
    unmapped = _row_id(inputs, "retry behaviour")
    plan = build_plan({"rows": [
        {"row_id": anchored, "anchors": [1],
         "verification_command": "pytest tests/test_container.py"},
        {"row_id": unmapped, "anchors": [],
         "verification_command": "pytest tests/test_container.py"},
    ]}, inputs, repo_root=str(repo))
    assert plan.row(anchored).symbol_basis == "existing"
    assert plan.row(unmapped).symbol_basis == "unmapped"


def test_counts_separate_existing_proposed_and_unmapped(graph, repo):
    inputs = _inputs(graph, repo)
    anchored = _row_id(inputs, "build_container")
    unmapped = _row_id(inputs, "retry behaviour")
    plan = build_plan({"rows": [
        {"row_id": anchored, "anchors": [1], "verification_kind": "existing_test",
         "verification_command": "pytest tests/test_container.py"},
        {"row_id": unmapped, "anchors": [], "verification_kind": "existing_test",
         "verification_command": "pytest tests/test_strict_mode.py"},
    ]}, inputs, repo_root=str(repo))
    counts = plan.counts()
    assert counts["rows_naming_existing_symbols"] == 1
    assert counts["rows_unmapped_to_any_symbol"] >= 1
    assert counts["rows_with_existing_test_checks"] == 1
    assert counts["rows_with_proposed_test_checks"] == 1


def test_the_rendered_block_says_which_check_does_not_exist_yet(graph, repo):
    plan, row_id = _plan(graph, repo, verification_kind="existing_test",
                         verification_command="pytest tests/test_strict_mode.py")
    block = render_plan_block(plan)
    line = next(x for x in block.splitlines() if "acceptance:" in x)
    assert "tests/test_strict_mode.py" in line
    assert "does not exist yet" in line


def test_the_rendered_block_does_not_call_an_absent_check_an_existing_test(graph, repo):
    plan, _row_id = _plan(graph, repo, verification_kind="existing_test",
                          verification_command="pytest tests/test_strict_mode.py")
    assert "existing_test" not in render_plan_block(plan)


def test_a_revised_command_drops_the_capture_time_classification(tmp_path, graph, repo):
    import hashlib
    import json

    from gt_engine.miniswe_integration import MiniSweAdapter

    plan, row_id = _plan(graph, repo, verification_kind="existing_test",
                         verification_command="pytest tests/test_container.py")
    assert plan.row(row_id).check_basis == "existing"
    adapter = MiniSweAdapter(task_id="revise-basis", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = plan
    inbox = adapter.store.root / "plan" / "requests"
    inbox.mkdir(parents=True)
    (inbox / "revise.json").write_text(json.dumps({
        "plan_digest": hashlib.sha256(plan.canonical_json().encode()).hexdigest(),
        "row_id": row_id, "operation": "revise",
        "value": {"verification_command": "pytest tests/test_other.py"},
    }), encoding="utf-8")
    adapter.apply_plan_requests()
    revised = adapter.persistent_plan.row(row_id)
    assert revised.verification_command == "pytest tests/test_other.py"
    # A classification made at capture does not describe a command written
    # later. Clearing it is deterministic; reclassifying against the edited
    # workspace would not replay to the same digest after a restart.
    assert revised.check_basis == ""
    assert revised.check_missing_paths == ()


def test_a_plan_whose_workspace_moved_says_its_anchors_were_not_revalidated(graph, repo):
    plan, _row_id = _plan(graph, repo, verification_command="pytest tests/test_container.py")
    assert plan.inputs.anchors_are_current
    assert "STALE ANCHORS" not in render_plan_block(plan)
    plan.inputs.observed_source_revision = "src2"
    assert not plan.inputs.anchors_are_current
    block = render_plan_block(plan)
    assert "STALE ANCHORS" in block
    assert "src2" in block and "src1" in block
    assert plan.counts()["anchors_are_current"] is False


def test_an_unobserved_workspace_raises_no_staleness_warning(graph, repo):
    plan, _row_id = _plan(graph, repo, verification_command="pytest tests/test_container.py")
    plan.inputs.observed_source_revision = ""
    # Nothing was observed, so nothing is known to have moved. A warning that
    # fires on every run is a warning nobody reads.
    assert plan.inputs.anchors_are_current
    assert "STALE ANCHORS" not in render_plan_block(plan)
