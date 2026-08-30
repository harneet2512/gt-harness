from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import validate_failure_ids


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_snapshot(path: Path, *, revision: str | None = None) -> Path:
    description_sha256 = "a" * 64
    revision = revision or f"sha256:{description_sha256}"
    payload = {
        "schema": "failure-id-ledger-snapshot.v1",
        "source": {
            "issue": "HAR-55",
            "revision": revision,
            "observed_updated_at": "2026-08-30T00:00:00.000Z",
            "description_sha256": description_sha256,
        },
        "canonical_definitions": {
            "FD-001": "first canonical defect",
            "FD-002": "second canonical defect",
        },
        "last_allocated_id": "FD-002",
        "next_unused_id": "FD-003",
        "legacy_ambiguities": {
            "FD-001": ["first historical title", "second historical title"],
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload["payload_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return _write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _snapshot_payload_sha256(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["payload_sha256"]


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


def test_directory_scan_includes_receipt_evidence(tmp_path: Path) -> None:
    _write(tmp_path / "a.receipt", "failure_id: FD-010\n")
    _write(tmp_path / "nested" / "b.receipt", "failure_id: FD-010\n")

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-010": ["a.receipt:1", "nested/b.receipt:1"],
    }


def test_machine_fields_are_found_at_any_supported_position(tmp_path: Path) -> None:
    _write(
        tmp_path / "ledger.json",
        '{"schema":"x","failure_id":"FD-010","failure_id":"FD-010"}\n',
    )
    _write(tmp_path / "ledger.jsonl", '{"x":1,"failure_id":"FD-010"}\n')
    _write(tmp_path / "ledger.yaml", "- failure_id: FD-010\n")

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-010": ["ledger.json:1", "ledger.json:1", "ledger.jsonl:1", "ledger.yaml:1"],
    }


def test_report_is_checkout_location_invariant(tmp_path: Path) -> None:
    first = _write(tmp_path / "one" / "same" / "ledger.md", "### FD-010 - definition\n")
    second = _write(tmp_path / "two" / "same" / "ledger.md", first.read_text(encoding="utf-8"))

    first_report = validate_failure_ids.validate([first.parent])
    second_report = validate_failure_ids.validate([second.parent])

    assert json.dumps(first_report, sort_keys=True) == json.dumps(second_report, sort_keys=True)


def test_snapshot_reports_legacy_ambiguity_without_accepting_new_duplicates(
    tmp_path: Path,
) -> None:
    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")

    report = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_next="FD-003",
        expected_ledger_revision=f"sha256:{'a' * 64}",
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )

    assert report["status"] == "pass"
    assert report["next_unused_id"] == "FD-003"
    assert report["legacy_ambiguities"] == {
        "FD-001": ["first historical title", "second historical title"],
    }

    _write(source.parent / "new.md", "### FD-001 - divergent new definition\n")
    rejected = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )
    assert rejected["status"] == "fail"
    assert rejected["snapshot_conflicts"] == {"FD-001": ["new.md:1"]}


