"""The request queue between the CLI and the engine.

`gt-plan` writes a request file and answers "requested". The engine reads the
queue once per turn and is the only thing that may change the plan. Everything
the agent believes about its own recorded design rests on those two halves
agreeing, so what the queue silently drops is as important as what it applies.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.event_journal import verify_event_journal
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan

PROMPT = (
    "The widget must preserve compatibility.\n"
    "The widget must reject invalid input.\n"
    "The widget must report the failing service.\n"
)


@pytest.fixture
def adapter(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")
    instance = MiniSweAdapter(task_id="cli-queue", state_dir=tmp_path / "state",
                              repo_root=str(repo), predicates=())
    instance.persistent_plan = build_plan(
        None, build_plan_inputs(PROMPT, repo_root=str(repo), capture_baseline=False),
        repo_root=str(repo),
    )
    instance.publish_plan_state()
    return instance


def _published_digest(adapter) -> str:
    state = json.loads(
        (adapter.store.root / "plan" / "current.json").read_text(encoding="utf-8")
    )
    return state["plan_digest"]


def _queue(adapter, name: str, **request) -> None:
    inbox = adapter.store.root / "plan" / "requests"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(json.dumps(request), encoding="utf-8")


def _rejections(adapter) -> list[dict]:
    rows = [json.loads(line)
            for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("event") == "plan_revision_rejected"]


def test_two_requests_authored_against_one_published_state_both_apply(adapter):
    """The CLI publishes one digest per turn, so a batch shares one base.

    `plan/current.json` is rewritten once, after the whole queue is drained.
    Two `gt-plan revise` calls in the same turn therefore read the SAME digest
    and both are answered "requested". Comparing each against a digest that
    moves as the batch applies rejects everything after the first, and the
    agent has no way to find out: it already saw success from the CLI.
    """
    first, second = [row.row_id for row in adapter.persistent_plan.rows[:2]]
    digest = _published_digest(adapter)
    _queue(adapter, "01.json", plan_digest=digest, row_id=first, operation="revise",
           value={"approach": "Keep the existing public signature."})
    _queue(adapter, "02.json", plan_digest=digest, row_id=second, operation="revise",
           value={"approach": "Reject unknown keys at the boundary."})
    adapter.apply_plan_requests()

    assert adapter.persistent_plan.row(first).approach == "Keep the existing public signature."
    assert adapter.persistent_plan.row(second).approach == "Reject unknown keys at the boundary."
    assert _rejections(adapter) == []
    assert verify_event_journal(adapter.store.path).valid


def test_the_journal_chains_a_batch_through_its_true_intermediate_digests(adapter):
    """Accepting a shared base must not loosen the recovery chain.

    Restart replay walks `previous_plan_digest` -> `resulting_plan_digest`. Those
    stay the ACTUAL digests either side of each application, whatever base the
    request was authored against, so a batch replays exactly.
    """
    first, second = [row.row_id for row in adapter.persistent_plan.rows[:2]]
    digest = _published_digest(adapter)
    _queue(adapter, "01.json", plan_digest=digest, row_id=first, operation="revise",
           value={"approach": "one"})
    _queue(adapter, "02.json", plan_digest=digest, row_id=second, operation="revise",
           value={"approach": "two"})
    adapter.apply_plan_requests()

    rows = [json.loads(line)
            for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]
    applied = [row for row in rows if row.get("event") == "plan_revision_applied"]
    assert len(applied) == 2
    assert applied[0]["previous_plan_digest"] == digest
    assert applied[0]["resulting_plan_digest"] == applied[1]["previous_plan_digest"]
    assert applied[1]["resulting_plan_digest"] == hashlib.sha256(
        adapter.persistent_plan.canonical_json().encode()).hexdigest()


def test_a_request_against_a_superseded_published_state_is_still_rejected(adapter):
    """A shared base is not no base. An older turn's digest is still stale."""
    row_id = adapter.persistent_plan.rows[0].row_id
    old = _published_digest(adapter)
    _queue(adapter, "01.json", plan_digest=old, row_id=row_id, operation="revise",
           value={"approach": "first turn"})
    adapter.apply_plan_requests()
    assert _published_digest(adapter) != old

    _queue(adapter, "02.json", plan_digest=old, row_id=row_id, operation="revise",
           value={"approach": "authored against the turn before"})
    adapter.apply_plan_requests()
    assert adapter.persistent_plan.row(row_id).approach == "first turn"
    assert any("stale" in row.get("detail", "") for row in _rejections(adapter))


