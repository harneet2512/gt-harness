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
recursively. The adapter runs the active GT task-contract extraction, graph
projection, evidence-need construction, and graph ranker. It records both the
full ranked candidates and the bounded three-item selected evidence set.

Index construction latency and post-index retrieval latency are separate.
Predictions are deterministic JSONL and include graph status, graph/source
revision, provenance-bearing candidate rows, abstention reason, and timing.

## Evaluation rules

- Positive subsets use official file-level MRR, Recall@K, Precision/F0.5, and
  budgeted BCY metrics.
- Natural and counterfactual no-gold rows are evaluated only in the selective
  retrieval track.
- Candidate universe is the official all-files corpus; task-aware filtering is
  diagnostic only.
- Gold is joined only after prediction output is finalized.
- Query/index failures are substrate failures, not successful abstentions.
- Empty certified retrieval is an explicit abstention.
- ARB results prove retrieval behavior only; they do not prove model reasoning,
  timing in a live agent loop, or task success.

## Gate

The retrieval gate requires deterministic complete output, no leakage, valid
base-commit snapshots, matched-budget comparison with reproduced local
baselines, and per-subset reporting. One generalized repair is allowed only
after a repeated failure class is demonstrated across repositories and subsets.
