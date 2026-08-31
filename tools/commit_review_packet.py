#!/usr/bin/env python3
"""Commit a gt.review_packet.v1 to refs/heads/gt-review-inbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "gt.review_packet.v1"
INBOX_ROOT = Path(__file__).resolve().parents[1] / "inbox"


def digest_packet(body: dict) -> str:
    payload = {k: v for k, v in body.items() if k != "packet_digest_sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_index() -> dict:
    path = INBOX_ROOT / "INDEX.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": "gt.review_inbox.v1", "live_packets": [], "tickets": {}}


def write_index(index: dict) -> None:
    (INBOX_ROOT / "INDEX.json").write_text(
        json.dumps(index, sort_keys=True, separators=(",", ":"), indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--system", required=True, choices=["cursor-finder", "cursor-bugbot", "coordinator"])
    parser.add_argument("--check", required=True)
    parser.add_argument("--kind", required=True, choices=["finding", "check_outcome"])
    parser.add_argument("--severity", default="substance")
    parser.add_argument("--status", default="open")
    parser.add_argument("--file", default="")
    parser.add_argument("--line", type=int, default=0)
    parser.add_argument("--message", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--supersedes")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args()

    body = {
        "schema": SCHEMA,
        "packet_id": args.packet_id,
        "ticket": args.ticket,
        "pr": args.pr,
        "head_sha": args.head_sha,
        "source": {"system": args.system, "check": args.check},
        "kind": args.kind,
        "severity": args.severity,
        "status": args.status,
        "file": args.file,
        "line": args.line,
        "message": args.message,
        "detail": args.detail,
        "supersedes": args.supersedes,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    body["packet_digest_sha256"] = digest_packet(body)

    rel = f"{args.ticket}/{args.packet_id}.json"
    out = INBOX_ROOT / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, sort_keys=True, separators=(",", ":"), indent=2) + "\n", encoding="utf-8")

    index = load_index()
    tickets = index.setdefault("tickets", {})
    ticket_packets = tickets.setdefault(args.ticket, [])
    if args.packet_id not in ticket_packets:
        ticket_packets.append(args.packet_id)
        ticket_packets.sort()
    live = set(index.get("live_packets") or [])
    live.add(args.packet_id)
    index["live_packets"] = sorted(live)
    write_index(index)

    msg = args.commit_message or f"inbox: {args.ticket}/{args.packet_id} ({args.kind})"
    subprocess.run(["git", "add", "inbox/"], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push", "origin", "gt-review-inbox"], check=True)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
