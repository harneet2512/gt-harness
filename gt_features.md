# Groundtruth Features: Unified SDLC Engine and Live-Proof Contract

Date: 2026-07-30
Repository scope: `gt-harness`
Status: live wiring/behavior proof passed; comparative benchmark benefit not yet established

## Executive conclusion

Combining Groundtruth's historical lifecycle layers with its current typed
FACTs, capability owners, gates, mediators, and behavior switches is the right
architecture. It can make GT materially stronger, but only if the combined
model becomes executable orchestration.

A unified table or renamed inventory improves comprehension and measurement. It
does not, by itself, improve Mini-SWE's behavior. GT becomes a stronger engine
when it:

1. executes the applicable deterministic mechanisms at the correct SDLC
   boundary;
2. evaluates the latest artifact, graph, failure, and test state;
3. records a terminal outcome for every mechanism that had an opportunity to
   run;
4. ranks positive evidence by correctness risk, specificity, freshness, and
   actionability;
5. sends no more than one bounded intervention at a boundary; and
6. proves the intervention was present in the provider request that produced
   the next model response.

The target lifecycle is:

```text
orient -> research -> pre_edit -> post_edit -> test
       -> recovery -> verify -> submit
```

This does not ask GT to perform deep or generative reasoning. Mini-SWE remains
the reasoner and actor. GT deterministically observes, derives, validates,
prioritizes, delivers, and records evidence.

Confidence:

- **High:** this architecture improves GT's timing, coverage, coherence,
  diagnosability, and attribution.
- **High:** taxonomy or telemetry alone does not improve the engine's behavior.
- **Moderate:** correctly implemented pre-edit, post-edit, recovery, and submit
  controls should improve agent efficiency and defect avoidance.
- **Unknown:** the size or direction of solved-rate, token, iteration, or
  wall-time changes until a controlled live comparison is run.

## Live proof update: run 30590129776

