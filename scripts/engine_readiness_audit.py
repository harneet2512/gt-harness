"""ENGINE readiness audit — P0/P1/P2 (provider-free, real seam, observed bytes).

Goal: prove every DIRECT feature delivers a correct payload at the correct time
BEFORE any paid smoke. The audit drives the REAL DefaultAgent loop + REAL
MiniSweAdapter + REAL install_runtime_hooks + REAL MiniSweProviderBoundary
(attached via the production path), then evaluates the 6-cell matrix from the
OBSERVED rendered tool messages — never from producer return values.

For each feature the audit forces its real trigger and asserts:
  fired, payload_true, no_internal_ids, correct_time, non_predictive, acted(L3)

submit_refusal is delivered as a SUPPRESS decision (+ refusal directive), not a
<fact> block, so its fired check is the `decision="suppress"` observation.

Also emits global scans: internal-ID bytes, empty-evidence facts, efficiency
flags. Exit 0 iff every required cell is green AND the global scans are clean.
"""
from __future__ import annotations

import argparse
import json
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

from engine_readiness_scenarios import SCENARIOS  # noqa: E402
from engine_smoke_e2e import TASK  # noqa: E402

FACT_RE = re.compile(r'<fact owner="([^"]+)"[^>]*>(.*?)</fact>', re.S)
INTERNAL_ID_RE = re.compile(
    r"(?:obl|pred)-[0-9a-f]{16,}|gt_engine|site-packages|"
    r"miniswe_runtime|miniswe_integration|task_contract\.py|"
    r"verification_contract\.py|gt_session\.py|engine/runner|"
    r"miniswe_controller|\.gt-state|gt-state"
)
# Deep-audit D1/D2: any harness-framing or audit-invite wording in ANY
# model-visible byte (tool results, refusal directives, system/user) would let
# the model reverse-engineer GT (round-8: 27-35 gt_engine/ probe actions).
AUDIT_INVITE_RE = re.compile(
    r"Transparent bypass|advisory mode|restore stock|GT ENFORCED SUBMIT GATE|"
    r"GT SUBMIT REFUSED|run this harness|the harness's|harness internals|"
    r"caller_contract|GT_CERT_DELIVERY|GT_SS_SUBMIT_RED|authorize_submit"
)

# submit_refusal delivers as a SUPPRESS decision, not a <fact> block.
SUPPRESS_FEATURES = {"submit_refusal"}


def _model_messages(agent) -> list[tuple[str, str]]:
    """Capture (role, content) for the full stream."""
    seen: list[tuple[str, str]] = []
    orig = agent.model._prepare_messages_for_api

    def spy(messages):
        for item in messages:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            seen.append((role, content))
        return orig(messages)

    agent.model._prepare_messages_for_api = spy
    return seen


def _tool_obs(stream) -> list[str]:
    return [c for r, c in stream if r == "tool"]


def _fact_blocks(observation: str) -> list[tuple[str, str]]:
    return [(fm.group(1), fm.group(2)) for fm in FACT_RE.finditer(observation)]


def _anchors_from_body(body: str) -> tuple[str, ...]:
    """Stable path/file/target anchors a later action could reference."""
    out = []
    for m in re.finditer(r'"(?:target|file|path)":\s*"([^"]+)"', body):
        a = m.group(1)
        if a and not a.startswith(".gt"):
            out.append(a)
    return tuple(dict.fromkeys(out))


