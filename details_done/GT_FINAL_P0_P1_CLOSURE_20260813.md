# GroundTruth final P0/P1 closure — 2026-08-13

## Scope

This pass repaired only defects that invalidated the final benchmark or directly
broke the GroundTruth mechanism. It did not add a planner, retrieval generation,
benchmark-specific heuristic, or new product feature. The architectural target
remains the current host-owned `MiniSweCentralAgent`, shared hybrid retriever,
deterministic persistent execution state, SHADOW preflight, and current
postflight engine.

## Closed defects

| ID | Severity | Defect | Minimal repair | Biting proof |
|---|---|---|---|---|
| F-01 | P0 | Mini-SWE `model.query()` could retry `_query` up to ten times under one logical receipt | Live bootstrap and executor use one direct `_query`; scripted models retain a public-query fallback | direct model records one raw call and zero public calls |
| F-02 | P0 | LiteLLM provider identity disappeared during `model_dump()` | copy the non-secret hidden provider identity into the response receipt | hidden-provider witness reports `deepseek` |
| F-03 | P1 | Received empty/malformed choices lost usage, cost, and identity | account raw response before action parsing and emit typed parse failure | empty-choices bootstrap retains tokens/cost/provider |
| F-04 | P1 | Real `litellm.exceptions.Timeout` was not a built-in `TimeoutError` | one provider-timeout classifier covers both exact timeout families | LiteLLM timeout becomes `ModelTimeout` and censor receipt |
| F-05 | P1 | Request hash omitted effective call temperature, timeout, output limit, and tool choice | hash sanitized merged model/per-call arguments; keep message hash separate | same messages/different kwargs produce different request hashes |
| F-06 | P0 | Guidance/retrieval/frontier/progress could be confirmed before the durable marker | prepare before marker; commit all delivery/dedup state only after dispatch begins | forced marker failure yields zero deliveries and zero provider calls |
| F-07 | P0 | Delivery audit accepted a prepared or marker-failed request | require `invoked`, `response_received`, or `response_error` dispatch status | marker-error receipt is deterministically invalid |
| F-08 | P0 | Source edit opened obligations from the pre-edit graph | defer graph obligations until rebase; invalidate old open advisory obligations and recreate from current links | removed edge leaves no open stale advisory |
| F-09 | P0 | A later executed SHADOW batch action was dropped after the first action changed revision | rebind only actually executed non-first batch postflight to the current revision; ordinary stale action still rejects | edit then validation records current PASS; stale singleton remains unchanged |
| F-10 | P1 | Old catalog symbol/line labels could become newly visible after graph revision changed | catalog labels render only while catalog graph revision is current | changed graph DELTA contains no old label |
| F-11 | P0 | Repeated validation failure made persistent state disappear after the first CRITICAL dispatch | empty non-CORE frames fall back to bounded CORE | second unchanged failure request contains current state |
| F-12 | P1 | Stable CORE omitted the current focus | add bounded repeatable focus path without source excerpt/line label | CORE contains path and stays under the 96-token ceiling |
| F-13 | P1 | Same-revision graph refresh could reorder or reopen completed state | semantic comparison canonicalizes obligation order and preserves satisfied same-revision obligations | version/material-transition counters remain unchanged |
| F-14 | P1 | Failed initial five-channel retrieval could still spend the bootstrap model call | graph-first initialization fails closed before catalog/bootstrap when accepted retrieval is absent | forced retrieval timeout produces zero bootstrap calls |
| F-15 | P0 | Paid DeepSWE canary ran before current-source certification | reusable exact-SHA provider-free workflow is a dependency of the canary | workflow graph/order test |
| F-16 | P0 | Censored GT-off rows could manufacture positive flips | reject censoring symmetrically and exclude invalid rows from flip arithmetic | timed-out control is rejected and does not appear as a flip |
| F-17 | P1 | NaN/Inf/negative resources could pass comparison gates | require finite nonnegative numeric domains and strictly positive setup measurements | NaN/Inf witness fails closed |
| F-18 | P0 | Solve booleans were not certified by official verifier output | require a 0/1 verifier reward consistent with uncensored solved state | disagreement witness fails closed |
| F-19 | P1 | Mechanism-only profile could be paid then mislabeled as the full product | reject profile/claim-scope mismatch in plan job before provider work | workflow contract test |
| F-20 | P1 | No checked-in producer existed for the exact GT-off result schema | the same DeepSWE workflow now has a `gt_off` + `baseline` profile with all GT surfaces disabled | workflow producer/profile assertions |
| F-21 | P0 | Terminal-Bench 2.0 and 2.1 were conflated in final instructions | freeze TB2.0 as the Mini-SWE product diagnostic and explicitly deny TB2.1 leaderboard equivalence | AGENTS/CLAUDE/plan/readiness drift repair |

## DeepSWE control reality

The historical DeepSWE material in local Downloads is useful engineering
evidence, but it is not the exact current control artifact. The discovered
results are older DeepSeek-V4-Pro/official trajectory exports or unrelated
Terminal-Bench baselines; none is a
`gt.deepswe.central.evaluation.v1.1` Mini-SWE V4-Flash control with the
current prompt/tool/provider/runner/limit/fingerprint schema. Therefore:

1. an online artifact may be reused only if the workflow's strict precheck
   accepts it;
2. otherwise the new `arm=gt_off, comparison_profile=baseline` workflow path
   must produce the control;
3. GT-on is not compared against a historical or censored substitute.

## Verification performed

