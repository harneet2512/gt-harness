# GT outcome-preservation implementation — 2026-08-06

## Executive result

The treatment run `31068690296` was not a 9/10 solve result. Its official
verifier reward was 9/10, but `write-compressor` was terminated by Harbor's
outer 900-second timeout. The comparable outcome was therefore:

| arm | official reward | uncensored resolved | outer-censored tasks |
| --- | ---: | ---: | --- |
| frozen GT-off baseline | 9/10 | 9/10 | `gpt2-codegolf` |
| GT-on diagnostic smoke | 9/10 | 8/10 | `gpt2-codegolf`, `write-compressor` |

The aggregate resource reduction in that run is not an efficiency win because
the treatment lost one uncensored solve and had positive per-task resource
deltas. The 89-task run remains blocked.

## Root causes fixed

### 1. Reward was being treated as an uncensored solve

`gt_engine/deep_metrics.py` now reports `official_solved`,
`uncensored_resolved`, and `solved` (the latter is the uncensored alias).
Harbor outer exceptions (`AgentTimeoutError`, `AgentAbortedError`, and related
trial failures) censor the solve even if a verifier reward was emitted. Clean
internal exhaustion and the engine's deadline-reserve exit remain distinct
from an outer cancellation and are retained as solver-exhausted witnesses.
`compare_arms()` reports official and uncensored outcome counts separately.

### 2. Harbor's task deadline did not reach the in-process loop

The workflow now exports each task's `task.toml`, resolves the exact
`agent.timeout_sec` with `scripts/resolve_harbor_budget.py`, records a SHA-256
budget receipt, and passes the value as `execution_budget_sec`. The central
agent starts its clock before setup, caps model and command waits against the
remaining budget, and reserves 15 seconds to return a normal result. A reserve
exit is `DeadlineReserveReached`, not a fabricated model timeout. No timeout or
resource limit is increased.

### 3. The agent had no executable completion boundary

Terminal-Bench's host workflow prose is removed before extracting the task
contract. `gt_engine/completion.py` compiles only mechanically equivalent
predicates. The current safe grammars cover exact decompression and bounded
artifact-size requirements; an uncovered obligation makes the plan partial and
disables auto-submit.

After a target transition or explicit validation at a new workspace revision:

1. each compiled predicate runs as a private host check;
2. observations are bound to the exact workspace revision and action ID;
3. a certificate is eligible only when every predicate is present, current, and
   passing;
4. the existing `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` marker is executed once;
5. any pre-decided suffix is cancelled and the loop terminates as `Submitted`.

Predicate checks and submit attempts are included in `effective_actions`, so
controller work cannot be hidden as a model-action reduction. A timeout,
ambiguity, stale revision, or failed predicate continues the model loop.

The archived `write-compressor` trajectory had already produced a valid
`OK` result and a 2,372-byte artifact at model action 32, then continued for
roughly eleven more calls/actions while optimizing the artifact and eventually
hit Harbor's outer timeout. The new controller path is provider-free tested to
check both real obligations and submit before another model call. This is a
counterfactual replay witness, not a paid causal claim.

### 4. Long-run repetition and context growth were uncontrolled

`gt_engine/progress.py` records deterministic controller state for three
identical observations, six alternating observations, and budget risk near 80%
of the step limit. It does not inject generic advice or block a command merely
because a threshold was crossed.

`gt_engine/provider_view.py` now has a non-aggressive deterministic transform:

- 70% of the 400,000-character envelope triggers compaction;
- 50% is the target;
- exact semantic duplicate turns are removed first (transport-local tool-call
  IDs are ignored, but return status and action metadata are retained);
- only older turns are compacted; the latest two turns remain verbatim;
- a bounded typed current-state frame carries current revisions, changes,
  validation, failures, requirements, and checks;
- below the threshold, the provider history remains unchanged apart from exact
  semantic duplicates;
- no LLM summarizes context, no unique reasoning is silently discarded, and
  the immutable audit history is preserved.

## GT lifecycle accounting

The 17 feature paths remain the same. Their receipts distinguish:

- `engine_internal_state`: deterministic producer work such as revision,
  validation debt, failure, lifecycle, or trigger updates;
