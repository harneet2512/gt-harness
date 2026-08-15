# GT ten-task smoke 31068690296 audit

## Verdict

**REJECTED as GT proof.** GitHub workflow `31068690296` completed all ten task
jobs and the merge job on commit `b0c7760`. Reward matched the frozen GT-off
baseline at 9/10, and aggregate resources improved substantially. The run still
fails the release gate because:

1. two of six provider payloads were semantically wrong;
2. six solved tasks regressed on at least one resource dimension; and
3. `write-compressor` newly hit Harbor's outer 900-second `AgentTimeoutError`.

The 89-task run remains blocked. This smoke is diagnostic evidence only.

## Exact experiment

- Treatment: `MiniSweCentralAgent`, `integration_mode=active`,
  `preflight_mode=shadow`, context compaction off, task-start advisory off.
- Model: `deepseek-v4-flash`, temperature 1.0.
- Frozen comparison: `D:\Downloads\gt-off-baseline deepseeknew`; it was not
  rerun.
- Treatment artifacts: `D:\gt_runs\31068690296`.
- Deep metrics: `D:\gt_runs\31068690296\metrics`.
- Completeness: 10 trajectories, 10 central receipts, 10 task artifacts, and
  one merged artifact.

## Outcome and resource comparison

Aggregate treatment-minus-baseline deltas:

| Metric | GT-off | GT-on | Delta |
|---|---:|---:|---:|
| Solved | 9/10 | 9/10 | 0 |
| Total tokens | 29,223,016 | 17,887,564 | **-11,335,452 (-38.79%)** |
| Input tokens | 28,682,113 | 17,355,151 | -11,326,962 (-39.49%) |
| Output tokens | 540,903 | 532,413 | -8,490 (-1.57%) |
| Cache tokens | 28,327,680 | 17,125,504 | -11,202,176 (-39.54%) |
| Uncached input | 354,433 | 229,647 | -124,786 (-35.21%) |
| API calls | 420 | 372 | **-48 (-11.43%)** |
| Actions | 483 | 397 | **-86 (-17.81%)** |
| Assistant steps | 420 | 371 | **-49 (-11.67%)** |
| Normalized cost | $0.280391 | $0.229178 | **-$0.051213 (-18.26%)** |
| Agent wall time | 6,239.7 s | 5,241.1 s | -998.6 s (-16.00%) |
| Trial wall time | 6,949.9 s | 5,616.7 s | -1,333.3 s (-19.18%) |
| Context characters | 30,874,834 | 16,524,630 | -14,350,204 (-46.48%) |
| Failed actions | 38 | 29 | -9 |
| Wasted-action proxy | 41 | 35 | -6 |

Per-task deltas are treatment minus baseline. Positive token/call/action/step
deltas are resource regressions.

| Task | Reward off/on | Token delta | Call delta | Action delta | Step delta | Cost delta | Agent-sec delta | GT effects / payloads | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| break-filter-js-from-html | 1/1 | +424,340 | +15 | +11 | +15 | +$0.004955 | +153.6 | 11 / 0 | Pareto fail |
| cobol-modernization | 1/1 | +267,733 | +9 | -11 | +9 | +$0.002943 | +111.8 | 77 / 1 | Pareto fail |
| fix-code-vulnerability | 1/1 | +519,281 | +7 | +20 | +7 | +$0.003945 | +37.2 | 50 / 0 | Pareto fail |
| gpt2-codegolf | 0/0 | -6,333,060 | -26 | -27 | -27 | -$0.031277 | +0.1 | 27 / 0 | Both arms timed out |
| headless-terminal | 1/1 | -3,123,462 | -36 | -28 | -36 | -$0.017220 | -322.5 | 50 / 1 | Strict Pareto pass |
| llm-inference-batching-scheduler | 1/1 | -1,280,096 | -1 | -1 | -1 | -$0.017057 | -392.4 | 36 / 1 | Pareto pass; bad payload |
| modernize-scientific-stack | 1/1 | +41,140 | +4 | -2 | +4 | +$0.000925 | +23.0 | 13 / 0 | Pareto fail |
| portfolio-optimization | 1/1 | +20,898 | +1 | -3 | +1 | -$0.000453 | -22.9 | 35 / 1 | Pareto fail; bad payload |
| schemelike-metacircular-eval | 1/1 | -4,269,663 | -47 | -71 | -47 | -$0.011610 | -822.1 | 28 / 1 | Strict Pareto pass |
| write-compressor | 1/1 | +2,397,437 | +26 | +26 | +26 | +$0.013636 | +235.5 | 78 / 1 | Pareto fail; new outer timeout |

The aggregate improvement is real as a descriptive sample but not a causal GT
claim. Six task-level resource failures and the new timeout defeat the
outcome-first gate.

## Why the per-task paths diverged

For all ten tasks, the initial system and user message objects were exactly
equal between the frozen baseline and treatment. The first assistant command
was different on all ten tasks. Because task-start delivery was disabled and
preflight was SHADOW, this divergence happened before GT could provide any
postflight payload or change an action. At temperature 1.0, the runs are
independent stochastic trajectories, not paired counterfactuals.

