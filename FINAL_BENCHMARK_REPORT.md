# Final Benchmark Report

Status: `AUTHORIZED_NOT_RUN`

Certified implementation: `79321e0da09174805a0909f69dc695dd129a5ebf`.

The final 20-task experiment has intentionally not been dispatched. No outcome,
solve-rate, cost, or causal claim exists yet for this implementation.

When run, this report must be updated once from the complete Harbor artifact and
full trajectories. Required adjudication for every task:

- official reward and grader result;
- completed/error/timeout status;
- exact graph and dense receipts;
- every GT packet, delivery timing, and source revision;
- whether each fact was source-correct, incorrect, unsupported, stale, duplicated,
  irrelevant, or merely unused;
- whether GT replaced exploration, influenced an edit/test decision, or added noise;
- steps, actions, provider calls, tokens, wall time, graph/dense build time, and cost;
- negative flips or errors attributable to GT;
- comparison to the matching local frozen task entry where available.

The local frozen baseline is useful context but is not a statistically controlled
same-model arm: it used Mini-SWE-Agent 2.2.8 with a different provider/model. Any
comparison must be labeled directional, not causal.

Historical Ox Alpha runs used retired compatibility paths and contained timeout,
receipt-finalization, or delivery defects. They are diagnostic history only and
must not be merged with the final result.