- `existing_engine_actuation`: an existing controller consumer changed state;
- `provider_payload`: grounded evidence present in the first eligible provider
  request;
- `audit_only`: a receipt with no downstream consumer, never claimed as causal
  help;
- `unread_private_state`: a producer result not yet read by any downstream
  component.

Completion predicates are controller checks, not a new 18th GT feature and not
model acknowledgement. They consume the task contract and workspace state;
their checks, certificate, auto-submit, and cancelled suffix are separately
receipted.

## Verification performed

Provider-free census passed all required lines, including:

`ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
`ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`,
`ALL_17_CONSUMER_PATHS_PROVEN`, `ALL_17_TRIGGERS_PROVEN`,
`ALL_17_PAYLOADS_CONCRETE`, `ALL_17_CONSUMERS_APPLIED`,
`ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST`, `NO_ACTIONS_BLOCKED`, and
`ALL_EFFECTS_CONTEXT_ACCOUNTED`.

`python scripts/central_readiness_audit.py` reports `READY` after the workflow,
budget resolver, completion, progress, context, and vendored-runtime-surface
assertions. Focused tests cover outcome censoring, clean exhaustion,
task-contract noise removal, completion certificates, exact-once auto-submit,
deadline reserve, progress cycles, semantic deduplication, and thresholded
compaction.

The first full-suite run exposed eight failures because the local interpreter
had an older/incomplete `groundtruth-mcp` installation. Installing the pinned
`vendor/groundtruth_mcp-1.0.0-py3-none-any.whl` made all eight pass. The guard
now checks `terminal_evidence`, `deterministic_queries`, and
`miniswe_provider_boundary`, and both provider-free workflows install that
wheel. The final full repository run is green: all collected tests passed with
three platform skips (Unix file modes, symlinks, and the graph-covered smoke).
The finalstand receipt was regenerated after the corrected smoke manifest so
its machine validator passes.

Archived treatment trajectories replay through the repaired policy with
`REPLAY_OK` for all ten tasks. The corrected deep comparison still reports the
historical treatment limitation honestly: baseline uncensored 9/10 versus
treatment uncensored 8/10 because `write-compressor` was outer-censored. This
replay proves no new policy invariant or receipt corruption across prior task
trajectories; it does not retroactively turn the old paid run into a successful
outcome experiment.

## Research basis and design limits

The design follows evidence from SWE-agent's action-computer interface, which
shows that the host/action interface materially changes agent behavior; the
deterministic localization→repair→validation decomposition used by Agentless;
AST/search and fault-localization guidance from AutoCodeRover; and finite-state
recovery prompting from RepairAgent. OpenHands' documented stuck detector also
uses repeated-error and alternating-cycle thresholds, which motivates the
3/6 controller thresholds here. These sources support bounded deterministic
control surfaces; they do not prove that this implementation improves a
stochastic benchmark.

Research links:

- SWE-agent: https://arxiv.org/abs/2405.15793
- Agentless: https://arxiv.org/abs/2407.01489
- AutoCodeRover: https://arxiv.org/abs/2404.05427
- RepairAgent: https://arxiv.org/abs/2403.17134
- OpenHands stuck detector: https://docs.openhands.dev/sdk/guides/agent-stuck-detector

## Remaining gates / TODOs

1. Commit and push only after reviewing the complete diff; the live gate is
   intentionally fail-closed on a dirty or unpublished commit.
2. Run one separately authorized matched ten-task smoke with the paid workflow
   still in `preflight_mode=shadow`; audit official versus uncensored outcomes,
   completion certificates, effective actions, deadlines, and per-task deltas.
3. Run repeated matched trials before claiming efficiency. Require outcome
   preservation first, then report median/p95 tokens, calls, actions,
   effective actions, wall time, and solve rate. A single temperature-1 smoke
   cannot establish causality.
4. Keep the 89-task run blocked until the repeated outcome-first gates pass.

No rewrite or feature-driven suppression was enabled. The default preflight
remains PASS/SHADOW, postflight remains authoritative for execution facts, and
the full GT integration remains disableable with `integration_mode=off`.
