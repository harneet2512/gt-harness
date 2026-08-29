from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import gt_engine.central_runtime as central_runtime
from gt_engine.central_runtime import (
    CENTRAL_FEATURE_IDS,
    CentralFeatureRuntime,
    ChangeOrigin,
    EvidenceLedger,
    FeatureReceipt,
    FileState,
    InterventionDecision,
    ValidationAuthority,
    ValidationStatus,
    WorkspaceSensor,
    WorkspaceSnapshot,
    WorkspaceTransition,
    classify_change,
    classify_validation_command,
    diff_snapshots,
    explicit_check_commands,
    feature_payload_grounded,
    graph_revision_receipt,
    parse_manifest,
    render_runtime_advisory,
    render_runtime_feedback,
    revision_changed_paths,
    select_declared_check,
    source_revision_of,
    source_revision_receipt,
    task_deliverable_paths,
)
from gt_engine.task_contract import task_external_paths, task_shebang_paths
from scripts.central_feature_census import census


def _snapshot(revision: str, **entries: FileState) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(revision=revision, entries=entries, healthy=True)


def test_semantic_source_revision_ignores_filesystem_timestamps():
    left = _snapshot(
        "workspace-a",
        **{
            "src/app.py": FileState(
                "f", 12, "1.0", "1.0", "", digest="a" * 64, content="print('x')\n"
            )
        },
    )
    right = _snapshot(
        "workspace-b",
        **{
            "src/app.py": FileState(
                "f", 12, "99.0", "101.0", "", digest="a" * 64, content="print('x')\n"
            )
        },
    )

    assert source_revision_of(left) == source_revision_of(right)


def test_graph_revision_includes_dependency_and_build_metadata() -> None:
    left = _snapshot(
        "workspace-a",
        **{
            "src/app.py": FileState(
                "f", 12, "1.0", "1.0", "", digest="a" * 64, content="print('x')\n"
            ),
            "pyproject.toml": FileState(
                "f", 20, "1.0", "1.0", "", digest="b" * 64, content="[project]\n"
            ),
        },
    )
    right = _snapshot(
        "workspace-b",
        **{
            "src/app.py": left.entries["src/app.py"],
            "pyproject.toml": FileState(
                "f", 24, "2.0", "2.0", "", digest="c" * 64, content="[project]\nname='x'\n"
            ),
        },
    )

    assert graph_revision_receipt(left).complete is True
    assert graph_revision_receipt(right).complete is True
    assert graph_revision_receipt(left).revision != graph_revision_receipt(right).revision


def test_semantic_source_revision_receipt_fails_closed_when_source_digest_is_missing():
    snapshot = _snapshot(
        "workspace",
        **{"src/app.py": FileState("f", 12, "1.0", "1.0", "")},
    )

    receipt = source_revision_receipt(snapshot)

    assert receipt.complete is False
    assert receipt.source_paths == ("src/app.py",)
    assert receipt.missing_digest_paths == ("src/app.py",)


def test_manifest_and_diff_are_non_git_and_cross_language():
    before = parse_manifest("f\t10\t1.0\t1.0\tsrc/a.js\t\nf\t20\t1.0\t1.0\tmain.c\t\n")
    after = parse_manifest("f\t11\t2.0\t2.0\tsrc/a.js\t\nf\t7\t2.0\t2.0\tjob.cob\t\n")

    transition = diff_snapshots(before, after, action_id=3, command="apply edits")

    assert transition.modified == ("src/a.js",)
    assert transition.created == ("job.cob",)
    assert transition.deleted == ("main.c",)
    assert transition.changed_paths == ("job.cob", "main.c", "src/a.js")


@pytest.mark.asyncio
async def test_large_manifest_bound_does_not_turn_sort_limit_into_sigpipe_failure():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            # ``head`` exits early and makes find return 141 under pipefail.
            # The production command must consume the sorted stream fully.
            if command.startswith("sha256sum"):
                return Result("a" * 64 + "  src/app.py\n")
            if "head -n" in command:
                return Result("", 141)
            assert "awk" in command
            return Result("f\t3\t2.0\t2.0\tsrc/app.py\t\n")

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app")

    assert snapshot.healthy is True
    assert snapshot.entries["src/app.py"].size == 3
    assert "head -n" not in central_runtime._MANIFEST_COMMAND


def test_workspace_manifest_prunes_known_derived_trees_before_entry_bound():
    command = central_runtime._MANIFEST_COMMAND

    assert "-prune" in command
    for directory in (".git", "node_modules", "target", ".venv", "build", "dist"):
        assert f"-name {directory}" in command


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX shell")
def test_workspace_manifest_command_is_shell_parseable_and_prunes_derived_trees(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")

    result = subprocess.run(
        # Production sends the manifest command directly to the task shell at
        # the task workspace.  A login shell may run profile code that changes
        # directory first (Codespaces does), which tests the profile rather
        # than the manifest command.
        ["bash", "-c", central_runtime._MANIFEST_COMMAND],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "src/app.py" in result.stdout
    assert ".git/ignored.txt" not in result.stdout
    assert "node_modules/ignored.js" not in result.stdout


@pytest.mark.asyncio
async def test_sensor_recovery_from_unhealthy_snapshot_rehashes_all_source():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    calls: list[str] = []

    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            if command.startswith("sha256sum"):
                return Result("a" * 64 + "  src/app.py\n")
            return Result("f\t300000\t2.0\t2.0\tsrc/app.py\t\n")

    unhealthy = WorkspaceSnapshot("old", {}, False, "temporary manifest failure")
    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app", previous=unhealthy)

    assert snapshot.healthy is True
    assert snapshot.entries["src/app.py"].digest == "a" * 64
    assert source_revision_receipt(snapshot).complete is True
    assert any(command.startswith("sha256sum") for command in calls)


def test_ctime_detects_same_size_rewrite_even_when_mtime_is_preserved():
    before = _snapshot("r1", **{"same.py": FileState("f", 4, "1.0", "1.0", "")})
    after = _snapshot("r2", **{"same.py": FileState("f", 4, "1.0", "2.0", "")})

    transition = diff_snapshots(before, after, action_id=1, command="rewrite")

    assert transition.modified == ("same.py",)


def test_failed_grounded_check_holds_once_then_fails_open():
    ledger = EvidenceLedger(max_holds=1)
    ledger.record_check("pytest -q", returncode=1, revision="r1", grounded=True)

    first = ledger.submit_decision("r1")
    second = ledger.submit_decision("r1")

    assert first.decision == InterventionDecision.HOLD_ONCE
    assert second.decision == InterventionDecision.PASS


def test_unverified_prose_requirements_fail_open_when_hold_is_not_authorized():
    ledger = EvidenceLedger(max_holds=1)

    for revision in ("r1", "r2"):
        decision = ledger.submit_decision(
            revision,
            plan_partial=True,
            uncovered_obligations=("Implement the algorithm correctly.",),
            validating_evidence_present=False,
            allow_unverified_obligation_hold=False,
        )
        assert decision.decision == InterventionDecision.PASS


def test_passing_rerun_clears_failure_without_revision_change():
    ledger = EvidenceLedger(max_holds=1)
    ledger.record_check("pytest -q", returncode=1, revision="r1", grounded=True)
    ledger.record_check("pytest -q", returncode=0, revision="r1", grounded=True)

    assert ledger.submit_decision("r1").decision == InterventionDecision.PASS


def test_edit_makes_old_failure_stale_and_unrelated_failure_never_blocks():
    ledger = EvidenceLedger(max_holds=1)
    ledger.record_check("pytest -q", returncode=1, revision="r1", grounded=True)
    ledger.record_check("curl bad-host", returncode=1, revision="r2", grounded=False)

    assert ledger.submit_decision("r2").decision == InterventionDecision.PASS


def test_degraded_sensor_disables_hard_decisions():
    ledger = EvidenceLedger(max_holds=1)
    ledger.record_check("pytest -q", returncode=1, revision="r1", grounded=True)

    assert ledger.submit_decision("r1", sensor_healthy=False).decision == (
        InterventionDecision.PASS
    )


def test_healthy_empty_repository_retrieval_is_not_recorded_as_substrate_failure():
    runtime = CentralFeatureRuntime(model_visible=True)

    runtime.record_repository_evidence_status(
        source_revision="s1",
        status="source_backed",
        available=False,
        substrate_ready=True,
        retrieval_disposition="empty",
    )

    applicability = runtime.summary()["feature_applicability"]
    for feature_id in (
        "localization",
        "GT_LOC_RESLOT",
        "def_partition",
        "caller_contract",
    ):
        assert applicability[feature_id]["status"] == "NOT_APPLICABLE"
        assert applicability[feature_id]["lifecycle_state"] == "NOT_APPLICABLE"
        assert applicability[feature_id]["reason_codes"] == ["empty"]


def test_feedback_is_concise_and_contains_no_private_runtime_identifier():
    text = render_runtime_feedback("x" * 1_000)

    assert len(text) <= 320
    assert "groundtruth" not in text.lower()
    assert "gt_" not in text.lower()


def test_advisory_is_complete_or_quiet_never_truncated():
    detail = "Concrete diagnostic: " + ("x" * 200)

    assert render_runtime_advisory(detail, limit=80) == ""
    assert "..." not in render_runtime_advisory("src/app.py:9 SyntaxError", limit=80)


def test_all_seventeen_central_features_have_real_trigger_receipts():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Implement the requested change and run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
    )
    runtime.register_structural_evidence(
        source_revision="r0",
        anchors=(
            {"path": "bottle.py", "line": 10, "symbol": "Bottle"},
            {"path": "tests/test_bottle.py", "line": 20, "symbol": "test_bottle"},
        ),
        definitions=(
            {
                "path": "bottle.py",
                "line": 10,
                "symbol": "Bottle",
                "semantics": "graph_definition",
            },
        ),
        references=(
            {
                "path": "tests/test_bottle.py",
                "line": 20,
                "symbol": "Bottle",
                "semantics": "graph_call_reference",
            },
        ),
        callers=(
            {
                "caller_path": "tests/test_bottle.py",
                "caller_line": 20,
                "caller_symbol": "test_bottle",
                "target_path": "bottle.py",
                "target_symbol": "Bottle",
                "semantics": "graph_recorded",
            },
        ),
        graph_revision="graph-r0",
    )
    runtime.observe_action(
        action_id=1,
        command="rg -n 'Bottle|caller' .",
        output=(
            "bottle.py:10:class Bottle\n"
            "tests/test_bottle.py:20:caller references Bottle; existing registry pattern\n"
        ),
        returncode=0,
        transition=WorkspaceTransition(
            action_id=1,
            command="rg -n 'Bottle|caller' .",
            before_revision="r0",
            after_revision="r0",
        ),
        revision="r0",
    )
    runtime.observe_action(
        action_id=2,
        command="sed -i 's/def f(/def f(x:/' app.py",
        output="def f(x: -> syntax error",
        returncode=0,
        transition=WorkspaceTransition(
            action_id=2,
            command="sed -i 's/def f(/def f(x:/' app.py",
            before_revision="r0",
            after_revision="r1",
            created=("new_module.py",),
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={
                "app.py": "def f(x, y):\n    return x + y\n",
                "new_module.py": "def helper():\n    pass\n",
            },
        ),
        revision="r1",
    )
    runtime.observe_action(
        action_id=3,
        command="pytest -q",
        output="1 failed: Error",
        returncode=1,
        transition=WorkspaceTransition(
            action_id=3,
            command="pytest -q",
            before_revision="r1",
            after_revision="r1",
        ),
        revision="r1",
    )
    runtime.observe_action(
        action_id=4,
        command="pytest -q",
        output="1 failed: Error",
        returncode=1,
        transition=WorkspaceTransition(
            action_id=4,
            command="pytest -q",
            before_revision="r1",
            after_revision="r1",
        ),
        revision="r1",
    )
    runtime.record_syntax(
        action_id=2,
        revision="r1",
        failed=True,
        reason="fixture_syntax_failure",
        path="app.py",
        command="python3 -m py_compile app.py",
        returncode=1,
        diagnostic="SyntaxError",
    )
    runtime.record_submit(action_id=5, revision="r1", refused=True, sensor_healthy=True)

    summary = runtime.summary()
    assert summary["feature_count"] == 18
    assert set(summary["feature_ids"]) == set(CENTRAL_FEATURE_IDS)
    assert all(
        summary["produced_counts"][feature] >= 1
        for feature in set(CENTRAL_FEATURE_IDS) - {"select_catalog"}
    )
    assert summary["produced_counts"]["select_catalog"] == 0
    assert all(item["fresh"] for item in summary["receipts"])
    visible = {item["feature_id"] for item in summary["receipts"] if item["model_visible"]}
    assert visible == {
        "covering_red",
        "newfile_precedent",
        "recovery",
        "signature_delta",
        "submit_refusal",
        "syntax_result",
    }
    assert all(item["payload"].get("message") for item in summary["receipts"])
    by_feature = {item["feature_id"]: item for item in summary["receipts"]}
    assert by_feature["obligations"]["boundary"] == "task_start"
    assert by_feature["def_partition"]["payload"]["definitions"] is True
    assert by_feature["caller_contract"]["boundary"] == "task_start"
    assert by_feature["caller_contract"]["payload"]["callers_verified"] is True
    assert by_feature["signature_delta"]["payload"]["signature_edit"] is True
    assert by_feature["recovery"]["payload"]["repeat_count"] == 2
    assert by_feature["submit_refusal"]["payload"]["refused"] is True


def test_model_guidance_is_one_prioritized_advisory_per_declared_check():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Implement the requested change and run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
    )
    assert runtime.model_feedback() == ""
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assertion error",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
    )
    feedback = runtime.model_feedback()
    assert "Validation failed" in feedback
    assert runtime.model_feedback() == ""
    summary = runtime.summary()
    assert summary["guidance_events"] == 1
    assert summary["guidance_chars"] == len(feedback)


