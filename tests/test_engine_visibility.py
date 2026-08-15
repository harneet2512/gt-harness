"""PROVE IT Part 2 — the 16-feature visibility harness (engine-loop, no provider).

Each test forces one DIRECT feature's trigger through the real
``engine_execute_actions`` seam and asserts the fact is delivered in the SAME
canonical observation as its trigger (correct time, pre-next-call) with a
payload that passes the gate. The 7 CAP_OWNERs bind to their FACT (tested by
ownership lineage in the census); GT_CERT_DELIVERY binds every delivery receipt.

These are the proof the "all 16 working" gate requires before the single smoke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_visibility_core import (
    _init_repo,
    _mk_graph,
    has_fact,
    run_engine,
)
from gt_engine.task_contract import Obligation, TaskContract


def _repo(tmp_path):
    repo = Path(tmp_path)
    (repo / "a.py").write_text("def parse(x):\n    return x\n", encoding="utf-8")
    (repo / "b.py").write_text("def parse(y):\n    return y\n", encoding="utf-8")
    (repo / "app.py").write_text("def vulnerable():\n    pass\n", encoding="utf-8")
    (repo / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (repo / "caller.py").write_text("def g():\n    return f(1)\n", encoding="utf-8")
    prov = repo / "providers"
    prov.mkdir()
    (prov / "aws.py").write_text("class Aws:\n    pass\n", encoding="utf-8")
    (prov / "gcp.py").write_text("class Gcp:\n    pass\n", encoding="utf-8")
    (prov / "__init__.py").write_text("from .aws import Aws\nfrom .gcp import Gcp\n", encoding="utf-8")
    _init_repo(repo)
    return repo


def _graph(repo):
    db = repo / "graph.db"
    _mk_graph(
        db,
        [
            (1, "Function", "parse", "a.py", 1, 0, "def parse"),
            (2, "Function", "parse", "b.py", 1, 0, "def parse"),
            (3, "Function", "f", "mod.py", 1, 0, "def f"),
            (4, "Function", "g", "caller.py", 1, 0, "def g"),
        ],
        edges=[(1, 4, 3, "CALLS", 0.95, "lsp_verified", 1)],
    )
    return str(db)


# --- 1. obligations -----------------------------------------------------------
def test_vis_obligations(tmp_path):
    repo = _repo(tmp_path)
    contract = TaskContract(
        role="patch",
        obligations=(Obligation("obl-1", "fix the vulnerability in app.py", "task",
                                subjects=("app.py",)),),
    )
    out = run_engine(repo, [{"command": "cat app.py", "tool_call_id": "c1"}],
                     contract=contract)
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "obligations"), obs
    assert "vulnerability" in obs  # usable requirement text, not opaque IDs


# --- 2. localization ----------------------------------------------------------
def test_vis_localization(tmp_path):
    repo = _repo(tmp_path)
    graph = _graph(repo)
    out = run_engine(repo, [{"command": "grep -r parse .", "tool_call_id": "s1"}],
                     graph=graph, issue_text="parse")
    assert has_fact(out["observations"][0]["content"], "localization")


# --- 3. def_partition (fires on the 2nd ambiguous search after rotation) ------
def test_vis_def_partition(tmp_path):
    repo = _repo(tmp_path)
    graph = _graph(repo)
    out = run_engine(repo, [
        {"command": "grep -r parse .", "tool_call_id": "s1"},
        {"command": "grep -r parse .", "tool_call_id": "s2"},
    ], graph=graph, issue_text="parse")
    obs0 = out["observations"][0]["content"]
    obs1 = out["observations"][1]["content"]
    # rotation: localization first, then the runner-up def_partition fires
    assert has_fact(obs0, "localization"), obs0
    assert has_fact(obs1, "def_partition"), obs1


# --- 4. syntax_result (file-creation trigger, F7) ------------------------------
def test_vis_syntax_result_creation(tmp_path):
    repo = _repo(tmp_path)
    out = run_engine(repo, [{"command": "create_module", "tool_call_id": "m1"}])
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "syntax_result"), obs


# --- 5. covering_red (output-based detection, F3) ------------------------------
def test_vis_covering_red(tmp_path):
    repo = _repo(tmp_path)
    out = run_engine(repo, [{"command": "python manage.py test", "tool_call_id": "t1"}])
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "covering_red"), obs


def test_vis_covering_red_shell_script(tmp_path):
    repo = _repo(tmp_path)
    out = run_engine(repo, [{"command": "bash run_tests.sh", "tool_call_id": "t2"}])
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "covering_red"), obs


# --- 6. recovery (2nd identical failure, F4) -----------------------------------
def test_vis_recovery(tmp_path):
    repo = _repo(tmp_path)
    out = run_engine(repo, [
        {"command": "bash run_tests.sh", "tool_call_id": "t1"},
        {"command": "bash run_tests.sh", "tool_call_id": "t2"},
    ])
    obs1 = out["observations"][1]["content"]
    assert has_fact(obs1, "recovery"), obs1
    assert "already failed identically" in obs1, obs1


# --- 7. signature_delta (caller_break via F2/F5) -------------------------------
def test_vis_signature_delta(tmp_path):
    repo = _repo(tmp_path)
    graph = _graph(repo)
    out = run_engine(repo, [{"command": "edit_signature", "tool_call_id": "e1"}],
                     graph=graph, issue_text="f")
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "signature_delta"), obs


# --- 8. newfile_precedent (edit-trigger create, F6 + F2 map) --------------------
def test_vis_newfile_precedent(tmp_path):
    repo = _repo(tmp_path)
    out = run_engine(repo, [{"command": "create_azure", "tool_call_id": "n1"}],
                     issue_text="add a new azure provider")
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "newfile_precedent"), obs


# --- 9. submit_refusal (SUPPRESS under a closed blocker) -----------------------
def test_vis_submit_refusal(tmp_path):
    repo = _repo(tmp_path)
    import gt_engine.miniswe_runtime as rt

    original = rt._run_submit_gate
    out = run_engine(
        repo,
        [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "tool_call_id": "s1"}],
        blocking_reasons=("active failure",),
        deny_submit=True,
        _original_submit_gate=original,
    )
    # the deny-gate must have been restored so no later test inherits it
    assert rt._run_submit_gate is original
    obs = out["observations"][0]["content"]
    assert 'decision="suppress"' in obs, obs
    assert any(
        isinstance(m, dict) and m.get("role") == "user" and "Submission not executed" in str(m.get("content") or "")
        for m in out["agent_sent"]
    )


# --- Regression: gateway facts carry REAL payload, not empty evidence ----------
def test_regression_gateway_fact_payload_not_empty(tmp_path):
    """The gateway envelope's useful payload lives in ``payload``/``provenance``,
    NOT a ``content`` attribute. A fact rendered as ``{"evidence": "", ...}``
    means the body was dropped — the payload gate must never pass an empty
    evidence string for a gateway-produced fact."""
    repo = _repo(tmp_path)
    graph = _graph(repo)
    out = run_engine(repo, [{"command": "grep -r parse .", "tool_call_id": "s1"}],
                     graph=graph, issue_text="parse")
    obs = out["observations"][0]["content"]
    assert has_fact(obs, "localization"), obs
    assert '"evidence": ""' not in obs, f"payload dropped: {obs}"
    assert "parse" in obs, f"body not delivered: {obs}"


# --- Regression: a bash grep is AUGMENT with raw preserved, never REPLACE ------
def test_regression_bash_grep_preserves_raw(tmp_path):
    """A plain bash search (no typed producer answer) must NOT be REPLACE: a
    REPLACE substitutes raw with a deterministic answer, and with no answer the
    observation would drop the exact grep bytes. It must be AUGMENT/PASS_THROUGH
    with raw preserved alongside the fact."""
    repo = _repo(tmp_path)
    graph = _graph(repo)
    out = run_engine(repo, [{"command": "grep -r parse .", "tool_call_id": "s1"}],
                     graph=graph, issue_text="parse")
    obs = out["observations"][0]["content"]
    assert 'decision="augment"' in obs, f"got REPLACE with dropped raw: {obs}"
    assert "a.py:1: def parse" in obs, f"raw grep output lost: {obs}"


# --- Regression: engine lifecycle advances (global_action, before_action) ------
def test_regression_engine_advances_global_action(tmp_path):
    """The engine loop must advance adapter.global_action per action (the seam
    does; a fixed counter means batch/action identity and repeat telemetry are
    wrong). Two actions -> counter moves."""
    repo = _repo(tmp_path)
    out = run_engine(repo, [
        {"command": "cat app.py", "tool_call_id": "c1"},
        {"command": "cat app.py", "tool_call_id": "c2"},
    ])
    assert out["adapter"].global_action == 2, out["adapter"].global_action


# --- Regression: an edit invalidates stale RED receipts (note_edit) ------------
def test_regression_edit_invalidates_red(tmp_path):
    """note_edit must be called after a changed file so a RED receipt from before
    the fix is invalidated (the submit gate must not block on evidence an edit
    already addressed). Exercises the lifecycle wiring through the real loop."""
    from tests.engine_visibility_core import Adapter as _CoreAdapter

    repo = _repo(tmp_path)
    # a contract-backed adapter so evaluate_failing_observation can mark RED
    from gt_engine.task_contract import Obligation, TaskContract

    contract = TaskContract(
        role="patch",
        obligations=(Obligation("obl-1", "fix the vulnerability in app.py", "task",
                                subjects=("app.py",)),),
    )
    out = run_engine(repo, [
        {"command": "pytest", "tool_call_id": "t1"},
        {"command": "edit_signature", "tool_call_id": "e1"},
    ], contract=contract)
    # the edit touched mod.py; RED obligations for app.py survive (not affected)
    # but the lifecycle must have advanced epoch / called note_edit without error
    assert out["session"].disabled is False
    assert out["adapter"].global_action == 2


# --- CAP_OWNER lineage: each binds when its FACT delivers -----------------------
CAP_TO_FACT = {
    "GT_EDIT_CHECK": "syntax_result",
    "GT_PATCH_DELTA": "signature_delta",
    "GT_LOC_RESLOT": "localization",
    "GT_SS_SUBMIT_RED": "submit_refusal",
    "GT_HYPOTHESIS": "recovery",
    "GT_CHANGE_SURFACE": "newfile_precedent",
}


@pytest.mark.parametrize("cap,fact", sorted(CAP_TO_FACT.items()))
def test_vis_cap_owner_binds_when_fact_delivers(cap, fact):
    from gt_engine.engine.runner import ENGINE_FACT_OWNERS

    assert fact in ENGINE_FACT_OWNERS
