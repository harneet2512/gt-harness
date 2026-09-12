# Handoff — 2026-09-12 — 10-task smoke analysis + graph-rebuild livelock

Branch: `codex/context-plan-integrity`. HEAD at dispatch: `a9c74095`
(attest-job harness install fix). `595c25b0` added the per-task metrics
extractor after dispatch; the smoke is pinned to `a9c74095` at plan time and
unaffected by it.

## Run record

| Item | Value |
|---|---|
| Workflow run | `34701523365` |
| Stage | `subset:` first 10 canonical tasks, 1 trial each |
| Readiness bound | `34701117160` (provider-free acceptance, green on `a9c74095`) |
| Rehearsal | `34701091848` green on `a9c74095` |
| Route | `deepseek/deepseek-v4-flash-0731` via OpenRouter, provider `relace` only, `allow_fallbacks: false`, `require_parameters: true` |
| GT mode | `advisory` (non-enforcing; required for baseline comparability) |
| Agent loop cap | 5400s; task budget 6900s (agent + verifier + teardown) |
| Result | **4/10 solved, 8 graded**; attestation FAIL |

Artifacts: `/d/tmp/smoke10/` (per-task dirs +
`attestation/attestation.json`). Metrics: `scripts/smoke_task_metrics.py`
(`595c25b0`) — per-task tokens (in/out/cached), provider-wait vs agent-gap
decomposition, GT delivered/refused context bytes, graph-build ms, deliveries,
churn. No cost accounting (per instruction; OpenRouter discount applies).

## Offline comparison (frozen DeepSWE trajectories, same 10 tasks)

Source: `/d/Downloads/deepswe_v1_deepseek_trajectories_20260710_231134`,
4 trials/task, per-trial pass@1 **5/40 = 0.1250** — a hard subset.

| Task | Offline | GT-on | Verdict |
|---|---|---|---|
| abs-module-cache-flags | 2/4 | solved | match (held) |
| adaptix-name-mapping-aliases | 0/4 | churn_abort | censored |
| arktype-json-schema-refs-dependencies | 0/4 | budget kill, ungraded | censored |
| csstree-shorthand-expansion-compression | 0/4 | **solved** | **positive flip** |
| boa-hierarchical-evaluation-cancellation | 0/4 | setup_error | censored (index timeout) |
| abs-stepped-slices | 1/4 | solved | match (held) |
| aiomonitor-task-snapshots-diff | 0/4 | graded, unsolved | match |
| awilix-async-container-initialization | 2/4 | solved | match (held) |
| katex-multicolumn-array-spans | 0/4 | graded, unsolved | match |
| fd-deterministic-multi-key-sorting | 0/4 | graded, unsolved | match |

Single GT-on trial vs 4 offline trials per task — exploratory, not causal.
No regressions: every task the baseline ever passed was solved.

## Failure classification (the part that matters)

| Task | Terminal | Class | Detail |
|---|---|---|---|
| arktype | budget kill | **wiring/efficiency defect** | 82.4 min graph-rebuild livelock, below |
| adaptix | exit 7 `churn_abort` | typed governor abort | localization pinned to stale revision → every `localization_fire_once` delivery refused → agent looped identical 735KB contexts → governor aborted at ~1540s. Journals conserved (686KB sealed). Currently misreported as `runner_setup_or_execution_failed` |
| boa | exit 6 `setup_error` | honest fail-closed | `BenchmarkGraphRequired(GT_INDEX_TIMEOUT)` — initial index exceeded the hard 600s cap on a large Rust repo; GT refused to run GT-on without the mechanism under test |
| abs-stepped | solved | minor receipt gap | `treatment_provider_receipt_count_mismatch` — one provider call's receipt off |
| katex | unsolved | telemetry gap | `treatment_dense_index_not_ready` — dense embedding index never became ready |
| aiomonitor, awilix, csstree, abs-module, fd | — | clean | receipts verify |

Solved tasks still show `verified=false` where the agent submitted with
undischarged plan obligations — advisory mode records this honestly and the
attestation's `product_completion_unverified`/`product_unmet_predicates`
errors are the designed fail-closed record, not wiring bugs (asserted by
`test_unverified_completion_and_unmet_predicates_fail_closed`).

## The arktype graph-rebuild livelock (primary finding)

Evidence: `graph_build_mode` journal events, `D:/tmp/smoke10/gt-harness-
deepswe20-task-3-*/.../events.jsonl`.

- 23 builds, `elapsed_ms` total **4,946,779 ms = 82.4 min** of a ~110-min task.
- Builds 1-4: `incremental` (~185-230s each).
- Builds 5-22: **`full` with `reason=parent_graph_missing`, ~3-4.7 min each,
  ~3.2 min apart, all naming the same dead parent
  `revisions/d434c195…/graph.db` rev `eb8c35ce`** — 19 in a row.
- `dirty_path_count` grew monotonically 8 → 43 (dirty set never cleared).
- Build 23 finally froze a new parent (`6cb1d2d1`) and amended normally.
- Scheduler/coalescing worked correctly: 3 `scheduled`, 96 `already_running`,
  82 `coalesced`, 182 `already_pending` of 363 `graph_refresh_scheduled`.

Mechanism (code-verified):

1. Edit bumps `EngineState.source_revision` → refresh scheduled; request frozen
   with `parent_graph_path = engine_state.graph_path`
   (`miniswe_integration.py:1757`).
