# Groundtruth + Mini-SWE-Agent Handoff

**Scope:** `gt-harness` Groundtruth integration with Mini-SWE-Agent only.  Nano-harness,
Terminal-Bench production wiring, and unrelated model experiments are out of scope for
the next phase.

**Date:** 2026-07-31  
**Branch:** `gt-integration`  
**Latest implementation commit:** `be3734b` (`feat(miniswe): add executable GT runner`)

## Executive state

The repository now contains a real Mini-SWE-Agent 2.2.8 entrypoint and a deterministic
GT sidecar.  GT compiles a task into typed obligations and predicates, owns the lifecycle
gate, records external state and receipts, and delivers a bounded, attributable control
suffix at the Mini-SWE provider boundary.  Mini-SWE remains responsible for model
reasoning, bash actions, and workspace mutation.

The implementation is wired and locally tested.  It is **not yet proven to improve**
Mini-SWE's solved rate, token cost, iteration count, or wall-clock time.  The current
live smoke (`30642616424`) uses `deepseek-v4-flash` and is still the active experiment;
do not mix its result with a future Pro run.

## What was fixed

### Lifecycle and control

- Added explicit `ORIENT -> IMPLEMENT -> VERIFY -> SUBMIT -> FINISHED/STUCK` state
  ownership in `GroundtruthController`.
- Added action legality checks, action budgets, no-progress detection, and bounded
  recovery.  Repeating the same failed action is no longer an admissible recovery;
  the controller must choose a materially different diagnostic/action or become `STUCK`.
- Added a final submit gate.  Submission is refused when required predicates are
  missing, stale, semantically unproven, or lack a current verification plan.
- Added one tool-free terminal response after `FINISHED` or `STUCK`; no post-acceptance
  tool calls are allowed.

### Evidence and verification

- Receipts now require explicit semantic evidence.  A successful shell exit or model
  claim alone cannot become `GREEN`.
- Added freshness-bound `VerificationPlan` records and predicate IDs.  Workspace epoch
  changes invalidate affected evidence.
- Added typed task contracts and predicate compilation for PATCH, BUILD_INSTALL,
  ARTIFACT, SERVICE, DATA_TRANSFORM, and MIXED tasks.
- Added corrective obligation-delta rendering so a failed submit exposes the exact
  unmet obligations rather than another generic reminder.
- Added `MiniSweAdapter.evaluate_observation()` to run contract predicates against real
  command output and record semantic receipts.

### Context delivery and attribution

- GT state is external to the graded workspace and persisted as a durable event/state
  record.
- Provider suffixes are bounded and schema-stable.  Unchanged state vectors do not
  generate duplicate control text.
- Every delivered capsule is tied to delivery bytes and the provider payload hash;
  transcript substring searches are not treated as attribution proof.
- The audit path distinguishes executed, delivered, and action-consistent evidence.

### Graph/index and audit robustness

- Graph refresh failures retain prior graph objects and emit an explicit
  `graph.context_refresh_recovered` event with `proof_available: false`.
- The live gate counts only unrecovered graph failures as a gate failure.
- Known host GT-index diagnostic lines are parsed as structured diagnostics instead of
  being misclassified as an unparsed transcript; the underlying refresh fault remains
  visible.
- Feature-opportunity auditing is terminal-state aware and joins DELIVERED outcomes to
  timing/action evidence.

## Current architecture

```text
task + workspace
        |
        v
GT contract compiler -> typed obligations/predicates -> external GT state/event log
        |                                                        |
        v                                                        v
  lifecycle controller -> bounded provider suffix -> Mini-SWE model request
        ^                                                        |
        |                                                        v
  semantic receipt <- observation/workspace diff <- Mini-SWE bash action
        |
        v
  submit gate -> FINISHED or bounded recovery -> STUCK
```

The real executable is [`scripts/miniswe_gt_run.py`](scripts/miniswe_gt_run.py).
It constructs Mini-SWE's `DefaultAgent`, `LitellmModel`, and `LocalEnvironment`, then
installs GT hooks at the provider/action seams.  The adapter is deliberately a sidecar:
it does not rewrite commands or silently mutate the task workspace.

## Model routing

The current proof model is `deepseek-v4-flash` through
`https://api.deepseek.com/v1`.  DeepSeek's current documentation lists both
`deepseek-v4-flash` and `deepseek-v4-pro`; Flash is currently served as
`DeepSeek-V4-Flash-0731`, with 1M context and tool-call support.  Pro is a separate
candidate and must pass its own preflight and paired experiment.  The existing Flash
baseline must remain frozen.

## Evidence already available

- Commits `0d963fc`, `4edeb45`, and `be3734b` contain the Mini-SWE integration,
  semantic/recovery fixes, and executable entrypoint respectively.
- Focused GT/Mini-SWE/audit tests pass; Ruff and `git diff --check` pass for the changed
  implementation.
- Real Mini-SWE construction and runner `--help` checks pass.
- Local GT-off Mini-SWE/SWE comparator: `83/300` (300 task IDs; 12 unevaluated).
- The separate ten-task Terminal-Bench comparator is recorded only as a diagnostic
  control and must not be combined with the Mini-SWE score.
- Live run `30615578051` exposed the remaining integration faults; replay after parser
  hardening removed a false COBOL `UNPARSED` diagnosis but correctly retained the graph,
  timeout, semantic-verification, and attribution issues.

These facts establish wiring and diagnosability, not causal performance advantage.

## Remaining gaps

1. Run a genuine Mini-SWE GT-off baseline with identical model, tasks, temperature,
   timeout, Harbor/runtime, and grader controls.
2. Run the matching GT-on arm and join every GT delivery to the next provider request,
   action, receipt, and final grader result.
3. Make task predicates task-specific enough that BUILD_INSTALL, SERVICE, DATA, and
   ARTIFACT tasks do not inherit code-edit assumptions.
4. Make verification receipts carry the exact command, output hash, workspace epoch,
   and predicate result; reject stale or prose-only evidence.
5. Complete the 17-feature opportunity table for Mini-SWE lifecycle boundaries
   (`orient`, `research`, `pre_edit`, `post_edit`, `test`, `recovery`, `verify`,
   `submit`) and report `NOT_APPLICABLE`, `EXECUTED`, `DELIVERED`, or
   `ACTION_CONSISTENT` per task.
6. Measure solved rate, cost, input/output tokens, iterations, tool errors, recovery
   count, verification latency, and timeout rate.  Do not claim improvement from GT
   telemetry alone.
7. Add a separate `deepseek-v4-pro` preflight, then a separately labeled Pro smoke;
   never overwrite the Flash control or combine model arms.

## Next execution order

1. Let Flash run `30642616424` finish; archive its workflow metadata and artifacts.
2. Audit the trajectory and external GT log with the feature-opportunity auditor.
3. Freeze the matched Mini-SWE GT-off baseline.
4. Re-run the same task manifest with GT-on and compute paired deltas.
5. Investigate every `UNKNOWN`, stale receipt, graph fallback, timeout, and attribution
   miss before changing prompts or thresholds.
6. Only after Flash's paired result is reproducible, preflight and test `deepseek-v4-pro`
   as a new model arm.

## Operational constraints

- Work only on Mini-SWE files and documentation in this repository.
- Do not alter the frozen baseline while evaluating GT-on.
- Do not claim “all 17 features work” unless each eligible terminal opportunity has a
  recorded outcome and the timing/action joins pass.
- Do not treat a GT message in a trajectory as proof that the provider received it.
- Do not run GCP authentication or credential-management commands.

