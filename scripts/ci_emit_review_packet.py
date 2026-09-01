"""Emit one digest-bound gt.review_packet.v1 CI outcome packet.

The packet is later copied byte-for-byte by the inbox job to
``refs/heads/gt-review-inbox``.  CI is the emitting system and is explicitly
separate from local checker observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def canonical(payload: dict) -> bytes:
    body = dict(payload)
    body.pop("packet_digest_sha256", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def emit(args: argparse.Namespace) -> dict:
    conclusion = args.conclusion.lower()
    if conclusion not in {"success", "failure", "cancelled", "skipped"}:
        raise ValueError("invalid_conclusion")
    variant = getattr(args, "variant", "")
    packet_id = f"ci-{args.check}-{args.run_id}" + (f"-{variant}" if variant else "")
    packet = {
        "schema": "gt.review_packet.v1",
        "packet_id": packet_id,
        "ticket": args.ticket,
        "pr": args.pr,
        "head_sha": args.head_sha,
        "source": {"system": "gt-ci", "check": args.check},
        "kind": "check_outcome",
        "severity": "error" if conclusion == "failure" else "info",
        "status": "open",
        "file": "",
        "line": 0,
        "message": f"{args.check} CI outcome: {conclusion}",
        "detail": {
            "conclusion": conclusion,
            "run_id": args.run_id,
            "run_url": args.run_url,
            "groundtruth_parity": (
                "Harness CI runs the provider-free control-plane suite and the "
                "18/18 feature matrix; Groundtruth parity remains covered by "
                "its existing provider-free Go/runtime workflows, with no "
                "provider or benchmark execution in this workflow."
            ),
        },
        "supersedes": None,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    packet["packet_digest_sha256"] = hashlib.sha256(canonical(packet)).hexdigest()
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--check", required=True, choices=["ci-pytest", "ci-feature-matrix"])
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument(
        "--run-url",
        default=(
            os.environ.get("GITHUB_SERVER_URL", "")
            + "/"
            + os.environ.get("GITHUB_REPOSITORY", "")
            + "/actions/runs/"
            + os.environ.get("GITHUB_RUN_ID", "local")
        ),
    )
    parser.add_argument("--variant", default="")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    packet = emit(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=args.output.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(
            json.dumps(packet, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"
        )
    os.replace(temporary, args.output)
    print(
        json.dumps(
            {
                "packet_id": packet["packet_id"],
                "packet_digest_sha256": packet["packet_digest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
