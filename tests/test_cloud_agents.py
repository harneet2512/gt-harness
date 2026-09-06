"""Worker coding agents on the cloud server (HAR-84).

FAKE BOUNDARY (module-wide, single fake): the model provider, exactly as in
``tests/test_cloud_chat.py`` — whose harness this module reuses. Real: the
FastAPI app on a loopback uvicorn port, JWT auth, the SQLite store, the event
bus and its SSE encoder, the agent turn loop, real bash, and real git (a real
seed repository, really cloned once per session, really diffed and really
``git apply``-ed from one workspace into another).

``SANDBOX_MODE`` is ``local`` here, so no container is needed: a worker is a
child *session*, and everything that makes it one — its own clone, its own
transcript, its report, the mirrored frames, the patch apply — is exercised.

Run: ``python -m pytest tests/test_cloud_agents.py -q`` from the repo root.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

import pytest

from cloud.server import runner as runner_module
from cloud.server.models import MAX_TASKS_PER_SPAWN
from tests import test_cloud_chat as chat
from tests.test_cloud_chat import (
    DEFAULT_SCRIPT,
    POLL_INTERVAL,
    POLL_TIMEOUT,
    Harness,
    _action,
    _create,
    _create_idle,
    _diff,
    _has,
    _post_message,
    _read_sse,
    _reply,
    _session,
    _turn_complete,
    _wait_status,
)

#: the chat module's fixtures, reused rather than reimplemented. Importing the
#: fixture names directly would shadow them in every test signature, so the
#: underlying generator is re-wrapped here instead.
_HARNESS_IMPL = getattr(chat.harness, "__wrapped__", chat.harness)


@pytest.fixture(scope="session")
def seed_repo(tmp_path_factory):
    """One real git repository, cloned by every session in this module."""
    return chat._make_seed_repo(tmp_path_factory.mktemp("seed"))


@pytest.fixture
def harness(seed_repo, tmp_path, monkeypatch):
    """The chat module's real-server harness, verbatim."""
    yield from _HARNESS_IMPL(seed_repo, tmp_path, monkeypatch)

