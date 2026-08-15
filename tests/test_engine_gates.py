"""ENGINE gates — provider-free checks that prevent the class of bug that broke
the witness run: journal-schema corruption, external-looking GT labels in model
bytes, and empty postflight payloads."""
from __future__ import annotations

import json
import subprocess

import pytest

from gt_engine.engine.contracts import (
    ActionKind,
    ActionRequest,
    Decision,
    EvidenceArtifact,
    Fidelity,
    InterceptionDecision,
)
from gt_engine.engine.observe import compile_observation
from gt_engine.engine.runner import (
    _covering_red_artifact,
    _git_changed_py,
    _syntax_artifact,
)
from gt_engine.event_journal import verify_event_journal


def _request():
    return ActionRequest(
        action_id="call_1", kind=ActionKind.SHELL, arguments={},
        literal_shell_form="pytest tests", snapshot_token="tok-1",
        configuration_digest="cfg-1", requested_fidelity=Fidelity.RAW,
    )


def _fact(owner="syntax_result", model_visible=True, content=None):
    return EvidenceArtifact(
        artifact_id="ev-1", owner=owner, semantics="syntax",
        content=content or {"file": "src/x.py", "ok": True},
        anchors=("src/x.py:1",), producer="py_ast", producer_version="1",
        freshness_revision="rev-9", coverage="complete",
        model_visible=model_visible,
    )


# --- Gate 1: engine_delivery events must keep the journal valid ------------


def test_engine_delivery_journal_schema_valid(tmp_path):
    """The runner's engine_delivery append must not break the tamper chain.

    Regression: passing schema='gt.engine.delivery_receipt.v1' OVERRIDES
    ExternalStateStore's forced gt.event.v1 and made verify_event_journal
    report 'unsupported or missing schema' (research_valid=false in the
    witness run)."""
    from gt_engine.miniswe_integration import ExternalStateStore

    store = ExternalStateStore(tmp_path, "task-x")
    store.append(
        "engine_delivery",
        delivery_id="d-0001",
        action_id="call_1",
        decision="pass_through",
        final_observation_sha256="a" * 64,
    )
    receipt = store.receipt()
    verification = verify_event_journal(
        store.path,
        event_count=receipt["event_count"],
        event_head=receipt["event_head"],
    )
    assert verification.valid, verification.issues


def test_schema_override_breaks_journal_documented(tmp_path):
    """The trap is real: a payload schema kwarg corrupts the chain."""
    from gt_engine.miniswe_integration import ExternalStateStore

    store = ExternalStateStore(tmp_path, "task-x")
    store.append(
        "engine_delivery",
        schema="gt.engine.delivery_receipt.v1",  # the bug that shipped
        delivery_id="d-0001",
    )
    verification = verify_event_journal(store.path)
    assert not verification.valid
    assert any("unsupported or missing schema" in i for i in verification.issues)


# --- Gate 2: model-visible bytes carry no external 'GT' framing -------------


def test_observation_render_has_no_gt_sentinels():
    """The engine's model bytes must never say 'gt-engine'/'gt-fact'/'GT_'.
    External labeling makes the model treat the bytes as out-of-band info."""
    observation = compile_observation(
        _request(),
        InterceptionDecision(decision=Decision.AUGMENT, reason="postflight"),
        raw_result="tests passed",
        evidence=(_fact(),),
        receipt_id="rcpt-1",
    )
    rendered = observation.render()
    lowered = rendered.lower()
    assert "gt-engine" not in lowered
    assert "gt-fact" not in lowered
    assert "gt_" not in lowered
    assert "gt_" not in rendered
    # raw preserved exactly, facts present in a neutral block
    assert "tests passed" in rendered
    assert "<result" in rendered and "</result>" in rendered
    assert "src/x.py" in rendered
    assert 'decision="augment"' in rendered


# --- Gate 3: postflight producers emit real facts ----------------------------


def test_syntax_artifact_positive(tmp_path):
    path = tmp_path / "good.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    artifact = _syntax_artifact(str(path), str(tmp_path))
    assert artifact is not None
    assert artifact.owner == "syntax_result"
    assert artifact.content["ok"] is True
    assert artifact.model_visible
    assert artifact.coverage == "complete"


