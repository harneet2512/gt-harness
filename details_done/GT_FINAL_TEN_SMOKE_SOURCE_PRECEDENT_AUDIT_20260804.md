# GT final ten-task smoke: source-precedent and effect audit

Date: 2026-08-04  
Workflow: `30954660207`  
Commit: `e7418a7`  
Branch: `inline-engine`

## Why the first post-fix smoke was rejected

The first run after the classifier guard (`30952995623`) showed that the trigger
selection was improved, but the `newfile_precedent` payload still copied the
whole workspace-created batch. A valid source trigger could therefore carry
`__pycache__`, generated binaries, `data.comp`, or dynamic output files in its
`created_files` field. That was incorrect model delivery even when the selected
precedent itself was source-like.

Commit `e7418a7` fixes both sides of the boundary:

1. A precedent trigger must be a regular, model-authored, validation-relevant
   source file with a recognized source suffix.
2. Candidate precedent paths receive the same source classification.
3. The payload reports only the selected source trigger, never the complete
   workspace transition batch.

## Final smoke evidence

All ten treatment jobs completed successfully and produced ten receipts.

| Metric | Result |
|---|---:|
| GT features enabled per receipt | 17 |
| Effects produced / applied | 372 / 372 |
| Engine-internal state effects | 297 |
| Existing engine-actuation effects | 11 |
| Audit-only effects | 48 |
| Provider-payload effects | 16 |
| Model payload deliveries | 14 |
| Late deliveries | 0 |
| Predictive deliveries | 0 |
| First-eligible delivery rate | 100% on every receipt |
| `newfile_precedent` receipts | 10 |
| Invalid/non-source `newfile_precedent` paths | 0 |

The ten precedent payloads named only source paths, for example
`headless_terminal.py`, `shape_aware_packer.py`, `run_bench8000.py`, and
`eval_debug.scm`; no cache directory, binary, generated output, or task-output
path was present. Every delivery was grounded before the next eligible model
call.

## Efficiency comparison to the frozen GT-off baseline

The final GT-on smoke totals were 20,087,865 tokens, 369 API calls, 367
assistant steps, and 380 actions. Against the frozen GT-off totals (29,223,016
tokens, 420 calls, 420 steps, 483 actions), the deltas are:

- tokens: **-9,135,151** (-31.26%)
- API calls: **-51** (-12.14%)
- assistant steps: **-53** (-12.62%)
- actions: **-103** (-21.33%)

These are matched-smoke efficiency signals, not a causal claim about model
quality. The 89-task run still needs a larger matched evaluation for confidence.

## Gate decision

The ten-task correctness gate is now satisfied for the repaired source-
precedent boundary: effects are applied, payloads are concrete and grounded,
and timing has no late/predictive violations. The 89-task run may proceed only
from commit `e7418a7` (or a descendant) and only after retaining this exact
receipt audit in the run artifacts.
