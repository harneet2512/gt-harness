# Handoff — 2026-09-12 — 20-task smoke analysis + post-run fixes

Branch: `codex/context-plan-integrity`. Smoke pinned to `c0b352f4`
(adoption-clock livelock fix + existence-checked publish). Post-run fix
`e3cd9475` (diagnostic canary boundary + provider fault-injection suite)
landed **after** the run sealed — the run is evidence of `c0b352f4`
behavior; do not retro-relabel.

## Run record

| Item | Value |
|---|---|
| Workflow run | `34715686102` (all 20 tasks, 1 GT-on trial each) |
| Superseded runs | `34712080523` (readiness fail-closed, $0 spent), `34712884669` (gate-one, cancelled per user direction — its aiomonitor task may have completed; excluded from analysis) |
| Readiness | provider-free acceptance green on `c0b352f4` (2nd clean pass) |
| Route | `deepseek/deepseek-v4-flash-0731` via OpenRouter, `relace` pinned |
| GT mode | `advisory` |
| Envelope | 6660s agent (`--time-budget-seconds`), 6900s job budget |
| Result | **10/20 solved, 18 graded, attestation FAIL** |
| Wall time | ~2h19m run; per-task job durations 998s–6761s, mean ~3887s |

Artifacts: `D:/gt_runs/smoke20_34715686102/` — per-task bundles +
`deepswe20-attestation.json`, `gt-audit.json`, `gt-live-gate.json`,
`metrics.json` (extractor output), `action_mix_gton.json`.

## Outcome table (official verifier, attestation-bound)

| Task | GT-on | GT-off flash (n=4) | Terminal / class | GT-on in/out tok | calls | deliv consumed |
|---|---|---|---|---|---|---|
| abs-module-cache-flags | **solved** | 4/4 | submitted_unverified → graded | unknown* | 183 | 64/104 |
| abs-stepped-slices | **solved** | 3/4 | submitted_unverified → graded | 33.9M/213.9K | 242 | 45/112 |
| actionlint-action-pinning-lint | **solved** | 4/4 | submitted_unverified → graded | 22.0M/137.2K | 184 | 43/138 |
| adaptix-name-mapping-aliases | **solved** | 1/4 | submitted_unverified → graded | 31.0M/178.5K | 201 | 96/136 |
| aiomonitor-task-snapshots-diff | ERROR | 2/4 | internal_error (canary bug, fixed `e3cd9475`) | 0.46M/31.9K | 19 | 18/21 |
| anko-default-function-arguments | **solved** | 3/4 | submitted_unverified → graded | 45.4M/213.9K | 222 | 37/120 |
| arktype-json-schema-refs-dependencies | **solved** | 2/4 | submitted_unverified → graded | 24.1M/146.0K | 193 | 62/140 |
| awilix-async-container-initialization | **solved** | **0/4** | submitted_unverified → graded | 32.5M/197.5K | 219 | 79/140 |
| bandit-incremental-cache-control | unsolved | 1/4 | graded, reward 0 | 20.2M/149.4K | 168 | 57/138 |
| bandit-interprocedural-taint-checks | **solved** | 4/4 | submitted_unverified → graded | 20.3M/159.2K | 161 | 57/113 |
| boa-hierarchical-evaluation-cancellation | ERROR | 3/4 | **timeout — agent never made a provider call** | 0/0 | 0 | 0/0 |
| clack-async-autocomplete-options | unsolved | 0/4 | graded, reward 0 | 35.5M/232.7K | 201 | 102/189 |
| claude-code-by-agents-recursive-delegation | ERROR | **4/4** | **churn_abort (governor)** | 2.3M/58.4K | 56 | 23/29 |
| csstree-shorthand-expansion-compression | unsolved | 0/4 | submitted_unverified → graded | unknown* | 134 | 38/68 |
| fd-deterministic-multi-key-sorting | **solved** | 3/4 | submitted_unverified → graded | 37.7M/184.2K | 250 | 37/138 |
| katex-multicolumn-array-spans | unsolved | 2/4 | graded, reward 0 | 51.6M/199.7K | 306 | 67/144 |
| oxvg-structural-selector-preservation | **solved** | 2/4 | submitted_unverified → graded | 69.7M/189.0K | 365 | 112/177 |
| pest-character-class-coalescing | unsolved | 1/4 | graded, reward 0 | 24.9M/157.9K | 187 | 72/152 |
| testem-bail-on-test-failure | unsolved | 0/4 | graded, reward 0 | 36.9M/202.7K | 223 | 71/152 |
| testem-per-launcher-reports | unsolved | 2/4 | graded, reward 0 | 12.1M/122.3K | 122 | 50/74 |

