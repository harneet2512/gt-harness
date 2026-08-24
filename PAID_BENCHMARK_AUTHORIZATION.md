# Paid Benchmark Authorization

Verdict: `AUTHORIZED`

Current implementation: `2bab25973fd1e4e90372aac30231bbbe3009b863`.

The previous limited authorization was consumed by GT-only repair20 runs
[32676409425](https://github.com/harneet2512/gt-harness/actions/runs/32676409425)
and [32680131105](https://github.com/harneet2512/gt-harness/actions/runs/32680131105).
The latter failed final attestation with two genuinely nonterminal product receipts.

Current code fixes the demonstrated causes, passes provider-free exact-SHA delta
certification, and has explicit user authorization for one official GT-only
20-task repair smoke through the canonical GitHub Actions workflow. Broad
Bare/GT/GitNexus or DeepSWE spending remains unauthorized.

Before a future authorization:

1. freeze the exact implementation and registered workflow hashes;
2. retain Mini-SWE-Agent 2.2.8, one attempt, task-owned Harbor ceilings, and full
   trajectories;
3. require terminal GT receipts and trajectory-backed call accounting for every task;
4. declare whether the run is GT-only lifecycle verification or a controlled
   same-model outcome comparison; and
5. forbid using a different-model local baseline as causal evidence.

Authorization is limited to `.github/workflows/tb2_miniswe_product.yml`, the frozen
repair20 task set, Mini-SWE-Agent 2.2.8, one attempt, `stealth/ox-alpha`, full
trajectories, and exact source SHA. The resulting run ID must be added after dispatch.