def test_an_invalid_check_spec_is_rejected_and_the_queue_keeps_going(adapter):
    """A check the engine cannot run must not be recorded as a check.

    A shell string is a program the engine cannot bind to a test identity, so
    the binding is refused. The requests after it must still be applied: one
    bad proposal cannot stop the agent's other work from landing.
    """
    first, second = [row.row_id for row in adapter.persistent_plan.rows[:2]]
    digest = _published_digest(adapter)
    _queue(adapter, "01.json", plan_digest=digest, row_id=first, operation="bind-check",
           value={"argv": ["bash", "-c", "pytest && echo done"]})
    _queue(adapter, "02.json", plan_digest=digest, row_id=second, operation="revise",
           value={"approach": "still recorded"})
    adapter.apply_plan_requests()

    assert not getattr(adapter, "_check_specs", {})
    assert adapter.persistent_plan.row(second).approach == "still recorded"
    assert len(_rejections(adapter)) == 1
    assert verify_event_journal(adapter.store.path).valid


def test_a_deferral_without_a_reason_is_refused_by_both_halves(adapter, tmp_path, monkeypatch):
    """The CLI refuses to write it, and the engine refuses to apply it.

    Two independent refusals, because either half alone can be bypassed: the
    request file is an ordinary file the agent can write directly.
    """
    from gt_engine.persistent_plan.cli import main

    row_id = adapter.persistent_plan.rows[0].row_id
    monkeypatch.setenv("GT_PLAN_ROOT", str(adapter.store.root / "plan"))
    assert main(["defer", row_id]) == 1
    assert not list((adapter.store.root / "plan" / "requests").glob("*.json"))

    _queue(adapter, "01.json", plan_digest=_published_digest(adapter), row_id=row_id,
           operation="defer", value={}, reason="   ")
    adapter.apply_plan_requests()
    assert adapter.plan_row_state(row_id) != "DEFERRED"
    assert len(_rejections(adapter)) == 1


def test_a_deferral_with_a_reason_is_recorded_without_granting_evidence(adapter):
    row_id = adapter.persistent_plan.rows[0].row_id
    _queue(adapter, "01.json", plan_digest=_published_digest(adapter), row_id=row_id,
           operation="defer", value={}, reason="Blocked on the caller migration.")
    adapter.apply_plan_requests()
    assert adapter.plan_row_state(row_id) == "DEFERRED"
    # Deferred is a state, not a proof: the row stays outstanding.
    assert row_id in adapter.unmet_plan_rows()


def test_an_unsupported_operation_is_rejected_without_touching_the_plan(adapter):
    row_id = adapter.persistent_plan.rows[0].row_id
    before = adapter.persistent_plan.canonical_json()
    _queue(adapter, "01.json", plan_digest=_published_digest(adapter), row_id=row_id,
           operation="prove", value={"state": "PROVEN"})
    adapter.apply_plan_requests()
    assert adapter.persistent_plan.canonical_json() == before
    assert len(_rejections(adapter)) == 1


def test_a_request_naming_an_unknown_row_is_rejected(adapter):
    _queue(adapter, "01.json", plan_digest=_published_digest(adapter),
           row_id="req-not-in-this-plan", operation="revise", value={"approach": "x"})
    adapter.apply_plan_requests()
    assert any("unknown plan row" in row.get("detail", "") for row in _rejections(adapter))


def test_every_request_file_is_read_at_most_once(adapter):
    row_id = adapter.persistent_plan.rows[0].row_id
    _queue(adapter, "01.json", plan_digest=_published_digest(adapter), row_id=row_id,
           operation="revise", value={"approach": "applied once"})
    adapter.apply_plan_requests()
    count = adapter.store.receipt()["event_count"]
    adapter.apply_plan_requests()
    adapter.apply_plan_requests()
    assert adapter.store.receipt()["event_count"] == count
