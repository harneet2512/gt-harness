# GT-on 10/10 versus 8/10 — exact comparison and next gate

Date: 2026-08-06

## Correct conclusion

The earlier 10/10 result was a real paid GT-on smoke, not a replay and not a
reporting mistake. Workflow `31136099371` ran commit `8ab1896` and returned ten
uncensored rewards of 1. Workflow `31142998081` ran the later repair commit
`5c92a6a` and returned 8/10, with a new uncensored `schemelike` solve loss.

Neither individual smoke proves an outcome claim. The mistake would be to call
the 10/10 run proof that GT always wins, or to call the 8/10 run proof that the
repair caused a regression, without checking experimental identity and code
reachability.

## Identity audit

| condition | 31136099371 (10/10) | 31142998081 (8/10) | result |
| --- | --- | --- | --- |
| tasks | identical frozen 10-task slice | identical | matched |
| model/temperature | DeepSeek V4 Flash / 1.0 | same | matched |
| agent | `MiniSweCentralAgent` | same | matched |
| integration | `active` | `active` | matched |
| preflight | `shadow` | `shadow` | matched |
| compaction | enabled | enabled | matched |
| completion/progress | enabled | enabled | matched |
| step limit | 100 | 100 | matched |
| Harbor task budgets | 600–3,600 sec per same task | same | matched |
| initial schemelike provider input | 2 messages, 4,224 bytes, SHA-256 `c8b2…e94d` | identical | matched |

The first sampled `schemelike` action differed despite the identical initial
request: the 10/10 run selected `ls -la`; the 8/10 run selected a combined
`ls -la && … find` command. This is direct observed temperature-1 trajectory
variance before any GT-visible evidence could affect the model.

## Code-reachability audit

The live code delta `8ab1896..5c92a6a` did **not** modify
`gt_engine/provider_view.py`, `gt_engine/completion.py`, or
`gt_engine/progress.py`. Thus it did not introduce a new compaction,
completion, or progress algorithm.

The executable changes were deliberately narrow:

1. GitHub now installs/proves the vendored graph runtime, so structural graph
   evidence can be available instead of silently failing.
2. `def_partition` requires graph definition/reference roles and
   `caller_contract` requires certified directed `CALLS` edges.
3. A provider-visible claim has one first-eligible window; an arbitration loser
   stays controller-private rather than leaking late into a later call.
4. Opportunity/applicability receipts expose a missed trigger or unsupported
   substrate instead of treating absence as natural non-firing.

For the disputed task these changes do not explain a changed prompt:

- in the 10/10 run the index was unavailable; in the 8/10 run graph evidence
  produced private `caller_contract`/`def_partition` effects;
- neither run selected a context state frame, nor executed an engine action,
  nor had a preflight intervention;
- both runs emitted exactly one 135-character `GT_EDIT_CHECK` fact with the
  same text and the same evidence hash (`ca5108d872cf7f56eef4`):
  `Unvalidated authored changes in eval.scm; declared check: echo '(+ 7 8)' | python3 interp.py test/calculator.scm.`
- it was emitted at different action numbers because the sampled model paths
  had already diverged.

Both archived paths replay successfully through the current repaired policy:
`python -m scripts.central_replay <run-root>` reports `REPLAY_OK` for all 20
task trajectories. The direct script form currently lacks repository-root path
bootstrap and fails import resolution; the module form is the supported
invocation until that small entrypoint defect is fixed.

## What is and is not proved

Proved:

- The new code repaired a real graph-runtime substrate failure and tightened
  stale-delivery semantics.
- It did not add a new command rewrite, completion action, progress action,
  context-state frame, or changed validation message in the lost task.
- The stochastic model path diverged before GT could deliver visible evidence.

Not proved:

- That the 8/10 outcome was caused by GT.
- That the outcome was pure sampling noise. Later compaction volume and the
  otherwise-correct action-71 validation fact remain possible contributors.
- That a single 10/10 or 8/10 sample establishes non-regression at temperature
  1.

## Next plan

### Phase A — make treatment differences mechanically inspectable

1. Add a direct-entrypoint test and repository-root bootstrap to
   `scripts/central_replay.py`, so documented direct and module invocations
   behave identically.
2. Add `scripts/central_run_diff.py`, a provider-free comparison that accepts
   two archived run roots and emits, per task and model call:
   - canonical provider-request hash and message-count differences;
   - first divergent model action and whether it predates GT-visible evidence;
   - context transform decision, old-observation elision, and state-frame
     character deltas;
   - semantic frame text/hash, evidence action, eligible call, and delivery
     status;
   - controller-executed actions, preflight disposition, and completion action.
3. Test the report using the two archived smokes and synthetic fixtures. A
   missing request hash, unaccounted difference, or late/predictive frame must
   fail closed.

### Phase B — fixed-trajectory differential proof

1. Replay the same archived action/output/snapshot stream through the old and
   repaired runtime policies.
2. Assert that a call with no new grounded first-eligible fact has identical
   canonical provider messages under both policies.
3. Assert that the intended graph repair adds only recorded private structural
   state unless a source-backed claim independently qualifies for delivery.
4. Assert that one-window suppression removes only an otherwise one-step-late
   fact, never a first-eligible fact.

This proves what the deterministic engine changes under identical evidence. It
does not replay a model or manufacture an outcome claim.

### Phase C — separate GT components in the next paid design

Expose auditable arm flags, retaining the current all-on treatment, for:

1. graph/runtime enrichment;
2. active first-eligible evidence delivery;
3. deterministic context compaction; and
4. completion/progress control.

Provider-free tests must establish that turning a flag off changes only its
declared surface. This is required because an integrated temperature-1 run
cannot distinguish an evidence effect from a context-view effect.

### Phase D — outcome gate

After Phases A–C pass, request authorization for the next matched GT-on
10-task smoke. Compare it with the existing GT-off baseline and the two
archived GT-on witnesses, but report outcome-first results:

- no new uncensored solve loss versus GT-off;
- no outer Harbor censor; and
- per-task resource deltas only for tasks solved in both arms.

Do not run the 89-task benchmark, retune feature triggers, or claim
non-regression from the current two samples. A random temperature-1 model can
choose different actions from identical requests. A deterministic engine can
make its own state and delivery replayable, but cannot turn a shadow
non-intervening model call into a deterministic sample.
