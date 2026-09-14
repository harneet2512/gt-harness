"""Replay-audit the recorded smoke20 oxvg trial (DeepSWE run 34801009507).

The recorded run delivered the same sealed ``churn_steer`` payload twice -
iteration 27 joined request 28, then again iteration 68 joined request 69,
under fresh dedup keys across the run's invalidation/recovery churn. Both
occurrences are legitimate: the runtime forbids only the same identity twice
into ONE boundary. The audit used to key delivery material by identity alone,
so the second occurrence read as ``duplicate GT delivery identity`` and its
request read as ``not joined to its immediate boundary``.

The fixture at ``tests/fixtures/smoke20_recorded/oxvg/`` is the vendored
recorded trial (``scripts/extract_replay_fixture.py``): the byte-faithful
journal plus every blob the audit verifies, minus the content stores it
never opens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_harness import runtime_receipts
from scripts import gt_audit
from tests.test_gt_audit import (
    make_native_miniswe_task,
    rewrite_native_events,
)

RECORDED_TASK = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "smoke20_recorded"
    / "oxvg"
    / "oxvg-structural-selector-preserv__9Tev39X"
)
REDELIVERED_IDENTITY = (
    "4ec9a448d8b5f59e75c0dde7ba66b231437ff4c61dbeb9f9f6145198bde2145c"
)
FIRST_BOUNDARY = "oxvg-structural-selector-preservation-28-d8dc67895a5111fb"
SECOND_BOUNDARY = "oxvg-structural-selector-preservation-69-f2e86f18beb2fd14"
STATE_DIR = (
    RECORDED_TASK / "agent" / "gt-state" / "oxvg-structural-selector-preservation"
)


@pytest.fixture(scope="module")
def recorded_audit() -> gt_audit.TaskAudit:
    # One pass over the 3.5 MB recorded journal; each test asserts a
    # different facet of the same audit.
    return gt_audit.audit_task(RECORDED_TASK)


def test_smoke20_oxvg_fixture_is_complete_and_intact():
    journals = sorted((RECORDED_TASK / "agent" / "gt-state").glob("*/events.jsonl"))
    assert len(journals) == 1
    rows = [
        json.loads(line)
        for line in journals[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 4325
    deliveries = [
        row
        for row in rows
        if row.get("event") in {"evidence_delivery", "context_addition_delivery"}
    ]
    occurrences = [
        (str(row.get("delivery_identity")), int(row.get("iteration") or 0))
        for row in deliveries
        if str(row.get("delivery_identity")) == REDELIVERED_IDENTITY
    ]
    # The recorded redelivery is two occurrences of one identity at two
    # different boundaries, both sealed-lane - never the same boundary twice.
    assert sorted(occurrences) == [
        (REDELIVERED_IDENTITY, 27),
        (REDELIVERED_IDENTITY, 68),
    ]
    lanes = {
        str(row.get("lane"))
        for row in deliveries
        if str(row.get("delivery_identity")) == REDELIVERED_IDENTITY
    }
    assert lanes == {"sealed"}


def test_smoke20_oxvg_legitimate_redelivery_audits_clean(recorded_audit):
    assert recorded_audit.attribution_rows == 4325
    assert recorded_audit.gt_deliveries == 163
    assert recorded_audit.attribution_issues == []
    assert recorded_audit.verdict == "GREEN-delivered"


def test_smoke20_oxvg_each_occurrence_joins_its_own_boundary(recorded_audit):
    redelivered = [
        verdict
        for verdict in recorded_audit.delivery_consumption
        if verdict["delivery_id"] == REDELIVERED_IDENTITY
    ]
    # One consumption verdict per delivery occurrence, each admitted into the
    # provider request immediately after its own delivery iteration.
    assert len(redelivered) == 2
    by_request = {verdict["request_id"]: verdict for verdict in redelivered}
    assert set(by_request) == {FIRST_BOUNDARY, SECOND_BOUNDARY}
    for verdict in redelivered:
        assert verdict["sent"], verdict
        assert verdict["visible"], verdict


def test_smoke20_oxvg_receipt_layer_accepts_sealed_redelivery():
    _journal_path, events = runtime_receipts._events(STATE_DIR)
    deliveries = runtime_receipts._delivery_rows(events)
    # Sealed-lane redelivery across boundaries is inside the delivery
    # contract: per-boundary identity uniqueness holds and no prompt-lane
    # identity repeats run-wide.
    runtime_receipts._validate_delivery_boundaries(deliveries)
    receipts = runtime_receipts._provider_delivery_receipts(events)
    redelivered = [
        receipt
        for receipt in receipts
        if receipt["delivery_identity"] == REDELIVERED_IDENTITY
    ]
    assert sorted(
        receipt["delivered_before_call"] for receipt in redelivered
    ) == [28, 69]
    assert {receipt["lane"] for receipt in redelivered} == {"sealed"}


def _insert_redelivery(task: Path, *, lane: str | None, same_boundary: bool) -> None:
    """Append a second occurrence of the fixture's delivery identity.

    ``rewrite_native_events`` re-chains parent/event hashes; sequence numbers
    are reassigned here so the journal stays admission-valid.
    """

    def mutate(rows: list[dict]) -> None:
        delivery = next(
            row for row in rows if row.get("event") == "evidence_delivery"
        )
        redelivery = dict(delivery)
        if not same_boundary:
            redelivery["iteration"] = int(delivery.get("iteration") or 0) + 1
        if lane is not None:
            delivery["lane"] = lane
            redelivery["lane"] = lane
        rows.insert(rows.index(delivery) + 1, redelivery)
        for index, row in enumerate(rows, start=1):
            row["sequence"] = index

    rewrite_native_events(task, mutate)


def test_native_same_boundary_duplicate_delivery_is_still_flagged(tmp_path):
    task = make_native_miniswe_task(tmp_path, delivery_text="inspect sibling.py")
    _insert_redelivery(task, lane=None, same_boundary=True)
    audit = gt_audit.audit_task(task)
    assert any(
        "duplicate GT delivery identity" in issue
        for issue in audit.attribution_issues
    )


def test_native_prompt_lane_redelivery_in_run_is_still_flagged(tmp_path):
    task = make_native_miniswe_task(tmp_path, delivery_text="inspect sibling.py")
    _insert_redelivery(task, lane="prompt", same_boundary=False)
    audit = gt_audit.audit_task(task)
    assert any(
        "duplicate GT delivery identity" in issue
        for issue in audit.attribution_issues
    )


def test_native_sealed_redelivery_to_later_boundary_is_legitimate(tmp_path):
    task = make_native_miniswe_task(tmp_path, delivery_text="inspect sibling.py")
    _insert_redelivery(task, lane="sealed", same_boundary=False)
    audit = gt_audit.audit_task(task)
    assert not any(
        "duplicate GT delivery identity" in issue
        for issue in audit.attribution_issues
    )
    assert not any(
        "not joined to its immediate boundary" in issue
        for issue in audit.attribution_issues
    )
