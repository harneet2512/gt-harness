# GroundTruth paired decision-point utility

This is a bounded diagnostic of the frozen GT delivery mechanism. It is not an
end-to-end solve-rate or efficiency result.

## Capture integrity

Three GitHub Terminal-Bench 2.0 capture slices used the same model,
`deepseek-v4-flash`, temperature `1.0`, Mini-SWE central runtime, and
`step_limit=1`:

| Capture | Planned | Valid first-intervention pairs | Legitimate no-intervention | Corrupt |
| --- | ---: | ---: | ---: | ---: |
| `31530343093` | 20 | 11 | 9 | 0 |
| `31531620414` | 10 | 4 | 6 | 0 |
| `31532480146` | 10 | 1 | 9 | 0 |
| **Total** | **40** | **16** | **24** | **0** |

`31532480146` failed its merge gate because `crack-7z-hash` had an invalid
repository graph. Its replay bundles were still audited; that task is not a
valid repository-intelligence treatment case and is excluded from utility
claims. The other 16 pairs have exact control/treatment provider messages,
matching revisions, response bodies, and tool schemas.

## Control responses

Corrected GitHub control runs were `31534502404` (11 cases), `31534732127`
(4 cases), and `31534734333` (1 case). Two earlier attempts failed before
provider dispatch because the workflow reintroduced an unsanitized secret;
those attempts are infrastructure failures and are not counted.

Across the 16 valid pairs:

| Mechanical comparison | Count |
| --- | ---: |
| Treatment action contains a payload anchor, control does not | 2 |
| Control action contains a payload anchor, treatment does not | 1 |
| First proposed action equivalent | 1 |
| Different first action, neither side an anchor proxy | 12 |

The per-case action comparison is deliberately weak evidence. It does not
inspect hidden reasoning, assert acknowledgement, or establish that GT caused
a solve. The 12 indeterminate cases may reflect temperature-1 sampling,
different action choices that are both reasonable, or an anchor that is only
useful after later observations. With `step_limit=1`, later trajectory use is
not observable by design.

## What this proves

1. GT can produce a grounded payload in the first eligible provider request.
2. The exact no-GT request and treatment request are replayable and differ only
   by the recorded payload.
3. A same-model control response can be collected without an extra agent tool
   action.
4. Action-anchor overlap is measurable without markers or hidden-reasoning
   inspection.

## What this does not prove

It does not prove model causality, task success, no regression, or efficiency.
The 16-case set is below the provisional 20-case target because only 16 of 30
deterministically selected tasks produced a first-intervention pair. No more
capture slices are authorized in this bounded pass; increasing the count by
selecting tasks from observed outcomes would be outcome-tuning.

## Next gate

Keep the frozen runtime unchanged. Decide whether to accept this mechanism-level
witness as sufficient to freeze GT, or authorize a separate matched end-to-end
outcome smoke. Do not start the 89-task benchmark from these data.
