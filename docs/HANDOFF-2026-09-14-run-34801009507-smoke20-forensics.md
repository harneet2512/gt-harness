# Forensic handoff — DeepSWE smoke20 run 34801009507 (2026-09-14)

Source `aef3a2f5` (branch `codex/phase5-harness-proof`), `all-20` stage,
readiness reused from `34799716299`, route `openrouter-deepseek-v4-flash-0731-relace-only`,
treatment `groundtruth`, approval recorded in plan.

## Outcome census

- **6/20 SOLVED** (verifier `resolved: true`, reward 1.0): abs-module-cache-flags,
  abs-stepped-slices, actionlint-action-pinning-lint (go), awilix (ts),
  csstree, katex (js). **30% on a hard mixed-language cohort** — descriptive;
  no matched GT-off pairing on these task ids.
- **12 GRADED-unsolved** (reward 0): genuine task failures.
- **2 typed ERROR**: boa (`provider_failure`/`ApiRateLimitError` — 20-way
  parallel burst on one route), fd (`governor_abort`/`churn_abort` — 45 calls
  /43 turns, plan gate aborted; correct behavior).
- Workflow conclusion: `failure` — all three attest verification steps
  rejected. Attestation JSON itself shipped (`status: FAIL`).

## Defects exposed (the point of the smoke)

1. **oxvg duplicate delivery identity + broken boundary join** — the same
   evidence payload was legitimately redelivered after a graph recovery
   (iterations 27 and 68, both `delivery_ordinal: 1`). The audit's
   one-identity-per-run join cannot model cross-request redelivery.
   New defect class; adjacent to F9 but distinct (real duplicate, not ordering).
2. **GT_GRAPH_REFRESH_FAILED on rust tasks** (oxvg, pest) — repeated
   `graph_sync_amend_refused` → `graph_invalidated` → `graph_recovery_failed`
   cycles under workspace churn (cargo builds + agent edits). Promotions went
   `obsolete` (13/18, 14/17). fd also showed a **180 s rust-analyzer
   project-readiness stall** (46 attempts, ready=False) before enrichment
   succeeded (71 hovers). 3 of 4 rust tasks had GT-side anomalies — the
   incremental graph pipeline degrades under rust-class file mutation.
3. **8 tasks missing diagnostics artifacts** — abs-stepped-slices (SOLVED),
   adaptix, arktype, bandit-incremental, boa, clack, fd, testem-bail.
   Collection/emission defect; includes successful jobs.
4. **Missing-patch bind-step crash** — errored trials (no `model.patch`)
   die at `docker cp` with `RuntimeError` instead of shipping a typed
   provider-failure/abort receipt. Converts every non-submitting task into a
   job failure; blocks the typed ERROR path it should emit.
5. **Provider rate-limit at 20-way parallelism** — `ApiRateLimitError`
   during the startup burst on the shared relace route. Cohort pacing or
   quota tier question, not a GT defect.
6. **`--tsserver-path` unknown to typescript-language-server@6.0.0** — the
   flag crashes TLS (`unknown option`), so JS/TS legs still classify
   unserviceable (honest, not-required). The correct fix is probably no flag
   (TLS resolves sibling `node_modules/typescript` itself); queued for the
   next SHA window.
7. **bandit-taint: serviceable pyright leg, zero edges**
   (`no_edge_mutations_15_of_36_obsolete`, required=True) — leg served
   requests, produced nothing. Real capability gap to inspect, or
   legitimately-unpromotable candidates; needs receipt-level check.

## What held under cohort load

- `lsp_promotion` WORKING where the toolchain exists: `published_306_edges`
  on go (abs-module-cache-flags); gopls/rust-analyzer legs served real requests.
- F9/F10/F11 all held: no `refused_then_delivered`, rewards bound `GRADED`,
  `run-status/` snapshots + error logs ship in artifact trees.
- `_leg_serviceable` classifier held: every env-bound leg read
  `:no_serviceable_candidates` + `required=False`; serviceable zero-edge legs
  (bandit-taint, katex) correctly stayed `required=True`.
- Churn governor: fd aborted at 45 calls instead of burning 90 min.
- Typed ERROR outcomes preserved: provider_failure and governor_abort both
  `reward: null`, never manufactured.

## Follow-up queue

Fix order suggested by severity: (4) missing-patch crash — it blocks typed
ERROR receipts on every non-submitting task; (1) redelivery-aware audit join;
(3) diagnostics emission on all task outcomes; (2) rust graph-refresh
resilience; (6) remove the `--tsserver-path` flag; (5) cohort pacing; then
re-run gate-one + smoke20 on the new SHA.
