"""Tamper-evident append-only event journal primitives.

The chain proves ordering and content integrity. A separately persisted
``event_count`` + ``event_head`` anchor is required to detect tail truncation;
the per-run reproducibility manifest owns that anchor.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
JOURNAL_SCHEMA = "gt.event.v1"


def canonical_event_bytes(row: Mapping[str, Any]) -> bytes:
    material = {key: value for key, value in row.items() if key != "event_hash"}
    return json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def event_hash(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_event_bytes(row)).hexdigest()


@dataclass(frozen=True)
class JournalVerification:
    valid: bool
    event_count: int
    event_head: str
    issues: tuple[str, ...]


def _read_rows(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    journal = Path(path)
    if not journal.exists():
        return rows, ["journal does not exist"]
    for line_number, line in enumerate(
        journal.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(row, dict):
            issues.append(f"line {line_number}: event is not an object")
            continue
        rows.append(row)
    return rows, issues


def verify_event_journal(
    path: str | Path,
    *,
    event_count: int | None = None,
    event_head: str | None = None,
) -> JournalVerification:
    rows, issues = _read_rows(path)
    expected_parent = GENESIS_HASH
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != JOURNAL_SCHEMA:
            issues.append(f"event {index}: unsupported or missing schema")
        if row.get("sequence") != index:
            issues.append(
                f"event {index}: sequence mismatch ({row.get('sequence')!r})"
            )
        if row.get("parent_hash") != expected_parent:
            issues.append(f"event {index}: parent hash mismatch")
        actual = event_hash(row)
        if row.get("event_hash") != actual:
            issues.append(f"event {index}: hash mismatch")
        expected_parent = str(row.get("event_hash") or actual)
    actual_head = expected_parent if rows else GENESIS_HASH
    if event_count is not None and len(rows) != int(event_count):
        issues.append(
            f"anchored event count mismatch: expected {event_count}, got {len(rows)}"
        )
    if event_head is not None and actual_head != event_head:
        issues.append(
            f"anchored head mismatch: expected {event_head}, got {actual_head}"
        )
    return JournalVerification(
        valid=not issues,
        event_count=len(rows),
        event_head=actual_head,
        issues=tuple(issues),
    )


def read_verified_events(
    path: str | Path,
    *,
    event_count: int | None = None,
    event_head: str | None = None,
) -> tuple[dict[str, Any], ...]:
    verification = verify_event_journal(
        path, event_count=event_count, event_head=event_head
    )
    if not verification.valid:
        raise ValueError(
            "invalid event journal: " + "; ".join(verification.issues)
        )
    rows, _issues = _read_rows(path)
    return tuple(rows)
