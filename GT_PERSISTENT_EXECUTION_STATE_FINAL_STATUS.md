# GroundTruth Persistent Execution State — Final Implementation Status

**Date:** 2026-08-12
**Last prior certified SHA:** `e0c63ae15be6eeff9eae67ffe873f3b44e2da31f`
**Current repair base SHA:** `ec2d68ed1d88c90aa7892c6d51faf4c5aef153c6`
**Status:** FINAL LIVE-DIAGNOSTIC REPAIR IMPLEMENTED LOCALLY; CURRENT TREE NOT CERTIFIED

## Executive result

The graph-first persistent execution-state mechanism is implemented in the active
Mini-SWE central agent. It is not a second autonomous agent and it does not replace
the existing 17-feature engine. After the repository graph is built and validated,
GroundTruth performs one bounded bootstrap model call that may select only
catalogued, graph-backed items. It then maintains typed state deterministically at
every executor boundary and exposes only a bounded current slice to the next normal
model request.

The prior provider-free workflow `31647174958` passed at its runtime SHA above.
It built that revision's indexer from source, provisioned the pinned Snowflake
Arctic ONNX asset, ran the central tests and readiness gates, printed `READY` and
`SMOKE_APPROVED`, and uploaded a receipt with `provider_calls: 0`. This proves
integrity for the earlier implementation. It does **not** certify the current
working-tree repair and does not prove solve-rate uplift, flips, no regression,
or efficiency on a sampled model run.

## Current corrective pass

The rejected live diagnostic `31656913063` isolated a failed bootstrap rather
than a graph failure. Its graph, dense backend, delivery timing, contribution
budget, preflight, decision sufficiency, validation, and retrieval-efficiency
checks pass under current replay. The old receipt fails exactly five persistent
state conditions: no selected bootstrap, no received response, no direct
single-call transport, invalid state runtime, and no applied selection.

The current repair now:

- performs one direct no-retry bootstrap provider call and accounts received
  parse failures without executing the returned Bash payload;
- prevents ordinary task prose from certifying common exact symbols;
- orders task-ranked hybrid candidates before generic graph-order catalog items;
- updates persistent state at every boundary and delivers an initial/critical
  frame, material delta, or <=96-token stable core on every applicable executor
  request;
- marks claims exposed only after the contribution is selected and provider
  dispatch begins;
- preserves bootstrap-selected optional focus, satisfied obligations, tuple
  ordering, state version, and transition metrics across semantic graph no-ops;
- packs the persistent state before every other GT contribution so a large
  diagnostic retrieval cannot make the living artifact disappear;
- stops before provider transport when the durable call marker cannot be written,
  delegates request timeout to the provider, and waits for transport completion;
- reports graph substrate health independently of bootstrap/delivery health;
- uses one pre-matrix provider canary instead of one paid preflight per task;
- defaults the final workflow to the full outcome-preservation profile in
  SHADOW preflight mode; and
- discloses the resolved workspace equally to both arms through the
  `resolved_workspace_v1` prompt contract; and
- rejects a baseline not proven GT-off, missing observed fingerprint identity,
  fake-zero setup accounting, and efficiency reports that omit pre-matrix canary
  overhead.

Local widened tests, lint, compilation, and workflow parsing pass. Local census,
readiness, and pre-smoke remain correctly blocked by the stale Windows indexer
missing Objective-C and by the unpushed working tree. A current source-built
Linux provider-free workflow must pass before any paid smoke.

## What shipped

### Graph-first lifecycle

```text
task enters MiniSweCentralAgent
  -> build and validate repository graph
  -> shared five-channel HybridRetriever runs once
  -> result seeds the bootstrap catalog and the first live retrieval cache
  -> one bounded temp=0 bootstrap call selects catalog IDs (no action executes)
  -> executor provider request receives the current state slice
  -> preflight projects the proposed action into state
  -> host executes the original action
  -> postflight commits observed result, validation, diff, and obligations
  -> source refresh rebases the graph and invalidates stale labels
  -> next provider request receives the updated state slice
```

