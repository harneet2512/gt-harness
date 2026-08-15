# GroundTruth Pre-Action Implementation Receipt

Date: 2026-08-05  
Branch: `inline-engine`  
Base HEAD used for regression comparison: `1de4bac`  
Status: provider-free implementation verified; paid assistive evaluation not run.

## Outcome

The central GroundTruth engine now receives a typed model-selected Bash action
before `environment.exec`. It performs bounded deterministic preflight while
preserving the existing post-execution feature engine. A material direct fact
can return the selected action to the model before execution in
`ASSISTIVE_SAFE`. The paid workflow remains `SHADOW`, so this change cannot
silently alter a paid run before a separate intervention gate is authorized.

This is pre-execution interception, not prediction. GT receives only what the
model already selected plus current host-owned state.

## Paid smoke audit and delivery-accounting repair (2026-08-05)

The authorized ten-task GitHub smoke completed all ten task jobs on commit
`3f69c1f`; the workflow merge step initially failed because it referenced an
undefined `n_errored` variable. That workflow defect was corrected and pushed
as `ff308b6`. The existing task artifacts were audited locally, so no paid
rerun was spent merely to recompute the merge.

The smoke exercised 739 model actions with preflight in `SHADOW` mode. Every
action received a deterministic preflight receipt, all 739 applied as `PASS`,
and preflight p95 was below 0.02 ms per task (maximum observed 0.037 ms).
The run produced 304 effects; 21 had confirmed provider payload delivery, 9
were consumed by existing engine reads, 1 entered a prepared decision frame,
and 273 changed only private controller state. Twenty model guidance
deliveries were timely (20/20 before the first eligible model request; no late
or predictive delivery).

The first receipt audit found a real correctness defect: a repeated grounded
`covering_red` effect was marked `model_visible=true` although semantic
deduplication correctly omitted it from the delivery. The runtime now marks
such effects and receipts `delivery_status=suppressed` with
`delivery_reason=semantic_duplicate`, prevents a suppressed receipt from
being selected as the source for a later delivery, and marks actually linked
effects/receipts `delivery_status=delivered`. A regression test covers the
exact repeated-failure trajectory. The all-17 census and 143 relevant
provider-free tests pass after this repair.

This smoke remains descriptive, not an efficiency claim: the workflow used
`preflight_mode=shadow`, so preflight did not change execution or batch
reasoning. The six censored tasks and mixed solve outcomes cannot prove an
assistive GT-on win. A future paid assistive run is gated on this accounting
repair and must separately report intervention, stale-batch prevention, and
matched GT-off deltas.

### Fresh smoke result after the repair (run `30976148466`)

The fresh ten-task workflow completed successfully, including the merged
artifact, on commit `951e136`. The receipt audit found:

| Invariant | Observed |
|---|---:|
| Actions / preflight calls | 698 / 698 |
| Preflight dispositions | 698 `PASS` (shadow mode) |
| Effects | 361 |
| Model-visible effects | 36 |
| Model-visible effects linked to a delivery | 36/36 |
| Orphan model-visible effects | 0 |
| Guidance deliveries | 34 (coalesced 36 effects) |
| Timely, non-predictive deliveries | 34/34 |
| Preflight latency p50 / p95 / p99 / max | 0.0077 / 0.0113 / 0.0213 / 0.0252 ms |

The task result was 4/10 solved; six tasks reached the assistant-step limit.
Because this workflow deliberately runs preflight in `shadow`, it did not
alter commands or batch reasoning. The result is therefore a correctness,
timing, and accounting proof for the integration—not evidence of a solve-rate
or efficiency gain over GT-off.

### Engine-work accounting correction

The original summary wording called all non-provider effects "private" and
could be read as saying they were inert. That was too coarse. The effect trace
distinguishes 274 `engine_internal_state` effects, 7 existing-engine-actuation
effects, 36 provider-payload effects, and 44 audit-only effects in this smoke.
The summary now uses `engine_internal_state` for deterministic producer work,
`existing_engine_actuation` for recorded downstream reads, and reserves
`unread_private_state` for a state mutation with no producer event or recorded
read. This matches the engine model: lack of model text is not evidence of
lack of engine work.

### Deterministic context-compiler and segment-accounting addendum

