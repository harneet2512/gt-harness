from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gt_engine.decision_value_corpus import (
    load_decision_value_corpus,
    score_decision_value_observations,
)
from gt_engine.decision_value_observations import observation_from_run_receipt
from gt_engine.run_receipt_v2 import RunReceiptFinalizer
from gt_engine.runtime_observation import capture_workspace


def _write_case(root: Path, language: str, suffix: str) -> dict:
    repository = root / language
    source = repository / f"src/owner{suffix}"
    source.parent.mkdir(parents=True)
    content = f"// {language}\nfn resolve_identity() {{}}\n".encode()
    source.write_bytes(content)
    return {
        "case_id": f"{language}-owner",
        "language": language,
        "task": "Update resolve_identity while preserving the public behavior.",
        "repository": repository.relative_to(root).as_posix(),
        "repository_revision": hashlib.sha256(content).hexdigest(),
        "expected_owners": [f"{language}:src/owner{suffix}:resolve_identity"],
        "facts": [
            {
                "claim_id": "owner-fact",
                "fact": "resolve_identity is implemented by the owner source file",
                "path": f"src/owner{suffix}",
                "start_line": 2,
                "end_line": 2,
                "content_sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }


def _corpus(tmp_path: Path) -> Path:
    cases = [
        _write_case(tmp_path, "python", ".py"),
        _write_case(tmp_path, "typescript", ".ts"),
        _write_case(tmp_path, "go", ".go"),
        _write_case(tmp_path, "rust", ".rs"),
    ]
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps({"schema": "gt.decision_value_corpus.v1", "cases": cases}),
        encoding="utf-8",
    )
    return path


def test_corpus_scores_facts_from_oracle_and_repository_not_producer_boolean(
    tmp_path: Path,
) -> None:
    corpus = load_decision_value_corpus(_corpus(tmp_path))
    assert all(case.task.startswith("Update resolve_identity") for case in corpus.cases)
    observations = []
    for case in corpus.cases:
        fact = case.facts[0]
        observations.append(
            {
                "case_id": case.case_id,
                "repository_revision": case.repository_revision,
                "ranked_owners": [case.expected_owners[0]],
                "certified_facts": [
                    {
                        "claim_id": fact.claim_id,
                        "fact": fact.fact,
                        "source_supported": False,
                        "source_evidence": [
                            {
                                "path": fact.path,
                                "start_line": fact.start_line,
                                "end_line": fact.end_line,
                                "content_sha256": fact.content_sha256,
                            }
                        ],
                    }
                ],
            }
        )

    scored = score_decision_value_observations(corpus, observations)

    assert len(scored.certified_fact_checks) == 4
    assert all(row["source_supported"] is True for row in scored.certified_fact_checks)
    assert len(scored.implementation_owner_cases) == 4
    assert all(
        row["expected"] in row["ranked"][:3]
        for row in scored.implementation_owner_cases
    )


def test_corpus_rejects_wrong_fact_even_when_producer_claims_supported(tmp_path: Path) -> None:
    corpus = load_decision_value_corpus(_corpus(tmp_path))
    case = corpus.cases[0]
    fact = case.facts[0]
    scored = score_decision_value_observations(
        corpus,
        [
            {
                "case_id": case.case_id,
                "repository_revision": case.repository_revision,
                "ranked_owners": [case.expected_owners[0]],
                "certified_facts": [
                    {
                        "claim_id": fact.claim_id,
                        "fact": "a different unsupported assertion",
                        "source_supported": True,
                        "source_evidence": [
                            {
                                "path": fact.path,
                                "start_line": fact.start_line,
                                "end_line": fact.end_line,
                                "content_sha256": fact.content_sha256,
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert scored.certified_fact_checks[0] == {
        "case_id": case.case_id,
        "claim_id": fact.claim_id,
        "source_supported": False,
        "reason": "fact_mismatch",
    }
    assert {
        row["reason"] for row in scored.certified_fact_checks[1:]
    } == {"missing_observation"}


def test_corpus_fails_closed_when_a_required_language_is_absent(tmp_path: Path) -> None:
    path = _corpus(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"] = [row for row in payload["cases"] if row["language"] != "rust"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required languages: rust"):
        load_decision_value_corpus(path)


def test_checked_in_decision_value_corpus_is_content_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    corpus = load_decision_value_corpus(
        root / "benchmarks" / "decision_value_v1" / "corpus.json"
    )

    assert [case.language for case in corpus.cases] == [
        "python",
        "typescript",
        "go",
        "rust",
    ]
    for case in corpus.cases:
        assert case.repository_revision == capture_workspace(case.repository).revision
        for fact in case.facts:
            assert fact.content_sha256 == hashlib.sha256(
                (case.repository / fact.path).read_bytes()
            ).hexdigest()


def test_corpus_marks_mutated_source_hash_as_unsupported(tmp_path: Path) -> None:
    corpus = load_decision_value_corpus(_corpus(tmp_path))
    case = corpus.cases[0]
    fact = case.facts[0]
    (case.repository / fact.path).write_text("mutated\n", encoding="utf-8")

    scored = score_decision_value_observations(
        corpus,
        [
            {
                "case_id": case.case_id,
                "repository_revision": case.repository_revision,
                "ranked_owners": [],
                "certified_facts": [
                    {
                        "claim_id": fact.claim_id,
                        "fact": fact.fact,
                        "source_evidence": [
                            {
                                "path": fact.path,
                                "start_line": fact.start_line,
                                "end_line": fact.end_line,
                                "content_sha256": fact.content_sha256,
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert scored.certified_fact_checks[0]["reason"] == "repository_hash_mismatch"


def test_gate_cli_scores_independent_corpus_mode(tmp_path: Path) -> None:
    corpus_path = _corpus(tmp_path)
    corpus = load_decision_value_corpus(corpus_path)
    observations = []
    for case in corpus.cases:
        fact = case.facts[0]
        observations.append(
            {
                "case_id": case.case_id,
                "repository_revision": case.repository_revision,
                "ranked_owners": [case.expected_owners[0]],
                "certified_facts": [
                    {
                        "claim_id": fact.claim_id,
                        "fact": fact.fact,
                        "source_evidence": [
                            {
                                "path": fact.path,
                                "start_line": fact.start_line,
                                "end_line": fact.end_line,
                                "content_sha256": fact.content_sha256,
                            }
                        ],
                    }
                ],
            }
        )
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    receipt_path = tmp_path / "gt-run-receipt.json"
    RunReceiptFinalizer(
        receipt_path,
        task_id="corpus-proof",
        requested_model="deterministic-fixture",
        started_at="2026-08-28T00:00:00+00:00",
    ).finalize(
        terminal="fixture_complete",
        infrastructure_classification="NONE",
        trajectory={"messages": []},
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/decision_value_gate.py",
            "--expected-runs",
            "1",
            "--run-receipts",
            str(receipt_path),
            "--corpus",
            str(corpus_path),
            "--observations",
            str(observations_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["certified_source_precision"] == 1.0
    assert report["implementation_owner_top3_recall"] == 1.0


def test_observation_export_uses_only_provider_visible_certified_claims(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    finalizer = RunReceiptFinalizer(
        receipt_path,
        task_id="task",
        requested_model="model",
    )
    finalizer.record_delivery(
        {
            "delivery_id": "delivery-1",
            "repository_revision": "repo-r1",
            "graph_revision": "",
            "model_visible_bytes_hex": b"owner".hex(),
        }
    )
    finalizer.record_feature_lifecycle(
        {
            "feature_id": "implementation_owner",
            "stage": "VALIDATED",
            "model_visible_bytes_hex": b"owner".hex(),
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "resolve_identity is the implementation owner",
                    "role": "edit_owner",
                    "symbol_identity": "python:src/owner.py:resolve_identity",
                    "source_evidence": [
                        {
                            "path": "src/owner.py",
                            "start_line": 1,
                            "end_line": 2,
                            "content_sha256": "a" * 64,
                        }
                    ],
                }
            ],
        }
    )
    receipt = finalizer.finalize(
        terminal="fixture_complete",
        infrastructure_classification="NONE",
        trajectory={"messages": []},
    )

    observation = observation_from_run_receipt("python-owner", receipt)

    assert observation["repository_revision"] == "repo-r1"
    assert observation["ranked_owners"][:2] == [
        "python:src/owner.py:resolve_identity",
        "src/owner.py",
    ]
    assert observation["certified_facts"][0]["claim_id"] == "claim-1"

    output_path = tmp_path / "observations.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/decision_value_observe.py",
            "--run",
            f"python-owner={receipt_path}",
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert exported == [observation]
