# Cloud producer build

The cloud server image builds the GroundTruth indexer (`gt-index`) **from
source** instead of shipping `vendor/gt-index-linux-amd64`, because the
vendored binary cannot index arbitrary repositories.

## Why

The vendored producer is the *certified benchmark* producer: byte-identical to
the artifact pinned in `config/deepswe_product_bundle_v1.json` and rebuilt from
source by `.github/workflows/deepswe_gt_harness_product.yml`. Its resolution
graph carries a strict derivation invariant: every candidate must satisfy
`validateCandidateDerivation`, and a single violation aborts the whole graph
transaction (`gt-index/cmd/gt-index/main.go` → `abortStagedBuild`).

That is the right behaviour for a fixed benchmark corpus, where an
underivable candidate means the producer is wrong and the measurement is void.
It is the wrong behaviour for a product that indexes whatever repository a user
pastes in: one malformed candidate out of thousands takes the entire index down
and the session degrades to no repository intelligence at all.

Indexing `https://github.com/pallets/click` on the cloud deployment parsed 131
files, built 1361 nodes and resolved 2757 calls, then exited 1 with:

```
attach graph-native resolution evidence: callsite <id> candidate 0:
variable_type_flow requires typed source or propagation facts
```

The invariant has no environment gate, so there is no way to relax it at
runtime. `0001-skip-invalid-candidates.patch` relaxes it at build time: a
candidate that fails derivation validation is logged and skipped as an
abstention rather than aborting the transaction, and one summary line reports
how many were skipped. Nothing else changes — the same candidates that were
valid before are written with the same facts.

## Divergence from the certified producer

This binary is **not** the certified benchmark producer and must never be used
as one. To make that unmistakable at runtime, the build stamps
`main.commitSHA` as `<PRODUCER_COMMIT>+cloud.1`, so `gt-index -build-info`
reports an identity that can never match the pinned manifest.

The benchmark path is unaffected: `vendor/gt-index-linux-amd64` stays in the
repository, unpatched, and the product workflow keeps rebuilding and byte-
comparing it. Do not apply this patch to the benchmark producer.

## Files

- `PRODUCER_COMMIT` — upstream commit of `harneet2512/groundtruth` that the
  vendored binary was built from, and that the patch is written against.
- `0001-skip-invalid-candidates.patch` — the one-file diff, applied by
  `cloud/Dockerfile`'s `producer` stage with `git apply`.

## Upstream bug to file

> `gt-index` aborts the entire resolution graph on a single derivation-invalid
> `variable_type_flow` candidate. `store.AttachResolutionGraphTx` returns on the
> first `validateCandidateDerivation` failure, so one candidate lacking
> `FlowSourceStableIDs`/`FlowEdgeStableIDs` discards a whole successfully-parsed
> index. Reproduce with `pallets/click@main`. An unpublishable candidate is an
> abstention, not a corrupt graph: it should be dropped with a warning (or gated
> by an environment variable) so indexing arbitrary repositories stays possible.
