from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import check_failure_ids


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config" / "failure_id_ledger_snapshot.json").write_text(
        "fixture\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    parent = _git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("reference\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "candidate"], check=True)
    head = _git(root, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", check_failure_ids.EXPECTED_REMOTE],
        check=True,
    )
    manifest = root / "manifest.json"
    payload = {
        "schema": check_failure_ids.MANIFEST_SCHEMA,
        "repository": "gt-harness",
        "lineage": {"head_sha": head, "parent_sha": parent, "source_revision": head},
        "fixture_hashes": {
            "config/failure_id_ledger_snapshot.json": check_failure_ids._sha256(
                root / "config/failure_id_ledger_snapshot.json"
            )
        },
        "expected_refusals": check_failure_ids.EXPECTED_REFUSALS,
        "trailer_lines": check_failure_ids.CANONICAL_TRAILER_LINES,
        "repository_locator": {
            "remote": check_failure_ids.EXPECTED_REMOTE,
            "default_branch": check_failure_ids.EXPECTED_BRANCH,
        },
        "spec_bindings": [
            {
                "comment": comment.removeprefix("GT-Spec-Comment: "),
                "body_sha256": body.removeprefix("GT-Spec-Body-SHA256: "),
            }
            for comment, body in zip(
                check_failure_ids.CANONICAL_TRAILER_LINES[::2],
                check_failure_ids.CANONICAL_TRAILER_LINES[1::2],
                strict=True,
            )
        ],
        "required_evidence": {
            "failure_scan": {
                "expected_next": "FD-031",
                "ledger_snapshot": check_failure_ids.SNAPSHOT,
                "ledger_revision": check_failure_ids.SNAPSHOT_REVISION,
                "ledger_payload_sha256": check_failure_ids.SNAPSHOT_PAYLOAD,
            }
        },
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return root, manifest


def test_missing_manifest_is_stable_refusal(tmp_path):
    root, manifest = _fixture(tmp_path)
    result = check_failure_ids.evaluate(root, manifest=manifest.with_name("missing.json"))
    assert result == check_failure_ids._refusal("MANIFEST_MISSING", "manifest_missing")


def test_mutated_fixture_refuses_without_scanning_as_pass(tmp_path):
    root, manifest = _fixture(tmp_path)
    (root / "config" / "failure_id_ledger_snapshot.json").write_text("mutated\n", encoding="utf-8")
    result = check_failure_ids.evaluate(root, manifest=manifest)
    assert result["status"] == "REFUSED"
    assert result["code"] == "FIXTURE_MISMATCH"


def test_manifest_mutation_and_trailer_mutation_are_refused(tmp_path):
    root, manifest = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["trailer_lines"][0] = "GT-Spec-Comment: mutated"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = check_failure_ids.evaluate(root, manifest=manifest)
    assert result == check_failure_ids._refusal("MANIFEST_INVALID", "manifest_trailer_block")


def test_refusal_receipt_is_byte_stable(tmp_path):
    root, manifest = _fixture(tmp_path)
    first = check_failure_ids._refusal("FIXTURE_MISMATCH", "same")
    second = check_failure_ids._refusal("FIXTURE_MISMATCH", "same")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
