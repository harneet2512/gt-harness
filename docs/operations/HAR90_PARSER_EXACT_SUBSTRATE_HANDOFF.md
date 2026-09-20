# HAR-90 parser-exact substrate handoff

Status as of 2026-09-20. Read this when continuing HAR-90 residuals work,
typed-query capability work, or producer schema changes. For the product-level
contract and pipeline state read `GT_HARNESS_SESSION_HANDOFF.md` and
`BENCHMARK_READINESS_STATUS.md` first — this file covers only the
parser-exact-evidence increment and its honest remainders.

## Shipped identities

| Artifact | Identity |
|---|---|
| Groundtruth source commit | `5681eeae99fb272e7e12b68e624e6f293878be97` on `wheel-patch-lsp-inventory` |
| Producer binary (CI artifact) | SHA-256 `8dceeec11cd5cfbc7d45c5bf089e6c31ad6b0b6b360d4ab6f21c3d986e0ade25`, workflow run `35520971900` |
| build-info | `cb383b8e94a351879f799c68fd7790c01e32a936d51fa6a82120bd7122b7db05` |
| Wheel (vendored + installed) | `groundtruth_mcp-1.0.0` SHA-256 `0a4eaf2e73581955ac043fb5bab7d1eaee7739b63459972d59194ac21677cd99` |
| Graph schema stamp | `v15.4-callsite-actuals`; `capability_cfg_uses/callsite_actuals/access_receiver=v1` in `project_meta` |
| gt-harness (gt-control-main) | `8e075ece` on `main` — manifest rebound, seal `73730c5a6c3e65d4` |
| Review inbox | `9604bec7` on `gt-review-inbox`, packet `har90-certified-producer-5681eeae` |
| Builder image pin | `golang:1.22.5-bookworm@sha256:af9b40f2b1851be993763b85288f8434af87b5678af04355b1e33ff530b5765f` |

## What the substrate now persists

Three producer-side evidence families consumers previously re-derived
approximately:

1. **`cfg_uses`** (table, v15.3): parser-exact identifier reads per CFG
   block — member chains (`c.JSON` emits `c`, `c.JSON`), augmented-assign
   LHS, case/loop-header reads, try-resource initializers, parameter
   defaults. Type positions skipped; nested-function bodies pruned.
   Retired with the file in `DeleteFileEdgesAndNodesTx`; `ReplaceCFG` /
   `InsertCFGTx` take the `uses` slice on both full and incremental paths.

2. **`edges.actual_args`** (column, v15.4): JSON array of raw top-level
   argument texts per callsite — kwargs, spreads, nested calls verbatim
   (`["ItemsController::javaWork"]`, `["x=2","*seq","g(a, b)","**opts"]`).
   `CallsiteOrdinal` joins `ResolvedCall` → `allCalls`; zero-arg calls and
   out-of-range ordinals persist NULL (consumers fall back, see below).

3. **`access_sites` payload v2** (`edges.access_sites`): per-site `receiver`
   chain plus top-level primary receiver — `self`/`this`/named receivers.
   v1 payloads lack `receiver` and use the correlation fallback.

## What the consumers do with it

- `cfg_store.load_stored_cfg`: `cfg_uses` replaces the lexical scan when
  the table exists → limitation narrows `approximate_use_detection` →
  `approximate_use_coverage`. `data_flow`/`field_read`/`side_effect`
  properties merge as supplemental parser-exact uses (coverage is partial,
  flag stays).
- `cfg_store.interprocedural_slice_stored`: `param` properties give
  ordered typed formals (`[required]`/`opt=`/`…rest`/`*args`/`**kw`);
  `_bind_actuals` binds kwargs by name, positionals to remaining formals,
  variadics absorb rest, `*seq`/`**opts` spreads bind only to matching
  variadics else abstain (`spread_actual:*`, `arity_mismatch`,
  `unknown_kw:*`). Persisted `actual_args` skips text re-splitting
  entirely (`approximate_actual_extraction` only when fallback ran).
  Callable-value callsites (`cb()` resolved to `goHelper`) prefer the
  caller's formal names when picking the textual call on shared lines.
- `deterministic_queries._taint`: owner = persisted receiver →
  `self/this/cls`→enclosing class (chains walk `class_field` declared
  types), named receivers via `param` annotations then class name-match.
  `_class_field_name_type` parses Java `Type name`, Go `name Type`,
  colon-typed, modifiers, annotations. **Unresolved receiver →
  `owner_unknown`, never the enclosing class** — same-name fields on
  different classes cannot join.

## Named residuals (honest, not closed)

- `interprocedural_name_matched` — binding is signature-proven, not
  type-proven. Type-proven needs compiler frontends (SCIP-tier); out of
  scope by design.
- `approximate_use_coverage` — exotic read positions (await expressions,
  decorators, nested-function bodies) aren't emitted as `cfg_uses` yet.
- `approximate_actual_extraction` — still fires when a hop's edge has
  NULL `actual_args` (zero-arg callable hops like `cb()`); the fallback
  produces the correct empty binding but the flag is conservative.
