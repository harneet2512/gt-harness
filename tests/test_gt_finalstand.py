from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import re
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_validator():
    path = ROOT / "scripts" / "validate_gt_finalstand.py"
    spec = importlib.util.spec_from_file_location("validate_gt_finalstand", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalstand_is_machine_valid() -> None:
    result = _load_validator().validate()
    assert result["errors"] == []
    assert result["counts"] == {
        "direct": 17,
        "role_audit": 129,
        "languages": 30,
        "language_operation_pairs": 210,
        "todo_statuses": 26,
    }


def test_single_witness_closes_fs024_without_claiming_population_efficacy() -> None:
    validator = _load_validator()
    statuses = validator._rows("closeout_status.csv")
    fs024 = next(row for row in statuses if row["todo"] == "FS-024")
    analysis = validator._json("receipts/fs024_single_witness_analysis.json")
    execution = validator._json("receipts/fs024_single_witness_execution.json")
    assert fs024["status"] == "COMPLETE"
    assert analysis["matched_tasks"] == 1
    assert analysis["inferential_claim"] is False
    assert validator._valid_single_witness_analysis(analysis)
    assert validator._valid_single_witness_execution(
        execution,
        ROOT / "gt_finalstand" / "receipts" / "fs024_single_witness_analysis.json",
    )


def test_live_todo_is_current_only_and_preserves_history_in_archive() -> None:
    live = (ROOT / "gt_finalstand" / "LIVE_TODO.md").read_text(encoding="utf-8")
    history = (ROOT / "gt_finalstand" / "LIVE_TODO_HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "## Checkpoints" not in live
    assert "25 `COMPLETE`, 0 `IN_PROGRESS`, 1 `REMOVED`" in live
    assert "LIVE_TODO_HISTORY.md" in live
    assert "historical and superseded" in history.lower()
    assert "### 2026-08-01T20:41:33Z" in history
    assert "### 2026-08-01T22:25:25Z" in history
    assert "3 `COMPLETE`, 23 `IN_PROGRESS`, 0 `REMOVED`" not in live


def _validate_with_status(module, todo: str, status: str) -> dict[str, object]:
    original_rows = module._rows
    statuses = copy.deepcopy(original_rows("closeout_status.csv"))
    next(row for row in statuses if row["todo"] == todo)["status"] = status
    module._rows = lambda name: statuses if name == "closeout_status.csv" else original_rows(name)
    return module.validate()


def test_fs025_completion_requires_conservative_keep_evidence() -> None:
    validator = _load_validator()
    analysis_path = ROOT / "gt_finalstand" / "receipts" / "fs024_single_witness_analysis.json"
    execution_path = ROOT / "gt_finalstand" / "receipts" / "fs024_single_witness_execution.json"
    promotion = validator._json("receipts/fs025_promotion_decision.json")
    assert validator._valid_keep_decision(
        promotion, execution_path, analysis_path
    )
    assert promotion["decision"] == "KEEP"
    assert promotion["mutation_performed"] is False
    assert promotion["default_behavior"]["groundtruth_default_enabled"] is False


def test_fs026_completion_is_hash_bound_and_bounded() -> None:
    validator = _load_validator()
    receipts = ROOT / "gt_finalstand" / "receipts"
    attestation = validator._json("receipts/fs026_final_attestation.json")
    assert validator._valid_final_attestation(
        attestation,
        receipts / "fs024_single_witness_execution.json",
        receipts / "fs024_single_witness_analysis.json",
        receipts / "fs025_promotion_decision.json",
    )
    assert attestation["claims"]["benchmark_wide_efficacy"] is False
    assert attestation["terminal_rows"] == {
        "complete": 25,
        "in_progress": 0,
        "removed": 1,
        "total": 26,
    }


def test_promotion_refusal_uses_terminal_offline_receipt() -> None:
    receipt = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "promotion_refusal.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["promote"] is False
    assert receipt["mutation_performed"] is False
    assert receipt["reasons"] == [
        "authorized_paired_experiment_missing",
        "go_source_binary_receipt_missing_or_failed",
        "rollback_rehearsal_missing_or_failed",
    ]


def test_fs023_provenance_cross_binds_terminal_workflow_and_artifact() -> None:
    validator = _load_validator()
    receipt = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "fs023_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "provider_free_workflow.json").read_text(
            encoding="utf-8"
        )
    )
    assert validator._valid_fs023_provenance(receipt, workflow)
    assert receipt["workflow_execution_identity_bound"] is True
    assert receipt["missing_immutable_linkage"] == []
    assert receipt["github_actions_run_id"] == "30729901088"
    assert receipt["uploaded_artifact_id"] == 8827623572


