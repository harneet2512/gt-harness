# Semantic progress and compaction repair

Date: 2026-08-07

## Scope

This repair addresses the confirmed causes of the latest GT-on efficiency
regression. It does not change model prompts, command execution, task
instructions, or paid workflow authorization.

## Changes

### Semantic progress

`ProgressLedger` accepts a semantic gain signal separate from workspace
activity. The central agent classifies each action as `validation_gain`,
`diagnostic_gain`, `localization_gain`, `patch_attempt`, or `no_gain`.

Raw command/output novelty is retained for audit only. Source edits are not
treated as solved progress, and fixture resets, scratch files, derived
artifacts, and new output hashes cannot clear `BUDGET_RISK`. New attributed
validation passes, new attributable diagnostics, and new task-linked read
anchors can advance semantic progress. Receipts now expose activity counts and
the semantic-kind distribution.

### Compaction state retention

The provider compiler previously computed a current fact frame and then
discarded it. It now attaches one bounded frame to the latest retained tool
observation when old tool bodies were cleared. The frame is capped at 4,000
characters by the existing compiler contract, contains only complete
source-backed facts, and records the exact message index. No distinct
assistant reasoning is removed. If no tool observation remains, the fact is
recorded as `no_safe_delivery_surface`.

### Completion probe caching

Completion predicates now declare dependency paths. Private probe observations
are cached by predicate ID and a deterministic dependency fingerprint of those
paths. Cached results are rebased to the current workspace revision before
certificate evaluation, so stale certificates cannot be issued. Probe
execution and cache-hit counts are exported in deep metrics.

### Shell coverage

Typed shell operations now carry a structural role: `action`, `shell_context`,
`output_only`, `opaque_program`, or `unknown`. `cd`, output-only `echo`/`printf`,
and opaque interpreter programs no longer inflate the actionable unknown count.
Unsupported syntax still abstains to `OTHER`/PASS.

## Verification

Passed:

- focused progress, provider-view, preflight, completion, deep-metrics, and
  central-agent tests;
- Python compilation for `eval`, `gt_engine`, and tests;
- `python -m scripts.central_feature_census`;
- `python scripts/central_readiness_audit.py`.

The census still reports all producer, consumer, timing, grounding, trigger,
context-accounting, and no-blocking success lines. No paid smoke has been run
for this repair. The paid workflow remains `ACTIVE + SHADOW`; the 89-task run
is blocked pending archived trajectory replay and an authorized matched smoke.
