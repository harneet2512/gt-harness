# Repository-intelligence substrate repair — 2026-08-09

## Failure reproduced

In paid smoke `31294018807`, `gpt2-codegolf` began with only model/data
artifacts. The initial source-only mirror correctly contained zero source files
and the first index status was `no_supported_source`. The model then authored
`gpt2.c`; later refreshes produced a current source-backed graph and the final
repository session was healthy. The receipt nevertheless carried
`graph_degraded_fallback=true`, and the merge workflow treated the historical
transient failures as an invalid treatment.

This was a gate-state bug, not permission to fabricate graph facts or to accept
an actually unhealthy final graph.

## Repair

Commit: `5b32295`

1. `eval/gt_central_agent.py` now computes `graph_degraded_fallback` from the
   final current graph-gate failures. Initial failures remain in
   `repository_graph_gate_initial_failures` and
   `repository_intelligence.transient_failures` for audit, but clear when a
   source-bound refresh recovers.
2. `.github/workflows/tb2_miniswe_central.yml` no longer rejects a task merely
   for recovered `repository_intelligence_transient_failures`. It still fails
   closed on final status, final graph-gate failure, degraded fallback, and
   recorded current failures.
3. Tests cover both a source-created-after-empty-mirror repository session and
   the merge-gate rule.

## Safety boundary

The graph gate remains strict for a current failure. A source-backed final graph
must still have a valid certified database, current source revision, complete
coverage, and valid intelligence. Source-less tasks remain explicitly
not-applicable and never receive invented facts. Historical failures are
observable but do not poison a healthy final substrate.

## Verification

With the vendored certified index binary:

```powershell
$env:GT_INDEX_BINARY='D:\gt-harness\vendor\gt-index-src\gt-index.exe'
$env:GT_INDEX_REQUIRE_COVERAGE='1'
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gt_repository_intelligence.py::test_repository_session_recovers_when_source_is_created_after_initial_empty_mirror `
  tests/test_gt_central_agent.py::test_recovered_initial_graph_failure_does_not_remain_degraded `
  tests/test_gt_central_agent.py::test_merge_gate_does_not_promote_recovered_transient_repository_failures
```

Result: `3 passed`.

The widened provider-free suite passed `126 tests`. The exact pushed-commit
certification then passed:

* direct and module central census, including `REPOSITORY_SUBSTRATE_PROVEN`;
* repository-intelligence substrate and pinned language contract;
* workflow/readiness audit: `READY`;
* exact pre-smoke gate: `SMOKE_APPROVED`.

The full provider-free census, readiness audit, and pre-smoke gate are still
required before another paid smoke; they now pass at this commit. No paid run or
89-task run was started by this repair.
