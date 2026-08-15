# GT-on matched ten-task smoke audit: `31223362041`

Date: 2026-08-07  
Commit: `bd7e8d8e3c8f82e6b2cda30bf1f55bf03c524532`  
Workflow: `tb2_miniswe_central.yml`  
Arm: treatment, `all17`, `integration_mode=active`, `preflight_mode=shadow`  
Model: `deepseek-v4-flash`, temperature 1.0  
Run: https://github.com/harneet2512/gt-harness/actions/runs/31223362041

## Outcome

All ten task jobs and the merge job completed successfully. The treatment
produced verifier rewards for all ten tasks and solved **9/10**, matching the
frozen GT-off baseline. `gpt2-codegolf` remained the only verifier miss in both
arms. The treatment had no Harbor outer censor and no errored trial.

`cobol-modernization` exited through the engine's `DeadlineReserveReached`
path but still returned reward 1 and `uncensored_resolved=true`; this is an
internal clean deadline exit, not a Harbor censor.

Outcome preservation therefore passed. This is not an efficiency win.

## Matched deep deltas

Delta is treatment minus the frozen GT-off baseline. Positive values are
regressions. `effective_actions` includes actual controller/sensor task
executions on GT-on; the baseline has only model-selected action executions,
so this column intentionally exposes GT overhead instead of hiding it.

| Task | Outcome | Tokens | API calls | Model actions | Steps | Effective-action delta |
|---|---:|---:|---:|---:|---:|---:|
| break-filter-js-from-html | 1→1 | +44,346 | +3 | +4 | +3 | +29 |
| cobol-modernization | 1→1 | +2,753,481 | +33 | +21 | +32 | +125 |
| fix-code-vulnerability | 1→1 | −296,125 | −13 | −13 | −13 | +20 |
| gpt2-codegolf | 0→0 | −6,948,480 | −29 | −23 | −29 | +20 |
| headless-terminal | 1→1 | −3,717,776 | −44 | −44 | −44 | +26 |
| llm-inference-batching-scheduler | 1→1 | −1,743,891 | −10 | −9 | −10 | +29 |
| modernize-scientific-stack | 1→1 | +3,653 | 0 | −2 | 0 | +19 |
| portfolio-optimization | 1→1 | +164,770 | +4 | +3 | +4 | +63 |
| schemelike-metacircular-eval | 1→1 | −3,932,239 | −37 | −62 | −37 | +53 |
| write-compressor | 1→1 | +385,184 | +7 | +8 | +7 | +61 |

Aggregate:

- tokens: **−13,287,077**;
- API calls: **−86**;
- model actions: **−117**;
- assistant steps: **−87**;
- normalized cost: **−$0.06242**;
- effective task actions: **+445**.

The aggregate token/call reduction is outweighed by the effective-action
increase and nine of nine commonly solved tasks failing strict per-task Pareto
because at least one primary resource dimension increased. The deep-metrics
gate therefore failed on `effective_actions`. The 89-task run remains blocked.

## GT receipt and delivery audit

Across the ten receipts:

- **283/283** feature effects were applied;
- effect provenance: 203 `engine_internal_state`, 10
  `existing_engine_actuation`, 63 `audit_only`, and 7 `provider_payload`;
- seven provider-payload effects coalesced into six provider guidance
  deliveries;
- all seven visible effects were grounded and non-empty;
- all arrived in the first eligible provider request;
- late deliveries: **0**;
- predictive deliveries: **0**;
- provider request hashes: **334/334**, coverage 1.0;
- provider budget failures: **0**;
- derived-artifact anchors in visible payloads: **0**;
- duplicate evidence, false fires, missed triggers, stale-batch prevention,
  and blocked actions: **0**.

The visible payloads were:

`signature_delta` (cobol-modernization and headless-terminal),
`newfile_precedent` (headless-terminal, schemelike-metacircular-eval, and
write-compressor), and `GT_EDIT_CHECK` (portfolio-optimization and
schemelike-metacircular-eval). Their concrete paths, symbols, checks, and
precedents were present in the receipt payloads.

## Feature census for this stochastic smoke

All 17 paths were enabled and provider-free proven. The paid trajectory
naturally fired 12 feature IDs:

`GT_CERT_DELIVERY`, `GT_CHANGE_SURFACE`, `GT_EDIT_CHECK`, `GT_LOC_RESLOT`,
`GT_PATCH_DELTA`, `caller_contract`, `def_partition`, `localization`,
`newfile_precedent`, `obligations`, `signature_delta`, and `syntax_result`.

The exact triggers for `covering_red`, `recovery`, `submit_refusal`,
`GT_HYPOTHESIS`, and `GT_SS_SUBMIT_RED` were absent. This is not a missed
implementation path. The receipt audit reports zero missed triggers and zero
false fires. Natural firing of all 17 is not required to claim that all 17
paths are implemented.

## Preflight and context accounting

- preflight calls: **366**, all `PASS` in shadow mode;
- material preflight evidence: **0**;
- rewrites, returns, suppressions, false interventions, and stale-batch
  prevention: **0**;
- context facts: **4,606 candidates, 4,606 accounted**;
- compaction epochs: **0**;
- duplicate-turn removal: **0**;
- unique assistant-reasoning removal: **0**;
- provider request budget failures: **0**.

The run therefore proves the observation/accounting boundary, not assistive
pre-action intervention. Shadow mode was intentional and must not be confused
with an active return-to-model experiment.

## Replay and decision

`python -m scripts.central_replay D:\\gt_runs\\31223362041` returned
`REPLAY_OK` for all ten trajectories. `scripts/central_effect_audit.py`
returned `EFFECT_TRACE_VALID` for all ten receipts.

This smoke proves outcome preservation and correct, on-time, grounded delivery
on the current commit. It does **not** prove efficiency because controller and
sensor executions increased effective work, several tasks used more tokens or
steps, and the strict per-task gate failed. Do not start the 89-task run or
switch the paid workflow to `assistive_safe` from this result.
