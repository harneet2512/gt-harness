# GT compaction elision/recap deep audit — 2026-08-14

Commit: `2649bb6` (initial implementation) + this audit's repair commit.
Audit target: `gt_engine/provider_view.py` Phase A (stale-read/validation
elision) and Phase B (typed recap receipts), plus their live-wiring into
`gt_engine/central_runtime.py` and `eval/gt_central_agent.py`.

## Executive verdict

**The initial implementation had two real live-path defects that unit tests
could not catch.** They are now fixed and locked by regression tests:

1. **FINDING A (critical):** `CentralFeatureRuntime.progress_ledger()` exposed
   `recent_reads` filtered to the current source revision only
   (`central_runtime.py:4482-4487`). `_stale_read_elidable` needs the
   *old-revision* read observation to hash-match a stale body, and
   `_turn_semantic_parts` needs it for recap read-identity. The live ledger
   never exposed it, so **stale-read elision and read recap-identity were dead
   code in production** — they fired only in hand-built unit fixtures.
2. **FINDING B (correctness):** the ledger computes `output_hash` with
   `.encode("utf-8","replace")` (`central_runtime.py:3130`, `:3461`) but
   `_raw_output_hash` hashed `extra.raw_output` with `.encode("utf-8",
   "surrogatepass")`. For normal UTF-8 both agree, but on lone surrogates the
   hashes diverge, so identity could silently fail (or, if the ledger ever
   changed encoding, mismatch). Aligned to `"replace"` on both sides.

FINDING C (documented, not a bug): `_latest_failure` is never cleared, so
`unresolved_failure` persists in the ledger. The elision guard
(`_stale_validation_elidable`) compares `unresolved_failure.source_revision`
against the *current* revision, so a stale (old-revision) failure never blocks
elision of a failure superseded by a pass at the current revision, while a
still-current failure correctly does. This is conservative-safe.

## Fixes applied

- `gt_engine/provider_view.py`:
  - `_raw_output_hash` now uses `"replace"` encoding, matching the ledger.
  - `_recent_read_observations` reads `active_state["read_history"]` first
    (the full, all-revision read ledger) and falls back to
    `active_state["recent_reads"]`. It also filters to `observation_kind` in
    `(None, "", "read")` so **search-anchor observations can never authorize
    read elision**.
- `gt_engine/central_runtime.py`:
  - `progress_ledger()` now emits `"read_history"` (all bounded `_recent_reads`
    with a non-empty source revision) alongside the current-revision-filtered
    `"recent_reads"`. The provider-visible state frame keeps consuming
    `recent_reads` (current-only) exactly as before; `read_history` is used
    only by elision/recap identity and never becomes a context fact (it is not
    in `_FACT_KIND_BY_STATE_KEY`).
- `tests/test_provider_view_compaction.py`: added 5 witnesses:
  - `test_live_ledger_shape_fires_elision_via_read_history` (the exact
    progress_ledger shape now produced live),
  - `test_search_anchor_observations_never_authorize_elision`,
  - `test_recap_read_identity_works_with_live_filtered_ledger`,
  - `test_real_runtime_ledger_enables_elision_end_to_end` (drives the real
    `CentralFeatureRuntime`),
  - `test_real_runtime_ledger_recap_read_identity`.

## Audit A — real archived trajectory replay

Source: DeepSWE diagnostic run `31557391617` task
`abs-module-cache-flags__AHCSYP4`
(`artifacts/deepswe_smoke_31557391617/.../miniswe_trajectory.json`): 339
messages, 167 tool bodies, 168 assistant actions, real Mini-SWE
`extra.raw_output`/`returncode` fields. Replayed through the actual
`build_provider_view` compact path with a forced epoch
(`scripts/audit_compaction_elision_replay.py`).

