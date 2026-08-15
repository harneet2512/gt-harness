# nano-harness

## Graph-first persistent execution state (2026-08-12)

`gt_engine.persistent_execution_state.PersistentExecutionStateEngine` is the
task-scoped living repository-semantic state used by the central Mini-SWE host.
It is enabled only by `enable_persistent_execution_state=true`; OFF, AUDIT, and
certified-shadow isolation forcibly disable it.

Creation is graph-first. A graph-applicable task must finish repository transfer,
GraphDB build, schema/coverage validation, and source/graph-revision binding
before the state is created. The accepted five-channel `HybridRetriever` first
constructs the task-start candidate surface from that exact checkout and graph;
the identical result seeds the first live retrieval cache so dense/lexical/graph
work is not repeated. Only then may the host make exactly one bounded
bootstrap model call. That call can select and order immutable certified catalog
IDs and explicitly non-certified hybrid-ranked candidates; it cannot introduce a
path, symbol, command, obligation, or repository
fact. Its Bash envelope is data transport only: it is never executed, never
added to executor history, and is always counted separately and in total API
calls, tokens, cost, and latency. Invalid/timeout bootstrap output degrades to a
deterministic fallback so Mini-SWE can continue, but invalidates the intended
treatment release gate.

After bootstrap there are no planning or advisor calls. The same state engine is
used repeatedly: `compile_context()` before every executor provider request,
`project_preflight()` for every typed proposal before environment execution,
`commit_postflight()` after every executed model action, and
`rebase_graph()` after every source-changing graph refresh. A source edit marks
the graph state unavailable; no frame may be served until the refreshed graph is
complete and revision-current. Old catalog line/symbol labels are not presented
as current after graph revision changes. Newly certified current edges create
deterministic advisory obligations.

Production GraphDB relations are normalized at the state boundary (`CALLS`,
`ASSERTED_BY`, and `CALLS_TRANSITIVE` included); uncertified/co-change-only edges
cannot create obligations. Validation state consumes the one shared immutable
classifier, including its canonical declared-check ID. The state never reparses
raw Bash to infer a pass/fail result, and a read cannot satisfy a task-owned
deliverable creation obligation.

The full state remains host-private. Each normal executor request receives one
complete bounded current slice through the existing contribution compiler and
natural tool-observation surface: initial/critical at most 512 packing tokens,
delta at most 256, stable core at most 96. This is not another agent tool/action,
is not durable duplicated history, and cannot rewrite, suppress, or execute a
command. Exact task checks and deliverables may be blocking. A certified graph
relationship alone is only advisory: it proves a dependency exists, not that a
particular repair must touch it.

Every receipt exposes the field-level determinism boundary, the one bootstrap,
all state transitions, graph rebases, preflight reads, postflight commits,
context frames, exact request/provider hashes, changed message index, timing,
and total/executor/bootstrap resource accounting.
`scripts/central_release_gate.py` rejects bootstrap-only behavior, missing
per-call delivery, hidden extra calls, fallback selection, stale final graph
state, or a disabled applicable treatment. Provider-free proof establishes only
integration integrity. No solve-rate or efficiency benefit may be claimed until
a frozen matched evaluation counts the bootstrap overhead.

Release accounting is **17 + 1**, not a silently renamed 18-feature census.
`CENTRAL_FEATURE_IDS` remains the executable registry of the 17 historical
FACT/CAP feature paths. Persistent execution state is the additional product
mechanism and must be proven separately: graph-first creation, exactly one
bounded bootstrap, more than one deterministic state read/update when the
trajectory permits it, a bounded frame in each applicable dispatched executor
request, exact provider-view/request hashes, and postflight/rebase receipts.
Neither 17/17 feature proof alone nor persistent-state initialization alone is a
complete GT-on treatment proof.

Every active live receipt now carries `product_mechanism_census`. The integrated
product count is **18 mechanisms**: the exact 17 IDs in `CENTRAL_FEATURE_IDS`
plus `persistent_execution_state`. A treatment release fails unless all 18 are
configured and the persistent mechanism is exercised repeatedly across its
real lifecycle, not merely initialized. Repeated use is counted from the one
bootstrap plus deterministic context compilations, preflight projections,
postflight commits, and graph rebases. The receipt reports natural legacy
feature fires separately. A stochastic task need not contain every exact
trigger, so `N/17 fired naturally` must never be rewritten as `18/18 fired`.
The correct live statement is: 18/18 product mechanisms configured, persistent
state repeatedly exercised, and N/17 legacy features naturally fired with every
produced effect explicitly consumed/accounted.

The TokenRouter route in the DeepSWE workflow is diagnostic-only. It must
authenticate, confirm the exact `deepseek/deepseek-v4-flash-0731` catalog ID,
retain its distinct provider identity, and run that checkpoint with
`thinking.type=disabled` because DeepSeek thinking mode mechanically rejects
Mini-SWE's required Bash tool choice. The observed exact TokenRouter route did
not return a system fingerprint; the diagnostic therefore requires the
authenticated exact catalog ID plus stable response model/provider identity and records fingerprint
absence explicitly. A monetary cost may be zero only when the provider/LiteLLM
explicitly reports it; missing cost remains missing rather than fabricated.
It is never a substitute for the frozen OpenRouter comparison provider and
cannot be merged into an A/B claim.

Live diagnostic workflow `31671479023` at `2a34fb2` proved the repaired route
and the executable 17+1 accounting boundary. Its source-built provider-free job
and one-call canary passed. Before external censoring, the task receipt recorded
18/18 configured mechanisms, five naturally fired and consumed legacy features,
29 persistent-state lifecycle uses, and a persistent frame in all ten attempted
executor requests. The authoritative audit certified 12/12 visible deliveries
and 34 claims with zero duplicates, late, predictive, grounding, timing, or hash
failures. TokenRouter then returned HTTP 429 (`Maximum 10 requests within 1
minutes`) after nine executor responses. This is valid live mechanism evidence
but not outcome or efficiency evidence; the row is provider-censored and cannot
be counted as a GT failure or solve.

Provider-free workflow `31647174958` passed runtime commit
`e0c63ae15be6eeff9eae67ffe873f3b44e2da31f`:
current-source indexer build, pinned Snowflake ONNX asset, full central tests,
`READY`, `SMOKE_APPROVED`, all mandatory 17-feature census lines, and an uploaded
receipt with `provider_calls: 0`. This proves implementation integrity only. It
does not prove that persistent state improves solves or efficiency, and it does
not authorize a paid run. It predates the current marker/timeout, stable-core,
semantic-no-op, and final A/B gate repairs; those require a new exact-commit
source-built provider-free proof.

### Native-provider and dynamic-applicability repair (2026-08-13)

Archived 15-task workflow `31734290105` is rejected treatment evidence. Its
outcome arithmetic was 12/15 versus 13/15 in the historical local GT-off
cohort, but the arms did not have comparable token accounting and the intended
persistent mechanism did not run correctly. On all 11 initially source-backed
tasks, the one bootstrap call failed before selection because DeepSeek V4
thinking mode is incompatible with the forced Bash `tool_choice`. On the four
transfer-time source-less tasks, the graph could become healthy after the model
authored source, but the transfer-time abstention incorrectly remained sticky
and persistent state never activated. The run therefore proves neither benefit
nor harm from the living state.

The bootstrap now uses an explicit call-only provider envelope. For native
DeepSeek V4 and the separately supported TokenRouter route it sets
`thinking.type=disabled`, preserves the forced Bash selection, performs one
physical provider call, and records a sanitized typed provider error on
failure. This override is bootstrap-only; it does not alter executor sampling
or reasoning mode. The paid workflow's generic text preflight is forbidden.
It must freeze one exact commit, pass the reusable source-built provider-free
workflow for that SHA, then run `scripts.central_bootstrap_canary` through the
exact production bootstrap function and retain its receipt before task jobs can
exist.

Repository applicability is lifecycle state, not an immutable task-start
label. A task with no supported source correctly abstains and spends no
bootstrap call while it remains source-less. If an executed model action later
creates a captured, indexable source and the incremental graph becomes complete
and current, the host builds the shared hybrid corpus, performs the one
bootstrap, activates persistent state, commits the creating action against the
new certified revision, and includes a bounded state frame in the first next
executor request. If activation fails, Mini-SWE continues but the treatment
fails closed. A task is denominator-excluded only if it never becomes
applicable. Release accounting starts context/preflight/postflight expectations
at the recorded activation action/call; a true never-applicable task must record
`correctly_abstained=true`, zero bootstrap calls, and zero exercised state.

Progress is a first-class provider-visible delivery surface. Its receipt must
carry plural claim IDs, the exact provider-view hash, changed message indices,
and `delivered_before_model_query=true`; otherwise the authoritative delivery
audit rejects it. A provider view retained by an existing compaction epoch must
record that transformation reason instead of reporting an unexplained change.

