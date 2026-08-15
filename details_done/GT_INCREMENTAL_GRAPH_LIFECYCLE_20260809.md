# Incremental graph lifecycle repair — 2026-08-09

## Decision

GroundTruth's repository graph is now refreshed from every finalized
workspace transition before the next model request.  The graph is not treated
as a startup-only artifact.

## Root causes found

1. `WorkspaceSensor` and the agent's transition classifier primarily used
   path suffixes.  A new or modified extensionless source (for example a
   shebang script) had no path-only language identity, so its captured content
   was not classified as validation-relevant and never reached the repository
   session.
2. `RepositorySession.apply_transition()` repeated the path-only check.  Even
   when the indexer's `refresh_index_files()` could resolve a file from its
   bounded content prefix, the session did not enqueue it.  Deleting such a
   file could therefore leave stale nodes in an otherwise current graph.
3. A hard eight-file capture slice could drop the suffix of a multi-file
   action, making the mirror incomplete.
4. A content-signature source edited into ordinary data was not recognized as
   a removal, so the old graph node could survive under `source_revision_only`.

## Implementation

- `WorkspaceSensor` now captures extensionless candidates within the existing
  `max_hashes`, `max_hash_bytes`, and per-file size bounds.  The shared
  resolver decides whether captured bytes are source; non-source files do not
  advance source revision.
- `MiniSweCentralAgent` passes after-action content (or captured pre-action
  content for deletions) to `classify_change()` at both transition and active
  graph-path boundaries.
- `RepositorySession.apply_transition()` resolves created/modified files from
  the same 65,536-byte content bound as the indexer.  It queues indexable files
  for incremental refresh, forces a full rebuild for indexed-file deletion or
  source-to-data transitions, and preserves safe-path/mirror-completeness
  fail-closed behavior.
- The arbitrary eight-file capture cap was removed.  The configured file and
  byte limits remain the resource bound.

## Provider-free proof

Added tests cover:

- extensionless source creation into an existing graph;
- extensionless source deletion and stale-node removal;
- extensionless source-to-data modification;
- all nine extensionless sources in one finalized transition;
- sensor capture of a shebang source without task-name hints;
- existing suffix-based incremental modification and recovery paths.

Commands run with the certified local index binary:

```text
python -m pytest -q tests/test_gt_central_runtime.py tests/test_gt_repository_intelligence.py
126 passed
python -m pytest -q tests/test_gt_central_agent.py
58 passed
```

The full central-agent suite is being rerun after the intentional host-scan
metric change.  No paid smoke or 89-task run was started for this repair.

## Post-smoke deduplication correction

The authorized diagnostic smoke `31297108410` exercised the lifecycle on nine
completed tasks. Source-backed repositories reached a current graph and
recorded incremental refreshes. The Scheme receipt exposed a separate
frontier-accounting defect: two graph call sites for `Pair` had different line
locations but the same line-independent semantic `claim_id`, so both were
selected in one provider frame. The receipt correctly rejected this as
`duplicate_frontier_fact_delivery` and `duplicate_frontier_claim_delivery`.

`compile_incremental_frontier()` now deduplicates candidates by both semantic
claim identity and physical location, retaining the first deterministic role
candidate. This prevents one-shot claims from being emitted twice while still
preserving the existing definition/anchor coalescing behavior. A regression
test covers multiple call sites for one semantic claim.

This correction is provider-free and does not alter graph construction, source
revision, or action execution. The paid smoke remains diagnostic until the
missing portfolio job and this corrected receipt path are separately verified;
no 89-task run is authorized.

## Operational semantics

```text
model action executes
  -> finalized workspace sensor captures bounded changed content
  -> typed content-aware classification
  -> mirror transition
  -> incremental index, or full rebuild for removals
  -> graph evidence current at next provider request
```

If capture, indexing, or revision validation fails, the engine fails closed and
does not expose stale graph evidence.  This repair proves lifecycle coverage;
it does not claim a benchmark outcome or efficiency improvement.
