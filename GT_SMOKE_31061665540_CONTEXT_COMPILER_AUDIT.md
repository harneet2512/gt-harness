# GT context-compiler smoke `31061665540` audit

Date: 2026-08-05 local / 2026-08-06 UTC

## Verdict

Confidence: high.

The deterministic integration mechanics passed. The experimental efficiency
gate failed.

- Verifier outcome was preserved: GT-off 9/10, GT-on 9/10.
- No baseline solve was lost.
- The strict gate failed because treatment had a new outer Harbor timeout on
  `cobol-modernization`, three treatment tasks were censored, and six
  baseline-solved tasks failed the per-task strict Pareto resource gate.
- Aggregate tokens, calls, steps, actions, failures, context characters, and
  agent wall time fell, but normalized token cost rose 13.33% because uncached
  and output tokens are priced differently from cache-hit tokens.
- The 89-task run remains blocked.

This is one stochastic matched smoke. It is descriptive evidence, not a
causal efficiency claim and not proof that GT can eliminate model variance.

## Run identity

| Field | Value |
|---|---|
| GitHub workflow | `31061665540` |
| URL | `https://github.com/harneet2512/gt-harness/actions/runs/31061665540` |
| Treatment commit | `a45601f0ba055d9699b611c59dcf9b077ecc0843` |
| Branch | `inline-engine` |
| Model | `deepseek-v4-flash` |
| GT mode | treatment / integrated central agent |
| Preflight mode | `shadow` |
| Lossy compaction | disabled |
| Task artifacts | 10/10 |
| Central receipts | 10/10 |
| Verifier rewards | 10/10 |
| Workflow jobs | 12/12 successful |

The frozen local GT-off trajectories were reused. No baseline run was started.

## Integration-integrity audit

| Invariant | Result |
|---|---:|
| Compiler calls / API calls | 334/334 |
| Typed proposals preflighted / actions | 349/349 |
| Applied shadow decisions | 349 PASS; 0 rewrite/suppress/return |
| Candidate facts accounted | 5,287/5,287 |
| Effects compiler-accounted | 339/339 |
| Visible deliveries | 21/21 grounded and request-bound |
| Late deliveries | 0 |
| Predictive deliveries | 0 |
| Unique assistant reasoning removed | 0 chars |
| Context compactions | 0 |
| Receipt invariant violations | 0 |
| Maximum task-level preflight p95 | 0.039198 ms |

Every visible delivery had concrete claim anchors, a request hash, a provider
message index, `not_predictive=true`, `one_step_late=false`, and
`delivered_before_call == first_eligible_call`.

## What happened to all GT state

The run produced and applied 339 effects:

| Effect disposition | Count | Meaning |
|---|---:|---|
| `engine_internal_state` | 263 | Updated deterministic controller state. |
| `existing_engine_actuation` | 12 | Exercised an existing non-provider engine actuator. |
| `audit_only` | 43 | Recorded evidence with no downstream influence claim. |
| `provider_payload` | 21 | Reached the model in the first eligible request. |

The context compiler assigned every effect a first terminal accounting state:

| Compiler disposition | Count |
|---|---:|
| `controller_state_considered` | 303 |
| `provider_payload` | 21 |
| `no_eligible_model_call` | 12 |
| `superseded_before_request` | 2 |
| `stale_state_rejected` | 1 |
| unaccounted | 0 |

This is the correct hierarchy: effects are not discarded merely because they
are private, and private effects are not relabeled as causal help.

## Context sent beyond the 21 active deliveries

The previous `gt_context_chars_added` counter covered only active guidance
messages. The provider-view receipt also records bounded compiler state frames.
The shared extractor now accounts for both:

| Context surface | Characters sent across requests |
|---|---:|
| Bounded compiler state frames | 182,536 |
| Active grounded guidance | 2,337 |
| Total GT-added request context | 184,873 |

The compiler considered 5,287 facts, selected 1,151 missing current facts,
proved 1,632 facts were already represented in exact provider messages, and
kept 2,113 controller-only. Of 1,147 selected facts with a measurable concrete
anchor, 110 anchors appeared in the immediately selected command. This is an
exposure/utilization proxy, not model acknowledgement and not causal proof.