def test_covering_red_rejects_heredoc_text_and_missing_executables():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement the requested change", revision="r0")

    command = "python3 - <<'EOF'\n# Test 1: exploratory probe\nprint('check')\nEOF"
    runtime.observe_action(
        action_id=1,
        command=command,
        output="bash: line 1: python3: command not found",
        returncode=127,
        transition=WorkspaceTransition(1, command, "r0", "r0"),
        revision="r0",
    )

    summary = runtime.summary()
    assert summary["produced_counts"]["covering_red"] == 0
    assert summary["produced_counts"]["GT_HYPOTHESIS"] == 0
    assert runtime.model_feedback() == ""


def test_validation_classifier_handles_timeout_without_matching_probes():
    assert classify_validation_command("timeout 600 pytest -q").is_validation
    classification = classify_validation_command("timeout --signal=TERM 25 python3 -c 'print(1)'")
    assert classification.is_validation is False


def test_validation_classifier_unwraps_javascript_package_executors():
    commands = (
        "npx jest --no-coverage",
        "npm exec -- vitest run",
        "npm x -- mocha test/unit.js",
        "pnpm exec vitest run",
        "yarn exec jest --runInBand",
        "bunx ava test/api.test.js",
    )

    for command in commands:
        classification = classify_validation_command(command)
        assert classification.is_validation is True, command
        assert classification.authority is ValidationAuthority.STANDARD_RUNNER, command


def test_validation_classifier_recognizes_tsc_but_not_introspection_modes():
    assert classify_validation_command("npx tsc --noEmit").is_validation
    assert classify_validation_command("pnpm exec tsc --build").is_validation
    assert classify_validation_command("yarn exec tsc -p tsconfig.json").is_validation

    for command in (
        "npx tsc --help",
        "npx tsc --version",
        "npx tsc --showConfig",
        "npx tsc --init",
        "npx tsc --listFilesOnly",
    ):
        assert classify_validation_command(command).is_validation is False, command


def test_javascript_test_runner_introspection_does_not_claim_validation():
    for command in (
        "npx jest --help",
        "npx jest --version",
        "npx jest --listTests",
        "npx vitest --help",
    ):
        assert classify_validation_command(command).is_validation is False, command


def test_dynamic_package_executor_and_source_text_never_claim_validation():
    for command in (
        "npm exec --call 'jest --runInBand'",
        "npx -c 'vitest run'",
        "cat package.json | grep jest",
        "python3 - <<'PY'\nprint('npx jest --no-coverage')\nPY",
    ):
        assert classify_validation_command(command).is_validation is False, command


def test_javascript_validator_pipeline_remains_unattributed_without_pipefail():
    classification = classify_validation_command("npx jest --no-coverage | tail -20").with_result(
        result_code=0,
        output="1 failed\n",
        source_revision="s1",
        workspace_revision="w1",
    )

    assert classification.is_validation is True
    assert classification.status is ValidationStatus.UNKNOWN
    assert classification.status_attributed is False


def test_first_standard_runner_failure_is_controller_only_not_duplicate_guidance():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement the requested change", revision="r0")
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assertion error",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
    )

    receipt = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "covering_red"
    )
    assert receipt["payload"]["command_class"] == "recognized_validation"
    assert receipt["payload"]["validation_authority"] == "standard_runner"
    assert receipt["payload"]["failure_kind"] == "validation_failure"
    assert receipt["model_visible"] is False
    assert runtime.model_feedback() == ""


def test_custom_probe_failure_never_claims_required_check_or_reaches_model():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Demonstrate the browser behavior.", revision="r0")
    command = "python3 /tmp/test_single.py"

    runtime.observe_action(
        action_id=1,
        command=command,
        output="UnexpectedAlertPresentException: alert proved the exploit fired",
        returncode=1,
        transition=WorkspaceTransition(1, command, "r0", "r0"),
        revision="r0",
    )

    summary = runtime.summary()
    classification = classify_validation_command(command)
    assert classification.authority is ValidationAuthority.CUSTOM_PROBE
    covering = next(row for row in summary["receipts"] if row["feature_id"] == "covering_red")
    assert covering["model_visible"] is False
    assert covering["payload"]["validation_authority"] == "custom_probe"
    assert summary["produced_counts"]["submit_refusal"] == 0
    assert summary["produced_counts"]["GT_SS_SUBMIT_RED"] == 0
    assert summary["required_check_claims_without_declared_id"] == 0
    assert runtime.model_feedback() == ""


