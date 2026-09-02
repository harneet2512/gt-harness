import hashlib
import json
from pathlib import Path

import pytest

from scripts.release_manifest import load_release_manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path: Path) -> Path:
    prediction = tmp_path / "prediction.json"
    baseline = tmp_path / "baseline.json"
    treatment = tmp_path / "treatment.json"
    prediction.write_text("{}\n", encoding="utf-8")
    baseline.write_text("{}\n", encoding="utf-8")
    treatment.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "active_release.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "gt.release_manifest.v1",
                "release_id": "fixture",
                "task_profile": "repair20-v1",
                "runtime_commit": "a" * 40,
                "prediction": {"path": "prediction.json", "sha256": _sha256(prediction)},
                "baseline": {"path": "baseline.json", "sha256": _sha256(baseline)},
                "treatment": {"path": "treatment.json", "sha256": _sha256(treatment)},
                "allowed_post_runtime_paths": ["active_release.json", "prediction.json"],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_release_manifest_resolves_and_hashes_every_active_input(tmp_path: Path) -> None:
    manifest = load_release_manifest(_write_fixture(tmp_path), root=tmp_path)
    assert manifest.release_id == "fixture"
    assert manifest.runtime_commit == "a" * 40
    assert manifest.prediction_path == tmp_path / "prediction.json"
    assert manifest.allowed_post_runtime_paths == (
        "active_release.json",
        "prediction.json",
    )


def test_release_manifest_rejects_stale_content_hash(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path)
    (tmp_path / "prediction.json").write_text('{"stale": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="prediction sha256 mismatch"):
        load_release_manifest(path, root=tmp_path)


def test_release_manifest_hash_is_stable_across_checkout_newlines(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path)
    prediction = tmp_path / "prediction.json"
    lf_bytes = b'{\n  "value": true\n}\n'
    prediction.write_bytes(lf_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["prediction"]["sha256"] = hashlib.sha256(lf_bytes).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    prediction.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    manifest = load_release_manifest(path, root=tmp_path)

    assert manifest.prediction_path == prediction


def test_release_manifest_still_rejects_non_newline_mutation(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path)
    prediction = tmp_path / "prediction.json"
    lf_bytes = b'{\n  "value": true\n}\n'
    prediction.write_bytes(lf_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["prediction"]["sha256"] = hashlib.sha256(lf_bytes).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    prediction.write_bytes(lf_bytes.replace(b"true", b"false").replace(b"\n", b"\r\n"))

    with pytest.raises(ValueError, match="prediction sha256 mismatch"):
        load_release_manifest(path, root=tmp_path)


def test_release_manifest_rejects_paths_outside_repository(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["prediction"]["path"] = "../prediction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="outside release root"):
        load_release_manifest(path, root=tmp_path)


def test_active_artifact_path_is_not_duplicated_across_release_consumers() -> None:
    root = Path(__file__).resolve().parents[1]
    dated_name = "GT_FINAL_20_TASK_OUTCOME_PREDICTION_2026-08-19_V2.json"
    consumers = (
        root / ".github/workflows/tb2_miniswe_central.yml",
        root / "scripts/tb2_merge_results.py",
        root / "tests/test_verify_frozen_outcome_prediction.py",
    )
    assert all(dated_name not in path.read_text(encoding="utf-8") for path in consumers)
    assert all(
        "eval/frozen_baselines/tb2_miniswe_20260731.json"
        not in path.read_text(encoding="utf-8")
        for path in consumers[:2]
    )
    assert all(
        "eval/treatments/tb2_central_relational_v2.json"
        not in path.read_text(encoding="utf-8")
        for path in consumers[:2]
    )