This is conclusive for the three positive-delta tasks with zero payloads:
`break-filter-js-from-html`, `fix-code-vulnerability`, and
`modernize-scientific-stack`. Their provider history was not transformed and
GT emitted no model-visible text, so their token/step regressions are not
caused by GT context. GT can still add host-side scan latency, but it cannot
cause those model-token deltas through a provider message that never existed.

That does not rescue the run. The paid comparison uses the frozen installed
Mini-SWE baseline rather than a same-central-loop `integration_mode=off` live
arm, so it cannot isolate every harness-level effect. A single temperature-1
sample also cannot prove causal efficiency or a per-task no-regression
guarantee.

## Engine and all-17 accounting

- Provider-free census: all 17 producer and consumer paths proven.
- Naturally fired in this smoke: **12/17**.
- Not naturally fired: `covering_red`, `GT_HYPOTHESIS`, `recovery`,
  `GT_SS_SUBMIT_RED`, `submit_refusal`. Their required grounded repeated/failing
  validation states did not occur.
- Effects produced/applied: **405/405**.
- Effect accountability: 328 `engine_internal_state`, 19
  `existing_engine_actuation`, 6 `provider_payload`, 45
  `unread_private_state`, and 7 `expired_unconsumed_claim`.
- Exact provider hash coverage: **372/372 calls (100%)**.
- Context candidates/accounted: **4,861/4,861**; unaccounted effects: 0.
- SHADOW preflight: **397 calls, 397 PASS**, 1,141 typed targets, 0 returns,
  rewrites, suppressions, batch interruptions, or cancelled actions.
- Context transformation: 0 state frames, 0 changed provider views, 0 removed
  duplicate/reasoning characters.
- Validation: 393 UNKNOWN, 2 attributable PASS, 1 attributable FAIL; 15
  recognized/declared validation intents correctly remained unattributed
  because a pipeline or trailing shell segment owned the outer status.
- Visible deliveries: 6, all in the first eligible request, 0 late, 0
  predictive.

Receipt counts therefore prove a working deterministic engine and exact
transport, not uniformly useful payload selection.

## Payload-by-payload semantic audit

| Task | Feature | Payload | Timing | Semantic verdict |
|---|---|---|---|---|
| cobol-modernization | signature_delta | `write_records` signature change in `program.py` | first eligible | Valid |
| headless-terminal | newfile_precedent | `headless_terminal.py` -> `base_terminal.py` | first eligible | Valid |
| llm-inference-batching-scheduler | newfile_precedent | `optimized_packer.py` -> empty `__init__.py` | first eligible | **Invalid/irrelevant**; `baseline_packer.py` was the useful sibling |
| portfolio-optimization | GT_EDIT_CHECK | called generated `data_8000.pkl` authored source and requested build validation | first eligible | **Invalid lifecycle**; `.pkl` was benchmark data |
| schemelike-metacircular-eval | GT_EDIT_CHECK | unvalidated `eval.scm` plus exact declared check | first eligible | Valid |
| write-compressor | newfile_precedent | `enc.c` -> `decomp.c` | first eligible | Valid |

Only **4/6** visible payloads passed semantic review. Correct timing cannot make
wrong lifecycle classification useful.

## Repairs made after the paid SHA

The following repairs are local descendants of the paid commit and were not in
workflow `31068690296`:

1. Serialized/generated formats (`.pkl`, `.pickle`, `.npy`, `.npz`, `.pt`,
   `.parquet`, `.feather`, `.arrow`, `.h5`, `.hdf5`, `.onnx`, `.pb`, `.wasm`)
   are derived artifacts. They cannot advance source revision or validation
   debt.
2. `newfile_precedent` requires concrete non-empty content and ranks sibling
   stems by deterministic token overlap, then content size. It selects
   `baseline_packer.py` for `optimized_packer.py` and abstains when only an
   empty `__init__.py` exists.
3. Required `/app/...` deliverables canonicalize to sensor-relative paths, so
   `/app/report.jsonl` remains a task deliverable rather than authored source.
4. Replay distinguishes declared validation intent from an attributable
   terminal result; pipelines do not create a false missing-certificate error.

RED-first tests cover all four boundaries. The full central/replay selection is
green, and all ten paid trajectories replay under the repaired policy with
`REPLAY_OK`.

## Remaining work

1. Run the complete provider-free census/readiness/pre-smoke gate on the new
   exact pushed repair commit.
2. Do **not** start another paid smoke without explicit authorization.
3. If authorized, repeat the same ten tasks and require: 10 complete artifacts,
   no new outer censor, 100% exact request coverage, zero provider-view change,
   100% semantic payload validity, no solve regression, and an outcome-first
   efficiency gate. One run remains descriptive; repeated matched trials are
   required for a causal efficiency claim.
4. Keep the 89-task run blocked.