def test_fs023_provenance_rejects_authority_hash_mismatches() -> None:
    validator = _load_validator()
    receipt = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "fs023_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "provider_free_workflow.json").read_text(
            encoding="utf-8"
        )
    )
    semantic_mismatch = copy.deepcopy(receipt)
    semantic_mismatch["semantic_artifact_sha256"] = "0" * 64
    assert not validator._valid_fs023_provenance(semantic_mismatch, workflow)

    source_mismatch = copy.deepcopy(receipt)
    source_mismatch["source_manifest_sha256"] = "0" * 64
    assert not validator._valid_fs023_provenance(source_mismatch, workflow)


def test_fs023_is_complete_with_external_workflow_identity() -> None:
    statuses = _load_validator()._rows("closeout_status.csv")
    fs023 = next(row for row in statuses if row["todo"] == "FS-023")
    assert fs023["status"] == "COMPLETE"
    assert "none for the provider-free immutable workflow criterion" in fs023[
        "missing_proof"
    ].lower()


def test_fs023_completion_is_rejected_while_workflow_linkage_is_missing() -> None:
    validator = _load_validator()
    original_optional_json = validator._optional_json
    validator._optional_json = lambda name: (
        None
        if name == "receipts/provider_free_workflow.json"
        else original_optional_json(name)
    )
    result = validator.validate()
    assert any(
        error.startswith("FS-023 cannot be COMPLETE")
        and "external workflow execution identity" in error
        for error in result["errors"]
    )


