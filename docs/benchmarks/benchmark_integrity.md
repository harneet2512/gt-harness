# Benchmark integrity rules for GT

A benchmark score estimates how the harness does on tasks it has never seen. GT's numbers
mean something only if these hold. `tests/test_benchmark_integrity.py` enforces rule 1 in
code; the rest are review rules.

1. **The harness uses only what a developer would have.** That is the task text, the
   repository at its starting commit, and the repository's own tests. It never uses the
   verifier's tests (`test.patch`, `/tests`), the pass/fail node-id lists (`config.json`
   f2p/p2p), verifier reports, rewards or logs (`/logs/verifier`), or a gold solution, not
   even indirectly.
2. **No per-task tailoring.** No task names, task-specific rules, prompts or thresholds in
   harness code.
3. **Diagnose freely, decide on unseen data.** Reading hidden results offline to understand
   failures is error analysis and is allowed. Any change it motivates is judged on the full
   benchmark with a comparison fixed in advance, or on tasks the analysis did not look at.
   It is never judged on a subset chosen by its outcome. Tasks picked because they narrowly
   failed improve on a rerun by chance alone (regression to the mean).
4. **Equal budget against the baseline.** Same model, step limit, time budget and number of
   attempts. No choosing among attempts, and no retries triggered by verifier outcomes.
5. **Report the variance.** Report replicate runs, paired per-task comparisons and
   confidence intervals. A single run on a temperature-1.0 model is not a result.

## Tasks already used for error analysis (2026-09-30)

- **DeepSWE, GT run 2 (36540668121):** p2p breaks were read for 10 tasks and near-miss
  hidden tests for 12. For any change motivated by that analysis, report results on all 113
  tasks and separately on the tasks excluding those 22.
- **TB2 and SWE-Live Lite:** no hidden-test analysis yet. Both serve as out-of-sample checks.