The post-smoke regression repair closes the remaining gap between owning the
action loop and owning context correctly. `ProposedAction` now contains every
mechanically known shell-segment operation, not only one primary command label.
The same object is reused by postflight, so a compound `cd && nl file | sed -n
'20,40p'` records the actual read path/range before the output is reduced into
controller state. Whole-command validation classification is attached only to
the runner segment, and shell programs such as `sed 's/x/y/'` are excluded from
file targets.

Every provider call now runs a deterministic `ContextFact` compiler. It records
exact provider-message indices when a fact is already present, selects a
bounded declarative frame only for current material facts that are absent, and
keeps controller-only/stale/budget omissions explicit. The request receipt
requires candidate and accounted fact counts to match. Exact-turn dedupe now
includes assistant content and reasoning, so different Mini-SWE reasoning is
never removed merely because a command and output repeat. Lossy compaction is
disabled in the paid workflow.

The archived ten-task replay completed `REPLAY_OK`: 641/698 primary actions
were typed (91.8%), 57 remained conservative `OTHER/PASS`, and all 324 replayed
feature effects were context-accounted (321 controller state, 3 provider
payloads, 0 unaccounted). This is an implementation/correctness proof. It does
not retroactively turn the 4/10 smoke into an efficiency win.

## Current call graph

```text
model.query(query_messages)
  -> assistant extra["actions"]
  -> classify each command once
  -> adapt each action to ProposedAction
  -> CentralFeatureRuntime.preflight_action
  -> apply OFF / SHADOW / ASSISTIVE_SAFE policy
  -> record ActionCycleReceipt
       PASS -------------------------------> environment.exec(original)
       AUGMENT ----------------------------> environment.exec(original)
       RETURN_TO_MODEL --------------------> synthetic tool result, no exec
       REWRITE/SUPPRESS candidate ---------> PASS (production-disabled)
  -> WorkspaceSensor.scan and diff_snapshots
  -> observe_action with the same ValidationClassification
  -> record_action_postflight using the proposal cycle ID
  -> consume_effects through the existing 17-feature engine
  -> hybrid stale-batch barrier
  -> next model call
  -> record whether a returned command changed
```

Source locations at the time of this receipt:

- model response/actions: `eval/gt_central_agent.py:640-662`
- proposal adaptation: `eval/gt_central_agent.py:667`
- preflight call: `eval/gt_central_agent.py:737`
- policy receipt: `eval/gt_central_agent.py:790`
- environment execution: `eval/gt_central_agent.py:870`
- postflight observation: `eval/gt_central_agent.py:965`
- proposal/postflight join: `eval/gt_central_agent.py:977`
- batch barrier: `eval/gt_central_agent.py:1043`
- runtime preflight producer: `gt_engine/central_runtime.py:1649`

## Confirmed gap resolution

| Original statement | Current status | Proof |
|---|---|---|
| `environment.exec` occurs before `observe_action` | TRUE, intentionally | Postflight remains authoritative for execution-derived facts. Preflight now occurs before both. |
| No GT function receives a proposed action first | FALSE | Ordered spy proves `preflight_action` occurs before selected-command execution. |
| GT cannot defer or augment before execution | FALSE in assistive mode | A real absent edit target returns a tool observation before execution; a revised edit then executes. |
| GT can rewrite or suppress feature actions | FALSE | Both dispositions are represented for contract tests but downgraded to PASS by production policy. |
| All actions in a response can execute stale | FALSE in assistive mode | Read/search may batch. Known stateful, validation, submit, materially changing, or revision-changing boundaries cancel the suffix. `OTHER` or a generic exploratory failure alone is not a barrier. |
| `record_predecided_continuation` cancels actions | FALSE | It remains audit-only. The host barrier is the distinct cancellation path. |
| GT receives typed model intent | PARTIAL | The public tool still supplies only Bash text. The host deterministically derives a typed proposal; unknown remains `OTHER`. |

## Typed contract

`ProposedAction` records:

- provider tool-call ID and stable cycle ID;
- raw command;
- `READ`, `SEARCH`, `EDIT`, `CREATE`, `DELETE`, `VALIDATE`, `SUBMIT`,
  `INSTALL`, or `OTHER`;
- bounded repository-relative targets;
- mutation and exclusive-create semantics;
- validation class;
- workspace/source revisions;
- model call, batch index, and batch size;
- parsed shell segments, parser evidence, and confidence.

