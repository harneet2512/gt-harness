# Architectural improvements — 2026-09-07

Four defects, each fixed at its cause rather than its symptom. Every number
below is measured on the arktype graph (181,200 nodes / 466,421 edges) or on
graded run `34144284449`, and the command that produced it is named.

The four are not independent. One of them explains why GT underperformed a
bare agent on the metric that matters; the other three are what stopped a run
from completing or made it cost more than it should.

---

## 1. Incremental indexing destroyed 98% of the graph on every edit

**The most consequential defect in the system.** `gt-index -file` is the
incremental path: re-index one changed file into an existing `graph.db`
instead of rebuilding the repository. It did the opposite of that.

### What it did

`runIncremental` invalidated the attached resolution overlay — callsites,
candidates, derivation/completeness/unresolved facts — **repository-wide**, on
every single-file reindex:

```sql
DELETE FROM edges WHERE type IN ('HAS_CALLSITE','CANDIDATE','CANDIDATE_TARGET',
  'HAS_DERIVATION_FACT','HAS_COMPLETENESS_FACT','HAS_UNRESOLVED_FACT')
  OR (type='CALLS' AND callsite_stable_id IS NOT NULL);
DELETE FROM nodes WHERE node_type IN ('callsite','derivation_fact',
  'completeness_fact','unresolved_fact') OR label='Callsite';
```

The reasoning was recorded in the code and is sound as far as it goes: *"A
single-file refresh cannot prove repository-wide candidate parity because
callers in other files may depend on the changed identities."* True. The
remedy was the problem — it was maximally conservative, and **the overlay is
the graph**:

| | count | share of graph |
|---|---:|---:|
| overlay nodes | 177,390 | **97.9%** |
| overlay edges | 459,523 | **98.5%** |

So reindexing one 20-node file left 3,810 nodes and 6,898 edges. Reproduced
twice, and `PRAGMA integrity_check` returned `ok` both times — this was never
corruption, it was a logical mass delete working exactly as written.

### Why it mattered

The overlay is what answers "who calls this". Destroy it and every caller
query has nothing to read, so only a **full** rebuild restores usefulness.
That is the whole causal chain behind GT's benchmark behaviour:

```
incremental destroys the overlay
  → only a full rebuild restores it   (~115s)
  → rebuild is slower than the edit interval  (~50s)
  → the graph is never current at an edit boundary
  → caller_coverage unavailable 189 of 255 reads (74%)
  → caller_contract_view delivered 3 times in 280 steps
  → the agent has no substitute for grep
  → 2.39 search commands per call vs the control's 1.77
  → 280 provider calls instead of 166
  → 34.07M input tokens instead of 20.81M
```

Every symptom previously tracked as a separate blocker — step inflation, the
obligation ledger resetting, grep inflation, token spend above the control —
is downstream of the first line.

### The fix

An edit to file `F` can only change resolution for a callsite that is **in**
`F`, that **resolves into** `F`, or that lives in a file **importing** `F`. A
file that neither references nor imports `F` cannot bind to it, so its overlay
is still valid and deleting it discards proven work.

The purge is now scoped to that blast radius, staged in a temp table:

- `F` itself;
- every file with an edge whose target is a node in `F` (callers of `F`);
- every file with an `IMPORTS` edge into `F`.

Two narrowings mattered as much as the idea:

- **Callees are not affected.** `F` calling `G` does not invalidate `G`'s own
  callsites; only `G` calling `F` does. Including callees cost 22% of edges.
- **Only overlay rows anchor an edge deletion.** Matching *any* node in an
  affected file deleted overlay belonging to files the edit cannot reach.

The one case the blast radius cannot see — a callsite elsewhere that was
`unresolved_fact` and that `F` now satisfies — is handled *after* insertion,
where `F`'s new symbols are visible. Placing it beside the scoped purge would
have matched `F`'s **old** symbols, which is the wrong set.

