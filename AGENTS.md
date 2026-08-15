# GT central-runtime behavioral contract

The active GT-on implementation is `eval.gt_central_agent:MiniSweCentralAgent`.
It is a host-owned engine, not a task-container package, prompt add-on, or
model-invoked sidecar. It owns the model loop and observes every model-selected
command before and after host-side execution.

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


## What counts as GT working

### Graph applicability boundary (2026-08-09)

`require_graph_ready=true` is a fail-closed gate for a task that contains
supported, source-backed files.  A task containing only data/model artifacts,
task outputs, documentation, or other non-indexable files is explicitly
`not_applicable_no_supported_source`: GT must not fabricate a graph, must not
emit repository facts, and must exclude that task from the repository-
intelligence denominator.  This legitimate abstention is not a degraded
fallback or an invalid treatment.  Any source-backed task with a missing,
stale, schema-invalid, incomplete, or unavailable graph remains a real
substrate failure and invalidates the treatment.  The workflow merge gate
must use the recorded applicability/denominator flag rather than treating
every non-`passed` status as a graph failure.

## Incremental graph lifecycle (2026-08-09)

Repository intelligence is refreshed at the post-action finalization boundary:
the workspace sensor captures every bounded changed source candidate, the
shared content-aware resolver classifies it, and the session applies the
captured transition before the next model request.  Extensionless and
content-signature sources (including shebang scripts and basename languages)
must be resolved from captured bytes, not path suffixes alone.  A created or
modified indexable file is queued for certified incremental indexing; a deleted
indexable file, or a source that becomes non-source, forces a full rebuild so
stale graph nodes cannot survive.  Multi-file transitions are all captured
within the existing file/byte bounds; no arbitrary eight-file suffix may be
dropped.  Non-source extensionless files may be captured for classification,
but never advance source revision or enter the graph.  A refresh is complete
before the next provider request, and a failed/incomplete capture fails closed
instead of serving stale graph evidence.

## Hybrid retrieval and additive preemptive frame (2026-08-10)

The shared retrieval mechanism is `gt_engine.hybrid_retrieval.HybridRetriever`.
The ARB adapter and the optional Mini-SWE provider-boundary frame must call this
same implementation; benchmark-only rankers are forbidden. One typed
`RetrievalState` is evaluated by five independent channels: exact path/symbol,
lexical overlap, BM25, the explicitly provisioned local Snowflake Arctic ONNX
embedder, and certified GraphDB structure. Equal-weight reciprocal-rank fusion
uses `k=60`, fuses unique files, and then packs at most eight complete evidence
spans inside the configured token budget. Source bytes always come from the
exact checkout. The structural corpus includes directed edges, resolved test
assertions, verified closure, pair co-change, and commit-set co-change.

Ranking support is not automatically delivery authority. Raw active/changed
paths seed exact and structural retrieval but their generic directory and
extension tokens do not enter lexical/BM25 queries. Exact path-token overlap
counts only when the token is repository-distinctive. Graph edges,
assertions, and closure receive a separate `certified` bit only at their
mechanical trust threshold; co-change can improve rank but can never certify a
delivery by itself. Dense, sparse, and structural support remain distinct
families. Missing dense assets, channel errors, stale revisions, incomplete
source spans, ambiguity, and budget pressure abstain or fail open; they never
fabricate evidence.

`enable_preemptive_retrieval` is default-false and is forcibly disabled by the
OFF, AUDIT, and certified-shadow shields. When explicitly active, the engine
retrieves from task plus current trajectory state before the next provider
request and may append one bounded `PreemptiveFrame` to the same tool
observation as existing GT evidence. It does not remove the 17-feature path,
does not replace the context frontier, does not add a model/tool call, and does
not execute, rewrite, suppress, or predict an action. Stale, duplicate, late,
over-budget, timed-out, or ungrounded frames abstain. Every attempt and delivery
records candidate ranks, channel receipts, evidence hashes, exact provider
request hash/message index, action/call timing, model identity, latency,
payload characters/tokens, and no-late/no-predictive status.

