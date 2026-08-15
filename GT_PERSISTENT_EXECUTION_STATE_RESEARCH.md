# GroundTruth Persistent Execution State — Research Decision

## 1. Executive conclusion

GroundTruth should experiment with a graph-first persistent execution state, but it
must not become a second autonomous agent or an always-replanning system. The
implemented design first runs the accepted five-channel hybrid retriever after the
repository graph is ready, then uses one bounded model call over that graph-backed
catalog. The identical task-start retrieval result seeds the first live retrieval
cache, avoiding a second dense/ranking pass before executor call one. That call may
select and order only immutable catalog IDs. Every subsequent update is
deterministic and is driven by typed proposed actions, actual workspace transitions,
attributable validation results, and refreshed certified graph edges. The executor
sees a bounded current-state slice in every normal provider request; the full state
remains external to the conversation.

This is an implementation hypothesis, not an outcome claim. Provider-free tests can
prove timing, isolation, replayability, and accounting. Only a frozen matched
benchmark can prove more solves or better efficiency.

## 2. Current GroundTruth architecture

The active host is `eval.gt_central_agent.MiniSweCentralAgent`. It owns the Mini-SWE
loop, builds or refreshes repository intelligence, compiles provider-visible evidence,
normalizes each Bash action into `ProposedAction`, evaluates preflight, executes it,
and commits postflight results. The new state engine is
`gt_engine.persistent_execution_state.PersistentExecutionStateEngine`.

The new lifecycle is:

    repository transfer and complete GraphDB build
    -> bounded bootstrap catalog
    -> one model selection call (catalog IDs only; no action execution)
    -> persistent typed state
    -> state frame in executor call N
    -> typed preflight projection for action N
    -> normal host execution
    -> deterministic postflight state transition
    -> graph refresh/rebase after source changes
    -> refreshed state frame in executor call N+1

## 3. External systems reviewed

