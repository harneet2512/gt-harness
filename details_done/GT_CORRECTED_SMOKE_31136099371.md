# Corrected GT preflight smoke — workflow 31136099371

Date: 2026-08-06 (workflow timestamps are UTC 2026-08-07)  
Commit: `8ab1896909a4ee88853600ffebafcff723c5a411`  
Workflow: `TB2 miniswe central matrix (GT-on)` (`326553235`)  
Model: DeepSeek V4 Flash, temperature 1  
Treatment: integrated GT-on, `integration_mode=active`, `preflight_mode=shadow`

## Outcome

All ten tasks completed and received verifier reward 1. The frozen GT-off
baseline solved 9/10, with `gpt2-codegolf` unsolved and `write-compressor`
solved. This corrected treatment solved 10/10, including both of those tasks.
That is a positive single-trial outcome witness, not a causal claim; repeated
matched trials are still required before opening the 89-task run.

Every task was uncensored (`uncensored_resolved=true`). Two tasks ended by
internal control boundaries (`headless-terminal: DeadlineReserveReached`,
`schemelike-metacircular-eval: StepLimitExceeded`) but both had reward 1 and
are not Harbor outer exceptions.

## Receipt proof of the intended interface

All ten receipts report:

- `integration_mode=active`;
- `preflight_mode=shadow`;
- exact Harbor-owned execution budget (600–3,600 seconds by task);
- no per-request `model_timeout_sec` or `model_loop_timeout_sec` override;
- preflight called once per normalized action (449 calls total);
- every applied preflight disposition was `PASS` (shadow never changes or
  blocks the selected command);
- 708 known shell segments, 1,366 unknown segments, and 950 typed targets;
- zero commands returned to the model, rewrites, suppressions, or assistive
  interventions;
- postflight remained authoritative and observed all executed actions.

This is the correct non-invasive pre-action measurement arm. It proves the
boundary was exercised, not that shadow preflight itself caused the solve win.

## Timing and delivery

Across all tasks:

- 359 effects produced and 359 applied;
- 7 grounded provider payloads delivered;
- 7/7 first-eligible and timely;
- late payloads: 0;
- predictive payloads: 0;
- 6,588/6,588 context facts accounted;
- provider-request budget failures: 0;
- provider request hash coverage: 1.0;
- 249 deterministic context compactions, eliding 22,507,082 old-context
  characters while retaining the latest two turns and typed current state;
- 23 pre-decided suffix actions remained after evidence, which is expected in
  SHADOW mode because it preserves historical batch behavior. No action was
  cancelled or rewritten.

All 17 feature IDs were enabled. Thirteen fired naturally in this stochastic
set: `GT_CERT_DELIVERY`, `GT_CHANGE_SURFACE`, `GT_EDIT_CHECK`,
`GT_HYPOTHESIS`, `GT_LOC_RESLOT`, `GT_PATCH_DELTA`, `GT_SS_SUBMIT_RED`,
`covering_red`, `localization`, `newfile_precedent`, `obligations`,
`submit_refusal`, and `syntax_result`. The four exact-trigger IDs absent from
these trajectories were `caller_contract`, `def_partition`, `recovery`, and
`signature_delta`; provider-free census and forced-trigger tests prove those
producer/consumer paths independently. This is 13/17 natural firing, not a
failure of the 17-feature implementation.

Correction (2026-08-06): the paragraph above combined infrastructure failure
with genuine trigger absence. A later receipt audit found 38/38 repository
refreshes were `index_unavailable`. Therefore `caller_contract` and
`def_partition` were unreachable in this run because the paid workflow lacked
the repository runtime substrate; only `recovery` and `signature_delta` were
legitimate exact-event absences. It also found that only four of 100 combined
localization/reslot receipts carried concrete anchors. The workflow, graph-role
semantics, applicability receipts, and provider-free gate have since been
repaired; this archived smoke remains outcome evidence but is not evidence that
the four missing features all correctly abstained.

## Matched baseline resource comparison

Baseline values are the frozen `miniswe_tb2_gtoff_20260731/SUMMARY.md` prompt
and completion token columns and calls. Treatment values are the corrected
deep-metrics totals. Deltas are treatment minus baseline.

| task | outcome GT-off → GT-on | tokens baseline → treatment | Δ tokens | calls baseline → treatment | Δ calls |
| --- | --- | ---: | ---: | ---: | ---: |
| break-filter-js-from-html | solved → solved | 326,564 → 291,535 | -35,029 | 12 → 16 | +4 |
| cobol-modernization | solved → solved | 2,852,517 → 1,535,000 | -1,317,517 | 39 → 46 | +7 |
| fix-code-vulnerability | solved → solved | 888,683 → 284,580 | -604,103 | 33 → 24 | -9 |
| gpt2-codegolf | unsolved → solved | 17,404,358 → 1,146,010 | -16,258,348 | 59 → 50 | -9 |
| headless-terminal | solved → solved | 9,794,473 → 2,348,589 | -7,445,884 | 86 → 56 | -30 |
| llm-inference-batching-scheduler | solved → solved | 5,961,364 → 897,142 | -5,064,222 | 41 → 30 | -11 |
| modernize-scientific-stack | solved → solved | 76,211 → 77,097 | +886 | 8 → 10 | +2 |
| portfolio-optimization | solved → solved | 861,659 → 750,809 | -110,850 | 26 → 32 | +6 |
| schemelike-metacircular-eval | solved → solved | 16,942,703 → 5,828,852 | -11,113,851 | 100 → 100 | 0 |
| write-compressor | solved → solved | 1,901,261 → 1,352,688 | -548,573 | 16 → 27 | +11 |

Aggregate recorded tokens fell from 57,009,793 to 14,512,302 (Δ
`-42,497,491`, `-74.54%`). Calls fell from 420 to 391 (Δ `-29`, `-6.90%`).
The largest token reductions are `gpt2-codegolf`,
`schemelike-metacircular-eval`, and `headless-terminal`; the small positive
token delta is `modernize-scientific-stack` (+886). The baseline table does
not contain action/effective-action counts, so no action delta is fabricated;
the treatment recorded 449 model actions and 489 effective actions.

## Gate status

`python scripts/central_pre_smoke_gate.py` passed as `SMOKE_APPROVED` on the
exact pushed commit before dispatch. The first run, `31134135706`, is retained
as an invalid release-path witness because its receipts were `preflight_mode=off`;
it is documented separately and is not mixed into this comparison.

The 89-task run remains blocked pending repeated matched outcome-first trials.

Artifacts: `D:\gt_runs\31136099371\corrected`  
Download copy: `C:\Users\Lenovo\Downloads\GT_CORRECTED_SMOKE_31136099371.md`
