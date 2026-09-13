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