\* abs-module + csstree: treatment receipt write failed (journal digest
mismatch + provider conservation) — token fields unrecovered; totals below
cover the 18 accounted tasks.

## Aggregate vs same-model GT-off baseline

Baseline: `D:/tmp/ds_trials11.json` — public DeepSWE trials.json,
`deepseek-v4-flash` only, same 20 tasks, **4 trials each (80 trials)**.

| Metric | GT-on (n=1/task) | GT-off (n=4/task mean) |
|---|---|---|
| Solved | **10/20 = 50.0%** | 41/80 = **51.25%** (expect 10.25/20) |
| Wilson 95% CI | [0.299, 0.701] | [0.405, 0.619] |
| Input tokens /task | ~27.8M (18 accounted) | 19.29M |
| Output tokens /task | ~154K (18 accounted) | 100.1K |
| Provider calls /task | ~184 (18 accounted; total 3319) | ~152 steps |
| Job duration | mean ~3887s (incl. setup+verify) | 1338s agent-only — not comparable |

**Read:** solve rate is statistically indistinguishable from baseline.
GT-on costs ~+44% input tokens (delivered context is not free) and ~+54%
output tokens. No evidence of a solve-rate regression; no evidence of a
significant improvement at n=1/task.

### Per-task flips

- **Positive:** awilix 0/4→solved (real flip — never solved by baseline);
  adaptix 1/4→solved; arktype 2/4→solved (and livelock gone, see below);
  oxvg 2/4→solved.
- **Negative:** claude-code 4/4→churn_abort; boa 3/4→never-ran (infra);
  aiomonitor 2/4→infra kill (fixed post-run).
- **Noise-level misses:** katex 2/4→0, testem-per-launcher 2/4→0,
  bandit-incr 1/4→0, pest 1/4→0 — single-trial variance, undecidable at n=1.

## Did GT help? — the honest decomposition

**Direct usage evidence (audit `delivery_consumption` verdicts):**
GT deliveries were not just served — they were *consumed* (agent acted on
delivered evidence) at high rates on healthy tasks: e.g. adaptix 96/136,
awilix 79/140, oxvg 112/177, abs-module 64/104. The mechanism works.

**Action mix (GT-on trajectories, lexical classifier):** 3760 actions total
— 26% retrieval / 14% edit / 30% test / 30% other. First-edit latency was
low on healthy tasks (median ~action 24). claude-code shows the failure
signature: 48% retrieval, churn-aborted at action 54 (matches manual count
~51/54 retrieval). GT-off action-mix trajectories are NOT retrievable
(artifact host 404s) — the GT-off side uses step counts only, so the
retrieval-share comparison is single-armed; stated as limitation.

**Interpretation:** GT evidence demonstrably reached and steered the model
(consumption verdicts + dense per-decision slivers). Whether it *reduced*
exploration vs GT-off cannot be established without GT-off trajectories —
claim bounded accordingly.

## Fix-proof: the livelock is dead

arktype (the 82.4-min livelock task): **0 `source_revision_superseded`,
0 `parent_graph_missing`, 0 reclaim events** — vs the incident where every
completed build was rejected. 16 builds, 65.6 min of adopted graph work,
193 admissions answered, task **solved**. The adoption-clock reclamation
fix held under exactly the workload that previously livelocked.

## New defects surfaced (paid → now provider-free-reproducible)

1. **boa: agent never issued a provider call.** `provider_boundary_attached`
   20:26:57 → `run_terminal` 22:03:01 — ~96 min of silence, SIGKILL at
   deadline. Producer parse-cache populated throughout (883 entries);
   `contract-embeddings.sqlite` an empty 12KB shell. Pre-admission
   synchronous GT work (index/embed path) starved the agent's first call.
   **Advisory mode must degrade, never block the agent loop.** Typed
   outcome was honest (`timeout`) but the task was foreclosed.
   Root-cause follow-up needed (producer-side log not in artifact).

2. **Predicate-observation channel produces nothing.** Every task's
   treatment receipt reports `verified=False` with 7–50 unmet
   contract obligations; audit `predicate_compiled_count=0` on all 20.
   All 17 submissions → `submitted_unverified` →
   `product_completion_unverified` × 18. One systematic gap, not 17 task
   failures. Attestation `verified` is unreachable by construction until
   obligation-satisfaction events are emitted.

