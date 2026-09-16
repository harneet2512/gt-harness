# Offline ability audit — graph/index/enrichment lifecycle under adverse timing (D6 lane)

- **Date:** 2026-09-15 (local run, this working tree)
- **Branch:** `codex/phase5-harness-proof` (shared worktree, no commits made)
- **Scope:** revision pinning/pruning, nested enrichment derivations
  (lsp-on-lsp, merge-on-lsp), restart while an older dependency is still
  live, out-of-order candidate completion, rapid edits against in-flight
  LSP work, publication/disk failure containment, memory pressure at seal,
  index completion before vs after the first edit, interruption before
  publication / after admission / during finalization, and seal-time
  certification across every state the pruner can leave — spec §D6 plus
  the `enrichment_cited_base_pruned` regression surface from SWE-Live run
  `35016130850`.
- **Evidence:** `tests/test_audit_graph_lifecycle.py` — 30 tests
  (27 passing, 3 `xfail` documenting confirmed cross-boundary defects).
  Mutation-sensitivity was verified by reverting each production fix in
  place and watching the tests go red.
- **Method:** certification paths run the real `certify_graph_artifact` /
  `certify_lsp_candidate` / `certify_scoped_merge` / `merge_lsp_candidate`
  producers over real sqlite graph files; adapter timing cases run the real
  `MiniSweAdapter` journal, epoch tracker, salvage scheduler and
  `ProviderWaitScheduler` drain; only the gt-index subprocess build itself
  is stubbed (the producer binary cannot run in this environment — see
  UNPROVEN).

## Ability verdicts

| Ability / lane | Verdict | Evidence |
|---|---|---|
| Retention prunes only unreferenced superseded revisions | **WORKING OFFLINE** | `test_nested_chain_certifies_and_survives_a_later_prune` — rev1 survives because `enrichments/lsp-a` cites it, while an uncited sibling is provably reclaimed. Pre-existing: `tests/test_revision_pinning.py` (pins, pending/running parents, enrichment + merge citation). |
| Nested derivation certification (lsp-on-lsp, merge-on-lsp) | **WORKING OFFLINE** | `_deep_chain` builds rev1 → lsp-a → lsp-b → merge-m through the real certifiers; `test_the_chain_break_is_typed_not_raised`, `test_a_foreign_phase_base_is_still_refused` prove the failure modes stay typed (`derivation_base_*` / `lsp_base_invalid:derivation_unknown`), never raise. |
| Portable base references resolve from nested dirs | **WORKING OFFLINE** | `test_a_revision_manifest_can_also_protect_its_base`; chain manifests resolve `../../revisions/<h>/` from the citing manifest's own directory. |
| Prune faults stay contained — never eat a publication | **FIXED** | `test_ensure_index_returns_the_graph_pruning_could_not_reclaim`, `test_ensure_index_benchmark_identity_survives_a_prune_fault`, `test_refresh_index_files_reports_the_amend_pruning_could_not_reclaim`, `test_prune_survives_a_manifest_scan_that_fails_mid_iteration`. Verified mutation-sensitive by reverting the containment boundary (all four went red). |
| Unauditable manifests freeze pruning | **WORKING OFFLINE** | `test_a_manifest_that_parses_but_is_not_an_object_freezes_pruning` (parametrized over `[1,2,3]`, `"text"`, `5`, `null`), corrupt-bytes freeze inside `test_every_surviving_pruner_state_still_certifies`; mutation proof `test_mutation_dropping_the_freeze_deletes_under_corruption`. |
| Out-of-order candidate completion | **WORKING OFFLINE** | `test_out_of_order_completion_dispositions_against_current_state` — drain order does not decide; each finished leg is judged against the graph the authority names at drain time. |
| Rapid edits while a leg is in flight | **WORKING OFFLINE** | `test_rapid_edits_while_a_leg_is_in_flight_stage_the_exact_stale_set` — real edit transactions → inline amends → obsolete leg salvages with `stale_path_count == 2` (exactly the post-schedule edits). `test_an_unenumerated_edit_during_the_leg_blocks_salvage` — an incomplete transaction poisons the stale set and salvage refuses `unenumerated_edit` rather than merge on faith. |
| Startup index before the first edit | **WORKING OFFLINE** | `test_startup_index_landing_before_any_edit_adopts` — adopted, journaled `initial_index_ready`, LSP leg offered. |
| Startup index after the first edit | **WORKING OFFLINE** | `test_startup_index_landing_after_an_edit_becomes_the_amend_parent` — superseded at admission, kept as `_unadopted_graph` certified fallback, the boundary amend builds ON it, and the adoption prune protects it. |
| Interruption before publication | **WORKING OFFLINE** | `test_interruption_before_publication_seals_clean` — leg in flight at close: bounded drain, journal verifies, no post-seal writes. |
| Interruption after admission | **WORKING OFFLINE** | `test_interruption_after_admission_keeps_the_delivered_pin` — delivered pin survives every later prune; graph still certifies at seal. |
| Interruption during finalization | **WORKING OFFLINE** | `test_interruption_during_finalization_cannot_append_post_seal` — a salvage worker finishing after seal cannot append; journal row count frozen at seal and still verifies. |
| Memory pressure held to seal | **WORKING OFFLINE** | `test_close_under_memory_pressure_is_a_typed_gap_not_a_crash` — no recovery build inside pressure, all refresh events WARNING+retryable, journal verifies. Defer-window math covered by `tests/test_miniswe_integration.py` (`_index_memory_defer_until`) and guard/kill semantics by `tests/test_index_resource_guard.py`. |
| D6 end-to-end: refresh → nested enrichment → pruning/restart → final certification | **WORKING OFFLINE** | `test_d6_refresh_nested_enrichment_prune_and_seal_certifies` — real `_Sim` adapter: boundary → leg → edit race → obsolete drain → scoped-merge salvage published → seal-time leg on the merged graph published → journal verifies → capability row `lsp_promotion == WORKING`. |
| Seal-time certification across every pruner state | **WORKING OFFLINE** | `test_every_surviving_pruner_state_still_certifies` — live, retained-superseded, pinned, enrichment-cited, and freeze-everything states all re-certify. |
| Restart sweep honours live derivation references | **CONFIRMED DEFECT — UNFIXED (cross-boundary)** | `_sweep_dead_enrichments` is reference-blind — see defects. |
| Delivered enrichment pinnable at restart | **CONFIRMED DEFECT — UNFIXED (cross-boundary)** | `_pin_graph_revision` refuses `enrichments/` paths — see defects. |

