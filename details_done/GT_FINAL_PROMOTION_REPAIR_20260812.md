# GT final promotion repair — 2026-08-12

## Status

Implementation is complete and the focused Python integration suite is green.
No paid smoke was launched. The local Windows census remains blocked by the
known stale `gt-index.exe`, which lacks `objective_c`; the authoritative Linux
provider-free workflow rebuilt the current indexer and passed on runtime commit
`80a8376` in run `31616184187`.

## Reproduced failure evidence

The authoritative DeepSWE diagnostic was workflow `31575925244`:

- outcome: GT-on 1/10; the frozen local baseline was 0/10;
- aggregate model tokens increased 31.91%; this is not an efficiency win;
- 1,145 preemptive retrieval calls produced 53 deliveries;
- retrieval consumed about 1,803 seconds in aggregate;
- 740 calls selected evidence and only afterward discarded it because the
  12,000-character task budget was already exhausted;
- 237 of 1,042 hashed states repeated and were cacheable;
- 34 of 186 selected facts were semantic repeats under
  path/span/symbol/relation/content identity;
- all ten jobs disabled context compaction, completion control, progress
  control, and adaptive validation timeout;
- 10 action returns were marked eligible in SHADOW, including an awilix import
  neighbor whose selected test span was unrelated and eight arktype repetitions
  of the same helper across graph rebuilds;
- adaptix reached 935,972 provider characters and KaTeX reached 943,746;
  KaTeX crossed the configured conservative provider limit without compaction.

Direct GT text was not the primary token cause. The run added roughly 128,000
provider characters over 1,144 calls, while the token increase came from longer
trajectories. The controllable causes were incorrect structural span identity,
late budget checks, repeated retrieval, disabled lifecycle controls, and
incomplete opportunity accounting.

## Implemented corrections

### Semantic and decision identity

`RetrievalCandidate.content_claim_id` hashes only path, complete span, symbol,
relation, and normalized source text. `claim_hash` remains an alias for
compatibility. Source revisions, graph row IDs, support channels, and delivery
metadata no longer create false novelty. `DecisionEvidenceClaim` adds a
separate operation/target/support-bound `decision_claim_id`.

### Edge-aligned structural payloads

`StructuralLink` now carries source and target symbol/start-line endpoints from
GraphDB. Structural retrieval stores every document span per file and selects
the exact linked endpoint. If the endpoint is absent or the bounded corpus is
incomplete, the fallback is marked `edge_endpoint_unresolved`; it can rank but
cannot certify provider delivery or action return.

RRF retains each channel's representative. Delivery selects the candidate that
owns its certificate. An exact-path hit can no longer certify unrelated
structural text from the same file. Generic imports remain available to rank or
deliver when their exact endpoint is present, but cannot authorize pre-action
return. Co-change remains rank-only.

### Budget-first, cached, priority-aware retrieval

- token budget zero or selection limit zero returns before all channels;
- a closed task/opportunity budget returns before repository/dense work;
- a positive partial character budget is passed into evidence packing, so a
  complete span that cannot fit is never marked selected and discarded later
  by the host;
- a bounded 128-entry session cache keys query hash, provider-visible claim
  set, retrieval configuration, and remaining character budget;
- cached channel receipts have zero current-call latency and explicit
  `cache_replay` provenance;
- up to 3,000 characters (25% of the default 12,000) are reserved for
  post-mutation, diagnostic, and validation opportunities;
- task-start and read/search traffic cannot consume that reserve.

### Opportunity accounting

Every provider boundary records opportunity kind, candidates generated,
evidence selected, delivered/model-visible status, abstention reasons, cache
status, and per-channel/total latency. This provides a complete denominator
without claiming that internal receipts or visible text caused a solve.

### Outcome-preservation controls and release gate

The DeepSWE workflow now enables bounded provider-budget compaction, fail-open
completion control, semantic progress control, and adaptive validation
timeouts. It accepts a bounded `task_count`, so the same task-agnostic workflow
can run a 10-task diagnostic, 20-task promotion cohort, or up to the full 113.

The release gate now rejects disabled controls, retrieval after budget closure,
missing opportunity accounting, cache hits with fresh channel latency,
duplicate delivered claims, missing content/decision identity, and structural
decision evidence with a non-material relation or unresolved endpoint.

### Missing wall-time metric