2. Build runs ~3.5 min on a ~470k-edge graph. Agent edits every ~18s →
   `source_revision` moves during the build.
3. Build finishes → owner `poll()` → `publish_graph` **refuses**:
   `source_revision != current` (`engine_state.py:172-176`,
   `source_revision_superseded`). State adoption never happens —
   `graph_path` stays at the old revision, overlay/dirty set not cleared.
4. But the file-level publish inside `ensure_index_with_receipt` already wrote
   the revision dir and ran `_prune_superseded_revisions(live=new)`
   (`indexer.py:1140`, retention = live + 1 superseded + referenced/pinned).
   Two consecutive un-adopted publishes → the revision `engine_state`
   still names is 2 generations back → **pruned**.
5. Next freeze reads the now-dead path → `parent_graph_missing`
   (`indexer.py:1676-1677`) → full rebuild → also superseded → repeat forever.

The structural defect: **file-level publication and pruning run for builds the
adoption gate will refuse.** Prune ordering assumes file-publish ≈ adoption;
the supersession gate violates that, and retention then deletes the exact
parent every future build needs — converting a staleness problem into a
permanent full-rebuild livelock. A run doing steady edits on a large repo
cannot win: builds must beat the edit cadence to ever be adopted, and 3.5-min
builds never beat 18s edits.

adaptix's `semantic_localization_source_revision_mismatch` is the same family:
delivered localization is pinned to a revision the graph/state no longer
matches, so every localization delivery refused and the agent looped on a dead
capability until the churn governor fired.

## Fix list (ranked, not yet implemented)

1. **Pin the state-named revision against pruning.** `_pinned_revisions`
   already honors `pinned.json` markers (`indexer.py:1119-1137`); write one
   into the adopted-current revision (or treat `engine_state.graph_path` as
   implicitly pinned). Breaks the livelock: parent stays on disk → amends
   keep working → ~13s builds instead of ~4-min fulls.
2. **Don't prune for un-adopted publishes.** Either refuse file-publish when
   input is already superseded, or mark un-adopted revisions prunable-first
   so retention never deletes the named parent. The prune's `live` argument
   must be the *adopted* graph, not the last-written file.
3. **Drop/re-freeze stale pending builds.** A pending request whose
   `source_revision` is already superseded at `_start_locked` time builds a
   graph that cannot be adopted; re-freeze against current state or discard
   in favor of the next schedule.
4. **Typed failure classification.** `churn_abort` is a deliberate
   runtime-control outcome, not `runner_setup_or_execution_failed`; report
   it distinctly so censored-vs-failed stays honest in cohort summaries.
5. **Large-repo index policy.** boa hit `_INDEX_TIMEOUT_SECONDS = 600`
   (`indexer.py:83`) on initial index → zero coverage. Options: raise the
   cap, resumable/chunked initial build, or accept as typed coverage gap —
   policy decision, not a bug.
6. **Dense-index readiness.** katex's `treatment_dense_index_not_ready` —
   check whether the embedding budget (60s rebuild cap) starved it or the
   initial build timed out.

## Proven vs not (evidence discipline)

- **Proven deterministically** (free gates on `a9c74095`): 21/21 feature
  matrix cells, installed rehearsal end-to-end verified completion, full
  serial suite.
- **Proven live this run**: real provider route under 10× concurrency, graph
  delivery at scale, churn governor abort + journal conservation, fail-closed
  index refusal, attestation error taxonomy separating wiring from outcomes.
- **Not proven**: uplift (no paired same-model GT-off arm in this run —
  the only honest claim is the absolute 4/10 plus the csstree flip vs a
  different-model frozen baseline); dense-index readiness; large-repo
  indexing; verified-completion under advisory mode (agents submit without
  discharging obligations — expected, and the seal records it).

## What's left before the next paid dispatch

- Fix items 1-3 (livelock) with a regression test reproducing
  prune-under-named-parent, then re-run free gates — **do not dispatch paid
  until the rebuild storm is dead**; on this evidence it can consume >70% of
  a task's budget on large repos.
- Item 4 (classification) before any cohort reporting.
- Decide item 5 (index cap) — it is a coverage decision.
- Provider-free acceptance on the fix SHA, then a repeat of this 10-task
  subset is the cheapest comparable re-measurement.

## Review surface (for external review of `595c25b0`/`a9c74095`)

- `gt_engine/graph_coordinator.py` — schedule/coalesce/adoption boundary.
- `gt_engine/indexer.py` — `_ensure_index_incremental_unlocked` (:1656),
  `_prune_superseded_revisions` (:1140), `_referenced_revisions`/`_pinned_revisions`
  (:1083-1137), `RETAINED_SUPERSEDED_REVISIONS=1` (:1080), index timeout (:83).
- `gt_engine/miniswe_integration.py` — `_frozen_graph_input` (:1740-1759),
  `_build_frozen_graph` (:1780-1871), close/drain ordering (:2091-2107).
- `gt_engine/engine_state.py` — `publish_graph` supersession refusal (:172-183).
- `scripts/attest_deepswe.py` — fail-closed taxonomy; `product_*` = honest
  outcome, `canonical_*`/`receipt_*`/`adapter_*` = wiring.
- `scripts/smoke_task_metrics.py` — new metrics extractor (`595c25b0`).
- `.github/workflows/deepswe_gt_harness_product_p0731.yaml` — attest-job
  harness install (`a9c74095`).