## Defects fixed in this slice (owner: `gt_engine/indexer.py`)

### FIX-1 — a retention fault could eat a certified publication

`_prune_superseded_revisions` is invoked inside `ensure_index`
(`indexer.py:2043-2044`) and `refresh_index_files` (`indexer.py:3246-3247`)
inside broad `except` boundaries: any raise turned the *successful* build
into `graph = None` / `amend_refused`. On the benchmark-bound shipping path
(`miniswe_gt_run._initial_index` → `ensure_index_with_receipt`,
`reclaim=True` default) the absent graph escalates to
`BenchmarkGraphRequired` — a dead run after a good build, indistinguishable
from an indexing failure.

**Fix:** `_prune_superseded_revisions` now delegates to
`_prune_superseded_revisions_inner` inside a fail-closed containment
boundary (`indexer.py:1254-1257`). Any fault in the scan or the delete
freezes reclamation — leaks disk, never eats a certified publication and
never raises.

**Regression tests:** the four containment tests above, each injecting a
`RuntimeError` from inside the reference scan — a fault class the typed
`(OSError, ValueError)` freeze inside `_referenced_revisions` cannot catch,
so only the new boundary holds the publication. Reverting the boundary in
place turned all four red (mutation-verified).

**Layer-4 note:** the first version of these tests poisoned the scan with a
non-object manifest; after FIX-2 that fault freezes *inside* the scan and
the tests could not see the boundary at all — they were rewritten to inject
an untyped `RuntimeError`, and the reversion check confirmed sensitivity.

### FIX-2 — valid non-object JSON manifest crashed the citation scan

`_referenced_revisions` called `json.loads(...).get("derivation")`
directly; a manifest containing `[1,2,3]`, `"text"`, `5` or `null` raised
`AttributeError` (`indexer.py:1173`). A manifest the scan cannot audit has
unknowable citations, and unknowable must freeze deletion — never authorise
it, never crash it.

