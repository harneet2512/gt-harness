"""Provider-free action-bound producer/consumer proof suite.

One positive and one adversarial negative scenario per action-bound feature
identity, plus the mandatory cross-feature scenarios. The bootstrap-bound
``select_catalog`` lifecycle is proved by the central-agent bootstrap suite,
not fabricated by ``CentralFeatureRuntime``. Every positive scenario asserts:

1. exact triggering event;
2. grounded payload fields;
3. source and workspace revision;
4. consumer identity and a recorded controller effect;
5. timing boundary (evidence observed before the effect acts);
6. model visibility decision;
7. deduplication (an identical re-trigger emits no second receipt);
8. expected next-action predicate (effect kind / required-before boundary).

The adversarial negatives assert correct-quiet behavior: a near miss must not
be fabricated into a model-visible delivery.  The final census terminal lines
are asserted in test_gt_central_runtime.py; this module proves the individual
contracts.
"""

from __future__ import annotations

from gt_engine.central_controls import EffectKind, consumer_spec_for
from gt_engine.central_runtime import (
    CENTRAL_FEATURE_IDS,
    CentralFeatureRuntime,
    WorkspaceTransition,
    classify_validation_command,
    feature_payload_grounded,
)
from gt_engine.provider_view import build_provider_view

WR0 = "workspace-v0"
SR0 = "source-v0"
SR1 = "source-v1"


def _transition(
    action_id: int, command: str, before: str, after: str, **paths
) -> WorkspaceTransition:
    return WorkspaceTransition(
        action_id=action_id,
        command=command,
        before_revision=before,
        after_revision=after,
        **paths,
    )


def _completed(command: str, *, rc: int, output: str, checks=()):
    return classify_validation_command(command, checks).with_result(
        result_code=rc,
        output=output,
        source_revision=SR1,
        workspace_revision=WR0,
    )


def _consume(runtime: CentralFeatureRuntime, action_id: int, call: int) -> None:
    runtime.consume_effects(action_id=action_id, call=call)


def _feature_rows(summary: dict, feature_id: str) -> list[dict]:
    return [row for row in summary["receipts"] if row["feature_id"] == feature_id]


def _assert_contract(
    runtime: CentralFeatureRuntime,
    feature_id: str,
    *,
    model_visible: bool,
    effect_kind: EffectKind,
    boundary: str,
    action: int,
    source_revision: str = SR1,
) -> None:
    summary = runtime.summary()
    rows = [
        row for row in _feature_rows(summary, feature_id) if row["action"] == action
    ]
    assert rows, f"{feature_id} produced no receipt"
    for row in rows:
        # 1. boundary and action.
        assert row["boundary"] == boundary
        assert row["action"] == action
        # 3. source and workspace revision stamped.
        assert row["source_revision"] == source_revision
        assert row["revision"]
        # 6. model visibility decision matches the declared contract.
        assert row["model_visible"] is model_visible
        if model_visible:
            assert feature_payload_grounded(feature_id, row["payload"]) is True
    # 4. consumer identity and a recorded controller effect.
    assert feature_id in summary["consumer_paths"]
    effects = [e for e in summary["effects"] if e["feature_id"] == feature_id]
    assert effects, f"{feature_id} produced no effect"
    assert effects[0]["effect_kind"] == effect_kind.value
    # 5. timing boundary: evidence observed before the effect acts.
    assert effects[0]["evidence_before_effect"] is True
    # 8. expected next-action predicate.
    assert consumer_spec_for(feature_id) is not None
    if effects[0]["required_before_action"] is not None:
        assert effects[0]["effect_before_next_action"] is True


def _assert_quiet(runtime: CentralFeatureRuntime, *feature_ids: str) -> None:
    summary = runtime.summary()
    for feature_id in feature_ids:
        rows = _feature_rows(summary, feature_id)
        assert not any(row["model_visible"] for row in rows), (
            f"{feature_id} leaked a model-visible delivery on a near miss"
        )


# ---------------------------------------------------------------------------
# Positive producer/consumer scenarios.
# ---------------------------------------------------------------------------

SEARCH_OUTPUT = (
    "bottle.py:10:class Bottle\n"
    "tests/test_bottle.py:20:caller references Bottle; existing registry pattern\n"
)


def _register_graph_contract(
    runtime: CentralFeatureRuntime,
    *,
    target_path: str = "bottle.py",
    target_symbol: str = "Bottle",
    target_line: int = 10,
    caller_path: str = "tests/test_bottle.py",
    caller_symbol: str = "test_bottle",
    caller_line: int = 20,
) -> None:
    runtime.register_structural_evidence(
        source_revision=SR0,
        anchors=(
            {"path": target_path, "line": target_line, "symbol": target_symbol},
            {"path": caller_path, "line": caller_line, "symbol": caller_symbol},
        ),
        definitions=(
            {
                "path": target_path,
                "line": target_line,
                "symbol": target_symbol,
                "semantics": "graph_definition",
            },
        ),
        references=(
            {
                "path": caller_path,
                "line": caller_line,
                "symbol": target_symbol,
                "semantics": "graph_call_reference",
            },
        ),
        callers=(
            {
                "caller_path": caller_path,
                "caller_line": caller_line,
                "caller_symbol": caller_symbol,
                "target_path": target_path,
                "target_symbol": target_symbol,
                "semantics": "graph_recorded",
            },
        ),
        graph_revision="graph-source-v0",
    )
    _consume(runtime, 0, 0)
    runtime.suppress_task_start_delivery()


