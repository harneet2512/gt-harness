# GT top-down regression finalization

Date: 2026-08-08

Branch: `inline-engine`

Status: **PROVIDER_FREE_CERTIFIED / OUTCOME_UNVERIFIED**. The checked-out Linux
index binary and deterministic integration passed on exact commit `e6ce41f`;
promotion still requires a separately authorized matched paid smoke.

## Release decision

The latest paid run, workflow `31234520516`, is rejected evidence. It solved
7/10 versus the frozen GT-off baseline's 9/10. The loss was not caused by four
missing feature triggers. The dominant failure was architectural: a controller
substrate error terminated three tasks before the model could run. Two of those
tasks, `cobol-modernization` and `write-compressor`, were frozen-baseline solves.

This repair removes that failure class and addresses the other deterministic
resource regressions found in the same run. It does **not** prove that the
temperature-1 model will never produce a different outcome. Deterministic GT
can guarantee its own ordering, evidence, fallback, and provider view. It
cannot guarantee a stochastic model's solve without replacing or constraining
the model's decisions. The release gate therefore requires outcome preservation
as measured evidence, not as an assertion inferred from deterministic code.

No paid smoke and no 89-task run were started for this repair.

## Rejected-run facts

The archived merged receipt reports:

| Task | Official reward | Model calls | Repository result |
| --- | ---: | ---: | --- |
| `cobol-modernization` | 0 | 0 | graph gate failure |
| `gpt2-codegolf` | 0 | 0 | graph gate failure; baseline also unsolved |
| `write-compressor` | 0 | 0 | graph gate failure |

The remaining seven tasks solved. On those common solved tasks, the rejected
treatment used 334,168 more total tokens (+1.875%), 182,143 more uncached input
tokens (+113.6%), 37 fewer API calls, and 60 fewer model actions. The aggregate
therefore mixed a controller-work reduction with a provider-context regression.
`schemelike-metacircular-eval` dominated the long-context cost.

The paid receipt also recorded all 17 features enabled and 13 feature IDs
firing naturally. The four absent IDs had no exact eligible event. That fact is
orthogonal to the solve loss: a path census cannot rescue a model loop that the
graph gate prevents from starting.

## Root-cause hierarchy

### P0 — outcome-destructive graph policy

`require_graph_ready=true` previously mapped any graph failure to
`RepositoryGraphGateFailed` before the first provider call. This conflated two
different requirements:

1. GT evidence must not be trusted when its substrate is invalid.
2. Mini-SWE must retain its ordinary opportunity to solve the task.

The implementation satisfied the first by violating the second. A deterministic
controller bug therefore caused a deterministic solve loss.

The repaired boundary is:

```text
invalid graph
  -> no graph-derived provider payload
  -> graph_degraded_fallback=true
  -> ordinary Mini-SWE loop continues
  -> merged GT treatment fails analytical promotion
```

This is operational fail-open and experimental fail-closed. It preserves
baseline capability without allowing a graph-less run to masquerade as a valid
GT treatment.

### P0 — repository transfer coupled to task artifacts

The previous initial mirror copied the workspace directory. On `gpt2-codegolf`
that included a roughly 498 MB checkpoint plus other task data. Graph readiness
therefore depended on irrelevant artifacts and hit the transfer timeout.

The new mirror is selected from the authoritative `WorkspaceSnapshot`. It
transfers only validation-relevant authored source and bounded project metadata.
It excludes checkpoints, datasets, compiled binaries, build products, caches,
and task deliverables. The NUL-separated manifest is deterministic and hashed.
An oversize or over-budget authored source set is explicitly incomplete; an
oversize optional lock file does not falsely invalidate source completeness.

### P1 — graph health conflated with retrieval success

A healthy current `graph.db` could be classified as unusable merely because the
task-conditioned query produced no high-confidence anchor. That created pressure
to send generic symbols simply to make provider visibility nonzero.

