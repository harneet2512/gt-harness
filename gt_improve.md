# GT Improvement Plan: From Attributable Wiring to Measured Advantage

Status: research complete; implementation must follow the ranked gates below
Repository: `gt-harness`  
Runtime: nano-harness with GroundTruth (GT)  
Last diagnosed live run: `30597263179`, commit `530668f`
Latest code under consideration: `6362926` (harness isolation, not live-proven)
Confidence: high on the dominant causes; moderate on the expected effect of
each proposed correction until replay ablations and repeated GT-on trials
establish it.

## Decision

GT is a deterministic evidence and control engine for nano-harness. It is not
the coding agent and must not perform open-ended reasoning. Nano owns ideation,
planning, code generation, and tool choice. GT must improve those activities by
supplying bounded repository evidence and enforcing checkable SDLC invariants
at the decision point where they matter.

The current engine has crossed the attribution threshold: sealed GT bytes can
be proved in the provider-final request, linked to the response, and associated
with the next action. It has not crossed the utility threshold. The latest run
solved three of five tasks. Its two failures show that correct wiring and valid
graph provenance do not guarantee useful evidence, semantic verification, or
effective stopping.

The objective is therefore:

> Improve solved tasks per provider token and per iteration while preserving
> correct-or-quiet evidence, exact provider attribution, and lifecycle timing.

No paid smoke is justified merely because a feature path exists. Offline,
replay, integration, audit, and configuration gates must pass first. The final
candidate proof must then be a real nano + GT provider run, not an offline or
pre-modelled simulation.

No new GT-off execution is part of this plan. The existing GT-off artifacts are
the frozen comparison source. All implementation, replay, and new live
execution below are GT-on only.

## Implementation status — 2026-07-30

The first implementation slice from this plan is now in the working tree.
This is not yet a live superiority claim.

Implemented:

- numeric predicates parse scientific notation, preserve the required
  comparator and unit, and refuse credit when the measured value is on the
  wrong side of the bound or omits the required unit;
- repository-wide negative content searches recognize the truthful
  `rg`/`grep` no-match status, reject excluded or narrow scopes, and can
  produce a content-scope receipt after the latest edit;
- predicate receipts carry measured value, operator, required value, unit,
  observation action, latest-edit action, and command/output hashes;
- a positive submit blocker remains authoritative for nano's existing three
  pushbacks, with a unique truthful refusal on each attempt;
- the selected role pack is enforced by the router; content tasks suppress
  caller noise, while code and data tasks retain valid definition partition
  and new-file precedent evidence;
- malformed new-file entities and task-irrelevant localization are suppressed
  with named reasons;
- graph FTS anchors are ordered by explicit subject, obligation coverage, and
  specificity rather than alphabetical first-24 truncation;
- ranked FTS rows retain surface, confidence, and graph revision as semantic
  facts;
- a shared deterministic `EvidenceNeed`/`GraphEvidence` layer links a bounded
  graph slice to unresolved obligations or the active edited target;
- graph evidence ranking runs at task start and atomically after graph
  refresh, with content-safe provenance and revision receipts, and its ranked
  file slice constrains subsequent graph-localization admission;
- a first attributable required RED near 80% of the iteration budget receives
  one bounded recovery intervention and outranks advisory localization, while
  the original repeated-failure-after-edit recovery remains; and
- audit/live-gate schemas reject invalid semantic predicate receipts,
  unlinked graph evidence, and stale graph-evidence revisions; and
- utility receipts expose freshness, unresolved relevance, expected
  information gain, repetition, token/interruption cost, and false-positive
  risk instead of only a static severity/confidence score.

Local proof now covers false numeric credit, unit mismatch, complete versus
excluded content scope, role-pack enforcement, malformed entity extraction,
good and bad localization, graph-query priority, decision-linked graph
evidence, repeated-submit authority, and near-budget recovery.

### First strict live candidate: run `30594350673`

Run `30594350673` executed the real five-task nano + GT workflow at commit
`20e4925` with `deepseek-v4-flash`, temperature `1`, Profile 2, concurrency 4,
and timeout multiplier `1.0`. Harbor completed all five live provider trials
with no trial error. Reward remained 3/5, but the solved-task composition
changed:

| Task | Reward | Iterations | Input tokens | GT chars | Result versus run `30590129776` |
|---|---:|---:|---:|---:|---|
| build | 0 | 75 | 2,290,305 | 6,882 | regressed; hidden verifier found `np.int` |
| headless | 1 | 37 | 410,075 | 2,329 | still solved, but more expensive |
| batching | 1 | 40 | 1,329,623 | 4,258 | improved from fail at 74 / 4,739,587 |
| reshard | 0 | 57 | 810,661 | 3,276 | still failed; root had 331 entries, over 30 |
| sanitizer | 1 | 45 | 1,797,776 | 2,479 | still solved, but much more expensive |

This is a real positive result for batching, not a general GT advantage.
Across the four tasks with graded frozen GT-off observations (build, batching,
reshard, sanitizer), GT-off solved 4/4 while this candidate solved 2/4. The
same four tasks used:

| Metric | Frozen GT-off | Run `30594350673` | Delta |
|---|---:|---:|---:|
| Iterations | 195 | 217 | +11.3% |
| Input tokens | 5,884,607 | 6,228,365 | +5.8% |
| Output tokens | 90,374 | 103,098 | +14.1% |

Therefore the candidate is worse than the existing baseline despite the large
batching improvement. Deterministic evidence is not enough; it must reduce
decision uncertainty. The main live defects were:

- generic `verification_missing` refusals repeated three times even when the
  unresolved set was unchanged, adding work without adding information;
- build had no positive NumPy-2 removed-alias check, so `np.int` survived;
- reshard's generic refusal hid the high-risk `30 entries` and `15MB`
  numeric constraints behind earlier prose obligations;
- sanitizer's explicit passing absence suite did not satisfy content-scope
  predicates, so the same seven unknowns repeated;
- all 21 rejected sanitizer localizations were named by the router but the
  census misclassified the canonical localization feature as dark; and
- reshard had one shell-process death with no active GT delivery, then a
  successful bash observation. The event was recovered but the gate treated
  any historical shell death as terminal.

The post-run correction now:

- suppresses an unchanged generic verification refusal after its first
  delivery, while positive syntax, covering, observed-RED, and NumPy blockers
  retain the full bounded authority;
- recognizes explicit passing repository-absence suites;
- prioritizes numeric/content/artifact unknowns in the smallest refusal set;
- executes a bounded positive NumPy-2 removed-alias source scan at submit;
- attributes router suppression to the canonical evidence feature; and
- records recovered versus unrecovered shell lifecycle failures, failing the
  live gate only on an unrecovered shell.

Replaying the immutable run through the corrected measurement code yields a
clean attribution gate: seven identities witnessed, 15 exercised, zero dark,
zero unexposed, zero stale/unlinked graph facts, and one recovered shell event.
That fixes the measurement; it does not retroactively improve the 3/5 reward.

## Current evidence

Run `30590129776` used `deepseek-v4-flash`, temperature `1`, Profile 2,
concurrency `4`, timeout multiplier `1.0`, and the exact five-task workflow
slice. Workflow, Harbor, provider attribution, feature census, lifecycle,
capsule expiry, graph refresh, and strict live gates passed.

It proved:

- reward was 3/5: build, headless, and sanitizer passed; batching and reshard
  failed;
- all five task contracts reached the provider-final request byte-for-byte;
- all 22 delivered capsules were unique, exposed once, and expired;
- 10/17 identities were provider-witnessed and 15/17 were exercised with a
  named outcome;
- no eligible identity went dark;
- every task recorded task-start, research, pre-edit, post-edit, test, verify,
  and submit boundaries;
- 44 graph refreshes completed without a refresh fault;
- the final per-task projections contained 373 semantic-fact records;
- all 277 tool outcomes were classified: 247 success, 13 useful RED, 8 product
  failure, 6 dependency/environment, 2 tool-contract, 1 agent-command, and no
  persistent-shell lifecycle failure.

The result is a wiring success and an efficacy failure. Batching missed one
numeric performance bound and reshard timed out only on the independently
generated hidden workload.

## Deep delivery audit: what GT actually sent

### Evidence channels

GT does not currently produce one unified deterministic research result. The
22 deliveries came from four disconnected channels:

| Channel | Deliveries | Characters | How it is produced |
|---|---:|---:|---|
| Issue/task contract | 5 | 5,581 | Normative issue extraction; no graph required |
| Direct graph producers | 10 | 5,155 | Ranked localization, caller queries, and new-file precedent |
| Tool observation | 2 | 132 | Deepest repository stack frame from the immediate failure |
| Verification/control state | 5 | 2,473 | Unresolved RED or missing predicate state at submit |
| **Total** | **22** | **13,341** | Exact sealed provider-delivered bytes |

“Research” in lifecycle telemetry means that nano searched or viewed something.
It does not mean GT synthesized the graph, task requirements, current patch,
recent failures, and verification state into a research conclusion.

### Every live delivery

Quality below measures whether the bytes were timely, discriminative, and
actionable. The linked response is association, not proof of causality.

