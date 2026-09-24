# CANONICAL GT — verified implementation

_Rendered 2026-09-24T04:40:42Z by `scripts/canonical/render_har90_section.py` from the canonical artifacts. The 2026-09-22 snapshot below is **superseded by this section for implementation facts**._

## A — Source state

| artifact | identity |
|---|---|
| harness | `canonical/gt-har90` @ `c5f1d12158194946e4baf8f0b42fb43bdbb167d0` |
| groundtruth (producer source) | `1e83ea687bf2df5d9a168b2bc2c77ea3fd990307` tree `eb3b81c5980831cc84becd8729fe134e334febb6` |
| wheel | `groundtruth_mcp-1.0.0-py3-none-any.whl` sha256 `658cad06f1ac450c7c707a8a108b493f79c3d0121a5201150015b786dc6086dc` |
| producer binary (vendored linux-amd64) | sha256 `b00914248c737abc86a1845ab27bc4da6ea80131679b36ea44c58908e7f52e35` |
| producer build-info | commit `1e83ea687bf2df5d9a168b2bc2c77ea3fd990307` toolchain `go1.22.2` tags `netgo,osusergo,sqlite_fts5` schema `v15.4-callsite-actuals` |
| producer capabilities | `atomic_graph_publication`, `batch_parser_node_reuse_v1`, `call_resolution_v2`, `data_access_edges_v1`, `framework_surface_resolution_v1`, `incremental_stale_suppression`, `parse_failure_accounting`, `parser_inspection_v1`, `retained_call_candidates`, `source_revision_meta_v1`, `versioned_query_policy` |

Wheel↔source correspondence: `scripts/verify_wheel_source.py` PASS — 328 files byte-identical to the producer tree at the recorded commit. The vendored binary is a native WSL2/musl build of the same commit (uncertified path — no pinned docker builder available; `builder-identity.json` records the real toolchain).

Reviewer prerequisites for reproducing V-A6/V-C1/V-F from this section on a fresh worktree:

- `GROUNDTRUTH_ROOT=/d/gt-canonical-producer` (or the equivalent producer worktree path) must be exported before `python scripts/generate_gt_finalstand.py --check`; its default `<harness-parent>/Groundtruth` resolves to a stale tree on this host and the check fails closed against it.
- The producer selection regex is `Convergence|Batch|W1|Source|Route|Api|Framework` — the `Batch` alternative is required, otherwise `TestBatchAmendConvergesToCleanRebuild` (the flagship batch-amend convergence test) is silently never selected. The pre-erratum regex in earlier snapshots lacked it.
- The canonical suite resolves a runnable producer through `GT_INDEX_BINARY`, `C:\gt-smoke-a6\gt-index-new.exe` on Windows, PATH, the vendored linux-amd64 binary on WSL, or a stamped `go build -tags sqlite_fts5` of `vendor/gt-index-src`; with none of these the graph-bound tests skip with the named reason.

## B — Architecture