def test_declared_check_failure_remains_grounded_and_visible():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision="r0", explicit_checks=("pytest -q",))
    classification = classify_validation_command("pytest -q", ("pytest -q",))
    assert classification.authority is ValidationAuthority.DECLARED

    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assertion error",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
    )

    summary = runtime.summary()
    covering = next(row for row in summary["receipts"] if row["feature_id"] == "covering_red")
    assert covering["model_visible"] is True
    assert covering["payload"]["validation_authority"] == "declared"
    assert covering["payload"]["declared_check_id"] == "pytest -q"
    required_claims = [
        row
        for row in summary["receipts"]
        if row["feature_id"] in {"submit_refusal", "GT_SS_SUBMIT_RED"}
    ]
    assert required_claims
    assert all(row["payload"]["declared_check_id"] == "pytest -q" for row in required_claims)
    assert summary["produced_counts"]["submit_refusal"] == 1
    assert summary["required_check_claims_without_declared_id"] == 0
    assert summary["redundant_provider_payloads"] == 0
    assert "Validation failed" in runtime.model_feedback()


def test_literal_timeout_wrapper_preserves_declared_check_authority():
    classification = classify_validation_command(
        "env CI=1 timeout 90s pytest -q",
        ("pytest -q",),
    )

    assert classification.authority is ValidationAuthority.DECLARED
    assert classification.declared_check_id == "pytest -q"
    assert classification.executable == "pytest"
    assert classification.requested_timeout_sec == 90.0


def test_declared_validator_identity_ignores_descriptor_redirection():
    classification = classify_validation_command(
        "cd /app && timeout 900 python3 benchmark.py 2>&1",
        ("python3 benchmark.py",),
    )

    assert classification.authority is ValidationAuthority.DECLARED
    assert classification.declared_check_id == "python3 benchmark.py"
    assert classification.executable == "python3"
    assert classification.requested_timeout_sec == 900.0
    assert classification.validator_segment_index == 1


def test_declared_validator_identity_ignores_file_output_redirection():
    classification = classify_validation_command(
        "cd /app && python3 benchmark.py >/tmp/benchmark.log 2>&1",
        ("python3 benchmark.py",),
    )

    assert classification.authority is ValidationAuthority.DECLARED
    assert classification.declared_check_id == "python3 benchmark.py"
    assert classification.executable == "python3"


def test_recovery_requires_the_same_validation_command_and_failure():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement the requested change", revision="r0")
    for action_id, command in enumerate(("pytest -q", "python -m pytest"), start=1):
        runtime.observe_action(
            action_id=action_id,
            command=command,
            output="1 failed: assertion error",
            returncode=1,
            transition=WorkspaceTransition(action_id, command, "r0", "r0"),
            revision="r0",
        )

    summary = runtime.summary()
    assert summary["produced_counts"]["covering_red"] == 2
    assert summary["produced_counts"]["recovery"] == 0


def test_model_guidance_excludes_passes_non_actionable_receipts_and_repeats():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement the requested change", revision="r0")

    # Obligations are already present in the task prompt and must stay receipt-only.
    assert runtime.model_feedback() == ""

    # A passing syntax check must never tell the model to repair syntax.
    runtime.record_syntax(
        action_id=1,
        revision="r1",
        failed=False,
        reason="changed_file_syntax_pass",
    )
    assert runtime.model_feedback() == ""

    runtime.record_syntax(
        action_id=2,
        revision="r2",
        failed=True,
        reason="changed_file_syntax_failure",
        path="app.py",
        command="python3 -m py_compile app.py",
        returncode=1,
        diagnostic="SyntaxError",
    )
    assert "Syntax check failed" in runtime.model_feedback()

    # Repeating the same advisory at the same workspace revision adds context
    # without adding evidence, so it must be coalesced.
    runtime.record_syntax(
        action_id=3,
        revision="r2",
        failed=True,
        reason="changed_file_syntax_failure",
    )
    assert runtime.model_feedback() == ""

    summary = runtime.summary()
    assert summary["guidance_events"] == 1
    assert summary["guidance_candidates"] == 1
    assert summary["guidance_suppressed"] == 0
    assert summary["feature_delivery_disposition_counts"]["private_ineligible"] >= 3
    assert summary["guidance_by_feature"] == {"syntax_result": 1}


def test_validation_debt_is_grounded_once_and_resets_after_a_real_check():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
    )
    for action_id, revision in ((1, "r1"), (2, "r2"), (3, "r3")):
        runtime.observe_action(
            action_id=action_id,
            command=f"write source {action_id}",
            output="",
            returncode=0,
            transition=WorkspaceTransition(
                action_id, "write", "r0", revision, modified=("app.py",)
            ),
            revision=revision,
        )
    feedback = runtime.model_feedback()
    assert "Unvalidated authored changes" in feedback
    assert "pytest -q" in feedback
    receipt = next(
        row
        for row in runtime.summary()["receipts"]
        if row["feature_id"] == "GT_EDIT_CHECK" and row["action"] == 3
    )
    assert receipt["payload"]["intervention"] == "validation_debt"
    assert receipt["model_visible"] is True

    runtime.observe_action(
        action_id=4,
        command="pytest -q",
        output="1 passed",
        returncode=0,
        transition=WorkspaceTransition(4, "pytest -q", "r3", "r3"),
        revision="r3",
    )
    assert runtime._unvalidated_material_edits == 0
    assert runtime._validation_debt_notified is False


def test_cache_artifacts_do_not_count_as_material_engine_edits():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision="r0", explicit_checks=("pytest -q",))
    for action_id in (1, 2):
        runtime.observe_action(
            action_id=action_id,
            command="python3 -c 'pass'",
            output="",
            returncode=0,
            transition=WorkspaceTransition(
                action_id,
                "python3 -c",
                "r0",
                f"r{action_id}",
                created=("__pycache__/app.cpython-313.pyc",),
            ),
            revision=f"r{action_id}",
        )
    assert runtime.model_feedback() == ""
    assert runtime.summary()["action_metrics"]["workspace_change_actions"] == 0


def test_explicit_verifier_path_is_a_grounded_check_without_an_interpreter():
    checks = explicit_check_commands("You can run /app/test_outputs.py to verify.")

    assert checks == ("/app/test_outputs.py",)
    assert classify_validation_command("python3 /app/test_outputs.py", checks).grounded


def test_declared_verifier_text_in_a_read_is_not_a_validation_result():
    checks = ("/app/test_outputs.py",)

    classification = classify_validation_command("cat /app/test_outputs.py", checks)

    assert classification.is_validation is False
    assert classification.grounded is False
    assert classification.status is ValidationStatus.UNKNOWN


def test_trailing_reporter_prevents_validator_status_attribution():
    classification = classify_validation_command("pytest -q; echo done").with_result(
        result_code=0,
        output="1 failed\ndone\n",
        source_revision="s1",
        workspace_revision="w1",
    )

    assert classification.is_validation is True
    assert classification.status is ValidationStatus.UNKNOWN
    assert classification.status_attributed is False
    assert classification.failure_kind == ""


def test_background_validation_is_pending_not_passing():
    classification = classify_validation_command("pytest -q &").with_result(
        result_code=0,
        output="",
        source_revision="s1",
        workspace_revision="w1",
    )

    assert classification.is_validation is True
    assert classification.status is ValidationStatus.PENDING
    assert classification.status_attributed is False


def test_pipeline_reporter_does_not_borrow_validator_status_without_pipefail():
    classification = classify_validation_command("pytest -q | tee /tmp/results.txt").with_result(
        result_code=0,
        output="1 failed\n",
        source_revision="s1",
        workspace_revision="w1",
    )

    assert classification.is_validation is True
    assert classification.status is ValidationStatus.UNKNOWN
    assert classification.status_attributed is False


def test_terminal_validator_owns_the_action_result():
    classification = classify_validation_command("python3 /app/test_outputs.py").with_result(
        result_code=0,
        output="all checks passed\n",
        source_revision="s1",
        workspace_revision="w1",
    )

    assert classification.status is ValidationStatus.PASS
    assert classification.status_attributed is True
    assert classification.validator_segment_index == 0


def test_wrapped_validator_has_one_normalized_invocation_and_literal_timeout():
    classification = classify_validation_command(
        "PYTHONUNBUFFERED=1 timeout 90s python3 -m pytest -q"
    )

    assert classification.is_validation is True
    assert classification.authority is ValidationAuthority.STANDARD_RUNNER
    assert classification.executable == "python3"
    assert classification.requested_timeout_sec == 90.0


def test_explicit_check_parser_keeps_contract_named_build_and_pipe_commands():
    instruction = (
        "To build, run `python3 setup.py build_ext --inplace`, then test using "
        "`python3 benchmark.py`.\n"
        "echo '(+ 7 8)' | python3 interp.py test/calculator.scm"
    )

    checks = explicit_check_commands(instruction)

    assert checks[:2] == ("python3 setup.py build_ext --inplace", "python3 benchmark.py")
    assert checks[2] == "echo '(+ 7 8)' | python3 interp.py test/calculator.scm"


