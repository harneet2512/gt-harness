# mini-SWE GT deep-metrics comparison

Run date: 2026-07-30  
GT run: `30599727385`  
Frozen GT-off run: `28627357720`  
Model label: `deepseek/deepseek-v4-flash`  
Population: the same five SWE-bench Live Lite tasks  

## Executive verdict

The observed outcome moved from **1/5 resolved (20%)** to **3/5 resolved
(60%)**, a **+2 task / +40 percentage-point** improvement with **zero observed
task regressions**. The two gains were `arviz-devs__arviz-2413` and
`aws-cloudformation__cfn-lint-3749`; `amoffat__sh-744` resolved in both arms.

This is a strong descriptive result and weak causal evidence. It is not a
controlled pair:

- both arms used the same model name, temperature 1.0, top-p 1.0, 16,384
  maximum output tokens, 150-step agent limit, and $3 cost limit;
- the frozen baseline had provider thinking **disabled**, while the GT run had
  thinking **enabled**;
- the agent/template versions differ, including baseline `mode=confirm` and
  `confirm_exit=true`;
- the sample contains only five tasks. With two discordant wins and no
  discordant losses, the exact paired randomization/McNemar two-sided p-value
  is 0.5.

Therefore: **GT is proven to have operated and delivered attributable bytes.
This run does not isolate how much of the +2 outcome was caused by GT.**

Confidence:

- raw rewards, steps, tokens, and costs: **high**;
- GT delivery and lifecycle attribution: **high after offline regrade**;
- GT caused the outcome improvement: **low**;
- the direction is promising enough to justify a corrected controlled pair:
  **moderate**.

## Task-level outcome and resource comparison

| Task | Reward off → GT | Steps | Input tokens | Output tokens | Cost USD |
|---|---:|---:|---:|---:|---:|
| aiogram-1594 | 0 → 0 | 18 → 12 | 102,221 → 51,481 | 2,025 → 1,749 | 0.002005 → 0.001357 |
| sh-744 | 1 → 1 | 58 → 37 | 718,574 → 530,448 | 9,256 → 8,188 | 0.006726 → 0.007714 |
| arviz-2413 | 0 → 1 | 31 → 26 | 333,236 → 416,162 | 3,927 → 7,979 | 0.003866 → 0.006442 |
| cfn-lint-3749 | 0 → 1 | 52 → 116 | 1,027,952 → 7,516,918 | 12,406 → 67,803 | 0.009493 → 0.052833 |
| cfn-lint-3764 | 0 → 0 | 31 → 22 | 347,019 → 272,440 | 5,598 → 5,260 | 0.004227 → 0.004649 |
| **Total** | **1 → 3** | **190 → 213** | **2,529,002 → 8,787,449** | **33,212 → 90,979** | **0.026317 → 0.072996** |

Aggregate deltas:

- resolved tasks: **+200% relative**, **+40 percentage points absolute**;
- total steps: **+12.1%**;
- input tokens: **+247.5%**;
- output tokens: **+173.9%**;
- cost: **+177.4%**;
- test executions: **19 → 25 (+31.6%)**;
- patch lines: **28 → 64 (+128.6%)**;
- detected degenerate loops: **3 → 1 (-66.7%)**.

Per resolved task:

| Metric | GT off | GT | Delta |
|---|---:|---:|---:|
| Steps / resolved | 190.0 | 71.0 | **-62.6%** |
| Input tokens / resolved | 2,529,002 | 2,929,150 | **+15.8%** |
| Output tokens / resolved | 33,212 | 30,326 | **-8.7%** |
| Cost / resolved | $0.026317 | $0.024332 | **-7.5%** |

The efficiency headline is mixed: GT bought two extra resolutions and reduced
cost per resolution, but total resource use rose sharply.

## Outlier decomposition

`cfn-lint-3749` dominates the overhead:

- 72.4% of all GT-arm cost;
- 85.5% of all GT-arm input tokens;
- 92.8% of the incremental cost;
- more than 100% of the incremental input tokens, because the other four tasks
  collectively used fewer input tokens with GT.

Excluding `cfn-lint-3749`:

- steps fell **138 → 97 (-29.7%)**;
- input tokens fell **1,501,050 → 1,270,531 (-15.4%)**;
- cost still rose **$0.016824 → $0.020163 (+19.8%)**.

The correct optimization target is therefore not “GT is globally expensive.”
It is the pathological long-tail behavior exposed by `cfn-lint-3749`, plus the
provider-thinking/configuration confound.

## Behavioral diagnostics

These fields are useful within each task but are not clean cross-arm KPIs
because the frozen baseline used an older metrics extractor and, for some
tasks, a different gold-file population. They must not be averaged as if their
denominators were identical.

| Task | Steps to gold view | Steps to gold edit | Wasted-step proxy | Tests | Patch lines | Loops |
|---|---:|---:|---:|---:|---:|---:|
| aiogram | 3 → 1 | 9 → 6 | 0.583 → 0.667 | 3 → 1 | 10 → 4 | 0 → 0 |
| sh | 1 → 2 | 0 → 11 | 0.909 → 0.190 | 8 → 5 | 3 → 8 | 0 → 0 |
| arviz | 2 → 1 | 0 → 16 | 0.714 → 0.615 | 3 → 2 | 7 → 5 | 1 → 0 |
| cfn-3749 | 1 → 3 | 0 → 39 | 0.750 → 0.667 | 3 → 12 | 4 → 43 | 2 → 1 |
| cfn-3764 | 1 → 2 | 0 → 7 | 0.833 → 0.455 | 2 → 5 | 4 → 4 | 0 → 0 |