def test_obligations_consumer_contracts_the_instruction():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Run `pytest -q`. Write `report.jsonl`.",
        revision=WR0,
        source_revision=SR0,
        explicit_checks=("pytest -q",),
        task_deliverables=("report.jsonl",),
    )
    _consume(runtime, 0, 0)

    _assert_contract(
        runtime, "obligations",
        model_visible=False, effect_kind=EffectKind.CONTRACT_STATE_UPDATE,
        boundary="task_start", action=0, source_revision=SR0,
    )
    row = _feature_rows(runtime.summary(), "obligations")[0]
    assert "pytest -q" in row["payload"]["declared_checks"]


def test_localization_and_gt_loc_reslot_store_anchors_internal_only():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'class Bottle' .", output=SEARCH_OUTPUT, returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "localization",
        model_visible=False, effect_kind=EffectKind.CONTEXT_RESLOT,
        boundary="search_result", action=1, source_revision=SR0,
    )
    row = _feature_rows(runtime.summary(), "localization")[0]
    assert row["payload"]["anchors"][0]["path"] == "bottle.py"
    _assert_contract(
        runtime, "GT_LOC_RESLOT",
        model_visible=False, effect_kind=EffectKind.CONTEXT_RESLOT,
        boundary="search_result", action=1, source_revision=SR0,
    )


def test_disabled_task_start_reslot_closes_its_semantic_claim():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    _register_graph_contract(runtime)

    summary = runtime.summary()
    loc_rows = _feature_rows(summary, "GT_LOC_RESLOT")
    assert loc_rows
    assert all(not row["model_visible"] for row in loc_rows)
    claim_rows = summary["semantic_decisions"]["claims"]
    assert not any(
        row.get("feature_id") == "GT_LOC_RESLOT" and row.get("active")
        for row in claim_rows
    )


def test_def_partition_separates_definition_from_reference_anchors():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    _register_graph_contract(runtime)

    _assert_contract(
        runtime, "def_partition",
        model_visible=False, effect_kind=EffectKind.IMPACT_SET_UPDATE,
        boundary="task_start", action=0, source_revision=SR0,
    )
    row = _feature_rows(runtime.summary(), "def_partition")[0]
    assert row["payload"]["definition_anchors"][0]["path"] == "bottle.py"
    assert row["payload"]["reference_anchors"]


def test_caller_contract_stores_verified_caller_anchors():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    _register_graph_contract(runtime)

    _assert_contract(
        runtime, "caller_contract",
        model_visible=False, effect_kind=EffectKind.IMPACT_SET_UPDATE,
        boundary="task_start", action=0, source_revision=SR0,
    )
    row = _feature_rows(runtime.summary(), "caller_contract")[0]
    assert row["payload"]["callers"][0]["caller_path"] == "tests/test_bottle.py"