Local Python tests prove these boundaries, including a biting witness where
the source-creating activation action was previously dropped as stale. They do
not certify the shipped Windows index binary, live provider behavior, solve
uplift, or efficiency. The next authorized evidence is a new exact-commit Linux
provider-free pass; only after it succeeds may the exact bootstrap canary run.
No task smoke or benchmark is authorized from this working tree.


Minimal coding agent harness. Score >30% on Terminal-bench and SWE-bench Verified with the smallest, most readable harness possible. Karpathy nano-aesthetic applied to agent harnesses.

## Current Status
**Phase:** Implemented — core harness built and hardened; benchmark runs deferred (cost)
**Started:** 2026-05-02 · **Hardening landed:** 2026-06-17
**Owner:** Troy

Core loop, 3 tools, 2 providers, CLI, logger all built with tests (52 passing).
Loop hardening: per-step token cap, API retries, output-truncation recovery,
verify pass, 60s bash timeout, system prompt v2 (see design doc §3.5a).
Terminal-Bench 2.0 adapter (`eval/tb_agent.py`, Harbor) written but never run —
first benchmark execution awaits budget approval.

## GT central runtime: current behavioral truth

### Graph applicability boundary (2026-08-09)

The graph gate applies to source-backed tasks. If a task has no supported
source files after artifact/deliverable exclusion (for example, a model
checkpoint plus a vocabulary file), the engine records
`not_applicable_no_supported_source`, emits no graph facts, and excludes the
task from the repository-intelligence denominator. That is a correct
deterministic abstention, not a degraded fallback or invalid treatment. A
source-backed task whose graph is missing, stale, schema-invalid, incomplete,
or unavailable remains a hard treatment failure. Workflow acceptance must
honor the receipt's applicability/denominator flag instead of invalidating
every non-`passed` status.

### Incremental repository graph (2026-08-09)

The graph is an evolving substrate, not a one-time startup snapshot.  After
each finalized workspace action, the sensor captures all bounded changed-file
content, including extensionless/content-signature sources; classification and
indexing use the same resolver evidence.  Creates and modifications enter the
certified incremental index before the next model call.  Deletions and
source-to-data transitions force a full rebuild to remove stale nodes.  The
existing hash/file/byte limits remain the bound, but there is no arbitrary
eight-file truncation that can make a multi-file transition incomplete.

Unknown or non-source files are allowed to be captured for deterministic
classification and are excluded from source revision and graph facts.  A
missing capture or failed refresh is fail-closed; GT must never present a
previous graph as current.  This lifecycle is the contract for all supported
languages and basename/shebang forms.

### Hybrid retrieval and additive preemptive frame (2026-08-10)

The current shared retriever is `gt_engine.hybrid_retrieval.HybridRetriever`,
used by both ARB and the optional in-process Mini-SWE provider boundary. It
runs exact path/symbol, lexical, BM25, local Snowflake Arctic ONNX dense, and
GraphDB structural channels independently, then applies equal RRF (`k=60`) at
the unique-file level. It selects at most three complete checkout-backed spans
within budget. Graph structure includes directed edges, test assertions,
verified closure, and co-change facts.

Do not equate rank evidence with delivery certification. Active/changed paths
seed exact and graph retrieval, but generic path tokens are excluded from
lexical/BM25 and exact token overlap must be repository-distinctive. A separate
structural certification bit is required; co-change ranks but never certifies
by itself. Dense/sparse/structural evidence families must be kept distinct.
Stale, ambiguous, incomplete, failed, or over-budget evidence abstains without
inventing a fact.

`enable_preemptive_retrieval` defaults false and OFF/AUDIT/certified-shadow
force it off. Explicit active mode may append one bounded grounded
`PreemptiveFrame` to the exact next provider request while retaining every
existing 17-feature/context-frontier payload. It adds no agent action or model
call and cannot rewrite, suppress, execute, or predict an action. Its receipt
binds candidate/channel ranks, evidence/action/call timing, provider message
index and hash, latency, payload size, deduplication, and model identity.

The GitHub ARB path uses local
`Snowflake/snowflake-arctic-embed-m@7802add0519e4bf94c46ef23552176697c7a1ac7`,
verifying ONNX SHA-256
`564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
It uses the published query prefix, CLS pooling, 512-token limit, L2
normalization, and no inference/provider API. Ranked and delivered ARB views
are separate. Until the 427-row run completes, this is implementation proof,
not evidence of retrieval or coding-agent uplift.

The active benchmark path is a host-owned engine in
`eval/gt_central_agent.py`, not the legacy installed inline runtime. It owns
the model/action loop, observes every execution transition, and keeps GT code,
state, credentials, and receipts outside the task container. The model never
asks a GT tool for help.

Do not equate a produced feature receipt with working integration. A triggered
feature must apply its typed payload to operational controller state. When the
model needs the result, the engine enriches the first provider request after the
evidence action with one bounded grounded payload. The pre-action boundary can
return a mechanically contradicted selected action for fresh model reasoning
only in `ASSISTIVE_SAFE`; it never rewrites or silently suppresses commands.

The 17 feature identities all have a registered consumer (`central_controls.py`);
most effects are internal and cost zero prompt tokens. The source revision is
separate from the whole-workspace revision: caches, binaries, build products,
logs, and background output never stale validation evidence. One immutable
validation classification is shared by the runtime, the evidence ledger, the
receipt, and deep metrics. OFF and SHADOW preserve chosen batches unchanged;
ASSISTIVE_SAFE permits read/search batches but breaks stale state-changing
suffixes. Fresh evidence is inserted before the next model query starts, never
one reasoning step later and never before its evidence exists. Every
model-visible payload must name concrete paths, symbols, commands, checks, or
diagnostics; related feature payloads are coalesced to avoid context spam.

Validation intent is not a result certificate. The shared classifier records
UNKNOWN/PENDING/PASS/FAIL, and only a terminal foreground validator may own the
outer shell status. Reads of verifier files, background commands, trailing
reporters, and unproven pipelines cannot pass or fail an obligation. The typed
shell adapter preserves command newlines and keeps heredoc/interpreter source
opaque, so source strings and diagnostics never become fake targets.

The paid path uses bounded deterministic context compaction: exact semantic
duplicate turns (including tool status, but excluding transport-local IDs) are
removed first, then only older turns are compacted once the 70%-of-400,000
character envelope is exceeded. The latest two turns and a typed current-state
frame survive; below the threshold the history is unchanged apart from exact
duplicates. No LLM summarizes context and unique reasoning is never silently
removed. Each call hashes the provider-prepared messages after private metadata
is stripped. `integration_mode=off|audit|active` is the one-switch policy; the
paid workflow explicitly selects ACTIVE with SHADOW preflight, completion
certificates, progress control, and the exact task-owned Harbor deadline.
Disabled task-start localization cannot surface on call two, and new-file
precedent is one-shot per task.

Workflow `31068690296` is a rejected diagnostic smoke: official reward was
9/10, but uncensored resolved was 8/10 because `write-compressor` gained an
outer 900-second timeout. Six tasks had a positive resource dimension and two
of six provider payloads were semantically wrong. The repairs permanently
classify serialized data/model files as derived artifacts, canonicalize
`/app/...` task deliverables to sensor-relative paths, and require a non-empty,
semantically ranked new-file precedent. Empty `__init__.py` is not useful
precedent. Do not cite this run as an approved GT win; 89 remains blocked.

Completion and deadline controls are now conservative and measurable. Host
workflow text is removed before extracting obligations; auto-submit is enabled
only when every remaining obligation has an executable, current, passing
predicate. A certificate invokes the existing submit marker once and cancels
pre-decided suffixes; predicate checks and submit attempts are counted in
`effective_actions`. Harbor's exported `task.toml` timeout is passed to the
agent with a reserve so the engine returns before outer cancellation. Reward,
outer-censor state, solver exhaustion, and uncensored resolved are reported as
separate fields.

Provider-free proof is gated by `python -m scripts.central_feature_census` and must
print all of:
`ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
`ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`,
`ALL_17_CONSUMER_PATHS_PROVEN`, `ALL_17_TRIGGERS_PROVEN`,
`ALL_17_PAYLOADS_CONCRETE`, `ALL_17_CONSUMERS_APPLIED`,
`ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST`, `NO_ACTIONS_BLOCKED`, and
`ALL_EFFECTS_CONTEXT_ACCOUNTED`.
Receipts are schema v3 with effect-application and exact request-boundary proof.
The 89-task run
remains blocked until the ten-task treatment smoke and repeated matched trials
pass. See `AGENTS.md` for the executable contract.

Before a paid smoke, run `python scripts/central_pre_smoke_gate.py`. Only its
`SMOKE_APPROVED` terminal line authorizes dispatch: it verifies both census
entrypoints, the exact paid workflow, and a deterministic all-17 run through
the real `MiniSweCentralAgent` lifecycle, including terminal submit effects.

