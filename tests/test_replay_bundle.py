"""Provider-free tests for opt-in counterfactual replay capture."""

from __future__ import annotations

import gzip
import json

import pytest

from gt_engine.replay_bundle import ReplayBundleWriter, load_replay_bundle


def _request() -> list[dict[str, str]]:
    return [{"role": "user", "content": "Fix app.py"}]


def test_capture_is_disabled_without_writing_an_artifact(tmp_path):
    writer = ReplayBundleWriter(tmp_path / "bundle.json", enabled=False)

    metadata = writer.finalize()

    assert metadata["enabled"] is False
    assert metadata["trajectory_replay_ready"] is False
    assert metadata["model_causal_replay_ready"] is False
    assert not (tmp_path / "bundle.json").exists()


def test_capture_records_exact_requests_without_provider_specific_controls(tmp_path):
    path = tmp_path / "gt_replay"
    writer = ReplayBundleWriter(path, enabled=True)
    writer.record_request(
        call=1,
        provider_messages=_request(),
        request_payload_sha256="request-1",
        provider_messages_sha256="messages-1",
        model_name="test-model",
        model_kwargs={"temperature": 1.0},
        temperature=1.0,
        active_state={"source_revision": "s1"},
        source_revision="s1",
        workspace_revision="w1",
    )
    writer.record_response(call=1, response={"role": "assistant", "content": "ok"})

    metadata = writer.finalize()
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    calls = [
        json.loads(line)
        for line in (path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    request_blob = calls[0]["request_blob_sha256"]
    with gzip.open(path / "blobs" / f"{request_blob}.json.gz", "rt", encoding="utf-8") as stream:
        request = json.load(stream)

    assert metadata["complete"] is True
    assert metadata["request_bodies_captured"] is True
    assert metadata["responses_captured"] is True
    assert metadata["trajectory_replay_ready"] is True
    assert metadata["model_causal_replay_ready"] is False
    assert manifest["schema"] == "gt.counterfactual_replay_bundle.v2"
    assert request == _request()
    loaded = load_replay_bundle(path)
    assert loaded["calls"][0]["provider_messages"] == _request()


def test_capture_records_exact_control_and_treatment_decision_point(tmp_path):
    path = tmp_path / "gt_replay"
    control = [
        {"role": "user", "content": "Fix app.py"},
        {"role": "tool", "content": "validation failed"},
    ]
    payload = "GroundTruth evidence:\n- app.py:12 direct caller test_app.py:8"
    treatment = [dict(item) for item in control]
    treatment[-1]["content"] += "\n\n" + payload
    writer = ReplayBundleWriter(path, enabled=True)

    writer.record_request(
        call=2,
        provider_messages=treatment,
        control_provider_messages=control,
        intervention={
            "payload": payload,
            "message_index": 1,
            "prior_visible_gt_count": 0,
            "selected_contribution_ids": ["gt-contribution-1"],
        },
        provider_tools=[
            {
                "type": "function",
                "function": {"name": "bash", "parameters": {"type": "object"}},
            }
        ],
        request_payload_sha256="request-2",
        provider_messages_sha256="messages-2",
        model_name="test-model",
        model_kwargs={"temperature": 1.0},
        temperature=1.0,
        active_state={"source_revision": "s1"},
        source_revision="s1",
        workspace_revision="w1",
    )
    writer.record_response(
        call=2,
        response={"role": "assistant", "extra": {"actions": [{"command": "pytest -q"}]}},
    )

    metadata = writer.finalize()
    loaded = load_replay_bundle(path)
    row = loaded["calls"][0]

    assert metadata["paired_decision_point_count"] == 1
    assert metadata["paired_decision_capture_ready"] is True
    assert row["control_provider_messages"] == control
    assert row["provider_messages"] == treatment
    assert row["intervention"]["payload"] == payload
    assert row["provider_tools"][0]["function"]["name"] == "bash"
    assert row["provider_tools_captured"] is True
    assert row["control_provider_messages_sha256"] != row["provider_messages_sha256"]


def test_capture_is_content_addressed_instead_of_bounding_out_large_requests(tmp_path):
    writer = ReplayBundleWriter(tmp_path / "gt_replay", enabled=True, max_call_chars=1_000)
    writer.record_request(
        call=1,
        provider_messages=[{"role": "user", "content": "x" * 2_000}],
        request_payload_sha256="request-1",
        provider_messages_sha256="messages-1",
        model_name="test-model",
        model_kwargs={"temperature": 1.0},
        temperature=1.0,
        active_state={},
        source_revision="s1",
        workspace_revision="w1",
    )
    writer.record_response(call=1, response={"role": "assistant", "content": "ok"})

    metadata = writer.finalize()

    assert metadata["complete"] is True
    assert metadata["trajectory_replay_ready"] is True
    assert metadata["model_causal_replay_ready"] is False


def test_replay_loader_fails_closed_on_blob_tampering(tmp_path):
    path = tmp_path / "gt_replay"
    writer = ReplayBundleWriter(path, enabled=True)
    writer.record_request(
        call=1,
        provider_messages=_request(),
        request_payload_sha256="request-1",
        provider_messages_sha256="messages-1",
        model_name="test-model",
        model_kwargs={},
        temperature=1.0,
        active_state={},
        source_revision="s1",
        workspace_revision="w1",
    )
    writer.record_response(call=1, response={"role": "assistant", "content": "ok"})
    writer.finalize()
    blob = next((path / "blobs").glob("*.json.gz"))
    blob.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="replay blob"):
        load_replay_bundle(path)
