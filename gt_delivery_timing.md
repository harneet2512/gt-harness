# GroundTruth deterministic SDLC: delivery-timing diagnosis

Status: measured design defect and implementation contract  
Repository: `gt-harness` (`nano + GroundTruth`, not mini-swe)  
Primary live artifacts: runs `30601595795` and `30603315821`  
Frozen comparison: existing GT-off observations only; do not dispatch GT-off

## Executive finding

GroundTruth's premise is valid only when deterministic evidence replaces model
work. Deterministic bytes are not automatically efficient. Evidence delivered
after the corresponding decision, evidence that is too broad, or evidence that
does not constrain the next action adds context and can induce more search.

The current implementation proves transport and attribution, but does not yet
prove the intended deterministic SDLC:

1. the graph projection is built and ranked at task start, but the only
   canonical task-start delivery is `obligations`;
2. canonical `localization` waits for a later search-result trigger;
3. context compaction retains recent turns rather than authoritative semantic
   work state;
4. progress control knows iteration state but not remaining wall-clock budget;
5. verification frequently acts as a late submit refusal rather than an early
   executable contract; and
6. feature census counts wiring, not whether evidence arrived before the
   decision and avoided work.

The required efficiency relation is:

```text
avoided search + avoided wrong work + avoided verification loops
>
GT payload + GT-induced work
```

No superiority claim is valid until that relation holds at non-worse reward.

## What the two latest live runs show

### Feature census

Global witnessed identities did not fall:

| Metric | Run `30601595795` | Run `30603315821` | Interpretation |
|---|---:|---:|---|
| unique witnessed identities | 9 | 9 | unchanged |
| unique exercised identities | 16 | 15 | `def_partition` did not trigger |
| action-consistent identities | 8 | 8 | unchanged |
| sealed deliveries | 84 | 21 | intentional recovery deduplication |
| unexposed deliveries | 2 | 0 | fixed |
| forbidden harness-path attempts | 1 false positive | 0 | fixed |

Per-task witnessed identities:

| Task | Old | New | Material change |
|---|---:|---:|---|
| build-cython-ext | 5 | 4 | localization no longer delivered |
| headless-terminal | 6 | 8 | gained submit certificate/refusal |
| llm-inference-batching-scheduler | 7 | 7 | unchanged |
| reshard-c4-data | 6 | 6 | different trigger mix |
| sanitize-git-repo | 6 | 6 | unchanged |

The delivery-count reduction is mostly correct: per-signature progress
interventions were replaced by a maximum of two per task. The loss of build
localization and the missing `def_partition` exercise are separate timing or
eligibility findings, not consequences of deduplication.

### Canonical localization timing

`task_start()` extracts obligations, builds the graph projection, reranks graph
evidence, and seals event `0` as `obligations`. It does not seal a localization
fact. Ranked localization is produced by a later gateway search-result event.

Provider-confirmed timing in run `30603315821`:

| Task | First canonical localization | Exact evidence |
|---|---:|---|
| build-cython-ext | never | graph unavailable/cached-empty or subject mismatch |
| headless-terminal | provider iteration 2 | `base_terminal.py:4:BaseTerminal` |
| batching-scheduler | provider iteration 2 | `cost_model.py:align`, `baseline_packer.py:load_requests` |
| reshard-c4-data | provider iteration 29 | `decompress.py`, `compress.py` |
| sanitize-git-repo | never | irrelevant `tools/eval_expdb.py` candidate was suppressed |

The deterministic context checkpoint can render decision-linked graph lines,
but those bytes are not the canonical localization delivery and cannot be
counted as proof that the localization feature reached the model at step 0.
This dual path is both confusing and operationally weak.

### Wall-clock result

Five-way concurrency was correct. Four trials became faster or remained close;
one straggler dominated the workflow:

| Task | Old agent time | New agent time | Change |
|---|---:|---:|---|
| build | 8m45s | 7m58s | faster |
| headless | 11m27s | 7m42s | faster |
| batching | 13m19s | 30m00s | outer timeout |
| reshard | 10m01s | 11m14s | slightly slower |
| sanitizer | 13m11s | 2m22s | much faster |

At iteration 54, batching launched a model-authored `timeout 2400` sweep with
tool timeout 2500 seconds inside Harbor's 1800-second whole-agent budget. Nano
accepted the requested tool timeout because the inner tool runner has no
knowledge of the outer deadline. Harbor killed the trial at exactly 1800
seconds. The repository artifact passed all six verifier tests, but the trial
correctly lacked a clean terminal and verification-plan receipt.