def test_syntax_artifact_reports_error(tmp_path):
    path = tmp_path / "bad.py"
    path.write_text("def f(:\n", encoding="utf-8")
    artifact = _syntax_artifact(str(path), str(tmp_path))
    assert artifact is not None
    assert artifact.content["ok"] is False
    assert "line" in artifact.content["detail"].lower()


def test_syntax_artifact_omits_non_python(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("anything", encoding="utf-8")
    assert _syntax_artifact(str(path), str(tmp_path)) is None


def test_covering_red_for_test_command():
    artifact = _covering_red_artifact("pytest tests/", "1 passed", 0)
    assert artifact is not None
    assert artifact.owner == "covering_red"
    assert artifact.content["outcome"] == "passed"
    failed = _covering_red_artifact("pytest tests/", "1 failed", 1)
    assert failed.content["outcome"] == "failed"


def test_covering_red_absent_for_plain_read():
    assert _covering_red_artifact("cat src/x.py", "content", 0) is None


def test_git_changed_py_detects_edits(tmp_path):
    git = subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True)
    if git.returncode != 0:
        pytest.skip("git unavailable")
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.t"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"],
                   capture_output=True)
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"],
                   capture_output=True)
    (src / "x.py").write_text("x = 2\n", encoding="utf-8")
    changed = _git_changed_py(str(tmp_path))
    assert any("x.py" in p for p in changed)


def test_git_changed_py_omits_non_repo(tmp_path):
    assert _git_changed_py(str(tmp_path)) == ()


# --- Gate 4: all 17 DIRECT features are wired --------------------------------


def test_all_seventeen_direct_features_wired():
    """Every DIRECT feature has a registered owner and a producer path.

    The ENGINE is the action-to-observation interface; all 17 DIRECT features
    must be able to fire on their triggers (per-task firing is then gated by
    the actual actions a task produces). caller_contract is REMOVE by design.
    """
    from scripts.engine_feature_census import census

    result = census()
    assert result["all_17_wired"], result
    assert result["facts_ok"] == 9
    assert result["caps_ok"] == 7


def test_all_registered_fact_owners_are_in_inventory():
    from gt_engine.engine.runner import ENGINE_FACT_OWNERS
    from scripts.engine_129_audit import build_transition_rows

    rows, _ = build_transition_rows()
    inventory = {row["identity"] for row in rows}
    for owner in ENGINE_FACT_OWNERS:
        assert owner in inventory, f"{owner} not in the 129-row inventory"
        row = next(r for r in rows if r["identity"] == owner)
        assert row["category"] == "FACT", f"{owner} is not a FACT identity"


# --- W1: affordances render ------------------------------------------------


def test_affordances_render_from_anchors():
    from gt_engine.engine.observe import compile_observation

    fact = EvidenceArtifact(
        artifact_id="ev-1", owner="localization", semantics="ranked_localization",
        content={"evidence": "hits", "target": "src/main.py:6"},
        anchors=("src/main.py:6",), model_visible=True,
    )
    observation = compile_observation(
        _request(),
        InterceptionDecision(decision=Decision.AUGMENT, reason="postflight"),
        raw_result="search done",
        evidence=(fact,),
        receipt_id="rcpt-1",
    )
    rendered = observation.render()
    assert "affordances: read(src/main.py:6)" in rendered
    assert "search done" in rendered  # raw retained after the block


# --- W2: information-gain value gate ----------------------------------------


class _FakeAdapter:
    repository_revision = "rev"
    repo_root = ""

    def gateway_state(self):
        raise AttributeError("no gateway in test")


def test_value_gate_drops_syntax_ok(tmp_path):
    from gt_engine.engine.runner import _postflight_facts

    git = subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True)
    if git.returncode != 0:
        pytest.skip("git unavailable")
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.t"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"],
                   capture_output=True)
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"],
                   capture_output=True)
    (tmp_path / "good.py").write_text("x = 2\n", encoding="utf-8")  # still parses OK
    facts = _postflight_facts(
        _request(), command="sed -i s/1/2/ good.py", raw="", returncode=0,
        repo_root=str(tmp_path), adapter=_FakeAdapter(),
    )
    assert not any(f.owner == "syntax_result" for f in facts)  # OK is zero-gain


