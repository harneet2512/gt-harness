from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

from scripts.check_red_evidence_producers import validate
from scripts.compare_red_evidence import compare


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repository_producer_inventory_routes_through_canonical_cli() -> None:
    repository = Path(__file__).resolve().parents[1]

    report = validate(repository)

    assert report["status"] == "pass"
    assert report["operative_producers"] == [".github/workflows/red-evidence-integrity.yml"]
    historical = report["historical_producers"]
    assert len(historical) == 14
    assert {item["status"] for item in historical} == {"frozen_historical"}
    assert {item["replay_disposition"] for item in historical} == {
        "inventory_only",
        "representative_replay",
    }


def test_producer_guard_rejects_bypass_and_unregistered_red_workflow(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    for logical in (
        "config/red_evidence_producers.json",
        "scripts/red_evidence.py",
        ".github/workflows/red-evidence-integrity.yml",
    ):
        source = repository / logical
        target = tmp_path / logical
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (tmp_path / "tests" / "red_artifacts").mkdir(parents=True)
    workflow = tmp_path / ".github" / "workflows" / "red-evidence-integrity.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("scripts/red_evidence.py capture", "go test"),
        encoding="utf-8",
    )

    bypass = validate(tmp_path)
    assert bypass["status"] == "fail"
    assert any("missing_canonical_token" in error for error in bypass["errors"])

    shutil.copyfile(repository / ".github/workflows/red-evidence-integrity.yml", workflow)
    _write(tmp_path / ".github" / "workflows" / "rogue-red.yml", "name: rogue\n")
    inventory = validate(tmp_path)
    assert "operative_producers:inventory_mismatch" in inventory["errors"]

    (tmp_path / ".github" / "workflows" / "rogue-red.yml").unlink()
    (tmp_path / ".github" / "workflows" / "alternate-name.yml").write_text(
        "name: decoy\nrun: scripts/red_evidence.py capture --evidence-dir x\n",
        encoding="utf-8",
    )
    alternate = validate(tmp_path)
    assert "operative_producers:inventory_mismatch" in alternate["errors"]


def test_producer_guard_binds_historical_commit_lineage(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    config = repository / "config" / "red_evidence_producers.json"
    target = tmp_path / "config" / "red_evidence_producers.json"
    target.parent.mkdir(parents=True)
    manifest = json.loads(config.read_text(encoding="utf-8"))
    manifest["historical_producers"][0]["commit"] = "0" * 40
    target.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(repository / "scripts/red_evidence.py", tmp_path / "scripts/red_evidence.py")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    shutil.copyfile(
        repository / ".github/workflows/red-evidence-integrity.yml",
        tmp_path / ".github/workflows/red-evidence-integrity.yml",
    )
    (tmp_path / "tests" / "red_artifacts").mkdir(parents=True)
    report = validate(tmp_path)
    assert (
        "historical_producers:wrong_commit:.githooks/tests/cha_rta_boundary_red.sh"
        in report["errors"]
    )


def test_cross_runner_compare_binds_body_and_toolchain(tmp_path: Path) -> None:
    body = (
        b"# example.invalid/redfixture [example.invalid/redfixture.test]\n"
        b"./proof.go:1:1: undefined: VTAFlowProof\nPACKAGE_OUTCOME=build_failed\n"
    )
    receipt = {
        "runner": {
            "architecture": "X64",
            "image_label": "ubuntu-22.04",
            "image_version": "20260820.1",
            "os_release_sha256": "1" * 64,
        },
        "capture_runtime": {"python_version": "3.10.12"},
        "diagnostic": {"sha256": hashlib.sha256(body).hexdigest()},
        "toolchain": {
            "text": "go version go1.26.6 linux/amd64\n",
            "executable": {"sha256": "a" * 64},
        },
    }
    roots = [tmp_path / "ubuntu-22", tmp_path / "ubuntu-24"]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "canonical.txt").write_bytes(body)
        row = copy.deepcopy(receipt)
        row["runner"]["image_label"] = f"ubuntu-{22 + index * 2}.04"
        row["runner"]["os_release_sha256"] = str(index + 1) * 64
        (root / "receipt.json").write_text(json.dumps(row), encoding="utf-8")

    assert compare(*roots)["status"] == "pass"

    changed = json.loads((roots[1] / "receipt.json").read_text(encoding="utf-8"))
    changed["toolchain"]["executable"]["sha256"] = "b" * 64
    (roots[1] / "receipt.json").write_text(json.dumps(changed), encoding="utf-8")
    assert "toolchain_executable_mismatch" in compare(*roots)["errors"]

    (roots[1] / "canonical.txt").write_bytes(body.replace(b"VTAFlowProof", b"OtherProof"))
    assert "canonical_body_mismatch" in compare(*roots)["errors"]
