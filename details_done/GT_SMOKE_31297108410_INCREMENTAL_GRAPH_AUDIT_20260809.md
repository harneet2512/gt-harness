# GT-on smoke 31297108410 — incremental-graph audit

Date: 2026-08-09  
Commit: `b686a5429d7d6cfd23848e29aaedacae73005df3`  
Corrective follow-up: `04c57c6`  
Workflow: [31297108410](https://github.com/harneet2512/gt-harness/actions/runs/31297108410)

## Verdict

This is diagnostic evidence, not an approved treatment and not a valid ten-task
outcome comparison. The portfolio job failed before producing a receipt, so the
merge has only nine of ten tasks. The Scheme receipt also found a real frontier
deduplication defect. That defect is repaired in `04c57c6` and covered by a
provider-free regression test, but this paid receipt predates the repair.

The run does prove that the graph lifecycle reached source-backed repositories
and performed incremental refreshes during action execution. It does not prove
that every task has a graph: `gpt2-codegolf` never authored a supported source
file, so `no_supported_source` is a correct applicability result, not a stale
graph claim.

## Workflow/outcome accounting

| item | result |
|---|---:|
| requested tasks | 10 |
| task receipts | 9 |
| missing task | `portfolio-optimization` (Harbor task job failure; budget artifact only) |
| official rewards in available tasks | 8/9 |
| frozen GT-off rewards on those same available tasks | 8/9 |
| full ten-task solve claim | not computable |
| 89-task dispatch | not authorized |

The missing portfolio job is an infrastructure censoring event, not a solve.
It cannot be counted as solved or unsolved.

## Graph lifecycle

| task | graph status | nodes | edges | refreshes | incremental | full | fallback |
|---|---|---:|---:|---:|---:|---:|---:|
| break-filter-js-from-html | passed/current | 2 | 0 | 9 | 2 | 1 | 0 |
| cobol-modernization | passed/current | 8 | 6 | 6 | 1 | 1 | 0 |
| fix-code-vulnerability | passed/current | 1,091 | 2,748 | 23 | 1 | 1 | 0 |
| gpt2-codegolf | not applicable: no supported source | 0 | 0 | 1 | 0 | 1 | 1 |
| headless-terminal | passed/current | 21 | 48 | 26 | 12 | 2 | 0 |
| llm-inference-batching-scheduler | passed/current | 21 | 44 | 3 | 0 | 1 | 0 |
| modernize-scientific-stack | passed/current | 10 | 20 | 9 | 1 | 1 | 0 |
| schemelike-metacircular-eval | graph current, receipt rejected for duplicate frontier claims | 351 | 299 | 67 | 10 | 1 | 0 |
| write-compressor | passed/current | 4 | 0 | 2 | 0 | 1 | 0 |

Among the eight source-backed tasks with healthy receipts, every graph ended at
the current source revision; 27 incremental refreshes were recorded. COBOL and
Scheme both produced parser-backed graph nodes, and Scheme produced certified
directed caller evidence. The no-source gpt2 result is explicitly separated from
an index failure: the trajectory never created `gpt2.c` or another supported
authored source file.

## GT effects and delivery

Across the nine receipts:

* 17 feature IDs were enabled in every task.
* 200 effects were produced and 200 applied.
* Effect dispositions: 135 `engine_internal_state`, 6
  `existing_engine_actuation`, 3 `provider_payload`, and 56 `audit_only`.
* 312/312 provider requests were hashed; 5,534 context facts were accounted.
* 18 graph-frontier deliveries and 3 feature-guidance deliveries were recorded.
  All 18 frontier deliveries were first-eligible, non-predictive, and not one
  step late. Frontier semantic matching was 8 same-response, 5 deferred, 4
  stale-after-source-revision, and 1 no typed-action match. This is a behavioral
  utilization proxy, not causal proof.
* Natural paid trajectories fired 11 of 17 IDs. The remaining IDs were not
  evidence of missing producers: their exact grounded triggers were absent in
  these trajectories. Provider-free forced-trigger census remains the proof of
  all 17 paths.

## Defect found and repaired

Scheme had two `Pair` call sites at different lines with one line-independent
semantic claim ID. The frontier selected both in one frame, causing:

```text
duplicate_frontier_fact_delivery
duplicate_frontier_claim_delivery
```

`04c57c6` coalesces candidates by semantic `claim_id` as well as physical
location, retaining the first deterministic role candidate. Tests pass for the
stable-claim and repeated-call-site cases. The paid Scheme receipt must not be
reclassified retroactively; a new authorized smoke is required to verify the
corrected receipt path.

## Frozen GT-off comparison (available common tasks only)

The frozen baseline is `C:\Users\Lenovo\Downloads\deep_metrics_baseline.json`.
For the nine tasks with a GT-on receipt, treatment minus baseline was:

| measure | GT-off | GT-on | delta |
|---|---:|---:|---:|
| total tokens | 28,763,326 | 19,339,319 | -9,424,007 (-32.76%) |
| API calls | 394 | 312 | -82 |
| assistant steps | 394 | 311 | -83 |
| model actions | 453 | 337 | -116 |
| rewards | 8/9 | 8/9 | 0 |

These are descriptive temperature-1 matched-slice deltas with one missing task;
they are not an efficiency or causal outcome claim. The large negative token
delta is dominated by stochastic trajectory differences and cannot approve the
treatment while portfolio is missing and the pre-fix duplicate receipt exists.

## Required next gate

1. Keep `04c57c6` as the candidate commit.
2. Rebuild the certified `gt-index` binary in CI and rerun the provider-free
   census/readiness suite; the local Windows binary is stale and cannot certify
   the language fixture gate.
3. Replay the archived Scheme receipt through the corrected frontier compiler.
4. Obtain separate authorization for one matched ten-task smoke. Require all
   ten task receipts, zero duplicate claims, healthy-current graph for every
   source-backed task, explicit no-source applicability for source-less tasks,
   and outcome-preserving/efficiency gates before considering 89.
