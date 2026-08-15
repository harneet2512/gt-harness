# GT Deterministic Context Compiler Implementation — Done

Date: 2026-08-05
Branch: `inline-engine`
Scope: central context selection, compound-action semantics, effect accounting,
deep metrics, release gates, and archived ten-task replay
Paid 89-task run: **blocked**

> **Superseded provider-view policy (2026-08-06):** This receipt describes the
> earlier context-compiler implementation. Workflow `31078501162` later proved
> that its state-frame/lossy-compaction policy was not outcome-safe. The active
> contract is documented in
> `GT_REGRESSION_PRESERVATION_IMPLEMENTATION_20260806.md`: paid compaction is
> enabled, assistant reasoning is never removed, only tool bodies may be
> bounded/receipted, no generic state frame is injected, and every exact
> provider request must pass the pre-query hard-budget gate. Treat statements
> below about disabled compaction or emitted state frames as historical.

## Result

GroundTruth is now implemented as the deterministic context compiler inside
`MiniSweCentralAgent`, not as an optional advice sidecar. Every final provider
request is compiled from the durable Mini-SWE history and typed current
controller state before `model.query()` begins. GT still makes no LLM call,
does not predict the model's next action, does not invent a plan, and does not
require an acknowledgement marker.

The implementation guarantees properties under GT's control:

1. distinct Mini-SWE reasoning is never removed by dedupe;
2. every candidate current fact has an explicit request disposition;
3. a fact described as present has exact provider-message indices;
4. a missing current material fact can enter one bounded declarative frame;
5. stale, private, duplicate, and over-budget facts remain explicit;
6. every feature effect receives first-eligible compiler accounting;
7. the typed proposed action is inspected before execution and the same action
   object is used by postflight;
8. ambiguous shell semantics remain `OTHER` and default to PASS;
9. the paid workflow preserves all historical reasoning by disabling lossy
   old-turn compaction;
10. final request hashes make exposure replayable.

This does not mathematically guarantee that a temperature-1 model will never
produce a worse solution. Determinism removes GT-caused timing, duplication,
staleness, and context-corruption variance; it cannot make stochastic model
sampling deterministic. Outcome preservation remains a matched-run gate.

## Root causes fixed

### 1. False context representation

The previous compiler used `represented_by_full_history` without proving where
the fact occurred. That made the accounting unverifiable. The new compiler
links represented facts to exact provider-message indices. If it cannot prove
representation, it either selects the complete current fact or gives an
explicit omission reason.

### 2. Dedupe could delete different reasoning

The old turn fingerprint used commands and tool outputs. Two turns with the
same command/output but different assistant reasoning could collapse. The new
fingerprint includes assistant content, reasoning content, commands, and tool
results. Only an exact duplicate turn may be removed.

### 3. Lossy compaction was too aggressive

The failed smoke compacted early even though observed requests were far below
the model's configured context capacity. That removed old reasoning without an
outcome-preservation proof. `enable_context_compaction=false` is now pinned in
the paid treatment workflow. Exact dedupe and typed fact accounting still run
on every request.

### 4. GT observed only one primary operation

Compound commands were reduced to one label, so real reads embedded in
`cd`, pipelines, and command chains did not reliably populate current state.
`ProposedAction.operations` now contains every mechanically classified segment.
Read observations record canonical path, requested line range, source revision,
workspace revision, action ID, return code, output hash, and whether output maps
to one read.

### 5. Validation leaked across compound segments

An immutable whole-command validation result was incorrectly applied to every
segment, including `echo` and setup commands. The classifier now binds
validation only to a mechanically recognized runner segment. The action-level
classification remains authoritative and is not reparsed by downstream
components.

### 6. Shell programs were mistaken for file targets

For commands such as `sed -i 's/x/z/' app.py`, `s/x/z/` was treated as a path.
In assistive preflight this could return a valid edit to the model as an absent
target. Program/query operands are now excluded from file targets, attached
redirections such as `>src/generated.py` are parsed, and pipeline connectors
are preserved so a `sed` range cannot leak across `&&`.

### 7. The effect funnel stopped at producer/controller categories

Each effect trace now records its first eligible compiler outcome:

- `provider_payload`;
- `controller_state_considered`;
- `stale_state_rejected`;
- `superseded_before_request`;
- `existing_engine_actuation`;
- `audit_only`; or
- `no_eligible_model_call` when the task ends before another request.

