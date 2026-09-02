#!/usr/bin/env python3
"""Commit a gt.review_packet.v1 to refs/heads/gt-review-inbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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


def supersession_chain(index: dict, *, ticket: str, packet_id: str | None) -> list[str]:
    """Return a verified same-ticket live ancestry, newest first."""
    chain: list[str] = []
    seen: set[str] = set()
    live = set(index.get("live_packets") or [])
    current = packet_id
    while current:
        if current in seen:
            raise SystemExit(f"supersession cycle: {current}")
        seen.add(current)
        matches = list(INBOX_ROOT.glob(f"*/{current}.json"))
        if len(matches) != 1:
            raise SystemExit(f"superseded packet must resolve exactly once: {current}")
        packet = json.loads(matches[0].read_text(encoding="utf-8"))
        if (
            packet.get("packet_id") != current
            or packet.get("ticket") != ticket
            or packet.get("packet_digest_sha256") != digest_packet(packet)
        ):
            raise SystemExit(f"invalid superseded packet: {current}")
        if current not in live:
            raise SystemExit(f"superseded packet is not live: {current}")
        chain.append(current)
        parent = packet.get("supersedes")
        if parent is not None and not isinstance(parent, str):
            raise SystemExit(f"invalid supersession link: {current}")
        current = parent
    return chain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--system", required=True, choices=["codex-check", "gt-ci", "coordinator"])
    parser.add_argument("--check", required=True)
    parser.add_argument("--kind", required=True, choices=["finding", "check_outcome"])
    parser.add_argument("--severity", default="substance")
    parser.add_argument("--status", default="open")
    parser.add_argument("--file", default="")
    parser.add_argument("--line", type=int, default=0)
    parser.add_argument("--message", required=True)
    parser.add_argument(
        "--detail", required=True, help="JSON object containing structured evidence"
    )
    parser.add_argument("--supersedes")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.packet_id):
        raise SystemExit("invalid packet id")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*-[0-9]+", args.ticket):
        raise SystemExit("invalid ticket id")

    try:
        detail = json.loads(args.detail)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"detail must be valid JSON: {exc}") from exc
    if not isinstance(detail, dict):
        raise SystemExit("detail must be a JSON object")

    index = load_index()
    known_ids = {
        packet_id
        for packet_ids in (index.get("tickets") or {}).values()
        for packet_id in packet_ids
    }
    if args.packet_id in known_ids:
        raise SystemExit(f"packet id already exists: {args.packet_id}")
    retired = supersession_chain(
        index, ticket=args.ticket, packet_id=args.supersedes
    )

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
        "detail": detail,
        "supersedes": args.supersedes,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    body["packet_digest_sha256"] = digest_packet(body)

    rel = f"{args.ticket}/{args.packet_id}.json"
    out = INBOX_ROOT / rel
    if out.exists():
        raise SystemExit(f"packet path already exists: {rel}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":"), indent=2) + "\n",
        encoding="utf-8",
    )

    stored = json.loads(out.read_text(encoding="utf-8"))
    if stored.get("packet_digest_sha256") != digest_packet(stored):
        raise SystemExit("packet digest verification failed after write")

    tickets = index.setdefault("tickets", {})
    ticket_packets = tickets.setdefault(args.ticket, [])
    if args.packet_id not in ticket_packets:
        ticket_packets.append(args.packet_id)
        ticket_packets.sort()
    live = set(index.get("live_packets") or [])
    live.difference_update(retired)
    live.add(args.packet_id)
    index["live_packets"] = sorted(live)
    write_index(index)
    stored_index = load_index()
    stored_live = set(stored_index.get("live_packets") or [])
    if args.packet_id not in stored_live or any(old in stored_live for old in retired):
        raise SystemExit("live packet supersession verification failed after write")

    msg = args.commit_message or f"inbox: {args.ticket}/{args.packet_id} ({args.kind})"
    subprocess.run(["git", "add", "inbox/"], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push", "origin", "gt-review-inbox"], check=True)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
