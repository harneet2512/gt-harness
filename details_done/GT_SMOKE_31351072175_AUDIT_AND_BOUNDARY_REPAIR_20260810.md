# GT smoke 31351072175: audit and boundary repair

## Verdict

The paid ten-task run completed all jobs, but it is rejected as an approved
treatment. It solved 8/10 official verifier tasks. `gpt2-codegolf` was marked
an intelligence substrate failure by the merge gate, and `headless-terminal`
was a new reward loss relative to the frozen GT-off task set. No efficiency or
causal outcome claim is made from this run.

## What the receipts prove

- All task jobs completed without an outer Harbor exception.
- The central loop preflighted every model action in SHADOW and applied PASS.
- Provider request hashes and timing receipts were present; no late or
  predictive delivery was found.
- `headless-terminal`'s one visible `newfile_precedent` payload was delivered
  in the first eligible provider request (call 12, action 11).
- Its semantic-use field was incorrectly recorded as `stale_source` because
  the tracker compared the delivery's workspace `revision` with the later
  source revision. The delivery was source-bound and the intervening changes
  were not authored source changes.
- `gpt2-codegolf` transferred no structurally supported source at task start
  (only model/data artifacts). The model later created Python and Perl helper
  files. The final receipt reclassified the task as source-backed and failed
  graph coverage on four unsupported Perl files. That is a substrate
  applicability bug, not evidence that the original task had a usable graph.

## Repairs

1. Guidance deliveries now record an explicit semantic `source_revision`.
   Utilization prefers that field and only falls back to legacy `revision` in
   archived rows that have no source revision.
2. Repository applicability now retains the task-start source-less state. A
   task that began without supported source remains denominator-excluded even
   if the model writes unsupported-language helpers later. It still receives no
   invented graph facts and remains operationally fail-open.
3. Added a regression test proving workspace-only revision changes do not mark a
   valid source-bound delivery stale.

## Verification

- Focused GT tests: 67 passed.
- Provider-free feature census: all permanent producer/consumer/timing,
  payload, context-accounting, graph-substrate, frontier, and baseline-shield
  lines passed.
- Readiness audit: `READY`.
- Offline replay of all ten smoke trajectories: `REPLAY_OK`.

The repairs require a new commit and a new authorized matched smoke before the
outcome gate can be reconsidered. The 89-task benchmark remains blocked.
