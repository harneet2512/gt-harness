from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from gt_harness.canonical_io import canonical_json_bytes
from gt_harness.groundtruth_provenance import verify_groundtruth_lineage


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", ".")
    _git(repository, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _packet(packet_id: str, *, head: str, supersedes: str | None = None) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "gt.review_packet.v1",
        "packet_id": packet_id,
        "ticket": "HAR-81",
        "kind": "check_outcome",
        "status": "open",
        "head_sha": head,
        "supersedes": supersedes,
    }
    packet["packet_digest_sha256"] = hashlib.sha256(canonical_json_bytes(packet)).hexdigest()
    return packet


def test_lineage_binds_clean_review_head_index_and_live_supersession(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    (source / "producer.go").write_text("package main\n", encoding="utf-8")
    source_head = _commit(source, "source")
    source_tree = _git(source, "rev-parse", "HEAD^{tree}")

    review = tmp_path / "review"
    review.mkdir()
    _git(review, "init")
    packet_id = "har81-source-proof"
    packet = _packet(packet_id, head=source_head)
    _write_json(review / "inbox" / "HAR-81" / f"{packet_id}.json", packet)
    _write_json(
        review / "inbox" / "INDEX.json",
        {
            "schema": "gt.review_inbox.v1",
            "live_packets": [packet_id],
            "tickets": {"HAR-81": [packet_id]},
        },
    )
    review_head = _commit(review, "review")

    manifest = tmp_path / "manifest.json"
    payload = {
        "groundtruth": {
            "source_commit": source_head,
            "source_tree": source_tree,
            "lineage_exception": {
                "accepted_default_commit": source_head,
                "certified_source_commit": source_head,
                "ancestry_path": [source_head],
                "post_certification_changed_paths": [],
                "review_inbox_commit": review_head,
                "review_packets": [
                    {
                        "packet_id": packet_id,
                        "head_sha": source_head,
                        "packet_digest_sha256": packet["packet_digest_sha256"],
                    }
                ],
            },
        }
    }
    _write_json(manifest, payload)
    assert verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )["status"] == "PASS"

    successor_id = "har81-source-proof-v2"
    _write_json(
        review / "inbox" / "HAR-81" / f"{successor_id}.json",
        _packet(successor_id, head=source_head, supersedes=packet_id),
    )
    _write_json(
        review / "inbox" / "INDEX.json",
        {
            "schema": "gt.review_inbox.v1",
            "live_packets": [packet_id, successor_id],
            "tickets": {"HAR-81": [packet_id, successor_id]},
        },
    )
    payload["groundtruth"]["lineage_exception"]["review_inbox_commit"] = _commit(
        review, "invalid live supersession"
    )
    _write_json(manifest, payload)
    result = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert result["status"] == "FAIL"
    assert f"review_packet_superseded_live:{packet_id}" in result["failures"]
