from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from gt_harness.canonical_io import canonical_json_bytes
from gt_harness.groundtruth_provenance import _checkout_status, verify_groundtruth_lineage


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


def _packet(
    packet_id: str,
    *,
    head: str,
    supersedes: str | None = None,
    status: str = "PASS",
    kind: str = "check_outcome",
) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "gt.review_packet.v1",
        "packet_id": packet_id,
        "ticket": "HAR-81",
        "kind": kind,
        "status": status,
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
    failed_content_id = "har81-content-failed"
    failed_content = _packet(
        failed_content_id, head=source_head, status="FAIL", kind="measurement"
    )
    content_id = "har81-content-pass"
    content = _packet(
        content_id,
        head=source_head,
        supersedes=failed_content_id,
        kind="measurement",
    )
    _write_json(review / "inbox" / "HAR-81" / f"{packet_id}.json", packet)
    _write_json(
        review / "inbox" / "HAR-81" / f"{failed_content_id}.json", failed_content
    )
    _write_json(review / "inbox" / "HAR-81" / f"{content_id}.json", content)
    _write_json(
        review / "inbox" / "INDEX.json",
        {
            "schema": "gt.review_inbox.v1",
            "live_packets": [packet_id, content_id],
            "tickets": {"HAR-81": [packet_id, failed_content_id, content_id]},
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
                        "kind": "check_outcome",
                        "status": "PASS",
                    }
                ],
                "product_review_packets": [
                    {
                        "purpose": "recorded_content_correctness",
                        "packet_id": content_id,
                        "head_sha": source_head,
                        "packet_digest_sha256": content["packet_digest_sha256"],
                        "kind": "measurement",
                        "status": "PASS",
                        "supersedes": failed_content_id,
                    }
                ],
            },
        }
    }
    _write_json(manifest, payload)
    clean = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert clean["status"] == "PASS", clean

    (source / "producer.go").write_text("package forged\n", encoding="utf-8")
    dirty = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert "source_checkout_dirty" in dirty["failures"]
    (source / "producer.go").write_text("package main\n", encoding="utf-8")

    successor_id = "har81-source-proof-v2"
    _write_json(
        review / "inbox" / "HAR-81" / f"{successor_id}.json",
        _packet(successor_id, head=source_head, supersedes=packet_id),
    )
    _write_json(
        review / "inbox" / "INDEX.json",
        {
            "schema": "gt.review_inbox.v1",
            "live_packets": [packet_id, successor_id, content_id],
            "tickets": {
                "HAR-81": [
                    packet_id,
                    successor_id,
                    failed_content_id,
                    content_id,
                ]
            },
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


def test_checkout_status_is_semantic_and_rejects_content_changes(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "core.autocrlf", "true")
    tracked = repository / "producer.json"
    tracked.write_bytes(b'{"status":"verified"}\n')
    _commit(repository, "source")

    tracked.write_bytes(b'{"status":"tampered"}\r\n')
    assert _checkout_status(repository) == "M producer.json"


def test_lineage_rejects_reviews_that_only_cover_ancestors(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    producer = source / "producer.go"
    producer.write_text("package main\n", encoding="utf-8")
    reviewed_head = _commit(source, "reviewed source")
    producer.write_text("package main\n\nconst fixed = true\n", encoding="utf-8")
    source_head = _commit(source, "unreviewed functional change")

    review = tmp_path / "review"
    review.mkdir()
    _git(review, "init")
    packet_id = "har81-ancestor-only-proof"
    packet = _packet(packet_id, head=reviewed_head)
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
    _write_json(
        manifest,
        {
            "groundtruth": {
                "source_commit": source_head,
                "source_tree": _git(source, "rev-parse", "HEAD^{tree}"),
                "lineage_exception": {
                    "accepted_default_commit": reviewed_head,
                    "certified_source_commit": reviewed_head,
                    "ancestry_path": [reviewed_head, source_head],
                    "post_certification_changed_paths": ["producer.go"],
                    "review_inbox_commit": review_head,
                    "review_packets": [
                        {
                            "packet_id": packet_id,
                            "head_sha": reviewed_head,
                            "packet_digest_sha256": packet["packet_digest_sha256"],
                            "kind": "check_outcome",
                            "status": "PASS",
                        }
                    ],
                },
            }
        },
    )
    result = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert result["status"] == "FAIL"
    assert result["exact_source_review_packet_match"] is False
    assert "exact_source_review_packet_missing" in result["failures"]
