# arch_pipeline — the GT harness, layer by layer

_Successor to `pipeline_flow.md`. Updated as final_hardening lands; every status row names a SHA or a run id._

Where a benchmark run starts, every layer it passes through, what each layer is
capable of, and — separately — what is actually *proven* about each one today.

Status vocabulary, used deliberately:

| Mark | Meaning |
|---|---|
| **VERIFIED** | I ran it this session and saw the result |
| **CODE-READ** | I read the implementation; behaviour asserted, not observed |
| **BROKEN** | Reproduced failing |
| **UNPROVEN** | Plausible from code, never observed end to end |

Nothing below is marked VERIFIED on the strength of a passing unit test alone.
A test proves the code does what the test says; it does not prove the layer
does its job in a real run.

---

## Layer 0 — Dispatch admission (before a single dollar is spent)

**Starts at:** `.github/workflows/deepswe_gt_harness_product_p0731.yaml` (paid
smoke20), with `deepswe_gt_harness_product.yml` as the provider-free
acceptance gate that must pass first.

**Capabilities**

- Rebuilds the producer *in-workflow* from the pinned Groundtruth source and
  asserts the vendored binary equals what it just built. The vendored binary is
  never trusted on sight.
  Recipe: `golang:1.22.5-bookworm@sha256:a07daa84…`, static
  (`-linkmode external -extldflags=-static`), `-trimpath -mod=readonly`,
  `-buildvcs=false`, `BUILD_TIME` hardcoded to `2026-09-02T06:51:07Z` so the
  digest reproduces.
- Verifies lineage and review provenance against a **separate review-inbox
  branch** (`gt-review-inbox`), pinned by `review_inbox_commit`. A review packet
  must exist there as a file, appear exactly once in `inbox/INDEX.json`
  `live_packets`, belong to exactly one ticket, and carry a digest over its own
  body.
- Pins that must move together: `producer_sha256`, `source_commit`,
  `source_tree`, `build_info_sha256`, `builder_image_digest`, plus two pins
  *outside* the bundle — `eval/miniswe_agent.py::_GT_BINARY_SHA256` (a runtime
  guard) and the workflow's `sha256sum --check`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| CI rebuild + digest assert | **VERIFIED** | Run 33778444842 failed the assert on a wrong pin; 33779096992 passed it after re-pinning to CI's own artifact `b4bfba37…` |
| Static linking | **VERIFIED** | CI `file` output: "statically linked"; ELF has no `PT_INTERP`, no `PT_DYNAMIC` |
| Lineage/provenance verification | **VERIFIED** | First refused a bundle-only edit (33779096992: `exact_source_review_packet_missing`), then passed once the packet existed on the branch (33785370968, 33791548818) — it cannot be satisfied by editing the bundle, which is the point |
| Review packet for `cffca1fd2` | **VERIFIED** | Exists on `gt-review-inbox` @ `ea2f30d3` (owner acceptance, `reviewer_verdict_present: false`); harness re-pinned at `5823193a`; provenance verifier `PASS`, CI 33785370968 green |

> This layer is the reason no money has been spent on a bad build. It is
> working *as designed* even though it is currently red.

---

## Layer 1 — Task materialisation

**Starts at:** Pier + Harbor (`datacurve-pier==0.3.1`, harbor 0.20.0) launching
the task's own container from `deepswe-bench/tasks/<task>/environment/Dockerfile`.

**Capabilities**

- Clones the upstream repo and rewrites its default branch to the task's
  `BASE_SHA`, then gc's future history so the reference solution cannot leak.
- Mounts the agent (`mini-swe-agent 2.4.6`) and the producer binary at
  `/opt/groundtruth/gt-index/gt-index`.
- Stages language servers into `/installed-agent/lsp-bin` and prefixes `PATH`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Task repo at base commit | **VERIFIED** | I cloned 19 of them at their own `BASE_SHA` and indexed them |
| LSP binaries staged | **CODE-READ** | 15 server references in the gate workflow; never observed resolving a symbol in a real run |

---

## Layer 2 — The producer (`gt-index`, Go)

**Starts at:** `gt_engine/indexer.py::ensure_index` → the binary with
`-root -output -max-files -workers -closure=true`.

**Three passes**

1. **Discover** — walk, language detect, `-max-files 200000`.
2. **Parse** — tree-sitter per language, `_INDEX_WORKERS` parallel. Parse
   failures are *counted*, not hidden (arktype: 384/458 parsed, 74 failures
   reported).
3. **Resolve** — a ladder of strategies, each with its own confidence and
   evidence type:

