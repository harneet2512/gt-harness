from __future__ import annotations

import pytest

from gt_engine.miniswe_integration import ExternalStateStore
from gt_engine.request_history import load_provider_request, store_provider_request


def test_repeated_history_messages_are_physically_deduplicated(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    first = {
        "model": "fixture/model",
        "messages": [{"role": "user", "content": "same"}],
    }
    second = {
        "model": "fixture/model",
        "messages": [
            {"role": "user", "content": "same"},
            {"role": "assistant", "content": "new"},
        ],
    }
    first_sha, first_path, first_manifest, first_stats = store_provider_request(
        store, first
    )
    second_sha, second_path, second_manifest, second_stats = store_provider_request(
        store, second
    )

    assert len(tuple((store.root / "provider_messages").glob("*.json"))) == 2
    assert first_stats["message_unique_objects_written"] == 1
    assert second_stats["message_reference_count"] == 2
    assert second_stats["message_unique_objects_written"] == 1
    assert second_stats["message_unique_bytes_written"] < second_stats[
        "message_referenced_bytes"
    ]
    assert not (store.root / "provider_requests").exists()
    assert load_provider_request(store.root, {
        "request_manifest": first_path,
        "request_manifest_sha256": first_manifest,
        "payload_sha256": first_sha,
    }) == first
    assert load_provider_request(store.root, {
        "request_manifest": second_path,
        "request_manifest_sha256": second_manifest,
        "payload_sha256": second_sha,
    }) == second


def test_message_tampering_is_rejected(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    payload = {"messages": [{"role": "user", "content": "exact"}]}
    request_sha, path, manifest_sha, _ = store_provider_request(store, payload)
    message = next((store.root / "provider_messages").glob("*.json"))
    message.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="message_identity_mismatch"):
        load_provider_request(store.root, {
            "request_manifest": path,
            "request_manifest_sha256": manifest_sha,
            "payload_sha256": request_sha,
        })
