# GT regression-preservation implementation

Date: 2026-08-06
Branch: `inline-engine`
Starting commit: `7a91e30`
Evidence status: provider-free implementation; paid confirmation not run

## Outcome

The repair plan prompted by rejected workflow `31078501162` is implemented.
The implementation fixes the two observed solve-loss mechanisms without
changing the paid preflight arm from SHADOW or claiming a benchmark recovery.

The rejected smoke resolved 7/10 against the frozen 9/10 GT-off baseline:

- `llm-inference-batching-scheduler` reached 100 assistant steps without a
  submit;
- `write-compressor` sent a provider request above the model context limit.

Receipt replay showed 312 produced/applied effects and correct delivery timing.
The regressions were therefore not explained by missing feature triggers. The
controller had four independent defects:

1. task resources were inferred from local lines and could conflate inputs
   with outputs;
2. arbitrary observation novelty could clear near-budget state;
3. provider compaction could delete distinct assistant reasoning while the
   metric reported zero removal; and
4. no hard check measured the exact provider-prepared request before dispatch.

## Implemented changes

### 1. Typed task resources

`gt_engine/task_contract.py` now emits `TaskResource` records with one of five
roles:

- `INPUT`
- `OUTPUT`
- `REFERENCE`
- `EXECUTABLE`
- `UNKNOWN`

The extractor carries explicit input/output flow across wrapped Markdown,
uses structural `input_data` and `output_data` paths as strong evidence, and
keeps conflicting/ambiguous paths unknown. `task_deliverable_paths()` is now a
strict projection of high-confidence output resources. It no longer treats
scheduler input buckets as deliverables and no longer treats `data.txt` as a
write-compressor output.

### 2. Output-aware completion without false completion

`gt_engine/completion.py` adds `required_output_exists` probes for confirmed
output paths. These probes:

- execute privately;
- use `test -s` against the exact output;
- have no obligation IDs;
- cannot change a partial plan into a complete plan; and
- cannot authorize auto-submit.

They measure real task progress for artifact/data tasks while preserving the
fail-open completion contract. Existing mechanically complete predicates for
write-compressor are unchanged and are not duplicated by existence probes.

### 3. Monotonic progress recovery

`gt_engine/progress.py` makes `BUDGET_RISK` sticky until the task state changes.
New output, scratch, cache, or diagnostic novelty cannot clear it by itself.
`MiniSweCentralAgent` now distinguishes:

- `material_workspace_change`, used conservatively for stale-batch safety; and
- `task_progress_change`, limited to authored source or a confirmed task
  output and used for progress recovery.

The receipt records the number of task-progress changes.

### 4. Reasoning-preserving provider view

`gt_engine/provider_view.py` no longer compacts assistant content or hidden
reasoning. The audit history remains exact, and the provider transform may
only change tool-observation bodies:

- oversized tool results are bounded to deterministic head/diagnostic/tail
  evidence, including the newest result;
- exact duplicate results are represented append-only with a prior-action
  reference, full-output hash, and character count;
- if still above the configured threshold, only old tool bodies may become
  hash/return-code receipts;
- no generic current-state frame is injected.

Every fact is still accounted. Facts absent from the retained provider view
remain controller-only instead of being rendered as recurring prompt text.
The actual unique-assistant-reasoning removal metric is computed from the
input and output views; it is no longer hard-coded to zero.

### 5. Exact provider budget

The exact provider-prepared message list is measured after Mini-SWE provider
normalization and after pending grounded evidence is attached, but before
`model.query()`.

The receipt records:

- configured context limit and hard ratio;
- token-counter estimate;
- conservative UTF-8 upper bound;
- effective estimate;
- hard prompt limit;
- remaining headroom; and
- whether the request is within the limit.

Timeout, counter failure, or an uncertain token estimate does not permit an
unsafe request. If the request exceeds the hard budget:

- the model is not called;
- pending guidance is not confirmed as delivered;
- the run exits as internal `ContextBudgetExhausted`; and
- the exit is not mislabeled as an outer Harbor censor.

### 6. Stable-prefix and compaction observability

Each call records the exact append-stable provider-message prefix relative to
the prior call. This is a cacheability measurement, not a model-attention or
causality claim. Deep metrics now include:

- provider budget failures and minimum headroom;
- stable-prefix characters and mean ratio;
- bounded observation count and removed characters;
- append-only duplicate representations;
- cleared old tool results;
- compaction count and elided characters;
- completion predicate/certificate counts;
- progress transitions; and
- task-progress changes.