def test_change_and_failure_capabilities_fire_only_at_their_real_boundaries():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision="r0")
    unchanged = WorkspaceTransition(1, "echo error", "r0", "r0")
    runtime.observe_action(
        action_id=1,
        command="echo error",
        output="error is ordinary text",
        returncode=1,
        transition=unchanged,
        revision="r0",
    )
    assert runtime.summary()["produced_counts"]["covering_red"] == 0
    assert runtime.summary()["produced_counts"]["GT_HYPOTHESIS"] == 0

    changed = WorkspaceTransition(2, "write", "r0", "r1", modified=("app.py",))
    runtime.observe_action(
        action_id=2,
        command="write",
        output="",
        returncode=0,
        transition=changed,
        revision="r1",
    )
    summary = runtime.summary()
    assert summary["produced_counts"]["GT_CHANGE_SURFACE"] == 1
    assert summary["produced_counts"]["GT_PATCH_DELTA"] == 1
    assert not next(row for row in summary["receipts"] if row["feature_id"] == "GT_CHANGE_SURFACE")[
        "model_visible"
    ]

    runtime.observe_action(
        action_id=3,
        command="pytest -q",
        output="1 failed: assertion error",
        returncode=1,
        transition=WorkspaceTransition(3, "pytest -q", "r1", "r1"),
        revision="r1",
    )
    summary = runtime.summary()
    assert summary["produced_counts"]["covering_red"] == 1
    assert summary["produced_counts"]["GT_HYPOTHESIS"] == 1


def test_newfile_precedent_is_one_shot_per_task_not_per_created_filename():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Add source modules.", revision="w0", source_revision="s0")
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "src/existing.py": FileState("f", 10, 1.0, 1.0, "h1"),
            "src/first.py": FileState("f", 10, 2.0, 2.0, "h2"),
        },
    )
    runtime.observe_action(
        action_id=1,
        command="touch src/first.py",
        output="",
        returncode=0,
        transition=WorkspaceTransition(
            1,
            "touch src/first.py",
            "w0",
            "w1",
            created=("src/first.py",),
            before_contents={"src/existing.py": "def existing(): pass\n"},
            after_contents={"src/first.py": "def first(): pass\n"},
        ),
        revision="w1",
        source_revision="s1",
        snapshot=snapshot,
    )
    snapshot2 = WorkspaceSnapshot(
        revision="w2",
        healthy=True,
        entries={
            **snapshot.entries,
            "src/second.py": FileState("f", 10, 3.0, 3.0, "h3"),
        },
    )
    runtime.observe_action(
        action_id=2,
        command="touch src/second.py",
        output="",
        returncode=0,
        transition=WorkspaceTransition(
            2,
            "touch src/second.py",
            "w1",
            "w2",
            created=("src/second.py",),
            before_contents={"src/existing.py": "def existing(): pass\n"},
            after_contents={"src/second.py": "def second(): pass\n"},
        ),
        revision="w2",
        source_revision="s2",
        snapshot=snapshot2,
    )

    rows = [
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "newfile_precedent"
    ]
    assert len(rows) == 1
    assert rows[0]["action"] == 1


def test_signature_delta_requires_explicit_before_after_evidence():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.observe_action(
        action_id=1,
        command="tee app.py <<'EOF'\ndef f(x): pass\nEOF",
        output="",
        returncode=0,
        transition=WorkspaceTransition(1, "write", "r0", "r1", created=("app.py",)),
        revision="r1",
    )
    assert runtime.summary()["produced_counts"]["signature_delta"] == 0

    runtime.observe_action(
        action_id=2,
        command="sed -i 's/def f(/def f(x, /' app.py",
        output="",
        returncode=0,
        transition=WorkspaceTransition(2, "edit", "r1", "r2", modified=("app.py",)),
        revision="r2",
    )
    receipt = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "signature_delta"
    )
    assert receipt["payload"]["before_signature"] != receipt["payload"]["after_signature"]


def test_all_eighteen_census_proves_producers_and_consumer_paths():
    result = census()

    # Producer-side proof must hold: all 18 IDs produced a valid, on-boundary,
    # fresh receipt and every guidance window was on time.
    assert result["all_18_producers_proven"] is True
    assert result["all_18_timing_valid"] is True
    assert result["all_guidance_on_time"] is True
    assert set(result["timing_audit"]) == {*CENTRAL_FEATURE_IDS, "_global"}
    assert all(row["valid"] for row in result["timing_audit"].values())
    assert all(
        row["not_predictive"] and row["not_late"] and row["delivered_before_next_decision"]
        for row in result["decision_window_audit"]
    )
    # Every produced receipt routes to a registered consumer with valid
    # timing, and every model-visible payload names concrete evidence.
    assert result["all_18_consumers_proven"] is True
    assert result["all_effects_timing_valid"] is True
    assert result["all_payloads_semantically_grounded"] is True
    assert result["all_18_consumer_paths_proven"] is True
    assert result["effect_window_audit"]
    assert all(
        row["evidence_before_effect"] and row["effect_before_next_action"] and row["non_late"]
        for row in result["effect_window_audit"]
    )


def test_signature_delta_is_coalesced_into_its_first_eligible_edit_frame():
    result = census()

    # signature_delta is produced at the edit boundary with concrete evidence,
    # so it enters the single first-eligible delivery arbitration...
    produced = [row for row in result["receipts"] if row["feature_id"] == "signature_delta"]
    assert produced
    assert all(feature_payload_grounded("signature_delta", row["payload"]) for row in produced)
    # Bounded arbitration uses one provider frame, but includes distinct
    # compatible same-action facts instead of selecting one claim twice or
    # leaking the impact into a later call.
    edit_frame = next(
        row for row in result["decision_window_audit"] if row["feature_id"] == "syntax_result"
    )
    assert "signature_delta" in edit_frame["contributing_features"]
    assert edit_frame["evidence_action"] == 2
    delivered = next(row for row in result["receipts"] if row["feature_id"] == "signature_delta")
    assert delivered["model_visible"] is True
    assert delivered["delivery_status"] == "delivered"
    frame = next(
        row
        for row in result["semantic_decisions"]["frames"]
        if len(row["evidence_actions"]) >= 2 and set(row["evidence_actions"]) == {2}
    )
    assert len(frame["claim_ids"]) == len(set(frame["claim_ids"]))


@pytest.mark.asyncio
async def test_sensor_carries_hash_without_reinventing_an_edit():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                return Result(("b" * 64) + "  app.js\n")
            return Result("f\t3\t2.0\t2.0\tapp.js\t\n")

    sensor = WorkspaceSensor()
    empty = parse_manifest("")
    created = await sensor.scan(Environment(), cwd="/app", previous=empty)
    unchanged = await sensor.scan(Environment(), cwd="/app", previous=created)

    transition = diff_snapshots(created, unchanged, action_id=2, command="pwd")
    assert created.revision == unchanged.revision
    assert transition.changed_paths == ()


@pytest.mark.asyncio
async def test_sensor_captures_source_when_task_image_has_no_python():
    """Source mirroring must not depend on a task-image Python interpreter."""
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    source = "int main(void) { return 0; }\n"
    encoded = base64.b64encode(source.encode()).decode()
    calls: list[str] = []

    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            if command.startswith("sha256sum"):
                return Result(("a" * 64) + "  app.c\n")
            if command.startswith("python3 -c"):
                return Result("bash: python3: command not found\n", 127)
            if "base64" in command:
                return Result(f"app.c\t{encoded}\n")
            return Result("f\t29\t2.0\t2.0\tapp.c\t\n")

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app")

    assert snapshot.healthy is True
    assert snapshot.entries["app.c"].content == source
    assert any(command.startswith("python3 -c") for command in calls)
    assert any("base64" in command for command in calls)


@pytest.mark.asyncio
async def test_sensor_caches_missing_python_capture_backend_across_source_edits():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    encoded = base64.b64encode(b"int value = 1;\n").decode()
    python_calls = 0
    manifest_calls = 0

    class Environment:
        async def exec(self, command, **kwargs):
            nonlocal python_calls, manifest_calls
            if "-printf" in command:
                manifest_calls += 1
                stamp = str(manifest_calls)
                return Result(f"f\t15\t{stamp}.0\t{stamp}.0\tapp.c\t\n")
            if command.startswith("sha256sum"):
                return Result((str(manifest_calls) * 64)[:64] + "  app.c\n")
            if command.startswith("python3 -c"):
                python_calls += 1
                return Result("python3: not found\n", 127)
            if "base64" in command:
                return Result(f"app.c\t{encoded}\n")
            raise AssertionError(command)

    sensor = WorkspaceSensor()
    first = await sensor.scan(Environment(), cwd="/app")
    second = await sensor.scan(Environment(), cwd="/app", previous=first)

    assert second.healthy is True
    assert python_calls == 1


@pytest.mark.asyncio
async def test_sensor_captures_binary_heads_only_on_request():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    elf_head = b"\x7fELF\x02\x01\x01\x00" + bytes(24)
    encoded = base64.b64encode(elf_head).decode()
    source = "int v = 1;\n"
    source_encoded = base64.b64encode(source.encode()).decode()

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                return Result(("a" * 64) + "  app.c\n")
            if command.startswith("python3 -c"):
                if "open(" in command:
                    return Result('{"a.bin": "' + encoded + '"}\n')
                return Result('{"app.c": "' + source_encoded + '"}\n')
            return Result("f\t9\t2.0\t2.0\tapp.c\t\nf\t28\t2.0\t2.0\ta.bin\t\n")

    sensor = WorkspaceSensor()
    without = await sensor.scan(Environment(), cwd="/app")
    with_heads = await sensor.scan(Environment(), cwd="/app", capture_binary_heads=True)

    assert without.binary_heads == {}
    assert with_heads.binary_heads == {"a.bin": elf_head}
    assert with_heads.entries["app.c"].content == source


