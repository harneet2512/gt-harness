# GT and gnx end to end: pipeline, stored depth, and the delta

**Issue:** HAR-83
**Rule carried from [00-comparison.md](00-comparison.md):** code wins over damaged or
aspirational documentation. Every number below was measured or read first-hand;
anything I did not verify is marked **UNVERIFIED** rather than asserted.

**Measurement baseline.** GT figures come from a real graph built by the producer
at `cffca1fd2` over arktype at its own task base commit `04355e8b` (458 files),
cross-checked against adaptix at `a691069f` (421 files). gnx figures come from
the pinned source at `abhigyanpatwari/GitNexus` — the same tree the harness
checks out as `.pinned/gitnexus` — read from its schema, not from a running
instance.

**Status vocabulary**

| Mark | Meaning |
|---|---|
| **MEASURED** | Read out of a real store or observed running |
| **CODE-READ** | Read in the implementation; behaviour asserted, not observed |
| **UNVERIFIED** | Not traced end to end. Stated as a gap, not a claim |
| **BROKEN** | Reproduced failing |

No claim is made anywhere in this document about any review verdict.

---

# Part A — GT, task start to receipt

## A1. What runs first, and in what order

```
task given
  └─ workflow resolves pins, rebuilds the producer from source, asserts the digest
     └─ container: repo checked out at BASE_SHA, agent + producer mounted
        └─ ensure_index()                      ← graph construction starts here
           ├─ pass 1  discover files
           ├─ pass 2  parse (tree-sitter, N workers)
           ├─ pass 3  resolve calls (strategy ladder)
           ├─ publish ATOMICALLY (one transaction)
           ├─ closure sidecar over VERIFIED CALLS edges
           └─ start_lsp_promotion()            ← background, non-blocking
              └─ create_bridge() → GTBridge
                 ├─ repository_start   → one planning call (select_catalog)
                 └─ per tool call: enrich(tool_name, args, output, …)
                    ├─ observation.received traced
                    ├─ boundary chosen
                    ├─ evidence selected, budgeted, deduped, sealed
                    └─ suffix appended to the tool output
                       └─ receipts → utilisation → attestation
```

The ordering constraint that matters: **the graph is built before the first
planning call**, and planning is the only non-deterministic LLM step. Everything
after it is selected deterministically from the graph plus the action taken.

## A2. Graph construction — what is actually built

**MEASURED**, arktype:

| Store | Rows | What it holds |
|---|---|---|
| `nodes` | 159,548 | see split below |
| `edges` | 188,264 | see split below |
| `resolution_callsites` | 16,431 | one per call site |
| `resolution_candidates` | 10,820 | possible targets per callsite |
| `resolution_symbols` | 3,509 | symbol identity table |
| `properties` | 9,233 | per-symbol semantic facts |
| `closure` | 3,602 | transitive reach, depth ≤ 3 |
| `assertions` | 105 | test → target links |
| `file_hashes` | 458 | incremental staleness |
| `cochanges` | **0** | empty in every graph built |
| `content_passages` | **table does not exist** | — |

**The node count is 97.8% provenance bookkeeping.**

| Node label | Count |
|---|---|
| CompletenessFact | 113,695 |
| Callsite | 16,431 |
| UnresolvedFact | 15,089 |
| DerivationFact | 10,820 |
| **Function** | **2,747** |
| **Class** | **350** |
| **Method** | **314** |
| **File** | **100** |

Code symbols: **3,511 — 2.2%**. Edges split the same way: 8,972 semantic
(CALLS 5,933 · IMPORTS 2,445 · CONTAINS 314) out of 188,264, and only
**4,593 CERTIFIED (2.4%)**. Resolution rate is **5,933 of 16,431 callsites =
36%**.

The producer writes ≈6.9 fact nodes per callsite about its own reasoning. That
is also the scaling wall: boa has 68,227 callsites, so publication attempts
roughly half a million fact nodes before any candidate blow-up.

## A3. Depth — what values are stored, per store

### Code symbol node (3,511 rows) — **MEASURED** fill rate

| Field | Filled |
|---|---|
| `name`, `qualified_name`, `file_path`, `start_line`, `end_line`, `language`, `is_test`, `is_exported` | 100% |
| `signature` | 40.1% |
| `return_type` | 24.2% |
| `parent_id` | 8.9% |

A complete row, verbatim:

```
label Function · name shouldThrow · file ark/attest/__tests__/demo.test.ts
start_line 9 · end_line 11 · signature "(a: false) =>" · is_exported 1 · is_test 1
```

