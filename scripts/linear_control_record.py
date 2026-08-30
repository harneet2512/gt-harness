"""Conflict-safe, idempotent appends for typed Linear description records."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Protocol


class RecordConflict(RuntimeError):
    """The durable stream cannot accept or deterministically replay a record."""


class RecordWriteResult(Enum):
    APPENDED = "appended"
    ALREADY_PRESENT = "already_present"


class IssueStore(Protocol):
    def read(self, issue_id: str) -> tuple[str, str]: ...

    def compare_and_swap(
        self, issue_id: str, expected_revision: str, description: str
    ) -> bool: ...


_RECORD_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_TYPED_HEADER = re.compile(r"(?m)^kind:[^\n]*$")
_MARKER = re.compile(
    r"(?m)^<!-- GT-TYPED-RECORD v1 "
    r"id=(?P<id>[A-Za-z][A-Za-z0-9_.:-]{0,127}) "
    r"sha256=(?P<sha>[0-9a-f]{64}) bytes=(?P<bytes>[1-9][0-9]*) -->\n"
)


def _canonical_record(record: str) -> tuple[str, str]:
    canonical = record.replace("\r\n", "\n").replace("\r", "\n").strip("\n") + "\n"
    first_line = canonical.partition("\n")[0]
    fields = [field.strip() for field in first_line.split("|")]
    if not fields or not fields[0].startswith("kind:") or not fields[0][5:].strip():
        raise ValueError("typed record header is missing a kind")
    ids = [field[3:].strip() for field in fields[1:] if field.startswith("id:")]
    if len(ids) != 1 or not _RECORD_ID.fullmatch(ids[0]):
        raise ValueError("record id is missing or malformed")
    return canonical, ids[0]


def _header_id(header: str) -> str | None:
    try:
        _, record_id = _canonical_record(header)
    except ValueError:
        return None
    return record_id


def _utf8_prefix(text: str, byte_count: int) -> str:
    encoded = text.encode("utf-8")
    if byte_count > len(encoded):
        raise RecordConflict("typed record marker exceeds the durable stream")
    try:
        return encoded[:byte_count].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecordConflict("typed record marker splits UTF-8 content") from exc


def _marker_state(description: str) -> dict[str, str]:
    state: dict[str, str] = {}
    for marker in _MARKER.finditer(description):
        record_id = marker.group("id")
        claimed_sha = marker.group("sha")
        body = _utf8_prefix(description[marker.end() :], int(marker.group("bytes")))
        actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        try:
            _, body_id = _canonical_record(body)
        except ValueError as exc:
            raise RecordConflict(f"corrupt typed record marker for {record_id}") from exc
        if body_id != record_id or actual_sha != claimed_sha:
            raise RecordConflict(f"corrupt typed record marker for {record_id}")
        previous = state.setdefault(record_id, claimed_sha)
        if previous != claimed_sha:
            raise RecordConflict(f"divergent bodies reuse record id {record_id}")
    return state


def _legacy_state(description: str, canonical: str, record_id: str) -> bool:
    matches = [
        match
        for match in _TYPED_HEADER.finditer(description)
        if _header_id(match.group(0)) == record_id
    ]
    if not matches:
        return False
    if any(not description.startswith(canonical, match.start()) for match in matches):
        raise RecordConflict(f"divergent body reuses record id {record_id}")
    return True


def append_typed_record(
    store: IssueStore,
    issue_id: str,
    record: str,
    *,
    max_attempts: int = 8,
) -> RecordWriteResult:
    """Append once with optimistic CAS; identical retries are read-only."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    canonical, record_id = _canonical_record(record)
    encoded = canonical.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    marker = (
        f"<!-- GT-TYPED-RECORD v1 id={record_id} "
        f"sha256={digest} bytes={len(encoded)} -->\n"
    )

    for _ in range(max_attempts):
        description, revision = store.read(issue_id)
        normalized = description.replace("\r\n", "\n").replace("\r", "\n")
        marker_state = _marker_state(normalized)
        if record_id in marker_state:
            if marker_state[record_id] != digest:
                raise RecordConflict(f"divergent body reuses record id {record_id}")
            return RecordWriteResult.ALREADY_PRESENT
        if _legacy_state(normalized, canonical, record_id):
            return RecordWriteResult.ALREADY_PRESENT

        separator = "" if not description else ("\n" if description.endswith("\n") else "\n\n")
        updated = description + separator + marker + canonical
        if store.compare_and_swap(issue_id, revision, updated):
            return RecordWriteResult.APPENDED

    raise RecordConflict(
        f"concurrent update prevented record {record_id} after {max_attempts} attempts"
    )
