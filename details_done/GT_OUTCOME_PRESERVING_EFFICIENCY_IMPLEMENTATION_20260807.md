# GT outcome-preserving efficiency implementation

Date: 2026-08-07

## Result

The deterministic repair is implemented and provider-free replayed. It removes
three controller-caused sources of resource growth without changing the model,
task instructions, or GroundTruth's postflight evidence engine:

1. ordinary or task-local probe failures can no longer be reframed as failures
   of a required task check;
2. a partial completion plan can no longer execute any completion probe;
3. context compaction is driven by measured provider headroom and, once
   necessary, creates an immutable prefix epoch instead of rewriting historical
   context on every call.

This is implementation evidence, not a live efficiency claim. A paid matched
smoke is still required, and the 89-task run remains blocked.

## Root causes addressed

### Semantic authority, not command shape

The old path could recognize `pytest`, a custom `python test_*.py` probe, or a
wrapped runner and then give the failure task-contract semantics it had not
earned. Recognition answers "what executable is this?"; it does not answer
"did the task declare this as an obligation?".

`ValidationClassification` now records one of `NONE`, `CUSTOM_PROBE`,
`STANDARD_RUNNER`, `DECLARED`, or `HOST_SYNTAX`. Only `DECLARED` may produce a
model-visible `covering_red` required-check claim or latch
`GT_SS_SUBMIT_RED`/`submit_refusal`. A standard runner may contribute private
failure/recovery state, but is not described to the model as a required check.
A custom probe remains private and cannot create submission debt.

All required-check receipts carry `declared_check_id`. The runtime and deep
metrics expose `required_check_claims_without_declared_id`; the smoke gate
requires it to remain zero.

### Shared executable normalization

`normalize_executable_invocation()` unwraps deterministic environment
assignments and literal `env`, `command`, `sudo`, and GNU `timeout` wrappers.
Preflight and postflight therefore see the same executable, arguments, wrapper
chain, and literal requested timeout. Dynamic shell expressions abstain to
low-confidence `OTHER`/PASS rather than being guessed.

### Complete-only completion

The host now executes completion predicates only when
`CompletionPlan.executable` is true. `PARTIAL` means no private probes and no
certificate. This removes background work that could not possibly authorize
submission while preserving all predicates for complete mechanically proven
plans.

### Adaptive validation timeout

The historical 30-second action timeout remains the default. In active mode,
only a high-confidence, terminal-foreground, literal-timeout invocation with
`DECLARED` or `STANDARD_RUNNER` authority may receive a larger timeout. The
grant is capped by 120 seconds, 20% of remaining task time, and the task
deadline reserve. Custom probes, dynamic timeouts, ambiguous shell structure,
and nonterminal validators remain at the default. OFF and AUDIT disable this
behavior.

### Headroom-triggered immutable compaction

The agent first builds the exact provider-prepared request and measures its
budget. Compaction is unnecessary while the request retains a 131,072-token
reserve (also capped at 25% of the hard prompt limit). When compaction is
actually necessary, `ProviderViewSession` creates one compacted checkpoint,
receipts its source and stable-prefix hashes, and reuses that checkpoint for
later calls while appending new turns. The checkpoint never mutates. A fresh
bounded state frame is attached only to a copy of the latest safe tool
observation, so current evidence cannot become a stale frozen frame and the
preceding prefix remains cacheable. No distinct assistant reasoning is removed.

### Complete host execution accounting

`HostExecutionRecorder` now wraps model actions, workspace manifest/hash/
capture reads, syntax probes, completion probes, auto-submit, and system
information calls. Cache hits are recorded separately. The corrected metric is
`effective_task_actions`: all actual task-environment executions except host
system information. `actions` remains the count of model-selected actions;
controller work can no longer disappear behind that number.

The deep metric schema is `central-deep-metrics-v2`. It reports actual and
controller environment executions, sensor executions, completion probes,
cache hits, semantic-authority counts, and provider compaction epochs.

## Archived trajectory replay

Command:

```text
python scripts/central_efficiency_replay.py D:\gt_runs\31190135547
```

Result:

- ten tasks replayed;
- four invalid visible failure receipts on two actions are suppressed;
- 28 partial-plan completion-probe executions become zero;
- a complete write-compressor plan retains five required probes;
- projected compaction epochs are zero;
- minimum reconstructed raw provider headroom is 211,100 tokens after a
  conservative archived-advisory allowance;
- marker: `ARCHIVED_EFFICIENCY_REPLAY_OK`.

The independent regression-preservation replay also passes. Its archived
write-compressor request has 833,034 tokens of measured headroom, so it remains
exact, creates zero compaction epochs, and removes zero assistant-reasoning
characters. The scheduler plan remains partial/non-executable and cannot claim
completion.

## Provider-free verification

- full repository suite: 1,026 collected, zero failures, three expected
  platform/coverage skips;
- all-17 direct and module census: every required producer, consumer, timing,
  grounding, context-accounting, applicability, and no-blocking marker passed;
- central readiness audit: `READY`;
- archived efficiency replay: `ARCHIVED_EFFICIENCY_REPLAY_OK`;
- archived preservation replay: `ARCHIVED_REGRESSION_REPLAY_OK`;
- Ruff, Python compilation, workflow YAML parsing, and `git diff --check`:
  passed.

## Outcome-first acceptance gate

Strict per-task Pareto remains diagnostic, not the sole acceptance rule for a
temperature-1 run. The release gate now requires all of the following:

1. no loss of a baseline solve and no new outer censor;
2. negative aggregate deltas for total tokens, API calls, effective task
   actions, and normalized cost;
3. nonpositive aggregate model-action and wall-time deltas when comparable;
4. no solved task exceeding the bounded outlier policy in two or more resource
   dimensions.

This avoids both errors: accepting an aggregate improvement that hides a large
task regression, or rejecting a useful aggregate result because harmless
temperature noise made one dimension slightly positive.

## Rollback and non-claims

`integration_mode=off` preserves the historical GT-off behavior. AUDIT remains
observation-only and forces SHADOW intervention. The paid workflow remains
ACTIVE plus SHADOW; no command rewrite or feature-driven suppression is
enabled.

No paid smoke was started as part of this implementation. The replay proves
the deterministic policy difference and preservation invariants, not future
solve rate, token savings, or causality. Those require the separately
authorized matched ten-task smoke before any 89-task run.