3. **abs-module + csstree: treatment receipt write failed** —
   `product_event_journal_digest_mismatch`,
   `provider_call/input_token/output_token_conservation_failed`,
   `provider_response_count_exceeds_attempts`. Receipt lost; tokens
   unrecovered. Grading still succeeded.

4. **aiomonitor: diagnostic canary vs task-id collision** — fixed
   `e3cd9475` (word-boundary match) + fault-injection + soak + terminal
   matrix suites (63 tests green).

5. **adaptix/awilix**: `semantic_localization_source_revision_mismatch` /
   `semantic_localization_certified_graph_missing` +
   `treatment_graph_utilisation_mismatch` +
   `treatment_graph_evidence_absent` — solved, but GT evidence chain
   incomplete.

6. **katex, testem-bail, testem-per-launcher**:
   `treatment_dense_index_not_ready` — dense index never ready (60s rebuild
   embed budget kept, per policy; may need revisiting for large repos).

7. **boa receipt artifact**: `effective_model_mismatch` is a false-positive
   when zero provider calls occur (`resolved: null` vs expected) —
   check should not fire on empty provider history.

8. **aiomonitor attribution gap**: `trajectory api_calls 17 + bootstrap 2
   != provider requests 17` — bootstrap calls not conserved in provider
   request accounting.

## Provider health

5 failed transport calls across 3,319 total (0.15%) — including the
`RepeatedFormatError` cluster on aiomonitor. Normal noise; the defects are
in how the harness *handled* them, now covered by the fault-injection
suite.

## What this means for benchmark readiness

- Canonical path works end-to-end: plan → readiness → digest gate →
  provider gate → 20 parallel tasks → verifier → attestation, all
  typed and auditable.
- GT delivery is real and consumed; livelock eliminated; churn governor
  produced a correct typed abort.
- Blocking items before a defensible full benchmark: boa pre-admission
  starvation (P0 — forecloses whole tasks silently), predicate-observation
  channel (P0 — makes `verified`/attestation unreachable), receipt-write
  conservation failures (P1), dense-index readiness on large repos (P1),
  aiomonitor attribution (P2), empty-provider model-mismatch false
  positive (P2).
- All now have provider-free reproduction paths via the fault-injection /
  soak / matrix suites added in `e3cd9475`.

## Postmortem resolution (post-smoke fixes, all provider-free verified)

Every defect above now has a root-cause fix in-tree. Smoke-20 artifacts
remain historical evidence of the defects; they are not re-attested.

1. **Boa pre-admission starvation (P0) — FIXED.** Root cause was NOT the
   startup embed budget: boa had a prebuilt graph, so no build ran, the
   contract-embedding store was empty, and the first dense call fell
   through `_rank_from_store` (`store_misses_pool`) into the **uncapped**
   `rank_documents` fallback in `retrieval.py` — ONNX inference over the
   whole node pool on the agent thread, ~96 min before the first provider
   call, then SIGKILL. The `MAX_RUNTIME_EMBED_DOCUMENTS` cap (68ee367e)
   only guarded the partially-populated-store path. Fix: the fallback
   pool is now capped (`_RUNTIME_FALLBACK_POOL_CAP`), so the first call
   abstains instead of starving; the provider-wait refresh populates the
   store in the background from the first provider window.

