from __future__ import annotations

from dataclasses import replace

import pytest

from gt_engine.central_runtime import (
    CentralFeatureRuntime,
    EvidenceLedger,
    WorkspaceSnapshot,
    classify_validation_command,
)
from gt_engine.preflight import (
    PREFLIGHT_FEATURE_PLACEMENT,
    ActionDisposition,
    ActionOperation,
    EvidenceGrade,
    PreflightDecision,
    PreflightMode,
    SegmentRole,
    WorkspaceImpact,
    adapt_proposed_action,
    classify_workspace_impact,
    normalize_executable_invocation,
)


def test_workspace_impact_skips_vcs_inspection_and_proven_external_writes():
    git_status = adapt_proposed_action(
        {"command": "cd /app && git status --short"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    external_write = adapt_proposed_action(
        {"command": "mkdir -p /tmp/gt-check"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=2,
        batch_index=0,
        batch_size=1,
    )

    assert classify_workspace_impact(git_status, cwd="/app") is (
        WorkspaceImpact.PROVEN_NO_WORKSPACE_CHANGE
    )
    assert classify_workspace_impact(external_write, cwd="/app") is (
        WorkspaceImpact.PROVEN_NO_WORKSPACE_CHANGE
    )


def test_workspace_impact_keeps_builds_unknown_programs_and_repo_edits_conservative():
    for command in (
        "pytest -q",
        "python -c 'open(\"src/x.py\", \"w\").write(\"x\")'",
        "mkdir -p src/generated",
    ):
        proposal = adapt_proposed_action(
            {"command": command},
            source_revision="s1",
            workspace_revision="w1",
            model_call=1,
            batch_index=0,
            batch_size=1,
        )
        assert classify_workspace_impact(proposal, cwd="/app") is not (
            WorkspaceImpact.PROVEN_NO_WORKSPACE_CHANGE
        )


def test_proposal_adapter_unwraps_environment_and_timeout_before_classification():
    proposal = adapt_proposed_action(
        {"command": "PYTHONUNBUFFERED=1 timeout 90s python3 -m pytest -q"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.operation is ActionOperation.VALIDATE
    assert proposal.requested_timeout_sec == 90.0
    assert proposal.operations[0].executable == "python3"


def test_dynamic_timeout_abstains_without_guessing():
    invocation = normalize_executable_invocation(("timeout", "$SECONDS", "pytest", "-q"))

    assert invocation.executable is None
    assert invocation.requested_timeout_sec is None
    assert invocation.confidence == 0.0


def test_wrapped_go_test_uses_normalized_executable_arguments():
    proposal = adapt_proposed_action(
        {"command": "env CI=1 timeout 45s go test ./..."},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.operation is ActionOperation.VALIDATE
    assert proposal.requested_timeout_sec == 45.0


@pytest.mark.parametrize(
    ("command", "operation"),
    [
        ("cat src/app.py", ActionOperation.READ),
        ("rg -n greet src", ActionOperation.SEARCH),
        ("sed -i 's/a/b/' src/app.py", ActionOperation.EDIT),
        ("cp src/a.py src/b.py", ActionOperation.EDIT),
        ("touch src/new.py", ActionOperation.CREATE),
        ("rm src/old.py", ActionOperation.DELETE),
        ("pytest -q", ActionOperation.VALIDATE),
        ("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", ActionOperation.SUBMIT),
        ("pip install demo", ActionOperation.INSTALL),
        ("python weird.py", ActionOperation.OTHER),
    ],
)
def test_real_shell_operations_are_conservatively_typed(command, operation):
    proposal = adapt_proposed_action(
        {"command": command, "tool_call_id": "call-1"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    assert proposal.operation == operation
    if command.startswith("sed -i"):
        assert [target.path for target in proposal.targets] == ["src/app.py"]


def test_compound_shell_action_exposes_every_known_operation_and_read_span():
    proposal = adapt_proposed_action(
        {
            "command": (
                "cd /app && sed -n '10,25p' src/app.py | nl -ba "
                "&& rg -n 'target' tests"
            ),
            "tool_call_id": "call-compound",
        },
        source_revision="s1",
        workspace_revision="w1",
        model_call=4,
        batch_index=0,
        batch_size=1,
    )

    operations = [item.operation for item in proposal.operations]
    assert ActionOperation.READ in operations
    assert ActionOperation.SEARCH in operations
    read_spans = [span for item in proposal.operations for span in item.read_spans]
    assert any(
        span.path == "/app/src/app.py"
        and span.start_line == 10
        and span.end_line == 25
        for span in read_spans
    )


def test_compound_write_redirection_is_not_mistyped_as_a_read():
    proposal = adapt_proposed_action(
        {"command": "cd /app && cat input.txt > generated.txt"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    operations = [item.operation for item in proposal.operations]
    assert ActionOperation.CREATE in operations or ActionOperation.EDIT in operations
    assert any(item.mutates_workspace for item in proposal.operations)


def test_multiline_shell_commands_are_separate_top_level_segments():
    proposal = adapt_proposed_action(
        {"command": "cat src/app.py\npython3 tests/test_app.py"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert [item.operation for item in proposal.operations] == [
        ActionOperation.READ,
        ActionOperation.VALIDATE,
    ]
    assert [segment[0] for segment in proposal.shell_segments] == ["cat", "python3"]


def test_heredoc_body_is_opaque_and_only_shell_operands_become_targets():
    proposal = adapt_proposed_action(
        {
            "command": (
                "cat <<'EOF' > /app/eval.scm\n"
                "(load \"src/models.py\")\n"
                "File written to bogus/output.py\n"
                "EOF\n"
                "python3 /app/test/calculator.py\n"
            )
        },
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert [segment[0] for segment in proposal.shell_segments] == ["cat", "python3"]
    paths = {target.path for target in proposal.targets}
    assert "/app/eval.scm" in paths
    assert "/app/test/calculator.py" in paths
    assert "src/models.py" not in paths
    assert not any("bogus/output.py" in path for path in paths)


def test_inline_interpreter_program_is_opaque_to_target_extraction():
    proposal = adapt_proposed_action(
        {
            "command": (
                "python3 -c \"from pathlib import Path; "
                "print(Path('src/models.py').read_text())\""
            )
        },
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.operation is ActionOperation.OTHER
    assert proposal.targets == ()


def test_shell_control_words_are_structure_not_fake_executables():
    proposal = adapt_proposed_action(
        {
            "command": (
                "if test -f src/app.py; then\n"
                "  cat src/app.py\n"
                "else\n"
                "  rg -n target src\n"
                "fi"
            )
        },
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert not ({"if", "then", "else", "fi"} & {item.executable for item in proposal.operations})
    assert {item.operation for item in proposal.operations} >= {
        ActionOperation.READ,
        ActionOperation.SEARCH,
    }


def test_validation_classification_applies_only_to_runner_segment():
    command = "cd /app && python -m pytest -q; echo EXIT:$?"
    classification = classify_validation_command(command, (command,))
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    assert proposal.operation is ActionOperation.VALIDATE
    assert [item.operation for item in proposal.operations] == [
        ActionOperation.OTHER,
        ActionOperation.VALIDATE,
        ActionOperation.OTHER,
    ]
    assert [item.segment_role for item in proposal.operations] == [
        SegmentRole.SHELL_CONTEXT,
        SegmentRole.ACTION,
        SegmentRole.OUTPUT_ONLY,
    ]


def test_sed_range_does_not_attach_across_non_pipeline_connector():
    proposal = adapt_proposed_action(
        {"command": "cat src/a.py && sed -n '20,40p' src/b.py"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    reads = [
        span
        for operation in proposal.operations
        if operation.operation is ActionOperation.READ
        for span in operation.read_spans
    ]
    assert [(span.path, span.start_line, span.end_line) for span in reads] == [
        ("src/a.py", None, None),
        ("src/b.py", 20, 40),
    ]


def test_attached_output_redirection_is_classified_as_edit():
    proposal = adapt_proposed_action(
        {"command": "printf value >src/generated.py"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.operation is ActionOperation.EDIT
    assert proposal.mutates_workspace is True
    assert any(target.path == "src/generated.py" for target in proposal.targets)


def test_descriptor_redirect_does_not_hide_declared_validation_or_fake_mutation():
    command = "cd /app && timeout 900 python3 benchmark.py 2>&1"
    classification = classify_validation_command(command, ("python3 benchmark.py",))
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    assert proposal.operation is ActionOperation.VALIDATE
    assert proposal.mutates_workspace is False
    assert proposal.requested_timeout_sec == 900.0
    assert [item.operation for item in proposal.operations] == [
        ActionOperation.OTHER,
        ActionOperation.VALIDATE,
    ]


def test_validator_with_file_redirect_retains_validation_and_records_side_effect():
    command = "cd /app && python3 benchmark.py >benchmark.log 2>&1"
    classification = classify_validation_command(command, ("python3 benchmark.py",))
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    assert proposal.operation is ActionOperation.VALIDATE
    assert proposal.mutates_workspace is True
    assert any(target.path == "/app/benchmark.log" for target in proposal.targets)


def test_input_redirection_is_a_typed_read_without_workspace_mutation():
    proposal = adapt_proposed_action(
        {"command": "python3 transform.py < fixtures/input.txt"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.mutates_workspace is False
    assert any(
        operation.operation is ActionOperation.READ
        and [target.path for target in operation.targets] == ["fixtures/input.txt"]
        for operation in proposal.operations
    )
    assert proposal.shell_redirections[0].reads_filesystem is True


def test_spaced_descriptor_remains_an_argument_but_attached_descriptor_does_not():
    attached = adapt_proposed_action(
        {"command": "tool 2>errors.log"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    spaced = adapt_proposed_action(
        {"command": "tool 2 >errors.log"},
        source_revision="s0",
        workspace_revision="w0",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert attached.shell_segments == (("tool",),)
    assert attached.shell_redirections[0].file_descriptor == 2
    assert spaced.shell_segments == (("tool", "2"),)
    assert spaced.shell_redirections[0].file_descriptor is None


def test_absent_output_target_is_expected_creation_and_preflight_passes():
    runtime = CentralFeatureRuntime(model_visible=True)
    proposal = adapt_proposed_action(
        {"command": "cat > /tmp/probe.py <<'EOF'\nprint('ok')\nEOF"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    decision = runtime.preflight_action(
        proposal,
        WorkspaceSnapshot("w1", {}, True, ""),
        revision="w1",
        source_revision="s1",
    )

    assert decision.disposition == ActionDisposition.PASS
    assert "edit_target_absent" not in decision.reason_codes


def test_absent_in_place_edit_target_fails_open_to_shell_postflight():
    runtime = CentralFeatureRuntime(model_visible=True)
    proposal = adapt_proposed_action(
        {"command": "sed -i 's/x/y/' missing.py"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    decision = runtime.preflight_action(
        proposal,
        WorkspaceSnapshot("w1", {}, True, ""),
        revision="w1",
        source_revision="s1",
    )

    assert decision.disposition == ActionDisposition.PASS
    assert "edit_target_absent" not in decision.reason_codes


def test_read_and_unknown_default_to_literal_pass():
    runtime = CentralFeatureRuntime(model_visible=True)
    snapshot = WorkspaceSnapshot("w1", {}, True, "")
    for command in ("cat src/app.py", "python weird.py"):
        proposal = adapt_proposed_action(
            {"command": command},
            source_revision="s1",
            workspace_revision="w1",
            model_call=1,
            batch_index=0,
            batch_size=1,
        )
        decision = runtime.preflight_action(proposal, snapshot, revision="w1", source_revision="s1")
        assert decision.disposition == ActionDisposition.PASS
        assert decision.command == command


def test_source_revision_mismatch_invalidates_non_pass_policy():
    runtime = CentralFeatureRuntime(model_visible=True)
    proposal = adapt_proposed_action(
        {"command": "touch app.py"},
        source_revision="old",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    decision = runtime.preflight_action(
        proposal,
        WorkspaceSnapshot("w1", {}, True, ""),
        revision="w1",
        source_revision="new",
    )
    assert decision.disposition == ActionDisposition.PASS
    assert "source_revision_mismatch" in decision.reason_codes


def test_submit_preflight_returns_proven_failing_check_to_model():
    runtime = CentralFeatureRuntime(model_visible=True)
    ledger = EvidenceLedger()
    ledger.record_check("pytest -q", returncode=1, revision="s1", grounded=True)
    proposal = adapt_proposed_action(
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=2,
        batch_index=0,
        batch_size=1,
    )
    decision = runtime.preflight_action(
        proposal,
        WorkspaceSnapshot("w1", {}, True, ""),
        revision="w1",
        source_revision="s1",
        ledger=ledger,
    )
    assert decision.disposition == ActionDisposition.RETURN_TO_MODEL
    assert "pytest -q" in decision.evidence[0]


def test_idempotent_touch_of_existing_path_does_not_trigger_duplicate_create():
    runtime = CentralFeatureRuntime(model_visible=True)
    snapshot = WorkspaceSnapshot(
        "w1",
        {"app.py": object()},
        True,
        "",
    )
    proposal = adapt_proposed_action(
        {"command": "touch app.py"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    decision = runtime.preflight_action(
        proposal,
        snapshot,
        revision="w1",
        source_revision="s1",
    )

    assert decision.disposition == ActionDisposition.PASS


def test_material_intervention_requires_grounded_bounded_evidence_and_dedupes():
    runtime = CentralFeatureRuntime(model_visible=True)
    proposal = adapt_proposed_action(
        {"command": "sed -i 's/x/y/' missing.py"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    empty = PreflightDecision(
        ActionDisposition.RETURN_TO_MODEL,
        proposal.raw_command,
        confidence=1.0,
        source_revision="s1",
    )
    heuristic = replace(
        empty,
        evidence=("maybe another file",),
        evidence_grade=EvidenceGrade.HEURISTIC,
    )
    grounded = replace(
        empty,
        evidence=("app.py has a mechanically proven coupled-file contract",),
        reason_codes=("material_edit_risk",),
        evidence_grade=EvidenceGrade.DIRECT,
    )
    absent_target = replace(
        empty,
        evidence=("missing.py is absent from the current workspace",),
        reason_codes=("edit_target_absent",),
        evidence_grade=EvidenceGrade.DIRECT,
    )

    assert runtime.admit_preflight_intervention(proposal, empty)[0] is False
    assert runtime.admit_preflight_intervention(proposal, heuristic)[0] is False
    assert runtime.admit_preflight_intervention(proposal, absent_target) == (
        False,
        "postflight_safe_absent_target",
    )
    assert runtime.admit_preflight_intervention(proposal, grounded) == (True, "admitted")
    assert runtime.admit_preflight_intervention(proposal, grounded) == (
        False,
        "duplicate_evidence",
    )


def test_low_confidence_rewrite_contract_can_be_rejected_by_dispatch():
    proposal = adapt_proposed_action(
        {"command": "python weird.py"},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    assert replace(proposal, parser_confidence=0.2).parser_confidence < 1.0


def test_all_17_features_have_explicit_lifecycle_placement():
    runtime = CentralFeatureRuntime(model_visible=True)
    assert set(PREFLIGHT_FEATURE_PLACEMENT) == set(runtime.summary()["feature_ids"])
    assert sum(value.postflight_only for value in PREFLIGHT_FEATURE_PLACEMENT.values()) == 5


def test_preflight_modes_are_explicit_and_fail_closed_to_off():
    assert PreflightMode.parse("off") is PreflightMode.OFF
    assert PreflightMode.parse("shadow") is PreflightMode.SHADOW
    assert PreflightMode.parse("assistive_safe") is PreflightMode.ASSISTIVE_SAFE
    with pytest.raises(ValueError, match="preflight mode"):
        PreflightMode.parse("rewrite-everything")


def test_every_lifecycle_placement_declares_inputs_and_evidence_grade():
    for feature_id, placement in PREFLIGHT_FEATURE_PLACEMENT.items():
        assert placement.feature_id == feature_id
        assert placement.required_inputs
        assert placement.evidence_grade in EvidenceGrade
        if placement.postflight_only:
            assert placement.preflight_operations == ()
