"""Provider-free verification of Groundtruth source lineage and review records."""

from __future__ import annotations

import hashlib
import json
import subprocess
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


def _raw_status(repository: Path) -> str:
    """Compare checkout bytes to the index without clean/smudge filter drift."""

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
    source_status = _raw_status(source)
    review_status = _raw_status(review)
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
    for expected in lineage["review_packets"]:
        packet_id = str(expected["packet_id"])
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
            or packet.get("kind") != "check_outcome"
            or packet.get("status") != "open"
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

    result: dict[str, Any] = {
        "schema": "gt.groundtruth_lineage_measurement.v1",
        "status": "PASS" if not failures else "FAIL",
        "provider_calls": 0,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "accepted_default_commit": accepted,
        "accepted_default_is_ancestor": ancestor.returncode == 0,
        "ancestry_path_match": observed_path == expected_path,
        "post_certification_diff_match": observed_changes == expected_changes,
        "review_packet_match_count": verified_packets,
        "review_inbox_commit": review_commit,
        "source_checkout_status": source_status.splitlines(),
        "review_checkout_status": review_status.splitlines(),
        "failures": failures,
    }
    result["measurement_digest_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return result


__all__ = ["verify_groundtruth_lineage"]