### Frozen GT-off token comparison

`headless-terminal` has no valid completed frozen GT-off row. For the four
descriptively comparable tasks:

| Task | GT-off iter | GT-on iter | GT-off input | GT-on input | GT-off output | GT-on output |
|---|---:|---:|---:|---:|---:|---:|
| build | 82 | 100 | 2,680,318 | 905,998 | 21,094 | 28,558 |
| batching | 21 | 54 | 557,372 | 1,112,185 | 33,398 | 98,392 |
| reshard | 57 | 48 | 1,350,848 | 1,226,805 | 20,980 | 62,416 |
| sanitizer | 35 | 28 | 1,296,069 | 367,140 | 14,902 | 14,301 |
| **total** | **195** | **230** | **5,884,607** | **3,612,128** | **90,374** | **203,667** |

Aggregate GT-on changed input by -38.6%, output by +125.4%, and iterations by
+17.9%. GT-off solved 4/4. GT-on earned repository reward on 3/4, but only two
of those four were clean non-timeout passes.

Compared with the immediately preceding GT-on run, the new run used 40.9%
fewer iterations but 11.7% more input. Average input per iteration rose from
8,148 to 15,394 (+88.9%) because retaining up to eight recent turns restored
memory by spending substantially more request context. Recent transcript is
not equivalent to compact semantic state.

## Required deterministic SDLC timing

### Task start: before provider iteration 1

One compact orientation block must contain:

- complete issue-derived obligations;
- top ranked files and symbols with a short relevance claim;
- proven repository precedents when applicable;
- the initial executable verification contract; and
- explicit uncertainty when graph evidence is unavailable.

The block remains one dose, but attribution records independent receipts for
`obligations`, `localization`, and `GT_LOC_RESLOT` when their bytes are present.

### Research: immediately after a view or search

Deliver only novel, decision-relevant facts:

- definitions separated from references;
- verified callers and contracts;
- relation or value-flow facts connected to the active target;
- repository precedent for an intended new file; and
- a reslotted localization only when it improves on step-0 orientation.

### Pre-edit: before edit execution

The edit must not execute until deterministic state records:

- target path and symbol;
- current file preimage;
- affected callers/registries/siblings;
- new-file precedent when the target does not exist;
- relevant obligation IDs; and
- the verification commands invalidated by the proposed edit.

This checkpoint may stay model-quiet when there is no new actionable evidence,
but it must be auditable before the tool dispatch.

### Post-edit: before the next provider request

Record and, when actionable, deliver:

- actual before/after patch delta;
- signature changes;
- syntax/compiler result;
- affected callers and covering checks;
- graph refresh revision; and
- invalidated verification receipts.

### Test and recovery

Map an observed result to edited surfaces and obligations. A recovery
intervention must name:

- the falsified hypothesis or repeated no-gain action;
- the attributable RED;
- the smallest graph-backed discriminating action; and
- the remaining time/iteration affordability of that action.

Generic "try something different" text is insufficient.

### Verify and submit

Verification is an early contract, not merely a late refusal. Before submission
GT must have fresh executable receipts for the changed behavior. A refusal
must name the exact missing or failing receipt and must not repeat unchanged.

### Wall-clock boundary

The outer agent deadline must be propagated into nano. Before each tool call:

```text
allowed tool time =
min(model requested timeout, remaining agent time - finalization reserve)
```

When the reserve is reached, exploratory tools are rejected and the next
provider request receives a deterministic verify-and-finish state. GT must
never allow a command whose requested runtime exceeds the remaining trial.

## Typed context contract

Active provider context should preserve authoritative state, not a fixed number
of recent turns:

1. original task;
2. compact obligations and their verification states;
3. current ranked work surface;
4. decisions made and hypotheses ruled out;
5. changed files and concise patch intent;
6. latest attributable RED/GREEN receipts;
7. unresolved obligations;
8. current smallest useful next action; and
9. at most one or two raw complete tool turns needed to perform that action.

Obsolete searches, installation logs, repeated outputs, superseded plans, and
already-exposed GT capsules must not remain in the active request merely
because they are recent.

## Research-backed design choices

The implementation must not equate "deeper" with "more context." Current
coding-agent research supports a smaller, staged interface:

1. **Deterministic lifecycle, bounded autonomy.** Agentless demonstrated that a
   simple localization -> repair -> validation workflow can be both effective
   and inexpensive. PatchPilot extends this into reproduction, localization,
   generation, validation, and refinement. GT should make those lifecycle
   states explicit while leaving code-level reasoning to the model.
2. **Hybrid retrieval.** Anthropic's context-engineering guidance recommends a
   small high-signal context, lightweight identifiers up front, and
   just-in-time retrieval for details. Therefore task start should contain
   ranked paths/symbols/reasons, not whole files or a flattened repository.
3. **Exploration must not pollute solving context.** FastContext reports that
   separating repository exploration from the solver can improve resolution
   while reducing coding-agent tokens by up to 60%. GT is deterministic rather
   than a separate reasoning agent, but it can enforce the same boundary:
   graph/query internals stay outside provider history and only the ranked
   result enters working context.
4. **Localization quality needs fixed-budget ranking.** SWE-Explore evaluates
   ranked relevant code regions under a fixed line budget and finds exploration
   quality tracks downstream repair. GT should measure target recall/rank and
   byte budget, not merely whether any localization capsule was delivered.
5. **Graph retrieval must be narrow and structured.** RepoGraph found that
   directly flattening a larger two-hop graph was its worst variant. LocAgent
   uses hierarchical entities, explicit relation direction, and tree-formatted
   subgraphs. GT should select a small one-hop decision slice rooted at the
   active symbol, with relation type and direction, rather than dump graph
   surfaces.
6. **Editing needs source, not prose alone.** Controlled context experiments
   report that compressed edit-relevant source can match whole-file context at
   far lower token cost, while natural-language summaries alone lose important
   behavioral information. Typed GT state must preserve the current target
   source or exact diff, not replace it with a vague summary.
7. **Condensation must be explicit state transformation.** OpenHands represents
   conversation state separately and records exactly which events are forgotten
   during condensation. GT should retain the durable transcript for audit,
   construct a separate provider view, and receipt which complete tool groups
   were omitted.
8. **Budget awareness must be continuous.** Budget-Aware Tool-Use shows that
   simply granting more tool calls does not improve agents that lack remaining
   resource awareness. A lightweight tracker can change when an agent should
   deepen, pivot, or verify. GT must expose remaining iterations, seconds, and
   affordable tool time at every decision boundary.
9. **Extra standing context can be harmful.** An empirical study of repository
   context files found increased inference cost and, on average, reduced task
   success when unnecessary instructions encouraged broader exploration. GT
   must be correct-or-quiet and delta-based; permanent generic guidance is a
   regression.

These findings imply the following concrete architecture:

```text
durable event log (complete, audit-only)
        |
        +--> deterministic typed state
        |      obligations / decisions / edits / RED-GREEN / budget
        |
        +--> graph selector
               ranked task-start identifiers
               one-hop JIT decision slice
        |
        +--> provider view
               task + typed state + exact active source/diff
               + at most two complete recent tool groups
```

### Primary sources

- Agentless, *Demystifying LLM-based Software Engineering Agents*:
  https://arxiv.org/abs/2407.01489
- PatchPilot, *A Cost-Efficient Software Engineering Agent*:
  https://openreview.net/forum?id=ybODpT8ydV
- RepoGraph, *Enhancing AI Software Engineering with Repository-level Code
  Graph*: https://arxiv.org/abs/2410.14684
- LocAgent, *Graph-Guided LLM Agents for Code Localization*:
  https://arxiv.org/abs/2503.09089
- FastContext, *Training Efficient Repository Explorer for Coding Agents*:
  https://arxiv.org/abs/2606.14066
- SWE-Explore, *Benchmarking How Coding Agents Explore Repositories*:
  https://arxiv.org/abs/2606.07297
- *What Context Does a Coding Agent Actually Need to Act?*:
  https://arxiv.org/abs/2607.09691
- Anthropic, *Effective context engineering for AI agents*:
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenHands event and condensation architecture:
  https://docs.openhands.dev/sdk/arch/events
- *Budget-Aware Tool-Use Enables Effective Agent Scaling*:
  https://arxiv.org/abs/2511.17006
- *Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding
  Agents?*: https://arxiv.org/abs/2602.11988

## Observable invariants

For every eligible feature:

```text
trigger observed
→ producer completed
→ bytes or deterministic state applied
→ provider request receipt
→ immediate model response receipt
→ next action classified
```

Additional timing invariants:

- task-start localization is present in provider iteration 1 when ranked
  locations exist;
- pre-edit checkpoint precedes the edit tool execution;
- post-edit evidence is present no later than the next provider request;
- no delivery is sealed without a following provider budget;
- no requested tool timeout exceeds remaining wall-clock affordability;
- a fresh passing verification transitions toward termination rather than more
  exploration; and
- feature census distinguishes eligible, suppressed, delivered, exposed,
  action-consistent, helpful, and harmful.

## Implementation sequence

### Phase 1: executable timing contract

Files: `tests/test_gt_engine.py`, `tests/test_gt_attribution.py`,
`tests/test_gt_live_gate.py`.

RED witnesses:

- a graph-backed task-start call exposes obligations but not localization;
- a localization delivered after the first edit is classified as late;
- a pre-edit checkpoint recorded after tool execution fails ordering;
- a terminal delivery with no following provider request fails lifecycle;
- all 17 identities retain explicit eligible/suppressed/ineligible reasons.

Exit gate: tests fail for the timing defect, not fixtures or unavailable graph
setup.

### Phase 2: unified step-0 orientation

Files: `gt_engine/bridge.py`, `gt_engine/graph_context.py`,
`gt_engine/attribution.py`.

Implementation:

- select a maximum of 3--5 obligation-linked file/symbol identifiers;
- render path, line/symbol, relation/relevance claim, and intended action;
- append this bounded section to the task contract before provider iteration 1;
- keep one sealed byte block while recording independent obligations,
  localization, and location-reslot application receipts;
- explicitly record `graph_not_ready`, `no_ranked_target`, or role suppression
  instead of silently deferring;
- later search localization must be novel relative to the step-0 targets.

Exit gate: provider iteration 1 contains the exact ranked identifiers and its
immediate response is linked to the same delivery; no second dose is created.

### Phase 3: semantic provider view

Files: `gt_engine/context.py`, `gt_engine/bridge.py`,
`gt_engine/verification_contract.py`.

Implementation:

- make typed state authoritative: obligations, decisions, changed paths,
  patch intent/delta, latest RED/GREEN, verification freshness, budget;
- retain exact active source/diff references when available;
- default to two complete recent tool groups, not eight;
- add an older group only when it contains an active changed path, current RED,
  or unresolved verification evidence;
- mask large stale observations and receipt all omitted group IDs;
- enforce both a hard character budget and a smaller target budget so the
  greedy selector does not fill spare context without semantic value.

Exit gates:

- the six-turn regression fixture retains the semantically marked old turn but
  drops irrelevant recent output;
- active context remains below target except when exact pending delivery bytes
  require the hard-budget fallback;
- durable messages are byte-identical after provider-view construction.

### Phase 4: wall-clock-aware control

Files: `eval/tb_agent.py`, `nano/cli.py`, `nano/agent.py`, `nano/tools.py`,
`.github/workflows/tb2_gt.yml`.

Implementation:

- propagate the effective Harbor agent budget to nano as seconds;
- start a monotonic deadline inside `Agent.run`;
- reserve time for one final provider response and verification/cleanup;
- clamp each bash timeout to affordable remaining seconds;
- reject a call when no safe execution window remains;
- attach structured `budget_exhausted` recovery metadata;
- place a compact verify-and-finish state in the next provider request;
- never convert an outer-timeout risk into a false successful tool receipt.

Exit gates:

- a requested 2500-second command inside a 1800-second budget is deterministically
  clamped or rejected;
- a near-deadline command leaves enough reserve for a final response;
- GT-off remains unchanged unless the explicit agent-budget option is supplied;
- timeout recovery still kills the complete process group and restores shell
  state.

### Phase 5: lifecycle verification as progress

Files: `gt_engine/bridge.py`, `gt_engine/verification_contract.py`,
`gt_engine/progress.py`.

Implementation:

- compile the initial verification plan at task start;
- show only the minimal commands/predicates relevant to current obligations;
- invalidate receipts on related edits;
- classify a fresh mapped GREEN as progress toward termination;
- classify repeated unmapped checks as no-gain;
- make recovery name the current RED, invalidated obligation, and smallest
  affordable next check;
- suppress unchanged submit refusals.

Exit gate: a mapped GREEN followed by no repository mutation causes the next
state to recommend completion, not further exploration.

### Phase 6: audit and live gate

Files: `scripts/gt_audit.py`, `scripts/gt_live_gate.py`,
`scripts/gt_replay.py`.