#: appends the workspace directory name — the session id — to README.md, so
#: two sessions running the SAME script still write different lines, and their
#: patches genuinely conflict.
SIGN_README = 'echo "signed-by-$(basename "$PWD")" >> README.md'
SIGN_SCRIPT: list[Any] = [_action(SIGN_README), _reply("Signed the readme.")]
WORKER_FILE_SCRIPT: list[Any] = [
    _action("echo made-by-a-worker > worker.txt"),
    _reply("Wrote worker.txt."),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _spawn(h: Harness, session_id: str, *tasks: str, **body: Any):
    return h.client.post(
        f"/api/sessions/{session_id}/agents",
        json={"tasks": list(tasks), **body},
        headers=h.auth,
    )


def _spawn_ok(h: Harness, session_id: str, *tasks: str) -> list[dict]:
    response = _spawn(h, session_id, *tasks)
    assert response.status_code == 202, response.text
    workers = response.json()["workers"]
    assert len(workers) == len(tasks)
    return workers


def _agents(h: Harness, session_id: str) -> list[dict]:
    response = h.client.get(f"/api/sessions/{session_id}/agents", headers=h.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _apply(h: Harness, session_id: str, worker_id: str):
    return h.client.post(
        f"/api/sessions/{session_id}/agents/{worker_id}/apply", headers=h.auth
    )


def _messages(h: Harness, session_id: str) -> list[dict]:
    response = h.client.get(f"/api/sessions/{session_id}/messages", headers=h.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _count(event_type: str, minimum: int):
    """Predicate: at least ``minimum`` frames of ``event_type`` have arrived."""

    def predicate(frames: list[dict]) -> bool:
        return sum(1 for f in frames if f["event"] == event_type) >= minimum

    return predicate


def _payloads(frames: list[dict], event_type: str) -> list[dict]:
    return [f["payload"]["data"] for f in frames if f["event"] == event_type]


def _wait_reports(h: Harness, session_id: str, count: int) -> list[dict]:
    """Read the parent's stream until ``count`` workers have reported."""
    return _read_sse(h, session_id, until=_count("agent_report", count))


def _wait_worker_idle(h: Harness, worker_id: str) -> dict:
    return _wait_status(h, worker_id, {"idle"}, timeout=POLL_TIMEOUT)


def _wait_report(h: Harness, worker_id: str) -> dict:
    """The worker row, once its report has landed on it."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        worker = _session(h, worker_id)
        if worker.get("report"):
            return worker
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"worker {worker_id} never reported")


# --------------------------------------------------------------------------
# 1: spawning
# --------------------------------------------------------------------------
def test_spawned_workers_are_children_that_run_and_report(harness: Harness) -> None:
    """FAKE BOUNDARY: the model provider. Real: app, store, bus, agent, git."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    workers = _spawn_ok(harness, parent_id, "task one", "task two")

    assert [w["parent_id"] for w in workers] == [parent_id, parent_id]
    assert [w["role"] for w in workers] == ["worker", "worker"]
    assert [w["task"] for w in workers] == ["task one", "task two"]
    # same repo, ref, model and GT mode as the parent — a worker is the same
    # session with a different job, not a differently configured one
    parent = _session(harness, parent_id)
    for worker in workers:
        assert (worker["repo"], worker["ref"], worker["model"], worker["gt_mode"]) == (
            parent["repo"], parent["ref"], parent["model"], parent["gt_mode"]
        )

    frames = _wait_reports(harness, parent_id, 2)

    spawned = _payloads(frames, "agent_spawned")
    assert [s["worker_id"] for s in spawned] == [w["id"] for w in workers]
    assert [s["task"] for s in spawned] == ["task one", "task two"]
    reports = _payloads(frames, "agent_report")
    assert {r["worker_id"] for r in reports} == {w["id"] for w in workers}
    for report in reports:
        assert report["finish_reason"] == "reply"
        assert "newfile.txt" in report["files_changed"]
        assert report["patch_sha256"]
        assert report["n_calls"] >= 1

    # ...and each worker went back to idle rather than closing itself
    for worker in workers:
        assert _wait_worker_idle(harness, worker["id"])["status"] == "idle"


def test_a_worker_runs_its_task_without_being_messaged(harness: Harness) -> None:
    """The task IS the first turn: creation and first message are one action."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    worker_id = _spawn_ok(harness, parent_id, "append to the readme")[0]["id"]

    frames = _read_sse(harness, worker_id, until=_turn_complete)
    started = _payloads(frames, "turn_started")
    assert started and started[0]["content"] == "append to the readme"
    # no second call was made: the transcript's only user message is the task
    roles = [(m["role"], m["content"]) for m in _messages(harness, worker_id)]
    assert ("user", "append to the readme") in roles
    assert sum(1 for role, _ in roles if role == "user") == 1
    worker = _wait_report(harness, worker_id)
    assert worker["report"]["finish_reason"] == "reply"
    assert worker["report"]["applied"] is False


def test_the_report_is_in_the_parents_messages_after_a_reload(
    harness: Harness,
) -> None:
    """A stream is not a record: a reload has to show what the worker said."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "do the thing")[0]["id"]

    _wait_reports(harness, parent_id, 1)

    reported = [
        m for m in _messages(harness, parent_id)
        if m["role"] == "agent" and m["meta"].get("agent_id") == worker_id
    ]
    assert len(reported) == 1, "exactly one report message per finished worker turn"
    meta = reported[0]["meta"]
    assert meta["finish_reason"] == "reply"
    assert "newfile.txt" in meta["files_changed"]
    assert meta["patch_sha256"]
    assert reported[0]["content"].strip()


# --------------------------------------------------------------------------
# 2: mirrored frames
# --------------------------------------------------------------------------
def test_worker_frames_are_mirrored_onto_the_parent_stream(harness: Harness) -> None:
    """One subscription draws every trail: `agent_id` says whose each frame is."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    # the parent's own turn first, so both kinds of frame are on one stream
    _post_message(harness, parent_id, "do it yourself first")
    _read_sse(harness, parent_id, until=_turn_complete)

    worker_id = _spawn_ok(harness, parent_id, "and now in parallel")[0]["id"]

    frames = _wait_reports(harness, parent_id, 1)

    mirrored = [
        f for f in frames
        if f["payload"]["data"].get("agent_id") == worker_id
    ]
    assert {f["event"] for f in mirrored} >= {
        "turn_started", "assistant", "tool_call", "tool_result", "turn_finished"
    }
    # the parent's own frames carry no agent_id at all
    own = [
        f for f in frames
        if f["event"] in {"assistant", "tool_call", "turn_finished"}
        and f not in mirrored
    ]
    assert own, "the parent ran a turn of its own"
    assert all("agent_id" not in f["payload"]["data"] for f in own)


def test_a_workers_gt_actions_are_mirrored_too(harness: Harness) -> None:
    """A worker's GroundTruth evidence belongs on the parent's one stream."""
    harness.install_gt_hook = True
    harness.set_script([chat._typed_action(), _reply("pkg/util.py defines it.")])
    parent_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "find where VALUE is defined")[0]["id"]

    frames = _wait_reports(harness, parent_id, 1)

    mirrored = [
        f for f in frames
        if f["event"] == "gt_action"
        and f["payload"]["data"].get("agent_id") == worker_id
    ]
    assert len(mirrored) == 1, "the worker's typed action reached the parent"
    event = mirrored[0]["payload"]["data"]
    assert event["kind"] == "exact_literal_search"
    assert event["semantics"] == "exact"
    assert event["match_count"] == 1

    # and it is on the worker's own stream, without an agent_id
    own = [
        f for f in _read_sse(harness, worker_id, until=_turn_complete)
        if f["event"] == "gt_action"
    ]
    assert len(own) == 1
    assert "agent_id" not in own[0]["payload"]["data"]


# --------------------------------------------------------------------------
# 3: applying a worker's patch
# --------------------------------------------------------------------------
def test_applying_a_worker_merges_its_files_into_the_parent(
    harness: Harness,
) -> None:
    """The point of the whole feature: the parent ends up with the work."""
    harness.set_script(list(WORKER_FILE_SCRIPT))
    parent_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "write worker.txt")[0]["id"]
    _wait_reports(harness, parent_id, 1)
    assert _diff(harness, parent_id)["files"] == [], "the parent wrote nothing itself"

    response = _apply(harness, parent_id, worker_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["worker_id"] == worker_id
    assert body["files"] == ["worker.txt"]
    assert body["patch_sha256"]

    diff = _diff(harness, parent_id)
    assert [f["path"] for f in diff["files"]] == ["worker.txt"]
    assert "made-by-a-worker" in diff["patch"]
    # and the same patch, recorded on both sides of the transaction
    worker = _session(harness, worker_id)
    assert worker["applied_at"] and worker["report"]["applied"] is True
    note = f"applied worker {worker_id}: 1 files"
    assert any(
        m["role"] == "system" and m["content"] == note
        for m in _messages(harness, parent_id)
    )
    frames = _read_sse(harness, parent_id, until=_has("agent_applied"))
    applied = _payloads(frames, "agent_applied")[0]
    assert applied == {
        "worker_id": worker_id,
        "files": ["worker.txt"],
        "patch_sha256": body["patch_sha256"],
    }


def test_a_conflicting_apply_names_the_paths_and_changes_nothing(
    harness: Harness,
) -> None:
    """Half a merge is not a result: on conflict the workspace is untouched."""
    harness.set_script(list(SIGN_SCRIPT))
    parent_id = _create_idle(harness)
    _post_message(harness, parent_id, "sign the readme")
    _read_sse(harness, parent_id, until=_turn_complete)
    _wait_status(harness, parent_id, {"idle"})
    before = _diff(harness, parent_id)
    assert [f["path"] for f in before["files"]] == ["README.md"]

    worker_id = _spawn_ok(harness, parent_id, "sign the readme too")[0]["id"]
    _wait_reports(harness, parent_id, 1)
    response = _apply(harness, parent_id, worker_id)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["conflicts"] == ["README.md"]
    assert "conflict" in body["detail"]

    after = _diff(harness, parent_id)
    assert after["patch"] == before["patch"], "the parent's own work is intact"
    assert "<<<<<<<" not in after["patch"], "no conflict markers were left behind"
    assert f"signed-by-{worker_id}" not in after["patch"]
    assert _session(harness, worker_id)["applied_at"] is None


def test_a_worker_with_nothing_to_apply_is_a_400(harness: Harness) -> None:
    harness.set_script([_action("true"), _reply("Nothing to change.")])
    parent_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "look around")[0]["id"]
    _wait_reports(harness, parent_id, 1)

    response = _apply(harness, parent_id, worker_id)

    assert response.status_code == 400, response.text
    assert "no changes" in response.json()["detail"]


def test_apply_needs_an_idle_parent_and_a_real_worker(harness: Harness) -> None:
    harness.set_script(list(WORKER_FILE_SCRIPT))
    parent_id = _create_idle(harness)
    other_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "write worker.txt")[0]["id"]
    _wait_reports(harness, parent_id, 1)

    # a worker of a different session is not this session's to apply
    assert _apply(harness, other_id, worker_id).status_code == 404
    assert _apply(harness, parent_id, "nope").status_code == 404

    harness.block_model_at(1)
    _post_message(harness, parent_id, "start a turn and stay in it")
    harness.wait_blocked(parent_id)
    try:
        response = _apply(harness, parent_id, worker_id)
        assert response.status_code == 409, response.text
        assert "idle" in response.json()["detail"]
    finally:
        harness.release_model()


# --------------------------------------------------------------------------
# 4: closing
# --------------------------------------------------------------------------
def test_closing_the_parent_closes_its_workers(harness: Harness) -> None:
    """A worker holds a clone of its own; it cannot outlive its parent."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    workers = _spawn_ok(harness, parent_id, "one", "two")
    _wait_reports(harness, parent_id, 2)
    for worker in workers:
        _wait_worker_idle(harness, worker["id"])

    closed = harness.client.post(
        f"/api/sessions/{parent_id}/close", headers=harness.auth
    )

    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    for worker in workers:
        row = _session(harness, worker["id"])
        assert row["status"] == "closed"
        assert row["closed_reason"] == "user"
    frames = _read_sse(harness, parent_id, until=_count("agent_closed", 2))
    assert {c["worker_id"] for c in _payloads(frames, "agent_closed")} == {
        w["id"] for w in workers
    }


def test_a_worker_can_be_closed_on_its_own(harness: Harness) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    workers = _spawn_ok(harness, parent_id, "one", "two")
    _wait_reports(harness, parent_id, 2)
    keep, drop = workers[0]["id"], workers[1]["id"]
    _wait_worker_idle(harness, drop)

    response = harness.client.post(
        f"/api/sessions/{parent_id}/agents/{drop}/close", headers=harness.auth
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    assert _session(harness, keep)["status"] == "idle"
    assert _session(harness, parent_id)["status"] == "idle"
    # and the alias is the same thing as closing the worker by its own id
    assert [w["status"] for w in _agents(harness, parent_id)] == ["idle", "closed"]


# --------------------------------------------------------------------------
# 5: caps
# --------------------------------------------------------------------------
def test_a_spawn_over_the_creation_cap_creates_nothing(harness: Harness) -> None:
    """All of them or none: a half-spawned set is worse than a refusal.

    The pool is FILLED first rather than asking for more than the default
    allows: the default now covers a whole spawn (see
    ``test_a_full_spawn_is_accepted_on_stock_defaults``), so a spawn is only
    over the cap when somebody else is already cloning.
    """
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    free = runner_module.DEFAULT_MAX_CONCURRENT_CREATIONS - 1
    harness.hold_worker()
    try:
        held = [_create(harness) for _ in range(free)]
        assert [r.status_code for r in held] == [201] * free

        response = _spawn(harness, parent_id, *[f"task {i}" for i in range(2)])

        assert response.status_code == 429, response.text
        assert "creations" in response.json()["detail"]
        assert _agents(harness, parent_id) == []
    finally:
        harness.release_worker()
    for response in held:
        _wait_status(harness, response.json()["id"], {"idle"})


def test_a_spawn_over_the_worker_cap_creates_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_WORKERS_PER_SESSION", "1")
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    response = _spawn(harness, parent_id, "a", "b")

    assert response.status_code == 429, response.text
    assert "MAX_WORKERS_PER_SESSION" in response.json()["detail"]
    assert _agents(harness, parent_id) == []
    assert runner_module.max_workers_per_session() == 1


def test_a_worker_cannot_spawn_workers(harness: Harness) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    worker_id = _spawn_ok(harness, parent_id, "one")[0]["id"]
    _wait_reports(harness, parent_id, 1)

    response = _spawn(harness, worker_id, "and one more")

    assert response.status_code == 409, response.text
    assert "worker" in response.json()["detail"]


def test_spawn_validates_its_body(harness: Harness) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    assert _spawn(harness, parent_id).status_code == 422
    assert _spawn(harness, parent_id, "   ").status_code == 422
    assert _spawn(harness, parent_id, "a", "b", "c", "d", "e").status_code == 422
    assert _spawn(harness, "no-such-session", "a").status_code == 404


# --------------------------------------------------------------------------
# 6: first_message
# --------------------------------------------------------------------------
def test_first_message_starts_the_first_turn_by_itself(harness: Harness) -> None:
    """Create-and-send in one call, the same path a spawned worker takes."""
    harness.set_script(list(DEFAULT_SCRIPT))

    response = _create(harness, first_message="append to the readme")

    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    frames = _read_sse(harness, session_id, until=_turn_complete)
    started = _payloads(frames, "turn_started")
    assert started and started[0]["content"] == "append to the readme"
    assert [m["role"] for m in _messages(harness, session_id)] == ["user", "agent"]
    assert _wait_status(harness, session_id, {"idle"})["turns"] == 1


def test_a_blank_first_message_is_a_422(harness: Harness) -> None:
    assert _create(harness, first_message="   ").status_code == 422


# --------------------------------------------------------------------------
# 7: /spawn from the chat box
# --------------------------------------------------------------------------
def test_a_spawn_message_spawns_instead_of_starting_a_turn(
    harness: Harness,
) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    response = _post_message(
        harness, parent_id, "/spawn fix the parser\n/spawn write the tests"
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["delivery"] == "spawned"
    assert body["message"]["role"] == "system"
    workers = _agents(harness, parent_id)
    assert [w["task"] for w in workers] == ["fix the parser", "write the tests"]
    for worker in workers:
        assert worker["id"] in body["message"]["content"]
    # no turn was started for the parent: it never built a model at all
    assert parent_id not in harness.models
    assert _session(harness, parent_id)["turns"] == 0
    roles = [m["role"] for m in _messages(harness, parent_id)]
    assert roles[:2] == ["user", "system"]


def test_a_single_line_spawn_command_works_too(harness: Harness) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    response = _post_message(harness, parent_id, "/spawn   fix the parser  ")

    assert response.status_code == 202, response.text
    assert response.json()["delivery"] == "spawned"
    assert [w["task"] for w in _agents(harness, parent_id)] == ["fix the parser"]


def test_a_half_spawn_message_is_refused_rather_than_run(harness: Harness) -> None:
    """"/spawn x" plus prose is neither a command nor a prompt — say so."""
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    response = _post_message(harness, parent_id, "/spawn fix it\nand also tidy up")

    assert response.status_code == 400, response.text
    assert "/spawn" in response.json()["detail"]
    assert _agents(harness, parent_id) == []
    assert parent_id not in harness.models


def test_a_message_that_only_mentions_spawn_is_an_ordinary_turn(
    harness: Harness,
) -> None:
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)

    response = _post_message(harness, parent_id, "tell me what /spawn does")

    assert response.status_code == 202, response.text
    assert response.json()["delivery"] == "turn_started"
    assert _agents(harness, parent_id) == []


# --------------------------------------------------------------------------
# 6: real parallelism
# --------------------------------------------------------------------------
def test_a_parents_workers_think_at_the_same_time(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three workers, three model calls in flight at once — not a queue.

    Worker turns used to draw on ``MAX_CONCURRENT_SESSIONS`` (default 3), the
    same pool as the humans' own turns, so "spawn four" could not mean four
    agents working. The primary pool is squeezed to ONE here: if a worker turn
    still took a slot from it, only one of the three would ever reach its
    model and the gate below would time out.

    Overlap is proved twice over — every worker is inside a model call at the
    same instant, and afterwards their ``turn_started``/``turn_finished``
    wall-clock windows are asserted to intersect pairwise.
    """
    manager = chat.deps.get_manager()
    monkeypatch.setattr(manager, "_max_concurrent", 1, raising=False)
    harness.set_script([_reply("nothing to change.")])
    parent_id = _create_idle(harness)
    # every model built from here on stops inside its first call
    harness.block_model_at(1)

    workers = _spawn_ok(harness, parent_id, "one", "two", "three")
    worker_ids = [w["id"] for w in workers]
    for worker_id in worker_ids:
        harness.wait_blocked(worker_id)

    # the instant of proof: three worker turns, three blocked model calls
    assert manager.running_worker_count == 3
    assert manager.running_count == 0, "no worker took a primary turn slot"
    harness.release_model()

    frames = _wait_reports(harness, parent_id, 3)
    spans = _turn_spans(frames, worker_ids)
    assert len(spans) == 3, f"expected three worker turns, saw {sorted(spans)}"
    for (one, first), (two, second) in itertools.combinations(spans.items(), 2):
        assert first[0] < second[1] and second[0] < first[1], (
            f"workers {one} and {two} did not overlap: {first} vs {second}"
        )


def _turn_spans(
    frames: list[dict], worker_ids: list[str]
) -> dict[str, tuple[float, float]]:
    """``{worker_id: (started_at, finished_at)}`` off the parent's own stream."""
    started: dict[str, float] = {}
    finished: dict[str, float] = {}
    for frame in frames:
        data = frame["payload"]["data"]
        worker_id = data.get("agent_id")
        if worker_id not in worker_ids:
            continue
        timestamp = float(frame["payload"]["timestamp"])
        if frame["event"] == "turn_started":
            started.setdefault(worker_id, timestamp)
        elif frame["event"] == "turn_finished":
            finished[worker_id] = timestamp
    return {
        worker_id: (started[worker_id], finished[worker_id])
        for worker_id in started
        if worker_id in finished
    }


def test_a_full_spawn_is_accepted_on_stock_defaults(harness: Harness) -> None:
    """``MAX_TASKS_PER_SPAWN`` workers is a number the defaults can serve.

    It was not: the creation cap was 3 against a spawn limit of 4, and a spawn
    takes its slots all-or-nothing, so the largest spawn the API advertises
    was refused 429 on every stock deployment before any work began.
    """
    harness.set_script([_reply("nothing to do.")])
    parent_id = _create_idle(harness)

    workers = _spawn_ok(harness, parent_id, *[f"task {i}" for i in range(4)])

    assert len(workers) == MAX_TASKS_PER_SPAWN
    assert runner_module.max_concurrent_worker_turns() >= MAX_TASKS_PER_SPAWN
    assert runner_module.DEFAULT_MAX_CONCURRENT_CREATIONS >= MAX_TASKS_PER_SPAWN
    _wait_reports(harness, parent_id, 4)


def test_external_agents_do_not_spend_the_worker_budget(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """They run nothing, so they cost nothing — not even a slot in the cap."""
    monkeypatch.setenv("MAX_WORKERS_PER_SESSION", "1")
    harness.set_script(list(DEFAULT_SCRIPT))
    parent_id = _create_idle(harness)
    for label in ("claude code", "codex"):
        registered = harness.client.post(
            f"/api/sessions/{parent_id}/external-agents",
            json={"agent_kind": "other", "label": label},
            headers=harness.auth,
        )
        assert registered.status_code == 201, registered.text

    response = _spawn(harness, parent_id, "the one worker this session may have")

    assert response.status_code == 202, response.text
    assert len(_agents(harness, parent_id)) == 3