def _future_fs023_receipts(validator):
    provenance = json.loads(
        (ROOT / "gt_finalstand" / "receipts" / "fs023_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    provenance.update(
        {
            "workflow_execution_identity_bound": True,
            "missing_immutable_linkage": [],
            "harness_execution_commit": "a" * 40,
            "github_actions_run_id": "123456789",
            "github_actions_run_attempt": 1,
            "github_actions_run_url": (
                "https://github.com/example/gt-harness/actions/runs/123456789"
            ),
            "github_repository": "example/gt-harness",
            "github_workflow_ref": (
                "example/gt-harness/.github/workflows/"
                "gt_finalstand_provider_free.yml@refs/heads/main"
            ),
            "github_workflow_sha": "b" * 40,
            "uploaded_artifact_id": 987654,
            "uploaded_artifact_bundle_sha256": "c" * 64,
            "verification_source": "github_actions_artifacts_api",
        }
    )
    receipt_inputs = {}
    for name in (
        "offline_suite.json",
        "language_manifest.json",
        "forbidden_scan.json",
        "runbook_validation.json",
        "experiment_dry_run.json",
        "experiment_execution_plan.json",
    ):
        path = ROOT / "gt_finalstand" / "receipts" / name
        content = path.read_bytes() if path.is_file() else b"{}\n"
        receipt_inputs[name] = hashlib.sha256(
            validator._normalized_text_bytes(content)
        ).hexdigest()
    workflow = {
        "schema": "gt.provider_free_workflow_receipt.v1",
        "ok": True,
        "job_status": "success",
        "github_actions": True,
        "event_name": "workflow_dispatch",
        "repository": "example/gt-harness",
        "run_id": "123456789",
        "run_attempt": 1,
        "run_url": "https://github.com/example/gt-harness/actions/runs/123456789",
        "workflow_ref": (
            "example/gt-harness/.github/workflows/"
            "gt_finalstand_provider_free.yml@refs/heads/main"
        ),
        "workflow_sha": "b" * 40,
        "harness_commit": "a" * 40,
        "groundtruth_commit": provenance["recorded_groundtruth_commit"],
        "receipt_inputs": receipt_inputs,
    }
    return provenance, workflow


def test_fs023_future_terminal_provenance_requires_cross_bound_actions_receipt() -> None:
    validator = _load_validator()
    provenance, workflow = _future_fs023_receipts(validator)
    validator._github_api_confirms_provenance = lambda _p, _w: True
    assert validator._valid_fs023_provenance(provenance, workflow)
    assert validator._fs023_terminal_ready(provenance, workflow)

    validator._github_api_confirms_provenance = lambda _p, _w: False
    assert not validator._valid_fs023_provenance(provenance, workflow)
    validator._github_api_confirms_provenance = lambda _p, _w: True

    assert not validator._valid_fs023_provenance(provenance, None)
    local_workflow = copy.deepcopy(workflow)
    local_workflow["github_actions"] = False
    assert not validator._valid_fs023_provenance(provenance, local_workflow)

    mismatched_url = copy.deepcopy(provenance)
    mismatched_url["github_actions_run_url"] = (
        "https://github.com/example/gt-harness/actions/runs/999"
    )
    assert not validator._valid_fs023_provenance(mismatched_url, workflow)

    fabricated_source = copy.deepcopy(provenance)
    fabricated_source["verification_source"] = "local"
    assert not validator._valid_fs023_provenance(fabricated_source, workflow)


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    return output.getvalue()


def _mock_github_artifact(validator, provenance, workflow, *, extra_inner=()):
    workflow_path = ROOT / ".github" / "workflows" / "gt_finalstand_provider_free.yml"
    compatibility = ROOT / "gt_finalstand" / "language_operation_compatibility.json"
    receipt_entries = []
    for name in workflow["receipt_inputs"]:
        path = ROOT / "gt_finalstand" / "receipts" / name
        receipt_entries.append(
            (
                f"receipts/{name}",
                validator._normalized_text_bytes(path.read_bytes())
                if path.is_file()
                else b"{}\n",
            )
        )
    inner_entries = [
        *receipt_entries,
        (
            "receipts/provider_free_workflow.json",
            (json.dumps(workflow, indent=2, sort_keys=True) + "\n").encode(),
        ),
        (
            ".github/workflows/gt_finalstand_provider_free.yml",
            validator._normalized_text_bytes(workflow_path.read_bytes()),
        ),
        (
            "language_operation_compatibility.json",
            validator._normalized_text_bytes(compatibility.read_bytes()),
        ),
        *extra_inner,
    ]
    inner = _zip_bytes(inner_entries)
    outer = _zip_bytes([("provider-free-bundle.zip", inner)])
    provenance["uploaded_artifact_bundle_sha256"] = hashlib.sha256(outer).hexdigest()
    run = {
        "id": 123456789,
        "html_url": provenance["github_actions_run_url"],
        "head_sha": provenance["harness_execution_commit"],
        "event": "workflow_dispatch",
        "conclusion": "success",
        "run_attempt": 1,
        "path": ".github/workflows/gt_finalstand_provider_free.yml",
    }
    artifact = {
        "id": 987654,
        "name": "gt-finalstand-provider-free-123456789",
        "digest": f"sha256:{provenance['uploaded_artifact_bundle_sha256']}",
        "expired": False,
        "size_in_bytes": len(outer),
        "archive_download_url": "https://api.github.com/mock/artifact.zip",
        "workflow_run": {"id": 123456789},
    }
    contents = {
        "encoding": "base64",
        "content": base64.b64encode(
            validator._normalized_text_bytes(workflow_path.read_bytes())
        ).decode(),
    }
    return run, artifact, contents, outer


def test_fs023_github_api_confirmation_verifies_run_artifact_and_members(
    monkeypatch, tmp_path: Path,
) -> None:
    validator = _load_validator()
    provenance, workflow = _future_fs023_receipts(validator)
    run, artifact, contents, outer = _mock_github_artifact(
        validator, provenance, workflow
    )
    isolated_finalstand = tmp_path / "gt_finalstand"
    isolated_receipts = isolated_finalstand / "receipts"
    isolated_receipts.mkdir(parents=True)
    (isolated_finalstand / "language_operation_compatibility.json").write_bytes(
        (ROOT / "gt_finalstand" / "language_operation_compatibility.json").read_bytes()
    )
    (isolated_receipts / "offline_suite.json").write_bytes(
        (ROOT / "gt_finalstand" / "receipts" / "offline_suite.json").read_bytes()
    )
    (isolated_receipts / "provider_free_workflow.json").write_text(
        json.dumps(workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(validator, "FINALSTAND", isolated_finalstand)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")

    def urlopen(request, timeout):
        assert timeout == 10
        assert request.unredirected_hdrs["Authorization"] == "Bearer fixture-token"
        if request.full_url == artifact["archive_download_url"]:
            return io.BytesIO(outer)
        if "/contents/" in request.full_url:
            payload = contents
        elif "/artifacts/" in request.full_url:
            payload = artifact
        else:
            payload = run
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(validator.urllib.request, "urlopen", urlopen)
    assert validator._github_api_confirms_provenance(provenance, workflow)

    actual_digest = provenance["uploaded_artifact_bundle_sha256"]
    provenance["uploaded_artifact_bundle_sha256"] = "0" * 64
    artifact["digest"] = "sha256:" + "0" * 64
    assert not validator._github_api_confirms_provenance(provenance, workflow)
    provenance["uploaded_artifact_bundle_sha256"] = actual_digest
    artifact["digest"] = f"sha256:{actual_digest}"

    contents["content"] = base64.b64encode(b"wrong workflow bytes").decode()
    assert not validator._github_api_confirms_provenance(provenance, workflow)


def test_fs023_artifact_rejects_duplicate_traversal_and_stale_receipts(
    monkeypatch, tmp_path: Path
) -> None:
    validator = _load_validator()
    provenance, workflow = _future_fs023_receipts(validator)
    isolated_finalstand = tmp_path / "gt_finalstand"
    isolated_receipts = isolated_finalstand / "receipts"
    isolated_receipts.mkdir(parents=True)
    (isolated_finalstand / "language_operation_compatibility.json").write_bytes(
        (ROOT / "gt_finalstand" / "language_operation_compatibility.json").read_bytes()
    )
    (isolated_receipts / "offline_suite.json").write_bytes(
        (ROOT / "gt_finalstand" / "receipts" / "offline_suite.json").read_bytes()
    )
    monkeypatch.setattr(validator, "FINALSTAND", isolated_finalstand)

    def rejected(extra_inner=(), workflow_receipt=None):
        candidate = workflow_receipt or workflow
        (isolated_receipts / "provider_free_workflow.json").write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        run, artifact, contents, outer = _mock_github_artifact(
            validator, provenance, candidate, extra_inner=extra_inner
        )

        def urlopen(request, timeout):
            if request.full_url == artifact["archive_download_url"]:
                return io.BytesIO(outer)
            payload = (
                contents
                if "/contents/" in request.full_url
                else artifact
                if "/artifacts/" in request.full_url
                else run
            )
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr(validator.urllib.request, "urlopen", urlopen)
        return validator._github_api_confirms_provenance(provenance, candidate)

    assert rejected()
    assert not rejected([("../receipts/escape.json", b"escape")])
    assert not rejected([("receipts/offline_suite.json", b"duplicate")])
    stale = copy.deepcopy(workflow)
    stale["receipt_inputs"]["offline_suite.json"] = "0" * 64
    assert not rejected(workflow_receipt=stale)


def test_provider_free_workflow_pins_actions_and_records_immutable_run_identity() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "gt_finalstand_provider_free.yml"
    ).read_text(encoding="utf-8")
    action_uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
    assert action_uses
    assert all(re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", use) for use in action_uses)
    assert re.search(
        r"-e\s+['\"]?\./gt-harness\[miniswe,eval\]['\"]?",
        workflow,
    ), "runtime probes and the full suite require the Mini-SWE and eval dependency sets"
    assert workflow.index("finalstand_offline.py provenance") < workflow.index(
        "validate_gt_finalstand.py"
    )
    assert 'echo "GT_INDEX_BINARY=$RUNNER_TEMP/gt-index" >> "$GITHUB_ENV"' in workflow
    assert "/opt/groundtruth/gt-index/gt-index" in workflow
    assert "GIT_AUTHOR_EMAIL: groundtruth-ci@example.invalid" in workflow
    assert "GIT_COMMITTER_EMAIL: groundtruth-ci@example.invalid" in workflow
    for field in (
        '"github_actions"',
        '"event_name"',
        '"repository"',
        '"run_attempt"',
        '"run_url"',
        '"workflow_ref"',
        '"workflow_sha"',
    ):
        assert field in workflow
    assert "re.fullmatch(r\"[0-9a-f]{40}\"" in workflow
    assert "rm -f" in workflow and "provider_free_workflow.json" in workflow
    assert "provider-free-bundle.zip" in workflow
    assert '"receipt_inputs"' in workflow


def test_post_audit_and_single_witness_receipts_close_terminal_rows() -> None:
    appendix = (
        ROOT / "gt_finalstand" / "POST_AUDIT_HARDENING.md"
    ).read_text(encoding="utf-8")
    assert "140/140" in appendix
    assert "57/57" in appendix
    assert "No machine receipt is created" in appendix
    assert "9,980 passed" in appendix
    assert (ROOT / "gt_finalstand" / "receipts" / "final_codespace_verification.json").is_file()

    statuses = _load_validator()._rows("closeout_status.csv")
    assert {
        row["todo"]: row["status"]
        for row in statuses
        if row["todo"] in {"FS-023", "FS-024", "FS-025", "FS-026"}
    } == {
        "FS-023": "COMPLETE",
        "FS-024": "COMPLETE",
        "FS-025": "COMPLETE",
        "FS-026": "COMPLETE",
    }
