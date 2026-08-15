# GroundTruth final contract reconciliation

Date: 2026-08-12

## Decision

The accepted GroundTruth persistent-state mechanism is the graph-first design in
`GT_PERSISTENT_EXECUTION_STATE_RESEARCH.md`, its living implementation plan, and
the top-level contracts in `AGENTS.md` and `CLAUDE.md`. Later diagnostic notes
may repair that design, but they do not silently replace it. In particular, the
later phrase `delta-only ... or NONE` conflicts with the accepted requirement
that every graph-applicable executor request receive a bounded current state
slice. It is superseded by the original tiered delivery contract:

* initial or critical frame: at most 512 packing tokens;
* changed-state delta: at most 256 packing tokens;
* unchanged-state stable core: at most 96 packing tokens;
* stale, incomplete, invalid, or unavailable state: `NONE` and fail-open normal
  Mini-SWE behavior.

This is not repeated planning. The host evaluates and updates one task-scoped
typed state at provider, preflight, postflight, and graph-rebase boundaries.
Only one catalog-bounded model bootstrap is permitted. Every later update and
frame is deterministic.

## Authority order

1. Current user requirements and the top active behavioral contract in
   `AGENTS.md`/`CLAUDE.md`.
2. `GT_PERSISTENT_EXECUTION_STATE_RESEARCH.md` and
   `GT_PERSISTENT_EXECUTION_STATE_IMPLEMENTATION_PLAN.md`.
3. Executable runtime and release-gate behavior, once it conforms to 1 and 2.
4. Current final-status and repair documents where they do not conflict with
   1-3.
5. Historical benchmark/audit documents, which remain evidence only for their
   named commit, workflow, architecture, and protocol.

## Current mechanism contract

1. Build and certify the exact-checkout repository graph before creating state.
2. Reuse the accepted five-channel hybrid retrieval result to build one bounded
   immutable catalog.
3. Make exactly one no-retry, forced-Bash, temperature-zero bootstrap provider
   call. Its command is JSON transport and is never executed or appended to
   executor history.
4. Accept only IDs and ID roles that the production
   `parse_bootstrap_selection()` accepts.
5. Compile one bounded current state slice before every graph-applicable
   executor provider call. A stable core is required when the semantic state is
   unchanged; `NONE` is reserved for an explicit fail-open condition.
6. Project every typed proposal through the current state before host
   execution, commit actual postflight results, and rebase after a complete
   graph refresh.
7. A semantic no-op does not increment state version or material-transition
   metrics, including a same-revision graph refresh.
8. A source excerpt or new semantic evidence claim is one-shot, but the small
   current-state core is intentionally repeatable because it is not retained as
   durable duplicate history.
9. A claim becomes exposed only after its contribution is selected, inserted in
   the provider-prepared request, and dispatch begins. Compilation alone cannot
   consume delivery eligibility.
10. Every bootstrap and executor provider invocation is marked before transport
    begins and is included in calls, tokens, cost, latency, provider identity,
    and retry/censoring accounting.
11. Timeout behavior cannot leave a live provider thread whose eventual result
    is absent from the task receipt. Provider transport timeout is authoritative;
    the host must not abandon an uncancellable thread and call that an accounted
    fallback.
12. OFF/AUDIT/shadow-isolation behavior stays available and must have zero
    persistent-state bootstrap or delivery.

## Benchmark contract

### Integrated GroundTruth product experiment

`OFF` versus `certified_full` is a legitimate whole-system experiment when the
arms have exact task, checkout, model route and observed response identity,
provider, prompt, tool schema, runner, timeout, budget, retry policy, and
verifier parity. It answers whether the integrated GroundTruth system helps. It
does not isolate which GT subsystem caused the result.

### Mechanism attribution

A claim specifically about repository evidence or persistent execution state
requires a separately labeled context/state-only arm or a paired decision-point
ablation. A `certified_full` result cannot be relabeled as proof of the
persistent-state mechanism alone.

### DeepSWE

The pinned official 113-task snapshot plus `datacurve-pier==0.3.1` provides the
correct v1.1 collect-and-fresh-verifier lifecycle. The current custom Mini-SWE
loop, one rollout, 300-call cap, and 5,400-second budget make the workflow a
matched GroundTruth system experiment, not a DeepSWE leaderboard-equivalent
run. Any existing GT-off artifact is usable only if the release gate proves the
same current manifest, prompt/tool hashes, provider response identity policy,
and row-level resource fields. User-supplied historical baseline status does
not waive parity checks.

