# Mini-SWE + GT gap audit — TB2 smoke 30615578051

This is a diagnosis-only report for the ten-task DeepSeek V4 Flash GT arm. No
task code or GT implementation was changed as part of this audit.

## Executive finding

The run was operationally healthy but acceptance failed. Harbor completed all
10 trials, GT-index/provider/artifact preflight passed, and the live gate saw
16 of 17 feature identities. The outcome was 3/9 graded successes versus the
GT-off comparator's 7/9. GT was active and attributable, but its semantic
verification and recovery controls were not strong enough to convert delivery
into correct task outcomes.

## Gap classification

| ID | Gap | Evidence | Owner | Severity |
|---|---|---|---|---|
| G1 | Contract extraction/shipping is incomplete | `fix-code-vulnerability` generated 42 obligations but shipped 32/42; verifier reward 0 | `gt_engine/task_contract.py`, contract renderer/receipt | Critical |
| G2 | Verification plans are not mandatory on graph-backed edits | `llm-inference-batching-scheduler` and `schemelike-metacircular-eval` had graph-backed edits without `GT_VERIFICATION_PLAN`; both failed | trigger/router + acceptance gate | Critical |
| G3 | Verification predicates are not bound to task-specific semantic checks | LLM scheduler emitted malformed code (`totals += m`, broken `lat_by_batch` logic) and ran to iteration 100; GT recorded activity but did not force a decisive semantic check | typed predicates, VERIFY handler | Critical |
| G4 | Recovery does not guarantee a discriminating next action or termination | Scheme repeated EOF/recovery through 100 iterations; GPT2 and compressor timed out after repeated recovery/tool activity | progress controller/recovery | Critical |
| G5 | Graph refresh failure has no fail-closed fallback | COBOL had graph refresh failure and the audit could not trust the claimed green result | graph adapter/context refresh | High |
| G6 | Transcript parser is not total over model output | COBOL had 6 unparsed lines; this makes verdict/replay partially untrustworthy | `scripts/gt_audit.py` parser | High |
| G7 | Pre-edit/delivery reconciliation is still lossy | Modernize had one missing `pre_edit` checkpoint and two sealed deliveries that could not be reconciled to transcript bytes | lifecycle attribution | High |
| G8 | Timeout budget is not adaptive to task class/progress | GPT2 and compressor reached 900s; compressor had only 7 iterations but still timed out, GPT2 reached 64 | Harbor timeout + GT budget controller | High |
| G9 | Provider-bound delivery proves message delivery, not correctness | 9 features were witnessed and 16 exercised, but only 3/9 graded tasks passed | measurement design | High |
| G10 | The 17-feature census is aggregate, not per-task acceptance | Aggregate exercised count was 16, while several task-specific predicates were absent or unevaluated | live gate/reporting | Medium |

## What is not yet proven to be a GT defect

- The model's implementation errors are not automatically GT bugs. Portfolio,
  modernize, and break-filter demonstrate that the same GT arm can succeed.
- `headless-terminal` is not a valid score regression because the local
  baseline has no graded reward for that task.
- Provider, wheel, index, temperature, and timeout configuration were correct
  for the paired comparison.

## Required fix acceptance tests

1. Every generated obligation is either shipped or explicitly rejected with a
   reason; a green task cannot have `shipped < total`.
2. Every graph-backed edit produces and evaluates a task-specific verification
   plan before submit; missing evaluation is a hard stop.
3. Typed predicates must invoke an observable verifier (test, artifact hash,
   service probe, or data comparison), not merely emit a receipt.
4. Repeated identical observations force a bounded alternate action and then a
   terminal STUCK result; no 100-iteration recovery loop.
5. Graph refresh failure must either recover deterministically or mark the
   dependent delivery unusable; it must not silently continue as normal.
6. Transcript parsing must preserve every provider/tool boundary or fail the
   audit explicitly before computing a verdict.
7. Pre-edit and post-edit checkpoints must bracket every mutating action, with
   exact provider receipt linkage.
8. Timeout/no-progress budgets must be task-mode aware and emit a reasoned
   terminal state before Harbor's hard timeout.
9. Reports must show, per task: feature triggered, lifecycle boundary,
   provider iteration, predicate evaluated, verifier result, and reward delta.

## Priority order

Fix G2/G3/G4 first because they directly explain incorrect outcomes and
runaway trajectories. Fix G1/G5/G6/G7 next because they invalidate trust in
the evidence. Fix G8/G10 for efficiency and reporting. G9 is the measurement
contract: it must remain a gate, not a success claim.
