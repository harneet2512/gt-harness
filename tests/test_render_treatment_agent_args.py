from __future__ import annotations

import hashlib
import json
from pathlib import Path

from gt_engine.treatment_adapter import BenchmarkManifest, treatment_from_descriptor
from scripts.render_treatment_agent_args import build_runtime_arguments, main


def test_runtime_arguments_are_derived_from_caller_configuration(tmp_path: Path) -> None:
    descriptor = {
        "adapter_kind": "groundtruth",
        "treatment_id": "candidate",
        "profile_id": "central_relational_v2",
        "preemptive_retrieval": True,
        "relational_context": True,
        "dense_fallback_only": True,
        "semantic_evidence": True,
        "runtime_agent_kwargs": {
            "preflight_mode": "assistive_safe",
            "require_graph_ready": True,
        },
    }

    rendered = build_runtime_arguments(
        descriptor,
        source_sha="a" * 40,
        max_steps=37,
    )

    assert rendered["agent_kwargs"]["step_limit"] == 37
    assert rendered["agent_kwargs"]["treatment_profile"] == "central_relational_v2"
    assert rendered["agent_kwargs"]["enable_relational_context"] is True
    assert rendered["agent_kwargs"]["enable_semantic_evidence"] is True
    assert rendered["agent_kwargs"]["preflight_mode"] == "assistive_safe"
    assert rendered["source_sha"] == "a" * 40
    assert len(rendered["contract_sha256"]) == 64
    assert json.loads(json.dumps(rendered)) == rendered


def test_runtime_arguments_reject_identity_override() -> None:
    descriptor = {
        "adapter_kind": "groundtruth",
        "treatment_id": "candidate",
        "profile_id": "central_relational_v2",
        "preemptive_retrieval": True,
        "relational_context": True,
        "dense_fallback_only": True,
        "runtime_agent_kwargs": {"treatment_profile": "central_pes_v1"},
    }

    try:
        build_runtime_arguments(descriptor, source_sha="b" * 40, max_steps=11)
    except ValueError as exc:
        assert "must not override" in str(exc)
    else:
        raise AssertionError("identity override was accepted")


def test_cli_can_resolve_treatment_only_from_canonical_release_manifest(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "runtime.json"

    assert main(
        [
            "--release-manifest",
            str(root / "eval/release/active_release.json"),
            "--source-sha",
            "a" * 40,
            "--max-steps",
            "100",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["profile_id"] == "central_relational_v3"
    assert payload["agent_kwargs"]["persistent_state_selection_mode"] == "generative"
    assert payload["agent_kwargs"]["enable_replay_capture"] is True


def test_runtime_arguments_bind_caller_benchmark_manifest() -> None:
    descriptor = {
        "adapter_kind": "groundtruth",
        "treatment_id": "candidate",
        "profile_id": "central_relational_v2",
        "preemptive_retrieval": True,
        "relational_context": True,
        "dense_fallback_only": True,
        "semantic_evidence": True,
    }
    pinned = {**descriptor, "source_sha": "c" * 40}
    execution_contract = {
        "task_count": 3,
        "task_order_sha256": hashlib.sha256(b"task-order").hexdigest(),
        "provider_identity": "fixture-model",
        "temperature": 1.0,
        "sampling_parameters": {"temperature": 1.0, "num_retries": 0},
        **{
            field: hashlib.sha256(field.encode()).hexdigest()
            for field in (
                "tool_envelope_sha256",
                "hook_envelope_sha256",
                "embedding_configuration_sha256",
                "hardware_assumptions_sha256",
                "retry_policy_sha256",
                "timeout_policy_sha256",
                "token_accounting_sha256",
            )
        },
    }
    manifest = BenchmarkManifest.create(
        benchmark_id="fixture-suite",
        task_manifest_sha256=hashlib.sha256(b"tasks").hexdigest(),
        model_id="fixture-model",
        scaffold_sha="c" * 40,
        max_steps=29,
        trials_per_task=1,
        execution_contract=execution_contract,
        treatments=(treatment_from_descriptor(pinned),),
    ).as_dict()

    rendered = build_runtime_arguments(
        descriptor,
        source_sha="c" * 40,
        max_steps=29,
        benchmark_manifest=manifest,
    )

    identity = rendered["agent_kwargs"]["benchmark_identity"]
    assert identity["benchmark_id"] == "fixture-suite"
    assert identity["max_steps"] == 29
    assert identity["treatment"]["treatment_id"] == "candidate"
    assert identity["manifest_sha256"] == manifest["manifest_sha256"]