**Fix:** parse, then require `isinstance(body, dict)`; non-objects take the
existing typed `except (OSError, ValueError)` freeze branch, which protects
every revision directory (`indexer.py:1173-1180`).

**Regression tests:** `test_a_manifest_that_parses_but_is_not_an_object_freezes_pruning`
(4 parametrized payloads). Honest classification: with FIX-1's containment
in place this guard is defense-in-depth — an `AttributeError` would freeze
at the outer boundary anyway. The guard keeps the freeze *inside* the scan
(audited citations already collected are preserved, and any future caller
without the outer boundary is still safe), so it stays.

### CONFIRMED — enrichment-cited bases must survive retention (regression guard for run 35016130850)

`_referenced_revisions` scans `parent.parent.rglob("graph.manifest.json")`
across BOTH `revisions/*/` and `enrichments/*/` namespaces
(`indexer.py:1171`). The mutation test
`test_mutation_a_revisions_only_scan_loses_the_cited_base` restores the
pre-fix revisions-only `glob` under a live three-hop chain: the cited base
rev1 is reclaimed and seal-time certification of the merged head fails
`derivation_base_manifest_unreadable` — the exact 35016130850 signature.
With the shipped scan the same layout passes. This is the load-bearing
proof that the fix, not retention luck, protects the base.

## Defects confirmed, reported, NOT patched (owner outside this slice)

All three live in `gt_engine/miniswe_integration.py` and are kept as
`xfail(strict=False)` reproducers — they flip to xpass when the owner fixes
them.

### XB-1 — `_sweep_dead_enrichments` is reference-blind (restart kills a live chain's base)

`miniswe_integration.py:4159-4196`. The sweep keeps only the directory that
IS the live graph (`entry.resolve() == live_dir`) plus anything newer than
`_PROCESS_START`. On restart, a live *enrichment* graph (e.g. adopted
`lsp-b`) whose manifest cites `enrichments/lsp-aaaa` through
`derivation.base_graph: "../lsp-aaaa/graph.db"` loses that base: lsp-a
predates the process and is not the live directory, so it is deleted. Any
later seal-time revalidation of the live graph's derivation hits
`derivation_base_manifest_unreadable` — run 35016130850's failure, one
namespace up. Reproducer:
`test_xbound_sweep_preserves_the_live_chains_cited_enrichment_base`.
Suggested fix direction: walk the live graph's derivation references (the
same portable-reference resolution `_referenced_revisions` uses) and exempt
the cited enrichment dirs, or stop deleting any enrichment that is an
ancestor-of-record of the live graph.

### XB-2 — `_sweep_dead_enrichments` deletes previously-adopted enrichments a delivered advisory still names

`miniswe_integration.py:4184-4192`. A published enrichment superseded
before a process restart is receipted evidence: a delivered
semantic-localization advisory records its `graph_sha256`, and
`verify_runtime_receipt` demands exactly one surviving certified graph
matching it. The sweep deletes it anyway (mtime < `_PROCESS_START`, not the
live dir). Reproducer:
`test_xbound_sweep_preserves_a_previously_adopted_enrichment`.

### XB-3 — `_pin_graph_revision` refuses enrichment graphs

`miniswe_integration.py:606-609`: `if revision.parent.name != "revisions"`
returns silently, so a delivered advisory ranked from an adopted
*enrichment* graph cannot pin it — there is no protection mechanism for a
delivered enrichment at all, which is what makes XB-2 unmitigable from the
delivery side. Reproducer:
`test_xbound_a_delivered_enrichment_graph_can_be_pinned`.

## What was verified but not changed

- `_reclaim_produced_graph` (`miniswe_integration.py:3649-3683`) already
  wraps prune/discard in its own boundary — reclamation never breaks a
  boundary; a failed produce is discarded, a refused-but-successful one
  survives as fallback parent.
- `discard_revision` refuses paths outside `revisions/` and the prune's
  rename-out-of-the-scheme (`os.replace` to `.pruned-*` then rmtree) means
  an interrupted delete leaves a discoverable, harmless directory rather
  than a half-present revision that digest-matches a missing graph.
- `_poll_lsp_promotions` dispositions: `published` / `obsolete` /
  `obsolete_after_certification` / `not_publishable` /
  `no_edge_mutations` / `identity_mismatch` / `certifier_exception` /
  `certification_failed` — each journaled on the real receipt.
