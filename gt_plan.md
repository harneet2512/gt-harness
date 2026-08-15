# GroundTruth x Mini-SWE-Agent Amplification Program

Status: implementation handoff and research protocol  
Baseline capability reference: Mini-SWE-Agent 2.3.0  
Primary repositories: `D:\gt-harness`, `D:\Groundtruth`  
Research outputs: `D:\gt-harness\.research\gt-amplification\`  
Last updated: 2026-08-01

## 1. Mission

Build an additive GroundTruth (GT) layer that preserves the complete Mini-SWE-Agent capability surface while adding deterministic intelligence that converts baseline failures into correct solutions with less waste.

The target is:

```text
same resolved model
+ same task environment
+ all original Mini-SWE-Agent capabilities
+ optional deterministic GT intelligence
= more officially resolved tasks
 + fewer redundant actions
 + lower cost per resolved task
 + no material negative flips
```

GT is not successful merely because evidence is generated, delivered, mentioned by the model, assigned an L4 receipt, or associated with a passing task. A positive claim requires a valid paired baseline, novel evidence delivered before the corrected decision, a changed downstream action, a correct patch consequence, and independent task verification.

## 2. Non-negotiable capability contract

Mini-SWE-Agent with GT enabled must retain:

1. Any shell command permitted by the task sandbox.
2. Multiple tool calls in model-selected order, exactly once each.
3. Repeated commands, polling, retries, and flaky-test confirmation.
4. `rg`, `grep`, `find`, `git grep`, Python, pipelines, or any other available search method.
5. Reading any accessible repository path, including files outside GT rankings.
6. Reading complete files or arbitrary ranges subject only to Mini-SWE's baseline observation cap.
7. The original command result, return code, exception, and tool-call association.
8. Editing, creating, deleting, and renaming any permissible file.
9. Running, repeating, combining, or omitting any available test.
10. Pursuing hypotheses not suggested by GT.
11. Ignoring GT evidence and changing strategy.
12. Submitting with no GT evidence or with UNKNOWN evidence.
13. Continuing after an advisory or a legitimate proof-backed refusal.
14. The same resolved model, provider policy, tools, parameters, budgets, task filesystem, and resource limits as GT-off.
15. Transparent startup bypass and automatic fail-open degradation.
16. Truthful separation of process, solver, GT, grader, artifact-integrity, and research-validity outcomes.

Any hidden restriction is a regression even when one scripted happy path still passes. This includes coercive prompt language, changed tool schemas, output replacement, discoverable environment drift, hidden live-workspace commands, repeat governors, invisible action suppression, and UNKNOWN-based proof loops.

## 3. Current-system verdict

Confidence is high that the current GT-on integration is capability-restricting and research-invalid.

### 3.1 Current execution path

```text
Harbor
-> eval/miniswe_agent.py selects GT-off or GT-on adapter
-> mutable in-container installation
-> scripts/miniswe_gt_run.py
-> LitellmModel + LocalEnvironment + stock DefaultAgent
-> GT contract/index construction
-> persistent GT system prompt
-> private Mini-SWE method monkeypatches
-> provider request/response wrappers
-> action parsing
-> GT lifecycle before_action
-> LocalEnvironment shell execution
-> workspace fingerprinting
-> syntax/covering analysis
-> legacy Groundtruth gateway
-> GT observation injection/directives
-> receipt and RED state
-> submit interception
-> runner terminal report and exit
-> Harbor result
-> independent verifier reward
```

### 3.2 Confirmed defects

- GT-off is close to stock `DefaultAgent`; GT-on changes policy and execution.
- The GT-on system prompt calls GT authoritative, makes `GT REQUIRES` binding, and forces ranked-file-first behavior.
- A repeat governor can suppress valid commands and transition to STUCK.
- Repeated refusal states can transition to STUCK.
- GT runs unrequested syntax and covering commands against the live workspace.
- Filename occurrence in failure output can be treated as causal RED.
- A deleted Python file can be passed to harness `py_compile` and mislabeled as syntax RED.
- `GTSession` exists but live hooks still call `MiniSweAdapter` directly.
- Groundtruth has a second canonical provider/commitment seam; enabling both would double-wrap Mini-SWE.
- Provider request commitment currently omits model, tools, temperature, routing, retry, and other effective configuration.
- Evidence is sealed before final tagging/capping, not over exact model-visible bytes.
- Provider failures lack symmetric terminal receipts.
- GT-on and GT-off resolve different mutable environments.
- Provider credentials are inherited by the process that owns the model-visible shell.
- The JSONL store cannot reconstruct the complete run or candidate workspace.
- Receipt promotion equates lifecycle completion with semantic resolution.

## 4. gton13 forensic ground truth

Run: `results/terminal-bench/miniswe-tb2-gton13-0801-1118`

```text
reported code-quality suite:    330 tests green, ruff clean
official verifier score:        8/10
submitted terminal reports:     2/10
GT STUCK terminations:          4/10
normal budget exhaustion:       1/10
outer Harbor timeouts:          3/10
runner reports present:         7/10
rewarded without submission:    6/10
research-valid run:             no
```

| Task | Reward | Termination | Derived classification |
|---|---:|---|---|
| break-filter-js-from-html | 1.0 | submitted_unverified | CLEAN_SUBMITTED_RESOLVED |
| fix-code-vulnerability | 1.0 | submitted_unverified | CLEAN_SUBMITTED_RESOLVED |
| cobol-modernization | 1.0 | outer timeout; no runner report | INTERRUPTED_RESOLVED |
| gpt2-codegolf | 0.0 | outer timeout; no runner report | INTERRUPTED_UNRESOLVED |
| write-compressor | 0.0 | outer timeout; no runner report | INTERRUPTED_UNRESOLVED |
| headless-terminal | 1.0 | LifecycleError after STUCK | GT_ABORTED_RESOLVED |
| llm-inference-batching-scheduler | 1.0 | LifecycleError after STUCK | GT_ABORTED_RESOLVED |
| modernize-scientific-stack | 1.0 | LifecycleError after STUCK | GT_ABORTED_RESOLVED |
| portfolio-optimization | 1.0 | repeat-action budget forced STUCK | GT_ABORTED_RESOLVED |
| schemelike-metacircular-eval | 1.0 | budget exhaustion returned exit 2 | SALVAGED_RESOLVED_WITH_EXIT_DEFECT |

The official score remains 8/10. It must not be rewritten as 2/10. The separate health facts prevent that score from being misrepresented as a healthy or causal GT result.

### 4.1 Stale-RED defect

An early failing test can create RED before the implementation is complete. Later workspace edits do not reliably invalidate the global latch. The current workaround accepts a passing test-file command with generic PASS markers and can mark every behavioral predicate GREEN. That can erase unrelated real failures.

Correct behavior is receipt-scoped:

```text
edit after RED -> prior receipt becomes INVALIDATED_BY_EDIT, not GREEN
same trusted verifier reruns successfully -> only matching RED is resolved
different passing command -> only directly grounded obligations may become GREEN
missing/stale/UNKNOWN -> nonblocking
fresh independently reproducible ACTIVE_RED -> potentially blocking
```

### 4.2 Repeat-governor defect

The `sleep; cat` exemption fixes one observed polling command but leaves the invalid policy. Repetition also covers flaky tests, different poll shapes, nondeterministic reproductions, repeated builds after background work, and stability confirmation. Repetition is telemetry, never an execution gate.

### 4.3 Outcome-audit defect

The current integrity script treats absent reports as `unknown` without necessarily failing and calls a correctly reported non-submitted reward "dishonest." Truthful reporting, health, completion, correctness, and research validity are orthogonal and require a joined outcome.

## 5. Research synthesis

The design is grounded in primary papers and official implementations. Local code and run artifacts remain authoritative for claims about the present integration.

| Source | Mechanism/finding | Decision for GT |
|---|---|---|
| [Mini-SWE control flow](https://mini-swe-agent.com/latest/advanced/control_flow/) and [v2 protocols](https://mini-swe-agent.com/latest/advanced/v2_migration/) | Small duck-typed Agent/Model/Environment loop | Preserve stock loop; attach one narrow session boundary. |
| [Mini-SWE environments](https://mini-swe-agent.com/latest/advanced/environments/) | LocalEnvironment uses subprocess without internal isolation | Keep credentials and GT authority outside the command namespace. |
| [Mini-SWE releases](https://github.com/SWE-agent/mini-swe-agent/releases) | 2.3.0 adds wall-clock handling | Pin 2.3.0 and test timeout layering; upgrade separately. |
| [SWE-agent ACI](https://arxiv.org/abs/2405.15793) | Interface design materially changes agent behavior | Treat prompt/tool/action changes as experimental treatments. |
| [Agentless](https://arxiv.org/abs/2407.01489) | Localization, repair, validation can be separated | Borrow optional deterministic stages; never replace autonomy. |
| [OpenHands runtime](https://docs.openhands.dev/openhands/usage/architecture/runtime) | Client/server sandbox separation | Place provider, journal, graph, verifier in a host-side broker/sidecar. |
| [Aider repository maps](https://aider.chat/docs/repomap.html) | Symbol/dependency maps provide compact context | Build bounded causal views while retaining full-file access. |
| [Repoformer](https://arxiv.org/abs/2403.10059) | Retrieval can be unnecessary or harmful | Lazy, selective, correct-or-quiet delivery. |
| [SWE-Explore](https://arxiv.org/abs/2606.07297) | Exploration should be scored for coverage/rank/efficiency | Component benchmark for file/function/line localization. |
| [FastContext](https://arxiv.org/abs/2606.14066) | Focused exploration can reduce solver context | Borrow focused ranges/accounting; no second model in same-model A/B. |
| [Fault-localization bias](https://doi.org/10.1109/ICST.2019.00020) | APR comparisons are biased by unequal localization | Identical localization conditions; no runtime gold leakage. |
| [Ekstazi](https://experts.illinois.edu/en/publications/practical-regression-test-selection-with-dynamic-file-dependencie/) | Dynamic file dependencies support regression-test selection | Recommend cheap affected tests; do not suppress arbitrary tests. |
| [Daikon](https://plse.cs.washington.edu/daikon/) | Dynamic invariants are likely properties, not proofs | Invariants remain advisory until an executed probe verifies them. |
| [SpecRover](https://haifengruan.com/assets/pdf/specrover_icse25.pdf) | Issue intent plus generated tests aids validation | Ground obligations and execute generated probes before evidence. |
| [UTBoost](https://arxiv.org/abs/2506.09289) | Existing tests may admit plausible wrong patches | Reward stays official authority but is not a GT semantic certificate. |
| [APR efficiency assessment](https://arxiv.org/abs/2008.00914) | Test-passing patches overfit and repair cost depends on localization/tests | Measure semantic evidence, test cost, patch candidates, and context. |
| [SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/) | Human validation improves but does not perfect task/test soundness | Keep benchmark correctness separate from harness and semantic claims. |
| [Harbor task model](https://www.harborframework.com/docs/tasks) | Agent and verifier are distinct phases | Never collapse process, solver, GT, grader, and artifact state. |

Rejected defaults:

- replacing Mini-SWE with a rigid Agentless workflow;
- always shipping a repository map;
- using a second explorer model in same-model comparisons;
- treating inferred invariants as proof;
- making test selection an allowlist;
- treating all passing tests as semantic certification;
- generating multiple patches for every task;
- enforcing regex/heuristic obligations.

## 6. Target architecture

```text
ORIGINAL MINI-SWE-AGENT
  owns hypotheses, Bash, search, reads, edits, tests, strategy, and submission