The additive `features.effect_trace` ledger distinguishes application from
downstream influence. It records existing consumer reads and confirmed
provider-delivery IDs; `audit_only` is not trajectory influence. This tracing
must not alter model prompts, effect routing, timing, action order, shadow
visibility, or submit behavior.

### Pre-action implementation state

Every selected Bash action is classified once and adapted to a typed
`ProposedAction` before host execution. `ActionCycleReceipt` joins proposal,
candidate preflight decision, applied policy, actual dispatch, postflight, and
reconsideration. Modes are `OFF`, `SHADOW`, and `ASSISTIVE_SAFE`; the paid
workflow is pinned to SHADOW. Timeout and parser failures fail open to the
original command with a receipt. Production rewrite and feature-driven
suppression are disabled. Five features remain strictly postflight-only;
twelve have evidence-gated two-sided placement. See
`GT_PRE_ACTION_IMPLEMENTATION_RECEIPT_20260805.md` for proof and remaining
benchmark gates.

### Deterministic context compiler state (2026-08-05)

GT is the in-process Mini-SWE controller and context compiler, not a stream of
extra advice. Every provider request now passes through a typed fact compiler.
It proves whether each current fact is already represented at exact provider
message indices, is selected into one bounded declarative state frame, remains
controller-only, is stale, or is omitted by the frame budget. The per-call
invariant is `candidate_fact_count == accounted_fact_count`; the final request
hash proves what the model actually received.

The compiler preserves all distinct Mini-SWE reasoning. The paid compaction
transform is enabled, but it may change only tool-observation bodies: oversized
results are bounded, exact duplicate results are represented append-only, and
old tool bodies may become hash/return-code receipts. Assistant content and
reasoning are never removed, and the compiler does not emit a recurring state
frame. Grounded decision evidence uses the bounded one-shot semantic-delivery
path. The exact provider-prepared request must pass the hard context-headroom
check before `model.query()` or guidance-delivery confirmation.

Compound Bash is classified segment by segment. Typed read observations carry
canonical path, requested line range, source/workspace revision, return code,
and output hash into controller state. Validation classification is bound only
to the actual runner segment; setup/reporting segments do not inherit it, and
shell programs such as `sed 's/x/y/'` are not treated as file targets.

Every feature effect receives first-eligible context accounting without being
misreported as model influence. `provider_payload` proves delivery;
`controller_state_considered` proves private deterministic controller work;
superseded, stale, audit-only, existing-actuation, and no-next-call outcomes
stay explicit. Next-action anchor alignment is a utilization proxy, not proof
of an internal model acknowledgement. A matched benchmark is still required
for a causal efficiency claim.

### Final ten-task correctness audit

The final paid treatment smoke is workflow `30954660207` on commit `e7418a7`.
All ten task jobs completed successfully. Every receipt had all 17 features
enabled; 372 effects were produced and applied. The effect breakdown was 297
engine-internal state effects, 11 existing engine-actuation effects, 48
audit-only effects, and 16 provider-payload effects. Fourteen payloads reached
the model; all were concrete, grounded, non-predictive, and delivered in the
first eligible provider request. Late deliveries: 0.

The source-precedent bug is fixed and tested. A `newfile_precedent` trigger must
be a regular model-authored validation-relevant source file with a recognized
source suffix; sibling candidates receive the same classification. The payload
contains only the selected source trigger, not every path in the workspace
transition. The final smoke had 10 valid precedent payloads and zero cache,
binary, generated-output, or task-output paths. The earlier run
`30952995623` is not valid evidence because it still exposed the whole created
batch.

Efficiency against the frozen GT-off baseline was: tokens `-9,135,151`
(-31.26%), API calls `-51`, assistant steps `-53`, and actions `-103`. This is
one matched smoke at temperature 1, so it is an efficiency signal rather than a
causal model-quality claim. The 89-task run is not yet started; it is ready for
the next gated evaluation from this commit or a descendant.

## What this is
An agent harness — the code that wraps an LLM and turns it into something that does work (loop, tools, context management, system prompt). This one is single-purpose: a coding agent. Built to score on benchmarks while staying tiny enough to read end-to-end.

## What this is NOT
- A general agent framework (no plugin system, no extensibility for arbitrary domains — that's Archon/DeerFlow's lane)
- A product with a UI (no dashboard, no auth, no SaaS — that's Agent OS's lane)
- A chat assistant (single-turn-ish, task-completion-focused)

## Decisions made
- **Scope:** Vertical coding agent (option A). Educational nanoharness (option C) emerges naturally from minimalism. Framework (option B) deferred indefinitely — premature abstraction kills these projects.
- **Benchmark target:** Terminal-bench primary, SWE-bench Verified secondary. Terminal-bench because (a) less crowded, (b) the harness shape (shell loop + minimal tools) is terminal-native, (c) attention is rising, (d) >30% is still respectable there. SWE-bench Verified runs the same harness for cross-validation credibility.

## Decisions pending
- **Model strategy:** Frontier-only vs provider-agnostic vs multi-model leaderboard table
- **Minimalism budget:** LOC ceiling, system prompt token ceiling, dependency count
- **Architecture sketch:** Loop shape, tool set, context management strategy
- **Repo layout:** Single file vs small file tree

## Files in this project
- `CLAUDE.md` — this file (project context, current status)
- `memory.md` — running decision log + notes that are project-specific
- `skills.md` — which skills to use during this work and when
- `docs/superpowers/specs/2026-05-06-nano-harness-design.md` — approved design (v1.1)
- `eval/tb_agent.py` — Terminal-Bench 2.0 adapter (Harbor `BaseInstalledAgent`)

## Strategic context
Tracked in user-level memory:
- `reference_agent_harnesses.md` — competitor watch list (Archon, DeerFlow 2.0, etc.)
- `reference_archon.md` — primary reference harness
- The wedge: "None of the popular harnesses publish benchmark scores. They compete on features. Nano-harness wedge: minimal harness with published >30% scores. Score-per-line-of-code as the differentiator."

## Working norms
- Don't auto-commit by default (Troy decides when to commit).
  - **Exception:** when executing a written and approved implementation plan from `docs/superpowers/plans/`, per-task commits specified in the plan are pre-authorized. Commit messages and staged-file lists must follow the plan exactly.
- Brainstorm → design doc → user approval → writing-plans → implementation. No code before design approval.
- Karpathy aesthetic: small, readable, end-to-end legible. If a file passes ~500 lines without a damn good reason, the design is wrong.

## GT coverage and engine-accounting rule

Keep these claims separate in every report:

1. The provider-free census proves all 17 producer/consumer paths.
2. A paid trajectory fires a feature only if its receipt set contains that
   feature ID.
3. A feature is consumed when its effect is applied and has a recorded
   downstream disposition.
4. A feature is model-delivered only when its grounded effect ID appears in a
   confirmed guidance delivery.

The ten-task smoke `30976148466` naturally fired 15/17 features; `recovery`
and `signature_delta` were absent because their exact triggers did not occur.
It produced 361 effects; 36 were model-visible payloads. Never describe that
as “GT produced only 36.”

Private effects are not automatically useless. Read the effect trace and
separate `engine_internal_state`, `existing_engine_actuation`,
`provider_payload`, `audit_only`, and `unread_private_state`. Producer-side
deterministic engine work counts as engine activity even when it does not emit
model text. Usefulness requires a downstream state read, decision-frame
contribution, validation/batch action, or provider delivery. The detailed
archived comparison is in
`GT_SMOKE_30976148466_BASELINE_COMPARISON.md`.

### Latest context-compiler smoke and hard gate

Smoke `31061665540` at `a45601f0ba05` preserved reward 9/10 and passed every
integration-accounting invariant, but failed the experiment gate. It had a new
outer Harbor timeout on `cobol-modernization`, a step-capped `schemelike`, six
strict per-task Pareto failures, and +13.33% aggregate normalized token cost.
Do not promote it and do not start 89 tasks.

The live receipts also proved that `edit_target_absent` was an invalid material
preflight rule: 104 normal edit/scratch commands became candidate returns.
Shadow mode prevented execution changes. Current code makes absent targets PASS
and provider-free replay yields zero such candidates. Never restore this rule.

Outer Harbor exceptions occur after the last central receipt and must be joined
from trial/merged results. Context accounting must include bounded compiler
state-frame characters as well as active guidance characters. The complete
audit is `GT_SMOKE_31061665540_CONTEXT_COMPILER_AUDIT.md`.

### Regression repair after workflow 31078501162

The later paid smoke `31078501162` regressed to 7/10 and is also rejected.
The scheduler reached 100 steps because task outputs were misclassified and
novel observations could clear budget risk without task progress. The
compressor exceeded the provider context limit because the old compactor
could remove distinct reasoning yet still produce an oversized request.

Current code uses typed task-resource roles, output-only deliverable
projection, non-certifying output-existence progress probes, sticky budget
risk, reasoning-preserving tool-result compaction, and an exact pre-query
provider budget. Over-budget exits are internal solver exhaustion, not Harbor
censoring, and unsent guidance is not marked delivered. New receipts include
provider headroom, stable-prefix/cacheability, bounded-observation, completion,
and task-progress metrics. This is provider-free proof, not a recovered solve
claim; another matched smoke still requires authorization and the 89-task run
remains blocked.

