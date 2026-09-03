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
is empty in every graph built; `cochange_partner` has no emitter anywhere in the
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

23 node tables: File, Folder, Function, Class, Interface, Method, CodeElement,
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

# Part C — The delta, itemized

Each line is a *capability*, not a schema to copy — consistent with
00-comparison's rule that the goal is a GT-native version, not imitation.

## C1. Not in GT

| # | gnx capability | GT today | Why it matters |
|---|---|---|---|
| 1 | **`content` on the node** — the symbol's source text | Not stored; GT keeps `file_path` + line range | GT cannot answer "show me this symbol" from the graph; every delivery needs a re-read |
| 2 | **`description` on the node** — generated summary, with `enrichedBy` provenance and a heuristic fallback | None | No natural-language handle for retrieval, ranking or delivery |
| 3 | **Chunked per-node embeddings + HNSW index** — `CodeEmbedding(nodeId, chunkIndex, startLine, endLine, embedding[384], contentHash)` | ONNX arctic-embed-m exists, but no per-node chunk table bound into the graph | The semantic neighbourhood of a *symbol* is not queryable |
| 4 | **Full-text index over content and description** | No FTS over source text in the graph | No lexical fallback when structure fails |
| 5 | **Content-hash staleness for embeddings** | `file_hashes` is file-level only | Re-embedding is coarser than it needs to be |
| 6 | **Community nodes** — keywords, description, `cohesion DOUBLE` | None | No mid-level grouping between file and repository |
| 7 | **Process nodes** — `processType`, `communities[]`, `entryPointId`, `terminalId`, `STEP_IN_PROCESS` | None | No end-to-end flow object to hand an agent |
| 8 | **23 node types** incl. Struct, Enum, Trait, Impl, TypeAlias, Namespace, Macro, Typedef, Union, Const, Static, Variable, Property, Section | 4 measured: Function, Class, Method, File | Rust/C++/Go type structure is invisible in GT's node layer despite GT parsing those languages |
| 9 | **30 relation types** incl. ACCESSES, HAS_PROPERTY, METHOD_OVERRIDES, METHOD_IMPLEMENTS, MEMBER_OF, HANDLES_ROUTE, FETCHES, HANDLES_TOOL, ENTRY_POINT_OF, WRAPS, QUERIES, INJECTS, CONDITIONAL_ON, DECLARES, ADVISED_BY | 4 measured semantic types: CALLS, IMPORTS, CONTAINS, IMPLEMENTS | Framework, DI and data-access structure is not represented |
| 10 | **`reason` on every relation** | Machine provenance only | GT's provenance is richer but not presentable to a model as-is |
| 11 | **`step` on every relation** — which pass produced it | `mechanism` is per-candidate, not per-edge | Harder to attribute an edge to a pass |
| 12 | **Overload narrowing** as a resolution pass | Absent | Overloaded APIs resolve worse |
| 13 | **MRO as a first-class pass** | `inherited` rung only | Multiple inheritance resolves worse |
| 14 | **Explicit unresolved-receiver modelling** (`unresolved-receivers.ts`, `undecided-satisfaction.ts`) | `UnresolvedFact` nodes exist (15,089 in arktype) — **partial parity**, and arguably GT's is stronger | — |
| 15 | **Bounded expensive passes** (explicit inspection/edge caps) | No cap on publication volume | This is the direct cause of the boa blow-up |
| 16 | **Separable analysis layer** (`skipGraphPhases`) | Publication is all-or-nothing | Cannot trade analysis depth for time |
| 17 | **Per-framework depth** — four dedicated Spring phases | One generic framework overlay | Spring-heavy Java repos are under-represented |

## C2. In GT, not in gnx

Recorded so the delta is not read as one-directional.

| GT capability | gnx equivalent |
|---|---|
| Per-candidate derivation provenance: mechanism, derivation kind, evidence set, dispatch state, candidate sets, trust tier, explicit resolution contract | `confidence DOUBLE` + `reason STRING` |
| Candidate *sets* retained per callsite (10,820 candidates over 16,431 callsites) with selected/unselected marking | Single resolved target |
| Trust tiers (CERTIFIED / CANDIDATE / SPECULATIVE / STRUCTURAL) | None |
| Atomic publication bound to a reproducible build identity, digest-asserted in CI | None observed |
| `properties` layer — `guard_clause`, `boundary_condition`, `side_effect`, `data_flow`, `call_order`, `field_read`, `param`, `return_shape` | No per-symbol fact table |
| `assertions` — test → target with expression and expected | None observed |
| Transitive closure sidecar | None observed |
| Receipts, attestation, replay, and enforcement that a built graph was *used* | None observed |
| **30 languages** | 10 grammars + COBOL + markdown |

## C3. Ordering, and one correction

**Cheapest with the largest effect — items 1, 2, 4.** `content` and `description`
are per-symbol writes on nodes GT already creates, and an FTS index over them is
additive. Together they would let GT deliver a symbol without re-reading the
file and give it a lexical fallback it currently lacks.

**Next, item 3** — the chunked embedding table. GT already ships the ONNX model;
what is missing is binding vectors to node ids with line ranges and a content
hash.

**Item 15 is not optional.** gnx caps its expensive passes explicitly. GT's
publication is uncapped, and that is the mechanism behind the one repository in
the smoke set that cannot be indexed at all.

**Items 6–7 (Community/Process) depend on a clustering pass GT does not have**,
and gnx's own enrichment is LLM-dependent with a heuristic fallback — so the
GT-native version should store `enrichedBy`-style provenance from day one rather
than presenting heuristic labels as semantic ones.

**Correction to an earlier plan item.** Raising closure `MaxDepth` from 3 to 6
widens traversal over a set where only 2.4% of edges are CERTIFIED. It makes the
closure larger without making it know more. Resolution rate (36%) and property
density (2.6 facts per symbol) are the levers that move quality — and items 1–4
above raise what a symbol *is worth* once reached.