The GT-added 184,873 characters are about 1.2% of the 15,247,274 treatment
context characters. They do not by themselves explain the much larger context
growth on the two long resource regressions.

## Natural 17-feature census

All 17 producer and consumer paths are provider-free proven. The paid smoke
naturally fired 11/17 feature IDs; the remaining six exact triggers were absent.

| Feature | Produced | Model deliveries |
|---|---:|---:|
| `caller_contract` | 0 | 0 |
| `covering_red` | 1 | 0 |
| `def_partition` | 0 | 0 |
| `GT_CERT_DELIVERY` | 29 | 0 |
| `GT_CHANGE_SURFACE` | 85 | 0 |
| `GT_EDIT_CHECK` | 18 | 0 |
| `GT_HYPOTHESIS` | 1 | 0 |
| `GT_LOC_RESLOT` | 53 | 7 |
| `GT_PATCH_DELTA` | 61 | 0 |
| `GT_SS_SUBMIT_RED` | 0 | 0 |
| `localization` | 53 | 0 |
| `newfile_precedent` | 14 | 14 |
| `obligations` | 10 | 0 |
| `recovery` | 0 | 0 |
| `signature_delta` | 0 | 0 |
| `submit_refusal` | 0 | 0 |
| `syntax_result` | 14 | 0 |

Absent-trigger features: `caller_contract`, `def_partition`,
`GT_SS_SUBMIT_RED`, `recovery`, `signature_delta`, and `submit_refusal`.
Nothing was fabricated to inflate the count.

### Features fired by task

- `break-filter-js-from-html`: `localization=1`, `obligations=1`,
  `GT_CERT_DELIVERY=6`, `GT_CHANGE_SURFACE=2`, `GT_EDIT_CHECK=1`,
  `GT_LOC_RESLOT=1`, `GT_PATCH_DELTA=1`.
- `cobol-modernization`: `localization=7`, `obligations=1`,
  `syntax_result=1`, `GT_CHANGE_SURFACE=15`, `GT_LOC_RESLOT=7`,
  `GT_PATCH_DELTA=15`.
- `fix-code-vulnerability`: `localization=8`, `obligations=1`,
  `syntax_result=1`, `GT_CERT_DELIVERY=4`, `GT_CHANGE_SURFACE=6`,
  `GT_EDIT_CHECK=2`, `GT_LOC_RESLOT=8`, `GT_PATCH_DELTA=2`.
- `gpt2-codegolf`: `localization=4`, `newfile_precedent=2`,
  `obligations=1`, `GT_CHANGE_SURFACE=7`, `GT_LOC_RESLOT=4`,
  `GT_PATCH_DELTA=6`.
- `headless-terminal`: `localization=4`, `newfile_precedent=8`,
  `obligations=1`, `syntax_result=9`, `GT_CERT_DELIVERY=2`,
  `GT_CHANGE_SURFACE=13`, `GT_LOC_RESLOT=4`, `GT_PATCH_DELTA=10`.
- `llm-inference-batching-scheduler`: `localization=5`,
  `newfile_precedent=1`, `obligations=1`, `syntax_result=1`,
  `GT_CERT_DELIVERY=1`, `GT_CHANGE_SURFACE=5`, `GT_LOC_RESLOT=5`,
  `GT_PATCH_DELTA=4`.
- `modernize-scientific-stack`: `localization=2`, `obligations=1`,
  `syntax_result=1`, `GT_CERT_DELIVERY=1`, `GT_CHANGE_SURFACE=3`,
  `GT_LOC_RESLOT=2`, `GT_PATCH_DELTA=2`.
- `portfolio-optimization`: `covering_red=1`, `localization=3`,
  `obligations=1`, `syntax_result=1`, `GT_CERT_DELIVERY=4`,
  `GT_CHANGE_SURFACE=7`, `GT_EDIT_CHECK=4`, `GT_HYPOTHESIS=1`,
  `GT_LOC_RESLOT=3`, `GT_PATCH_DELTA=4`.
- `schemelike-metacircular-eval`: `localization=16`, `obligations=1`,
  `GT_CERT_DELIVERY=10`, `GT_CHANGE_SURFACE=21`, `GT_EDIT_CHECK=11`,
  `GT_LOC_RESLOT=16`, `GT_PATCH_DELTA=11`.
