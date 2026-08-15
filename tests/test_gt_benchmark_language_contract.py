from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_tb2_language_contract import verify_contract


def test_pinned_tb2_language_contract_covers_every_required_witness() -> None:
    receipt = verify_contract()

    assert receipt["dataset_commit"] == "2fd12b88aafdd04a52c298e3940bcb189f9766d6"
    assert receipt["expected_task_count"] == 89
    assert receipt["witness_count"] >= 14
    assert receipt["missing_languages"] == []
    assert receipt["non_structural_languages"] == []
    assert receipt["ambiguous_samples"] == []


def test_dataset_backed_contract_rejects_a_missing_instruction_witness(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "gt.tb2.language-contract.v1",
                "dataset_repository": "test",
                "dataset_commit": "",
                "expected_task_count": 1,
                "witnesses": [
                    {
                        "task": "task-a",
                        "path": "missing.stan",
                        "language": "stan",
                        "sample": "parameters { real mu; }",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task = tmp_path / "task-a"
    task.mkdir()
    (task / "task.toml").write_text("[task]\nname='task-a'\n", encoding="utf-8")
    (task / "instruction.md").write_text("Create another.file", encoding="utf-8")

    with pytest.raises(RuntimeError, match="instruction witness missing"):
        verify_contract(contract_path=contract_path, dataset_root=tmp_path)


def test_dataset_backed_contract_rejects_an_unclassified_structural_suffix(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "gt.tb2.language-contract.v1",
                "dataset_repository": "test",
                "dataset_commit": "",
                "expected_task_count": 1,
                "instruction_source_suffixes": {},
                "witnesses": [
                    {
                        "task": "task-a",
                        "path": "model.stan",
                        "language": "stan",
                        "sample": "parameters { real mu; }",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task = tmp_path / "task-a"
    task.mkdir()
    (task / "task.toml").write_text("[task]\nname='task-a'\n", encoding="utf-8")
    (task / "instruction.md").write_text(
        "Complete model.stan", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="unclassified structural suffix"):
        verify_contract(contract_path=contract_path, dataset_root=tmp_path)
