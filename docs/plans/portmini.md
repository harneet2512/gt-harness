# Porting Groundtruth onto Mini-SWE-Agent

Status: design and implementation contract  
Scope: `gt-harness`, Mini-SWE-Agent plus Groundtruth only  
Model for the first proof: DeepSeek V4 Flash, temperature 1, profile 2  
Benchmark proof: five-task Terminal-Bench 2.0 smoke, concurrency exactly 5

## Executive decision

Groundtruth should be ported to Mini-SWE-Agent as a deterministic sidecar with
an explicit lifecycle controller. It must not be ported as the current nano
integration's stream of synthetic advice, recurring context blocks, or generic
tool restrictions.

The purpose of the port is not to make GT reason generatively. GT remains
deterministic. Its job is to compile the task and repository into typed state,
deliver only decision-relevant evidence, execute narrow verification, and own
the admissible transitions to `FINISHED` or `STUCK`. Mini-SWE-Agent remains the
model-facing action-observation loop.

The claim to prove is:

> For the same model, tasks, resources, and temperature, Mini-SWE-Agent plus a
> correctly integrated GT sidecar solves at least as many tasks as the frozen
> Mini-SWE baseline while using fewer total model tokens and fewer iterations,
> with no increase in timeout, harness, or verification faults.

“GT delivered a message” is not evidence of improvement. The proof must join a
GT delivery to the immediately following provider request, the model action,
the workspace change or verification receipt, and the final grader result.

## What the existing experiments establish

The current checkout is a nano-harness implementation; it does not yet contain
the upstream Mini-SWE-Agent runtime. The existing nano GT-off smoke is therefore
not a Mini-SWE baseline. It is still a valuable diagnostic control because it
uses the same model and task loop as the failing nano+GT candidate and passed
the comparable tasks. The port must create and freeze a separate Mini-SWE
GT-off baseline before making Mini-SWE-specific improvement claims. The old
nano baseline remains a compatibility control, not a substitute for that
Mini-SWE baseline.

The frozen GT-off nano baseline passed all four comparable tasks. GT-on runs
did not. Therefore the present regression cannot be attributed to the model or
to the basic terminal loop alone.

The latest live candidate, run `30610945914` on commit `6d26ebb`, produced 3/5
overall. On the four tasks comparable with the frozen GT-off run it produced
2/4, versus GT-off 4/4. It used 2,301,454 input tokens and 321,419 output
tokens over 347 iterations. The frozen GT-off run used 5,884,607 input tokens,
90,374 output tokens, and 195 iterations. GT reduced input bytes but increased
iterations by 77.9% and output tokens by 255.7%, while correctness fell.

The failure pattern is architectural:

| Observation | Meaning for the port |
|---|---|
| Batching knew two output artifacts were missing, received GT controls, and still researched through iteration 100 | Correct text is not deterministic control. A missing-artifact state needs an executable next-action boundary and a finite budget. |
| Build failed because global import/install state was not satisfied | Code localization and post-edit evidence are not a universal task ontology. Build/install predicates must be first-class. |
| Headless could stop earlier after verified completion, but other tasks reached their cap | Completion ownership is split. GT and the base loop need one authoritative finish transition. |
| Reshard could edit late and produce a final GREEN with no remaining turn | The controller must reserve a tool-free final turn and invalidate/rearm verification after edits. |
| Predicate receipts were frequently `0/N` despite external passes | Receipts must be typed executable checks, not successful-tool heuristics or prose. |
| Thousands of graph facts were ranked/rendered while obligations remained open | Graph retrieval must be obligation- and action-scoped, not a standing context dump. |
| A real `.gt` inspection occurred before state externalization | Harness state must be outside the graded workspace and guarded before dispatch. |
| Two tasks hit the 900-second Harbor timeout | Tool and lifecycle recovery are part of correctness, not post-hoc telemetry. |

## What Mini-SWE-Agent contributes

The upstream Mini-SWE-Agent design is useful as a constraint, not as a magic
score multiplier. Its documented properties are a small agent class, a bash
environment, independent subprocess actions, and a completely linear message
history. The project recommends it as a simple, stable baseline and reports
that it is used by multiple organizations, including Meta. None of that proves
that Muse Spark's Terminal-Bench result used Mini-SWE-Agent.