| Rung | Mechanism | Claim strength |
|---|---|---|
| 1.75 | `same_file` / `inherited` (self/this/Self + inheritance) | receiver proven |
| 1.93/1.94a | `import` / `import_type` | declared type |
| 1.94 | `impl_method` (1–3 implementors) | implementation set |
| 1.95/1.96 | `type_flow` (assignment + construction tracking) | declared type |
| 1.97 | `return_type` (return-shape bridging) | declared type |
| 1.98 | `unique_method` (name unique to one class) | **name only**, capped CANDIDATE 0.6 |
| 2 | `name_match` / `verified_unique` | global name, fallback |
| — | `vta` (variable type analysis) | flow-proven, candidate-only |

Then **publish**, atomically: nodes, edges, `closure`, `properties`,
`assertions`, `content_passages`, `cochanges`, `file_hashes`, plus resolution-v2
(`resolution_callsites`, `resolution_candidates`, `resolution_symbols`,
derivation facts).

**The property that dominates everything else:** publication is a single
transaction. One rejected candidate discards the *entire* index, and the run
then proceeds with **no graph at all**. For a graph-only product that is the
worst possible failure mode, and until this session it was silent.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Parses and resolves real repos | **VERIFIED** | 18/19 smoke20 repos, e.g. arktype 458 files → 159,548 nodes / 188,264 edges / 16,431 callsites |
| Atomic publication | **VERIFIED (the hard way)** | 4 distinct defects each destroyed a whole graph |
| Closure | **VERIFIED** | arktype depth 1: 1402, depth 2: 1150, depth 3: 1050 |
| `MaxDepth = 3`, `MinEdgeConfidence = 0.7` | **VERIFIED** | `internal/closure/closure.go:52,60` — the requested depth-6 change is **not done** |
| boa | **BROKEN — fix in flight** | Publication never terminates; WAL past 11.5 GB; *not* the closure (`-closure=false` reproduces it). Root cause traced: `AnalyzeVTA` called unbounded although `AnalyzeVTAWithBudget` and a typed `budget_exhausted` abstention already exist; the per-candidate flow-fact writer does a `SELECT` per fact with no prepared statements. final_hardening item 1 (stream A) owns it |

**Defects found and fixed this session** (all fixture-first, RED replayed in a
clean checkout by the pre-push hook):

1. `validateCandidateDerivation` rejected an empty `Mechanism` that
   `resolutionDerivation` already maps to `global_name`/`partial` — two
   validators contradicting each other.
2. `unique_method` missing from the derivation vocabulary entirely.
3. Partial-VTA callsites claimed `vta` over merged hierarchy candidates that
   carry no flow proof.
4. Candidates sharing one `TargetStableID` collided on the candidate edge id
   (**pre-existing** — the shipped producer `0aadb1b9` fails aiomonitor
   identically, so that task has never had a graph).

---

## Layer 2b — Derived layers landed by final_hardening

Everything here is a pure projection or extraction over what Layer 2 already
stores. None of it calls a model. Each row names the exact SHA and the number it
was measured at, on the arktype graph (`04355e8b`) unless stated.

