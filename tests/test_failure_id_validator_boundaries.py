from __future__ import annotations

import hashlib
import json
import subprocess
import sys
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


def test_corrupt_machine_files_fail_closed_instead_of_hiding_definitions(
    tmp_path: Path,
) -> None:
    cases = {
        "nul-byte": ("hidden.receipt", b"failure_id: FD-030\0\n"),
        "invalid-utf8": ("hidden.json", b'{"failure_id":"FD-030"}\xff\n'),
    }

    for name, (filename, content) in cases.items():
        root = tmp_path / name
        root.mkdir(parents=True)
        (root / filename).write_bytes(content)
        _write(root / "visible.receipt", "failure_id: FD-030\n")

        report = validate_failure_ids.validate([root])

        assert report["status"] == "fail", name
        assert report["unreadable_files"], name
        assert report["unreadable_files"][0].startswith(f"{filename}:"), name


def test_corrupt_machine_cli_is_deterministic_across_roots(tmp_path: Path) -> None:
    script = Path(validate_failure_ids.__file__).resolve()
    outputs: list[bytes] = []
    for checkout_number in (1, 2):
        root = tmp_path / f"machine-checkout-{checkout_number}"
        root.mkdir()
        (root / "hidden.json").write_bytes(b'{"failure_id":"FD-030"}\xff\n')
        _write(root / "visible.receipt", "failure_id: FD-030\n")

        completed = subprocess.run(
            [sys.executable, str(script), str(root)],
            check=False,
            capture_output=True,
        )

        assert completed.returncode == 1
        assert completed.stderr == b""
        assert json.loads(completed.stdout)["unreadable_files"] == ["hidden.json:invalid_utf8"]
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]


def test_yaml_alternate_keys_are_consumed_or_fail_closed(tmp_path: Path) -> None:
    _write(tmp_path / "explicit.yaml", "? failure_id\n: FD-030\n")
    _write(tmp_path / "escaped-u.yaml", '"failure\\u005fid": "FD-030"\n')
    _write(tmp_path / "escaped-x.yaml", '"failure\\x5fid": "FD-030"\n')
    _write(tmp_path / "escaped-long.yaml", '"failure\\U0000005fid": "FD-030"\n')

    report = validate_failure_ids.validate([tmp_path])

    assert report["status"] == "fail"
    assert report["duplicate_definitions"] == {
        "FD-030": [
            "escaped-long.yaml:1",
            "escaped-u.yaml:1",
            "escaped-x.yaml:1",
            "explicit.yaml:1",
        ]
    }


def test_yaml_nested_flow_and_reserved_multiline_forms_never_pass_silently(
    tmp_path: Path,
) -> None:
    variants = {
        "ordinary.yaml": ("failure_id: FD-030\n", "consumed"),
        "nested.yaml": ("outer:\n  failure_id: FD-030\n", "consumed"),
        "flow.yaml": ("outer: {failure_id: FD-030}\n", "consumed"),
        "multiline.yaml": ("failure_id: >-\n  FD-030\n", "malformed"),
        "duplicate-key.yaml": ("failure_id: FD-030\nfailure_id: FD-030\n", "duplicate"),
        "malformed.yaml": ("failure_id: [FD-030\n", "malformed"),
    }

    for name, (content, expected) in variants.items():
        root = tmp_path / name.removesuffix(".yaml")
        _write(root / name, content)

        report = validate_failure_ids.validate([root])

        if expected == "consumed":
            assert report["definitions"] == {
                "FD-030": [f"{name}:2" if name == "nested.yaml" else f"{name}:1"]
            }
        elif expected == "duplicate":
            assert report["status"] == "fail", name
            assert report["duplicate_definitions"], name
        else:
            assert report["status"] == "fail", name
            assert report["malformed_definitions"], name


def test_lone_snapshot_surrogates_return_deterministic_json_without_traceback(
    tmp_path: Path,
) -> None:
    fields = (
        ("source", "description"),
        ("canonical_definitions", "FD-001"),
        ("legacy_ambiguities", "FD-001", 0),
        ("key",),
        ("arbitrary",),
    )
    script = Path(validate_failure_ids.__file__).resolve()
    outputs: list[bytes] = []

    for checkout_number in (1, 2):
        checkout = tmp_path / f"checkout-{checkout_number}"
        repo = checkout / "repo"
        _write(repo / "README.md", "Reference FD-001.\n")
        for surrogate_number, surrogate in enumerate(("\ud800", "\udc00")):
            for field_number, path in enumerate(fields):
                data = _snapshot_data()
                if path == ("source", "description"):
                    data["source"]["description"] = surrogate
                elif path == ("canonical_definitions", "FD-001"):
                    data["canonical_definitions"]["FD-001"] = surrogate
                elif path == ("legacy_ambiguities", "FD-001", 0):
                    data["legacy_ambiguities"] = {"FD-001": [surrogate, "second"]}
                elif path == ("key",):
                    data[surrogate] = "value"
                else:
                    data["arbitrary"] = surrogate
                data["payload_sha256"] = "0" * 64
                snapshot = checkout / f"snapshot-{surrogate_number}-{field_number}.json"
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_text(json.dumps(data), encoding="utf-8")

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        str(repo),
                        "--ledger-snapshot",
                        str(snapshot),
                        "--expected-ledger-payload-sha256",
                        "0" * 64,
                    ],
                    check=False,
                    capture_output=True,
                )

                assert completed.returncode == 1, (path, surrogate_number)
                assert completed.stderr == b"", (path, surrogate_number)
                report = json.loads(completed.stdout)
                assert report["status"] == "fail", (path, surrogate_number)
                assert "snapshot:invalid_unicode" in report["snapshot_errors"], (
                    path,
                    surrogate_number,
                )
                outputs.append(completed.stdout)

    midpoint = len(outputs) // 2
    assert outputs[:midpoint] == outputs[midpoint:]
