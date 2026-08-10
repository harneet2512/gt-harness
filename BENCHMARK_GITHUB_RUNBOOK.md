# Benchmark execution location

All memory-heavy benchmark evaluation is executed on GitHub Actions. The
workstation is limited to source edits, unit tests, provider-free gates, and
small smoke probes. It is not a benchmark runner and must not hold the ARB
corpus or build every repository index locally.

## Retrieval proof

Dispatch [`.github/workflows/arb_gt_retrieval.yml`](.github/workflows/arb_gt_retrieval.yml)
with `release=all`, `shard_count=8`, and `run_baselines=true` for the complete
Agent Retrieval Bench run. The workflow:

1. installs the pinned ARB source commit;
2. downloads and checksum-verifies the official releases on the runner;
3. creates gold-free redacted inputs (gold is never passed to GT);
4. runs the production GroundTruth adapter against exact base-commit Git
   worktrees, one snapshot at a time, across eight independent shards;
5. optionally runs the official all-files lexical baselines on GitHub; and
6. uploads immutable per-shard JSONL receipts and baseline details.

The runner is lossless with respect to repository snapshots and bounded with
respect to parallel memory: each shard processes one exact `(repository,
base_commit)` worktree at a time. Shards are intentionally independent so a
single failure can be retried without rebuilding the whole run.

The workflow is retrieval evidence only. It cannot prove that a model uses a
fact or that a coding task is solved. Those claims require the paired
decision-point evaluation and then a contemporaneous same-wrapper A/B run.

## End-to-end benchmarks

The existing Terminal-Bench/Mini-SWE workflows are also GitHub-only. The
planned order is **DeepSWE first**, using the existing GitHub workflows in the
GroundTruth repository (`deepswe_full.yml` and `deepswe_trial.yml`). Those
workflows launch the pinned Mini-SWE agent through Pier; this is not an
OpenHands/OpenAgents evaluation. A matched GT-off arm is still required.
Terminal-Bench 2.0 follows through this harness's existing GitHub
Mini-SWE/Harbor workflows. SWE-Live Lite is deferred until those gates are
complete. All must be dispatched only from a frozen manifest after runtime,
retrieval, and decision-point gates pass. The 89-task run remains blocked. No
local command may substitute a historical baseline for a contemporaneous
control arm.

## Artifact policy

Inputs, raw trajectories, provider requests, and benchmark outputs remain in
the GitHub Actions artifact store. Do not commit downloaded corpora, cloned
repositories, model responses, credentials, or provider keys. The local
`artifacts/final_execution` directory contains setup receipts and small test
fixtures only; the interrupted local ARB baseline is not evidence.

## Current completion definition

GroundTruth is not “complete” merely because all 17 provider-free paths pass.
Completion requires, in order:

1. deterministic runtime proof (delivery, abstention, timing, revision and
   failure behavior);
2. the GitHub ARB retrieval run and miss taxonomy;
3. at most one generalized retrieval repair, only if ARB shows a repeated
   mechanical defect;
4. paired decision-point model-utility evidence with identical control and
   treatment requests except for grounded GT evidence;
5. a frozen GT/harness/model manifest;
6. same-wrapper SWE-Live Lite A/B and causal gain/loss analysis; and
7. conditional DeepSWE and Terminal-Bench 2.1 generalization runs.

Until those gates are complete, the honest status is **deterministic engine
implemented; empirical usefulness and benchmark readiness unproven**.