### Terminal-Bench

The repository currently mixes Terminal-Bench 2.0 and 2.1 targets. Existing
2.0 artifacts must remain labeled 2.0. A 2.1 leaderboard-grade claim requires
the canonical 89-task dataset, at least five trials per task, default limits,
and the official artifact/audit protocol. The present custom capped workflow is
diagnostic only.

## Reproduced P0 defects and local repair status

| ID | Reproduced defect | Local repair |
|---|---|---|
| P0-BOOTSTRAP-CANARY | The workflow canary duplicated only part of the production parser. | It now builds the production catalog/messages and calls `parse_bootstrap_selection()`. |
| P0-PROVIDER-MARKER | Bootstrap was unmarked, and later marker-write failures remained fail-open. | Both call kinds are marked immediately before transport; marker failure prevents transport and is release-gated. |
| P0-TIMEOUT-ACCOUNTING | Executor used `wait_for(to_thread(model.query))`; the provider thread could finish after the receipt reported timeout. | Timeout is passed to the no-retry provider transport and the host awaits thread completion. |
| P0-BASELINE-ARM | An ACTIVE artifact could be supplied as both baseline and treatment and pass. | Baseline must prove GT-off/control and every baseline row must record `integration_mode=off`; treatment must prove ACTIVE `certified_full`. |
| P0-PAIR-IDENTITY | Missing fingerprints and incomplete provider identity could compare equal. | Model/provider/fingerprint and prompt/tool identities are nonempty and fail-closed at manifest and row level. |
| P0-RESOURCE-ACCOUNTING | Pre-matrix setup overhead was merely reported and could be all-zero. | Nonzero canary usage/cost/latency is required and mapped into all-in tokens/calls/cost/wall-time deltas. Effective actions and assistant steps remain row-gated. |

## Reproduced P1 mechanism defects and local repair status

| ID | Reproduced defect | Local repair |
|---|---|---|
| P1-STABLE-CORE | Unchanged valid state returned `NONE`. | It returns a <=96-token CORE frame on every unchanged applicable call. |
| P1-CORE-STARVATION | A 1,195-token priority-5 diagnostic could budget-drop priority-10 CORE. | Persistent state is packed first at priority 0; biting budget test proves CORE survives. |
| P1-EARLY-EXPOSURE | Compilation consumed claim exposure before request-wide selection/dispatch. | `mark_context_dispatched()` is the sole exposure boundary. |
| P1-REBASE-NOOP | Transition text, optional-item filtering, and obligation tuple order created artificial graph transitions. | Semantic comparison canonicalizes obligation identity, preserves tuple order on no-op, and retains still-current optional bootstrap selections. |

## Not defects in this repair

* The persistent artifact is not a free-form plan and does not need repeated
  planner/advisor calls.
* The bootstrap's model choice is intentionally the bounded non-deterministic
  boundary; catalog creation, validation, state transitions, and delivery remain
  deterministic.
* `certified_full` is not inherently invalid. It is invalid only if reported as
  isolated persistent-state causality or paired against a non-equivalent control.
* A historical baseline need not be rerun merely because it is historical. It
  must be rejected if it cannot prove the current parity contract.
* Provider-free proof establishes integration integrity, not solve uplift,
  efficiency, or leaderboard equivalence.

## Test-first repair order

1. RED: unchanged valid state produces a non-empty `CORE` frame within 96 tokens.
2. RED: same-revision graph rebase is a semantic no-op.
3. RED: a contribution rejected by the request-wide compiler remains eligible
   on the next call; dispatch marks it exposed exactly once.
4. RED: workflow canary accepts and rejects exactly what
   `parse_bootstrap_selection()` accepts.
5. RED: provider marker exists during the bootstrap transport call and records
   invocation kind/count.
6. RED: bootstrap timeout cannot create an abandoned unaccounted provider call.
7. RED: DeepSWE release rejects missing/mismatched observed identity,
   prompt/tool hashes, effective resources, and unreported setup overhead.
8. Apply the smallest changes at the existing state engine, central host,
   canary adapter, workflow manifest, and release gate.
9. Repeat adversarial code review, widened tests, static checks, receipt replay,
   and the source-built Linux provider-free gate.

All repairs above pass their focused RED-to-GREEN tests and the widened local
affected suite. They remain **implemented but not release-certified** until the
current source-built Linux provider-free workflow passes at the exact pushed
commit. No paid smoke or full benchmark is authorized by this reconciliation.