Implementation:

- report produced, sealed, provider-exposed, response-linked and action-consistent
  iterations separately;
- add `decision_deadline_iteration` and `timing_status` per delivery;
- require task-start localization by provider iteration 1 when eligible;
- report late-but-witnessed as a timing failure, not success;
- report requested versus allowed tool timeout and remaining wall time;
- retain exact replay accounting without storing raw sensitive provider text.

Exit gate: replay of run `30603315821` deterministically identifies reshard
localization at iteration 29 and the batching unaffordable command.

### Phase 7: verification and live proof

Local order:

1. focused RED-to-GREEN tests;
2. bridge/agent/tool/audit integration tests;
3. `pytest -m gt_all17`;
4. full `tests/` suite;
5. scoped Ruff and `git diff --check`;
6. exact replay against both latest immutable artifacts.

Live order:

1. push one candidate commit;
2. dispatch only the GT-on five-task workflow;
3. use `deepseek-v4-flash`, temperature 1, Profile 2, timeout multiplier 1.0;
4. set concurrency to exactly 5;
5. audit provider requests, immediate responses, next actions, tool budgets,
   verifier results, and per-task economics;
6. compare with the frozen existing GT-off rows; and
7. do not claim stable superiority from one stochastic run.

## Acceptance gates

The next live candidate is accepted only when:

- all five tasks start in parallel with concurrency exactly 5;
- every task with ranked graph locations receives localization in provider
  iteration 1;
- all sealed deliveries are provider- and response-confirmed;
- all pre/post/test/verify/submit timing invariants pass;
- no task has a tool timeout larger than its remaining agent budget;
- no task ends in an agent timeout or unexposable terminal delivery;
- reward is non-worse than the frozen comparable baseline;
- input, output, iterations, and wall time are reported per task;
- token reduction does not hide a correctness regression; and
- stable superiority is claimed only after repeated GT-on trials because
  temperature 1 is stochastic.

## Candidate-result addendum: run 30606642296

The first implementation of this plan was tested live in run `30606642296`.
It proved the timing contract but failed the outcome contract:

- all eligible step-0 localization was provider-exposed and response-linked at
  iteration 1;
- all delivery, lifecycle, replay, budget, and isolation checks pass after
  correcting a parser false positive for explicit `.gt` exclusions;
- input tokens fell 67.7% on the four frozen-comparable tasks;
- reward fell from the frozen 4/4 to 2/4, iterations rose 77.9%, and output
  tokens rose 63.9%.

The trace shows that request compaction succeeded while deterministic control
was incomplete. Required output paths remained only in the durable task
contract, fresh GREEN did not produce a salient completion boundary, and the
ranker could favor generic cross-obligation overlap.

The follow-up therefore adds three just-in-time states:

1. `artifact_completion` at 50% when a contract-scoped required artifact is
   absent;
2. `verified_completion` after a fresh post-edit GREEN; and
3. `finalization` at 80% with remaining requests and the smallest unresolved
   requirement.

Each mode is issued at most once and receipted as
`progress.control_issued`. The checkpoint now carries exact missing artifact
paths, prioritized unresolved obligation text and predicate types, remaining
iterations, and the last concrete action. Artifact existence is never promoted
to semantic verification without an executable check.

## Enforceability addendum: run 30608738489

Run `30608738489` showed that correct-time delivery is necessary but not
sufficient. `artifact_completion` reached batching at iteration 50 and
`finalization` at 80, yet the model returned to unrelated source reads and
finished without the required artifacts. Six controls fired across the run,
but three tasks still reached iteration 100.

The same run exposed a separate hard failure: sanitizer executed real `.gt`
inspection commands. The graph database had been stored under the graded
repository, so prompt-only isolation was structurally false.

The revised timing contract now includes pre-dispatch enforceability:

- graph state lives under the external `GT_STATE_DIR`, not the task root;
- explicit harness-path access is rejected before execution;
- artifact-completion rejects unrelated repository observation until the
  required output paths exist;
- finalization rejects broad observation but permits edits, executable checks,
  and a targeted read named by the latest failure; and
- finalization prioritizes install/deploy and required-artifact end states
  before descriptive compatibility clauses; and
- the audit distinguishes rejected access from executed access using
  `tool.control_decision` receipts.

The next live gate must show zero executed harness accesses. A blocked attempt
is reported separately and does not count as an isolation violation.