### Feature applicability and graph-runtime repair (2026-08-06)

The corrected smoke's 13/17 statement was incomplete. Across its ten tasks,
38/38 repository refreshes were `index_unavailable`; `caller_contract` and
`def_partition` were therefore infrastructure misses. Only `recovery` and
`signature_delta` were valid exact-trigger absences. Of 100 localization and
reslot receipts, only four carried concrete anchors.

Current paid workflows install the vendored GroundTruth wheel, export the
pinned index binary, and execute a real binary-to-SQLite fixture before any
provider call. Definitions, references, and certified directed callers come
only from graph roles; grep prose cannot create them. Search filters,
ambiguous output, unsupported source, and incomplete graph evidence record a
typed abstention and emit no empty effect.

Every task receipt now reports feature applicability as fired, correct
abstention, trigger absent, ambiguous, substrate unavailable, or missed. Deep
metrics expose the corresponding feature IDs plus false fires. Provider facts
must be coalesced into their first eligible call or remain controller-only;
they cannot leak one step late. The all-17 census additionally requires zero
eligible misses, false fires, empty localization, unverified callers, and
duplicate frame evidence, plus a real repository-substrate proof.

### Latest outcome gate — workflow 31142998081 (2026-08-06)

The post-repair matched smoke at `5c92a6a` is **rejected**: 8/10 rewards
against the frozen GT-off 9/10. `schemelike-metacircular-eval` newly lost at
the internal step cap, with no outer Harbor censor. Do not aggregate its token
drop into an efficiency claim and do not start the 89-task run.

The failure is not a missing-feature or timing failure: all ten tasks enabled
17 features; 304/304 effects applied; 15 IDs fired naturally; the remaining
two had no eligible exact trigger; all 459 shadow preflights passed without
execution changes; and five payloads were grounded, first-eligible, timely,
and non-predictive. The lost task's initial provider request was byte-identical
to GT-off, but its temperature-1 first action already differed. This rules out
a direct first-turn GT delivery cause, not later bounded compaction or the
single action-71 validation payload. Read
`details_done/GT_SMOKE_31142998081_OUTCOME_AUDIT_20260806.md` before changing
features or funding another run.

### 10/10 versus 8/10 comparison (2026-08-06)

`31136099371` was a real GT-on 10/10 paid smoke. Its configuration matches
the later rejected 8/10 smoke: task slice, model/temperature, active/shadow
modes, compaction, controls, step cap, and budgets. The first schemelike
provider request was byte-identical, while the temperature-1 first response
already differed. `5c92a6a` changed graph runtime/semantics and first-window
delivery, not the compactor, completion, or progress code. The sole visible
schemelike payload was byte-for-byte the same in both runs.

The observed loss is neither proof that the repair caused a regression nor
proof of harmless noise. The next gate is a provider-free request-diff and
fixed-trajectory replay, followed by a separately authorized component
ablation. Details:
`details_done/GT_ON_10OF10_VS_8OF10_COMPARISON_AND_PLAN_20260806.md`.

### Provider-free run-diff gate (2026-08-06)

`scripts/central_run_diff.py` is the required offline comparator for two
GT-on artifact roots. It identifies the first model-action divergence,
attributes whether it predates visible evidence, compares prepared-request
hashes and context transforms, and fails on incomplete receipt accounting.
It must not call the provider or change any artifact. The replay CLI now works
both directly and as a module; both run forms and the comparator are in the
release gate. Full details:
`details_done/GT_PROVIDER_FREE_RUN_DIFF_GATE_20260806.md`.

### GT-on smoke 31145623534 (2026-08-07)

Run `31145623534` (`f03cb02`) matched the frozen baseline at 9/10 official
and uncensored resolves, with no outer censor. It passes engine integrity but
fails efficiency: the nine common solved tasks were +77,900 total tokens and
+55 API calls. All 330 produced effects were applied; 14/17 IDs fired
naturally (three exact events absent); six grounded payloads were first-call
timely with no late/predictive delivery; 456 request hashes and 8,125 facts
were accounted. The 89-task run remains blocked. See
`details_done/GT_SMOKE_31145623534_OUTCOME_AND_INTEGRITY_AUDIT_20260807.md`.
## Semantic-progress and compaction repair (2026-08-07)

The regression repair is implemented provider-free and is not yet approved for
a paid smoke. Progress now distinguishes workspace activity from semantic
gain; scratch commands, fixture resets, derived artifacts, and unvalidated
patches cannot clear budget risk. Compaction retains one bounded current-state
frame on the latest retained tool observation after clearing old tool bodies,
while preserving all distinct Mini-SWE assistant reasoning. Completion probes
carry dependency paths and use deterministic dependency-fingerprint caching.
Shell metrics separate context, output-only, opaque, and genuinely unknown
segments. The workflow remains ACTIVE + SHADOW, the provider-free census and
readiness audit pass, and the 89-task run remains blocked pending archived
replay and an authorized matched smoke.

## Outcome-preserving efficiency boundary (2026-08-07)

Validation recognition is not task-contract authority. Every validation action
has a typed authority (`NONE`, `CUSTOM_PROBE`, `STANDARD_RUNNER`, `DECLARED`,
or `HOST_SYNTAX`) derived from the shared normalized executable invocation.
Only a `DECLARED` check may create model-visible required-check failure text or
submission debt. Standard-runner failures may update private recovery state;
custom probes remain private. Every required-check receipt must name its
`declared_check_id`, and `required_check_claims_without_declared_id` must be
zero.

Completion is complete-only: a `PARTIAL` plan executes zero private predicates
and cannot produce a certificate. Adaptive action timeout is active-only and
may extend the historical 30-second timeout solely for a high-confidence,
terminal-foreground, literal-timeout `DECLARED` or `STANDARD_RUNNER` command.
It is capped at 120 seconds, 20% of remaining task time, and the deadline
reserve. Ambiguity and dynamic shell expressions keep the default timeout.

Compaction is based on the measured provider-prepared request, not raw history
size. Preserve the exact request while at least 131,072 tokens of reserve
remain (reserve is also capped at 25% of the hard prompt limit). Once required,
create one immutable compacted-checkpoint epoch and append later turns. Refresh
the bounded current-state frame only on a provider-view copy of the latest safe
tool observation; never mutate the checkpoint or freeze stale state into it.
Receipt every epoch and never remove distinct assistant reasoning.

All task-environment executions pass through `HostExecutionRecorder`, including
model actions, sensor manifest/hash/capture calls, syntax and completion probes,
and auto-submit. `effective_task_actions` is the actual execution count minus
host system-information calls; cache hits are separate. Never substitute model
action count for total task work. Deep metrics use schema v2.

Archived replay of run `31190135547` suppresses four non-authoritative visible
failure receipts on two actions, removes 28 partial-plan probe executions while
retaining five probes for the complete write-compressor plan, and projects zero
compaction epochs because reconstructed raw final provider requests retain at
least 211,100 tokens of headroom after a conservative advisory allowance. This
is provider-free policy proof, not a live efficiency claim. See
`details_done/GT_OUTCOME_PRESERVING_EFFICIENCY_IMPLEMENTATION_20260807.md`.

## Repository-intelligence regression boundary (2026-08-08)

Treat GT as Mini-SWE's deterministic repository-intelligence layer. Feature
receipts alone do not satisfy that contract. Every active coding task requires
a certified current repository substrate, but substrate health is separate from
retrieval outcome and provider delivery. A healthy `EMPTY` result or a fact
already `REPRESENTED` in Mini-SWE history is a correct accounted abstention,
not a reason to fabricate visible context. Execution fails open on substrate
failure so GT cannot destroy the model's work; evaluation and promotion fail
closed so a dead graph cannot hide behind model luck or favorable tokens.

The single language resolver drives source revision, workspace capture, syntax
probes, indexing, and both Mini-SWE bridges. It uses bounded content as well as
paths. Structural support matches the vendored `gt-index` resolver and specs.
Unsupported or ambiguous authored languages are explicit substrate failures.
Never fabricate definitions/callers to make coverage appear green.

Suffixes are candidates, not identities. `.v` is Coq or Verilog only when
bounded declarations prove one dialect; conflict or insufficient evidence is
`AMBIGUOUS` and invalidates source coverage. `.conf` becomes Nginx only from
recognized directives. Exact build-file basenames and extensionless shebangs
are resolved explicitly. Both Python and Go implementations share these
fail-closed rules.

The graph runtime has provider-free fixtures for R, Verilog, Coq, Stan,
SPARQL, Turtle, LaTeX, Vim, Nginx, G-code, Red, POV-Ray, Make, Dockerfile,
CMake, Meson, and Autotools in addition to the existing languages. Conservative
structural adapters emit only fixture-proven constructs; unknown syntax emits
no speculative edge. A non-empty source may receive a concrete file node, but
not an invented symbol. Parser failures are persisted and invalidate the
substrate.

