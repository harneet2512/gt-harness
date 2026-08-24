# GroundTruth Competitor Research

Status: Gate 13 research complete. This is a primary-source capability audit, not a head-to-head execution result and not paid-benchmark authorization.

Access date: `2026-08-23`

GroundTruth subject entering this gate: `3e2185d3f4ba0a228c740ab2a6d23a287cfc5380`.
Current implementation response: `8931876541ec82ec96799f6c4462b5c0726e4518`.

## Certified implementation response

The research identified composition, hybrid retrieval, process delivery, impact
formatting, live freshness, and compactness as the mechanisms most likely to affect
agent decisions. GT did not copy GitNexus's product surface. It retained the existing
exact-revision graph and added:

- a persistent repository-wide 768-dimensional Snowflake ONNX index;
- deterministic dense+sparse reciprocal-rank fusion;
- strict separation of exact edit targets from inspection candidates;
- exact persisted bounded process and impact projections;
- compact v4 evidence ledgers within 500/350 conservative tokens;
- same-observation Mini-SWE delivery after every relevant edit/diagnostic;
- dense, graph, delivery, and restart receipts tied to the actual source revision.

These mechanisms passed the final Linux product certificate. Communities, optional
PDG/def-use, broad framework extractors, and contract-aware cross-repository graphs
remain GitNexus advantages. Their agent value remains a benchmark hypothesis, not a
reason to add decorative features before the final smoke.

## Scope and evidence rules

This report inspects the current public implementation and official material for:

- GitNexus, the open-source engine currently built by Akon Labs.
- Akon Labs' managed/enterprise claims and published DeepSWE benchmark.
- Graphify, a separate Graphify-Labs product that Akon uses as a benchmark comparator.
- Sourcegraph's SCIP code-intelligence substrate and Cody context delivery.
- CodeGraphContext, a graph-oriented open MCP implementation.

No competitor was scored on repository questions in this gate. No model/provider was called, no paid benchmark was run, and no vendor benchmark result is used as a GT product fact.

Claim labels are strict:

- **VERIFIED IMPLEMENTATION FACT**: confirmed in current source or an open protocol schema at the pinned revision.
- **VENDOR CLAIM**: stated by an official site or official product documentation but not independently reproduced here.
- **INFERENCE**: a conclusion drawn from inspected implementation or documented behavior. It is not presented as a measured result.
- **UNKNOWN**: the required fact was not established from public primary material.

Inspected open-source revisions:

| System | Revision | Package/version evidence | Confidence |
| --- | --- | --- | --- |
| GitNexus | [`aac7515d2a8c50a1f8f923c6fb77218b333560d6`](https://github.com/abhigyanpatwari/GitNexus/tree/aac7515d2a8c50a1f8f923c6fb77218b333560d6) | `gitnexus` `1.6.9` | High |
| Graphify | [`b2cd36267456c166788c95be6e68574064a92a42`](https://github.com/Graphify-Labs/graphify/tree/b2cd36267456c166788c95be6e68574064a92a42) | `graphifyy` `0.9.48` | High |
| CodeGraphContext | [`39557ada8ea88dfe23ff54cef1df1bedfa542b9a`](https://github.com/CodeGraphContext/CodeGraphContext/tree/39557ada8ea88dfe23ff54cef1df1bedfa542b9a) | `codegraphcontext` `0.6.5` | High |
| SCIP | Current public schema as accessed | Language-neutral Protobuf protocol | High for schema, not Sourcegraph service behavior |

## Strong conclusions

1. **GitNexus is GT's closest current public competitor. Confidence: high.** It is not merely a graph visualization or vector-search wrapper. The pinned implementation has a multi-phase parsing and resolution pipeline, broad typed relationships, Leiden communities, explicit bounded execution-flow objects, hybrid retrieval, impact analysis, incremental persistence, MCP delivery, hooks, and real multi-repository contract bridging.

2. **Akon Labs and Graphify are not the same system. Confidence: high.** [Akon Labs](https://www.akonlabs.com/) says it builds GitNexus. [Graphify-Labs](https://github.com/Graphify-Labs/graphify) independently builds Graphify. Akon compares GitNexus against Graphify in its benchmark. Describing Graphify as Akon's graph engine would be factually wrong.

3. **GitNexus's marketing is stronger than its implementation warrants. Confidence: high.** Akon says relationships are “100% deterministic,” “compiler-grade exact,” and involve “0 guesses.” The source contains bounded traversal, confidence-scored edges, guarded fuzzy fallbacks, heuristic process labeling, and explicit truncation states. Those are reasonable engineering choices; they contradict a literal universal-exactness claim.

4. **GT already has a defensible integrity differentiator. Confidence: high within the inspected boundary.** GitNexus checks and reports staleness, but its MCP query boundary deliberately attaches staleness as a non-blocking warning. Commit-check failures fail open, and that check does not establish dirty-working-tree identity. Graphify and CodeGraphContext also do not prove exact source identity before every graph answer. GT's exact commit plus working-tree/source receipt and fail-closed query-readiness invariant is materially stronger if it remains enforced.

5. **GT entered the gate behind GitNexus on higher-order repository intelligence. Confidence: high as a feature gap; unknown as an accuracy gap.** GitNexus exposes first-class communities, processes, impact/change detection, optional PDG/data-flow queries, proactive agent hooks, and contract-aware cross-repository traversal. The certified response closes process, impact, hybrid-retrieval, and automatic-delivery gaps; communities, PDG, broad framework extraction, and cross-repository contracts remain open.

6. **Sourcegraph is the strongest inspected standard for compiler-derived symbol navigation and organization-scale scope. Confidence: high for SCIP; moderate for the proprietary product.** SCIP models precise occurrences, definitions, references, imports, implementations, type definitions, signatures, and documentation across language-specific indexers. It is not, by itself, a call/process/community graph.

7. **Graphify and CodeGraphContext are useful secondary comparators, not substitutes for GitNexus. Confidence: high.** Graphify is unusually broad across code plus documents/media and makes edge provenance visible. CodeGraphContext offers a rich property graph and MCP tool catalog. Neither inspected read boundary matches GT's exact-revision fail-closed invariant, and neither has GitNexus's first-class process abstraction.

8. **No public competitor evidence establishes comprehensive relationship precision/recall across claimed languages. Confidence: high.** Feature lists and benchmark solve rates are not graph-truth audits. Gate 14 must independently establish repository facts and score the information delivered to an agent.

## Capability comparison

The matrix describes public implementation or documentation, not a winner.

| Dimension | GitNexus 1.6.9 | Graphify 0.9.48 | Sourcegraph SCIP/Cody | CodeGraphContext 0.6.5 |
| --- | --- | --- | --- | --- |
| Indexing | **Verified:** Tree-sitter-centered pipeline plus language providers, cross-file/scope/MRO/framework phases; optional PDG | **Verified:** Tree-sitter AST for code; optional model-assisted documents/media | **Verified schema:** language-specific SCIP indexers emit compiler/semantic occurrences; **vendor:** Sourcegraph auto-indexing and search fallback | **Verified:** Tree-sitter with optional SCIP path; per-language extractors and graph resolution |
| Symbol model | **Verified:** typed code declarations, routes, tools, communities, processes, optional blocks | **Verified:** flexible node-link graph with code, document, config, package, and media concepts | **Verified:** stable global/local symbols, kinds, signatures, docs, occurrences and enclosing symbols | **Verified:** repository/directory/file plus code and framework/data-source node labels |
| Structural edges | **Verified:** definitions, calls, imports, inheritance, implementations, uses/accesses, ownership, framework edges | **Verified:** calls, imports, references, inheritance/implements/mixins, re-exports and flexible relations | **Verified:** reference/implementation/type-definition relations and occurrence roles; SCIP has no general `CALLS` edge kind | **Verified:** calls and heuristic calls, imports/includes, inheritance/implements, containment, parameters, framework/build/data-source edges |
| Process/execution analysis | **Verified:** explicit `Process` nodes and ordered `STEP_IN_PROCESS` edges; bounded heuristic traces | No equivalent first-class ordered Process abstraction found; graph paths/call-flow exports exist | Not present in the SCIP schema; no equivalent public Cody interface established | No first-class Process abstraction found; callers/callees and bounded call-chain traversal exist |
| Community detection | **Verified:** seeded Graphology Leiden default; optional experimental Icebug | **Verified:** seeded Leiden when installed, NetworkX Louvain fallback, deterministic community-ID ordering | Not in SCIP; no public Cody community abstraction established | No implementation found in the inspected revision |
| Hybrid retrieval | **Verified:** BM25 plus optional 384D Snowflake Arctic embeddings, fused with RRF; graph/process grouping | **Verified:** lexical/trigram scoring plus graph traversal; deliberately no vector store for code | **Vendor:** keyword search, Sourcegraph Search, and Code Graph are combined for Cody context | **Verified:** keyword/fuzzy search; optional local/remote embeddings disambiguate call targets, not a documented general hybrid query surface |
| Impact/change analysis | **Verified:** upstream/downstream impact, diff-to-symbol/flow detection, API impact, optional statement-level PDG queries | **Verified:** reverse-edge affected traversal and PR-impact/triage tools | **Vendor:** precise references across repos help assess impact; no dedicated public blast-radius contract established here | Transitive caller/callee traversal can approximate impact; no dedicated change-surface contract found |
| Repository scope | **Verified:** multiple registered repos plus group bridge graphs and one contract-boundary crossing | **Verified:** global graph aggregates prefixed repo graphs; external nodes may merge by label | **Vendor:** multi-repository code search and precise cross-repository navigation | **Verified:** multiple repository nodes/contexts can coexist; no verified contract-aware cross-repo flow layer |
| Persistence | **Verified:** LadybugDB, metadata, file hashes, parse cache, schema/analyzer identity, WAL/shadow and registry | **Verified:** `graph.json`, manifest, content caches, analysis and optional committed outputs | **Vendor:** uploaded SCIP indexes and search indexes retained by repo/commit policies | **Verified:** embedded or external property-graph backends and persistent context mapping |
| Incremental/lifecycle | **Verified:** file-hash diff, importer closure, crash dirty flag, write lock, rebuild escalation, branch slots; limitations below | **Verified:** changed/add/delete reconciliation, watcher and Git hooks, partial-build shrink guard | **Vendor:** CI/auto-indexing produces commit-scoped indexes; search and code-intel have different freshness behavior | **Verified:** watcher reparses changed files/neighbors and deletes renamed/deleted paths; SCIP path is full rebuild |
| Query-time freshness | **Verified weakness:** staleness is attached as a non-blocking result field; Git errors omit it; dirty tree is not part of the MCP commit check | **Verified weakness:** graph stamps `built_at_commit`, but MCP loads/serves the JSON without comparing it to current source | **Vendor:** precise index is selected by repo/commit where available, with search fallback; no GT-style graph receipt found | **Verified weakness:** no exact Git/source identity or readiness receipt found at the query boundary |
| Agent delivery | **Verified:** CLI, MCP tools/resources/prompts, generated skills, pre/post tool hooks and context files | **Verified:** CLI/skill, stdio or HTTP MCP, project instructions and commit hooks | **Vendor:** VS Code, JetBrains, Visual Studio and web; agentic context can call local MCP tools | **Verified:** CLI plus stdio MCP with 29 tool definitions in source |
| Token controls | **Verified:** result limits, depth/fan-out caps, pagination, compact process grouping | **Verified:** default 2,000-token query budget and scoped graph output | **Vendor:** configurable model/context limits; current docs expose large model-specific windows | No independently verified token-efficiency benchmark; relationship responses are structured |
| Latency/scale evidence | Engineering controls exist; independent real-repo latency not established here | Local benchmark scripts/claims exist; independent real-repo latency not established here | Organization-scale claims and architecture docs exist; not benchmarked here | Multiple backends and background jobs exist; independent real-repo latency not established here |
| Determinism | Structural pipeline is rule-based and clustering is seeded, but fuzzy/confidence/bounded analyses remain; agent use is model-dependent | Code-only AST path and clustering are designed for repeatability; docs/media semantic path is model-dependent | SCIP production is deterministic for fixed indexer/build inputs; Cody retrieval/reflection/final answer is model-dependent | Tree-sitter graph is largely deterministic; optional embeddings and backend behavior add variability; no exact build identity receipt |

## GitNexus and Akon Labs

### Product identity

**VERIFIED IMPLEMENTATION FACT — high confidence**

GitNexus is an open-source graph-intelligence engine published from [`abhigyanpatwari/GitNexus`](https://github.com/abhigyanpatwari/GitNexus). The [Akon Labs site](https://www.akonlabs.com/) states that Akon Labs builds GitNexus and offers managed or self-hosted enterprise use. Graphify is a comparator on Akon's benchmark page, not an Akon code name.

### Canonical indexing pipeline

**VERIFIED IMPLEMENTATION FACT — high confidence**

The current [architecture](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/ARCHITECTURE.md) and [pipeline registry](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/ingestion/pipeline-phases/registry.ts) define a real indexing DAG. Its phases cover scanning, structural nodes, parsing, Markdown and COBOL handling, routes and tools, cross-file and language-agnostic scope resolution, local-symbol pruning, inheritance/MRO, dependency injection, community detection, and process construction. Optional `--pdg` work adds CFG/control/data-flow and taint-related relationships.

The source registers language providers for JavaScript, TypeScript, Python, Java, C, C++, C#, Go, Ruby, Rust, PHP, Kotlin, Swift, Dart and Vue, with a separate COBOL extraction path. That is **implementation reachability**, not independently audited language support.

Cross-file resolution is materially more ambitious than a name-only Tree-sitter pass. The [scope-resolution architecture](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/ARCHITECTURE.md#scope-resolution) covers receiver/member chains, imports, inheritance, method dispatch, callable-value flow, property dispatch, and guarded fallbacks. It is also explicitly bounded. Examples include fan-out caps and confidence levels. Ambiguous or over-budget cases can abstain rather than emit partial call edges.

### Graph schema

**VERIFIED IMPLEMENTATION FACT — high confidence**

The [LadybugDB schema](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/lbug/schema.ts) persists separate tables for files/folders, common and language-specific declarations, communities, processes, routes, tools, sections and embeddings. Optional PDG indexing adds basic blocks.

The default relationship vocabulary includes:

`CONTAINS`, `DEFINES`, `CALLS`, `IMPORTS`, `INHERITS`, `EXTENDS`, `IMPLEMENTS`, `USES`, `DECORATES`, `HAS_METHOD`, `HAS_PROPERTY`, `ACCESSES`, `METHOD_OVERRIDES`, `METHOD_IMPLEMENTS`, `MEMBER_OF`, `STEP_IN_PROCESS`, `HANDLES_ROUTE`, `FETCHES`, `HANDLES_TOOL`, `ENTRY_POINT_OF`, `WRAPS`, `QUERIES`, `INJECTS`, `CONDITIONAL_ON`, `DECLARES`, `ADVISED_BY`, `BINDS_EVENT_HANDLER`, and `EMITS_EVENT`.

Optional PDG relationships include CFG, reaching definitions, control dependence, taint, sanitization and taint-path summaries. The optional status matters: a normal index does not imply program-dependence coverage.

### Communities and execution flows

**VERIFIED IMPLEMENTATION FACT — high confidence**

The [community processor](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/ingestion/community-processor.ts) projects the code graph and applies a seeded vendored Graphology Leiden implementation. It sorts inputs and uses a deterministic PRNG. The optional Icebug engine is experimental and may produce different community IDs; the code falls back if the native implementation lacks required seed/thread controls.

The [process processor](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/ingestion/process-processor.ts) creates explicit ordered flows by:

1. scoring candidate entry points,
2. traversing `CALLS`,
3. recognizing selected outward sinks such as fetch/ORM sites,
4. deduplicating paths,
5. ranking paths, and
6. persisting `Process` plus `STEP_IN_PROCESS` relationships.

This is useful but not complete execution semantics. The defaults cap depth at 10, branch expansion at 4, and require at least three steps. The implementation exposes separate counters for candidates never ranked, entry points never traced, depth caps, dropped callees, walk-budget exhaustion and process caps. Therefore an absent GitNexus flow is not evidence that the real code path is absent.

### Retrieval and impact

**VERIFIED IMPLEMENTATION FACT — high confidence**

GitNexus combines BM25 with optional 384-dimensional Snowflake Arctic embeddings and Reciprocal Rank Fusion. See [hybrid search](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/search/hybrid-search.ts) and [embedding implementation](https://github.com/abhigyanpatwari/GitNexus/tree/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/embeddings). It then groups results around processes and graph neighborhoods rather than returning only isolated text chunks.

The [MCP tool implementation](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/mcp/tools.ts) exposes symbol context, impact, working-tree change detection, API impact, trace/route/tool maps, graph shape checks, raw Cypher, and optional PDG queries in addition to repository search. The tools employ result budgets, depth/fan-out bounds, ambiguity handling, and truncation metadata. Impact errors can report unknown risk rather than turning a failed analysis into a false zero-risk answer.

### Persistence and lifecycle

**VERIFIED IMPLEMENTATION FACT — high confidence**

The [analysis path](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/run-analyze.ts) stores LadybugDB, metadata, file hashes, parse caches, schema/analyzer identity and WAL/shadow state under `.gitnexus/`. It uses a single-writer lock, writes an incremental-in-progress dirty flag before mutation, and can force recovery through a full rebuild after interruption. It detects a dirty working tree before treating a same-commit index as reusable.

Incremental analysis computes file changes and an importer closure, deletes/replaces affected subgraphs, and can escalate to a full rebuild. The repository includes an [incremental-versus-full equivalence test](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/test/unit/incremental-orchestration.test.ts), but that internal fixture is not independent real-repository proof.

Declared/observed limitations include:

- importer invalidation is bounded, with relationships beyond the supported closure treated best-effort;
- fully atomic incremental replacement is optional because it copies the database;
- cross-platform database replacement behavior differs;
- a matching commit is not a complete source-identity receipt by itself;
- internal equivalence tests are not a substitute for Gate 5 real-repository lifecycle evidence.

### Query-time freshness is non-blocking

**VERIFIED IMPLEMENTATION FACT — high confidence**

This is the most important integrity difference from certified GT.

[`checkStaleness`](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/git-staleness.ts) counts commits from the indexed `lastCommit` to `HEAD`. A Git failure returns `isStale: false`. The [local MCP backend](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/mcp/local/local-backend.ts#L1175-L1189) calls staleness a “non-blocking” signal, attaches it to otherwise valid tool results, and omits it if checking fails. This query-time path does not establish the dirty working tree's exact source identity.

GitNexus separately has a stronger CLI `status` check and post-commit stale hooks. Those reduce exposure; they do not change the MCP read invariant. Current source can still serve graph-derived results after commit drift with a warning, after uncommitted edits without a commit-drift warning, or after Git verification failure without a warning.

**INFERENCE — high confidence:** GT should not imitate this behavior. GT's competitive advantage is to keep graph evidence unavailable until exact commit, graph-input revision, builder identity, database integrity, allowed status, and query readiness all match.

### Multi-repository intelligence

**VERIFIED IMPLEMENTATION FACT — high confidence**

GitNexus has more than a global list of independent repositories. Its [group pipeline](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/group/PIPELINE.md), [bridge schema](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/group/bridge-schema.ts), and [cross-impact code](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/group/cross-impact.ts) build a bridge graph from contracts such as HTTP routes, messaging topics, gRPC surfaces and package/manifest dependencies.

Current traversal crosses at most one contract boundary. Full inter-program data flow is not implemented; PDG facts remain repository-local or are used near the boundary. Akon's “hundreds of repos as one graph” and universal cross-service blast-radius language remains broader than the publicly inspected implementation establishes.

### Agent delivery

**VERIFIED IMPLEMENTATION FACT — high confidence**

GitNexus exposes CLI and stdio MCP tools/resources/prompts, generates repository context and skill files, and installs pre/post tool hooks for selected agents. Its [current README](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/README.md) documents automatic enrichment of grep/glob/shell exploration and post-commit reindex prompts.

This is a meaningful design advantage over a passive tool catalog: the graph is more likely to reach the agent at the decision point. It also carries a regression risk—extra context and mandatory-seeming tool guidance can consume steps. Only controlled treatment-delivery measurements can determine the net effect.

### Akon DeepSWE publication

**VENDOR CLAIM — not independently verified**

The [Akon benchmark page](https://www.akonlabs.com/benchmarks) reports:

| Arm | Reported pass rate | Reported cost/trial | Reported output tokens | Reported steps |
| --- | ---: | ---: | ---: | ---: |
| GitNexus | 68.37% | $0.6008 | 21,077 | 44.94 |
| Graphify | 54.02% | $0.6364 | 21,769 | 48.00 |
| Bare | 36.99% | $0.6631 | 22,252 | 50.25 |

The page says the experiment used 113 tasks, three arms, ten trials per task per arm, and 3,471 total trials. Those numbers do not reconcile:

- `113 × 3 × 10 = 3,390`, not 3,471.
- The same page mentions a 116-entry manifest; `116 × 3 × 10 = 3,480`, also not 3,471.

Failed/omitted trials may explain the difference, but the public page does not say so. Raw trajectories were not publicly available from the inspected material. The page provides confidence intervals for some aggregate pass rates, but the public evidence is insufficient to independently verify task selection, treatment delivery, exclusions, paired outcomes, model/scaffold equality, token accounting or cost.

The benchmark also calls its Graphify treatment “code extraction only.” That may be a deliberately bounded comparator, but it is not automatically equivalent to Graphify's complete current product boundary.

**Conclusion:** the figures are a serious causal claim worth reproducing, not certified competitor performance and not a basis for `AUTHORIZED` by themselves.

## Graphify

### Identity and architecture

**VERIFIED IMPLEMENTATION FACT — high confidence**

Graphify is maintained by Graphify-Labs, separately from Akon. Its [architecture](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/ARCHITECTURE.md) is a local Python/NetworkX pipeline:

`detect → extract → build → cluster → analyze → report/export`

Code uses Tree-sitter AST extraction. Documents, PDFs, images and media may use an optional model-assisted semantic pass. The graph is serialized as `graphify-out/graph.json`, and can also produce HTML, Markdown/wiki and interchange exports.

Its flexible node/edge model is broader than a code-symbol database. Edges store relation, source, target, source evidence and a confidence class: `EXTRACTED`, `INFERRED`, or `AMBIGUOUS`. See [graph construction](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/build.py) and [extraction](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/extract.py).

Graphify's official material uses changing headline language counts. The current README says 37 Tree-sitter grammars plus additional/fallback extractors, while older architecture material describes narrower coverage. This report treats reachability as verified and language quality as unverified until a same-gate language matrix is run.

### Communities and retrieval

**VERIFIED IMPLEMENTATION FACT — high confidence**

The [clusterer](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/cluster.py) sorts graph inputs, uses fixed seeds, prefers Leiden through `graspologic`, and falls back to NetworkX Louvain. It splits oversized/low-cohesion communities and orders final memberships deterministically before assigning community IDs.

The [MCP server](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/serve.py) performs lexical/trigram scoring plus bounded graph expansion. It exposes graph queries, node/neighborhood lookup, communities/statistics, shortest paths, structural-hub analysis and PR-impact/triage tools. The current default context budget is 2,000 tokens.

Graphify deliberately does not use a code vector store. Its optional semantic document/media extraction can add model-generated concepts and inferred edges, but that is not the same as hybrid query-time retrieval.

No first-class `Process` node plus ordered execution-step abstraction equivalent to GitNexus was found. Graph paths and call-flow export can answer some execution questions, but the agent must compose them from general graph relationships.

### Persistence, incremental update and freshness

**VERIFIED IMPLEMENTATION FACT — high confidence**

Graphify persists `graph.json`, a relative-path manifest, content caches and analysis artifacts. Incremental mode reconciles added, changed, deleted and newly excluded files. Watch mode rebuilds code changes locally and marks surviving non-code changes as needing semantic refresh. A post-commit/post-checkout hook can keep artifacts current. The graph writer has partial-build/shrink guards designed to avoid overwriting a larger good graph with an incomplete extraction.

The writer stamps `built_at_commit`; see [export](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/export.py#L405-L407). The [MCP loader](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/serve.py) verifies JSON readability/file limits and reloads when the graph file's size or modification time changes. It does not compare `built_at_commit` or current source hashes before serving queries.

**INFERENCE — high confidence:** a stale but readable Graphify graph can therefore appear healthy to an MCP client unless an external watch/hook/update discipline has refreshed it.

The [global graph](https://github.com/Graphify-Labs/graphify/blob/b2cd36267456c166788c95be6e68574064a92a42/graphify/global_graph.py) prefixes repository-local IDs and aggregates project graphs, deduplicating selected external nodes by label. That is verified graph aggregation. It is not verified contract-aware cross-repository call/data-flow resolution comparable to GitNexus's bridge graph.

### Determinism boundary

**VERIFIED IMPLEMENTATION FACT — high confidence**

Code-only extraction and clustering deliberately sort inputs and fix random seeds. Optional model-assisted document/media extraction, semantic deduplication and LLM community naming are not deterministic in the same sense. Graphify does the right thing by tagging inferred relationships, but an `INFERRED` label is provenance—not proof of accuracy.

## Sourcegraph SCIP and Cody

### Precise code-intelligence substrate

**VERIFIED IMPLEMENTATION FACT — high confidence**

[SCIP](https://github.com/scip-code/scip) is a language-neutral Protobuf protocol for code intelligence. The current [schema](https://github.com/scip-code/scip/blob/main/scip.proto) models documents, typed source ranges, occurrences, stable local/global symbols, symbol kinds, signatures, documentation, diagnostics and relationships.

Occurrence roles include definition, import, read/write, generated code and tests. Symbol relationships represent references, implementations, type definitions and definition redirection. SCIP deliberately lets language-specific indexers supply semantic/compiler information. Official repositories list indexers for Go, TypeScript/JavaScript, JVM languages, C/C++, Rust, Python, Ruby, .NET, Dart and PHP.

SCIP itself does not define a generic `CALLS` relation, execution-flow object, community, change-impact score or agent delivery policy. A consumer may derive additional intelligence, but it is not in the protocol contract.

### Sourcegraph product behavior

**VENDOR CLAIM — moderate confidence**

Sourcegraph says [Precise Code Navigation](https://sourcegraph.com/docs/code-navigation/precise-code-navigation) uploads or auto-generates SCIP indexes for repository commits and provides definitions, references and implementations. [Search-based navigation](https://sourcegraph.com/docs/code-navigation) is a syntax/search heuristic fallback when a precise index is unavailable. Sourcegraph documents cross-repository/dependency navigation and repository/commit selection.

[Auto-indexing](https://sourcegraph.com/docs/code-navigation/auto-indexing) clones candidate repository commits into executor sandboxes, runs language-specific indexers, uploads indexes and exposes job state/logs. Policies control branches/tags, commit age and retention. This is stronger organization-scale indexing machinery than a local single-repository watcher, but the service behavior was not independently deployed in this gate.

Sourcegraph says [Cody context](https://sourcegraph.com/docs/cody/core-concepts/context) combines keyword search, Sourcegraph Search and Code Graph relationships, and supports multi-repository context in its editor/web clients. [Agentic context fetching](https://sourcegraph.com/docs/cody/capabilities/agentic-context-fetching) uses model reflection and tools such as code search, file reads, terminal, web, OpenCtx and local MCP. Sourcegraph's MCP support is a way for Cody to consume external local MCP tools; this audit did not find a public Sourcegraph code-graph MCP server equivalent to GT or GitNexus.

Current [token-limit documentation](https://sourcegraph.com/docs/cody/core-concepts/token-limits) exposes model-specific context/output caps and explicitly discusses the accuracy, latency and cost trade-off. It does not provide a competitor-neutral repository-intelligence latency or token benchmark.

### Determinism and graph scope

**INFERENCE — high confidence**

For a pinned build and indexer, SCIP occurrence/reference data can be deterministic and compiler-derived. Cody as a complete product is not deterministic: precise SCIP can be missing, search-based fallback is heuristic, retrieval can be agentic/model-directed, and the final response is provider/model generated.

Sourcegraph's most credible competitive advantage is precise cross-repository symbol navigation at organization scale. The inspected public contract does not establish GitNexus-like execution processes, Leiden architecture communities, MCP-native impact receipts, or a model-agnostic deterministic repository harness.

## CodeGraphContext

### Graph implementation

**VERIFIED IMPLEMENTATION FACT — high confidence**

CodeGraphContext parses with Tree-sitter and has an optional SCIP route, writes a property graph, and supports embedded or external backends. Its current [schema contract](https://github.com/CodeGraphContext/CodeGraphContext/blob/39557ada8ea88dfe23ff54cef1df1bedfa542b9a/src/codegraphcontext/tools/indexing/schema_contract.py) defines repository/directory/file and common code nodes plus framework/build/data-source nodes.

Relationship types include `CONTAINS`, `CALLS`, `HEURISTIC_CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`, `HAS_PARAMETER`, `INCLUDES`, decoration/mixin/embedding relations, Spring DI/routes, build-module dependencies and data-source reads/writes/mappings. Separating `HEURISTIC_CALLS` from stronger calls is a useful truth signal.

The current source has 29 MCP tool definitions in [`tool_definitions.py`](https://github.com/CodeGraphContext/CodeGraphContext/blob/39557ada8ea88dfe23ff54cef1df1bedfa542b9a/src/codegraphcontext/tool_definitions.py). They include indexing/job management, keyword/fuzzy search, callers/callees/transitive traversal, call chain, hierarchy/overrides, dead-code and complexity queries, raw read-only Cypher, contexts/graphs, reporting, Java/Spring and data-source analysis. The published MCP reference still describes version 0.4.16 and 25 tools, so current source—not that stale document—is authoritative for this report.

### Retrieval, lifecycle and limitations

**VERIFIED IMPLEMENTATION FACT — high confidence**

The [embedding path](https://github.com/CodeGraphContext/CodeGraphContext/blob/39557ada8ea88dfe23ff54cef1df1bedfa542b9a/src/codegraphcontext/tools/indexing/embeddings.py) supports OpenAI, Sentence Transformers or FastEmbed and stores vectors on Function nodes. The [vector resolver](https://github.com/CodeGraphContext/CodeGraphContext/blob/39557ada8ea88dfe23ff54cef1df1bedfa542b9a/src/codegraphcontext/tools/indexing/vector_resolver.py) uses them to disambiguate call targets. The public search tool remains keyword/fuzzy search; this is not a verified general BM25/vector/graph fusion surface.

The [watcher](https://github.com/CodeGraphContext/CodeGraphContext/blob/39557ada8ea88dfe23ff54cef1df1bedfa542b9a/src/codegraphcontext/core/watcher.py) serializes updates, reparses the changed file plus selected caller/inheritance neighbors, clears outgoing stale relationships, and handles create/modify/delete/move events. Startup sync removes paths no longer on disk. The optional SCIP path is full re-index rather than incremental.

No commit/source-revision field or GT-style readiness receipt was found in the active graph schema or MCP load/query boundary. Repository identity is path-based. No community-detection implementation or first-class ordered process abstraction was found in the inspected revision.

**INFERENCE — high confidence:** CodeGraphContext offers broader immediately queryable graph types than a minimal symbol index, but its public implementation gives weaker evidence that a readable graph is the exact healthy graph for the current checkout.

## What GT should learn without copying

These are causal hypotheses for Gate 15, not authorization to change GT before Gate 14 establishes a real deficit.

| Missing or weaker capability | Repository fact not directly available today | Agent decision affected | Deterministic mechanism to evaluate | Main risk | Required test |
| --- | --- | --- | --- | --- | --- |
| Execution processes | Bounded ordered paths from real entry points to meaningful sinks | Which path/files to inspect before editing | Rank source-evidenced call paths; emit explicit caps and incompleteness counters | Heuristic flows presented as complete | Relation-level path truth plus task-level ablation |
| Communities | Functionally coupled symbol clusters independent of folders | Where a change belongs and which subsystem context matters | Seeded Leiden over selected high-confidence edges; stable content-derived IDs | Clusters look meaningful but do not help decisions | Stability, modularity, manual labels, agent ablation |
| Hybrid ranking | Relevant symbol can be lexically distant from issue text | Which graph anchor to start from | Deterministic RRF over lexical, graph and optional pinned local embedding scores | Semantic false positives and provisioning cost | Blind ranking truth set, p50/p95/token budget |
| Dedicated impact receipt | Typed, depth-bounded blast radius with confidence and truncation | Whether an edit is safe and what to test | Traverse only evidence-qualified reverse edges and report omitted/unsupported relations | Transitive overreach; noisy agent steps | Precision/recall by edge type and negative-flip A/B |
| Diff-to-graph mapping | Exact symbols/flows touched by staged and unstaged hunks | Verification after editing | Map diff ranges to receipt-bound symbols, then recompute affected graph | Dirty-tree identity mismatch | Add/modify/delete/rename/worktree lifecycle |
| Proactive delivery | Relevant graph facts available without spending many discovery turns | When the agent asks and whether it consumes the evidence | One bounded pre-decision context packet with delivery/consumption receipt | Repeats historical GT step/token regression | Bare/GT paired delivery and negative-flip accounting |
| Cross-repo contracts | Consumer/provider edges for routes, RPC, events and packages | What breaks in another service/repository | Explicit contract nodes keyed by protocol identifiers and version; no label-only merge | False cross-repo links can be catastrophic | Independently auditable multi-repo fixtures and real systems |
| Optional PDG/data flow | Control/data conditions behind a value or sink | Where data originates and which guard matters | Language-native CFG/def-use where verified; explicit unsupported state elsewhere | Cost and false precision | Compiler/AST oracle by language, scale and ablation |

GT should preserve and extend its stronger substrate rather than replacing it:

- exact repository, commit and dirty-source identity;
- atomic publication and recovery;
- fail-closed readiness at the query boundary;
- graph receipts attributable to every production and benchmark treatment;
- source-evidenced edges and explicit limitations;
- independent graph-truth and lifecycle campaigns;
- model-agnostic graph construction and retrieval.

The competitive goal is not “have a Leiden phase because GitNexus has one.” It is:

`new repository fact → better agent decision → measured solve/efficiency effect`

If that causal chain is not demonstrated, the feature is weight, not an advantage.

## Gate 14 requirements established by this research

Gate 13 is complete. The bounded provider-free execution described by these requirements was subsequently performed and is reported in `GT_COMPETITIVE_INTELLIGENCE_AUDIT.md`; it must not be confused with the paid agent experiment.

The direct repository-intelligence audit must now:

1. pin exact comparator versions/revisions, including all build-mode flags;
2. use identical frozen repositories and checkouts;
3. record each comparator's repository identity, build status, limitations, files/symbols/edges and query availability;
4. construct a blind fact set independently of every system;
5. score definitions, references, callers/callees, imports/re-exports, implementations/inheritance, paths, impact, communities and relevant-file ranking separately;
6. record ambiguity, abstention, truncation and unsupported states instead of forcing an answer;
7. measure build cost, query p50/p95 and tokens delivered—not feature names;
8. verify Graphify's full treatment rather than accepting Akon's “code extraction only” label;
9. verify GitNexus treatment delivery and staleness behavior through its actual CLI/MCP boundary; and
10. keep Sourcegraph/Cody claims separate unless an equivalent executable product treatment is available.

This document alone establishes `COMPETITOR_CAPABILITIES_RESEARCHED`, not competitive validation. Any later claim depends on the separate execution receipt and must remain bounded to its repositories, questions, and comparator modes.

Confidence: **high** for facts directly inspected at the pinned open-source revisions; **moderate** for current official Sourcegraph product behavior; **low/unknown** for vendor benchmark generalization and any unexecuted accuracy, latency, cost or agent-uplift comparison.
