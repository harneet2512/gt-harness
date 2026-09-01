"""Commit CI outcome packets atomically to the review-inbox branch.

This is intentionally a small CI-only bridge: it accepts only the two packet
files emitted by this workflow, validates their self-digests, copies their
bytes unchanged, and updates INDEX.json in the same working-tree commit.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _digest(packet: dict) -> str:
    body = dict(packet)
    body.pop("packet_digest_sha256", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def commit_packets(source_root: Path, inbox_root: Path) -> list[str]:
    packets = sorted(source_root.rglob("ci-*.json"))
    if not packets:
        raise ValueError("no_ci_packets")
    accepted: list[tuple[str, bytes, dict]] = []
    for path in packets:
        raw = path.read_bytes()
        packet = json.loads(raw)
        if packet.get("schema") != "gt.review_packet.v1" or packet.get("kind") != "check_outcome":
            raise ValueError(f"invalid_packet:{path.name}")
        if packet.get("ticket") != "HAR-76" or packet.get("source", {}).get("system") != "gt-ci":
            raise ValueError(f"invalid_packet_provenance:{path.name}")
        if packet.get("packet_digest_sha256") != _digest(packet):
            raise ValueError(f"packet_digest_mismatch:{path.name}")
        packet_id = packet.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.startswith("ci-"):
            raise ValueError(f"invalid_packet_id:{path.name}")
        accepted.append((packet_id, raw, packet))

    index_path = inbox_root / "INDEX.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    live = set(index.get("live_packets") or [])
    tickets = index.setdefault("tickets", {})
    ticket_packets = set(tickets.setdefault("HAR-76", []))
    for packet_id, raw, _packet in accepted:
        out = inbox_root / "HAR-76" / f"{packet_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        live.add(packet_id)
        ticket_packets.add(packet_id)
    index["live_packets"] = sorted(live)
    tickets["HAR-76"] = sorted(ticket_packets)
    index_path.write_text(
        json.dumps(index, sort_keys=True, separators=(",", ":"), indent=2) + "\n",
        encoding="utf-8",
    )
    return [packet_id for packet_id, _, _ in accepted]


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: ci_commit_review_packets.py <artifact-dir> <inbox-dir>")
    ids = commit_packets(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps({"packet_ids": ids}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