| Capability | Where | Mark | What was measured |
|---|---|---|---|
| **Symbol contract** — `{params, returns, guards, side_effects, boundaries, data_flow, visibility}` projected from `properties`, every claim carrying its `properties.id`, byte-identical across runs, explicitly empty when no facts exist | harness `fac84bcc` — `gt_engine/contract.py` | **MEASURED** | 3,511 symbols; contract density **1.39 facts/symbol** (the 2.6 headline includes non-contract kinds); **60% of symbols empty**; `side_effects` on 68 symbols (1.9%), `boundaries` 197, `guards` 240. Carries the producer-minted id for 3,509/3,511 |
| **Symbol-level hybrid retrieval** — FTS5 over `nodes_fts`, property-value rank, dense via `dense_runtime` with a *named* degraded reason, fused by RRF with per-source provenance; never touches an edge or tier | harness `1122c213` — `gt_engine/retrieval.py` (companion to the file-level, identity-bound `hybrid_retrieval.py`) | **MEASURED** | Both example queries < 0.1 s over the 379 MB graph; property-only hits demonstrated ("validates empty input" → `createNode` via a `param` fact, no identifier match). `nodes_fts` indexes all 159,548 nodes, so results are filtered to source labels |
| **Co-change extraction** — bounded by construction (500 commits × C(50,2) = 612,500 pairs worst case, zero means default, no way to spell unlimited), every return path carries a `Reason` | producer `ce5e0370` — `internal/cochange/` (REV-255 GREEN); wiring waits on item 1 | **MEASURED** | Depth-1 fixture → `shallow_clone`, 0 pairs. Same repo at `--depth=500` (the state CI builds) → **23,720 pairs**, 442 commits, 58 mass commits skipped and counted |
| **Test-witnessed processes** — certified `CALLS` paths (`confidence ≥ 0.7`) from an entry point, emitted only with a witnessing `assertions` row; `witness_assertion_id NOT NULL` in the schema | producer `a2d536bf4` — `internal/process/`; wiring waits on item 1 | **MEASURED** | 105 assertions → 53 with a target (52 score 0.0) → **6 entry points, 95 processes**, depth 1:25 / 2:54 / 3:16. Only **3 of 53** test→target CALLS edges are CERTIFIED — the assertion is the bridge certified traversal cannot cross |
| **Communities with falsifiable cohesion** — deterministic Leiden-style CPM optimiser (explicit resolution, not modularity; `cpm_leiden_deterministic_v1`), weight from CERTIFIED `CALLS` + co-change only, every community internally connected, membership never touches an edge or tier; cohesion = held-out co-modification rate with a Wilson interval, NULL-with-reason when unmeasurable | producer `43514ced1` — `internal/community/`; wiring waits on item 1 | **MEASURED** | 81 communities over 982 of 1,046 files; held-out cohesion **0.4585 [0.4277, 0.4897] n=988** vs chance 0.0452 (~10× lift) — but 848 of 988 pairs sit in one 154-file test community, the mean over the 20 measurable communities is **0.0611**, and 61 of 81 were unmeasurable on a 29-commit holdout. One community: structural 1.000, held-out **0.000** over 35 pairs. Certified structure alone: 165 files at 0.1007; with co-change: 982 at 0.4585 |

**One identity across all of it.** `nodes.stable_id` is NULL on every code symbol,
but the producer mints one in `resolution_symbols` (`native_id = nodes.id`) for
3,509 of 3,511, and the engine's `resolution_provenance.stable_symbol_id`
reproduces it **400/400 bit-identical**. Contracts, retrieval, processes and the
resolution candidates all carry that one id.

**The closure sidecar cannot yield a path.** Its columns are
`(source_id, target_id, depth, min_confidence)` — it records *that* A reaches B
in N hops, never *which* intermediates. Processes walk `edges` directly under a
stricter rule, and a test fails loudly if the closure ever gains path detail.

---

## Layer 3 — Index lifecycle in the harness

**Starts at:** `gt_engine/indexer.py::ensure_index`.

**Capabilities**

- `_build_index_with_attempts` — 3 bounded retries, attempt trail sealed into
  `index-failure-resource.json` with cgroup memory evidence, exit code, and a
  4 KiB scrubbed stderr tail.
- **`BenchmarkGraphRequired`** — under `identity_scope == "benchmark_bound"`,
  an indexable repo that produced no graph *raises* rather than degrading.
  Re-raised **by name** at both real call sites before any broad handler
  (`scripts/miniswe_gt_run.py:397`, `gt_engine/__init__.py:37`).
- `start_lsp_promotion` → sealed as `gt.lsp_promotion.v1` with four states.
- Dense retrieval: `Snowflake/snowflake-arctic-embed-m@7802add0`, ONNX,
  `gt.snowflake_onnx_asset.v1`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Fail-fast prevents a graphless paid run | **VERIFIED** | Gate 33730572741 aborted at **zero provider cost** with the real abort captured |
| Captured stderr is diagnostic | **VERIFIED** | It named `candidate derivation requires stable target identities and mechanism`, which reproduced exactly |
| Retries | **VERIFIED** | `build_attempts: 3` recorded, all `nonzero_exit` |
| LSP promotion actually promotes edges | **UNPROVEN** | Wired and sealed; never observed changing a graph |
| ONNX actually retrieves | **UNPROVEN** | Asset pinned and real; retrieval quality unmeasured |

---

## Layer 4 — The agent loop and decision boundaries

**Starts at:** `gt_engine/__init__.py::create_bridge` → `GTBridge.enrich`
(`gt_engine/bridge.py:3082`).

**Seven boundaries** (`gt_engine/graph_lease.py`): `repository_start`,
`identity_ambiguity`, `pre_edit`, `post_edit_graph_delta`,
`failure_observation`, `verification_selection`, `pre_submit`.

**Capabilities**

- One planning call at `repository_start`; thereafter delivery is deterministic
  and driven by localisation and the action taken (view / edit / open).
