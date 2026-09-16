# Plan: close the two failure classes gate-one exposed — proven offline before any re-dispatch

Owner rule, same as `PLAN-DEEPSWE10-READINESS.md`: **paid runs confirm proofs; they never discover
bugs.** Gate-one (`35056493769`) solved and attested, but the strict fleet gate FAILED on two classes
the offline gates never exercised. This plan is the remediation contract for those two classes. Until
both are closed with behavior-visible tests that fail without the fix, **no DeepSWE dispatch happens —
not gate-one, not the remaining-19, not the 10-task certified cohort.**

Read with: `docs/PLAN-DEEPSWE10-READINESS.md` (the certificate framework this feeds),
`docs/BENCHMARK-CONFORMANCE-2026-09-16.md`, `BENCHMARK_READINESS_STATUS.md`.
Evidence base: `artifacts/runs/deepswe_gate1_35056493769_events.jsonl` (4,661 events, recovered from
the task artifact) and `artifacts/runs/deepswe_gate1_35056493769/lsp-j4j_uiac-graph.db` (the actual
corrupt graph — permanent fixture input).

## 0. What the journal proves

Anomaly inventory, complete — everything not listed here is typed designed behavior
(`localization_task_ceiling`, `no_cochange_partners`, churn backoff, rate-limit pacing, salvage
races):

| class | count | verdict |
|---|---|---|
| `semantic_localization_unavailable` `TypeError: float() ... 'NoneType'` | 9 | **Defect G1 — root-caused** |
| `graph_sync_amend_refused` `GT_INDEX_MEMORY_GUARD_TRIGGERED` exit -9 (+ `index_memory_backoff`, `graph_amend_deferred`) | 2 | **Defect G2 — mechanism identified** |
| `dense_index_ready query_ready=false` `graph_snapshot_not_current` | 2 | typed, designed |
| `delivery_recipe_unresolved` `graph_unavailable` cluster (seq 3057–3431) | 6 | typed, downstream of G2 |
| `lsp_salvage` `input_missing`/`superseded`, `lsp_salvage_refused` `live_not_current` | 6 | typed, designed |
| `episode_failure_recorded` | 7 | model-action failures, designed |
| `provider_attempt_failed` `RateLimitError` + `provider_retry_paced` | 2 | provider pacing worked |

All three `semantic_localization_unavailable` revisions (`5f1d4ea9`, `5b46a60e`, `2570c644`) are the
last three consecutive publications — deterministic per-graph, consistent with index corruption that
survived amend cycles.

## 1. Defect G1 — `bm25()` NULL → `float(None)` crash in `lexical_rank`

**Forensic chain (all proven on the real artifact, not inferred):**

1. `gt_engine/retrieval.py:636` executes `-float(row[9])` where `row[9]` is `bm25(nodes_fts)`. It is
   the only unguarded `float()` on a SQL-nullable value reachable inside
   `_semantic_task_localization`'s try — every other `float()` site in the path is guarded or typed.
2. The real graph's `nodes_fts_data` id=1 averages record claims `nRow = 95,644` while
   `nodes_fts_docsize` holds **190,953 rows** — one full dead generation (contiguous rowids
   1,550,266–2,024,755) stacked underneath the live generation (2.0M–2.2M). Every live node id is
   indexed; the extras are all dead rowids.
3. `bm25`'s idf term `log((nRow − nHit + 0.5)/(nHit + 0.5))` goes `log` of non-positive when a term's
   doclist exceeds `nRow`. `aiomonitor` (the repo name — in nearly every `qualified_name`) has
   178,485 doclist hits > 95,644 → NaN → SQLite maps NaN to SQL `NULL` → `float(None)`.
   Low-coverage terms (`snapshot`) score normally, which is why the crash is query-dependent.
4. Transplanting the four shadow tables into a fresh DB reproduces the NULLs exactly; FTS5's native
   `INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')` on the transplant repairs it — zero NULLs on
   every probed term. `DELETE FROM nodes_fts` on the corrupt index raises `database disk image is
   malformed` instead — which `PopulateFTS5` WARN-swallows, so a broken index publishes anyway.
