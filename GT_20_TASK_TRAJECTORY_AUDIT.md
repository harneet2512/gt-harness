# GT 20-task Mini-SWE trajectory audit

Audit date: 2026-08-24

Canonical run: `32700056236`

Run subject: `1fd7efae7eca064cbec56ba319f2169eb81a9ceb`

Current repaired implementation: `1fd7efae7eca064cbec56ba319f2169eb81a9ceb`

Agent: `eval.harbor_gt_harness_adapter:GtHarnessMiniSwe228Agent`

Model request: `stealth/ox-alpha` through OpenRouter

Parallelism/attempts: `20 / 1`

The latest authoritative rerun passed the final attestation. It produced 20
terminal GT receipts and 20 full trajectories. Raw Harbor reward was 9/20.
The frozen local baseline for the same task set was 17/20, but used a different
model route (`deepseek-v4-flash` versus GT's `stealth/ox-alpha`).

The prior tables are retained for audit history; the latest table is in
`FINAL_BENCHMARK_REPORT.md` and the receipt
`audit/receipts/smoke-32700056236-summary.json`.

The retry repair changed Mini-SWE transport retries from one to three. It removed
the two prior OpenRouter read-timeout failures, but the solve rate remained 9/20.
The latest two product-process errors were COBOL and Corewars. The frozen 17/20
baseline remains a different model route and is not a causal GT comparison.

| Task | Reward | GT status | Calls | Delivery | Graph |
| --- | ---: | --- | ---: | ---: | --- |
| cobol-modernization | 0 | COMPLETED | 24 | 1 | READY_WITH_DECLARED_LIMITATIONS |
| count-dataset-tokens | 1 | COMPLETED | 8 | 0 | NOT_APPLICABLE |
| extract-elf | 1 | COMPLETED | 16 | 1 | READY_WITH_DECLARED_LIMITATIONS |
| feal-linear-cryptanalysis | 1 | COMPLETED | 28 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| fix-code-vulnerability | 1 | COMPLETED | 37 | 4 | READY |
| headless-terminal | 1 | COMPLETED | 23 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| largest-eigenval | 0 | COMPLETED | 17 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| llm-inference-batching-scheduler | 0 | COMPLETED | 34 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| mcmc-sampling-stan | 1 | COMPLETED | 30 | 0 | NOT_APPLICABLE |
| portfolio-optimization | 1 | COMPLETED | 23 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| prove-plus-comm | 1 | COMPLETED | 9 | 1 | READY_WITH_DECLARED_LIMITATIONS |
| qemu-alpine-ssh | 0 | COMPLETED | 49 | 0 | NOT_APPLICABLE |
| regex-chess | 0 | COMPLETED | 85 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| sanitize-git-repo | 1 | COMPLETED | 26 | 4 | READY_WITH_DECLARED_LIMITATIONS |
| schemelike-metacircular-eval | 1 | RUNNING/invalid | 57 trajectory / 58 checkpoint | 4 | READY_WITH_DECLARED_LIMITATIONS |
| torch-pipeline-parallelism | 1 | COMPLETED | 25 | 0 | NOT_APPLICABLE |
| torch-tensor-parallelism | 1 | COMPLETED | 31 | 0 | NOT_APPLICABLE |
| video-processing | 0 | COMPLETED | 100 | 0 | NOT_APPLICABLE |
| winning-avg-corewars | 0 | RUNNING/invalid | 48 | 1 | READY_WITH_DECLARED_LIMITATIONS |
| write-compressor | 0 | COMPLETED | 21 | 1 | READY_WITH_DECLARED_LIMITATIONS |

`NOT_APPLICABLE` is explicit abstention, not a fake healthy graph. Those six tasks
received no GT context. Every active treatment had a query-ready current-revision
graph and dense index before its first provider delivery.

## Latest authoritative-run findings

The latest attested run recorded 20 terminal receipts and 20 full trajectories.
It solved 9/20 with 591 provider calls and 22,153,615 input/output tokens. Two
tasks ended with explicit product-process errors (`cobol-modernization` and
`winning-avg-corewars`); no provider read-timeout error remained. Active-treatment
packets contained repository-derived text and query-ready dense indexes; the
NOT_APPLICABLE tasks explicitly abstained because the graph had no supported
source after repository changes. No dummy or fabricated context was observed.

The latest baseline-only task set is COBOL, FEAL, Headless Terminal, QEMU,
Largest Eigenval, Regex Chess, Scheme, Video Processing, Winning Average
CoreWars, and Write Compressor; this list is directional because the baseline
model differs.

## Full-text findings

- No dummy or fabricated repository text was found.
- Paths and excerpts were source-real; structural edges and semantic facts carried
  graph claim identities and repository revisions.
- Exact-path handling was too strong: seven path-derived claims across five tasks
  inherited an unrelated or unjustified representative symbol/range. This was a
  GroundTruth epistemic bug even when the underlying file was relevant.
- Inspection-only candidates were labeled as inspection candidates. Thirty-one
  later inspection-only updates were correctly suppressed rather than repeated.
- Delivered context was consumed in the actual Mini-SWE observation stream; it was
  not present only in controller receipts.
- GT delivered useful higher-order information on tasks such as FEAL (bounded call
  paths/impact), portfolio optimization (imports/value flow), regex chess (value
  flow), and the LLM scheduler (imports/value flow).
- Some active tasks received only relevant file identification or broad dense
  candidates. That context was real but weak; the trajectories do not establish
  that it caused the edit or solve.

## Failure attribution

The second smoke did not reproduce the first run's 11 stale-after-edit errors or
QEMU startup failure. Remaining failures were product lifecycle boundaries:

1. the adapter did not terminalize a durable receipt after exit 137;
2. a provider/tool operation could cross the GT inner deadline and reach Harbor's
   outer cancellation; and
3. final attestation counted assistant messages rather than Mini-SWE API attempts.

The retry repair is live-verified by run `32700056236`. The pending inspection-only
abstention patch has focused provider-free regression coverage but has not yet
been paid-replayed.

## Baseline interpretation

The local frozen repair20 baseline is the same task set and Mini-SWE version but a
different model route. Its 17/20 result is directional context only. It cannot be
used to assign GT causality to solve flips, token reduction, or failures.
