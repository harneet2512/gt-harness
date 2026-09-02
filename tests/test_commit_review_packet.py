from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools import commit_review_packet as writer


def _packet(root: Path, packet_id: str, *, supersedes: str | None = None) -> None:
    body = {
        "schema": writer.SCHEMA,
        "packet_id": packet_id,
        "ticket": "HAR-81",
        "pr": 0,
        "head_sha": "f" * 40,
        "source": {"system": "coordinator", "check": "test"},
        "kind": "check_outcome",
        "severity": "process",
        "status": "resolved",
        "file": "",
        "line": 0,
        "message": "test",
        "detail": {},
        "supersedes": supersedes,
        "created_at": "2026-09-02T00:00:00Z",
    }
    body["packet_digest_sha256"] = writer.digest_packet(body)
    path = root / "HAR-81" / f"{packet_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_supersession_accepts_live_child_with_retired_verified_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(writer, "INBOX_ROOT", tmp_path)
    _packet(tmp_path, "ancestor")
    _packet(tmp_path, "live-child", supersedes="ancestor")

    chain = writer.supersession_chain(
        {"live_packets": ["live-child"]},
        ticket="HAR-81",
        packet_id="live-child",
    )

    assert chain == ["live-child", "ancestor"]


def test_supersession_rejects_nonlive_direct_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(writer, "INBOX_ROOT", tmp_path)
    _packet(tmp_path, "retired")

    with pytest.raises(SystemExit, match="superseded packet is not live: retired"):
        writer.supersession_chain(
            {"live_packets": []}, ticket="HAR-81", packet_id="retired"
        )
