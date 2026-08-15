# GT Effect Provenance Implementation — 2026-08-04

## Purpose

The existing `effects_applied` counter proved state writes, not downstream
use. This change adds an additive provenance ledger so each effect can be
classified without changing GT routing, prompts, timing, action order, shadow
behavior, or submission behavior.

## Implementation

- `CentralFeatureRuntime` now records `effect_trace` rows for every applied
  effect.
- Each row has a stable effect ID, evidence/application timing, state section,
  existing state reads, actuator events, provider delivery IDs, and a terminal
  disposition.
- Confirmed model guidance is linked back to the exact contributing effect IDs.
- The existing `caller_contract` read used by signature-delta generation is
  recorded as an existing engine actuation.
- `central_receipt.json` keeps all prior fields and adds the trace under
  `features.effect_trace`.
- `scripts/central_effect_audit.py` validates terminal dispositions without
  changing runtime behavior.

## Disposition semantics

`provider_payload` requires a confirmed provider request.  
`existing_engine_actuation` requires a recorded existing consumer read.  
`engine_internal_state` records producer-side GT control work such as source
revision tracking, validation-debt updates, failure-state latching, lifecycle
transitions, and trigger selection.  
`audit_only` means the effect was applied but no downstream consumer was
observed; it is not counted as trajectory influence.  Unknown dispositions are
not permitted by the proof tests.

## Verification

Passed:

- `python -m pytest tests/test_gt_central_runtime.py tests/test_gt_central_consumer_proof.py tests/test_gt_central_agent.py -q`
- `python -m pytest tests/test_gt_central_consumer_proof.py -q`
- `python scripts/central_feature_census.py`
- `python -m pytest tests/test_central_effect_audit.py -q`

The provider-free census reported all existing all-17, timing, payload,
consumer, and no-action-blocked gates. Its trace contained provider payload,
existing engine actuation, and audit-only dispositions, demonstrating that the
new ledger distinguishes those cases.

The full repository suite exceeded the 120-second command limit without
returning a test failure; it must be rerun with a longer timeout before a
release claim.

No paid smoke was started by this change.

## Fresh paid smoke after instrumentation

- Workflow: `30947423816`
- Commit: `fdde1c5`
- Arm: GT-on treatment, all17
- Verifier reward: 9/10
- Clean submitted trajectories: 8/10
- Censored trajectory: `schemelike-metacircular-eval` (`WallTimeExceeded`)
- Non-solved trajectory: `gpt2-codegolf` (reward 0)

The new provenance audit found 354 effects applied, 28 provider-contributing
effects, 326 audit-only effects, and zero unknown dispositions. All 28 provider
deliveries were first-eligible, non-late, and non-predictive. No existing
private-state consumer read occurred in this live smoke.

Against the frozen ten-task GT-off aggregate already documented in the report:

| Metric | GT-on | GT-off | Delta |
|---|---:|---:|---:|
| total tokens | 15,122,509 | 29,223,016 | -14,100,507 |
| API calls | 324 | 420 | -96 |
| assistant steps | 323 | 420 | -97 |
| actions | 359 | 483 | -124 |
| context characters | 15,152,500 | 30,874,834 | -15,722,334 |

This is an efficiency signal, not yet a causal proof that audit-only effects
helped. The trace proves they were not provider or existing-engine actuations
in this run; the 89-task run remains blocked pending repeated matched trials
and a decision on missing private consumers.
