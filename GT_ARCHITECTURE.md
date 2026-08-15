# GroundTruth central engine architecture

Authoritative date: 2026-08-12

## Graph-first persistent execution state (2026-08-12)

The active central runtime optionally creates one task-scoped typed execution
state after the repository GraphDB is complete and before the first executor
request. See `GT_PERSISTENT_EXECUTION_STATE_RESEARCH.md` for the architectural
decision and `GT_PERSISTENT_EXECUTION_STATE_IMPLEMENTATION_PLAN.md` for the
living implementation record.

The lifecycle is:

    complete repository graph
    -> one shared five-channel task-start retrieval (local; zero provider calls)
    -> one catalog-ID bootstrap call (never executed as an action)
    -> bounded state frame in provider request N
    -> typed preflight read
    -> host execution
    -> deterministic postflight transition
    -> graph refresh/rebase when source changed
    -> bounded current state frame in provider request N+1

The bootstrap is the only new model call. All subsequent state maintenance is
deterministic. Graph-derived dependency items are advisory unless a separate
mechanical fact makes them required; exact task checks and deliverables are the
only task-owned blocking obligations. The state cannot rewrite, suppress, or
execute commands. All call/token/context overhead is included in totals.

Task-start retrieval is not a benchmark-only or second retrieval path. It calls
the same `HybridRetriever` and frozen live profile used at the provider boundary.
Its exact result both populates the bootstrap catalog and seeds the first live
retrieval cache, preventing duplicate Snowflake embedding/ranking work. Hybrid
rank is task relevance, not certification: its catalog rows remain explicitly
non-certified, while paths/spans still come from the current checkout. A valid
bootstrap may select no focus; invalid output cannot promote a ranked candidate.

The state consumes production relation names through a bounded alias map and
only certified mechanical relations can create advisory obligations. Validation
updates reuse the central immutable `ValidationClassification`, including the
canonical declared-check ID, so shell wrappers do not strand a satisfied task
check and raw return codes are never reinterpreted as validation authority.

Provider-free workflow `31647174958` passed runtime commit
`e0c63ae15be6eeff9eae67ffe873f3b44e2da31f` with
the source-built current indexer, pinned Snowflake ONNX backend, central tests,
`READY`, `SMOKE_APPROVED`, and a receipt recording zero provider calls. This is
runtime-integrity evidence, not a solve-rate or efficiency result.

GroundTruth is an in-process deterministic evidence and control layer owned by
`eval.gt_central_agent.MiniSweCentralAgent`. It is not an MCP sidecar and the
model does not invoke it.

```text
model-selected Bash action
  -> typed ProposedAction / SHADOW preflight
  -> host execution
  -> workspace + source revision sensor
  -> incremental certified graph refresh
  -> postflight validation + 17 feature runtime
  -> frozen hybrid retrieval for the next decision boundary
  -> typed GTContribution compiler
  -> provider-evidence ledger + exact request hash
  -> immediate next model request
```

## Retrieval profile

`gt_engine.retrieval_profile.FINAL_RETRIEVAL_PROFILE` is shared by ARB and the
live loop. It combines exact, lexical, BM25, structural, and pinned Snowflake
ONNX dense channels through the existing reciprocal-rank fusion. It ranks 20
files, selects at most eight complete evidence units inside 1,200 tokens, and
embeds at most 32 dense candidate spans. Active/changed paths and stale source
revisions retain the existing exclusion rules.

Cold retrieval has a 30-second deadline derived from the accepted ARB latency
distribution. Cached turns have a two-second fail-open deadline. Failure,
timeout, incomplete graph, or missing model asset produces abstention and does
not block Mini-SWE.

## Composition contract

Each potential provider payload becomes a `GTContribution` with surface,
kind, payload hash, claim/fact IDs, evidence action, eligible call, source
revision, and priority. `compile_contributions()` gives every candidate one
replayable disposition. Selection is complete-fact only; stale, expired,
future, duplicate, controller-only, and over-budget rows are not rendered.
The call receipt must satisfy candidate/accounted equality.

The compiler does not turn private engine work into model text. The provider
ledger remains the authority for actual dispatch, message indices, exact
request hash, and first-eligible timing.

## Paired decision-point capture

