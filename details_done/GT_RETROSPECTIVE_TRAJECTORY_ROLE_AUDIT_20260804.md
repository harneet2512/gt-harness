# Retrospective Trajectory Role Audit — Smoke 30947423816

This audit uses the already completed ten-task trajectories and
`central_receipt.json` files. It does not rerun provider calls or task images.

## What is provable from the existing artifacts

- 354 effect applications are present across ten tasks.
- `guidance_deliveries` identifies the exact provider-facing feature/action
  references; 28 provider deliveries were recorded, with 29 contributor
  references after coalescing.
- Every applied effect has its feature, evidence action, revision, state
  section, and payload in the receipt.
- The trajectory contains the command and tool-observation boundary for every
  action, so feature roles can be joined to the action that produced them.

## Retrospective role classification

| Feature | Applied | Provider contributor refs | Existing engine role |
|---|---:|---:|---|
| `GT_CERT_DELIVERY` | 25 | 0 | readiness/certificate state |
| `GT_CHANGE_SURFACE` | 108 | 0 | source revision, edit scope, validation debt, lifecycle |
| `GT_EDIT_CHECK` | 10 | 0 | declared-check validation scheduling |
| `GT_HYPOTHESIS` | 2 | 0 | failure repeat-count state |
| `GT_LOC_RESLOT` | 38 | 2 | ranked search-anchor computation |
| `GT_PATCH_DELTA` | 75 | 1 | changed-path validation surface |
| `covering_red` | 2 | 0 | failure phase/state classification |
| `localization` | 38 | 0 | search anchoring and location lifecycle |
| `newfile_precedent` | 25 | 25 | repository precedent verification |
| `obligations` | 10 | 0 | contract and declared-check capture |
| `signature_delta` | 1 | 1 | signature-impact validation payload |
| `syntax_result` | 20 | 0 | validation result/lifecycle state |

The five features absent from this live trajectory were
`GT_SS_SUBMIT_RED`, `caller_contract`, `def_partition`, `recovery`, and
`submit_refusal`. Their absence is an event-coverage fact, not an integration
failure.

## Correct interpretation

The earlier `326 audit_only` label was a measurement defect. The old trace did
not record producer-side engine work, so it treated source-revision tracking,
validation-debt updates, failure-state latching, lifecycle transitions, and
anchor computation as if they were idle. The current code now records those
operations as `engine_internal_state`.

The existing artifacts are sufficient to classify the role of the 354 effects
without another paid run. They are not sufficient to prove a hidden frontier
model causal effect where no request, tool output, action policy, or state read
was recorded. That requires a future run with the corrected trace, not a
rerun merely to recover the role classification above.

## Utility evidence from the trajectory

The effects were not all inert:

- 126 effect instances participated in same-action GT control chains: change
  surface with patch/check scheduling, localization with anchor reslotting,
  and failure coverage with hypothesis/submission-risk generation.
- 29 feature-contributor references reached 28 actual provider requests after
  coalescing.
- Signature-delta guidance was followed by a command inspecting the affected
  implementation and child code.
- Localization guidance was followed by a task-relevant interpreter/check
  command.
- New-file precedent guidance was followed by subsequent task-specific test,
  build, benchmark, and inspection commands.

These are demonstrated engine/trajectory consequences, not model
acknowledgments. The remaining effects are proven internal state and lifecycle
operations, but the old run cannot establish whether each one changed the
frontier model's counterfactual choice. That distinction is why the corrected
trace is needed for the next run; another run is not needed to conclude that
the 326 were not garbage records.
