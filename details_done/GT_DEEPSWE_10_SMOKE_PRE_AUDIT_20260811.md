# GT DeepSWE ten-task smoke pre-audit (2026-08-11)

## Scope

This audit covers the current `gt-harness` HEAD (`e52ff9d814be56de53ae92764c4875fda93fe179`) and the requested ten-task DeepSWE smoke. It does not treat the older `D:\Groundtruth` checkout as the active product.

## Current implementation proof

- Active agent: `eval.gt_central_agent:MiniSweCentralAgent`.
- Current runtime is host-owned and in-process. It resolves the task image working directory, mirrors selected source into the host, observes model actions before and after execution, refreshes repository intelligence before the next provider call, and compiles one bounded provider view.
- The current integration repair is on the pushed HEAD. Tracked files are clean after restoring the locally rebuilt diagnostic binary.
- Provider-free GitHub certification at the exact HEAD is green: workflow `31545142600` passed the source-built index, all 17 producer/consumer/timing/payload/context gates, readiness, static checks, and exact pre-smoke.
- Local exact pre-smoke is also green when `GT_INDEX_BINARY` points to a source-built temporary binary. The default checked-in Windows binary is older and lacks the newly registered Objective-C adapter; this is an environment artifact, not the CI path. The paid workflow must build the index from `vendor/gt-index-src` and set `GT_INDEX_BINARY` to that build, as the certified workflow does.
- Local census markers: `ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`, `ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`, `ALL_17_CONSUMER_PATHS_PROVEN`, `ALL_17_TRIGGERS_PROVEN`, `ALL_17_PAYLOADS_CONCRETE`, `ALL_17_CONSUMERS_APPLIED`, `ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST`, and `ALL_EFFECTS_CONTEXT_ACCOUNTED`.

## DeepSWE asset audit

- Local protocol checkout: `D:\tmp\deep-swe-upstream`, with the v1.0.0 tag and
  the immutable current-main snapshot `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9`.
  Both contain 113 task IDs, but only current main carries the v1.1 task
  contract described below.
- Public v1.1 metadata: 113 tasks, 91 repositories, and
  Go/Python/TypeScript/JavaScript/Rust coverage. The checked-in workflow pins
  current main and verifies the 113-manifest count.
- Existing downloaded trajectories and summaries are historical evidence only. They use the retired `artifact_deepswe.gt_agent:GTMiniSweAgent` path and are not evidence for the current central runtime.
- The active workflow is `.github/workflows/deepswe_miniswe_central.yml`. It
  uses the current host-owned agent and the pinned `datacurve-pier==0.3.1`
  runner. The prior direct-Harbor invocation was rejected because it skipped
  the v1.1 separate-verifier and collect-patch lifecycle.
- DeepSWE task TOMLs remain Harbor-compatible, but v1.1 requires Pier's
  separate verifier, commit-to-patch collection, and pristine grading. The
  workflow uses `--include-task-name` to run each exact matrix task.

## Smoke task set

The ten-task set will be frozen before dispatch from the v1.1-compatible main
snapshot, balanced across the five declared language families (two each). The
exact IDs, manifest hash, workflow SHA, model, arm, runner version, and task
order will be recorded in the smoke contract and uploaded with the run
artifacts.

## Gate decision

**GT runtime: APPROVED.** Exact-head provider-free implementation and delivery gates pass.

**DeepSWE paid smoke: NOT YET DISPATCHABLE.** The workflow protocol repair is
implemented and its focused contract test is green. The source-built
provider-free gate must be rerun on the final workflow commit; only then may a
paid smoke be dispatched. It will use Pier's v1.1 lifecycle while retaining the
current central agent, fresh per-task graph/index construction, pinned
Snowflake ONNX backend, and existing receipt/delivery audit.

## Required smoke measurements

Per task and cumulatively: verifier reward, uncensored resolve status, outer/inner timeout classification, provider request hash coverage, first-eligible timing, late/predictive counts, delivery-audit failures, graph applicability/readiness, dense backend identity/availability, GT effect dispositions, provider-visible chars, total GT context chars, model calls, assistant steps, effective actions, actual environment executions, input/output/total tokens, wall time, and cost. Historical DeepSWE-off results will be labeled descriptive unless the exact model/harness/config parity is proven.