The state kernel is `gt_engine/persistent_execution_state.py`. The active loop is
`eval/gt_central_agent.py`. Retrieval is shared with the accepted ARB path through
`gt_engine.hybrid_retrieval.HybridRetriever`; the initial retrieval result is reused
to avoid duplicate task-start dense/ranking work.

### Determinism boundary

| Component | Behavior |
|---|---|
| Graph build, source revision, language resolution | Deterministic and fail-closed |
| Exact/lexical/BM25/Snowflake-ONNX/structural retrieval | Deterministic for a fixed checkout and asset |
| Catalog construction and evidence provenance | Deterministic |
| Bootstrap selection | One bounded model call; returns catalog IDs only |
| State transitions, validation consumption, graph rebase | Deterministic; unknown evidence causes no mutation |
| Context packing, deduplication, request receipts | Deterministic |
| Executor action choice and code edits | Model-driven Mini-SWE behavior |
| Replanning/advisor loops, command rewrite, feature suppression | Not present |

The mechanism therefore constrains non-determinism to one bounded bootstrap choice
and the ordinary executor. It does not claim to make a temperature-1 model
deterministic.

## State used repeatedly

The artifact is task-scoped and held in memory; it is not a one-time Markdown plan.
The same state object is consumed and updated at these boundaries:

1. **Initialization:** graph-backed catalog, task requirements, current revision.
2. **Provider compilation:** active phase, focus, obligations, evidence gaps, and
   recent deterministic deltas are packed into one bounded frame.
3. **Preflight:** the proposed typed action is associated with the current state
   before `environment.exec`.
4. **Postflight:** action result, validation classification, changed paths, and
   newly discovered obligations update the state.
5. **Graph rebase:** source changes refresh the graph; stale graph labels are
   removed before any later frame can expose them.

No additional executor action is created by state maintenance. Bootstrap calls,
executor calls, and effective actions are counted separately in deep metrics.

## Correct delivery contract

The model receives only a bounded declarative state frame in the normal provider
request. It contains source-backed claims, active obligations, current focus, and
deterministic progress/validation state. Initial/critical material is capped at
512 packing tokens, changed-state delta at 256, and unchanged-state core at 96.
The stable core is deliberately repeated because it is not appended to durable
history; source excerpts and newly exposed semantic claims remain one-shot. The
frame does not contain raw Bash programs, heredoc bodies, speculative file
choices, or a free-form plan. Existing Mini-SWE observations remain intact.

Every prepared request is audited for:

- exact provider request hash and message index;
- one first-eligible delivery, never predictive or late;
- source/workspace revision validity;
- evidence provenance and token/character budget;
- duplicate suppression and complete accounting;
- state initialization, boundary use, and graph-current status.

Private state transitions are not counted as model influence. A receipt proves
production; a controller disposition proves internal consumption; only an exact
provider-visible frame proves delivery. Solve causality still requires a matched
control/treatment trajectory comparison.

## Defects found and repaired before certification

- Production GraphDB relations are normalized across the bounded certified aliases;
  uppercase `CALLS`/`ASSERTED_BY` data no longer disappears at the state boundary.
- Validation obligations consume the shared canonical
  `ValidationClassification.declared_check_id`; wrapped commands do not require
  unsafe raw-command reparsing.
- Reads cannot satisfy a creation/deliverable obligation.
- Unknown or pending validation cannot be converted into a fabricated failure.
- Invalid or timed-out bootstrap cannot invent a ranked focus.
- Initial graph retrieval and first live retrieval share one result/cache key.
- Graph rebase removes stale labels after source changes.
- Same-revision semantic no-ops preserve optional bootstrap focus and obligation
  ordering without incrementing version/material-transition counters.
- A failed provider-query marker prevents transport, and provider timeouts cannot
  abandon an unaccounted worker thread.
- Request-wide packing cannot budget-drop the persistent core behind a larger
  diagnostic retrieval.