The GitHub ARB workflow provisions
`Snowflake/snowflake-arctic-embed-m` at immutable revision
`7802add0519e4bf94c46ef23552176697c7a1ac7` and verifies ONNX SHA-256
`564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
The model is local inference only: query prefix, CLS pooling, 512-token
truncation, L2 normalization, zero provider/API calls. ARB must publish both
the top-20 ranked view and the actually selected/delivered view. Provider-free
tests and runtime request proof establish implementation integrity only; no
retrieval or solve-rate improvement may be claimed until the full 427-row ARB
evaluation completes.

## Final live-integration repair (2026-08-11)

The accepted ARB retriever is not complete merely because its standalone
ranking tests pass. The live Mini-SWE path must project the already-normalized
`ProposedAction` into bounded `RetrievalActionState`; raw Bash, heredoc bodies,
and interpreter `-c`/`-e` programs must never become retrieval query text.
Exact delivery authority requires canonical full-path equality or a unique,
explicit, non-common identifier of at least four characters. Weak token
overlap remains rank-only. A claim identifies semantic evidence
(path/span/symbol/relation/provenance/content), not the global source revision,
so unrelated workspace edits cannot redeliver unchanged evidence.

Preemptive retrieval must abstain for a task classified at transfer time as
`not_applicable_no_supported_source` and after an attributable passing
validation with no diagnostic. This is separate from incremental refresh on a
source-backed task: changed source files still rebuild/refresh before the next
provider request. Every selected preemptive row persists its support class and
supporting channel set. A delivery without that semantic certificate is
invalid, even if it was timely and its request hash exists.

`gt_engine.delivery_audit.audit_provider_deliveries` is the only authoritative
visible-delivery counter. It combines preemptive retrieval, feature guidance,
repository frontier, progress, and explicitly visible completion surfaces. A
valid delivery must have unique claims, first-eligible/non-predictive timing,
an exact request and provider-view hash matching the call receipt, and a
recorded provider-message index that is in range and listed among the messages
changed by GT. Preemptive deliveries additionally require persisted semantic
support. Never report only `guidance_deliveries` as total GT visibility.

Archived outcome smoke `31535815764` is rejected deterministic-integrity
evidence. Canonical reconstruction finds 60 visible deliveries and 44,372
characters: 44 preemptive, 9 repository-frontier, and 7 feature-guidance
deliveries. All were first-eligible and hash-accounted, but the 44 old
preemptive receipts lack the new semantic-support certificate. The smoke also
ran without the pinned dense backend in the matrix jobs and its first prompt
contained runner-kernel identity. It cannot certify the repaired treatment.

The paid matrix now provisions and executes the pinned Snowflake ONNX backend
inside every applicable task job. Merge accepts dense treatment only when the
pre-run embedding proof and the live central receipt both explicitly report
success. The agent records a pre-GT control request hash on every call, while
`central_run_diff.py` compares a baseline provider view to the treatment's
pre-GT control view. Same-run control-versus-final differences are intervention
accounting, not A/B prompt-parity failures.

Local Windows release gates may fail if the checked-in `gt-index.exe` predates
the current Go source (the known witness is missing `objective_c`). Do not
weaken the graph gate or claim readiness from Python tests. The authoritative
provider-free workflow builds `vendor/gt-index-src` on Linux, provisions the
content-hashed ONNX asset, then must print every census line, `READY`, and
`SMOKE_APPROVED`. No paid smoke is allowed before that exact pushed workflow
passes.

Exact provider-free workflow `31544885372` passed this contract at commit
`338b391`: current-source graph build, pinned real ONNX asset, language
coverage, full central tests, `READY`, `SMOKE_APPROVED`, and static checks. Its
uploaded receipt records `provider_calls: 0`. This verifies the implementation
and integration repair; it does not prove solve uplift or authorize a paid
smoke. The next paid matched smoke still requires separate authorization.

Keep these states distinct in every audit:

1. **Receipt:** a FACT or CAP payload was produced at the correct action and
   source/workspace revision. This proves observation, not trajectory influence.
2. **Controller consumption/decision:** a registered consumer used the payload
   to change internal state or schedule a deterministic check. A `PASS` is a
   real decision but does not alter the model's next action.
3. **Integrated consequence:** the engine updates operational controller state
   and, when the model needs the result, places one bounded grounded payload in
   the first provider request after the evidence action. Under the separately
   gated `ASSISTIVE_SAFE` preflight mode, a mechanically proven contradiction
   may return the selected action to the model before execution. GT never
   rewrites a command or silently suppresses one.

## Pre-action interface (2026-08-05)

The central agent normalizes every Bash tool call into one typed
`ProposedAction` after model selection and before `environment.exec`. The same
shell segmentation and immutable validation classification are reused by
preflight and postflight. The host applies exactly one mode:

- `OFF`: the historical postflight loop; no preflight evaluation or receipts.
- `SHADOW`: evaluate and receipt every proposal, but execute the original
  command and preserve batch behavior.
- `ASSISTIVE_SAFE`: allow only `PASS`, bounded `AUGMENT`, or grounded
  `RETURN_TO_MODEL`. `REWRITE` and feature-driven `SUPPRESS` are rejected to
  `PASS`. Timeout, exception, ambiguity, low confidence, heuristic evidence,
  stale evidence, or duplicate evidence also degrade to recorded `PASS`.

Read/search batches may continue. In assistive mode a known mutation,
validation, submit, material workspace change, or source-revision
change prevents a pre-decided suffix from executing on stale reasoning. An
`OTHER` action does not split a batch merely because parsing abstained; it
splits only when postflight proves a material change. A generic exploratory
nonzero exit is recorded but is not by itself worth another model call. Each proposal receives
an `ActionCycleReceipt` joining candidate decision, applied disposition,
dispatch, postflight result/revisions, and the next command after a return.

The five evidence-correct postflight-only features remain
`GT_CHANGE_SURFACE`, `signature_delta`, `GT_PATCH_DELTA`, `syntax_result`, and
`covering_red`. The other twelve have explicit two-sided lifecycle placement,
but preflight may use them only when their required evidence already exists.
No feature is moved earlier merely to increase trigger counts.

The paid workflow is deliberately `preflight_mode=shadow`. Do not change it to
`assistive_safe` until provider-free gates and a separately authorized matched
smoke approve intervention behavior.

## Effect provenance (2026-08-04)

`central_receipt.json.features.effect_trace` is an additive provenance ledger.
It links each applied effect to existing state reads and confirmed provider
deliveries without changing routing, prompt selection, timing, or action
execution. `audit_only` means the effect was recorded but no existing
downstream consumer was exercised; it must not be reported as trajectory
influence. `provider_payload` and `existing_engine_actuation` require a
recorded downstream event. `engine_internal_state` records producer-side GT
control work (revision, validation-debt, failure, lifecycle, or trigger
updates) and is distinct from provider delivery. Unknown dispositions fail the
audit.

Private receipts must never be mistaken for an inactive engine. Conversely,
receipt counts must never be claimed as causal help.

## Source-revision model

The engine keeps two revisions: the raw workspace revision (audit) and a
validation-relevant source revision. Caches, compiled objects, binaries, build
products, logs, benchmark output, directories, and background writes never
advance source revision. Task-required deliverables satisfy obligations without
pretending to be source. Validation evidence goes stale only when authored
source changes.

## One validation classifier

Every executed action is classified exactly once in the agent. The immutable
`ValidationClassification` is shared by the feature runtime, the evidence
ledger, the receipt writer, and deep metrics. No component reparses the
command; runtime, ledger, and metrics cannot disagree about the same action.
Submit certificates report real current checks bound to the source revision.

## Active delivery policy

The engine may deliver only new, grounded control evidence that names concrete
anchors (paths, symbols, commands, diagnostics):

- a concrete changed-file syntax failure;
- a real, structurally recognized validation failure;
- the same failure repeating at an unchanged source revision;
- a source-derived signature delta with affected caller evidence;
- a concrete new-file precedent or ranked-context reslot; or
- source-bound validation or submission-risk state naming the exact check.

A fresh syntax failure is delivered before the next available model decision.
In OFF/SHADOW, actions already selected in the same response continue
unchanged; ASSISTIVE_SAFE uses the hybrid stale-batch barrier. Generic
obligations, search echoes, passing syntax checks, and submission certificates
remain private unless they contribute new decision-relevant evidence. CAP
features must apply their own actuator payload rather than copy an owner message.
If the engine cannot name the evidence, the payload stays private.

Every active delivery must be present in the exact final provider request before
`model.query()` begins. Evidence from action N belongs in the first call after
action N; call N+2 is one-step-late. Audit revision, request hash, message index,
non-prediction, deduplication, and next action. Do not re-enable the historical
generic guidance stream; its 94 advisories in run `30869649342` were the
documented context/token regression.

## Deterministic context compiler contract (2026-08-05)

GT is not an advice sidecar. `MiniSweCentralAgent` compiles every provider
request from the durable Mini-SWE history plus typed current controller state.
The compiler may expose only source-backed task facts; it does not reason,
predict intent, invent a plan, ask the model to acknowledge GT, or delete
distinct Mini-SWE reasoning.

Every candidate `ContextFact` receives exactly one replayable disposition in
the request receipt:

- `represented_message`: exact provider-message indices already contain the
  command/result fact, so no duplicate text is added;
- `selected_state_frame`: a current material fact is absent from retained
  history and is emitted once in a bounded declarative frame;
- `controller_only`: revision/control state affects deterministic selection but
  is not useful model text;
- `stale_source_revision`: revision-bound evidence is rejected;
- `state_frame_budget`: a complete fact could not fit and is omitted rather
  than truncated into a misleading fragment.

`candidate_fact_count == accounted_fact_count` must hold on every model call.
Request hash and message indices prove exposure. `next_action_anchor_aligned`
is only a behavioral utilization proxy; it must never be called proof of an
internal model acknowledgement or causal benefit. Causal influence requires a
matched arm/ablation. Effect receipts separately record first-eligible compiler
status (`provider_payload`, `controller_state_considered`,
`stale_state_rejected`, `superseded_before_request`,
`existing_engine_actuation`, `audit_only`, or `no_eligible_model_call`).

Exact-turn deduplication includes assistant content, hidden reasoning content,
action metadata, tool output, and tool status (transport-local tool-call IDs are
ignored). Therefore duplicate semantic turns may be removed, but two turns
with different Mini-SWE reasoning or return status must both survive. The paid
workflow now enables the bounded deterministic transform at a 70% of the
400,000-character context envelope trigger and a 50% target. It first removes
duplicate turns, then compacts only older turns while retaining the latest two
turns and a typed current-state frame. Below the trigger it preserves the
provider history byte-for-byte (apart from exact duplicate turns). No LLM is
used to summarize. Read observations come from every typed shell segment, not
only a command's primary label, and retain path/range/revision/result hash.

## Regression-hardening contract (2026-08-05)

Validation intent and outcome are separate. `ValidationStatus` is `UNKNOWN`,
`PENDING`, `PASS`, or `FAIL`; PASS/FAIL is legal only when the validator is the
terminal foreground segment that owns the shell action's return status. A
verifier mentioned by `cat`, a background check, a validator followed by
`echo`, or a validator piped into a reporter without mechanically proven
`pipefail` remains UNKNOWN/PENDING and cannot create or clear a certificate.

The shared shell parser preserves top-level newlines, treats heredoc bodies and
interpreter `-c`/`-e` programs as opaque, and derives targets only from parsed
operands/redirections. It never regex-scans raw source or diagnostic text into
typed targets. Unsupported syntax abstains to `OTHER`/PASS.

When compaction is disabled, the compiler is observation-only: it may account
facts but must not deduplicate turns, append a user state frame, or change any
provider message. Missing facts are `no_compaction_controller_only`.
Exact-turn deduplication and bounded state frames belong only to the separately
gated compaction transform. Read identity canonicalizes `/app/path` and
relative paths and excludes output hashes from the identity.

The receipt hashes Mini-SWE's provider-prepared message list after private
`extra` metadata is removed/reordered. No model marker is required.
`provider_request_hash_coverage` must be 1.0. With compaction enabled, view
changes are permitted only at the deterministic threshold and must report
duplicate removal or bounded old-turn elision; unique reasoning removal,
unaccounted facts, and duplicate evidence remain failures.

`integration_mode` is the single host switch: `off` disables GT behavior,
`audit` permits private accounting but preserves provider history and
downgrades intervention to SHADOW, and `active` enables grounded one-shot
delivery. The paid workflow is explicitly ACTIVE + SHADOW preflight + bounded
deterministic compaction + executable completion checks + progress control.
Harbor's task-owned `agent.timeout_sec` is resolved from the exported
`task.toml` and passed unchanged to the agent; a small reserve exits cleanly
before Harbor's outer cancellation. A disabled task-start advisory is resolved
at action zero and may not leak into call two. `newfile_precedent` is one-shot
per task.

## Outcome-preservation lifecycle (2026-08-06)

Reward and solve are not interchangeable. The official verifier reward is
reported separately from `uncensored_resolved`; a rewarded run with an outer
Harbor exception is a censored salvage witness and cannot count as a preserved
solve. Internal clean exhaustion (`LimitsExceeded`, step/cost cap, or the
engine's deadline-reserve exit) is recorded separately and is not mislabeled as
an outer censor when Harbor still receives a normal result.

Completion control is deliberately fail-open. The task contract first removes
Terminal-Bench's host workflow text. Only a complete set of mechanically
equivalent predicates can produce a certificate. Each predicate is executed
privately at one workspace revision; a current all-pass certificate emits the
existing submit marker exactly once and cancels the pre-decided suffix. Any
uncovered obligation, timeout, ambiguity, stale revision, or failed predicate
continues the model loop. Completion probes and auto-submit attempts are
included in `effective_actions`, so controller savings cannot be hidden as
model-action savings.

Progress control records repeated identical observations at three occurrences,
alternating cycles at six, and budget risk near 80% of the step limit. It is
controller state, not generic model advice, and does not block a command by
itself. Context compaction similarly preserves the complete audit history and
changes only the provider view when the bounded threshold is exceeded.

The archived `31068690296` treatment remains rejected evidence: official reward
was 9/10, but uncensored resolved was 8/10 because `write-compressor` hit the
outer 900-second timeout. The corrected archived replay identifies that the
task had already satisfied both real obligations before the timeout; the new
certificate/auto-submit path is provider-free proven but still needs an
authorized matched smoke. The 89-task run remains blocked until outcome
preservation and repeated outcome-first efficiency gates pass.

## Post-smoke semantic hardening (workflow 31068690296)

Paid workflow `31068690296` on `b0c7760` is diagnostic evidence, not an
approved treatment. Outcomes matched the frozen ten-task baseline at 9/10 and
aggregate resources fell, but the correctness/efficiency gate failed: six
solved tasks had a positive resource dimension, `write-compressor` newly hit
Harbor's 900-second outer timeout, and only four of six provider payloads were
semantically valid. Do not cite this run as proof that GT improves outcomes.

The two invalid visible payloads exposed permanent classification rules:

- serialized/model/data artifacts such as `.pkl`, `.npy`, `.npz`, `.pt`,
  `.parquet`, `.h5`, `.onnx`, and `.wasm` are derived artifacts. They cannot
  advance source revision, increase validation debt, or appear as authored
  source in a provider payload;
- `newfile_precedent` requires a non-empty sibling and deterministically ranks
  semantically related stems. An empty package marker such as `__init__.py` is
  not a concrete precedent when a related implementation exists, and the
  feature abstains when it is the only candidate.

Replay also exposed an engine-private path mismatch: task deliverables named as
`/app/path` must canonicalize to the sensor's relative `path`. A required
`/app/report.jsonl` is a deliverable, never source revision. Finally, replay
requires a certificate only for an attributable terminal declared validator;
an un-attributable pipeline is validation intent, not a lost certificate.

The run produced and applied 405 effects and naturally fired 12/17 feature IDs.
The five absent IDs (`covering_red`, `GT_HYPOTHESIS`, `recovery`,
`GT_SS_SUBMIT_RED`, and `submit_refusal`) lacked their exact grounded failure
triggers. Provider-free proof still covers all 17 paths. All 372 provider calls
were exactly hashed; SHADOW preflight made 397/397 PASS decisions; context
transformation, batch interruption, late delivery, and predictive delivery
were all zero. The 89-task run remains blocked, and another paid smoke requires
separate authorization after the repaired commit passes the exact gate.

## Provider-free proof

`python -m scripts.central_feature_census` must print all required lines before any paid
run: `ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
`ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`,
`ALL_17_CONSUMER_PATHS_PROVEN`, `ALL_17_TRIGGERS_PROVEN`,
`ALL_17_PAYLOADS_CONCRETE`, `ALL_17_CONSUMERS_APPLIED`,
`ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST`, and `NO_ACTIONS_BLOCKED`.
It must additionally print `ALL_EFFECTS_CONTEXT_ACCOUNTED`.
The census cannot pass on producer receipts alone.
`scripts/central_readiness_audit.py` must print `READY`. The workflow's
provider-free suite must also include `tests/test_gt_preflight.py`.