The release unit is **17 + 1**: all 17 registered feature producer/consumer
paths plus the separately gated persistent-execution-state mechanism. The live
mechanism gate requires graph-first creation, one accounted bootstrap, repeated
deterministic state use, bounded current-state delivery in applicable executor
requests, and exact dispatch/provider hashing; bootstrap-only evidence fails.

- Full `tests/test_gt_central_agent.py`: 122 passed, one skipped because the
  pinned ONNX asset is not installed locally.
- Persistent state + DeepSWE release gate + delivery audit: 66 passed.
- Broad provider-free test scope: all non-indexer tests passed after the final
  workflow assertion repair; two environment skips remain.
- Ruff passed on every changed runtime/gate/test file.
- Python compilation passed for changed Python modules.
- Both workflow YAML files parse.
- All embedded workflow Python heredocs compile.
- `git diff --check` passes (line-ending warnings only).

## Intentionally unresolved local gate

Six broad-suite tests call the shipped Windows `gt-index.exe`. They fail
because that binary predates current Objective-C registry coverage. The source
already contains the capability; the authoritative workflow builds the Go
indexer from current source on Linux. No local workaround, registry downgrade,
or gate weakening is allowed.

Consequently census, `READY`, and `SMOKE_APPROVED` are not claimed for this
working tree. They require an exact committed/pushed SHA and a green
`central_provider_free.yml` run. No paid canary or benchmark is authorized
before that result.

## Benchmark protocol boundary

- DeepSWE: v1.1 catalog-compatible 113-task pin, Pier 0.3.1, Mini-SWE central
  adapter, one rollout, 300 calls, 5,400-second agent budget. This is a matched
  product experiment and is explicitly not official leaderboard equivalence.
- Terminal-Bench: 2.0 is the next frozen Mini-SWE product diagnostic. A 2.0
  result is never relabeled as 2.1.
- `persistent_state_only`: mechanism diagnostic.
- `certified_full`: integrated GT-on product treatment.
- `baseline`: GT-off control produced by the same workflow.
- Primary truth: uncensored official verifier outcome. Resources are evaluated
  only with complete finite accounting and outcome preservation.

## Exact next action

Commit only the intended final-stand files, push the exact SHA, dispatch the
provider-free workflow, and require its source-built index, real pinned ONNX,
census, readiness, pre-smoke, static, and zero-provider receipt gates to pass.
If it passes, inspect the existing DeepSWE-off artifact with the strict baseline
precheck. Produce a new GT-off control only if that precheck rejects it. Do not
start GT-on or any paid smoke before both conditions are green.

## Live diagnostic provider boundary (2026-08-13)

Exact-SHA provider-free run `31668867798` passed at `c9b6831`, including the
source-built Objective-C-capable indexer, pinned Snowflake ONNX asset, 17/17
feature-path proof, persistent-state structural proof, `READY`,
`SMOKE_APPROVED`, and `provider_calls: 0`. The first bounded live attempt was
then stopped by its canary because the configured OpenRouter credential returned
HTTP 401; no DeepSWE task ran. This is external route failure, not live GT
evidence.

TokenRouter is permitted only for the bounded diagnostic continuation. Its
authenticated catalog was checked for the exact
`deepseek/deepseek-v4-flash-0731` identifier before adding the route. The
workflow must fail before completion if that exact ID disappears. TokenRouter
results cannot be compared with, merged into, or relabeled as the frozen
OpenRouter A/B contract.

The first TokenRouter canary, workflow `31669920913` at `3b3002a`, proved the
credential and exact catalog route but failed before any DeepSWE task with the
provider's typed 400 error: a required/specific `tool_choice` is unsupported in
DeepSeek thinking mode. This was not a GT outcome. The repaired diagnostic route
sets `thinking.type=disabled` in both the standalone canary and the live
`MiniSweCentralAgent` model configuration, preserving the forced Bash contract.
The canary records that mode and the task merge rejects any route that does not.

The live accounting unit is now executable rather than documentation-only.
`central_receipt.json.product_mechanism_census` records the exact 17 historical
feature IDs plus the persistent-state mechanism for a product count of 18. The
central release gate and DeepSWE merge reject a treatment unless all 18 are
configured and persistent state has repeated lifecycle use. Natural feature
fires remain a separate N/17 trajectory observation; absent events cannot be
fabricated to inflate that number.

Exact-SHA workflow `31671479023` at `2a34fb2` then passed the source-built
provider-free gate and the repaired one-call TokenRouter canary. The live task
receipt proved the mechanism path before the provider censored the trajectory:

- 18/18 product mechanisms configured;
- 5/17 legacy features naturally fired and all five were consumed;
- persistent execution state was applicable, initialized, and repeatedly used
  across 29 lifecycle operations: one bootstrap, ten context compilations,
  nine preflight projections, and nine postflight commits;
- persistent state reached all ten attempted executor requests (one 159-token
  initial frame and nine 43--47-token bounded current slices);
- the authoritative trajectory audit certified 12/12 visible deliveries and
  34 claims with zero duplicates, zero late deliveries, zero predictive
  deliveries, and zero grounding/hash/timing failures;
- the current graph and pinned Snowflake ONNX backend were healthy.

The task is **not outcome evidence**. TokenRouter returned nine executor
responses and then rejected the next physical request with HTTP 429,
`Maximum 10 requests within 1 minutes`. The row is a provider-censored
diagnostic (`RateLimitError`), not an unsolved GT treatment and not a solve or
efficiency comparison. No retry or additional model call was hidden. A
complete live outcome check requires either a provider route without this
quota or an explicitly accounted host pacing policy; changing that transport
policy must not be presented as a GroundTruth mechanism improvement.