| Check | Result |
|---|---|
| Forced-epoch compaction ran | 166 old bodies cleared |
| Markers char+sha256 consistent with cleared bodies | 166/166 |
| No marker contains raw command text | yes |
| Unique assistant reasoning removed | 0 |
| Recap receipts on real bodies | 12 (read-identity from read_history) |
| Recap fallbacks | 0 |
| Below-trigger byte-identical | yes |
| Below-trigger no elision/recap | yes |
| `_raw_output_hash` == ledger `output_hash` (replace) | 40/40 |
| Read-history resolution excludes search anchors | yes |
| **Stale-read elision on a real body** | real `go.mod` read (5,461 chars, rev s0) elided when re-read at s17; old body removed; typed marker `path=go.mod revision=s0 reread_revision=s17 chars=5461 sha256=...` |

The real run never reached a compaction epoch (max body 8,022 chars; total
141KB < 70% trigger), so `stale_reads_elided: 0` on the unmodified real
trajectory is the correct abstention, not a missed trigger.

## Audit B — invariant properties (60 randomized cases)

`scripts/audit_compaction_elision_properties.py`, bodies up to 15KB (below the
pre-existing 20KB per-observation bound so the byte-identity check isolates
this feature), mixed read/validation turns, forced epochs.

| Property | Result |
|---|---|
| Below-trigger view byte-identical | 60/60 |
| No command text in any marker | 60/60 |
| Assistant messages untouched | 60/60 |
| Marker chars/sha256 consistent | 360/360 |

## Audit C — adversarial fuzz

| Case | Result |
|---|---|
| Missing/absent `extra.raw_output` — no crash, no fabricated elision | pass |
| Byte-identical content across distinct paths — no cross-path elision | pass |
| `keep_recent_turns=0` clamped to 1 | pass |
| Already-markerized bodies never re-elided/re-recapped (idempotent) | pass |
| Recap atomicity — overflow returns None, within-cap is whole & ≤200 chars | pass |

## Audit D — wiring proof

`scripts/audit_compaction_elision_wiring.py`, exercised real code paths:
- `ProviderViewMetrics.as_dict()` exposes `stale_reads_elided`,
  `recap_receipts`, `recap_chars_added`, `recap_fallbacks` (values verified).
- `CompactionEpochReceipt.as_dict()` serializes the 3 epoch keys; JSON-safe.
- `gt_engine.deep_metrics.DIAGNOSTIC_METRICS` contains all four
  `context_*` keys; `compare_arms` aggregates them from task rows (deltas
  2/3/41/1 verified).
- `eval/gt_central_agent.py:4985` writes `context_compiler =
  provider_view_metrics.as_dict()` into every model-call row, and the receipt
  aggregation (`:8332-8347`) sums the four keys into top-level
  `context_*` task metrics consumed by `compare_arms`.
- `tests/test_gt_deep_metrics.py` fixture carries all four keys.

## Audit E — live call-site proof

Drives the real `CentralFeatureRuntime.observe_action` → `progress_ledger()`
chain (not a hand-built fixture):
- `recent_reads` = current revision only (s2) — the provider-visible frame
  contract unchanged;
- `read_history` = both revisions (s1, s2) — enables elision identity;
- `_recent_read_observations(ledger)` resolves 2 rows;
- `build_provider_view` over that real ledger elides the stale read and the
  old body is removed; the real ledger also produces a read-identity recap
  receipt.

## Regression and verification status

- Focused suites pass: provider-view, provider-view-compaction, deep-metrics,
  central agent, central runtime, progress, replay, trajectory-audit,
  release-gate, delivery-audit, preflight, host-execution, run-diff,
  deepswe-release-gate. Only pre-existing Windows `gt-index.exe` (missing
  `objective_c` parser) failures remain, identical on the base commit.
- Ruff clean; compileall clean.
- Three audit scripts all print PASS.

## Remaining boundaries (unchanged by this audit)

- Local census/readiness/pre-smoke still fail on the stale Windows indexer;
  only the source-built Linux provider-free workflow is authoritative.
- No solve, efficiency, or non-regression claim; a paid matched smoke still
  requires separate authorization; the 89-task run remains blocked.