- Late observations after journal sealing are dropped and counted
  (`_dropped_observations`) — post-seal workers cannot corrupt journal
  conservation.
- `_lsp_epochs`/`_incomplete_edit_epoch`/`_path_edit_epochs` make the
  salvage stale-set exact: enumerated edits → merge proceeds on the precise
  set; unenumerated edits → `lsp_salvage_refused:unenumerated_edit`.
- Seal-time convergence bypasses the armed churn backoff (no further edits
  can arrive) — covered by `test_workload_simulation.py` and D6.

## Platform note (test honesty)

On Windows, an open sqlite handle inside a revision directory makes the
prune's `os.replace` fail with `PermissionError`; containment then retains
the directory — fail-closed and correct, but a missing citation protection
would masquerade as a retained dir. The tests `gc.collect()` before pruning
so a masked delete cannot pass vacuously, and each protection test asserts
an uncited sibling was actually reclaimed — proving the prune ran.

## UNPROVEN (offline limits — not claims of failure)

- **The gt-index subprocess itself never ran.** Every build boundary is
  stubbed at `_ensure_index_unlocked` / `_ensure_index_incremental_unlocked`
  or `_receipt_for_published_graph`; the producer binary is a Linux
  executable and this box has no installed Linux producer (one
  `test_index_incremental` case skipped for exactly that). Real producer
  timing, real RSS profiles, and real `-wal` behaviour are unproven here.
- **cgroup OOM kill and POSIX process-group semantics** are
  platform-skipped on Windows (`test_index_resource_guard.py` skips);
  `GT_INDEX_CGROUP_OOM` attribution is covered by unit seams, not by a live
  cgroup kill.
- **True multi-process races** — the publication lock is exercised
  in-process; concurrent processes contending on `.graph.lock` are not
  proven offline.
- **Real disk exhaustion** is simulated by injected `OSError`, not by a
  full device; ENOSPC at other phases (manifest write vs db write vs
  upload) is only partially enumerated.
- **Benchmark/model efficacy** is out of scope by design: this audit proves
  runtime evidence handling, not solve rates, provider availability, or
  receipt acceptance by the official verifier online.

## Test commands and counts

```text
python -m pytest tests/test_audit_graph_lifecycle.py -q
  -> 27 passed, 3 xfailed   (30 collected)

python -m pytest tests/test_audit_graph_lifecycle.py \
    tests/test_revision_pinning.py tests/test_index_incremental.py \
    tests/test_index_reuse.py tests/test_index_resource_guard.py \
    tests/test_scoped_merge.py tests/test_workload_simulation.py \
    tests/test_lsp_graph_certification.py tests/test_lsp_graph_publication.py \
    tests/test_lsp_promotion_wiring.py tests/test_lsp_server_staging.py \
    tests/test_lsp_watch.py -q
  -> 196 collected: all green (3 platform skips, 3 xfail)

python -m ruff check gt_engine/indexer.py tests/test_audit_graph_lifecycle.py
  -> clean

git diff --check   -> clean
```

Mutation checks performed (each reverted in place, tests went red, revert
restored):

| Mutant | Tests that catch it |
|---|---|
| Containment boundary removed (`_prune_superseded_revisions` propagates) | 4 containment tests fail |
| Scan restored to revisions-only `glob` (35016130850 shape) | `test_mutation_a_revisions_only_scan_loses_the_cited_base` + chain tests |
| Freeze dropped on unreadable manifests | `test_mutation_dropping_the_freeze_deletes_under_corruption` |

## capability_matrix-relevant entries

- `graph_revision_lifecycle` — offline: retention, pinning, citation
  protection, containment: **proven offline**; online receipt issuance
  depends on the delivered-pin path (XB-2/XB-3 weaken it for adopted
  enrichments).
- `lsp_promotion` — **proven offline** through D6 (schedule → race →
  obsolete → salvage → merge published → seal leg published → WORKING
  capability row); cross-boundary restart sweep defect reported.
- `scoped_merge_salvage` — **proven offline**; stale-set exactness and
  unenumerated-edit refusal verified.
- `restart_resume` (graph lifecycle half) — **partial**: in-run evidence
  proven; cross-process restart leaves the reference-blind enrichment sweep
  and the enrichment pin hole (XB-1..3) open.