@pytest.mark.asyncio
async def test_sensor_binary_heads_preserved_across_unchanged_scans():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    elf_head = b"\x7fELF\x02\x01\x01\x00" + bytes(24)
    encoded = base64.b64encode(elf_head).decode()
    capture_calls = 0

    class Environment:
        async def exec(self, command, **kwargs):
            nonlocal capture_calls
            if command.startswith("sha256sum"):
                return Result(("a" * 64) + "  app.c\n")
            if command.startswith("python3 -c") and "open(" in command:
                capture_calls += 1
                return Result('{"a.bin": "' + encoded + '"}\n')
            return Result("f\t9\t2.0\t2.0\tapp.c\t\nf\t28\t2.0\t2.0\ta.bin\t\n")

    sensor = WorkspaceSensor()
    first = await sensor.scan(Environment(), cwd="/app", capture_binary_heads=True)
    second = await sensor.scan(
        Environment(), cwd="/app", previous=first, capture_binary_heads=True
    )

    assert first.binary_heads == {"a.bin": elf_head}
    assert second.binary_heads == {"a.bin": elf_head}
    assert capture_calls == 1


@pytest.mark.asyncio
async def test_sensor_binary_heads_do_not_change_workspace_revision():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    elf_head = b"\x7fELF\x02\x01\x01\x00" + bytes(24)
    encoded = base64.b64encode(elf_head).decode()

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                return Result(("a" * 64) + "  app.c\n")
            if command.startswith("python3 -c") and "open(" in command:
                return Result('{"a.bin": "' + encoded + '"}\n')
            return Result("f\t9\t2.0\t2.0\tapp.c\t\nf\t28\t2.0\t2.0\ta.bin\t\n")

    sensor = WorkspaceSensor()
    plain = await sensor.scan(Environment(), cwd="/app")
    headed = await sensor.scan(Environment(), cwd="/app", capture_binary_heads=True)

    assert plain.revision == headed.revision


@pytest.mark.asyncio
async def test_sensor_binary_head_capture_skips_grader_artifact_names():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    encoded = base64.b64encode(b"\x7fELF" + bytes(24)).decode()

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                lines = []
                for token in command.split("-- ", 1)[-1].split():
                    lines.append(("a" * 64) + "  " + token)
                return Result("\n".join(lines) + "\n")
            if command.startswith("python3 -c") and "open(" in command:
                return Result('{"reward.txt": "' + encoded + '"}\n')
            return Result(
                "f\t9\t2.0\t2.0\tapp.c\t\n"
                "f\t28\t2.0\t2.0\treward.txt\t\n"
                "f\t28\t2.0\t2.0\tctrf.json\t\n"
                "f\t28\t2.0\t2.0\ttest_outputs.py\t\n"
                "f\t28\t2.0\t2.0\tsolution/main.py\t\n"
            )

    snapshot = await WorkspaceSensor().scan(
        Environment(), cwd="/app", capture_binary_heads=True
    )

    assert snapshot.binary_heads == {}
    assert snapshot.healthy is True


@pytest.mark.asyncio
async def test_sensor_binary_head_failure_fails_open():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                return Result(("a" * 64) + "  app.c\n")
            if command.startswith("python3 -c") and "open(" in command:
                return Result("boom\n", 1)
            return Result("f\t9\t2.0\t2.0\tapp.c\t\nf\t28\t2.0\t2.0\ta.bin\t\n")

    snapshot = await WorkspaceSensor().scan(
        Environment(), cwd="/app", capture_binary_heads=True
    )

    assert snapshot.healthy is True
    assert snapshot.binary_heads == {}


@pytest.mark.asyncio
async def test_sensor_captures_allowlisted_external_source_path():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    source = "http {\n    include /etc/nginx/mime.types;\n}\n"
    encoded = base64.b64encode(source.encode()).decode()

    class Environment:
        async def exec(self, command, **kwargs):
            if "find . -xdev" in command:
                return Result("")
            if "find /etc/nginx/nginx.conf" in command:
                return Result("f\t48\t2.0\t2.0\t/etc/nginx/nginx.conf\t\n")
            if command.startswith("sha256sum"):
                return Result(("c" * 64) + "  /etc/nginx/nginx.conf\n")
            if command.startswith("python3 -c"):
                return Result("bash: python3: command not found\n", 127)
            if "base64" in command:
                return Result(f"/etc/nginx/nginx.conf\t{encoded}\n")
            raise AssertionError(command)

    snapshot = await WorkspaceSensor().scan(
        Environment(), cwd="/app", external_paths=("/etc/nginx/nginx.conf",)
    )

    assert snapshot.healthy is True
    assert snapshot.entries["/etc/nginx/nginx.conf"].content == source


@pytest.mark.asyncio
async def test_sensor_probes_external_paths_independently_and_accepts_expected_absence():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        def __init__(self):
            self.external_commands = []

        async def exec(self, command, **kwargs):
            if "find . -xdev" in command:
                return Result("")
            if command.startswith("sha256sum"):
                return Result(("c" * 64) + "  /etc/nginx/nginx.conf\n")
            if command.startswith("python3 -c"):
                return Result("{}\n")
            if "base64" in command:
                return Result("")
            if "/etc/nginx/nginx.conf" in command:
                self.external_commands.append(command)
                return Result("f\t48\t2.0\t2.0\t/etc/nginx/nginx.conf\t\n")
            if "/etc/nginx/conf.d/optional.conf" in command:
                self.external_commands.append(command)
                return Result("")
            raise AssertionError(command)

    environment = Environment()
    snapshot = await WorkspaceSensor().scan(
        environment,
        cwd="/app",
        external_paths=(
            "/etc/nginx/nginx.conf",
            "/etc/nginx/conf.d/optional.conf",
        ),
    )

    assert snapshot.healthy is True
    assert "/etc/nginx/nginx.conf" in snapshot.entries
    assert "/etc/nginx/conf.d/optional.conf" not in snapshot.entries
    assert len(environment.external_commands) == 2
    assert all("test ! -e" in command for command in environment.external_commands)


@pytest.mark.asyncio
async def test_sensor_discovers_bounded_extensionless_shebang_source():
    import base64

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    source = "#!/usr/bin/env python3\nprint('ok')\n"
    encoded = base64.b64encode(source.encode()).decode()

    class Environment:
        async def exec(self, command, **kwargs):
            if command.startswith("sha256sum"):
                return Result(("d" * 64) + "  run-script\n")
            if command.startswith("python3 -c"):
                return Result(f'{{"run-script":"{encoded}"}}\n')
            if "base64" in command:
                return Result(f"run-script\t{encoded}\n")
            return Result("f\t36\t2.0\t2.0\trun-script\t\n")

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app")

    assert snapshot.healthy is True
    assert snapshot.entries["run-script"].content == source

    snapshot = await WorkspaceSensor().scan(
        Environment(), cwd="/app", shebang_paths=("run-script",)
    )

    assert snapshot.entries["run-script"].content == source


@pytest.mark.asyncio
async def test_sensor_captures_all_extensionless_sources_in_one_action():
    import base64
    import json

    source = "#!/bin/sh\necho ok\n"
    encoded = base64.b64encode(source.encode()).decode()
    paths = tuple(f"run-{index}" for index in range(9))

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            if "find . -xdev" in command:
                return Result("".join(f"f\t{len(source)}\t2.0\t2.0\t{path}\t\n" for path in paths))
            if command.startswith("sha256sum"):
                return Result("".join(("d" * 64) + f"  {path}\n" for path in paths))
            if command.startswith("python3 -c"):
                return Result(json.dumps({path: encoded for path in paths}) + "\n")
            raise AssertionError(command)

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app")

    assert snapshot.healthy is True
    assert all(snapshot.entries[path].content == source for path in paths)


@pytest.mark.asyncio
async def test_sensor_exception_fails_open_and_preserves_last_known_revision():
    class Environment:
        async def exec(self, command, **kwargs):
            raise TimeoutError("scan exceeded budget")

    previous = parse_manifest("f\t3\t2.0\t2.0\tapp.js\t\n")
    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app", previous=previous)

    assert snapshot.healthy is False
    assert snapshot.revision == previous.revision
    assert snapshot.entries == previous.entries


@pytest.mark.asyncio
async def test_malformed_manifest_fails_open_without_inventing_deletions():
    class Result:
        stdout = "not-a-valid-manifest\n"
        return_code = 0

    class Environment:
        async def exec(self, command, **kwargs):
            return Result()

    previous = parse_manifest("f\t3\t2.0\t2.0\tapp.js\t\n")
    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/app", previous=previous)

    transition = diff_snapshots(previous, snapshot, action_id=2, command="pwd")
    assert snapshot.healthy is False
    assert transition.changed_paths == ()


def test_task_deliverable_paths_extracts_contract_outputs():
    instruction = (
        "Run `pytest -q` to validate. Write your final report to `report.jsonl` "
        "as a JSON list of findings."
    )

    assert "report.jsonl" in task_deliverable_paths(instruction)
    assert task_deliverable_paths("Create a /app/report.jsonl file.") == ("report.jsonl",)
    assert task_deliverable_paths("just fix the bug") == ()


