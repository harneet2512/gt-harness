from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import validate_failure_ids


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot_data() -> dict:
    description = "### FD-001 - first\n### FD-002 - second\n"
    description_sha256 = hashlib.sha256(description.encode()).hexdigest()
    return {
        "schema": "failure-id-ledger-snapshot.v1",
        "source": {
            "issue": "HAR-55",
            "revision": f"sha256:{description_sha256}",
            "observed_updated_at": "2026-08-30T00:00:00.000Z",
            "description_sha256": description_sha256,
            "description_utf8_bytes": len(description.encode()),
            "description": description,
        },
        "canonical_definitions": {
            "FD-001": "first canonical defect",
            "FD-002": "second canonical defect",
        },
        "last_allocated_id": "FD-002",
        "next_unused_id": "FD-003",
        "legacy_ambiguities": {},
    }


def _write_signed(path: Path, data: dict) -> tuple[Path, str]:
    data.pop("payload_sha256", None)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    data["payload_sha256"] = payload_sha256
    return _write(path, json.dumps(data, indent=2, sort_keys=True) + "\n"), payload_sha256


def test_repository_scan_includes_csv_and_extensionless_utf8(tmp_path: Path) -> None:
    _write(tmp_path / "first.md", "### FD-030 - first definition\n")
    _write(tmp_path / "second.csv", "### FD-030 - duplicate in CSV\n")
    _write(tmp_path / "NOTICE", "### FD-030 - duplicate extensionless text\n")

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {"FD-030": ["NOTICE:1", "first.md:1", "second.csv:1"]}


def test_json_machine_definitions_are_structural_and_escape_aware(tmp_path: Path) -> None:
    snapshot, payload_sha256 = _write_signed(tmp_path / "snapshot.json", _snapshot_data())
    variants = {
        "multiline.json": '{\n  "failure_id"\n  :\n  "FD-003"\n}\n',
        "escaped-key.json": '{"fail\\u0075re_id": "FD-003"}\n',
        "escaped-value.json": '{"failure_id": "FD-\\u0030\\u0030\\u0033"}\n',
    }

    for name, content in variants.items():
        root = tmp_path / name.removesuffix(".json")
        _write(root / name, content)
        report = validate_failure_ids.validate(
            [root],
            ledger_snapshot=snapshot,
            expected_ledger_payload_sha256=payload_sha256,
        )
        assert report["status"] == "fail", name
        assert report["unallocated_definitions"] == {"FD-003": [f"{name}:1"]}, name


def test_malformed_snapshot_returns_json_instead_of_raising(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    variants: dict[str, dict] = {}

    invalid_key = _snapshot_data()
    invalid_key["canonical_definitions"]["FD-X"] = invalid_key["canonical_definitions"].pop(
        "FD-002"
    )
    variants["invalid-key"] = invalid_key
    for field in ("last_allocated_id", "next_unused_id"):
        missing = _snapshot_data()
        missing.pop(field)
        variants[f"missing-{field}"] = missing

    for name, data in variants.items():
        snapshot, payload_sha256 = _write_signed(tmp_path / f"{name}.json", data)
        report = validate_failure_ids.validate(
            [source.parent],
            ledger_snapshot=snapshot,
            expected_ledger_payload_sha256=payload_sha256,
        )
        assert report["status"] == "fail", name
        assert report["snapshot_errors"], name

        exit_code = validate_failure_ids.main(
            [
                str(source.parent),
                "--ledger-snapshot",
                str(snapshot),
                "--expected-ledger-payload-sha256",
                payload_sha256,
            ]
        )
        cli_report = json.loads(capsys.readouterr().out)
        assert exit_code == 1, name
        assert cli_report["status"] == "fail", name


def test_snapshot_embeds_and_verifies_exact_source_bytes(tmp_path: Path) -> None:
    source = _write(tmp_path / "repo" / "README.md", "Reference FD-001.\n")
    data = _snapshot_data()
    data["source"]["description"] += "mutated after hashing\n"
    snapshot, payload_sha256 = _write_signed(tmp_path / "snapshot.json", data)

    report = validate_failure_ids.validate(
        [source.parent],
        ledger_snapshot=snapshot,
        expected_ledger_payload_sha256=payload_sha256,
    )

    assert report["status"] == "fail"
    assert "snapshot:invalid_source" in report["snapshot_errors"]
