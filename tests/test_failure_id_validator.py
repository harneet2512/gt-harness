from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_failure_ids


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_references_do_not_create_duplicate_definitions(tmp_path: Path) -> None:
    _write(
        tmp_path / "ledger.md",
        "# Ledger\n\n### FD-029 - historical defect\n\n"
        "Reference FD-029 twice.\n\n### FD-030 - new defect\n",
    )

    report = validate_failure_ids.validate([tmp_path], expected_next="FD-031")

    assert report["status"] == "pass"
    assert report["definitions"] == {"FD-029": ["ledger.md:3"], "FD-030": ["ledger.md:7"]}
    assert report["next_unused_id"] == "FD-031"


def test_duplicate_definition_fails_across_repository(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "### FD-030 - first definition\n")
    _write(tmp_path / "nested" / "b.md", "## FD-030: second definition\n")

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-030": ["a.md:1", "nested/b.md:1"],
    }


def test_machine_readable_definition_participates_in_duplicate_check(tmp_path: Path) -> None:
    _write(tmp_path / "ledger.json", '{"failure_id": "FD-030"}\n')
    _write(tmp_path / "receipt.yaml", "failure_id: FD-030\n")

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-030": ["ledger.json:1", "receipt.yaml:1"],
    }


def test_malformed_definition_and_expected_next_mismatch_fail(tmp_path: Path) -> None:
    _write(tmp_path / "ledger.md", "### FD-29 - malformed\n### FD-029 - allocated\n")

    report = validate_failure_ids.validate([tmp_path], expected_next="FD-031")

    assert report["status"] == "fail"
    assert report["malformed_definitions"] == ["ledger.md:1:FD-29"]
    assert report["expected_next_id"] == "FD-031"
    assert report["next_unused_id"] == "FD-030"


def test_cli_emits_deterministic_json_and_nonzero_for_duplicates(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "z.md", "### FD-001 - z\n")
    _write(tmp_path / "a.md", "### FD-001 - a\n")

    exit_code = validate_failure_ids.main([str(tmp_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["duplicate_definitions"]["FD-001"] == ["a.md:1", "z.md:1"]
    assert output["schema"] == "failure-id-validation.v1"