def test_value_gate_keeps_syntax_error(tmp_path):
    from gt_engine.engine.runner import _postflight_facts

    git = subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True)
    if git.returncode != 0:
        pytest.skip("git unavailable")
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.t"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"],
                   capture_output=True)
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"],
                   capture_output=True)
    (tmp_path / "bad.py").write_text("def f():\n    return\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    facts = _postflight_facts(
        _request(), command="cat bad.py", raw="", returncode=0,
        repo_root=str(tmp_path), adapter=_FakeAdapter(),
    )
    assert any(f.owner == "syntax_result" and f.content.get("ok") is False
               for f in facts)


def test_value_gate_drops_covering_pass_keeps_red():
    from gt_engine.engine.runner import _postflight_facts

    facts_pass = _postflight_facts(
        _request(), command="pytest tests", raw="1 passed", returncode=0,
        repo_root="", adapter=_FakeAdapter(),
    )
    assert not any(f.owner == "covering_red" for f in facts_pass)
    facts_fail = _postflight_facts(
        _request(), command="pytest tests", raw="1 failed", returncode=1,
        repo_root="", adapter=_FakeAdapter(),
    )
    assert any(f.owner == "covering_red" and f.content["outcome"] == "failed"
               for f in facts_fail)


# --- W5: ladder census -----------------------------------------------------


def test_ladder_census_referenced_and_acted():
    from scripts.engine_ladder_census import _ladder

    msgs = [
        {"role": "tool", "content": '<result decision="augment"><fact owner="localization">'
                                    '{"target": "src/a.py", "file": "src/a.py"}</fact></result>'},
        {"role": "assistant", "content": "I should look at src/a.py now.",
         "extra": {"actions": [{"command": "cat src/a.py"}]}},
    ]
    census = _ladder(msgs)
    assert census["localization"]["delivered"] == 1
    assert census["localization"]["referenced"] == 1
    assert census["localization"]["acted"] == 1


# --- Obligations: the 'right info' actually delivered ------------------------


def test_obligations_fact_delivers_on_matching_action():
    """The task-contract obligations producer must fire when an action matches
    an obligation — the model learns which task requirement it is working on
    (info it may not have parsed from the issue)."""
    from gt_engine.task_contract import Obligation, TaskContract
    from gt_engine.engine.runner import _obligations_fact

    tc = TaskContract(
        role="patch",
        obligations=(
            Obligation("obl-1", "fix the vulnerability in app.py", "task",
                       subjects=("app.py",)),
        ),
    )

    class Adapter:
        contract = tc
        repository_revision = "rev-1"

    fact = _obligations_fact(
        command="cat app.py", raw="app.py contents", returncode=0, adapter=Adapter(),
    )
    assert fact is not None
    assert fact.owner == "obligations"
    # Gap-1: obligation IDs ride in witnesses (audit-only, never rendered) so
    # the fire-once dedup identity is preserved WITHOUT leaking `obl-` into the
    # model-visible payload.
    assert "obl-1" in fact.witnesses
    assert "obl-" not in json.dumps(fact.content)
    # the content must be USABLE: real requirement text + subject anchors, not
    # opaque IDs with empty rendered text (the round-5 bug)
    assert any("vulnerability" in r for r in fact.content["requirements"])
    assert "app.py" in fact.anchors
    assert fact.model_visible
    # a non-matching action abstains honestly
    none = _obligations_fact(command="ls", raw="", returncode=0, adapter=Adapter())
    assert none is None


def test_obligations_absent_without_contract():
    from gt_engine.engine.runner import _obligations_fact

    class NoContract:
        repository_revision = "rev-1"

    assert _obligations_fact(
        command="cat app.py", raw="", returncode=0, adapter=NoContract()
    ) is None


def test_dedup_fire_once_per_episode():
    """The same fact (same obligation requirement / anchor) must not spam the
    conversation across actions — the round-5 242x repeat."""
    from gt_engine.task_contract import Obligation, TaskContract
    from gt_engine.engine.runner import _postflight_facts

    tc = TaskContract(
        role="patch",
        obligations=(
            Obligation("obl-1", "fix the vulnerability in app.py", "task",
                       subjects=("app.py",)),
        ),
    )

    class Adapter:
        repository_revision = "rev-1"
        repo_root = ""
        contract = tc
        _dedup_chain = set()

        def gateway_state(self):
            raise AttributeError("no gateway in test")

    adapter = Adapter()
    for _ in range(3):
        facts = _postflight_facts(
            _request(), command="cat app.py", raw="app.py contents",
            returncode=0, repo_root="", adapter=adapter,
        )
        obligations = [f for f in facts if f.owner == "obligations"]
        assert len(obligations) <= 1, "same obligation re-fired"
    # after three identical actions, the obligation was delivered exactly once
    assert sum(1 for k in adapter._dedup_chain if k.startswith("obligations:")) == 1


