from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from gt_harness.recorded_content import measure_recorded_content, verify_recorded_content

FIXTURE = Path(__file__).parent / "fixtures" / "har81_attestation" / "content_recordings.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_run_19_20_21_recorded_content_rederives_exactly() -> None:
    result = verify_recorded_content(_fixture())

    assert result["status"] == "PASS"
    assert result["run_count"] == 3
    assert result["delivery_count"] == 12
    assert result["rederived_count"] == 12
    assert result["match_count"] == 12
    assert result["mismatch_count"] == 0
    assert result["target_match_count"] == 12
    assert result["trigger_match_count"] == 12
    assert result["claim_match_count"] == 12
    assert result["consequence_match_count"] == 12
    body = dict(result)
    supplied = body.pop("packet_digest_sha256")
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == supplied


def test_a15_manifest_routes_to_the_content_correctness_fixture() -> None:
    manifest = json.loads((FIXTURE.parent / "manifest.json").read_text(encoding="utf-8"))
    content = manifest["content_correctness"]

    assert content["fixture"] == FIXTURE.name
    assert content["run_ids"] == ["33563173631", "33565965241", "33567358689"]
    assert content["shipped_delivery_count"] == 12
    assert content["mutation_expected"] == "FAIL_AS_DESIGNED"


def test_recorded_content_payload_mutation_fails_closed() -> None:
    fixture = _fixture()
    fixture["runs"][0]["deliveries"][0]["recorded_payload_sha256"] = "0" * 64

    result = verify_recorded_content(fixture)

    assert result["status"] == "FAIL"
    assert result["mismatch_count"] == 1
    assert "payload_hash_mismatch" in result["failures"][0]["reason_codes"]


def test_recorded_content_graph_fact_mutation_fails_closed() -> None:
    fixture = _fixture()
    mutated = copy.deepcopy(fixture)
    localization = next(
        fact
        for fact in mutated["graph_states"][0]["facts"]
        if fact["kind"] == "localization"
    )
    localization["ranked_targets"][0]["score"] += 1

    result = verify_recorded_content(mutated)

    assert result["status"] == "FAIL"
    assert result["mismatch_count"] >= 1
    assert any(
        "payload_hash_mismatch" in failure["reason_codes"]
        for failure in result["failures"]
    )


def test_recorded_envelope_target_trigger_claim_and_consequence_are_checked() -> None:
    mutations = (
        ("target", "missing/file.go", "target_not_in_recorded_graph"),
        ("trigger_class", "unrecorded_trigger", "trigger_class_mismatch"),
        ("canonical_claim", "invented_claim", "canonical_claim_mismatch"),
        ("consequence", "invented_consequence", "consequence_mismatch"),
    )
    for field, value, expected_reason in mutations:
        fixture = _fixture()
        fixture["runs"][0]["deliveries"][0]["envelope"][field] = value

        result = verify_recorded_content(fixture)

        assert result["status"] == "FAIL"
        assert expected_reason in result["failures"][0]["reason_codes"]


def test_measurement_proves_its_mutation_case_fails() -> None:
    result = measure_recorded_content(_fixture())

    assert result["status"] == "PASS"
    assert result["mutation_case"]["outcome"] == "FAIL_AS_DESIGNED"
    assert result["mutation_case"]["mismatch_count"] == 1
    body = dict(result)
    supplied = body.pop("packet_digest_sha256")
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == supplied