| Task/action | Delivery | Basis | Quality | Delivery-byte diagnosis and linked response |
|---|---|---|---|---|
| build/0 | `obligations` | issue | Medium | Correct checklist, but fenced README code is excluded, so the required executable example is named but not preserved; nano began inspection |
| build/35 | `localization` | graph | Low | Ranked catalogue/setup/general Python symbols instead of Cython/NumPy compatibility surfaces; nano read other files |
| build/36 | `caller_contract_view` | graph | Medium | Valid caller facts for `invariants.py`, but 1,685 characters covered a broad API surface without tying it to the active NumPy failure; nano edited two files |
| build/46 | `caller_contract_view` | graph | Medium-high | Exact callers and unpacking behavior for `openknot.py`; nano immediately edited the target |
| build/54 | `caller_contract_view` | graph | Medium-high | Bounded caller constraints for `representation.py`; nano immediately edited the target |
| build/88 | `submit_refusal` | verification | High | Correctly named an unresolved observed RED; nano ran another verification command and the task passed |
| headless/0 | `obligations` | issue | High | Compact, task-specific interface and behavior checklist; nano began repository inspection |
| headless/1 | `localization` | graph | High | Correctly identified `BaseTerminal` at the first research boundary; the next response continued shell inspection |
| headless/7 | `trace_frame` | observation | Medium | Correct failing file and line, but only the frame was sent—not exception meaning, relevant values, or graph neighborhood |
| headless/20 | `submit_refusal` | verification | Medium | Correctly identified incomplete behavioral evidence, but the next action read files rather than executing the missing checks; the task later passed |
| batching/0 | `obligations` | issue | Medium | Complete-looking but flat checklist; thresholds are prose/table fragments rather than typed inequalities and mojibake is present |
| batching/1 | `caller_contract_view` | graph | Low-medium | Correct `align()` caller, but mostly redundant with the just-viewed files and not connected to the active optimization decision |
| batching/20 | `missing_role_postcreate:implementation` | graph + issue | Low | Sibling files were useful, but entity extraction produced malformed text beginning `2.jsonl...`; this should have abstained |
| batching/31 | `trace_frame` | observation | Medium-high | Exact failing `_plan_for_requests` frame; nano immediately edited that target, but the exception semantics were absent |
| batching/72 | `submit_refusal` | verification | High | Correctly named six unresolved requirements and triggered three checks |
| batching/73 | `localization` | graph | Medium content, low timing | Correctly named optimized/baseline/cost-model files, but only after the first submit at action 73; two later checks were RED and the second submit still passed |
| reshard/0 | `obligations` | issue | Medium | Good functional contract, but “similarly sized and distributed” remained prose rather than a scaling/data-distribution predicate |
| reshard/37 | `localization` | graph | Medium | Correct scripts, but delivered after both scripts existed and after substantial implementation; nano ran a target-related command |
| reshard/58 | `submit_refusal` | verification | Medium-high | Correctly demanded executable verification and triggered a full local round trip; GT could not judge whether the sample represented the hidden distribution |
| sanitize/0 | `obligations` | issue | High | Clear repository-wide replacement, preservation, and non-contamination invariants |
| sanitize/1 | `localization` | graph | Low | Returned unrelated symbols/files rather than contaminated locations or repository scope; nano continued a broad shell search |
| sanitize/29 | `submit_refusal` | verification | Medium | Correctly requested repository-wide proof, but reported all seven requirements generically even though the eventual patch passed |

Of the ten graph-derived deliveries, only three linked next responses explicitly
referenced the delivered target. That classifier is intentionally conservative,
but the payload audit confirms the larger defect: graph provenance is often
valid while task utility is weak.

### Why “373 graph facts” did not become 373 useful facts

`gt_engine.graph_context.build_graph_projection` now reads all 14 trusted
surfaces and creates semantic facts for properties, assertions, edge metadata,
file hashes, and project metadata. The bridge atomically rebuilds the projection
and evidence router after graph wake/refresh. The earlier claim that wake keeps
an old projection/router is no longer true.

However:

1. `GraphProjection.semantic_facts` is counted in telemetry but is not supplied
   to GroundTruth Gateway producers or rendered into model-facing evidence.
2. Gateway producers independently query `graph.db`; the router receives only
   flattened file and symbol sets plus a revision.
3. The projection query alphabetically sorts lexical tokens and takes the first
   24, rather than ranking anchors by obligation importance, current target, or
   failure relevance.
4. Test nodes are excluded from primary FTS seeds, weakening pre-submit test
   discovery.
5. Passage retrieval contributes a node identity but discards the bounded code
   excerpt that explains why it matched.
6. Edge expansion flattens relation direction/type into sets; closure paths are
   not preserved.
7. Co-change adds filenames but drops count, recency, and concrete precedent
   commits from semantic delivery.
8. Properties/assertions are collected only for the original seed IDs, not for
   the most relevant expanded impact or test nodes.
9. File-hash and project-metadata records inflate the semantic-fact count even
   though they are freshness/control facts, not model-facing task evidence.
10. Ranked localization is issue-fixed and offered at the first qualifying
    search. It is not conditioned on the current hypothesis, patch, unresolved
    predicate, or recent RED, so it can be irrelevant early or useful too late.
11. Caller-contract delivery is conditioned on the viewed file, not the exact
    symbol being changed or the unresolved obligation. Valid broad caller lists
    can therefore interrupt without adding decision value.
12. Selected role packs are telemetry, not router authority. The
    `data_transform` pack does not list caller-contract evidence, yet batching
    received `caller_contract_view`; `EvidenceRouter` is constructed from the
    task contract and never receives the selected pack.
13. Static utility scoring ranks available candidates by feature class,
    confidence, actionability, and byte length. It does not score novelty,
    unresolved-predicate coverage, current patch relevance, expected
    information gain, or whether nano is already taking the suggested action.

The conclusion is precise:

> GT sends some correct graph-derived facts, but it does not yet compile the
> best available graph evidence for the decision nano is making now.

### Tool-error diagnosis

Tool failures are not the common root cause. Only nine of 277 observations were
harmful, and no persistent-shell lifecycle failure occurred. The batching task
did exhibit trajectory/tool expansion, but GT delivered only six capsules
during that trajectory. The engine failed by supplying weak/late evidence,
over-crediting lexical verification, and allowing submission after one refusal;
it did not directly emit the long sequence of nano commands.

## Non-goals

- GT will not replace nano's reasoning policy.
- GT will not dump `graph.db` into the prompt.
- GT will not force all 17 identities to deliver on every task.
- GT will not infer behavior from lexical overlap alone.
- GT will not expose hidden tests or verifier-only information.
- GT will not optimize feature-fire count.
- GT will not claim superiority from one temperature-1 run.

## Target operating model

```text
issue
  -> complete TaskContract
  -> typed executable predicates
  -> task-role capability pack
  -> fresh semantic graph projection
  -> stage transaction
  -> deterministic utility arbiter
  -> one bounded ephemeral capsule
  -> provider-final request
  -> response
  -> action and tool outcome
  -> progress/recovery governor
  -> executable verify
  -> submit or precise refusal
```

Durable facts and receipts live in host state. Only evidence still useful for
the current decision belongs in the provider request.

## Invariants

1. **Correct or quiet:** unsupported, stale, or irrelevant evidence is worse
   than silence.
2. **Opportunity, not frequency:** every genuine trigger reaches a named
   terminal state; more deliveries are not intrinsically better.
3. **Provider-final attribution:** only exact sealed bytes found in the final
   structured request count as delivery.
4. **Freshness:** patch, graph, contract, and verification revisions agree.
5. **One bounded dose:** at most one intervention per observation unless a
   specified coalition is demonstrably better.
6. **Stage locality:** deliver at the earliest useful lifecycle boundary and
   expire after that decision.
7. **Executable completion:** requirements are verified by semantic checks
   against affected surfaces.
8. **Visible abstention:** suppression, expiry, ineligibility, and failure have
   explicit reasons.
9. **Outcome before rhetoric:** wiring, behavior, efficiency, and outcome are
   separate claims.

## Workstream 1: Executable obligation verification

### Defect

The first implementation now compiles `behavior`, `artifact`,
`numeric_threshold`, and `content_scope` predicates, but observation remains
lexical. Numeric verification checks that obligation numbers occur in passing
command/output text; it does not parse and evaluate the measured inequality.
Artifact and content checks likewise prove command shape more often than the
required postcondition.

### Change

Replace lexical receipts with typed assertion receipts such as:

- test selector;
- content presence or absence over an explicit scope;
- artifact existence or schema;
- numeric threshold;
- build/import success; and
- command/output contract.

Each predicate receipt records obligation, scope, extracted observed value,
operator, required value, units, command and output hashes, workspace revision,
action, and `pass`, `fail`, `unknown`, or `stale`.
An obligation becomes verified only when its required fresh predicates pass.
Uncompilable requirements remain unknown. A generic test pass cannot certify
unrelated content, artifact, or numeric obligations.

### Code and tests

- Replace the lexical evaluators in `gt_engine/verification_contract.py`.
- Keep `matching_obligation_ids` only as candidate routing, never proof.
- Update verification and submit certification in `gt_engine/bridge.py`.
- Add predicate state to `scripts/gt_audit.py` and `scripts/gt_live_gate.py`.
- Add role adapters for numeric inequalities, input immutability, artifact
  schema, repository-wide absence, round-trip conservation, and scale/runtime.
- Test unrelated full-suite passes, wrong-side numeric values, unit mismatch,
  scoped scans, exact artifacts, post-edit staleness, and multi-obligation
  selectors.

### Acceptance

- No lexical-only verified transition.
- Every verified obligation has a fresh executable receipt.
- Replay keeps removed NumPy aliases, every batching inequality (including
  bucket 1 cost `<= 3.0e11` and bucket 2 latency `<= 3.2e7`), and
  repository-wide token absence unresolved until semantically proven.
- Refusal delivers the smallest unresolved predicate set.

## Workstream 2: Semantic use of `graph.db`

### Defect

`GraphProjection` now reads every trusted surface and retains bounded semantic
facts. The bridge and router use only flattened file/symbol sets, while Gateway
producers query the database through separate logic. Rank, path, relation,
excerpt, assertion, co-change, and lifecycle need are not unified. The 373
final projected facts therefore measure availability, not delivered utility.

### Change

Introduce one shared graph evidence IR with source surface, provenance,
confidence, revision, rank, lifecycle need, obligation link, and intended
action:

```text
EvidenceNeed
  = task role
  + unresolved predicates
  + current viewed/edited symbol
  + patch delta
  + latest RED
  + lifecycle boundary
  + remaining budget

GraphEvidence
  = claim
  + minimal proof rows
  + relation/path
  + freshness
  + consequence
  + deterministic next check
```

| Surface | Required semantic use |
|---|---|
| `nodes` | Canonical identity, symbol kind, source/test role |
| `nodes_fts` | Ranked name/path localization |
| `symbol_content_fts` | Implementation-body matches and spans |
| `content_passages` | Bounded source excerpt and line provenance, not only its node |
| `content_passages_fts` | Requirement-specific passage retrieval |
| `edges` | Directional typed caller, callee, import, inheritance, and test relations |
| `edge_metadata` | Relation provenance, confidence, and stale filtering |
| `closure` | Bounded transitive path with intermediate nodes |
| `properties` | Signature, decorator, schema, constant, and stored properties |
| `assertions` | Existing invariants and verification predicate candidates |
| `cochanges` | Ranked companion surface with count and recency |
| `cochange_sets` | Concrete precedent commits and complete relevant sets |
| `file_hashes` | Freshness and receipt invalidation |
| `project_meta` | Index schema, revision, and compatibility |