def test_task_deliverable_paths_uses_wrapped_output_context_and_rejects_inputs():
    instruction = """Your goal is to read requests from
``/app/task_file/input_data/requests_bucket_1.jsonl``
``/app/task_file/input_data/requests_bucket_2.jsonl`` and produce a plan in
``/app/task_file/output_data/plan_b1.jsonl``
``/app/task_file/output_data/plan_b2.jsonl``.

## Deliverables
Generate the two output plan files above. Keep the input files unchanged.
"""

    assert task_deliverable_paths(instruction) == (
        "task_file/output_data/plan_b1.jsonl",
        "task_file/output_data/plan_b2.jsonl",
    )


def test_task_deliverable_paths_does_not_conflate_compressor_input_and_output():
    instruction = (
        "I have a decompressor in /app/decomp. It reads compressed data and a "
        "file /app/data.txt. Write me data.comp such that running "
        "cat data.comp | /app/decomp gives exactly data.txt."
    )

    assert task_deliverable_paths(instruction) == ("data.comp",)


def test_task_external_paths_is_allowlisted_and_deduplicated():
    instruction = (
        "Put the server config in /etc/nginx/conf.d/benchmark-site.conf and "
        "the limit zone in /etc/nginx/nginx.conf. Logs go to "
        "/var/log/nginx/benchmark-access.log. Do not scan /etc/passwd."
    )

    assert task_external_paths(instruction) == (
        "/etc/nginx/conf.d/benchmark-site.conf",
        "/etc/nginx/nginx.conf",
        "/var/log/nginx/benchmark-access.log",
    )


def test_task_shebang_paths_requires_explicit_script_path():
    assert task_shebang_paths("Run the Python script at /app/tools/run-check and inspect it.") == (
        "tools/run-check",
    )
    assert task_shebang_paths("Run /app/decomp against the input file.") == ()


def test_classify_change_never_advances_source_for_artifacts():
    for path, kind in (
        ("benchmark_out.txt", "f"),
        ("callback-test.txt", "f"),
        ("a.out", "f"),
        ("data.comp", "f"),
        ("build/x.o", "f"),
        ("app.so", "f"),
        ("data_8000.pkl", "f"),
        ("weights.npy", "f"),
        ("checkpoint.pt", "f"),
        ("logs/run.log", "f"),
        ("__pycache__/x.pyc", "f"),
        ("build", "d"),
    ):
        change = classify_change(path, kind=kind)
        assert change.validation_relevant is False, path
        assert change.origin != ChangeOrigin.MODEL_AUTHORED, path

    assert classify_change("app.py", kind="f").validation_relevant is True
    assert classify_change("eval.scm", kind="f").validation_relevant is True


def test_report_jsonl_is_a_deliverable_not_source():
    change = classify_change("report.jsonl", kind="f", task_deliverables={"report.jsonl"})

    assert change.origin == ChangeOrigin.TASK_DELIVERABLE
    assert change.validation_relevant is False


def test_code_deliverable_remains_validation_and_graph_source():
    change = classify_change(
        "parallel_linear.py",
        kind="f",
        task_deliverables={"parallel_linear.py"},
        content="def parallel_linear():\n    pass\n",
    )

    assert change.origin == ChangeOrigin.TASK_DELIVERABLE
    assert change.validation_relevant is True
    assert change.graph_indexable is True


def test_private_effects_are_not_counted_as_suppressed_guidance_candidates():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.receipts.extend(
        (
            FeatureReceipt(
                feature_id="GT_CHANGE_SURFACE",
                kind="FACT",
                boundary="edit_result",
                action_id=1,
                revision="w1",
                decision="DELIVERED",
                reason="workspace changed",
                payload={"message": "private change state", "changed_paths": ["app.py"]},
                fresh=True,
                model_visible=False,
                source_revision="s1",
            ),
            FeatureReceipt(
                feature_id="syntax_result",
                kind="FACT",
                boundary="syntax_result",
                action_id=2,
                revision="w2",
                decision="DELIVERED",
                reason="syntax failed",
                payload={
                    "message": "syntax failed",
                    "path": "app.py",
                    "command": "python3 -m py_compile app.py",
                    "returncode": 1,
                    "diagnostic": "SyntaxError",
                },
                fresh=True,
                model_visible=True,
                delivery_status="delivered",
                source_revision="s2",
            ),
        )
    )

    summary = runtime.summary()

    assert summary["guidance_candidates"] == 1
    assert summary["guidance_suppressed"] == 0
    assert summary["feature_delivery_disposition_counts"] == {
        "private_ineligible": 1,
        "candidate_delivered": 1,
        "candidate_represented": 0,
        "candidate_window_unselected": 0,
        "candidate_stale": 0,
        "candidate_budget_rejected": 0,
        "candidate_policy_rejected": 0,
        "no_eligible_model_call": 0,
    }


@pytest.mark.asyncio
async def test_initial_sensor_hashes_all_source_files_beyond_one_batch():
    import base64
    import json
    import shlex

    paths = tuple(f"src/mod_{index:03d}.py" for index in range(125))

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            if "find . -xdev" in command:
                return Result("".join(f"f\t8\t1.0\t1.0\t{path}\t\n" for path in paths))
            if command.startswith("sha256sum"):
                batch = shlex.split(command)[2:]
                return Result("".join(("a" * 64) + f"  {path}\n" for path in batch))
            if command.startswith("python3 -c"):
                batch = shlex.split(command)[3:]
                encoded = base64.b64encode(b"x = 1\n").decode()
                return Result(json.dumps({path: encoded for path in batch}))
            raise AssertionError(command)

    snapshot = await WorkspaceSensor(max_hashes=32).scan(Environment(), cwd="/workspace")
    receipt = source_revision_receipt(snapshot)

    assert snapshot.healthy is True
    assert receipt.complete is True
    assert len(receipt.source_paths) == 125
    assert receipt.missing_digest_paths == ()


@pytest.mark.asyncio
async def test_initial_sensor_hashes_oversized_source_without_inline_capture():
    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    class Environment:
        async def exec(self, command, **kwargs):
            if "find . -xdev" in command:
                return Result("f\t300000\t1.0\t1.0\tlarge.py\t\n")
            if command.startswith("sha256sum"):
                return Result(("b" * 64) + "  large.py\n")
            if command.startswith("python3 -c") or "base64" in command:
                raise AssertionError("oversized source must not be captured inline")
            raise AssertionError(command)

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/workspace")
    receipt = source_revision_receipt(snapshot)

    assert receipt.complete is True
    assert receipt.source_paths == ("large.py",)
    assert snapshot.entries["large.py"].content is None


@pytest.mark.asyncio
async def test_sensor_does_not_put_unconsumed_validation_metadata_in_graph_identity():
    import base64
    import json
    import shlex

    class Result:
        def __init__(self, stdout="", return_code=0):
            self.stdout = stdout
            self.return_code = return_code

    calls: list[str] = []

    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            if "find . -xdev" in command:
                return Result("f\t12\t1.0\t1.0\trequirements.txt\t\n")
            if command.startswith("sha256sum"):
                paths = shlex.split(command)[2:]
                return Result("".join(("c" * 64) + f"  {path}\n" for path in paths))
            if command.startswith("python3 -c"):
                paths = shlex.split(command)[3:]
                encoded = base64.b64encode(b"pytest==8.0\n").decode()
                return Result(json.dumps({path: encoded for path in paths}))
            raise AssertionError(command)

    snapshot = await WorkspaceSensor().scan(Environment(), cwd="/workspace")
    receipt = graph_revision_receipt(snapshot)

    assert snapshot.healthy is True
    assert receipt.complete is True
    assert receipt.source_paths == ()
    assert receipt.entries == ()
    assert snapshot.entries["requirements.txt"].digest == ""
    assert snapshot.entries["requirements.txt"].content is None
    assert not any(command.startswith("sha256sum") for command in calls)


def test_revision_changed_paths_compares_content_addressed_entries():
    before = graph_revision_receipt(
        _snapshot(
            "w1",
            **{
                "src/app.py": FileState("f", 1, "1", "1", "", digest="a" * 64),
                "Cargo.lock": FileState("f", 1, "1", "1", "", digest="b" * 64),
            },
        )
    )
    after = graph_revision_receipt(
        _snapshot(
            "w2",
            **{
                "src/app.py": FileState("f", 2, "2", "2", "", digest="c" * 64),
                "package.json": FileState("f", 1, "2", "2", "", digest="d" * 64),
            },
        )
    )

    assert revision_changed_paths(before, after) == ("package.json", "src/app.py")


def test_source_revision_ignores_artifact_changes_but_not_source_edits():
    base = _snapshot(
        "w0",
        **{
            "app.py": FileState("f", 10, "1.0", "1.0", "", digest="a" * 64),
            "benchmark_out.txt": FileState("f", 5, "1.0", "1.0", "", digest="b" * 64),
        },
    )
    artifact_only = _snapshot(
        "w1",
        **{
            "app.py": FileState("f", 10, "1.0", "1.0", "", digest="a" * 64),
            "benchmark_out.txt": FileState("f", 99, "2.0", "2.0", "", digest="c" * 64),
        },
    )
    source_edit = _snapshot(
        "w2",
        **{
            "app.py": FileState("f", 11, "2.0", "2.0", "", digest="d" * 64),
            "benchmark_out.txt": FileState("f", 5, "1.0", "1.0", "", digest="b" * 64),
        },
    )

    assert source_revision_of(artifact_only) == source_revision_of(base)
    assert source_revision_of(source_edit) != source_revision_of(base)


