# Draft: upstream bug report for `harneet2512/groundtruth`

Ready-to-file text for the `gt-index` derivation-invariant bug found while
indexing arbitrary repositories for the cloud coding agent. **Not yet filed** —
copy the block below into a new issue on `harneet2512/groundtruth`.

Context and the local workaround: `cloud/producer/README.md`,
`cloud/producer/0001-skip-invalid-candidates.patch`,
[docs/cloud-gt-run.md](cloud-gt-run.md) §1–§3.

---

**Title:** `gt-index`: one derivation-invalid `variable_type_flow` candidate
aborts the entire resolution graph

**Labels:** bug, indexer, robustness

---

## Summary

`store.AttachResolutionGraphTx` returns an error on the **first** candidate that
fails `validateCandidateDerivation`. The caller turns that into
`abortStagedBuild`, so a single unpublishable candidate discards an otherwise
complete, successfully parsed index. On `pallets/click@main` this is 27
candidates out of thousands taking down a graph of 62,839 nodes and 79,216
edges.

The invariant has **no environment gate**, so there is no way to relax it at
runtime — a consumer that indexes user-supplied repositories has to patch the
producer or give up on indexing.

## Where

At commit `0aadb1b9111f70f3c6b8874e1b8eff927397d22b`:

* `gt-index/internal/store/sqlite.go:390` — `validateCandidateDerivation`,
  reached from `AttachResolutionGraphTx`. The candidate loop that calls it is
  near `sqlite.go:1356`:

  ```go
  for _, candidate := range row.Candidates {
      if err := validateCandidateDerivation(derivationKind, candidate); err != nil {
          return fmt.Errorf("callsite %s candidate %d: %w", c.CallsiteID, candidate.Ordinal, err)
      }
      ...
  }
  ```

* `gt-index/cmd/gt-index/main.go` (~`:947`) turns the returned error into
  `abortStagedBuild`, so nothing is written at all.

## Reproduce

Any full build over the repository reaches it — Pass 3 is where it fires:

```bash
git clone https://github.com/pallets/click
gt-index -root click -output graph.db -max-files 10000 -workers 2 -closure=true
```

(that argv is what the harness itself runs — `gt_engine/indexer.py`,
`_index_command`). Observed with a binary built from
`0aadb1b9111f70f3c6b8874e1b8eff927397d22b` with the project's own recipe
(static CGO, `sqlite_fts5`, Go 1.22.5; `graph_schema_version`
`v15.2-trust-tier`):

```
Pass 1: discovering files ... Found 131 source files
Pass 2: parsing 131 files (2 workers)... Parsed 131/131 files in 1.029s
  Inserted 1361 nodes ... Extracted 1361 definitions, 580 imports
Pass 3: resolving 5587 call references... Resolved 2757/5587 calls in 325ms
... attach graph-native resolution evidence: callsite <id> candidate 0:
    variable_type_flow requires typed source or propagation facts
```

Exit code 1, no graph written. The candidate that trips it is a
`variable_type_flow` derivation missing `FlowSourceStableIDs` /
`FlowEdgeStableIDs`. With the abort removed, the same repository produces a
complete graph and reports **27** such candidates:

```
[WARN] 27 resolution candidates skipped as abstentions
Done in 16.758s / Files: 131 / Nodes: 62839 / Edges: 79216
```

`psf/requests@main` reproduces the same class of failure.

## Why this is a bug rather than a policy

An unpublishable candidate is an **abstention**, not evidence of a corrupt
graph. The producer already knows how to say "I do not have facts for this";
`validateCandidateDerivation` failing means exactly that, and the correct
response is to omit the candidate — not to discard the 2,700 calls that *were*
resolved with complete provenance.

Aborting is defensible on a **fixed benchmark corpus**, where an underivable
candidate means the producer is wrong and the measurement is void. It is not
defensible for a producer pointed at arbitrary user repositories: one malformed
candidate out of thousands leaves the consumer with no repository intelligence
at all, and the failure mode is total rather than proportional.

## Proposed fix

Either of these; the first is preferred.

1. **Abstain and skip.** Log the offending callsite and candidate, increment a
   counter, `continue`, and emit one summary line (`N resolution candidates
   skipped as abstentions`) at the end of `AttachResolutionGraphTx`. The
   candidates that *were* valid are written with exactly the same facts as
   today; nothing else changes. This is what
   `cloud/producer/0001-skip-invalid-candidates.patch` does locally — a ~10-line
   diff against `sqlite.go`, offered as a starting point.

   To keep the abstention auditable rather than silent, the skip count belongs
   in the build receipt / `-build-info` output as well, so a consumer can see
   how much was dropped.

2. **Gate the invariant on the environment.** Keep the abort as the default,
   but let a caller opt out — e.g. `GT_INDEX_STRICT_DERIVATION=0`, or infer it
   from the existing benchmark-scope signals (`GT_TASK_ID` /
   `GT_PRODUCT_SOURCE_SHA`), so benchmark builds stay fail-closed while
   `local_unbound` builds degrade gracefully.

What we would rather not keep doing is shipping a patched producer: it forces a
build-time fork, and it forces a deliberate identity divergence
(`main.commitSHA` is stamped `<commit>+cloud.1`) so the patched binary can never
be mistaken for the certified one.

## Impact

Every consumer indexing repositories outside the benchmark corpus. For the GT
cloud coding agent it is the difference between `gt_status: ready` and
`gt_status: unavailable` for a mainstream Python repository — i.e. the whole
GroundTruth value proposition, off, on a repository that parses cleanly.
