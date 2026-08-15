# Mini-SWE + Groundtruth implementation plan

Status: research-backed plan only. This document does not claim that Mini-SWE + GT
has been implemented or that GT improves benchmark performance.

Source design contract: `portmini.md` at commit `f3b5b5a`.

## 1. Decision and current gap

The port must be an adapter around a pinned upstream Mini-SWE-Agent, not a rewrite
of the existing nano bridge and not a prompt-only feature layer.

The current checkout contains `eval/swe_agent.py`, `eval/tb_agent.py`,
`gt_engine/bridge.py`, and the existing GT engine, but no upstream
`minisweagent` runtime. The existing GT-off runs are therefore diagnostic only;
they are not a valid Mini-SWE GT-off baseline.

The first deliverable is a reproducible stock Mini-SWE baseline. No superiority
claim is allowed before that baseline is frozen.

## 2. Evidence used

The upstream Mini-SWE runtime is deliberately small: a bash-only agent with a
linear message history and independent subprocess execution. Its public seams
are the agent loop (`run`, `query`, `execute_actions`), local environment
`execute`, and model request preparation. These are the correct integration
points for a deterministic controller.

- [Mini-SWE-Agent repository](https://github.com/SWE-agent/mini-swe-agent)
- [Default agent implementation](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/agents/default.py)
- [Local subprocess environment](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/environments/local.py)
- [LiteLLM provider adapter](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/litellm_model.py)
- [Mini-SWE CLI entry point](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/run/mini.py)
- [Terminal-Bench 2.0 run protocol](https://www.tbench.ai/docs/run-terminal-bench-2-0)
- [Terminal-Bench 2.0 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
- [Meta Muse Spark announcement](https://ai.meta.com/blog/introducing-muse-spark-meta-model-api/)

The leaderboard demonstrates that Mini-SWE is not automatically the strongest
harness. Muse Spark's announcement discusses coding and harness support, but does
not establish that its Terminal-Bench result used Mini-SWE. Those facts constrain
the experiment; they do not predict its outcome.

## 3. Target architecture

```text
task + workspace
    -> deterministic contract compiler
    -> external GT event/receipt log
    -> GroundtruthController
       ORIENT -> IMPLEMENT -> VERIFY -> SUBMIT
          |          |          |          |
       provider suffix -> Mini-SWE model -> bash -> observation
          |                                   |
          +------ provider/action/receipt/grader join ------+
    -> FINISHED (one tool-free response) or STUCK (bounded explanation)
```

There are exactly two durable authorities:

1. Mini-SWE's linear conversation/tool observations.
2. GT's typed contract, state, predicate, event, and receipt log.

The provider view is derived state, never a third history. GT state must live
outside the task workspace; `.gt` files must not be visible to the model or grader.

Implement this adapter API first:

```python
class GroundtruthController:
    def start_task(self, task, workspace) -> TaskContract: ...
    def before_action(self, action) -> ActionDecision: ...
    def after_observation(self, action, result) -> State: ...
    def provider_suffix(self) -> ProviderSuffix: ...
    def submit_decision(self) -> SubmitDecision: ...
    def final_state(self) -> FinalState: ...
```

Use composition or a thin subclass of upstream Mini-SWE. Keep stock Mini-SWE
behavior available behind a GT-off flag and preserve its independent subprocess
semantics.

## 4. Workstreams

### W0 — Freeze identity and the genuine baseline

1. Pin the Mini-SWE repository version and commit; record Python/package/model
   versions and the exact Harbor image.
2. Select the five Terminal-Bench 2.0 smoke task IDs from `portmini.md`.
3. Fix model ID, temperature=1, profile=2, concurrency=5, timeout and resource
   settings. Do not change them between GT-off and GT-on.
4. Run stock Mini-SWE with no GT imports, no GT messages, and no GT files in the
   workspace.
5. Freeze per-task trajectory, grader result, tool/timeout errors, token usage,
   iterations, and a manifest hash. This is the only comparison baseline.

Exit gate: all five tasks have complete artifacts, or an explicitly documented
infrastructure failure; baseline identity is immutable.

### W1 — Adapter and isolation

1. Add a locked Mini-SWE integration package and an importable Harbor agent.
2. Wrap the agent's `query`/`execute_actions` boundaries and the local
   environment's `execute` without changing stock action syntax.
3. Create an external run directory keyed by run/task/request/iteration IDs.
4. Add path assertions preventing GT state, receipts, or graph data from entering
   `/testbed` or any grader-visible path.
5. Add an adapter no-op mode and replay a stock trajectory; actions, observations,
   termination, and messages must be byte-equivalent apart from external logs.

Exit gate: GT-off adapter equals stock Mini-SWE and isolation tests pass.

### W2 — Typed contracts and predicates

Extend the contract compiler with deterministic task modes:

`PATCH`, `BUILD_INSTALL`, `ARTIFACT`, `SERVICE`, `DATA_TRANSFORM`, and `MIXED`.

Each predicate must specify an ID, command or checker, semantic validation,
dependencies, workspace epoch, receipt schema, and freshness rule. A successful
shell exit, grep, listing, or file existence is not semantic GREEN by itself.
Edits invalidate dependent receipts. UNKNOWN is a first-class result.

Examples:

- PATCH: diff scope, syntax, focused tests, intended signature/behavior.
- BUILD_INSTALL: build result, clean-cwd import, extension/load check, tests.
- ARTIFACT: required path, schema, count, readability, and content constraints.
- SERVICE: process, listener, health endpoint, and protocol response.
- DATA_TRANSFORM: input inventory, output invariants, counts, and reproducibility.

MIXED tasks use grouped dependency predicates; graph support is never proof.

### W3 — Authoritative lifecycle controller

Implement explicit transitions:

`ORIENT -> IMPLEMENT -> VERIFY -> SUBMIT -> FINISHED | STUCK`.

GT owns transitions and Mini-SWE selects the next legal action. ORIENT compiles
obligations and localizes the relevant surface. IMPLEMENT permits edits/builds and
diagnostics. VERIFY runs fresh predicates. SUBMIT accepts only a complete fresh
GREEN set. FINISHED emits one tool-free final response; STUCK emits one bounded
explanation and stops tools.

Reject continued tools after acceptance, synthetic FINISHED events, and submit
decisions based on stale receipts. Submit refusal must name the unmet predicate
and permit one bounded recovery path.

### W4 — Provider-bound delivery and attribution

Capture the exact normalized provider payload at the final provider boundary, not
by grepping conversation text. Every delivery receives a request UUID, iteration,
payload hash, phase, suffix reason, and admitted predicate IDs. Join it to the
provider response, parsed action, workspace observation, receipt, and grader result.

The suffix is at most one compact control view: current phase, top one or two
predicates, one relevant graph capsule, latest RED/receipt change, one legal next
action class, and remaining budget. Do not emit an unchanged suffix unless the
epoch or predicate set changed. Synthetic messages are forbidden except one typed
control event linked to the request.

Attribution status must distinguish `confirmed`, `not_consumed`, `inconsistent`,
`absent`, `ineligible`, `blocked`, and `forbidden_execution`.

### W5 — Context engineering and bounded recovery

Maintain semantic state rather than replaying a standing graph:

`phase, workspace_epoch, contract_epoch, unmet_predicates, latest_red,
changed_paths, chosen_approach, patch_intent, receipts, avoid_repeat,
next_boundary, action_budget`.

Retrieve by exact failing paths/IDs/call relationships/current predicate terms;
abstain on low confidence. Compact only at safe lifecycle boundaries and retain a
stable prefix plus a small suffix. Track ranked, admitted, suppressed, and
rendered context bytes.

Fingerprint each action/observation as:

`(phase, tool_kind, normalized_command, affected_paths, observation_hash,
workspace_diff_hash, unmet_predicate_set)`.

On the first repeat, expose a changed fact. On the second exact repeat, reject it
and name alternatives. Apply per-phase budgets and bounded recovery, then STUCK.
Classify timeout, process failure, malformed tool call, shell lifecycle failure,
and semantic negative separately.

### W6 — Implement and audit all 17 features

Use the canonical names in `gt_features.md` and record opportunity, trigger,
delivery, consumption, action consistency, and outcome for every task. The mapping
must include the twelve identities explicitly listed in `portmini.md`:

| Feature | Mini-SWE boundary |
|---|---|
| obligations | `start_task` contract compile |
| localization / `GT_LOC_RESLOT` | ORIENT first search |
| caller_contract | targeted view/edit |
| def_partition | ambiguous localization |
| newfile_precedent / `GT_CHANGE_SURFACE` | artifact/new-file predicate |
| signature_delta | post-edit delta |
| syntax_result | VERIFY |
| covering_red | VERIFY after failure |
| recovery / `GT_HYPOTHESIS` | repeated RED/no-progress |
| submit_refusal / `GT_SS_SUBMIT_RED` | SUBMIT |
| `GT_EDIT_CHECK` | pre/post-edit |
| `GT_CERT_DELIVERY` | accepted submit |

The apparent twelve-row versus seventeen-identity difference is intentional:
five rows pair a semantic identity with its byte-owner alias (for example
`localization` + `GT_LOC_RESLOT`). Before W1/W2 implementation, reconcile the
authoritative 17-name list from `gt_features.md` with these 12 trigger rows and
version the resulting manifest. Do not invent names from historical toggles and
do not count lifecycle labels as features without an identity. No feature may be
inferred from a log substring. An eligible feature with no terminal status is an
audit failure.

### W7 — Local proof and replay

Add unit, integration, contract, state-machine, provider-normalization,
receipt-invalidation, isolation, timeout, no-progress, recovery, replay, and
attribution tests. Replay recorded Mini-SWE trajectories with a fake provider and
assert deterministic transitions and exact joins. Run the full repository test
suite, lint, and workflow/config validation.

Exit gate: no dark eligible feature, orphan provider delivery, stale GREEN submit,
duplicate unchanged suffix, forbidden GT access, or post-FINISHED tool call.

### W8 — Live proof

Run the frozen five-task smoke through Harbor using the official custom-agent
import path and exact concurrency=5. Retain raw trajectories, normalized provider
payloads, event/receipt logs, workspace diffs, grader output, and the manifest.
Run GT-off and GT-on as separate immutable runs; do not mix records or tune from
the GT-on result.

Then repeat GT-on enough times to estimate variance (minimum three complete trials
if cost permits). A single smoke establishes wiring; repeated paired trials are
needed for an improvement claim.

## 5. Required metrics and decision rule

Emit one row per task with:

`task, reward, stop_reason, iterations, input_tokens, output_tokens,
cache_read_tokens, tool_errors, timeouts, gt_suffix_tokens, graph_ranked,
graph_admitted, graph_rendered, controls_delivered, controls_rejected,
features_witnessed, features_exercised, predicates_green, predicates_required,
step0_localization, submit_decision, forbidden_access_executed`.

Join each row to the frozen baseline and report absolute and percentage deltas.
Report mean, median, variance, and confidence intervals across repeated trials.

GT-on is accepted only if correctness is non-worse than the genuine Mini-SWE
baseline, there are no new timeout/harness/forbidden-access faults, all required
predicates are satisfied on accepted tasks, and iterations and total model output
tokens do not increase. “GT delivered a message” is not evidence of causality;
the provider-to-action-to-receipt-to-grader chain must be complete.

## 6. Stop conditions and unresolved decisions

Stop and report rather than patching during a live proof if any of these occur:

- baseline is not genuinely stock Mini-SWE or its identity is incomplete;
- provider payload cannot be captured at the normalized request boundary;
- GT state leaks into the task workspace or grader-visible artifacts;
- a feature cannot be assigned a real lifecycle opportunity and terminal status;
- a submit is accepted from stale/UNKNOWN predicates;
- Mini-SWE continues tools after FINISHED or repeats unchanged actions past budget;
- benchmark correctness falls or cost/iterations rise without a pre-registered
  explanation and separate experiment.

Before implementation, resolve and record: exact upstream commit, exact DeepSeek
model identifier/provider settings, whether provider response IDs are available,
the five task IDs, and the Harbor image/resource defaults. If provider IDs are
missing, use an internal request UUID plus exact payload hash; never pretend a
conversation substring is provider attribution.

## 7. Completion definition

The port is complete only when stock Mini-SWE remains independently reproducible,
GT is the authoritative lifecycle controller, typed predicates and fresh receipts
govern submission, context is JIT/compacted, state is isolated, recovery is
bounded, all 17 features have auditable real triggers, and the five-task GT-on
proof contains a complete causal audit and baseline deltas. Until then, GT is an
integration candidate, not a demonstrated performance engine.