Soundness rests on `F`'s nodes being deleted and reinserted with new
autoincrement ids: any overlay edge pointing at an old id would dangle. It
cannot, because callers of `F` are in the affected set, and
`DeleteFileEdgesAndNodesTx` already removes every edge whose target is a node
in `F`, with `SnapshotIncomingEdgesTx`/`ResolveIncomingEdgesTx` rebinding them
against the new nodes.

### Measured

Same file, same graph, before and after:

| | original | patched |
|---|---|---|
| nodes | 181,200 → 3,810 (**−97.9%**) | 181,200 → 180,516 (**−0.38%**) |
| edges | 466,421 → 6,898 (**−98.5%**) | 466,421 → 465,619 (**−0.17%**) |
| overlay nodes | 177,390 → 0 | 177,390 → 176,706 |
| duration | 39.3s | **12.0s** |

Functional check — the query the overlay exists to answer, before and after an
incremental reindex:

```
pre-incremental : callsites 19,639  candidate_targets 282,133  callers_into(ark/schema/node.ts) 270
post-incremental: callsites 19,567  candidate_targets 282,015  callers_into(ark/schema/node.ts) 270
```

Identical. Under the old behaviour that last column was 0.

**The regime change:** an incremental refresh now costs 12s against a ~50s
edit interval, so for the first time it fits inside the edit window. Before,
the only restorative operation was a 115s full rebuild, which never converged.

---

## 2. `nodes_fts` was cleared with a statement its table type forbids

`nodes_fts` is an **external-content** FTS5 table (`content='nodes',
content_rowid='id'`). `PopulateFTS5` cleared it with:

```sql
DELETE FROM nodes_fts
```

That is not how an external-content table is cleared. FTS5 must re-read each
content row to work out which terms to un-index, and by the time this ran the
incremental path had already replaced that file's rows in `nodes`. Index and
content disagreed, so SQLite raised **`database disk image is malformed`** on
every `-file` reindex, and the code fell into its own DROP+recreate recovery —
rebuilding the entire index in order to clear it.

Isolated in pure SQLite on a copy of the real graph, which also cleared it of
suspicion for defect 1:

```
DELETE FROM nodes_fts : ERR database disk image is malformed
nodes after DELETE    : 181200      ← unchanged; not the cause of the mass delete
DROP TABLE nodes_fts  : OK
nodes after DROP      : 181200
```

Fixed by using the documented command, which does not consult content rows:

```sql
INSERT INTO nodes_fts(nodes_fts) VALUES('delete-all')
```

The DROP+recreate recovery is retained as a genuine fallback. Removing the
spurious rebuild is most of the 39.3s → 12.0s improvement above.

---

## 3. A graph identity no run could ever satisfy

`gt_engine/indexer.py`. The revision directory was keyed on the index reuse
key alone, while `certify_graph_artifact` additionally required the artifact's
sealed `task_id` and `product_source_sha` to match the identity asking for it.
Nothing reconciled the two.

A graph built under one product SHA was therefore uncertifiable under the
next, and the immutable-artifact rule then refused to rebuild over it
(`immutable_graph_artifact_invalid`). That `ValueError` was swallowed twice —
once by `_ensure_index_unlocked`, once by `ensure_index` — so the run died
reporting only `benchmark run has no graph`, with a valid 912 MB graph on disk
and **no way to ever recover that directory**.

Two fixes:

- **`_revision_identity()`** derives the revision path from exactly what
  certification enforces: reuse key + task id + product source SHA. A
  different identity is a different artifact, so it gets a different path.
  Immutability holds and never blocks a rebuild. The failure state is removed
  rather than handled.
- **The refusal names its cause.** A diagnostics channel carries the
  certification reason and any swallowed exception into
  `BenchmarkGraphRequired`. This failure was undiagnosable by construction,
  which is precisely why it read as an environment problem.

Verified against the real poisoned artifact: the derived path moved to a fresh
revision, so the builder could publish and certify again.

---

## 4. A history marker that moved, and took the prompt cache with it