5. Minimal deterministic reproducer (no big artifact needed): build a small index, then
   `UPDATE nodes_fts_data SET block = x'054b' WHERE id = 1` (nRow=5 while 25 docs are indexed) →
   `bm25()` returns NULL for every hit on a fresh connection. This is the test fixture.

**Fix surface — three layers, all required:**

- **Producer maintenance** (`vendor/gt-index-src/internal/store/sqlite.go` `PopulateFTS5`):
  replace the hand-rolled `DELETE`+`INSERT` with FTS5-native `'rebuild'`, mirroring
  `properties_fts.go`'s `populatePropertiesFTS5`/`recreatePropertiesFTS5` (rebuild, then
  DROP+recreate on malformed shadow tables). `rebuild` reconstructs the index from content in one
  statement — it cannot strand dead-rowid entries the way DELETE+INSERT can.
- **Publication integrity**: after maintenance, verify before publish —
  `COUNT(nodes_fts_docsize) == COUNT(nodes)` (the exact invariant the corrupt graph violated:
  190,953 vs 95,644) plus a `MATCH`+`bm25` probe returning finite scores on a real term. Call sites
  `main.go:320,569,1851` currently WARN on `PopulateFTS5` failure — under `GT_REQUIRE_FTS5=1` a
  failed/unverifiable index must `abortStagedBuild`, not publish a degraded graph. The
  `GT_REQUIRE_FTS5` gate at `main.go:576` checks `COUNT(*) FROM nodes_fts` which reads `nodes` —
  the failure mode that *looks* healthy; replace with the docsize-parity + bm25 probe.
- **Consumer resilience** (`gt_engine/retrieval.py` `lexical_rank`): a NULL or non-finite bm25 must
  degrade the lexical source with a typed reason — `SourceRanking(available=False,
  reason="fts_bm25_score_invalid", detail={matched_rows, null_scores, terms})` — never an exception,
  and never silently-as-empty (the contract: unavailable ≠ ran-and-found-nothing). Property and
  dense sources continue; RRF degrades gracefully; the journal records which sources contributed.

**Tests (RED first):**

- `tests/test_lexical_rank.py::test_bm25_null_degrades_source_typed` — real sqlite db, stats record
  forced stale via the `x'054b'` fixture; assert `available=False`, reason names the defect, no
  exception escapes, `SourceRanking` detail records the NULL count.
- `test_bm25_nan_or_malformed_*` — `fts_query_failed` path on genuinely malformed shadow tables
  (`DELETE FROM nodes_fts_docsize` → `malformed` on MATCH) returns `available=False`, not a crash.
- `test_hybrid_rank_continues_when_lexical_degraded` — property/dense still rank; fused result
  attributes only live sources.
- `vendor/gt-index-src/internal/store/sqlite_fts_test.go` (new or extend):
  `TestPopulateFTS5RepairsStaleStats` — build index, corrupt the averages record, run
  `PopulateFTS5`, assert docsize-parity and finite bm25 on all probed terms;
  `TestPopulateFTS5RecreatesMalformedShadow` — drop a shadow table's rows to force `malformed`,
  assert DROP+recreate recovers; `TestPopulateFTS5CleansDeadGeneration` — delete content rows then
  populate, assert no docsize rows name dead rowids.
- `scripts/verify_fts_health.py` (or in-module helper used by both Go preflight and Python):
  docsize-parity + sample MATCH/bm25 finite probe, exercised against the real corrupt artifact
  (assert FAIL) and the repaired output (assert PASS).

## 2. Defect G2 — `GT_INDEX_MEMORY_GUARD_TRIGGERED` on single-file batch amends

**Mechanism (code-proven):** the certified producer declares only `batch_parser_node_reuse_v1`; the
legacy `-file` incremental is deliberately not enabled (invalidates the resolution sidecar). So every
amend — even the two single-file dirty sets that died — runs `-amend-parent`: copy the parent, then
the **entire full-build pipeline** (walk all files, parse all into one `results` array, full
`ReplaceParsedStructure` reconcile, resolver, all repository-wide derived passes, both FTS rebuilds).
Amend peak memory ≈ full-build peak, not delta size.

