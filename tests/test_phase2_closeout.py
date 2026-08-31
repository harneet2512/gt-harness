from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_har9_input_digest_matches_git_lf_blob_with_autocrlf(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"], cwd=repo, check=True
    )
    (repo / ".gitattributes").write_bytes(
        b"/receipts/** text eol=lf\n"
    )
    receipt = repo / "receipts" / "audit.json"
    receipt.parent.mkdir()
    receipt.write_bytes(b'{\r\n  "status": "PASS"\r\n}\r\n')

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    issuer = _load("issue_har9_closeout")
    observed = issuer.git_blob_sha256(receipt, repo_root=repo)

    subprocess.run(
        ["git", "add", ".gitattributes", "receipts/audit.json"],
        cwd=repo,
        check=True,
    )
    committed_bytes = subprocess.run(
        ["git", "show", ":receipts/audit.json"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert b"\r\n" not in committed_bytes
    assert observed == hashlib.sha256(committed_bytes).hexdigest()


def test_har9_input_digest_refuses_unpinned_receipt_path(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    receipt = repo / "audit.json"
    receipt.write_bytes(b'{\r\n  "status": "PASS"\r\n}\r\n')

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    issuer = _load("issue_har9_closeout")
    with pytest.raises(ValueError, match="lacks plain UTF-8 LF policy"):
        issuer.git_blob_sha256(receipt, repo_root=repo)


def _certification_rows() -> list[dict[str, str]]:
    with (ROOT / "gt_finalstand" / "language_operation_certification.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        return list(csv.DictReader(stream))


def _language_manifest() -> dict:
    names = sorted({row["registry_identity"] for row in _certification_rows()})
    return {
        "schema": "gt.language_manifest.v1",
        "languages": [
            {
                "name": name,
                "extensions": [f".{name}"],
                "definitions": True,
                "calls": True,
                "imports": True,
                "bodies": True,
                "parameters": True,
                "return_types": True,
                "test_patterns": True,
            }
            for name in names
        ],
    }


def test_live_go_manifest_contract_matches_210_pair_matrix() -> None:
    offline = _load("finalstand_offline")
    assert offline.validate_language_manifest(
        _language_manifest(), _certification_rows()
    ) == []
    broken = _language_manifest()
    broken["languages"] = broken["languages"][:-1]
    assert "exactly 30" in " ".join(
        offline.validate_language_manifest(broken, _certification_rows())
    )


def test_pre_artifact_provenance_binds_inputs_without_claiming_run_success() -> None:
    offline = _load("finalstand_offline")
    offline_bytes = (
        json.dumps(
            {
                "schema": "gt.finalstand.offline_suite.v2",
                "terminal": True,
                "native_graph_battery": {"semantic_artifact_sha256": "a" * 64},
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    receipt = offline.build_pre_artifact_provenance(
        offline_receipt_bytes=offline_bytes,
        compatibility={"source_manifest_sha256": "b" * 64},
        gt_index_bytes=b"binary",
        workflow_bytes=b"workflow",
        groundtruth_commit="c" * 40,
    )
    assert receipt["offline_receipt_sha256"] == hashlib.sha256(offline_bytes).hexdigest()
    assert receipt["binary_sha256"] == hashlib.sha256(b"binary").hexdigest()
    assert receipt["workflow_definition_sha256"] == hashlib.sha256(b"workflow").hexdigest()
    assert receipt["workflow_execution_identity_bound"] is False
    assert set(receipt["missing_immutable_linkage"]) == {
        "harness_execution_commit",
        "github_actions_run_id",
        "github_actions_run_url",
        "uploaded_artifact_bundle_sha256",
    }

    broken = json.dumps({"terminal": False}).encode()
    try:
        offline.build_pre_artifact_provenance(
            offline_receipt_bytes=broken,
            compatibility={"source_manifest_sha256": "b" * 64},
            gt_index_bytes=b"binary",
            workflow_bytes=b"workflow",
            groundtruth_commit="c" * 40,
        )
    except ValueError as exc:
        assert "terminal offline receipt" in str(exc)
    else:
        raise AssertionError("non-terminal offline receipt was accepted")


def test_harness_consumes_generated_native_language_manifest_authority() -> None:
    from gt_engine.generated_typed_capabilities import (
        LANGUAGE_MANIFEST_SHA256,
        REGISTERED_LANGUAGE_IDENTITIES,
    )

    compatibility = json.loads(
        (ROOT / "gt_finalstand" / "language_operation_compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(REGISTERED_LANGUAGE_IDENTITIES) == 30
    assert tuple(sorted(REGISTERED_LANGUAGE_IDENTITIES)) == REGISTERED_LANGUAGE_IDENTITIES
    assert LANGUAGE_MANIFEST_SHA256 == compatibility["source_manifest_sha256"]


def test_forbidden_runtime_scan_detects_registration_and_runtime_matches(
    tmp_path: Path,
) -> None:
    offline = _load("finalstand_offline")
    harness = tmp_path / "harness"
    core = tmp_path / "core"
    (harness / "gt_engine").mkdir(parents=True)
    (core / "src").mkdir(parents=True)
    (harness / "gt_engine" / "bridge.py").write_text(
        "def _rerank_graph_evidence(): pass\n", encoding="utf-8"
    )
    (core / "src" / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
    rules = {
        "rules": [
            {
                "id": "reranker",
                "pattern": "_rerank_graph_evidence",
                "targets": ["harness:gt_engine/bridge.py", "groundtruth:src/safe.py"],
            }
        ]
    }
    receipt = offline.scan_forbidden(rules, {"harness": harness, "groundtruth": core})
    assert receipt["ok"] is False
    assert receipt["findings"] == [
        {
            "rule": "reranker",
            "target": "harness:gt_engine/bridge.py",
            "line": 1,
            "kind": "definition",
            "import_chain": ["harness:gt_engine/bridge.py"],
        }
    ]


def test_offline_action_evidence_freshness_leak_determinism_and_cost_cases() -> None:
    offline = _load("finalstand_offline")
    cases = json.loads(
        (ROOT / "gt_finalstand" / "offline_cases.json").read_text(encoding="utf-8")
    )
    receipt = offline.run_offline_cases(cases)
    assert receipt["ok"] is True
    assert receipt["failures"] == []
    assert receipt["counts"] == {
        "action_identifiability": 20,
        "typed_action_identifiability": 3,
        "evidence_sufficiency": 3,
        "freshness": 2,
        "observation_leak": 2,
        "determinism": 2,
        "cost_samples": 3,
    }
    assert receipt["cost"]["canonical_json_p95_ns"] >= 0


def test_provider_free_battery_is_honest_without_native_indexer() -> None:
    offline = _load("finalstand_offline")
    cases = json.loads(
        (ROOT / "gt_finalstand" / "offline_cases.json").read_text(encoding="utf-8")
    )
    receipt = offline.run_provider_free_battery(cases)
    assert receipt["ok"] is True
    assert receipt["terminal"] is False
    assert receipt["runtime_probes"] == {
        "ok": True,
        "failures": [],
        "typed_exact_literal_executed": True,
        "freshness_invalidation_executed": True,
        "sentinel_clean_replacement_certified": True,
        "sentinel_leak_rejected": True,
        "stock_bash_parse_parity": True,
        "gt_off_parity_regression": "tests/test_miniswe_runtime.py::"
        "test_gt_off_never_attaches_terminal_or_provider_authorities",
        "provider_calls": 0,
    }
    assert receipt["native_graph_battery"]["cold_builds"] == 0
    assert receipt["limitations"]


def test_graph_semantic_snapshot_excludes_only_declared_volatile_data(tmp_path) -> None:
    offline = _load("finalstand_offline")
    snapshots = []
    for suffix, indexed_at, root in (("a", "time-1", "/one"), ("b", "time-2", "/two")):
        database = tmp_path / f"{suffix}.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE file_hashes (file_path TEXT, content_hash TEXT, indexed_at TEXT);"
            "CREATE TABLE project_meta (key TEXT, value TEXT);"
            "CREATE TABLE binary_payload (id INTEGER, payload BLOB);"
        )
        connection.execute(
            "INSERT INTO file_hashes VALUES (?, ?, ?)", ("a.py", "hash", indexed_at)
        )
        connection.executemany(
            "INSERT INTO project_meta VALUES (?, ?)",
            (("root", root), ("build_time_utc", indexed_at), ("schema_version", "v1")),
        )
        connection.execute("INSERT INTO binary_payload VALUES (?, ?)", (1, b"\x00\xff"))
        connection.commit()
        connection.close()
        snapshots.append(offline._semantic_graph_snapshot(database))
    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["binary_payload"]["rows"] == [[1, {"bytes_hex": "00ff"}]]


def test_six_arm_dry_run_makes_zero_provider_calls() -> None:
    experiment = _load("phase2_experiment")
    manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    receipt = experiment.dry_run(manifest)
    assert receipt["ok"] is True
    assert receipt["executed"] is False
    assert receipt["provider_calls"] == 0
    assert tuple(receipt["planned_arms"]) == experiment.ARMS


def test_canonical_ten_smoke_plan_is_deterministic_and_provider_free() -> None:
    experiment = _load("phase2_experiment")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )

    first = experiment.build_execution_plan(phase_manifest, smoke_manifest)
    second = experiment.build_execution_plan(phase_manifest, smoke_manifest)

    assert first == second
    assert first["schema"] == "gt.phase2.execution_plan.v1"
    assert first["executed"] is False
    assert first["provider_calls"] == 0
    assert first["task_count"] == 10
    assert first["trial_count"] == 60
    assert len(first["trials"]) == 60
    assert len({row["trial_id"] for row in first["trials"]}) == 60
    assert all(row["matched_pair_id"] for row in first["trials"])
    assert {
        (row["task_id"], row["arm"])
        for row in first["trials"]
    } == {
        (task_id, arm)
        for task_id in smoke_manifest["tasks"]
        for arm in experiment.ARMS
    }


def test_ten_smoke_plan_names_every_execution_blocker_without_fake_receipts() -> None:
    experiment = _load("phase2_experiment")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )

    plan = experiment.build_execution_plan(phase_manifest, smoke_manifest)

    assert plan["ready_for_authorized_execution"] is False
    assert plan["authorization_receipt"] is None
    assert plan["provider_receipt_root_sha256"] is None
    assert "template_manifest_not_frozen" in plan["blockers"]
    assert "arm_executors_not_bound" in plan["blockers"]
    assert plan["unbound_arms"] == list(experiment.ARMS)


def test_ten_smoke_plan_reports_malformed_numeric_fields_without_crashing() -> None:
    experiment = _load("phase2_experiment")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest["temperature"] = "not-a-number"

    plan = experiment.build_execution_plan(phase_manifest, smoke_manifest)

    assert plan["task_count"] == 0
    assert plan["trial_count"] == 0
    assert "invalid_smoke_manifest" in plan["blockers"]
    assert "canonical smoke temperature mismatch" in plan["validation_errors"]


def test_plan_cli_fails_closed_unless_inspection_is_explicit(
    tmp_path: Path, monkeypatch
) -> None:
    experiment = _load("phase2_experiment")
    manifest = ROOT / "gt_finalstand" / "phase2_experiment_manifest.json"
    tasks = ROOT / "config" / "tb2_deepseek_smoke10.json"
    output = tmp_path / "plan.json"
    base = [
        "phase2_experiment.py", "plan", "--manifest", str(manifest),
        "--task-manifest", str(tasks), "--out", str(output),
    ]
    monkeypatch.setattr(sys, "argv", base)
    assert experiment.main() == 2
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    monkeypatch.setattr(sys, "argv", base + ["--inspect"])
    assert experiment.main() == 0


def test_arm_bindings_reject_arbitrary_and_secret_shaped_fields(tmp_path: Path) -> None:
    experiment = _load("phase2_experiment")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )
    bindings = {
        arm: {
            "schema": "gt.phase2.arm_binding.v1",
            "runner": "scripts/fake.py",
            "runner_sha256": "a" * 64,
            "agent": "eval.miniswe_agent:MiniSweGtAgent",
            "mode": arm,
            "provider_calls_per_iteration": 1,
            "OPENAI_API_KEY": "sentinel-not-a-real-secret",
        }
        for arm in experiment.ARMS
    }

    plan = experiment.build_execution_plan(
        phase_manifest, smoke_manifest, bindings, repository_root=tmp_path
    )

    assert plan["ok"] is False
    assert plan["ready_for_authorized_execution"] is False
    assert "arm_executors_invalid" in plan["blockers"]
    assert all("unsupported keys" in issue for issue in plan["binding_errors"])
    assert all(row["runner_binding"] is None for row in plan["trials"])


def test_arm_binding_container_must_be_an_object(tmp_path: Path) -> None:
    experiment = _load("phase2_experiment")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )

    plan = experiment.build_execution_plan(
        phase_manifest, smoke_manifest, ["not-an-object"], repository_root=tmp_path
    )

    assert plan["ok"] is False
    assert plan["unbound_arms"] == list(experiment.ARMS)
    assert plan["binding_errors"] == ["arm bindings must be an object"]


def test_strict_static_arm_bindings_can_make_a_frozen_plan_ready(tmp_path: Path) -> None:
    experiment = _load("phase2_experiment")
    runner = tmp_path / "scripts" / "phase2_trial.py"
    runner.parent.mkdir()
    runner.write_text(
        "PHASE2_SUPPORTED_MODES = " + repr(experiment.ARMS) + "\n",
        encoding="utf-8",
    )
    runner_sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    phase_manifest["template_only"] = False
    phase_manifest["frozen"] = {
        "model": smoke_manifest["model"],
        "prompt_sha256": "a" * 64,
        "task_manifest_sha256": experiment._canonical_sha256(smoke_manifest),
        "environment_sha256": "b" * 64,
        "budget_sha256": "c" * 64,
    }
    phase_manifest["missing_run_policy"] = "fail_closed"
    phase_manifest["multiplicity_policy"] = "holm"
    bindings = {
        arm: {
            "schema": "gt.phase2.arm_binding.v1",
            "runner": "scripts/phase2_trial.py",
            "runner_sha256": runner_sha,
            "agent": (
                "eval.miniswe_agent:MiniSweAgent"
                if arm == "stock_raw"
                else "eval.miniswe_agent:MiniSweGtAgent"
            ),
            "mode": arm,
            "provider_calls_per_iteration": 1,
        }
        for arm in experiment.ARMS
    }

    plan = experiment.build_execution_plan(
        phase_manifest, smoke_manifest, bindings, repository_root=tmp_path
    )

    assert plan["ok"] is True
    assert plan["blockers"] == []
    assert plan["ready_for_authorized_execution"] is True
    assert plan["unbound_arms"] == []
    assert plan["binding_errors"] == []


def test_finalstand_validator_semantically_recomputes_execution_plan() -> None:
    experiment = _load("phase2_experiment")
    validator = _load("validate_gt_finalstand")
    phase_manifest = json.loads(
        (ROOT / "gt_finalstand" / "phase2_experiment_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_manifest = json.loads(
        (ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )
    plan = experiment.build_execution_plan(phase_manifest, smoke_manifest)
    assert validator._valid_phase2_execution_plan(plan) is True
    plan["trials"][0]["trial_id"] = "0" * 64
    assert validator._valid_phase2_execution_plan(plan) is False


def test_paired_analysis_requires_provider_bound_authorization() -> None:
    experiment = _load("phase2_experiment")
    header = (
        "task_id,matched_pair_id,arm,solved,exploration_actions,"
        "raw_bytes_consumed,false_interventions,stale_incomplete_incidents,verified_by\n"
    )
    lines = [header]
    for task_id, pair_id, stock_solved, stock_exploration in (
        ("t1", "p1", 0, 5),
        ("t2", "p2", 1, 6),
    ):
        for arm in experiment.ARMS:
            solved = 1 if arm == "typed_interface" else stock_solved
            exploration = stock_exploration - 2 if arm == "typed_interface" else stock_exploration
            lines.append(
                f"{task_id},{pair_id},{arm},{solved},{exploration},100,0,0,verifier-a\n"
            )
    rows = list(csv.DictReader(io.StringIO("".join(lines))))
    try:
        experiment.analyze(rows, {"authorized": False})
    except ValueError as exc:
        assert "provider-bound" in str(exc)
    else:
        raise AssertionError("analysis accepted an unauthenticated run")
    receipt = {
        "schema": "gt.phase2.execution_receipt.v1",
        "authorized": True,
        "provider_receipt_root_sha256": "f" * 64,
        "manifest_sha256": "e" * 64,
        "task_count": 2,
    }
    analysis = experiment.analyze(rows, receipt)
    typed = analysis["comparisons"]["typed_interface"]
    assert analysis["paid_run"] is True
    assert typed["paired_tasks"] == 2
    assert typed["solve_rate_delta"] == 0.5
    assert typed["exploration_delta"] == -2.0


def test_promotion_refuses_without_every_terminal_receipt() -> None:
    promotion = _load("phase2_promotion")
    refusal = promotion.decide(
        None,
        {"schema": "gt.finalstand.offline_suite.v1", "ok": True},
        None,
        None,
    )
    assert refusal["promote"] is False
    assert refusal["mutation_performed"] is False
    assert refusal["reasons"] == [
        "authorized_paired_experiment_missing",
        "go_source_binary_receipt_missing_or_failed",
        "rollback_rehearsal_missing_or_failed",
    ]


def test_promotion_rejects_nonterminal_v2_offline_receipt() -> None:
    promotion = _load("phase2_promotion")
    refusal = promotion.decide(
        None,
        {"schema": "gt.finalstand.offline_suite.v2", "ok": True, "terminal": False},
        None,
        None,
    )
    assert "offline_validation_missing_or_failed" in refusal["reasons"]


def test_promotion_gate_can_select_but_never_mutates_configuration() -> None:
    promotion = _load("phase2_promotion")
    analysis = {
        "schema": "gt.phase2.analysis.v1",
        "paid_run": True,
        "execution_receipt_sha256": "d" * 64,
        "matched_identities": 50,
        "comparisons": {
            "typed_interface": {
                "paired_tasks": 50,
                "solve_rate_delta_ci95": [0.0, 0.1],
                "exploration_delta_ci95": [-4.0, -1.0],
            }
        },
    }
    offline = {"schema": "gt.finalstand.offline_suite.v1", "ok": True}
    go = {
        "schema": "gt.go_workflow_receipt.v1",
        "ok": True,
        "commit_sha": "a" * 40,
        "source_sha256": "b" * 64,
        "binary_sha256": "c" * 64,
    }
    rollback = {"schema": "gt.rollback_receipt.v1", "ok": True, "rehearsed": True}
    result = promotion.decide(analysis, offline, go, rollback)
    assert result["promote"] is True
    assert result["eligible_arms"] == ["typed_interface"]
    assert result["mutation_performed"] is False


def test_clean_machine_and_rollback_runbooks_are_structurally_complete() -> None:
    offline = _load("finalstand_offline")
    paths = [
        ROOT / "gt_finalstand" / "CLEAN_MACHINE_RUNBOOK.md",
        ROOT / "gt_finalstand" / "ROLLBACK_RUNBOOK.md",
    ]
    assert offline.validate_runbooks(paths) == []