## Live-run gate

Before any paid smoke, `python scripts/central_pre_smoke_gate.py` must print
`SMOKE_APPROVED` at the intended commit. It fails closed unless the exact paid
workflow timeout, the direct and module census entrypoints, all 17 agent-loop
producer/consumer effects, non-predictive/non-late timing, and non-blocking
submission-risk consumption are proven. Then replay archived trajectories
through the policy and confirm that each effect is reachable only on its
intended lifecycle state. A smoke is confirmation, never exploratory debugging.
The 89-task run remains blocked until outcome preservation and repeated
outcome-first efficiency gates pass.

## Permanent coverage and accounting rule (2026-08-05)

Never write “all 17 features worked” when referring to one stochastic paid
trajectory without checking feature IDs in its receipts. The statements are
different:

- **17 paths proven:** provider-free census and forced-trigger tests exercised
  every producer and consumer path.
- **17 features fired:** the paid receipt set contains at least one receipt for
  every feature ID.
- **17 features consumed:** every produced effect has an applied engine
  effect and an explicit downstream disposition.
- **17 features delivered to the model:** every feature produced a grounded
  model-visible payload and a confirmed provider delivery. This is not the
  normal requirement; many features are correctly engine-private.

In smoke `30976148466`, 15/17 feature IDs fired naturally; `recovery` and
`signature_delta` did not because their exact triggers were absent. The run
still had 361 applied effects, not 36. The 36 count was only model-visible
provider payload effects.

Never equate private with unused. The effect trace must distinguish
`provider_payload`, `existing_engine_actuation`, `engine_internal_state`,
`audit_only`, and `unread_private_state`. `engine_internal_state` proves
deterministic GT work even when no model text was emitted; only an explicit
downstream read, decision-frame contribution, or provider delivery proves
that the work influenced a later decision. The summary must never relabel
producer-side engine work as inert solely because it was not model-visible.

## Final ten-task smoke (2026-08-04)

The repaired treatment smoke is workflow `30954660207` on commit `e7418a7`
(`inline-engine`). All ten jobs completed successfully. The receipt audit found
372 effects produced and applied, with 297 `engine_internal_state`, 11
`existing_engine_actuation`, 48 `audit_only`, and 16 `provider_payload`
effects. There were 14 model payload deliveries, all grounded and in the first
eligible request: 0 late and 0 predictive deliveries.

The final source-precedent boundary is now strict. `newfile_precedent` may fire
only for a regular model-authored validation-relevant source file with a
recognized source suffix, and its payload names only that source trigger and a
source-classified sibling. The ten-task audit found 10/10 valid precedent
payloads and zero cache, binary, generated-output, or task-output paths. The
previous smoke `30952995623` was rejected as evidence because it exposed the
entire workspace-created batch in the payload; do not use it for readiness.

Against the frozen GT-off baseline, the final smoke measured token delta
`-9,135,151` (-31.26%), API-call delta `-51`, assistant-step delta `-53`, and
action delta `-103`. These are a single matched-smoke efficiency signal, not a
causal quality claim. The 89-task run has not been started; dispatch it only
from this commit or a descendant after retaining the receipt audit.

## Context-compiler smoke audit (2026-08-05)

Paid shadow smoke `31061665540` ran commit `a45601f0ba05`. Integration
integrity passed: 334/334 compiler/API calls, 349/349 preflighted actions all
applied as PASS, 5,287/5,287 facts accounted, 339/339 effects accounted, and
21/21 grounded first-eligible deliveries with zero late/predictive payloads,
zero compactions, and zero unique reasoning removal. The smoke naturally fired
11/17 feature IDs; all 17 paths remain provider-free proven.

The efficiency acceptance gate failed despite preserving verifier reward 9/10.
`cobol-modernization` was a new treatment `AgentTimeoutError`,
`schemelike-metacircular-eval` reached the step cap, six solved tasks failed
strict per-task Pareto, and aggregate normalized token cost increased 13.33%.
The 89-task run remains blocked.

## Terminal-Bench language-resolution and graph stage (2026-08-08)

The current branch includes parser-backed R and Verilog using pinned upstream
Tree-sitter Go bindings (`r-lib/tree-sitter-r v1.3.0` and
`tree-sitter-verilog v1.0.3`). Redcode and POV-Ray use bounded structural
adapters. Unknown syntax remains source-only; unbounded regex graph inference
is prohibited.

File suffix alone is not a language identity. `.v` is shared by Coq and
Verilog, so both the host registry and native indexer resolve it from bounded
source declarations after reading the file. A conflicting or unrecognized
`.v` file is `AMBIGUOUS` and makes source coverage incomplete; it is never
silently parsed as Verilog. `.conf` is Nginx only when bounded content contains
a mechanically recognized Nginx directive; otherwise it remains generic
configuration. Exact basenames (`Makefile`, `Dockerfile`/`Containerfile`,
`CMakeLists.txt`, Meson, and Autotools files) and bounded extensionless
shebangs are resolved by the same authority.

The vendored indexer also ships conservative structural adapters for the
Terminal-Bench witnesses Coq, Stan, SPARQL, Turtle, LaTeX, Vim, Nginx, and
G-code, plus Make, Dockerfile, CMake, Meson, and Autotools control files. These
adapters emit only syntax constructs covered by hand-checked fixtures. A
non-empty structured source that contains no recognized declaration still
receives a concrete file node, not a fabricated symbol or caller. Parser
failures are stored in graph metadata and invalidate substrate readiness.
All parser `CallRef.CallerNodeIdx` values are zero-based. The runtime fixture
must prove directed SQLite `CALLS` edges per advertised caller-capable
language; an in-memory call receipt or a nonzero node count is insufficient.
This permanently covers the earlier structured-adapter off-by-one defect and
the COBOL grammar's sibling paragraph/`PERFORM` ownership boundary. Native R
functions take their name from the assignment's AST `lhs`; the anonymous
`function` keyword is never indexed as the symbol. POV-Ray calls belong to the
enclosing macro, never to the invoked callee or a file-level invocation.

Registry closure is not benchmark closure. The checked-in language contract
pins the official Terminal-Bench 2 repository commit, requires exactly 89 task
directories, verifies every declared language witness and source-like suffix
family, and independently rejects any registry-recognized structural suffix
observed in instructions but left unclassified. Static and exact-tree forms are
both required by the provider-free workflow.

Provider-free workflow `31273427487` at `d2ae8d7` compiled the cgo bindings and
proved R/Verilog definitions, directed edges, and SQLite integrity. The
expanded provider-free workflow `31274090882` at `2cdc8f2` also passed the
adapter build and fixture gate: R=2, Verilog=2, Redcode=1, POV-Ray=1,
42/42 source/file-hash coverage, SQLite integrity, six graph edges, central
census, readiness, static checks, and exact pre-smoke. This is substrate
evidence only, not a solve-rate or efficiency claim. Do not start a paid smoke
without separate authorization; regression approval still requires the
archived replay and matched outcome smoke.

Never infer censoring only from `central_receipt.json`: Harbor can terminate the
agent after the last receipt. Shared metrics must consume the adjacent trial
result or frozen merged result and report outer exception type plus agent wall
time.