`scripts/miniswe_gt_run.py`. `_history_reference_marker` embedded the anchor's
`tool_call_id` in the **provider-visible** marker. The anchor is the newest
full copy of a duplicated tool result, so it moves whenever another duplicate
arrives — and every older marker was rewritten with it, mutating messages the
provider had already seen.

Measured by diffing consecutive request manifests from a codespace run:
nothing shifted position, so this was an in-place rewrite. At request 22 a
single 533-byte message at index 35 changed and invalidated the fifteen
byte-identical messages behind it — **26,645 bytes re-sent uncached to change
533**.

Against the frozen GT-off control on the same task:

| | control | GT-on |
|---|---:|---:|
| input | 20,811,415 | 34,074,940 |
| cached | 20,678,272 (99.4%) | 28,957,440 (85.0%) |
| **uncached input** | **133,143** | **5,117,500** |

85% vs 99.4% reads like a 14-point gap; on the tokens billed at full rate it
is **38×**.

The marker is now `{sha256, utf8_bytes}`. The digest identifies the payload
and does not move, so the marker is stable for the life of the history. The
anchor is still recorded under `extra`, which is audit state and never sent,
so newest-anchor rebinding is unchanged and its test passes untouched.

A related finding worth recording: **per-call context was never the problem.**
GT-on averaged 121,264 input tokens per call against the control's 125,370 —
GT is slightly *leaner* per call. The excess input was entirely the extra
calls, which defect 1 explains.

---

## Blocker ledger — what closed, what did not

Eight blockers were tracked into this session. Six closed, one is accepted,
and two remain open with their cause identified rather than guessed.

| # | blocker | state | commit |
|---|---|---|---|
| 1 | incremental indexing destroyed 98% of the graph | **closed** | `043e14c6` |
| 2 | `nodes_fts` cleared with a statement its table type forbids | **closed** | `043e14c6` |
| 3 | graph identity no run could satisfy (`BenchmarkGraphRequired`) | **closed** | `1920a127` |
| 4 | history marker moved and destroyed the prompt cache | **closed** | `1920a127` |
| 5 | producer committed non-executable; refusals named nothing | **closed** | `da43ebcd` |
| 6 | `provider_manifest_count_mismatch` on every retried call | **closed** | `3f53b55c` |
| 7 | grep inflation / obligation ledger resets | **open** — downstream of 8 | |
| 8 | graph cannot keep pace at an edit boundary | **open** — cause identified | |

### Why 7 and 8 are one blocker, and what closing it requires

The live path -- `MiniSweAdapter` -> `GTSession` -> `GraphBuildCoordinator` ->
`_build_frozen_graph` -> `ensure_index_with_receipt` -- performs a **FULL**
index on every invalidation. `-file` appears nowhere in `gt_engine/` or
`scripts/`; it exists only inside the installed `groundtruth` wheel and one
offline script. So fixing the producer's incremental mode made incremental
*safe* without making anything *use* it, and blocker 8 is untouched by it.

Two things must both be true before the live path can adopt it:

- **Speed.** `PromotePropertyEdges` runs whole-graph on every single-file
  reindex; the producer's own comment names a file-scoped variant as the
  pending optimization. Clean measurement is 12.0s (a 77s reading was taken
  while a 300-step run saturated the same box and should not be quoted).
- **Authority.** `queryAttachedCandidates` refuses when
  `graph_resolution_complete != "1"`, and `runIncremental` sets it to 0 --
  deliberately, pinned by `inheritance_incremental_test.go`: *"the new
  graph-native authority is explicitly incomplete and hidden from the generic
  edge query until a full rebuild restores a single revision."* Setting that
  flag without re-deriving the overlay would assert repository-wide candidate
  parity that was never computed. Earning it means running the equivalent of
  `publishAnalysisPhase` scoped to the blast radius.

The scoped purge in blocker 1 is what makes that argument available at all:
before it, nothing outside the edited file survived, so fail-closed was the
only honest marker. It is now a bounded piece of resolver work rather than an
impossibility.

### Not product defects