Run
[`30590129776`](https://github.com/harneet2512/gt-harness/actions/runs/30590129776)
executed the real nano-harness + GT path at commit `0d3dd88`, using
`deepseek-v4-flash`, temperature `1`, Profile 2, the frozen five-task slice,
concurrency `4`, and timeout multiplier `1.0`. The workflow, Harbor execution,
17-path preflight, artifact upload, attribution audit, and strict live gate all
passed.

The online result proves wiring and timing, not superiority:

| Task | Reward | Witnessed identities | Action-consistent identities | GT deliveries | Result |
|---|---:|---:|---:|---:|---|
| `build-cython-ext` | 1 | 8 | 5 | 6 | solved |
| `headless-terminal` | 1 | 6 | 1 | 4 | solved |
| `llm-inference-batching-scheduler` | 0 | 9 | 4 | 6 | bucket-1 cost missed threshold |
| `reshard-c4-data` | 0 | 6 | 5 | 3 | decompression timed out |
| `sanitize-git-repo` | 1 | 6 | 3 | 3 | solved |

Every task recorded `task_start`, `research`, `pre_edit`, `post_edit`, `test`,
`verify`, and `submit`. Across the run:

- 10 of 17 canonical identities were provider-witnessed;
- 15 of 17 were exercised with a named terminal outcome;
- no eligible trigger was dark, no delivery was unexposed, and no telemetry
  fault occurred;
- all 22 exposed capsules were unique, exposed exactly once, subsequently
  expired, and never repeated;
- 44 atomic graph refreshes completed with zero refresh faults and 373 bounded
  semantic graph facts in task projections;
- 62 typed predicate observations, 16 utility scores, and 9 progress
  transitions were recorded;
- all 277 tool outcomes were classified: 9 harmful, 13 useful RED observations,
  and zero persistent-shell lifecycle failures.

The existing GT-off data is not a fully matched experiment: its headless task
crashed and its timeout/temperature controls differ. On the four graded pairs,
however, GT-off solved 4/4 while this GT-on run solved 2/4. GT-on used 7.7%
more iterations, 53.5% more input tokens, and 70.2% more output tokens, while
using 9.3% fewer tool results and producing 11.8% fewer raw tool errors.
Therefore there is no benefit claim.

The two live failures expose the remaining engine defects:

1. numeric predicates can witness threshold-shaped checks without proving the
   final measured inequalities;
2. a bounded runtime probe can time out while later prose still claims it
   passed;
3. one submit refusal is insufficient when positive unresolved evidence
   remains;
4. role packs and semantic graph facts are measured but do not yet govern every
   verification and evidence decision; and
5. repeated matched runs remain outstanding.

## What GT is in the Mini-SWE system

GT is not a post-run trace analyzer attached as an afterthought. In the current
harness it wraps the Mini-SWE provider boundary, observes tool and lifecycle
events, derives deterministic evidence, and can add a bounded capsule to the
actual API request.

The evidence chain required for a model-facing GT action is:

```text
triggering event
  -> deterministic producer/check
  -> eligibility and authority decision
  -> sealed delivery bytes
  -> provider-final request receipt
  -> provider response
  -> next model action classification
```

Trajectories are not the delivery witness. Request-path capsules are proven
from the delivery ledger and its `bound_provider_payload_json`. Provider
messages may contain block lists, so a naive substring check against the
stringified message object is invalid.

The distinction between the three proof levels must remain explicit:

| Proof level | What it establishes | What it does not establish |
|---|---|---|
| Executed | GT ran a check at the boundary | The check found actionable evidence |
| Delivered | Exact GT bytes reached the provider request and response | The model acted because of those bytes |
| Action-consistent | The next action was consistent with the intended GT action | Counterfactual causal benefit or task success |

## Why the historical and current names looked contradictory

GT has used two overlapping vocabularies:

1. **Lifecycle/architectural layers** describe when GT operates.
2. **Runtime identities and controls** describe what mechanism operates and
   which component owns the result.

They are orthogonal. A feature such as `signature_delta` belongs at the
`post_edit` stage. `GT_PATCH_DELTA` owns the bytes for that fact. Eligibility
gates decide whether it may run or deliver. Mediators control authority,
deduplication, cooldown, and dose. None of those should be counted as four
independent model-facing features.

The current "17 features" are therefore not the complete historical GT system.
They are a deliberately selected attribution census:

- 10 model-facing canonical FACT identities; and
- 7 byte-owning capability identities.

The installed canonical registry also contains `cochange_prior`, an internal
support FACT excluded from the 10 model-facing identities. The broader runtime
contains eligibility gates, mediators, and behavior switches beyond the 17.

## Historical layers mapped to the current harness

| Historical layer or name | Purpose | Current boundary or mechanism | Current assessment |
|---|---|---|---|
| L1 localization | Rank relevant files and symbols | `localization` at task start/search; `GT_LOC_RESLOT` | Wired |
| L2 brief / `L1_brief` | Orient the task and extract requirements | `task_start`, `obligations`, initial localization | Wired |
| `post_view` / L3b | Caller, callee, importer, and similar-pattern evidence after navigation | `research`, `caller_contract`, `def_partition`, search localization | Partially consolidated |
| Historical pre-edit navigation | Inspect related surfaces before changing code | `research`, then explicit `pre_edit` checkpoint | Explicitly wired now |
| Preimage capture | Capture the old artifact before an edit | edit-before bridge and `pre_edit_checkpoint` | Wired |
| L3 post-edit | Inspect the actual patch, contract drift, callers, siblings, and verification implications | `post_edit`, `GT_EDIT_CHECK`, `signature_delta`, `newfile_precedent`, caller and covering lanes | Wired |
| L4 prefetch/endpoints | Fetch precedents, constraints, and graph evidence | Gateway producers for localization, caller contracts, and new-file precedents | Partial |
| L5 trajectory governor | Detect loops, repeated failure, premature completion, and unverified patches | `recovery`, `GT_HYPOTHESIS`, observed-RED tracking, SDLC verify gate | Partial subset |
| L5b intervention | Deliver a bounded correction | recovery or submit-refusal delivery | Partial subset |
| L6 reindex | Refresh graph state after edits | graph-refresh path | Invoked; success needs an explicit receipt |
| L6 verify | Check changed code before completion | test, covering lane, syntax check, verify | Wired |
| Finish gate | Refuse completion with unresolved positive evidence | submit, `submit_refusal`, `GT_CERT_DELIVERY`, `GT_SS_SUBMIT_RED` | Wired |
| Hygiene/curation | Remove scaffolding or block a dirty completion state | no equivalent passed to submit today; `hygiene=None` | Missing parity |

There was no universal historical model-facing layer named `pre_edit`.
Historical GT had post-view navigation that usually happened before editing,
the `pre_edit_nav_actions` metric, and preimage capture. The current harness
adds a real, explicitly recorded `pre_edit` lifecycle boundary before tool
dispatch. Historical L3 post-edit was real and remains a real `post_edit`
boundary.

## The current 17-identity attribution census

The source of truth for this projection is `gt_engine/attribution.py`.

| Identity | Role | Trigger | Correct timing | Intended model action |
|---|---|---|---|---|
| `obligations` | FACT | Issue text yields evidence-backed requirements | Orient/task start | Satisfy issue-derived requirements |
| `localization` | FACT | Indexed task or search yields ranked relevant locations | Orient or research | Inspect ranked locations |
| `caller_contract` | FACT | A viewed or signature-edited callable has verified callers | Research or post-edit | Inspect or update proven callers |
| `def_partition` | FACT | Search results contain separable definitions and references | Research/search | Distinguish definitions from references |
| `newfile_precedent` | FACT | Repeated failed search or a new file exposes a verified precedent | Research or pre/post-edit | Follow repository precedent |
| `signature_delta` | FACT | Before/after edit changes a callable signature with verified call sites | Post-edit | Repair affected call sites |
| `syntax_result` | FACT | Executed syntax/compiler check fails on edited source | Post-edit or submit | Repair the syntax failure |
| `covering_red` | FACT | A covering test fails because of an edited source file | Post-edit or submit | Repair the attributable regression |
| `recovery` | FACT | The same formal failure recurs after an intervening edit | Test/tool result | Abandon the falsified hypothesis |
| `submit_refusal` | FACT | Submission has unresolved positive failing evidence | Submit | Resolve the evidence before resubmitting |
| `GT_LOC_RESLOT` | Byte owner | Ranked localization is placed in the task-start or next search-result request | Orient/research | Make localization model-visible at the decision boundary |
| `GT_EDIT_CHECK` | Capability owner | Deterministic edit checker executes; it delivers only on a positive failure | Post-edit or submit | Validate the edited code |
| `GT_CERT_DELIVERY` | Byte owner | Completion certificate owns a submit-refusal delivery | Submit | Name completion evidence state |
| `GT_CHANGE_SURFACE` | Byte owner | Change-surface producer yields a new-file precedent | Research/edit | Deliver proven change-surface evidence |
| `GT_PATCH_DELTA` | Byte owner | Patch-delta producer yields a signature delta | Post-edit | Deliver evidence from the actual patch |
| `GT_HYPOTHESIS` | Byte owner | Governor yields repeated-failure recovery evidence | Test/tool result | Deliver a new-hypothesis intervention |
| `GT_SS_SUBMIT_RED` | Byte owner | Submit gate yields an unresolved-RED refusal | Submit | Refuse completion once |

Aliases such as `trace_frame`, `brief_localization`, `name_fold`,
`caller_contract_view`, and `companion_surface` are concrete evidence types.
They map to canonical identities; they are not additional top-level features.

### Correct interpretation of a clean run

All 17 identities should not be forced to deliver in every task. That would be
incorrect and would flood Mini-SWE with irrelevant bytes.

Examples:

- `newfile_precedent` should not fire when no new-file or failed-search
  opportunity exists.
- `signature_delta` should not fire when the patch changes no callable
  signature.
- `syntax_result` should remain quiet when the executed syntax check is green.
- `covering_red` should remain quiet when no attributable covering test fails.
- `recovery` should remain quiet when a formal failure does not recur after an
  edit.
- `submit_refusal` should remain quiet when completion has no unresolved
  positive RED evidence.

The correct requirement is:

> Every feature with a real trigger opportunity must execute or receive a
> named terminal disposition. Every delivered feature must be byte-proven,
> provider-bound, on time, and attributable to its intended action.

## The full control inventory must not be flattened into 17

The unified model should preserve these runtime roles:

| Role | Responsibility | Model-facing by default? |
|---|---|---|
| FACT | Typed semantic evidence | Only when positive and actionable |
| Byte owner | Owns the rendered delivery for a FACT | Yes, when its FACT is delivered |
| Eligibility gate | Decides whether required trigger inputs exist | No |
| Mediator | Applies authority, deduplication, cooldown, suppression, and arbitration | No |
| Behavior switch | Enables a deterministic runtime behavior | No |
| Lifecycle checkpoint | Establishes when evaluation occurred | No |

Current inventory analysis found 48 capability IDs in the installed canonical
runtime:

- 7 byte owners;
- 14 eligibility controls; and
- 27 mediators.

The harness profile intentionally omits one eligibility control and one
mediator, leaving 46 active capability controls. Seven behavior flags are also
enabled by the profile. The workflow adds the harness-local
`GT_SDLC_VERIFY`, yielding 54 effective GT-related toggles in that execution
configuration.

Those numbers describe runtime controls, not 54 pieces of model-facing advice.
Counting every switch as a feature would double-count implementation machinery
and make live attribution meaningless.

## Proposed unified executable architecture

### 1. One capability manifest

Every mechanism should have a machine-readable manifest entry:

```yaml
feature_id: signature_delta
historical_layer: L3
stage: post_edit
role: fact
owner: GT_PATCH_DELTA
trigger: callable signature changed and verified call sites exist
required_inputs:
  - before_artifact_hash
  - after_artifact_hash
  - fresh_graph_revision
intended_action: repair affected call sites
provider_delivery_required: true
terminal_outcomes:
  - APPLIED_QUIET
  - DELIVERED
  - INELIGIBLE
  - SUPPRESSED
  - FAULT
```

This manifest should extend the existing registry and attribution trace. A new
external framework is unnecessary.

### 2. A deterministic stage transaction

Each lifecycle boundary should run as one transaction:

```text
observe boundary and artifact version
  -> identify applicable mechanisms
  -> execute every eligible deterministic check
  -> record one terminal receipt per mechanism
  -> rank positive evidence
  -> arbiter selects at most one visible dose
  -> seal exact bytes
  -> bind bytes to provider request and response
  -> classify the immediate next action
```

This provides both behavior and proof. Recording a checkpoint without running
the mechanisms gives only observability. Running mechanisms without terminal
receipts gives behavior that cannot be audited.

### 3. Terminal outcomes

Every applicable mechanism must terminate in exactly one state:

| Outcome | Meaning |
|---|---|
| `APPLIED_QUIET` | Check executed successfully and found no positive actionable evidence |
| `DELIVERED` | Positive evidence was selected, sealed, and sent |
| `INELIGIBLE` | Required trigger inputs or opportunity were absent |
| `SUPPRESSED` | Positive evidence existed but authority, deduplication, cooldown, or arbitration blocked delivery |
| `FAULT` | The mechanism should have evaluated but its execution or telemetry failed |

The existing attribution statuses can remain as derived audit projections.
The stage transaction needs the smaller terminal contract above so that a clean
check is not confused with an absent check.

### 4. One bounded arbiter

All eligible deterministic checks should run, but GT should deliver no more
than one intervention at a lifecycle boundary. Suggested priority:

```text
unresolved correctness blocker / RED
  > precise patch or caller repair evidence
  > recovery from repeated failure
  > navigation and context evidence
  > quiet success
```

Selection requirements:

- evidence is tied to the latest relevant artifact;
- evidence is specific enough to imply an action;
- evidence is fresh relative to the current graph and patch;
- duplicate evidence is suppressed by evidence and artifact hash;
- suppression has a named reason; and
- blocking interventions are based on positive evidence, not missing
  telemetry or low-confidence inference.

This keeps GT deterministic and useful without turning it into a second
reasoner or an unbounded prompt generator.

## Stage-by-stage behavior

| Stage | Inputs | Mechanisms that should run | Visible result only when |
|---|---|---|---|
| Orient | Issue text, indexed repository, graph revision | obligations, initial localization | Requirements or ranked locations are supported |
| Research | Search/view result, symbols, graph | definition/reference partition, caller contracts, localization reslot, precedents | Evidence narrows the next inspection |
| Pre-edit | Proposed operation, target path, preimage, graph | change-surface, callers, repository precedent, precondition checks | A specific risk or required companion edit exists |
| Post-edit | Exact before/after artifacts, patch, refreshed graph | syntax, signature delta, callers, new-file precedent, covering-test selection | A defect or concrete impact exists |
| Test | Command, result, affected artifacts, failure identity | covering RED, failure classification, freshness | Failure is attributable and actionable |
| Recovery | Failure identity, edit history, prior hypothesis | repeated-failure governor | The same failure persisted after an intervening edit |
| Verify | Latest patch version, post-edit checks, test evidence | freshness and verification sufficiency | Verification is missing, stale, or RED |
| Submit | Latest patch, unresolved evidence, hygiene, certificate | submit gate, completion certificate, curation | Positive unresolved evidence or dirty completion state exists |

### Pre-edit and post-edit are both required

Pre-edit and post-edit answer different questions:

- **Pre-edit:** what must be understood or preserved before this proposed
  change is executed?
- **Post-edit:** what did the actual patch change, break, or require us to
  verify?

Pre-edit should not predict a patch that has not happened. Post-edit should not
rely on stale pre-edit assumptions. Both must refer to artifact hashes so the
audit can reject evidence computed against an obsolete file version.

## What the research confirms

The design is consistent with primary software-engineering research:

- Agentless demonstrates a deliberately staged
  localization -> repair -> validation process. It supports explicit phase
  separation rather than a flat always-on feature bundle.
  [Agentless](https://arxiv.org/abs/2407.01489)
- SWE-agent reports that a purpose-built agent-computer interface materially
  affects repository navigation, editing, testing, behavior, and performance.
  GT must therefore be wired into the actual tool/request path, not appended
  only to a post-run audit.
  [SWE-agent](https://arxiv.org/abs/2405.15793)
- CodePlan combines incremental dependency analysis, change-impact analysis,
  previous changes, and adaptive planning across repository edits. This
  supports fresh pre-edit impact evidence, post-edit graph refresh, and
  re-evaluation after the real patch.
  [CodePlan](https://arxiv.org/abs/2309.12499)
- Spectrum-based fault-localization research supports ranking compact
  suspicious sets from execution evidence while balancing evidence precision
  and collection cost. This supports ranked, bounded localization rather than
  dumping all potentially related code.
  [Spectrum-based fault localization survey](https://arxiv.org/abs/1607.04347)
- Automated program-repair research describes fault localization, patching,
  and patch validation, and motivates selecting regression tests affected by a
  patch. This supports an attributable covering-test lane.
  [APR regression testing](https://www.cs.purdue.edu/homes/lintan/publications/apr-regression-tosem24.pdf)
- Passing the available or generated tests does not establish semantic
  correctness; test-suite-based repair can overfit. GT should combine
  obligations, caller contracts, patch impact, syntax checks, and behavioral
  tests rather than treating one green result as a universal certificate.
  [Test generation for program repair](https://arxiv.org/abs/1703.00198)

The provenance design also follows mature primary standards:

- W3C PROV supplies Entity, Activity, Agent, usage, generation, derivation, and
  attribution relationships. GT artifacts and evidence can be modeled as
  entities; checks, deliveries, requests, responses, and actions as activities;
  and GT, tools, and Mini-SWE as agents.
  [PROV-O](https://www.w3.org/TR/prov-o/)
- W3C PROV constraints support validating event order and causal consistency.
  The audit should reject impossible chains such as post-edit evidence before
  the edit, verification that predates the latest patch, or a response
  attribution without a bound request.
  [PROV constraints](https://www.w3.org/TR/prov-constraints/)
- OpenTelemetry's operation, span, event, status, and causal-link model is a
  useful pattern for stage and mechanism receipts. It is a design pattern here,
  not a required runtime dependency.
  [OpenTelemetry Trace API](https://opentelemetry.io/docs/specs/otel/trace/api/)
- CloudEvents' typed, uniquely identified event envelope is a useful pattern
  for idempotent lifecycle receipts.
  [CloudEvents specification](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)

These sources support the architecture. They do not prove a GT benchmark delta.
That conclusion requires a live controlled experiment.

## Current live evidence

### Historical comparison in the handoff

| Run | State | Direct-delivery result | Interpretation |
|---|---|---|---|
| `30478454517` (`smoke5`) | Pre-fix | 1/17 delivering | Major delivery and observability gaps |
| `30507453355` (`smoke6`) | Post F1-F3 | 3/17 delivering; invoked absences named | Attribution improved; many triggers absent |
| `30510979443` (`smoke7`) | Success, 5/5 workflow tasks, 0 workflow failures; commit `4b8df3c87` | Audit target in the handoff | Commit/substrate parity held |

The handoff also records that task rewards were 0/5 for smoke5 and smoke6
because the agents hit the 150-step cap. Workflow success is not SWE task
success, and feature delivery is a separate measurement axis from both.

### Later supplied audit snapshot

The later live-run snapshot for `30567497685` reports:

- lifecycle checkpoints: task start 5, research 136, pre-edit 100, post-edit
  67, test 35, verify 10, submit 10;
- 7 of the 17 direct identities witnessed:
  `GT_CERT_DELIVERY`, `GT_EDIT_CHECK`, `GT_LOC_RESLOT`,
  `caller_contract`, `localization`, `obligations`, and `submit_refusal`;
- all 17 identities received terminal states, with no dark, fault, or
  unexposed state;
- 20 provider-confirmed deliveries and 4,334 attribution rows; and
- 6 of 7 witnessed identities had an action-consistent next response;
  `GT_EDIT_CHECK` was quiet on clean checks.

This is evidence that the lifecycle boundaries and direct attribution model
work. It is not evidence that the complete control plane is covered. The same
snapshot exposes control decisions for only a small subset of active controls,
so the remaining gates, mediators, and behavior switches still need terminal
receipts.

## Current gaps that prevent a complete claim

1. **L4 is partial.** Gateway producers provide some localization, caller, and
   precedent evidence, but the historical prefetch/endpoint surface is not
   represented as a complete, independently audited suite.
2. **L5/L5b is partial.** Repeated-failure recovery and submit refusal exist,
   but the broader trajectory governor is not fully represented.
3. **Hygiene/curation is missing.** Submit currently passes `hygiene=None`.
4. **Control-plane receipts are incomplete.** Most active eligibility,
   mediator, and behavior controls are not given an independently auditable
   terminal outcome.
5. **L3b evidence is consolidated.** Caller, peer, similar-pattern, and
   navigation evidence maps to canonical identities, which is valid, but
   sub-capability coverage is not separately visible.
6. **Graph refresh success is under-attested.** The refresh path is invoked;
   an explicit receipt should prove which graph revision resulted.
7. **Live opportunity coverage is limited.** A five-task smoke cannot prove a
   feature whose trigger did not occur. The task set must be selected from
   structured historical actions to create genuine trigger opportunities.
8. **Behavioral benefit is not yet causal.** Action consistency is stronger
   than delivery proof, but weaker than a matched GT-off counterfactual.

## Implementation order

### P0: Make the inventory truthful

- Add the unified capability manifest.
- Preserve role distinctions.
- Map every alias to one canonical identity.
- Define trigger inputs and intended action.
- Validate that every feature has one owner and one stage contract.

This improves measurement and prevents double counting. It does not yet make
the engine behaviorally stronger.

### P1: Make lifecycle execution atomic and auditable

- Implement stage transactions.
- Issue terminal receipts for all applicable mechanisms.
- Bind every receipt to artifact and graph revisions.
- Add a single arbiter and named suppression.
- Preserve correct-or-quiet behavior for clean checks.

This is the first phase that directly strengthens the engine.

### P2: Restore missing historical value

- Complete L4 change-surface/prefetch coverage.
- Expand L5/L5b loop and premature-finish controls using positive evidence.
- Add submit hygiene/curation.
- Add explicit graph-refresh success receipts.
- Expose sub-capability coverage without inventing new top-level FACTs.

### P3: Prove behavior in live runs

- Select tasks with structured, pre-existing trigger opportunities.
- Audit every opportunity, terminal outcome, delivery, provider binding, and
  next action.
- Run matched GT-off, current-GT, and unified-GT comparisons.
- Add suite ablations only after the integrated system is stable.

## Live-run proof protocol

The strongest feasible comparison uses three arms:

1. **GT off:** existing baseline.
2. **Current GT:** current lifecycle plus 17-identity attribution.
3. **Unified GT:** stage transactions, full receipts, arbiter, and restored
   missing suites.

Hold constant:

- identical task IDs;
- identical Mini-SWE code and tool interface;
- bare provider-native `deepseek-v4-flash`;
- explicit temperature 1;
- identical timeout and step cap;
- Terminal-Bench/Harbor concurrency 4;
- identical substrate and GT commit within an arm;
- identical prompt and provider settings; and
- repeated trials or an explicit statement that stochastic single runs are not
  replay-equivalent.

The current trigger-oriented smoke configuration already identifies five tasks
from structured GT-off tool-call/tool-result pairs. It targets indexed success,
change-surface, unresolved-RED submit, and search-partition opportunities. Its
acceptance target is at least 7 live-witnessed identities and at least 12
specifically exercised identities, zero triggered-dark identities, zero
telemetry faults, and a provider-final receipt for every delivery. “Exercised”
means that the identity has a specific evaluation or terminal outcome; merely
appearing in the 17-row census as `no_trigger_observed` does not count.

### 2026-07-30 live audit and corrective finding

Run `30571573718` executed the five-task trigger-oriented smoke on commit
`3e30b62`, bare `deepseek-v4-flash`, explicit temperature 1, Profile 2,
concurrency 4, and timeout multiplier 1.0. The run was diagnostically useful
but did not pass the acceptance gate:

- all 5 task executions were healthy and 3/5 received reward 1;
- 18 sealed deliveries were byte-bound to their immediate provider requests
  and linked responses;
- all seven SDLC checkpoints were observed;
- all 17 identities received a named terminal state;
- there were zero triggered-dark identities, telemetry faults, unexposed
  deliveries, dose violations, or hash-chain failures;
- 7 identities were witnessed and 6 were action-consistent; and
- the gate correctly failed because 7 witnessed identities were below the
  required 9.

The opportunity census then exposed a real wiring defect. `headless-terminal`
created a new file, but `newfile_precedent` and `GT_CHANGE_SURFACE` did not
trigger. The workflow explicitly set `GT_RL_PROFILE=2`; the bridge resolved
explicit profiles with the inventory-only resolver, which omitted all seven
Profile-2 behavior switches. The local tests had removed GT environment
variables and therefore exercised the unset-profile path, masking the defect.

Commit `5e37407` corrects the fan-out and adds a names-only activation receipt.
The live gate now requires every expected Profile-2 control and these seven
behavior switches on every task:

- `GT_CS_EDIT_TRIGGER`;
- `GT_SS_EDIT_PREVENTIVE`;
- `GT_INFRA_NOISE_GUARD`;
- `GT_HYP_CONTRA_GUARD`;
- `GT_RECOVERY_ESCALATE`;
- `GT_OBLIG_STEER_GUARD`; and
- `GT_ROLE_DRIVEN_COALITION`.

The deterministic verification after the fix is 14/14 canonical trigger tests
and 266 passed repository tests (two Windows-only capability skips). An actual
indexed new-file edit under explicit Profile 2 now produces both
`newfile_precedent` and `GT_CHANGE_SURFACE`. This proves the trigger path and
prevents the earlier configuration from passing silently; the confirmation
live run remains the required proof of provider-bound execution.

Run `30573558407` then confirmed the Profile-2 activation fix on commit
`5e37407`. Every task recorded all 53 expected controls active, no missing
controls, and all seven behavior flags. It also recorded 20 sealed deliveries,
7 witnessed identities, 13 specifically exercised identities, 6
action-consistent identities, all seven lifecycle stages, a complete 17-state
census, temperature 1, and zero dark, faulted, unexposed, dose-invalid, or
hash-invalid deliveries. Three of five tasks received reward 1.

That run still failed acceptance for one independent reason:
`crack-7z-hash` reached Harbor’s 900-second agent timeout. The run also showed
why the former “9 witnessed” target was unsound. `headless-terminal` created a
new file, but the change-surface producer found no nonredundant registration,
companion, or destination advice after creation and correctly stayed quiet.
Forcing two positive deliveries from that event would reward false or redundant
advice.

The corrected proof contract therefore separates:

- identities live-witnessed through a delivery or deterministic capability
  receipt (`min_witnessed=7`);
- identities that were actually evaluated or reached a specific terminal
  outcome (`min_exercised=12`); and
- identities whose trigger never occurred, which remain honestly ineligible.

The bridge now emits an explicit correct-quiet receipt when the new-file edit
trigger executes but yields no useful repository precedent. The default smoke
replaces the timeout-prone `crack-7z-hash` trajectory with
`reshard-c4-data`; it does not weaken the timeout multiplier or hide unhealthy
tasks. Post-change deterministic verification is 15/15 canonical trigger tests
and 268 passed repository tests (the same two Windows-only capability skips).

### Accepted live proof: run 30575786568

Run `30575786568` passed the full live gate on commit `051eedb` with the
following frozen configuration:

- tasks: `build-cython-ext`, `headless-terminal`,
  `llm-inference-batching-scheduler`, `reshard-c4-data`, and
  `sanitize-git-repo`;
- model: bare `deepseek-v4-flash`;
- temperature: 1;
- Profile 2;
- Harbor concurrency: 4; and
- agent timeout multiplier: 1.0.

All five task executions were healthy and audited `GREEN-delivered`; four
received reward 1. The run produced 18 sealed GT deliveries. Every delivery’s
exact byte hash was present in the immediate provider-final request and linked
model response. The aggregate live result was:

| Measure | Result |
|---|---:|
| Complete feature census | 17/17 on every task |
| Active Profile-2 controls | 53/53 on every task |
| Active behavior flags | 7/7 on every task |
| Exercised identities | 16/17 |
| Live-witnessed identities | 9/17 |
| Action-consistent identities | 8/17 |
| Lifecycle stages observed | 7/7 |
| Sealed deliveries | 18 |
| Triggered-dark identities | 0 |
| Telemetry faults | 0 |
| Unexposed deliveries | 0 |
| Gate issues | 0 |
| Tasks rewarded | 4/5 |
| Mini-SWE iterations | 209 |
| Provider input tokens | 6,171,645 |
| Provider output tokens | 97,604 |
| Sealed GT characters | 5,670 |

The nine live-witnessed identities were `obligations`, `localization`,
`caller_contract`, `signature_delta`, `submit_refusal`, `GT_LOC_RESLOT`,
`GT_EDIT_CHECK`, `GT_PATCH_DELTA`, and `GT_CERT_DELIVERY`.

Eight of those identities owned or received provider-bound delivery bytes;
`GT_EDIT_CHECK` was witnessed through five clean deterministic capability
receipts and correctly emitted no model bytes for those clean results.

The correct-quiet path was also proven live. `headless-terminal` and
`reshard-c4-data` each created a file; both `newfile_precedent` and
`GT_CHANGE_SURFACE` recorded `producer_abstained_correct_quiet` because no
nonredundant post-creation precedent was available. This is a successful
execution, not a missing feature and not a fabricated delivery.

The locally re-run auditor reproduced the downloaded artifact exactly except
for the serialized `run_dir` path (GitHub runner path versus local download
path). This result supports the wiring and immediate behavior claims. It does
not, by itself, establish a causal solved-rate or token-cost improvement over
GT-off because the existing baseline used a different timeout multiplier and
implicit temperature.

### Complete-contract and graph-context diagnostic: run 30582455019

Run `30582455019` executed commit `4694257` with the same five GT tasks, bare
`deepseek-v4-flash`, temperature 1, Profile 2, concurrency 4, and timeout
multiplier 1.0. The workflow and the independently rerun local gate both
passed. This is a positive wiring result and a negative outcome result: only
two of five tasks received reward 1.

This run added four proof surfaces that the earlier accepted run did not have:

- a graph-independent complete task contract at task start;
- a receipt for all 14 trustworthy `graph.db` surfaces;
- a task projection over lexical, symbol-body, passage, edge, closure,
  property, assertion, and co-change surfaces; and
- obligation-mapped verification plus role/relevance admission receipts.

The task contract is not a replacement for the original prompt. It is a
bounded deterministic checklist carried from task start into the penultimate
verification boundary. Every one of the five task-start checklists was
provider-confirmed byte-for-byte.

| Task | Role | Contract shipped | Verified at last submit | Graph at task start | Router suppressed | Reward |
|---|---|---:|---:|---|---:|---:|
| `build-cython-ext` | code behavior | 7/7 | no submit reached | no | 0 | 0 |
| `headless-terminal` | code behavior | 7/7 | 7/7 | yes | 0 | 1 |
| `llm-inference-batching-scheduler` | data transform | 16/16 | 13/16 | yes | 0 | 0 |
| `reshard-c4-data` | data transform | 13/13 | 11/13 | no | 0 | 1 |
| `sanitize-git-repo` | content scan | 7/7 | 0/7 | yes | 13 | 0 |

`build-cython-ext` starts with no repository and clones it during the
trajectory. `reshard-c4-data` also begins without an indexable source tree.
Their task-start graph receipts are therefore honestly unavailable rather than
silently treated as empty evidence. Both later exercised the graph refresh and
verification-plan control after edits. This exposes a remaining gap: the
task-level projection and relevance router are not rebuilt after a dormant
graph wakes.

The three graph-present tasks proved different graph shapes:

| Task | Nonempty stored surfaces | Task projection |
|---|---:|---|
| `headless-terminal` | 8/14 | 1 file, 2 symbols, 2 nodes; edge, FTS, and property hits |
| `llm-inference-batching-scheduler` | 11/14 | 2 files, 16 symbols, 16 nodes; passage, body, edge, closure, and property hits |
| `sanitize-git-repo` | 14/14 | 40 files, 64 symbols, 80 nodes; passage, edge, closure, property, assertion, and co-change hits |

“Use everything in `graph.db`” must mean inventory every trustworthy surface
and query the surfaces relevant to the current task. It must not mean dumping
the whole database into the prompt. Counts and revision receipts prove surface
availability; bounded projections feed routing and verification; the global
arbiter still owns the one-dose model-facing budget.

#### Per-task live feature result

All 17 canonical identities were censused on every task. Sixteen had a
specific evaluation or terminal outcome across the run, nine were
live-witnessed across the run, and no eligible identity went dark. Per-task
live-witnessed identities were:

| Task | Witnessed | Identities |
|---|---:|---|
| `build-cython-ext` | 5 | `obligations`, `localization`, `caller_contract`, `GT_LOC_RESLOT`, `GT_EDIT_CHECK` |
| `headless-terminal` | 4 | `obligations`, `localization`, `GT_LOC_RESLOT`, `GT_EDIT_CHECK` |
| `llm-inference-batching-scheduler` | 7 | `obligations`, `localization`, `caller_contract`, `submit_refusal`, `GT_LOC_RESLOT`, `GT_EDIT_CHECK`, `GT_CERT_DELIVERY` |
| `reshard-c4-data` | 8 | `obligations`, `localization`, `signature_delta`, `submit_refusal`, `GT_LOC_RESLOT`, `GT_EDIT_CHECK`, `GT_PATCH_DELTA`, `GT_CERT_DELIVERY` |
| `sanitize-git-repo` | 6 | `obligations`, `localization`, `submit_refusal`, `GT_LOC_RESLOT`, `GT_EDIT_CHECK`, `GT_CERT_DELIVERY` |

This is the correct interpretation of “features working.” It is not valid to
claim 17 deliveries per task. Features whose positive trigger did not occur
must remain named `INELIGIBLE`.

Lifecycle timing was also task-specific:

| Task | start | research | pre-edit | post-edit | test | verify | submit |
|---|---:|---:|---:|---:|---:|---:|---:|
| `build-cython-ext` | 1 | 58 | 29 | 28 | 6 | 0 | 0 |
| `headless-terminal` | 1 | 12 | 8 | 5 | 6 | 2 | 2 |
| `llm-inference-batching-scheduler` | 1 | 31 | 6 | 2 | 7 | 1 | 1 |
| `reshard-c4-data` | 1 | 25 | 18 | 14 | 2 | 2 | 2 |
| `sanitize-git-repo` | 1 | 26 | 4 | 4 | 0 | 2 | 2 |

The missing build submit/verify stages are not a telemetry defect: the model
hit the 100-iteration cap before attempting completion. Sanitization performed
shell checks, but none qualified as a trustworthy test mapped to the complete
contract, so its verified count correctly remained zero.

#### Existing GT-off comparison

The available GT-off observations used the same model and tasks but timeout
multiplier 2.0 and implicit temperature. The GT-on diagnostic used timeout
multiplier 1.0 and explicit temperature 1. `headless-terminal` GT-off exited
137 after seven iterations and was not graded. Consequently these are
descriptive observations, not causal GT deltas.

| Task | Reward off -> on | Iterations off -> on | Input tokens off -> on | Output tokens off -> on | Tool results off -> on | Tool errors off -> on |
|---|---:|---:|---:|---:|---:|---:|
| `build-cython-ext` | 1 -> 0 | 82 -> 100 | 2,680,318 -> 3,985,588 | 21,094 -> 22,903 | 114 -> 128 | 15 -> 19 |
| `headless-terminal` | ungraded -> 1 | 7 partial -> 39 | 25,235 partial -> 614,460 | 5,005 partial -> 19,881 | 8 partial -> 43 | 0 partial -> 9 |
| `llm-inference-batching-scheduler` | 1 -> 0 | 21 -> 100 | 557,372 -> 5,043,318 | 33,398 -> 90,001 | 24 -> 109 | 1 -> 17 |
| `reshard-c4-data` | 1 -> 1 | 57 -> 71 | 1,350,848 -> 2,754,622 | 20,980 -> 39,963 | 67 -> 79 | 0 -> 5 |
| `sanitize-git-repo` | 1 -> 0 | 35 -> 32 | 1,296,069 -> 808,960 | 14,902 -> 8,276 | 76 -> 30 | 18 -> 1 |

Across the four graded GT-off pairs, GT-on changed:

| Metric | GT-off | GT-on | Observed delta |
|---|---:|---:|---:|
| Rewarded tasks | 4/4 | 1/4 | -3 tasks |
| Iterations | 195 | 303 | +55.4% |
| Input tokens | 5,884,607 | 12,592,488 | +114.0% |
| Output tokens | 90,374 | 161,143 | +78.3% |
| Tool results | 281 | 346 | +23.1% |
| Tool errors | 34 | 42 | +23.5% |

The result does **not** support an efficiency or solved-rate benefit claim.
Compared with the immediately preceding matched-config GT-on smoke, reward
also fell from 4/5 to 2/5, iterations rose from 209 to 342, input tokens rose
from 6,171,645 to 13,206,948, and output tokens rose from 97,604 to 181,024.
Temperature-1 sampling means one run cannot isolate causality, but the
direction and magnitude forbid a positive claim.

#### Exact failure diagnosis

- `build-cython-ext` left `np.int` in `ccomplexity.pyx`; the hidden verifier
  failed that compiled extension. The model hit the iteration cap before
  submission. Six caller-contract deliveries and two trace frames did not
  prevent the omission.
- The batching scheduler missed only bucket 2's sequential-time threshold:
  `36,664,059.48` versus the required `32,000,000`. The complete numeric
  contract was shipped, but after a 13/16 verification refusal the model used
  the remaining budget without resubmitting.
- Sanitization explicitly excluded `exp_data/` from its supposedly
  repository-wide searches. The remaining HF token was in the contaminated
  JSON there. The router then suppressed 13 localization candidates as
  ungrounded in that narrowed search. This is over-suppression: graph-grounded
  content-scan evidence must be allowed to challenge an agent's incomplete
  search scope.

The run therefore proves complete-contract shipping, graph inventory,
projection execution, role routing, provider attribution, and lifecycle
timing. It simultaneously falsifies the claim that those changes already make
GT more efficient or sufficient. The next implementation must refresh task
projection when a graph wakes, admit graph-grounded content-scan evidence, and
make repository-wide scope exclusions a deterministic pre-submit concern
before spending another five-task smoke.

### Timely-context live result and lifecycle-control follow-up

Run `30606642296` is the current timing proof for commit `58655d4`. All three
tasks eligible for task-start localization received the compound
obligations/localization block in provider iteration 1 and response iteration
1. Across five tasks, 10 canonical identities were witnessed, 15 were
exercised, and no identity was dark, faulted, or unexposed. The immutable
artifact passes the corrected audit and live gate; its original audit failure
was a parser false positive for commands that explicitly excluded `.gt`.

That run is not outcome proof. On the four frozen-comparable tasks, reward fell
from 4/4 to 2/4 and iterations rose from 195 to 347. Input tokens fell 67.7%,
but output tokens rose 63.9%. The batching task never created its required
`plan_b1.jsonl` and `plan_b2.jsonl` outputs, while two passing tasks continued
to iteration 100. Deterministic context was cheaper but insufficiently
controlling.

The follow-up adds lifecycle controls around—not new members of—the canonical
17:

| control state | exact trigger | model-visible action | proof |
|---|---|---|---|
| `artifact_completion` | at least 50% of the iteration budget and a contract-scoped required artifact is absent | names exact missing paths; directs creation plus executable check | `progress.control_issued` with mode and iteration |
| `verified_completion` | a post-edit behavioral check is GREEN and no later edit/current RED exists | stops broad research; names only unmet requirements; directs finish | same |
| `finalization` | at least 80% of the iteration budget | names requests remaining and the smallest unresolved check/artifact; forbids repeated search | same |

Each mode fires at most once. Missing or present artifact state is advisory and
cannot mint a verification receipt. The checkpoint also retains bounded
unresolved obligation text, predicate kind/scope, remaining iterations, and
the last concrete action. Graph ranking now weights distinctive anchors and
down-ranks words repeated across many obligations, preventing generic overlap
from masquerading as task localization.

Run `30608738489` proved that those lifecycle controls were live but not yet
enforceable. Six controls fired at their specified iterations; headless stopped
at iteration 53, but batching ignored the artifact directive and still ended
without either required plan. The sanitizer agent also directly inspected
`.gt`, making the run an isolation failure even though its task verifier
passed.

The corrected lifecycle implementation therefore moves `graph.db` outside the
repository and adds a GT-only pre-dispatch tool policy. Harness-path access is
rejected and receipted; artifact-completion and finalization states reject
unrelated repository observation while preserving output/input access,
edits, tests, and targeted reads named by a fresh failure. These are lifecycle
controls, not additional canonical features and not semantic-delivery claims.

### Required engine metrics

| Metric | Required interpretation |
|---|---|
| Stage coverage | Did each stage that had an opportunity execute? |
| Mechanism opportunity coverage | Which triggers actually occurred? |
| Terminal-state coverage | Did every applicable mechanism end in a named state? |
| Provider-bound delivery | Did exact sealed bytes reach the immediate provider request? |
| Response linkage | Did that request produce the linked response? |
| Action consistency | Was the immediate next action compatible with the evidence? |
| False intervention rate | Did GT deliver irrelevant, stale, or unsupported advice? |
| Dose and suppression | Did arbitration prevent duplicate or lower-value deliveries? |
| Artifact freshness | Was evidence computed from the latest relevant patch and graph? |

### Required outcome and cost metrics

- solved tasks and reward;
- provider input, output, and cache tokens;
- provider calls and Mini-SWE iterations;
- edit operations and unique changed files;
- tests and verification commands;
- repeated identical failures;
- submit attempts and refusals;
- wall time; and
- task-level paired deltas, not only aggregate totals.

### Acceptance for the unified engine

A live run supports a **wiring claim** when:

- every observed lifecycle opportunity has a stage receipt;
- every applicable mechanism has one terminal outcome;
- no triggered mechanism is dark;
- no terminal producer receipt is missing;
- every delivery is byte-proven in the provider-final payload;
- every delivery is linked to the immediate model response;
- artifact and graph revisions are fresh; and
- every suppression has a specific reason.

A live run supports a **behavior claim** when, in addition:

- immediate actions are consistent with delivered evidence;
- interventions are not stale, duplicative, or unsupported; and
- the behavior expected at each trigger occurs at the correct time.

A live experiment supports a **benefit claim** only when matched comparisons
show an improvement in solved rate, cost, iterations, repeated failures, or
verification quality. One stochastic five-task smoke can demonstrate wiring
and examples; it cannot establish a general performance effect.

## Failure modes to prevent

| Failure mode | Required control |
|---|---|
| All features forced to fire | Opportunity-based eligibility and `APPLIED_QUIET` |
| Prompt flooding | One arbiter; at most one intervention per boundary |
| Alias double counting | Canonical identity mapping |
| Owner counted as a separate semantic fact | Preserve FACT/owner role distinction |
| Stale post-edit advice | Artifact and graph revision binding |
| Weak evidence blocks submission | Positive-evidence requirement and bounded single refusal |
| Green syntax treated as full correctness | Multi-technique verification |
| Trajectory mistaken for delivery proof | Provider-final byte receipt |
| Stringification misses block-list messages | Structural payload traversal and byte hashes |
| Workflow success mistaken for solved task | Report workflow, task reward, and attribution separately |
| Feature absence mistaken for failure | Named `INELIGIBLE` with trigger inputs |
| Missing telemetry mistaken for clean execution | `FAULT`, never `APPLIED_QUIET` |

## Final decision

Proceed with the unified design, but define success precisely:

- The historical layers become lifecycle stages.
- The canonical FACTs remain semantic evidence.
- Capability owners remain byte and behavior owners.
- Eligibility gates and mediators remain control-plane mechanisms.
- Every stage runs applicable deterministic checks against fresh state.
- Every mechanism receives a terminal receipt.
- One arbiter controls the visible dose.
- Provider-bound provenance proves what GT actually sent and when.

That architecture makes GT more powerful as an SDLC engine. Merely combining
names into a larger "feature list" does not.
