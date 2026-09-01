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
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def canonical(payload: dict) -> bytes:
    body = dict(payload)
    body.pop("packet_digest_sha256", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_diagnosis(path: Path | None) -> dict[str, object] | None:
    """Extract stable RED witnesses from a captured pytest transcript.

    The packet keeps node IDs and the first actionable error for compatibility, and
    adds bounded per-test excerpts plus pytest's ``-ra`` summary.  The excerpts are
    deliberately text-only and capped so a failure packet remains useful without
    becoming an unbounded log transport.
    """
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    failures = sorted(
        set(re.findall(r"^FAILED\s+([^\s]+)", text, flags=re.MULTILINE))
    )
    first_error = next(
        (
            line.strip()
            for line in text.splitlines()
            if re.match(r"^E\s+", line) or "Traceback (most recent call last)" in line
        ),
        "",
    )
    summary_match = re.search(
        r"(?ms)^=+ short test summary info =+\s*\n(?P<body>.*?)(?=\n=+\s*$|\Z)",
        text,
    )
    short_summary = summary_match.group("body").strip() if summary_match else ""
    failure_sections = list(
        re.finditer(r"(?m)^_{5,}[^\n]*_{5,}\s*$", text)
    )
    excerpts: list[dict[str, str]] = []
    for node in failures:
        test_name = node.rsplit("::", 1)[-1]
        section = next(
            (item for item in failure_sections if test_name in item.group(0)),
            None,
        )
        if section is not None:
            start = section.start()
            following = [item.start() for item in failure_sections if item.start() > start]
            summary_start = text.find("\n= short test summary", start + 1)
            end_candidates = following + ([summary_start] if summary_start >= 0 else [])
            end = min(end_candidates) if end_candidates else len(text)
        else:
            marker = text.find(node)
            if marker < 0:
                continue
            start = max(0, text.rfind("\n", 0, marker) + 1)
            end = text.find("\n= short test summary", marker + len(node))
            if end < 0:
                end = len(text)
        excerpt = text[start:end].strip()
        if len(excerpt) > 2400:
            excerpt = excerpt[-2400:]
        excerpts.append({"node_id": node, "traceback_excerpt": excerpt})
    if not failures and not first_error and not short_summary:
        return None
    return {
        "failures": failures,
        "first_error": first_error,
        "traceback_excerpts": excerpts,
        "pytest_short_summary": short_summary[:4000],
    }


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
            "durations": {
                "setup_seconds": getattr(args, "setup_duration", None),
                "test_seconds": getattr(args, "test_duration", None),
                "parallel": bool(getattr(args, "parallel", False)),
                "workers": getattr(args, "workers", None),
            },
        },
        "supersedes": None,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    diagnosis = read_diagnosis(getattr(args, "diagnosis_file", None))
    if diagnosis is not None:
        packet["detail"]["diagnosis"] = diagnosis
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
    parser.add_argument("--diagnosis-file", type=Path)
    parser.add_argument("--setup-duration", type=float)
    parser.add_argument("--test-duration", type=float)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--workers")
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
