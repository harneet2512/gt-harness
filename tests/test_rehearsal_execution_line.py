"""The rehearsal auditor rebuilds the delivered execution-evidence block from
the stored journal payload. The live delivery now appends the baseline
classification clause and the submit-window advisory to that line; an auditor
that rebuilds only the five original fields rejects every run that classified
a failure (``admitted`` False -> ``execution_evidence_verified`` False)."""
from __future__ import annotations

from gt_engine.runtime_observation import execution_evidence_model_line
from scripts.gt_installed_rehearsal import expected_execution_evidence_block


def _payload(**extra) -> dict:
    return {
        "kind": "test", "outcome": "fail", "returncode": 1,
        "observed_test_outcome": "fail", **extra,
    }


def test_plain_payload_rebuilds_the_original_line():
    block = expected_execution_evidence_block(_payload(), "python -m unittest")
    assert block == "[GT_EXECUTION_EVIDENCE]\n" + execution_evidence_model_line(
        command="python -m unittest", kind="test", outcome="fail",
        returncode=1, observed_test_outcome="fail",
    )


def test_classified_payload_rebuilds_the_delivered_clause():
    classification = {
        "layout_schema": "gt.baseline_classification.v1",
        "scope": "scoped",
        "verdicts": [["tests/test_x.py::test_a", "unverified_scope"]],
        "summary": {"unverified_scope": 1},
    }
    block = expected_execution_evidence_block(
        _payload(baseline_classification=classification), "pytest tests/test_x.py"
    )
    assert ("; baseline: tests/test_x.py::test_a passed at baseline; it failed here "
            "in a scoped run - run the full suite before investigating") in block


def test_advisory_payload_rebuilds_the_delivered_clause():
    block = expected_execution_evidence_block(
        _payload(outcome="pass", returncode=0, observed_test_outcome="pass",
                 submit_window={"layout_schema": "gt.submit_window.v1",
                                "advisory": "suite green vs baseline"}),
        "pytest -q",
    )
    assert block.endswith("; suite green vs baseline")


def test_row_only_keys_reach_the_block_when_the_blob_cannot_hold_them():
    """The real path: the hashed blob is ``canonical_bytes`` - a fixed 15-key
    artifact - and the producer adds the two clause fields to the journal row
    only after hashing it. Rebuilding from the blob alone drops both clauses."""
    blob_payload = {  # exactly ExecutionEvidence.canonical_bytes' key set
        "schema": "gt.runtime_observation.v1", "kind": "test",
        "protocol": "unittest", "outcome": "fail", "observed_test_outcome": "fail",
        "action_id": 3, "command_sha256": "0" * 64, "returncode": 1,
        "repository_revision": "a" * 40, "raw_output_sha256": "1" * 64,
        "raw_output_bytes": 12, "raw_preserved": True,
        "environment_sha256": "", "timed_out": False, "encoding": "utf-8",
    }
    row = {
        **blob_payload, "artifact_sha256": "2" * 64, "raw_blob": "output_evidence/x",
        "baseline_classification": {
            "layout_schema": "gt.baseline_classification.v1", "scope": "suite",
            "verdicts": [["tests/test_y.py::test_b", "regression_new"]],
            "summary": {"regression_new": 1},
        },
        "submit_window": {"layout_schema": "gt.submit_window.v1",
                          "advisory": "suite green vs baseline"},
    }
    assert "baseline_classification" not in blob_payload
    from scripts.gt_installed_rehearsal import expected_block_for_row

    block = expected_block_for_row(row, blob_payload, "python -m unittest")
    assert ("; baseline: tests/test_y.py::test_b passed before your edits and fails "
            "in the full suite - this one is yours") in block
    assert block.endswith("; suite green vs baseline")
    # the blob-only rebuild is what regressed: it must be strictly shorter
    assert block != expected_execution_evidence_block(blob_payload, "python -m unittest")


def test_blob_stays_authoritative_for_the_fields_it_carries():
    """Only the two row-only keys come from the row; a row whose five original
    fields drifted from the hashed blob must not change the rebuilt line."""
    blob_payload = _payload()
    row = {**blob_payload, "kind": "build", "outcome": "pass", "returncode": 0,
           "observed_test_outcome": "pass"}
    from scripts.gt_installed_rehearsal import expected_block_for_row

    assert expected_block_for_row(row, blob_payload, "pytest -q") == (
        expected_execution_evidence_block(blob_payload, "pytest -q")
    )
