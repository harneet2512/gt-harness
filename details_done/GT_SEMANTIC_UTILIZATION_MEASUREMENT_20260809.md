# GT semantic utilization measurement

## Why `anchor_followed` was insufficient

The legacy flag in `eval/gt_central_agent.py` checks only the first command
returned after a delivery and uses literal path/symbol substring matching. It
cannot see a later action, a later action in the same model-response batch, or
the typed operation/targets already produced by the shell parser.

It is retained for compatibility but is no longer the primary utilization
metric.

## New measurement

`gt_engine/trajectory_utilization.py` adds a bounded semantic tracker. It:

1. consumes the existing typed `ProposedAction` objects;
2. matches certified evidence paths to normalized action targets;
3. accepts symbol-only matching only for a known typed operation;
4. inspects every action in the same response and later actions for five model
   calls or ten actions;
5. stops at a source-revision change, because revision-bound evidence is stale;
6. records `same_response`, `deferred`, `stale_source`, or `no_match`.

This is behavioral trajectory alignment, not model acknowledgment or causal
proof. Causal usefulness still requires a matched payload-on/payload-off
ablation.

## Archived smoke replay

The read-only command

```text
python scripts/central_semantic_utilization.py D:\\gt_runs\\31288984308
```

produced:

| classification | count |
|---|---:|
| same response | 14 |
| deferred | 5 |
| stale source before a possible later use | 12 |
| bounded no-match | 0 |
| total deliveries | 31 |

Thus 19/31 deliveries had a typed semantic target match before becoming
stale. The earlier 13/31 number was a first-command string-overlap proxy and
must not be used as the utilization result. The 12 stale rows are not called
ignored: their revision-bound evidence expired after authored source changed.

The replay writes an optional JSON report only when `--json` is supplied and
does not modify the archived receipt or trajectory.