- `write-compressor`: `localization=3`, `newfile_precedent=3`,
  `obligations=1`, `GT_CERT_DELIVERY=1`, `GT_CHANGE_SURFACE=6`,
  `GT_LOC_RESLOT=3`, `GT_PATCH_DELTA=6`.

## Per-task matched deltas

Delta is GT-on minus frozen GT-off. Positive resource values are regressions.

| Task | Reward | Tokens | Calls | Steps | Actions | Failed | Repeats | Context chars | Agent sec | GT censor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| break-filter-js-from-html | 1→1 | +14,934 | +4 | +4 | +5 | -1 | 0 | -17,588 | +5 | no |
| cobol-modernization | 1→1 | +2,388,049 | +27 | +26 | +10 | +3 | 0 | +680,376 | +559 | `AgentTimeoutError` |
| fix-code-vulnerability | 1→1 | -189,142 | -9 | -9 | -9 | 0 | 0 | -406,630 | -16 | no |
| gpt2-codegolf | 0→0 | -7,467,559 | -27 | -28 | -28 | +1 | 0 | -10,736,357 | 0 | `AgentTimeoutError` |
| headless-terminal | 1→1 | -4,475,513 | -59 | -59 | -59 | -4 | 0 | -6,380,616 | -562 | no |
| llm-inference-batching-scheduler | 1→1 | -1,877,489 | -13 | -13 | -13 | -1 | 0 | -1,786,818 | -316 | no |
| modernize-scientific-stack | 1→1 | -4,281 | -1 | -1 | -4 | 0 | 0 | -12,550 | +5 | no |
| portfolio-optimization | 1→1 | -145,697 | -6 | -6 | -10 | -3 | 0 | -106,638 | -54 | no |
| schemelike-metacircular-eval | 1→1 | +2,165,582 | 0 | 0 | -24 | -10 | +3 | +3,279,215 | +229 | `StepLimitExceeded` |
| write-compressor | 1→1 | -663,725 | -2 | -2 | -2 | 0 | 0 | -139,954 | -294 | no |

## Aggregate metrics

### All ten tasks

| Metric | GT-off | GT-on | Delta | Delta % |
|---|---:|---:|---:|---:|
| Solved | 9 | 9 | 0 | 0% |
| Total tokens | 29,223,016 | 18,968,175 | -10,254,841 | -35.09% |
| API calls | 420 | 334 | -86 | -20.48% |
| Assistant steps | 420 | 332 | -88 | -20.95% |
| Actions | 483 | 349 | -134 | -27.74% |
| Failed actions | 38 | 23 | -15 | -39.47% |
| Repeated commands | 3 | 6 | +3 | +100% |
| Context characters | 30,874,834 | 15,247,274 | -15,627,560 | -50.62% |
| Agent wall seconds | 6,239.715 | 5,794.755 | -444.959 | -7.13% |
| Normalized token cost | $0.280391 | $0.317759 | +$0.037368 | +13.33% |

These totals are not an acceptance win because they include censored tasks and
the failed `gpt2-codegolf` timeout witness.

### Seven uncensored tasks solved in both arms

Tasks: `break-filter-js-from-html`, `fix-code-vulnerability`,
`headless-terminal`, `llm-inference-batching-scheduler`,
`modernize-scientific-stack`, `portfolio-optimization`, and
`write-compressor`.

| Metric | GT-off | GT-on | Delta | Delta % |
|---|---:|---:|---:|---:|
| Total tokens | 10,274,021 | 2,933,108 | -7,340,913 | -71.45% |
| API calls | 222 | 136 | -86 | -38.74% |
| Assistant steps | 222 | 136 | -86 | -38.74% |
| Actions | 240 | 148 | -92 | -38.33% |
| Failed actions | 18 | 9 | -9 | -50.00% |
| Repeated commands | 0 | 0 | 0 | 0% |
| Context characters | 11,486,618 | 2,635,824 | -8,850,794 | -77.05% |
| Agent wall seconds | 3,214.867 | 1,982.569 | -1,232.298 | -38.33% |
| Normalized token cost | $0.130848 | $0.097287 | -$0.033561 | -25.65% |

