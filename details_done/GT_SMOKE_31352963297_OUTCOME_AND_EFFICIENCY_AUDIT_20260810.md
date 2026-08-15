# GT-on smoke 31352963297: outcome and efficiency audit

## Verdict

This is a valid matched ten-task GT-on smoke at commit `34e712e`. It solved
9/10 official verifier tasks, exactly matching the frozen GT-off baseline's
9/10 on the same task set. It is not evidence of causal superiority and it is
not a clean Pareto efficiency win because controller-inclusive executions
increased.

## Outcomes and graph gate

| Task | GT-on reward | Baseline | Graph/intelligence |
|---|---:|---:|---|
| break-filter-js-from-html | 1 | 1 | passed |
| cobol-modernization | 1 | 1 | passed |
| fix-code-vulnerability | 1 | 1 | passed |
| gpt2-codegolf | 0 | 0 | not applicable; source-less at task start |
| headless-terminal | 1 | 1 | passed |
| llm-inference-batching-scheduler | 1 | 1 | passed |
| modernize-scientific-stack | 1 | 1 | passed |
| portfolio-optimization | 1 | 1 | passed |
| schemelike-metacircular-eval | 1 | 1 | passed |
| write-compressor | 1 | 1 | passed |

There was no solve regression against the frozen baseline. `gpt2-codegolf`
remained the known baseline-unsolved task; its binary/data-only initial
substrate is correctly excluded from the graph denominator even though the
model later wrote unsupported helper files.

## Common-solved resource deltas

The nine common solved tasks (excluding the common-unsolved GPT-2 task) changed
as follows, GT-on minus GT-off:

| Metric | Delta |
|---|---:|
| Total model tokens | -2,704,977 (-13.29%) |
| Model API calls | -43 |
| Assistant steps | -43 |
| Model actions | -82 |
| Controller-inclusive effective executions | +345 |

The all-ten aggregate token delta is `-8,277,482` (`-28.33%`) only because the
common-unsolved GPT-2 trajectory was much cheaper; it is not the right primary
efficiency claim. Effective executions include syntax, repository, workspace,
completion, and other controller work, so the positive delta prevents a clean
Pareto claim.

## GT integrity

- 232/232 produced effects applied.
- 4 grounded provider payload deliveries; 4/4 first-eligible and timely.
- 0 late and 0 predictive deliveries.
- 100% provider request-hash coverage.
- 6,277 context facts accounted.
- 396/396 SHADOW preflights applied PASS.
- 0 context compactions and 0 unique assistant-reasoning characters removed.
- Graph intelligence passed for all source-backed tasks; source-less GPT-2 was
  correctly denominator-excluded.

The headless payload was delivered correctly. Its later `stale_source`
semantic-use classification is valid in this trajectory: the model edited the
anchored source file before a matching action, so the original source-bound
fact was stale. This is not a timing failure and is not counted as causal
model acknowledgement.

## Decision

The repairs fixed the two prior audit defects and preserved outcomes on this
smoke. The 89-task benchmark remains blocked because the outcome-first
efficiency gate has not passed: controller-inclusive effective work is higher
and this is still a single temperature-1 witness. The next engineering target
is reducing redundant controller executions without removing required graph,
validation, or completion evidence; do not weaken the graph gate or count
private effects as model-visible help.