2. **Predicate-observation channel (P0) — root cause refined + fixed.**
   The channel was not dead: arktype logged 211 `plan_check_observed`
   events, 40 CHECK_FAILED, 171 UNVERIFIED, **zero CHECK_PASSED**, and
   `predicate_receipt_recorded`/`obligation_reverified` did fire. Two
   real defects: (a) the DeepSWE submission boilerplate
   ("work on this in a new branch from main and commit everything when
   you are done") was scraped into a plan row on every task — normative
   (a run that never commits grades against pristine base, so it must be
   tracked) but unprovable by any bound test, keeping `verified`
   unreachable by construction; (b) check→row binding quality (the
   boilerplate row bound to `intersections.test.ts`). Fix: process
   directives get `verification_kind="process"`, are excluded from
   test-check binding, and are verified by a real workspace git-state
   probe (`_observe_process_rows`: branch-not-main + HEAD moved) at
   drain time. Rows stay tracked; evidence is real.

3. **Receipt-write failures (P1) — FIXED, single root cause.** The
   `delivery_receipt_evidence_join_failed` raise on abs-module/csstree:
   `caller_contract_view` was legitimately delivered twice in one
   iteration (action 7→8, distinct payloads) and the join matched only
   `dedup_key`+`iteration` → ambiguity. The receipt already carries its
   delivery's unique `payload_hash`; the join now matches on it. The
   cascade — `product_event_journal_digest_mismatch`, conservation
   errors — came from the fallback path hitting the same join inside
   `_journal_derived_treatment_receipt`. Verified against the real
   abs-module (104 receipts) and csstree (68) journals.

4. **adaptix `semantic_localization_source_revision_mismatch` — FIXED.**
   The witness snapshot was `complete: False` all run because
   `git ls-files` lists the `release_data` submodule (mode 160000) as a
   path but it is a directory — `read_bytes()` raised `IsADirectoryError`
   → `unreadable:` forever. Fix: gitlinks are captured as typed entries
   whose identity is the pinned commit read from the index
   (`git ls-files -s`, works on unborn HEAD); other non-regular nodes
   become `kind="special"` keyed on mode. Snapshot is complete again;
   the certification check stays strict.

5. **awilix `semantic_localization_certified_graph_missing` — already
   fixed in-tree.** The localization named graph `42a52a41…`, pruned by
   superseded-revision reclamation before attestation. `_pin_graph_revision`
   writes `pinned.json` at localization delivery and `_pinned_revisions`
   protects them during pruning ("reclaim only what the authority does
   not name"). The pin commit postdates the smoke SHA — the historical
   artifact stays inconsistent; the defect is closed.

6. **katex/testem `treatment_dense_index_not_ready` (P1) — FIXED.**
   Two-layer fix. (a) The provider-wait refresh (post-smoke) populated
   the store but journaled only `dense_wait_refresh` — the receipt reads
   `dense_index_ready`, so a completed refresh was invisible. Drain now
   emits `dense_index_ready` with a measured probe (`PRAGMA quick_check`
   + `documents_after` from the refresh payload), `query_ready` true only
   when both hold. (b) The wait scheduler coalesced only same-name jobs;
   under katex churn (133 revisions) stale-revision refreshes queued
   serially on the single worker — pure spend ahead of the live job.
   `drop_pending_family` now keeps newest pending refresh per enqueue;
   running jobs finish (content-keyed vectors stay valid). Dense
   abstention paths also emit a measured `dense_index_receipt` instead of
   silence, so not-ready is recorded with a reason.

7. **Boa `effective_model_mismatch` false positive (P2) — FIXED.**
   `effective_model: null` is now valid iff no provider call was ever
   admitted or served; it is still an error when the journal proves
   provider activity. Applied at `verify_runtime_receipt`, the
   adapter-level attestation row, and the product-level row.

8. **aiomonitor bootstrap accounting (P2) — FIXED.** GT-internal
   bootstrap calls (`native_query`) bypass the admission/delivery
   boundary, so they were missing from `provider_admission`,
   `provider_delivery`, and terminal-request censuses, and their
   `bind_provider_response` attached the previous agent request's
   identity. Fix: namespaced request ids
   (`{task}-gt-internal-select-catalog`, `-persistent-plan`) bound
   explicitly on success AND failure, `delivery_ids=[]`, terminal marked;
   receipt equations now read
   `provider_calls = agent_turn + catalog_bootstrap + plan_bootstrap` and
   `provider_attempts = admissions + bootstrap` (num_retries=0 → one wire
   attempt each). Fixtures were rewritten to the real journal shape
   (lifecycle rows, not fake admissions). Real aiomonitor journal now
   conserves: 19 attempts = 16 responses + 3 failed = manifest 19.

### Phase-5 proof record (branch `codex/phase5-harness-proof`)

- Invariant suite + arktype incident replay (`4c5f5eee`),
  producer-schema scoped merge (`7abb9a7d`), deferred-catalog boundary
  stamping (`5246205f`), bootstrap queue isolation (`5716205f`),
  audit prose-line binding (`fbff833b`) — green on both
  `codex/phase5-harness-proof` and `codex/context-plan-integrity`
  (installed rehearsal `34745605772` SUCCESS on `2cad281e`; the interim
  failure `34745603139` captured the tree mid-cherry-pick).

### Benchmark readiness after this batch

- The 90-minute task envelope is intact; no graph-build or embed caps
  weaken the benchmark (the fallback cap converts silent starvation into
  an honest abstention + async refresh, it does not truncate indexing).
- Remaining pre-benchmark work: Phase-4 sidecar deletion, C3 GT-block
  supersession at prepare, provider-free acceptance on final state,
  installed rehearsal, then 1-task paid smoke (`deepseek-v4-flash`,
  OpenRouter/relace, 90-min) before the 20-task cohort.