- `owner_unknown` — genuinely dynamic receivers (untyped params,
  `getattr` results) can't bind; channels join weaker, flagged.

## Verification state

- Product suite: **4717 passed, 0 failed** (149 skipped, 4 xfailed) —
  `PYTHONPATH=src python -m pytest tests/ -x -q` (~14.5 min).
- Go suite: `go test -tags sqlite_fts5 ./...` green — **the tag is
  mandatory**; without it FTS5-dependent tests fail with
  `no such module: fts5`.
- `product_acceptance`: **14/14** — `VERIFIED_ROUTE_B_SOURCE_BOUND`
  re-earned (seal, lineage, provenance, vendored-vs-installed identity).
- Real-artifact battery: stamped CI binary produced a 255-node graph from
  `tests/fixtures/polyglot_repo`; installed wheel answered SLICE (2 hops
  through callable_value edge, `cb`↔`goHelper` bound from persisted args)
  and TAINT (6 channels all `owner_bound`).

## Key files

Producer (`D:\gt-product-source\gt-index`):
- `internal/parser/cfg.go` — `CFGUse`, `usesFor`/`collectUsesInto`,
  case-header/try-resource/param-default use collection
- `internal/parser/parser.go` — `CallRef.ArgumentTexts` (raw per-arg text)
- `internal/resolver/promote.go` — `fieldReadRe`/`fieldWriteRe` capture
  receiver, `edgeAccessMeta` v2
- `internal/store/sqlite.go` — `cfg_uses` DDL, `actual_args` column +
  migration entry, all three edge-insert paths
- `internal/store/cfg.go`, `internal/store/incremental.go` — `ReplaceCFG`/
  `InsertCFGTx`/`DeleteFileEdgesAndNodesTx` threading
- `cmd/gt-index/main.go` — `schemaVersion`, `actualArgsJSON`, capability
  stamps, cfg_uses collection on both paths

Consumer (`D:\gt-product-source\src\groundtruth`):
- `runtime/cfg_store.py` — `load_stored_cfg` (cfg_uses + supplemental
  uses), `_param_properties`, `_bind_actuals`, `_split_actuals`
  (prefer_names), `_callees` (actual_args join), composer integration
- `runtime/deterministic_queries.py` — `_taint` receiver resolution,
  `_class_field_name_type`, `_owner_key`, `_type_name_to_class`,
  sites-array iteration
- `index/schema_version.py` — ordered `_version_tuple` compare

Tests: `tests/test_typed_topology_queries.py` (fixtures carry
`properties`, `cfg_uses`, `actual_args`, v2 access sites; `_build_graph`
stays v15.2-era to keep old-graph fallback coverage, `_build_graph_v15x`
applies new objects).

## Gotchas that will bite you

- **pytest resolves `groundtruth` from site-packages, not `src/`** — run
  tests with `PYTHONPATH=src` or rebuild+install the wheel. The
  acceptance gate requires installed==vendored wheel bytes.
- **WSL distro is `Ubuntu`** (`wsl -d Ubuntu`), default is docker-desktop.
  `/tmp` in WSL is tmpfs — it clears between boots; copy graphs to
  `/mnt/d/tmp/` to keep them.
- **`gt-index-src` snapshot drifted once already** — its contract is
  "source the certified binary was built from"; re-sync on every
  re-vendor (was stale at `193b9d93b` while binary was `56106068`).
- **Builder-image digest was stale in two prior records** — the pin lives
  in `scripts/swebench/build_gt_index_linux.sh` (`DEFAULT_GO_IMAGE`);
  read it at the source commit, don't copy the previous packet's value.
- **`inbox/INDEX.json` serialization is `indent=1`, default separators** —
  hand-rolled reserializations with `sort_keys` or `separators=(",",":")`
  produce 880-line diffs; preserve the format.
- **The manifest seal** is `_digest(lineage_exception minus
  attestation_digest_sha256)` via `gt_harness/product.py` — recompute
  after any rebind; `test_filed_lineage_exception_seal_matches_its_own_content`
  verifies it.
- **Incremental `runIncremental` re-stamps `schema_version`** on old
  graphs (means "producer version that last wrote") but does NOT stamp
  capability flags — flags only appear on full rebuilds. Consumers must
  gate on schema-object presence + per-row NULL, not flags alone.
- **Never commit docs to gt-product-source** — its CLAUDE.md forbids
  reports/analysis `.md` in the repo; docstrings carry documentation.
  This file lives in gt-control-main where docs/operations is the
  sanctioned handoff surface.

## Open questions carried forward

- `param` coverage parity with cfg entry defs was ~10-vs-9 on the small
  fixture — the block-0 fallback stays until a larger corpus confirms
  parity.
- `edges.receiver_type/origin/shape/chain` columns are still NULL on
  observed graphs — check whether other resolution methods populate them
  before relying on them.
- `callsite_stable_id` is not populated on legacy CALLS edges; not needed
  while `actual_args` rides the edge row, but required if a future
  consumer joins `resolution_callsites`.
