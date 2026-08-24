# GT 20-task Mini-SWE trajectory audit

Audit date: 2026-08-24

Canonical run: `32717496816`

Run subject: `c0b296f9f95f1e7b162b36d81063dafb8860e693`

Current repaired implementation: `c0b296f9f95f1e7b162b36d81063dafb8860e693`

Agent: `eval.harbor_gt_harness_adapter:GtHarnessMiniSwe228Agent`

Model request: `stealth/ox-alpha` through OpenRouter

Parallelism/attempts: `20 / 1`

The latest official run [32717496816](https://github.com/harneet2512/gt-harness/actions/runs/32717496816)
passed the final attestation. It produced 20 product receipts and 20 full
trajectories. Raw Harbor reward was 13/20.
The frozen local baseline for the same task set was 17/20, but used a different
model route (`deepseek-v4-flash` versus GT's `stealth/ox-alpha`).

The machine-readable receipt is
`audit/receipts/smoke-32717496816-summary.json`. Earlier tables are retained
below for audit history.

The bounded retry repair removed the earlier provider read-timeout failures. The
dense-update fallback prevented the prior treatment-unavailable failure. The
final run still recorded one Harbor `Timeout` (`regex-chess`) and one
`ProductProcessExitError` (`write-compressor`). The frozen 17/20 baseline uses a
different model route and is not a causal GT comparison.

### Historical task table: superseded run `32700056236`

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

The final attested run recorded 20 terminal receipts and 20 full trajectories. It
solved 13/20 with 627 provider calls and 22,333,393 input/output tokens. The two
explicit errors were `regex-chess: Timeout` and
`write-compressor: ProductProcessExitError` with
`SUPERVISOR:product_process_exit_124`. Active-treatment packets contained
repository-derived text and query-ready dense indexes. Six source-less tasks
explicitly abstained with `NOT_APPLICABLE:graph_not_ready:FAILED`; no stale or
partial graph was represented as healthy and no dummy or fabricated context was
observed.

Against the frozen baseline, GT solved one baseline miss (`count-dataset-tokens`)
but lost five baseline solves: `extract-elf`, `regex-chess`, `video-processing`,
`winning-avg-corewars`, and `write-compressor`. This is a cross-model directional
comparison, not a causal result.

The final baseline-only task set is `extract-elf`, `regex-chess`,
`video-processing`, `winning-avg-corewars`, and `write-compressor`; the only
GT-only solve is `count-dataset-tokens`. This list is directional because the
baseline model differs.

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

The retry repair is live-verified by runs `32700056236` and `32717496816`. The
dense-update fallback is live-verified by runs `32711664602` and `32717496816`;
the final attestation passed. The final run still shows that weak
inspection-only initial packets can be delivered, so delivery quality remains a
known prerelease limitation.

## Baseline interpretation

The local frozen repair20 baseline is the same task set and Mini-SWE version but a
different model route. Its 17/20 result is directional context only. It cannot be
used to assign GT causality to solve flips, token reduction, or failures.