Native parser caller indices are zero-based. Readiness requires real directed
SQLite edges for every fixture language that advertises caller support, not
just in-memory parser calls or nonzero nodes. COBOL out-of-line `PERFORM`
statements are attached only to the mechanically preceding paragraph header.
R assignment-bound functions use the AST `lhs` as identity. POV-Ray calls are
owned only by a parsed enclosing macro; file-level invocation cannot fabricate
a caller.

The checked-in Terminal-Bench 2 language contract pins the official dataset
commit and expected 89 tasks. Its exact-tree gate verifies every named witness
and declared source-like suffix family and independently rejects any
registry-recognized structural suffix observed but unclassified; registry
self-parity is not accepted as benchmark completeness. This proves language
resolution and graph construction only. It does not prove that retrieved facts
are relevant, model-visible, outcome-improving, or efficient.

Language support does not expand the workspace trust boundary. The sensor now
supports only explicitly named `/etc/nginx/**` and `/var/log/nginx/**` paths;
it records them with bounded metadata/content probes and mirrors authored Nginx
configuration under a safe `__external__/` graph prefix. It also probes a
bounded set of extensionless regular files and accepts one only when its
content proves a supported shebang. No arbitrary external or filesystem-wide
scan is allowed.

`graph.db` health requires a certified schema, FTS surface, current source
revision, binary hash, graph hash, and recorded node/edge/source counts. The
task mirror transfers only authored source and bounded project metadata before
the full build; checkpoints, datasets, binaries, caches, build outputs, and
task deliverables are excluded. The indexer uses real per-file incremental
refresh plus closure rebuild, atomic graph/manifest publication, and
exact-revision cache hits. Missing source, unsafe paths, incomplete transfer,
sensor degradation, stale revision, incomplete language coverage, or invalid
schema invalidate the substrate. Empty or low-relevance retrieval receives an
explicit non-delivery disposition without invalidating a healthy graph.

The context frontier runs before each model query and advances beyond provider
history. It may select only concrete source-backed definitions, signatures,
callers, references, tests, or bounded ranked anchors with path, positive line,
symbol, graph revision, source revision, semantic certainty, and retrieval
relevance. Limits are three facts/1,200 characters per call and 6,000
characters per task. Every candidate
has one disposition; facts are never truncated, duplicated, predicted, or sent
from stale evidence. The exact request hash and provider message index prove
visibility; later action alignment remains only a behavioral proxy.

If the graph is healthy and current but provides only a concrete high-confidence
ranked anchor, the frontier uses a bounded `FILE`/`SYMBOL` fallback rather than
silently keeping that usable source fact private. The fallback names only the
certified path, positive line, and optional symbol, is deduplicated against a
richer structural role or retained history, and never fabricates callers,
definitions, or intent.

Semantic certainty and retrieval relevance are independent gates. Graph
confidence cannot promote a generic or task-unrelated symbol. Semantic claim
IDs remain stable across source revisions for delivery deduplication; versioned
fact IDs preserve the exact graph/source evidence used in each receipt.
Multiple graph occurrences that share a semantic claim ID are coalesced before
selection, even when their physical lines differ, so one provider frame cannot
repeat the same claim.

Workspace scanning is skipped only for a parser-certified read-only proposal.
Opaque interpreters, unsupported syntax, shell uncertainty, and partial parse
coverage are `MAY_MUTATE` and retain the scan. GT never rewrites or suppresses
the selected model command in the paid SHADOW configuration.

Deep metrics and the paid merge must report graph schema/source/node/edge
health, frontier accounting and characters, provider exposure, controller
cost, model work, outcomes, and censoring per task. Frontier text is part of
`total_gt_context_chars_added`; omitting it is an accounting bug. Any required
task whose intelligence status is not `passed` fails the merged treatment gate
while preserving artifacts for audit.

`require_graph_ready=true` is an analytical treatment gate, not a provider-loop
kill switch. Substrate failure records a degraded fallback, permits ordinary
Mini-SWE execution without graph payloads, and fails the merged experiment.
This preserves potential baseline solves without allowing a graph-less run to
count as valid GT evidence.

Typed tool observations are bounded before provider use. Successful large
reads retain deterministic head, three interior windows, and tail rather than
discarding the entire middle. Soft checkpoint
compaction is considered at 120,000 provider characters toward an 80,000 target
only when the projected view saves at least 20,000 characters and 10%; smaller
changes are receipted and deferred to preserve the stable cache prefix. No
distinct assistant content or reasoning is removed. Hard provider-budget
headroom remains fail-before-query.

## Portable source capture boundary (2026-08-08)

The host-side `WorkspaceSensor` must not assume that a task image contains
Python. It first attempts bounded `python3 -c` JSON/base64 source capture,
then falls back to shell-native `base64 | tr -d '\\n'` records for validated
changed paths when Python is missing or returns malformed data. Both paths are
recorded as workspace-capture executions and decoded only for exact manifest
paths; hashes and metadata remain authoritative if both mechanisms fail. A
missing capture must never be silently treated as a current graph: repository
refresh becomes `mirror_incomplete`, execution fails open, and the paid merge
fails closed.

Diagnostic paid workflow `31270761663` exposed this boundary. COBOL had a
passed current graph but zero context-frontier deliveries because its
candidates were already represented in durable Mini-SWE history; its one
157-character guidance event is not evidence of causal help. `write-compressor`
solved, but after authored C edits its source mirror could not refresh because
the task image reported `python3: command not found`, so graph health was
`mirror_incomplete` with zero final nodes/edges. That run is rejected as GT
evidence. The portable fallback is provider-free fixed and requires a new
authorized matched smoke before any outcome claim.

The staged language-completeness implementation is recorded in
`details_done/GT_ALL_TERMINAL_BENCH_LANGUAGE_SUPPORT_IMPLEMENTATION_PLAN_20260808.md`.
Phase 0 inventory/fail-closed accounting and all-registered-parser binary
parity are implemented; R/Verilog native grammar work and Red/POV structural
support remain required before claiming full Terminal-Bench language coverage.

This repair is provider-free certified on exact implementation commit
`e6ce41f` by workflow `31244088870`. The checked-out Linux binary passed the
COBOL/Python/Scheme repository fixture; 311 workflow-scope tests, all-17 census
coverage, readiness, archived replay, and Ruff passed. This proves the
deterministic integration, not live outcome or efficiency. No post-repair paid
smoke has run; the 89-task run remains blocked pending a separately authorized
matched smoke.

The provider-free workflow also runs `central_pre_smoke_gate.py`; the exact
pushed commit intended for a paid smoke must print `SMOKE_APPROVED`. A green
parent commit cannot authorize a descendant.

## Native R/Verilog language stage (2026-08-08)

The branch now includes parser-backed R (`.r`) and Verilog (`.v`) support through
pinned upstream Tree-sitter Go bindings (`r-lib/tree-sitter-r v1.3.0` and
`tree-sitter-verilog v1.0.3`). The gt-index specs, grammar-scoped Verilog name
unwrapping, module-instantiation attribution, and provider-free fixtures are
included. Redcode (`.red`) and POV-Ray (`.pov`) use bounded structured
adapters: labels/control-flow for Redcode, and macros/declarations/includes/
local macro calls for POV-Ray. Unknown syntax remains source-only; regex-only
graph inference is prohibited.

Provider-free workflow `31273427487` at `d2ae8d7` compiled the cgo bindings and
proved R/Verilog definitions, directed edges, SQLite integrity, and complete
The expanded provider-free workflow `31274090882` at `2cdc8f2` also passed the
adapter build and fixture gate: R=2, Verilog=2, Redcode=1, POV-Ray=1,
42/42 source/file-hash coverage, SQLite integrity, six graph edges, central
census, readiness, static checks, and exact pre-smoke. This remains substrate
evidence only, not an efficiency or solve-rate claim. Retain the archived
replay and matched-smoke regression gates; the 89-task run stays blocked.

## Conservative uplift policy and provider baseline shield (2026-08-08)

The latest paid smoke `31282615178` is rejected outcome evidence: 8/10 versus
the frozen GT-off reference's 9/10. On the eight common solved tasks, GT-on
used 24.54% more tokens, 26.82% more calls/steps, and 22.74% more actions. A
single frozen temperature-1 baseline cannot establish causal regression or
uplift. Deterministic GT guarantees its own evidence, timing, abstention,
provider view, controller actions, and receipts; it cannot guarantee the next
sample from a stochastic model after provider-visible bytes change.

Every active consequence crosses one `CertifiedOpportunity` boundary. It
requires mechanical or certified-structural authority, current revisions,
concrete anchors, evidence identity, a decision need, absence from retained
provider history, and the exact first-eligible window. Heuristic/rank-only,
ambiguous, stale, duplicate, unanchored, or late candidates abstain. Feature
guidance, graph frontier facts, admitted preflight returns, and completion
auto-submit use this boundary. No GT LLM exists.

