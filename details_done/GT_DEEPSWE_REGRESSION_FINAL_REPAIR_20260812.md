# GT DeepSWE regression final repair

Date: 2026-08-12

## Outcome

The `4/10 -> 1/10` observation did not test persistent execution state. The
first paid persistent-state attempt failed OpenRouter preflight before a task
executed. The two completed runs being compared also did not use the same
served model route:

| Run | Completed task artifacts | Solved | Response model | Provider evidence |
| --- | ---: | ---: | --- | --- |
| `31557391617` | 10 (9 valid trajectories; Katex infrastructure failure) | 4 | `deepseek-v4-flash` | one fingerprint on 1,450 responses |
| `31575925244` | 10 | 1 | `deepseek/deepseek-v4-flash` | StreamLake 479, GMICloud 657, no fingerprint |

The outcome change is therefore confounded. It cannot be assigned either to
GT or to persistent execution state.

There was still a mechanically demonstrated GT delivery regression. The
earlier run emitted five preemptive frames totaling 11,339 characters, all on
`fd-deterministic-multi-key-sorting`. The 1/10 run emitted 53 preemptive frames
totaling 117,395 characters across all ten tasks. The total visible stream was
71 deliveries and 121,201 characters. The graph/source-revision repair at
`0c25ee4` activated task-start candidates that an earlier mismatch had
silenced. The resulting context was timely and hash-accounted, but broad and
frequently incomplete for the repair surface.

## Repairs

### One task-start authority

When the persistent bootstrap has selected a focus, generic preemptive
retrieval abstains at action zero with
`persistent_bootstrap_owns_task_start`. Action-, mutation-, diagnostic-, and
validation-conditioned retrieval remains unchanged. If bootstrap fails,
generic retrieval remains available as fail-open fallback.

### Selection before source delivery

The bootstrap request still receives catalog metadata without repository
bytes. After its single temperature-zero selection, the first executor
request receives one exact checkout-backed source span for the selected item.
The receipt records path, start/end line, symbol, claim ID, support kind,
retrieval rank, and supporting channels. Multi-symbol files resolve by exact
symbol; ambiguous file-only matches abstain instead of choosing the first span.
After the executor reads the selected path, the source excerpt is not repeated.

### One request-wide GT budget

All model-visible GT surfaces now enter one contribution compiler with a
frozen 1,200-token and 9,600-character ceiling. Priority is deterministic:
critical state, post-diagnostic/post-validation retrieval, ordinary state,
feature facts, ordinary preemptive evidence, graph frontier, then progress.
Every contribution is selected or receives one explicit suppression
disposition. The release gate rejects missing compiler calls, accounting
mismatch, token-limit mismatch, payload overflow, and duplicate selected
surfaces.

### Validation lifecycle coverage

The shared validator now unwraps literal `npx`, `npm exec --`, `npm x --`,
`pnpm exec`, `yarn exec`, and `bunx` invocations. Jest, Vitest, Mocha, AVA,
TAP, TypeScript compilation, and literal custom test/check scripts are
recognized. Dynamic command forms, raw program/heredoc text, and
help/version/list-only commands remain non-validation. A pipeline without
mechanically proven terminal ownership remains `UNKNOWN` even when its text
contains a failing test.

Archived DeepSWE trajectories contain 50 matching wrapper commands: 49 now
resolve to a standard runner and one dynamic `node -e` program correctly
abstains. This repairs state transitions and validation debt; it does not
retroactively change the archived outcomes.

### Provider identity and artifact audit

Each receipt now summarizes the actual provider-response model, provider, and
system fingerprint for executor calls and records the bootstrap response
identity separately. The DeepSWE preflight records the same non-secret fields.
Merge rejects unstable response models, a response model different from the
preflight response, a bootstrap-model mismatch, and a fingerprint mismatch
when a fingerprint exists. The merged manifest persists the observed model,
provider, and fingerprint sets.

DeepSWE task discovery now reads `task_name` from the Pier result adjacent to
the trajectory. The archived 1/10 tree consequently audits as ten distinct
tasks rather than collapsing into one directory-derived name.

## Verification

- `tests/test_persistent_execution_state.py` plus
  `tests/test_gt_contributions.py`: 33 passed.
- `tests/test_gt_central_agent.py`: 111 passed, one skipped because the real
  pinned ONNX asset is not present locally.
- `tests/test_central_trajectory_audit.py` plus
  `tests/test_gt_delivery_audit.py`: 17 passed.
- `tests/test_central_release_gate.py`: 19 passed.
- `tests/test_gt_central_runtime.py` excluding the three census tests that
  invoke the stale Windows native binary: 83 passed, one POSIX-only skip.
- Ruff, Python byte compilation, YAML parse, and `git diff --check`: passed.
- Archived run `31575925244`: `task_count=10`, deterministic audit certified,
  71 deliveries, 121,201 characters, zero late/predictive/duplicate delivery
  failures; causality remains unidentifiable.

The complete local census/readiness/pre-smoke commands correctly fail closed
because the checked-in Windows `gt-index.exe` lacks Objective-C. That is not a
new failure and was not weakened. The Linux provider-free workflow must build
the current Go source and certify the pushed commit.

## Remaining gates

1. [Complete] Commit and push only the tracked repair files: `9be71ad`.
2. [Complete] Source-built Linux provider-free workflow `31655082336` passed
   at `9be71ad`: current indexer, pinned Snowflake ONNX, every census line,
   `READY`, `SMOKE_APPROVED`, static checks, and `provider_calls: 0`.
3. Do not dispatch a paid task while OpenRouter rejects the pinned official
   DeepSeek endpoint. A successful one-call preflight must produce the exact
   response identity first.
4. Validate the frozen GT-off DeepSWE artifact's task/model/provider/runner/
   budget manifest. Requested model identity alone is insufficient if the raw
   response identity cannot be reconstructed.
5. Only then run the planned GT-on cohort. Promotion still requires zero
   baseline-solve losses, no censored treatment task, non-positive
   common-solved token/call/action/cost deltas, at least one flip, and strictly
   more solved tasks.

## Current status

`VERIFIED_COMPLETE` for implementation integrity at runtime commit `9be71ad`.
The external provider route remains blocked, so live outcome validation is not
complete and no paid outcome claim is made.