The official Terminal-Bench 2.0 leaderboard also shows no universally dominant
harness. It contains high-scoring custom agents, Codex CLI, Terminus, OpenHands,
OpenCode, and Mini-SWE-Agent. For Claude Sonnet 4.5, the published results are
close: Goose 43.1%, Terminus 42.8%, OpenHands 42.6%, Mini-SWE-Agent 42.5%, and
Claude Code 40.1%. Harness/model interaction matters more than selecting a
single fashionable scaffold.

References:

- [Mini-SWE-Agent source and architecture](https://github.com/SWE-agent/mini-swe-agent)
- [Terminal-Bench 2.0 official leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
- [Terminal-Bench 2.0 execution contract](https://www.tbench.ai/docs/run-terminal-bench-2-0)
- [Meta's Muse Spark 1.1 announcement](https://ai.meta.com/blog/introducing-muse-spark-meta-model-api/)

## Target architecture

```text
task + workspace
      |
      v
GT contract compiler -------------------- durable GT event log
      |                                             |
      v                                             v
typed obligations/predicates                 graph/index state
      |                                             |
      +------------------+--------------------------+
                         v
             lifecycle state machine
  ORIENT -> IMPLEMENT -> VERIFY -> SUBMIT -> FINISHED
       ^          |          |         |
       +----------+----------+---------+----> STUCK
                         |
                         v
              one compact provider view
                         |
                         v
                  Mini-SWE model action
                         |
                         v
                independent bash execution
                         |
                         v
      observation + workspace diff + executable receipt
```

There are exactly two durable authorities:

1. Mini-SWE's linear conversation and tool observations.
2. GT's typed event/contract/receipt log.

The provider view is derived state. It is never a third history, and it never
silently rewrites Mini-SWE's canonical conversation. Every GT byte in a request
must have a delivery ID and a reason for being present.

## Integration seam

Implement one adapter rather than scattering GT calls through Mini-SWE:

```python
class GroundtruthController:
    def start_task(self, task: str, workspace: str) -> TaskContract: ...
    def before_action(self, action: Action) -> ActionDecision: ...
    def after_observation(self, action: Action, result: Observation) -> State: ...
    def provider_suffix(self) -> ProviderSuffix: ...
    def submit_decision(self) -> SubmitDecision: ...
    def final_state(self) -> FinalState: ...
```

The adapter must be called at these physical points:

| Adapter point | Required behavior |
|---|---|
| `start_task` | Compile obligations, classify task mode, initialize external GT state, and deliver one bounded orientation capsule. |
| `before_action` | Check harness isolation, phase legality, duplicate/no-progress fingerprint, and remaining action budget. |
| Mini-SWE provider request | Append one schema-stable GT suffix to the provider view. Do not create unbounded synthetic user turns. |
| `after_observation` | Classify tool outcome, update workspace epoch, invalidate affected receipts, refresh graph only when required, and compile one next decision. |
| `submit_decision` | Run fresh predicate checks. Accept, refuse with exact unmet predicates, or transition to `STUCK`. |
| `final_state` | Stop tool calls. Permit one tool-free model response for the final summary. |

Mini-SWE's independent subprocess behavior should remain intact. GT may reject
an action before execution, but it must not mutate the command or filesystem
behind the model's back.

## Groundtruth state model

GT state is a typed, compact vector, not a transcript summary:

```json
{
  "phase": "VERIFY",
  "workspace_epoch": 12,
  "contract_epoch": 1,
  "unmet_predicates": ["artifact.plan_b1", "artifact.plan_b2"],
  "latest_red": {"fingerprint": "...", "command": "...", "output_hash": "..."},
  "changed_paths": ["..."],
  "chosen_approach": "...",
  "patch_intent": "...",
  "receipts": [{"id": "...", "status": "GREEN", "epoch": 12}],
  "avoid_repeat": [{"action_hash": "...", "observation_hash": "..."}],
  "next_boundary": "CREATE_REQUIRED_ARTIFACTS",
  "action_budget": 4
}
```

The state must preserve decisions and causally relevant evidence across
compaction:

- exact latest failure command and fingerprint;
- the chosen approach and patch intent;
- changed paths and workspace epoch;
- unmet predicate IDs;
- executable receipt hashes and their epochs;
- an avoid-repeat set;
- the single next decision boundary.

Do not preserve arbitrary old tool output merely because it is recent. Do not
discard the original task and system prefix. A stable prefix plus a small,
schema-stable suffix is the target context shape.

## Lifecycle and phase ownership

Only GT owns lifecycle transitions. Mini-SWE owns selecting the next legal
action inside the current phase.

| Phase | Entry condition | Allowed model work | Exit condition |
|---|---|---|---|
| `ORIENT` | Task starts or repository materializes | Exact inspection and targeted search | Contract compiled and orientation receipt delivered |
| `IMPLEMENT` | At least one unmet predicate is actionable | Edit, create, install, generate, or run a targeted diagnostic | Workspace epoch changes or a typed RED observation arrives |
| `VERIFY` | A relevant edit or candidate artifact exists | Execute named predicate checks and minimal diagnostics | Fresh GREEN/RED/UNKNOWN receipts for affected predicates |
| `SUBMIT` | All required predicates have fresh GREEN receipts | One submit request or final check | Accepted `FINISHED`, exact refusal, or `STUCK` |
| `FINISHED` | Submit accepted | One tool-free final response | Episode terminates |
| `STUCK` | No legal progress or bounded recovery exhausted | One tool-free explanation | Episode terminates |

The base Mini-SWE prompt must not say “keep working while iterations remain.”
The iteration limit is a safety bound, not an objective. Once GT accepts
submission, no further tool action is legal.

## Task-mode contracts

The current GT ontology is too code-edit-centric for Terminal-Bench. Contract
compilation must classify the task before choosing evidence:

| Mode | Examples | First-class predicates |
|---|---|---|
| `PATCH` | Fix an existing function | Changed diff, signature, syntax, focused tests |
| `BUILD_INSTALL` | Cython extension/import task | Package installed, clean-cwd import, extension load, required tests |
| `ARTIFACT` | Batching outputs | Exact file paths, schema, row/content counts, downstream readability |
| `SERVICE` | Start or repair a service | Process/listener, health endpoint, protocol behavior |
| `DATA_TRANSFORM` | Reshard/sanitize | Output inventory, invariants, counts, reproducibility |
| `MIXED` | Code plus generated state | All relevant predicates, grouped by phase and dependency |

Graph evidence is retrieval support. It is not proof of an artifact, package,
service, or test result.

## Context engineering rules

GT must improve the model's decision signal per token.

### Provider view

Each request gets at most one GT suffix containing:

1. current phase;
2. one or two highest-priority unmet predicates;
3. one exact relevant graph capsule, if confidence clears the margin;
4. latest RED or receipt change;
5. one permitted next action class;
6. remaining phase/action budget.

No standing full graph projection. No repeated identical capsule unless the
workspace or predicate epoch changed. No generic research dump.

### Retrieval

Rank exact failing paths, rare identifiers, call relationships, and current
predicate terms above common lexical overlap. Penalize generic anchors and
cross-obligation fan-out. Abstain when the margin is low. Record ranked,
admitted, suppressed, and rendered counts so overproduction is measurable.

### Compaction

Compact only at safe boundaries. Preserve semantic state listed above and the
stable task prefix. Keep the latest RED, the changed-file summary, and the
next decision. Never compact away the exact command that produced a failure or
the receipt that made a predicate GREEN.

### Synthetic messages

Prefer a provider suffix over repeated user-role messages. If a control must be
visible in the canonical Mini-SWE history, append exactly one typed control
event and link it to the request that consumed it. Never use a text message as
the only enforcement mechanism.

## Deterministic verification

Every contract predicate compiles to a minimal check where possible:

```json
{
  "predicate_id": "artifact.plan_b1",
  "command": "test -s /app/task_file/output_data/plan_b1.jsonl",
  "command_hash": "...",
  "exit_code": 0,
  "output_hash": "...",
  "workspace_epoch": 14,
  "status": "GREEN"
}
```

Rules:

- A compound shell command does not automatically prove every clause.
- A successful `grep`, `echo`, or directory listing is not semantic GREEN.
- File existence alone is insufficient when schema or content is required.
- `UNKNOWN` is explicit and triggers the exact check; it is not silently GREEN.
- Any edit touching a predicate's dependency invalidates its receipt.
- A receipt is joined to the provider request and action that produced it.
- `SUBMIT` is accepted only from fresh GREEN receipts covering all required
  predicates.

## Loop control and recovery

Define a no-progress fingerprint:

```text
(phase, tool_kind, normalized_command, affected_paths,
 observation_hash, workspace_diff_hash, unmet_predicate_set)
```

On the first repeat, expose the changed fact. On the second exact repeat,
reject that exact action and name the legal alternatives. After the bounded
recovery budget, transition to `STUCK`. Do not reject a new diagnostic merely
because it uses the same tool.

Action budgets are per phase and reset only when the relevant state epoch
changes. Controls are never deduplicated solely by mode; they are keyed by
phase, predicate set, workspace epoch, and RED fingerprint.

Shell handling must classify:

- command timeout;
- process failure;
- malformed tool call;
- persistent shell lifecycle failure;
- successful command with semantically negative result.

Each must have a bounded recovery path and an auditable receipt. Harbor timeout
is a failed run condition, not merely an agent stop reason.

## GT feature mapping in the Mini-SWE port

The 17 live-audit identities remain available, but they are triggered by real
Mini-SWE boundaries:

| GT identity | Mini-SWE port trigger | Required effect |
|---|---|---|
| `obligations` | `start_task` | Compile typed task contract |
| `localization` / `GT_LOC_RESLOT` | ORIENT or first relevant search | Rank exact relevant surfaces |
| `caller_contract` | targeted view/edit | Provide caller/callee contract evidence |
| `def_partition` | search with ambiguous references | Separate definitions from uses |
| `newfile_precedent` / `GT_CHANGE_SURFACE` | artifact/new-file task | Show repository precedent only when eligible |
| `signature_delta` / `GT_PATCH_DELTA` | post-edit | Report callable contract drift |
| `syntax_result` | VERIFY | Record isolated syntax/compiler receipt |
| `covering_red` | VERIFY after test failure | Map regression to changed surface |
| `recovery` / `GT_HYPOTHESIS` | repeated RED or no-progress repeat | Give one bounded alternative |
| `submit_refusal` / `GT_SS_SUBMIT_RED` | SUBMIT | Refuse with exact unmet predicates |
| `GT_EDIT_CHECK` | pre/post-edit | Execute deterministic edit checks |
| `GT_CERT_DELIVERY` | accepted SUBMIT | Deliver completion certificate |

Historical pre-edit and post-edit are lifecycle boundaries, not extra model
reasoning features. Research is a bounded ORIENT/diagnostic operation, not a
permission to browse indefinitely.

## Attribution contract

For every GT delivery, the port must retain:

1. `feature_id` and trigger ID;
2. lifecycle phase and workspace/predicate epoch;
3. exact sealed bytes and hash;
4. provider request ID and iteration;
5. linked model response/action;
6. next observation and receipt, if any;
7. action-consistency classification;
8. final task outcome.

The authoritative witness is the provider-bound payload, not a trajectory
substring. Provider payload blocks must be parsed structurally; substring tests
are invalid because tool messages are block lists.

The audit must distinguish:

- delivered and provider-confirmed;
- delivered but not consumed;
- consumed but action-inconsistent;
- eligible but trigger absent;
- ineligible by task contract;
- blocked before execution;
- forbidden access actually executed.

## Migration phases

### Phase 0: Freeze the comparison

- Keep the existing GT-off run as the frozen comparison.
- Record model, temperature, profile, tasks, concurrency, resources, and timeout.
- Do not claim superiority from a single temperature-1 smoke.
- Preserve all previous GT-on runs as diagnostics, not as interchangeable trials.

### Phase 1: Build the Mini-SWE adapter

- Add an adapter boundary around Mini-SWE's provider request and independent
  subprocess execution.
- Keep the canonical linear history intact.
- Move GT state to an external per-run directory.
- Add provider-bound delivery IDs and request receipts.
- Add contract compilation and task-mode classification.

Exit gate: Mini-SWE without GT behavior is unchanged; adapter tests prove no
state leakage and byte-accurate request attribution.

### Phase 2: Make GT state authoritative

- Implement the phase machine and explicit submit action.
- Remove generic successful-tool verification for GT-enabled tasks.
- Reserve one final tool-free response.
- Add fresh receipt invalidation and epoch-scoped control deduplication.

Exit gate: synthetic episodes cannot reach `FINISHED` without complete fresh
predicate coverage and cannot continue tool calls after accepted submission.

### Phase 3: Context engineering

- Replace standing graph context with decision-scoped retrieval.
- Implement semantic checkpoint state and stable-prefix compaction.
- Add exact no-progress fingerprints and bounded phase budgets.
- Add artifact/build/service/data-transform contract adapters.

Exit gate: replay shows no repeated identical capsule within an unchanged
epoch, and every rejected action has an allowed escape path.

### Phase 4: Feature and attribution audit

- Exercise all 17 identities through real Mini-SWE opportunities.
- Prove task-start localization timing for eligible tasks.
- Prove pre-edit/post-edit ordering around actual tool dispatch.
- Prove provider payload attribution and response/action linkage.
- Prove graph surface receipts and verification-plan receipts.

Exit gate: complete census, no dark eligible feature, no orphan delivery, no
unreconciled provider join, no forbidden harness access execution, and all
required lifecycle stages observed where eligible.

### Phase 5: Local proof

Run unit, integration, adapter, contract, replay, and audit suites. Required
checks:

- full pytest;
- all 17 feature tests;
- provider normalization tests;
- Mini-SWE linear-history invariants;
- external GT-state/isolation tests;
- receipt invalidation tests;
- submit/finish/stuck state-machine tests;
- no-progress and timeout recovery tests;
- immutable replay and provider-bound attribution audit;
- Ruff and workflow YAML validation.

### Phase 6: Live proof

Run the real five-task Mini-SWE plus GT smoke with:

- DeepSeek V4 Flash only;
- temperature 1;
- profile 2;
- concurrency exactly 5;
- the frozen GT-off baseline for comparison;
- no task or trajectory pre-modeling;
- full artifacts retained on local disk or Downloads.

## Acceptance metrics

The live run is accepted only if all four classes pass.

### Correctness

- reward is non-worse than frozen baseline on the comparable task set;
- no new Harbor timeout or harness error;
- no forbidden harness-state execution;
- every passing task has complete predicate coverage or an explicitly audited
  task-specific exception.

### Efficiency

- total iterations no higher than baseline after accounting for task count;
- total output tokens no higher than baseline;
- input reduction is not accepted as an efficiency win if output or iterations
  rise enough to erase it;
- graph bytes and GT suffix bytes are reported separately;
- cache-read tokens are reported, never assumed.

### Timeliness and causality

For each eligible delivery:

- provider request is the immediate witness;
- lifecycle stage is correct;
- the following action is legal and classified;
- a workspace, receipt, or explicit abstention records the effect;
- controls react on the next relevant action;
- accepted submission causes immediate tool termination.

### Stability

Because temperature is 1, one smoke proves wiring and a candidate result, not
stable superiority. After the implementation gate passes, run repeated
GT-on trials under the same configuration. Report mean, variance, per-task
paired outcomes, timeout rate, token distributions, and confidence intervals.

## Required per-task report

Never report only cumulative totals. Emit one row per task containing:

```text
task, reward, stop_reason, iterations, input_tokens, output_tokens,
cache_read_tokens, tool_errors, timeouts, gt_suffix_tokens,
graph_ranked/admitted/rendered, controls_delivered/rejected,
features_witnessed/exercised, predicates_green/required,
step0_localization, submit_decision, forbidden_access_executed
```

The report must include a baseline row for each comparable task and a delta
row for GT-on. A task that passes with missing GT receipts is a task success,
not proof that GT verified it.

## What must not be repeated

- Do not treat trajectories as the delivery witness.
- Do not search payload messages with naive substrings.
- Do not call input-token reduction a win when output and iterations grow.
- Do not render the full graph at every request.
- Do not let a control remain advisory when its predicate is high-confidence.
- Do not block broad classes of useful actions without an escape hatch.
- Do not deduplicate controls across changed workspace epochs.
- Do not permit edits after accepted submission.
- Do not let a generic end-turn heuristic compete with GT's finish state.
- Do not claim Muse Spark used Mini-SWE without a primary source.
- Do not replace nano merely because Mini-SWE appears on a leaderboard.

## Definition of done

The port is complete when:

1. Mini-SWE's minimal action loop remains independently reproducible.
2. GT has one authoritative lifecycle and finish/stuck controller.
3. Every task obligation is typed and executable where feasible.
4. Graph evidence is JIT, ranked, bounded, and linked to the current decision.
5. Compaction preserves semantic decisions and failure continuity.
6. Pre-edit, post-edit, research, test, verify, and submit triggers are real
   Mini-SWE boundaries with provider-bound attribution.
7. GT state is isolated from the graded workspace.
8. No-progress and timeout recovery terminate boundedly.
9. The five-task smoke passes the complete audit.
10. Repeated GT-on trials demonstrate non-worse correctness and lower total
    cost than the frozen baseline.

Until those conditions hold, GT is a promising deterministic engine under
repair—not an efficiency improvement that has been proven.