The Bash tool schema was not changed. Mini-SWE still exposes one `bash` function
with one required `command` string. Several actions remain representable in one
assistant response. Malformed provider tool calls are still rejected by
Mini-SWE's existing model normalization/format-error boundary.

## Policy modes and invariants

### OFF

- no preflight call;
- no action-cycle receipts;
- historical postflight behavior;
- one-switch rollback.

### SHADOW

- proposal, candidate decision, latency, and evidence are recorded;
- original command always executes;
- original batching behavior is preserved;
- used by the paid workflow.

### ASSISTIVE_SAFE

- active dispositions: PASS, AUGMENT, RETURN_TO_MODEL;
- REWRITE is always rejected;
- feature-driven SUPPRESS is always rejected;
- timeout or exception becomes recorded PASS;
- unhealthy workspace sensor becomes PASS;
- parser ambiguity/unknown becomes PASS;
- heuristic, empty, oversized, low-confidence, stale, or duplicate evidence
  cannot intervene;
- source revision is checked again at dispatch;
- an unknown classification or generic exploratory nonzero exit alone does not
  force another model call; a proven material change does;
- returned evidence is a standard tool observation, not a task prompt mutation;
- the current command and cancelled suffix each receive exactly one tool result.

The initial state-only preflight target is 25 ms p95 with a 100 ms host timeout.
Those are engineering limits, not benchmark measurements. Receipts record the
actual p50/p95/p99/max.

## All-17 lifecycle placement

| Feature | Preflight eligibility | Execution-derived postflight required | Evidence rule |
|---|---|---:|---|
| obligations | SUBMIT | yes | current task contract/obligations |
| localization | EDIT/CREATE | yes | fresh source-bound graph only |
| GT_LOC_RESLOT | EDIT/CREATE | yes | ranked source anchors; shadow initially |
| def_partition | EDIT | yes | definition/reference evidence |
| caller_contract | EDIT | yes | directed caller edges only |
| newfile_precedent | CREATE | yes | exact create target plus source sibling |
| GT_CHANGE_SURFACE | none | **yes, only** | actual workspace diff |
| signature_delta | none | **yes, only** | before/after source contents |
| GT_PATCH_DELTA | none | **yes, only** | actual patch/change surface |
| GT_EDIT_CHECK | EDIT/VALIDATE | yes | source-bound validation debt/check |
| syntax_result | none | **yes, only** | generated source plus syntax result |
| covering_red | none | **yes, only** | executed validator output |
| GT_HYPOTHESIS | VALIDATE | yes | unchanged revision plus failure fingerprint |
| recovery | VALIDATE | yes | exact repeated failure plus concrete alternative |
| submit_refusal | SUBMIT | yes | fresh grounded failing check |
| GT_SS_SUBMIT_RED | SUBMIT | yes | same blocker state, no duplicate message |
| GT_CERT_DELIVERY | SUBMIT | yes | current revision-bound checks |

Exactly five features are postflight-only. Placement is executable structured
data, not a prose-only table. One proposal is evaluated once; there are not 17
independent preflight calls.

## Material interventions currently implemented

1. Submit while a fresh explicit grounded check is failing at the current
   source revision.
2. A simple high-confidence in-place edit targets a path absent from the
   current healthy workspace snapshot.
3. An exclusive create operation targets an already existing path.

`touch existing.py` is explicitly PASS because touch is idempotent; it is not
treated as proof of an erroneous duplicate creation. Graph-derived caller,
coupled-file, and precedent advisories remain shadow until their mechanical
materiality predicates are separately proven.

## Repository intelligence

The repository mirror is now task-scoped instead of being destroyed after call
one. After a source transition, only sensor-captured source contents are applied
to the host mirror and the graph is re-indexed before the next reasoning turn.
If any required source content is absent, a path escapes the mirror, the sensor
is degraded, or refresh times out, the graph becomes unavailable rather than
stale. Task-start effects are not re-emitted during refresh.

## Observability

`central_receipt.json` now includes:

- every `ActionCycleReceipt`;
- proposal operation/confidence/revisions/batch coordinates;
- candidate and applied disposition separately;
- reason codes and evidence grade;
- execution command/result and postflight revisions;
- next command after reconsideration and whether it changed;
- candidate/applied disposition distributions;
- operation distribution;
- preflight p50/p95/p99/max latency;
- parser confidence mean/min;
- material-evidence and return counts;
- commands changed after return;
- duplicate-evidence count;
- stale batched actions prevented;
- postflight-only feature count;
- explicit `false_intervention_status=requires_outcome_oracle` rather than a
  fabricated zero.

