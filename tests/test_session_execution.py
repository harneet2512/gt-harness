import json
import subprocess
import sys

import pytest

from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter


def session_at(path, mode="assistive"):
    adapter = MiniSweAdapter(task_id="task", state_dir=path, predicates=[])
    return GTSession(GTSessionConfig(task_id="task", state_dir=str(path), mode=mode,
                                    capabilities=("exact_provider_payload",)), engine=adapter)


def test_real_execution_has_one_identity_and_terminal_record(tmp_path):
    session = session_at(tmp_path)
    def execute():
        result = subprocess.run([sys.executable, "-c", "print('real output')"],
                                capture_output=True, text=True, check=True)
        return {"output": result.stdout, "returncode": result.returncode}
    for _ in range(2):
        assert session.execute({"command": "fixture"}, execute)["output"].strip() == "real output"
    rows = [json.loads(line) for line in session.engine.store.path.read_text().splitlines()]
    starts = [row for row in rows if row["event"] == "execution_started"]
    finishes = [row for row in rows if row["event"] == "execution_finished"]
    assert len(starts) == len(finishes) == 2
    assert [row["execution_id"] for row in starts] == [row["execution_id"] for row in finishes]
    assert starts[0]["execution_id"] != starts[1]["execution_id"]
    assert all(row["result_sha256"] for row in finishes)
    assert session.integrity_receipt()["valid"] is True


def test_exception_is_preserved_and_terminal_is_recorded(tmp_path):
    session = session_at(tmp_path)
    error = RuntimeError("fixture")
    def execute():
        raise error
    with pytest.raises(RuntimeError) as caught:
        session.execute({"command": "failure"}, execute)
    assert caught.value is error
    rows = [json.loads(line) for line in session.engine.store.path.read_text().splitlines()]
    assert rows[-1]["disposition"] == "raised"
    assert not session._open_executions


def test_receipt_failure_preserves_action_but_invalidates_gt(tmp_path, monkeypatch):
    session = session_at(tmp_path)
    def broken(*args, **kwargs):
        raise OSError("fixture storage failure")
    monkeypatch.setattr(session.engine.store, "append", broken)
    result = {"output": "preserved", "returncode": 0}
    assert session.execute({"command": "fixture"}, lambda: result) is result
    assert session.integrity_receipt()["valid"] is False


def test_off_mode_keeps_native_result_without_gt_execution_records(tmp_path):
    session = session_at(tmp_path, GTMode.OFF)
    result = object()
    assert session.execute({}, lambda: result) is result
    assert session._execution_sequence == 0
    before = session.engine.store.receipt()
    assert session.suppress({}, result, reason="fixture") is result
    assert session.engine.store.receipt() == before


def test_reopened_journal_does_not_reuse_execution_identity(tmp_path):
    for _ in range(2):
        session = session_at(tmp_path)
        session.execute({"command": "fixture"}, lambda: {"output": "ok", "returncode": 0})
    rows = [json.loads(line) for line in session.engine.store.path.read_text().splitlines()]
    ids = [row["execution_id"] for row in rows if row["event"] == "execution_started"]
    assert len(ids) == len(set(ids)) == 2


def test_suppression_has_receipt_without_claiming_execution(tmp_path, monkeypatch):
    session = session_at(tmp_path)
    result = {"output": "refused", "returncode": 2}
    assert session.suppress({"command": "fixture"}, result, reason="fixture_policy") is result
    rows = [json.loads(line) for line in session.engine.store.path.read_text().splitlines()]
    assert rows[-1]["event"] == "action_suppressed"
    assert rows[-1]["reason"] == "fixture_policy"
    assert rows[-1]["executed"] is False
    assert rows[-1]["result_sha256"]
    assert not any(row["event"] == "execution_started" for row in rows)
    assert session.suppress({"command": "fixture"}, result, reason="fixture_policy") is result
    repeated = [json.loads(line) for line in session.engine.store.path.read_text().splitlines()]
    assert rows[-1]["action_id"] != repeated[-1]["action_id"]
    monkeypatch.setattr(session.engine.store, "append", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert session.suppress({}, result, reason="fixture_policy") is result
    assert session.integrity_receipt()["valid"] is False