OpenAI's published ExecPlan guidance treats plans as self-contained living documents
whose progress, discoveries, and decisions are updated during implementation. That is
evidence for persistence, but it is a natural-language human/agent convention rather
than a deterministic runtime state machine. See the official
[OpenAI Agents Python PLANS.md](https://github.com/openai/openai-agents-python/blob/main/PLANS.md)
and [OpenAI Cookbook ExecPlans guidance](https://github.com/openai/openai-cookbook/blob/main/articles/codex_exec_plans.md).

The official [mini-SWE-agent repository](https://github.com/SWE-agent/mini-swe-agent)
documents a Bash-only agent with linear append-only history. This makes a host-owned,
same-request state contribution the smallest integration that preserves Mini-SWE's
executor and tool surface.

The official [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
documents pre-tool interception, post-tool observation, and `additionalContext` on the
next model request. It supports the general timing pattern, but GroundTruth uses its
existing in-process Mini-SWE boundary rather than adding hooks or MCP.

[LivePlan](https://arxiv.org/abs/2608.06701) separates deterministic monitoring from
conditional model advice. Its full evaluation reports that triggered monitoring is
safer and cheaper than periodic/global replanning. It also reports that inaccurate
global plans can regress previously solved tasks. GroundTruth therefore does not
replan periodically and does not let the bootstrap invent repository facts.

## 4. Normalized architecture matrix

| System | Persistent artifact | Update mechanism | Repeated model calls | Executor delivery |
| --- | --- | --- | --- | --- |
| OpenAI ExecPlans | Natural-language living document | Agent/human edits | Normal executor reasoning | Plan/document context |
| Claude Code hooks | External hook/session state is possible | Hook program plus model loop | No required planner call | Hook context at defined events |
| mini-SWE-agent | Linear conversation only | Append action/result messages | One call per executor step | Entire retained linear history |
| LivePlan | Process-centric trajectory monitor | Deterministic rules; advisor on trigger | Conditional advisor | Corrective next-step advice |
| GroundTruth state | Typed repository-semantic state | One bounded bootstrap, then deterministic transitions | Exactly one bootstrap; no replans | Bounded current frame in every executor request |

Undocumented proprietary internals are not used as evidence.

## 5. Repeated successful patterns

The transferable patterns are external persistence, explicit progress and validation
state, event-driven updates, bounded current context, and escalation only on a proven
need. The deterministic portion is strongest when it records observations and
mechanically certified graph relationships. It is weakest when it turns a relationship
into a mandatory repair step without proof.

## 6. Lessons from failed / harmful planning approaches

An initial plan can anchor the executor to a wrong hypothesis. Periodic replanning adds
cost and can introduce new hallucinations. Repeating a large plan in every prompt
pollutes context. Treating every caller or related test as a mandatory edit creates
false blockers. The implementation prevents these failures by catalog-bounding the
bootstrap, using small state frames, making graph-derived dependency items advisory,
and reserving blocking status for explicit task checks, deliverables, or a current
attributable failure.

## 7. LivePlan overlap analysis

LivePlan monitors behavioral trajectory structure: repeated actions, oscillation,
stagnation, and phase violations. GroundTruth already has separate progress controls
for some of those signals. The persistent state implemented here is not another
behavioral advisor. It records repository-semantic repair state: current graph-bound
focus, inspected and modified paths, explicit task obligations, certified related
paths, and validation state. It does not call an advisor when drift occurs.

## 8. GroundTruth differentiation

The narrow differentiation is a deterministic repository-semantic state plane tied to
the same source and graph revisions as execution-time evidence. Its contribution is
not “planning” by itself. It is the ability to keep certified dependencies,
requirements, and validation state current across an otherwise linear Bash trajectory,
then expose only the decision-relevant slice in the exact next provider request.

## 9. Four GT architecture options

| Option | Resolve potential | Regression risk | Cost | Determinism | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| A. Context only | 3/5 | 2/5 | 5/5 | 5/5 | Existing control |
| B. Fixed one-shot plan | 3/5 | 4/5 | 4/5 | 2/5 | Reject as static |
| C. LLM living plan | 3/5 | 5/5 | 1/5 | 1/5 | Reject |
| D. Hybrid persistent state | 4/5 | 2/5 | 3/5 | 4/5 | Experiment |

The scores are architectural risk judgments, not benchmark results.

## 10. Recommended architecture

Use Option D with a strict boundary: one generative selection over certified IDs,
followed by deterministic state transitions. Keep it additive to existing retrieval,
frontier, feature, preflight, postflight, completion, and progress paths. Do not let it
rewrite or suppress commands. Its first release is context-shaping and accounting only.

## 11. Proposed persistent-state schema

The implemented `PersistentExecutionState` contains task and state identity; source,
graph-source, and graph revisions; graph-current status; phase; bootstrap status;
initial selected IDs; current focus ID/path; inspected and modified files; typed
obligations; attributable validation state; a bounded current failure; and the last
transition. The receipt emits a field-level authority map with these classes:

* `IMMUTABLE_INPUT`: task digest.
* `DETERMINISTIC_DERIVED`: IDs and bootstrap status.
* `GENERATIVE_BOOTSTRAP`: only selected catalog IDs and ordering.
* `DETERMINISTIC_MUTABLE`: phase, revisions, focus state, and obligations.
* `EXECUTOR_OBSERVED`: files, validation, and failure state.

Raw Bash bodies, heredoc contents, and full outputs are not stored.

## 12. Deterministic state-transition specification

Successful reads record normalized inspected paths and satisfy matching advisory
inspection items. Model-authored changes record modified paths, invalidate validation,
and make graph state unavailable until refresh. A successful graph rebase replaces
the structural edge set, invalidates vanished non-task obligations, and opens advisory
items from newly certified adjacent edges. An attributable validation failure records
one bounded diagnostic and creates a critical frame. An attributable pass binds a
validation certificate to the current source revision. Unknown or stale evidence
causes no speculative mutation.

## 13. Creation timing

Creation occurs only after initial repository transfer, graph construction, schema and
coverage checks, and exact source/graph revision binding. A source-less task abstains.
An incomplete or stale graph does not create a state artifact or invoke the bootstrap
model call.

## 14. Context-delivery strategy

The full state stays in the host. Before every executor `model.query`, the engine emits
one complete bounded frame through the existing contribution compiler and normal tool
observation surface. It is not a new tool call and is not appended as durable duplicate
history. Initial/critical frames may use up to 512 deterministic packing tokens, delta
frames up to 256, and stable core refreshes up to 96.

## 15. Context-budget recommendation

Freeze the implemented ceilings for the first causal evaluation: bootstrap input 2,000
bytes conservatively bounded against tokens, bootstrap output 512 tokens, persistent
frame maximum 512 tokens, and core refresh maximum 96 tokens. Do not tune these after
seeing final benchmark tasks. Report bootstrap and executor tokens/calls separately and
together.

## 16. Determinism boundary

Repository transfer, graph construction, catalog construction, selection validation,
state transitions, graph rebase, context packing, timing, and receipts are
deterministic. The one bootstrap selection and the executor are model-driven. If the
selection is invalid or times out, deterministic fallback lets Mini-SWE continue, but
the treatment fails the benchmark release gate and cannot be called the intended arm.

## 17. Triggered-advisor policy

No triggered advisor is implemented. LivePlan provides evidence that conditional
advising can help behavioral drift, but this experiment must first isolate persistent
repository-semantic state. Adding an advisor now would confound the mechanism and add
unbounded calls.

## 18. Running example

For a service signature repair, the complete graph yields a service definition, a
caller, a related test, and an explicit task check. The bootstrap selects the service
focus once. The first executor request sees that focus and the required check. A read
records the service path. An edit advances source revision, marks validation pending,
and temporarily suppresses state delivery until the graph rebuild completes. Rebase
opens caller/test advisory items from current certified edges. The next request sees
the modified focus plus those related checks. A failing test creates a critical frame
with the exact bounded diagnostic. A passing declared check satisfies the explicit
validation requirement. No additional planner call occurs.

## 19. Failure-mode red team

Bad bootstrap selection is constrained to real IDs and remains observable. Stale graph
labels are not re-emitted after a graph revision changes. Missing dynamic-language
edges produce no obligation. Graph-derived relations are non-blocking. Source-less and
incomplete-graph tasks abstain. Context overload is bounded and fully metered. Provider
hashes and changed-message indices prove exact delivery. The largest remaining risk is
that even correct state framing distracts a capable executor or fails to offset the
one-call bootstrap cost.

## 20. Novelty audit

Deterministic trajectory monitoring is not novel because LivePlan directly establishes
it. Living natural-language plans are not novel. Graph-based code retrieval is not
novel. The potentially differentiating combination is repository-revision-bound,
graph-grounded persistent repair state updated from real execution events while the
model receives only a bounded current slice. Novelty confidence is moderate until a
broader prior-art review and empirical result establish more than an implementation
combination.

## 21. Minimal implementation surface

The implementation adds one state module, integrates it into the existing central
agent boundaries, extends provider/delivery/deep-metric accounting, adds release gates,
and enables it only in certified repository-context treatment arms. It adds no MCP,
sidecar, new executor tool, autonomous planner, rewrite, or command suppression.

## 22. Experiment design

First run provider-free integrity proof. Then use one frozen diagnostic set with the
same model, prompt, environment, tools, timeout, and executor budget. Compare current
GT context against current GT context plus persistent state. Count the bootstrap as a
real API call and include all its tokens, latency, and cost. Primary outcome is solved
tasks. Efficiency includes total and executor-only calls, actions, turns, tokens,
wall-clock time, time to first edit, repeated reads/searches, validation attempts, and
open blocking/advisory state at submission.

## 23. Kill criteria

Stop this direction if exact request proof fails, graph rebases serve stale state,
false blocking obligations occur, the state is absent on any graph-applicable executor
call, or the frozen diagnostic shows causally attributable losses without offsetting
gains. A provider-free pass alone is not success.

## 24. Exact next engineering step

The complete source-built Linux provider-free workflow passed at runtime commit
`e0c63ae15be6eeff9eae67ffe873f3b44e2da31f` (run `31647174958`). It built the current indexer, provisioned the pinned
Snowflake ONNX backend, ran the persistent-state kernel/integration/release tests,
printed `READY` and `SMOKE_APPROVED`, and uploaded a receipt with `provider_calls: 0`.
The exact next engineering step is a separately authorized frozen matched diagnostic;
no paid run is implied by the provider-free proof.

```text
RECOMMENDATION:
EXPERIMENT FIRST

ARTIFACT:
A graph-first typed persistent execution state with one catalog-bounded bootstrap selection.

CREATED:
After complete repository graph construction and before the first executor model call.

PERSISTED:
In the task-scoped host runtime for the complete Mini-SWE trajectory and final receipt.

UPDATED:
At every typed preflight, executed-action postflight, source revision change, validation result, and certified graph rebase.

MODEL CALLS:
One initial bounded bootstrap; zero repeated planner/advisor calls; normal executor calls only.

EXECUTOR CONTEXT:
One bounded current-state frame in every normal provider request for graph-applicable tasks.

DETERMINISTIC CORE:
Graph/catalog construction, validation, state transitions, rebasing, packing, delivery, and accounting.

NON-DETERMINISTIC BOUNDARY:
The one catalog-ID selection and the normal Mini-SWE executor.

LIVEPLAN DIFFERENCE:
LivePlan monitors process/behavioral drift and may call an advisor; GT maintains repository-semantic repair state from certified graph and execution evidence without repeated advising.

EXPECTED BENEFIT:
Retain current repository obligations and validation state across a linear trajectory while reducing rediscovery and missed coupled work.

BIGGEST RISK:
Correct but unnecessary persistent context plus one bootstrap call may cost more or distract more than it helps.

FIRST EXPERIMENT:
Provider-free exact-request proof, then one frozen matched diagnostic with all bootstrap overhead counted.

KILL CONDITION:
Any stale/false state delivery, attributable solve regression, or no outcome/efficiency signal after the frozen diagnostic.
```
