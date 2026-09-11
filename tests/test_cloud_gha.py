"""``/gha`` — GitHub Actions as the compute substrate (HAR-84).

A ``/gha <task>`` message registers an external agent (a worker we do not
run) and dispatches the repo's ``agent-turn.yml`` workflow to fill it.
The run posts its own events through the ingest contract like any other
external agent — the only thing this surface owns is the dispatch.

FAKE BOUNDARY: ``cloud.server.gha.dispatch_run`` — the one outbound call
to GitHub's API. Everything else is real: the FastAPI app on a loopback
port, JWT auth, the store, the agent row, the stream.

Run: ``python -m pytest tests/test_cloud_gha.py -q`` from the repo root.
"""
from __future__ import annotations

import pytest

from cloud.server import gha
from tests import test_cloud_chat as chat
from tests.test_cloud_chat import Harness, _create_idle
from tests.test_cloud_external_agents import _agents

_HARNESS_IMPL = getattr(chat.harness, "__wrapped__", chat.harness)


@pytest.fixture(scope="session")
def seed_repo(tmp_path_factory):
    return chat._make_seed_repo(tmp_path_factory.mktemp("seed"))


@pytest.fixture
def harness(seed_repo, tmp_path, monkeypatch):
    yield from _HARNESS_IMPL(seed_repo, tmp_path, monkeypatch)


def _post_gha(h: Harness, session_id: str, content: str):
    return h.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": content},
        headers=h.auth,
    )


def test_gha_task_runs_in_actions(harness: Harness, monkeypatch) -> None:
    """The whole point: the task leaves this process entirely. A run is
    dispatched; the agent row waits for the runner's own ingest events."""
    parent_id = _create_idle(harness)
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)

    monkeypatch.setenv("GHA_TOKEN", "ghp_test")
    monkeypatch.setenv("GHA_REPO", "harneet2512/gt-harness")
    monkeypatch.setattr(gha, "dispatch_run", fake_dispatch)

    response = _post_gha(harness, parent_id, "/gha fix the readme")

    assert response.status_code == 202, response.text
    assert response.json()["delivery"] == "gha"
    assert len(calls) == 1
    call = calls[0]
    assert call["task"] == "fix the readme"
    assert call["ingest_url"].endswith(f"/external-agents/{call['agent_id']}/events")
    assert call["ingest_token"]

    agents = _agents(harness, parent_id)
    gha_agent = next(a for a in agents if a.get("agent_kind") == "gha")
    assert gha_agent["task"] == "fix the readme"


def test_gha_without_token_fails_closed(harness: Harness, monkeypatch) -> None:
    """No dispatch credential: the request 400s and the registered row is
    closed with the honest reason — never left sitting 'working'."""
    parent_id = _create_idle(harness)
    monkeypatch.delenv("GHA_TOKEN", raising=False)
    monkeypatch.delenv("GHA_REPO", raising=False)

    response = _post_gha(harness, parent_id, "/gha fix it")

    assert response.status_code == 400, response.text
    agents = _agents(harness, parent_id)
    assert len(agents) == 1
    assert agents[0]["status"] == "failed"


def test_gha_needs_a_task(harness: Harness) -> None:
    parent_id = _create_idle(harness)

    response = _post_gha(harness, parent_id, "/gha")

    assert response.status_code == 400, response.text
    assert _agents(harness, parent_id) == []


def test_gha_is_one_line(harness: Harness) -> None:
    parent_id = _create_idle(harness)

    response = _post_gha(harness, parent_id, "/gha fix it\nand also this")

    assert response.status_code == 400, response.text


def test_a_normal_message_still_reaches_the_model(
    harness: Harness, monkeypatch
) -> None:
    """The command is strict: a message merely containing /gha is a message."""
    parent_id = _create_idle(harness)
    monkeypatch.setenv("GHA_TOKEN", "ghp_test")

    response = _post_gha(harness, parent_id, "does /gha fix it work?")

    assert response.status_code == 202, response.text
    assert response.json()["delivery"] != "gha"
