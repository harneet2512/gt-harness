# GT call/step efficiency repair

Date: 2026-08-09
Branch: `inline-engine`
Evidence source: paid smoke `31343081886` plus provider-free replay
Paid execution in this implementation pass: none

## Outcome

The implementation defects behind the mixed efficiency profile have been
repaired and promoted into the fail-closed local release gate. The repair is
provider-free verified. It does **not** establish that a future live smoke will
have negative token, call, step, and action deltas; only a new authorized paid
GT-on smoke can measure that outcome.

The prior common-solved comparison remains rejected:

| Metric | GT-off | GT-on | Delta | Gate |
|---|---:|---:|---:|---|
| Tokens | 19,303,944 | 18,612,616 | -691,328 (-3.58%) | passed alone |
| API calls | 345 | 357 | +12 (+3.48%) | failed |
| Assistant steps | 345 | 356 | +11 (+3.19%) | failed |
| Model actions | 407 | 383 | -24 (-5.90%) | passed |

The run also lost `write-compressor`, so resource savings could not promote the
treatment regardless of their sign.

## Root-cause stack

### 1. Redirected validator lost semantic identity

The shell parser allowed redirection syntax to pollute executable argv. The
real portfolio command was:

```text
cd /app && timeout 900 python3 benchmark.py 2>&1
```

The archived action returned `-1` with `RuntimeError: Command timed out after
30.0 seconds`. The declared check was not matched, so the action received the
default 30-second host timeout rather than the bounded declared-validator
extension. The model then spent additional calls creating and polling a
background benchmark. This is a deterministic controller defect, not a mere
token fluctuation.

Repair:

- `ShellRedirection` is distinct from semantic argv;
- `2>&1` is descriptor duplication, not a mutation;
- file output remains a typed write;
- file input becomes a typed read;
- attached versus spaced descriptors preserve shell semantics;
- the validation segment is the primary proposal operation even when output is
  also written;
- exact compound declared checks choose the recognized validator segment, not
  an arbitrary trailing reporter.

### 2. Progress identity collapsed unlike attempts and leaked repeat frames

The previous semantic signature could group unrelated no-gain commands while
also consuming a read path after a failed executable. Repeated updates inside
the same `STALLED` state could emit another progress frame. Both errors distort
the controller and can add context without reducing a decision.

Repair:

- `attempt_id` = operation + normalized executable + targets + source revision
  + declared check;
- `observation_id` = attempt ID + typed result + output hash + diagnostic;
- valid nonzero conventions (`rg`/`grep` no match and `diff`/`cmp` difference)
  are observations, not generic failures;
- shell `124` and Mini-SWE's exact `-1` host-timeout protocol are timeouts;
- failed read/search actions do not consume anchors;
- workspace activity, observation gain, and task-progress gain are independent;
- only an attributed validation pass or a confirmed task-output change advances
  task progress;
- same-state stall, contradiction, and budget-risk updates remain private.

### 3. Graph certainty was being confused with decision relevance

A task-visible file path could make any high-ranked definition in the same file
eligible. That is grounded repository data but not grounded current context.
Correct facts can still waste attention when their relationship to the current
decision is absent.

Repair:

- path-only context produces a certified file-location fact;
- definitions, signatures, callers, references, tests, and named symbols need
  an exact symbol or relationship target already represented at the Mini-SWE
  decision boundary;
- malformed structural symbols are rejected as low precision;
- every abstention remains accounted, so precision is not mistaken for an
  inactive graph.

### 4. The aggregate gate omitted two forms of work

`assistant_steps` was a primary metric but was not part of the aggregate
failure list. Controller-inclusive `effective_actions` was reported but not
gated. That allowed a token decrease to obscure extra reasoning turns or host
work.

Repair:

- response/action batching is extracted per trajectory;
- `actions_per_api_call` uses the authoritative model invocation count;
- assistant steps are a strict aggregate dimension;
- positive effective-action delta fails the aggregate gate;
- validator preservation, timeout selection, action timeouts, typed progress,
  and failed-anchor counts are exported to deep metrics.

## Files changed

- `gt_engine/preflight.py`: typed redirections and input/output semantics.
- `gt_engine/central_runtime.py`: exact compound-check validator selection.
- `gt_engine/progress.py`: result kinds and content-addressed observations.
- `eval/gt_central_agent.py`: real-loop progress semantics and receipts.
- `gt_engine/context_frontier.py`: decision-conditioned graph boundary.
- `gt_engine/deep_metrics.py`: batching metrics and strict gate dimensions.
- `scripts/central_pre_smoke_gate.py`: permanent regression tests.
- focused tests under `tests/test_gt_*.py`.

## Verification evidence

- Focused repair scope: passed.
- Exact provider-free workflow test scope: passed.
- Python compilation: passed.
- Ruff on changed implementation/tests: passed before final documentation.
- Direct all-17 census: passed all permanent producer, consumer, timing,
  grounding, context-accounting, substrate, frontier, opportunity, and
  baseline-shield lines.
- Readiness audit: `READY`.
- Archived replay of all ten tasks from `D:\gt_runs\31343081886`: `REPLAY_OK`.
- The exact pre-smoke command passed lifecycle tests, both census entrypoints,
  repository substrate, language contract, and readiness. It correctly printed
  `SMOKE_BLOCKED` only because the repaired tracked tree is not yet committed
  and pushed at the same revision.
- The repository-wide unbounded test command exceeded the local five-minute
  command ceiling without reporting a failure; it is not counted as a pass or
  failure. The exact release workflow scope is the authoritative local test
  witness.

## What this proves

High confidence:

- redirected declared validators retain their authority and timeout policy;
- result semantics and progress identities are replayable;
- repeated controller state no longer duplicates provider frames;
- graph facts require current decision anchors;
- the aggregate gate cannot label lower tokens plus higher steps/effective work
  an efficiency win;
- all 17 feature paths and the existing postflight engine remain intact.

Not yet proved:

- zero live outcome regression;
- negative live common-solved deltas for every aggregate resource;
- a solve improvement;
- causal model benefit from a provider fact.

## Release state and next gate

The 89-task benchmark remains blocked. After the repaired tracked tree is
committed and pushed, `scripts/central_pre_smoke_gate.py` must print
`SMOKE_APPROVED` at that exact commit. The next paid action, only with separate
authorization, is the ten-task `certified_full` GT-on smoke against the frozen
local GT-off baseline. Acceptance requires no uncensored solve loss or new
censor, healthy graph substrate, complete evidence/timing accounting, and a
strict common-solved decreases for tokens, actual model calls, assistant steps,
and normalized cost, with no positive delta in model actions, wall time, or
controller-inclusive effective actions.
