# ENGINE Gap Analysis — why GT added no value in the witness

Source: ENGINE witness run `30736459512` (9 trajectories, 417 actions) read at
the message level. Question: the ENGINE was built to deliver GT facts at the
correct time (action-bound, same observation, before the next model call, no
prediction). Did it? **The mechanism did; the payload did not.**

## 1. Timing proof — the ENGINE delivers ON TIME (no prediction, no lateness)

Per-task message-stream audit:

| task | turns | engine obs | obs on-time | obs late | predictive bytes | interleaved GT |
|---|---|---|---|---|---|---|
| fix-code-vulnerability | 49 | 48 | 48 | 0 | 0 | 0 |
| portfolio-optimization | 31 | 30 | 30 | 0 | 0 | 0 |
| modernize-scientific-stack | 8 | 8 | 8 | 0 | 0 | 0 |
| headless-terminal | 42 | 41 | 41 | 0 | 0 | 0 |
| llm-inference-batching-scheduler | 49 | 48 | 48 | 0 | 0 | 0 |
| break-filter-js-from-html | 28 | 34 | 34 | 0 | 0 | 0 |
| write-compressor | 27 | 29 | 28 | 1 | 0 | 0 |
| schemelike-metacircular-eval | 100 | 100 | 99 | 1 | 0 | 0 |
| cobol-modernization | 72 | 73 | 73 | 0 | 0 | 0 |

- 411/411 observations are the engine's canonical `<gt-engine>` observation for
  that action, delivered in the same tool message before the next assistant
  turn. The 2 "late" are the final observation of a run with no following turn.
- **0 predictive bytes**: no task-start localization, no pre-action contract
  injection (the ENGINE posture removed it).
- **0 interleaved GT user directives**: no late/global advisory messages.

Conclusion: the ENGINE's causal-timing requirement — the reason we moved off
advisory — is satisfied by the mechanism.

## 2. Payload proof — the ENGINE delivered ZERO facts

| task | bash actions | typed actions | engine obs | with <gt-fact> | with GT_EVIDENCE | raw-only |
|---|---|---|---|---|---|---|
| fix-code-vulnerability | 49 | 0 | 48 | 0 | 0 | 48 |
| portfolio-optimization | 31 | 0 | 30 | 0 | 0 | 30 |
| modernize-scientific-stack | 9 | 0 | 8 | 0 | 0 | 8 |
| headless-terminal | 42 | 0 | 41 | 0 | 0 | 41 |
| llm-inference-batching-scheduler | 49 | 0 | 48 | 0 | 0 | 48 |
| break-filter-js-from-html | 35 | 0 | 34 | 0 | 0 | 34 |
| write-compressor | 29 | 0 | 29 | 0 | 0 | 29 |
| schemelike-metacircular-eval | 100 | 0 | 100 | 0 | 0 | 100 |
| cobol-modernization | 74 | 0 | 73 | 0 | 0 | 73 |

- 417 actions, **0 typed** `groundtruth(...)` calls — the model never chose the
  typed tool, so no typed producer ever had a request.
- **0/411 observations carried any deterministic fact** (`<gt-fact` / `GT_EVIDENCE`
  both zero). Every observation is `raw + <gt-engine>` wrapper, nothing else.
- All decisions `pass_through` (correct for opaque bash) — but AUGMENT/REPLACE
  never had a candidate fact to attach.

## 3. Which of the 17 DIRECT features worked — and were they on time?

In the ENGINE witness: **0 of 17 delivered.** The on-time mechanism was empty.
Per-feature status and why (each maps to a concrete wiring gap):

| feature | delivered | on-time-capable in ENGINE | gap |
|---|---|---|---|
| syntax_result | NO | yes (postflight after edit) | producer not ported into runner bash path |
| signature_delta | NO | yes (postflight after edit txn) | producer not ported |
| covering_red | NO | yes (postflight after test/build) | producer not ported |
| obligations | NO | yes (action-bound read/edit/test/submit) | producer not ported |
| recovery | NO | yes (postflight on repeated failure) | producer not ported |
| localization | NO | yes (action-keyed SEARCH/READ) | producer not ported |
| newfile_precedent | NO | yes (CREATE_PROPOSAL preflight) | mutation slice not invoked by bash edits |
| def_partition | NO | yes (typed search) | typed tool never used by model |
| submit_refusal | NO | yes (submit decision) | submit gate never fired (no blockers seen) |
| caller_contract | NO | n/a (REMOVE disposition) | removed from schema by design |
| GT_CHANGE_SURFACE / GT_PATCH_DELTA / GT_LOC_RESLOT / GT_SS_SUBMIT_RED / GT_EDIT_CHECK / GT_HYPOTHESIS / GT_CERT_DELIVERY | NO | n/a (lineage/receipt) | engine_delivery receipts exist; no feature bytes emitted |

## 4. Root cause (one sentence)

The ENGINE owns the correct-timing boundary (`runner.py` normalizes → decides →
executes → compiles → receipts every action on time) but **its deterministic
producers are not wired into the engine path**: the advisory postflight gateway
(`_run_evidence` / `run_evidence_pipeline` → syntax_result, signature_delta,
covering_red, obligations, recovery, localization, newfile_precedent) is absent
from `runner.py`'s bash branch, and the typed tool that would trigger
def_partition/covering_red was never selected by the model.

## 5. Fix plan (makes the on-time engine actually deliver facts)

1. **Port the postflight producers into `runner.py`'s bash/verification path**
   (IE-06 completion): after each action, run the deterministic producers gated
   on action kind — edit→syntax_result + signature_delta, test/build→covering_red,
   repeated-failure→recovery, read/search→action-keyed localization. Attach the
   resulting EvidenceArtifacts to the same canonical observation (AUGMENT).
2. **Wire the mutation proposal/commit slice to bash edits** so create/edit
   preflight (newfile_precedent, signature_delta pre-commit) can fire.
3. **Measure typed-tool adoption** (or add a mechanism that surfaces the typed
   affordance without selecting actions) so def_partition/covering_red can fire.
4. **Re-witness** the same ten tasks; the endpoint becomes: do the on-time
   facts change behavior (fewer re-reads, faster first edit, less repeated
   acquisition) — not just solve-rate (already tied).

## 6. Why the next full run is currently pointless

A full 89-task ENGINE run with the current engine re-measures an empty
envelope: correct timing, zero facts, token overhead from the wrapper. The
witness already showed the ceiling — reward ties, +5..+253% tokens. GT only
becomes testable after §5 wires the producers.
