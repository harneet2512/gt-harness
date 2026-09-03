from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.verify_producer_binding import evaluate, language_specs


def _repo(tmp_path: Path, *, specs: list[str]) -> Path:
    root = tmp_path / "repo"
    spec_dir = root / "vendor" / "gt-index-src" / "internal" / "specs"
    spec_dir.mkdir(parents=True)
    for name in specs:
        (spec_dir / f"{name}.go").write_text(f"package specs // {name}\n", encoding="utf-8")
    (spec_dir / "spec.go").write_text("package specs\n", encoding="utf-8")
    (spec_dir / "spec_test.go").write_text("package specs\n", encoding="utf-8")
    (root / "vendor" / "gt-index-linux-amd64").write_bytes(b"binary")

    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "vendor"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def _bundle(root: Path, *, source_tree: str, source_commit: str = "") -> dict:
    digest = hashlib.sha256((root / "vendor" / "gt-index-linux-amd64").read_bytes()).hexdigest()
    return {
        "groundtruth": {
            "producer_path": "vendor/gt-index-linux-amd64",
            "producer_sha256": digest,
            "producer_build": {
                "source_tree": source_tree,
                "source_commit": source_commit,
            },
        }
    }


def _tree(root: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD:vendor/gt-index-src"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_matching_source_and_binary_verifies(tmp_path: Path):
    root = _repo(tmp_path, specs=["python", "rust"])
    report, failures = evaluate(_bundle(root, source_tree=_tree(root)), root=root)

    assert failures == []
    assert report["status"] == "VERIFIED"


def test_drifted_source_tree_is_reported(tmp_path: Path):
    """The live defect: the executed binary was built from another tree."""

    root = _repo(tmp_path, specs=["python"])
    report, failures = evaluate(_bundle(root, source_tree="0" * 40), root=root)

    assert "vendored_source_tree_mismatch" in failures
    assert report["status"] == "UNVERIFIED"


def test_unreachable_source_commit_is_reported(tmp_path: Path):
    root = _repo(tmp_path, specs=["python"])
    bundle = _bundle(root, source_tree=_tree(root), source_commit="0" * 40)
    _report, failures = evaluate(bundle, root=root)

    assert failures == ["producer_source_commit_absent_from_repository"]


def test_binary_digest_is_checked(tmp_path: Path):
    root = _repo(tmp_path, specs=["python"])
    bundle = _bundle(root, source_tree=_tree(root))
    bundle["groundtruth"]["producer_sha256"] = "f" * 64
    _report, failures = evaluate(bundle, root=root)

    assert "producer_binary_digest_mismatch" in failures


def test_missing_binary_is_reported(tmp_path: Path):
    root = _repo(tmp_path, specs=["python"])
    bundle = _bundle(root, source_tree=_tree(root))
    (root / "vendor" / "gt-index-linux-amd64").unlink()
    _report, failures = evaluate(bundle, root=root)

    assert "producer_binary_missing" in failures


def test_languages_are_measured_not_declared(tmp_path: Path):
    """Depth becomes an observation of the source, not an assertion in config."""

    root = _repo(tmp_path, specs=["cobol", "python", "verilog"])
    assert language_specs(root) == ["cobol", "python", "verilog"]


def test_spec_scaffolding_is_not_counted_as_a_language(tmp_path: Path):
    root = _repo(tmp_path, specs=["python"])
    measured = language_specs(root)

    assert "spec" not in measured
    assert "spec_test" not in measured


@pytest.mark.parametrize("missing", ["source_tree"])
def test_absent_pin_fails_closed(tmp_path: Path, missing: str):
    root = _repo(tmp_path, specs=["python"])
    bundle = _bundle(root, source_tree=_tree(root))
    bundle["groundtruth"]["producer_build"].pop(missing)
    _report, failures = evaluate(bundle, root=root)

    assert "producer_build_source_tree_missing" in failures
