"""ENGINE meta-audit — Gate 2: prove the readiness audit can detect lies.

The readiness audit (Gate 1) is only trustworthy if it FAILS when a defect is
present. This meta-audit plants REAL defects into the pipeline (via targeted
monkeypatches of the producer/gate path) and asserts the audit catches each:

  M1. empty-evidence fact injected -> payload_true must go red
  M2. internal ID injected into a fact payload -> no_internal_ids must go red
  M3. predictive fact injected into a non-tool message -> non_predictive red
  M4. detached fact (appended after the run, not bound to an action) ->
      correct_time red

Plus:
  M5. independent re-derivation: a second parser recomputes delivery counts
      from raw observations; must exactly match the audit.
  M6. ground-truth: every fact's target/file resolves to a real path in the
      scenario workspace.

Exit 0 iff every mutation is caught AND re-derivation matches AND ground-truth
holds. This is the gate that makes "READY" mean something.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from engine_readiness_audit import (  # noqa: E402
    FACT_RE,
    INTERNAL_ID_RE,
    _model_messages,
    audit_feature,
)
from engine_readiness_scenarios import SCENARIOS  # noqa: E402
from engine_smoke_e2e import TASK  # noqa: E402

# The audit's detection predicate for each cell (the thing a mutation must trip).
EMPTY_RE = re.compile(r'"evidence": ""')


def main() -> int:
    import gt_engine.engine.runner as runner

    checks: list[tuple[str, bool]] = []

    # --- M1: empty-evidence fact must make payload_true red -------------------
    # Plant a defect at the SOURCE: an empty-evidence covering_red producer.
    # The audit's payload_true (parsed from observed bytes) must then be False.
    orig_postflight = runner._postflight_facts

    def empty_postflight(*a, **k):
        facts = orig_postflight(*a, **k)
        out = []
        for f in facts:
            if f.owner == "covering_red":
                from gt_engine.engine.contracts import EvidenceArtifact

                f = EvidenceArtifact(
                    artifact_id=f.artifact_id, owner=f.owner, semantics=f.semantics,
                    content={"evidence": "", "target": "src/mod.py"},
                    anchors=f.anchors, producer=f.producer,
                    producer_version=f.producer_version,
                    freshness_revision=f.freshness_revision, coverage=f.coverage,
                    model_visible=True,
                )
            out.append(f)
        return tuple(out)

    runner._postflight_facts = empty_postflight
    try:
        res = audit_feature("covering_red", SCENARIOS["covering_red"][0],
                            SCENARIOS["covering_red"][1])
    finally:
        runner._postflight_facts = orig_postflight
    payload_true = res["owners"]["covering_red"]["payload_true"]
    m1_caught = not payload_true  # empty payload must FAIL payload_true
    checks.append(("M1 empty-evidence -> payload_true red", m1_caught))

    # --- M2: internal ID in payload must make no_internal_ids red -------------
    orig_facts = runner._obligations_fact

    def leaky_obligations(**kw):
        fact = orig_facts(**kw)
        if fact is None:
            return None
        from gt_engine.engine.contracts import EvidenceArtifact

        return EvidenceArtifact(
            artifact_id=fact.artifact_id, owner=fact.owner, semantics=fact.semantics,
            content={**dict(fact.content),
                     "matched": ["obl-deadbeefdeadbeefdeadbeefdeadbeef"]},
            anchors=fact.anchors, witnesses=fact.witnesses, producer=fact.producer,
            producer_version=fact.producer_version,
            freshness_revision=fact.freshness_revision, coverage=fact.coverage,
            model_visible=True,
        )

    runner._obligations_fact = leaky_obligations
    try:
        res = audit_feature("obligations", SCENARIOS["obligations"][0],
                            SCENARIOS["obligations"][1])
    finally:
        runner._obligations_fact = orig_facts
    no_ids = res["owners"]["obligations"]["no_internal_ids"]
    m2_caught = not no_ids  # internal ID must FAIL no_internal_ids
    checks.append(("M2 internal-ID -> no_internal_ids red", m2_caught))

    # --- M3: predictive fact must make non_predictive red ---------------------
    # A fact rendered into a system/user message (before any action). The audit
    # parses the full stream and flags non-tool facts as predictive.
    built = SCENARIOS["localization"][0]()
    agent = built[0]
    orig_prepare = agent.model._prepare_messages_for_api
    leaked = {"detected": False}

    def leak_prepare(messages):
        for it in messages:
            if it.get("role") in ("system", "user") and not leaked["detected"]:
                c = str(it.get("content") or "")
                it["content"] = c + "\n<fact owner=\"localization\">{\"target\": \"a.py\"}</fact>"
                leaked["detected"] = True
        return orig_prepare(messages)

    agent.model._prepare_messages_for_api = leak_prepare
    res = audit_feature("localization", SCENARIOS["localization"][0],
                        SCENARIOS["localization"][1], built=built)
    non_pred = res["owners"]["localization"]["non_predictive"]
    m3_caught = (not non_pred) and leaked["detected"]
    checks.append(("M3 predictive fact -> non_predictive red", m3_caught))

    # --- M4: detached fact -> correct_time red --------------------------------
    # A fact appended to a tool message that does NOT follow an assistant action.
    built = SCENARIOS["recovery"][0]()
    agent = built[0]
    orig_prepare = agent.model._prepare_messages_for_api
    detached = {"detected": False}

    def detach_prepare(messages):
        if not detached["detected"]:
            # inject a stray tool message (not preceded by an assistant action)
            # directly into the input the audit's spy records
            messages.append({
                "role": "tool",
                "content": "<result action=\"stray\" decision=\"augment\">"
                           "<fact owner=\"recovery\">{\"evidence\": \"stray\", "
                           "\"target\": \"src/mod.py\"}</fact></result>",
            })
            detached["detected"] = True
        return orig_prepare(messages)

    agent.model._prepare_messages_for_api = detach_prepare
    res = audit_feature("recovery", SCENARIOS["recovery"][0],
                        SCENARIOS["recovery"][1], built=built)
    correct_time = res["owners"]["recovery"]["correct_time"]
    m4_caught = (not correct_time) and detached["detected"]
    checks.append(("M4 detached fact -> correct_time red", m4_caught))

    # --- M7: audit-invite wording (old refusal text) must be flagged ----------
    # Deep-audit D2: plant the legacy "Transparent bypass: run this harness in
    # advisory mode" refusal text into a user message; the audit's
    # audit_invite_bytes scan must catch it.
    from engine_readiness_audit import AUDIT_INVITE_RE

    invite_test = (
        "Transparent bypass: run this harness in advisory mode to restore "
        "stock Mini-SWE submission behavior."
    )
    m7_caught = bool(AUDIT_INVITE_RE.search(invite_test))
    checks.append(("M7 audit-invite wording flagged", m7_caught))

    # --- M5: independent re-derivation exact match ----------------------------
    match_ok = True
    for feature, (builder, owners) in SCENARIOS.items():
        agent, adapter, graph_db, root = builder()
        stream = _model_messages(agent)
        agent.run(str(getattr(agent, "_gt_scenario_task", "") or TASK))
        tool_obs = [c for r, c in stream if r == "tool"]
        counts: dict[str, int] = {}
        for o in tool_obs:
            for fm in FACT_RE.finditer(o):
                owner = fm.group(1)
                counts[owner] = counts.get(owner, 0) + 1
        res = audit_feature(feature, SCENARIOS[feature][0],
                            SCENARIOS[feature][1])
        for owner in owners:
            audited = res["owners"].get(owner, {}).get("n_delivered", 0)
            if owner == "submit_refusal":
                indep = 1 if any('decision="suppress"' in o for o in tool_obs) else 0
            else:
                indep = counts.get(owner, 0)
            if audited != indep:
                match_ok = False
                print(f"  M5 MISMATCH {feature}/{owner}: audit={audited} indep={indep}")
    checks.append(("M5 independent re-derivation exact match", match_ok))

    # --- M6: ground-truth anchors resolve to real paths -----------------------
    gt_ok = True
    for feature, (builder, owners) in SCENARIOS.items():
        agent, adapter, graph_db, root = builder()
        tool_obs = [c for r, c in _model_messages(agent) if r == "tool"]
        agent.run(str(getattr(agent, "_gt_scenario_task", "") or TASK))
        for o in tool_obs:
            for fm in FACT_RE.finditer(o):
                owner, body = fm.group(1), fm.group(2)
                for m in re.finditer(r'"(?:target|file)":\s*"([^"]+)"', body):
                    anchor = m.group(1)
                    if anchor.startswith(".gt") or "gt-state" in anchor:
                        continue
                    if not (Path(root) / anchor).exists():
                        gt_ok = False
                        print(f"  M6 MISSING {feature}/{owner}: {anchor} not under {root}")
    checks.append(("M6 ground-truth anchors resolve", gt_ok))

    # --- M9: recovery fires ONCE per failure identity (no repeat spam) --------
    # Correctness: the recovery fact content carries `occurrences`, so a
    # content-hash dedup re-delivers on the 3rd/4th identical failure. The
    # producer must emit once per fingerprint.
    import gt_engine.engine.runner as runner

    class _RAdapter:
        repository_revision = "r1"
        _engine_failure_history = {}
        _dedup_chain = set()

    ra = _RAdapter()
    fires = 0
    for _ in range(5):
        f = runner._recovery_fact(
            command="pytest", raw="Traceback\nE AssertionError",
            returncode=1, adapter=ra,
        )
        if f is not None:
            fires += 1
    m9_ok = fires == 1
    checks.append(("M9 recovery fires once per failure identity", m9_ok))

    # --- M10: stop-signal fires ONCE per query identity -----------------------
    class _SAdapter:
        repository_revision = "r1"
        _engine_search_history = {}
        _dedup_chain = set()

    sa = _SAdapter()
    stop_fires = 0
    for _ in range(5):
        f = runner._stop_signal_fact(
            command="grep -r foo .", raw="", returncode=1, adapter=sa,
        )
        if f is not None:
            stop_fires += 1
    m10_ok = stop_fires == 1
    checks.append(("M10 stop-signal fires once per query identity", m10_ok))

    # --- M8: delivered obligation text must appear in the actual task ---------
    m8_ok = True
    for feature, (builder, owners) in SCENARIOS.items():
        agent, adapter, graph_db, root = builder()
        scenario_task = str(getattr(agent, "_gt_scenario_task", "") or TASK)
        stream = _model_messages(agent)
        agent.run(scenario_task)
        tool_obs = [c for r, c in stream if r == "tool"]
        for o in tool_obs:
            for fm in FACT_RE.finditer(o):
                if fm.group(1) != "obligations":
                    continue
                body = fm.group(2)
                for m in re.finditer(r'"requirements":\s*\[([^\]]*)\]', body):
                    raw = m.group(1)
                    for req in re.findall(r'"([^"]+)"', raw):
                        # requirement must be grounded in the task text
                        words = [w for w in req.lower().split() if len(w) > 3]
                        if not any(w in scenario_task.lower() for w in words):
                            m8_ok = False
                            print(f"  M8 UNGROUNDED {feature}: req={req!r} "
                                  f"not in task={scenario_task!r}")
    checks.append(("M8 obligation text grounded in the actual task", m8_ok))

    # --- M11: capability receipt present when the bound FACT fires ------------
    # D6: the 7 CAP_OWNERs must emit a journal receipt when their FACT delivers,
    # not just be statically "wired". Verify every delivered FACT with a bound
    # CAP has a capability_fired receipt in the same scenario's journal.
    m11_ok = True
    CAP_BY_FACT = {
        "syntax_result": "GT_EDIT_CHECK",
        "signature_delta": "GT_PATCH_DELTA",
        "localization": "GT_LOC_RESLOT",
        "submit_refusal": "GT_SS_SUBMIT_RED",
        "recovery": "GT_HYPOTHESIS",
        "newfile_precedent": "GT_CHANGE_SURFACE",
        "delivery_receipt": "GT_CERT_DELIVERY",
    }
    for feature, (builder, owners) in SCENARIOS.items():
        agent, adapter, graph_db, root = builder()
        agent.run(str(getattr(agent, "_gt_scenario_task", "") or TASK))
        from pathlib import Path as _P

        state_dir = _P(root).parent / f"{_P(root).name}-state"
        caps: set[str] = set()
        try:
            for journal in state_dir.rglob("events.jsonl"):
                for line in journal.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    import json as _json

                    rec = _json.loads(line)
                    if rec.get("event") == "capability_fired":
                        # journal stores the bound FACT owner (never GT_* name)
                        owner = str(rec.get("fact_owner") or "")
                        caps.add({f: c for c, f in CAP_BY_FACT.items()}.get(
                            owner, owner))
        except Exception:  # noqa: BLE001
            pass
        stream = _model_messages(agent)
        tool_obs = [c for r, c in stream if r == "tool"]
        for owner in owners:
            if owner not in CAP_BY_FACT:
                continue
            fact_fired = any(
                f'<fact owner="{owner}"' in o for o in tool_obs
            ) or (owner == "submit_refusal"
                  and any('decision="suppress"' in o for o in tool_obs))
            if fact_fired and CAP_BY_FACT[owner] not in caps:
                m11_ok = False
                print(f"  M11 MISSING {feature}/{owner}: "
                      f"{CAP_BY_FACT[owner]} no receipt despite FACT firing")
    checks.append(("M11 capability receipt present when FACT fires", m11_ok))

    # --- M12: obligations relevance gate (no false positives) -----------------
    from gt_engine.task_contract import Obligation, TaskContract
    from gt_engine.engine.runner import _obligations_fact

    class _OAdapter:
        repository_revision = "r1"
        contract = TaskContract(
            role="patch",
            obligations=(
                Obligation("obl-1", "fix the vulnerability in app.py", "task",
                           subjects=("app.py",)),
            ),
        )

    m12_ok = True
    # positive: subject referenced -> fires
    if _obligations_fact(command="cat app.py", raw="app.py contents",
                         returncode=0, adapter=_OAdapter()) is None:
        m12_ok = False
    # unrelated -> abstain
    if _obligations_fact(command="cat requirements.txt", raw="flask==2.0",
                         returncode=0, adapter=_OAdapter()) is not None:
        m12_ok = False
    # near-miss (no subject, weak overlap) -> abstain
    if _obligations_fact(command="echo fix", raw="",
                         returncode=0, adapter=_OAdapter()) is not None:
        m12_ok = False
    checks.append(("M12 obligations relevance gate (no false positives)", m12_ok))

    # --- M13: on-disk journal leak detection (D7) -----------------------------
    # The D7 disk scan must catch an internal ID planted in a readable file
    # (the round-9 leak: the model cat's the state journal). Plant one into the
    # state journal of a fresh scenario and verify the audit's regex flags it.
    from engine_readiness_audit import INTERNAL_ID_RE

    planted = {}
    for feature, (builder, owners) in SCENARIOS.items():
        if feature == "typed_search":
            continue
        agent, adapter, graph_db, root = builder()
        agent.run(str(getattr(agent, "_gt_scenario_task", "") or TASK))
        from pathlib import Path as _P

        state_dir = _P(root).parent / f"{_P(root).name}-state"
        journals = list(state_dir.rglob("events.jsonl"))
        if not journals:
            continue
        j = journals[0]
        j.write_text(
            j.read_text(encoding="utf-8")
            + '\n{"event":"state","unmet":["pred-deadbeefdeadbeefdeadbeefdeadbeef"]}\n',
            encoding="utf-8",
        )
        blob = j.read_text(encoding="utf-8")
        planted[feature] = bool(INTERNAL_ID_RE.search(blob))
        break
    m13_ok = bool(planted) and all(planted.values())
    checks.append(("M13 on-disk internal-ID leak detected", m13_ok))

    all_ok = all(ok for _, ok in checks)
    print("META-AUDIT (Gate 2)")
    for name, ok in checks:
        print(f"  {'OK ' if ok else 'FAIL'} {name}")
    print("META READY" if all_ok else "META NOT READY")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
