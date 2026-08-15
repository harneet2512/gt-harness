# GT-on smoke `30976148466` versus frozen GT-off baseline

Date: 2026-08-05  
GT-on commit: `951e136`  
GT-off baseline run: `30665246698`  
Dataset: Terminal-Bench 2.0, task checksum bound in the archived receipts  
Model: DeepSeek V4 Flash, temperature 1.0

This is a descriptive comparison of archived runs. The GT-off baseline was not
rerun. The GT-on workflow used `preflight_mode=shadow`, so preflight did not
change commands, batches, or model reasoning. It is therefore not a causal
efficiency experiment.

## Per-task metrics

Baseline tokens/calls come from
`D:\gt_runs\miniswe_tb2_gtoff_20260731\per_task_tokens.json`. Baseline actions
are derived from the assistant `extra.actions` arrays in the frozen
`matrix_cache` trajectories. GT-on values come from each task's
`agent_result.metadata` in `artifacts/run-30976148466`.

| Task | Reward B→GT | Total tokens B→GT | Δ tokens | Calls B→GT | Δ calls | Actions B→GT | Δ actions | GT censored |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| break-filter-js-from-html | 1→1 | 166,564→113,869 | -52,695 | 12→14 | +2 | 16→16 | 0 | no |
| cobol-modernization | 1→0 | 1,437,605→581,750 | -855,855 | 39→100 | +61 | 59→100 | +41 | yes |
| fix-code-vulnerability | 1→0 | 451,819→517,472 | +65,653 | 33→100 | +67 | 33→100 | +67 | yes |
| gpt2-codegolf | 0→0 | 8,784,582→429,107 | -8,355,475 | 59→43 | -16 | 59→42 | -17 | no |
| headless-terminal | 1→1 | 4,921,513→538,799 | -4,382,714 | 86→100 | +14 | 86→103 | +17 | yes |
| llm-inference-batching-scheduler | 1→0 | 3,000,980→695,046 | -2,305,934 | 41→100 | +59 | 42→101 | +59 | yes |
| modernize-scientific-stack | 1→1 | 40,243→91,450 | +51,207 | 8→12 | +4 | 16→12 | -4 | no |
| portfolio-optimization | 1→1 | 435,035→510,465 | +75,430 | 26→100 | +74 | 30→100 | +70 | yes |
| schemelike-metacircular-eval | 1→0 | 8,489,839→902,763 | -7,587,076 | 100→100 | 0 | 125→100 | -25 | yes |
| write-compressor | 1→0 | 953,933→327,706 | -626,227 | 16→25 | +9 | 17→24 | +7 | no |

## Cumulative comparison

| Metric | Frozen GT-off | GT-on shadow smoke | Delta | Interpretation |
|---|---:|---:|---:|---|
| Solved | 9/10 | 4/10 | -5 tasks | Regression in this sample |
| Total tokens | 28,682,113 | 4,708,427 | -23,973,686 (-83.6%) | Censored/descriptive; not an efficiency win |
| API calls | 420 | 694 | +274 (+65.2%) | Worse |
| Tool actions | 483 | 698 | +215 (+44.5%) | Worse |
| GT-on step-limit censures | n/a in frozen per-task file | 6/10 | n/a | Six trajectories stopped at 100 assistant steps |

The token delta cannot be interpreted as savings: the GT-on run lost five
baseline solves and censored six tasks. The primary acceptance metric is solved
outcome under matched budgets; this smoke fails that comparison.

## Correct feature-coverage interpretation

The provider-free census proves all 17 producer/consumer paths and forced
trigger cases. The paid smoke naturally triggered 15/17 feature IDs;
`recovery` and `signature_delta` had no receipts because their exact events did
not occur. This distinction must be preserved:

```text
17 paths proven by census != 17 features fired in every paid trajectory
361 effects != 36 model-visible payloads
private engine work != unused work
model-visible delivery != model comprehension
```

The smoke's effect trace contained 274 `engine_internal_state` effects, 7
existing-engine-actuation effects, 36 provider-payload effects, and 44
audit-only effects. The accounting correction in commit `837c124` prevents
producer-side engine work from being mislabeled as inert merely because it was
not provider-visible.

## Decision

This run proves receipt integrity, payload grounding, delivery timing, and
preflight measurement. It does not prove GT-on efficiency or outcome
improvement. Before the 89-task run, the next required evidence is a matched
assistive-mode smoke with forced coverage for `recovery` and
`signature_delta`, followed by repeated GT-off/GT-on trials.
