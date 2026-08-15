# GT-on graph-repair smoke audit — workflow 31329364101

Date: 2026-08-09  
Commit: `31f3db5a33b76cfa07d7d13d30eadeadc5e50ca3`  
Provider-free gate: `31329277692` (`SMOKE_APPROVED`)  
Paid workflow: `31329364101`  
Arm: `certified_full` / `integrated`  
Replay capture: enabled

## Graph repair result

The workflow merge completed successfully. `invalid_repository_intelligence_tasks`
is empty. The previous `gpt2-codegolf` invalid-treatment classification is
fixed:

| Task class | Tasks | Result |
|---|---|---|
| Source-backed | 9 | `repository_intelligence_status=passed`, current schema-valid graph |
| Source-less/artifact-only | 1 (`gpt2-codegolf`) | `not_applicable_no_supported_source`, denominator excluded, no fallback |
| Invalid repository-intelligence treatments | 0 | merge gate passed |

The nine source-backed graph receipts reported valid nodes/edges:

```text
break-filter-js-from-html       2 / 0
cobol-modernization             8 / 7
fix-code-vulnerability       1091 / 2748
headless-terminal              28 / 89
llm-inference-batching         32 / 66
modernize-scientific-stack     11 / 24
portfolio-optimization         14 / 12
schemelike-metacircular-eval  347 / 272
write-compressor               35 / 19
```

GPT-2 correctly reported zero graph nodes and edges because its workspace had
no supported source: only a checkpoint, vocabulary data, and model-generated
scratch C files outside the source mirror. GT emitted no fabricated graph
facts.

## Outcomes

| Outcome | Frozen GT-off | GT-on repaired smoke |
|---|---:|---:|
| Official verifier resolves | 9/10 | 8/10 |
| Uncensored resolves | 9/10 | 8/10 |
| Solve regressions | — | `write-compressor` |
| Treatment censors | — | 0 |
| Invalid graph treatments | — | 0 |

The new loss was not caused by repository intelligence: `write-compressor`
had a valid 35-node/19-edge graph, no degraded fallback, and ended with the
engine's internal `DeadlineReserveReached` exhaustion. `gpt2-codegolf` remained
the known GT-off-unsolved task and also ended in the deadline reserve. The
solve gate therefore fails despite the graph repair succeeding.

## GT effects and delivery timing

All ten tasks enabled all 17 feature IDs. The run produced and applied 262/262
effects, with 11 feature IDs firing naturally across the slice. The remaining IDs had
no eligible event in these trajectories; provider-free census and forced
triggers still cover all 17 paths.

There were five model-visible payloads. Every one was delivered before the
first eligible provider request, with zero late and zero predictive deliveries:

| Task | Feature | Eligible call | Delivered call |
|---|---|---:|---:|
| headless-terminal | `newfile_precedent` | 17 | 17 |
| llm-inference-batching-scheduler | `newfile_precedent` | 35 | 35 |
| portfolio-optimization | `GT_EDIT_CHECK` | 18 | 18 |
| schemelike-metacircular-eval | `GT_EDIT_CHECK` | 22 | 22 |
| write-compressor | `newfile_precedent` | 8 | 8 |

## Baseline resource comparison

The full deep delta is at
`D:\gt_runs\31329364101\delta\DEEP_DELTA.md`. The strict efficiency gate
failed. The treatment lost one baseline solve and failed strict per-task Pareto
on eight solved tasks. Notable deltas (treatment minus baseline):

- aggregate model tokens: **-7,804,195**, API calls **-28**, model actions
  **-65**, but context characters increased by **+1,061,104**;
- aggregate reductions are not an accepted efficiency claim because the
  outcome gate failed;
- `break-filter-js-from-html`: +85,361 tokens;
- `portfolio-optimization`: +129,461 tokens;
- `schemelike-metacircular-eval`: +1,076,828 tokens;
- `write-compressor`: -511,460 tokens but lost the solve at deadline reserve.

## Replay capture

Six of ten bundles were complete trajectory-replay inputs. Four exceeded the
bounded 25 MB capture budget. This limits deterministic replay coverage but
does not affect the graph applicability result. `model_causal_replay_ready`
remains false by design; no model-causal claim is made.

## Release decision

**Graph repair: PASS.** The source-less applicability bug is fixed and all
source-backed graphs passed in the paid workflow.

**GT treatment: REJECTED for benchmarking.** The smoke regressed one baseline
solve and failed strict efficiency. Keep the 89-task run blocked. The next
investigation is the `write-compressor` deadline-reserve regression and the
resource expansions, not graph applicability.