When replay capture is explicitly enabled, a visible GT call stores both the
provider-prepared control view before GT text and the dispatched treatment
view, plus the exact tool schema and compiled contribution metadata. The
validator reconstructs the treatment from the control and recorded payload;
any other byte difference rejects the case. This measurement path is inert
when capture is disabled and never adds a model or agent action.

## Active component authority

`gt_engine.component_registry` enumerates the active central subsystems and
derives all 17 feature contracts from executable inventories. The five
postflight-only features remain `GT_CHANGE_SURFACE`, `signature_delta`,
`GT_PATCH_DELTA`, `syntax_result`, and `covering_red`. A stochastic run is not
required to fire every feature; eligibility, abstention, firing, consumption,
and provider delivery remain separate statuses.

## Current evidence boundary

ARB workflow `31517629497` proves retrieval metrics only. Local real-ONNX
tests prove live cold/warm execution, dense availability, exact first-request
delivery, and zero extra calls/actions. Exact-tree GitHub provider-free run
`31527155811` passed at `90896d4`; paired decision-point utility remains
required before freeze. Archived run `31421610097` has zero eligible pairs
because it omitted exact controls. End-to-end solve
uplift and non-regression remain unproven until the frozen GT-on evaluations.

## Pre-execution decision sufficiency

The engine has a narrow, opt-in compiler between typed proposal normalization
and host execution:

```text
ProposedAction
  -> bounded target/structural-neighbor repository slice
  -> hybrid ranking without per-action dense inference
  -> exact selecting-request visibility check
  -> certified complete evidence bundle or PASS
  -> SHADOW receipt, or separately gated ASSISTIVE_SAFE return
```

It does not predict an action, rewrite a command, or add generic advice. A
single-target mutation is `RETURN_ELIGIBLE` only when one current
exact/mechanical or certified structural claim is missing from what the model
already saw. Semantic and graph revisions are checked independently. Ambiguous
parsing, incomplete state, staleness, sparse/dense-only evidence, co-change
evidence, duplicates, and complete-evidence budget overflow fail to `PASS`.

Visibility is certified from exact provider-prepared messages, including
ordinary Mini-SWE tool observations such as `sed` or `cat`; no marker or model
acknowledgement is used. A biting perturbation disabling this check caused a
duplicate second return, and the restored end-to-end test rejects it. DeepSWE
and Terminal-Bench workflows currently qualify this mechanism in `SHADOW`, so
it cannot change execution or add calls during treatment measurement.

## Substrate recovery invariants

Derived trees are pruned before manifest entry limits. Recovery after an
unhealthy sensor snapshot performs a complete supported-source rehash. Host
waiting exceeds the bounded index subprocess timeout, preventing a timed-out
coroutine from racing a live index worker. Final graph state comes from the
atomic repository session. `scripts/central_release_gate.py` rejects substrate,
dense, delivery, preflight, or decision-receipt violations before paid work.

## Final promotion repair (2026-08-12)

The live DeepSWE diagnostic exposed three objects that must not be collapsed
into one word such as "evidence": a repository candidate may be broad and
useful for ranking; a provider-deliverable content claim must name one complete,
grounded span; and a decision claim must additionally be mechanically material
to the exact proposed action.

`RetrievalCandidate.content_claim_id` now hashes only semantic content
(path/span/symbol/relation/text). Graph row IDs, channel receipts, revision IDs,
and delivery support do not create a new fact. `claim_hash` remains a
compatibility alias. Decision bundles carry a separate `decision_claim_id`
bound to content, operation, target, and support kind.

Graph structure is no longer file-only at delivery time. `StructuralLink`
retains source and target symbol/line endpoints from GraphDB. The structural
channel indexes every document span per path and selects the exact endpoint;
an unresolved endpoint remains rankable but receives
`edge_endpoint_unresolved` and cannot certify delivery or action return. RRF
retains its per-channel representatives, so an exact-path certificate cannot be
borrowed to deliver an unrelated structural span. Generic import and co-change
facts can rank context, but they cannot authorize pre-action return.

