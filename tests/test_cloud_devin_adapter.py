"""Devin adapter tests: session-API rows mapped onto the contract, honestly.

FAKE BOUNDARY: the Devin API (a stub client returning canned rows) and the GT
server (a recording Bridge). Real: the watcher, the state mapping, the change
detection, the child-session tree, the finish decision.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "cloud" / "adapters" / "devin")
)

import gt_cloud_devin as devin  # noqa: E402


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "session_id": "devin-1",
        "status": "running",
        "status_detail": "working",
        "acus_consumed": 0.0,
        "pull_requests": [],
        "child_session_ids": [],
        "title": "Fix the flaky test",
        "url": "https://app.devin.ai/sessions/devin-1",
        "org_id": "org-x",
        "created_at": 1,
        "updated_at": 1,
        "tags": [],
    }
    row.update(over)
    return row


class StubClient:
    """FAKE BOUNDARY: api.devin.ai. ``rows[devin_id]`` is the current answer."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = dict(rows)
        self.gets = 0

    def session(self, devin_id: str) -> dict[str, Any] | None:
        self.gets += 1
        return self.rows.get(devin_id)

    def sessions(self, first: int = 50) -> list[dict[str, Any]]:
        return list(self.rows.values())


class FakeBridge:
    """FAKE BOUNDARY: the GT server. Records every contract call."""

    instances: list["FakeBridge"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.agent_id = f"agent-{len(FakeBridge.instances)}"
        self.enabled = True
        self.events: list[tuple] = []
        self.finished: list[tuple] = []
        FakeBridge.instances.append(self)

    def start(self) -> bool:
        return True

    def status(self, state, note=None, activity=None, tokens=None) -> bool:
        self.events.append(("status", state, note, activity, tokens))
        return True

    def assistant(self, text) -> bool:
        self.events.append(("assistant", text))
        return True

    def finish(self, status="done", summary=None, deadline=5.0) -> bool:
        self.finished.append((status, summary))
        self.enabled = False
        return True


@pytest.fixture
def watcher(monkeypatch):
    FakeBridge.instances = []
    monkeypatch.setattr(devin, "Bridge", FakeBridge)
    return devin.DevinWatcher(StubClient({}))


def test_running_working_maps_to_a_working_status(watcher):
    watcher.client.rows["devin-1"] = _row()
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    bridge = watcher.bridges["devin-1"]
    assert ("status", "working", None, "Working", None) in bridge.events


def test_waiting_for_user_is_idle_not_working(watcher):
    watcher.client.rows["devin-1"] = _row(status_detail="waiting_for_user")
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    bridge = watcher.bridges["devin-1"]
    assert ("status", "idle", None, "Waiting for you", None) in bridge.events


def test_acu_is_a_note_never_tokens(watcher):
    watcher.client.rows["devin-1"] = _row(acus_consumed=3.7)
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    bridge = watcher.bridges["devin-1"]
    statuses = [e for e in bridge.events if e[0] == "status"]
    assert statuses[-1][2] == "ACU 3.70"  # the note
    assert all(e[4] is None for e in statuses)  # tokens never fabricated


def test_no_event_when_nothing_changed(watcher):
    watcher.client.rows["devin-1"] = _row()
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    count = len(watcher.bridges["devin-1"].events)
    watcher.poll_watched()
    assert len(watcher.bridges["devin-1"].events) == count


def test_a_new_pull_request_is_announced_once(watcher):
    pr = {"pr_url": "https://github.com/o/r/pull/9"}
    watcher.client.rows["devin-1"] = _row(pull_requests=[pr])
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    watcher.poll_watched()
    texts = [e[1] for e in watcher.bridges["devin-1"].events if e[0] == "assistant"]
    assert texts == ["Opened pull request: https://github.com/o/r/pull/9"]


def test_a_child_session_registers_under_its_parent(watcher):
    watcher.client.rows["devin-1"] = _row(child_session_ids=["devin-2"])
    watcher.client.rows["devin-2"] = _row(session_id="devin-2", title="child task")
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    child = watcher.bridges["devin-2"]
    assert child.kwargs["parent_agent_id"] == watcher.bridges["devin-1"].agent_id
    assert child.kwargs["agent_kind"] == "devin"


def test_terminal_status_finishes_the_card(watcher):
    watcher.client.rows["devin-1"] = _row(status="exit", status_detail="finished")
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    # A finished card leaves the watched set so polling moves on.
    assert "devin-1" not in watcher.bridges
    finished = FakeBridge.instances[0].finished
    assert finished and finished[0][0] == "done"


def test_an_error_status_finishes_as_error(watcher):
    watcher.client.rows["devin-1"] = _row(status="error")
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    assert FakeBridge.instances[0].finished[0][0] == "error"


def test_unreachable_api_notes_itself_without_dying(watcher):
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    # rows empty -> client.session returns None -> failure path
    for _ in range(devin._FAILURE_NOTE_AFTER):
        watcher.poll_watched()
    bridge = watcher.bridges["devin-1"]
    assert bridge.enabled
    assert any("unreachable" in str(e[2]) for e in bridge.events)


def test_children_are_not_followed_when_disabled():
    FakeBridge.instances = []
    watcher = devin.DevinWatcher(StubClient({}), follow_children=False)
    watcher.client.rows["devin-1"] = _row(child_session_ids=["devin-2"])
    watcher.attach("devin-1", _row(), parent_agent=None, depth=0)
    watcher.poll_watched()
    assert "devin-2" not in watcher.bridges


def test_discover_org_skips_terminal_sessions():
    client = StubClient(
        {
            "devin-1": _row(session_id="devin-1"),
            "devin-2": _row(session_id="devin-2", status="exit"),
        }
    )
    watcher = devin.DevinWatcher(client)
    assert watcher.discover_org() == ["devin-1"]