The baseline `steps_to_gold_edit=0` values do not mean instant edits. They are
an older-extractor sentinel and must not be interpreted numerically.

## What can be attributed directly to GT

The five GT journals contain **68 provider-delivered attempts**. Those attempts
carried **69 attributable feature rows** totaling **21,562 characters**:

| Feature/fact | Rows | Characters | Tasks | Timing |
|---|---:|---:|---|---|
| obligations | 66 | 19,372 | sh, arviz, cfn-3749, cfn-3764 | **on-time 4/4 at task start** |
| caller_contract | 2 | 1,701 | aiogram, cfn-3749 | **on-time 2/2 after view** |
| def_partition | 1 | 489 | cfn-3764 | delivered; chronology timing not evaluable |

This attribution is stronger than grepping for a `GT` marker. Each credited
delivery has:

1. a feature/fact identity;
2. an authorized byte owner;
3. a contracted lifecycle;
4. a capsule and exact provider-payload hash;
5. provider acceptance and terminal delivery;
6. response commitment;
7. an append-only evidence transition
   `RELEASED → DELIVERED` with reason
   `PROVIDER_TERMINAL_DELIVERY_PROVEN`.

The feature-outcome pattern is not separable at n=5:

- `caller_contract` appeared on one failure and one success;
- `obligations` appeared on three successes and one failure;
- `def_partition` appeared on a failure;
- the two incremental wins received `obligations`, and one also received
  `caller_contract`.

That proves delivery, not behavioral causation.

## The 17-feature smoke verdict

| Verdict | Count |
|---|---:|
| FIRED | 3 |
| TRIGGER-ABSENT / correct-quiet | 14 |
| ARBITRATED | 0 |
| NO-INSTRUMENTATION | 0 |
| DELIVERY-FAILURE | 0 |

The 14 quiet features were evaluated and abstained because their predicates
were absent. They were not missing. Examples include: no syntax error for
`syntax_result`/`GT_EDIT_CHECK`, no signature-changing edit with callers for
`signature_delta`/`GT_PATCH_DELTA`, no unresolved observed RED at submission
for `submit_refusal`/`GT_SS_SUBMIT_RED`/`GT_CERT_DELIVERY`, and no qualifying
stall for `recovery`/`GT_HYPOTHESIS`.

This smoke therefore proves **17/17 instrumentation coverage**, **3/17 actual
feature triggers**, and **2/3 fired-feature timing contracts**. It does not
prove that a majority fired, nor that all fired at a measurable correct time.

## Measurement defects found and fixed

### 1. Historical delivery was erased by later invalidation

The original diagnosis bundle rejected `cfn-lint-3749` and `cfn-lint-3764`.
Both had a valid delivery chain followed by a later revision:

`PENDING → READY/HELD → RELEASED → DELIVERED → ACTIVE → INVALIDATED`

The reader incorrectly required the **final** lifecycle to remain in a
delivered-like state. The fix requires the historical
`RELEASED → DELIVERED` provider-terminal transition instead. Evidence
invalidated after a later edit no longer erases an earlier delivery.

Offline regrade result:

- 5/5 task journals integrity-clean;
- 68 delivered attestations;
- zero rejected attempts;
- 129/129 exact feature-family records;
- zero missing records, leaks, dose violations, or incomplete inputs;
- `publishable: true`.

### 2. Lifecycle timing used the wrong step-budget denominator

The real mini-SWE configs in both arms specify `agent.step_limit=150`, but the
GT workflow exported `GT_STEP_LIMIT=300`. Consequently the agent stopped at
150 while GT's phase and verification-horizon logic believed it had 300
steps. This can delay penultimate/pre-submit behavior and makes the current
run unsuitable as final timing proof for those features.

The workflow is corrected to 150, and the paid-run cost guard now rejects any
future mismatch between `GT_STEP_LIMIT` and the actual agent config.

## Bottom line

Observed performance improved from 1/5 to 3/5 with no task regression and a
7.5% lower cost per resolved task. Total cost rose 177%, almost entirely due
to one newly solved outlier. GT itself is directly measurable and
cryptographically attributable in the provider-delivery journals.

What remains unproven is causal uplift and complete lifecycle timing. A new
GT-only smoke is required after the 150-step timing fix, followed by a truly
matched GT-off/GT-on pair with identical thinking mode and agent/template
versions. The frozen baseline remains useful for descriptive comparison and
must not be mislabeled as that controlled pair.

## Evidence locations

- GT artifacts: `D:\gt_runs\30599727385\artifacts`
- Frozen baseline: `D:\gt_runs\gtoff_baseline_trajs\half0`
- 17-feature verdict: `D:\gt_runs\30599727385\gt_17_feature_verdicts.json`
- Corrected regrade: `D:\gt_runs\30599727385\regrade_f9457b9_attestation_fix`
- Original failed diagnosis:
  `D:\gt_runs\30599727385\artifacts\gt-diagnosis-30599727385`