The new contract has independent states:

- substrate: healthy/current, unavailable, stale, incomplete, or invalid;
- retrieval: matched, represented, empty, low precision, stale, or not
  evaluated;
- delivery: selected, already represented, low precision, over budget, stale,
  or substrate failure.

Healthy `EMPTY` and already `REPRESENTED` are valid accounted abstentions. They
do not fabricate text and do not invalidate the graph.

### P1 — semantic certainty conflated with task relevance

The old ranking could reuse graph/extractor confidence as retrieval relevance.
That let structurally real but generic anchors such as `app`, `url`, or `repr`
approach the visibility threshold.

The repair carries two independent scores through graph ranking, repository
evidence, context-frontier accounting, and receipts:

- `semantic_certainty`: whether the graph claim is mechanically supported;
- `retrieval_relevance`: whether the claim is linked to the active task through
  an exact active path, typed task resource, or distinctive subject/symbol.

Both must reach 0.95 for model delivery. Out-of-range scores are rejected, not
clamped into apparently valid provider facts.

### P1 — resource roles leaked across prose clauses

Task-resource extraction used broad line context. A cue such as “write” could
incorrectly label a provided input or a pipeline executable as the output. The
latest run exposed this around `decomp.c`, `a.out`, the GPT-2 checkpoint, and
vocabulary data.

The parser now applies cues within punctuation-bounded clauses, recognizes the
expanded real suffix set, distinguishes provided source as `REFERENCE`, and
uses mechanically stronger compile-target and pipeline-executable positions.
Only high-confidence `OUTPUT` resources can become deliverables.

### P1 — frontier identity was revision-coupled

The old frontier fact ID included source revision. An unrelated edit therefore
made an unchanged semantic fact look new and eligible for another delivery.

Each fact now has:

- a semantic `claim_id`, stable across revisions, for one-shot delivery;
- a versioned `fact_id`, bound to source and graph revisions, for exact replay.

The agent tracks and audits both. Duplicate claim delivery is a release failure.

### P1 — large observations were replayed on every later call

The rejected Scheme trajectory contained broad read output that remained in the
linear Mini-SWE history for roughly 99 calls. Waiting for whole-history
compaction made the repeated cost dominant.

The provider view now bounds each typed tool observation before every provider
call:

| Operation | Provider body limit |
| --- | ---: |
| READ | 12,000 chars |
| SEARCH | 8,000 chars |
| EDIT / CREATE | 8,000 chars |
| DELETE / SUBMIT | 4,000 chars |
| VALIDATE | 16,000 chars |
| INSTALL | 12,000 chars |
| OTHER / parser abstention | 20,000 chars (historical fail-open bound) |

The bounded view retains deterministic head, diagnostic lines on failure, tail,
and—on successful large reads—three evenly spaced interior windows. It also
retains the full-output hash, return code, and an instruction to issue a
narrower command when omitted detail is required. Durable trajectories remain
complete.

Typed operation metadata is attached privately to the Mini-SWE tool observation
and removed by provider preparation. No GT marker or model acknowledgement is
required.

### P1 — cache-breaking compaction could buy negligible savings

Starting a compacted checkpoint changes the provider prefix. The prior policy
could do that when the view crossed a size threshold even if only a few thousand
characters were removable.

The new soft policy considers an epoch at 120,000 provider characters toward an
80,000-character target, then previews the exact transformed view. It proceeds
only when both conditions hold:

- at least 20,000 characters saved;
- at least 10% of the current provider view saved.

Otherwise it receipts `insufficient_cache_break_benefit` and preserves the
existing prefix. Hard prompt-budget headroom remains authoritative. Distinct
assistant content and reasoning are never removed.

## Implementation map