Before measured provider-budget pressure, the provider request remains the
stock Mini-SWE request unless certified bounded evidence is added. The removed
soft character trigger and eager per-observation transform must not return.
Current requested read/search output stays exact. Only a real budget-compaction
epoch may replace older tool bodies; assistant content/reasoning is immutable.
Facts are complete-or-quiet, never ellipsized.

Receipts separately record stock/final provider hashes and characters,
feature-guidance and graph characters, compaction removal/receipt characters,
changed message indices, and change reason. Durable trajectory + deterministic
replay + these exact hashes are the audit surface; do not add model markers.
Next-command anchor alignment is only a behavioral proxy, never acknowledgement
or causal proof.

Graph rank is candidate ordering, not relevance certification. Exact typed
READ/SEARCH/EDIT/CREATE source paths cause a cached re-query of the existing
current graph without an index rebuild. Delivery still requires >=0.95
structural certainty, mechanically assigned >=0.95 relevance, and an exact
path/symbol already present at the Mini-SWE decision boundary. Semantic claim
identity ignores line movement and revision churn.

The single central engine now has explicit `off`, `audit`,
`certified_context`, `certified_controllers`, and `certified_full` workflow
arms; default is `audit`, and paid preflight remains SHADOW. Release evidence
must use fresh balanced OFF/full ABBA/BAAB trials, at least two trials per arm
per task, hierarchical bootstrap, failure-capped resources, and outcome-first
confidence bounds. The frozen baseline is descriptive only. The 89-task run
remains blocked. See
`details_done/GT_CONSERVATIVE_UPLIFT_IMPLEMENTATION_20260808.md`.

The current provider-free release scope is complete: all 394 tests in the exact
workflow test scope passed, along with direct/module census, the real vendored
graph-runtime fixture, readiness, direct/module archived replay, ten-task run
diff, Ruff, compilation, workflow YAML parsing, and diff checks. The repair is
pushed at commit `567bca1`, and the exact pre-smoke gate printed
`SMOKE_APPROVED`. No paid run was started during this audit; the ten-task GT-on
smoke remains the next authorized measurement and the 89-task run remains
blocked.

## Trajectory causality audit (2026-08-09)

Use `scripts/central_trajectory_audit.py` before interpreting a paid run. It
separates deterministic GT work (`engine_internal_state`,
`existing_engine_actuation`, `provider_payload`, `audit_only`) from model-level
causality. A trajectory's anchor-following or semantic-utilization label is a
behavioral proxy, not proof that GT changed a model decision. Archived hashes
without provider message bodies and model replay state must produce
`MODEL_CAUSALITY_UNIDENTIFIABLE`; do not promote such a run as causal
efficiency evidence. See
`details_done/GT_TRAJECTORY_COUNTERFACTUAL_AUDIT_31297108410_20260809.md`.

Counterfactual capture is now available behind `enable_replay_capture=true`.
It is bounded and disabled by default; paid workflows remain unchanged. It is
model-agnostic and never injects a provider-specific seed or sampling control.
Use it for deterministic trajectory/controller replay; do not label that model
causality.

## Final regression-repair contract (2026-08-09)

GT source identity is semantic. SourceRevisionReceipt hashes canonical source path plus full-content SHA-256 only; raw workspace metadata remains a separate audit revision. Missing source digests invalidate graph refresh and completion certification without blocking Mini-SWE. Internal revision hashes are never model-visible.

Repository facts have persistent provenance (TASK_START, MODEL_AUTHORED, OBSERVED_EXTERNAL, or UNKNOWN) and exactly one eligible provider call. Task-start facts cannot spill, and new claims on model-authored paths remain controller-only. Genuine new cross-file consequences may remain eligible. newfile_precedent can use only a non-empty compatible task-start source and receipts precedent_origin=task_start_repository.

ProviderEvidenceLedger is the authoritative provider-context accounting surface. It joins graph_frontier, feature_fact, state_frame, progress_frame, and preflight_return events to evidence action, eligible/prepared/dispatched calls, exact provider message indices, request hash, characters, disposition, reasons, and revision. A represented fact with zero newly inserted characters is correct GT operation; never force provider text merely to avoid a zero-visible count.

Provider request lifecycle is explicit: provider_requests_prepared, model_query_invocations, provider_responses_received, and provider_requests_not_sent. api_calls equals actual model_query_invocations. An unsent prepared request confirms no delivery and contributes no visible context.

Deterministic compaction restores only a current fact whose last concrete provider representation it removed. It does not inject generic controller state, repeat adjacent frames, delete unique assistant reasoning, or truncate a fact. StallAggregateFact is a separately gated controller fact, not an eighteenth feature: deterministic, declarative, <=320 characters, at most twice per task, first-eligible, source-bound, and non-predictive.

Replay v2 is exact and content-addressed under gt_replay/ (manifest.json, calls.jsonl, blobs/<sha256>.json.gz). The verifier fails closed on corruption. Workspace source capture caches its working backend; a missing task-image python3 is not retried on every edit. Local graph resolution prefers the checked-out pinned gt-index binary over obsolete machine-global builds.

Efficiency gates aggregate provider/model resources only across common uncensored solves. Tokens, actual model calls, model-selected actions, assistant responses, cost, and wall time are primary. Effective actions and host/controller/sensor executions are reported separately. Cheap failed tasks cannot improve the aggregate.

Provider-free implementation evidence is recorded in details_done/GT_FINAL_REGRESSION_REPAIR_AND_89_GATE_20260809.md. The archived ten-task replay passed; this is not live outcome proof. The exact pushed pre-smoke gate passed on commit 567bca1 and printed SMOKE_APPROVED. The authorized ten-task certified_full/integrated GT-on smoke has now run at commit 8720ad9 with preflight SHADOW; its integrity passed but its outcome gate failed. The 89-task run remains blocked pending outcome-preserving efficiency evidence.

Live smoke 31343081886 (commit 8720ad9) completed with integrity certified but
the outcome gate rejected: GT-on resolved 8/10 versus GT-off 9/10. All ten
receipts enabled all 17 features; 447/447 preflights were PASS; 187/187
effects applied; 6,777/6,777 context facts accounted; and five grounded
feature plus seven graph-frontier deliveries were first-eligible, with zero
late/predictive/duplicate/ungrounded deliveries. `write-compressor` was the
new uncensored reward-0 deadline-reserve loss; `gpt2-codegolf` remained the
baseline-known miss. Aggregate tokens fell 21.12%, but this is not an
efficiency win after the solve regression. See
`details_done/GT_SMOKE_31343081886_DEEP_AUDIT_20260809.md`; 89 remains blocked.

## Generalized regression repair after workflow 31421610097 (2026-08-10)

Workflow `31421610097` is rejected evidence: GT-on solved 15/20 against the
frozen reference's 17/20, and `prove-plus-comm` plus `sanitize-git-repo` had
invalid graph substrate. Do not reduce the four solve losses to either
temperature variance or GT causation. All four first model actions diverged
before any GT evidence, but all four later received provider-visible progress
frames; most of those frames falsely said `STALLED` because distinct searches
and opaque experiments collapsed to one attempt identity.

Progress attempt identity now includes a hash of the exact selected command.
Command identity, observation identity, observation gain, and verified
task-progress gain remain separate. A different command prevents a false
repeated-action classification but is not itself task progress. Only an
attributed validation pass or confirmed task output may clear `BUDGET_RISK` or
support completion. Exact repetitions still produce the bounded one-shot stall
frame.

Action classification must not invert harmless and destructive behavior.
Redirecting diagnostics to `/dev/null` is not a workspace mutation. Generic
Git history/worktree mutations including `filter-branch`, `filter-repo`, `gc`,
`reflog`, and `update-ref` are typed mutating before execution. Replaying the
20 archived receipts under the repaired classifier removes 155 false mutating
actions and recognizes one previously missed destructive action; this is a
deterministic host-scan reduction projection, not a solve-rate claim.

Repository archive members and transforms are rooted at the resolved task cwd,
never hard-coded `/app`. Action targets are canonicalized against the same cwd,
so `/workspace/...` and nested `/app/<repo>/...` tasks can transfer and query
their graphs. Initially source-less tasks already retain an incremental
repository session and may index model-created source while remaining excluded
from the task-start graph denominator; do not add a second bootstrap path.

The frozen stock Mini-SWE reference and the host-central treatment differ in
loop/execution interface and therefore are not a clean causal GT ablation.
Keep the frozen result as an outcome target, but isolate causal GT behavior
with `integration_mode=off` versus `active` inside the same host loop when such
evidence is required. The complete diagnosis, research basis, tests, and
remaining release boundary are in
`details_done/GT_GENERALIZED_REGRESSION_ROOT_CAUSE_AND_REPAIR_20260810.md`.
The repaired implementation was pushed as `dd2884e`; its exact-pushed-commit
pre-smoke gate printed `SMOKE_APPROVED`. No post-repair paid smoke has run, and
the 89-task benchmark remains blocked.