One canonical implementation, two consumers. `EngineState` owns current/graph source revisions, the edit overlay, omissions, and graph completeness/currentness; `GTSession` owns context-unit admission/supersession ledgers (each unit carries its `source_revision`; `unit_state`/`is_stale` expose staleness internally). `ExternalStateStore` persists append-only journal events + CAS blobs. Typed actions run one path — `build_action_request` → `execute_typed_action` → vendored `groundtruth.runtime.deterministic_queries` — shared by the model-facing `groundtruth` tool and every host-side facade. Model-facing admission remains centralized in `GTSession.admit_decision_packet` / `MiniSweAdapter.admit_model_visible_delivery`; no facade emits model-visible text or invokes admission. Edits flow `capture_workspace/diff_workspace → EditTransaction → engine_state.apply_transaction + synchronous batch amend → certified re-publication (copy-of-parent; the published parent stays immutable); uncertifiable parents refuse by name and fall to a clean rebuild.

## C — Capability matrix

60 registry entries (31 facades, 16 certified typed kinds, 13 runtime components). Cost column is the measured p50/p95 from COSTS.json (fixture substrate); typed-kind rows carry the wire-envelope byte cost instead.

| # | Capability | Canonical implementation | Status | Depth | Internal API | Runtime trigger | Freshness | Cost | Model-facing currently? | Tests |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `freshness.index_revision` | `gt_engine/capabilities/freshness.py:index_revision` | AVAILABLE | exact | `gt_engine/capabilities/freshness.py:index_revision` | none | edit/graph_revision | p50 0ms / p95 0ms | no | 1 ref |
| 2 | `freshness.graph_state` | `gt_engine/engine_state.py:EngineState.query_snapshot` | AVAILABLE | exact | `gt_engine/capabilities/freshness.py:graph_state` | none | edit/graph_revision | p50 0ms / p95 0ms | no | 1 ref |
| 3 | `freshness.amend_state` | `gt_engine/miniswe_integration.py:MiniSweAdapter._sync_amend_graph` | AVAILABLE | exact | `gt_engine/capabilities/freshness.py:amend_state` | none | edit/graph_revision | p50 0ms / p95 0ms | no | 1 ref |
| 4 | `freshness.fallback_state` | `gt_engine/miniswe_integration.py:MiniSweAdapter._recovery_build_inline` | AVAILABLE | exact | `gt_engine/capabilities/freshness.py:fallback_state` | none | edit/graph_revision | p50 0ms / p95 0ms | no | 2 refs |
| 5 | `freshness.unit_state` | `gt_engine/gt_session.py:GTSession.unit_state` | AVAILABLE | exact | `gt_engine/capabilities/freshness.py:unit_state` | none | edit | p50 0ms / p95 0ms | no | 3 refs |
| 6 | `localization.lexical_search` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | exact | `gt_engine/capabilities/localization.py:lexical_search` | typed_on_request | graph_revision | p50 60ms / p95 66ms | yes | 2 refs |
| 7 | `localization.hybrid_rank` | `gt_engine/retrieval.py:hybrid_rank` | MODEL_FACING | heuristic | `gt_engine/capabilities/localization.py:hybrid_rank` | gateway_auto:localization | edit/graph_revision | p50 13ms / p95 17ms | yes | 4 refs |
| 8 | `localization.definition` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/localization.py:definition` | typed_on_request | graph_revision | p50 48ms / p95 63ms | yes | 2 refs |
| 9 | `localization.references` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/localization.py:references` | typed_on_request | graph_revision | p50 46ms / p95 52ms | yes | 1 ref |
| 10 | `structure.callers` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/structure.py:callers` | typed_on_request | graph_revision | p50 46ms / p95 51ms | yes | 1 ref |
| 11 | `structure.callees` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/structure.py:callees` | typed_on_request | graph_revision | p50 55ms / p95 64ms | yes | 1 ref |
| 12 | `structure.symbol_context` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/structure.py:symbol_context` | typed_on_request | graph_revision | p50 56ms / p95 63ms | yes | 1 ref |
| 13 | `structure.processes` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/structure.py:processes` | typed_on_request | graph_revision | p50 55ms / p95 62ms | yes | 2 refs |
| 14 | `structure.communities` | `gt_engine/capabilities/structure.py:communities` | AVAILABLE | partial | `gt_engine/capabilities/structure.py:communities` | none | graph_revision | p50 1ms / p95 1ms | no | 2 refs |
| 15 | `structure.framework_relationships` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/structure.py:framework_relationships` | typed_on_request | graph_revision | p50 52ms / p95 56ms | yes | 3 refs |
| 16 | `analysis.cfg` | `groundtruth.runtime.cfg_store:analyze_stored` | AVAILABLE | partial | `gt_engine/capabilities/analysis.py:cfg` | none | graph_revision | p50 65ms / p95 80ms | no | 3 refs |
| 17 | `analysis.reaching_definitions` | `groundtruth.runtime.cfg_store:analyze_stored` | AVAILABLE | partial | `gt_engine/capabilities/analysis.py:reaching_definitions` | none | graph_revision | p50 64ms / p95 158ms | no | 2 refs |
| 18 | `analysis.control_dependence` | `groundtruth.runtime.cfg_store:analyze_stored` | AVAILABLE | partial | `gt_engine/capabilities/analysis.py:control_dependence` | none | graph_revision | p50 63ms / p95 67ms | no | 2 refs |
| 19 | `analysis.slice` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/analysis.py:slice` | typed_on_request | graph_revision | p50 53ms / p95 55ms | yes | 2 refs |
| 20 | `analysis.callable_values` | `gt_engine/capabilities/analysis.py:callable_values` | AVAILABLE | partial | `gt_engine/capabilities/analysis.py:callable_values` | none | graph_revision | p50 1ms / p95 1ms | no | 2 refs |
| 21 | `analysis.taint` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/analysis.py:taint` | typed_on_request | graph_revision | p50 53ms / p95 57ms | yes | 1 ref |
| 22 | `change.edit_transaction` | `gt_engine/miniswe_integration.py:MiniSweAdapter.record_edit_transaction` | AVAILABLE | partial | `gt_engine/capabilities/change.py:edit_transaction` | none | edit | p50 0ms / p95 0ms | no | 1 ref |
| 23 | `change.patch_impact` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/change.py:patch_impact` | typed_on_request | graph_revision/edit | p50 58ms / p95 60ms | yes | 1 ref |
| 24 | `change.route_impact` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/change.py:route_impact` | typed_on_request | graph_revision | p50 50ms / p95 54ms | yes | 1 ref |
| 25 | `change.shape_change` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `gt_engine/capabilities/change.py:shape_change` | typed_on_request | graph_revision | p50 50ms / p95 53ms | yes | 2 refs |
| 26 | `change.affected_tests` | `gt_engine/miniswe_covering.py:_symbols_for_files` | AVAILABLE | partial | `gt_engine/capabilities/change.py:affected_tests` | none | graph_revision/edit | p50 5ms / p95 7ms | no | 2 refs |
| 27 | `runtime.last_test_result` | `gt_engine/miniswe_integration.py:MiniSweAdapter.record_execution_evidence` | MODEL_FACING | partial | `gt_engine/capabilities/runtime.py:last_test_result` | gateway_auto:execution_evidence | execution | p50 0ms / p95 0ms | yes | 1 ref |
| 28 | `runtime.covering_tests` | `gt_engine/miniswe_covering.py:_symbols_for_files` | AVAILABLE | partial | `gt_engine/capabilities/runtime.py:covering_tests` | none | graph_revision/edit | p50 5ms / p95 5ms | no | 1 ref |
| 29 | `runtime.failure_fingerprint` | `gt_engine/bridge.py:failure_fingerprint` | AVAILABLE | partial | `gt_engine/capabilities/runtime.py:failure_fingerprint` | none | execution | p50 0ms / p95 1ms | no | 1 ref |
| 30 | `runtime.repeated_failure_state` | `gt_engine/miniswe_integration.py:MiniSweAdapter.note_failure_fingerprint` | MODEL_FACING | exact | `gt_engine/capabilities/runtime.py:repeated_failure_state` | steer | execution | p50 0ms / p95 0ms | yes | 3 refs |
| 31 | `runtime.verification_state` | `gt_engine/miniswe_integration.py:MiniSweAdapter._classify_execution_vs_baseline` | MODEL_FACING | partial | `gt_engine/capabilities/runtime.py:verification_state` | gateway_auto:execution_evidence | execution | p50 0ms / p95 0ms | yes | 2 refs |
| 32 | `kind.exact_literal_search` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | exact | `localization.lexical_search` | typed_on_request | graph_revision | env 6149B / ans 899B | yes | 2 refs |
| 33 | `kind.syntax` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | exact | `—` | typed_on_request | graph_revision | env 3873B / ans 234B | yes | 2 refs |
| 34 | `kind.patch_impact` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `change.patch_impact` | typed_on_request | graph_revision | env 4850B / ans 485B | yes | 1 ref |
| 35 | `kind.verification_status` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | execution_specific | `—` | typed_on_request | execution | env 3288B / ans 41B | yes | 1 ref |
| 36 | `kind.definition` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `localization.definition` | typed_on_request | graph_revision | env 3983B / ans 294B | yes | 1 ref |
| 37 | `kind.references` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `localization.references` | typed_on_request | graph_revision | env 7038B / ans 1296B | yes | 1 ref |
| 38 | `kind.callers` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `structure.callers` | typed_on_request | graph_revision | env 5096B / ans 666B | yes | 2 refs |
| 39 | `kind.symbol_context` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `structure.symbol_context, structure.callees, structure.framework_relationships` | typed_on_request | graph_revision | env 4875B / ans 598B | yes | 1 ref |
| 40 | `kind.processes` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `structure.processes` | typed_on_request | graph_revision | env 3290B / ans 71B | yes | 2 refs |
| 41 | `kind.route_map` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `structure.framework_relationships` | typed_on_request | graph_revision | env 7638B / ans 1542B | yes | 2 refs |
| 42 | `kind.api_impact` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `change.route_impact, structure.framework_relationships` | typed_on_request | graph_revision | env 3272B / ans 56B | yes | 1 ref |
| 43 | `kind.taint` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `analysis.taint` | typed_on_request | graph_revision | env 5659B / ans 847B | yes | 1 ref |
| 44 | `kind.rename` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `—` | typed_on_request | graph_revision | env 5448B / ans 806B | yes | 1 ref |
| 45 | `kind.shape_check` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `change.shape_change` | typed_on_request | graph_revision | env 4623B / ans 517B | yes | 2 refs |
| 46 | `kind.tool_map` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `structure.framework_relationships` | typed_on_request | graph_revision | env 3176B / ans 30B | yes | 2 refs |
| 47 | `kind.slice` | `gt_engine/miniswe_typed_actions.py:execute_typed_action` | MODEL_FACING | partial | `analysis.slice` | typed_on_request | graph_revision | env 4678B / ans 535B | yes | 3 refs |
| 48 | `task_contract` | `gt_engine/task_contract.py:extract_task_contract` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 49 | `persistent_plan` | `gt_engine/persistent_plan/deterministic.py:build_deterministic_plan` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 50 | `plan_gate` | `gt_engine/persistent_plan/gate.py:decide` | AVAILABLE | — | `—` | gate | — | — | no | 1 ref |
| 51 | `churn_governor` | `gt_engine/churn_governor.py:ChurnGovernor.observe` | AVAILABLE | — | `—` | steer | — | — | no | 1 ref |
| 52 | `submit_finalization` | `gt_engine/gt_session.py:GTSession._finalization_candidate` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 53 | `select_catalog` | `gt_engine/gt_session.py:GTSession.prepare_select_catalog` | MODEL_FACING | — | `—` | gateway_auto:sealed | — | — | yes | 1 ref |
| 54 | `history_supersession` | `gt_engine/gt_session.py:GTSession.demote_overbudget_context_units` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 55 | `drift_relocalization` | `gt_engine/miniswe_integration.py:MiniSweAdapter.localization_drift_pending` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 56 | `reactive_syntax` | `gt_engine/runtime_observation.py:compile_transaction_artifacts` | MODEL_FACING | — | `—` | gateway_auto:sealed | — | — | yes | 2 refs |
| 57 | `recovery_suspension` | `gt_engine/miniswe_integration.py:MiniSweAdapter._count_recovery_outcome` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 58 | `cochange_priors` | `gt_engine/cochange_evidence.py:run_cochange_prior` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 59 | `context_admission` | `gt_engine/gt_session.py:GTSession.admit_decision_packet` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |
| 60 | `incremental_amend` | `gt_engine/miniswe_integration.py:MiniSweAdapter._sync_amend_graph` | AVAILABLE | — | `—` | none | — | — | no | 1 ref |

