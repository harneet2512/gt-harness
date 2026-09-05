"""Deduplicated, content-addressed provider request history."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

SCHEMA = "gt.provider_request_manifest.v1"


class BlobStore(Protocol):
    def put_blob(self, namespace: str, digest: str, payload: bytes) -> Path: ...

    def blob_exists(self, namespace: str, digest: str) -> bool: ...


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def store_provider_request(
    store: BlobStore, payload: Mapping[str, Any]
) -> tuple[str, str, str, dict[str, int]]:
    """Store an envelope manifest plus ordered message CAS references.

    Returns ``(request_sha256, manifest_path, manifest_sha256)``. Repeated
    history messages occupy one physical blob regardless of request count.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list) or any(
        not isinstance(message, Mapping) for message in messages
    ):
        raise ValueError("provider payload requires object messages")
    request_sha256 = hashlib.sha256(_canonical(dict(payload))).hexdigest()
    references = []
    referenced_bytes = 0
    unique_bytes = 0
    unique_objects = 0
    for message in messages:
        encoded = _canonical(dict(message))
        digest = hashlib.sha256(encoded).hexdigest()
        existed = store.blob_exists("provider_messages", digest)
        store.put_blob("provider_messages", digest, encoded)
        references.append({"sha256": digest, "bytes": len(encoded)})
        referenced_bytes += len(encoded)
        if not existed:
            unique_bytes += len(encoded)
            unique_objects += 1
    manifest = {
        "schema": SCHEMA,
        "request_sha256": request_sha256,
        "envelope": {
            key: value for key, value in payload.items() if key != "messages"
        },
        "messages": references,
    }
    encoded_manifest = _canonical(manifest)
    manifest_sha256 = hashlib.sha256(encoded_manifest).hexdigest()
    store.put_blob("provider_request_manifests", manifest_sha256, encoded_manifest)
    return (
        request_sha256,
        f"provider_request_manifests/{manifest_sha256}.json",
        manifest_sha256,
        {
            "message_reference_count": len(references),
            "message_referenced_bytes": referenced_bytes,
            "message_unique_objects_written": unique_objects,
            "message_unique_bytes_written": unique_bytes,
        },
    )


def load_provider_request(state_dir: str | Path, event: Mapping[str, Any]) -> dict:
    """Load either a v1 CAS manifest or a legacy monolithic request blob."""
    root = Path(state_dir).resolve()
    manifest_path = str(event.get("request_manifest") or "")
    if not manifest_path:
        legacy_path = str(event.get("request_blob") or "")
        if not legacy_path:
            raise ValueError("provider_request_reference_missing")
        path = (root / legacy_path).resolve()
        if path != root and root not in path.parents:
            raise ValueError("provider_request_path_outside_state")
        payload = path.read_bytes()
        expected = str(
            event.get("payload_sha256")
            or event.get("request_blob_sha256")
            or event.get("request_sha256")
            or ""
        )
        if expected and hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("provider_request_digest_mismatch")
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise ValueError("provider_request_object_required")
        return row

    path = (root / manifest_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError("provider_manifest_path_outside_state")
    encoded_manifest = path.read_bytes()
    manifest_sha256 = hashlib.sha256(encoded_manifest).hexdigest()
    if manifest_sha256 != str(event.get("request_manifest_sha256") or ""):
        raise ValueError("provider_manifest_digest_mismatch")
    manifest = json.loads(encoded_manifest)
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("provider_manifest_schema_invalid")
    envelope = manifest.get("envelope")
    references = manifest.get("messages")
    if not isinstance(envelope, dict) or not isinstance(references, list):
        raise ValueError("provider_manifest_shape_invalid")
    messages = []
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("provider_message_reference_invalid")
        digest = str(reference.get("sha256") or "")
        message_path = (root / "provider_messages" / f"{digest}.json").resolve()
        if root not in message_path.parents:
            raise ValueError("provider_message_path_outside_state")
        encoded = message_path.read_bytes()
        if (
            hashlib.sha256(encoded).hexdigest() != digest
            or len(encoded) != int(reference.get("bytes") or -1)
        ):
            raise ValueError("provider_message_identity_mismatch")
        message = json.loads(encoded)
        if not isinstance(message, dict):
            raise ValueError("provider_message_object_required")
        messages.append(message)
    request = {**envelope, "messages": messages}
    request_sha256 = hashlib.sha256(_canonical(request)).hexdigest()
    expected_request = str(manifest.get("request_sha256") or "")
    event_request = str(
        event.get("payload_sha256") or event.get("request_sha256") or ""
    )
    if request_sha256 != expected_request or (
        event_request and request_sha256 != event_request
    ):
        raise ValueError("provider_request_digest_mismatch")
    return request


__all__ = ["SCHEMA", "load_provider_request", "store_provider_request"]