The smoke exposed 104 shadow candidate returns, all caused by
`edit_target_absent` on normal scratch-file creation or shell edits. That is not
a material contradiction. Current code defaults such proposals to PASS and
rejects legacy absent-target interventions; post-fix replay produces zero
material candidates. Only new mechanically grounded evidence may return to the
model.

GT context accounting includes both active guidance and compiler state frames.
For this smoke the exact totals were 2,337 guidance characters plus 182,536
state-frame characters, not merely 21 delivery receipts. Keep
`gt_context_chars_added`, `context_state_frame_chars_added`, and
`total_gt_context_chars_added` distinct.

## Feature-applicability and repository-substrate rule (2026-08-06)

Do not report every absent paid-run feature as an absent task trigger. Classify
each ID as `fired_when_eligible`, `correct_abstention`, `trigger_absent`,
`ambiguous_evidence`, `substrate_unavailable`, or `missed_trigger`. Every
evaluation records a reason code and evidence hash; an eligible event without
an effect is a release failure, and a fired feature without an eligible event
is a false fire.

Corrected smoke `31136099371` had 38/38 repository refreshes
`index_unavailable`. Therefore its absent `caller_contract` and
`def_partition` were substrate failures, not proven natural abstentions.
`recovery` and `signature_delta` had no exact repeated-failure/signature-change
event and were legitimate trigger absences. The same smoke emitted 100
localization/reslot receipts but only four concrete anchors; do not cite the
other 96 as useful localization.

The paid workflow must install the vendored GroundTruth wheel, export the
pinned `GT_INDEX_BINARY`, and run `scripts/verify_gt_index_runtime.py` before
provider use. Readiness requires actual binary execution, SQLite integrity,
definition nodes, and a certified directed `CALLS` edge. Import availability
alone is insufficient.

Search text may produce localization only when typed command scope and output
anchors are deterministic. It never certifies definitions or callers.
`def_partition` uses separate graph definition/reference roles;
`caller_contract` uses only directed `CALLS` edges with confidence >= 0.95,
`CERTIFIED` trust, and one candidate. Missing or ambiguous evidence abstains.

Provider facts have one delivery window. Compatible facts from action N may be
coalesced in call N+1; an unselected fact remains controller state and is
explicitly suppressed from provider delivery. It may not leak into call N+2.
Each decision frame has unique claim IDs and unique fact text.

## Regression repair after workflow 31078501162 (2026-08-06)

Workflow `31078501162` is rejected evidence: GT-on resolved 7/10 against the
frozen 9/10 baseline. `llm-inference-batching-scheduler` exhausted 100 steps;
`write-compressor` reached the provider context limit. The failures exposed
four controller defects, not missing 17-feature triggers:

1. line-local deliverable parsing could label an input as the output and miss
   wrapped output paths;
2. novelty could clear `BUDGET_RISK` even when no source or required output
   changed;
3. the provider compactor could delete distinct assistant reasoning while
   reporting zero unique-reasoning removal; and
4. no exact provider-prepared request budget stopped an overflow before
   `model.query()`.

The repaired contract is permanent. Task paths are first normalized into
typed `TaskResource` rows (`INPUT`, `OUTPUT`, `REFERENCE`, `EXECUTABLE`, or
`UNKNOWN`). Only high-confidence outputs become task deliverables. Confirmed
outputs may produce private `test -s` progress probes, but those probes cover
no normative obligation and can never make a partial completion plan eligible
for auto-submit.

Provider compaction preserves every assistant content and reasoning field.
It bounds oversized tool observations (including the newest observation),
represents exact duplicate results append-only, and may clear only old tool
bodies to hash/return-code receipts. It does not inject a generic state frame.
Every exact provider-prepared request is measured before `model.query()` with
a configured hard headroom. An over-budget request is not sent, no pending
guidance is confirmed as delivered, and the exit is recorded as internal
`ContextBudgetExhausted`, not an outer censor.

`BUDGET_RISK` is monotonic until authored source or a confirmed task output
changes. Scratch, cache, derived-artifact, and observation novelty cannot clear
it. Receipts now expose bounded-observation counts/chars, duplicate-result
representation, cleared old tool results, provider budget/headroom, exact
append-stable provider-prefix metrics, completion probes, and task-progress
changes. These repairs are provider-free implementation proof only. They do
not restore the 9/10 baseline until an authorized matched smoke passes; the
89-task run remains blocked.

## Trajectory causality audit (2026-08-09)

`scripts/central_trajectory_audit.py` is the fail-closed audit for archived
GT-on receipts. It certifies deterministic receipt integrity, grounded
first-eligible delivery, provider-request hash coverage, effect dispositions,
and complete context accounting. It must never call `anchor_followed`,
`same_response`, or later action similarity causal proof. Unless a bundle
contains provider request bodies plus model sampling/checkpoint state, model
causality is `UNIDENTIFIABLE`; only a counterfactual replay can certify it.
The audit test is part of the central provider-free workflow.

## Counterfactual replay capture (2026-08-09)

`enable_replay_capture` is opt-in and default-false. It writes exact,
content-addressed capture under `gt_replay/`: `manifest.json`, `calls.jsonl`,
and gzip-compressed `blobs/<sha256>.json.gz`. The verifier fails closed on a
missing, truncated, corrupt, or hash-mismatched request/response blob. A bundle
is replay-ready only when every invoked request has its exact response. It
never injects a provider-specific seed or sampling control; model causality
remains `UNIDENTIFIABLE`. Capture must not alter provider messages or the model
loop.

For a provider-visible GT call, current capture also stores the exact provider-
prepared control messages before GT text, the exact treatment messages, the
compiled payload and contribution IDs, first-visible count, revisions, timing,
and the content-addressed provider tool schema. The fail-closed decision-point
validator accepts a pair only when applying that payload at the recorded message
index reconstructs the treatment byte-for-byte. Prior GT context, stale or late
evidence, missing responses/tools, and any non-GT byte difference reject the
case. `paired_decision_capture_ready` proves only that exact pairs were captured;
it is not a model-utility or causal result. Archived run `31421610097` has
0/1,051 valid pairs because its legacy bundles omitted exact controls.

## Conservative uplift policy and provider baseline shield (2026-08-08)

The latest paid smoke `31282615178` is rejected outcome evidence: it resolved
8/10 against the frozen GT-off reference's 9/10. On the eight common solved
tasks, GT-on used 24.54% more tokens, 26.82% more calls/assistant steps, and
22.74% more model actions. Its all-task token reduction was dominated by
different failure trajectories and is not an efficiency win. A frozen
single-rollout baseline cannot estimate temperature-1 outcome variance or
causal uplift. Do not use it as the release control for the repaired policy.

Deterministic GT can guarantee its evidence, timing, abstention, controller
state, provider transformation, and receipts. It cannot guarantee the sampled
output of a temperature-1 model after changing provider-visible bytes. The
release target is therefore no *GT-attributable* regression under certified
interventions plus statistically supported outcome/resource uplift against
fresh contemporaneous OFF controls. A literal promise of zero observed solve
losses in every stochastic rollout is not a code-level invariant.

All active consequences now cross one common `CertifiedOpportunity` boundary.
Certification is conjunctive: mechanical or certified-structural authority,
current source/workspace revision, concrete anchors, evidence identity, an
open decision need, absence from provider history, and the exact first
eligible decision window. Rank, generic lexical similarity, ambiguity,
heuristics, stale evidence, missing anchors, duplicate representation, and an
expired window abstain. The same boundary covers feature guidance, graph
frontier delivery, admitted preflight returns, and completion auto-submit. GT
never uses another LLM to certify evidence.

Provider history is a baseline shield. Before measured provider-budget
pressure, GT preserves Mini-SWE's stock provider-prepared request exactly
unless a certified opportunity contributes bounded current evidence. The old
soft character trigger and eager per-observation bounding are disabled. A
successful requested read/search observation remains exact while current; only
an actual provider-budget compaction epoch may replace older tool bodies with
hash/return-code receipts. Distinct assistant content and reasoning are never
removed. An over-budget semantic fact is omitted whole; it is never truncated
into an ellipsis.

Every provider call records stock/final provider character counts and hashes,
feature-guidance characters, certified-graph characters, compaction removed
characters, compaction-receipt characters, changed message indices, and the
reason the provider view changed. Full per-call request snapshots are not
duplicated because that would add large observer overhead; the durable
trajectory, deterministic replay, exact hashes, and changed-index/component
ledger are the audit source. No model marker or acknowledgement is required.
`anchor_followed` is a non-causal behavioral proxy only.

Graph retrieval is action-conditioned without prediction. FTS/BM25 rank orders
candidates but contributes zero certification relevance. After execution of a
typed READ, SEARCH, EDIT, or CREATE with an exact validation-relevant source
path, the repository session re-ranks the existing current graph for that path,
without rebuilding it, and caches repeated path queries. Provider delivery
still requires semantic certainty >=0.95, mechanically assigned relevance
>=0.95, and the exact path or symbol in Mini-SWE's provider-visible history.
Line movement and source refresh do not reopen a stable semantic claim.

The paid workflow exposes five explicit component arms through the same
`MiniSweCentralAgent`: `off`, `audit`, `certified_context`,
`certified_controllers`, and `certified_full`. The default is `audit`.
Preflight remains SHADOW in every paid arm; assistive return-to-model behavior
requires a separately authorized experiment. OFF is the contemporaneous
within-wrapper control; the historical frozen GT-off run is descriptive only.