The guard limit is `min(4 GiB, cgroup_max//2, headroom−128 MiB)`
(`indexer.py:_effective_index_memory_limit`), computed at launch from live cgroup pressure. It fit at
run start (initial index succeeded); 35+ minutes in, the grown agent process had shrunk headroom, the
limit dropped below the pipeline's need, and the poll loop SIGKILLed mid-pass — twice. Defer+retry
(120s backoff) later succeeded; the strict gate still counts the refusals, correctly.

**What must change (in order of honesty, not convenience):**

1. **Refuse early instead of dying late.** If the computed limit is below the measured floor for a
   batch amend on this graph size, the bounded runner must return a typed `memory_headroom`
   outcome *before* launch — same refusal to the gate, but no minutes of doomed child work, no
   half-written staged candidate, and an honest signal that scheduling, not the amend, is the
   problem. Requires the measured floor (item 2).
2. **Measure the real envelope and pin it.** Windows build of the vendored producer
   (`go build -tags "netgo osusergo sqlite_fts5"`) is now proven working — run
   `-amend-parent` on synthetic repos at several graph sizes while sampling `PeakWorkingSet`, and
   record the amend's RSS curve vs. graph size in `docs/`. That number becomes the refusal floor in
   (1) and exposes whether the reconcile/derived passes scale linearly or worse.
3. **Shrink the envelope where the measurement says it's cheap** — the standing suspects:
   `results := make([]*parser.ParseResult, len(files))` materializes every file's parse output at
   once (stream it), the single-transaction `ReplaceParsedStructure` reconcile (the giant
   `DELETE FROM nodes WHERE id NOT IN …` anti-join), and `GOMEMLIMIT` at `min(3 GiB, limit·¾)`
   leaving non-heap CGO/sqlite pressure unbounded (`GOGC`/cache tuning). Only what measurement
   proves; no blind surgery.
4. **Gate posture stays strict.** A deferred-then-recovered amend is still a run that paid wall-clock
   and risk for a hiccup. Do not reclassify `graph_sync_amend_refused` downward.

**Tests:** `_effective_index_memory_limit` unit tests for the early-refusal floor (snapshot-driven —
no Linux needed); producer-side test asserting `PopulateFTS5` memory stays bounded if (3) lands a
change; installed-rehearsal coverage remains the Linux-cgroup proof surface — offline tests cover the
policy, the rehearsal covers the physics.

## 3. How this merges into the certificate framework

- `PLAN-DEEPSWE10-READINESS.md` §4 gains two entries:
  `deepswe_fts_bm25_null` (G1) and `deepswe_amend_memory_guard` (G2) — each certificate-blocking
  until its tests flip green.
- Certificate check **C5** already asserts no `GT_INDEX_MEMORY_GUARD`; extend the journal scan to
  also fail on `semantic_localization_unavailable` / any `*_unavailable` event carrying an exception
  class, so a typed-but-crashing capability blocks issuance (today it reads as "graceful").
- Phase B replay corpus must include run `35056493769` (gate-one) — the journal fixture already
  exists at `artifacts/runs/deepswe_gate1_35056493769_events.jsonl`.
- `scripts/issue_task_certificate.py` asserts C5-extended over the rehearsal artifact.

## 4. Sequence and exit criteria