`metrics.wall_time_sec` now aliases the authoritative monotonic elapsed time,
so merged benchmark artifacts no longer publish null wall time.

## Verification

Focused verification:

```text
python -m pytest tests/test_hybrid_retrieval.py tests/test_hybrid_repository.py tests/test_decision_sufficiency.py tests/test_central_release_gate.py tests/test_gt_central_agent.py tests/test_live_retrieval_profile.py -q
```

Result after the final character-budget repair: 202 selected tests passed; one
real-Snowflake test skipped because the pinned model asset is not installed
locally. Ruff lint, Python compilation, and `git diff --check` pass.

The shared-retriever compatibility suite also passed all 45 ARB adapter,
checkout-runner, extraction, evaluation, aggregation, and live-profile tests.
The DeepSWE workflow parsed successfully as YAML after its bounded 10/20/113
task-count generalization.

The repository-wide suite collected 1,422 tests. It finished with 1,411 pass,
five platform/asset skips, and six failures. All six failures were reproduced
individually and terminate at the same native substrate error:

```text
RuntimeError: registered parser languages missing from binary: objective_c
```

There was no second Python runtime, retrieval, delivery, or workflow failure.
The full suite is therefore not reported as green; the authoritative Linux
provider-free build remains mandatory.

New biting witnesses cover graph-rebuild identity, zero-work closed budgets,
runtime caching, diagnostic budget reservation, exact graph endpoint spans,
unresolved endpoint abstention, certificate/representative alignment, generic
import decision rejection, and fail-closed workflow/retrieval gates.

Local exact gates were attempted and failed, correctly, at the native boundary:

```text
RuntimeError: registered parser languages missing from binary: objective_c
```

No readiness claim is made from Python tests alone.

## Provider-free Linux certification

Workflow `31616184187` passed on exact runtime commit `80a8376`. It proved the
current-source native graph build, the pinned Snowflake ONNX asset, repository
substrate, all advertised language fixtures, central runtime tests, structural
readiness, exact pre-smoke approval, and static checks. Its uploaded receipt is
`gt.central.provider-free.v1` and records `provider_calls: 0`.

The log contains every required literal, including all 17 producer/consumer
proof lines, grounded payloads, first-eligible visibility, context accounting,
`NO_ACTIONS_BLOCKED`, `READY`, and `SMOKE_APPROVED`. The earlier dispatch
`31615759833` failed at checkout because a seven-character SHA was interpreted
as a branch/tag. Run `31615908442` used the full SHA and passed all substantive
checks but intentionally failed exact-commit parity because checkout was in a
detached HEAD. Neither failed dispatch called a provider. The accepted run used
the pushed branch, whose HEAD was exactly `80a8376`.

## Archived-run release-gate replay

The new gate was applied to all ten raw treatment receipts from workflow
`31575925244`. Every task was rejected. Aggregated failures were:

- 10/10 context-compaction controls disabled;
- 10/10 completion controllers disabled;
- 10/10 progress controls disabled;
- 10/10 adaptive validation timeouts disabled;
- 1,145/1,145 retrieval decisions missing typed opportunity identity;
- 740 retrieval decisions performing channel work after the character budget
  was already exhausted;
- 10/10 task receipts missing valid opportunity-accounting aggregates; and
- 10 return-eligible claims missing the new decision identity, using a
  non-material structural relation, and lacking an exact edge endpoint.

This replay proves that the gate detects the observed failure modes. It does
not retroactively repair or validate the archived outcome run.

## Remaining execution order

1. Do not run a paid task if the new release gate fails any receipt.
2. With explicit authorization, run a 10-task GT-on diagnostic in SHADOW and
   compare it to the existing frozen local baseline.
3. Require zero new uncensored solve loss, zero invalid payloads, zero late or
   predictive deliveries, zero duplicate claims, zero post-budget retrieval,
   valid opportunity accounting, and no aggregate regression across calls,
   effective actions, tokens, or wall time.
4. If that passes, run a 20-task mixed promotion cohort selected from previously
   solved and failed baseline cases without repository-specific heuristics.
5. Freeze the commit before any full 113-task DeepSWE or 89-task
   Terminal-Bench evaluation.

The repair proves implementation integrity locally. It does not yet prove more
flips, solve uplift, or non-regression; those remain paid outcome gates.