MINISWE_GT_SESSION
  one versioned integration boundary; no competing hook owner

GT OBSERVER
  observes task, requests, responses, actions, results, diffs, tests, and submit

GT DETERMINISTIC ENGINES
  repository facts, failure reproduction, causal localization,
  change surface, test mapping, receipt reconciliation

GT ADVISOR
  delta-only evidence IDs; bounded and optional; correct-or-quiet

GT ASSISTIVE OPERATIONS
  optional mechanical work; original Mini-SWE command path always available

GT GATE
  empty by default; only fresh proof-qualified ACTIVE_RED can block

HOST/SIDECAR AUTHORITY
  credentials, provider lifecycle, journal, graph, verifier, artifact store
```

Modes:

```text
OFF        exact stock behavior
SHADOW     compute/log only; no model or execution change
ADVISORY   optional tagged deltas; no action denial
ASSISTIVE  optional operation; baseline path retained
ENFORCED   unavailable to new mechanisms until independently promoted
```

## 7. Interfaces and data model

### 7.1 MiniSweGtSession

```text
start(task, environment_snapshot) -> SessionStart
before_model(finalized_logical_request) -> AdvisoryDelta | NoChange
after_model(provider_terminal) -> None
before_action(action) -> Allow
after_action(action, original_result, workspace_delta) -> AdvisoryDelta | NoChange
request_submit(workspace_snapshot) -> SubmitDecision
degrade(reason) -> BaselineContinuation
close(terminal_state) -> SessionSummary
```

OFF, SHADOW, and ADVISORY always allow non-submit actions.

### 7.2 Joined run outcome

```text
process_outcome:
  COMPLETED | SETUP_ERROR | PROVIDER_ERROR | PROVIDER_MODEL_MISMATCH |
  HARNESS_ERROR | SANDBOX_ERROR | INTERRUPTED