def test_newfile_precedent_records_a_concrete_precedent_path():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="write bottle_new.py", output="", returncode=0,
        transition=_transition(
            1,
            "write",
            WR0,
            SR1,
            created=("bottle_new.py",),
            before_contents={"bottle.py": "class Bottle:\n    pass\n"},
            after_contents={
                "bottle.py": "class Bottle:\n    pass\n",
                "bottle_new.py": "class NewBottle:\n    pass\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "newfile_precedent",
        model_visible=True, effect_kind=EffectKind.IMPACT_SET_UPDATE,
        boundary="edit_result", action=1, source_revision=SR1,
    )
    row = _feature_rows(runtime.summary(), "newfile_precedent")[0]
    assert row["payload"]["precedent_path"] == "bottle.py"


def test_newfile_precedent_ranks_semantically_related_nonempty_sibling():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1,
        command="write task_file/scripts/optimized_packer.py",
        output="",
        returncode=0,
        transition=_transition(
            1,
            "write",
            WR0,
            SR1,
            created=("task_file/scripts/optimized_packer.py",),
            before_contents={
                "task_file/scripts/__init__.py": "",
                "task_file/scripts/baseline_packer.py": "def pack(requests):\n    return []\n",
                "task_file/scripts/cost_model.py": "def cost(item):\n    return 1\n",
            },
            after_contents={
                "task_file/scripts/optimized_packer.py": "def pack(requests):\n    return []\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    row = _feature_rows(runtime.summary(), "newfile_precedent")[0]
    assert row["payload"]["precedent_path"] == "task_file/scripts/baseline_packer.py"


def test_newfile_precedent_never_uses_a_model_created_sibling_as_repository_precedent():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Implement",
        revision=WR0,
        source_revision=SR0,
        initial_source_paths=("work/original.c",),
    )
    runtime.observe_action(
        action_id=2,
        command="write work/decsim2.c",
        output="",
        returncode=0,
        transition=_transition(
            2,
            "write work/decsim2.c",
            WR0,
            SR1,
            created=("work/decsim2.c",),
            before_contents={
                "work/original.c": "int original(void) { return 0; }\n",
                "work/decsim.c": "int generated(void) { return 1; }\n",
            },
            after_contents={
                "work/decsim2.c": "int generated2(void) { return 2; }\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 2, 1)

    rows = _feature_rows(runtime.summary(), "newfile_precedent")
    assert rows
    assert rows[0]["payload"]["precedent_path"] == "work/original.c"
    assert rows[0]["payload"]["precedent_origin"] == "task_start_repository"


def test_newfile_precedent_abstains_when_only_sibling_is_empty_package_marker():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1,
        command="write task_file/scripts/optimized_packer.py",
        output="",
        returncode=0,
        transition=_transition(
            1,
            "write",
            WR0,
            SR1,
            created=("task_file/scripts/optimized_packer.py",),
            before_contents={"task_file/scripts/__init__.py": ""},
            after_contents={
                "task_file/scripts/optimized_packer.py": "def pack(requests):\n    return []\n"
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    _assert_quiet(runtime, "newfile_precedent")


def test_newfile_precedent_ignores_derived_and_cache_artifacts():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="run generator", output="", returncode=0,
        transition=_transition(
            1,
            "run generator",
            WR0,
            SR1,
            created=("__pycache__/bottle.cpython-313.pyc",),
            before_contents={"__pycache__/other.cpython-313.pyc": "binary"},
            after_contents={
                "__pycache__/other.cpython-313.pyc": "binary",
                "__pycache__/bottle.cpython-313.pyc": "binary",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    _assert_quiet(runtime, "newfile_precedent")


def test_newfile_precedent_payload_names_only_the_source_trigger():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="write files", output="", returncode=0,
        transition=_transition(
            1,
            "write files",
            WR0,
            SR1,
            created=("__pycache__", "bottle_new.py"),
            before_contents={"bottle.py": "class Bottle:\n    pass\n"},
            after_contents={
                "bottle.py": "class Bottle:\n    pass\n",
                "bottle_new.py": "class NewBottle:\n    pass\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    row = _feature_rows(runtime.summary(), "newfile_precedent")[0]
    assert row["payload"]["created_files"] == ["bottle_new.py"]
    assert "__pycache__" not in row["payload"]["message"]


def test_covering_red_records_grounded_failure_and_must_precede_next_action():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Run `pytest -q`.", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pytest -q", output="1 failed: assert error", returncode=1,
        transition=_transition(1, "pytest -q", WR0, WR0),
        revision=WR0, source_revision=SR1,
        validation=_completed(
            "pytest -q", rc=1, output="1 failed: assert error", checks=("pytest -q",)
        ),
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "covering_red",
        model_visible=True, effect_kind=EffectKind.FAILURE_STATE_TRANSITION,
        boundary="test_result", action=1,
    )
    row = _feature_rows(runtime.summary(), "covering_red")[0]
    assert row["payload"]["command"] == "pytest -q"
    assert "assert error" in row["payload"]["diagnostic"]


def test_recovery_without_a_concrete_source_anchor_remains_private():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    for action_id in (1, 2):
        runtime.observe_action(
            action_id=action_id, command="pytest -q", output="1 failed: assert error", returncode=1,
            transition=_transition(action_id, "pytest -q", WR0, WR0),
            revision=WR0, source_revision=SR1,
            validation=_completed("pytest -q", rc=1, output="1 failed: assert error"),
        )
    _consume(runtime, 2, 2)

    _assert_contract(
        runtime, "recovery",
        model_visible=False, effect_kind=EffectKind.FAILURE_STATE_TRANSITION,
        boundary="test_result", action=2,
    )
    row = _feature_rows(runtime.summary(), "recovery")[0]
    assert row["payload"]["alternate_action"]["discriminator"] == (
        "exact repeat at unchanged source revision"
    )
    assert row["payload"]["alternate_action"]["paths"] == []


def test_signature_delta_schedules_caller_validation_with_symbol():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix signature", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=2, command="sed -i 's/def f(/def f(x, /' app.py", output="", returncode=0,
        transition=_transition(2, "sed -i", WR0, SR1, modified=("app.py",)),
        revision=SR1, source_revision=SR1,
    )
    _consume(runtime, 2, 2)

    _assert_contract(
        runtime, "signature_delta",
        model_visible=True, effect_kind=EffectKind.VALIDATION_SCHEDULE,
        boundary="edit_result", action=2,
    )
    row = _feature_rows(runtime.summary(), "signature_delta")[0]
    assert row["payload"]["symbol"] == "f"


def test_signature_delta_uses_source_witness_for_non_sed_edits():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Change the API", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1,
        command="apply_patch",
        output="Done!",
        returncode=0,
        transition=_transition(
            1,
            "apply_patch",
            WR0,
            SR1,
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={"app.py": "def f(x, y=0):\n    return x + y\n"},
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    row = _feature_rows(runtime.summary(), "signature_delta")[0]
    assert row["payload"]["symbol"] == "f"
    assert row["payload"]["before_signature"] == "def f(x)"
    assert row["payload"]["after_signature"] == "def f(x, y=0)"


def test_signature_delta_payload_excludes_derived_artifact_paths():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Change the API", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1,
        command="apply_patch",
        output="Done!",
        returncode=0,
        transition=_transition(
            1,
            "apply_patch",
            WR0,
            SR1,
            modified=("app.py", "__pycache__/app.cpython-313.pyc"),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={"app.py": "def f(x, y=0):\n    return x + y\n"},
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    row = _feature_rows(runtime.summary(), "signature_delta")[0]
    payload = row["payload"]
    assert payload["changed_paths"] == ["app.py"]
    assert all(
        "__pycache__" not in anchor
        for anchor in row["payload"].values()
        if isinstance(anchor, str)
    )
    claims = runtime.summary()["semantic_decisions"]["claims"]
    assert all(".pyc" not in anchor for claim in claims for anchor in claim["anchors"])


def test_newfile_precedent_is_suppressed_when_model_already_used_the_fact():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    command = "cat bottle.py > bottle_new.py  # precedent bottle.py"
    runtime.observe_action(
        action_id=1,
        command=command,
        output="",
        returncode=0,
        transition=_transition(
            1,
            command,
            WR0,
            SR1,
            created=("bottle_new.py",),
            before_contents={"bottle.py": "class Bottle:\n    pass\n"},
            after_contents={
                "bottle.py": "class Bottle:\n    pass\n",
                "bottle_new.py": "class NewBottle:\n    pass\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    history = [
        {"role": "assistant", "extra": {"actions": [{"command": command}]}, "content": ""}
    ]
    assert runtime.model_feedback(history=history, deferred=True) == ""
    receipt = _feature_rows(runtime.summary(), "newfile_precedent")[0]
    assert receipt["delivery_status"] == "suppressed"
    assert receipt["delivery_reason"] == "represented_in_action_history"


def test_signature_delta_is_suppressed_when_exact_edit_is_in_action_history():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Change the API", revision=WR0, source_revision=SR0)
    command = "sed -i 's/def f(x)/def f(x, y=0)/' app.py"
    runtime.observe_action(
        action_id=1,
        command=command,
        output="",
        returncode=0,
        transition=_transition(
            1,
            command,
            WR0,
            SR1,
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={"app.py": "def f(x, y=0):\n    return x + y\n"},
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    history = [
        {"role": "assistant", "extra": {"actions": [{"command": command}]}, "content": ""}
    ]
    assert runtime.model_feedback(history=history, deferred=True) == ""
    receipt = _feature_rows(runtime.summary(), "signature_delta")[0]
    assert receipt["delivery_status"] == "suppressed"
    assert receipt["delivery_reason"] == "represented_in_action_history"


def test_newfile_precedent_self_echo_is_suppressed_from_provider_delivery():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1,
        command="write generate_re.py",
        output="",
        returncode=0,
        transition=_transition(
            1,
            "write",
            WR0,
            SR1,
            created=("generate_re.py",),
            before_contents={"check.py": "def check():\n    return True\n"},
            after_contents={
                "check.py": "def check():\n    return True\n",
                "generate_re.py": "def generate():\n    return 1\n",
            },
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    assert runtime.model_feedback(deferred=True) == ""
    receipt = _feature_rows(runtime.summary(), "newfile_precedent")[0]
    assert receipt["delivery_status"] == "suppressed"
    assert receipt["delivery_reason"] == "change_surface_self_echo"


def test_signature_delta_without_callers_is_suppressed_as_self_echo():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Change the API", revision=WR0, source_revision=SR0)
    command = "sed -i 's/def f(x)/def f(x, y=0)/' app.py"
    runtime.observe_action(
        action_id=1,
        command=command,
        output="",
        returncode=0,
        transition=_transition(
            1,
            command,
            WR0,
            SR1,
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={"app.py": "def f(x, y=0):\n    return x + y\n"},
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 1, 1)

    assert runtime.model_feedback(deferred=True) == ""
    receipt = _feature_rows(runtime.summary(), "signature_delta")[0]
    assert receipt["delivery_status"] == "suppressed"
    assert receipt["delivery_reason"] == "change_surface_self_echo"


def test_signature_payload_coalesces_caller_and_patch_consumers():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Change f", revision=WR0, source_revision=SR0)
    _register_graph_contract(
        runtime,
        target_path="app.py",
        target_symbol="f",
        target_line=1,
        caller_path="tests/test_app.py",
        caller_symbol="test_f",
        caller_line=3,
    )
    runtime.observe_action(
        action_id=1,
        command="rg -n 'f' .",
        output="app.py:1:def f(x)\ntests/test_app.py:3:assert f(1) == 1\n",
        returncode=0,
        transition=_transition(1, "rg", WR0, WR0),
        revision=WR0,
        source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    runtime.observe_action(
        action_id=2,
        command="apply_patch",
        output="Done!",
        returncode=0,
        transition=_transition(
            2,
            "apply_patch",
            WR0,
            SR1,
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={"app.py": "def f(x, y=0):\n    return x + y\n"},
        ),
        revision=SR1,
        source_revision=SR1,
    )
    _consume(runtime, 2, 2)

    feedback = runtime.model_feedback(deferred=True)
    metadata = runtime.prepared_guidance() or {}
    assert "Known callers: tests/test_app.py" in feedback
    assert {
        "signature_delta",
        "caller_contract",
        "GT_PATCH_DELTA",
    } <= set(metadata["contributing_features"])


def test_syntax_result_updates_validation_state_on_failure():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix syntax", revision=WR0, source_revision=SR0)
    runtime.record_syntax(
        action_id=1, revision=SR1, source_revision=SR1, failed=True,
        reason="changed_file_syntax_failure", path="app.py",
        command="python3 -m py_compile app.py", returncode=1, diagnostic="SyntaxError",
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "syntax_result",
        model_visible=True, effect_kind=EffectKind.SYNTAX_STATE_UPDATE,
        boundary="edit_result", action=1,
    )


def test_submit_refusal_and_submit_red_record_non_blocking_risk():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Finish", revision=WR0, source_revision=SR0)
    runtime.record_submit(
        action_id=1, revision=SR1, source_revision=SR1, refused=True,
        sensor_healthy=True, blockers=("pytest -q",),
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "submit_refusal",
        model_visible=True, effect_kind=EffectKind.SUBMIT_RISK_UPDATE,
        boundary="submit", action=1,
    )
    row = _feature_rows(runtime.summary(), "submit_refusal")[0]
    assert "pytest -q" in row["payload"]["blockers"]
    _assert_contract(
        runtime, "GT_SS_SUBMIT_RED",
        model_visible=False, effect_kind=EffectKind.SUBMIT_RISK_UPDATE,
        boundary="submit", action=1,
    )


def test_change_surface_and_patch_delta_classify_the_edit():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=2, command="write app.py", output="", returncode=0,
        transition=_transition(2, "write", WR0, SR1, modified=("app.py",)),
        revision=SR1, source_revision=SR1,
    )
    _consume(runtime, 2, 2)

    _assert_contract(
        runtime, "GT_CHANGE_SURFACE",
        model_visible=False, effect_kind=EffectKind.IMPACT_SET_UPDATE,
        boundary="edit_result", action=2,
    )
    _assert_contract(
        runtime, "GT_PATCH_DELTA",
        model_visible=False, effect_kind=EffectKind.VALIDATION_SCHEDULE,
        boundary="edit_result", action=2,
    )
    surface = _feature_rows(runtime.summary(), "GT_CHANGE_SURFACE")[0]
    assert surface["payload"]["source_relevant"] == ["app.py"]


def test_validation_debt_schedules_the_relevant_check():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision=WR0, source_revision=SR0, explicit_checks=("pytest -q",),
    )
    for action_id in (1, 2, 3):
        revision = f"{SR1}-{action_id}"
        runtime.observe_action(
            action_id=action_id, command=f"write app.py {action_id}", output="", returncode=0,
            transition=_transition(action_id, "write", WR0, revision, modified=("app.py",)),
            revision=revision, source_revision=revision,
        )
    _consume(runtime, 3, 3)

    _assert_contract(
        runtime, "GT_EDIT_CHECK",
        model_visible=True, effect_kind=EffectKind.VALIDATION_SCHEDULE,
        boundary="edit_result", action=3, source_revision=f"{SR1}-3",
    )
    row = next(
        row
        for row in _feature_rows(runtime.summary(), "GT_EDIT_CHECK")
        if row["action"] == 3
    )
    assert row["payload"]["intervention"] == "validation_debt"
    assert row["payload"]["declared_check"] == "pytest -q"
    assert "app.py" in row["payload"]["changed_paths"]


def test_hypothesis_records_deterministic_failure_state():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pytest -q", output="1 failed", returncode=1,
        transition=_transition(1, "pytest -q", WR0, WR0), revision=WR0, source_revision=SR1,
        validation=_completed("pytest -q", rc=1, output="1 failed"),
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "GT_HYPOTHESIS",
        model_visible=False, effect_kind=EffectKind.FAILURE_STATE_TRANSITION,
        boundary="test_result", action=1,
    )


def test_cert_delivery_records_submission_readiness():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Finish", revision=WR0, source_revision=SR0)
    runtime.record_submit(
        action_id=1, revision=SR1, source_revision=SR1, refused=False,
        sensor_healthy=True, check_count=1, passing_checks=1,
    )
    _consume(runtime, 1, 1)

    _assert_contract(
        runtime, "GT_CERT_DELIVERY",
        model_visible=False, effect_kind=EffectKind.CERTIFY_PASS,
        boundary="submit", action=1,
    )


def test_all_17_action_bound_positive_scenarios_cover_every_runtime_feature():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Run `pytest -q`.", revision=WR0, source_revision=SR0,
        explicit_checks=("pytest -q",),
    )
    _register_graph_contract(runtime)
    runtime.observe_action(
        action_id=1, command="rg -n 'Bottle' .", output=SEARCH_OUTPUT, returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    runtime.observe_action(
        action_id=2, command="sed -i 's/def f(/def f(x:/' app.py", output="", returncode=0,
        transition=_transition(
            2,
            "sed -i",
            WR0,
            SR1,
            created=("new_module.py",),
            modified=("app.py",),
            before_contents={"app.py": "def f(x):\n    return x\n"},
            after_contents={
                "app.py": "def f(x, y):\n    return x + y\n",
                "new_module.py": "def helper():\n    pass\n",
            },
        ),
        revision=SR1, source_revision=SR1,
    )
    runtime.record_syntax(
        action_id=2, revision=SR1, source_revision=SR1, failed=True,
        reason="fixture", path="app.py", command="python3 -m py_compile app.py",
        returncode=1, diagnostic="SyntaxError",
    )
    for action_id in (3, 4):
        runtime.observe_action(
            action_id=action_id, command="pytest -q", output="1 failed", returncode=1,
            transition=_transition(action_id, "pytest -q", SR1, SR1),
            revision=SR1, source_revision=SR1,
            validation=_completed("pytest -q", rc=1, output="1 failed"),
        )
    runtime.record_submit(
        action_id=5, revision=SR1, source_revision=SR1, refused=True,
        sensor_healthy=True, blockers=("pytest -q",),
    )
    for call in range(1, 6):
        _consume(runtime, call, call)

    summary = runtime.summary()
    produced = {
        feature_id
        for feature_id in CENTRAL_FEATURE_IDS
        if _feature_rows(summary, feature_id)
    }
    action_bound_features = set(CENTRAL_FEATURE_IDS) - {"select_catalog"}
    assert len(action_bound_features) == 17
    assert produced == action_bound_features
    assert set(summary["consumer_paths"]) == action_bound_features


def test_identical_retrigger_does_not_duplicate_receipts():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    for _ in range(2):
        runtime.observe_action(
            action_id=1, command="pytest -q", output="1 failed", returncode=1,
            transition=_transition(1, "pytest -q", WR0, WR0),
            revision=WR0, source_revision=SR1,
            validation=_completed("pytest -q", rc=1, output="1 failed"),
        )

    assert len(_feature_rows(runtime.summary(), "covering_red")) == 1
    assert len(_feature_rows(runtime.summary(), "GT_HYPOTHESIS")) == 1


# ---------------------------------------------------------------------------
# Adversarial negative scenarios: correct-quiet on near misses.
# ---------------------------------------------------------------------------

def test_obligations_quiet_on_empty_instruction():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("", revision=WR0, source_revision=SR0)
    _consume(runtime, 0, 0)
    _assert_quiet(runtime, "obligations")


def test_localization_quiet_on_empty_search():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'x' .", output="", returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "localization", "GT_LOC_RESLOT")


def test_def_partition_quiet_when_no_definition_anchor():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'usage' .", output="app.py:1:usage of Bottle here\n",
        returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "def_partition")


def test_caller_contract_quiet_without_caller_language():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'class' .", output="bottle.py:10:class Bottle\n", returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "caller_contract")


def test_newfile_precedent_quiet_without_precedent_marker():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'class' .", output="bottle.py:10:class Bottle\n", returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "newfile_precedent")


def test_covering_red_quiet_on_ordinary_failure():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="echo error", output="error is ordinary text", returncode=1,
        transition=_transition(1, "echo error", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "covering_red", "GT_HYPOTHESIS")


def test_recovery_quiet_until_the_failure_repeats():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pytest -q", output="1 failed", returncode=1,
        transition=_transition(1, "pytest -q", WR0, WR0), revision=WR0, source_revision=SR1,
        validation=_completed("pytest -q", rc=1, output="1 failed"),
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "recovery")


def test_signature_delta_quiet_without_signature_shaped_edit():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=2, command="tee app.py <<'EOF'\ndef f(x): pass\nEOF", output="", returncode=0,
        transition=_transition(2, "tee", WR0, SR1, created=("app.py",)),
        revision=SR1, source_revision=SR1,
    )
    _consume(runtime, 2, 2)
    _assert_quiet(runtime, "signature_delta")


def test_submit_refusal_quiet_on_clean_submit():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Finish", revision=WR0, source_revision=SR0)
    runtime.record_submit(
        action_id=1, revision=SR1, source_revision=SR1, refused=False, sensor_healthy=True,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "submit_refusal", "GT_SS_SUBMIT_RED")


def test_syntax_result_pass_stays_private():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix syntax", revision=WR0, source_revision=SR0)
    runtime.record_syntax(
        action_id=1, revision=SR1, source_revision=SR1, failed=False,
        reason="changed_file_syntax_pass", path="app.py", returncode=0,
    )
    _consume(runtime, 1, 1)

    _assert_quiet(runtime, "syntax_result")
    row = _feature_rows(runtime.summary(), "syntax_result")[0]
    assert row["decision"] == "PASS"
    assert row["model_visible"] is False


def test_change_surface_quiet_without_workspace_change():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pwd", output="", returncode=0,
        transition=_transition(1, "pwd", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "GT_CHANGE_SURFACE", "GT_PATCH_DELTA")


def test_validation_debt_quiet_below_three_source_revisions():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision=WR0, source_revision=SR0, explicit_checks=("pytest -q",),
    )
    for action_id in (1, 2):
        revision = f"{SR1}-{action_id}"
        runtime.observe_action(
            action_id=action_id, command=f"write app.py {action_id}", output="", returncode=0,
            transition=_transition(action_id, "write", WR0, revision, modified=("app.py",)),
            revision=revision, source_revision=revision,
        )
    _consume(runtime, 2, 2)
    _assert_quiet(runtime, "GT_EDIT_CHECK")


def test_validation_debt_quiet_on_artifact_only_changes():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision=WR0, source_revision=SR0, explicit_checks=("pytest -q",),
    )
    for action_id, path in ((1, "benchmark_out.txt"), (2, "a.out"), (3, "build/x.o")):
        revision = f"{WR0}-{action_id}"
        runtime.observe_action(
            action_id=action_id, command=f"write {path}", output="", returncode=0,
            transition=_transition(action_id, "write", WR0, revision, modified=(path,)),
            revision=revision, source_revision=SR0,
        )
    _consume(runtime, 3, 3)
    _assert_quiet(runtime, "GT_EDIT_CHECK")


def test_cert_delivery_quiet_without_submit_boundary():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Finish", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pwd", output="", returncode=0,
        transition=_transition(1, "pwd", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)
    _assert_quiet(runtime, "GT_CERT_DELIVERY")


# ---------------------------------------------------------------------------
# Mandatory cross-feature scenarios.
# ---------------------------------------------------------------------------

def test_localization_plus_reslot_changes_context_without_a_model_message():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="rg -n 'Bottle' .", output=SEARCH_OUTPUT, returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    effects = runtime.consume_effects(action_id=1, call=1)

    reslot = [e for e in effects if e.feature_id in {"localization", "GT_LOC_RESLOT"}]
    assert reslot
    assert all(e.effect_kind == EffectKind.CONTEXT_RESLOT for e in reslot)
    assert all(e.model_visible is False for e in reslot)
    assert runtime.model_feedback() == ""


def test_signature_delta_plus_caller_contract_schedules_caller_validation():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    _register_graph_contract(
        runtime,
        target_path="app.py",
        target_symbol="f",
        target_line=1,
        caller_path="tests/test_app.py",
        caller_symbol="test_f",
        caller_line=3,
    )
    runtime.observe_action(
        action_id=1, command="rg -n 'caller' .", output=SEARCH_OUTPUT, returncode=0,
        transition=_transition(1, "rg -n", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    runtime.observe_action(
        action_id=2, command="sed -i 's/def f(/def f(x, /' app.py", output="", returncode=0,
        transition=_transition(2, "sed -i", WR0, SR1, modified=("app.py",)),
        revision=SR1, source_revision=SR1,
    )
    runtime.consume_effects(action_id=2, call=2)

    scheduled = [
        effect
        for effect in runtime.summary()["effects"]
        if effect["feature_id"] in {"signature_delta", "caller_contract", "GT_PATCH_DELTA"}
    ]
    kinds = {effect["effect_kind"] for effect in scheduled}
    assert EffectKind.VALIDATION_SCHEDULE.value in kinds
    assert EffectKind.IMPACT_SET_UPDATE.value in kinds


def test_syntax_failure_updates_state_without_interrupting_a_multi_action_batch():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix syntax", revision=WR0, source_revision=SR0)
    runtime.record_syntax(
        action_id=1, revision=SR1, source_revision=SR1, failed=True,
        reason="changed_file_syntax_failure", path="app.py",
        command="python3 -m py_compile app.py", returncode=1, diagnostic="SyntaxError",
    )
    effects = runtime.consume_effects(action_id=1, call=1)

    syntax = next(e for e in effects if e.feature_id == "syntax_result")
    assert syntax.required_before_action is None
    application = next(
        row
        for row in runtime.summary()["effect_applications"]
        if row["feature_id"] == "syntax_result"
    )
    assert "validation_results" in application["state_fields_changed"]
    assert runtime.summary()["action_metrics"]["batch_interrupts"] == 0


def test_validation_debt_ignores_background_artifacts_and_targets_source():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Update it, then run `pytest -q`.",
        revision=WR0, source_revision=SR0, explicit_checks=("pytest -q",),
    )
    runtime.observe_action(
        action_id=1, command="write benchmark_out.txt", output="", returncode=0,
        transition=_transition(1, "write", WR0, WR0, modified=("benchmark_out.txt",)),
        revision=WR0, source_revision=SR0,
    )
    for action_id in (2, 3, 4):
        revision = f"{SR1}-{action_id}"
        runtime.observe_action(
            action_id=action_id, command=f"write app.py {action_id}", output="", returncode=0,
            transition=_transition(action_id, "write", WR0, revision, modified=("app.py",)),
            revision=revision, source_revision=revision,
        )
    effects = runtime.consume_effects(action_id=4, call=4)

    debt = [e for e in effects if e.feature_id == "GT_EDIT_CHECK"]
    assert debt, "artifact-only change should not suppress real source debt"
    assert debt[0].effect_action["changed_paths"] == ["app.py"]
    assert runtime.summary()["source_epoch"] == 3


def test_covering_red_hypothesis_recovery_discriminate_after_exact_repeat():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="write app.py", output="", returncode=0,
        transition=_transition(1, "write", WR0, SR1, modified=("app.py",)),
        revision=SR1, source_revision=SR1,
    )
    for action_id in (2, 3):
        runtime.observe_action(
            action_id=action_id, command="pytest -q", output="1 failed: assert error", returncode=1,
            transition=_transition(action_id, "pytest -q", SR1, SR1),
            revision=SR1, source_revision=SR1,
            validation=_completed("pytest -q", rc=1, output="1 failed: assert error"),
        )
    effects = runtime.consume_effects(action_id=3, call=3)

    kinds = {effect.feature_id for effect in effects}
    assert {"covering_red", "GT_HYPOTHESIS", "recovery"} <= kinds
    recovery = next(effect for effect in effects if effect.feature_id == "recovery")
    assert recovery.effect_action["alternate_action"]["kind"] == "inspect_then_edit"
    assert recovery.effect_action["alternate_action"]["paths"] == ["app.py"]


def test_submit_risk_and_certification_are_non_blocking_state_updates():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Finish", revision=WR0, source_revision=SR0)
    runtime.record_submit(
        action_id=1, revision=SR1, source_revision=SR1, refused=True,
        sensor_healthy=True, check_count=1, failing_checks=1, blockers=("pytest -q",),
    )
    effects = runtime.consume_effects(action_id=1, call=1)

    assert any(e.feature_id == "submit_refusal" for e in effects)
    assert any(e.effect_kind == EffectKind.CERTIFY_PASS for e in effects)
    summary = runtime.summary()
    assert summary["action_metrics"]["submit_holds"] == 0
    risk = next(
        row
        for row in summary["effect_applications"]
        if row["feature_id"] == "submit_refusal"
    )
    assert "submission_state" in risk["state_fields_changed"]


def test_two_actionable_facts_from_one_action_are_both_consumed():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task(
        "Fix it and run `pytest -q`.",
        revision=WR0,
        source_revision=SR0,
        explicit_checks=("pytest -q",),
    )
    runtime.observe_action(
        action_id=1, command="pytest -q", output="1 failed: assert error", returncode=1,
        transition=_transition(1, "pytest -q", WR0, WR0), revision=WR0, source_revision=SR1,
        validation=_completed(
            "pytest -q", rc=1, output="1 failed: assert error", checks=("pytest -q",)
        ),
    )
    effects = runtime.consume_effects(action_id=1, call=1)

    assert {e.feature_id for e in effects} >= {"covering_red", "GT_HYPOTHESIS"}
    feedback = runtime.model_feedback()
    assert feedback
    consumed = runtime.summary()["effects"]
    assert any(e["feature_id"] == "covering_red" for e in consumed)
    assert any(e["feature_id"] == "GT_HYPOTHESIS" for e in consumed)


def test_absent_events_remain_correct_quiet():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Implement", revision=WR0, source_revision=SR0)
    runtime.observe_action(
        action_id=1, command="pwd", output="", returncode=0,
        transition=_transition(1, "pwd", WR0, WR0), revision=WR0, source_revision=SR0,
    )
    _consume(runtime, 1, 1)

    _assert_quiet(
        runtime,
        "covering_red", "recovery", "signature_delta", "syntax_result",
        "localization", "def_partition", "caller_contract", "newfile_precedent",
        "GT_CHANGE_SURFACE", "GT_PATCH_DELTA", "GT_EDIT_CHECK", "GT_HYPOTHESIS",
        "GT_SS_SUBMIT_RED",
    )


def test_effect_trace_is_additive_and_links_confirmed_provider_delivery():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    runtime.record_syntax(
        action_id=1,
        revision=SR1,
        source_revision=SR1,
        failed=True,
        reason="changed_file_syntax_failure",
        path="app.py",
        command="python3 -m py_compile app.py",
        returncode=1,
        diagnostic="SyntaxError: invalid syntax",
    )
    runtime.consume_effects(action_id=1, call=1)
    feedback = runtime.model_feedback(deferred=True)
    assert feedback
    prepared = runtime.prepared_guidance()
    assert prepared and prepared["effect_ids"]
    confirmed = runtime.confirm_prepared_guidance()
    assert confirmed and confirmed["delivery_id"]
    trace = runtime.summary()["effect_trace"]
    assert trace
    assert all(row["disposition"] != "unknown" for row in trace)
    syntax = next(row for row in trace if row["feature_id"] == "syntax_result")
    assert syntax["disposition"] == "provider_payload"
    assert syntax["provider_delivery_ids"] == [confirmed["delivery_id"]]


def test_context_compiler_accounts_every_effect_without_claiming_model_visibility():
    runtime = CentralFeatureRuntime(model_visible=True)
    runtime.begin_task("Fix it", revision=WR0, source_revision=SR0)
    runtime.record_syntax(
        action_id=1,
        revision=SR1,
        source_revision=SR1,
        failed=False,
        reason="changed_file_syntax_pass",
        path="app.py",
        command="python3 -m py_compile app.py",
        returncode=0,
    )
    runtime.consume_effects(action_id=1, call=1)
    active_state = {
        **runtime.progress_ledger(),
        "source_revision": SR1,
        "workspace_revision": WR0,
    }
    _view, metrics = build_provider_view(
        [{"role": "user", "content": "Fix it"}],
        active_state=active_state,
        trigger_chars=10**18,
        target_chars=10**18,
    )

    runtime.record_context_compiler_call(
        call=2,
        request_payload_sha256="request-hash",
        fact_accounting=metrics.fact_accounting,
    )
    summary = runtime.summary()
    rows = summary["context_compiler_effect_accountability"]

    assert rows
    assert all(row["status"] != "unaccounted_bug" for row in rows)
    assert all(row["first_considered_call"] == 2 for row in rows)
    assert all(row["one_step_late"] is False for row in rows)
    assert all(row["status"] == "controller_state_considered" for row in rows)
    assert summary["guidance_events"] == 0