This is a descriptive witness only. `break-filter-js-from-html` still has a
positive token/call/action delta, so even this subset is not per-task Pareto.

## Audit-driven bugs found after the smoke

### 1. Outer Harbor censoring was undercounted

The old extractor read only the Mini-SWE trajectory exit status. If Harbor
terminated the agent after the last receipt, the receipt could not record that
future exception. `gpt2-codegolf` therefore appeared uncensored even though its
trial `result.json` contained `AgentTimeoutError`.

Fixed behavior:

- shared extraction accepts the outer Harbor result;
- adjacent trial results are discovered automatically;
- frozen merged baseline results can be supplied explicitly;
- outer timeout/cancellation types mark the trajectory censored;
- agent and trial wall time are measured from Harbor timestamps;
- reports show censor reasons and fail the gate.

This correction also exposed a new treatment-only timeout on
`cobol-modernization`. It earned reward 1 because the verifier found a working
workspace, but the agent did not terminate normally within 900 seconds.

### 2. `edit_target_absent` was not a material contradiction

The paid shadow receipts recorded 104 candidate `RETURN_TO_MODEL` decisions.
All 104 were `edit_target_absent`; all were edit-classified commands; many were
ordinary `/tmp` scratch-file redirects or directory creation. Shadow mode
downgraded every one to PASS, so the paid trajectory was not changed. Enabling
assistive mode would have added unnecessary provider calls.

Fixed behavior:

- absence of an edit target defaults to PASS;
- normal redirect/scratch creation is not treated as a contradiction;
- an in-place tool may execute and expose its real diagnostic through
  authoritative postflight;
- admission rejects any legacy `edit_target_absent` return;
- submit blockers and injected mechanically proven edit contradictions retain
  the real return-to-model boundary.

Replaying all ten trajectories through the fixed policy produced 0 material
preflight candidates, down from 104, without changing recorded actions.

### 3. Compiler state-frame characters were missing from GT context totals

The receipts always stored `context_compiler.active_state_chars`, but the
shared summary reported only runtime advisory characters. The extractor and
new receipts now expose `context_state_frame_chars_added`,
`gt_context_chars_added`, and `total_gt_context_chars_added`.

This directly accounts for deterministic context that influenced request
construction even when no active guidance delivery occurred.

## Post-fix provider-free verification

- Exact paid-workflow semantic suite: 173 tests passed.
- Focused regression tests: absent redirects PASS; absent in-place edits PASS;
  outer Harbor timeout and wall-time extraction pass.
- Ruff on changed Python: clean.
- Both census entrypoints: all required lines, including
  `ALL_EFFECTS_CONTEXT_ACCOUNTED` and `NO_ACTIONS_BLOCKED`.
- Readiness audit: `READY`.
- Ten-task policy replay: `REPLAY_OK`.
- Post-fix replay material preflight candidates: 0.

The workflow/readiness rule was renamed to say there is no *additional inner*
model timeout. Harbor's matched outer timeout still exists and must never be
hidden by the receipt audit.

## What is proven and what is not

Proven:

- typed proposals reached GT before execution;
- shadow preflight observed every proposed action without changing it;
- postflight observed actual execution;
- context facts and effects were completely accounted;
- no unique Mini-SWE reasoning was removed;
- visible facts were grounded and on time;
- verifier outcome was preserved in this smoke;
- the two audit bugs and one accounting gap are fixed provider-free.

Not proven:

- per-task no-regression;
- strict Pareto efficiency;
- assistive-safe intervention quality in a paid run;
- causal benefit from the compiler;
- that 11 naturally fired features means all 17 fired in this smoke;
- readiness for the 89-task run.

## Remaining work

1. Commit and push the post-smoke audit fixes.
2. Run the exact pushed-commit provider-free pre-smoke gate.
3. Do not launch another paid run without separate authorization.
4. If authorized, use a repeated matched 10-task design; require no new
   treatment censor, no lost solve, zero compiler-accounting violations, and
   report normalized cost as well as raw tokens.
5. Keep `ASSISTIVE_SAFE` paid execution disabled until a provider-free
   intervention corpus proves only mechanically grounded contradictions return.
6. Keep the 89-task run blocked until repeated outcome-first and strict
   efficiency gates pass.
