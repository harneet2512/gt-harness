# GT 20-task Mini-SWE trajectory and local-baseline audit

Audit date: 2026-08-23  
GT workflow run: `32661619500`  
GT source SHA: `5705a1aacd0370e18e02b8d391b165dcd3a55811`  
GT agent: `eval.gt_central_agent:MiniSweCentralAgent`  
Model route: `stealth/ox-alpha` through OpenRouter (`OPENROUTER_NEW`)  
Parallelism: 20 task jobs (`max-parallel: 20`)

This is an end-to-end Mini-SWE smoke and trajectory audit. It is not a causal
agent comparison: the local baseline uses `eval.miniswe_agent:MiniSweAgent`,
while GT uses `MiniSweCentralAgent` and the GT runtime. The baseline is still
the correct directional comparator for the same task artifacts.

## Cohorts

The GT cohort is the frozen 20-task contract in
`eval/release/ox_alpha_smoke20.json`. All 20 task jobs started and completed.
The local baseline is Harbor run `32589885199` in
`D:\tmp\gt_smoke_metrics_20260822\tb2`; it contains 17 overlapping task IDs
and does not contain `cobol-modernization`, `largest-eigenval`, or `regex-chess`.

## Results

GT solved 9/20 tasks. Two tasks had provider/timeout failures and therefore
cannot be treated as ordinary GT trials:

* `portfolio-optimization`: OpenRouter/Stealth returned HTTP 502; no response
  was available to replay.
* `llm-inference-batching-scheduler`: Harbor's 1,800-second agent timeout
  cancelled the in-flight provider call.

The runtime correctly marked both receipts `task_execution_certificate=BLOCKED`
with `runtime-task:trajectory_replay_not_ready`; it did not claim those
trajectories were complete.

| Task | GT | Baseline | Comparison | GT receipt |
| --- | ---: | ---: | --- | --- |
| count-dataset-tokens | 1 | 1 | tie | PASS |
| extract-elf | 0 | 0 | tie | PASS |
| feal-linear-cryptanalysis | 1 | 1 | tie | PASS |
| fix-code-vulnerability | 1 | 1 | tie | PASS |
| headless-terminal | 1 | 1 | tie | PASS |
| llm-inference-batching-scheduler | 0 | 0 | unscored provider timeout | BLOCKED |
| mcmc-sampling-stan | 1 | 1 | tie | PASS |
| portfolio-optimization | no reward | 1 | unscored HTTP 502 | BLOCKED |
| prove-plus-comm | 1 | 1 | tie | PASS |
| qemu-alpine-ssh | 0 | 1 | baseline win | PASS |
| sanitize-git-repo | 1 | 1 | tie | PASS |
| schemelike-metacircular-eval | 0 | 1 | baseline win | PASS |
| torch-pipeline-parallelism | 0 | 0 | tie | PASS |
| torch-tensor-parallelism | 0 | 1 | baseline win | PASS |
| video-processing | 1 | 0 | GT win | PASS |
| winning-avg-corewars | 0 | 0 | tie | PASS |
| write-compressor | 0 | 0 | tie | PASS |
| cobol-modernization | 1 | n/a | GT-only | PASS |
| largest-eigenval | 0 | n/a | GT-only | PASS |
| regex-chess | 0 | n/a | GT-only | PASS |

On the 17-task overlap, 16 trials had a numeric GT reward: GT won 1, lost 3,
and tied 12 (GT 8/16 scored; baseline 11/16 on the scored overlap). This is
directional only and does not establish that GT caused the difference.

## Production-path and trajectory checks

* 20/20 central receipts are present and map to the frozen task IDs.
* 20/20 receipts report `repository_intelligence.status=passed`.
* 20/20 receipts report `treatment_validity=VALID`.
* 20/20 receipts report initialized persistent execution state. The QEMU
  shell-only task explicitly records the `empty_catalog` limitation rather
  than pretending symbol facts exist.
* 18/20 receipts have `trajectory_replay_ready=true`.
* The two non-ready receipts correspond exactly to the provider 502 and the
  outer timeout above; they are explicitly blocked, not silently accepted.
* Every replay-ready receipt has request envelopes, provider messages, and
  responses captured and cryptographically joined to its central receipt.
* The model-visible replay envelopes were inspected. GT evidence appears in
  actual provider messages (for example `Current task evidence:` and
  `Current certified repository context:`), not only in controller state.
  Delivery receipts include source/workspace revisions, claim anchors, hashes,
  first eligible call, and whether the next model request used the delivery.
* No GT receipt in this run reports a missing graph or stale repository
  revision. The product correctly distinguishes a source-less/empty catalog
  limitation from an indexed graph.

## Interpretation

The full-parallel workflow is mechanically correct and materially faster in
wall time (about 53 minutes versus about 63 minutes for the earlier 10-way
run), but peak concurrency is not free: it coincided with one provider 502 and
one long-tail timeout, and the observed solve count was 9/20 versus 12/20 in
the earlier valid 10-way GT run. That is a benchmark/provider-capacity issue,
not evidence that GT should hide failures or downgrade certificate rules.

The current evidence proves that the GT path builds and records repository
intelligence, delivers deterministic context when selected, and preserves
truthful receipts through successful and failed calls. It does not prove a
solve-rate improvement over the Mini-SWE baseline, nor does it justify a
`CERTIFIED` competitive claim. A causal comparison requires identical agent
scaffolds and repeated trials after provider capacity is controlled.