## D — Runtime matrix

Verbatim from `docs/canonical/RUNTIME_LEDGER.md` (C3):

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
| 5 | Test/build observation | INTELLIGENCE/STATE | `runtime_observation.py:2485 compile_execution_evidence`; predicate eval `miniswe_integration.py:5361 evaluate_observation`, failing `integ:6546 evaluate_failing_observation` | outcome lattice + obligation predicate status (GREEN/RED, lexical basis) | none (pure classify) | executed command text | yes — runtime truth signal |
| 6 | Execution evidence | DELIVERY POLICY | `miniswe_integration.py:2931 record_execution_evidence`; render `runtime_observation.py:290 execution_evidence_model_line` | `[GT_EXECUTION_EVIDENCE]` model line vs baseline ledger | queues model-facing unit | test/build classification | yes — certified delivery producer |
| 7 | Covering attribution | INTELLIGENCE | `gt_engine/miniswe_covering.py:144 attribute_test_failure` (no execution) | covering test↔failure attribution map | none | test observation | yes — failure evidence enrichment |
| 8 | Failure fingerprint | INTELLIGENCE/STATE | `miniswe_integration.py:6582 note_failure_fingerprint` | canonical fingerprint + recurrence counter per epoch | sets `pending_transient` | test observation | yes — recurrence detection |
| 9 | Recovery steer | AGENT CONTROL | `miniswe_integration.py:6617 prepare_recovery_delivery`; injected as extra user msg `miniswe_runtime.py:1320` | sealed `recovery` delivery + `GT_RECOVERY` text | model-facing injection (≤2/task) | fingerprint recurrence | yes — advisory steer mechanism |
| 10 | Churn governor | AGENT CONTROL | `gt_engine/churn_governor.py:105 ChurnGovernor` (window 40, steer 25, abort 50, repeat-abort 15, max 2, verify-steer 20) | stall/steer state; `churn_abort.json` on abort | `GT_CHURN_STEER`/`GT_VERIFY_STEER` msgs; supervisor kill on abort | action history, plan rows | yes — GT terminal authority (advisory) |
| 11 | Drift re-localization | DELIVERY POLICY | `miniswe_integration.py:6097 localization_drift_pending` (last-3 distinct searches), queues recipe; render `:5999 _render_localization_now` | pending-localization recipe | schedules a delivery | search history, retrieval | yes — re-localization trigger |
| 12 | Reactive syntax | INTELLIGENCE | `runtime_observation.py:2701 compile_transaction_artifacts`; lane `miniswe_runtime.py:880-915` | post-edit syntax artifacts (AST-level deltas) | none | edit txn | yes — live lane |
| 13 | History supersession/demotion | DELIVERY POLICY | `gt_session.py:1017 demote_overbudget_context_units`; `miniswe_runtime.py:611 collapse_superseded_context_units` | unit pointer lines; ≤8,192 B live GT units | rewrites history entries | admitted units | yes — context-budget lifecycle |
| 14 | Persistent plan | INTELLIGENCE/STATE | `gt_engine/persistent_plan/` (`baseline.py` 3% wall clamp 20–120 s; `anchors.py` caps 6/12; `checks.py`; `gate.py`) | plan rows, baseline suite ledger, bound checks | runs repo test cmd once at startup | task contract, graph anchors | yes — 17+1 mechanism |
| 15 | Plan gate | AGENT CONTROL | `gt_session.py:1693 plan_submit_gate` → `persistent_plan/gate.py:89 decide`; `drain_plan_checks` ≤30 s + baseline re-run bounded | gate decision | blocks/annotates submit (advisory: records) | plan rows, checks | yes — submit-time authority |
| 16 | Finalization | AGENT CONTROL + BENCHMARK SCAFFOLD | `gt_session.py:768 _finalization_candidate`; protected prefix `gt_session.py:229`; supervisor patch export `scripts/miniswe_gt_run.py:_write_model_patch` | finalization candidate; receipt/patch artifacts | run termination path | plan gate, supervisor | yes (gate path); scaffold receipts undecided |
| 17 | Task contract | INTELLIGENCE/STATE | `gt_engine/task_contract.py:430 extract_task_contract`; predicates `scripts/miniswe_gt_run.py:969-975` | obligations + predicate table | journal events | instruction text | yes — obligation substrate (heuristic) |
| 18 | Localization | INTELLIGENCE | `integ:5999 _render_localization_now` → `_semantic_task_start_localization` (`retrieval.hybrid_rank`, RRF k=60 over FTS5 BM25 + properties + dense ONNX), fallbacks: gateway pipeline, `_lexical_task_localization` (5000-file cap) | ranked file list (top-4, ≤1,400 B) | model-facing unit | index + ONNX assets | yes — automatic retrieval core |
| 19 | Admission | DELIVERY POLICY | `gt_session.py:1363 admit_decision_packet` + `miniswe_integration.py:5718 admit_model_visible_delivery` | admitted/refused units; `delivery_refused` journal | caps: 1,400 B/unit, 4 claims/request, 9,600 B/request, dedup, cochange ≤2, loc fire-once/3/6 | candidates, CAS | yes — the single choke point |
| 20 | Select-catalog | DELIVERY POLICY | `gt_session.py:569 observe_select_catalog_action` + SelectCatalog lifecycle | catalog selection consumed by matching Bash action | consumes offered item | delivered catalog | yes — certified catalog consumption |
| 21 | Provider-view compaction | DELIVERY POLICY | `gt_engine/context.py:262 compact_provider_view`; caps tool outputs 65,536 chars (`rt:1357-1390`) | compacted outgoing provider view | drops whole old turn groups | message history, window | yes — request shaping |
| 22 | BoundedHistoryAgent | BENCHMARK SCAFFOLD | `scripts/miniswe_gt_run.py:203 BoundedHistoryAgent` | `_compact_miniswe_history` lossless refs | history rewrite before query | Mini-SWE 2.4.6 DefaultAgent | yes — the integration shell itself |
| 23 | Output preview | DELIVERY POLICY | `output_evidence.py` head 49,152 + tail 16,384 chars spool-to-CAS | bounded output previews | observation shaping | command output | yes — evidence bounding |
| 24 | Unit freshness (C5) | INTELLIGENCE/STATE | `gt_session.py:1169 unit_state`, `:1221 is_stale`; facade `gt_engine/capabilities/freshness.py` (`index_revision`, `graph_state`, `unit_state`) | per-unit `source_revision` vs `EngineState.source_revision` → current/stale/undecidable | none — read-only state | admitted context units, EngineState | yes — freshness surface for integration |

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

