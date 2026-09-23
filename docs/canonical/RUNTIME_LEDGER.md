# Canonical Runtime Ledger (C3)

Re-verified against `canonical/gt-har90` at `873b6a1e`/`64f0a3dd`. Starting
point: freeze audit `A_pipeline_boundaries.md` §2–5; every `file:function`
below was re-confirmed to exist in this tree.

Classes: **INTELLIGENCE/STATE** (computes or stores product state),
**DELIVERY POLICY** (decides what/how much reaches the model),
**AGENT CONTROL** (steers/gates/terminates the run),
**BENCHMARK SCAFFOLD** (harness/CI lifecycle, not product semantics),
**LEGACY** (retained for compat; see C4 ledger for removal decisions).

| # | Component | Class | Canonical file:function | State produced | Side effects | Depends on | Needed for Mini-SWE |
|---|-----------|-------|-------------------------|----------------|--------------|------------|---------------------|
| 1 | Workspace snapshot | INTELLIGENCE/STATE | `gt_engine/runtime_observation.py:2328 capture_workspace`, `:2429 diff_workspace` | pre/post file-hash snapshots | filesystem reads only | task workspace | yes — substrate for edit detection |
| 2 | Edit transaction | INTELLIGENCE/STATE | `gt_engine/runtime_observation.py:122 EditTransaction`; recorded via `miniswe_integration.py:2603 record_edit_transaction` | typed edit set (created/modified/deleted, bytes) | journal rows | snapshot diff | yes — the edit→graph edge |
| 3 | Sync amend | INTELLIGENCE/STATE | `miniswe_integration.py:2657 _sync_amend_graph`, `:3490 _amend_graph_inline` (`gt-index -file`) | amended graph.db at new source revision | subprocess exec of producer; WAL churn | producer binary, edit txn | yes — semantic freshness core |
| 4 | Recovery suspension | INTELLIGENCE/STATE | `miniswe_integration.py:3625 _recovery_build_inline`; streaks `AMEND_FAILURE_ESCALATION_STREAK=2`, `RECOVERY_FAILURE_SUSPEND_STREAK=3`, `RECOVERY_FAILURE_EPISODE_CAP=8` | recovery streaks, suspended flag | full rebuild subprocess | producer binary | yes — fail-closed freshness |
| 5 | Test/build observation | INTELLIGENCE/STATE | `runtime_observation.py:2485 compile_execution_evidence`; predicate eval `miniswe_integration.py:5361 evaluate_observation`, failing `integ:6533 evaluate_failing_observation` | outcome lattice + obligation predicate status (GREEN/RED, lexical basis) | none (pure classify) | executed command text | yes — runtime truth signal |
| 6 | Execution evidence | DELIVERY POLICY | `miniswe_integration.py:2931 record_execution_evidence`; render `runtime_observation.py:290 execution_evidence_model_line` | `[GT_EXECUTION_EVIDENCE]` model line vs baseline ledger | queues model-facing unit | test/build classification | yes — certified delivery producer |
| 7 | Covering attribution | INTELLIGENCE | `gt_engine/miniswe_covering.py:144 attribute_test_failure` (no execution) | covering test↔failure attribution map | none | test observation | yes — failure evidence enrichment |
| 8 | Failure fingerprint | INTELLIGENCE/STATE | `miniswe_integration.py:6582 note_failure_fingerprint` | canonical fingerprint + recurrence counter per epoch | sets `pending_transient` | test observation | yes — recurrence detection |
| 9 | Recovery steer | AGENT CONTROL | `miniswe_integration.py:6617 prepare_recovery_delivery`; injected as extra user msg `miniswe_runtime.py:1320` | sealed `recovery` delivery + `GT_RECOVERY` text | model-facing injection (≤2/task) | fingerprint recurrence | yes — advisory steer mechanism |
| 10 | Churn governor | AGENT CONTROL | `gt_engine/churn_governor.py:105 ChurnGovernor` (window 40, steer 25, abort 50, repeat-abort 15, max 2, verify-steer 20) | stall/steer state; `churn_abort.json` on abort | `GT_CHURN_STEER`/`GT_VERIFY_STEER` msgs; supervisor kill on abort | action history, plan rows | yes — GT terminal authority (advisory) |
| 11 | Drift re-localization | DELIVERY POLICY | `miniswe_integration.py:6097 localization_drift_pending` (last-3 distinct searches), queues recipe; render `:5999 _render_localization_now` | pending-localization recipe | schedules a delivery | search history, retrieval | yes — re-localization trigger |
| 12 | Reactive syntax | INTELLIGENCE | `runtime_observation.py:2701 compile_transaction_artifacts`; lane `miniswe_runtime.py:880-915` | post-edit syntax artifacts (AST-level deltas) | none | edit txn | yes — live lane |
| 13 | History supersession/demotion | DELIVERY POLICY | `gt_session.py:1017 demote_overbudget_context_units`; `miniswe_runtime.py:611 collapse_superseded_context_units` | unit pointer lines; ≤8,192 B live GT units | rewrites history entries | admitted units | yes — context-budget lifecycle |
| 14 | Persistent plan | INTELLIGENCE/STATE | `gt_engine/persistent_plan/` (`baseline.py` 3% wall clamp 20–120 s; `anchors.py` caps 6/12; `checks.py`; `gate.py`) | plan rows, baseline suite ledger, bound checks | runs repo test cmd once at startup | task contract, graph anchors | yes — 17+1 mechanism |
| 15 | Plan gate | AGENT CONTROL | `gt_session.py:1627 plan_submit_gate` → `persistent_plan/gate.py:89 decide`; `drain_plan_checks` ≤30 s + baseline re-run bounded | gate decision | blocks/annotates submit (advisory: records) | plan rows, checks | yes — submit-time authority |
| 16 | Finalization | AGENT CONTROL + BENCHMARK SCAFFOLD | `gt_session.py:768 _finalization_candidate`; protected prefix `gt_session.py:229`; supervisor patch export `scripts/miniswe_gt_run.py:_write_model_patch` | finalization candidate; receipt/patch artifacts | run termination path | plan gate, supervisor | yes (gate path); scaffold receipts undecided |
| 17 | Task contract | INTELLIGENCE/STATE | `gt_engine/task_contract.py:430 extract_task_contract`; predicates `scripts/miniswe_gt_run.py:969-975` | obligations + predicate table | journal events | instruction text | yes — obligation substrate (heuristic) |
| 18 | Localization | INTELLIGENCE | `integ:5999 _render_localization_now` → `_semantic_task_start_localization` (`retrieval.hybrid_rank`, RRF k=60 over FTS5 BM25 + properties + dense ONNX), fallbacks: gateway pipeline, `_lexical_task_localization` (5000-file cap) | ranked file list (top-4, ≤1,400 B) | model-facing unit | index + ONNX assets | yes — automatic retrieval core |
| 19 | Admission | DELIVERY POLICY | `gt_session.py:1297 admit_decision_packet` + `miniswe_integration.py:5718 admit_model_visible_delivery` | admitted/refused units; `delivery_refused` journal | caps: 1,400 B/unit, 4 claims/request, 9,600 B/request, dedup, cochange ≤2, loc fire-once/3/6 | candidates, CAS | yes — the single choke point |
| 20 | Select-catalog | DELIVERY POLICY | `gt_session.py:569 observe_select_catalog_action` + SelectCatalog lifecycle | catalog selection consumed by matching Bash action | consumes offered item | delivered catalog | yes — certified catalog consumption |
| 21 | Provider-view compaction | DELIVERY POLICY | `gt_engine/context.py:262 compact_provider_view`; caps tool outputs 65,536 chars (`rt:1357-1390`) | compacted outgoing provider view | drops whole old turn groups | message history, window | yes — request shaping |
| 22 | BoundedHistoryAgent | BENCHMARK SCAFFOLD | `scripts/miniswe_gt_run.py:203 BoundedHistoryAgent` | `_compact_miniswe_history` lossless refs | history rewrite before query | Mini-SWE 2.4.6 DefaultAgent | yes — the integration shell itself |
| 23 | Output preview | DELIVERY POLICY | `output_evidence.py` head 49,152 + tail 16,384 chars spool-to-CAS | bounded output previews | observation shaping | command output | yes — evidence bounding |