# --- Gateway producer flags: full GT is enabled ---------------------------------


def test_engine_enables_all_gateway_producer_flags(monkeypatch):
    """The ENGINE must enable every gateway producer flag. Round-4 failed to
    deliver most features because GT_GATEWAY/GT_LOC_RESLOT/etc. default OFF and
    the engine never set them — produce_raw returned [] on every action."""
    import os

    from gt_engine.engine import runner as _runner

    for flag in ("GT_GATEWAY", "GT_LOC_RESLOT", "GT_PATCH_DELTA",
                 "GT_CS_EDIT_TRIGGER", "GT_CHANGE_SURFACE"):
        monkeypatch.delenv(flag, raising=False)
    _runner._GATEWAY_FLAGS_ENABLED = False  # force re-apply
    _runner._ensure_gateway_flags()
    for flag in ("GT_GATEWAY", "GT_LOC_RESLOT", "GT_PATCH_DELTA",
                 "GT_CS_EDIT_TRIGGER", "GT_CHANGE_SURFACE"):
        assert os.environ.get(flag) == "1", f"{flag} not enabled"


def test_census_requires_flags():
    from scripts.engine_feature_census import census

    result = census()
    assert result["flags_ok"], result["flags"]


# --- WS-1: localization now delivers (deterministic graph localizer) -----------


def test_localizer_injected_into_gateway(monkeypatch):
    """gateway._localize must be the deterministic localizer, not the None stub
    that made localization impossible (the round-5/6 gap)."""
    from gt_engine.engine import runner as _runner

    monkeypatch.delenv("GT_LOC_RESLOT", raising=False)
    import groundtruth.runtime.gateway as gw

    gw._localize = None
    _runner._LOCALIZER_INJECTED = False
    _runner._GATEWAY_FLAGS_ENABLED = False
    _runner._ensure_gateway_flags()
    assert callable(gw._localize)
    assert gw._localize.__module__ == "gt_engine.engine.localizer"


def test_deterministic_localize_like_fallback(tmp_path):
    """The deterministic localizer ranks candidate files from a populated
    graph (LIKE fallback when no FTS5 table), preferring non-test files."""
    import sqlite3

    from gt_engine.engine.localizer import deterministic_localize

    db = tmp_path / "graph.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT, file_path TEXT, "
        "start_line INTEGER, is_test INTEGER)"
    )
    con.executemany(
        "INSERT INTO nodes (name, file_path, start_line, is_test) VALUES (?,?,?,?)",
        [("Bottle", "bottle.py", 30, 0), ("Route", "bottle.py", 12, 0),
         ("test_x", "tests/test_a.py", 1, 1)],
    )
    con.commit()
    con.close()
    res = deterministic_localize(
        "fix the vulnerability in /app/bottle.py", str(db), "/app"
    )
    assert res.candidates, "localizer returned no candidates"
    assert res.candidates[0].file_path == "bottle.py"  # non-test preferred
    assert any(s for s in res.anchor_symbols)


# --- Gateway delivery path: covering fires on a realistic test failure --------


def test_gateway_covering_delivers_red_fact(monkeypatch):
    """The gateway's covering producer must deliver a covering_red fact when a
    test fails with a source traceback frame (the leak-law rejects test paths;
    the target is the source under test)."""
    import os

    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_PATCH_DELTA", "1")
    from gt_engine.engine.runner import _gateway_facts
    from groundtruth.runtime.gateway import GatewayState

    class Adapter:
        repository_revision = "rev-1"
        repo_root = ""

        def gateway_state(self):
            return GatewayState(repo_root="")

    raw = ("tests/test_a.py:4: in test_x\n    app_function()\n"
           "src/app.py:12: in app_function\n    assert x == 1\n"
           "E   AssertionError\n1 failed, 12 passed")
    facts = _gateway_facts(
        command="pytest tests/test_a.py", raw=raw, returncode=1,
        changed_files=(), viewed_files=(), adapter=Adapter(),
    )
    assert any(f.owner == "covering_red" and f.semantics == "covering_verdict"
               and "src/app.py" in str(f.content.get("target", ""))
               for f in facts)
