# GT-on smoke 31145623534 — outcome and integrity audit

Commit: `f03cb023a6914c523f8062688aec52b0f9bcc913` (`inline-engine`)

Scope: the frozen ten-task GT-on treatment set, DeepSeek V4 Flash at the
existing temperature-1 workflow configuration, `integration_mode=active`,
`preflight_mode=shadow`, deterministic context compaction, completion control,
and progress control.

## Release decision

The implementation integrity gate passed. The efficiency gate did not.
This smoke is valid diagnostic evidence and outcome-preservation evidence, but
it is **not** evidence that GT improves efficiency. The 89-task run remains
blocked.

The code change under test (`f03cb02`) is an offline artifact comparator and a
direct-CLI import repair. It does not change Mini-SWE provider messages, GT
feature scheduling, command execution, preflight policy, completion control,
or context compaction. It cannot be the runtime cause of any live trajectory
change in this smoke.

## Outcome

| Metric | GT-off frozen baseline | GT-on 31145623534 |
| --- | ---: | ---: |
| Official verifier rewards | 9 / 10 | 9 / 10 |
| Uncensored resolved | 9 / 10 | 9 / 10 |
| Outer Harbor exceptions | 0 | 0 |

`gpt2-codegolf` is the common unsolved task. `schemelike-metacircular-eval`,
which was the new uncensored loss in `31142998081`, returned reward 1 in this
smoke. It did reach the 100-step limit, but Terminal-Bench ran the verifier,
reported reward 1, and no Harbor exception/censor occurred; it is therefore a
valid uncensored resolution under the project contract.

`cobol-modernization` reached the agent's deadline reserve and still received
an uncensored verifier reward 1. That is an inefficient clean salvage, not an
outer-timeout solve claim.

## GT correctness and timing

All task receipts declared 17 enabled features and a healthy workspace sensor.

| Check | Result |
| --- | ---: |
| Produced effects | 330 |
| Applied effects | 330 / 330 |
| Natural feature IDs fired | 14 / 17 |
| Missed triggers / false fires | 0 / 0 |
| Grounded provider deliveries | 6 |
| First-eligible timely / late / predictive | 6 / 0 / 0 |
| Provider request hash coverage | 456 / 456 (1.0) |
| Context facts accounted | 8,125 / 8,125 |
| Unique Mini-SWE reasoning chars removed | 0 |
| Replayed archived/current trajectory checks | 10 / 10 PASS |

The naturally absent exact triggers were `GT_SS_SUBMIT_RED`, `recovery`, and
`submit_refusal`; their provider-free forced-trigger paths remain part of the
release census. This is not a claim that all 17 fired in this stochastic
trajectory.

The six model deliveries were concrete: three `newfile_precedent`, one
`signature_delta`, and two `GT_EDIT_CHECK`. Each recorded an evidence action,
source revision, first eligible provider call, request hash, message index,
and `delivered_before_model_query=true` / `one_step_late=false` /
`not_predictive=true`. SHADOW preflight did not alter command execution.

## Resource comparison

The common solved set is the only fair aggregate. On those nine tasks GT-on
used **20,422,063** total tokens vs **20,344,163** GT-off: **+77,900**
(+0.38%), and **416** API calls vs **361**: **+55**. The all-ten token total
is lower only because the common unsolved `gpt2-codegolf` happened to use far
fewer tokens; it must not be used as a treatment efficiency win.

Largest adverse per-task deltas among the common solved set:

| Task | Token delta | API-call delta |
| --- | ---: | ---: |
| llm-inference-batching-scheduler | +3,193,455 | +40 |
| cobol-modernization | +2,633,475 | +33 |
| portfolio-optimization | +379,495 | +12 |
| break-filter-js-from-html | +239,741 | +17 |

The smoke therefore preserves the frozen outcome count but does **not**
preserve per-task efficiency. The next work item is causal component ablation
of the expensive trajectories (especially LLM batching and COBOL), not a
larger benchmark dispatch.

## Evidence locations

* Live run: `https://github.com/harneet2512/gt-harness/actions/runs/31145623534`
* Downloaded artifacts: `D:\gt_runs\31145623534`
* Merged metrics: `tb2-miniswe-central-treatment-integrated-31145623534-MERGED\deep_metrics_treatment.json`
* Offline comparisons:
  * `comparison_31136099371_vs_31145623534.json`
  * `comparison_31142998081_vs_31145623534.json`

`central_run_diff` found complete accounting in every task and a first action
divergence before any visible GT evidence in each cross-smoke comparison. It
rules out claims that a later GT delivery caused the initial different sampled
trajectory; it does not establish causal harmlessness for later behavior.