solver_outcome:
  SUBMITTED | UNVERIFIED_SUBMISSION | EXHAUSTED | DECLINED |
  STUCK | NOT_STARTED | UNKNOWN

gt_outcome:
  INACTIVE | SHADOW_OK | ADVISORY_OK | DEGRADED_FAIL_OPEN |
  PROVEN_RED_REFUSAL | GT_ABORTED | INVALID

grader_outcome:
  PASS | FAIL | UNAVAILABLE

artifact_integrity:
  COMPLETE | INCOMPLETE | CONTRADICTORY | TAMPERED

research_validity:
  VALID | INVALID
```

Derived labels:

```text
CLEAN_SUBMITTED_RESOLVED
CLEAN_RESOLVED
SALVAGED_RESOLVED
INTERRUPTED_RESOLVED
GT_ABORTED_RESOLVED
CLEAN_UNRESOLVED
INFRASTRUCTURE_INVALID
UNCLASSIFIABLE
```

### 7.3 Evidence and obligations

Evidence records contain stable ID, producer, source artifacts, workspace epoch, content digest, direct/inferred status, confidence, novelty, actionability, affected obligations/hypotheses, freshness, mode, exact model-visible byte digest, and expiry rules.

Obligations contain stable ID, claim, kind, exact source span/type, grounding artifacts, verification method, confidence, enforcement eligibility, and state. Only grounded obligations can become verification requirements.

### 7.4 RED receipts

```text
receipt_id
predicate_id
obligation_id
trusted_verifier and version
normalized argv
controlled environment digest
workspace epoch and digest
dependency-surface digest
failure fingerprint
direct requirement link
artifact references
status
```

Statuses are `ACTIVE_RED`, `INVALIDATED_BY_EDIT`, `RESOLVED_BY_RERUN`, `SUPERSEDED`, `ACTIVE_GREEN`, and `UNKNOWN`. Only current `ACTIVE_RED` blocks.

### 7.5 Canonical events and provider receipts

Events are sequenced, parent-hashed, timestamped, treatment-tagged, workspace-epoch-bound, and refer to content-addressed payloads.

Provider receipts bind requested, normalized, effective, provider-reported, and fallback models; messages; tools; temperature; request kwargs; routing; retry; timeouts; provider IDs; response/error digest; usage; and latency. A local correlation ID is never called a provider request ID.

## 8. Implementation phases

### Phase 1: execution truth, capability preservation, isolation, replay

Target failure class: `HARNESS_FAILURE` plus capability regression.

1. Snapshot both repositories, dirty diffs, vendored artifacts, configs, and historical runs.
2. Create execution-edge and claim ledgers.
3. Add RED-first tests for the exact gton13 outcome combinations.
4. Introduce the joined outcome schema and reducer.
5. Replace the binary integrity audit with a Harbor/runner/journal/grader join.
6. Make normal exhaustion process-success and gradable.
7. Add wrapper-owned reports for construction failures and outer timeouts.
8. Coordinate work, graceful-close, and Harbor deadlines without reducing the model's effective budget.
9. Make one session the runtime owner.
10. Reuse canonical journal/provider lifecycle but disable commitment withholding.
11. Remove authoritative and binding prompt text.
12. Demote repeat control to SHADOW.
13. Remove refusal-to-STUCK escalation.
14. Replace global RED with receipt-scoped freshness.
15. Disable hidden commands against the live workspace.
16. Add bounded fail-open degradation and restoration of original methods.
17. Pin Mini-SWE 2.3.0, Python, all dependencies, wheel, binary, and runner sources.
18. Use the identical bundle in both arms.
19. Move provider credentials behind a per-attempt broker.
20. Hash finalized logical request and exact model-visible evidence bytes.
21. Record symmetric provider failure receipts.
22. Implement hash-linked journal replay with workspace deltas.
23. Generate a per-run reproducibility manifest.
24. Produce Sections 0, 1, and 3 reports.
25. Stop before solver-intelligence changes.

### Phase 2: baseline failure atlas

Run fresh valid GT-off trials only after explicit paid authorization. Reconstruct the earliest causal failure, exact missed fact, and smallest additive intervention. Do not describe the intervention as merely "more context."

### Phase 3: causal audit of the 17 features

For each mechanism measure trigger, producer, timing, model-visible bytes, hidden actions, latency, duplication, next action, positive/negative flips, capability effect, and recommended mode. Triggering or delivery alone receives no efficacy credit.

### Phase 4: component benchmarks

Build offline evaluations for contract grounding, reproduction, file/function/line localization, change surface, test selection, hypothesis contradiction, semantic verification, and capability preservation. Historical gold is label-only and never exposed at runtime.

### Phases 5-10: isolated mechanisms

Select the largest addressable failure class, implement the smallest mechanism in SHADOW, measure trigger accuracy and cost, adversarially falsify it, move to ADVISORY, run an isolated paired A/B, then retain/revise/revert.

### Phases 11-13: combination and full evaluation

Combine only individually proven mechanisms. Run repeated paired experiments and produce a causal dossier for every claimed flip.

## 9. Offline verification

Offline tests make no paid provider calls.

### Static architecture checks

Fail on multiple hook owners, coercive prompt phrases, SHADOW byte changes, GT activation in OFF, action denial in advisory modes, enforced weak evidence, secret leakage, unpinned official dependencies, or live installation in official paths.

### Real-loop capability parity

Use the real Mini-SWE 2.3.0 `DefaultAgent`, a deterministic scripted provider, and a disposable real shell repository. Cover arbitrary Bash, searches, full/unranked reads, cap boundaries, edit/create/delete/rename, multiple tool calls, repeated actions, alternate hypotheses, ignored advice, strategy changes, immediate submit, UNKNOWN submit, and work after refusal.

After removing tagged advisory additions:

```text
executed actions equal
action order equal
tool-call IDs equal
baseline observations byte-equal
workspace equal
model/tools/temperature/provider policy equal
terminal outcome equal
```

### RED-state tests

Cover early failure followed by edit, exact rerun pass, different pass, unrelated PASS marker, stale RED, current RED, pre-existing failure, candidate-only failure, filename-only text, deletion, wrong interpreter, verifier-version change, workspace mismatch, and stale replay.

### Failure injection

Inject setup, index, graph, contract, localization, producer, renderer, sealer, journal, provider, model mismatch, action-before, action-after, delta-capture, submit, close, runner-timeout, and Harbor-timeout failures. Optional GT failure must restore baseline without re-executing ambiguous actions.

### Outcome joins

Cover reward 1/0 crossed with submit, exhaustion, GT abort, timeout, missing/malformed report, process mismatch, journal mismatch, provider failure, verifier unavailable, and artifact tampering. UNKNOWN is always research-invalid.

### Replay

Replace provider and executor with traps; reconstruct messages, actions, observations, workspace epochs/content, receipts, submit, and terminal state with zero calls. Crash after each event boundary and test mutation/reorder/duplicate/truncation rejection.

### Component metrics

- Contract: precision, recall, garbage-obligation rate.
- Localization: file/function recall, line coverage, rank, bytes, time to correct region.
- Change surface: required recall, unnecessary precision, false MUST_CHANGE.
- Tests: failure exposure, changed-code coverage, redundancy, runtime.
- Semantics: false accept/refusal, wrong-patch detection, stale rejection.

## 10. Online validation

Online means real Harbor containers and real provider calls. Every paid dispatch requires fresh explicit authorization.

1. One-call canary for model identity, broker secrecy, exact receipts, and failure accounting.
2. Paired GT-off/SHADOW smoke requiring byte/action/workspace parity and replay.
3. Shared advisory smoke for one mechanism, measuring novelty, timing, next action, saved work, and terminal health.
4. Failure-flip suite stratified by baseline failure class.
5. Single-mechanism ablations.
6. Combination only after individual value.
7. Repeated full benchmark with identical model, environment, packages, order, concurrency, limits, and retries.

Invalid pairs are never silently dropped. They remain in the report and are rerun only under a preregistered retry rule.

## 11. Analysis and promotion

Metrics include official/clean/submitted resolves, positive/negative/net flips, invalid rate, actions, tokens, latency, cost, cost per resolution, redundancy, time to correct region/edit, false assertions, false blocking, and degradation.

Use paired McNemar analysis for flips, paired bootstrap confidence intervals for net flips, paired permutation or Wilcoxon analysis for costs/actions, task effects across repetitions, and one-sided false-block confidence bounds.

Advisory promotion requires complete capability preservation, research-valid runs, positive net flips with a confidence interval excluding zero, reduced cost per official resolve, no false blocking, controlled overhead, and generalization beyond the design slice.

Enforcement additionally requires deterministic proof, zero false blocks in at least 300 independent offline applicable cases, zero false blocks in online shadow/advisory trials, visible reproducible reason, demonstrated net benchmark value, bypass, and UNKNOWN nonblocking.

## 12. Efficiency budgets

Initial limits:

```text
SHADOW synchronous hook p95 <= 50 ms
hidden live-workspace commands = 0
ADVISORY delivery <= 1,200 characters
ADVISORY deliveries <= 3 per task
repeated full evidence blocks = 0
mandatory UNKNOWN proof loops = 0
silent GT stalls = 0
```

Use evidence IDs, delta-only delivery, bounded ranges, external full history, compact active state, stale evidence expiry, lazy computation, and explicit feature budgets. Full-file access remains available through stock Mini-SWE.

## 13. Deliverables

```text
SYSTEM_RECONSTRUCTION.md
CAPABILITY_PRESERVATION_MATRIX.md
RESEARCH_VALIDITY_AUDIT.md
BASELINE_FAILURE_ATLAS.md
baseline_failure_atlas.json
FEATURE_CAUSAL_AUDIT.md
RESEARCH_GAP_MATRIX.md
GT_AMPLIFICATION_ARCHITECTURE.md
COMPONENT_BENCHMARK_SPEC.md
EXPERIMENT_MATRIX.md
IMPLEMENTATION_PLAN.md
RISK_REGISTER.md
causal-dossiers/
task-timelines/
reproducibility_manifest.schema.json
run-manifests/
test-results/
```

Research reports remain local and uncommitted unless explicitly requested.

## 14. Immediate build order

1. Preserve pre-change repository/artifact identities.
2. Add failing outcome-join tests for gton13.
3. Implement outcome types/reducer and repair integrity reporting.
4. Correct process versus solver exits and timeout records.
5. Add failing capability tests.
6. Remove repeat/refusal control and coercive prompt text.
7. Replace global RED with receipt-scoped freshness.
8. Consolidate to one fail-open session owner.
9. Add request receipts, manifest, journal integrity, and replay foundations.
10. Run targeted tests, actual CLI/integration flow, full suite, ruff, coverage, and diff review.
11. Produce Sections 0, 1, and 3 artifacts.
12. Stop before adding solver intelligence or launching paid runs.

## 15. Locked assumptions

- Mini-SWE-Agent 2.3.0 is the first capability reference.
- Current dirty WIP is preserved and selectively salvaged.
- Model forwarding and typed exits are provisional until failure injection passes.
- Generic PASS-marker global clearing is rejected.
- Sleep-poll exemption is a regression witness, not the safety mechanism.
- Official reward, process health, solver state, GT state, semantic evidence, and research validity stay separate.
- Fresh paired baselines are allowed only after Phase 1 and explicit paid authorization.
- No GCP action is part of this work.
- No solver-intelligence mechanism is added before Phase 1 passes.