No source text, no docstring, no decorators, no structured parameter types.
**At node level this is ctags plus a signature string.**

### `properties` — the real semantic layer (9,233 rows, ≈2.6 per symbol)

Columns: `node_id`, `kind`, `value`, `line`, `confidence`.

| Kind | Rows | Kind | Rows |
|---|---|---|---|
| caller_usage | 1,637 | field_read | 565 |
| fingerprint | 1,407 | visibility | 403 |
| param | 1,285 | boundary_condition | 323 |
| data_flow | 1,236 | guard_clause | 317 |
| return_shape | 1,158 | call_order | 279 |
| class_field | 255 | side_effect | 151 |

`guard_clause`, `boundary_condition`, `side_effect` and `data_flow` are the
facts that justify the product. They exist and they are the right shape. They
are also the thinnest kinds in the table.

### `resolution_candidates` (10,820 rows) — deepest table in the schema

| Column | Filled | |
|---|---|---|
| `target_stable_id`, `target_native_id`, `ordinal`, `selected`, `declared_scope`, `receiver_shape`, `export_status`, `parser_complete`, `verification_status`, `dynamic_dispatch` | 100% | identity and flags |
| `mechanism` | 97.1% | which rung resolved it |
| `receiver_chain` | 55.2% | |
| `import_chain` | 16.2% | |
| `receiver_origin` | 3.4% | |
| **`receiver_type`** | **0.1% — 8 rows** | the actual type evidence |

Empty across the entire graph: `allocated_type_id`, `allocation_site_id`,
`declared_receiver_type_id`, `receiver_value_id`, `field_id`,
`configuration_artifact_id`, `input_fact_ids` — the allocation-site and
field-sensitive VTA columns. **Modelled, storing nothing.**

### `edges` — trust tiers

| Tier | Count |
|---|---|
| STRUCTURAL | 156,035 |
| CANDIDATE | 26,535 |
| CERTIFIED | 4,593 |
| SPECULATIVE | 1,101 |

### `closure`

`MaxDepth = 3`, `MinEdgeConfidence = 0.7` (`internal/closure/closure.go:52,60`).
arktype: depth 1 = 1,402 · depth 2 = 1,150 · depth 3 = 1,050.

## A4. Localization

**CODE-READ.** Localization is delivered as `localization`, `brief_localization`,
`ranked_localization` and `trace_frame` envelopes, all mapping to the single
`localization` census feature. `trace_frame` is derived from the runtime failure
trace, not the graph — which is why it is deliberately **excluded** from the
graph-backed feature set: counting it would let a graphless run claim graph use.

The router extracts candidate paths from tool output with a path regex and
carries recent failure paths (`_recent_failure_paths`, capped at 8) forward from
an errored observation to the next boundary.

**UNVERIFIED:** the exact query issued against the graph for ranked localization,
and what it returns, was not traced end to end for this document.

## A5. Interactive events — what is sent on OPEN and on EDIT

Every tool call enters `GTBridge.enrich(tool_name, tool_args, output, is_error,
edit_before=…, edit_after=…)`. It classifies the observation into
`(cmd, rc, changed, viewed, eba)` and traces `observation.received`:

```json
{
  "tool_call_id": "...",
  "tool_name": "...",
  "command_sha256": "<sha256 of the normalized command>",
  "returncode": 0,
  "is_error": false,
  "changed_files": ["..."],
  "viewed_files": ["..."],
  "output_chars": 1234
}
```

The command is stored **as a digest, not as text** — commands can carry secrets.

**On OPEN / view** (`viewed_files` non-empty): the caller-side envelopes are
eligible — `caller_contract`, `caller_contract_view`, `caller_break`,
`companion_surface` (grouped as `_CALLER_TYPES` in `evidence_router.py`), plus
`def_partition` family (`def_ref_partition`, `name_fold`, `wrong_surface`,
`body_concept`) and localization.

**On EDIT** (`changed_files` non-empty): `pre_edit` and `post_edit_graph_delta`
boundaries, with `signature_delta` / `signature_mismatch` / `companion_surface`,
`syntax_result`, `covering_red`, and `newfile_precedent` for a created file
(`missing_role:` / `missing_role_postcreate:` prefixes both fold into
`newfile_precedent`).

**On ERROR**: paths are harvested from the output and held as
`_recent_failure_paths`; `recovery` and `coherence_collapse` fire when the same
failure recurs across an edit.

Two delivery laws, **CODE-READ** from `bridge.py`:

- seal before append — the sealed record covers the exact bytes shipped;
- an over-budget delta is **dropped whole, never clipped** — a truncated fact
  would be a lie, so it is withheld instead.

Repeats are suppressed by `dedup_key` plus a last-payload hash.

**UNVERIFIED:** the exact rendered payload for each envelope type at each
boundary. What is established here is the vocabulary, the routing and the
sealing laws — not the byte-level content of each delivery.

## A6. Evidence → feature census, and what the run is graded on

`feature_for_evidence()` folds ~28 envelope types into 17 census features. The
capability pack for a task role gates which are even allowed
(`role_packs.py`). For the default `code_behavior` pack:

```
obligations, localization, caller_contract, def_partition, newfile_precedent,
signature_delta, syntax_result, covering_red, recovery, submit_refusal
```

Then `graph_utilisation()` asks which delivered features could only have come
from the graph:

```python
GRAPH_BACKED_FEATURES = {"caller_contract", "cochange_prior",
                         "def_partition", "signature_delta"}
```

and `runtime_receipts.py:864` fails the run when an indexed repository produced
no graph-backed delivery:

```python
if indexed_files > 0 and not utilisation.get("graph_backed_delivery"):
    errors.append("treatment_graph_evidence_absent")
```

**Defect in that set, MEASURED:** `cochange_prior` can never fire. `cochanges`
is empty in every graph built. Correction (REV-253): the producer DOES have an
emitter -- `mineCochanges` (`cmd/gt-index/main.go:2520`, wired at `:1331`, format bug
fixed in `7713e9cd`). The zero rows come from the indexed checkouts carrying no usable
history (depth-1 fixture clones), not from a missing miner. What is genuinely absent is
on the engine side: `cochange_partner` evidence is not emitted in `gt_engine` and
`cochange_prior` is in no capability pack. Earlier text here claimed "no emitter anywhere in the
codebase; and it is absent from every capability pack's `allowed_evidence`. The
enforcement therefore rests on **three** features, not four.

## A7. Status per stage

| Stage | Status | Basis |
|---|---|---|
| Pin/provenance admission | **Working, currently red** | CI rebuilds the producer and asserts the digest; lineage is checked against a review-inbox branch that must actually contain a packet |
| Task materialisation | **MEASURED working** | 19 repos cloned at their own base commits and indexed |
| Parse + resolve | **MEASURED working, 18/19** | boa **BROKEN** — publication does not terminate, WAL past 11.5 GB, not the closure |
| Atomic publication | **Working after 4 fixes** | Each of 4 defects destroyed an entire graph; one was pre-existing and means aiomonitor has never had a graph |
| Depth of stored values | **MEASURED thin** | 2.2% code symbols; `receiver_type` 0.1%; VTA columns empty; `cochanges` empty |
| Fail-fast on missing graph | **MEASURED working** | A gate run aborted at zero provider cost with the real abort captured |
| LSP promotion | **UNVERIFIED** | Wired and sealed; never observed promoting an edge |
| ONNX dense retrieval | **UNVERIFIED** | Model pinned by revision and real; retrieval never measured |
| Localization query/return | **UNVERIFIED** | Vocabulary and routing read; the query itself not traced |
| Edit/open payload bytes | **UNVERIFIED** | Routing and sealing laws read; exact payloads not traced |
| Graph-use enforcement | **Never fired** | Correct by construction, minus the dead feature; untested against a real graph-having run |
| Receipts/attestation | **UNVERIFIED as a fix** | Canonical-id keying diagnosed from artifacts; no paid run completed since |

---

# Part B — gnx (GitNexus), end to end

Read from the pinned source `abhigyanpatwari/GitNexus` — the tree the harness
checks out as `.pinned/gitnexus`. Store is LadybugDB (Kùzu-family, Cypher), not
SQLite. No gnx instance was run for this document, so everything here is
**CODE-READ** unless marked otherwise.

## B1. The ingestion pipeline — 21 named phases, topologically ordered

gnx does not have a three-pass producer. It has a phase registry where each
phase declares dependencies and the runner executes them in topological order,
passing typed outputs downstream (`pipeline.ts`, `pipeline-phases/runner.ts`):

```
scan → structure → markdown → cobol → parse
     → routes → tools → orm
     → crossFile → scopeResolution
     → springConfig → springAutoConfiguration → springAop → springAopInheritance
     → pruneLocalSymbols
     → taintSummaries → callSummaries
     → mro → di
     → communities → processes
```

What each adds, in GT terms:

| Phase | What it produces |
|---|---|
| `scan` / `structure` | filesystem walk, File/Folder nodes |
| `markdown`, `cobol` | non-mainstream ingestion paths with their own processors |
| `parse` | tree-sitter AST → symbol nodes |
| `routes` | HTTP route surfaces → `HANDLES_ROUTE` |
| `tools` | agent/tool handlers → `HANDLES_TOOL` |
| `orm` | ORM entities and queries → `QUERIES` |
| `crossFile` | cross-file import/reference stitching |
| `scopeResolution` | the call-resolution ladder (below) |
| `springConfig`, `springAutoConfiguration`, `springAop`, `springAopInheritance` | Spring bean wiring and aspect weaving → `ADVISED_BY`, `INJECTS` |
| `pruneLocalSymbols` | drops inert local value symbols — explicitly *graph construction*, not analysis |
| `taintSummaries` | per-function taint summaries |
| `callSummaries` | per-call summaries |
| `mro` | method resolution order (multiple inheritance) |
| `di` | dependency injection graph |
| `communities` | clustering into `Community` nodes |
| `processes` | end-to-end flow objects → `Process` nodes, `STEP_IN_PROCESS` |

The Spring phases carry explicit **cost caps** as options
(`springAopMaxCandidateInspectionsPerAdvice`, `springAopMaxAdvisedEdges`, …),
i.e. the expensive passes are bounded by construction. GT's publication has no
equivalent cap, which is why boa runs away.

`skipGraphPhases` turns off MRO, communities and processes for fast runs — the
analysis layer is separable from graph construction.

## B2. The scope-resolution ladder — 11 passes

`scope-resolution/passes/`:

```
receiver-bound-calls      compound-receiver        property-dispatch
imported-return-types     imported-value-refs      return-shape-members
callable-value-flow       overload-narrowing       unique-name-properties
mro                       free-call-fallback
```

Resolution outcome vocabulary: `resolved`, `external`, `unknown`, `suppressed`,
plus call-shape tags `call`, `field`, `index`, `await`.

Compare GT's ladder (same purpose, different names): `same_file`, `inherited`,
`import`, `import_type`, `type_flow`, `return_type`, `impl_method`,
`unique_method`, `name_match`, `verified_unique`, `vta`.

The overlap is close — `return-shape-members` ≈ `return_type`,
`unique-name-properties` ≈ `unique_method`, `imported-*` ≈ `import_type`. gnx has
two GT lacks outright: **`overload-narrowing`** (choosing among overloads by
arity/type) and **`mro`** as a first-class resolution pass. gnx also has explicit
`unresolved-receivers.ts` and `undecided-satisfaction.ts` — it *models* what it
failed to resolve, which is the ambiguity representation 00-comparison flagged.

## B3. What gnx stores per node

17 node tables: File, Folder, Function, Class, Interface, Method, CodeElement,
Community, Process, Section, Struct, Enum, Macro, Typedef, Union, Namespace,
Trait, Impl, TypeAlias, Const, Static, Variable, Property.

```
CREATE NODE TABLE Function (
  id STRING, name STRING, filePath STRING,
  startLine INT64, endLine INT64, isExported BOOLEAN,
  content STRING,          -- the actual source text of the symbol
  description STRING,      -- generated natural-language summary
  convexEndpointFactory STRING,
  PRIMARY KEY (id))
```

`content` + `description` recur on File (content only), Function, Class,
Interface, Method, CodeElement and every `CODE_ELEMENT_BASE` type. `Method` adds
`returnType`; `Class` adds `frameworkAnnotations STRING[]`; `Const`/`Function`
carry `convexEndpointFactory` (a framework-specific hook).

**Community** — `label`, `heuristicLabel`, `keywords STRING[]`, `description`,
`enrichedBy`, `cohesion DOUBLE`.
**Process** — `label`, `heuristicLabel`, `processType`, `communities STRING[]`,
`entryPointId`, `terminalId`.

`heuristicLabel` alongside `label`, and `enrichedBy`, mean gnx **records whether
a name came from a heuristic or from an LLM**. `cluster-enricher.ts` is explicit:
"LLM-based enrichment for community clusters. Generates semantic names,
keywords, and descriptions using an LLM," behind an injectable `LLMClient`. So
enrichment is optional and its provenance is stored — the graph degrades to
heuristic labels without a model.

## B4. Relations

```
CREATE REL TABLE CodeRelation (
  <typed FROM/TO pairs>, type STRING, confidence DOUBLE,
  reason STRING, step INT32)
```