- The codespace launcher never passed `--product-source-sha`, so
  `issue_runtime_receipts` raised `product_source_sha_invalid` and every run
  reported ERROR at the finish line regardless of outcome. CI supplies it via
  pier's `--ak product_source_sha=`; the harness script did not. A launcher
  bug, fixed in the launcher.
- Sustained `BadGatewayError` retries against `{"only":["relace"],
  "allow_fallbacks":false}`. The pinned route was probed directly and returned
  HTTP 200 with `provider: Relace`, so this is intermittent gateway failure on
  ~120k-token requests, not our code and not an outage.

## Known defect, measured but not fixed: pointers nobody follows

`SEALED_DELIVERY_BYTE_LIMIT` is 1_400. A sealed delivery above it is not
truncated -- it is replaced by a `GT_CONTEXT_UNIT_REFERENCE` carrying a
`gt-evidence read ...` retrieval command for the model to run itself.

On run 34144284449 the model issued that command **zero times in 280
commands**, so every one of the 56 references delivered nothing:

```
delivery blobs                     175
  shipped as a pointer              56   (32%)
  dereferenced by the model          0
payload sizes            1,090-1,854 bytes, median 1,413
content withheld              75,420 bytes
pointers shipped              36,502 bytes
net saving                    38,918 bytes  = 0.1% of a 34,074,940-token run
```

The ceiling cuts through the middle of the payload distribution, so the
deliveries it converts to pointers are the ones barely over it. Measured on
the delivery-attribution instrument, `context_delta` -- 52% of everything GT
said on that run -- was acted on 2% of the time.

**Why the obvious fix was reverted.** Raising the ceiling to 1_900 (clearing
the observed maximum while staying under the `context_contract` ceiling of
2_000, which a sealed delivery must not borrow) is a one-line change and the
suite says it is not a local one: it moves which deliveries take the reference
path, and that ripples into receipt census, budget conservation and the
compaction assertions. Three tests were realigned and a fourth kept surfacing.
A budget constant that reprices four contracts is load-bearing, and changing
it belongs with the delivery-contract work rather than beside a producer fix.

Recorded here rather than half-applied. The measurement is the durable part.

## What this does not fix

- **`provider_manifest_count_mismatch`** — the harness receipt check that made
  run `34144284449` report `failure` despite completing, submitting and being
  graded (f2p 24/25, p2p 1679/1679). A valid measurement that cannot yet be
  attested.
- **`obligation_reverified` has never produced a row.** A check that has never
  emitted its non-default output has not been shown to work.
- **n=1.** One task establishes nothing about outcomes. The public leaderboard
  is 113 tasks at `n_runs=4`.

## Method notes earned here

- **Reproduce before repairing.** The FTS error was loud, real, and not the
  cause. Isolating it in pure SQLite took one command and stopped a fix that
  would have changed nothing.
- **`integrity_check: ok` next to catastrophic loss means policy, not
  corruption.** That single observation redirected the whole investigation
  from a storage bug to a `DELETE` statement working as written.
- **A refusal that cannot name its cause will be diagnosed as the
  environment.** Defect 3 cost a full run for that reason alone.
- **Confirm the mass before blaming the mechanism.** The overlay was 97.9% of
  nodes and the survivors were 3,810 — matching the reproduction to the row,
  which is what turned a hypothesis into a cause.

---

## 2026-09-10 — derived surfaces, transport fixes, producer parity

Shipped on `codex/context-plan-integrity` through `4fc6d384`, with the
producer side on `codex/context-plan-producer` through `df9b983b`. The
gate-by-gate record lives in
`docs/plans/context-plan-implementation-status.md`; this is the architectural
summary.

