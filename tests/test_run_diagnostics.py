from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gt_engine.run_diagnostics import (
    CapabilityState,
    DiagnosticCode,
    DiagnosticEvent,
    DiagnosticJournal,
    diagnose_artifact_root,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_diagnostic_event_is_stable_secret_free_and_aggregated(tmp_path: Path):
    journal = DiagnosticJournal(tmp_path, task_id="task-1")
    for sequence in (2, 5):
        journal.record(
            DiagnosticEvent.create(
                code=DiagnosticCode.GT_DENSE_MODEL_UNAVAILABLE,
                severity="ERROR", phase="startup", subsystem="retrieval",
                capability="dense_retrieval", task_id="task-1",
                classification="primary", cause="model asset not mounted",
                impact="hybrid required disabled",
                recovery="stage verified model in task bundle",
                retryable=False, event_sequence=sequence,
                identities={"bundle": "a" * 64}, evidence_refs=(),
            )
        )
    journal.capability("dense_retrieval", CapabilityState.FAILED, "model digest absent")
    paths = journal.seal()

    payload = json.loads(paths.json.read_text(encoding="utf-8"))
    row = payload["diagnostics"][0]
    assert row["occurrence_count"] == 2
    assert row["first_event_sequence"] == 2
    assert row["last_event_sequence"] == 5
    expected = b"gt.incident.v1\0GT_DENSE_MODEL_UNAVAILABLE\0startup\0retrieval\0"
    expected += b"model_asset_not_mounted\0task-1\0" + (b"a" * 64)
    assert row["fingerprint"] == hashlib.sha256(expected).hexdigest()
    assert "model asset" not in paths.text.read_text(encoding="utf-8")
    assert "GT_DENSE_MODEL_UNAVAILABLE" in paths.text.read_text(encoding="utf-8")


def test_diagnostic_event_rejects_unknown_codes_and_secret_material():
    common = dict(
        severity="ERROR", phase="provider", subsystem="transport",
        capability="provider_transport", task_id="x", classification="primary",
        cause="request too large", impact="request refused", recovery="refine query",
        retryable=False, event_sequence=1,
    )
    with pytest.raises(ValueError, match="closed diagnostic code"):
        DiagnosticEvent.create(code="MADE_UP", **common)
    with pytest.raises(ValueError, match="secret-like"):
        DiagnosticEvent.create(
            code=DiagnosticCode.GT_PROVIDER_REQUEST_TOO_LARGE,
            evidence_refs=({"path": "events.json", "api_key": "sk-secret"},),
            **common,
        )


def test_diagnose_root_validates_evidence_hashes_and_plan_conservation(tmp_path: Path):
    evidence = tmp_path / "nested" / "event.json"
    evidence.parent.mkdir()
    evidence.write_text('{"event":"dense_missing"}\n', encoding="utf-8")
    journal = DiagnosticJournal(evidence.parent, task_id="a")
    journal.record(
        DiagnosticEvent.create(
            code=DiagnosticCode.GT_DENSE_MODEL_UNAVAILABLE,
            severity="ERROR", phase="startup", subsystem="retrieval",
            capability="dense_retrieval", task_id="a", classification="primary",
            cause="model_asset_not_mounted", impact="hybrid_required_disabled",
            recovery="stage_verified_model_in_task_bundle", retryable=False,
            event_sequence=1,
            evidence_refs=({"path": "event.json", "sha256": _sha(evidence)},),
        )
    )
    journal.capability("dense_retrieval", CapabilityState.FAILED, "asset absent")
    journal.seal()
    (tmp_path / "task-plan.json").write_text(
        json.dumps({"tasks": ["a"]}), encoding="utf-8"
    )

    report = diagnose_artifact_root(tmp_path, strict=True)
    assert report.exit_code == 1
    assert report.primary_by_task["a"].code == DiagnosticCode.GT_DENSE_MODEL_UNAVAILABLE

    evidence.write_text("tampered\n", encoding="utf-8")
    malformed = diagnose_artifact_root(tmp_path, strict=True)
    assert malformed.exit_code == 2
    assert any("digest mismatch" in issue for issue in malformed.artifact_issues)


def test_strict_diagnosis_rejects_missing_task_diagnostics(tmp_path: Path):
    (tmp_path / "task-plan.json").write_text(
        json.dumps({"tasks": ["planned-a", "planned-b"]}), encoding="utf-8"
    )
    journal = DiagnosticJournal(tmp_path / "one", task_id="planned-a")
    journal.capability("dense_retrieval", CapabilityState.WORKING, "verified output")
    journal.seal()

    report = diagnose_artifact_root(tmp_path, strict=True)
    assert report.exit_code == 2
    assert "planned-b" in " ".join(report.artifact_issues)


def test_packaged_cli_discovers_nested_healthy_artifact(tmp_path: Path):
    journal = DiagnosticJournal(tmp_path / "deep" / "trial", task_id="healthy")
    journal.capability("receipt_writer", CapabilityState.WORKING, "verified journal")
    journal.seal()
    (tmp_path / "task-plan.json").write_text(
        json.dumps({"tasks": ["healthy"]}), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable, "-m", "scripts.diagnose_benchmark_run",
            "--root", str(tmp_path), "--strict", "--write-summary",
        ],
        cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["exit_code"] == 0
    assert (tmp_path / "diagnostic-summary.json").is_file()
