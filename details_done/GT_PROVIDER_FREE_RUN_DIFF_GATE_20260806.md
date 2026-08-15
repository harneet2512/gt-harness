# Provider-free GT-on run-diff gate

Date: 2026-08-06
Status: implemented and locally verified; no new paid smoke yet.

## Purpose

The paid GT-on witnesses disagree: workflow `31136099371` resolved 10/10 and
workflow `31142998081` resolved 8/10. A single temperature-1 outcome cannot
tell whether the model diverged before GT evidence, a grounded payload changed
a later decision, or a deterministic controller transform changed the provider
view. The engine therefore needed an artifact-level diagnostic before another
paid run.

## Implemented boundary

### `scripts/central_run_diff.py`

New read-only CLI and public function:

```text
python -m scripts.central_run_diff <left-run-root> <right-run-root> --json report.json
```

It consumes only archived `miniswe_trajectory.json` and `central_receipt.json`
pairs. For every common task it reports:

- the first divergent model call and the exact selected action batches;
- whether that divergence preceded either run's first visible GT delivery;
- canonical prepared-request hash differences by model call;
- GT frames, feature IDs, evidence actions, delivery calls, and text;
- deterministic compaction, added-context, effective-action, and preflight
  disposition counters; and
- whether each receipt has a complete, unique prepared-request identity.

It neither imports a model client nor edits a trajectory, receipt, workspace,
or workflow. Missing/duplicate task evidence or missing request identities fail
the report rather than producing a causal claim.

### `scripts/central_replay.py`

The direct CLI form previously failed before argument parsing because Python
added `scripts/`, not the repository root, to `sys.path`. It now bootstraps the
repository root before GT imports. The module and direct forms are equivalent:

```text
python scripts/central_replay.py --help
python -m scripts.central_replay --help
```

No replay policy, feature runtime, provider view, model request, harness loop,
or paid workflow treatment changed.

### Release coverage

`tests/test_central_run_diff.py` provides the RED-to-green contract:

1. an action divergence at call 1 is classified as preceding a call-2 payload;
2. identical trajectories/receipts produce no spurious differences and have
   complete accounting; and
3. direct replay CLI invocation imports the project correctly.

The test is in the exact pre-smoke release-test list and the GitHub
provider-free workflow. Both new scripts are also linted there.

## Deep audit result

The new comparator ran on both real GT-on artifact roots:

```text
python -m scripts.central_run_diff \
  D:\gt_runs\31136099371\corrected \
  D:\gt_runs\31142998081
```

All 10 tasks had complete request accounting. For `schemelike`, and in fact
for every compared task, the first model action differed before the first
GT-visible evidence in either trajectory. In `schemelike`:

- both initial provider inputs are byte-identical;
- the 10/10 sample began with `ls -la`;
- the 8/10 sample began with `ls -la && echo "---" && find . -type f | head -100`;
- both later rendered the same `GT_EDIT_CHECK` text, source evidence hash, and
  135-character payload;
- both preflight arms were all PASS with no controller action, state frame,
  rewrite, suppression, return, or batch interruption.

This proves a first-turn temperature-1 trajectory divergence. It does not
claim that later compaction or grounded evidence had no influence. It makes
that distinction inspectable before a future smoke.

Both archived roots also passed the real provider-free replay:

```text
python -m scripts.central_replay D:\gt_runs\31136099371\corrected
python -m scripts.central_replay D:\gt_runs\31142998081
```

Each printed `REPLAY_OK` for all ten tasks.

## Non-regression proof for this implementation

The production integration is unchanged. The diff contains only:

- the offline run-diff script;
- direct-CLI import bootstrap for the existing offline replay script;
- tests; and
- release/provider-free test wiring.

It cannot alter an agent action, context compiler output, feature trigger,
payload, preflight decision, completion/progress policy, provider request, or
task-container execution. The full test suite passed after the change.

## Verification

- RED: `python -m pytest -q tests/test_central_run_diff.py` failed because the
  comparator module did not exist.
- Green narrow suite: 12 tests passed across run-diff, replay, and readiness.
- Actual CLI/runtime proof: both direct replay CLI help and module run-diff
  executed; both archived ten-task replays printed `REPLAY_OK`.
- Static: Ruff passed on every changed Python file; `git diff --check` passed.
- Broad regression: `python -m pytest -q` passed (three expected platform or
  fixture skips).

## Smoke decision

This implementation removes a diagnostic blind spot. It cannot by itself make
a temperature-1 model outcome deterministic. The next GT-on 10-task smoke is
permitted only after the exact pushed-commit `central_pre_smoke_gate.py`
passes. Its result must still be evaluated outcome-first against the existing
GT-off baseline; the 89-task run stays blocked.
