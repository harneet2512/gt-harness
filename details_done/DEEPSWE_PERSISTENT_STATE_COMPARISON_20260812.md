# DeepSWE persistent-state comparison gate

Date: 2026-08-12

## Status

The new treatment did not execute a task. Workflow `31650276972` checked out
GT commit `e9bd64e660ceb714458262f52f88b6f8f1ad3d72`, passed planning, and
failed during OpenRouter provider preflight with:

```text
No endpoints available matching your guardrail restrictions and data policy.
Configure: https://openrouter.ai/settings/privacy
```

The merged artifact records `rows=[]`, `missing=["abs-module-cache-flags"]`,
and `solved=0/1`. It is an infrastructure-blocked run, not a GT outcome and
must not be compared as a 0/1 treatment result.

## Previous valid treatment witness

Workflow `31575925244` (GT commit
`61c4a38049504102653b8e7757c60149c8617322`) completed the same ten DeepSWE
task IDs with the pre-persistent-state controller. It solved `1/10` and
reported:

| Metric | Previous total |
| --- | ---: |
| Provider calls | 1,144 |
| Assistant steps | 1,136 |
| Effective actions | 2,723 |
| Shell actions | 1,187 |
| Total tokens | 90,631,272 |
| GT context characters | 121,201 |
| Visible GT deliveries | 71 |
| Solved | 1/10 |

The previous config contains no `persistent_execution_state` flag, so it remains
a useful engineering witness. It is not automatically accepted as the final
control. The fail-closed release gate must prove from its manifest and raw rows
that it is GT-off and matches the current prompt/tool/model/provider/runner/budget
contract. Missing evidence causes rejection; it does not require a rerun if the
existing raw artifact can supply that proof.

## Metrics that must be compared after a valid run

The first comparison is outcome-preserving, not token-only:

1. solved, gained, lost, both-pass, both-fail;
2. outer exceptions versus clean verifier outcomes;
3. provider calls, assistant steps, effective actions, shell actions;
4. total/input/output tokens, provider cost, wall time;
5. GT context characters, visible deliveries, first-eligible/late/predictive
   delivery counts and semantic-certificate failures;
6. persistent-state initialization, bootstrap calls/tokens/latency,
   context compilations, preflight projections, postflight commits, graph
   rebases, material transitions, and state-frame deliveries;
7. per-task deltas and first trajectory divergence for every gain/loss.

No efficiency win is accepted unless outcome is preserved and the aggregate
resource direction improves without a new timeout or exception. No causal
claim is accepted from the old run because its provider alias was not pinned
to the current 0731 model route.

## Provider correction already applied

The route now explicitly uses `deepseek/deepseek-v4-flash-0731`, whose current
model metadata advertises `temperature`, and records the explicit
`data_collection=allow` policy. The provider-free certification passed at
workflow `31650136061`. The account-level OpenRouter guardrail still rejects
the route, so the next action is account configuration, not GT code tuning.

## Release gate

Do not launch the ten-task comparison until a one-task provider preflight
produces a route proof for the exact 0731 model and the first task artifact
contains a provider query. After that proof, rerun the same ten tasks and
populate the metric table above. Until then, the only defensible comparison is
the previous witness plus this blocked-run diagnosis.

## Superseding live diagnostic and final repair

Workflow `31656913063` did execute `abs-module-cache-flags`, but it is rejected
as outcome evidence: bootstrap returned `error_fallback`, persistent state
delivered zero frames, and the run exhausted context after 121 executor calls
and 14.15M tokens. Its current release replay proves that GraphDB, the pinned
dense backend, timing/accounting, preflight, decision sufficiency, validation,
and retrieval-efficiency gates were healthy. Only the old bootstrap/lifecycle
contract fails.

The corrective working tree implements direct one-call/no-retry bootstrap,
syntax-marked entity authority, task-ranked catalog packing, tiered persistent
delivery (initial/critical, material delta, or stable core), semantic no-op
stability, marker/timeout accounting, independent graph-substrate accounting,
one pre-matrix provider canary, full-profile defaults, and the shared
`resolved_workspace_v1` prompt contract. The matched workflow records
`leaderboard_equivalent=false` and attributes results only to the integrated GT
product. Full details and the exact remaining release order are in
`details_done/GT_FINAL_STAND_REPAIR_20260812.md`.

This working tree has not yet passed the source-built Linux provider-free gate
and has no new outcome result.