Promotion requires repeated balanced OFF versus `certified_full` trials. Use
the deterministic ABBA/BAAB crossover, at least two trials per arm per task,
task-level hierarchical bootstrap, failure-capped tokens/actions/calls/steps/
effective actions/wall time, a positive solve-rate lower confidence bound (or
an explicitly predeclared noninferiority margin), and resource-ratio upper
bounds. Treatment-only and control-only solves are reported separately.
Component arms isolate instrumentation, context, and controller effects before
full promotion. No 89-task run may start until the repeated outcome-first gate
passes.

Provider-free census now additionally requires
`CERTIFIED_OPPORTUNITY_POLICY_PROVEN`, `PROVIDER_BASELINE_SHIELD_PROVEN`, and
`REPEATED_CONTROL_GATE_PROVEN`. These prove implementation and measurement,
not live efficacy. Exact current implementation and remaining work are in
`details_done/GT_CONSERVATIVE_UPLIFT_IMPLEMENTATION_20260808.md`.

The local conservative-uplift implementation passed 376 central-engine tests,
the exact 161-test pre-smoke lifecycle selection, both census entrypoints, the
real 48-language vendored graph-runtime gate, readiness, direct and module
archived replay, ten-task run-diff accounting, Ruff, compilation, workflow
YAML parsing, and diff checks. This is provider-free implementation proof only.
The worktree is not yet an exact clean pushed commit, so do not claim
`SMOKE_APPROVED`; no paid run has been started and the 89-task run is blocked.

## Latest outcome gate — workflow 31142998081 (2026-08-06)

Workflow `31142998081` at `5c92a6a` is rejected as an outcome-preserving
treatment: 8/10 verifier rewards versus the frozen GT-off baseline's 9/10.
`schemelike-metacircular-eval` is a new uncensored step-cap loss; the known
GT-off-unsolved `gpt2-codegolf` also remained unsolved. Never call the green
workflow or its aggregate token reduction an improvement.

The integration audit itself passed: every task enabled all 17 features,
304/304 effects were applied, 15 IDs fired naturally, the other two were
legitimate trigger absences, all 459 shadow preflights were PASS, and all five
model-visible payloads were grounded, first-eligible, timely, and
non-predictive. The exact first provider request in the lost task was byte
identical to GT-off, while the temperature-1 first response already differed.
That rules out a first-turn GT payload cause but does not clear the later
bounded context transform as a contributor. Use the archived audit
`details_done/GT_SMOKE_31142998081_OUTCOME_AUDIT_20260806.md`; do a request
diff/replay and a separately authorized ablation before another paid smoke.

## 10/10 versus 8/10 comparison (2026-08-06)

The prior 10/10 GT-on smoke (`31136099371`, `8ab1896`) is real paid evidence,
not a replay. The later 8/10 smoke (`31142998081`, `5c92a6a`) used the same
tasks, model/temperature, agent, active/shadow modes, compaction,
completion/progress controls, step cap, and task-owned budgets. Their initial
schemelike provider request was byte-identical, but the temperature-1 first
model action already differed before any GT-visible evidence.

The repair did not change the compactor, completion controller, or progress
controller. It repaired graph-runtime installation and graph-role semantics,
added applicability accounting, and prevents claims from leaking after their
first eligible provider call. In the lost task both runs rendered the exact
same 135-character `GT_EDIT_CHECK` payload; current graph effects were private
and caused no controller action/state frame. Therefore do not attribute the
observed outcome loss to the repair, and do not dismiss it as proven noise
either. Follow `details_done/GT_ON_10OF10_VS_8OF10_COMPARISON_AND_PLAN_20260806.md`:
first request-diff/fixed-trajectory proof, then a separately authorized
component ablation smoke.

## Provider-free run-diff gate (2026-08-06)

Before interpreting two GT-on smokes, run
`python -m scripts.central_run_diff <left-root> <right-root>`. It is an
offline, fail-closed receipt/trajectory comparator: it reports first divergent
model action, whether that predates visible GT evidence, prepared-request hash
differences, frames, compaction, preflight, and accounting completeness. It
must never call a model or modify an artifact. Both direct and module forms of
`central_replay` are required to work. This gate is in the provider-free and
pre-smoke suites. See
`details_done/GT_PROVIDER_FREE_RUN_DIFF_GATE_20260806.md`.

## GT-on smoke 31145623534 (2026-08-07)

Smoke `31145623534` on `f03cb02` is integrity-valid but efficiency-rejected.
It matched the frozen GT-off outcome at 9/10 official and uncensored resolves
with no outer Harbor exceptions. The former `schemelike-metacircular-eval`
loss returned reward 1, although it reached the 100-step cap before a clean
verifier success; it is uncensored, not a timeout salvage.

All 330 produced effects were applied. Fourteen of 17 feature IDs fired
naturally; `GT_SS_SUBMIT_RED`, `recovery`, and `submit_refusal` had no exact
grounded events, with all paths still provider-free proven. Six grounded
payloads reached their first eligible request (zero late/predictive), all 456
provider requests and all 8,125 context facts were accounted, and no unique
Mini-SWE reasoning was removed. Do not call this a 17-feature-fired smoke.

Efficiency failed on the nine common solved tasks: GT-on used 20,422,063
tokens and 416 API calls, versus GT-off's 20,344,163 and 361 (+77,900 tokens,
+55 calls). The all-ten aggregate is misleading because common-unsolved
`gpt2-codegolf` happened to be cheaper. Do not start 89; first isolate the
large LLM-batching and COBOL expansions with a component ablation. Full audit:
`details_done/GT_SMOKE_31145623534_OUTCOME_AND_INTEGRITY_AUDIT_20260807.md`.
## Semantic-progress and compaction repair (2026-08-07)

The first implementation of the regression repair is provider-free and remains
behind the existing host integration switch. Workspace activity is now
separate from semantic progress: source edits are `patch_attempt`, while only
new task-linked read anchors, new attributable diagnostics, or attributed
validation passes advance `task_progress_changes`. Scratch commands, fixture
resets, derived artifacts, and novel output hashes cannot clear `BUDGET_RISK`.
Receipts expose `activity_events` and `semantic_progress_kinds` separately.

When deterministic compaction clears old tool bodies, the compiler now attaches
one bounded current-state frame to the latest retained tool observation, with
fact IDs and the exact provider message index. It never removes distinct
assistant reasoning, injects a user instruction, or fabricates a fact. If no
tool observation survives, the selected fact is recorded as
`no_safe_delivery_surface` rather than silently claimed as delivered.

Completion predicates now carry dependency paths and cache private probe
results by predicate plus dependency fingerprint; cached observations are
rebased to the current workspace revision before certificate evaluation.
Shell coverage distinguishes `shell_context`, `output_only`,
`opaque_program`, and genuinely `unknown` segments; unsupported syntax still
fails open to `OTHER`/PASS.

These changes have passed the focused GT/progress/provider-view/preflight/
completion/deep-metrics tests, compilation, the central feature census, and
the readiness audit. No paid smoke has been run for this repair. A smoke is
blocked until the full provider-free suite and archived trajectory replay pass;
the paid workflow remains `ACTIVE + SHADOW` and the 89-task run remains
blocked.

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

GT is the deterministic repository-intelligence layer inside Mini-SWE, not a
sidecar that may be counted as healthy merely because feature receipts exist.
Keep substrate health, retrieval outcome, and provider delivery separate. A
certified current graph is a release requirement. A healthy graph may return
`EMPTY` because no new task-linked fact clears the precision threshold, or
`REPRESENTED` because Mini-SWE history already contains the fact. Those are
accounted abstentions, not substrate failures and not permission to force
generic text. Mini-SWE fails open operationally on a real graph failure so GT
cannot erase a baseline solve; the experiment still fails closed analytically.

`graph.db` is certified evidence, not a boolean file-presence check. Every
build/refresh receipt records source coverage, unsupported suffixes, schema
validity, node/edge counts, FTS tables, binary hash, graph revision, latency,
and error type. The language registry is the one authority used by the sensor,
source revision, syntax probes, indexer, and bridges. Its structural suffixes
match the vendored `gt-index` specs. Authored languages without a shipped
parser are `unsupported_language` or `incomplete_source_coverage`; they are
never relabeled as no source and never approximated with regex symbols.

The task-scoped repository mirror transfers only validation-relevant authored
source plus bounded project metadata before the certified full build. It never
copies checkpoints, datasets, binaries, build products, caches, or task
deliverables into the host index. The indexer uses its real `-file`
incremental path plus closure rebuild, atomically publishes graph and manifest,
and reuses a graph only at the identical validation-relevant source revision.
Deletes, unsafe paths, incomplete transfer, sensor degradation, schema failure,
stale revision, or incomplete authored-language coverage invalidate the
substrate. Healthy empty retrieval and low-relevance candidates instead produce
explicit retrieval dispositions. Derived artifacts and deliverables never
advance source revision.

Before every provider call, the deterministic context frontier advances beyond
facts already represented in durable Mini-SWE history. It emits only certified
definition, signature, caller, reference, test, or bounded ranked-anchor facts
with concrete path/line/symbol anchors, at most three facts and 1,200
characters per call and 6,000 characters per task. It never truncates a fact,
invents an anchor,
predicts the model's action, duplicates a delivered fact, or emits on stale or
unhealthy evidence. Candidate count must equal accounted count. Provider hash,
message index, source/graph revisions, fact IDs, timing, and exact characters
are receipted.

