from __future__ import annotations

import json

from gt_engine.event_journal import read_verified_events, verify_event_journal
from gt_engine.miniswe_integration import ExternalStateStore


def test_event_store_writes_monotonic_hash_linked_rows(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("first", value=1)
    store.append("second", value=2)

    rows = [json.loads(line) for line in store.path.read_text(
        encoding="utf-8"
    ).splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["parent_hash"] == "0" * 64
    assert rows[1]["parent_hash"] == rows[0]["event_hash"]
    receipt = store.receipt()
    assert receipt == {"event_count": 2, "event_head": rows[1]["event_hash"]}
    result = verify_event_journal(store.path, **receipt)
    assert result.valid is True
    assert result.issues == ()


def test_event_journal_detects_payload_tampering(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("first", value=1)
    store.append("second", value=2)
    rows = [json.loads(line) for line in store.path.read_text(
        encoding="utf-8"
    ).splitlines()]
    rows[0]["value"] = 999
    store.path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"))
                  for row in rows) + "\n",
        encoding="utf-8",
    )
    result = verify_event_journal(store.path)
    assert result.valid is False
    assert any("hash mismatch" in issue for issue in result.issues)


def test_event_journal_accepts_typed_payload_schemas(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("receipt", schema="gt_receipt.v1", transition="delivered")
    store.append(
        "execution_evidence",
        schema="gt.runtime_observation.v1",
        outcome="pass",
    )

    result = verify_event_journal(store.path, **store.receipt())

    assert result.valid is True
    assert result.issues == ()


def test_event_journal_rejects_unknown_schema(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("first", schema="gt.unknown.v1")

    result = verify_event_journal(store.path)

    assert result.valid is False
    assert any("unsupported or missing schema" in issue for issue in result.issues)


def test_event_journal_anchor_detects_tail_truncation(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("first")
    store.append("second")
    receipt = store.receipt()
    first = store.path.read_text(encoding="utf-8").splitlines()[0]
    store.path.write_text(first + "\n", encoding="utf-8")
    result = verify_event_journal(store.path, **receipt)
    assert result.valid is False
    assert any("event count" in issue for issue in result.issues)
    assert any("anchored head" in issue for issue in result.issues)


def test_verified_replay_rejects_reordered_rows(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("first")
    store.append("second")
    lines = store.path.read_text(encoding="utf-8").splitlines()
    store.path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    result = verify_event_journal(store.path)
    assert result.valid is False
    try:
        read_verified_events(store.path)
    except ValueError as exc:
        assert "invalid event journal" in str(exc)
    else:
        raise AssertionError("tampered journal was replayed")


def test_reopened_store_continues_verified_typed_event_chain(tmp_path):
    store = ExternalStateStore(tmp_path, "task")
    store.append("execution_evidence", schema="gt.runtime_observation.v1", outcome="fail")
    reopened = ExternalStateStore(tmp_path, "task")
    reopened.append("checkpoint_recovered")
    result = verify_event_journal(reopened.path, **reopened.receipt())
    assert result.valid, result.issues


def test_reopened_store_detects_a_truncated_tail(tmp_path):
    """A truncated prefix is still a VALID chain, which is the whole problem.

    Sequence numbers and parent hashes are internally consistent after the tail
    is cut, so verify_event_journal(path) with no anchor returns valid. Startup
    recovery then rebuilds from a journal that has silently lost its most recent
    events, and the plan checkpoint and check definitions it restores describe a
    state the run already moved past.
    """
    store = ExternalStateStore(tmp_path, "task")
    store.append("first")
    store.append("second")
    store.append("third")

    kept = store.path.read_text(encoding="utf-8").splitlines()[0]
    store.path.write_text(kept + "\n", encoding="utf-8")

    # The chain alone still looks perfect. That is why an anchor is required.
    assert verify_event_journal(store.path).valid is True

    reopened = ExternalStateStore(tmp_path, "task")
    assert reopened.startup_journal_valid is False


def test_reopened_store_survives_a_crash_between_row_and_anchor(tmp_path):
    """An anchor one row behind is a crash, not tampering, and must not fail.

    The row is written before the anchor, so an unclean stop leaves the anchor
    trailing. Treating that as corruption would make every crash look like an
    attack and would discard a journal that is entirely intact.
    """
    store = ExternalStateStore(tmp_path, "task")
    store.append("first")
    store.append("second")
    anchor = store.root / "events.anchor.json"
    assert anchor.is_file()
    behind = json.loads(anchor.read_text(encoding="utf-8"))

    store.append("third")
    anchor.write_text(json.dumps(behind), encoding="utf-8")

    reopened = ExternalStateStore(tmp_path, "task")
    assert reopened.startup_journal_valid is True


def test_reopened_store_rejects_a_rewritten_history_under_a_stale_anchor(tmp_path):
    """Trailing by count must not become a licence to rewrite what came before."""
    store = ExternalStateStore(tmp_path, "task")
    store.append("first")
    store.append("second")
    anchor = store.root / "events.anchor.json"
    behind = json.loads(anchor.read_text(encoding="utf-8"))

    rebuilt = ExternalStateStore(tmp_path, "task2")
    rebuilt.append("different")
    rebuilt.append("history")
    rebuilt.append("longer")
    store.path.write_text(rebuilt.path.read_text(encoding="utf-8"), encoding="utf-8")
    anchor.write_text(json.dumps(behind), encoding="utf-8")

    reopened = ExternalStateStore(tmp_path, "task")
    assert reopened.startup_journal_valid is False