30 types: CONTAINS, DEFINES, IMPORTS, CALLS, EXTENDS, IMPLEMENTS, HAS_METHOD,
HAS_PROPERTY, ACCESSES, METHOD_OVERRIDES, OVERRIDES (legacy alias),
METHOD_IMPLEMENTS, MEMBER_OF, STEP_IN_PROCESS, HANDLES_ROUTE, FETCHES,
HANDLES_TOOL, ENTRY_POINT_OF, WRAPS, QUERIES, INJECTS, CONDITIONAL_ON, DECLARES,
ADVISED_BY, CFG, REACHING_DEF, TAINTED, SANITIZES, TAINT_PATH, CDG,
POST_DOMINATE.

Every relation carries `reason` — free-text justification — plus `confidence`
and `step` (which pass produced it). `REACHING_DEF` rides the variable name in
`reason`; `CDG` rides its `'T'|'F'` branch label there.

**Honesty note, from gnx's own comments:** the taint/PDG substrate (CFG,
REACHING_DEF, TAINTED, SANITIZES, TAINT_PATH, CDG, POST_DOMINATE) is *reserved
and emitted by no phase yet* — CFG is M1, REACHING_DEF M2, the taint types M3/M4
— and the CFG/PDG build is opt-in behind a `--pdg` flag. This is the same
declared-but-empty pattern found on the GT side and must not be counted as a
delivered gnx capability.

## B5. Embeddings and search

```
CREATE NODE TABLE CodeEmbedding (
  id STRING, nodeId STRING, chunkIndex INT32,
  startLine INT64, endLine INT64,
  embedding FLOAT[384], contentHash STRING, PRIMARY KEY (id))
```

- 384 dims, snowflake-arctic-embed-**xs** (GT uses arctic-embed-**m**).
- HNSW vector index, cosine metric.
- Held in a **separate table** explicitly to avoid copy-on-write overhead.
- **Chunked**: `chunkIndex` + `startLine`/`endLine`, so a long function embeds as
  several vectors that still point back to line ranges.
- `contentHash` drives staleness — unchanged nodes are not re-embedded.

**Full-text search** (`fts-schema.ts`) indexes `name`, `content` *and*
`description` on Function, Class, Method, Interface, Constructor, Struct and the
rest of the embeddable labels; File is name+content only because it has no
`description` column. So gnx supports lexical search over source text and over
generated summaries, alongside vector search.

That is a **three-way retrieval surface**: graph traversal, full-text, and
vector — over the same node ids.

## B6. Language and framework coverage

tree-sitter grammars: c-sharp, cpp, go, java, javascript, php, python, ruby,
rust, typescript — **10** — plus dedicated COBOL and markdown processors.

Framework awareness is deep but narrow: `frameworks/` contains **spring** only,
with four dedicated phases (config, auto-configuration, AOP, AOP inheritance),
plus generic `routes`, `tools`, `orm` and `di` phases.

**GT covers 30 languages** (bash, c, cpp, csharp, css, cue, elixir, elm, golang,
groovy, hcl, html, java, javascript, kotlin, lua, markdown, ocaml, php,
protobuf, python, ruby, rust, scala, sql, svelte, swift, toml, typescript, yaml)
and has its own framework overlay in `api_edges.go` covering route/handler
surfaces across frameworks. **Breadth is GT's, depth-per-framework is gnx's.**

---

# Part C — The delta, and how GT beats it

**Citation discipline.** The references below are named from model knowledge, not
fetched. They are load-bearing for the *design argument*, so they must be
verified at exact-citation level before this document is treated as referenced
work. Marked **[cite-unverified]** collectively here rather than on every line.

**Strategy vocabulary.** Not every gap should be closed the same way.

| Strategy | When it applies |
|---|---|
| **INVERT** | gnx stores a conclusion; GT stores the evidence and derives something stronger |
| **COMPOSE** | GT already holds the inputs; the capability is a join or a pass away |
| **PROJECT** | The data exists; only a rendering is missing |
| **COPY + AMPLIFY** | GT has nothing to derive from. Copy the design, then extend it where gnx structurally cannot follow |

---

## C1. The table