## Prior certification evidence (does not cover the current repair)

Workflow `31647174958` passed these mandatory provider-free census lines:

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
READY
SMOKE_APPROVED
```

That earlier workflow also passed source-built graph coverage, pinned ONNX provisioning,
central release tests, static checks, and byte-compilation. The local Windows
checked-in `vendor/gt-index.exe` remains an intentionally known stale-binary
witness; it lacks current Objective-C support. It must not be used to override or
weaken the source-built Linux gate.

## What is not proven

The following claims remain unproven until a paid, contemporaneous matched run:

1. GT-on resolves more tasks than GT-off.
2. GT creates positive flips without GT-attributable losses.
3. GT reduces tokens, calls, actions, steps, wall time, or cost after counting the
   bootstrap call and all effective controller actions.
4. Persistent state improves multi-file completeness or validation success.
5. The mechanism generalizes to DeepSWE or Terminal-Bench without regressions.

The historical GT-off artifacts remain the control reference supplied by the user;
they are not a same-commit proof for this new treatment. No paid run was launched
in this documentation pass.

## Release gates and remaining TODOs

### Current implementation checklist

- [x] Implement graph-first persistent state.
- [x] Use one bounded catalog-ID bootstrap after graph construction.
- [x] Reuse the initial HybridRetriever result at the first live retrieval boundary.
- [x] Update state at provider, preflight, postflight, and graph-rebase boundaries.
- [x] Add exact delivery, state-transition, bootstrap, and resource accounting.
- [x] Add regression tests for graph relations, validation identity, reads, and
      invalid bootstrap.
- [x] Add biting tests for stable-core budget priority, marker fail-closed
      transport, provider-owned timeouts, no-op graph rebases, baseline-arm
      identity, observed fingerprints, and all-in setup accounting.
- [ ] Pass the exact source-built provider-free release gate for the current repair.

### Remaining, in order

1. **Run the source-built provider-free workflow.** The current repair needs its
   own Linux build/census/readiness/pre-smoke proof; prior passing SHAs do not
   certify this tree.
2. **Validate the existing GT-off artifact without rerunning it.** Require its raw
   receipts to prove `arm=gt_off`, `integration_mode=off`, current prompt/tool
   hashes, Mini-SWE 2.2.8, no-retry transport, and nonempty observed response
   identity. Reject rather than repair metadata that raw evidence cannot prove.
3. **Freeze a paid diagnostic manifest.** Pin model/checkpoint, prompt, task set,
   wrapper, temperature, timeout, tool schema, graph/indexer revision, ONNX hash,
   and evaluator. Keep GT-off and GT-on identical except for the treatment switch.
4. **Run one bounded matched diagnostic** on a predeclared mixture of historical
   gains, losses, both-fail tasks, source-applicable tasks, and legitimate
   no-source tasks. This requires separate paid-run authorization.
5. **Audit every trajectory** for graph applicability, one bootstrap/zero bootstrap
   actions, cache reuse, state-boundary receipts, exact next-request delivery,
   stale-state rejection, outer timeout/censoring, and complete resource counts.
6. **Compute outcome-first comparisons:** resolved, gained, lost, both-pass,
   both-fail, uncensored resolved, total/executor/bootstrap calls, tokens, actions,
   steps, wall time, cost, and per-task Pareto status.
7. **Apply the release decision.** Any GT-attributable loss or invalid delivery
   blocks expansion. A non-regressive positive result permits the frozen mechanism
   to proceed to the planned DeepSWE and Terminal-Bench evaluation arms.
8. **Do not tune the state mechanism against individual task IDs** after seeing the
   diagnostic. A failed gate requires a new explicit defect diagnosis and plan.

## Immediate next step

The next action is the exact pushed, source-built Linux provider-free workflow;
it is not a paid smoke or a full benchmark. It must build the current indexer,
provision the pinned ONNX asset, print every census line plus `READY` and
`SMOKE_APPROVED`, and record zero provider calls. Only after that passes may a
matched paid diagnostic manifest be reviewed. The correct project state is:

```text
runtime integrity: PASS in local widened tests
deterministic state lifecycle: PASS in local widened tests
provider-visible delivery accounting: PASS in archived replay; current workflow pending
outcome uplift: UNKNOWN
regression safety: UNKNOWN
efficiency: UNKNOWN
full benchmark readiness: BLOCKED on current Linux provider-free certification
```

## Authoritative files

- `gt_engine/persistent_execution_state.py`
- `eval/gt_central_agent.py`
- `scripts/central_release_gate.py`
- `scripts/central_readiness_audit.py`
- `scripts/central_pre_smoke_gate.py`
- `GT_PERSISTENT_EXECUTION_STATE_RESEARCH.md`
- `GT_PERSISTENT_EXECUTION_STATE_IMPLEMENTATION_PLAN.md`
- `AGENTS.md`
- `CLAUDE.md`
- `GT_ARCHITECTURE.md`


## Final P0/P1 closure boundary (2026-08-13)

The final benchmark path has one physical-call boundary. When the installed
Mini-SWE model exposes `_query`, `_prepare_messages_for_api`, and
`_parse_actions`, both the one-time persistent bootstrap and every executor
request call `_query` exactly once. The public `model.query()` wrapper is not
used on that path because its tenacity loop can turn one logical receipt into
multiple paid provider attempts. The direct adapter preserves Mini-SWE cost
accounting, copies the non-secret provider identity from LiteLLM hidden
metadata, records usage/cost/identity before parsing choices, types an empty or
unparsable response, and recognizes the real LiteLLM timeout class. The exact
request hash includes effective per-call temperature, output limit, timeout,
retry, and tool-choice arguments; the message-list hash remains separate.

Visible-delivery state is now two-phase. Retrieval, frontier, feature, progress,
and persistent-state contributions may be prepared before the provider marker,
but claim deduplication, producer confirmation, visible-delivery receipts, and
exposure state are committed only after the marker succeeds and dispatch
begins. The authoritative delivery audit rejects `prepared_not_sent` and
`marker_error` contexts. Marker failure therefore produces zero provider
transport and zero visible deliveries.

Persistent execution state binds postflight to observed execution rather than
selection time only. A later action in a pre-decided SHADOW batch may commit at
the current revision after an earlier mutation; an unrelated stale proposal is
still rejected. Source edits no longer open obligations from the pre-edit
graph. A successful rebase invalidates old open advisory obligations and
recomputes only those supported by the current certified edge set. Old catalog
line/symbol labels are not rendered after a graph revision change. Repeated
failures fall back to a bounded stable CORE instead of disappearing, and CORE
includes the current focus path without repeating a source excerpt.

The DeepSWE workflow is fail-closed before spend: the exact-SHA source-built
provider-free reusable workflow must pass before the one paid bootstrap canary.
The same workflow can now produce a schema-compatible `gt_off`/`baseline`
control as well as the gated GT-on profiles. Baseline and treatment outcomes
must contain a boolean solve, an official 0/1 verifier reward that agrees with
it, complete observed identity, finite nonnegative resources, and no censoring.
Censored baseline rows cannot manufacture flips. `persistent_state_only` is a
mechanism diagnostic; only `certified_full` is labeled the integrated product.
The one-rollout, 300-call, 5,400-second DeepSWE experiment is matched product
evidence, not official leaderboard equivalence.

Local broad tests pass apart from the explicitly fail-closed Windows indexer
checks: the checked-in `gt-index.exe` predates Objective-C registry support.
This is not waived. The current source-built Linux provider-free workflow must
rebuild the indexer, provision the pinned Snowflake ONNX asset, and pass census,
readiness, pre-smoke, static, workflow, and receipt gates at the exact pushed
commit. Until then the status is implementation-verified locally but release-
unverified; no paid smoke, solve uplift, non-regression, or efficiency claim is
authorized.
