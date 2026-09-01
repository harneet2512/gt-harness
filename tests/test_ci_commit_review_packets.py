from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci_commit_review_packets import commit_packets


def test_ci_commit_copies_bytes_and_updates_index(tmp_path: Path) -> None:
    source = tmp_path / "source"
    inbox = tmp_path / "inbox"
    source.mkdir()
    (inbox / "HAR-76").mkdir(parents=True)
    (inbox / "INDEX.json").write_text('{"schema":"gt.review_inbox.v1","live_packets":[],"tickets":{}}\n')
    packet = {
        "schema": "gt.review_packet.v1", "packet_id": "ci-ci-pytest-1", "ticket": "HAR-76",
        "pr": 34, "head_sha": "a" * 40, "source": {"system": "gt-ci", "check": "ci-pytest"},
        "kind": "check_outcome", "severity": "info", "status": "open", "file": "", "line": 0,
        "message": "ok", "detail": {"conclusion": "success"}, "supersedes": None,
        "created_at": "2026-01-01T00:00:00Z",
    }
    import hashlib
    canonical = dict(packet)
    packet["packet_digest_sha256"] = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    raw = json.dumps(packet, sort_keys=True, indent=2).encode() + b"\n"
    (source / "ci-pytest.json").write_bytes(raw)
    assert commit_packets(source, inbox) == ["ci-ci-pytest-1"]
    assert (inbox / "HAR-76" / "ci-ci-pytest-1.json").read_bytes() == raw
    index = json.loads((inbox / "INDEX.json").read_text())
    assert index["live_packets"] == ["ci-ci-pytest-1"]


def test_ci_commit_rejects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    inbox = tmp_path / "inbox"
    source.mkdir()
    (inbox / "HAR-76").mkdir(parents=True)
    (inbox / "INDEX.json").write_text('{"schema":"gt.review_inbox.v1","live_packets":[],"tickets":{}}\n')
    (source / "ci-bad.json").write_text('{"schema":"gt.review_packet.v1","packet_digest_sha256":"bad"}\n')
    with pytest.raises(ValueError, match="invalid_packet"):
        commit_packets(source, inbox)


def test_ci_commit_routes_har79_packets_to_their_ticket(tmp_path: Path) -> None:
    source = tmp_path / "source"
    inbox = tmp_path / "inbox"
    source.mkdir()
    inbox.mkdir()
    (inbox / "INDEX.json").write_text(
        '{"schema":"gt.review_inbox.v1","live_packets":[],"tickets":{}}\n'
    )
    packet = {
        "schema": "gt.review_packet.v1", "packet_id": "ci-ci-pytest-79", "ticket": "HAR-79",
        "pr": 38, "head_sha": "b" * 40,
        "source": {"system": "gt-ci", "check": "ci-pytest"},
        "kind": "check_outcome", "severity": "info", "status": "open", "file": "", "line": 0,
        "message": "ok", "detail": {"conclusion": "success"}, "supersedes": None,
        "created_at": "2026-01-01T00:00:00Z",
    }
    import hashlib
    packet["packet_digest_sha256"] = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    raw = json.dumps(packet, sort_keys=True, indent=2).encode() + b"\n"
    (source / "ci-pytest.json").write_bytes(raw)
    assert commit_packets(source, inbox) == ["ci-ci-pytest-79"]
    assert (inbox / "HAR-79" / "ci-ci-pytest-79.json").read_bytes() == raw
    index = json.loads((inbox / "INDEX.json").read_text())
    assert index["tickets"]["HAR-79"] == ["ci-ci-pytest-79"]
