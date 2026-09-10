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
candidate that fails derivation validation is logged and dropped as an
abstention rather than aborting the transaction, the surviving candidates are
written with exactly the facts they had before, and the number dropped is
recorded in the graph receipt.

## What the patch does (cloud.2)

The patch is the port of upstream PR
[harneet2512/groundtruth#6](https://github.com/harneet2512/groundtruth/pull/6)
(`fix/gt-index-skip-invalid-candidates`, fixes issue #5) onto the pinned
commit in `PRODUCER_COMMIT`. `gt-index/internal/store/` is byte-identical
between that commit and the PR's base `gt-trial`, so the upstream diff applies
verbatim — there is no cloud-local reimplementation any more.

In `AttachResolutionGraphTx` (`gt-index/internal/store/sqlite.go`):

- Candidates are **partitioned before `prepareResolutionV2`**. Only the
  derivable ones reach graph preparation, so an abstained candidate produces
  no `CANDIDATE_TARGET` edge, no `DerivationFact` node and no VTA flow facts.
- The skip count is persisted to `project_meta` under
  `store.GraphSkippedCandidatesKey` = **`graph_resolution_skipped_candidates`**,
  written on every attach (including when zero were skipped, so "0" and "not
  recorded" stay distinguishable), with a reader `(*DB).GraphSkippedCandidates()
  (int, bool, error)`.
- Tests: new `candidate_abstention_test.go`, and the contract test
  `TestAttachResolutionGraphRejectsKnownDerivationMissingTypedDetails` is
  renamed to `…AbstainsKnownDerivationMissingTypedDetails` and now asserts the
  graph is published, zero candidate edges are written, and the receipt counter
  reads 1.

### Difference from cloud.1

cloud.1 was a local one-hunk hack that only `continue`d in the **final insert
loop**. The abstained candidate was still passed to `prepareResolutionV2`, so
its `CANDIDATE_TARGET` edge, `DerivationFact` node and flow facts were written
to the graph — and `QueryAttachedCandidates` joins `CANDIDATE_TARGET`, so an
abstained candidate could still surface as attached evidence with no backing
derivation. cloud.1 also had no persisted receipt: the only trace was a log
line in `stderr_tail`. cloud.2 fixes both.

## Divergence from the certified producer

This binary is **not** the certified benchmark producer and must never be used
as one. To make that unmistakable at runtime, the build stamps
`main.commitSHA` as `<PRODUCER_COMMIT>+<PRODUCER_VARIANT>`, currently
`…+cloud.2`, so `gt-index -build-info` reports an identity that can never
match the pinned manifest. The patch also adds test files, so
`main.sourceFingerprint` differs from the certified build as well.

The benchmark path is unaffected: `vendor/gt-index-linux-amd64` stays in the
repository, unpatched, and the product workflow keeps rebuilding and byte-
comparing it. Do not apply this patch to the benchmark producer.

## Files

- `PRODUCER_COMMIT` — upstream commit of `harneet2512/groundtruth` that the
  vendored binary was built from, and that the patch is written against.
- `PRODUCER_VARIANT` — the variant stamp appended to `main.commitSHA`
  (`cloud.2`). `cloud/Dockerfile`'s `ARG PRODUCER_VARIANT` should read this
  file so the stamp lives next to the patch it describes.
- `0001-skip-invalid-candidates.patch` — the upstream PR #6 diff rebased onto
  `PRODUCER_COMMIT`, applied by `cloud/Dockerfile`'s `producer` stage with
  `git apply`.

## Regenerating the patch

```sh
git clone https://github.com/harneet2512/groundtruth gt && cd gt
git fetch origin pull/6/head:pr6
git diff gt-trial..pr6 -- gt-index/ > 0001-skip-invalid-candidates.patch
git worktree add --detach ../at "$(tr -d '[:space:]' < .../PRODUCER_COMMIT)"
cd ../at && git apply --check ../gt/0001-skip-invalid-candidates.patch
cd gt-index && CGO_ENABLED=1 go test -tags sqlite_fts5 ./internal/store/...
```

Re-verify after every upstream force-push to PR #6, and bump
`PRODUCER_VARIANT` whenever the patch content changes.

## Upstream status

Filed as issue #5 and fixed by PR #6 (`fix/gt-index-skip-invalid-candidates`,
base `gt-trial`). Once that merges into the commit this image pins, the patch
and this directory can be deleted.

> `gt-index` aborts the entire resolution graph on a single derivation-invalid
> `variable_type_flow` candidate. `store.AttachResolutionGraphTx` returns on the
> first `validateCandidateDerivation` failure, so one candidate lacking
> `FlowSourceStableIDs`/`FlowEdgeStableIDs` discards a whole successfully-parsed
> index. Reproduce with `pallets/click@main`. An unpublishable candidate is an
> abstention, not a corrupt graph: it should be dropped with a warning so
> indexing arbitrary repositories stays possible.