| # | gnx capability | GT primitive today | Strategy | The move |
|---|---|---|---|---|
| 1 | `content` (source text) on every node | Line ranges on 3,511 symbols; byte ranges on 16,431 callsites; `file_hashes`; `fingerprint` ×1,407 | **INVERT** | Store a content *address* `(file_hash, start, end)`, resolved at delivery against a hash-verified file |
| 2 | `description` — LLM prose per node | `properties`: `param` 1,285, `return_shape` 1,158, `guard_clause` 317, `boundary_condition` 323, `side_effect` 151 | **INVERT** | Emit a structured behavioural contract instead of prose |
| 3 | Chunked embeddings of raw text, arctic-embed-**xs**, HNSW | ONNX arctic-embed-**m**, pinned by revision | **INVERT** | Embed the contract, not the source |
| 4 | FTS over name + content + description | `nodes_fts` already populated; `sqlite_fts5` already in build tags | **COMPOSE** | Hybrid retrieval: lexical + dense over the contract, fused |
| 5 | `contentHash` staleness | `fingerprint` property, `file_hashes`, `incremental_stale_suppression` | **INVERT** | Invalidate on the semantic fingerprint, not the byte hash |
| 6 | `Community` — keywords, description, cohesion, enrichedBy | `closure`, 4 trust tiers, `caller_usage` 1,637, **`cochanges` exists but empty** | **COPY + AMPLIFY** | §C2 |
| 7 | `Process` — heuristic entry/terminal, `STEP_IN_PROCESS` | `closure` paths, `dispatch_form`, `api_edges.go`, **`assertions`** | **COMPOSE** | Processes as interprocedural slices witnessed by a covering test |
| 8 | 17 node tables | 4 labels + `properties` carrying `class_field`, `param`, `visibility` | **COMPOSE** | Extend symbol emission; keep one node model. Lands across 30 languages, not 10 |
| 9 | 30 relation types, one flat table (`type`, `confidence`, `reason`, `step`) | Typed edges + `DerivationFact` ×10,820 + 4 tiers + **retained candidate sets** | **COMPOSE** | Keep the over-approximation visible instead of collapsing it |
| 10 | `reason` free text per relation | `DerivationFact` ×10,820 | **PROJECT** | Render `reason` from derivation facts |
| 11 | `step` — producing pass | `pass_kind` on 124,515 nodes | **PROJECT** | Join onto edges |
| 12 | `overload-narrowing` pass | `param` ×1,285 + `signature` (40%) | **COMPOSE** | Arity/type narrowing as a query over stored data |
| 13 | `mro` pass | Inheritance chains (772 parent-linked classes in boa) | **COMPOSE** | C3 linearisation over stored parents, published with a trust tier |
| 14 | Explicit caps on expensive passes | **Deficit — publication uncapped** | **COPY + AMPLIFY** | §C3 |
| 15 | `skipGraphPhases` | One all-or-nothing transaction | **COMPOSE** | Two transactions, two receipts |

---

## C2. Row 6 — Community, copied then amplified

### Copy exactly

The node shape, including the discipline worth stealing: gnx keeps
`heuristicLabel` *alongside* `label` and records `enrichedBy`, so the graph
degrades to heuristics when no model is available and a reader can always tell
which they got. Copy that verbatim.

### Amplify on four axes gnx structurally cannot follow

**1. Populate `cochanges` and cluster on evolutionary coupling.**
The table exists in GT's schema and is empty. Logical/evolutionary coupling —
files that change together across history — was established as a first-class
architectural signal by Gall, Hajek and Jazayeri (1998) and operationalised for
change guidance by Zimmermann, Weißgerber, Diehl and Zeller (*Mining Version
Histories to Guide Software Changes*, ICSE 2004 / TSE 2005), which showed
history-derived coupling surfaces dependencies static structure misses.

Critically, Beck and Diehl (*On the Congruence of Modularity and Code Coupling*,
FSE 2010) found structural and evolutionary coupling are **not congruent** —
which is exactly why using both is strictly more informative than either.
**gnx's store is a snapshot with no history at all**, so this is not a feature it
declined to build; its model cannot hold the signal.

**2. Cluster with Leiden and an explicit resolution parameter — not plain
modularity.**
Traag, Waltman and van Eck (*From Louvain to Leiden*, Scientific Reports 2019)
showed Louvain can produce internally **disconnected** communities and that
Leiden guarantees well-connected ones. Separately, Fortunato and Barthélemy
(PNAS 2007) proved modularity optimisation has a **resolution limit** that hides
small communities in large graphs — so the objective must expose a resolution
parameter (CPM) rather than optimise raw modularity. Software-specific module
clustering (Mancoridis et al., *Bunch*, IWPC 1998; Mitchell and Mancoridis on
MQ) is the prior art for applying this to code.

Amplification: cluster a **trust-weighted multigraph** — certified `CALLS` edges
plus co-change edges, weighted by tier. gnx clusters a confidence-flat
structural graph, so its communities silently inherit every resolution error the
resolver made.