def test_snapshot_missing_tampered_and_stale_fail_closed(tmp_path: Path) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    missing = validate_failure_ids.validate(
        [source.parent], ledger_snapshot=tmp_path / "missing.json"
    )
    assert missing["status"] == "fail"
    assert missing["snapshot_errors"] == ["snapshot:not_found"]

    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    unpinned = validate_failure_ids.validate([source.parent], ledger_snapshot=snapshot)
    assert unpinned["status"] == "fail"
    assert unpinned["snapshot_errors"] == ["snapshot:expected_payload_sha256_required"]

    tampered_data = json.loads(snapshot.read_text(encoding="utf-8"))
    tampered_data["canonical_definitions"]["FD-001"] = "tampered"
    snapshot.write_text(json.dumps(tampered_data), encoding="utf-8")
    tampered = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=tampered_data["payload_sha256"],
    )
    assert tampered["status"] == "fail"
    assert tampered["snapshot_errors"] == [
        "snapshot:payload_sha256_mismatch",
        "snapshot:unexpected_payload_sha256",
    ]

    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    stale = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_revision=f"sha256:{'b' * 64}",
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )
    assert stale["status"] == "fail"
    assert stale["snapshot_errors"] == ["snapshot:unexpected_source_revision"]

    gap_data = json.loads(snapshot.read_text(encoding="utf-8"))
    gap_data["canonical_definitions"].pop("FD-002")
    gap_data["canonical_definitions"]["FD-003"] = "third canonical defect"
    gap_data["last_allocated_id"] = "FD-003"
    gap_data["next_unused_id"] = "FD-004"
    gap_data["legacy_ambiguities"] = {}
    gap_data.pop("payload_sha256")
    encoded = json.dumps(gap_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    gap_data["payload_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    snapshot.write_text(json.dumps(gap_data), encoding="utf-8")
    gap = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )
    assert gap["status"] == "fail"
    assert gap["snapshot_errors"] == ["snapshot:allocation_gap"]


def test_snapshot_rejects_recomputed_self_hash_when_external_pin_differs(
    tmp_path: Path,
) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    expected_payload_sha256 = _snapshot_payload_sha256(snapshot)
    tampered_data = json.loads(snapshot.read_text(encoding="utf-8"))
    tampered_data["canonical_definitions"]["FD-001"] = "tampered and re-signed"
    tampered_data.pop("payload_sha256")
    encoded = json.dumps(tampered_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tampered_data["payload_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    snapshot.write_text(json.dumps(tampered_data), encoding="utf-8")

    report = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=expected_payload_sha256,
    )

    assert report["status"] == "fail"
    assert report["snapshot_errors"] == ["snapshot:unexpected_payload_sha256"]


def test_snapshot_rejects_malformed_external_payload_pin(tmp_path: Path) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    snapshot = _write_snapshot(tmp_path / "snapshot.json")

    report = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256="not-a-sha256",
    )

    assert report["status"] == "fail"
    assert report["snapshot_errors"] == ["snapshot:unexpected_payload_sha256"]


def test_snapshot_rejects_duplicate_keys_at_every_object_level(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    original = _write_snapshot(tmp_path / "original.json")
    expected_payload_sha256 = _snapshot_payload_sha256(original)
    original_text = original.read_text(encoding="utf-8")
    variants = {
        "top-level": original_text.replace("{\n", '{\n  "schema": "contradictory-schema",\n', 1),
        "canonical": original_text.replace(
            '  "canonical_definitions": {\n',
            '  "canonical_definitions": {\n    "FD-001": "contradictory definition",\n',
            1,
        ),
        "nested-source": original_text.replace(
            '  "source": {\n', '  "source": {\n    "issue": "contradictory-issue",\n', 1
        ),
    }

    for name, text in variants.items():
        snapshot = _write(tmp_path / f"{name}.json", text)
        report = validate_failure_ids.validate(
            [source.parent],
            ledger_snapshot=snapshot,
            expected_ledger_payload_sha256=expected_payload_sha256,
        )

        assert report["status"] == "fail", name
        assert report["snapshot_errors"] == ["snapshot:duplicate_key"], name
        exit_code = validate_failure_ids.main(
            [
                str(source.parent),
                "--ledger-snapshot",
                str(snapshot),
                "--expected-ledger-payload-sha256",
                expected_payload_sha256,
            ]
        )
        cli_report = json.loads(capsys.readouterr().out)
        assert exit_code == 1, name
        assert cli_report["snapshot_errors"] == ["snapshot:duplicate_key"], name


def test_orphan_ledger_expectations_fail_closed_in_library(tmp_path: Path) -> None:
    source = _write(tmp_path / "repo" / "README.md", "No failure IDs.\n")
    revision = f"sha256:{'a' * 64}"
    payload_sha256 = "b" * 64
    variants = (
        {"expected_ledger_revision": revision},
        {"expected_ledger_payload_sha256": payload_sha256},
        {
            "expected_ledger_revision": revision,
            "expected_ledger_payload_sha256": payload_sha256,
        },
    )

    unpinned = validate_failure_ids.validate([source.parent])
    assert unpinned["status"] == "pass"

    for expectations in variants:
        report = validate_failure_ids.validate([source.parent], **expectations)
        assert report["status"] == "fail"
        assert report["snapshot_errors"] == ["snapshot:expectation_without_snapshot"]


def test_orphan_ledger_expectations_fail_closed_in_cli(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path / "repo" / "README.md", "No failure IDs.\n")
    revision_args = ["--expected-ledger-revision", f"sha256:{'a' * 64}"]
    payload_args = ["--expected-ledger-payload-sha256", "b" * 64]

    for expectation_args in (revision_args, payload_args, revision_args + payload_args):
        exit_code = validate_failure_ids.main([str(source.parent), *expectation_args])
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        assert report["snapshot_errors"] == ["snapshot:expectation_without_snapshot"]


def test_snapshot_detects_cross_repository_duplicate_definitions(tmp_path: Path) -> None:
    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    first = _write(tmp_path / "one" / "ledger.md", "### FD-001 - new one\n")
    second = _write(tmp_path / "two" / "ledger.md", "### FD-001 - new two\n")

    report = validate_failure_ids.validate(
        [first.parent, second.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-001": ["root-1/ledger.md:1", "root-2/ledger.md:1"],
    }
    assert report["snapshot_conflicts"] == {
        "FD-001": ["root-1/ledger.md:1", "root-2/ledger.md:1"],
    }


def test_snapshot_rejects_single_unallocated_definition(tmp_path: Path) -> None:
    snapshot = _write_snapshot(tmp_path / "snapshot.json")
    source = _write(tmp_path / "repo" / "ledger.md", "### FD-003 - not allocated yet\n")

    report = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(snapshot),
    )

    assert report["status"] == "fail"
    assert report["unallocated_definitions"] == {"FD-003": ["ledger.md:1"]}


def test_snapshot_report_is_checkout_location_invariant(tmp_path: Path) -> None:
    first_root = tmp_path / "one" / "same"
    second_root = tmp_path / "two" / "same"
    _write(first_root / "README.md", "Reference FD-001.\n")
    _write(second_root / "README.md", "Reference FD-001.\n")
    first_snapshot = _write_snapshot(first_root / "snapshot.json")
    second_snapshot = _write_snapshot(second_root / "snapshot.json")

    first = validate_failure_ids.validate(
        [first_root],
        ledger_snapshot=first_snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(first_snapshot),
    )
    second = validate_failure_ids.validate(
        [second_root],
        ledger_snapshot=second_snapshot,
        expected_ledger_payload_sha256=_snapshot_payload_sha256(second_snapshot),
    )

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_repository_snapshot_and_ci_are_wired() -> None:
    repository = Path(__file__).resolve().parents[1]
    snapshot = repository / "config" / "failure_id_ledger_snapshot.json"

    report = validate_failure_ids.validate(
        [repository],
        ledger_snapshot=snapshot,
        expected_next="FD-031",
        expected_ledger_revision=(
            "sha256:b9828534a6ca2b1e935fc795e54505f25b958c28ae5a415fe147fe9cde103507"
        ),
        expected_ledger_payload_sha256=(
            "ba37ea27439f5c36b131a5325b9d81f0a8df16a872d2761b5a0e15effebcd48d"
        ),
    )

    assert report["status"] == "pass"
    assert report["next_unused_id"] == "FD-031"
    workflow = (repository / ".github" / "workflows" / "failure-id-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "scripts/validate_failure_ids.py" in workflow
    assert "config/failure_id_ledger_snapshot.json" in workflow
    assert "--expected-ledger-payload-sha256 ba37ea27439f5c36" in workflow
