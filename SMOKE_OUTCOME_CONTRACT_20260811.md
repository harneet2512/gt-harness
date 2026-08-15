# Matched outcome smoke contract — 2026-08-11

This is a diagnostic ten-task smoke, not the final 89-task benchmark.

## Frozen task set

```text
prove-plus-comm
sanitize-git-repo
write-compressor
regex-chess
qemu-alpine-ssh
torch-tensor-parallelism
headless-terminal
portfolio-optimization
schemelike-metacircular-eval
count-dataset-tokens
```

This is the repository's pre-existing repair-mix slice. It is retained without
changing membership after seeing the new results. The archived GT-off witness
resolved 9/10, with `count-dataset-tokens` the unsolved task.

## Arms

Both arms use `.github/workflows/tb2_miniswe_central.yml` and
`eval.gt_central_agent:MiniSweCentralAgent` at the same evaluation commit:

| Factor | Frozen value |
|---|---|
| Dataset | `terminal-bench@2.0` |
| Model | `deepseek-v4-flash` |
| Temperature | 1.0 |
| Mini-SWE | 2.2.8 |
| Image tag | 20251031 |
| Parallelism | 10 |
| Timeout multiplier | 1.0 |
| Step limit | 100 |
| Replay capture | enabled |
| Baseline arm | `off` |
| Treatment arm | `certified_context` |
| Preflight | `shadow` |

The baseline disables GT and all controller/context features. The treatment
enables the certified repository evidence/frontier path but leaves completion,
progress, adaptive timeout, lint, and compaction controllers disabled. This
isolates evidence delivery; it is not a `certified_full` product-policy test.

## Acceptance audit

For each arm record official reward separately from `uncensored_resolved`,
outer exceptions/censoring, graph applicability, calls, actions, tokens,
wall-clock, and provider request/replay integrity. For each treatment task
audit feature effects, grounded payload timing, and whether the graph substrate
was valid. A graph failure on a source-backed treatment task invalidates the
corresponding treatment comparison; it is not a treatment abstention.

This ten-task smoke can show a descriptive outcome witness and expose a
regression class. It cannot establish causal uplift, efficiency, or the final
89-task result. The 89-task run remains blocked until this smoke preserves
outcomes and passes the outcome-first efficiency gate.