## Call/step efficiency repair after smoke 31343081886 (2026-08-09)

Do not accept token savings when common-solved model calls or assistant steps
increase. Shell parsing separates executable argv from typed redirections:
`2>&1` is descriptor duplication, output files are side effects, and input
files are reads. Redirecting a declared validator cannot erase its validation
authority or its bounded adaptive timeout.

Progress accounting uses an `attempt_id` and an `observation_id`. Executable
exit conventions are typed; grep/rg no-match and diff differences are valid
observations, while shell code `124` and Mini-SWE's exact `return_code=-1`
timeout protocol are timeouts. Failed reads do not consume anchors. Workspace
activity, new observations, and actual task progress are distinct. Only an
attributed validation pass or confirmed task output advances task progress,
and a same-state stall/contradiction/budget update is private rather than a
second provider frame.

Graph facts are selected against the current decision boundary. A path alone
can justify a file anchor, not an arbitrary symbol or definition in that file;
structural facts need an exact symbol/relation anchor, and malformed symbols
abstain. Deep metrics report batching, actions per actual invocation, progress
identities, validator preservation/timeouts, and effective actions. The strict
gate now fails on positive `assistant_steps` or positive `effective_actions`
even when tokens fall.

Provider-free tests and archived replay pass. No repaired paid smoke has run;
the live efficiency effect is unverified and the 89-task run remains blocked.

Smoke 31351072175 exposed two accounting boundaries now fixed: semantic-use
matching must compare source revisions, never a guidance row's workspace
revision; and graph applicability is anchored to the task-start substrate, so
a source-less binary/data task cannot become a graph failure after the model
writes unsupported helper files. See
`details_done/GT_SMOKE_31351072175_AUDIT_AND_BOUNDARY_REPAIR_20260810.md`.

The authorized follow-up smoke `31352963297` at `34e712e` matched the frozen
baseline at 9/10, fixed the prior headless loss, and correctly classified
source-less GPT-2 as denominator-excluded. Common-solved tokens/calls/steps/
model actions fell, but controller-inclusive effective executions rose by
345, so the 89-task outcome-first efficiency gate remains blocked. See
`details_done/GT_SMOKE_31352963297_OUTCOME_AND_EFFICIENCY_AUDIT_20260810.md`.

## Regression repair implementation (2026-08-10)

## Final execution measurement contract (2026-08-10)

ARB measures whether retrieval found useful repository evidence. It does not
measure model reasoning, timing in the live loop, or task success. Those are
separate proof layers. The decision-point evaluator compares exact provider
requests with and without the production GT payload and grades only the next
external action using mechanical repository/task facts. No marker,
acknowledgement, or hidden-reasoning claim is permitted. Track execution in
`FINAL_EXECUTION_TODOS.md`; emit a 15-minute heartbeat while active, and do not
launch paid work without the relevant gate and authorization.

Opt-in replay capture now records the exact pre-GT control messages, exact
treatment messages, compiled payload, contribution IDs, revisions, timing, and
provider tool schema. A pair is eligible only if the recorded payload alone
reconstructs the treatment byte-for-byte and it is the first visible GT
intervention. `paired_decision_capture_ready` is capture integrity, not utility.
Run `31421610097` contains 1,051 treatment calls but zero eligible pairs because
the legacy capture did not retain exact controls; do not use it for the final
decision-point result.

The next repair pass corrected the two misleading conclusions from the
89-task treatment `31355487270`. `guidance_suppressed=2,264` was not a count of
withheld model guidance: the old counter incremented for almost every private
engine effect. The authoritative accounting is now disposition-based. In that
run the recorded totals were 2,365 effects, 2,337 private engine effects, 36
real guidance candidates, 28 candidate receipts, 26 coalesced provider frames,
6 facts already represented in history, and 8 candidates not delivered. Private
effects are not inert, but they are not model-visible guidance; the receipt
must identify `private_ineligible`, `candidate_delivered`,
`candidate_represented`, `candidate_window_unselected`, `candidate_stale`,
`candidate_budget_rejected`, `candidate_policy_rejected`, or
`no_eligible_model_call`.

The repaired substrate separates validation source revision from graph source
revision. A code deliverable remains graph-indexable; a JSON/data/task output
does not. Workspace capture is batched by byte/file bounds without dropping the
suffix after an arbitrary 100-file cap. Oversized source is transferred and
hash-verified before incremental indexing. Index failures retain bounded stderr
diagnostics. The graph mirror is source-only, bounded, and no longer writes
static `/tmp/gt-source-*` files: every transfer uses a unique mode-700 private
directory and verifies cleanup. The agent resolves the task cwd from the host
environment, validates a configured override, and records an explicit fallback
instead of silently assuming `/app`.

The controller repairs are equally conservative. Progress does not treat a new
output hash as task progress; only an attributed validation pass or confirmed
task output advances completion, while deadline risk is tracked separately.
Provider compaction measures actual request pressure, can bound the newest
oversized observation, preserves distinct assistant reasoning, and uses a
scaled target rather than a fixed 80k window. These changes default to PASS and
preserve the historical provider view below the compaction trigger.

Verification at this worktree: the focused central-runtime, agent-loop,
repository, provider-view, progress, and semantic-engine suites pass; the
all-17 census prints every producer/consumer/timing/payload/context-accounting
line; readiness is `READY`; archived 89-task replay and regression-preservation
replay both pass; and the strict pre-smoke lifecycle tests pass. The exact
pushed gate now prints `SMOKE_APPROVED` on `e38fa06`. That authorizes only a
separately requested ten-task paid smoke; the 89-task run remains blocked until
the smoke preserves outcome and passes outcome-first efficiency gates.
+
## Frozen hybrid retrieval and contribution compiler (2026-08-11)

Use Agent Retrieval Bench workflow `31517629497` and
`RETRIEVAL_BENCH_RESULTS.md` as the retrieval-only authority. The complete run
contains 427/427 rows at retrieval commit `433c330`. Do not reinterpret ARB as
proof that a model used the evidence or that an agent solved more tasks.

The benchmark adapter and live `MiniSweCentralAgent` share
`gt_engine.retrieval_profile.FINAL_RETRIEVAL_PROFILE`: channel limit 100,
top-K 20, selection limit 8, 1,200 evidence tokens, 12,000 task characters,
and at most 32 dense spans. Dense retrieval is the pinned local Snowflake
Arctic Embed M ONNX backend. The live GitHub workflows fetch its content-hashed
files from release `gt-retrieval-runtime-v1`, verify the model SHA-256, and pass
the local directory to the central agent. GT itself performs no network or
provider call for embeddings.

Cold retrieval receives a 30-second measured allowance; cached steady-state
retrieval fails open after two seconds. A local real-ONNX central-agent witness
completed cold retrieval in 4.9–6.5 seconds and the immediate next turn in
303 ms, with zero extra model calls/actions and first-eligible, non-predictive
delivery. These figures are runtime integrity witnesses, not outcome evidence.

Before provider injection, retrieval, graph-frontier, feature, and progress
payloads are normalized into typed `GTContribution` rows. The contribution
compiler accounts every row once, rejects stale/expired evidence, suppresses
duplicate claim/fact/text identities across surfaces, and never truncates a
fact to fit. Private controller state remains private. The active subsystem and
all-17 lifecycle inventory lives in `gt_engine.component_registry`; audit that
registry instead of inferring activity from old modules or documents.

GitHub provider-free workflow `31526751148` passed at `e4eab72`, including the
real dense witness, readiness, all-17 census, exact pushed-tree check, and
`SMOKE_APPROVED`. The next release gate is paired decision-point utility. The
existing online DeepSWE-off arm is reusable only after it passes the exact
evaluation-v1.1 schema and full task/model/provider/runner/prompt/tool/limit/
outcome identity gate. If it is censored, older-schema, or identity-incomplete,
the same checked-in Mini-SWE workflow must produce a new GT-off control; never
force an invalid A/B merely to avoid rerunning baseline. After freeze, run the
matched arm, then use Terminal-Bench 2.0 as a Mini-SWE product diagnostic.
TB2.0 is not a Terminal-Bench 2.1 leaderboard claim. OpenHands/OpenAgents are
outside this evaluation.

## Final live-integration repair (2026-08-11)

Live retrieval now consumes a bounded `RetrievalActionState` projected from
Mini-SWE's shared `ProposedAction`. Raw commands, heredoc bodies, and
interpreter program strings are not query state. Exact evidence authority is
restricted to canonical full paths and unique explicit non-common identifiers;
weak token matches remain ranking signals. Semantic claim identity excludes
the global source revision, so an unrelated edit cannot make unchanged
evidence appear new.

