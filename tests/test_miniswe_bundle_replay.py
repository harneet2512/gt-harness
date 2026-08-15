from __future__ import annotations

import hashlib
import json

from gt_engine.miniswe_integration import ExternalStateStore
from scripts.miniswe_gt_replay import audit_replay_bundle


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_replay_bundle_validates_event_anchor_and_provider_blobs(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("session_started")
    request_dir = store.root / "provider_requests"
    request_dir.mkdir()
    blob = request_dir / "blob.json"
    blob.write_text('{"messages":[]}', encoding="utf-8")
    provider = store.root / "provider_events.jsonl"
    rows = [
        {
            "event": "provider_request", "request_id": "r1",
            "request_blob": "provider_requests/blob.json",
            "request_blob_sha256": _sha(blob),
        },
        {"event": "provider_response", "request_id": "r1"},
    ]
    provider.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": "gt.repro.v1",
        "research_valid": True,
        "event_journal": {**store.receipt(), "path": "events.jsonl"},
        "provider_receipts": {
            "events_path": "provider_events.jsonl",
            "events_sha256": _sha(provider),
            "request_count": 1,
            "valid": True,
        },
    }
    manifest_path = store.root / "reproducibility_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = audit_replay_bundle(manifest_path)
    assert report["valid"] is True
    assert report["event_count"] == 1
    assert report["provider_request_count"] == 1


def test_replay_bundle_rejects_tampered_provider_blob(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("session_started")
    request_dir = store.root / "provider_requests"
    request_dir.mkdir()
    blob = request_dir / "blob.json"
    blob.write_text("original", encoding="utf-8")
    original_hash = _sha(blob)
    provider = store.root / "provider_events.jsonl"
    provider.write_text(json.dumps({
        "event": "provider_request", "request_id": "r1",
        "request_blob": "provider_requests/blob.json",
        "request_blob_sha256": original_hash,
    }) + "\n", encoding="utf-8")
    manifest_path = store.root / "reproducibility_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": "gt.repro.v1",
        "research_valid": True,
        "event_journal": {**store.receipt(), "path": "events.jsonl"},
        "provider_receipts": {
            "events_path": "provider_events.jsonl",
            "events_sha256": _sha(provider), "request_count": 1,
            "valid": True,
        },
    }), encoding="utf-8")
    blob.write_text("tampered", encoding="utf-8")
    report = audit_replay_bundle(manifest_path)
    assert report["valid"] is False
    assert any("provider request blob hash mismatch" in issue
               for issue in report["issues"])
