from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from scripts.verify_producer_binding import evaluate, language_specs


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _producer_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A stand-in producer repo. Returns (path, commit, root tree)."""

    root = tmp_path / "producer"
    (root / "gt-index").mkdir(parents=True)
    (root / "gt-index" / "main.go").write_text("package main\n", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "producer"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root, _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")


def _harness(tmp_path: Path) -> Path:
    root = tmp_path / "harness"
    spec = root / "vendor" / "gt-index-src" / "internal" / "specs"
    spec.mkdir(parents=True)
    (spec / "python.go").write_text("package specs\n", encoding="utf-8")
    (spec / "spec.go").write_text("package specs\n", encoding="utf-8")
    (root / "vendor" / "gt-index-linux-amd64").write_bytes(b"binary")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "harness"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def _bundle(harness: Path, *, commit: str, tree: str) -> dict:
    digest = hashlib.sha256(
        (harness / "vendor" / "gt-index-linux-amd64").read_bytes()
    ).hexdigest()
    return {
        "groundtruth": {
            "producer_path": "vendor/gt-index-linux-amd64",
            "producer_sha256": digest,
            "producer_build": {"source_commit": commit, "source_tree": tree},
        }
    }


def test_a_binary_built_from_the_pinned_commit_verifies(tmp_path: Path):
    producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)

    report, failures = evaluate(
        _bundle(harness, commit=commit, tree=tree), root=harness, producer_repo=producer
    )

    assert failures == []
    assert report["binding"] == "verified"
    assert report["status"] == "VERIFIED"


def test_a_tree_that_is_not_the_commits_tree_is_caught(tmp_path: Path):
    producer, commit, _tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)

    _report, failures = evaluate(
        _bundle(harness, commit=commit, tree="0" * 40), root=harness, producer_repo=producer
    )

    assert "producer_source_tree_does_not_match_commit" in failures


def test_a_commit_absent_from_the_producer_is_caught(tmp_path: Path):
    producer, _commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)

    _report, failures = evaluate(
        _bundle(harness, commit="0" * 40, tree=tree), root=harness, producer_repo=producer
    )

    assert "producer_source_commit_absent_from_producer_repo" in failures


def test_without_the_producer_repo_the_binding_is_undecided_not_failed(tmp_path: Path):
    """The producer lives in another repository. Absence is a limitation of
    where the check runs, not evidence of drift — and CI must not fail on it."""

    _producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)

    report, failures = evaluate(
        _bundle(harness, commit=commit, tree=tree), root=harness, producer_repo=None
    )

    assert failures == []
    assert report["binding"] == "not_checked_without_producer_repo"


def test_the_vendored_copy_is_recorded_never_asserted(tmp_path: Path):
    """It is an inspection copy, not the build source; divergence is normal."""

    producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)

    report, failures = evaluate(
        _bundle(harness, commit=commit, tree=tree), root=harness, producer_repo=producer
    )

    assert report["vendored_source_tree"]  # measured
    assert not any("vendored" in f for f in failures)  # never a failure


def test_a_tampered_binary_is_caught(tmp_path: Path):
    producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)
    bundle = _bundle(harness, commit=commit, tree=tree)
    bundle["groundtruth"]["producer_sha256"] = "f" * 64

    _report, failures = evaluate(bundle, root=harness, producer_repo=producer)

    assert "producer_binary_digest_mismatch" in failures


def test_a_missing_binary_is_caught(tmp_path: Path):
    producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)
    bundle = _bundle(harness, commit=commit, tree=tree)
    (harness / "vendor" / "gt-index-linux-amd64").unlink()

    _report, failures = evaluate(bundle, root=harness, producer_repo=producer)

    assert "producer_binary_missing" in failures


def test_absent_pins_fail_closed(tmp_path: Path):
    producer, commit, tree = _producer_repo(tmp_path)
    harness = _harness(tmp_path)
    bundle = _bundle(harness, commit=commit, tree=tree)
    bundle["groundtruth"]["producer_build"] = {}

    _report, failures = evaluate(bundle, root=harness, producer_repo=producer)

    assert "producer_build_source_tree_missing" in failures
    assert "producer_build_source_commit_missing" in failures


def test_languages_are_measured_from_the_vendored_copy(tmp_path: Path):
    harness = _harness(tmp_path)
    assert language_specs(harness) == ["python"]
