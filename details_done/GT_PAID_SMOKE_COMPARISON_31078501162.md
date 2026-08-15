# Paid GT-on smoke comparison: workflow 31078501162

Date: 2026-08-06  
Commit executed: `7c70d3780a1d42b84e76fd26198b7209a2c9f17d`  
Treatment: integrated GT-on, `preflight_mode=shadow`, DeepSeek V4 Flash  
Tasks: the ten-task matched set used by the frozen local GT-off baseline

## Outcome gate

The workflow completed all ten jobs and the merge job successfully. The
outcome gate did **not** pass:

| measure | GT-off baseline | GT-on treatment |
|---|---:|---:|
| official solved | 9/10 | 7/10 |
| uncensored resolved | 9/10 | 7/10 |
| clean outcome preservation | — | **FAIL** |

The two treatment losses are different:

* `llm-inference-batching-scheduler`: baseline solved; treatment ended at
  `StepLimitExceeded` after 100 assistant steps, with 16 unresolved
  obligations and no submit.
* `write-compressor`: baseline solved; treatment hit the provider's
  `ContextWindowExceededError` at 1,065,370 requested tokens against a
  1,048,576-token model limit. This is censored, not a verifier result.

The deep-metrics classifier was corrected after this run to classify
`ContextWindowExceededError` as a Harbor censor (commit `9860d8e`); the paid
artifacts themselves were not rerun.

## Matched resource deltas

All deltas below are treatment minus baseline over the same ten task names.
Positive resource deltas are regressions.

| task | outcome | tokens | calls | actions | assistant steps | context chars |
|---|---|---:|---:|---:|---:|---:|
| break-filter-js-from-html | solved → solved | +177,266 | +10 | +14 | +10 | +74,533 |
| cobol-modernization | solved → solved | -455,457 | +17 | +25 | +17 | +574,014 |
| fix-code-vulnerability | solved → solved | +155,832 | +1 | +1 | +1 | +494,008 |
| gpt2-codegolf | unsolved → unsolved | -8,290,323 | -27 | -24 | -28 | -10,640,251 |
| headless-terminal | solved → solved | -4,548,299 | -59 | -59 | -59 | -6,240,035 |
| llm-inference-batching-scheduler | solved → unsolved | -1,062,741 | +59 | +60 | +59 | +9,888,884 |
| modernize-scientific-stack | solved → solved | +5,250 | +1 | -7 | +1 | +17,038 |
| portfolio-optimization | solved → solved | +793,344 | +49 | +51 | +49 | +2,914,346 |
| schemelike-metacircular-eval | solved → solved | -7,442,019 | 0 | -21 | 0 | +24,319,739 |
| write-compressor | solved → censored | -720,525 | -4 | -2 | -5 | -173,728 |

Aggregate over the ten matched tasks: tokens `-21,387,672`, calls `+47`,
actions `+38`, assistant steps `+45`, context characters `+21,228,548`,
uncached input tokens `+2,198,826`, failed actions `+7`, repeated commands
`+45`, and wasted-action proxy `+52`. The token reduction cannot be called an
efficiency win because it coincides with one real solve loss and one provider
censor; only 1 of the 7 treatment-preserved solved tasks was strict-Pareto
better.

## GT engine accounting in this smoke

The ten receipts contain 312 produced effects and 312 applied effects. Their
disposition ledger is:

* 225 `engine_internal_state`;
* 13 `existing_engine_actuation`;
* 6 `provider_payload`;
* 68 `audit_only`.

All 17 feature IDs are present in the receipt schema, but only 13 fired
naturally in this stochastic task set. The absent exact-trigger features were
`caller_contract`, `def_partition`, `recovery`, and `signature_delta`.
This is not a producer-path failure; the provider-free census and forced
trigger suite prove all 17 paths independently.

There were 5 model-visible payload deliveries across the ten tasks. All 5
were grounded, in the first eligible provider request, with 0 late and 0
predictive deliveries. Shadow preflight produced 763 known segments, 991
unknown segments, and 828 typed targets, but every candidate and applied
disposition was `PASS`; no action was returned, rewritten, or suppressed.

## Diagnosis

1. The aggregate token delta is dominated by three large reductions
   (`gpt2-codegolf`, `headless-terminal`, and `schemelike-metacircular-eval`),
   while context volume increased in most solved tasks. This is not a uniform
   per-task efficiency improvement.
2. The scheduler regression is an outcome/control failure: the treatment
   reached the 100-step ceiling, oscillated between `BUDGET_RISK` and
   `RECOVERED`, accumulated unresolved obligations, and never produced a
   completion certificate. GT delivered no provider payload on that task, so
   this run does not prove that a GT payload caused the loss; it does prove the
   treatment did not preserve the baseline outcome.
3. The compressor failure is a hard context-budget failure. Five compactions
   occurred, but the final request still exceeded the provider limit. The next
   fix must enforce a token headroom invariant before every provider request,
   not merely count characters or compactions.

## Verification performed

* `python -m scripts.central_replay D:\gt_runs\31078501162` → `REPLAY_OK`
  for all 10 trajectories.
* `pytest -q tests/test_gt_deep_metrics.py` → 8 passed after the censor
  classifier fix.
* The raw receipts confirm 0 late and 0 predictive payload deliveries.

This single temperature-1 smoke is descriptive. It is not evidence for a
causal efficiency claim, and the 89-task run remains blocked until the
context-headroom and completion-control fixes pass repeated matched trials.