When a healthy current graph has a concrete high-confidence ranked anchor but
no separate definition, reference, caller, or test role, the frontier may use
that anchor as a bounded `FILE`/`SYMBOL` fallback. It names only the certified
source path, positive line, and optional symbol; it never invents a structural
relationship, and it is deduplicated against richer roles and retained history.

Semantic certainty and task retrieval relevance are independent. A structurally
valid graph node is not automatically relevant to the task. Generic anchors
such as `app`, `url`, or `repr` cannot become visible merely because they have a
high graph confidence. Frontier claim identity is semantic and stable across
source revisions; revision-bound fact IDs remain available for audit, while a
claim already delivered is not resent after an unrelated revision change.
Multiple graph occurrences of one semantic claim (for example, repeated call
sites) are coalesced before selection; physical line differences must never
create duplicate facts or claims in one provider frame.

Preflight mutation certainty is `PROVEN_READ_ONLY`, `PROVEN_MUTATION`, or
`MAY_MUTATE`, with parser coverage and opaque/unknown segment flags. A workspace
rescan may be skipped only for `PROVEN_READ_ONLY`; ambiguity remains fail-open
and is scanned. This optimization changes no model command.

Deep metrics must include frontier characters in total GT context and report
per task: intelligence status/failures, schema health, source/indexable counts,
nodes/edges, refreshes, frontier candidates/accounting/deliveries/facts/chars,
duplicates, provider hashes, model/API work, controller work, tokens, and
outcome/censoring. The paid merge fails when any required task is not
`repository_intelligence.status=passed`, but still uploads artifacts.

Provider-free proof now requires `REPOSITORY_SUBSTRATE_PROVEN` and
`CONTEXT_FRONTIER_PROVEN` in addition to the permanent all-17 census lines.
This proves deterministic integration and accounting, not a solve-rate or
efficiency gain. No new paid smoke has run for this implementation; the
89-task run remains blocked until an authorized matched smoke passes outcome,
intelligence-health, timing, payload, and efficiency gates. A matched slice
containing authored COBOL or Scheme source is now eligible for the parser gate:
the pinned Tree-sitter grammars are compiled into the checked-out `gt-index`
binary and the runtime fixture must observe nonzero COBOL and Scheme nodes.
R, Verilog, Coq, Stan, SPARQL, Turtle, LaTeX, Vim, Nginx, G-code, Red, and
POV-Ray now have provider-free graph fixtures. An unsupported or ambiguous
language remains analytically fail-closed and is never silently dropped. This
is source-substrate proof, not proof of provider usefulness, solves, or
efficiency. Runtime paths outside the task workspace are captured only through
the explicit allowlist: named `/etc/nginx/**` and `/var/log/nginx/**` paths use
bounded metadata/content probes, and authored Nginx configuration is mirrored
under `__external__/`. Extensionless files are bounded shebang candidates and
must prove their interpreter from captured content. No broad external scan is
allowed.

`central_provider_free.yml` must run `central_pre_smoke_gate.py` and print
`SMOKE_APPROVED` on the exact pushed commit intended for a paid smoke. Passing
the component tests on a parent commit is not sufficient.

`require_graph_ready=true` is an experimental validity requirement, not a
pre-provider execution kill switch. Missing, stale, empty, incomplete, or
schema-invalid substrate records `graph_degraded_fallback=true`, preserves the
ordinary Mini-SWE provider loop, suppresses uncertified graph payloads, and
causes the merged treatment gate to fail. This prevents a graph bug from
destroying a baseline solve while preventing a graph-less run from being
promoted as valid GT evidence.

## Portable source capture boundary (2026-08-08)

Workspace source mirroring is host-owned and must work in task images without
Python. `WorkspaceSensor` first tries bounded `python3 -c` JSON/base64 capture,
then falls back to shell-native `base64 | tr -d '\\n'` records for validated
changed paths when Python is missing or output is malformed. It decodes only
exact manifest paths and retains digest/metadata authority. If both captures
fail, the repository session is `mirror_incomplete`; Mini-SWE execution stays
fail-open, while the required intelligence gate fails closed.

Diagnostic paid workflow `31270761663` exposed this defect. COBOL had a healthy
graph but no frontier delivery because its candidates were already represented
in durable Mini-SWE messages; its one guidance event is not a causal-use
claim. `write-compressor` solved but lost current graph substrate after the
task image returned `python3: command not found`; its final graph had zero
nodes/edges, so the run is invalid GT evidence. A portable-capture regression
test and implementation now protect this boundary. Do not start a paid rerun
until provider-free gates pass on the pushed commit and a matched smoke is
separately authorized.

The staged language-completeness implementation is recorded in
`details_done/GT_ALL_TERMINAL_BENCH_LANGUAGE_SUPPORT_IMPLEMENTATION_PLAN_20260808.md`.
Phase 0 inventory/fail-closed accounting, R/Verilog native grammars, and
bounded Red/POV structural adapters are implemented. The adapters emit only
proven labels/control-flow or macro/include facts; unknown syntax stays
source-only. The adapter commit still requires its exact Linux build and
fixture gate before promotion.

Provider-view efficiency is governed by the later conservative baseline-shield
contract. Typed observations remain exact before measured provider-budget
pressure; eager per-observation bounding and the soft character trigger are
retired regression sources. During a genuine provider-budget compaction epoch,
only older tool bodies may become bounded hash/return-code receipts. The newest
successful requested read/search result and every distinct assistant content or
reasoning field remain exact. Hard provider-budget headroom fails before
`model.query()` rather than sending overflow.

The top-down repair is provider-free certified on exact implementation commit
`e6ce41f` by workflow `31244088870`: the checked-out Linux `gt-index` build,
COBOL/Python/Scheme repository fixture, 311 workflow-scope tests, all-17 census
coverage, readiness audit, archived replay, and Ruff passed. This is
deterministic integration evidence, not live solve-rate or token evidence. A
separately authorized matched smoke is still required before promotion. The
89-task run remains blocked.

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

The common-solved token reduction in smoke `31343081886` did not satisfy the
efficiency contract because model calls and assistant steps increased. The
repair separates four boundaries that must never be collapsed into a generic
"progress" signal.

Shell parsing now separates executable argv from typed `ShellRedirection`
records before both validation classification and preflight. Descriptor
duplication such as `2>&1` is neither an argv operand nor a workspace mutation;
file output remains a typed side effect, and file input is a typed read. A
declared validator therefore remains `VALIDATE` when its output is redirected.
The concrete portfolio command `cd /app && timeout 900 python3 benchmark.py
2>&1` now retains declared authority and receives the bounded adaptive timeout
instead of the historical 30-second default.

Progress has two identities. `attempt_id` describes operation, normalized
executable, targets, source revision, and declared check. `observation_id` adds
the typed result and output hash. Exit status is executable-aware: search
no-match and diff differences are valid observations, while Mini-SWE's
`return_code=-1` timeout protocol and shell `124` are both `TIMEOUT`. A failed
read never consumes a path anchor. Observation gain, task-progress gain, and
workspace activity are separate; only an attributed validation pass or a
confirmed task output is task progress. Repeated same-state
`STALLED`/`CONTRADICTED`/`BUDGET_RISK` updates stay private and cannot emit
duplicate progress frames.

Repository delivery is decision-conditioned. A task-mentioned path may expose
only the certified file location; it does not authorize arbitrary definitions
inside that file. Structural roles require an exact symbol or relationship
target already present at the Mini-SWE decision boundary, and malformed graph
symbols are rejected before provider delivery. This is a precision boundary,
not a requirement to make every task receive text.

Deep metrics now report response batching, actions per actual model invocation,
typed progress observations/gains, preserved redirected validators, adaptive
versus default validator timeouts, and observed action timeouts. The strict
aggregate gate includes `assistant_steps` and controller-inclusive
`effective_actions`; lower tokens cannot hide extra calls, steps, or host work.
The exact regressions are part of `central_pre_smoke_gate.py`.

The focused repair tests, exact provider-free workflow scope, all-17 census,
readiness audit, Ruff, compilation, and archived ten-task replay pass locally.
This is provider-free implementation proof, not evidence that live call/step
deltas have turned negative. No post-repair paid smoke has run, and the
89-task benchmark remains blocked.

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

Agent Retrieval Bench is a retrieval diagnostic, not proof that the model
reasoned over GroundTruth or that tasks improved. Final evidence is layered:
retrieval correctness, exact next-request delivery, paired decision-point
utility, then end-to-end outcome. Decision-point evaluation uses identical
control/treatment requests differing only by the bounded grounded GT payload;
it does not add markers, request acknowledgements, or inspect hidden reasoning.
Observable action changes are classified as beneficial, harmful, equivalent, or
indeterminate. The durable execution ledger and 15-minute heartbeat are in
`FINAL_EXECUTION_TODOS.md`; no paid run starts without its gate and explicit
authorization.

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

Agent Retrieval Bench run `31517629497` is the authoritative retrieval-only
measurement. It evaluated all 427 rows at retrieval commit `433c330`; the
stored report is `RETRIEVAL_BENCH_RESULTS.md`. ARB proves ranked/delivered file
selection and bounded context packing. It does not prove model utilization,
solve uplift, or causal benefit.