Preserve rank and provenance instead of flattening results into sets. Rank
anchors by obligation specificity and current decision state; never use
alphabetical first-24 truncation as relevance. Retrieve production and test
nodes in separate lanes. Query only role-relevant surfaces; never dump the
database.

### Acceptance

- Every trustworthy surface changes a typed projection or has an explicit
  `inventory_only_by_design` outcome.
- Every graph fact has surface and revision provenance.
- Every model-facing graph fact links to an unresolved obligation, active
  target/patch, or recent failure.
- Replay localizes Cython/NumPy compatibility surfaces, the exact batching
  cost path, representative reshard verification, and complete sanitizer scope.
- Malformed entity extraction abstains instead of delivering.
- Prompt bytes stay inside the dose budget.

## Workstream 3: Atomic graph wake and refresh

### Implemented foundation

Run `30590129776` proves that `_refresh_graph` rebuilds the task projection and
router atomically. Build and reshard wake from an initially unavailable graph,
and all 44 refreshes completed without a fault.

### Remaining change

Preserve the existing atomic transaction, then invalidate graph-dependent
verification and delivery candidates by revision. Recompute `EvidenceNeed`,
rerank the changed graph slice, and supersede stale graph capsules. Audit must
distinguish graph refresh from semantic reranking.

### Acceptance

- `build-cython-ext` and `reshard-c4-data` retain populated post-wake
  projections.
- Router paths and evidence revisions change with refresh.
- Audit rejects database/projection/router revision mismatch.
- A changed graph causes a new decision-specific ranking rather than replaying
  an issue-fixed answer.

## Workstream 4: Challenge incomplete content-search scope

### Defect

For `content_scan`, `gt_engine/evidence_router.py` suppresses candidates outside
the model's observed search paths. In the earlier sanitizer failure, an
explicit `exp_data/` exclusion became the boundary of truth and suppressed 13
graph candidates. In run `30590129776`, sanitizer passed, but the one delivered
graph localization still named unrelated symbols rather than contaminated
scope. Both outcomes show that observed search scope and generic graph ranking
are poor substitutes for a repository-wide content invariant.

### Change

Parse search inclusions and exclusions into a scope receipt. Observed scope is
evidence, not authority. When the contract requires repository-wide coverage,
one fresh high-confidence candidate outside the searched scope may become a
bounded `scope_gap` containing:

- required root;
- observed inclusions/exclusions;
- relevant candidate outside scope;
- provenance; and
- a deterministic next check.

Unrelated graph neighbors remain suppressed. Positive unresolved scope gaps
block submit.

### Acceptance

- Sanitizer replay identifies excluded `exp_data/` before submit.
- The 13 suppressions collapse into ranked named outcomes and at most one
  corrective capsule.
- Content-scan tasks still reject call-graph noise.

## Workstream 5: Early progress and recovery

### Defect

Recovery requires the same formal failure across an intervening source edit.
The new progress ledger records `STALLED`, `CONTRADICTED`, and `BUDGET_RISK`,
but these are shadow observations. A fresh useful RED after batching's first
submit produced neither a second refusal nor a bounded recovery action.

### Change

Track deterministic progress in unresolved predicates, patch fingerprint,
failure fingerprint, localization frontier, verified count, test outcome, and
submit readiness. Add a configurable state machine:

```text
PROGRESS
  -> STALLED
  -> CONTRADICTED
  -> ESCALATED
  -> BUDGET_RISK
  -> RECOVERED
```

Calibrate thresholds from stored trajectories. Deliver one bounded steer when
there is positive evidence of one of these states:

- fresh required RED near submit;
- unresolved predicate set unchanged across repeated probes;
- patch fingerprint changes without changing the failure;
- exploration expands while the localization frontier and verified set do not;
- remaining iteration budget crosses a threshold with positive unresolved
  evidence.

The steer must contain current blocker, last meaningful evidence, and one
deterministic next action. Allow one escalation only when the first recovery
produces no material transition.

### Acceptance

- Replay detects batching's live expansion before action 72.
- Improving state never triggers a stall.
- Environment errors do not masquerade as source contradictions.
- Recovery and escalation are provider-bound and action-linked.
- A fresh required RED invalidates readiness immediately.

## Workstream 6: Ephemeral GT context

### Implemented foundation

Run `30590129776` proves that every one of 22 capsules is exposed once and then
expired. The parallel-batch expiry defect found in run `30589336562` is fixed.

### Remaining change

Keep one-exposure delivery. Replace the large task-start checklist after its
first use with a durable host-side contract and compact unresolved deltas at
verification/submit. Measure total provider history growth separately from GT
capsule bytes: batching's 4.7M input tokens came from a long nano trajectory,
not repeated active GT capsules.

### Acceptance

- Task-start contracts are superseded by compact unresolved deltas.
- Post-edit capsules expire after the decision they govern.
- Provider block-list tests prove expired text is absent.
- Audit reports active GT bytes separately from ordinary conversation history.

## Workstream 7: Deterministic utility arbitration

### Defect

The implemented one-dose arbiter uses static feature severity, envelope
confidence, presence of target/provenance, and byte length. It does not know
whether evidence resolves an outstanding decision, is redundant with nano's
current action, or is likely to reduce uncertainty.

### Change

Use documented bounded factors:

```text
utility =
    severity
  * evidence_strength
  * actionability
  * freshness
  * unresolved_relevance
  * expected_information_gain
  - repetition_cost
  - token_cost
  - interruption_cost
  - false_positive_risk
```

This is not a learned reasoner. Every component comes from deterministic
receipts. Admission requires highest coalition score and a minimum threshold;
silence is valid. Penalize already exposed facts and actions nano is already
taking.

### Acceptance

- Every delivery has an inspectable score decomposition.
- Fresh failing predicates outrank repeated localization.
- All-low candidates produce `utility_abstain`.
- Malformed, redundant, already-acted, and decision-irrelevant candidates
  abstain.
- Replay reduces low-value delivery without suppressing the build caller facts
  that preceded target edits.

## Workstream 8: Task-role capability packs

### Defect

Role packs now declare allowed evidence and predicate kinds, but the selected
pack is not passed into `EvidenceRouter`. The declarations are therefore
mostly receipts: batching received caller-contract evidence even though the
`data_transform` pack does not allow that class. Packs also do not yet define
complete lifecycle-specific graph queries, verification adapters, recovery
policy, or submit authority.

### Change

Configure stable canonical features through declarative packs:

| Pack | Evidence | Verification |
|---|---|---|
| Code/build | Build metadata, bodies, callers, tests, impact | Build, import, targeted behavior, API surface |
| Data transform | Schemas, invariants, reference patterns, numeric obligations | Samples, conservation, order, schema, thresholds |
| Content scan | Required root, patterns, tracked/untracked scope, exclusions | Complete-scope absence and placeholder correctness |
| Artifact/CLI | Expected paths, entry points, executable contract | Existence, invocation, output/schema |
| Service/system | Config, process, port, dependency topology | Health, protocol, persistence |

Allow deterministic multi-label selection when necessary. Record pack version

Make the selected pack an explicit input to the router, graph query planner,
predicate compiler, progress governor, and submit policy. Any cross-pack
exception must be declared and receipted rather than emerging from hard-coded
router special cases.

### Acceptance

- The five smoke tasks select the intended pack(s).
- Content tasks avoid caller noise.
- Build tasks receive compile/import and impact predicates.
- Data tasks preserve exact numeric thresholds.

## Workstream 9: GT-on validation against the frozen existing baseline

### Defect

The existing baseline already exists. Re-running GT-off would spend time and
money without improving the engine. What is missing is a stable GT-on
candidate, repeated GT-on evidence, and a comparison reader that consumes the
frozen baseline without mutating or replacing it.

### Change

Separate these claims:

| Claim | Required evidence |
|---|---|
| Wiring | Complete census and terminal outcomes at correct lifecycle stages |
| Attribution | Exact provider-final bytes, linked response, linked action |
| Behavior | Fresh actionable evidence, action consistency, low false intervention |
| Efficiency | Repeated GT-on runs with lower cost at non-worse reward, compared with the frozen baseline where controls are compatible |
| Outcome | Repeated GT-on runs with higher reward and acceptable cost, with incompatibilities named |

Freeze exact tasks/order, substrate, nano version, model, explicit temperature,
timeout, iteration limit, concurrency, prompt/tools, adapter, and grader. Run
at least three GT-on repetitions; five are preferred. Use deterministic replay
and local feature ablations before the live candidate, not new paid GT-off
runs.

Candidate stages:

1. offline attribution/quality replay;
2. contract and semantic-verification integration;
3. graph evidence compiler integration;
4. progress/submit governor integration;
5. full GT-on live candidate.

Use replay to eliminate broken arms before paid execution. Report every task
before aggregates. Primary metrics are reward, input tokens per reward,
iterations per reward, and wall time per reward.

### Acceptance

- Workflow rejects configuration mismatch.
- Every artifact carries the full configuration receipt.
- Provider block lists are inspected structurally.
- Reports distinguish observation, causal estimate, and hypothesis.
- No workflow dispatches a new GT-off run.

## Workstream 10: Tool outcomes and GT-induced misuse

This is a cross-cutting safety/measurement requirement, not an eighteenth
canonical feature.

### Defect

Tool outcomes are now classified and capsule exposures are unique. The
remaining defect is behavioral coupling: useful RED, harmful misuse, progress,
verification invalidation, utility, and submit readiness are still separate
state machines.

### Change

Retain the implemented classes:

- `useful_red`;
- `expected_negative_probe`;
- `agent_command_error`;
- `tool_contract_error`;
- `dependency_or_environment`;
- `timeout_or_resource`;
- `shell_lifecycle`;
- `product_failure`; or
- `unknown`.

Link request, response, delivery ID, tool call, observation, information gain,
predicate invalidation, progress transition, next recovery action, and submit
state.

Detect top-level `exit`/`logout` in persistent-shell commands and run that
command in an isolated child shell or reject it precisely. Do not silently
rewrite arbitrary shell semantics. Feed repeated harmful outcomes—not useful
RED—into progress and utility penalties.

### Acceptance

- All 277 live observations retain a class and reason.
- `unknown` is below a predeclared limit.
- Useful RED and harmful errors are reported separately per task.
- Useful RED deterministically invalidates readiness and can trigger recovery.

## Dependency-ordered execution

### Phase 0: Measurement freeze and replay

Deliver:

- preserve runs `30582455019`, `30589336562`, and `30590129776` as immutable
  replay fixtures;
- add a 22-delivery quality manifest recording basis, timing, target,
  obligation/recent-RED link, novelty, next response, and observed outcome;
- exact capsule exposure/expiry metrics;
- tool-outcome taxonomy and response-to-observation linkage;
- existing baseline import/validation with no new GT-off execution;
- deterministic replay for predicates, routing, progress, and utility; and
- goldens for malformed new-file evidence, poor localization, late
  localization, false numeric credit, representative-scale verification, and
  second-submit fail-open.

Exit:

- reproduce graph wake, poor sanitizer/build localization, malformed batching
  entity extraction, absent recovery, lexical numeric verification, and the
  second-submit fail-open;
- prove provider block-list handling is structural; and
- keep the complete suite green.

### Phase 1: Truthful completion

Implement workstream 1 and the verification half of workstream 8. Exit when
every smoke task has role-appropriate semantic predicates, batching's measured
miss remains RED, reshard's non-representative pass remains insufficient, and
fresh RED cannot be hidden by a later submit.

### Phase 2: Decision-specific graph research

Implement the shared `EvidenceNeed`/`GraphEvidence` IR and workstreams 2, 3,
and 4. Exit when:

- every graph delivery is tied to an unresolved obligation, active target,
  patch impact, or recent RED;
- build localization names Cython/NumPy compatibility surfaces;
- batching evidence names the exact cost path before broad optimization;
- reshard evidence proposes a representative distribution/runtime check;
- sanitizer evidence names contaminated scope rather than unrelated symbols;
- malformed entity extraction abstains;
- rank/relation/excerpt provenance survives rendering; and
- graph bytes remain bounded.

### Phase 3: Reduce waste and prevent exhaustion

Implement workstreams 5, 6, and 7. Exit when both historical stalls are found
early, every capsule expires after one exposure, total-history cost is reported
separately, and every intervention has a decision-specific utility score.

### Phase 4: Pre-live audit

Run:

- full unit and integration suite;
- repository static/format checks;
- five-trajectory replay;
- complete 17-identity census;
- all lifecycle boundary tests;
- provider block-list attribution tests;
- graph surface/revision/wake tests;
- predicate freshness and refusal tests;
- context expiry/exposure tests;
- tool-outcome and shell-lifecycle tests;
- progress shadow precision review; and
- workflow/substrate parity audit against the frozen receipts.

The audit emits exactly:

- `GO_LIVE`;
- `NO_GO_CODE`; or
- `NO_GO_EXPERIMENT`.

### Phase 5: Real nano + GT live runs

Dispatch `.github/workflows/tb2_gt.yml` only after `GO_LIVE`:

- nano-harness with GT, not Mini-SWE;
- `deepseek-v4-flash` only;
- explicit temperature `1`;
- Profile 2;
- exact task slice:
  `build-cython-ext,headless-terminal,llm-inference-batching-scheduler,reshard-c4-data,sanitize-git-repo`;
- concurrency `4`;
- timeout multiplier `1.0`;
- exact expected count `5`; and
- strict contract, profile, lifecycle, provider attribution, feature census,
  behavior flag, and action-consistency gates.

Every trial must contain result/reward, nano trajectory, GT ledger and sealed
deliveries, provider receipts, response/action/observation linkage, roles and
predicates, graph revisions, capsule lifecycle, progress, utility, and
classified tool outcomes.

First run one strict five-task GT-on smoke. If it passes artifact and behavior
gates, repeat the identical GT-on configuration. Compare with the existing
frozen baseline artifact; do not dispatch GT-off.

### Live outcome targets

- batching and reshard pass the checks that failed in run `30590129776`;
- no previously passing task regresses;
- no malformed or decision-irrelevant graph capsule is delivered;
- every submit refusal remains authoritative while its blocker remains;
- graph evidence arrives before the governed edit/verification decision;
- batching materially reduces its 74-iteration, 4.74M-input-token trajectory;
- aggregate reward improves before an efficiency superiority claim; and
- repeated GT-on observations agree before the result is called stable.

## Five-task acceptance matrix

| Task | Required GT opportunity | Proof required before submit |
|---|---|---|
| `build-cython-ext` | Graph wake, build pack, compiled-source impact | All affected Cython sources checked for removed aliases; build/import pass |
| `headless-terminal` | Code/CLI pack | Required terminal behavior and repository checks pass |
| `llm-inference-batching-scheduler` | Data pack, exact numeric obligations, cost-path graph slice | Parsed per-bucket inequalities, including bucket 1 cost `<= 3.0e11`, with measured values |
| `reshard-c4-data` | Graph wake, data/artifact pack, scale/distribution need | Schema, shards, order/conservation, CLI, representative distribution, and runtime checks |
| `sanitize-git-repo` | Content pack, repository scope challenge | Complete-scope sensitive-content absence and placeholders |

## Telemetry and gate additions

Recommended events:

- `contract.predicate_compiled|observed|invalidated`
- `graph.context_refreshed|refresh_failed`
- `search.scope_observed|scope_gap`
- `progress.transition`
- `recovery.shadow_candidate`
- `capsule.issued|exposed|expired|superseded`
- `utility.scored|abstained`
- `tool.outcome_classified`

Every event includes episode/action, lifecycle stage, role-pack version,
workspace/graph revisions, evidence hashes, decision/reason, and provider
request/response/action linkage when model-facing.

`scripts/gt_audit.py` must report per task:

- predicate state by obligation;
- graph revisions, ranked surface semantics, evidence-need links, and refresh
  consistency;
- search inclusions/exclusions and scope gaps;
- progress/recovery transitions;
- unique, repeated, and expired capsule bytes;
- utility winners and suppressions;
- tool-outcome class, information gain, exposure position, and recovery; and
- configuration receipt and frozen-baseline compatibility.

`scripts/gt_live_gate.py` must fail on:

- verified obligations without fresh executable receipts;
- stale/mismatched graph evidence;
- unresolved eligible repository scope gaps;
- capsules exceeding exposure policy;
- interventions without utility decisions;
- harmful tool errors lacking the response/action chain;
- top-level `exit` still killing the persistent shell;
- eligible-dark identities;
- missing provider attribution/response linkage; or
- a new GT-off dispatch request.

It must not fail because an ineligible feature stayed quiet.

## Reviewable implementation slices

1. Measurement, outcome taxonomy, shell protection, and replay fixtures.
2. Predicate compiler/evaluator and audit schema.
3. Role packs and five-task predicate adapters.
4. Semantic graph projection and freshness.
5. Graph revision invalidation and decision reranking.
6. Content-scope challenge.
7. Progress ledger and recovery shadow mode.
8. Ephemeral provider request view.
9. Utility arbiter.
10. Recovery delivery after shadow review.
11. GT-on workflow parity, repeated live dispatch, and result report.

Every slice needs unit tests, replay, audit compatibility, and a rollback flag
when it changes model-facing behavior.

## Stop/go rules

Do not dispatch if:

- a known incomplete patch can be certified;
- graph wake leaves stale evidence or predicate receipts;
- sanitizer exclusions remain invisible;
- historical stalls have no shadow transition;
- a capsule is exposed more than once;
- utility/expiry is unauditable;
- tool outcomes are unclassified or checks can kill the shell;
- attribution relies on trajectory text;
- frozen configuration receipts are missing; or
- the local suite is red.

Claim improvement only after repeated GT-on runs show higher reward, or
non-worse reward with materially lower cost against the compatible portion of
the frozen existing baseline, without hiding a task-level regression. Flat
reward with higher cost means GT is worse. A single positive temperature-1 run
is promising but inconclusive.

## Definition of done

1. Completion credit is predicate-backed.
2. Every trustworthy graph surface is semantically used or explicitly
   abstained from.
3. Graph wake atomically refreshes projection and routing.
4. Strong repository evidence can challenge incomplete searches.
5. Stalls and contradictions are detected before exhaustion.
6. GT context expires and repeated exposure is bounded.
7. Every intervention passes deterministic utility admission.
8. Role packs provide task-appropriate SDLC support.
9. GT-on live configuration is frozen and repeated; the existing baseline is
   consumed without a new GT-off run.
10. Tool outcomes are classified and harmful GT-linked misuse is prevented or
    surfaced.

## Live execution receipt and remaining gap

