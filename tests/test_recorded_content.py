from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from gt_harness.recorded_content import measure_recorded_content, verify_recorded_content

FIXTURE = Path(__file__).parent / "fixtures" / "har81_attestation" / "content_recordings.json"


def _digest_is_sealed(result: dict) -> bool:
    body = dict(result)
    supplied = body.pop("packet_digest_sha256")
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest() == supplied


def test_real_run_19_20_21_content_is_rederived_from_retained_artifacts() -> None:
    result = verify_recorded_content(FIXTURE)

    assert result["status"] == "PASS"
    assert result["run_count"] == 3
    assert result["delivery_count"] == 12
    assert result["rederived_count"] == 12
    assert result["payload_match_count"] == 12
    assert result["provider_request_match_count"] == 12
    assert result["target_match_count"] == 12
    assert result["trigger_match_count"] == 12
    assert result["claim_match_count"] == 12
    assert result["consequence_match_count"] == 12
    assert result["attestation_match_count"] == 12
    assert result["mismatch_count"] == 0
    assert result["renderer_provenance_match_count"] == 3
    assert result["historical_adjudication_count"] == 5
    assert result["failures"] == []
    assert {
        (row["run_id"], row["kind"], row["disposition"])
        for row in result["adjudications"]
    } == {
        (
            "33563173631",
            "localization",
            "artifact_json_count_proven_by_renderer_source",
        ),
        (
            "33565965241",
            "localization",
            "artifact_json_count_proven_by_renderer_source",
        ),
        (
            "33567358689",
            "localization",
            "artifact_json_count_proven_by_renderer_source",
        ),
        (
            "33563173631",
            "new_file_destination",
            "legacy_precap_telemetry_defect_repaired",
        ),
        (
            "33565965241",
            "new_file_destination",
            "legacy_precap_telemetry_defect_repaired",
        ),
    }
    assert _digest_is_sealed(result)


def test_measurement_reproduces_all_four_mutation_surfaces() -> None:
    result = measure_recorded_content(FIXTURE)

    assert result["status"] == "PASS"
    assert result["provider_calls"] == 0
    assert result["mutation_cases"] == {
        "payload": "FAIL_AS_DESIGNED",
        "graph": "FAIL_AS_DESIGNED",
        "event_stream": "FAIL_AS_DESIGNED",
        "provider_request": "FAIL_AS_DESIGNED",
    }
    assert _digest_is_sealed(result)


def test_renderer_source_or_product_revision_mutation_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "attestation"
    shutil.copytree(FIXTURE.parent, copied)
    copied_fixture = copied / FIXTURE.name
    fixture = json.loads(copied_fixture.read_text(encoding="utf-8"))
    run = fixture["runs"][0]
    source_record = next(
        row
        for row in run["renderer_provenance"]["source_files"]
        if row["repository_path"] == "scripts/miniswe_gt_run.py"
    )
    source = copied / source_record["path"]
    source.write_bytes(source.read_bytes() + b"\n# tampered\n")

    result = verify_recorded_content(copied_fixture)
    reasons = {reason for failure in result["failures"] for reason in failure["reason_codes"]}

    assert result["status"] == "FAIL"
    assert "renderer_source_digest_mismatch" in reasons


def test_renderer_source_cannot_self_certify_against_mutated_fixture_hashes(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "attestation"
    shutil.copytree(FIXTURE.parent, copied)
    copied_fixture = copied / FIXTURE.name
    fixture = json.loads(copied_fixture.read_text(encoding="utf-8"))
    record = fixture["runs"][0]["renderer_provenance"]["source_files"][0]
    source = copied / record["path"]
    tampered = source.read_bytes() + b"\n# forged fixture source\n"
    source.write_bytes(tampered)
    record["bytes"] = len(tampered)
    record["sha256"] = hashlib.sha256(tampered).hexdigest()
    record["git_blob_sha1"] = hashlib.sha1(
        b"blob " + str(len(tampered)).encode("ascii") + b"\0" + tampered
    ).hexdigest()
    copied_fixture.write_text(json.dumps(fixture), encoding="utf-8")

    result = verify_recorded_content(copied_fixture)
    reasons = {
        reason for failure in result["failures"] for reason in failure["reason_codes"]
    }

    assert result["status"] == "FAIL"
    assert "renderer_source_git_blob_mismatch" in reasons


def test_new_file_adjudication_requires_delivery_time_snapshot_target(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "attestation"
    shutil.copytree(FIXTURE.parent, copied)
    copied_fixture = copied / FIXTURE.name
    fixture = json.loads(copied_fixture.read_text(encoding="utf-8"))
    delivery = next(
        delivery
        for run in fixture["runs"]
        for delivery in run["deliveries"]
        if delivery["kind"] == "new_file_destination"
    )
    delivery["delivery_time_snapshot_sequence"] = delivery["event_sequence"] + 1
    copied_fixture.write_text(json.dumps(fixture), encoding="utf-8")

    result = verify_recorded_content(copied_fixture)
    reasons = {reason for failure in result["failures"] for reason in failure["reason_codes"]}

    assert result["status"] == "FAIL"
    assert "delivery_time_snapshot_order_invalid" in reasons


def test_independent_derivation_artifact_tampering_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "attestation"
    shutil.copytree(FIXTURE.parent, copied)
    copied_fixture = copied / FIXTURE.name
    fixture = json.loads(copied_fixture.read_text(encoding="utf-8"))
    trace = next(
        delivery
        for run in fixture["runs"]
        for delivery in run["deliveries"]
        if delivery["kind"] == "trace_frame"
    )
    trajectory = copied / trace["derivation_path"]
    payload = json.loads(trajectory.read_text(encoding="utf-8"))
    message = payload["messages"][trace["provider_message_index"]]
    native = message["content"].split("</gt-facts>", 1)[1]
    assert "evaluator/functions.go:2346" in native
    message["content"] = message["content"].replace(
        "evaluator/functions.go:2346", "evaluator/functions.go:9999"
    )
    trajectory.write_text(json.dumps(payload), encoding="utf-8")

    result = verify_recorded_content(copied_fixture)
    reasons = {reason for failure in result["failures"] for reason in failure["reason_codes"]}

    assert result["status"] == "FAIL"
    assert "derivation_artifact_digest_mismatch" in reasons
    assert "shipped_payload_mismatch" in reasons


def test_a15_manifest_routes_to_real_retained_recordings() -> None:
    manifest = json.loads((FIXTURE.parent / "manifest.json").read_text(encoding="utf-8"))
    content = manifest["content_correctness"]

    assert content["fixture"] == FIXTURE.name
    assert content["run_ids"] == ["33563173631", "33565965241", "33567358689"]
    assert content["shipped_delivery_count"] == 12
    assert content["artifact_source"] == "HAR-82"
    assert content["historical_adjudication_count"] == 5
    assert content["renderer_source_revisions"] == [
        "529d835a2b3e1c6c3e3752cd8bfa477b27b591a0",
        "357666739cfe778b71508c68341948087f639b89",
        "ca744e7ebb7d03b6d11e31eeab8600cdb3669bdd",
    ]
    assert content["mutation_surfaces"] == [
        "payload",
        "graph",
        "event_stream",
        "provider_request",
    ]
