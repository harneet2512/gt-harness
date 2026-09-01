from __future__ import annotations

import copy
import hashlib
from argparse import Namespace
from pathlib import Path

from scripts.ci_emit_review_packet import canonical, emit


def test_ci_packet_is_digest_bound_and_records_run_identity() -> None:
    packet = emit(
        Namespace(
            ticket="HAR-76",
            pr=34,
            head_sha="a" * 40,
            check="ci-pytest",
            conclusion="success",
            run_id="123",
            run_url="https://example.invalid/run/123",
        )
    )
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
    diagnosis = packet["detail"]["diagnosis"]
    assert diagnosis["failures"] == ["tests/test_one.py::test_case"]
    assert diagnosis["first_error"] == "E       AssertionError: broken"
    assert diagnosis["traceback_excerpts"][0]["node_id"] == "tests/test_one.py::test_case"
    assert "AssertionError: broken" in diagnosis["traceback_excerpts"][0]["traceback_excerpt"]


def test_ci_packet_records_durations_and_pytest_short_summary(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text(
        "________________ test_case ________________\n"
        "E       AssertionError: broken\n"
        "FAILED tests/test_one.py::test_case - AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_one.py::test_case - AssertionError\n"
        "1 failed, 2 passed in 0.42s\n",
        encoding="utf-8",
    )
    packet = emit(
        Namespace(
            ticket="HAR-79",
            pr=39,
            head_sha="c" * 40,
            check="ci-pytest",
            conclusion="failure",
            run_id="789",
            run_url="https://example.invalid/run/789",
            diagnosis_file=log,
            setup_duration=12.5,
            test_duration=4.25,
        )
    )
    assert packet["detail"]["durations"] == {
        "setup_seconds": 12.5,
        "test_seconds": 4.25,
        "parallel": False,
        "workers": None,
    }
    assert "1 failed, 2 passed" in packet["detail"]["diagnosis"]["pytest_short_summary"]
