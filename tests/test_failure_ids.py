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
    (root / ".githooks").mkdir()
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
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", check_failure_ids.EXPECTED_REMOTE],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    parent = _git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("reference\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    message = "candidate\n\n" + "\n".join(check_failure_ids.CANONICAL_TRAILER_LINES) + "\n"
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qF", "-"],
        input=message,
        text=True,
        check=True,
    )
    head = _git(root, "rev-parse", "HEAD")
    manifest = root / ".githooks" / "failure-gate.json"
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


def test_wrong_origin_is_a_lineage_refusal(tmp_path):
    root, manifest = _fixture(tmp_path)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/wrong.git",
        ],
        check=True,
    )
    result = check_failure_ids.evaluate(root, manifest=manifest)
    assert result == check_failure_ids._refusal("LINEAGE_MISMATCH", "origin_repository")


def test_common_hook_installation_and_post_commit_contract_are_tracked():
    repository = Path(__file__).resolve().parents[1]
    install = (repository / ".githooks" / "install").read_text(encoding="utf-8")
    pre_commit = (repository / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    post_commit = (repository / ".githooks" / "post-commit").read_text(encoding="utf-8")
    assert "--git-common-dir" in install
    assert "core.hooksPath" in install
    assert "post-commit" in install
    assert "core.hooksPath" in pre_commit
    assert "common repository .githooks" in pre_commit
    assert "git push --porcelain" in post_commit
    assert "GNX_PUSH_REMOTE" in post_commit
    assert "gnx-autopush.failures.log" in post_commit
    for hook in (".githooks/install", ".githooks/pre-commit", ".githooks/post-commit"):
        index = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "--stage", "--", hook],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert index.startswith("100755 "), (hook, index)
    receipt = json.loads(
        (repository / ".githooks" / "hook-installation.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["schema"] == "gt.hook_installation_receipt.v1"
    assert receipt["status"] == "PASS"
    assert all(hook["mode"] == "100755" for hook in receipt["hooks"])
    assert receipt["post_commit"]["behavior"] == "preserved-auto-push"


def test_pre_commit_direct_command_bootstraps_repository_import_path():
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["python", str(repository / ".githooks" / "pre-commit")],
        cwd=repository,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" not in result.stderr
    assert "LINEAGE_MISMATCH" in result.stdout

def test_final_readiness_requires_verdict_registry(tmp_path):
    root, manifest = _fixture(tmp_path)
    result = check_failure_ids.evaluate(
        root, manifest=manifest, require_verdict_coverage=True
    )
    assert result == check_failure_ids._refusal(
        "VERDICT_COVERAGE_MISSING", "registry_missing"
    )


def test_final_readiness_rejects_nonterminal_or_duplicate_rows(tmp_path):
    root, manifest = _fixture(tmp_path)
    head = _git(root, "rev-parse", "HEAD")
    digest = "0" * 64
    registry = root / check_failure_ids.VERDICT_REGISTRY
    registry.write_text(
        json.dumps(
            {
                "schema": check_failure_ids.VERDICT_SCHEMA,
                "source_revision": head,
                "rows": [
                    {
                        "sha": head,
                        "verdict": "REJECT",
                        "manifest_sha256": digest,
                        "success_criteria_sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = check_failure_ids.evaluate(
        root, manifest=manifest, require_verdict_coverage=True
    )
    assert result == check_failure_ids._refusal(
        "VERDICT_COVERAGE_INVALID", "registry_verdict"
    )


def test_shipped_manifest_matches_gate_refusal_contract() -> None:
    manifest, error = check_failure_ids._load_manifest(
        Path(__file__).resolve().parents[1] / ".githooks" / "failure-gate.json"
    )
    assert error is None
    assert manifest is not None