## E — Known limitations

- **Full producer-repo index is storage-blocked on this host.** The unbounded groundtruth index peaks at ~58 GiB WAL+DB and failed three times on this machine (documented in the A6 smoke notes). Bounded 600-file comparisons are green; the full-repo number is an infrastructure limit, not a correctness defect.
- **Per-file amend lane unsupported by the certified producer.** `gt-index` @1e83ea68 declares `batch_parser_node_reuse_v1` (batch amend, measured below) but not `incremental_amend_in_place`; the per-file lane never runs and every amend takes the batch path.
- **Persisted CFG coverage is per-language.** `analysis.cfg`/`reaching_definitions`/`control_dependence` return `ok` where the producer persists CFG rows (verified on Go/TS/Java/JS fixture functions) and abstain with `no_persisted_cfg` where it does not (Python fixture functions have no persisted CFG in this build) — coverage truth, never a fabricated graph.
- **Sync amend defers past 512 MiB parents by design.** Parents larger than `SYNC_AMEND_MAX_GRAPH_BYTES` skip the transaction-boundary amend and publish on the serving boundary (or refuse with a journaled reason under memory pressure). Measured timings below reflect that lane.
- **C6 resolved: option A — declare and consume.** All 32 `--ak` knobs are declared as `CliFlag`s (`eval/miniswe_agent.py`) and consumed by `gt_engine/treatment_flags.py` through the supervisor and runner: `integration_mode=off` now delivers the real GT-off path, `execution_budget_sec` drives the deadline, and knobs asserting mechanisms canonical lacks refuse by name (`treatment_knob_unsupported:<name>`) instead of dropping silently. The `certified_full`/`persistent_state_only` workflow profiles therefore refuse on this build — the treatment machinery they describe lives on another lineage.
- **Comparison workflow's treatment profiles are not runnable on canonical.** `certified_full`/`persistent_state_only` assert PES/preemptive/shadow-gate mechanisms absent from this branch; they now fail closed with named refusals. The `baseline` arm runs correctly (real GT-off).
- `freshness.index_revision`: pure EngineState/adapter attribute read; the revision value rides inside typed-action honesty envelopes but no revision report is ever delivered to the model
- `freshness.graph_state`: the same snapshot the adapter's graph_query_snapshot serves the typed path's graph binding; the state report itself is never rendered to the model
- `freshness.amend_state`: the amend mechanism is live on every recorded edit transaction and its outcome is journaled, never delivered
- `freshness.fallback_state`: recovery/rebuild streaks and the suspended flag are adapter-internal bookkeeping, journaled but never delivered
- `freshness.unit_state`: read-only freshness view; demotion/collapse consume _context_unit_rendered live-byte records, not this report
- `localization.lexical_search`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `localization.lexical_search`: exact kind: 20-match/256-byte-line caps; a '.' scope also scans .git/ (documented producer defect)
- `localization.hybrid_rank`: the model receives the compacted top-4 render (<=1400 B) the task-start/drift localization lane produces from this ranking - never the raw HybridRanking; admission fire-once and soft/hard task ceilings bound redelivery
- `localization.definition`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `localization.definition`: partial semantics: name-level resolution
- `localization.references`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `localization.references`: partial semantics: name-level resolution; bands silently keep 20 rows while labelling the answer exact (documented wheel defect)
- `structure.callers`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `structure.callers`: partial semantics: name-level resolution; 20 rows per band (documented wheel defect)
- `structure.callees`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `structure.callees`: no callee kind exists; surfaces the certified symbol_context answer's callees band verbatim
- `structure.symbol_context`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `structure.symbol_context`: partial semantics: name-level resolution
- `structure.processes`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `structure.processes`: partial semantics
- `structure.communities`: read-only lookup over producer-published communities/community_members tables; consumed downstream by graph_context.build_graph_projection - no model-facing render of community data observed
- `structure.framework_relationships`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `structure.framework_relationships`: target shape picks the kind (path-like -> route_map, symbol -> symbol_context); kind override is constrained to route_map, api_impact, tool_map
- `analysis.cfg`: wheel stored-CFG analysis over the persisted graph; abstains when the symbol is unresolved, the graph is absent, or the wheel is missing; no model-facing delivery
- `analysis.reaching_definitions`: same stored-CFG pipeline as analysis.cfg; host-side only — positive coverage is the persisted-Go-CFG leg, Python abstains no_persisted_cfg
- `analysis.control_dependence`: same stored-CFG pipeline as analysis.cfg; host-side only — positive coverage is the persisted-Go-CFG leg, Python abstains no_persisted_cfg
- `analysis.slice`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `analysis.slice`: partial semantics: CFG substrate only; interprocedural hops are name-matched
- `analysis.callable_values`: reads producer-retained resolution_callsites/resolution_candidates tables; an empty candidate set is reported as an omission, never fabricated
- `analysis.taint`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `analysis.taint`: partial semantics: symbol-level CALLS reachability, not statement dataflow; several sources map to one query per source
- `change.edit_transaction`: journal row + CAS payload of the recorded edit transaction; derived syntax_result evidence is delivered through the post-edit lane but the transaction record itself is never rendered
- `change.patch_impact`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `change.patch_impact`: partial semantics: conservatively incomplete
- `change.route_impact`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `change.route_impact`: partial semantics: fixed framework manifest; MIDDLEWARE_ON not surfaced
- `change.shape_change`: host-side facade shares the certified pipeline (capabilities/_query.run_typed -> execute_typed_action); the facade object itself emits no model-facing text - delivery happens only when the model selects the groundtruth tool
- `change.shape_change`: partial semantics: TS interface members are not counted, so a class missing a method can pass vacuously (documented defect)
- `change.affected_tests`: selection only: _symbols_for_files + wheel select_covering_tests compose the same front half the live covering lane runs pre-execution; the lane continues into run_covering_tests and only a failing verdict becomes a covering_red dose - the raw selection is never delivered
- `runtime.last_test_result`: the model receives the rendered [GT_EXECUTION_EVIDENCE] line queued as an execution_evidence candidate; the journal row, CAS artifact and raw output the facade returns stay host-side
- `runtime.covering_tests`: delegates to change.affected_tests; same selection-only limit - no execution, no delivery of the selection itself
- `runtime.failure_fingerprint`: the live recovery-steer lane fingerprints via the wheel's canonical_test_failure_fingerprint, a different implementation - bridge.failure_fingerprint is production-parity code used by the bridge episode machinery and is never emitted to the model
- `runtime.repeated_failure_state`: the model receives only the bounded GT_RECOVERY steer (<=2 per task) carrying the fingerprint hash and workspace epoch; the raw recurrence map and delivered counter stay host-side
- `runtime.verification_state`: recorded baseline-vs-current classification, not a fresh verdict; the model sees the baseline clause inside the [GT_EXECUTION_EVIDENCE] line, not the state dict
- `kind.exact_literal_search`: exact semantics; explicit scopes only, 20 matches, 256 B/line; scope '.' also scans .git/ (documented producer defect)
- `kind.syntax`: exact semantics; parse-only over go/js/py/rb/ts certified extensions; no facade exposes it
- `kind.patch_impact`: partial semantics: conservatively incomplete, augments only
- `kind.verification_status`: execution_specific semantics bound to command and revision; no facade exposes it (runtime.verification_state reads the recorded journal classification instead)
- `kind.definition`: partial semantics: name-level resolution
- `kind.references`: partial semantics: name-level resolution; bands silently keep 20 rows while labelling the answer exact (documented wheel defect)
- `kind.callers`: partial semantics: name-level resolution; 20 rows per band (documented wheel defect)
- `kind.symbol_context`: partial semantics: name-level resolution
- `kind.processes`: partial semantics
- `kind.route_map`: partial semantics: fixed framework manifest; MIDDLEWARE_ON not surfaced; an API_CALL-only route yields anchor line 0 and the answer becomes producer_not_supported (documented wheel defect)
- `kind.api_impact`: partial semantics: fixed framework manifest; MIDDLEWARE_ON not surfaced
- `kind.taint`: partial semantics: symbol-level CALLS reachability - not dataflow and not a sound over-approximation
- `kind.rename`: partial semantics; no facade exposes it
- `kind.shape_check`: partial semantics: TS interface members are not counted (required_count 0), so a class missing a method can pass vacuously (documented defect)
- `kind.tool_map`: partial semantics: DECORATES is emitted only for class decorators resolving to in-repository callables, so @mcp.tool() functions are never detected (documented defect)
- `kind.slice`: partial semantics: CFG substrate only; interprocedural hops are name-matched
- `task_contract`: runner-side machinery: the obligations:task context unit is admitted through miniswe_gt_run, not the canonical runtime — extraction is verbatim-line anchored, not a semantic proof of coverage
- `persistent_plan`: runner-side machinery: the plan_cursor:task context unit is admitted through miniswe_gt_run, not the canonical runtime; rows carry checks only when a test command was discovered
- `plan_gate`: pure decision function; its verdict steers submission and is never itself model-visible text
- `churn_governor`: returns steer/abort signals the caller applies; the signal itself is not a delivery
- `submit_finalization`: runner-side machinery: the advisory is emitted through the plan-submit path in miniswe_gt_run, not the canonical runtime; observes collector-shaped commit bytes only — never commits or assesses correctness on the agent's behalf
- `select_catalog`: one certified task-start offer per run; abstains honestly on a stale or graph-incomplete bootstrap
- `history_supersession`: internal history bookkeeping: superseded/demoted units queue onto the drain and collapse to CAS pointers — demotion is not deletion and renders no standalone output
- `drift_relocalization`: pending flags only; the re-localization they trigger ships through the localization delivery path, not this mechanism
- `reactive_syntax`: syntax verdicts are exact producer-anchored parses; graph caller rows are graph_recorded, never claimed complete
- `recovery_suspension`: suspends the rebuild loop after the episode cap; consumers degrade to the no-graph path for the rest of the episode
- `cochange_priors`: co-change improves rank but can never certify a delivery by itself; quiet on repositories without git history
- `context_admission`: the admission owner every delivered lane passes through; its decisions are admission records, not delivered text
- `incremental_amend`: batch-path amend only: the certified producer declares batch_parser_node_reuse_v1, not incremental_amend_in_place; parents past SYNC_AMEND_MAX_GRAPH_BYTES defer to the coordinator's clock, and uncertifiable parents refuse by name