Retrieval is budget-first and event-accounted. A zero/closed delivery budget
runs no channel. A positive partial character budget is enforced while
complete spans are packed, so selection cannot precede a host-side budget
discard. Identical state/revision/visibility/configuration queries use
a bounded 128-entry result cache. Up to 3,000 of the 12,000 task characters is
reserved for post-mutation, diagnostic, and validation opportunities so
task-start and read/search traffic cannot consume the failure-recovery budget.
Every provider boundary records opportunity kind, candidates, selection,
delivery, abstention reason, cache status, latency, and exact visibility hashes.
This accounting is measurement, not a claim that a delivery helped.

The DeepSWE treatment workflow now enables bounded provider-budget compaction,
the fail-open completion controller, semantic progress control, and adaptive
validation timeouts. The release gate rejects disabled controls, work after a
closed budget, missing opportunity accounting, duplicate content claims, and
non-material or non-endpoint-aligned decision evidence. Preflight remains
`SHADOW`; no action-changing claim is made before a separately approved smoke.

Provider-free workflow `31616184187` passed the complete implementation gate on
runtime commit `80a8376`, with current native graph build, pinned Snowflake ONNX,
all-17/timing/context proof, `READY`, `SMOKE_APPROVED`, and zero provider calls.
This is architecture/integration evidence only; outcome and efficiency remain
live matched-smoke gates.


## Final no-regression hardening contract (2026-08-12)

This section supersedes earlier wording that the final workflow had already
switched to `ASSISTIVE_SAFE`. The executable DeepSWE workflow defaults to
`ACTIVE + SHADOW` through the `persistent_state_only` profile; `certified_full`
is an explicit opt-in `ASSISTIVE_SAFE` profile. No paid task is eligible until
the exact pushed provider-free workflow rebuilds the current Go indexer and
passes the complete census, readiness, and pre-smoke gates. This is an
implementation state, not an outcome claim.

The final hardening is top-down:

1. The live retrieval action is projected from the action-bearing shell segment,
   not a leading `cd` or other shell context segment. Raw heredoc and
   interpreter program bodies remain excluded.
2. Validation failure output is bounded and parsed into exact traceback,
   JavaScript/TypeScript stack, or compiler path/line anchors only when the
   referenced path exists in the current repository manifest. Diagnostic
   symbols and paths enter the immediate postflight retrieval query before the
   next provider call.
3. Repository-query cache identity includes source revision, paths, symbols,
   retrieval boundary, and diagnostic fingerprint. A prior read query cannot
   satisfy a later failure query.
4. Retrieval grounding and decision relevance are separate. Same-file unseen
   spans may rank, but an active path alone cannot authorize delivery.
   Model-visible evidence requires an exact symbol, a direct mechanically
   relevant caller/test relation, a change-impact relation for a mutation, or a
   validation-linked test. Dense/sparse/co-change support never becomes
   delivery authority by itself.
5. Retrieval budgets are lifecycle-specific. Task start, read/search, mutation,
   and diagnostic/validation opportunities have separate bounded allowances;
   diagnostic and validation share their late-recovery reserve. Closed budgets
   run zero retrieval channels.
6. Project checks are discovered only from real repository contracts: declared
   pytest configuration/dependency/test trees, non-placeholder npm test
   scripts, exact Make test targets, Cargo, or Go modules. Checks are scoped to
   the nearest project root. A generic `pyproject.toml` is not a pytest
   contract.
7. Standard-runner failures are grounded submit blockers. A standard-runner
   pass clears whole-project validation debt only when its scope is
   mechanically project-wide. In assistive mode, if authored source is still
   unvalidated at submit, the controller may run exactly one discovered
   project check for that source revision. A failure returns the real bounded
   diagnostic; success clears debt; timeout/exception fails open; the probe is
   never repeated at the same revision.
8. Progress is semantic, not command novelty. Source revision, validation
   state, diagnostic identity, obligations, and validation debt form the
   progress fingerprint. Model-visible stall requires 12 no-progress actions
   (24 for a cycle), while unresolved budget risk begins at 60% of the action
   limit. Novel scratch commands do not erase validation debt or budget risk.
9. Context compaction remains deterministic and loss-bounded. It starts only
   after the configured character threshold and minimum savings, removes no
   unique assistant reasoning, keeps the latest two turns, and preserves the
   full audit history outside the provider view.
10. Workspace impact is independently classified. Proven external-only writes
    skip repository sensing; unknown or workspace-affecting commands continue
    to scan fail closed. Metrics separately report model decision actions,
    harness/substrate executions, and controller interventions.

