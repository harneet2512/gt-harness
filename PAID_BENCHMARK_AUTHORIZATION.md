# Paid Benchmark Authorization

Verdict: `NOT_AUTHORIZED`

Current implementation: `8931876541ec82ec96799f6c4462b5c0726e4518`.

The previous limited authorization was consumed by GT-only repair20 runs
[32676409425](https://github.com/harneet2512/gt-harness/actions/runs/32676409425)
and [32680131105](https://github.com/harneet2512/gt-harness/actions/runs/32680131105).
The latter failed final attestation with two genuinely nonterminal product receipts.

Current code fixes the demonstrated causes and passes provider-free exact-SHA
certification. A new paid run is still a separate experiment and requires explicit
user authorization. Broad Bare/GT/GitNexus or DeepSWE spending is not authorized.

Before a future authorization:

1. freeze the exact implementation and registered workflow hashes;
2. retain Mini-SWE-Agent 2.2.8, one attempt, task-owned Harbor ceilings, and full
   trajectories;
3. require terminal GT receipts and trajectory-backed call accounting for every task;
4. declare whether the run is GT-only lifecycle verification or a controlled
   same-model outcome comparison; and
5. forbid using a different-model local baseline as causal evidence.

No paid run was dispatched for `8931876` while producing this document.
