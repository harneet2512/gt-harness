# GroundTruth Retrieval Bench Contract

## Pinned source

- Official repository: `eyuansu62/agent-retrieval-bench`
- Pinned commit: `07014c986f3deadb1548c62b32c0ffbe6a81465d`
- Official release tag reference checked: `v0.2.1`
- Current benchmark: 427 rows, 345 positive and 82 no-gold across the four
  workflow subsets and two abstention strata.

The checked-out source is retained under the ignored final-execution artifact
directory. Official gold data is never passed to `scripts/arb_adapter.py`.

## Adapter contract

`scripts/arb_adapter.py` accepts only redacted JSONL rows containing:

```text
sample_id
repository
base_commit
instruction or query
optional given_files/active_paths
```

Gold, expected files, patches, fixes, labels, and evaluator fields are rejected
recursively. The adapter constructs the same typed `RetrievalState` and
`HybridRepository` used by the optional central-runtime retrieval frame. It
runs exact path/symbol, lexical, BM25, local Snowflake ONNX dense, and GraphDB
structural channels independently, applies equal RRF (`k=60`) over unique
files, and records both the top-20 ranked candidates and the bounded
three-item selected evidence set. It contains no task IDs, gold paths, or
benchmark-specific ranking weights.

The Snowflake asset is provisioned once by GitHub Actions from immutable model
revision `7802add0519e4bf94c46ef23552176697c7a1ac7`; model SHA-256 must equal
`564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
Inference is local ONNX CPU inference, never an embedding API. Dense absence
fails the full-hybrid workflow instead of silently reporting graph-only
results as hybrid.

Index construction latency and post-index retrieval latency are separate.
Predictions are deterministic JSONL and include graph status, graph/source
revision, exact checkout spans/text, per-channel ranks and receipts, dense
backend identity/digest, abstention reason, selected token/payload size, and
index/query timing. Snapshot reuse is explicit in each row through
`index_cache_hit` and `repository_cache_hit`; cached rows report zero per-row
index-build latency so aggregate latency does not charge the same preparation
work repeatedly.

The checkout runner also emits flushed `[arb-progress]` lines while a shard is
running: shard/group assignment, repository and commit, sample ID, completed
row count, graph status, delivered-evidence count, query latency, and elapsed
time. These lines are observability only and are not parsed as predictions or
included in any benchmark metric. A shard artifact remains complete only after
all of its groups finish.

Preparation is snapshot-scoped: for rows sharing one `(repository,
base_commit)` and source revision, the graph/index receipt and checkout-backed
hybrid document/link corpus are built once and reused. Each row still runs its
own typed intent, query, ranking, selection, and payload compilation. Mixed
source revisions deliberately bypass this cache. This keeps progress timing
honest while preventing repeated index/corpus construction from dominating
the dense retrieval measurement.

## Evaluation rules

- Positive subsets use official file-level MRR, Recall@K, Precision/F0.5, and
  budgeted BCY metrics.
- The report also publishes top-3 precision/recall/F1, Any@1/5/10/20,
  nDCG@5/10/20, task/repository macro views, channel contributions, payload
  characters/tokens, and latency p50/p95/p99.
- Natural and counterfactual no-gold rows are evaluated only in the selective
  retrieval track.
- Candidate universe is the official all-files corpus; task-aware filtering is
  diagnostic only.
- Gold is joined only after prediction output is finalized.
- Query/index failures are substrate failures, not successful abstentions.
- Empty certified retrieval is an explicit abstention.
- ARB results prove retrieval behavior only; they do not prove model reasoning,
  timing in a live agent loop, or task success.
- Ranked and delivered views are both mandatory. Ranking quality cannot hide
  an over-strict delivery gate, and permissive delivery cannot hide poor rank
  order.

## Gate

The retrieval gate requires deterministic complete output, no leakage, valid
base-commit snapshots, matched-budget comparison with reproduced local
baselines, and per-subset reporting. One generalized repair is allowed only
after a repeated failure class is demonstrated across repositories and subsets.