The final DeepSWE workflow pins the official OpenRouter model identifier
`deepseek/deepseek-v4-flash` and a DeepSeek-only provider route with fallbacks
disabled and required parameters enforced. Mini-SWE's exact model kwargs now
participate in request hashing; secrets never enter receipts. The workflow
records the checked-out GT SHA, not the dispatcher SHA, uses exact
`reward == 1` with no outer exception, runs at parallelism 20, and permits
one infrastructure retry only when neither a provider-query marker nor a
central receipt exists. OpenRouter routing semantics are documented at
https://openrouter.ai/docs/guides/routing/provider-selection.

`scripts/deepswe_release_gate.py` is the outcome authority.
The baseline artifact and manifest are validated in a prerequisite GitHub job;
the paid treatment matrix cannot start if that control is absent or confounded.
Preservation requires the identical task/model/provider/runner/budget manifest, zero
baseline-solved losses, zero censored treatment tasks, and non-positive
common-solved deltas for tokens, provider calls, model decision actions, and
provider cost. Promotion additionally requires at least one positive flip and
strictly more treatment solves. This gate does not make regressions impossible;
it prevents a regressing run from being promoted or mislabeled.

Current proof boundary: focused Python suites and static checks pass. The full
local graph-dependent census is blocked by the known stale Windows
`gt-index.exe` lacking Objective-C. Only the source-built Linux provider-free
workflow can clear that gate. No new paid run, solve uplift, efficiency uplift,
or non-regression claim exists yet. The existing online DeepSWE-off result is
usable only if its frozen artifact satisfies the exact comparison manifest;
otherwise it is descriptive, not the release control.


## DeepSWE provider-and-delivery regression repair (2026-08-12)

The observed `4/10 -> 1/10` change is not a persistent-execution-state
result and is not a valid same-model A/B comparison. Run `31557391617`
served all 1,450 recorded assistant responses as `deepseek-v4-flash` with
one stable fingerprint. Run `31575925244` served 1,136 responses as
`deepseek/deepseek-v4-flash` across two reported upstream providers
(StreamLake and GMICloud) with no fingerprint. The persistent-state workflow
then failed provider preflight before executing a task. Outcome causality is
therefore confounded by provider identity and by a simultaneous delivery-policy
change.

The delivery change was nevertheless a real GT defect. The earlier good run
had five preemptive deliveries totaling 11,339 characters; all five occurred
on one task. The 1/10 run had 53 preemptive deliveries totaling 117,395
characters across all ten tasks. A repaired graph/source-revision seam had
activated large generic task-start frames, and persistent bootstrap would have
duplicated the same task-start authority.

The current contract is:

1. Persistent bootstrap exclusively owns task-start localization after a valid
   selection. The generic preemptive task-start frame abstains with
   `persistent_bootstrap_owns_task_start`; later action/result-conditioned
   retrieval remains active.
2. The bootstrap selection request contains bounded catalog metadata and no
   source bytes. The first executor request receives exactly the selected
   checkout-backed symbol span, with path/span/claim provenance. In a
   multi-symbol file the selected symbol, never the first file span, owns the
   excerpt. Once the executor reads that path, the excerpt is not repeated.
3. All visible GT surfaces share one 1,200-token request budget. The
   contribution compiler accounts every candidate, emits only complete facts,
   and the release gate rejects missing calls, unaccounted candidates,
   duplicate surfaces, or budget overflow.
4. JavaScript/TypeScript validators invoked through literal `npx`,
   `npm exec --`, `pnpm exec`, `yarn exec`, or `bunx` are recognized
   without scanning source text. Dynamic executor forms and help/version/list
   commands abstain. Pipeline outcomes remain unattributed unless the shell
   mechanically proves terminal ownership.
5. Every live receipt records actual response model/provider/fingerprint
   identity for executor and bootstrap calls. DeepSWE merge requires complete,
   stable, matching model, provider, and fingerprint identity; missing identity
   fails closed. Requested model names alone are not parity proof.
6. DeepSWE artifact discovery reads the task ID from the adjacent Pier result,
   so task directories without `-task-` no longer collapse into a false
   one-task audit.