Existing resolve rate, calls, actions, assistant steps, tokens, normalized cost,
context characters, and wall time remain present for matched ablation analysis.

## Executable verification

Focused integration command:

```text
python -m pytest tests/test_gt_preflight.py tests/test_gt_semantic_engine.py
tests/test_gt_repository_intelligence.py tests/test_gt_checkpoint_ledger.py
tests/test_gt_central_runtime.py tests/test_gt_central_agent.py
tests/test_gt_central_consumer_proof.py tests/test_gt_deep_metrics.py
tests/test_central_replay.py -q
```

Result: 149 passed (current collected focused set).

Provider-free census result:

- `ALL_17_PRODUCERS_PROVEN`
- `ALL_17_CONSUMERS_PROVEN`
- `ALL_EFFECTS_TIMING_VALID`
- `ALL_PAYLOADS_GROUNDED`
- `ALL_17_CONSUMER_PATHS_PROVEN`
- `ALL_17_TRIGGERS_PROVEN`
- `ALL_17_PAYLOADS_CONCRETE`
- `ALL_17_CONSUMERS_APPLIED`
- `ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST`
- `NO_ACTIONS_BLOCKED` (historical postflight census; assistive preflight is a
  separate gate)

`scripts/central_readiness_audit.py` prints `READY` and now checks default OFF,
paid SHADOW, and provider-free preflight test inclusion.

The archived 89-task run was then replayed locally through the repaired
postflight policy and conservative preflight shadow audit: `REPLAY_OK` for all
89 tasks and 4,525 actions. The action adapter classified 4,192 actions as
`OTHER`, 186 validation, 69 submit, 63 read, 11 search, 3 delete, and 1 create.
The first draft would have split 240 multi-action calls and prevented 255
suffix actions merely because `OTHER` was conservative; that was rejected as
an efficiency defect. Rejecting generic exploratory failures as barriers, and
requiring an observed filesystem delta for parser-inferred mutations, reduced
the repaired evidence-based projection to 19 barrier calls and 20 prevented
suffix actions: 13 source-changing boundaries, 3 additional observed-mutation
boundaries, and 3 validation boundaries. Five of the 13 source-changing
boundaries also had a positive mutation signal; they are not double-counted in
the 19.
Archived data produced zero material
preflight candidates because no grounded submit blocker was current and the
archive lacks complete pre-execution file snapshots; file policy correctly
abstained rather than inventing facts.

The final full repository suite collected 918 tests and completed with 915
passing and 3 expected platform skips; it had zero failures. An earlier
intermediate run had seven failures that were also reproducible at base HEAD
`1de4bac`, but that intermediate comparison is superseded by the final green
working-tree run recorded in `artifacts/preflight-full-suite.log`.

No provider request, paid smoke, or 89-task benchmark was run.

## Remaining gates and non-goals

Remaining before paid assistive evaluation:

1. Inspect the 19 projected batch barriers and keep only dependency boundaries
   whose avoided stale reasoning justifies an extra model call.
2. Keep graph-derived edit/caller/create advisories shadow because the archive
   cannot prove their pre-execution file snapshot.
3. Collect actual snapshot-bound candidates in a separately authorized SHADOW
   smoke; SHADOW does not alter commands or batches.
4. If shadow is clean, request explicit authorization for a matched 10-task
   assistive smoke. Reuse the frozen GT-off baseline; do not rerun it.
5. Compare outcomes first, then tokens, calls, assistant steps, actions,
   predecided actions prevented, wall time, intervention rate, reconsideration
   rate, and per-task deltas.
6. Do not start the 89-task run until repeated matched trials clear the outcome
   and efficiency gates.

Non-goals remain: action prediction, MCP/sidecar integration, a GT model call,
full-file injection, speculative file selection, benchmark task-ID tuning, or
claims of causal benefit from one temperature-1 run.

## Rollback

- Operational rollback: `preflight_mode=off`.
- Paid-workflow safety: retain `preflight_mode=shadow` until authorization.
- Code rollback: revert `gt_engine/preflight.py`, the central runtime policy and
  receipt methods, and the central-agent dispatch seam. The existing postflight
  engine is preserved independently.