## F — Removed/deprecated paths

C4 dead-path proofs (live-caller search before every removal):

- Removed: `gt_engine/context_packet.py`, `tests/test_context_packet.py`, and its acceptance-suite listing reference — zero non-test callers (`49efeede`).
- Retained as live: `HybridRetriever`/`HybridRepository`, `EvidenceRouter`, `graph_context`, `graph_lease`, `<gt-facts>` sealed delivery, `why_this_edge`, the `GT_LEGACY_MODEL_VISIBLE` escape hatch — each has live callers or is a functioning flag path (documented dormant, not dead).

## G — Canonical source map

| location | role |
|---|---|
| `D:\gt-canonical` (this checkout) | canonical harness worktree, branch `canonical/gt-har90` |
| `D:\gt-canonical-producer` | producer source worktree, branch `canonical/gt-har90` @ `1e83ea68` |
| `vendor/` | vendored binary + wheel + producer source export (provenance-verified @873b6a1e) |
| `docs/canonical/` | `RUNTIME_LEDGER.md` (C3), `COSTS.json`/`.md` (G), `MAIN_PORT_LEDGER.md`, `TYPED_SURFACE.md`, this section |
| `tests/canonical/` | F suite: polyglot fixture, goldens, static/determinism/runtime/cross-layer/registry/facade/runtime-components/treatment-flags tests |
| `gt_engine/capabilities/` | D API + E registry |
| `D:\gt-harness` | shared worktree (other branches) |
| `D:\gt_freeze\2026-09-22-canonical` | frozen plan + audit |
| `C:\gt-smoke-a6` | smoke artifacts + producer binaries |