This accounts for private deterministic work without falsely claiming that it
was visible to the model or causally helpful.

## ContextFact request contract

Each fact contains a stable ID, kind, source/workspace revision, evidence
action, anchors, payload hash, and freshness. Every call assigns exactly one
disposition:

| Disposition | Meaning |
|---|---|
| `represented_message` | Exact request messages already contain the fact. |
| `selected_state_frame` | Current material fact was missing and was emitted in a bounded frame. |
| `controller_only` | Fact controls deterministic selection but is not useful prompt text. |
| `stale_source_revision` | Revision-bound fact was rejected. |
| `state_frame_budget` | Complete fact did not fit; no partial/misleading fragment was emitted. |

Per-call invariant:

```text
candidate_fact_count == accounted_fact_count
```

The receipt also records selected IDs, omission reasons, frame hash, raw and
final character counts, exact duplicate bytes removed, and unique assistant
reasoning bytes removed. The last metric must remain zero.

## Measuring use without acknowledgement markers

Internal model absorption is not directly observable. The implementation uses
three progressively stronger measurements and keeps them distinct:

1. **Exposure:** exact request hash plus provider-message indices proves the
   fact was present before `model.query()`.
2. **Behavioral utilization proxy:** a selected fact's concrete path, symbol,
   or command anchor appears in the immediately selected action. This is stored
   as `next_action_anchor_aligned` and is not called causal proof.
3. **Causal outcome/resource effect:** matched GT-off/GT-on or policy ablation
   with outcomes first, then tokens, calls, assistant steps, actions, failures,
   repeats, context characters, wall time, and censored-task counts.

No marker or forced acknowledgement is inserted into the prompt.

## Deep metrics added

- compiler calls;
- candidate/selected/represented/controller-only/omitted/accounted facts;
- stale and duplicate facts;
- exact duplicate characters removed;
- unique assistant reasoning characters removed;
- selected facts with measurable and aligned next-action anchors;
- compiler-considered, no-next-call, and unaccounted feature effects;
- primary operation distribution;
- segment operation distribution;
- known and unknown segment counts;
- existing preflight latency/disposition/reconsideration metrics;
- solve, censoring, tokens, normalized cost, calls, steps, actions, failures,
  repeats, context size, and wall time.

These metrics are extracted by the shared arm-neutral deep-metrics path. Counts
that exist only in GT-on are diagnostics, not efficiency resources.

## Code changed

- `gt_engine/preflight.py`: typed segment operations, connectors, read spans,
  redirection targets, runner-only validation, conservative target parsing.
- `gt_engine/central_runtime.py`: typed read ledger, same proposal in
  postflight, current feature-state candidates, per-effect compiler accounting,
  declarative visible-fact rendering.
- `gt_engine/provider_view.py`: exact reasoning-safe dedupe, `ContextFact`,
  representation proof, bounded missing-fact selection, per-call accounting.
- `eval/gt_central_agent.py`: compiler on every request before the provider,
  request hashes, action-alignment proxy, segment/deep metrics.
- `gt_engine/deep_metrics.py`: shared extraction/comparison fields.
- `scripts/central_feature_census.py`: provider-free all-effect context proof.
- `scripts/central_replay.py`: typed reads, segment metrics, effect accounting.
- `scripts/central_readiness_audit.py`: execution-order, compiler, workflow, and
  accounting invariants.
- `scripts/central_pre_smoke_gate.py`: new regression nodes in the paid gate.
- `.github/workflows/tb2_miniswe_engine.yml`: lossy compaction disabled; compiler
  and deep-metrics tests required.
- focused tests: preflight, provider view, real agent loop, consumer provenance,
  replay, census, and deep metrics.

## Provider-free verification

The exact paid-workflow semantic suite completed with 170 passing tests and no
failures:

```text
tests/test_gt_preflight.py
tests/test_gt_semantic_engine.py
tests/test_gt_repository_intelligence.py
tests/test_gt_checkpoint_ledger.py
tests/test_gt_central_runtime.py
tests/test_gt_central_consumer_proof.py
tests/test_central_replay.py
tests/test_gt_central_agent.py
tests/test_provider_view.py
tests/test_gt_deep_metrics.py
```

Additional evidence:

- changed-file Ruff: clean;
- Python compileall: clean;
- `git diff --check`: clean;
- central readiness audit: `READY`;
- direct census: all 17 producer/consumer/timing/payload lines plus
  `ALL_EFFECTS_CONTEXT_ACCOUNTED`;
- full repository suite: attempted twice, but the broad unrelated suite
  exceeded both 60-second and 180-second local command budgets; therefore no
  full-suite pass is claimed.

## Archived ten-task replay

Source: run `30976148466` trajectories.
Artifact: `artifacts/context-compiler-replay-30976148466.json`.

Result: `REPLAY_OK` for 10/10 tasks and 698 actions.

| Replay metric | Result |
|---|---:|
| Typed primary operations | 641/698 (91.8%) |
| Conservative primary `OTHER` | 57/698 (8.2%) |
| Replayed effects context-accounted | 324/324 |
| Controller-state-considered effects | 321 |
| Provider-payload effects | 3 |
| Unaccounted effects | 0 |
| Artifact-driven validation-debt regressions | 0 |
| Replay policy failures | 0 |

The segment-level classifier intentionally has more `OTHER` segments because
shell scaffolding (`cd`, `echo`, loop syntax, arbitrary binary execution) is not
invented into read/edit/validate intent. Primary action classification is the
relevant preflight statistic; segment counts explain all internal operations.

## Paid-smoke acceptance gate

Before dispatch, the implementation commit must be pushed and the exact commit
must pass:

```text
python scripts/central_pre_smoke_gate.py
```

Only `SMOKE_APPROVED` authorizes the paid ten-task GitHub workflow. The paid
configuration remains `preflight_mode=shadow`; the smoke validates the new
compiler, accounting, parser coverage, payload timing, and outcome/resource
deltas without allowing preflight to alter commands. The frozen local GT-off
baseline is reused and must not be rerun.

Required post-smoke audit:

1. all ten artifacts and rewards present;
2. no treatment censoring or lost baseline solve;
3. compiler calls equal API calls on every task;
4. candidate facts equal accounted facts on every call;
5. unique Mini-SWE reasoning removed equals zero;
6. every eligible effect has a compiler disposition;
7. all visible payloads are grounded, non-predictive, and first-eligible;
8. per-task and cumulative deltas for reward, tokens, calls, assistant steps,
   actions, failures, repeats, context chars, and wall time;
9. no efficiency claim unless outcome preservation passes first.

## Remaining work

- Commit and push the implementation and documentation.
- Run the exact-commit pre-smoke gate.
- If and only if it prints `SMOKE_APPROVED`, dispatch the authorized ten-task
  GitHub smoke.
- Download and audit every smoke artifact using the new compiler metrics.
- Keep the 89-task run blocked until the ten-task outcome-preservation gate and
  repeated matched efficiency trials pass.

No 89-task run was started by this implementation phase.

## Paid smoke and audit addendum

The authorized GitHub smoke completed as run `31061665540` from exact audited
commit `a45601f0ba05`. All ten task jobs and the merge job succeeded. The
integration audit passed with 334/334 compiler calls, 349/349 shadow PASS
actions, 5,287/5,287 facts accounted, 339/339 effects accounted, and 21/21
grounded first-eligible deliveries. There were zero compactions and zero unique
assistant-reasoning characters removed.

The experiment gate failed. Reward was preserved at 9/10, but
`cobol-modernization` incurred a treatment-only outer `AgentTimeoutError`,
`schemelike-metacircular-eval` reached the step limit, six solved tasks failed
strict Pareto, and normalized token cost increased 13.33%. The 89-task run
remains blocked.

Audit-driven repairs after the paid commit:

1. arm-neutral metrics now join Harbor outer exceptions and agent/trial wall
   time, preventing post-receipt timeouts from being mislabeled uncensored;
2. `edit_target_absent` no longer returns to the model—the live shadow receipts
   showed 104 false material candidates, while post-fix replay shows zero;
3. compiler context accounting now includes 182,536 state-frame characters in
   addition to 2,337 active-guidance characters.

Post-fix proof: 173 paid-workflow semantic tests passed, both census entrypoints
passed, readiness printed `READY`, Ruff passed, and the ten-task replay printed
`REPLAY_OK`. Full detail is in
`GT_SMOKE_31061665540_CONTEXT_COMPILER_AUDIT.md`.

No second paid smoke and no 89-task run were started.