ARB and the live central agent must import the single immutable
`gt_engine.retrieval_profile.FINAL_RETRIEVAL_PROFILE`. The profile fixes
channel limit 100, top-K 20, complete-evidence selection limit 8, 1,200 tokens,
12,000 task characters, and a 32-span dense candidate pool. The dense backend
is the pinned local Snowflake Arctic Embed M ONNX model. It never downloads or
calls a provider from the agent. GitHub workflows provision the content-hashed
asset from release `gt-retrieval-runtime-v1`; the expected model SHA-256 is
enforced before Mini-SWE starts.

Live retrieval has two measured deadlines. A cold repository/retriever receives
30 seconds because the accepted ARB p99 was approximately 23.1 seconds. After
the backend's content-hash passage cache is populated, every turn has a strict
two-second fail-open deadline. The local real-model witness completed cold in
4.9–6.5 seconds on a two-document repository and the next turn in 303 ms. A
timeout or backend failure abstains and preserves the ordinary model loop.

All model-visible GT surfaces now enter the typed
`gt_engine.contributions.GTContribution` boundary before provider injection.
The compiler assigns every contribution exactly one disposition, rejects stale
or late facts, suppresses duplicate claim/fact/text identities across surfaces,
and packs only complete contributions. Controller-only work is accounted but
never rendered as text. `candidate_count == accounted_count` is mandatory on
every call. `gt_engine.component_registry` is the machine-auditable inventory
for the active engine and all 17 lifecycle contracts; historical files are not
active merely because they exist.

This is implementation and provider-free proof. Exact GitHub workflow
`31526751148` passed the real dense witness, readiness, all-17 census, exact
pushed-tree gate, and `SMOKE_APPROVED` at `e4eab72`. Paired decision-point
utility remains required before any paid GT-on benchmark. An existing
DeepSWE-off artifact is reusable only if it passes the exact
`gt.deepswe.central.evaluation.v1.1` schema and the complete
task/model/provider/runner/prompt/tool/limit/outcome identity gate. A censored,
older-schema, or identity-incomplete artifact is not a frozen control; in that
case the same checked-in Mini-SWE workflow must produce a new GT-off arm before
any A/B claim. The next broader product diagnostic is Terminal-Bench 2.0
through Mini-SWE, not OpenHands or OpenAgents. TB2.0 is not labeled a current
Terminal-Bench 2.1 leaderboard result.

## Decision-sufficiency and release boundary (2026-08-12)

The action boundary now has a separately gated deterministic
`decision_sufficiency` stage. It receives the normalized `ProposedAction`
after model selection and before `environment.exec`, but it may return an
action only for a current, complete, mechanically certified repository claim
that is absent from the exact selecting provider request and retained history.
Only a single-target `EDIT`, `CREATE`, or `DELETE` is eligible. Ambiguous
parsing, incomplete provider visibility, stale semantic or graph revision,
sparse/dense-only support, co-change evidence, duplicate evidence, and any
budget overflow produce `PASS`. Evidence is complete-fact only and never
contains the raw command.

This stage is not a second retrieval pipeline and does not execute the
Snowflake ONNX embedder per action. It takes a bounded target-and-neighbor slice
from the already refreshed repository substrate and uses exact, sparse-ranking,
and certified structural evidence to decide sufficiency. The frozen hybrid
retriever remains the general next-observation retrieval engine. Paid workflows
keep preflight in `SHADOW`: eligible decisions are receipted but the original
command and batch behavior are preserved. `ASSISTIVE_SAFE` requires a separate
provider-free and matched-smoke approval.

Repository readiness is fail-closed. Manifest construction prunes derived
trees before entry limits; recovery from an unhealthy sensor snapshot rehashes
all supported source; graph refresh waits longer than the indexer's bounded
subprocess deadline; and final receipts resolve graph evidence from the atomic
session state. `scripts/central_release_gate.py` checks these substrate facts,
dense readiness, exact delivery timing and request hashes, preflight accounting,
and every decision receipt. Provider-free GitHub certification must build the
current Go indexer; a stale local Windows binary never justifies weakening the
gate.

## Final promotion repair contract (2026-08-12)

Do not equate a ranked repository candidate, a provider-deliverable content
claim, and a decision-authorizing claim. A content claim is identified only by
path/span/symbol/relation/text; graph row IDs, channel support, and unrelated
source revisions cannot make it new. A decision claim is separately bound to
the proposed operation and exact target.

Structural payloads must be edge-endpoint aligned. GraphDB source/target
symbols and lines survive into `StructuralLink`; unresolved file-level
neighbors may rank but cannot certify delivery or `RETURN_ELIGIBLE`. RRF must
deliver the representative that owns the certificate; it may never borrow an
exact or structural certificate to expose another channel's unrelated span.
Generic `IMPORTS` and co-change evidence cannot authorize action return.

Live retrieval is budget-first. A closed budget executes zero channels. A
positive partial character budget is enforced while complete evidence spans
are packed, so the host never marks an over-budget frame selected and then
discards it.
Identical query/source/visibility/budget state reuses the bounded runtime cache.
The task budget reserves up to 3,000 characters for post-mutation, diagnostic,
and validation opportunities. Every provider boundary records a typed
opportunity plus candidate, selected, delivered, visible, abstention, cache,
latency, and reason accounting. Delivery count alone is neither engine work nor
causal help.

DeepSWE treatment requires all four fail-open outcome controls: provider-budget
compaction, completion control, semantic progress control, and adaptive
validation timeout. `scripts/central_release_gate.py` fails closed when any is
disabled, when retrieval runs after budget closure, when opportunity accounting
is absent, or when decision evidence lacks semantic/endpoint identity. The
workflow remains `SHADOW` for pre-action return until a separately authorized
matched smoke passes. The local Windows `gt-index.exe` remains non-authoritative
when it lacks `objective_c`; only the Linux build gate may certify the native
substrate.

Provider-free workflow `31616184187` passed this final-promotion contract on
runtime commit `80a8376`: current native indexer, pinned Snowflake ONNX,
repository and language substrates, central tests, readiness, every required
all-17/timing/context line, `READY`, and `SMOKE_APPROVED`. Its uploaded receipt
records `provider_calls: 0`. This proves implementation integrity, not solve
uplift or non-regression; a paid matched smoke still requires authorization.


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

## Frozen local baseline downloads — check before any comparison

GT-off baselines are already downloaded locally; do not re-download,
re-derive, or treat a GitHub artifact as the only copy:

- **Terminal-Bench 2.0 GT-off (frozen, 89-task):**
  `D:\gt_runs\miniswe_tb2_gtoff_20260731\merged_local.json`
  (+ `SUMMARY.md`, `SUMMARY_local.md`, `per_task_tokens.json`; raw matrix at
  `matrix_cache\`). Model `deepseek-v4-flash` via `eval.miniswe_agent:MiniSweAgent`,
  89/89 graded, **66 solved**, 4 infra timeouts (AgentTimeoutError). Sharded
  TB2 baselines also at `D:\gt_runs\full89_2026-07-29\` (runs 30500795038..).
- **DeepSWE GT-off (frozen, 10-task matched control):**
  `D:\tmp\opencode\gt-off-31824834187\DEEPSWE_EVALUATION_RESULTS.json`
  (identical copy at `D:\tmp\opencode\smoke-baseline-check\`). GitHub run
  31824834187 at GT commit `67b7ef50`, arm=gt_off, integration_mode=off,
  comparison_profile=baseline, model `deepseek-v4-flash`,
  provider `deepseek:native:api.deepseek.com`, step_limit 300, budget 5400s,
  runner `datacurve-pier==0.3.1`, `resolved_workspace_v1`, **4/10 solved**,
  no censoring. SHA-256:
  `707d7eb7c36d1ea147b6b337eb855022acd74d6ac33ce7c39134f3590a6fac63`.
- DeepSWE/TB2 benchmark sources are also retrievable from datacurve.io and the
  `datacurve-ai/deep-swe` GitHub repo (pinned snapshot
  `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9`). Prefer the local copies.

## NEVER run GT-off yourself — fetch the online baseline instead

NEVER run a GT-off evaluation ourselves, and never put GT-off in a plan —
**not even in the plan** — unless the user explicitly types the override
("run GT-off" / "not even in the plan and get the results of GT off" is the
rule: fetch results online, never run them). Use the official public
leaderboard for the comparison baseline:

- **DeepSWE v1.1 official leaderboard (GT-off reference, live):**
  `curl -s https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json`
  (local copy: `D:\tmp\opencode\deepswe_v11_leaderboard.json`). Also
  `/artifacts/v1/tasks.json` (task list) and
  `/artifacts/v1/distribution.json` at the same host. Generated
  `2026-08-13T16:11:55Z`.
- **deepseek-v4-flash GT-off baseline (mini-swe-agent, v1.1, 113-task set,
  n_runs=4):** pass@1 **0.5332** (241/452), 95% CI [0.4975, 0.5689],
  pass@4 0.8053, mean cost $0.100/task, mean duration 1,439s,
  mean agent steps 152.9. Use this `deepseek-v4-flash` row ONLY. The
  `deepseek-v4-pro` row (pass@1 0.6283) is a different model — never use
  pro as our baseline.
- For Terminal-Bench 2.0 use the frozen local 89-task GT-off baseline above
  (66/89, `deepseek-v4-flash`); do not run a fresh TB2 GT-off either.

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

## Semantic-authority repair (2026-08-13)

The earlier repeated-CORE and bootstrap-selection requirements above are
superseded by this boundary. Persistent state remains updated at every eligible
host transition, but provider delivery is selective: a frame is sent only when
it contains a newly certified related file, a new unresolved task obligation,
a current attributable failure, a declared validation-state change, a stale
assumption invalidation, or a source-bound completion blocker. An unchanged
phase, focus, or generic validation-pending state produces no provider payload.

Repository evidence now carries explicit origin and authority. Exact path or
symbol equality proves identity only and may rank a file; it cannot by itself
authorize delivery. Model-authored source, task deliverables, generated
artifacts, external runtime paths, and the active/changed file are never
presented as novel repository context. A different pre-existing file connected
to the active file by a current certified graph relation remains deliverable.
Execution-derived failures and diagnostics retain their separate observation
authority. Selected source text already represented in the retained provider
view is filtered before dispatch.

The one bounded bootstrap provider call remains accounted, but its generative
selection is optional guidance. Invalid JSON, invalid catalog IDs, timeout, or
a typed provider error selects a deterministic catalog fallback and does not
silence the state engine. Receipts distinguish `generative_selected` from
`deterministic_fallback`; a fallback must never be described as model-selected.
Initially source-less tasks may activate state after source creation, but the
first model-authored file does not become pre-existing repository evidence.

Current focus is typed as repository source, task deliverable, external
runtime, or artifact. Only canonical, workspace-contained, indexable current
repository source may appear as repository focus. `/tmp`, `/root`, `/etc`,
caches, binaries, data files, unsupported files, and generated outputs remain
controller-only. Deleted or reclassified paths clear focus deterministically.

Observed validation and completion authority are separate. A self-authored
check may record an observed pass but cannot produce `READY_TO_SUBMIT`.
Readiness requires all task-declared or host-certified checks to pass at the
current source revision. Failed validation diagnostics skip misleading leading
`PASS`, `OK`, or `SUCCESS` lines and prefer the actual assertion, exception,
error, or final failure summary.

Model-visible surfaces use neutral provenance labels such as `Repository facts
for the next decision` and `Current task execution status`. They never claim
that the controller is GroundTruth, a reference implementation, or a hidden
evaluator. Delivery receipts record origin, authority, provider-view novelty,
model knowledge, materiality reason, source and origin revisions, relation
endpoint, and declared-validation ID. The authoritative audit rejects
self-echoed source, model-authored content presented as prior repository fact,
non-repository focus, self-test-only readiness, phase-only repetition, and
evidence already present in provider history.

Local Python proof of this boundary does not waive the stale Windows
`gt-index.exe` failure. Release remains blocked until the exact pushed commit
passes the source-built Linux provider-free workflow with the pinned dense
asset. No paid smoke or benchmark is authorized by this repair alone; the next
comparison must use a contemporaneous Mini-SWE GT-off control with identical
provider, model settings, tools, task revision, budgets, and runner.

## Stale-read/validation elision and typed recap receipts (2026-08-14)

Compaction is deterministic and reasoning-preserving: it clears only old tool
bodies, never assistant content or reasoning. Inside a compaction epoch it now
additionally applies two typed mechanisms, both driven by the shared
`progress_ledger` and never by raw command parsing:

1. **Stale-read elision (Phase A).** A tool body is superseded and replaced by
   the typed marker `[Superseded read result cleared: path=… revision=<old>
   reread_revision=<current> chars=… sha256=….]` only when the body's
   `extra.raw_output` hash-identifies exactly one typed read observation at an
   earlier source revision AND the ledger records a different read of the same
   path at the current source revision AND the executing command mentions the
   path. Stale failed validations are elided only when the same command passed
   (returncode 0) at the current revision with no matching unresolved failure at
   the current revision. Search-anchor observations can never authorize read
   elision.
2. **Typed recap receipts (Phase B).** A cleared body that carries typed ledger
   identity becomes one atomic bounded receipt
   `[Earlier tool result cleared: command_sha256=…; read path@rev; returncode=…;
   chars=…; sha256=….]` capped at 200 characters, never containing command text.
   Any overflow falls back to the historical bare hash receipt byte-for-byte.

The identity ledger is authoritative and all-revision. `progress_ledger()`
exposes `recent_reads` (current-revision only, consumed by the provider-visible
state frame) AND `read_history` (the full bounded read ledger, consumed only by
elision/recap identity and never a context fact). Stale-read elision and recap
read-identity must read `read_history`; a `recent_reads`-only implementation is
dead code in the live path. `output_hash` and `_raw_output_hash` both use
UTF-8 `replace` encoding so identity can never diverge. Every marker's
`chars`/`sha256` must verify against the cleared body, and every elision/recap
fires only inside an epoch: below-trigger views remain byte-identical.

The deep audit is recorded in
`details_done/GT_COMPACTION_ELISION_RECAP_DEEP_AUDIT_20260814.md`, including
real-trajectory replay (run `31557391617`, real `go.mod` read elided), 60/60
property cases, 5 adversarial cases, wiring proof through
`DIAGNOSTIC_METRICS`/`compare_arms`, and end-to-end `CentralFeatureRuntime`
ledger tests. This is deterministic implementation/integration proof only; it
does not waive the stale Windows `gt-index.exe` blocker, does not authorize a
paid smoke, and does not claim solve/efficiency uplift. Only the source-built
Linux provider-free workflow certifies readiness.

## Python-depth language parity and graph-bound frontier revisions (2026-08-14)

Every registered caller-capable structural language must be proven at the same
depth as Python, and the frontier staleness comparison must key on the graph's
own revision — not the full semantic revision.

1. **C/C++ declarator-name fix.** tree-sitter-c/cpp expose the function name
   only through the NameField `declarator` wrapper
   (`function_declarator -> identifier`), so the naive extractor stored names
   like `get_bit(int ctx)`. The resolver binds bare call callees to node names,
   so a signature-laden name silently produced **zero CALLS edges on every C/C++
   task** (write-compressor: 18 C nodes, 0 edges despite real intra-file calls).
   `functionNodeName` now unwraps the declarator chain to the bare identifier,
   grammar-scoped like the Verilog fallback. C++ overloads
   (`foo(int)` vs `foo(long)`) fall to the existing multi-def CANDIDATE branch
   and are never mis-certified. A poisoned name (contains `(`) is dropped rather
   than emitted. Verified by replication: write-compressor's decomp.c now yields
   clean names and 8 certified same-file edges.
2. **Name-sanity invariant.** A definition node name must never carry signature
   text. Enforced in the parser (poisoned names are dropped) and as a
   registry-wide audit in `scripts/verify_gt_index_runtime.py` (any fixture
   language emitting `(`/`)` in a node name fails the gate). This invariant
   would have caught the C regression at parse time.
3. **Fixture-gate depth parity.** `verify_gt_index_runtime.py` certifies
   directed SQLite CALLS edges for 30 caller-capable languages: the prior 14
   plus C, C++, JavaScript, Rust, bash, Go, Java, C#, PHP, Swift, Kotlin,
   Scala, Ruby, TypeScript, Elm, and OCaml. C's comment-only fixture is
   replaced by a real multi-function file; bash adds a negative control
   (`return`/`echo`/`cat` must never certify an external-command edge). A
   fail-closed cross-check requires every caller-capable structural registry
   language that ships a real fixture to be edge-certified, so a future
   regression cannot go silent at file-hash coverage. Go parser tests assert
   bare names and zero-based `CallerNodeIdx` for C/C++/bash/elm/ocaml.
4. **Registry over-claim fixes.** The vendored-spec-vs-grammar audit found
   specs referencing node types their grammars do not emit, so `caller_support`
   is now `False` for every language that cannot be proven at Python depth:
   `css`/`html`/`protobuf`/`sql` (grammars expose no call nodes), `cue`/`hcl`
   (grammars emit calls but no definition nodes), `lua` (spec
   `function_declaration`/`function_definition_statement` absent; grammar emits
   `function_statement` with no named fields), `groovy` (spec
   `method_declaration`/`method_invocation` absent; grammar uses `func` for both
   defs and calls), `svelte` (`<script>` content is `raw_text`), and `elixir`
   (spec BodyField `body` absent; the `def` keyword is the first call child).
   Reaching Python depth for those requires grammar-aware spec/parser work
   verified on the source-built Linux indexer. `ocaml`'s CallNodes were fixed
   (`application` -> `application_expression`) and elm/ocaml name extraction
   now descends `function_declaration_left`/`value_name` wrappers.
5. **Frontier staleness keys on the graph-bound revision.** The frontier
   compared evidence (graph-bound) against the full semantic source revision,
   which includes non-indexable authored files (a model-written `.pl` helper in
   write-compressor; 389 `exp_data/*.json` data files in sanitize-git-repo).
   Those tasks could never converge, so post-edit delivery was permanently
   stale-rejected (write-compressor 7/11, sanitize-git-repo 28 total).
   `compile_incremental_frontier` is now called with
   `graph_source_revision or source_revision`, which only advances on completed
   graph refreshes; genuine staleness is still caught because the graph revision
   is the evidence's own currency. Replayed write-compressor evidence through the
   real frontier compiler: before-fix `stale_source_revision`, after-fix
   `selected_frontier` with three certified caller facts rendered to the model.

This is implementation proof only. The local Windows `gt-index.exe` predates the
fix and the gate now correctly fails-closed on it (C/C++ edges are SPECULATIVE
0.2 and elm/ocaml produce no definitions in the old binary); only the
source-built Linux provider-free workflow certifies readiness for this repair,
and no paid smoke or solve/efficiency claim is authorized.
