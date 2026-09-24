"""C6 option A: every ``--ak`` treatment knob is declared and consumed.

The consumption contract lives in ``gt_engine.treatment_flags``: knobs map to
real canonical mechanisms, record their inert/moot state honestly, or refuse
by name when the asserted mechanism does not exist on this build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

import pytest

from gt_engine.treatment_flags import (
    TreatmentFlagRefusal,
    resolve_deadline_seconds,
    resolve_treatment_flags,
)


def _ns(**over):
    base = dict(
        integration_mode="", policy_mode="", preflight_mode="",
        treatment_profile="", treatment_runtime_contract_path="",
        preemptive_retrieval_model_dir="", require_graph_ready="",
        gt_request_token_budget=0, repository_initial_index_timeout_sec=0,
        repository_refresh_timeout_sec=0,
        persistent_state_bootstrap_timeout_sec=0,
        persistent_state_bootstrap_input_tokens=0,
        persistent_state_bootstrap_output_tokens=0,
        persistent_state_context_tokens=0,
        execution_budget_sec=0, time_budget_seconds=1, step_limit=100,
        task_id="", product_source_sha="",
        gt_off=False, gt_mode="advisory",
        **{f"enable_{n}": "" for n in (
            "all_features", "submit_readiness", "repository_intelligence",
            "lint", "feature_guidance", "context_frontier",
            "preemptive_retrieval", "persistent_execution_state",
            "context_compaction", "completion_controller", "progress_control",
            "adaptive_validation_timeout", "shadow_submit_gate",
            "decision_sufficiency", "task_start_advisory", "replay_capture",
        )},
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_integration_mode_off_wires_to_gt_off():
    args = _ns(integration_mode="off")
    resolution = resolve_treatment_flags(args)
    assert args.gt_off is True
    assert args.gt_mode == "off"
    assert resolution["integration_mode"]["disposition"] == "wired:gt_off"


def test_integration_mode_off_normalizes_feature_knobs_to_moot():
    args = _ns(integration_mode="off", enable_all_features="false",
               enable_repository_intelligence="false",
               require_graph_ready="false", policy_mode="off",
               preflight_mode="off", enable_replay_capture="true")
    resolution = resolve_treatment_flags(args)
    assert resolution["enable_all_features"]["disposition"] == "moot_gt_off"
    assert resolution["require_graph_ready"]["disposition"] == "moot_gt_off"
    assert resolution["policy_mode"]["disposition"] == "moot_gt_off"
    assert resolution["enable_replay_capture"]["disposition"] == "moot_gt_off"


def test_integration_mode_active_is_advisory():
    args = _ns(integration_mode="active")
    resolution = resolve_treatment_flags(args)
    assert args.gt_off is False
    assert resolution["integration_mode"]["disposition"] == "wired:advisory"


def test_integration_mode_unknown_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="integration_mode_invalid"):
        resolve_treatment_flags(_ns(integration_mode="certified_active"))


def test_integration_mode_active_conflicts_with_gt_off():
    with pytest.raises(TreatmentFlagRefusal, match="active_vs_gt_off"):
        resolve_treatment_flags(_ns(integration_mode="active", gt_off=True))


def test_integration_mode_off_conflicts_with_nondefault_gt_mode():
    # ``advisory`` is the runner default and yields to integration_mode=off;
    # a deliberately different --gt-mode is a real conflict.
    with pytest.raises(TreatmentFlagRefusal, match="off_vs_gt_mode"):
        resolve_treatment_flags(_ns(integration_mode="off", gt_mode="shadow"))


def test_execution_budget_sec_wires_deadline():
    args = _ns(execution_budget_sec=5400)
    resolution = resolve_treatment_flags(args)
    assert args.time_budget_seconds == 5400
    assert resolution["execution_budget_sec"]["disposition"] == "wired:deadline"


def test_budget_conflict_refuses():
    args = _ns(execution_budget_sec=5400, time_budget_seconds=300)
    with pytest.raises(TreatmentFlagRefusal, match="budget_conflict"):
        resolve_deadline_seconds(args)


def test_budget_prefers_explicit_time_budget():
    args = _ns(time_budget_seconds=300)
    assert resolve_deadline_seconds(args) == 300


def test_absent_mechanism_true_refuses_by_name():
    args = _ns(enable_persistent_execution_state="true")
    with pytest.raises(
        TreatmentFlagRefusal,
        match="treatment_knob_unsupported:enable_persistent_execution_state",
    ):
        resolve_treatment_flags(args)


def test_absent_mechanism_false_is_inert():
    args = _ns(enable_persistent_execution_state="false",
               enable_lint="false", enable_task_start_advisory="false")
    resolution = resolve_treatment_flags(args)
    for name in ("enable_persistent_execution_state", "enable_lint",
                 "enable_task_start_advisory"):
        assert resolution[name]["disposition"] == "inert:mechanism_absent"


def test_present_mechanism_true_is_satisfiable():
    args = _ns(enable_repository_intelligence="true",
               enable_submit_readiness="true",
               enable_context_compaction="true",
               enable_decision_sufficiency="true",
               enable_all_features="true", require_graph_ready="true")
    resolution = resolve_treatment_flags(args)
    for name in ("enable_repository_intelligence", "enable_submit_readiness",
                 "enable_context_compaction", "enable_decision_sufficiency",
                 "enable_all_features", "require_graph_ready"):
        assert resolution[name]["disposition"].startswith("satisfiable")


def test_present_mechanism_false_refuses_under_gt_on():
    with pytest.raises(
        TreatmentFlagRefusal,
        match="treatment_knob_unsupported:enable_repository_intelligence",
    ):
        resolve_treatment_flags(_ns(enable_repository_intelligence="false"))


def test_policy_mode_certified_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="policy_mode"):
        resolve_treatment_flags(_ns(policy_mode="certified_active"))


def test_treatment_profile_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="treatment_profile"):
        resolve_treatment_flags(_ns(treatment_profile="central_relational_v2"))


def test_persistent_state_subknobs_refuse():
    with pytest.raises(TreatmentFlagRefusal, match="persistent_state"):
        resolve_treatment_flags(
            _ns(persistent_state_bootstrap_timeout_sec=45))


def test_require_graph_ready_true_under_off_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="require_graph_ready"):
        resolve_treatment_flags(
            _ns(integration_mode="off", require_graph_ready="true"))


def test_require_graph_ready_false_under_gt_on_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="require_graph_ready"):
        resolve_treatment_flags(_ns(require_graph_ready="false"))


def test_replay_capture_is_record_only():
    args = _ns(enable_replay_capture="true")
    resolution = resolve_treatment_flags(args)
    assert resolution["enable_replay_capture"]["disposition"] == (
        "record_only:declared_unapplied")


def test_index_timeout_wires_env():
    args = _ns(repository_initial_index_timeout_sec=60)
    try:
        resolution = resolve_treatment_flags(args)
        assert os.environ["GT_INDEX_TIMEOUT_SECONDS"] == "60"
        assert resolution["repository_initial_index_timeout_sec"][
            "disposition"] == "wired:GT_INDEX_TIMEOUT_SECONDS"
    finally:
        os.environ.pop("GT_INDEX_TIMEOUT_SECONDS", None)


def test_model_dir_missing_is_inert(tmp_path):
    args = _ns(preemptive_retrieval_model_dir=str(tmp_path / "absent"))
    resolution = resolve_treatment_flags(args)
    assert resolution["preemptive_retrieval_model_dir"]["disposition"].startswith(
        "inert")


def test_model_dir_present_wires_env(tmp_path):
    args = _ns(preemptive_retrieval_model_dir=str(tmp_path))
    try:
        resolution = resolve_treatment_flags(args)
        assert os.environ["GT_DENSE_MODEL_DIR"] == str(tmp_path)
        assert resolution["preemptive_retrieval_model_dir"]["disposition"] == (
            "wired:GT_DENSE_MODEL_DIR")
    finally:
        os.environ.pop("GT_DENSE_MODEL_DIR", None)


def _contract(tmp_path, kwargs):
    doc = {"agent_kwargs": kwargs, "schema": "test.v1"}
    digest = hashlib.sha256(
        json.dumps(doc, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8", "surrogatepass")
    ).hexdigest()
    doc["contract_sha256"] = digest
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_contract_merges_as_default(tmp_path):
    path = _contract(tmp_path, {"integration_mode": "off",
                                "enable_all_features": "false"})
    args = _ns(treatment_runtime_contract_path=path)
    resolution = resolve_treatment_flags(args)
    assert args.gt_off is True
    assert resolution["integration_mode"]["value"] == "off"


def test_contract_explicit_cli_wins(tmp_path):
    path = _contract(tmp_path, {"integration_mode": "off"})
    args = _ns(treatment_runtime_contract_path=path,
               integration_mode="active")
    resolution = resolve_treatment_flags(args)
    assert args.gt_off is False
    assert resolution["integration_mode"]["value"] == "active"


def test_contract_digest_mismatch_refuses(tmp_path):
    path = _contract(tmp_path, {"integration_mode": "off"})
    doc = json.loads(open(path).read())
    doc["agent_kwargs"]["integration_mode"] = "active"
    open(path, "w").write(json.dumps(doc))
    with pytest.raises(TreatmentFlagRefusal, match="digest_mismatch"):
        resolve_treatment_flags(_ns(treatment_runtime_contract_path=path))


def test_contract_missing_refuses():
    with pytest.raises(TreatmentFlagRefusal, match="contract_unreadable"):
        resolve_treatment_flags(
            _ns(treatment_runtime_contract_path="/nonexistent/c.json"))


def test_baseline_profile_args_all_consumed():
    """The exact baseline-arm argv must resolve without a single silent drop."""
    args = _ns(
        integration_mode="off", policy_mode="off", preflight_mode="off",
        enable_all_features="false", enable_submit_readiness="false",
        enable_repository_intelligence="false", require_graph_ready="false",
        enable_lint="false", enable_feature_guidance="false",
        enable_context_frontier="false", enable_preemptive_retrieval="false",
        enable_persistent_execution_state="false",
        enable_context_compaction="false", enable_completion_controller="false",
        enable_progress_control="false",
        enable_adaptive_validation_timeout="false",
        enable_decision_sufficiency="false",
        enable_replay_capture="true", enable_task_start_advisory="false",
        step_limit=50, execution_budget_sec=5400,
    )
    resolution = resolve_treatment_flags(args)
    assert args.gt_off is True
    assert args.time_budget_seconds == 5400
    assert resolution["enable_replay_capture"]["disposition"] == "moot_gt_off"
    assert len(resolution) >= 30


def test_certified_full_profile_refuses_honestly():
    """The treatment arm asserts mechanisms canonical does not carry."""
    args = _ns(
        integration_mode="active", policy_mode="certified_active",
        preflight_mode="shadow", treatment_profile="central_relational_v2",
        enable_all_features="true", enable_lint="true",
    )
    with pytest.raises(TreatmentFlagRefusal, match="treatment_knob"):
        resolve_treatment_flags(args)


def test_cli_flags_cover_workflow_knobs():
    """Every --ak name in the workflow yaml has a declared CliFlag.

    Reads the ``CliFlag(...)``/``EnvVar(...)`` ``kwarg=`` literals out of
    ``eval/miniswe_agent.py`` by AST so the coverage check runs in a clean
    verify venv — importing the module requires ``harbor`` (the ``eval``
    extra), which the documented verify install does not include."""
    import ast
    import re
    from pathlib import Path

    yaml = Path(
        ".github/workflows/deepswe_miniswe_central.yml"
    ).read_text(encoding="utf-8")
    knob_names = set(re.findall(r"--ak ([a-z_]+)=", yaml))
    tree = ast.parse(
        Path("eval/miniswe_agent.py").read_text(encoding="utf-8")
    )
    declared = {
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"CliFlag", "EnvVar"}
        for kw in node.keywords
        if kw.arg == "kwarg" and isinstance(kw.value, ast.Constant)
    }
    assert declared, "no CliFlag/EnvVar kwarg literals parsed"
    missing = knob_names - declared
    assert not missing, f"undelcared --ak knobs: {sorted(missing)}"
