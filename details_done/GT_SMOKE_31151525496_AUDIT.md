# GT-on smoke 31151525496 — complete audit

Run `31151525496` was dispatched through GitHub Actions on commit
`8002002d33b0ec8c6b1ef8b86b4c5e874706edcd`, using the treatment arm with
`all17`, `integration_mode=active`, `preflight_mode=shadow`, deterministic
context compaction, completion control, progress control, DeepSeek V4 Flash,
temperature 1.0, and Harbor timeout multiplier 1.0. Artifacts are archived at
`D:\gt_runs\31151525496`.

## Release result

The workflow and all ten task jobs completed. Every trial was graded and
uncensored. The treatment solved 9/10, exactly matching the frozen GT-off
baseline; `gpt2-codegolf` was the only unsolved task in both arms. This is
outcome preservation, not proof that every task is efficient or that GT caused
the result.

The provider-free replay and regression-preservation replay both passed for
all ten trajectories.

## Matched per-task deltas

Token totals are the frozen baseline prompt+completion columns versus the
treatment deep-metrics `total_tokens`. Calls and assistant steps are from the
same archived trajectories. Baseline action counts are counted from the
archived GT-off trajectories; treatment actions are receipt metrics. Deltas
are treatment minus baseline.

| Task | Outcome | Tokens GT-off → GT-on | Δ tokens | Calls Δ | Steps Δ | Actions Δ |
|---|---|---:|---:|---:|---:|---:|
| fix-code-vulnerability | 1 → 1 | 888,683 → 309,001 | -579,682 | -9 | -9 | -9 |
| portfolio-optimization | 1 → 1 | 861,659 → 397,398 | -464,261 | 0 | 0 | 0 |
| modernize-scientific-stack | 1 → 1 | 76,211 → 46,212 | -29,999 | 0 | 0 | -5 |
| headless-terminal | 1 → 1 | 9,794,473 → 2,678,316 | -7,116,157 | -26 | -27 | -27 |
| llm-inference-batching-scheduler | 1 → 1 | 5,961,364 → 1,679,226 | -4,282,138 | -6 | -6 | -6 |
| break-filter-js-from-html | 1 → 1 | 326,564 → 501,405 | **+174,841** | **+15** | **+15** | **+14** |
| write-compressor | 1 → 1 | 1,901,261 → 1,703,440 | -197,821 | **+15** | **+15** | **+14** |
| gpt2-codegolf | 0 → 0 | 17,404,358 → 1,711,899 | -15,692,459 | -15 | -16 | -9 |
| schemelike-metacircular-eval | 1 → 1 | 16,942,703 → 8,210,495 | -8,732,208 | 0 | 0 | -14 |
| cobol-modernization | 1 → 1 | 2,852,517 → 1,207,645 | -1,644,872 | 0 | 0 | -13 |

All ten tasks: 57,009,793 → 18,445,037 tokens (Δ -38,564,756), 420 →
394 calls (Δ -26), 420 → 392 assistant steps (Δ -28), and 483 → 428 model
actions (Δ -55). The fair common solved-set comparison excludes the common
unsolved `gpt2-codegolf`: 39,605,435 → 16,733,138 tokens (Δ -22,872,297,
-57.75%), 361 → 350 calls (Δ -11), 361 → 349 steps (Δ -12), and 424 → 378
actions (Δ -46).

The aggregate is materially better, but the treatment is not yet a strict
per-task Pareto win: `break-filter-js-from-html` regressed on every measured
resource, and `write-compressor` used 15 more calls/steps and 14 more model
actions despite using fewer tokens. The 89-task run remains blocked by this
per-task regression gate.

## Feature and effect accounting

All ten receipts reported all 17 features enabled and a healthy sensor. Across
the stochastic trajectory, 12 feature IDs fired naturally:

`GT_CERT_DELIVERY`, `GT_CHANGE_SURFACE`, `GT_EDIT_CHECK`, `GT_LOC_RESLOT`,
`GT_PATCH_DELTA`, `caller_contract`, `def_partition`, `localization`,
`newfile_precedent`, `obligations`, `signature_delta`, and `syntax_result`.

The five absent exact triggers were `GT_HYPOTHESIS`, `GT_SS_SUBMIT_RED`,
`covering_red`, `recovery`, and `submit_refusal`. They were not fabricated on
unrelated actions. Applicability accounting recorded 71
`fired_when_eligible`, 16 `correct_abstention`, 81 `trigger_absent`, two
`substrate_unavailable` rows (the repository index timed out for
`gpt2-codegolf` caller/definition features), zero missed triggers, and zero
false fires.

The engine produced 270 effects and applied all 270. The provenance
dispositions were:

| Disposition | Count | Meaning |
|---|---:|---|
| `engine_internal_state` | 193 | deterministic controller state/validation/lifecycle work |
| `existing_engine_actuation` | 7 | an existing controller actuator consumed the effect |
| `provider_payload` | 5 | grounded model-visible delivery |
| `audit_only` | 65 | receipt recorded, with no downstream consumer exercised |

No effect was silently dropped or labeled unused. The 65 audit-only effects
are not evidence of trajectory influence; the 193 internal effects prove GT
work but not model causality. Only the seven existing actuations and five
provider deliveries have an explicit downstream consequence in this run.

## Delivery correctness and timing

There were five provider payload deliveries: two `newfile_precedent`, one
`signature_delta`, and two `GT_EDIT_CHECK`. Their effect traces had concrete
source/workspace revisions, evidence actions, delivery IDs, and request
hashes. Four payloads were semantically clean. The `signature_delta` payload
was not fully clean: its frame named the real `headless_terminal.py` change,
but also included `__pycache__` and
`__pycache__/headless_terminal.cpython-313.pyc` in `changed_paths`/anchors.
Those are derived artifacts and must not be presented as authored source.
Thus the strict grounded-content count is **4/5**, even though all five were
delivered at the correct lifecycle boundary. The likely code path is
`gt_engine/central_runtime.py:3265`, which serializes every
`transition.changed_paths`; the grounding predicate at line 454 checks only
that required fields are non-empty and does not reject derived paths.

All five were delivered in the first eligible
provider request; late deliveries = 0 and predictive deliveries = 0. Provider
request hash coverage was 100% for every task. No preflight command was
returned to the model: all 428 shadow preflight decisions were `PASS`, with
zero material evidence, rewrites, suppressions, duplicate evidence, false
interventions, or stale-batch barriers.

This proves correct non-intervening delivery and timing. It does not prove
that a temperature-1 model used the five payloads; that requires a matched
ablation or replay comparison.

## Model-utilization audit

The receipts support two different utilization measurements:

1. **Provider consumption:** all five payloads had non-zero runtime-advisory
   characters in the exact first-eligible request, with a request hash and a
   subsequent model query. This proves the model received the payload.
2. **Behavioral utilization proxy:** the runtime's strict immediate-next-action
   check found an anchor in the first command after delivery for only
   `schemelike-metacircular-eval` (1/5). The other four next commands were
   different actions. A broader deep metric finds an anchor in *some later*
   command for all five deliveries (5/5), but that can be coincidental and is
   not causal proof.

| Task / feature | Delivery call | Advisory chars | Immediate next action | Any later anchor |
|---|---:|---:|---|---|
| headless-terminal / `newfile_precedent` | 9 | 103 | no | yes |
| headless-terminal / `signature_delta` | 20 | 311 | no | yes |
| portfolio-optimization / `GT_EDIT_CHECK` | 18 | 130 | no | yes |
| schemelike-metacircular-eval / `GT_EDIT_CHECK` | 60 | 135 | yes | yes |
| write-compressor / `newfile_precedent` | 10 | 87 | no | yes |

Therefore the precise conclusion is: **5/5 were delivered to and consumed by
the provider request, but only 4/5 were strictly clean payloads; 1/5
immediately changed the next action's anchor, and 5/5 eventually had an
anchor appear in a later action.** There is no receipt field that can establish
hidden model acknowledgement or prove that GT caused those later actions. A
matched no-delivery/shadow ablation is required for that causal claim.

## Context and engine metrics

- Context facts: 6,448 candidates and 6,448 accounted; unaccounted effects = 0.
- Deterministic compaction: 234 provider-view changes and 230 state-frame calls;
  9,716 old tool-result clearances; 19,913,499 characters elided.
- Unique Mini-SWE reasoning removed: 0. Exact duplicate-turn removal:
  0. No distinct reasoning was deleted.
- GT context added: 61,435 characters total (766 guidance + 60,669 bounded
  state-frame text).
- Completion controller: 24 private probes, 19 cache hits, one auto-submit.
- Semantic progress classifications: 297 no-gain, 63 localization gains, 61
  patch attempts, and 7 validation gains.

## Decision

`31151525496` is a valid, outcome-preserving, aggregate-efficiency-positive
diagnostic witness. It is not a strict no-regression proof because two solved
tasks became more expensive in calls/steps/actions. Before dispatching 89
tasks, isolate those two trajectories with matched ablations (postflight-only,
shadow preflight, compaction-only, and completion/progress control) and require
the per-task Pareto gate to pass without sacrificing the 9/10 uncensored
outcome.
