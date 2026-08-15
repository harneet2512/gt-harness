# GroundTruth Agent Retrieval Bench results

Date: 2026-08-11

## Authoritative run

- GitHub workflow: `31517629497`
- Frozen retrieval/runtime commit: `433c330d0f34e6d80c1d251366de9bf8105f1b93`
- Provider-free certification: `31517629386` (passed)
- Dataset: pinned ARB v2 at `07014c986f3deadb1548c62b32c0ffbe6a81465d`
- Rows: 427/427, with 345 positive and 82 no-gold cases
- Repositories: 25
- Execution: 20 balanced GitHub shards, 21--22 cases each
- Missing/extra rows: 0/0
- Graph status: 427/427 `source_backed`
- Dense failures: 0/427
- Gold isolation: redacted inputs only; evaluator joined gold after prediction

The superseded full run `31480339522` measured the pre-repair path and is not
the current GT score. The cancelled run `31516933743` exposed a 7--88 row shard
imbalance and produced no accepted aggregate result.

## Primary metrics

| View | MRR | Recall@20 | BCY@8K | Meaning |
| --- | ---: | ---: | ---: | --- |
| Ranked top 20 | **0.4372** | **0.7072** | **0.5198** | Retrieval ordering before delivery policy |
| Delivered payload | **0.4207** | **0.4990** | **0.4734** | Bounded context GT would actually expose |

The delivered view contains at most complete, supported chunks within the
1,200-token payload limit. It is the relevant retrieval measurement for a
model-facing GT observation; ranked top-20 remains necessary to diagnose
candidate generation and ranking separately from delivery.

## Comparison to the published ARB values supplied for this evaluation

| Retriever | MRR | Recall@20 | BCY@8K |
| --- | ---: | ---: | ---: |
| **GT ranked** | **0.4372** | **0.7072** | **0.5198** |
| **GT delivered** | **0.4207** | 0.4990 | **0.4734** |
| Qwen3-Embedding-4B | 0.2379 | 0.6306 | 0.3409 |
| Qwen3-Embedding-8B | 0.2336 | 0.7029 | 0.3732 |
| RepoMap | 0.2158 | 0.6333 | 0.3788 |
| Lexical | 0.1574 | 0.4940 | 0.2650 |
| BM25 | 0.1520 | 0.4452 | not provided in the supplied table |

On the common pinned metric definitions, GT ranked leads the supplied systems
on all three primary metrics. The delivered payload leads on MRR and BCY@8K,
but its Recall@20 is below the dense and RepoMap ranked lists because delivery
is deliberately bounded to a much smaller context.

## Positive-subset breakdown

| Subset | Cases | Ranked MRR | Ranked R@20 | Ranked BCY@8K | Delivered MRR | Delivered R@20 | Delivered BCY@8K |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| code2test | 106 | 0.4328 | 0.7396 | 0.4733 | 0.4104 | 0.4811 | 0.4434 |
| comment2context | 80 | 0.2882 | 0.4917 | 0.3417 | 0.2310 | 0.2708 | 0.2292 |
| edit2ripple | 58 | 0.3604 | 0.5675 | 0.4368 | 0.3247 | 0.3879 | 0.3822 |
| trace2code | 101 | 0.6040 | 0.9241 | 0.7574 | 0.6370 | 0.7624 | 0.7508 |

`comment2context` is the weakest remaining positive subset. This is a result,
not authorization to add another benchmark-tuned retrieval generation.

## Selective/abstention result

The active delivery policy returned context on 418/427 cases and abstained on
9. Of those abstentions, 6 were true no-gold cases and 3 were positive cases:

- abstention precision: 66.67%;
- no-gold abstention recall: 7.32%;
- positive pass rate: 99.13%;
- coverage: 97.89%;
- balanced accuracy: 0.5322;
- active selective success@20: 0.4895;
- always-return delivered selective success@20: 0.4754.

Thus active abstention is conservative and slightly improves selective
success, but it does not solve ARB's abstention problem. The official
repo-grouped out-of-fold score threshold abstains on 57/82 no-gold cases but
also 122/345 positives. It lowers ranked selective success from 0.6487 to
0.5621 and leaves delivered selective success unchanged at 0.4754. A simple
score threshold is therefore rejected.

## Payload and latency

- Payload tokens: 382,630 total; mean 896.1; maximum 1,200.
- Query latency: p50 19.352 s; p95 22.378 s; p99 23.118 s.
- Repository preparation: mean 0.319 s.
- Retrieval: mean 17.417 s.
- Snowflake dense channel: mean 17.018 s (96.0% of mean retrieval time).
- Exact/lexical/BM25/structural mean channel latencies: 5.6/7.5/11.5/0.3 ms.
- Dense cache: 1,329 document hits and 12,321 misses.
- Network/provider calls for embeddings: zero.

The earlier repeated full repository and graph scans are gone. The remaining
latency is the pinned Snowflake ONNX CPU inference itself, run as one bounded
batch of at most 32 candidate documents. It is not a hidden per-document model
call loop.

## Gate decision

**Positive retrieval gate: PASS.** Candidate generation, hybrid ranking, and
bounded delivery are now competitive on the complete benchmark.

**Abstention gate: PARTIAL.** GT can abstain and its active policy does not
collapse positive coverage, but no-gold recall remains low. This limitation is
frozen as an experimental finding; the one allowed generalized retrieval
repair has been used. No additional ARB-tuned policy change is permitted before
end-to-end evaluation.

ARB proves retrieval behavior only. It does not prove that a frontier model
uses the payload, that task resolve rate increases, or that latency is
acceptable inside every live coding trajectory. Those claims require the
already-specified runtime proof and controlled end-to-end benchmark.