| Boundary | Files | Change |
| --- | --- | --- |
| host loop | `eval/gt_central_agent.py` | graph degraded fallback, source-only transfer, claim dedup, bounded observations, cache-benefit gate, receipts |
| source mirror | `gt_engine/repository_mirror.py` | deterministic source/metadata selection and completeness proof |
| graph substrate | `gt_engine/repository_intelligence.py` | independent substrate and retrieval status; empty retrieval remains healthy |
| task retrieval | `gt_engine/graph_evidence.py`, `gt_engine/graph_context.py` | typed task paths, independent certainty/relevance, generic-anchor rejection |
| provider frontier | `gt_engine/context_frontier.py` | semantic claim IDs, versioned fact IDs, strict score/accounting rules |
| task contract | `gt_engine/task_contract.py` | clause-local resource typing and expanded real resources |
| provider view | `gt_engine/provider_view.py` | typed observation governor and immutable, benefit-gated epochs |
| metrics/replay | `gt_engine/deep_metrics.py`, `scripts/central_efficiency_replay.py` | fallback, mirror, claim, operation, bounding, compaction and replay metrics |
| release gates | `scripts/central_pre_smoke_gate.py`, `scripts/central_readiness_audit.py`, `.github/workflows/central_provider_free.yml` | exact new tests and checked-out binary proof |

## Provider-free evidence

### Tests and static verification

- exact changed-file Ruff workflow scope: PASS;
- Python compilation of `eval`, `gt_engine`, `scripts`, and `tests`: PASS;
- workflow-like runtime suite: all non-substrate tests passed;
- five local test cases are blocked by the same external substrate condition:
  three census cases and two readiness cases. This Windows
  environment has no Go compiler and the cached old index binary emits zero
  COBOL and Scheme nodes;
- `git diff --check`: PASS.

The five locally blocked cases were not waived. GitHub workflow `31244088870`
built the checked-out vendored Go source with the pinned grammars on exact
commit `e6ce41f1177084480a4fbc8ead3caa2da4662b18` and cleared that external
substrate condition:

- `REPOSITORY_SUBSTRATE_PROVEN`;
- 3 source and 3 indexable files;
- 6 graph nodes, 2 edges, 4 definitions, and 2 directed call edges;
- nonzero COBOL, Python, and Scheme nodes;
- all required FTS tables and schema checks;
- 311/311 workflow-scope tests passed;
- structural readiness printed `READY`;
- exact changed-file Ruff scope printed `All checks passed!`.

The preceding workflow `31243969685` is rejected as certification evidence. It
correctly exposed one stale census fixture that omitted the new independent
substrate/certainty/relevance fields. Commit `e6ce41f` repaired that proof
fixture; the replacement workflow then passed every stage. Neither workflow
called a paid model.

When the explicit exact pre-smoke gate was first added, workflow `31244388485`
passed every behavioral check but correctly rejected the checkout as dirty: the
install step had unnecessarily changed the executable bit on the tracked
fallback binary. The workflow now executes only the certified binary built in
`$RUNNER_TEMP` and does not mutate the tracked fallback before exact-commit
approval.

### Archived policy replay

The permanent replay policy passed all archived tasks in three runs:

- `31145623534`: 10/10 `REPLAY_OK`;
- `31142998081`: 10/10 `REPLAY_OK`;
- `31078501162`: 10/10 `REPLAY_OK`.

The regression-preservation replay also passed the latest rejected archive.

### Provider-view replay of `31234520516`

This replay does not call a model and cannot prove tokens, cache billing, or
solve rate. It deterministically projects the new provider view over the seven
archived trajectories that contain model calls:

| Metric | Result |
| --- | ---: |
| Raw cumulative provider characters | 147,318,883 |
| Projected cumulative provider characters | 139,539,742 |
| Avoided characters | 7,779,141 |
| Reduction | 5.2805% |
| Unique oversized observations bounded | 6 |
| Benefit-approved compaction epochs | 3 |
| Insufficient-benefit deferrals | 47 |
| Distinct assistant reasoning characters removed | 0 |

Per-task projected provider-view savings:

| Task | Characters avoided | Ratio | Bounded observations | Epochs |
| --- | ---: | ---: | ---: | ---: |
| `fix-code-vulnerability` | 976,992 | 11.4555% | 0 | 1 |
| `headless-terminal` | 51,390 | 0.5951% | 2 | 0 |
| `llm-inference-batching-scheduler` | 622,998 | 5.2769% | 0 | 1 |
| `schemelike-metacircular-eval` | 6,127,761 | 5.5335% | 4 | 1 |

The other completed trajectories had no removable provider body under the new
policy. The three graph-blocked archived tasks contain no provider calls, so
offline replay cannot prove their repaired outcome path; the ordered agent test
proves the provider loop now executes, and a matched smoke must supply live
outcome evidence.

## Feature correctness boundary

No feature was forced to fire on an ineligible event. The release claims remain:

1. all 17 producer and consumer paths must be provider-free proven;
2. every effect produced in a paid trajectory must be consumed and explicitly
   accounted;
3. only naturally eligible features should fire in that trajectory;
4. every visible payload must be grounded, concrete, first-eligible,
   non-predictive, deduplicated, and present in the exact provider request;
5. engine-private state is real GT work but is not relabeled as model influence.

Natural firing of all 17 in ten stochastic tasks is neither required nor
desirable. Forcing absent failure/recovery/submit triggers would create false
fires and context regression.

## Acceptance gate and remaining work

The implementation is not promoted until these gates pass in order:

1. **Passed:** commit and push the exact tracked patch without any local
   artifacts or credentials.
2. **Passed:** `central_provider_free.yml` on exact commit `e6ce41f`. The real
   Linux binary fixture, all-17 census coverage, readiness audit, 311 tests,
   and Ruff all passed in workflow `31244088870`.
3. The provider-free workflow now runs
   `python scripts/central_pre_smoke_gate.py`. Require `SMOKE_APPROVED` on the
   intended exact pushed smoke commit; a green parent commit is insufficient.
4. With separate authorization, run one matched ten-task GT-on smoke against
   the existing frozen GT-off baseline. Do not rerun baseline.
5. Audit outcomes first: at least 9/10 official and uncensored resolved, no new
   baseline solve loss, and no outer censor.
6. Audit the engine: substrate status, retrieval dispositions, all effects,
   payload semantics/timing, request-hash coverage, duplicate claims,
   observation bounds, compaction epochs/deferrals, and zero reasoning removal.
7. Compare common-solved per-task tokens, uncached input, cached input, API
   calls, model steps/actions, effective task executions, controller
   executions, and wall time. Aggregate savings cannot hide a solve loss.
8. Require a repeated matched outcome-first efficiency signal before starting
   89 tasks.

## Rollback

One switch remains authoritative: `integration_mode=off` disables GT behavior.
The paid preflight remains `SHADOW`. The graph fallback never rewrites,
suppresses, or blocks a model command. Reverting this patch restores the prior
implementation, but must not restore the pre-provider graph kill switch.

## What is and is not proved

Proved provider-free on the exact pushed implementation:

- the previous graph failure no longer blocks the provider loop;
- graph health is independent from task retrieval;
- source transfer excludes the large irrelevant artifacts seen in the failed
  run;
- resource roles, relevance, and semantic claim dedup are deterministic;
- observation bounding and the soft compaction benefit gate preserve distinct
  assistant reasoning;
- archived provider-view work decreases under the deterministic projection.
- the checked-out Linux index binary passes schema, graph, COBOL, Python,
  Scheme, definition, directed-call, and frontier fixture checks;
- the full workflow-scope deterministic suite and structural readiness pass.

Not yet proved:

- a live GT-on smoke restores the frozen 9/10 outcome;
- actual provider tokens, uncached input, cache behavior, calls, actions, and
  wall time improve;
- GT improves solve rate beyond baseline;
- the 89-task treatment is ready.
