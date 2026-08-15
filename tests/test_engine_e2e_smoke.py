"""ENGINE-mode real-seam end-to-end smoke tests (provider-free, scripted).

Drives the REAL DefaultAgent loop with the REAL MiniSweAdapter + real
install_runtime_hooks in GTMode.ENGINE — the exact production code path the
paid smoke runs — and asserts the review's three defect guards hold through the
real seam:
  * gateway facts carry NON-EMPTY payload (bug-1)
  * bash actions preserve raw output (bug-2)
  * lifecycle advances: global_action, note_edit RED invalidation (bug-3)
plus the closed-blocker registration that feeds submit_refusal.

A ScriptedModel cannot attach the real provider boundary (production uses
GroundTruthLitellmModel), so submit SUPPRESS is exercised via a boundary-enabled
fake adapter mirror in the submit-while-RED scenario.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# minisweagent prints a rich banner; on Windows cp1252 stdout that raises.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from engine_smoke_e2e import (  # noqa: E402
    TASK,
    ScriptedEnv,
    ScriptedModel,
    build_engine_run,
    build_engine_run_submit_red,
)


def _model_observations(agent) -> list[str]:
    seen: list[str] = []

    orig = agent.model._prepare_messages_for_api

    def spy(messages):
        for item in messages:
            if item.get("role") == "tool":
                seen.append(str(item.get("content") or ""))
        return orig(messages)

    agent.model._prepare_messages_for_api = spy
    return seen


def _facts_and_raw(observation: str):
    facts = re.findall(r'<fact owner="([^"]+)"', observation)
    decisions = re.findall(r'decision="([^"]+)"', observation)
    has_raw_after = "</result>\n" in observation
    return facts, decisions, has_raw_after


def test_e2e_engine_renders_no_internal_ids(tmp_path):
    """Gap-1 gate: NO internal harness identifiers may appear in any
    model-visible observation. Round-8 evidence: the model read
    `matched: ["obl-<sha>"]` / `pred-<sha>` and spent 27-35 actions
    reverse-engineering gt_engine/ source (token blowup +3324%..+3842%).
    Rendered bytes may carry task text/anchors, never harness internals."""
    agent, adapter, graph_db, root = build_engine_run_submit_red()
    from groundtruth.runtime.miniswe_provider_boundary import (
        MiniSweProviderBoundary,
    )
    import os

    os.environ["GT_SUBMIT_SUPPRESSION_ENFORCE"] = "1"
    adapter.provider_boundary = MiniSweProviderBoundary(
        model=agent.model, agent=agent, fault_handler=lambda stage, exc: None,
    )
    seen = _model_observations(agent)
    agent.run(TASK)

    forbidden = re.compile(
        r"obl-[0-9a-f]{6,}|pred-[0-9a-f]{6,}|gt_engine|site-packages|"
        r"miniswe_runtime|miniswe_integration|task_contract\.py|"
        r"verification_contract\.py|gt_session\.py|engine/runner|"
        r"miniswe_controller|\.gt-state|gt-state"
    )
    violations = [
        (i, s[:200]) for i, s in enumerate(seen)
        if forbidden.search(s)
    ]
    assert not violations, f"internal IDs leaked into model-visible bytes: {violations}"


def test_e2e_engine_runs_and_delivers_payload(tmp_path):
    agent, adapter, graph_db, root = build_engine_run()
    seen = _model_observations(agent)
    agent.run(TASK)

    assert adapter.phase == "FINISHED"
    assert adapter.unmet_predicates == ()
    assert adapter.iteration == 6
    assert adapter.global_action == 6
    assert adapter.workspace_epoch == 2  # edit bumped epoch -> stale RED invalidated
    assert adapter.repository_revision  # snapshot seeding populated it

    # find the observations with facts and assert payload + raw preservation
    fact_obs = [s for s in seen if "<fact owner=" in s]
    assert fact_obs, "no fact-bearing observation was delivered"
    for obs in fact_obs:
        facts, decisions, has_raw = _facts_and_raw(obs)
        assert facts, obs
        assert '"evidence": ""' not in obs, f"empty payload leaked: {obs}"
        assert all(d in ("augment", "pass_through") for d in decisions), (
            f"REPLACE with dropped raw: {obs}"
        )
        assert has_raw, f"raw output dropped after fact: {obs}"

    # covering_red is graph-independent (execution evidence) and MUST deliver
    # with real payload + raw on the failing test. localization is graph-backed
    # (needs the gt-index binary); its real-seam delivery is proven by the
    # visibility suite against a synthetic graph, so only assert it when the
    # indexer produced a graph in this environment.
    blob = "\n".join(fact_obs)
    assert "covering_red" in blob, f"covering_red not delivered: {blob}"
    assert "outcome" in blob, f"covering payload missing: {blob}"
    assert "1 failed" in blob, f"raw test output not preserved: {blob}"
    if graph_db:
        assert "localization" in blob, f"localization not delivered with graph: {blob}"
        assert "src/mod.py:1:compute" in blob  # localization body line


def test_e2e_engine_preserves_raw_on_bash(tmp_path):
    agent, adapter, graph_db, root = build_engine_run()
    seen = _model_observations(agent)
    agent.run(TASK)

    # the failing-test observation is graph-independent (execution evidence):
    # it must carry covering_red AND the exact raw pytest output after the
    # </result> block (bug-2 guard: no REPLACE dropping raw).
    red_obs = next(
        (s for s in seen if "<fact owner=\"covering_red\"" in s), None
    )
    assert red_obs is not None, "no covering_red observation delivered"
    assert "tests/test_mod.py::test_compute FAILED" in red_obs, (
        f"raw test output not preserved in the same observation: {red_obs}"
    )


def test_e2e_engine_red_invalidated_on_edit(tmp_path):
    agent, adapter, graph_db, root = build_engine_run()
    agent.run(TASK)
    import json

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(r.get("event") == "semantic_red" for r in rows)
    assert any(r.get("event") == "red_invalidated_by_edit" for r in rows)
    assert any(r.get("event") == "failure_recorded" for r in rows)
    # the failing test's closed blocker was registered (feeds submit_refusal)
    assert any(
        bool(r.get("blocker_id")) and r.get("event") == "failure_recorded"
        for r in rows
    )


def test_e2e_engine_submit_while_red_blocks_then_recovers(tmp_path):
    """With a provider boundary present, a submit under a fresh RED closed
    blocker must SUPPRESS (submit_refusal), then after the fix + green test the
    second submit is accepted."""
    agent, adapter, graph_db, root = build_engine_run_submit_red()

    # Install the REAL MiniSweProviderBoundary so authorize_submit_suppression
    # runs its real deterministic zero-delivery path (production attaches it to
    # GroundTruthLitellmModel; a ScriptedModel cannot, so we attach it here).
    from groundtruth.runtime.miniswe_provider_boundary import (
        MiniSweProviderBoundary,
    )

    boundary = MiniSweProviderBoundary(
        model=agent.model,
        agent=agent,
        fault_handler=lambda stage, exc: None,
    )
    adapter.provider_boundary = boundary
    import os

    os.environ["GT_SUBMIT_SUPPRESSION_ENFORCE"] = "1"

    agent.run(TASK)

    assert adapter.provider_boundary is not None
    suppressions = getattr(
        adapter.provider_boundary, "_submit_suppression_receipts", ()
    )
    # EXACTLY the submit-while-RED is suppressed; after the edit + green test
    # the revision advances and the final submit is accepted.
    assert len(suppressions) == 1, (
        f"expected exactly one suppression, got {len(suppressions)}"
    )
    assert adapter.phase == "FINISHED", f"stuck at {adapter.phase}"
    assert adapter.unmet_predicates == ()
    import json

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    suppress = [r for r in rows if r.get("event") == "submit_refusal"]
    assert len(suppress) == 1, f"expected one refusal, got {len(suppress)}"
    decisions = [r for r in rows if r.get("event") == "submit_decision"]
    assert any(d.get("accepted") for d in decisions), "final submit not accepted"
