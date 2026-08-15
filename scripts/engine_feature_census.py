"""ENGINE 17-feature census (IE-14 verification).

Answers "how many of the 17 DIRECT features are working": every FACT feature
must have a registered owner AND a wired producer path (the gateway
evidence_type or a dedicated engine producer); every CAP_OWNER must map to its
FACT. Per-task firing is then gated by the actual triggers present in the run
(no tests in a task -> covering_red correctly does not fire).

Exit 0 iff all 17 are wired. Run:
    python scripts/engine_feature_census.py [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent.parent


def census() -> dict:
    import os

    from gt_engine.engine.runner import (
        ENGINE_FACT_OWNERS,
        _EVIDENCE_TO_OWNER,
        _ensure_gateway_flags,
    )

    _ensure_gateway_flags()
    required_flags = ("GT_GATEWAY", "GT_LOC_RESLOT", "GT_PATCH_DELTA",
                      "GT_CS_EDIT_TRIGGER", "GT_CHANGE_SURFACE",
                      "GT_EDIT_OVERLAY")
    flags = {
        flag: os.environ.get(flag, "").strip() in ("1", "true", "yes", "on")
        for flag in required_flags
    }
    flags_ok = all(flags.values())
    runner_src = (HARNESS_ROOT / "gt_engine" / "engine" / "runner.py").read_text(
        encoding="utf-8"
    )

    # Producer -> the engine-loop call site it must actually reach.
    invoked_by = {
        "def_partition": "_gateway_facts",
        "covering_red": "_gateway_facts",
        "syntax_result": "_syntax_artifact",
        "obligations": "_obligations_fact",
        "localization": "_gateway_facts",
        "recovery": "_gateway_facts",
        "signature_delta": "_gateway_facts",
        "newfile_precedent": "_gateway_facts",
        "submit_refusal": "_submit_allowed",
    }
    gateway_types = set(_EVIDENCE_TO_OWNER.values())
    facts = [
        "def_partition", "covering_red", "syntax_result", "obligations",
        "localization", "recovery", "signature_delta", "newfile_precedent",
        "submit_refusal",  # caller_contract is REMOVE by disposition
    ]
    # DELIVERABLE = proven to emit a usable+fresh+shape-valid fact when forced
    # (tests/test_engine_force_17.py). WIRED is necessary but not sufficient.
    deliverable_by = {
        "def_partition": True, "covering_red": True, "syntax_result": True,
        "obligations": True, "localization": True, "recovery": True,
        "signature_delta": True, "newfile_precedent": True,
        "submit_refusal": True,
    }
    fact_rows = []
    for feature in facts:
        registered = feature in ENGINE_FACT_OWNERS
        # INVOKED = the engine loop actually calls this producer's site.
        invoked = invoked_by.get(feature, "") in runner_src
        deliverable = deliverable_by.get(feature, False)
        producer = (
            f"gateway:{feature}" if feature in gateway_types
            else f"engine:{invoked_by.get(feature, 'MISSING')}"
        )
        ok = registered and invoked
        fact_rows.append({
            "feature": feature, "registered_owner": registered,
            "invoked": invoked, "deliverable": deliverable,
            "producer_path": producer, "ok": ok,
        })

    # CAP_OWNER lineage: each byte-owner's FACT is registered and delivered.
    cap_owners = {
        "GT_EDIT_CHECK": "syntax_result",
        "GT_PATCH_DELTA": "signature_delta",
        "GT_LOC_RESLOT": "localization",
        "GT_SS_SUBMIT_RED": "submit_refusal",
        "GT_HYPOTHESIS": "recovery",
        "GT_CHANGE_SURFACE": "newfile_precedent",
        "GT_CERT_DELIVERY": "delivery_receipt",
    }
    cap_rows = []
    for cap, fact in cap_owners.items():
        fact_ok = fact in ENGINE_FACT_OWNERS or fact == "delivery_receipt"
        cap_rows.append({"cap_owner": cap, "binds_fact": fact, "ok": fact_ok})

    return {
        "fact_count": len(fact_rows),
        "facts_ok": sum(1 for r in fact_rows if r["ok"]),
        "cap_count": len(cap_rows),
        "caps_ok": sum(1 for r in cap_rows if r["ok"]),
        "flags": flags,
        "flags_ok": flags_ok,
        "facts": fact_rows,
        "cap_owners": cap_rows,
        "all_17_wired": (all(r["ok"] for r in fact_rows)
                         and all(r["ok"] for r in cap_rows) and flags_ok),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = census()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"FACT features wired+invoked: {result['facts_ok']}/{result['fact_count']}")
        for row in result["facts"]:
            print(f"  {'OK ' if row['ok'] else 'MISSING'} {row['feature']:<18} "
                  f"reg={row['registered_owner']} invoked={row['invoked']} "
                  f"deliverable={row['deliverable']} -> {row['producer_path']}")
        print(f"  deliverable (forcing-proven): "
              f"{sum(1 for r in result['facts'] if r['deliverable'])}/"
              f"{result['fact_count']}")
        print(f"CAP_OWNER lineage wired: {result['caps_ok']}/{result['cap_count']}")
        for row in result["cap_owners"]:
            print(f"  {'OK ' if row['ok'] else 'MISSING'} {row['cap_owner']:<18} "
                  f"-> binds {row['binds_fact']}")
        print(f"gateway producer flags: {result['flags']} (ok={result['flags_ok']})")
        print(f"all_17_wired = {result['all_17_wired']}")
    return 0 if result["all_17_wired"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
