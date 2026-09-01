from __future__ import annotations

import copy
import hashlib
from argparse import Namespace

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