**Producer determinism — amend ≡ rebuild** (producer repo). `c4d4a54f`:
node ids are AUTOINCREMENT artifacts and a batch amend re-inserts the edited
file's nodes at the top of the id space, so every cross-file pick that rode
raw id order named a different logical declaration under an amend than under
a rebuild (measured on matplotlib: 49,053 vs 49,040 edges, ±726/713 gross
CALLS relabels). Every first-found pick now sorts name-index candidates by
`(file_path, start_line, id)` before choosing — resolver fallbacks (1.94,
1.94a, 1.95, 1.96, 1.97), `classByName`/serde promotion, relationship
resolution, MRO ties, taxonomy truncation and assertion/inheritance
tie-breaks. `87d755a6`: a batch amend over a pinned-identical history window
no longer re-walks `git log` and delete-rewrites identical `cochanges`/
`communities` rows — the parent's recorded coupling receipt is read from the
staged copy, and when the reuse key (repository revision, shallow flag,
window boundary commits) matches, the tables are preserved through the
structural rebuild. The community partition additionally requires its
recorded window bounds plus the certified call-pair digest
(`derived_community_call_pairs_sha256`) to still answer the amended graph;
`derived_coupling_reused` records which layers were carried.

**Derived tables consumed** (`f28108c9`). `gt_engine/derived_context.py` is
the gated read side for what the producer already published: community
membership joined through `community_members.member == nodes.file_path`,
process participation through the producer's effective stable id
(`COALESCE(nodes.stable_id, resolution_symbols.stable_id)` on
`native_id = nodes.id`). Each layer gates on `derived_*_state == 'ok'` plus
recorded-vs-live count markers — the admission contract
`_closure_is_fresh` applies to `closure_count` — and surfaces additively
through `HybridRanking.attribution_record` (the
`gt.semantic_localization.v1` artifact) and `build_graph_projection` facts.
Degraded states serve typed-empty output with the state named verbatim,
never a fabricated partition.

**Transport fixes.** F1 `7901de98` — the wire carries the exact admitted
tool set the request envelope recorded; ordinary turns had shipped only
`[BASH_TOOL]` while the manifest claimed the model's whole advertised set,
so the typed-action surface was dead code and receipts over-claimed it.
F2 `0251bd43` — delivery ledger and shipped latches commit only after
transport returns (`bind_provider_payload(..., commit=False)`); a raised
attempt leaves pending deliveries, exposures and queued candidates intact
for the retry. F4 `fb79ab91` — sealed `<gt-facts>` envelopes are extracted
verbatim before an oversized observation is bounded (original bytes
CAS-archived); evidence journaled delivered at turn N no longer vanishes at
N+1 with no revocation record. F9 `55f5b774` — elided context-unit markers
carry `retrieval_hint` naming `gt-evidence read <sha> 0 8192`, so recovery
no longer depends on the model spontaneously invoking undocumented
plumbing. F10 `daded316` — the post-edit syntax probe sends each checkable
file's on-disk bytes through the certified `gt-index -inspect-jsonl` parser
boundary (.py/.pyi/.go/.ts/.tsx/.js/.jsx/.rs) instead of py_compile on
Python only; positive evidence requires the producer's
`syntax_tree_incomplete`, fault rows stay abstentions, extensions outside
the certified set are never probed.

**Six derived MCP endpoints** (producer `fceae84c` + format `df9b983b`;
bundle rebind `4fc6d384` — vendored wheel, `gt-index` binary and
`SOURCE-COMMIT`, CI-backed packet, lineage PASS): `gt_trace` (bounded BFS
path A→B over CALLS + `nodes.parent_id` containment, depth ≤ 6, expansions
≤ 500), `gt_detect_changes` (diff → changed symbols → test-witnessed
`process_steps`/`processes`; `risk_level` is `unknown` on failure, never
guessed), `gt_route_map` + `gt_api_impact` (`HANDLES_ROUTE`/`API_CALL`
service boundary; multi-fetch consumers carry `attributionNote`),
`gt_closure` (precomputed transitive reach, producer bounds stated,
staleness flagged via the `_closure_is_fresh` probe), `gt_community`
(`communities`/`community_members` verbatim; NULL cohesion stays null —
unmeasurable is not zero). Typed abstention throughout: absent tables,
unreadable decorator lines and ambiguous names return typed results, never
fabricated rows. The endpoint contract is documented in the producer
repo's `docs/kernel/DERIVED_ENDPOINTS.md`.
