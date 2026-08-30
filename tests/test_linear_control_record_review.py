from __future__ import annotations

import subprocess

import pytest

from scripts.linear_control_record import RecordConflict, append_typed_record
from tests.test_linear_control_record import _MemoryIssueStore


def test_malformed_reserved_marker_fails_closed() -> None:
    record = "kind: RECEIPT | id: REVIEW | status: PASS\n\nbody\n"
    store = _MemoryIssueStore("")
    append_typed_record(store, "HAR-34", record)
    store._issue.description = store._issue.description.replace("sha256=", "sha256=0", 1)

    with pytest.raises(RecordConflict, match="marker"):
        append_typed_record(store, "HAR-34", record)


def test_record_writer_source_has_stable_lf_identity() -> None:
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", "scripts/linear_control_record.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip().endswith(": lf")