def test_graph_revision_is_independent_from_nonstructural_validation_files():
    base = _snapshot(
        "w0",
        **{
            "app.py": FileState("f", 10, "1", "1", "", digest="a" * 64),
            "fixtures.json": FileState("f", 10, "1", "1", "", digest="b" * 64),
        },
    )
    config_changed = _snapshot(
        "w1",
        **{
            "app.py": FileState("f", 10, "1", "1", "", digest="a" * 64),
            "fixtures.json": FileState("f", 20, "2", "2", "", digest="c" * 64),
        },
    )
    source_changed = _snapshot(
        "w2",
        **{
            "app.py": FileState("f", 11, "2", "2", "", digest="d" * 64),
            "fixtures.json": FileState("f", 10, "1", "1", "", digest="b" * 64),
        },
    )

    assert graph_revision_receipt(config_changed).revision == graph_revision_receipt(base).revision
    assert graph_revision_receipt(source_changed).revision != graph_revision_receipt(base).revision


def test_validation_debt_does_not_fire_on_artifact_only_changes():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
        task_deliverables={"report.jsonl"},
    )
    for action_id, path in ((1, "benchmark_out.txt"), (2, "report.jsonl"), (3, "build/x.o")):
        runtime.observe_action(
            action_id=action_id,
            command=f"write {path}",
            output="",
            returncode=0,
            transition=WorkspaceTransition(
                action_id, "write", "r0", f"r{action_id}", modified=(path,)
            ),
            revision=f"r{action_id}",
        )

    assert runtime.model_feedback() == ""
    assert runtime.summary()["source_epoch"] == 0
    assert runtime.summary()["action_metrics"]["workspace_change_actions"] == 0


def test_generated_binary_data_cannot_trigger_validation_debt_after_source_edits():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Build then verify with `python3 setup.py build_ext --inplace`.",
        revision="r0",
        explicit_checks=("python3 setup.py build_ext --inplace",),
    )
    for action_id, path in ((1, "app.py"), (2, "app.py"), (3, "data_8000.pkl")):
        runtime.observe_action(
            action_id=action_id,
            command=f"write {path}",
            output="",
            returncode=0,
            transition=WorkspaceTransition(
                action_id, "write", f"r{action_id - 1}", f"r{action_id}", modified=(path,)
            ),
            revision=f"r{action_id}",
        )

    summary = runtime.summary()
    action_three = [row for row in summary["receipts"] if row["action"] == 3]
    surface = next(row for row in action_three if row["feature_id"] == "GT_CHANGE_SURFACE")
    assert surface["payload"]["source_relevant"] == []
    assert not any(row["feature_id"] == "GT_EDIT_CHECK" for row in action_three)
    assert summary["source_epoch"] == 2


def test_validation_debt_selects_highest_priority_declared_check():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Build then verify: run `python3 setup.py build_ext --inplace` and "
        "`python3 /app/verify_solution.py`.",
        revision="r0",
        explicit_checks=(
            "python3 setup.py build_ext --inplace",
            "python3 /app/verify_solution.py",
        ),
    )
    for action_id in (1, 2, 3):
        runtime.observe_action(
            action_id=action_id,
            command=f"write app.py {action_id}",
            output="",
            returncode=0,
            transition=WorkspaceTransition(
                action_id, "write", "r0", f"r{action_id}", modified=("app.py",)
            ),
            revision=f"r{action_id}",
        )

    feedback = runtime.model_feedback()
    assert "/app/verify_solution.py" in feedback


def test_select_declared_check_skips_freshly_passing_checks():
    checks = ("build", "verify")
    assert select_declared_check(checks, {"verify": "passed"}) == "build"
    assert select_declared_check(checks, {"build": "passed", "verify": "passed"}) is None
    assert select_declared_check(checks, {"verify": "stale"}) == "verify"


def test_ledger_records_declared_validation_bound_to_source_revision():
    ledger = EvidenceLedger(max_holds=1)
    source_r = "source-v1"
    classification = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
        result_code=1,
        output="1 failed: assert error",
        source_revision=source_r,
        workspace_revision="workspace-v9",
    )
    ledger.record_check(
        "pytest -q",
        returncode=1,
        revision=source_r,
        grounded=True,
        classification=classification,
    )

    readiness = ledger.readiness_evidence(source_r)
    assert len(readiness) == 1
    assert readiness[0].command_class == "declared_validation"
    assert readiness[0].failure_kind == "validation_failure"
    assert ledger.submit_decision(source_r).decision == InterventionDecision.HOLD_ONCE


def test_attributable_standard_runner_failure_is_a_submit_blocker():
    ledger = EvidenceLedger(max_holds=1)
    classification = classify_validation_command("pytest -q").with_result(
        result_code=1,
        output="tests/test_api.py:12: AssertionError",
        source_revision="source-v1",
        workspace_revision="workspace-v1",
    )

    ledger.record_check(
        "pytest -q",
        returncode=1,
        revision="source-v1",
        grounded=classification.grounded,
        classification=classification,
    )

    assert classification.authority is ValidationAuthority.STANDARD_RUNNER
    assert classification.grounded is False
    assert ledger.readiness_evidence("source-v1")[0].returncode == 1
    assert ledger.submit_decision("source-v1").decision == InterventionDecision.HOLD_ONCE


def test_only_full_project_standard_pass_becomes_readiness_evidence():
    full = classify_validation_command("pytest -q").with_result(
        result_code=0,
        output="12 passed",
        source_revision="source-v1",
        workspace_revision="workspace-v1",
    )
    targeted = classify_validation_command("pytest -q tests/test_api.py").with_result(
        result_code=0,
        output="1 passed",
        source_revision="source-v1",
        workspace_revision="workspace-v1",
    )
    full_ledger = EvidenceLedger()
    targeted_ledger = EvidenceLedger()

    full_ledger.record_check(
        full.command,
        returncode=0,
        revision="source-v1",
        grounded=False,
        classification=full,
    )
    targeted_ledger.record_check(
        targeted.command,
        returncode=0,
        revision="source-v1",
        grounded=False,
        classification=targeted,
    )

    assert full.project_scoped is True
    assert targeted.project_scoped is False
    assert len(full_ledger.readiness_evidence("source-v1")) == 1
    assert targeted_ledger.readiness_evidence("source-v1") == ()


def test_fresh_passing_declared_check_yields_positive_certificate_counts():
    ledger = EvidenceLedger(max_holds=1)
    source_r = "source-v1"
    classification = classify_validation_command("pytest -q", ("pytest -q",))
    ledger.record_check(
        "pytest -q",
        returncode=0,
        revision=source_r,
        grounded=True,
        classification=classification,
    )

    evidence = ledger.readiness_evidence(source_r)
    assert len(evidence) == 1
    assert evidence[0].returncode == 0
    assert ledger.submit_decision(source_r).decision == InterventionDecision.PASS


def test_runtime_validation_log_records_declared_checks():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Run `pytest -q`.",
        revision="r0",
        explicit_checks=("pytest -q",),
    )
    classification = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
        result_code=0,
        output="1 passed",
        source_revision="r0",
        workspace_revision="r0",
    )
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 passed",
        returncode=0,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
        source_revision="r0",
        validation=classification,
    )

    log = runtime.summary()["validation_log"]
    assert len(log) == 1
    assert log[0]["command_class"] == "declared_validation"
    assert log[0]["declared_check_id"] == "pytest -q"
    assert log[0]["result_code"] == 0


def test_repository_project_checks_extend_contract_without_creating_payload():
    runtime = CentralFeatureRuntime(enabled=True, model_visible=True)
    runtime.begin_task(
        "Implement the change.",
        revision="w0",
        source_revision="s0",
        explicit_checks=("python -m unittest",),
    )
    receipts_before = len(runtime.receipts)

    checks = runtime.register_project_checks(("go test ./...", "python -m unittest"))

    assert checks == ("python -m unittest", "go test ./...")
    assert len(runtime.receipts) == receipts_before


def test_effect_timing_consumes_evidence_before_the_next_action():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision="r0")
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assert error",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
    )

    effects = runtime.consume_effects(action_id=1, call=1)

    assert effects
    for effect in effects:
        row = effect.as_dict()
        assert row["evidence_before_effect"] is True
        assert row["effect_before_next_action"] is True
        assert row["non_late"] is True
        # The effect is applied only after the action has produced its
        # evidence.  Same-action delivery is immediate, not predictive.
        assert row["predictive"] is False
        assert row["predecided_actions_executed_after_evidence"] == 0
    # Full 17-ID consumer coverage is proven by the census, not one action.
    assert runtime.summary()["consumer_paths"]


