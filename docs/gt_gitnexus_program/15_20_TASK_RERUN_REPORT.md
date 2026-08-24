# Same-20 validation report

Current repaired subject: `8931876541ec82ec96799f6c4462b5c0726e4518`.

## Decision

The latest live GT-only run failed final attestation. The demonstrated causes are
fixed and exact-SHA provider-free certification passes, but another paid run is
`NOT_AUTHORIZED`. Release remains `HOLD`.

## Executed cohort

Workflow [32680131105](https://github.com/harneet2512/gt-harness/actions/runs/32680131105)
ran the frozen repair20 task set, Mini-SWE-Agent 2.2.8, Ox Alpha through OpenRouter,
one attempt, and 20-way parallelism against subject `b6e1609`.

- raw reward: 12/20;
- valid terminal treatment reward: 11/20;
- artifacts: 20 Harbor results, 20 adapter receipts, 20 GT receipts, 20 full
  trajectories;
- GT lifecycle: 18 terminal receipts and two invalid `RUNNING` receipts;
- trajectory usage: 691 API attempts, 33,554,636 input+output tokens;
- delivery: 14 active treatments, six explicit abstentions, 41 GT packets, 31
  weak inspection-only updates suppressed.

No fabricated or dummy source text was found. Seven file-path-derived facts across
five tasks incorrectly inherited symbol-level authority. Current code renders those
facts as file identity only.

## Failure-to-fix ledger

| Observed failure | Root cause | Current deterministic fix |
| --- | --- | --- |
| Scheme reward 1 but GT receipt `RUNNING` after exit 137 | adapter raised without terminalizing durable checkpoint | atomic external supervisor changes only `RUNNING` to evidenced `ERROR` and reconciles trajectory usage |
| Corewars receipt `RUNNING` at Harbor timeout | retries/provider/tool operation could cross inner deadline | one model attempt; provider timeout <= 60 s or remaining time; shell timeout <= 30 s or remaining time; 90 s shutdown gap |
| portfolio/sanitize call mismatch | format-error attempts have no assistant message | compare receipt calls to `trajectory.info.model_stats.api_calls` |
| exact path displayed arbitrary symbol/line | compiler promoted representative symbol for a path-only anchor | path-only anchors emit `file_identity`, line 1, empty symbol/excerpt |

The real Scheme receipt was replayed through the new supervisor and became a
terminal, trajectory-consistent error. Full local provider-free Python, targeted
lint, and Go passed. Clean Codespaces certification passed all 13 gates.

## Causal boundary

The frozen local repair20 baseline solved 17/20 but used another model. Its outcome,
1,041 calls, and 65,625,578 tokens are directional context only. No same-model
GT-off run exists, so neither solve-rate regression nor efficiency uplift is
causally assigned to GroundTruth.
