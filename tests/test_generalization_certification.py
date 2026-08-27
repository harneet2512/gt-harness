from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import runtime_leak_scan
from scripts.replay_smoke20_localization import _tracked_source_fingerprint
from tests.generalization_helpers import (
    assert_distractor_monotonic,
    assert_homonym_is_scoped,
    assert_order_invariant,
    assert_paraphrase_preserves_anchors,
)


def _report(*, implementation_precision: float, implementation_recall: float) -> dict:
    return {
        "schema": "gt.localization_truth_report.v2",
        "status": "PASS",
        "summary": {
            "schema": "gt.localization_truth_report.v2",
            "compiler_fingerprint": "stale-is-replaced-by-test",
            "retrieval_mode": "hybrid_required",
            "cases_expected": 1,
            "cases_run": 1,
            "case_failures": [],
            "missing_oracle_tasks": [],
            "extra_oracle_tasks": [],
            "tasks_with_false_edit_authority": [],
            "tasks_below_half_required_coverage": [],
            "treatment_failures": [],
            "dense_not_ready_tasks": [],
            "mean_exact_edit_precision": 1.0,
            "mean_required_facet_coverage": 0.95,
            "mean_ambiguity_candidate_recall": 1.0,
            "implementation_role_precision": implementation_precision,
            "implementation_role_recall": implementation_recall,
        },
        "results": [
            {
                "task_id": "synthetic-task",
                "score": {"required_facts": 1, "required_facet_coverage": 1.0},
            }
        ],
    }


def test_localization_gate_defaults_are_strict_and_support_recall(
    monkeypatch, tmp_path: Path
) -> None:
    from scripts import localization_truth_gate

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(_report(implementation_precision=0.79, implementation_recall=0.90))
    )
    monkeypatch.setattr(
        localization_truth_gate,
        "_compiler_fingerprint",
        lambda: "stale-is-replaced-by-test",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["localization_truth_gate", "--report", str(report)],
    )
    assert localization_truth_gate.main() == 1


def test_localization_gate_accepts_required_thresholds(monkeypatch, tmp_path: Path) -> None:
    from scripts import localization_truth_gate

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(_report(implementation_precision=0.80, implementation_recall=0.85))
    )
    monkeypatch.setattr(
        localization_truth_gate,
        "_compiler_fingerprint",
        lambda: "stale-is-replaced-by-test",
    )
    monkeypatch.setattr("sys.argv", ["localization_truth_gate", "--report", str(report)])
    assert localization_truth_gate.main() == 0


def test_localization_gate_rejects_task_below_half_coverage(monkeypatch, tmp_path: Path) -> None:
    from scripts import localization_truth_gate

    payload = _report(implementation_precision=0.90, implementation_recall=0.90)
    payload["results"][0]["score"]["required_facet_coverage"] = 0.49
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload))
    monkeypatch.setattr(
        localization_truth_gate,
        "_compiler_fingerprint",
        lambda: "stale-is-replaced-by-test",
    )
    monkeypatch.setattr("sys.argv", ["localization_truth_gate", "--report", str(report)])
    assert localization_truth_gate.main() == 1


def test_runtime_scan_rejects_dynamic_benchmark_artifact_access(tmp_path: Path) -> None:
    source = tmp_path / "runtime.py"
    source.write_text("from pathlib import Path\nPath('fixtures/reference.patch').read_text()\n")
    findings = runtime_leak_scan.scan_paths((source,), forbidden_values=())
    assert any("reference" in finding.reason for finding in findings)


def test_runtime_scan_rejects_forbidden_value_without_task_specific_rules(tmp_path: Path) -> None:
    source = tmp_path / "runtime.py"
    source.write_text("TASK = 'synthetic-repository-name'\n")
    findings = runtime_leak_scan.scan_paths(
        (source,), forbidden_values=("synthetic-repository-name",)
    )
    assert any("forbidden value" in finding.reason for finding in findings)


def test_compiler_fingerprint_uses_committed_git_objects_not_checkout_line_endings(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "GT Test"], check=True
    )
    source = repository / "compiler.py"
    source.write_bytes(b"first\nsecond\n")
    subprocess.run(["git", "-C", str(repository), "add", "compiler.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True
    )

    expected = _tracked_source_fingerprint(repository, ("compiler.py",))
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.autocrlf", "true"], check=True
    )
    source.write_bytes(b"first\r\nsecond\r\n")

    assert (
        subprocess.run(
            ["git", "-C", str(repository), "diff", "--quiet", "--", "compiler.py"]
        ).returncode
        == 0
    )
    assert _tracked_source_fingerprint(repository, ("compiler.py",)) == expected


def test_compiler_fingerprint_marks_real_worktree_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "GT Test"], check=True
    )
    source = repository / "compiler.py"
    source.write_text("first\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "compiler.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True
    )
    clean = _tracked_source_fingerprint(repository, ("compiler.py",))

    source.write_text("changed\n", encoding="utf-8")

    assert _tracked_source_fingerprint(repository, ("compiler.py",)) != clean


def test_metamorphic_task_and_retrieval_invariants() -> None:
    base = "Implement the parser in src/core.py and add validation tests."
    paraphrase = "Add validation tests while changing the parser in src/core.py."
    reordered = "Add validation tests. Implement the parser in src/core.py."
    distractor = base + " Update the unrelated documentation example."
    assert_paraphrase_preserves_anchors(base, paraphrase)
    assert_order_invariant(base, reordered)
    assert_distractor_monotonic(base, distractor)
    assert_homonym_is_scoped("Inspect parse in src/core.py, not parse in tests/example.py.")
