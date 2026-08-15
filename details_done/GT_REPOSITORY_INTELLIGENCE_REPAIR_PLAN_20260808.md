# Repository-Intelligence Repair Plan

Date: 2026-08-08  
Scope: repair the rejected treatment smoke `31279355854`; do not start the
89-task run or another paid smoke until the provider-free gates pass.

## Confirmed current defects

### 1. Recovered frontier failure poisons final status

`eval/gt_central_agent.py` builds `intelligence_failures` at task completion
and currently promotes every historical frontier decision with disposition
`substrate_failure` or `stale_source_revision` into the final treatment
status. In the rejected smoke, `write-compressor` had one temporary
sensor-degraded call, then recovered to a healthy current graph and emitted
`no_frontier`. The historical failure remained in the final receipt, so a
healthy final substrate was reported as failed.

Required semantics:

- retain every transient failure in a separate receipt field for diagnosis;
- evaluate promotion against the final repository evidence and the latest
  frontier decision for the current source revision;
- a final current `substrate_failure` or `stale_source_revision` remains
  fail-closed;
- a recovered transient is never silently discarded, but does not invalidate
  the task;
- fact/accounting, duplicate-claim, stale-delivery, and budget failures remain
  fail-closed regardless of later recovery.

### 2. Source-less task classification is ambiguous

`gpt2-codegolf` mirrors zero supported source files. The graph engine correctly
returns `no_supported_source`, zero nodes/edges, and no fabricated payload, but
the treatment gate reports the same shape as a broken graph substrate.

Required semantics:

- add an explicit `not_applicable_no_supported_source` classification at the
  task/report boundary;
- preserve the raw graph failure and zero-payload receipt;
- never mark the task as a green GT success or claim graph coverage;
- make the promotion policy explicit: source-less tasks are excluded from the
  repository-intelligence denominator, while their verifier outcome remains
  in the outcome denominator;
- an expected source task with an empty mirror remains a hard failure.

## Minimal implementation

1. Add a small helper in `eval/gt_central_agent.py` that partitions frontier
   decisions into current-final failures and transient recovered failures by
   source revision and decision order.
2. Add receipt/metric fields:
   - `repository_frontier_transient_failures`;
   - `repository_frontier_current_failures`;
   - `repository_intelligence_applicability`;
   - `repository_intelligence_denominator_excluded`.
3. Update final status computation to use only current failures plus existing
   hard integrity checks. Preserve the full decision log unchanged.
4. Add `classify_repository_applicability(...)` in the repository-intelligence
   module. It must distinguish `source_backed`, `not_applicable_no_supported_source`,
   and `substrate_failure` using the source mirror receipt and final evidence.
5. Update merge/deep-metrics acceptance logic to exclude only explicit
   not-applicable source-less rows from the intelligence denominator. Do not
   convert them into solved tasks.

## Tests to add or update

1. Healthy graph → degraded frontier call → recovered healthy/no-frontier:
   final intelligence status is `passed`; transient failure is receipted.
2. Degraded frontier as the final current decision: status remains `failed`.
3. Stale current decision after source revision change: status remains
   `failed`; no stale payload is delivered.
4. Source-less mirror: classification is
   `not_applicable_no_supported_source`, no payload is fabricated, and the
   outcome denominator is unchanged.
5. Empty retrieval on a healthy current graph remains a valid abstention.
6. A source-backed empty/invalid graph remains a hard failure.
7. Existing fact accounting, duplicate claim, budget, and request-hash gates
   remain fail-closed.
8. Deep metrics and merge output expose both denominators and never report an
   invalid treatment as an efficiency win.

## Validation gates

Provider-free, in order:

```text
python -m pytest -q tests/test_gt_central_agent.py tests/test_gt_repository_intelligence.py tests/test_gt_deep_metrics.py
python -m scripts.central_feature_census
python scripts/central_readiness_audit.py
python scripts/central_pre_smoke_gate.py
```

Then replay the archived `31279355854` receipts and the prior scheme loss.
The replay must show:

- `write-compressor`: healthy final intelligence, transient failure retained;
- `gpt2-codegolf`: explicit not-applicable classification, zero fabricated
  graph facts;
- `schemelike-metacircular-eval`: unchanged causal status until an ablation
  proves otherwise;
- no late, predictive, duplicate, stale, or unaccounted delivery.

Only after these gates pass may a separately authorized matched 10-task smoke
run. The 89-task run remains blocked until outcome preservation and repeated
efficiency gates pass.

## Validation performed before implementation

The current focused suite ran 73 tests: 70 passed and 3 failed. The failures
are local runtime-fixture failures, not the two target defects:

- the dirty local `vendor/gt-index-src/graph.db` lacks certified directed
  edges for the full parser-language matrix;
- two repository tests report `graph_source_coverage_incomplete` from that
  stale local fixture.

The GitHub provider-free gate for the smoke commit passed the certified index
runtime. The local untracked graph/binary artifacts must not be staged or used
as certification evidence. The implementation must therefore validate against
the vendored/certified runtime in CI as well as focused unit tests.

## Implementation and validation result

Implemented in commits `544dd93` and `5042a3a`, pushed to `inline-engine`.

- Recovered refresh/frontier failures are retained under
  `repository_intelligence.transient_failures` but no longer poison a healthy
  final source revision.
- Current final failures remain fail-closed.
- Initial graph-gate failures are now compared with the final graph revision;
  recovered initial failures are retained as transient `graph_gate:*` events.
- Source-less tasks report
  `not_applicable_no_supported_source` and are excluded only from the
  repository-intelligence invalidity denominator; they remain in outcome
  results and never receive fabricated graph facts.
- Deep metrics now expose applicability, denominator exclusion, and transient
  failures.
- Focused tests and lint pass.
- Archived replay of all ten `31279355854` trajectories returned `REPLAY_OK`.
- GitHub provider-free certification `31281512977` passed the certified index,
  all feature gates, readiness audit, and exact pre-smoke gate (`READY`,
  `SMOKE_APPROVED`).

The local census still fails against the untracked stale graph fixture, but
the certified CI runtime passes the complete language/index contract. No paid
rerun or 89-task run has been started after this repair.

## Non-goals and rollback

- Do not fabricate graph facts for source-less tasks.
- Do not weaken graph validation for source-backed repositories.
- Do not change model prompts, context budgets, or feature trigger timing in
  this repair.
- Do not enable assistive preflight or start the 89-task run.
- Rollback is a single host integration commit; receipt schema additions are
  additive and can be ignored by older readers.
