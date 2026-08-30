from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

import pytest

from scripts.linear_control_record import (
    RecordConflict,
    RecordWriteResult,
    append_typed_record,
)


@dataclass
class _Issue:
    description: str
    revision: int = 1


class _MemoryIssueStore:
    """Deterministic compare-and-swap store used at the record boundary."""

    def __init__(self, description: str) -> None:
        self._issue = _Issue(description)
        self._lock = Lock()
        self.updates = 0

    def read(self, issue_id: str) -> tuple[str, str]:
        assert issue_id == "HAR-34"
        with self._lock:
            return self._issue.description, str(self._issue.revision)

    def compare_and_swap(
        self, issue_id: str, expected_revision: str, description: str
    ) -> bool:
        assert issue_id == "HAR-34"
        with self._lock:
            if str(self._issue.revision) != expected_revision:
                return False
            self._issue.description = description
            self._issue.revision += 1
            self.updates += 1
            return True


HEADER = """<!-- GT-CONTROL-HEADER v1 -->
* `latest_reviewer_sequence: 41`
* `latest_worker_ack_sequence: 41`
* `work_authorized: false`
<!-- /GT-CONTROL-HEADER -->
"""

RECORD = """kind: RECEIPT | id: C76 | reply_to: R41 | status: PASS

Exact immutable body.
"""


def test_identical_retry_is_idempotent() -> None:
    store = _MemoryIssueStore(HEADER)

    first = append_typed_record(store, "HAR-34", RECORD)
    second = append_typed_record(store, "HAR-34", RECORD)

    assert first is RecordWriteResult.APPENDED
    assert second is RecordWriteResult.ALREADY_PRESENT
    assert store.updates == 1
    assert store.read("HAR-34")[0].count("id: C76") == 1


def test_divergent_body_with_same_id_fails_closed() -> None:
    store = _MemoryIssueStore(HEADER)
    append_typed_record(store, "HAR-34", RECORD)

    with pytest.raises(RecordConflict, match="C76"):
        append_typed_record(store, "HAR-34", RECORD.replace("immutable", "different"))

    assert store.updates == 1


def test_concurrent_identical_retries_append_once() -> None:
    store = _MemoryIssueStore(HEADER)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: append_typed_record(store, "HAR-34", RECORD), range(32))
        )

    assert results.count(RecordWriteResult.APPENDED) == 1
    assert results.count(RecordWriteResult.ALREADY_PRESENT) == 31
    assert store.updates == 1
    assert store.read("HAR-34")[0].count("id: C76") == 1


def test_deterministic_replay_accepts_historical_identical_duplicates() -> None:
    store = _MemoryIssueStore(HEADER + "\n" + RECORD + "\n" + RECORD)

    assert (
        append_typed_record(store, "HAR-34", RECORD)
        is RecordWriteResult.ALREADY_PRESENT
    )
    assert store.updates == 0


def test_missing_or_malformed_record_id_fails_before_write() -> None:
    store = _MemoryIssueStore(HEADER)

    with pytest.raises(ValueError, match="typed record header"):
        append_typed_record(store, "HAR-34", "free text")
    with pytest.raises(ValueError, match="record id"):
        append_typed_record(store, "HAR-34", "kind: RECEIPT | status: PASS")

    assert store.updates == 0


def test_retry_exhaustion_fails_closed_without_claiming_append() -> None:
    class _AlwaysRacingStore(_MemoryIssueStore):
        def compare_and_swap(
            self, issue_id: str, expected_revision: str, description: str
        ) -> bool:
            return False

    store = _AlwaysRacingStore(HEADER)

    with pytest.raises(RecordConflict, match="concurrent update"):
        append_typed_record(store, "HAR-34", RECORD, max_attempts=3)

    assert store.updates == 0