| step | done when | status |
|---|---|---|
| R1 consumer guard + typed reason, RED→green | `test_lexical_rank` bm25-NULL fixture passes; hybrid continues degraded | **DONE** — `retrieval.py` guards invalid scores → typed `fts_bm25_score_invalid`; proven on the real artifact (40 invalid scores, hybrid continued) |
| R2 producer rebuild maintenance + verify + fail-closed under `GT_REQUIRE_FTS5` | Go tests green incl. corrupt-fixture repair; preflight rejects the real corrupt artifact | **DONE and shipped** — native rebuild + DROP/recreate + `VerifyFTS5Integrity` (docsize parity + finite-bm25 probe); final rebuild at true pipeline end; real artifact repaired (190,953→95,644 docsize, 0 NULLs). Python preflight `_graph_schema_receipt` runs `_graph_fts5_health_reason` — refuses `fts5_invalid:nodes_fts_desynced` on the real corrupt artifact, passes the repaired one. Upstream `d2e4a1c3` on `gate1-fts5-amend-fix`; producer CI 35120201968 all green (go-build runs full `go test -tags sqlite_fts5 ./...` incl. `nodes_fts_test.go`); certified binary rebuilt in run 35117328791 (sha 9e2758c0) and rebound in commit `515fe702` |
| R3 memory envelope measured + early-refusal floor wired | measured table in `docs/`; policy tests green | **DONE** — measured: batch amend peak 1,262 MiB vs full build 1,333 MiB on a 93.6k-node graph (amend ≈ full build regardless of delta). Floor = 170 MiB + 16 KiB/node from certified manifest `indexed_node_count` (`_graph_scale` fallback). Pre-launch refusal `GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT:batch_amend_floor:limit=…need=…` — bare typed code, not `amend_failed:*`; opens the cgroup defer window only (no spawn window, no producer-death count). GOMEMLIMIT=600MiB still peaked 1,043 MiB — env tuning alone insufficient |
| R4 journal-anomaly scan extended | certificate C5 extension tested on gate-one journal (must FAIL it) | **DONE** — `lsp_watch.py` counts `*_unavailable` events whose reason is an exception class as `CAPABILITY_CRASH(n)`, added to `fail_flags`. Gate-one journal now reads `AMEND_FAILURES(2), CAPABILITY_CRASH(9)` |
| R5 provider-free acceptance + serial/xdist/lint + Go suite | all green on the remediation SHA | **DONE** — producer CI 35120201968 on `d2e4a1c3`: lint + go-build (full `sqlite_fts5` suite) + benchmark + 6-job pytest matrix all green. Python side on `109c379e`: all previously-failing files green (143 tests incl. product-acceptance, vendored-binding, scoped-merge, hammer, audit-lifecycle). Provider-free acceptance ran to completion: `provider_calls=0`, `benchmark_runs=0`, content correctness 12/12 PASS, `release_eligible=true`; sole blocker `container_install_not_executed` is environmental (no Docker daemon on this host — that arm is CI-only) |
| R6 docs + HAR-83 updated; gate-one failure preserved as historical evidence | status doc records run, defects, proofs | **DONE** — this doc records landed state; review packet `har83-context-plan-producer-d2e4a1c3-ci` pushed to `gt-review-inbox-d2e4a1c3` (`256d7127`); provenance verifier PASS locally |
| R7 re-dispatch gate-one | strict fleet gate PASS on the gate task journal — *then* the remaining-19 release decision belongs to the owner, and the DEEPSWE-10 certificate path supersedes it if that framework lands first | **READY-BLOCKED on route scope** — all GT-side fixes are shipped and bound. Active route is `stealth/union-alpha` (functional verification only, free preview, delistable — NOT valid benchmark evidence). Before any matched-cohort claim the route must flip back to `deepseek-v4-flash-0731`/`relace`; before ANY dispatch, fresh paid approval is required |

**Never:** reclassify a refusal to pass the gate; publish a graph whose FTS integrity check failed;
treat a typed-unavailable capability row as "working"; run GT-off; dispatch anything before R1–R6.

## 5. Shipping dependency — certified binary rebind — **RESOLVED**

The paid workflow runs `vendor/gt-index-linux-amd64`, not the vendored source it compiles and
discards. The rebind completed:

1. `gate1-fts5-amend-fix` (commit `d2e4a1c3`, branched off `4e346402`) pushed to
   `harneet2512/groundtruth`; producer CI run 35120201968 all green.
2. `producer_build.yml` run 35117328791 emitted binary `9e2758c0` + build-info + builder
   identity (image `golang:1.22.5-bookworm@3600cb0c`, fingerprint `f381466a`).
3. Vendored in commit `515fe702`: binary + build-info + `SOURCE-COMMIT`; bundle rebound —
   `producer_build.source_commit/tree`, `producer_sha256`, lineage ancestry path (140),
   `post_certification_changed_paths` (751), review packet `har83-context-plan-producer-
   d2e4a1c3-ci` on inbox commit `256d7127`, attestation digest recomputed. Both
   `verify_groundtruth_lineage` and `verify_producer_binding` PASS locally;
   `test_vendored_producer_binding` green.
