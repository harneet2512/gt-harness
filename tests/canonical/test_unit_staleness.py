"""C5: admitted context units carry an internal source_revision, and
``is_stale``/``unit_state`` report it against EngineState — without
changing what is admitted, sent, or demoted.
"""

from __future__ import annotations

from gt_engine.gt_session import GTDecisionCandidate, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter


def _adapter(tmp_path):
    return MiniSweAdapter(task_id="staleness", state_dir=tmp_path, predicates=[])


def _unit_candidate(revision: str) -> GTDecisionCandidate:
    return GTDecisionCandidate(
        rendered="observed fact at rev-a",
        kind="syntax_result",
        dedup_key="staleness-fact",
        supersession_key="syntax_result:staleness-fact",
        source_revision=revision,
        action_index=1,
    )


def _admit_unit(session: GTSession, candidate: GTDecisionCandidate) -> str:
    session.admit_decision_packet([candidate], iteration=0, action_index=1)
    pending = tuple(session._pending_context_units)
    assert pending, "candidate did not stage a context unit"
    session.provider_request_admitted(pending)
    (record,) = session._active_context_units.values()
    return record["unit_id"]


def test_admitted_unit_is_stale_after_a_workspace_edit(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.engine_state.bind_initial_source("rev-a")
    session = GTSession(GTSessionConfig(task_id="staleness"), engine=adapter)

    unit_id = _admit_unit(session, _unit_candidate("rev-a"))

    state = session.unit_state(unit_id)
    assert state is not None
    assert state["source_revision"] == "rev-a"
    assert state["workspace_revision"] == "rev-a"
    assert state["is_stale"] is False
    assert session.is_stale(unit_id) is False

    # The real edit path: an observed workspace change advances the
    # EngineState source_revision. No bytes move anywhere.
    adapter.engine_state.mark_paths_dirty(("src/x.py",), revision="rev-b")

    state = session.unit_state(unit_id)
    assert state["workspace_revision"] == "rev-b"
    assert state["source_revision"] == "rev-a"
    assert state["is_stale"] is True
    assert session.is_stale(unit_id) is True


def test_is_stale_is_none_for_unknown_or_undecidable_units(tmp_path):
    adapter = _adapter(tmp_path)
    session = GTSession(GTSessionConfig(task_id="staleness"), engine=adapter)

    assert session.is_stale("never-admitted") is None
    assert session.unit_state("never-admitted") is None

    # Unit admitted before any workspace revision exists: the comparison is
    # undecidable, reported as None rather than guessed fresh or stale.
    unit_id = _admit_unit(session, _unit_candidate("rev-a"))
    assert session.is_stale(unit_id) is None
    state = session.unit_state(unit_id)
    assert state["source_revision"] == "rev-a"
    assert state["workspace_revision"] == ""
    assert state["is_stale"] is None
