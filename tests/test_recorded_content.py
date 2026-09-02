from __future__ import annotations

import hashlib
import json
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

    assert result["status"] == "FAIL"
    assert result["run_count"] == 3
    assert result["delivery_count"] == 12
    assert result["rederived_count"] == 12
    assert result["payload_match_count"] == 12
    assert result["provider_request_match_count"] == 12
    assert result["target_match_count"] == 10
    assert result["trigger_match_count"] == 12
    assert result["claim_match_count"] == 12
    assert result["consequence_match_count"] == 12
    assert result["attestation_match_count"] == 7
    assert result["mismatch_count"] == 5
    reasons = {reason for failure in result["failures"] for reason in failure["reason_codes"]}
    assert "recorded_rendered_bytes_mismatch" in reasons
    assert "receipt_payload_hash_mismatch" in reasons
    assert "target_not_in_recorded_graph" in reasons
    assert _digest_is_sealed(result)


def test_measurement_reproduces_all_four_mutation_surfaces() -> None:
    result = measure_recorded_content(FIXTURE)

    assert result["status"] == "FAIL"
    assert result["provider_calls"] == 0
    assert result["mutation_cases"] == {
        "payload": "FAIL_AS_DESIGNED",
        "graph": "FAIL_AS_DESIGNED",
        "event_stream": "FAIL_AS_DESIGNED",
        "provider_request": "FAIL_AS_DESIGNED",
    }
    assert _digest_is_sealed(result)


def test_a15_manifest_routes_to_real_retained_recordings() -> None:
    manifest = json.loads((FIXTURE.parent / "manifest.json").read_text(encoding="utf-8"))
    content = manifest["content_correctness"]

    assert content["fixture"] == FIXTURE.name
    assert content["run_ids"] == ["33563173631", "33565965241", "33567358689"]
    assert content["shipped_delivery_count"] == 12
    assert content["artifact_source"] == "HAR-82"
    assert content["mutation_surfaces"] == [
        "payload",
        "graph",
        "event_stream",
        "provider_request",
    ]