`gt_engine/deep_metrics.py` carries these metrics into matched-arm reports.

### 7. Fail-closed release gate

The paid workflow provider-free suite now includes `tests/test_gt_progress.py`.
`scripts/central_pre_smoke_gate.py` explicitly includes the regressions for:

- wrapped task outputs and input rejection;
- compressor input/output separation;
- distinct reasoning preservation;
- recent oversized observations;
- append-only duplicate representation;
- hard provider budgeting; and
- sticky budget risk.

The readiness audit requires these release-gate witnesses.

## Tests added

The new tests prove:

1. wrapped scheduler outputs are extracted and input buckets are rejected;
2. compressor source and output are not conflated;
3. a partial scheduler plan gains output probes but cannot auto-submit;
4. exact completion plans do not get duplicate output probes;
5. distinct assistant reasoning survives compaction byte-for-byte;
6. a single recent oversized observation is bounded;
7. duplicate turns retain the assistant entry and replace only duplicate tool
   output with a receipt;
8. over-budget provider requests fail before the model query;
9. the failure is receipted as internal solver exhaustion; and
10. fresh scratch observations cannot clear `BUDGET_RISK`.

Targeted verification completed during implementation:

```text
python -m py_compile eval/gt_central_agent.py gt_engine/provider_view.py
  gt_engine/task_contract.py gt_engine/completion.py gt_engine/progress.py

python -m pytest -q tests/test_gt_central_agent.py
  tests/test_gt_central_runtime.py tests/test_gt_completion.py
  tests/test_gt_progress.py tests/test_provider_view.py
  tests/test_task_contract_noise.py

122 passed
```

Additional targeted deep-metrics/readiness tests passed. The final full suite,
17-feature census, readiness audit, and exact pre-smoke gate are recorded in
the verification section below when run.

## Claims that are and are not supported

Supported now:

- task input/output roles are deterministic and confidence-bounded;
- output progress cannot masquerade as task completion;
- assistant reasoning is not removed by the active provider transform;
- oversized observations are bounded before provider dispatch;
- an over-budget exact request is never sent;
- unsent evidence is not marked delivered;
- budget risk cannot be cleared by mere novelty; and
- the new behavior is replayable through receipts and deep metrics.

Not supported until a new authorized matched smoke:

- restored 9/10 solve preservation;
- per-task or aggregate efficiency improvement;
- causal benefit from GT context;
- zero stochastic outcome regressions; or
- readiness for the 89-task run.

The next paid action remains a separately authorized ten-task matched smoke.
The 89-task run remains blocked until outcome preservation and repeated
outcome-first efficiency gates pass.

## Verification record

Final provider-free verification:

```text
python -m pytest -q
PASS (three expected platform skips)

python scripts/central_feature_census.py
PASS: all required ALL_*/NO_* lines

python -m scripts.central_feature_census
PASS: all required ALL_*/NO_* lines

python scripts/central_readiness_audit.py
READY

python scripts/central_pre_smoke_gate.py
semantic tests: PASS
direct census: PASS
module census: PASS
readiness: PASS
exact pushed commit: FAIL
SMOKE_BLOCKED
```

The final release wrapper is blocked only because the verified worktree has
not been committed and pushed. That guard is intentional; no paid smoke was
started.

Archived trajectory replay gives two concrete counterfactual implementation
witnesses without claiming a new model outcome:

```text
python scripts/central_regression_preservation_replay.py D:\gt_runs\31078501162
ARCHIVED_REGRESSION_REPLAY_OK
```

1. The exact scheduler task text now classifies both request buckets as
   `INPUT` at confidence 1.0 and both plan files as `OUTPUT` at confidence
   1.0. The deliverable projection contains only `plan_b1.jsonl` and
   `plan_b2.jsonl`. Its completion plan remains partial/non-executable and has
   two zero-obligation output-existence probes.
2. Recompiling the final archived write-compressor provider history through
   the repaired view bounds the 2,804,946-character tool observation. The
   exact Mini-SWE provider-prepared request is 306,990 characters; the token
   counter estimates 85,404 tokens and the conservative bound is 307,037
   against a 943,718 hard prompt limit, leaving 636,681 tokens of conservative
   headroom. Assistant reasoning removed: 0.

These replays prove that the two diagnosed boundaries now behave as designed.
They do not prove that the stochastic model would solve either task; only the
next authorized matched smoke can establish outcome preservation.
