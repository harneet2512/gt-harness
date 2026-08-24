# Paid Benchmark Authorization

Verdict: `AUTHORIZED`

Certified implementation: `79321e0da09174805a0909f69dc695dd129a5ebf`.

Authorization is limited to **one final 20-task GT-on prerelease smoke** through
`.github/workflows/tb2_miniswe_product.yml`. It does not authorize a broad paid
Bare/GT/GitNexus matrix.

## Preconditions satisfied

- product verdict `CERTIFIED_WITH_DECLARED_LIMITATIONS` with zero errors;
- exact graph identity and ten-repository matrix PASS;
- 62/62 bounded independent graph truth PASS;
- 9/9 graph lifecycle PASS;
- six-language lifecycle PASS;
- pinned real dense build/query/edit/restart PASS;
- actual Mini-SWE 2.2.8 same-observation Harness E2E PASS;
- 18/18 failure campaign PASS;
- bounded GitNexus fact comparison and mechanism analysis complete;
- final workflow statically rejects Nano, MCP, central-agent, and alternate scaffold paths;
- no unresolved critical product defect.

## Frozen run contract

- repository: `harneet2512/gt-harness`;
- workflow: `tb2_miniswe_product.yml`;
- task set: `repair20-v1`;
- task-set SHA-256: `36d5c8945f6f8d9ae23fe2cea759f16da0c0cea424a98f710cfaa0d9d6fd0303`;
- agent: Mini-SWE-Agent `2.2.8` only;
- model request: `stealth/ox-alpha` through `OPENROUTER_NEW`;
- treatment: GroundTruth, `hybrid_required`;
- concurrency: 20;
- attempts: one per task;
- trajectories and GT receipts: required for all outcomes;
- final source SHA: the documentation-only release SHA created after this file;
- reruns: forbidden unless the user separately authorizes a new experiment.

The run must still be manually dispatched after the user reviews the benchmark-ready
SHA. Authorization is not execution.
