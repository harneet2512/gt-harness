# GT pre-smoke readiness record

Date: 2026-08-07  
Branch: `inline-engine`  
Commit: `48d73ad1f340ebb6b202bab2947ecbd30190b455`  
Paid smoke status: **not dispatched**

## Purpose

This record freezes the state immediately before the next matched ten-task
GT-on smoke. It separates provider-free correctness from live outcome evidence.
The provider-free gates are a release prerequisite; they are not a claim that
the treatment has already improved solve rate or efficiency.

## What is being tested

The smoke must run the current paid workflow at the exact pushed commit, using
the existing frozen GT-off ten-task baseline. The baseline must not be rerun.
The treatment is expected to preserve baseline solves and avoid new outer
censors before any efficiency claim is considered.

The run must retain, per task and cumulatively:

- official verifier reward and `uncensored_resolved` separately;
- outer exception/censor type and agent wall time;
- model calls, assistant steps, model-selected actions, effective task
  actions, controller/sensor executions, completion probes, auto-submit, and
  cache hits;
- total tokens, GT context characters, state-frame characters, API calls,
  wall-clock time, and normalized cost;
- all 17 feature IDs fired, applied effects, effect-trace dispositions, and
  first-eligible delivery receipts;
- provider request hash coverage, message index, late/predictive flags,
  duplicate evidence, source revision, and payload-grounding checks;
- compaction epochs, duplicate-turn removal, unique-reasoning removal, and
  accounted/unaccounted context facts.

## Exact pre-dispatch checklist

Run these checks on the pushed commit and capture their complete output:

```text
git status --short
git rev-parse HEAD
git rev-parse origin/inline-engine
python -m scripts.central_feature_census
python scripts.central_readiness_audit.py
python scripts.central_pre_smoke_gate.py
```

The direct and module census must include all of these markers:

```text
ALL_17_PRODUCERS_PROVEN
ALL_17_CONSUMERS_PROVEN
ALL_EFFECTS_TIMING_VALID
ALL_PAYLOADS_GROUNDED
ALL_17_CONSUMER_PATHS_PROVEN
ALL_17_TRIGGERS_PROVEN
ALL_17_PAYLOADS_CONCRETE
ALL_17_CONSUMERS_APPLIED
ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST
NO_ACTIONS_BLOCKED
ALL_EFFECTS_CONTEXT_ACCOUNTED
```

The readiness audit must print `READY`; the pre-smoke gate must print
`SMOKE_APPROVED`. A dirty tree, commit mismatch, failed marker, or missing
replay evidence is a **no-go** and must not be bypassed.

## Already completed on this commit

- Provider-free GitHub workflow `31215205770` passed.
- Full repository suite: 1,026 collected, zero failures, three expected
  platform/coverage skips.
- Scoped Ruff, Python compilation, workflow YAML parsing, and diff checks
  passed.
- Archived efficiency replay passed with marker
  `ARCHIVED_EFFICIENCY_REPLAY_OK`.
- Archived regression-preservation replay passed with marker
  `ARCHIVED_REGRESSION_REPLAY_OK`.
- Archived raw provider headroom remained above the conservative reserve; no
  projected compaction epoch or distinct reasoning removal was found.
- The paid workflow remains `ACTIVE + SHADOW` with bounded deterministic
  compaction, executable completion checks, and progress control.
- `integration_mode=off` remains the rollback switch. No feature-driven
  rewrite or suppression is enabled in the paid treatment.

## Live-run acceptance gate

The smoke is a confirmation run, not exploratory debugging. After completion,
accept only if all conditions hold:

1. No frozen-baseline solve is lost.
2. No new Harbor outer censor occurs; rewarded-but-censored tasks do not count
   as preserved solves.
3. Aggregate total tokens, API calls, effective task actions, and normalized
   cost are negative relative to GT-off.
4. Aggregate model actions and comparable wall time are nonpositive.
5. No solved task exceeds the bounded outlier policy in two or more resource
   dimensions.
6. Every visible payload is grounded, non-empty, deduplicated, source-bound,
   and present in the first eligible provider request.
7. No late or predictive delivery, unaccounted context fact, unique reasoning
   removal, or duplicate evidence is present.
8. Controller work is reported separately from model work; private effects are
   not called model-visible deliveries, and model-visible count is not used as
   a proxy for total GT work.

Any failed condition keeps the 89-task run blocked and produces a diagnosis
before another paid run. A single successful smoke is not a causal proof;
matched repeated trials are required for that claim.

## Authorization boundary

This document and the read-only gates do **not** dispatch a paid run. The next
action after a clean audit is to present the captured `SMOKE_APPROVED` output
and wait for explicit authorization to launch the matched ten-task workflow.
The 89-task workflow remains blocked.