- Dedup via `dedup_key` and a last-payload hash, so the same fact is not
  re-shipped.
- Budgets: "an over-budget delta is dropped **whole**, never clipped" — a
  truncated fact would be a lie, so it is withheld instead.
- Graph freshness states: `ABSENT`, `CURRENT`, `STALE`, `BUILDING`, `FAILED`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Boundary set and dedup | **CODE-READ** | Read the enum and the dedup/budget laws |
| One LLM planning call | **CODE-READ** | Asserted by design; not measured in a run |
| Deliveries actually improve the agent | **UNPROVEN** | This is what the smoke is *for* |

---

## Layer 5 — Attribution

**Starts at:** `gt_engine/attribution.py::feature_for_evidence` — maps a
concrete evidence envelope to one of the 17 census features. Trace is
append-only and hash-chained, and "correct-or-quiet": tracing can never break
the engine path.

**Status: CODE-READ.** The mapping is real and the trace is chained. What the
run journal showed for the failed gate is the point below.

---

## Layer 6 — Receipts and enforcement

**Starts at:** `gt_harness/runtime_receipts.py::issue_runtime_receipts`.

**The enforcement that matters** (`runtime_receipts.py:864`):

```python
if indexed_files > 0 and not utilisation.get("graph_backed_delivery"):
    errors.append("treatment_graph_evidence_absent")
```

Keyed on indexed **files**, not nodes — a broken index that produced zero nodes
must not be excused as "empty repo".

`GRAPH_BACKED_FEATURES` = `caller_contract`, `cochange_prior`, `def_partition`,
`signature_delta`. Localisation is deliberately **excluded**: `trace_frame` maps
to it and is runtime-derived, so counting it would let a graphless run claim
graph backing.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Enforcement present and correctly keyed | **VERIFIED (by reading + the incident)** | Run 33708231670's journal carried only `new_file_destination`, `context_delta`, `trace_frame`, `missing_role_postcreate:*`, `context_contract` — **zero** graph-backed evidence. GT-on ran without its graph and nothing stopped it. That hole is what this rule closes. |
| Rule has fired in anger | **UNPROVEN** | No paid run has reached it since |

---

## Layer 7 — Grading and attestation

Official verifier result → `miniswe_report.json` → attestation artifact.
Diagnostics and receipts are keyed by the **canonical task id**
(`resolve_run_task_identity`), not `sha256(task_text)[:16]` — the mismatch that
tripped both arms of attestation on run 33708231670.

**Status: VERIFIED as a diagnosis** (root-caused from the artifacts),
**UNPROVEN as a fix** (no paid run has completed since).

---

## Honest whole-system summary

| Layer | Working? |
|---|---|
| 0 Dispatch admission | Working as designed; **green** — packet on `gt-review-inbox` @ `ea2f30d3`, CI 33785370968 and 33791548818 |
| 1 Task materialisation | Working |
| 2 Producer | Working on 18/19; **boa broken**, fix in flight (item 1) |
| 2b Derived layers | **Measured** — contract, retrieval, co-change, processes landed; wiring of the two producer packages waits on item 1 |
| 3 Index lifecycle | Working; LSP + ONNX **unproven in a run** |
| 4 Agent loop | Unproven end to end |
| 5 Attribution | Unproven end to end |
| 6 Receipts/enforcement | Correct by construction; never fired in a real run |
| 7 Grading | Diagnosed, unproven |

**The system has never been observed working end to end with a graph.** Every
paid run so far either had no graph (silently) or was stopped before spending
money. Layers 0–3 are now genuinely evidenced. Layers 4–7 are exactly what a
successful smoke would prove, and nothing short of one will.

### Open, ordered

1. ~~Review packet for `cffca1fd2`~~ — landed (`ea2f30d3`); dispatch is unblocked and awaits typed authorisation.
2. boa's non-terminating publication — final_hardening item 1, stream A in flight. Blocks the remaining 19, not the gate.
3. `MaxDepth` 3 → 6 — **deliberately not scheduled**: at 2.4% CERTIFIED edges it widens traversal over an already-thin set. Resolution rate (36%) and property density are the levers. Owner decides whether the request stands.
4. LSP promotion and ONNX retrieval — wired but unmeasured in a run.
5. Wiring `internal/cochange/` and `internal/process/` into `main.go` — fixture-first, after item 1.
6. Engine side of co-change: emit `cochange_partner`, allow-list `cochange_prior` in the capability packs — today it is dead at both layers.

---

# Graph depth — what values are actually stored