The first post-implementation run,
[`30589336562`](https://github.com/harneet2512/gt-harness/actions/runs/30589336562)
at `66c30ed`, found a live-only capsule lifecycle defect: evidence created by
an early tool result in a parallel batch was expired by later sibling action
indices before its first provider request. Five deliveries across three tasks
had exposure count zero. Commit `0d3dd88` changed expiry from action-age to
provider-exposure state and added a parallel-batch regression test.

The corrective run,
[`30590129776`](https://github.com/harneet2512/gt-harness/actions/runs/30590129776),
passed the strict gate. It proves provider-bound lifecycle wiring, complete
outcome classification, atomic graph refresh, one-exposure capsule expiry,
role selection, predicate execution, and SDLC boundary coverage.

It does **not** satisfy this plan's outcome definition. Reward was 3/5 overall
and the graph-derived delivery audit found valid but weak, malformed, and late
evidence. The next dependency-ordered work is:

1. make numeric/content/artifact predicates validate observed semantics rather
   than command vocabulary;
2. compile decision-specific graph evidence from unresolved requirements,
   active patch/target, recent RED, and role;
3. treat non-representative, timed-out, or incomplete required probes as
   unresolved positive evidence;
4. replace the single submit bounce with a bounded unresolved-predicate policy
   coordinated with nano's remaining pushback budget;
5. render bounded role-relevant graph facts into applicable feature evidence,
   not only projection telemetry;
6. turn calibrated `STALLED`/`BUDGET_RISK` shadow states into one bounded
   recovery action; and
7. run the frozen repeated GT-on protocol before claiming improvement, using
   the existing baseline without dispatching a new GT-off run.

All 17 identities must retain complete per-task terminal-state accounting,
every delivery must remain provider-final attributable and action-linked, and
the final proof must be a real nano + GT run with a per-task result report.

### Efficiency result after the decision-specific implementation

Run
[`30594350673`](https://github.com/harneet2512/gt-harness/actions/runs/30594350673)
at `20e4925` completed all five trials and scored 3/5. It fixed the earlier
batching failure (74 to 40 iterations; 4.74M to 1.33M input tokens) but
regressed build and reshard. The immutable artifacts exposed three concrete
problems: repeated generic verification refusals, no bounded NumPy-2 removed
alias check, and shell-death accounting that confused a recovered command with
terminal tool failure. Commit `48f2015` fixed those defects and passed 325
tests plus Ruff.

The identical live configuration at `48f2015`, run
[`30595670669`](https://github.com/harneet2512/gt-harness/actions/runs/30595670669),
scored 4/5:

| Task | Reward | Iterations | Input tokens | Output tokens | GT shipped chars | Deliveries |
|---|---:|---:|---:|---:|---:|---:|
| `build-cython-ext` | 1 | 100 | 3,084,870 | 25,853 | 8,003 | 12 |
| `headless-terminal` | 1 | 37 | 626,979 | 21,160 | 1,812 | 5 |
| `llm-inference-batching-scheduler` | 0 | 49 | 1,846,312 | 60,003 | 2,896 | 4 |
| `reshard-c4-data` | 1 | 49 | 1,121,296 | 27,406 | 1,915 | 5 |
| `sanitize-git-repo` | 1 | 25 | 898,498 | 10,532 | 1,539 | 3 |

This is not an efficiency win against the frozen compatible GT-off rows. On
the four comparable tasks, GT-on scored 3/4 versus 4/4, used 223 versus 195
iterations (+14.4%), 6,950,976 versus 5,884,607 input tokens (+18.1%), and
123,794 versus 90,374 output tokens (+37.0%). GT's own 14,353 shipped
characters are small; the excess comes from model search and verification
work that GT failed to prevent. Therefore deterministic evidence is only a
mechanism, not an efficiency result. The acceptance metric is:

`saved model search + avoided failed work > GT payload + GT-induced work`

The run also found two exact semantic defects:

1. Expiring a previously exposed capsule used global substring replacement.
   An old `base_terminal.py` localization was a strict substring of a fresh
   two-file localization, so expiry deleted bytes from delivery 34 before the
   provider request. The ledger claimed 73 sealed characters while the request
   received only a fragment. Pending capsules must be protected before old
   capsule removal, and receipts must exclude already-expired substring
   matches.
2. The batching threshold-table rows compiled as generic behavior predicates
   instead of numeric predicates. The model printed
   `2.723253e+08 >= 2.700000e+08`, then incorrectly declared success. GT kept
   the run RED and refused submission, but the threshold contract was too
   coarse and the intervention arrived too late. Multi-bound data-transform
   rows must compile numerically so a passing command cannot certify a
   wrong-side measured value.

No superiority claim is permitted until a subsequent identical live run has a
clean provider attribution gate and reaches non-worse reward with materially
lower cost. A reroll that merely changes which temperature-1 task fails is not
evidence of stable improvement.

Run
[`30597263179`](https://github.com/harneet2512/gt-harness/actions/runs/30597263179)
at `530668f` proved the capsule-overlap correction: Harbor completed all five
trials, the audit reported zero unexposed deliveries, zero dark identities,
zero attribution faults, 11 witnessed identities, 15 exercised identities,
and all seven lifecycle boundaries. It nevertheless scored only 3/5 and is
therefore further negative outcome evidence:

| Task | Reward | Iterations | Input tokens | Output tokens | GT chars | Terminal state |
|---|---:|---:|---:|---:|---:|---|
| `build-cython-ext` | 0 | 100 | 3,612,915 | 40,749 | 2,768 | max iterations |
| `headless-terminal` | 0 | 86 | 3,685,480 | 72,172 | 1,302 | 900-second agent timeout |
| `llm-inference-batching-scheduler` | 1 | 65 | 4,147,849 | 125,048 | 4,267 | end turn |
| `reshard-c4-data` | 1 | 100 | 4,453,930 | 85,521 | 2,469 | max iterations |
| `sanitize-git-repo` | 1 | 40 | 1,660,690 | 47,300 | 1,798 | end turn |

On the four frozen-baseline-compatible tasks, this run used 305 versus 195
iterations (+56.4%), 13,875,384 versus 5,884,607 input tokens (+135.8%), and
298,618 versus 90,374 output tokens (+230.4%), while scoring 3/4 versus 4/4.
The batching numeric-table fix compiled seven numeric predicates and the task
passed, but this did not offset the other trajectories.

The dominant newly observed waste was harness self-interference. On headless,
the model discovered `/installed-agent/nano-harness` with `find /` at
iteration 3, began reading `gt_engine/task_contract.py` and
`verification_contract.py` at iteration 4, and later spent many decisions
reverse-engineering submit-refusal internals. It timed out and the hidden
verifier found a real `pyte` API mismatch. This is not useful GT reasoning; it
is implementation leakage caused by staging the agent source in a root-readable
container. The adapter must remove the exact staged checkout after its
non-editable uv install and smoke check, and the GT-only prompt must explicitly
classify `.gt`, `/installed-agent`, the agent environment, and GroundTruth
implementation as out-of-scope harness internals.

## Frontier-lab research and root-cause decision

### Strong conclusion

The next thing to fix is **not another GT feature and not a larger graph
dump**. It is the harness control plane that turns deterministic state into
model context and actions.

The current implementation has four compounding defects:

1. useful state is often delivered as prose rather than a minimal
   decision-specific delta;
2. successful work is under-credited by coarse verification predicates, so
   the model keeps testing or receives generic refusals;
3. progress detection is mainly telemetry and does not reliably redirect or
   terminate a stalled trajectory; and
4. each additional iteration resends an already large history, so a small
   behavioral detour becomes a very large token regression.

`graph.db` is therefore under-used semantically and overvalued conceptually.
The bridge inventories and queries 14 surfaces, builds a ranked
`_graph_evidence` tuple, and uses that tuple to constrain router admission.
But `_graph_evidence` is never directly rendered to the model. Relation,
closure, co-change, property, and assertion rows are mostly set expansion,
receipts, or admission support. The model usually receives separate gateway
producer prose, not the compact decision-linked fact set that was ranked.

### What the latest run proves

For the four tasks with compatible frozen GT-off rows:

| Metric | Frozen GT-off | GT-on `30597263179` | Delta |
|---|---:|---:|---:|
| Reward | 4/4 | 3/4 | worse |
| Iterations | 195 | 305 | +56.4% |
| Input tokens | 5,884,607 | 13,875,384 | +135.8% |
| Output tokens | 90,374 | 298,618 | +230.4% |

The direct GT payload was only 1,302--4,267 characters per task. It cannot
explain millions of additional input tokens by byte volume. The provider
requests grew to roughly 120,000--150,000 characters and were resent for
dozens of iterations. The relevant cost relation is:

`cumulative input ~= iterations * average request context`

The excess therefore comes primarily from behavior induced or not prevented
by the harness: broad search, redundant verification, stalled work, and
internal-harness investigation. GT's bytes are small; GT's downstream work is
not.

### Tool-error ruling

Elevated tool errors are a secondary defect, not the dominant cause established
by this run:

| Task | Tool outcomes | Non-success | Harmful | Harmful with active GT delivery |
|---|---:|---:|---:|---:|
| build | 109 | 6 | 0 | 0 |
| headless | 99 | 6 | 1 | 0 |
| batching | 65 | 6 | 1 | 0 |
| reshard | 122 | 2 | 2 | 0 |
| sanitizer | 51 | 6 | 5 | 0 |

Build failed with zero harmful outcomes. Sanitizer passed with five and used
fewer tool calls than its frozen baseline. None of the harmful outcomes was
action-linked to an active GT delivery. This rejects the strong claim that GT
payloads directly caused bad tool calls. It does not reject the weaker claim
that longer GT-on trajectories created more opportunities for errors.

The known shell-lifecycle and timeout cases still require regression coverage
and actionable error text. They must not displace context, verification, and
stall control as the first optimization target.

### What frontier labs do differently

The relevant frontier-lab pattern is consistent:

- OpenAI reports that every agent iteration includes prior conversation and
  tool history, making prompt growth effectively quadratic over a long
  stateless loop. Codex preserves stable prefixes for caching and compacts the
  conversation when it crosses a threshold. This directly matches nano's
  120,000--150,000-character late requests.
  [OpenAI: Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- OpenAI's harness engineering guidance is to give an agent a map, not a
  thousand-page manual: keep the injected entry point small, expose deeper
  information progressively, and enforce invariants mechanically with
  actionable remediation. GT should return a small next-decision map and keep
  the graph outside the prompt until a boundary needs it.
  [OpenAI: Harness engineering](https://openai.com/index/harness-engineering/)
- Anthropic defines good context as the smallest high-signal token set that
  produces the desired behavior. Claude Code combines just-in-time retrieval
  with compaction and clears old raw tool results while retaining decisions,
  unresolved bugs, and recent files. This contradicts permanently protecting
  every historical evidence-bearing result block.
  [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic recommends a few distinct high-impact tools, targeted searches,
  concise response modes, and evaluations that record accuracy, token use,
  tool calls, runtime, and errors. GT's 17 identities are an audit inventory;
  they must not become 17 competing prompt surfaces.
  [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Anthropic has observed coding agents inspect git history for benchmark
  answers and identify the benchmark itself. It treats filesystem containment,
  not a prompt request, as the hard boundary. That is direct support for
  removing `/installed-agent/nano-harness` rather than trusting the new prompt
  sentence alone.
  [Anthropic: How we contain Claude across products](https://www.anthropic.com/engineering/how-we-contain-claude)
- Microsoft Research's Magentic-One maintains a task ledger and a separate
  progress ledger; after repeated lack of progress it updates the task state
  and replans. GT currently records progress transitions but rarely turns them
  into an early, bounded change of course.
  [Microsoft Research: Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
- Google DeepMind's AlphaEvolve pairs model proposals with automated
  evaluators and retains candidates according to objective scores. The
  transferable point is not evolutionary search: deterministic infrastructure
  should evaluate executable outcomes and select useful state, not add generic
  reasoning prose.
  [Google DeepMind: AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
- Anthropic distinguishes the transcript from the outcome and recommends
  multiple trials because model behavior varies. A single five-task smoke can
  prove wiring and expose failures; at temperature 1 it cannot establish a
  stable causal superiority claim.
  [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

### Open-source terminal-agent context comparison

The strongest open-source agents do not solve context growth with one
technique. They separate durable history from the active model view, budget
repository evidence, compact old interaction state, and preserve a recent
working tail. The implementation details matter:

| Agent | Relevant context mechanism | What GT should adopt | What GT should not copy blindly |
|---|---|---|---|
| OpenCode | Replaces old active context with a checkpoint plus a bounded recent tail; its current source caps retained tool output at 2,000 characters and keeps durable messages outside the compact active view | preflight request sizing, checkpoint plus tail, exact tool-output caps, one overflow retry | a model-generated checkpoint as authoritative verification or attribution state |
| OpenHands | Separates immutable event history from the LLM-ready `View`; condensers can summarize the middle while retaining first and recent events, and record the IDs omitted from the view | a durable GT ledger distinct from a compact provider view, with explicit omission provenance | treating a lossy condensation as deletion of audit evidence |
| Aider | Ranks repository definitions and references with a graph, excludes files already in chat, and binary-searches the ranked prefix into a hard token budget; map cache keys include files and mentioned identifiers | budgeted `graph.db` projection keyed by active obligations, changed paths, graph revision, and already-visible evidence | dumping the repository map or all graph surfaces into every turn |
| SWE-agent | Deterministically elides older observations, can always retain or remove tagged observation classes, and batches pruning changes to avoid destroying prompt-cache reuse | typed retention classes and cache-stable compaction epochs | rewriting the whole prompt on every observation |
| Goose | Uses proactive auto-compaction, summarizes older tool calls while retaining recent calls, has an overflow fallback, and bounds agent turns | proactive rather than emergency-only compaction, recent-tool cutoff, explicit loop ceiling | copying its thresholds or large tool-response limit without nano replay measurements |
| Gemini CLI | Offers explicit whole-session compression; path inclusion is filtered and large or binary content is skipped or truncated | an operator-visible compression event and repository-aware file filtering | relying on a manual command during an unattended smoke |
| Codex | Maintains a stable request prefix for prompt caching and compacts before the model context window is exhausted | stable-prefix/cache accounting and automatic thresholding | using the model's maximum context window as the economic operating target |

Primary implementation references:

- [OpenCode compaction documentation](https://opencode.ai/v2/docs/compaction)
  and [OpenCode compaction source](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/compaction.ts)
- [OpenHands condenser architecture](https://docs.openhands.dev/sdk/arch/condenser)
- [Aider repository-map source](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)
  and [Aider configuration](https://github.com/Aider-AI/aider/blob/main/aider/website/assets/sample.aider.conf.yml)
- [SWE-agent history processors](https://swe-agent.com/1.0/reference/history_processor_config/)
  and [model/cache guidance](https://swe-agent.com/latest/config/models/)
- [Goose smart context management](https://goose-docs.ai/docs/guides/sessions/smart-context-management/)
  and [Goose context environment variables](https://github.com/block/goose/blob/main/documentation/docs/guides/environment-variables.md)
- [Gemini CLI commands](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/commands.md)
- [OpenAI: Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)

### Target context architecture for nano + GT

Context engineering is necessary but not sufficient. It should reduce
cumulative input and distraction with high confidence. It cannot repair a
false verification refusal, select a relevant graph fact, or stop a no-gain
loop unless P3--P5 are also implemented.

GT should maintain two planes:

```text
durable evidence plane                         active provider view
----------------------                         --------------------
immutable user task -------------------------> task/contract anchor
full messages and tool results                 stable system/tool prefix
GT delivery bytes + exposure receipts ------> pending exact capsule (once)
graph revisions and source provenance ------> one budgeted JIT graph slice
verification observations ------------------> deterministic GT checkpoint
iteration and progress ledger --------------> bounded recent interaction tail
```

The durable plane is the audit source of truth and never needs to fit in the
model context. The active provider view is reconstructed from durable typed
state for each request. It contains only:

1. a stable system/tool prefix;
2. the original task plus its normalized obligation identifiers;
3. one deterministic GT checkpoint;
4. the current boundary's just-in-time graph slice;
5. a bounded recent tail; and
6. an exact capsule whose first provider exposure is still pending.

The checkpoint must not be a free-form model summary. It is a versioned,
deterministically rendered record containing:

- task and obligation IDs;
- active lifecycle boundary and intended next decision;
- patch fingerprint, changed paths, and graph revision;
- verified, unresolved, stale, and RED predicate IDs;
- latest distinct failure fingerprint and smallest next check;
- recent relevant files/symbols;
- progress/stall state; and
- exposed GT delivery IDs, provenance hashes, and outcomes.

This gives GT a structural advantage over generic agents: it can compact
without paying for a summarization call and without asking a stochastic model
to preserve verification truth.

#### Retention and compaction policy

Each GT delivery follows an auditable state machine:

```text
SEALED -> BOUND_TO_REQUEST -> PROVIDER_EXPOSED -> FOLDED_INTO_CHECKPOINT
                                                    |
                                                    v
                                             RAW_BLOCK_ELIDABLE
```

The exact bytes remain in the durable ledger in every state. They are protected
in the active view only through the first provider-confirmed exposure. Folding
records delivery ID, payload hash, request ID, boundary, affected obligation,
freshness, and observed outcome.

The recent tail should be bounded by both structure and tokens:

- keep the last two complete interaction turns by default;
- expand only up to a measured 2,000--8,000-token tail budget;
- keep the current diff, newest useful RED, and newest verification receipt;
- replace older successful or repetitive tool results with typed references;
- cap ordinary inline tool output initially at 2,000--4,000 characters and
  keep the full result in the durable transcript;
- treat file/media attachments as descriptors after their first useful view;
  and
- never elide the original task, unresolved contract, or unexposed capsule.

These are starting ranges from the open-source evidence, not hard-coded final
values. P0 replay must select nano's thresholds against reward-preserving
counterfactuals.

#### Preflight, caching, and economic threshold

Compaction must estimate the final provider request--system prompt, tools,
messages, checkpoint, graph slice, and reserved output--before dispatch. It
must run before the active request reaches the model's technical context
limit. Nano's observed failure is economic: repeatedly sending
120,000--150,000 characters is costly even though the request still fits.

The active view should therefore change in cache-stable epochs:

1. append deltas while the current epoch remains under its replay-selected
   budget;
2. fold state at stable lifecycle boundaries such as post-edit and
   post-verification, or when the size threshold is crossed;
3. leave the system/tool/task prefix byte-identical;
4. record pre/post size, checkpoint version, omitted event IDs, and provider
   cache-read tokens; and
5. retry once with forced compaction on an actual context-overflow response.

Compacting every turn can reduce prompt-cache hits and make cost worse. Waiting
until overflow preserves too much irrelevant state. The replay-selected
threshold and epoch policy must optimize cumulative billed input, not merely
peak request size.

#### Why this is not “context engineering alone”

Four independent conditions are required for a better agent:

| Condition | Failure if omitted |
|---|---|
| bounded active context | old observations and repeated requests dominate token cost |
| semantically correct receipts | successful work remains “unknown,” causing repeat tests and refusals |
| decision-linked graph retrieval | compact context is still irrelevant context |
| deterministic progress control | the agent can resend a compact but useless loop indefinitely |

Accordingly, the correct claim is: context engineering is the largest known
efficiency lever, while verification, graph selection, and loop control
determine whether the saved tokens also improve task reward.

### Ranked correction plan

The order matters. Later work is invalid if an earlier gate fails.

#### P0: freeze measurement and prove the regressions by replay

Before changing runtime behavior:

1. add a per-iteration replay report containing request characters, input
   tokens, cache-read tokens, active delivery, lifecycle phase, changed-file
   fingerprint, unresolved predicate count, test state, progress state, and
   next action;
2. classify every iteration as useful research, edit, useful RED,
   verification, redundant repeat, harness-internal investigation, or idle
   narration;
3. calculate marginal work after each GT delivery instead of attributing the
   whole subsequent trajectory to GT;
4. replay context policy with and without historical evidence-block
   exemptions; and
5. keep raw task/verifier outcomes immutable.

Go only if the report reproduces the published run totals and accounts for at
least 95% of provider input tokens and iterations.

#### P1: enforce real harness isolation

The exact staged checkout removal at `6362926` is directionally correct but
not yet proven live.

1. test that the installed package still imports and runs after source removal;
2. assert the task agent cannot read `.gt`, the staged nano source, GT source,
   workflow metadata, verifier files, or answer-bearing harness artifacts;
3. keep graph indexing and the GT bridge outside the task-visible namespace;
4. fail the pre-live audit if forbidden paths appear in any provider-visible
   tool result; and
5. replay the two contaminated traces to show those commands would return no
   source.

This removes the largest clearly identified single-task detour. It is not by
itself an efficiency proof.

#### P2: replace evidence immortality with a compact state ledger

Implement this as four separately testable changes:

1. **Active-view separation:** retain the complete transcript and ledger for
   replay, but construct a bounded provider view instead of mutating or
   deleting durable events.
2. **Deterministic checkpoint:** fold exposed capsules and old observations
   into the versioned schema above. Preserve a new capsule verbatim only until
   its first provider-final exposure.
3. **Typed tail policy:** retain the last two complete turns plus the newest
   useful RED, diff, and verification receipt under a hard token budget. Elide
   old output by event type, not naive substring matching.
4. **Preflight and cache epochs:** size the final provider request, compact at
   replay-selected lifecycle/size boundaries, keep the prefix byte-stable, and
   retry one overflow after forced compaction.

The existing `smart_truncate(... delivered_spans=self._gt.delivered_spans)`
protects blocks containing any historical delivery, including already exposed
ones. Because the raw delivery-bearing outputs measured only about
1,500--9,400 characters per task, changing that alone is expected to help but
not explain the full regression. It must be evaluated as one component of
state compaction, not shipped as a speculative standalone fix.

P2 acceptance requires:

- every sealed delivery has exactly one provider exposure receipt before its
  raw block becomes elidable;
- replay can reconstruct the active provider view byte-for-byte from durable
  events and policy version;
- no verified, unresolved, stale, or RED predicate changes meaning across a
  compaction;
- original task and active changed-file identities survive every compaction;
- the stable prefix remains identical within an epoch and cache-read tokens do
  not regress materially;
- each immutable task replay shows lower cumulative provider input without
  changing the sequence of semantically relevant observations; and
- a synthetic overflow compacts and retries at most once.

#### P3: make verification recognize executable completion

1. compile obligation predicates into explicit verifier plans at task start;
2. map a passing observation by structured command scope, exit status,
   measured values, artifacts, and post-edit freshness--not primarily lexical
   overlap;
3. let one representative passing suite satisfy every behavior obligation it
   actually covers;
4. invalidate only receipts affected by a later edit, rather than clearing all
   completion state;
5. render the smallest unresolved predicate set with the exact executable next
   check; and
6. never repeat an unchanged generic unknown-state refusal.

Acceptance requires replaying batching and reshard so their successful checks
become credited at the action where they happened, with no later generic
refusal for already-proven requirements.

#### P4: turn `graph.db` into just-in-time decision evidence

Do not put all graph rows in context. Use all trustworthy surfaces as a query
backend:

- FTS/body/passages identify candidate definitions;
- edges and closure identify callers, callees, importers, and affected tests;
- properties and assertions supply concrete contracts and invariants;
- co-change surfaces identify companion files;
- hashes and revision state establish freshness; and
- project metadata selects language/build/test adapters.

At each lifecycle boundary, render at most the top facts needed for one
decision:

| Boundary | Graph question | Required output |
|---|---|---|
| orient/research | Where is the behavior and its closest precedent? | ranked paths/symbols plus why |
| pre-edit | What contracts and dependents can this edit break? | callers, assertions, companion files |
| post-edit | What changed semantically and what became stale? | signature/impact delta |
| verify | Which smallest executable checks cover the changed surface? | commands/targets with coverage basis |
| submit | Which positive facts remain RED or unknown? | only blockers and exact remediation |

Every rendered fact must link to an unresolved obligation or active changed
target, carry graph revision/provenance, prescribe an intended action, and
expire after that decision. `_graph_evidence` must either feed these canonical
features or be deleted; telemetry-only ranking is not product value.

#### P5: make progress state control the loop

1. define progress from patch fingerprint, verified-obligation delta,
   localization-frontier delta, failure fingerprint, and new information;
2. after two equivalent no-gain actions, deliver one bounded alternative
   action;
3. after a failed check, point to the changed surface and smallest next
   discriminating probe;
4. near 80% of the budget, stop broad research and require either an edit, an
   explicit blocker, or the verification plan; and
5. suppress further advisory localization once the agent is already editing or
   verifying the correct surface.

This uses GT deterministically: GT detects state equivalence and chooses a
predefined intervention class; nano still decides and writes the solution.

#### P6: harden tools without confusing RED with infrastructure failure

1. retain nonzero test exits as useful RED with their output;
2. preserve the isolated-shell handling for model-authored `exit`;
3. distinguish command timeout, dependency/environment failure, agent-command
   error, product failure, and persistent shell death;
4. make timeout/error responses state the exact recovery action;
5. prevent stale background-process assumptions after shell restart; and
6. fail the live gate only on an unrecovered harness lifecycle fault.

This work follows P1--P5 because the latest evidence does not support tool
errors as the principal GT regression.

#### P7: pre-live and live proof

No live run starts until:

- all local tests and Ruff pass;
- replay attribution is exact;
- forbidden harness paths are absent;
- every eligible feature is delivered at its correct lifecycle boundary;
- already-exposed capsules compact without losing the first exposure proof;
- predicate receipts are fresh and semantically valid;
- unchanged refusals and repeated no-gain actions are bounded; and
- the predicted token saving is positive on at least four of five immutable
  replays.

Then run the real five-task `nano + GT` workflow with
`deepseek-v4-flash`, temperature `1`, Profile 2, concurrency 5, and timeout
multiplier 1.0. Compare per task against the frozen GT-off rows. The live report
must include reward, iterations, input/output/cache tokens, wall time, tool
outcomes, request-size curve, delivered features and boundary, predicate
receipts, forbidden-path attempts, and the exact next action after every GT
delivery.

One clean run can prove wiring and demonstrate a candidate improvement. A
stable claim requires repeated GT-on trials because temperature-1 output is
stochastic; it does not require another GT-off run.

### First live result and measured follow-up

Run `30601595795` exercised the new bounded provider view on all five tasks.
Harbor completed all five trials and exact replay accounted for every provider
iteration and input token, but the run is not acceptance evidence: reward was
2/5 and the audit found two terminal-iteration deliveries with no following
provider request plus one false-positive filesystem-isolation finding.

The trace isolated three concrete defects:

1. after the context threshold, the provider view retained exactly two complete
   tool turns even when the remaining character budget could safely retain
   more, discarding recent semantic work;
2. progress intervention was bounded per normalized signature rather than per
   task, producing 7--22 recovery capsules on individual tasks; and
3. the isolation audit treated explicit `.gt` exclusion expressions as access.

The follow-up therefore:

- greedily retains up to eight complete recent turns while remaining inside the
  existing character budget;
- caps progress interventions at two per task;
- forbids sealing any tool-result delivery when no provider iteration remains;
- distinguishes explicit `.gt` exclusions from actual `.gt` access; and
- makes five-way parallelism the workflow default as well as an explicit live
  dispatch input.

The next live run must use `concurrency=5` exactly. It remains a GT-on run only;
the comparison continues to use the frozen GT-off baseline.

### Corrected five-way live result: run 30603315821

Run `30603315821` used commit `d9ab376`, `deepseek-v4-flash`,
temperature 1, Profile 2, timeout multiplier 1.0, and
`n_concurrent_trials=5` exactly. Harbor launched and completed all five trials
in one invocation. Four repositories earned reward 1; `build-cython-ext`
earned 0. The batching repository passed all six verifier tests, but Harbor
correctly recorded the trial as errored because the agent exceeded the
1800-second outer timeout before producing a clean terminal result. Therefore
the live gate failed and this run is not clean acceptance evidence.

Per-task results:

| task | reward | terminal state | iterations | input | output | deliveries | witnessed |
|---|---:|---|---:|---:|---:|---:|---:|
| build-cython-ext | 0 | max iterations | 100 | 905,998 | 28,558 | 3 | 4 |
| headless-terminal | 1 | clean end turn | 49 | 682,926 | 38,665 | 6 | 8 |
| llm-inference-batching-scheduler | 1 | outer agent timeout | 54 | 1,112,185 | 98,392 | 4 | 7 |
| reshard-c4-data | 1 | clean end turn | 48 | 1,226,805 | 62,416 | 4 | 6 |
| sanitize-git-repo | 1 | clean end turn | 28 | 367,140 | 14,301 | 4 | 6 |

The follow-up fixes were directly witnessed:

- exact iteration replay reported zero issues for every task;
- all 21 sealed capsules were observed in provider requests and expired after
  exposure; no delivery was left unexposed;
- progress interventions were bounded to 2, 1, 1, 0, and 2 respectively;
- the isolation audit reported zero forbidden path attempts on every task;
- all seven lifecycle boundaries were observed across the run; and
- the run witnessed nine canonical identities and exercised fifteen, with no
  dark or faulted feature.

The frozen GT-off comparison is mixed and does not establish superiority. On
the four tasks with frozen rows, GT-on reduced input tokens by 66.2% for build,
9.2% for reshard, and 71.7% for sanitizer, but increased batching input by
99.5%. Output tokens increased by 35.4%, 194.6%, and 197.5% on build, batching,
and reshard respectively; sanitizer decreased 4.0%. The frozen baseline passed
all four, while this run produced three reward passes and only two clean
non-timeout passes among those four.

The traces now isolate the remaining control defects:

1. batching started a model-authored command with `timeout=2500` near the end
   of an outer 1800-second agent budget; the repository output happened to
   pass, but the trial could not terminate or emit verification-plan receipts;
2. build spent 100 iterations rebuilding/installing the package while leaving
   NumPy-2 removed aliases in the installed Python sources; and
3. compact input context is no longer the dominant cost on those tasks, but
   verbose model output and insufficient wall-clock-aware stopping remain
   severe.

The next change should be researched and tested as wall-clock-aware bounded
tool execution plus a deterministic verify-and-finish boundary. Increasing the
Harbor timeout would hide the defect and would violate frozen-baseline parity.

### Implemented candidate after run 30603315821

The earlier “retain up to eight turns when budget permits” policy is now
superseded. It reduced iteration count but raised average input per iteration
by 88.9% in the corrected five-way run. Spare context-window capacity is not
an instruction to refill the request with old observations.

The candidate implementation follows the full contract in
`gt_delivery_timing.md`:

1. **Step-0 graph orientation.** Task start now renders three to five bounded,
   obligation-linked file/symbol targets into the same sealed block as the
   complete task contract. The block remains one dose, but independent
   `obligations`, `localization`, and `GT_LOC_RESLOT` receipts all join to
   delivery `0`. The provider and immediate response must both report
   iteration 1.
2. **Semantic active context.** Durable history remains complete for audit,
   while the provider view defaults to the task, typed GT state, and two
   complete recent tool groups. It may retain one older group only when the
   group contains an active changed path or current RED identifier and still
   fits the smaller target budget. Omitted groups are receipted by content
   hash, not raw text.
3. **Typed verify-and-finish state.** The deterministic checkpoint now carries
   unresolved/verified obligations, changed paths, latest edit, latest fresh
   GREEN, predicate-receipt count, current RED, progress state, wall-clock
   budget, and a bounded next action. A fresh post-edit GREEN with no current
   RED recommends `summarize_and_submit`; stale verification recommends the
   smallest unresolved check.
4. **Wall-clock affordability.** The GT Harbor arm passes its effective agent
   budget to nano. `Agent.run` starts a monotonic deadline, reserves 180 seconds
   for termination, clamps every valid bash timeout to the remaining
   affordable window, and refuses to start a command when only the finish
   reserve remains. GT-off is unchanged unless the explicit time-budget option
   is supplied.
5. **Timing and budget proof.** Attribution now reports exact provider
   iterations per canonical fact and capability. The auditor proves pre-edit
   occurs before a changed tool dispatch, post-edit follows it, task-start
   localization is compound with obligations, and requested/allowed tool
   timeouts do not exceed remaining time minus the reserve. The live gate
   treats localization at iteration 2 or later as a failure even if it was
   eventually exposed.

The paid live run remains gated on local integration, all-17, full-suite,
Ruff, diff, and workflow validation. The next run is GT-on only:
`deepseek-v4-flash`, temperature 1, Profile 2, timeout multiplier 1.0, and
concurrency exactly 5. Comparison remains against the frozen GT-off rows; no
new GT-off run is authorized.

### Live result for the timely-context candidate: run 30606642296

Run `30606642296` tested commit `58655d4` with the required live configuration:
`deepseek-v4-flash`, temperature 1, Profile 2, timeout multiplier 1.0, and
concurrency exactly 5. Harbor completed in 15 minutes 33 seconds without an
outer timeout. The initial workflow audit failed only because its isolation
parser treated commands that explicitly pruned or excluded `.gt` as forbidden
access. Recomputing the immutable artifact after correcting that parser yields
`passed=true` with zero issues and all five task verdicts
`GREEN-delivered`. This correction changes audit classification only; it does
not change rewards, tokens, trajectories, or feature witnesses.

| task | reward | stop | iterations | input | output | deliveries | witnessed identities |
|---|---:|---|---:|---:|---:|---:|---:|
| build-cython-ext | 0 | max iterations | 100 | 445,991 | 32,306 | 3 | 3 |
| headless-terminal | 1 | max iterations | 100 | 529,229 | 78,148 | 9 | 9 |
| llm-inference-batching-scheduler | 0 | max iterations | 100 | 579,011 | 17,335 | 5 | 6 |
| reshard-c4-data | 1 | max iterations | 100 | 622,620 | 67,830 | 5 | 8 |
| sanitize-git-repo | 1 | end turn | 47 | 255,209 | 30,652 | 4 | 8 |

The delivery-timing and safety assertions succeeded:

- all three eligible task-start localization capsules were in provider
  iteration 1 and linked to response iteration 1;
- the run witnessed 10 canonical identities and exercised 15, with no dark,
  faulted, or unexposed feature;
- every bash call had exactly one budget receipt, with zero timeout violations,
  clamps, or rejections;
- all lifecycle ordering checks passed; and
- all five isolation censuses are clean after the exclusion-parser correction.

The efficiency result is not superiority. Against the four frozen GT-off rows,
this candidate earned 2/4 rather than 4/4 and used 347 rather than 195
iterations. It reduced input tokens from 5,884,607 to 1,902,831 (-67.7%), but
increased output tokens from 90,374 to 148,123 (+63.9%). The deterministic
context compactor therefore worked, but correctness and termination control
regressed. Lower input cost cannot compensate for two failed tasks.

Exact verifier diagnosis:

1. `build-cython-ext` built local extension objects but never completed the
   task's required global installation. The final trajectory was still
   inspecting build surfaces at iteration 100.
2. `llm-inference-batching-scheduler` never created the required
   `/app/task_file/output_data/plan_b1.jsonl` and `plan_b2.jsonl` artifacts.
   It repeatedly edited model and packer code despite those output paths being
   present in the task-start contract.
3. `headless-terminal` and `reshard-c4-data` had already reached passing end
   states but continued to iteration 100. A typed checkpoint recommendation
   was insufficiently salient to terminate the live model loop.
4. The graph ranker overvalued a fact matching one generic word across many
   obligations. On the sanitizer task this put an unrelated metadata-filter
   surface ahead of the actual target.

### Deterministic control follow-up after run 30606642296

The next candidate addresses those trace-proven defects without adding model
reasoning:

- the request checkpoint includes remaining iterations, bounded text and
  predicate type for the five highest-priority unresolved obligations, the
  last concrete action, exact missing required artifact paths, and
  present-but-unverified artifact paths;
- at 50% of the iteration budget, missing required artifacts cause one
  explicit lifecycle control message that orders artifact creation and an
  executable existence/content check;
- a fresh post-edit GREEN causes one completion control message; at 80%, one
  finalization message forbids repeated search and names the smallest remaining
  requirement or missing artifact;
- these controls are one-shot typed state transitions recorded as
  `progress.control_issued`, not repeatedly retained evidence blocks;
- artifact presence remains advisory state and never becomes a verification
  receipt without an executable check;
- absolute Linux task paths are preserved by predicate compilation; and
- graph facts now require a distinctive weighted match. A generic anchor that
  occurs across many obligations cannot win by accumulating link count.

The sanitizer isolation parser regression is also pinned: `.gt` prune
expressions and prose stating that `.gt` is excluded are removed before the
forbidden-path census, while actual reads remain reportable.

Local acceptance for this follow-up requires the new progress-control,
artifact-readiness, graph-specificity, and audit regression tests; the complete
engine and all-17 suites; full pytest; Ruff; diff validation; workflow parsing;
and immutable-artifact replay. Only then may another paid five-task GT-on smoke
run. The live acceptance condition remains reward non-worse than the frozen
baseline plus per-task reductions that do not hide a correctness regression.

### Lifecycle-control live result: run 30608738489

Run `30608738489` tested commit `15deec1` with the same required five-way
DeepSeek V4 Flash configuration. Harbor completed in 18 minutes 43 seconds and
the artifact uploaded. The attribution gate failed, and unlike the preceding
parser defect, this failure is real: the sanitizer agent executed `ls .gt` and
`find .gt`, directly inspecting GT state inside the graded repository.

| task | reward | stop | iterations | input | output | controls |
|---|---:|---|---:|---:|---:|---|
| build-cython-ext | 0 | max iterations | 100 | 487,883 | 40,738 | finalization@80 |
| headless-terminal | 1 | end turn | 53 | 275,778 | 40,060 | verified_completion@41 |
| llm-inference-batching-scheduler | 0 | max iterations | 100 | 620,725 | 16,866 | artifact_completion@50; finalization@80 |
| reshard-c4-data | 1 | max iterations | 100 | 669,939 | 85,211 | verified_completion@43; finalization@80 |
| sanitize-git-repo | 1 | end turn | 76 | 508,896 | 63,464 | none |

The controls were wired at their declared times. Headless improved from 100 to
53 iterations after its fresh-GREEN control. The batching control named the
missing output paths at iteration 50, but the immediate response only listed
the output/input directories and then resumed reading `cost_model.py`; neither
required plan file was ever created. A model-visible directive without a
pre-dispatch policy is therefore not deterministic loop control.

The result again fails the benefit gate. On the four frozen-comparable tasks,
reward is 2/4 versus 4/4, iterations are 376 versus 195 (+92.8%), input tokens
are 2,287,443 versus 5,884,607 (-61.1%), and output tokens are 206,279 versus
90,374 (+128.2%). All eligible step-0 localization remained on time (3/3);
10 identities were witnessed and 15 exercised; six lifecycle controls were
receipted. Those wiring facts do not offset the outcome and isolation failures.

The verifier failures remain exact:

- build never installed `pyknotid` globally, so 9 of 11 verifier tests failed
  with `ModuleNotFoundError`;
- batching never created `plan_b1.jsonl` or `plan_b2.jsonl`, so 5 of 6 verifier
  tests failed; and
- reshard and sanitizer passed, but consumed more iterations/output than their
  deterministic state justified.

### Isolation and enforceable-control correction

The trace requires a real boundary, not another prompt sentence:

1. Terminal-Bench now sets `GT_STATE_DIR=/tmp/.nano-gt-state`. `ensure_index`
   stores `graph.db` under a hash of the repository identity there and never
   creates `<repo>/.gt`.
2. With GT active, nano rejects direct tool access to `.gt`,
   `/installed-agent`, `/logs/agent`, and `/tmp/.nano-gt-state` before
   dispatch. Explicit prune/exclusion expressions remain legal.
3. Rejections are hash-safe `tool.control_decision` receipts. The audit reports
   rejected harness access separately from an executed forbidden-path
   violation; a blocked command is not mislabeled as filesystem access.
4. After `artifact_completion`, unrelated read/search calls are rejected while
   required outputs remain absent. Reads of the named input/output surfaces
   remain allowed; edits, generation commands, and executable checks remain
   allowed.
5. After `finalization`, broad read/search calls are rejected. A target named
   by the latest concrete failure traceback is still readable, and edits/tests
   remain allowed, so the boundary does not suppress a proven repair.
6. Unresolved end-state obligations are semantically ordered before rendering:
   install/deploy requirements first, required artifacts next, then explicit
   must/should conditions. Build's global-install requirement can no longer be
   hidden as item seven behind the three-item finalization limit.

No further paid smoke is justified until these rules pass focused isolation,
agent, engine, audit, all-17, and full-suite verification. The next artifact
must show either zero harness attempts or explicit pre-dispatch rejections and
zero executed violations.

## Research basis

- [Terminal-Bench](https://arxiv.org/abs/2601.11868): hard multi-step terminal
  tasks and comprehensive end-state tests make correctness the primary target.
- [SWE-agent](https://arxiv.org/abs/2405.15793) and its
  [ACI documentation](https://swe-agent.com/0.7/background/aci/): interface
  design and concise purpose-built interaction surfaces affect performance.
- [Agentless](https://arxiv.org/abs/2407.01489): bounded localization, repair,
  and validation can outperform more complex agent loops at lower cost.
- [RepoGraph](https://arxiv.org/abs/2410.14684): graph retrieval can improve
  repository agents, but indiscriminately flattening a larger two-hop graph was
  the worst tested variant; targeted subgraphs and early use matter.
- [LocAgent](https://arxiv.org/abs/2503.09089): hierarchical entity search,
  relation-aware traversal, and graph formatting improve fine-grained
  localization; whole-query retrieval is too coarse for difficult tasks.
- [ARISE](https://arxiv.org/abs/2605.03117): structural graphs alone miss
  statement-level value flow; definition-use slices improve function/line
  localization and downstream repair.
- [VRpilot](https://arxiv.org/abs/2405.15690): compiler/test/sanitizer feedback
  is useful when it is iteratively incorporated into the next repair decision,
  matching GT's need to connect RED to recovery and readiness invalidation.
- [SWT-Bench](https://arxiv.org/abs/2406.12952): executable fail-to-pass tests
  support more precise completion decisions.
- [Failure as Process](https://arxiv.org/abs/2607.09510): many failures begin
  before final submission, supporting early progress controls.
- [HarnessFix](https://arxiv.org/abs/2606.06324): trace-grounded cross-layer
  diagnosis is superior to treating final failure as an isolated model error.

## Repository evidence

- `gt_features.md`: historical/current feature map and live-run diagnoses
  through `30590129776`.
- `gt_engine/task_contract.py`: contract extraction and lexical matching.
- `gt_engine/graph_context.py`: 14-surface inventory and task projection.
- `gt_engine/evidence_router.py`: role/relevance suppression.
- `gt_engine/bridge.py`: lifecycle, refresh, recovery, verification, delivery,
  and attribution.
- `scripts/gt_audit.py` and `scripts/gt_live_gate.py`: evidence accounting and
  acceptance.
- `.github/workflows/tb2_gt.yml`: real nano + GT Terminal-Bench workflow.
