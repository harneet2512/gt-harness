# GT-on repair-mix smoke audit — workflow 31421610097

Date: 2026-08-10
Commit: `18720f2` (`inline-engine`)
Workflow: [31421610097](https://github.com/harneet2512/gt-harness/actions/runs/31421610097)
Mode: `ACTIVE` + `SHADOW` preflight, certified graph gate, replay capture enabled, parallelism 20

## Executive result

All 20 task jobs completed and were graded. The merge job failed closed because
`prove-plus-comm` and `sanitize-git-repo` had invalid repository intelligence.
This is a treatment-validity failure, not a claim that the other 18 task
executions failed.

The run solved **15/20** selected tasks. The frozen GT-off reference solved
**17/20** of this same selected set. Relative to that reference there were:

- four baseline-solved tasks lost: `extract-elf`, `sanitize-git-repo`,
  `torch-tensor-parallelism`, and `video-processing`;
- two baseline-unsolved tasks solved: `count-dataset-tokens` and
  `largest-eigenval`;
- one task remained baseline-unsolved: `torch-pipeline-parallelism`.

The run is therefore **not release evidence**. It does not prove an efficiency
or solve-rate improvement, and it must not be used to authorize the 89-task
benchmark.

## Resource comparison

The common-solved set contains 13 tasks. On that set:

| Metric | GT-off | GT-on | Delta |
| --- | ---: | ---: | ---: |
| Provider tokens | 89,272,437 | 42,807,652 | -46,464,785 (-52.0%) |
| Provider calls | 705 | 701 | -4 (-0.57%) |

These are descriptive matched-set deltas only. They do not establish causal
efficiency because four baseline solves were lost and the treatment was
invalid for two graph-backed tasks. All-selected-task totals are confounded by
those outcome differences and are not a valid efficiency gate.

## Repository-intelligence failure

The invalid set is exactly:

- `prove-plus-comm`
- `sanitize-git-repo`

Both receipts contain a complete source-mirror plan followed by
`mirror_transfer: failed (RuntimeError)`, and every repository-intelligence
attempt is marked `substrate_failure` with `graph_missing`,
`source_revision_missing`, and `graph_not_current`. The merge gate correctly
rejects the treatment instead of serving stale or fabricated graph facts.

The implementation defect is visible in `eval/gt_central_agent.py`:

```python
archive_members = ("app/" + path for path in mirror_plan.paths)
...
tar ... -C / -T paths.nul
```

The mirror planner returns paths relative to the task working directory, but
the transfer unconditionally addresses them under `/app`. The same run has a
task whose resolved working directory is `/workspace` (`prove-plus-comm`) and
one under `/app/dclm` (`sanitize-git-repo`). Consequently the archive command
can address the wrong remote paths even though the source manifest is complete.
The repair must derive the remote prefix from the resolved `self.cwd` and use
a matching tar transform; it must be covered for `/workspace`, `/app`, and a
nested `/app/<task>` cwd before another paid run.

This is a real substrate defect. It is not evidence that GT's deterministic
feature producers are wrong. Until fixed and provider-free proven, any task
with this failure remains treatment-invalid.

## GT receipts and delivery

- 20/20 central receipts found; all 17 feature IDs enabled in every receipt.
- 657 effects produced and applied.
- Effect disposition totals: 597 private-ineligible, 11 candidate-delivered,
  2 candidate-represented, and 47 candidate-policy-rejected.
- Naturally fired IDs: obligations (20), syntax_result (15),
  GT_CERT_DELIVERY (17), GT_CHANGE_SURFACE (20), GT_PATCH_DELTA (19),
  newfile_precedent (6), caller_contract (2), def_partition (2),
  localization (9), GT_EDIT_CHECK (3), GT_LOC_RESLOT (9), covering_red (1),
  GT_HYPOTHESIS (1), and signature_delta (3).
- Exact-trigger IDs absent in this trajectory: `recovery`, `submit_refusal`,
  and `GT_SS_SUBMIT_RED`. This means 17 paths are proven, not that all 17
  fired naturally in this stochastic run.

Provider evidence accounting contains 805 events: 612 controller-only, 136
already represented in retained provider messages, and 57 selected new
context frames. The 57 selected frames were all dispatched in their first
eligible request; there were zero late, predictive, or duplicate selected
deliveries. Receipt request-hash coverage is 1.0.

`scripts/central_trajectory_audit.py` passed with
`DETERMINISTIC_AUDIT_CERTIFIED` and `TRAJECTORY_AUDIT_CERTIFIED`. Its result is
also `MODEL_CAUSALITY_UNIDENTIFIABLE`: trajectory anchor-following cannot prove
that a sampled model action was caused by GT without a valid counterfactual
replay state.

## Regression interpretation

The four outcome losses are not one common failure:

- `sanitize-git-repo` is both a real graph-substrate failure and a solve loss.
- `extract-elf`, `torch-tensor-parallelism`, and `video-processing` had no
  selected feature-guidance frames, but that did **not** mean GT was invisible.
  Each received provider-visible controller progress state. Together with
  `sanitize-git-repo`, all four lost tasks received at least one progress frame,
  and most of those frames were false `STALLED` classifications caused by
  command-identity collapse. The first model action still diverged before any
  GT evidence in all four tasks, so the trajectories prove both pre-GT sampling
  divergence and a later GT-attributable controller defect; they do not prove
  a single exclusive cause.
- `prove-plus-comm` had invalid graph substrate but still received reward 1,
  demonstrating that graph invalidity and solve loss are distinct dimensions.

The correct next audit is therefore not “increase delivery count.” It is:

1. repair the cwd-aware source archive transfer and cwd-relative graph lookup;
2. add and pass provider-free tests for all three cwd layouts and archive
   member safety;
3. remove false stalls by making attempt identity command-specific without
   promoting observation novelty to task progress;
4. repair `/dev/null` and destructive-Git action classification, then replay
   this exact 20-task mix through the repaired policy;
5. run a newly authorized matched smoke only after the exact pre-smoke gate
   and repository-intelligence gate pass;
6. keep outcome preservation ahead of any 89-task dispatch.

No new paid run was started after 31421610097.
