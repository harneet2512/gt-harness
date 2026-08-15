# GroundTruth final-stand repair

Date: 2026-08-12

## Verdict

The DeepSWE `1/1 attempted, 0/1 solved` diagnostic did not show that the graph
or hybrid retrieval substrate failed. It showed that the persistent execution
state was never validly bootstrapped and therefore never delivered. The
executor then ran for 121 calls and 14.15M tokens until context exhaustion.
The current corrective pass fixes that exact mechanism boundary and the
resource-amplifying defects surrounding it. It has not yet passed the current
source-built Linux release workflow and has not established an outcome win.

## Frozen failed witness

- Workflow: `31656913063`
- Task: `abs-module-cache-flags`
- Result: `ContextBudgetExhausted`, unsolved
- Executor calls/actions: 121 / 121
- Bootstrap calls: 1 attempted, invalid fallback
- Tokens: 14.15M
- Wall time: 888 seconds
- Graph: 754 nodes, 2,242 edges, 45 source files
- Dense backend: pinned Snowflake ONNX available
- Persistent deliveries: 0
- Preemptive deliveries: 13 from 85 opportunities
- Initial trajectory waste: four repository-location commands in `/root` and
  `/` before the already-resolved `/app` workspace was found

Current release replay passes graph substrate, dense backend, delivery timing,
contribution budget, preflight, decision sufficiency, diagnostic isolation,
project validation, and retrieval efficiency. It rejects only:

1. `persistent_bootstrap_not_selected`
2. `persistent_bootstrap_response_missing`
3. `persistent_bootstrap_transport_not_single_call`
4. `persistent_state_runtime_invalid`
5. `persistent_state_selection_not_applied`

## Repairs implemented

### One-call bootstrap

`MiniSweCentralAgent` now uses Mini-SWE's provider preparation and raw provider
query exactly once, with `num_retries=0`, temperature zero, bounded output, and
forced Bash tool selection. It parses the received response separately. A
format error cannot erase response usage, cost, provider identity, or the fact
that exactly one call occurred. The bootstrap Bash payload is data and is never
executed. The release gate rejects retry-wrapped transport.

### Correct entity authority

Exact-symbol certification no longer tokenizes the entire task prose. It uses
backticked entities, function-call syntax, uppercase identifiers, active
symbols, typed code-shaped action tokens, and exact diagnostic entities.
Lexical, BM25, and dense retrieval still receive ordinary task prose. This
keeps recall while preventing a common word such as `clear` from becoming an
exact certified symbol. The failed task now exposes `require` and
`ABS_MODULE_PATH` as task entities.

### Task-conditioned catalog

Hybrid-ranked evidence replaces equivalent generic graph items and receives
higher bounded priority. Generic anchors, callers, and structural items remain
available but cannot crowd task-ranked candidates out of the bootstrap's
visible catalog.

### Repeated deterministic state with bounded persistent context

The state engine runs at provider, preflight, postflight, and graph-rebase
boundaries. Every applicable executor request receives exactly one current slice:
initial/critical <=512 packing tokens, a material delta <=256, or a stable core
<=96. Source excerpts and new semantic claims are one-shot; the small core is
repeatable because it is not stored in durable model history. Claims become
exposed only after request-wide selection and provider dispatch. Same-revision
semantic no-ops preserve optional bootstrap focus, obligation order/status,
state version, and transition metrics.

### Provider transport and contribution integrity

A marker-write failure now stops before any provider transport. Executor timeout
is passed into the no-retry provider call and the host awaits thread completion,
so a late provider response cannot arrive after a contradictory final receipt.
The persistent state has highest request-wide contribution priority; large
diagnostic retrieval cannot consume its required core budget.

### Correct graph accounting

The receipt and release gate now report graph substrate health separately from
overall repository-intelligence mechanism validity. Bootstrap failure cannot
relabel a current healthy GraphDB as invalid.

### Workflow efficiency and final profile

DeepSWE now uses one exact bootstrap-shape/provider-route canary before the
matrix. The old per-task provider preflight was removed, eliminating up to 113
redundant paid calls on a complete v1.1 run. The default is `certified_full`:
ACTIVE integration, SHADOW preflight, deterministic compaction, completion
controller, progress controller, and adaptive validation timeouts.

### Shared workspace contract

The resolved task workspace is added to the normal Mini-SWE task message for
both GT-off and GT-on. This removes the observed filesystem-location detour
without treatment-only repository evidence. The receipt records
`resolved_workspace_v1`. An older baseline without that contract is not an
exact-prompt final control.

### Fail-closed final A/B contract

The DeepSWE gate now requires a proven GT-off baseline, ACTIVE `certified_full`
treatment, Mini-SWE 2.2.8/no-retry parity, exact prompt/tool hashes, and nonempty
observed model/provider/fingerprint identity. The one pre-matrix canary must have
nonzero token/cost/latency accounting, and its overhead is added to all-in token,
call, cost, and wall-time deltas. The workflow is explicitly a one-rollout,
bounded matched diagnostic, not a DeepSWE leaderboard-equivalent run.

## Verification completed

- Focused RED tests reproduced the entity, catalog, and repeated-state defects.
- Focused repaired tests pass.
- Widened hybrid/state/release/contribution/central/provider-view/delivery/runtime
  suites pass; the real ONNX test is skipped locally because its pinned asset is
  not provisioned.
- Ruff passes on every changed Python file.
- Python byte-compilation passes.
- DeepSWE workflow parses as YAML, contains one bootstrap canary, and contains
  exactly one explicit `litellm.completion` call.
- `git diff --check` passes.
- Archived failed artifact replay rejects only the five old-bootstrap defects.

## Release blockers

Local Windows cannot certify the graph runtime: the existing untracked
`vendor/gt-index-src/gt-index.exe` predates Objective-C support and Go is not
installed. Census, readiness, and pre-smoke correctly fail closed on
`registered parser languages missing from binary: objective_c`. Pre-smoke also
requires the exact pushed commit. Neither condition may be bypassed.

## Exact next order

1. Review and commit only the intended tracked repair files.
2. Push the repair.
3. Run the source-built Linux provider-free workflow; require every census
   line, `READY`, `SMOKE_APPROVED`, and `provider_calls: 0`.
4. Validate the frozen GT-off control against the new
   `resolved_workspace_v1` prompt contract. If it lacks the contract, it is not
   the final exact baseline and must not be called one.
5. Freeze a matched diagnostic manifest.
6. Run the one-task provider canary and then the bounded treatment diagnostic
   only after explicit paid authorization.
7. Audit outcomes first, then delivery/state/resource metrics. Any attributable
   loss, invalid delivery, new censor, or positive common-solved aggregate
   calls/actions/cost blocks benchmark expansion.

## Claims forbidden at this point

No solve uplift, positive flip, non-regression, efficiency win, causal model
use, or full-benchmark readiness has been proven by this corrective pass.
