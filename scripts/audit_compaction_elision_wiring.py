"""Deep-audit D: wiring proof for the compaction elision/recap counters.

Proves every new field is present and correct at each boundary using the
real code paths:
  - ProviderViewMetrics.as_dict() exposes the 4 new keys;
  - CompactionEpochReceipt serializes the 3 new epoch keys;
  - DIAGNOSTIC_METRICS (deep_metrics) contains all 4 context_* keys and
    compare_arms can aggregate them from task rows;
  - the central agent writes top-level context_* sums from model-call rows
    whose context_compiler is ProviderViewMetrics.as_dict();
  - the deep-metrics test fixture carries the keys.
"""

from __future__ import annotations

import json
import sys

from gt_engine.deep_metrics import DIAGNOSTIC_METRICS, compare_arms
from gt_engine.provider_view import CompactionEpochReceipt, ProviderViewMetrics

NEW_METRICS_KEYS = ("stale_reads_elided", "recap_receipts", "recap_chars_added", "recap_fallbacks")
NEW_EPOCH_KEYS = ("stale_reads_elided", "recap_receipts", "recap_fallbacks")


def audit() -> dict:
    findings: dict = {}

    # --- D1: ProviderViewMetrics.as_dict ---
    metrics = ProviderViewMetrics(
        compiler_ran=True,
        compacted=True,
        raw_input_chars=1000,
        input_chars=1000,
        output_chars=500,
        elided_chars=500,
        preserved_recent_messages=10,
        active_state_chars=0,
        duplicate_turns_removed=0,
        exact_duplicate_chars_removed=0,
        unique_assistant_reasoning_chars_removed=0,
        candidate_fact_count=0,
        selected_fact_count=0,
        represented_fact_count=0,
        controller_only_fact_count=0,
        omitted_fact_count=0,
        accounted_fact_count=0,
        stale_fact_count=0,
        duplicate_fact_count=0,
        stale_reads_elided=3,
        recap_receipts=4,
        recap_chars_added=55,
        recap_fallbacks=1,
    )
    as_dict = metrics.as_dict()
    findings["d1_metrics_as_dict_keys_present"] = all(
        key in as_dict for key in NEW_METRICS_KEYS
    )
    findings["d1_metrics_as_dict_values"] = {key: as_dict[key] for key in NEW_METRICS_KEYS}

    # --- D2: CompactionEpochReceipt serialization ---
    receipt = CompactionEpochReceipt(
        epoch=1,
        trigger_tokens=900000,
        trigger_kind="character_pressure",
        trigger_chars=1000,
        original_prefix_hash="a",
        stable_prefix_hash="b",
        tool_chars_elided=500,
        duplicate_turns_removed=0,
        reasoning_messages_removed=0,
        stale_reads_elided=3,
        recap_receipts=4,
        recap_fallbacks=1,
    )
    receipt_dict = receipt.as_dict()
    findings["d2_epoch_receipt_keys_present"] = all(
        key in receipt_dict for key in NEW_EPOCH_KEYS
    )
    json.dumps(receipt_dict)
    findings["d2_epoch_receipt_json_serializable"] = True
    findings["d2_epoch_receipt_values"] = {key: receipt_dict[key] for key in NEW_EPOCH_KEYS}

    # --- D3: DIAGNOSTIC_METRICS membership ---
    context_keys = [f"context_{key}" for key in NEW_METRICS_KEYS]
    findings["d3_diagnostic_metrics_membership"] = {
        key: key in DIAGNOSTIC_METRICS for key in context_keys
    }
    findings["d3_all_in_diagnostic_metrics"] = all(
        key in DIAGNOSTIC_METRICS for key in context_keys
    )

    # --- D4: compare_arms aggregates the keys from task rows ---
    before = {
        "task": "t1",
        "solved": True,
        "censored": False,
        "reward": 1,
        "context_stale_reads_elided": 1,
        "context_recap_receipts": 2,
        "context_recap_chars_added": 10,
        "context_recap_fallbacks": 0,
    }
    after = {
        "task": "t1",
        "solved": True,
        "censored": False,
        "reward": 1,
        "context_stale_reads_elided": 3,
        "context_recap_receipts": 5,
        "context_recap_chars_added": 51,
        "context_recap_fallbacks": 1,
    }
    comparison = compare_arms({"t1": before}, {"t1": after})
    d4 = {
        key: comparison["aggregate_deltas"].get(key)
        for key in context_keys
    }
    findings["d4_compare_arms_deltas"] = d4
    findings["d4_deltas_correct"] = (
        d4["context_stale_reads_elided"] == 2
        and d4["context_recap_receipts"] == 3
        and d4["context_recap_chars_added"] == 41
        and d4["context_recap_fallbacks"] == 1
    )

    # --- D5: agent writes top-level sums from context_compiler rows ---
    with open("eval/gt_central_agent.py", encoding="utf-8") as handle:
        agent_source = handle.read()
    findings["d5_agent_aggregates_each"] = {
        key: f'"context_{key}": sum(' in agent_source
        and f'row.get("context_compiler") or {{}}).get("{key}")' in agent_source
        for key in NEW_METRICS_KEYS
    }
    findings["d5_agent_context_compiler_is_as_dict"] = (
        '"context_compiler": provider_view_metrics.as_dict()' in agent_source
    )

    # --- D6: deep-metrics test fixture carries the keys ---
    with open("tests/test_gt_deep_metrics.py", encoding="utf-8") as handle:
        test_source = handle.read()
    findings["d6_fixture_has_keys"] = {
        key: f'"context_{key}"' in test_source for key in NEW_METRICS_KEYS
    }

    return findings


if __name__ == "__main__":
    import pprint

    result = audit()
    pprint.pprint(result)
    ok = (
        result["d1_metrics_as_dict_keys_present"]
        and result["d2_epoch_receipt_keys_present"]
        and result["d2_epoch_receipt_json_serializable"]
        and result["d3_all_in_diagnostic_metrics"]
        and result["d4_deltas_correct"]
        and result["d5_agent_context_compiler_is_as_dict"]
        and all(result["d5_agent_aggregates_each"].values())
        and all(result["d6_fixture_has_keys"].values())
    )
    print("AUDIT_D_PASS" if ok else "AUDIT_D_FAIL")
    sys.exit(0 if ok else 1)