**3. Make `cohesion` falsifiable rather than descriptive.**
gnx stores a structural cohesion score, which is a property of the partition,
not a claim about the world. GT should hold out the last *N* commits and measure
whether the community **predicted co-modification**, storing that measured rate
with a Wilson interval — the same evaluation discipline
[02-trust-calibration.md](02-trust-calibration.md) already sets for resolution
confidence. This is a metric gnx cannot compute at any effort, because it has no
history.

**4. Store the evidence set behind every label.**
A wrong label becomes traceable to the edges that produced it, rather than to an
opaque model call.

**Net:** gnx's community asserts *these things look related*. GT's would assert
*these things change together, over certified edges, and here is the measured
hit rate on held-out history.*

---

## C3. Row 14 — Caps, copied then amplified

### Copy exactly

gnx's cap ergonomics are well designed and should be taken as-is: a per-unit cap
and an aggregate cap, both named, with `0` meaning disabled
(`springAopMaxCandidateInspectionsPerAdvice` / `springAopMaxCandidateInspections`).

### Amplify on four axes

**1. Budget the layer that actually explodes.**
GT writes ≈6.9 fact nodes per callsite. boa's 68,227 callsites is roughly half a
million fact nodes before any candidate blow-up, which is why publication never
terminates and the WAL passes 11.5 GB. The budget belongs on facts-per-callsite
and total facts — not on an analysis phase.

**2. Exceeding budget must produce evidence, not silence — this is the anytime
contract.**
Zilberstein (*Using Anytime Algorithms in Intelligent Systems*, AI Magazine
1996) defines the requirement precisely: an interruptible algorithm must expose
a **performance profile** — a stated relationship between resources consumed and
answer quality — so a consumer knows what it is holding. A cap that merely stops
work, as gnx's does, violates this: the graph quietly contains less and nothing
records it.

GT already has the machinery to satisfy the contract. `resolutionDerivation`
returns an `abstentionReason`, and the vocabulary already includes
`candidate_only_flow_evidence` and `dynamic_target_not_statically_proven`. Over
budget should publish the callsite as `candidate_only` with
`abstention_reason = candidate_budget_exceeded`. **The cap becomes recorded
evidence.**

This is also the soundiness position: Livshits et al. (*In Defense of
Soundiness: A Manifesto*, CACM 2015) argue analyses should **declare** the
constructs they do not model rather than silently under-approximate. An
abstention is a declaration; a silent cap is exactly the unsoundness the
manifesto objects to. Reif et al. (*Judge*, ISSTA 2019) demonstrated how much
real-world call-graph unsoundness goes unrecorded in practice.

**3. Bound by demand, not by uniform truncation.**
The analysis literature's answer to cost is demand-driven and refinement-based
evaluation — Heintze and Tardieu (PLDI 2001) for demand-driven pointer analysis,
Sridharan and Bodík (PLDI 2006) for refinement-based points-to that returns a
result at a *stated precision* under a budget. Amplification: spend the fact
budget where the agent is actually looking (the localisation frontier), and
abstain at uniform low precision elsewhere — rather than truncating every
callsite equally.

**4. Seal the budget into the receipt.**
A run declares the budget it ran under, making two runs comparable and a
degraded graph provably degraded. gnx's caps are runtime options that leave no
trace. This also makes GT's atomic publication **survivable**: today it publishes
everything or nothing, which is why one runaway repository yields no graph at
all.

**Net:** gnx's cap prevents a blow-up. GT's would prevent the blow-up, record
exactly what was given up, and let the consumer price it.

---

## C4. Why the other thirteen rows are inversions, not copies

**Rows 2–4 — structure beats prose and beats raw text.**
Guo et al. (*GraphCodeBERT*, ICLR 2021) showed injecting **data-flow structure**
into pretraining improves code search and clone detection over token-only
models; CodeSearchNet (Husain et al. 2019) established the natural-language↔code
vocabulary gap that raw-token embedding must fight. Embedding a normalised
behavioural contract — params, return shape, guards, side effects, boundary
conditions — attacks that gap directly, and is invariant to renaming and
reformatting in a way raw-text chunks are not.

For retrieval composition, Thakur et al. (*BEIR*, NeurIPS 2021) showed dense
retrievers frequently underperform BM25 out of domain, and reciprocal rank
fusion (Cormack, Clarke and Büttcher, SIGIR 2009) is the standard, robust way to
combine lexical and dense rankings. GT should therefore run **both** over the
contract and fuse — not replace lexical with dense. `nodes_fts` already exists,
so the lexical half is nearly free.

