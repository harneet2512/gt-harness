"""Post-kill journal sealing closes provider-request conservation."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_engine.event_journal import verify_event_journal  # noqa: E402
from gt_engine.miniswe_integration import ExternalStateStore  # noqa: E402
from scripts.miniswe_supervisor import _seal_terminated_journals  # noqa: E402


def _make_state(tmp_path: Path) -> Path:
    state = tmp_path / "gt-state"
    store = ExternalStateStore(state, "task-x")
    for index in range(3):
        store.append("provider_admission", status="admitted",
                     request_tokens=100)
    store.append("provider_delivery", request_id="task-x-1-aaa",
                 resolved_model="openai/deepseek/deepseek-v4-flash-0731")
    store.append("provider_response", request_id="task-x-1-aaa")
    store.append("provider_delivery", request_id="task-x-2-bbb",
                 resolved_model="openai/deepseek/deepseek-v4-flash-0731")
    store.append("provider_response", request_id="task-x-2-bbb")
    store.append("provider_admission", status="admitted", request_tokens=90)
    # transport journal: request-1,2 terminated; request-3 orphaned (kill).
    provider = state / "task-x" / "provider_events.jsonl"
    provider.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {"event": "provider_request", "request_id": "request-1",
                 "schema": "gt.provider-receipt.v2"},
                {"event": "provider_response", "request_id": "request-1",
                 "schema": "gt.provider-receipt.v2"},
                {"event": "provider_request", "request_id": "request-2",
                 "schema": "gt.provider-receipt.v2"},
                {"event": "provider_failure", "request_id": "request-2",
                 "schema": "gt.provider-receipt.v2",
                 "error_type": "InternalServerError"},
                {"event": "provider_request", "request_id": "request-3",
                 "schema": "gt.provider-receipt.v2"},
            ]
        ),
        encoding="utf-8",
    )
    return state


def test_seal_closes_orphaned_request_and_chains_terminal(tmp_path):
    state = _make_state(tmp_path)
    journal = state / "task-x" / "events.jsonl"
    assert verify_event_journal(journal).valid

    seal = _seal_terminated_journals(
        state, reason="deadline_exceeded", terminal="timeout", exit_code=3
    )

    assert seal["orphaned_provider_requests"] == ["request-3"]
    assert seal["provider_failures_sealed"] == 1
    assert seal["run_terminal_appended"] == ["task-x"]

    verified = verify_event_journal(journal)
    assert verified.valid, verified.issues
    last = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "run_terminal"
    assert last["terminal"] == "timeout"
    assert last["orphaned_provider_requests"] == ["request-3"]
    assert last["provider_request_count"] == 3
    assert last["provider_response_count"] == 1
    assert last["provider_failure_count"] == 2  # 1 real + 1 sealed

    provider = [json.loads(line) for line in
                (state / "task-x" / "provider_events.jsonl")
                .read_text(encoding="utf-8").splitlines()]
    requests = {row["request_id"] for row in provider
                if row["event"] == "provider_request"}
    terminal = {row["request_id"] for row in provider
                if row["event"] in {"provider_response", "provider_failure"}}
    assert requests - terminal == set()
    sealed = [row for row in provider
              if row.get("error_type") == "DeadlineExceeded"]
    assert sealed and sealed[0]["request_id"] == "request-3"


def test_seal_truncates_torn_tail_before_appending(tmp_path):
    state = _make_state(tmp_path)
    journal = state / "task-x" / "events.jsonl"
    with journal.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "provider_admission", "seq')  # torn write

    seal = _seal_terminated_journals(
        state, reason="supervisor_termination", terminal="timeout", exit_code=3
    )
    verified = verify_event_journal(journal)
    assert verified.valid, verified.issues
    last = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "run_terminal"
    assert last["supervisor_reason"] == "supervisor_termination"


def test_seal_refuses_to_extend_an_invalid_journal(tmp_path):
    state = _make_state(tmp_path)
    journal = state / "task-x" / "events.jsonl"
    raw = journal.read_bytes()
    journal.write_bytes(raw + b'{"event": "injected", "bogus": true}\n')

    seal = _seal_terminated_journals(
        state, reason="deadline_exceeded", terminal="timeout", exit_code=3
    )
    # The injected row parses as JSON but breaks sequence/hash — journal stays
    # unsealed and flagged, never extended into a fabricated history.
    assert seal["run_terminal_appended"] == []
    assert any("journal_invalid" in item for item in seal["journals_skipped"])
    assert not verify_event_journal(journal).valid
