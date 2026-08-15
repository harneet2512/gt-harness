# GT delivery deduplication and signature-evidence repair

Date: 2026-08-07
Scope: archived treatment smoke `31151525496`; no new paid run was started.

## Defects confirmed

The smoke receipt audit found five provider-visible advisories. Timing was
correct for all five, but the exact model-authored history already contained
the concrete fact for three of them. In addition, one signature advisory named
`__pycache__` and a `.pyc` artifact. A receipt is not evidence of useful model
context, so both defects were repaired at their source.

## Repairs

### Source-bound signature payloads

`CentralFeatureRuntime.observe_action` now serializes `signature_delta` paths
from the classified validation-relevant source set (`source_relevant`) rather
than the raw workspace change set. Cache, bytecode, generated, task-output,
and other derived paths can therefore never become signature claim anchors or
provider text. The existing source-revision classifier remains the authority;
no second artifact regex was introduced.

### Representation-aware delivery

`model_feedback` accepts the durable Mini-SWE message history. Before rendering
a selected claim, it performs a bounded lexical representation check over
assistant-authored content and action commands:

* `newfile_precedent` is suppressed when both the created path and concrete
  precedent path are already present in one assistant turn;
* `signature_delta` is suppressed when the changed path, symbol, and exact
  before/after signatures are already present in one assistant turn;
* all other feature types retain the existing delivery policy.

Suppression is explicit (`delivery_status=suppressed`,
`delivery_reason=represented_in_action_history`) and keeps the private engine
receipt/effect for accounting. It does not rewrite, block, or delay the shell
action. The agent passes history at both task-start and post-action feedback
boundaries.

## Proof

RED tests reproduced the old artifact leak and the missing history parameter.
The new tests cover:

1. signature payload and semantic claim anchors exclude `__pycache__/*.pyc`;
2. new-file precedent is suppressed when the action already contains both
   concrete paths;
3. signature delta is suppressed when the exact edit is already in the
   action history.

Focused suite: **218 passed** (`central_runtime`, all-17 consumer proofs,
agent loop, provider view, and deep metrics). Compilation and `git diff
--check` pass.

Recorded runtime proof:

* `python -m scripts.central_replay D:\gt_runs\31151525496` — `REPLAY_OK`;
  headless-terminal and write-compressor redundant advisories replay to zero,
  while portfolio-optimization and schemelike-metacircular-eval retain their
  grounded new guidance.
* `python scripts/central_regression_preservation_replay.py
  D:\gt_runs\31151525496` — `ARCHIVED_REGRESSION_REPLAY_OK`.
* Provider-free census and readiness audit pass all required lines.

The pre-smoke gate was rerun before commit and failed closed only on its
expected exact-pushed-commit check. A new paid smoke is intentionally not part
of this repair; outcome and model-use efficacy still require a separately
authorized matched smoke.

## Interpretation

The fix does not claim that every private GT effect should become model text.
It proves the stronger boundary required here: only grounded, decision-new
facts are sent to the provider, exact facts already represented by the model
are retained as private accounted effects, and derived artifacts cannot enter
source-bound guidance.