## Notes

- **Removed/deprecated (C4):**
  - `gt_engine/context_packet.py` + `tests/test_context_packet.py` —
    **DELETED**. Proof: zero non-test callers across *.py/*.yml/*.yaml/
    *.json/*.md (only self, its test, fixtures, docs). HAR-74-era packet
    mechanism superseded by the compiled delivery path. Acceptance-suite
    listing in `scripts/gt_engine_acceptance.py` updated.
- **Proven LIVE, retained** (C4 candidates that failed the dead-path proof):
  - `HybridRetriever`/`HybridRepository` — `hybrid_repository.py:10,91`,
    `retrieval.py:11`; the shared retriever per AGENTS.md.
  - `EvidenceRouter` — instantiated `bridge.py:1972`, `:4157`.
  - `graph_context` — imported by `bridge.py` (×2), `graph_evidence.py`,
    `miniswe_integration.py:76`, `retrieval.py:75`.
  - `graph_lease.py` — `repository_intelligence.py:14,24,58` uses
    `GraphLease.current`.
  - `<gt-facts>` splice — live sealed-delivery mechanism
    (`miniswe_runtime.py:2413` writes envelopes; `miniswe_receipt.py:188-199`
    + `context.py:41` validate them).
  - `why_this_edge` compat — exercised by
    `tests/test_typed_graph_real_producer.py::test_why_this_edge_reads_a_producer_edge`;
    "once replaced" precondition unmet.
  - `GT_LEGACY_MODEL_VISIBLE` block — dormant-but-functional operator escape
    hatch (`miniswe_gt_run.py:1226`); not proven dead.
- All caps/constants are hand-set heuristics — no golden evaluation exists
  for any of them (audit §2, unchanged here).
- UNION-vs-MAIN deltas (audit S3/S8/S11/S12): canonical carries UNION's
  daemon-startup index, unguarded `_classify_test`, history demotion +
  supersession collapse, and persisted-into-history context additions.
