from __future__ import annotations

from gt_engine.central_runtime import (
    CentralFeatureRuntime,
    FileState,
    WorkspaceSnapshot,
    WorkspaceTransition,
)
from gt_engine.preflight import adapt_proposed_action


def _proposal(command: str):
    return adapt_proposed_action(
        {"command": command, "tool_call_id": "call-search"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )


def _snapshot(*paths: str) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        revision="w0",
        entries={path: FileState("f", 20, "1", "1", "") for path in paths},
        healthy=True,
    )


def _observe(
    runtime: CentralFeatureRuntime,
    *,
    command: str,
    output: str,
    snapshot: WorkspaceSnapshot | None = None,
) -> None:
    runtime.observe_action(
        action_id=1,
        command=command,
        output=output,
        returncode=0,
        transition=WorkspaceTransition(1, command, "w0", "w0"),
        revision="w0",
        source_revision="s0",
        snapshot=snapshot,
        proposed=_proposal(command),
    )


def test_single_target_grep_line_output_becomes_grounded_anchor():
    runtime = CentralFeatureRuntime(model_visible=True)
    _observe(
        runtime,
        command="grep -n 'def greet' src/greeter.py",
        output="10:def greet(name):",
        snapshot=_snapshot("src/greeter.py"),
    )

    localization = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "localization"
    )
    assert localization["payload"]["anchors"] == [
        {"path": "src/greeter.py", "line": 10, "text": "def greet(name):"}
    ]


def test_stdin_filter_is_not_repository_localization():
    runtime = CentralFeatureRuntime(model_visible=True)
    command = "cobc --help 2>&1 | grep -i sequential | head -40"
    _observe(runtime, command=command, output="sequential file organization")

    summary = runtime.summary()
    assert not any(
        row["feature_id"] in {"localization", "GT_LOC_RESLOT"}
        for row in summary["receipts"]
    )
    opportunity = next(
        row
        for row in summary["feature_opportunities"]
        if row["feature_id"] == "localization"
    )
    assert opportunity["evidence_status"] == "correct_abstention"
    assert opportunity["reason_code"] == "stdin_filter_not_repository_search"


def test_empty_or_unparseable_search_never_emits_empty_localization():
    runtime = CentralFeatureRuntime(model_visible=True)
    _observe(
        runtime,
        command="grep -n TODO app.py",
        output="unstructured diagnostic text",
        snapshot=_snapshot("app.py"),
    )

    summary = runtime.summary()
    assert not any(
        row["feature_id"] in {"localization", "GT_LOC_RESLOT"}
        for row in summary["receipts"]
    )
    assert all(
        row["payload"].get("anchors") or row["feature_id"] != "localization"
        for row in summary["receipts"]
    )


def test_non_definition_search_hit_is_never_claimed_as_verified_caller():
    runtime = CentralFeatureRuntime(model_visible=True)
    _observe(
        runtime,
        command="rg -n 'greet' .",
        output=(
            "src/greeter.py:10:def greet(name):\n"
            "README.md:2:Call greet from your application\n"
        ),
        snapshot=_snapshot("src/greeter.py", "README.md"),
    )

    summary = runtime.summary()
    assert not any(row["feature_id"] == "caller_contract" for row in summary["receipts"])
    assert not any(row["feature_id"] == "def_partition" for row in summary["receipts"])


def test_graph_structural_features_require_separate_certified_roles():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.register_structural_evidence(
        source_revision="s0",
        graph_revision="g0",
        anchors=({"path": "src/greeter.py", "line": 10, "symbol": "greet"},),
        definitions=({"path": "src/greeter.py", "line": 10, "symbol": "greet"},),
        references=({"path": "tests/test_greeter.py", "line": 8, "symbol": "greet"},),
        callers=(
            {
                "caller": "test_greet",
                "caller_path": "tests/test_greeter.py",
                "caller_line": 8,
                "target": "greet",
                "target_path": "src/greeter.py",
                "semantics": "graph_recorded",
            },
        ),
    )

    receipts = runtime.summary()["receipts"]
    partition = next(row for row in receipts if row["feature_id"] == "def_partition")
    callers = next(row for row in receipts if row["feature_id"] == "caller_contract")
    assert partition["payload"]["definition_anchors"][0]["symbol"] == "greet"
    assert partition["payload"]["reference_anchors"][0]["path"] == "tests/test_greeter.py"
    assert callers["payload"]["callers"][0]["caller"] == "test_greet"
    assert callers["payload"]["callers_verified"] is True
