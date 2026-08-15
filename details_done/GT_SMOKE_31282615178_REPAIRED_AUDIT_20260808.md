# GT-on Smoke 31282615178 — Repaired Treatment Audit

Date: 2026-08-08  
Commit: `75a9b2e`  
Workflow: `31282615178`  
Slice: frozen ten-task matched smoke, all 17 features, ACTIVE + SHADOW

## Verdict

The repository-intelligence repair worked: the merge reported zero invalid
repository-intelligence tasks. `gpt2-codegolf` began with no supported source,
then authored source during the trajectory; its initial graph failures are now
recorded as transient and the final source-backed graph passed. `write-
compressor` ended with a healthy current graph and passed.

The treatment still fails the outcome gate: 8/10 verifier rewards versus the
GT-off baseline's 9/10. `schemelike-metacircular-eval` is the new clean solve
loss; it was submitted normally, without an outer censor or solver timeout.
`gpt2-codegolf` remained the known baseline-unsolved task and reached the
deadline reserve normally.

This is valid integrity evidence but rejected efficiency/outcome evidence.

## Per-task deltas

Delta is GT-on minus the frozen GT-off baseline. Positive resource deltas are
regressions.

| Task | Reward delta | Token delta | API-call delta | Step delta | Action delta |
|---|---:|---:|---:|---:|---:|
| break-filter-js-from-html | 0 | +692,825 | +17 | +17 | +15 |
| cobol-modernization | 0 | +3,136,372 | +37 | +37 | +17 |
| fix-code-vulnerability | 0 | +1,886,870 | +27 | +27 | +38 |
| gpt2-codegolf | 0 | -6,616,501 | -17 | -18 | -18 |
| headless-terminal | 0 | -2,700,107 | -35 | -35 | -35 |
| llm-inference-batching-scheduler | 0 | -339,019 | +3 | +3 | +7 |
| modernize-scientific-stack | 0 | +21,367 | 0 | 0 | +2 |
| portfolio-optimization | 0 | +874,805 | +25 | +25 | +25 |
| schemelike-metacircular-eval | **-1** | -204,359 | -23 | -23 | -35 |
| write-compressor | 0 | -687,588 | -4 | -4 | -1 |
| **Total** | **-1** | **-3,935,335** | **+30** | **+29** | **+15** |

Aggregate token reduction is not an efficiency win: calls, steps, and actions
increased, and the only new outcome loss is on a baseline-solved task.

## GT integrity

- All 10 tasks had all 17 features enabled.
- Natural firing ranged from 3 to 11 feature IDs; no paid trajectory fired
  all 17 naturally.
- 371 effects were produced and applied.
- Accountability: 212 engine-internal, 22 existing-engine actuation, 6
  expired claims, 124 unread-private, 6 provider payloads, and 1 prepared
  decision frame.
- Six model-visible feature payloads were delivered. Four tasks had zero
  model-visible feature guidance; private engine work must not be reported as
  model assistance without downstream evidence.
- All visible payloads were first-eligible, grounded, non-predictive, and zero
  steps late.
- Provider request hash coverage was 100% for all tasks.
- Context candidate and accounted-fact counts matched for all tasks.
- No unique assistant reasoning was removed.

## Regression diagnosis

The archived run diff shows first model-action divergence before visible GT
evidence on most tasks, consistent with temperature-1 stochasticity. The
scheme task did receive repository frontier facts at calls 1–2 and one
grounded edit-check payload at call 25; timing and accounting were correct.
The trajectory still ended unsolved at 77 calls. This run cannot establish
that those facts caused the loss; a matched ablation is required.

The large positive cost/resource deltas are concentrated in
`cobol-modernization`, `fix-code-vulnerability`, and `portfolio-optimization`.
They are not explained by a repository-intelligence failure: all three had
healthy current graphs and complete accounting. They require component-level
ablation before another full smoke.

## Next gate

Do not start the 89-task run. First run provider-free request/trajectory
ablation for the large-expansion tasks and the scheme loss, then obtain a new
authorization for another matched smoke only if the ablation identifies a
repair. The repaired graph/applicability code is validated; outcome efficiency
is not.

