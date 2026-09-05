"""Provider-free verification of Groundtruth source lineage and review records."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gt_harness.canonical_io import canonical_json_bytes


def _git(repository: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0:
        raise ValueError(f"git_command_failed:{arguments[0]}")
    return process.stdout.strip()


def _packet_digest(packet: Mapping[str, Any]) -> str:
    body = dict(packet)
    body.pop("packet_digest_sha256", None)
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _retired_supersession_chain_valid(
    review: Path,
    *,
    packet_id: str,
    ticket: str,
    tickets: Mapping[str, Any],
    live_packets: list[str],
) -> bool:
    current: str | None = packet_id
    seen: set[str] = set()
    first = True
    while current is not None:
        if current in seen or current in live_packets:
            return False
        seen.add(current)
        candidates = list((review / "inbox").rglob(f"{current}.json"))
        if len(candidates) != 1:
            return False
        try:
            packet = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        memberships = sum(
            current in list(packet_ids or []) for packet_ids in tickets.values()
        )
        detail = packet.get("detail")
        measurement = detail.get("measurement") if isinstance(detail, Mapping) else None
        if (
            packet.get("schema") != "gt.review_packet.v1"
            or packet.get("packet_id") != current
            or packet.get("ticket") != ticket
            or packet.get("packet_digest_sha256") != _packet_digest(packet)
            or memberships != 1
            or current not in list(tickets.get(ticket) or [])
        ):
            return False
        if first and not (
            packet.get("status") == "FAIL"
            or (
                packet.get("kind") == "measurement"
                and isinstance(measurement, Mapping)
                and measurement.get("status") == "FAIL"
            )
        ):
            return False
        parent = packet.get("supersedes")
        if parent is not None and not isinstance(parent, str):
            return False
        current = parent
        first = False
    return True


def _recorded_content_review_valid(
    packet: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    detail = packet.get("detail")
    if not isinstance(detail, Mapping):
        return False
    measurement = detail.get("measurement")
    if not isinstance(measurement, Mapping):
        return False
    unsigned = dict(measurement)
    measurement_digest = unsigned.pop("packet_digest_sha256", None)
    count = measurement.get("delivery_count")
    conserved_fields = (
        "attestation_match_count",
        "claim_match_count",
        "consequence_match_count",
        "payload_match_count",
        "provider_request_match_count",
        "rederived_count",
        "target_match_count",
        "trigger_match_count",
    )
    mutation_cases = measurement.get("mutation_cases")
    adjudications = measurement.get("adjudications")
    provider_free_ci = detail.get("provider_free_ci")
    independent_review = detail.get("independent_review")
    file_digest = hashlib.sha256(canonical_json_bytes(measurement) + b"\n").hexdigest()
    return bool(
        expected.get("purpose") == "recorded_content_correctness"
        and packet.get("kind") == "measurement"
        and packet.get("status") == "PASS"
        and detail.get("implementation_sha") == packet.get("head_sha")
        and detail.get("provider_calls") == 0
        and detail.get("paid_dispatch") is False
        and detail.get("source_runs") == expected.get("source_runs")
        and isinstance(provider_free_ci, Mapping)
        and provider_free_ci.get("head_sha") == packet.get("head_sha")
        and provider_free_ci.get("conclusion") == "success"
        and isinstance(independent_review, Mapping)
        and independent_review.get("verdict") == "PASS"
        and measurement.get("schema") == "gt.recorded_content_measurement.v3"
        and measurement.get("status") == "PASS"
        and measurement.get("provider_calls") == 0
        and measurement.get("run_count") == len(expected.get("source_runs") or [])
        and isinstance(count, int)
        and count > 0
        and all(measurement.get(field) == count for field in conserved_fields)
        and measurement.get("mismatch_count") == 0
        and measurement.get("failures") == []
        and isinstance(adjudications, list)
        and measurement.get("historical_adjudication_count") == len(adjudications)
        and isinstance(mutation_cases, Mapping)
        and set(mutation_cases)
        == {"event_stream", "graph", "payload", "provider_request"}
        and set(mutation_cases.values()) == {"FAIL_AS_DESIGNED"}
        and measurement_digest == expected.get("measurement_digest_sha256")
        and hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        == measurement_digest
        and detail.get("measurement_file_sha256")
        == expected.get("measurement_file_sha256")
        and file_digest == expected.get("measurement_file_sha256")
    )


def _checkout_status(repository: Path) -> str:
    """Return Git-semantic dirt while tolerating checkout EOL materialization.

    This deliberately does not claim literal byte identity for every source file:
    text EOLs and LFS materialization are checkout concerns. Source identity is
    bound by the exact commit/tree and the clean-filter comparison here; the
    executable and its build-info use separate literal SHA-256 verification.
    """

    return _git(
        repository,
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.clean=cat",
        "-c",
        "filter.lfs.smudge=cat",
        "-c",
        "filter.lfs.required=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )


def verify_groundtruth_lineage(
    manifest_path: str | Path,
    *,
    groundtruth_checkout: str | Path,
    review_checkout: str | Path,
) -> dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    groundtruth = manifest.get("groundtruth")
    if not isinstance(groundtruth, Mapping):
        raise ValueError("groundtruth_manifest_missing")
    lineage = groundtruth.get("lineage_exception")
    if not isinstance(lineage, Mapping):
        raise ValueError("groundtruth_lineage_exception_missing")

    source = Path(groundtruth_checkout)
    review = Path(review_checkout)
    source_commit = str(groundtruth["source_commit"])
    source_tree = str(groundtruth["source_tree"])
    accepted = str(lineage["accepted_default_commit"])
    certified = str(lineage["certified_source_commit"])
    expected_path = list(lineage["ancestry_path"])
    observed_path = [accepted, *_git(source, "rev-list", "--reverse", "--ancestry-path", f"{accepted}..{source_commit}").splitlines()]
    observed_changes = sorted(
        value
        for value in _git(source, "diff", "--name-only", certified, source_commit).splitlines()
        if value
    )
    expected_changes = sorted(lineage["post_certification_changed_paths"])
    failures: list[str] = []
    from scripts.verify_wheel_source import verify_wheel_source

    correspondence: dict[str, Any] = {"status": "FAIL"}
    try:
        # Product manifests live in config/; paths are rooted in the bundle,
        # never in the current shell directory or the reviewed source checkout.
        bundle_root = Path(manifest_path).resolve().parent.parent
        relative_wheel = Path(str(groundtruth.get("wheel_path") or ""))
        wheel = (bundle_root / relative_wheel).resolve()
        if (relative_wheel.is_absolute() or ".." in relative_wheel.parts
                or not wheel.is_relative_to(bundle_root) or not wheel.is_file()):
            raise ValueError("invalid_bundle_wheel_path")
        correspondence = verify_wheel_source(wheel, source / "src")
        if correspondence["wheel_sha256"] != groundtruth.get("wheel_sha256"):
            failures.append("wheel_sha256_mismatch")
        if correspondence["status"] != "PASS":
            failures.append("wheel_source_correspondence_mismatch")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        correspondence = {"status": "FAIL", "error_type": type(exc).__name__}
        failures.append("wheel_source_correspondence_invalid")
    source_status = _checkout_status(source)
    review_status = _checkout_status(review)
    if _git(source, "rev-parse", "HEAD") != source_commit:
        failures.append("source_commit_mismatch")
    if _git(source, "rev-parse", "HEAD^{tree}") != source_tree:
        failures.append("source_tree_mismatch")
    if source_status:
        failures.append("source_checkout_dirty")
    ancestor = subprocess.run(
        ["git", "-C", str(source), "merge-base", "--is-ancestor", accepted, source_commit],
        capture_output=True,
    )
    if ancestor.returncode != 0:
        failures.append("accepted_default_not_source_ancestor")
    if observed_path != expected_path:
        failures.append("ancestry_path_mismatch")
    if observed_changes != expected_changes:
        failures.append("post_certification_diff_mismatch")

    review_commit = str(lineage["review_inbox_commit"])
    if _git(review, "rev-parse", "HEAD") != review_commit:
        failures.append("review_checkout_commit_mismatch")
    if review_status:
        failures.append("review_checkout_dirty")
    try:
        index = json.loads((review / "inbox" / "INDEX.json").read_text(encoding="utf-8"))
        if index.get("schema") != "gt.review_inbox.v1":
            failures.append("review_index_schema_mismatch")
        live_packets = list(index.get("live_packets") or [])
        tickets = index.get("tickets") or {}
        if not isinstance(tickets, Mapping):
            raise ValueError("review_index_tickets_invalid")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        failures.append("review_index_invalid")
        live_packets = []
        tickets = {}

    verified_packets = 0
    exact_source_review = False
    for expected in lineage["review_packets"]:
        packet_id = str(expected["packet_id"])
        expected_kind = str(expected.get("kind") or "check_outcome")
        expected_status = str(expected.get("status") or "open")
        candidates = list((review / "inbox").rglob(f"{packet_id}.json"))
        if len(candidates) != 1:
            failures.append(f"review_packet_count:{packet_id}")
            continue
        packet = json.loads(candidates[0].read_text(encoding="utf-8"))
        ticket = str(packet.get("ticket") or "")
        ticket_memberships = sum(
            packet_id in list(packet_ids or []) for packet_ids in tickets.values()
        )
        if (
            packet.get("packet_id") != packet_id
            or packet.get("head_sha") != expected["head_sha"]
            or packet.get("packet_digest_sha256") != expected["packet_digest_sha256"]
            or _packet_digest(packet) != expected["packet_digest_sha256"]
            or packet.get("schema") != "gt.review_packet.v1"
            or packet.get("kind") != expected_kind
            or packet.get("status") != expected_status
            or live_packets.count(packet_id) != 1
            or ticket_memberships != 1
            or packet_id not in list(tickets.get(ticket) or [])
        ):
            failures.append(f"review_packet_mismatch:{packet_id}")
            continue
        superseding_live = []
        for live_path in (review / "inbox").rglob("*.json"):
            if live_path.name == "INDEX.json":
                continue
            try:
                live_packet = json.loads(live_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                live_packet.get("packet_id") in live_packets
                and live_packet.get("supersedes") == packet_id
            ):
                superseding_live.append(str(live_packet["packet_id"]))
        if superseding_live:
            failures.append(f"review_packet_superseded_live:{packet_id}")
            continue
        verified_packets += 1
        exact_source_review = exact_source_review or (
            packet.get("head_sha") == source_commit
            and packet.get("kind") == "check_outcome"
            and packet.get("status") == "PASS"
        )

    if not exact_source_review:
        failures.append("exact_source_review_packet_missing")

    product_review_packets = lineage.get("product_review_packets")
    verified_product_reviews = 0
    verified_product_purposes: list[str] = []
    if not isinstance(product_review_packets, list) or not product_review_packets:
        failures.append("product_review_packets_missing")
        product_review_packets = []
    for expected in product_review_packets:
        if not isinstance(expected, Mapping):
            failures.append("product_review_packet_invalid")
            continue
        packet_id = str(expected.get("packet_id") or "")
        candidates = list((review / "inbox").rglob(f"{packet_id}.json"))
        if len(candidates) != 1:
            failures.append(f"product_review_packet_count:{packet_id}")
            continue
        packet = json.loads(candidates[0].read_text(encoding="utf-8"))
        ticket = str(packet.get("ticket") or "")
        ticket_memberships = sum(
            packet_id in list(packet_ids or []) for packet_ids in tickets.values()
        )
        supersedes = expected.get("supersedes")
        if (
            packet.get("packet_id") != packet_id
            or packet.get("head_sha") != expected.get("head_sha")
            or packet.get("packet_digest_sha256")
            != expected.get("packet_digest_sha256")
            or _packet_digest(packet) != expected.get("packet_digest_sha256")
            or packet.get("schema") != "gt.review_packet.v1"
            or packet.get("kind") != expected.get("kind")
            or packet.get("status") != expected.get("status")
            or packet.get("supersedes") != supersedes
            or live_packets.count(packet_id) != 1
            or ticket_memberships != 1
            or packet_id not in list(tickets.get(ticket) or [])
            or (isinstance(supersedes, str) and supersedes in live_packets)
            or not isinstance(supersedes, str)
            or not _retired_supersession_chain_valid(
                review,
                packet_id=supersedes,
                ticket=ticket,
                tickets=tickets,
                live_packets=live_packets,
            )
            or not _recorded_content_review_valid(packet, expected)
        ):
            failures.append(f"product_review_packet_mismatch:{packet_id}")
            continue
        verified_product_reviews += 1
        verified_product_purposes.append(str(expected.get("purpose") or ""))

    if verified_product_purposes.count("recorded_content_correctness") != 1:
        failures.append("recorded_content_review_packet_missing")

    result: dict[str, Any] = {
        "schema": "gt.groundtruth_lineage_measurement.v1",
        "status": "PASS" if not failures else "FAIL",
        "provider_calls": 0,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "wheel_source_correspondence": correspondence,
        "accepted_default_commit": accepted,
        "accepted_default_is_ancestor": ancestor.returncode == 0,
        "ancestry_path_match": observed_path == expected_path,
        "post_certification_diff_match": observed_changes == expected_changes,
        "review_packet_match_count": verified_packets,
        "exact_source_review_packet_match": exact_source_review,
        "product_review_packet_match_count": verified_product_reviews,
        "product_review_purposes": sorted(verified_product_purposes),
        "review_inbox_commit": review_commit,
        "source_checkout_status": source_status.splitlines(),
        "review_checkout_status": review_status.splitlines(),
        "failures": failures,
    }
    result["measurement_digest_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return result


__all__ = ["verify_groundtruth_lineage"]
