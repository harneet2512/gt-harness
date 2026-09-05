from __future__ import annotations

import copy
import hashlib
import subprocess
import zipfile
from pathlib import Path

from gt_harness.canonical_io import canonical_json_bytes
from gt_harness.groundtruth_provenance import (
    _checkout_status,
    _recorded_content_review_valid,
    _retired_supersession_chain_valid,
    verify_groundtruth_lineage,
)


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


def _content_packet(packet_id: str, *, head: str, supersedes: str) -> dict[str, object]:
    measurement: dict[str, object] = {
        "schema": "gt.recorded_content_measurement.v3",
        "status": "PASS",
        "provider_calls": 0,
        "run_count": 1,
        "delivery_count": 1,
        "attestation_match_count": 1,
        "claim_match_count": 1,
        "consequence_match_count": 1,
        "payload_match_count": 1,
        "provider_request_match_count": 1,
        "rederived_count": 1,
        "target_match_count": 1,
        "trigger_match_count": 1,
        "mismatch_count": 0,
        "failures": [],
        "adjudications": [],
        "historical_adjudication_count": 0,
        "mutation_cases": {
            "event_stream": "FAIL_AS_DESIGNED",
            "graph": "FAIL_AS_DESIGNED",
            "payload": "FAIL_AS_DESIGNED",
            "provider_request": "FAIL_AS_DESIGNED",
        },
    }
    measurement["packet_digest_sha256"] = hashlib.sha256(
        canonical_json_bytes(measurement)
    ).hexdigest()
    packet = _packet(
        packet_id,
        head=head,
        supersedes=supersedes,
        kind="measurement",
    )
    packet["detail"] = {
        "implementation_sha": head,
        "provider_calls": 0,
        "paid_dispatch": False,
        "source_runs": [1],
        "provider_free_ci": {"head_sha": head, "conclusion": "success"},
        "independent_review": {"verdict": "PASS"},
        "measurement": measurement,
        "measurement_file_sha256": hashlib.sha256(
            canonical_json_bytes(measurement) + b"\n"
        ).hexdigest(),
    }
    packet["packet_digest_sha256"] = hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in packet.items() if key != "packet_digest_sha256"}
        )
    ).hexdigest()
    return packet


def test_lineage_binds_clean_review_head_index_and_live_supersession(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    (source / "producer.go").write_text("package main\n", encoding="utf-8")
    package = source / "src" / "groundtruth"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"VALUE = 1\n")
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
    content = _content_packet(content_id, head=source_head, supersedes=failed_content_id)
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

    manifest = tmp_path / "config" / "manifest.json"
    wheel = tmp_path / "vendor" / "fixture.whl"
    wheel.parent.mkdir()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("groundtruth/__init__.py", b"VALUE = 1\n")
    payload = {
        "groundtruth": {
            "wheel_path": "vendor/fixture.whl",
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
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
                        "measurement_digest_sha256": content["detail"]["measurement"]["packet_digest_sha256"],
                        "measurement_file_sha256": content["detail"]["measurement_file_sha256"],
                        "kind": "measurement",
                        "status": "PASS",
                        "supersedes": failed_content_id,
                        "source_runs": [1],
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
    original_wheel = wheel.read_bytes()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("groundtruth/__init__.py", b"VALUE = 2\n")
    # Even a manifest correctly hashing the wrong package must be rejected.
    payload["groundtruth"]["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    _write_json(manifest, payload)
    mismatch = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert "wheel_source_correspondence_mismatch" in mismatch["failures"]
    wheel.write_bytes(b"not a wheel")
    malformed = verify_groundtruth_lineage(
        manifest, groundtruth_checkout=source, review_checkout=review
    )
    assert "wheel_source_correspondence_invalid" in malformed["failures"]
    assert malformed["status"] == "FAIL"
    wheel.write_bytes(original_wheel)
    payload["groundtruth"]["wheel_sha256"] = hashlib.sha256(original_wheel).hexdigest()
    _write_json(manifest, payload)
    assert not _retired_supersession_chain_valid(
        review,
        packet_id="nonexistent-prior-packet",
        ticket="HAR-81",
        tickets={"HAR-81": ["nonexistent-prior-packet"]},
        live_packets=[content_id, packet_id],
    )

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


def test_recorded_content_review_rejects_resealed_inner_failure() -> None:
    packet = _content_packet("content-pass", head="a" * 40, supersedes="content-fail")
    measurement = packet["detail"]["measurement"]
    expected = {
        "purpose": "recorded_content_correctness",
        "measurement_digest_sha256": measurement["packet_digest_sha256"],
        "measurement_file_sha256": packet["detail"]["measurement_file_sha256"],
        "source_runs": [1],
    }
    assert _recorded_content_review_valid(packet, expected)

    forged = copy.deepcopy(packet)
    forged_measurement = forged["detail"]["measurement"]
    forged_measurement["status"] = "FAIL"
    forged_measurement["failures"] = [{"reason": "tampered"}]
    unsigned = dict(forged_measurement)
    unsigned.pop("packet_digest_sha256")
    forged_measurement["packet_digest_sha256"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    forged["detail"]["measurement_file_sha256"] = hashlib.sha256(
        canonical_json_bytes(forged_measurement) + b"\n"
    ).hexdigest()
    forged_expected = dict(expected)
    forged_expected["measurement_digest_sha256"] = forged_measurement[
        "packet_digest_sha256"
    ]
    forged_expected["measurement_file_sha256"] = forged["detail"][
        "measurement_file_sha256"
    ]
    assert not _recorded_content_review_valid(forged, forged_expected)