## H — Integration-ready interfaces (the D API)

Every function returns one `CapabilityResult` (`gt_engine/capabilities/_query.py`): `capability`, `status` (`ok|partial|abstain|unavailable|error`), `answer`, `omissions`, `limitations`, `semantics`, `graph_revision`, `source_revision`, `fresh`, `cost` (elapsed ms + output bytes), `provenance`.

```
gt_engine/capabilities/localization.py:
    lexical_search(query, scope)
    hybrid_rank(query, k)
    definition(symbol, hints)
    references(symbol, hints)
gt_engine/capabilities/structure.py:
    callers(symbol, depth)
    callees(symbol)
    symbol_context(symbol)
    processes(concept)
    communities(file)
    framework_relationships(target)
gt_engine/capabilities/analysis.py:
    cfg(function)
    reaching_definitions(function)
    control_dependence(function)
    slice(symbol, line, direction, interprocedural)
    callable_values(symbol)
    taint(sources, sinks)
gt_engine/capabilities/change.py:
    edit_transaction(latest)
    patch_impact(edited_files)
    route_impact(route|handler)
    shape_change(symbol)
    affected_tests(files)
gt_engine/capabilities/runtime.py:
    last_test_result()
    covering_tests(files)
    failure_fingerprint(observation=None)
    repeated_failure_state()
    verification_state()
gt_engine/capabilities/freshness.py:
    index_revision()
    graph_state()
    amend_state()
    fallback_state()
    unit_state(unit_id)
```

---

**DONE — CANONICAL GT READY** pending independent verifier pass. STOP: no Mini-SWE integration design until the verifier reruns the checks and posts VERIFIED PASS.
