# DeepSWE v1.1 output contract

The benchmark denominator is the pinned task manifest (113 tasks for the full
catalog; the 20-task smoke uses its explicit manifest). A task is **graded**
only when its nested Pier `result.json` contains an explicit verifier reward
`0` or `1`; `1` is solved and `0` is unsolved. Missing, malformed, or timed-out
verifier results are **ungraded**, never silently converted to unsolved.

Each task artifact must retain the Pier result tree and task log. The merged
submission contains task identity, reward, verifier exception/censor status,
agent exit status, and the observed model/provider identity. Resource metrics
are retained per task: provider calls/assistant steps, total input/output and
total tokens, provider cost (or explicit missing-cost), wall time, and the
configured step/time multiplier. The independent `PARTIAL` artifact publishes
returned/graded/ungraded/solved/unsolved counts and rows even when diagnostics
or the final merge job fails.

The partial artifact is reporting-only: it never changes the denominator,
verifier, task selection, model, step budget, or benchmark timeout.