On delivery format: Liu et al. (*Lost in the Middle*, TACL 2023) showed model
accuracy degrades sharply with position in long contexts, which is an argument
for compact structured facts over long prose summaries — and matches GT's
existing law that an over-budget delta is dropped whole rather than clipped.

**Row 7 — a process is a slice, and a slice is only credible if something
executes it.**
The formal object is an interprocedural slice: Weiser (1981) for slicing,
Ferrante, Ottenstein and Warren (*The Program Dependence Graph*, TOPLAS 1987)
for the dependence substrate. What makes GT's version stronger is the witness:
spectrum-based fault localisation (Jones and Harrold, *Tarantula*, ASE 2005;
Abreu, Zoeteweij and van Gemund on *Ochiai*, 2006–07) established that coverage
spectra from real test executions localise behaviour far better than static
structure alone, and test-to-code traceability (Van Rompaey and Demeyer, CSMR
2009) is the established technique for the link. GT already stores `assertions`
mapping tests to targets — gnx has no test↔code linkage at all, so its processes
can never be more than plausible.

**Row 9 — keeping candidate sets is keeping the over-approximation honest.**
GT's resolution ladder is the CHA → RTA → VTA lineage: Dean, Grove and Chambers
(*Class Hierarchy Analysis*, ECOOP 1995), Bacon and Sweeney (*Rapid Type
Analysis*, OOPSLA 1996), Sundaresan et al. (*Variable Type Analysis*, OOPSLA
2000). Each refines an over-approximate call set. gnx's storage model —
one row, one target, one confidence — forces a **collapse** of that set at write
time; GT retains all 10,820 candidates across 16,431 callsites with
selected/unselected marking, so two analyses that disagree remain
representable and the consumer chooses by tier. That is not a feature gnx can
retrofit without changing its schema.

**Row 13 — MRO is a solved algorithm.**
Python's method resolution order is C3 linearisation (Barrett et al., 1996). GT
already extracts inheritance chains; the pass is a linearisation over stored
parents, published with a trust tier so an ambiguous or inconsistent hierarchy
stays visibly ambiguous rather than silently ordered.

**Rows 1, 5 — invalidation granularity is the whole cost of incremental
analysis.**
Content-addressing is the Git object model applied to symbols, and incremental
analysis work (Arzt and Bodden, *Reviser*, ICSE 2014) shows correctness under
change depends on invalidating exactly what changed. Hashing bytes re-embeds on
whitespace; hashing the semantic fingerprint — which GT already computes for
1,407 symbols — re-embeds on behaviour change. Addressing rather than copying
also converts silent staleness into a detectable hash mismatch.

---

## C5. In GT, not in gnx

Recorded so the delta is not read as one-directional.

| GT capability | gnx equivalent |
|---|---|
| Per-candidate derivation provenance: mechanism, derivation kind, evidence set, dispatch state, trust tier, explicit resolution contract | `confidence DOUBLE` + `reason STRING` |
| Retained candidate sets with selected/unselected marking | Single resolved target |
| Four trust tiers | None |
| Atomic publication bound to a reproducible, digest-asserted build identity | None observed |
| `properties` fact layer — guards, boundary conditions, side effects, data flow, call order, field reads | No per-symbol fact table |
| `assertions` — test → target with expression and expected | None observed |
| Transitive closure sidecar | None observed |
| Receipts, attestation, replay, and enforcement that a built graph was *used* | None observed |
| **30 languages** | 10 grammars + COBOL + markdown |

## C6. Ordering

1. **Row 14 first.** It is the only item currently costing a whole repository,
   and the anytime contract makes GT's atomicity survivable rather than brittle.
2. **Rows 1, 2, 4** — content addressing, the structured contract, and hybrid
   retrieval over it. All three reuse machinery GT already ships (`file_hashes`,
   `properties`, `nodes_fts`).
3. **Rows 3, 5** — embed the contract; invalidate on the fingerprint.
4. **Row 6** — requires the co-change extractor to exist first, and is worth the
   most once it does, because it is the one signal gnx can never have.
5. **Row 7** onwards.

**Correction to an earlier plan item.** Raising closure `MaxDepth` from 3 to 6
widens traversal over a set where only 2.4% of edges are CERTIFIED. It makes the
closure larger without making it know more. Resolution rate (36%) and property
density (2.6 facts per symbol) are the levers — and items 1–4 raise what a
symbol is worth once reached.