The live gate abstains on transfer-time
`not_applicable_no_supported_source` tasks and on attributable passing
validation with no diagnostic. Source-backed tasks retain the incremental
graph lifecycle. Every delivered preemptive row stores a support class and its
supporting retrieval channels.

Use `gt_engine.delivery_audit.audit_provider_deliveries` for all visibility
counts. It combines every model-visible surface and validates unique claims,
first-eligible/non-predictive timing, exact request/provider hashes, and the
in-range provider-message index changed by GT. Preemptive evidence without a
persisted semantic-support certificate fails closed.

This invalidates the old summary of smoke `31535815764`. It emitted 60 visible
deliveries and 44,372 GT characters (44 preemptive, 9 graph frontier, 7 feature
guidance), not seven total. The 44 preemptive receipts predate the semantic
certificate, the task jobs lacked the pinned dense backend, and runner-kernel
identity perturbed the initial prompt. The archived run is diagnostic only.

The repaired GitHub matrix installs retrieval dependencies and provisions the
pinned Snowflake ONNX asset inside each task job, executes a real query and
document embedding before Mini-SWE, and requires both that proof and a live
backend receipt for each applicable source task. Every model call records the
pre-GT control request/provider hashes. The authoritative provider-free gate
must build the current Go indexer source on Linux and prove the real ONNX asset;
the stale local Windows binary is known to lack `objective_c`. Do not weaken
that gate, dispatch a paid smoke, or claim completion until the exact pushed
workflow prints `READY` and `SMOKE_APPROVED`.

Exact provider-free workflow `31544885372` passed at `338b391`, including the
current-source graph, pinned real ONNX backend, all language/runtime tests,
`READY`, `SMOKE_APPROVED`, and static checks. The receipt records zero provider
calls. The implementation repair is therefore verified; outcome benefit is
not. A corrected paid smoke still requires separate authorization.

## Decision-sufficiency and release boundary (2026-08-12)

The action boundary now has a separately gated deterministic
`decision_sufficiency` stage after model selection and before
`environment.exec`. Only a current, complete, mechanically certified claim for
a single-target `EDIT`, `CREATE`, or `DELETE` can become `RETURN_ELIGIBLE`, and
only when exact provider-prepared messages prove the model has not already seen
it. Ambiguous parsing, incomplete visibility, stale source or graph revision,
sparse/dense-only support, co-change evidence, duplicate evidence, and budget
overflow all produce `PASS`.

This action check does not rerun the Snowflake ONNX embedder. It uses a bounded
target-and-neighbor slice of the already refreshed repository with exact,
sparse-ranking, and certified structural channels. Paid workflows remain in
`SHADOW`, so eligibility is measured without changing the command or adding a
model call; `ASSISTIVE_SAFE` requires separate approval.

Derived trees are pruned before manifest bounds, unhealthy sensor recovery
rehashes every supported source, graph waiting exceeds the index subprocess
deadline, and final receipts use the atomic repository-session result.
`scripts/central_release_gate.py` validates substrate/dense readiness, exact
provider delivery, preflight accounting, and decision receipts. GitHub proof
must build the current Go indexer; a stale local Windows binary is not
authoritative.

## Final promotion repair contract (2026-08-12)

GroundTruth now keeps three identities separate: broad ranked candidates,
semantic content claims, and action-bound decision claims. Semantic identity is
path/span/symbol/relation/text only; GraphDB row IDs and channel/revision
metadata cannot cause redelivery. Decision identity additionally binds the
operation, target, and support type.

Certified structural text must come from the exact GraphDB edge endpoint.
`StructuralLink` retains endpoint symbol/line metadata, the channel keeps every
span in a file, and unresolved endpoints remain rank-only. Fusion preserves
per-channel representatives so support cannot certify another channel's span.
Import and co-change relations cannot authorize a model return.

Retrieval performs a budget precheck before repository or embedding work. A
positive partial character budget is enforced while complete spans are packed,
so selection cannot precede a host-side budget discard. It caches identical
state/revision/visibility queries in a bounded session cache,
and reserves up to 3,000 of 12,000 task characters for post-mutation,
diagnostic, and validation opportunities. Each provider boundary is accounted
by opportunity, candidates, selection, delivery, visibility, abstention reason,
cache status, and latency. This is implementation accounting, not causal proof.

DeepSWE treatment must enable context compaction, completion control, progress
control, and adaptive validation timeout. The release gate rejects disabled
controls, work after budget closure, missing opportunity accounting, duplicate
content claims, and non-material/non-endpoint-aligned decision evidence.
Preflight remains SHADOW until a separately authorized outcome smoke. Local
Windows native-index results are not authoritative while the checked-in binary
lacks Objective-C; the pushed Linux build remains the substrate authority.

Provider-free workflow `31616184187` passed the final-promotion contract on
runtime commit `80a8376`, including current native build, pinned Snowflake ONNX,
all substrate/runtime/readiness gates, `READY`, and `SMOKE_APPROVED`. The
uploaded receipt records `provider_calls: 0`. It does not prove outcome uplift;
the matched paid smoke remains separately authorized.


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
   identity for executor and bootstrap calls. DeepSWE merge requires a stable
   response model equal to the provider preflight result, and requires the
   fingerprint to match when the provider supplies one. Requested model names
   alone are not parity proof.
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
it does not establish solve uplift, non-regression, or efficiency.

## Final live-diagnostic repair contract (2026-08-12)

DeepSWE diagnostic workflow `31656913063` is rejected outcome evidence. Its
single task, `abs-module-cache-flags`, ended in `ContextBudgetExhausted` after
121 executor calls, 121 actions, 14.15M model tokens, and 888 seconds. The graph
was healthy (754 nodes, 2,242 edges, 45 source files), the pinned Snowflake ONNX
backend was available, and all provider-visible evidence was timely and
hash-accounted. Release replay passes graph substrate, dense retrieval,
delivery, contribution budget, preflight, decision sufficiency, project
validation, and retrieval efficiency. It rejects only the old persistent-state
bootstrap/lifecycle contract.

The permanent repair rules are:

1. Bootstrap is exactly one direct provider transport call with
   `num_retries=0`, forced Bash tool choice, temperature zero, and no action
   execution. A received but unparsable response retains usage, cost, provider
   identity, and a typed parse error. Release rejects retry-wrapped transport.
2. Exact-symbol authority comes only from syntax-marked task entities, active
   symbols, exact diagnostic entities, or code-shaped typed-action tokens.
   Ordinary prose remains available to lexical/BM25/dense ranking but cannot
   certify a common word such as `clear`. The failed task's certified entities
   are `require` and `ABS_MODULE_PATH`, not `terminal/terminal.go#clear`.
3. Hybrid task-ranked catalog entries precede generic GraphDB repository-order
   anchors and replace equivalent generic entries.
4. Persistent state is evaluated at every provider/preflight/postflight/rebase
   boundary. Every graph-applicable executor request receives exactly one
   bounded current slice: initial/critical up to 512 packing tokens, a material
   delta up to 256, or an unchanged-state core up to 96. `NONE` is reserved for
   explicit fail-open invalid/stale/unavailable state. Semantic no-ops do not
   increment state version or reorder obligations; source excerpts and new
   semantic claims are one-shot, while the small current core is intentionally
   repeatable. A claim becomes exposed only after selection, insertion, and
   provider dispatch.
5. Graph substrate health is independent of downstream bootstrap or delivery
   validity. A healthy graph cannot be relabeled invalid because another GT
   mechanism failed.
6. DeepSWE performs one provider bootstrap canary before the matrix, not one
   paid preflight per task. The canary uses the production bootstrap parser and
   records nonzero calls, tokens, cost, and latency. The default final profile is `certified_full` with
   ACTIVE integration, SHADOW preflight, context compaction, completion,
   progress, and adaptive-validation controls enabled.
7. Provider transport is fail-closed on marker-write failure. Bootstrap and
   executor calls use no provider retries, delegate timeout to the provider,
   and await transport completion; the host never abandons an uncancellable
   provider thread while finalizing a contradictory receipt.
8. The resolved task workspace is disclosed in the ordinary Mini-SWE task
   prompt for both GT-off and GT-on. A final comparison must use the same
   `resolved_workspace_v1` prompt contract in both arms; an older baseline
   without it is not exact prompt parity. The final A/B gate also requires a
   proven GT-off baseline arm, ACTIVE `certified_full` treatment, nonempty
   observed model/provider/fingerprint identity, exact prompt/tool hashes, and
   all-in efficiency including the one pre-matrix bootstrap canary.

Local widened runtime tests, static checks, Python compilation, and workflow
parsing pass. Archived replay now fails only the five expected old-bootstrap
conditions. The current working tree is **not** release-certified: local
census, readiness, and pre-smoke remain fail-closed because the stale Windows
`gt-index.exe` lacks Objective-C and the tree is not the exact pushed commit.
Only a pushed source-built Linux provider-free workflow may print `READY` and
`SMOKE_APPROVED` for this repair. No new solve, flip, non-regression, or
efficiency claim exists.


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