def test_source_epoch_tracks_authored_source_not_arbitrary_workspace_files():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Run `pytest -q` and write solution.py.",
        revision="w0",
        source_revision="s0",
        explicit_checks=("pytest -q",),
        task_deliverables=("solution.py",),
    )
    passed = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
        result_code=0,
        output="1 passed",
        source_revision="s0",
        workspace_revision="w0",
    )
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 passed",
        returncode=0,
        transition=WorkspaceTransition(1, "pytest -q", "w0", "w0"),
        revision="w0",
        source_revision="s0",
        validation=passed,
    )

    # An ordinary non-source workspace artifact must not invalidate a source-
    # bound pass or advance the feature runtime's source epoch.
    notes = FileState("f", 5, "1", "1", "", digest="a" * 64, content="notes")
    runtime.observe_action(
        action_id=2,
        command="printf notes > notes.txt",
        output="",
        returncode=0,
        transition=WorkspaceTransition(
            2,
            "printf notes > notes.txt",
            "w0",
            "w1",
            created=("notes.txt",),
            after_contents={"notes.txt": "notes"},
        ),
        revision="w1",
        source_revision="s0",
        snapshot=_snapshot("w1", **{"notes.txt": notes}),
    )
    summary = runtime.summary()
    assert summary["source_epoch"] == 0
    assert summary["declared_check_states"]["pytest -q"] == "passed"

    # A required deliverable can also be authored source.  Its task role must
    # not prevent the source lifecycle from invalidating the prior pass.
    source = "def solve():\n    return 1\n"
    runtime.observe_action(
        action_id=3,
        command="write solution.py",
        output="",
        returncode=0,
        transition=WorkspaceTransition(
            3,
            "write solution.py",
            "w1",
            "w2",
            created=("solution.py",),
            after_contents={"solution.py": source},
        ),
        revision="w2",
        source_revision="s1",
        snapshot=_snapshot(
            "w2",
            **{
                "notes.txt": notes,
                "solution.py": FileState(
                    "f",
                    len(source),
                    "2",
                    "2",
                    "",
                    digest="b" * 64,
                    content=source,
                ),
            },
        ),
    )
    summary = runtime.summary()
    assert summary["source_epoch"] == 1
    assert summary["declared_check_states"]["pytest -q"] == "stale"


def test_extensionless_source_classification_uses_captured_post_action_bytes():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Repair runner", revision="w0", source_revision="s0")
    source = "#!/usr/bin/env python3\nprint('fixed')\n"
    runtime.observe_action(
        action_id=1,
        command="write runner",
        output="",
        returncode=0,
        transition=WorkspaceTransition(
            1,
            "write runner",
            "w0",
            "w1",
            created=("runner",),
            after_contents={"runner": source},
        ),
        revision="w1",
        source_revision="s1",
        snapshot=_snapshot(
            "w1",
            **{
                "runner": FileState(
                    "f",
                    len(source),
                    "1",
                    "1",
                    "",
                    digest="c" * 64,
                    content=source,
                )
            },
        ),
    )

    change_surface = next(
        row["payload"]
        for row in runtime.summary()["receipts"]
        if row["feature_id"] == "GT_CHANGE_SURFACE"
    )
    assert change_surface["source_relevant"] == ["runner"]
    assert change_surface["origins"]["model_authored"] == 1
    assert runtime.summary()["source_epoch"] == 1


def test_documented_direct_census_entrypoint_is_executable():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/central_feature_census.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ALL_18_CONSUMER_PATHS_PROVEN" in completed.stdout
    assert "ALL_18_TRIGGERS_PROVEN" in completed.stdout
    assert "ALL_18_PAYLOADS_CONCRETE" in completed.stdout
    assert "ALL_18_CONSUMERS_APPLIED" in completed.stdout
    assert "ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST" in completed.stdout
    assert "NO_ACTIONS_BLOCKED" in completed.stdout
    assert "ALL_EFFECTS_CONTEXT_ACCOUNTED" in completed.stdout
    assert "ALL_FEATURE_OPPORTUNITIES_ACCOUNTED" in completed.stdout
    assert "NO_ELIGIBLE_TRIGGER_MISSES" in completed.stdout
    assert "NO_FALSE_FEATURE_FIRES" in completed.stdout
    assert "NO_EMPTY_LOCALIZATION_EFFECTS" in completed.stdout
    assert "NO_UNVERIFIED_CALLERS" in completed.stdout
    assert "NO_DUPLICATE_FRAME_EVIDENCE" in completed.stdout
    assert "REPOSITORY_SUBSTRATE_PROVEN" in completed.stdout
    assert "CERTIFIED_OPPORTUNITY_POLICY_PROVEN" in completed.stdout
    assert "PROVIDER_BASELINE_SHIELD_PROVEN" in completed.stdout
    assert "REPEATED_CONTROL_GATE_PROVEN" in completed.stdout


def test_predecided_actions_are_audited_without_cancellation():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision="r0")
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assert error",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
    )
    effects = runtime.consume_effects(action_id=1, call=1)
    assert all(effect.required_before_action is None for effect in effects)

    runtime.record_predecided_continuation(evidence_action=1, executed=2)

    stamped = next(
        effect for effect in runtime.summary()["effects"] if effect["feature_id"] == "covering_red"
    )
    assert stamped["predecided_actions_cancelled"] == 0
    assert stamped["predecided_actions_executed_after_evidence"] == 2
    assert runtime.summary()["action_metrics"]["batch_interrupts"] == 0


def test_grounded_payloads_require_concrete_evidence():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision="r0", explicit_checks=("pytest -q",))
    classification = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
        result_code=1,
        output="1 failed: assert error at tests/test_app.py:42",
        source_revision="r0",
        workspace_revision="r0",
    )
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="1 failed: assert error at tests/test_app.py:42",
        returncode=1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
        source_revision="r0",
        validation=classification,
    )

    covering = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "covering_red"
    )
    assert covering["model_visible"] is True
    assert feature_payload_grounded("covering_red", covering["payload"]) is True
    assert covering["payload"]["command"] == "pytest -q"
    assert "assert error" in covering["payload"]["diagnostic"]
    assert covering["payload"]["attribution"] == "pytest -q"


def test_empty_failure_diagnostic_never_reaches_model_feedback():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision="r0", explicit_checks=("pytest -q",))
    classification = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
        result_code=-1,
        output="",
        source_revision="r0",
        workspace_revision="r0",
    )
    runtime.observe_action(
        action_id=1,
        command="pytest -q",
        output="",
        returncode=-1,
        transition=WorkspaceTransition(1, "pytest -q", "r0", "r0"),
        revision="r0",
        source_revision="r0",
        validation=classification,
    )

    covering = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "covering_red"
    )
    assert covering["model_visible"] is False
    assert feature_payload_grounded("covering_red", covering["payload"]) is False
    assert runtime.model_feedback() == ""


def test_repeated_grounded_failure_is_explicitly_suppressed_not_orphaned():
    """Semantic deduplication must agree with model-visible delivery accounting."""
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision="r0", explicit_checks=("pytest -q",))
    for action_id in (1, 2):
        classification = classify_validation_command("pytest -q", ("pytest -q",)).with_result(
            result_code=1,
            output="1 failed: assert error",
            source_revision="r0",
            workspace_revision="r0",
        )
        runtime.observe_action(
            action_id=action_id,
            command="pytest -q",
            output="1 failed: assert error",
            returncode=1,
            transition=WorkspaceTransition(action_id, "pytest -q", "r0", "r0"),
            revision="r0",
            source_revision="r0",
            validation=classification,
        )
        runtime.consume_effects(action_id=action_id, call=action_id)
        runtime.model_feedback(for_call=action_id)

    summary = runtime.summary()
    duplicate = [
        row
        for row in summary["effects"]
        if row["feature_id"] == "covering_red" and row["evidence_action"] == 2
    ][0]
    assert duplicate["model_visible"] is False
    assert duplicate["delivery_status"] == "suppressed"
    assert duplicate["delivery_reason"] == "semantic_duplicate"
    delivered_ids = {
        trace["effect_id"] for trace in summary["effect_trace"] if trace["provider_delivery_ids"]
    }
    assert duplicate["receipt_id"] not in delivered_ids
    # The recovery effect is a legitimate next decision candidate and may be
    # pending until the following model turn; only the semantic duplicate is
    # required to be explicitly private rather than a visible orphan.
    assert duplicate["delivery_status"] == "suppressed"
    delivered = next(
        row
        for row in summary["effects"]
        if row["feature_id"] == "covering_red" and row["evidence_action"] == 1
    )
    assert delivered["delivery_status"] == "delivered"


def test_localization_payload_names_concrete_anchors():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Find Bottle", revision="r0")
    runtime.observe_action(
        action_id=1,
        command="rg -n 'class Bottle' .",
        output="bottle.py:10:class Bottle\n",
        returncode=0,
        transition=WorkspaceTransition(1, "rg -n 'class Bottle' .", "r0", "r0"),
        revision="r0",
    )

    receipt = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "localization"
    )
    assert receipt["payload"]["anchors"][0]["path"] == "bottle.py"
    assert receipt["payload"]["anchors"][0]["line"] == 10


def test_signature_delta_payload_names_the_symbol():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.observe_action(
        action_id=2,
        command="sed -i 's/def f(/def f(x, /' app.py",
        output="",
        returncode=0,
        transition=WorkspaceTransition(2, "edit", "r1", "r2", modified=("app.py",)),
        revision="r2",
    )

    receipt = next(
        row for row in runtime.summary()["receipts"] if row["feature_id"] == "signature_delta"
    )
    assert receipt["payload"]["symbol"] == "f"
    assert receipt["payload"]["before_signature"] != receipt["payload"]["after_signature"]
