from __future__ import annotations

import copy
import hashlib
from argparse import Namespace
from pathlib import Path

from scripts.ci_emit_review_packet import canonical, emit


def test_ci_packet_is_digest_bound_and_records_run_identity() -> None:
    packet = emit(Namespace(ticket="HAR-76", pr=34, head_sha="a" * 40, check="ci-pytest", conclusion="success", run_id="123", run_url="https://example.invalid/run/123"))
    assert packet["source"] == {"system": "gt-ci", "check": "ci-pytest"}
    assert packet["detail"]["run_id"] == "123"
    assert "Groundtruth parity" in packet["detail"]["groundtruth_parity"]
    assert hashlib.sha256(canonical(packet)).hexdigest() == packet["packet_digest_sha256"]
    tampered = copy.deepcopy(packet)
    tampered["detail"]["conclusion"] = "failure"
    assert hashlib.sha256(canonical(tampered)).hexdigest() != tampered["packet_digest_sha256"]


def test_ci_failure_packet_carries_node_and_first_error_diagnosis(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text(
        "FAILED tests/test_one.py::test_case - AssertionError\n"
        "E       AssertionError: broken\n",
        encoding="utf-8",
    )
    packet = emit(
        Namespace(
            ticket="HAR-76",
            pr=34,
            head_sha="b" * 40,
            check="ci-pytest",
            conclusion="failure",
            run_id="456",
            run_url="https://example.invalid/run/456",
            diagnosis_file=log,
        )
    )
    assert packet["detail"]["diagnosis"] == {
        "failures": ["tests/test_one.py::test_case"],
        "first_error": "E       AssertionError: broken",
    }
