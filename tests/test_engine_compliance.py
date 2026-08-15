"""IE-10/11/12 compliance certificate tests."""
from __future__ import annotations

import hashlib

import pytest

from gt_engine.engine.compliance import (
    engine_import_closure,
    perf_passivity,
    registered_owner_consistency,
    verify_engine_delivery_events,
)


def test_perf_rows_never_model_visible():
    from scripts.engine_129_audit import build_transition_rows

    rows, _ = build_transition_rows()
    errors = perf_passivity(rows)
    assert errors == []


def test_acq_rows_stay_internal():
    errors = perf_passivity([
        {"identity": "graph_validity", "category": "ACQ", "model_visibility": "true",
         "target_disposition": "MODIFY"},
    ])
    assert errors and "ACQ" in errors[0]


def test_unregistered_fact_owner_flagged():
    errors = perf_passivity([
        {"identity": "mystery_fact", "category": "FACT", "model_visibility": "true",
         "target_disposition": "MODIFY"},
    ])
    assert any("mystery_fact" in e for e in errors)


def test_registered_owners_in_inventory():
    errors = registered_owner_consistency()
    assert errors == []


def test_engine_import_closure_is_clean():
    violations = engine_import_closure()
    assert violations == [], violations


def test_advisory_import_is_detected(tmp_path):
    engine_pkg = tmp_path / "engine"
    engine_pkg.mkdir()
    (engine_pkg / "bad.py").write_text(
        "import gt_engine.bridge  # noqa\n"
        "from gt_engine.miniswe_covering import x  # noqa\n",
        encoding="utf-8",
    )
    violations = engine_import_closure(engine_pkg)
    assert any("gt_engine.bridge" in v for v in violations)
    assert any("miniswe_covering" in v for v in violations)


def _good_event(delivery_id="delivery-0001"):
    return {
        "event": "engine_delivery",
        "delivery_id": delivery_id,
        "action_id": "a1",
        "decision": "pass_through",
        "final_observation_sha256": hashlib.sha256(b"obs").hexdigest(),
    }


def test_delivery_events_replayable():
    ok, issues = verify_engine_delivery_events([_good_event()])
    assert ok and issues == []


def test_delivery_event_missing_hash_fails():
    event = _good_event()
    del event["final_observation_sha256"]
    ok, issues = verify_engine_delivery_events([event])
    assert not ok and any("observation hash" in i for i in issues)


def test_delivery_event_secret_key_fails():
    event = _good_event()
    event["api_key"] = "sk-secret"
    ok, issues = verify_engine_delivery_events([event])
    assert not ok and any("secret" in i for i in issues)


def test_delivery_event_duplicate_id_fails():
    ok, issues = verify_engine_delivery_events([_good_event("d1"), _good_event("d1")])
    assert not ok and any("duplicate" in i for i in issues)


def test_empty_delivery_stream_is_replayable():
    ok, issues = verify_engine_delivery_events([])
    assert ok