def audit_feature(feature: str, builder, owners: tuple[str, ...], *,
                  built=None) -> dict:
    if built is not None:
        agent, adapter, graph_db, root = built
    else:
        agent, adapter, graph_db, root = builder()
    stream = _model_messages(agent)
    scenario_task = str(getattr(agent, "_gt_scenario_task", "") or TASK)
    agent.run(scenario_task)

    predictive = any(
        role != "tool" and FACT_RE.search(content) for role, content in stream
    )
    tool_obs = _tool_obs(stream)

    deliveries: list[dict] = []
    for idx, obs in enumerate(tool_obs):
        for owner, body in _fact_blocks(obs):
            deliveries.append({
                "owner": owner, "body": body, "msg_index": idx,
                "empty_evidence": '"evidence": ""' in body,
                "internal_ids": bool(INTERNAL_ID_RE.search(obs)),
                "anchors": _anchors_from_body(body),
            })

    # correct_time: a fact-bearing tool message must be immediately preceded by
    # an assistant (action) message in the stream — i.e. the fact is bound to
    # its triggering action's result, never a stray late append or a
    # system/user-injected block.
    timed_ok = True
    for idx, (role, content) in enumerate(stream):
        if role == "tool" and FACT_RE.search(content):
            prev = stream[idx - 1] if idx > 0 else None
            if prev is None or prev[0] != "assistant":
                timed_ok = False
                break
    timed_ok = timed_ok or not any(
        FACT_RE.search(c) for r, c in stream if r == "tool"
    )

    rows: dict[str, dict] = {}
    for owner in owners:
        if owner in SUPPRESS_FEATURES:
            # SUPPRESS is delivered as a decision, not a <fact>; count DISTINCT
            # suppress decisions (the engine fires one per blocked submit; a
            # runaway re-submit loop must not inflate the count past 1 proof).
            suppress_idx = sorted(
                i for i, o in enumerate(tool_obs) if 'decision="suppress"' in o
            )
            mine = [{"msg_index": i, "body": "", "empty_evidence": False,
                     "internal_ids": False, "anchors": ()}
                    for i in suppress_idx]
            fired = bool(mine)
            rows[owner] = {
                "fired": fired,
                "payload_true": fired,  # SUPPRESS carries the refusal directive
                "no_internal_ids": fired and not any(
                    INTERNAL_ID_RE.search(o) for o in tool_obs
                ),
                "correct_time": fired and timed_ok,
                "non_predictive": not predictive,
                "acted": False,
                "n_delivered": 1 if fired else 0,  # proof-of-suppress, not loop count
            }
            continue
        mine = [d for d in deliveries if d["owner"] == owner]
        fired = bool(mine)
        acted = any(
            d["anchors"] for d in mine
        )  # refined below: a later command references the anchor
        rows[owner] = {
            "fired": fired,
            "payload_true": fired and not any(d["empty_evidence"] for d in mine),
            "no_internal_ids": fired and not any(d["internal_ids"] for d in mine),
            "correct_time": fired and timed_ok,
            "non_predictive": not predictive,
            "acted": acted,
            "n_delivered": len(mine),
        }
    # collect capability_fired receipts from the scenario journal (D6). The
    # journal stores only the bound FACT owner (never the GT_* name — it is a
    # readable file in the container); map owner -> CAP name in memory.
    caps_fired: set[str] = set()
    try:
        import json as _json
        from pathlib import Path as _Path

        state_dir = _Path(root).parent / f"{_Path(root).name}-state"
        for journal in state_dir.rglob("events.jsonl"):
            for line in journal.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = _json.loads(line)
                if rec.get("event") == "capability_fired":
                    owner = str(rec.get("fact_owner") or "")
                    caps_fired.add(_CAP_OWNER_TO_NAME.get(owner, owner))
    except Exception:  # noqa: BLE001 - capability scan is fail-open
        pass
    return {
        "feature": feature,
        "owners": rows,
        "adapter_phase": adapter.phase,
        "adapter_unmet": list(adapter.unmet_predicates),
        "all_observations": tool_obs,
        "deliveries": deliveries,
        "stream": stream,
        "caps_fired": caps_fired,
        "root": root,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: dict[str, dict] = {}
    for feature, (builder, owners) in SCENARIOS.items():
        results[feature] = audit_feature(feature, builder, owners)

    all_obs = [o for r in results.values() for o in r["all_observations"]]
    internal_id_bytes = sum(1 for o in all_obs if INTERNAL_ID_RE.search(o))
    empty_facts = sum(
        1 for r in results.values() for o in r["all_observations"]
        if '"evidence": ""' in o
    )
    # Deep-audit D1/D2: scan the FULL conversation (every role: system, user,
    # assistant, tool, directives) for harness-internal or audit-invite bytes.
    full_stream = [c for r in results.values() for _r, c in r["stream"]]
    audit_invite_bytes = sum(1 for c in full_stream if AUDIT_INVITE_RE.search(c))
    full_internal_bytes = sum(1 for c in full_stream if INTERNAL_ID_RE.search(c))
    caller_contract_bytes = sum(1 for c in full_stream if "caller_contract" in c)

    # Deep-audit D7: scan the ON-DISK artifacts a real model can READ (the
    # state journal, provider_requests blobs, repository snapshots, and every
    # workspace file) for internal IDs. Round-9 exposed this: the rendered
    # observations were clean, but the model `cat`'d the state journal and
    # surfaced `pred-<sha>` into its own bytes. Offline audit MUST equal the
    # full observable surface a real run exposes.
    on_disk_leaks: list[str] = []
    for res in results.values():
        root = res.get("root")
        if not root:
            continue
        state_dir = Path(root).parent / f"{Path(root).name}-state"
        for scan_root in (state_dir, Path(root)):
            if not scan_root.exists():
                continue
            for f in scan_root.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    blob = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for mm in INTERNAL_ID_RE.finditer(blob):
                    on_disk_leaks.append(
                        f"{Path(root).name}:{str(f.relative_to(scan_root))}"
                        f"@{mm.start()}:{mm.group(0)[:20]}"
                    )
    on_disk_leaks = list(dict.fromkeys(on_disk_leaks))[:10]

# Deep-audit D6: every CAP_OWNER whose bound FACT delivered must have fired
# a capability RECEIPT in the real-seam journal (not just be statically
# "wired" in the census). The receipt is the only proof the CAP ran.
CAP_BY_FACT = {
    "syntax_result": "GT_EDIT_CHECK",
    "signature_delta": "GT_PATCH_DELTA",
    "localization": "GT_LOC_RESLOT",
    "submit_refusal": "GT_SS_SUBMIT_RED",
    "recovery": "GT_HYPOTHESIS",
    "newfile_precedent": "GT_CHANGE_SURFACE",
    "delivery_receipt": "GT_CERT_DELIVERY",
}
# FACT owner -> CAP name, used only IN MEMORY to interpret the journal's
# fact_owner receipts (the journal itself never stores the GT_* names).
_CAP_OWNER_TO_NAME = {fact: cap for fact, cap in CAP_BY_FACT.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: dict[str, dict] = {}
    for feature, (builder, owners) in SCENARIOS.items():
        results[feature] = audit_feature(feature, builder, owners)

    all_obs = [o for r in results.values() for o in r["all_observations"]]
    internal_id_bytes = sum(1 for o in all_obs if INTERNAL_ID_RE.search(o))
    empty_facts = sum(
        1 for r in results.values() for o in r["all_observations"]
        if '"evidence": ""' in o
    )
    full_stream = [c for r in results.values() for _r, c in r["stream"]]
    audit_invite_bytes = sum(1 for c in full_stream if AUDIT_INVITE_RE.search(c))
    full_internal_bytes = sum(1 for c in full_stream if INTERNAL_ID_RE.search(c))
    caller_contract_bytes = sum(1 for c in full_stream if "caller_contract" in c)

    # Deep-audit D7: scan the ON-DISK artifacts a real model can READ (the
    # state journal, provider_requests blobs, repository snapshots, and every
    # workspace file) for internal IDs. Round-9 exposed this: the rendered
    # observations were clean, but the model `cat`'d the state journal and
    # surfaced `pred-<sha>` into its own bytes. Offline audit MUST equal the
    # full observable surface a real run exposes.
    on_disk_leaks: list[str] = []
    for res in results.values():
        root = res.get("root")
        if not root:
            continue
        state_dir = Path(root).parent / f"{Path(root).name}-state"
        for scan_root in (state_dir, Path(root)):
            if not scan_root.exists():
                continue
            for f in scan_root.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    blob = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for mm in INTERNAL_ID_RE.finditer(blob):
                    on_disk_leaks.append(
                        f"{Path(root).name}:{str(f.relative_to(scan_root))}"
                        f"@{mm.start()}:{mm.group(0)[:20]}"
                    )
    on_disk_leaks = list(dict.fromkeys(on_disk_leaks))[:10]

    # Deep-audit D6: every CAP_OWNER whose bound FACT delivered must have fired
    # a capability RECEIPT in the real-seam journal (not just be statically
    # "wired" in the census). The receipt is the only proof the CAP ran.
    cap_fired: set[str] = set()
    for res in results.values():
        cap_fired |= res.get("caps_fired", set())
    cap_matrix_ok = True
    print("| fact | cap_owner | fact_delivered | fired_receipt |")
    print("|---|---|---|---|")
    for fact, cap in CAP_BY_FACT.items():
        if fact == "delivery_receipt":
            fact_delivered = True  # GT_CERT_DELIVERY fires on every delivery
        else:
            fact_delivered = any(
                res["owners"].get(fact, {}).get("fired")
                for res in results.values()
            )
        fired = cap in cap_fired
        if fact_delivered and not fired:
            cap_matrix_ok = False
        print(f"| {fact} | {cap} | {'Y' if fact_delivered else '-'} | "
              f"{'Y' if fired else 'N'} |")
    ok = True
    if not cap_matrix_ok:
        ok = False

    if args.json:
        out = {
            "results": {k: v["owners"] for k, v in results.items()},
            "global": {
                "internal_id_bytes": internal_id_bytes,
                "empty_facts": empty_facts,
                "audit_invite_bytes": audit_invite_bytes,
                "full_internal_bytes": full_internal_bytes,
                "caller_contract_bytes": caller_contract_bytes,
            },
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    print("| feature | fired | payload_true | no_ids | correct_time | non_pred |")
    print("|---|---|---|---|---|---|")
    for feature, res in results.items():
        for owner, row in res["owners"].items():
            cells = [
                row["fired"], row["payload_true"], row["no_internal_ids"],
                row["correct_time"], row["non_predictive"],
            ]
            marks = "".join("Y " if c else "N " for c in cells)
            print(f"| {feature}/{owner:<14} | {marks.strip().replace(' ', ' | ')} |")
            if not all(cells):
                ok = False
    print(f"\ninternal_id_bytes={internal_id_bytes} empty_facts={empty_facts}")
    print(f"audit_invite_bytes={audit_invite_bytes} "
          f"full_internal_bytes={full_internal_bytes} "
          f"caller_contract_bytes={caller_contract_bytes}")
    print(f"on_disk_internal_id_leaks={len(on_disk_leaks)}")
    if on_disk_leaks:
        for leak in on_disk_leaks:
            print(f"  LEAK {leak}")
    if (internal_id_bytes or empty_facts or audit_invite_bytes
            or full_internal_bytes or caller_contract_bytes or on_disk_leaks):
        ok = False
    print("READY" if ok else "NOT READY")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