The archived 1/10 artifacts now audit as ten tasks with 71 timely, hash-valid
visible deliveries and 121,201 visible characters. That proves the old engine
delivered what it selected; it does not make the selection useful. Local
Python tests for the repaired boundaries pass. The only local census blocker
is the known stale Windows `gt-index.exe` missing Objective-C; the source-built
Linux provider-free workflow remains authoritative. No new solve,
non-regression, or efficiency claim exists until that pushed workflow passes
and the external provider route can produce an exact response-identity proof.


### Authoritative provider-free certification

Workflow `31655082336` passed at runtime commit
`9be71ad9309d99bfe2eb6d7d942a89bdae8d39b3`. It built the current Linux
indexer, provisioned the pinned Snowflake ONNX asset, passed repository and
language substrate proof, central runtime tests, all required all-17,
grounding, timing, visibility, and context-accounting lines, `READY`,
`SMOKE_APPROVED`, and static checks. Its receipt records
`provider_calls: 0`. This upgrades the repair to implementation-certified;
it does not establish solve uplift, non-regression, or efficiency, and it
predates the current marker/timeout, stable-core, semantic-no-op, and final A/B
gate repairs.

## Final live-diagnostic repair contract (2026-08-12)

The rejected DeepSWE diagnostic `31656913063` proved a healthy graph and dense
substrate but a failed persistent bootstrap. The final repair changes only the
bootstrap/catalog/state/delivery boundaries; it does not redesign the graph or
add another agent:

```text
resolved workspace disclosed to both arms
  -> current graph and five-channel retrieval
  -> task-ranked bounded catalog
  -> one direct no-retry bootstrap provider call
  -> selected certified excerpt in the first executor request
  -> deterministic state update at every boundary
  -> initial/critical frame, material delta, or <=96-token stable core
  -> full-profile outcome-preservation controllers
```

Exact authority is limited to syntax-marked task entities, active symbols,
diagnostic entities, and typed code-shaped action tokens. Sparse and dense
channels may rank ordinary prose but cannot certify it. Task-ranked catalog
items precede generic graph order. Semantic no-ops do not bump state versions,
source excerpts and new semantic claims are not repeated, and same-revision
refresh cannot reopen a satisfied obligation or reorder state. Every applicable
normal request receives one bounded current-state slice; `NONE` is only a
fail-open invalid/stale/unavailable-state disposition.

The workflow has one pre-matrix bootstrap canary and no per-task provider
preflight. `certified_full` is the default, with ACTIVE integration, SHADOW
preflight, and all outcome-preservation controls enabled. Graph substrate
validity is reported independently of downstream mechanism validity.

This repair is locally test-verified but not release-certified or
benchmark-proven. The stale Windows indexer lacks Objective-C, so only the
pushed source-built Linux provider-free workflow can approve a paid smoke.


## Final P0/P1 closure boundary (2026-08-13)

The provider boundary is now single-transport and two-phase: prepare every
bounded GT contribution, persist the request marker, then commit exposure and
perform exactly one direct Mini-SWE/LiteLLM `_query`. Provider identity,
usage, cost, malformed-response state, real timeout type, and effective call
arguments remain in replayable receipts. A marker failure produces no provider
call and no visible-delivery receipt.

Persistent state is updated after every actually executed action, including a
later pre-decided SHADOW batch action whose selection revision became stale
after an earlier mutation. Graph-derived obligations are recomputed only from
the successful current rebase; old catalog labels and disappeared edges cannot
remain current model facts. Every applicable normal request retains a bounded
CORE with its current focus, while material deltas/critical evidence remain
one-shot and source excerpts are not repeated.

DeepSWE uses one executable workflow for both a strict GT-off baseline profile
and the GT-on diagnostic/product profiles. A reusable exact-SHA provider-free
job gates the paid canary. Censoring, invalid verifier outcomes, missing
identity, NaN/Inf resources, profile/claim-scope mismatch, and stale baseline
schemas fail before an A/B claim. This workflow is a matched product
experiment, not an official leaderboard-equivalent DeepSWE configuration.

The implementation remains release-unverified until Linux rebuilds the current
Go indexer and the exact pushed provider-free workflow passes. The local
Windows binary is known stale and lacks Objective-C coverage; that failure is
not weakened or reclassified.