"Depth" here means the *kind of information* each row carries, not traversal
distance. Measured on the real arktype graph (458 files, base commit
`04355e8b`), not on the schema.

## The headline count is 97.8% bookkeeping

| | arktype | adaptix |
|---|---|---|
| Total nodes | 159,548 | 110,677 |
| **Code symbols** (Function/Class/Method/File) | **3,511 — 2.2%** | **3,160 — 2.9%** |
| Provenance facts (CompletenessFact 113,695, Callsite, UnresolvedFact, DerivationFact) | 156,035 | 107,515 |
| Total edges | 188,264 | 150,918 |
| **Semantic edges** (CALLS/IMPORTS/CONTAINS) | **8,972 — 4.8%** | 8,153 — 5.4% |
| CERTIFIED edges | 4,593 — 2.4% | 9,575 — 6.3% |

Quoting "159,548 nodes" as evidence of a rich graph is wrong, and I did it
repeatedly. The producer writes ~6.9 fact nodes per callsite about its own
reasoning. That is also why boa dies: 68,227 callsites is roughly half a
million fact nodes before any candidate blow-up. **The thing that does not
scale is the provenance layer, not the code graph.**

## What a code symbol carries

Fill rate across all 3,511 code symbols:

| Field | Filled |
|---|---|
| name, qualified_name, file_path, start_line, end_line, language, is_test, is_exported | 100% |
| signature | 40.1% |
| return_type | 24.2% |
| parent_id | 8.9% |

A real row, complete:

```
label Function · name shouldThrow · file ark/attest/__tests__/demo.test.ts
start_line 9 · end_line 11 · signature "(a: false) =>" · is_exported 1 · is_test 1
```

No docstring, no decorators, no structured parameter types, no visibility on the
node itself. **At the node level this is ctags plus a signature string.**

## The depth is in `properties`, and it is real

9,233 rows — about 2.6 per symbol — and these are genuinely semantic:

| Kind | Rows | Kind | Rows |
|---|---|---|---|
| caller_usage | 1,637 | field_read | 565 |
| fingerprint | 1,407 | visibility | 403 |
| param | 1,285 | boundary_condition | 323 |
| data_flow | 1,236 | guard_clause | 317 |
| return_shape | 1,158 | call_order | 279 |
| class_field | 255 | side_effect | 151 |

`guard_clause`, `boundary_condition`, `side_effect` and `data_flow` are exactly
the GT+gnx-class facts that justify the product. They exist. There are just
**2.6 of them per symbol**, and the thinnest kinds — side effects, guards,
boundary conditions — are the ones a model would most benefit from.

`assertions` links tests to targets with kind/expression/expected. For a
458-file TypeScript repo with a substantial suite: **105 rows**.

## The type-flow columns are declared and empty

`resolution_candidates` (10,820 rows) is the deepest table in the schema. Its
identity columns are 100% filled. Its *evidence* columns are not:

| Column | Filled |
|---|---|
| receiver_chain | 55.2% |
| import_chain | 16.2% |
| receiver_origin | 3.4% |
| **receiver_type** | **0.1%** (8 rows of 10,820) |

And these node columns are **entirely empty** across the whole graph:
`allocated_type_id`, `allocation_site_id`, `declared_receiver_type_id`,
`receiver_value_id`, `field_id`, `configuration_artifact_id`, `input_fact_ids`.

That is the allocation-site and field-sensitive VTA schema. It is modelled and
stores nothing.

## Empty capabilities

- `cochanges` — **0 rows** in every graph built *from the local fixtures*, because every smoke fixture is a depth-1 clone; the producer's `mineCochanges` emitter exists and production containers clone fully. At `--depth=500` the same repo yields 23,720 pairs. `cochange_prior`, one of the
  four `GRAPH_BACKED_FEATURES` the graph-use enforcement depends on, can never
  fire; `cochange_partner` has no emitter anywhere in the codebase.
- `content_passages` — **the table does not exist**. I listed it as schema.

## Honest reading

The schema promises GT+gnx depth. The values deliver:

- a ctags-level symbol table (3,511 symbols),
- a resolved call graph at 36% resolution (5,933 of 16,431 callsites), only
  2.4% of edges CERTIFIED,
- a genuinely interesting but thin property layer (2.6 facts per symbol),
- almost no type-flow evidence (receiver_type 0.1%),
- and two advertised capabilities that are empty.

**Raising closure MaxDepth 3 → 6 would widen traversal over an already-thin
certified set. It is the wrong lever.** Resolution rate and property density are
the metrics that would move quality.
