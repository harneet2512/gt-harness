from __future__ import annotations

import hashlib

from gt_engine.runtime_attestation import capture_runtime_attestation


def test_runtime_attestation_binds_versions_source_route_and_binary(tmp_path):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"binary")

    attestation = capture_runtime_attestation(
        gt_source_revision="a" * 40,
        provider_route_identity="deepseek-v4-flash",
        index_binary_path=binary,
    )
    row = attestation.as_dict()

    assert row["schema"] == "gt.runtime_attestation.v1"
    assert row["gt_source_revision"] == "a" * 40
    assert row["provider_route_identity"] == "deepseek-v4-flash"
    assert row["index_binary_sha256"] == hashlib.sha256(b"binary").hexdigest()
    assert len(row["attestation_sha256"]) == 64
