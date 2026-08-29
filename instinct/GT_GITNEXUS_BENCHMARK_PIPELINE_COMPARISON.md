# Instinct head-to-head: GroundTruth and GitNexus benchmark pipelines

**Status:** source-audited comparison and benchmark contract

**GroundTruth runtime:** [`gt-harness@249cfc1`](https://github.com/harneet2512/gt-harness/tree/249cfc1efc23341e403eaa10cf734271f8b7f47f)

**GroundTruth graph source:** [`groundtruth@04c3da7`](https://github.com/harneet2512/groundtruth/tree/04c3da7e55cc9f776d492aeee396682c52f84f08)

**GitNexus source audited:** [`GitNexus@b059ab3`](https://github.com/abhigyanpatwari/GitNexus/tree/b059ab3541ea68c2ce292955fc367a5de04b39ea), 2026-08-28 main

**GitNexus latest stable at audit time:** [`v1.6.10`](https://github.com/abhigyanpatwari/GitNexus/releases/tag/v1.6.10), source commit [`6088d2e`](https://github.com/abhigyanpatwari/GitNexus/tree/6088d2e309de134688cb465fc76988ce801e06c6)
**Confidence:** high for source-level facts; unknown for an Instinct implementation because no Instinct code or prior `instinct/` directory exists in the repository

**Licensing precision:** GitNexus publishes its source, but its audited license is [PolyForm Noncommercial 1.0.0](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/LICENSE), not an OSI-approved open-source license. This report therefore calls it source-available. Source visibility is sufficient for this technical audit; copying code into a commercial product is a separate licensing question.

## Executive judgment

There is no defensible single winner yet because the existing evidence compares different objects.

- **GitNexus currently has the deeper product graph.** Its current ingestion DAG has 19 default phases and 21 with `--pdg`; it models framework routes, MCP/tool handlers, ORM queries, dependency injection, AOP, method resolution order, communities, inferred processes, cross-repository contracts, and optional CFG/PDG/taint layers. GroundTruth does not yet match all of that semantic depth.
- **GroundTruth has the broader declared language surface and the stronger benchmark control plane.** Its registry distinguishes 60 validation-relevant language capabilities, 50 structurally indexed capabilities, 46 symbol-capable capabilities, and 30 caller-capable capabilities. Its active runtime binds graph revision, certified evidence, exact provider-visible bytes, decision boundary, feature lifecycle, action uptake, validation, and terminal run accounting.
- **GitNexus's current SWE-bench treatment is much narrower than GitNexus the product.** The harness exposes five measured wrappers—query, context, impact, cypher, and overview—plus an augmentation wrapper. It disables embeddings in both native modes, so that evaluation does not test the product's semantic-vector/RRF path. Many product tools and graph layers are not exercised.
- **GroundTruth's benchmark treatment is more automatic and more auditable, but it carries more orchestration risk.** GT injects selected evidence into the model's existing observation at decision boundaries; GitNexus mostly relies on the model deciding to call a graph wrapper, with grep augmentation as the automatic path. GT can prove exposure and uptake more precisely, but a defect in its controller, freshness barrier, compiler, or receipts can contaminate every task.
- **GitNexus's general SWE-bench environment is not fail-closed.** Its setup path logs a GitNexus failure and continues without it. Unless the final result separately proves graph readiness, a nominal treatment row can become an unlabelled baseline-like row. GT's active release requires graph readiness and mechanical release gates.
- **GitNexus's newer workflow benchmark is substantially more rigorous than its older SWE-bench harness.** It uses fresh worktrees, hidden behavioral oracles, immutable graph assets, parent-captured event streams, paired arms, containment, and deterministic promotion rules. GT should copy this separation of authored tests from harness-owned hidden oracles and its explicit candidate-versus-incumbent promotion discipline.
- **Neither project has published evidence that establishes universal superiority.** GT has measured retrieval and matched-smoke results plus a frozen descriptive baseline; GitNexus ships evaluation machinery and a small historical workflow calibration, but the audited repository does not contain a completed, controlled broad SWE-bench result that proves resolve-rate superiority.

The correct objective for Instinct is therefore not to be “another graph.” It is to combine GitNexus's semantic-program graph depth with GT's revision-certified, decision-boundary delivery and causal receipts, then evaluate all three through one common, fail-closed harness.

## 1. Comparison boundary

Three layers must never be collapsed into one claim:

1. **Product capability:** what the repository graph and tools can compute.
2. **Treatment capability:** what the benchmark actually makes available to the model.
3. **Measured evidence:** what completed, comparable runs actually prove.

A feature counts in the product column when source code implements it. It counts in the treatment column only when the benchmark enables it and records its availability. It counts as a demonstrated benefit only when a comparable run connects exposure to behavior and verified outcome.

This distinction matters most for GitNexus. Its product has a broad MCP surface and optional PDG, but its SWE-bench treatment exposes a small wrapper subset and runs with embeddings disabled. Conversely, GT's active treatment exercises the integrated evidence pipeline, but the presence of receipts proves execution and exposure—not solve-rate causality.

## 2. End-to-end pipeline, side by side

| Stage | GroundTruth active Mini-SWE treatment | GitNexus product | GitNexus SWE-bench treatment | Instinct requirement |
|---|---|---|---|---|
| Task materialization | Harbor/Pier task and verifier lifecycle, task profile and frozen release identity | Not a benchmark concern | SWE-bench Docker image at task commit | One immutable task manifest shared by every arm |
| Repository discovery | Workspace scan plus graph-input classification and source-revision receipts | Filesystem glob with layered ignore rules and deterministic sorting | Runs `gitnexus analyze` inside `/testbed`, optionally restores commit-keyed cache | Record every included, excluded, failed, and oversized path with reason |
| Language selection | One registry separates validation relevance, structural indexing, symbol support, and caller support | 16 compile-time-registered language providers feed one schema | Whatever `gitnexus analyze` supports; no per-task capability receipt | Declare per-task semantic coverage, not a binary “index ready” flag |
| Graph build | Vendored `gt-index`, SQLite graph and supporting tables, hybrid repository adapters | 19-phase DAG; LadybugDB; 21 phases with PDG summaries | Index with `--skip-embeddings`; no PDG in the standard configs inspected | Build identical source snapshot; pin graph mode; attest binary and config |
| Planning | Graph and hybrid retrieval complete before one bounded generative bootstrap selection | `gitnexus-plan` skill is a separate workflow surface | Standard SWE-bench Mini-SWE prompt; explicit wrappers and optional augmentation | Planning call occurs only after a certified graph snapshot and capability envelope exist |
| Retrieval | Exact paths, lexical/BM25, local dense retrieval, graph relationships, roles, evidence certification | BM25 + semantic search merged with RRF; Cypher and structured tools | Semantic path disabled; explicit wrappers call eval server or CLI fallback | Run lexical, semantic, graph, and framework retrievers independently; fuse certified subsets only |
| Delivery | Automatic, bounded, same-observation injection at decision boundaries | Model invokes MCP/CLI tools; generated skills guide use; editor hooks can add context before search and warn after mutation | Five wrappers; augmentation appends graph context after grep/rg/ag | Support both explicit tools and automatic boundary delivery; receipt both |
| Freshness after edits | Source revision and graph lease; stale claims suppressed; graph refreshed at graph-dependent boundaries | Freshness metadata and incremental writeback mechanisms; MCP surfaces stale hints | Standard harness indexes before agent and does not demonstrate per-edit reindex in the inspected path | One revision identity from task snapshot through graph, delivery, edit, refresh, and verification |
| Tool execution | Mini-SWE Bash tool and GT host controller | MCP stdio, HTTP bridge, CLI | Bash wrappers → loopback HTTP eval server → CLI fallback | Every tool call records requested capability, backend used, latency, output hash, truncation, and error |
| Verification | Command classification, affected-test guidance, Harbor/Pier verifier, terminal receipt | `detect_changes`, impact and review/workflow skills support validation | Official SWE-bench evaluation optional; patch and model stats collected | Authored tests are advisory; harness-owned hidden oracle decides resolve |
| Accounting | `gt.run_receipt.v2`, lifecycle, provider delivery, graph, tokens, actions, terminal classification | Operational logs and index metadata; no equivalent end-to-end causal run receipt found | Patch/cost/API/tool/augmentation metrics | One mandatory terminal receipt for every selected task, including startup and finalization failures |
| Promotion | Frozen release manifest, treatment hash, baseline hash, prediction and mechanical gates | Workflow benchmark has paired candidate promotion rules | General SWE-bench analyzer compares modes descriptively | Quality-first paired promotion with task-clustered uncertainty and no missing-row arithmetic |

## 3. Graph breadth: what code is represented

### 3.1 Language breadth

GroundTruth's runtime registry currently contains 60 named capabilities. That number must not be reported as “60 equally deep language graphs.” The registry itself explicitly separates four levels:

- validation-relevant source;
- structural indexing;
- symbol extraction;
- caller extraction.

At the audited commit, the registry reports 50 structural, 46 symbol-capable, and 30 caller-capable entries. Several formats use bounded adapters rather than Tree-sitter; several grammars provide symbols but no certified callers. This honesty is a strength: the benchmark can know that a file affects the task revision without falsely claiming a call graph for it. The authoritative declaration is [`gt_engine/language_registry.py`](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/language_registry.py).

GitNexus documents 16 language providers feeding a single unified schema. Registration is compile-time checked, and the provider contract covers extensions, imports, type behavior, exports, scope resolution and language-specific hooks. This is a smaller language surface but, for the supported mainstream languages, often a deeper semantic surface. See the [language-agnostic graph section](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md#language-agnostic-graph-feeding).

**Judgment:** GT wins raw breadth and capability honesty. GitNexus wins average semantic depth over its primary supported languages. “Number of languages” alone is a bad benchmark metric.

### 3.2 Repository and structural layer

GroundTruth persists nodes and edges in SQLite with repositories, file hashes, project metadata, normalized edge metadata, properties, assertions, co-change data, transitive closure, content passages and FTS surfaces. The schema is visible in [`internal/store/sqlite.go`](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/vendor/gt-index-src/internal/store/sqlite.go), while logical revision construction is in [`internal/store/revision.go`](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/vendor/gt-index-src/internal/store/revision.go). GT's differentiator is not merely schema richness; it is the use of trust tier, resolution method, candidate count, evidence type, verification status and source revision to decide whether an edge is authoritative enough for a model-visible claim.

GitNexus starts with File/Folder containment, parses symbols and relations, resolves across files and scopes, prunes invalid local symbols, then adds higher semantic layers. Its DAG is explicit and dependency-validated. The current phase registry is [`pipeline-phases/registry.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/ingestion/pipeline-phases/registry.ts); the phase semantics are documented in [`ARCHITECTURE.md`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md#ingestion-pipeline).

**Judgment:** both have a real graph rather than a glorified symbol index. GitNexus has the clearer staged semantic enrichment DAG. GT has the stronger epistemic metadata and revision certification around graph facts.

### 3.3 Call, type and receiver resolution

GitNexus is ahead here. Its scope-resolution layer owns calls, accesses, uses and inheritance; language providers customize imports and type behavior without forking the entire pipeline. Current source documents generic-aware interface dispatch, method ownership reconciliation, receiver-chain encoding, MRO, overrides/implements, and dependency injection. These are exactly the relations that turn “a function with this name exists” into “this is the implementation reached from this receiver in this call context.”

GroundTruth stores resolution method, trust tier, candidate count, receiver metadata and evidence status. It can abstain from ambiguous or heuristic links and prevent them from becoming edit instructions. Its weakness is that certification can only be as good as the upstream resolver: a conservative shallow graph is safer than an invented edge, but it can still miss the correct owner.

**Judgment:** GitNexus wins resolution depth. GT wins explicit authority handling. Instinct needs both: GitNexus-class typed receiver resolution and GT-class abstention/certification.

### 3.4 Framework and architectural semantics

GitNexus has first-class extraction and edges for:

- framework and static routes, including Next.js, Expo, Laravel, Django, Spring, FastAPI, NestJS, dispatch guards and data route tables;
- MCP/tool handlers through `HANDLES_TOOL`;
- ORM queries through `QUERIES`;
- Spring configuration and AOP;
- framework-neutral dependency injection through `INJECTS`;
- method overrides and interface implementation;
- community detection and inferred process steps;
- HTTP/gRPC/Thrift/topic contracts and group-level cross-repository joins.

The route extractor is deliberately precision-weighted and documents missing coverage rather than manufacturing handlers. These capabilities are summarized in the [phase table](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md#ingestion-pipeline) and [route extraction section](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md#where-routes-come-from).

GT has public-surface, affected-test, impact, process, supporting-file and validation roles in its decision compiler, but those are delivery roles over heterogeneous evidence; they are not all equally strong first-class graph semantics. The distinction is important. A role-labelled claim is not equivalent to a repository-wide framework extraction pass.

**Judgment:** GitNexus wins decisively on framework semantics today.

### 3.5 Communities and processes

GitNexus runs Leiden community detection and derives Process nodes with `STEP_IN_PROCESS` relationships. Its tools can return processes around symbols and map changes into affected processes. This provides a higher-order view beyond one-hop call relations.

The output is not exhaustive business-process truth. Community projection is limited to selected symbol and relationship kinds and is filtered more aggressively on large graphs. Process extraction is a capped heuristic call-trace search: current defaults bound depth, branching, candidate entry points and total processes, and the implementation records truncation counters. Those limits are sensible operational safeguards, but any consumer must treat the resulting process set as a bounded sample rather than evidence that no other process exists. See [`community-processor.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/ingestion/community-processor.ts) and [`process-processor.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/ingestion/process-processor.ts).

GT composes bounded process evidence and impact evidence for decisions, with source authority and explicit truncation/coverage concerns. It is better positioned to say “this output is a lower bound,” but the underlying process model is less mature than GitNexus's dedicated community/process phases.

**Judgment:** GitNexus has the stronger graph-native process layer; GT has the stronger delivery-time epistemic controls.

### 3.6 CFG, PDG and taint

GitNexus supports an opt-in `--pdg` path. Current architecture source describes BasicBlock nodes, CFG edges, reaching definitions, taint/sanitization/path relationships and control dependence. `pdg_query` and `explain` expose bounded anchored reads. The limitations are material:

- the layer is opt-in and off by default;
- current CFG visitor support is narrower than the general language surface;
- queries are intra-procedural at the PDG read surface;
- data flow does not cross repository boundaries;
- PDG edges are excluded from default impact traversal;
- truncation and unsupported shapes remain possible.

See [optional CFG/PDG emission](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md#optional-cfgpdg-emission---pdg-20812086) and the [`pdg_query` tool declaration](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/mcp/tools.ts).

GT has property/assertion/dataflow-related storage and retrieval surfaces, but its active treatment does not demonstrate a GitNexus-equivalent, broadly queryable CFG/PDG/taint pipeline.

**Judgment:** GitNexus wins this category. Instinct should not claim parity until it can pass language-specific data-flow precision and recall fixtures.

### 3.7 Cross-repository depth

Both projects have multi-repository concepts. GitNexus's group mode is more concretely productized: cross-repository trace joins per-repository graphs over a `ContractLink` identified by a shared contract symbol. The implementation intentionally permits one contract boundary, reports deeper requested crossings as notes, and does not pretend to offer cross-program data flow. This is a good bounded contract, not a defect disguised as completeness.

GT stores repository provenance and cross-repository edges, but the active coding benchmark is task-repository centered and does not establish superior cross-repository trace quality.

**Judgment:** GitNexus wins demonstrated cross-repository product depth.

## 4. Retrieval and ranking

### GroundTruth

GT's active pipeline combines exact paths and identifiers, lexical search, BM25, local dense retrieval, and certified graph relationships. It ranks around task requirements and repository roles, then compiles only facts that are relevant, novel, source-backed and valid for the active repository revision. Weak graph candidates can be downgraded to inspection targets instead of edit owners. The treatment budget is explicit: 4,096 task evidence tokens with a 512-token critical reserve in the active profile.

The measured 427-row retrieval benchmark reports ranked MRR 0.4372, Recall@20 0.7072, BCY@8K 0.5198 and delivered-payload MRR 0.4207. Those numbers demonstrate useful retrieval, not end-to-end solve superiority.

### GitNexus

GitNexus product search merges BM25 and semantic results using reciprocal rank fusion with `k=60`. Results track whether they came from lexical, semantic or both sources. The implementation is readable in [`hybrid-search.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/search/hybrid-search.ts).

The product also supplies structured graph reads—context, impact, trace, route map, tool map, API impact, process and arbitrary Cypher—that can outperform generic retrieval when the question already names the right anchor.

The crucial benchmark limitation is explicit in both [`native.yaml`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/configs/modes/native.yaml) and [`native_augment.yaml`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/configs/modes/native_augment.yaml): `skip_embeddings: true`. Therefore the standard evaluation does not measure the semantic side of the advertised hybrid search.

### Judgment

GT has the better benchmark-integrated retrieval and evidence-selection layer. GitNexus has the better structured semantic graph and a clean product-level RRF implementation. A fair head-to-head needs two GitNexus arms—lexical-only as currently configured and full hybrid—not one vague “GitNexus on” arm.

## 5. How the model sees and uses the tools

### 5.1 GroundTruth: host-owned delivery

The active GT path uses Mini-SWE-Agent 2.4.6 for model configuration, Bash execution, interruption and accounting. GT owns repository intelligence and provider-boundary delivery. The graph is constructed before the generative bootstrap planning call. During execution, evidence can be compiled at seven boundaries:

1. `REPOSITORY_START`;
2. `IDENTITY_AMBIGUITY`;
3. `PRE_EDIT`;
4. `POST_EDIT_GRAPH_DELTA`;
5. `FAILURE_OBSERVATION`;
6. `VERIFICATION_SELECTION`;
7. `PRE_SUBMIT`.

The boundary and role mapping lives in [`gt_engine/decision_value.py`](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/decision_value.py). The active treatment requests `integrated_same_observation`, which means repository evidence is attached to the relevant tool observation rather than sent as a detached generic warning.

This design has four advantages:

- it does not depend on the model remembering that a graph tool exists;
- it can deliver immediately after the observation that creates the decision;
- it can record the exact bytes included in the provider request;
- it can connect those bytes to a later action, validation or contradiction.

Its risk is centralized blast radius. Bad boundary classification, stale graph certification, over-aggressive ranking or malformed injection affects every task even when the base Mini-SWE loop would have succeeded unaided.

### 5.2 GitNexus product: model-invoked semantic tools

The current MCP registry exposes a far richer surface: repository listing, semantic query, Cypher, symbol context, impact, change detection, rename, API impact, trace, route map, tool map, shape checking, explanation, PDG query, and group operations. The declarations include read-only/destructive annotations and capability caveats. See [`gitnexus/src/mcp/tools.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/mcp/tools.ts).

This is good product design for an interactive coding agent. The model can ask a precise semantic question and receive a compact structured answer. But availability is not consumption. If the agent does not call the tool—or calls it after making the wrong edit—the graph provides no decision value.

GitNexus also has automatic editor-hook delivery. Its Claude/Codex hook intercepts selected Grep, Glob and Bash activity, can return `additionalContext`, and warns after Git mutations that may make the index stale. The augmentation engine is intentionally shallow and bounded: it uses BM25-oriented file/symbol/neighbour hits and returns empty on error. This is more than a purely voluntary MCP product, but it is still event-pattern enrichment rather than GT's typed decision-boundary lifecycle. See [`gitnexus-hook.cjs`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/hooks/claude/gitnexus-hook.cjs) and [`augmentation/engine.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/augmentation/engine.ts).

### 5.3 GitNexus SWE-bench: five wrappers and grep augmentation

The standard evaluation wrapper exposes and counts only:

- `gitnexus-query`;
- `gitnexus-context`;
- `gitnexus-impact`;
- `gitnexus-cypher`;
- `gitnexus-overview`.

An additional `gitnexus-augment` wrapper is used by native augmentation. The exact registry is [`eval/tool_registry.py`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/tool_registry.py). Each wrapper first calls a loopback HTTP evaluation server and falls back to the CLI.

`GitNexusAgent.execute_actions` lets Mini-SWE execute the model's Bash action, then optionally inspects grep/rg/ag commands and appends augmentation. The automatic path is therefore narrower than GT's decision-boundary compiler:

- it triggers on a search-command regex rather than task state or decision state;
- the inspected regex does not include every search command described in prose;
- augmentation failures are swallowed so ordinary execution continues;
- a hit is counted from a marker in output, not from demonstrated use in the next action;
- no full feature lifecycle connects candidate, delivery, consumption and validation.

The implementation is [`eval/agents/gitnexus_agent.py`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/agents/gitnexus_agent.py).

### 5.4 Required Instinct tool contract

Instinct should expose two complementary surfaces:

- **Explicit semantic tools** for context, impact, trace, routes, tools, data flow, contracts, change surface and graph queries.
- **Automatic decision packets** at the seven GT boundaries, generated from the same graph and authority policy.

Every model-visible packet or tool result must have one receipt containing:

```text
task identity
workspace/source revision
graph source revision and logical revision
graph build identity and capability envelope
triggering action and observation
tool or decision boundary
query and parameters
source node/edge identifiers
authority and uncertainty
truncation/completeness status
exact model-visible bytes and hash
provider request hash
next model action
later validation or contradiction
```

Without that chain, “tool call count” is instrumentation theater.

## 6. Freshness, incremental updates and failure behavior

### GroundTruth

GT's intended invariant is that graph-derived evidence must agree with the current graph-input source revision. The active runtime marks changes, suppresses stale facts and refreshes before a graph-dependent boundary. Graph leases and publication receipts separate source revision, logical graph revision, database hash, manifest hash and binary identity. The release profile requires graph readiness.

This is the right benchmark invariant because an agent changes the repository while it works. An index that was correct at task start can become wrong after the first edit.

### GitNexus product

GitNexus has meaningful incremental machinery: changed-subgraph extraction, dependent expansion, incremental writeback, incomplete-index reasons and escalation to a full write plan. The audited escalation gate switches when the effective write set is above 50% and at least 50 files—not GT's older 20% design target. The source explicitly documents population mismatch and chooses safe escalation. See [`escalation-gate.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/incremental/escalation-gate.ts).

GitNexus also persists incomplete reasons for incremental work, embedding checkpoints and graph-write collapse. Its graph-write health check distinguishes collapsed, healthy and unmeasurable states instead of treating an unreadable count as zero. See [`index-freshness.ts`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/index-freshness.ts).

Its parser pool can quarantine files after attributed failures and complete a graph with logged omissions. Staleness is generally surfaced as a warning rather than universally blocking every graph answer. Those are valid product availability choices, but a benchmark must promote both conditions into machine-readable treatment status; otherwise a degraded graph remains indistinguishable from a complete graph that simply found fewer relations.

### GitNexus SWE-bench gap

The evaluated environment constructs or restores an index at startup. In the inspected standard harness, GitNexus setup exceptions are logged and execution continues with `_gitnexus_ready = False`. The wrapper path can also fall back from the server to CLI. This makes the treatment resilient as software but dangerous as an experiment: a nominal native row may not have received a working treatment unless readiness is recorded and gated. The path is [`eval/environments/gitnexus_docker.py`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/environments/gitnexus_docker.py).

### Required head-to-head failure semantics

- Index startup failure: treatment-invalid, never a normal unsolved task.
- Missing graph capability required by the task: explicit `NOT_APPLICABLE` or capability-limited classification.
- Incremental refresh failure: preserve prior complete graph, suppress its stale claims, and classify infrastructure failure if the treatment cannot continue honestly.
- Tool fast-path failure followed by successful fallback: record both attempts and backend identity.
- Missing terminal receipt: invalidate that row and all aggregate efficiency denominators that require it.
- Timeout or exception: finalize a terminal receipt from a host-owned `finally` boundary.

## 7. Experimental rigor and accounting

### GroundTruth benchmark strength

The active GT release pins a runtime commit, treatment hash, baseline hash, task profile and authorized workflows. Terminal-Bench runs through Harbor 0.20.0; DeepSWE runs through Pier 0.3.1; both use the same Mini-SWE central agent. The active release requires Mini-SWE-Agent 2.4.6 and graph-first planning. The contract is summarized in the repository [`README.md`](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/README.md) and frozen in [`active_release.json`](https://github.com/harneet2512/gt-harness/blob/24838a77e8103395c827b94d2b29993b8d8ced3d/eval/release/active_release.json).

GT records substantially more causal telemetry than GitNexus's general SWE-bench harness: feature lifecycle, graph revisions, exact delivery, provider usage, actions, verification, terminal classification and decision-value measures. This supports diagnosis of why a treatment failed even when the task outcome is binary.

### GitNexus general SWE-bench strength and gaps

GitNexus uses the same `GitNexusAgent` class for baseline and treatment modes, a good parity choice. Standard modes use a 30-step and $3 cost limit. Index caches are keyed from repository and commit. Metrics include patch presence, optional official resolve result, cost, API calls, graph-tool calls and augmentation hits. See [`eval/README.md`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/README.md) and [`eval/analysis/analyze_results.py`](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/analysis/analyze_results.py).

The main gaps are:

- setup can degrade silently to no GitNexus;
- native arms disable embeddings;
- the treatment surface is a small subset of product capabilities;
- augmentation hits do not prove model uptake;
- no equivalent mandatory terminal, graph-revision and exact-delivery receipt was found;
- cache identity is repository and commit, not visibly the complete analyzer binary/config/capability identity;
- published broad controlled results were not found in the audited repository.

### GitNexus workflow benchmark: what GT should copy

The newer [`eval/workflow_bench`](https://github.com/abhigyanpatwari/GitNexus/tree/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/workflow_bench) is a different and stronger artifact. It provides:

- fresh detached worktrees per arm/run;
- identical task text and repository ref;
- hidden harness-owned behavioral oracles separated from model-visible verification;
- immutable, sanitized, harness-built graph assets;
- Linux Bubblewrap containment and credential scrubbing;
- parent-captured event streams with digests;
- explicit session/infra exclusions;
- paired incumbent/candidate promotion;
- quality-first gating before efficiency;
- minimum three valid paired runs per task;
- no per-task quality regression;
- conservative efficiency thresholds and expiry;
- transactional candidate application.

Its own historical calibration is appropriately negative: on small tasks, the full plan→work workflow cost much more than baseline while all arms solved, and direct workflow remained closer to baseline. That is useful evidence because it rejects the assumption that more graph workflow is automatically more efficient.

## 8. Capability scorecard

Scores describe audited source capability, not marketing and not causal outcome evidence. `5` means strong current implementation; `0` means absent or not found.

| Dimension | GT product | GT active benchmark | GitNexus product | GitNexus SWE-bench | Evidence-backed leader |
|---|---:|---:|---:|---:|---|
| Declared language breadth | 5 | 5 | 3 | 3 | GT |
| Mainstream-language semantic depth | 3 | 3 | 5 | 3 | GitNexus product |
| Calls/types/receiver resolution | 3 | 3 | 5 | 3 | GitNexus product |
| Framework routes/tools/ORM/DI/AOP | 2 | 2 | 5 | 2 | GitNexus product |
| Communities/processes | 3 | 3 | 5 | 3 | GitNexus product |
| CFG/PDG/taint | 2 | 1 | 5 opt-in | 0 standard mode | GitNexus product |
| Cross-repository contracts | 3 | 1 | 5 bounded | 0 standard mode | GitNexus product |
| Hybrid lexical+dense retrieval | 5 | 5 | 5 | 2 embeddings off | Product tie; GT treatment |
| Evidence authority/abstention | 5 | 5 | 3 | 2 | GT |
| Automatic timely delivery | 5 | 5 | 2 | 3 grep-triggered | GT |
| Explicit semantic tool surface | 3 | 2 | 5 | 3 | GitNexus product |
| Exact provider-visible byte receipt | 5 | 5 | 1 not found | 1 not found | GT |
| Per-feature uptake/contradiction | 5 | 5 | 1 not found | 2 call/hit counts | GT |
| Revision-certified post-edit freshness | 5 | 5 intended | 4 product | 2 harness | GT treatment |
| Incremental index engineering | 4 | 4 | 5 | 2 | GitNexus product |
| Fail-closed treatment readiness | 5 | 5 | 4 | 1 | GT |
| Terminal receipt completeness | 5 | 5 | 2 | 2 | GT |
| Hidden-oracle workflow benchmark | 3 | 3 | 5 | 5 workflow bench | GitNexus workflow bench |
| Frozen release/treatment identity | 5 | 5 | 4 | 3 | GT |
| Broad published causal benchmark proof | 1 | 1 | 1 | 1 | Neither |

The scorecard's most important row is the last one. Strong software plus a sophisticated harness does not equal proven outcome superiority.

## 9. Failure modes that a head-to-head must expose

| Failure | Why ordinary solve rate misses it | Required detector |
|---|---|---|
| Unsupported language counted as graph-ready | The model may solve with grep anyway | Per-file semantic capability receipt |
| Index silently excludes authored files | Task may pass on unrelated evidence | Complete discovery census with reason codes |
| Stale graph after edit | Wrong context can look plausible | Source/graph revision agreement on every delivery |
| Ambiguous symbol presented as owner | Model edits the wrong same-named target | Candidate count, receiver identity and abstention state |
| Correct fact delivered after the decision | Tool appears “used” but adds no value | Boundary timestamp and first relevant action |
| Automatic augmentation repeated unchanged facts | More context looks like more intelligence | Novelty hash and duplicate-delivery gate |
| Tool setup failed and arm continued | Treatment result is mislabelled | Fail-closed readiness receipt |
| Graph query truncated but presented as complete | Missing impact is read as absence | Epistemic completeness/truncation envelope |
| Embeddings disabled while reporting hybrid product | Benchmark claim overstates treatment | Exact capability/config attestation |
| Authored test passes but behavior remains wrong | Agent can test its own misconception | Hidden harness-owned oracle |
| Missing receipt counted as zero usage | Treatment appears efficient because accounting failed | Completeness denominator and invalid-row classification |
| Cache built with different analyzer/config | Paired arms consume different intelligence | Content-addressed graph build identity |
| More calls caused by useful diagnosis | Efficiency metric punishes information gain | Decision-value measures before consequence metrics |

## 10. The common Instinct head-to-head benchmark

### 10.1 Arms

Use one common Mini-SWE-Agent base and run these frozen arms:

1. `CONTROL`: no repository-intelligence treatment.
2. `GT_V3`: current integrated same-observation treatment.
3. `GITNEXUS_NATIVE_LEXICAL`: current five-wrapper treatment, embeddings off.
4. `GITNEXUS_NATIVE_HYBRID`: same wrappers, embeddings on and attested.
5. `GITNEXUS_NATIVE_AUGMENT`: explicit tools plus search augmentation.
6. `GITNEXUS_FULL_SURFACE`: product MCP capabilities exposed through benchmark-safe wrappers, including trace, route, tool, change and PDG where available.
7. `INSTINCT_EXPLICIT`: Instinct graph tools only.
8. `INSTINCT_BOUNDARY`: Instinct automatic decision packets only.
9. `INSTINCT_FULL`: explicit tools plus automatic decision packets.

Do not compare `GT_V3` only with `GITNEXUS_NATIVE_LEXICAL` and call it a product comparison. That would compare GT's full active delivery to a deliberately restricted GitNexus treatment.

### 10.2 Frozen parity contract

Every arm must share:

- exact task IDs and repository commits;
- container image and dependency snapshot;
- Mini-SWE-Agent version and prompt scaffold;
- model identifier, provider route, temperature and sampling controls;
- maximum actions, wall time, provider calls, context and output budgets;
- task-visible prompt and tool descriptions, except treatment-specific capabilities;
- task oracle and official verifier;
- concurrency class and retry policy;
- network policy;
- patch capture and terminal classification;
- run-count schedule.

Graph build time and graph tokens are treatment costs, not free preprocessing. Cache both paired arms only when the cache key binds repository bytes, analyzer commit/binary, configuration, language providers, embeddings, PDG mode and schema.

### 10.3 Graph-intrinsic fixture suite

Before any paid agent run, evaluate graph correctness directly.

#### Breadth fixtures

- one fixture per declared language capability;
- source, test, configuration, generated and vendor boundary cases;
- nested ignore files, submodules, symlinks, untracked files and oversized files;
- framework fixtures for routes, tools, ORM, DI, AOP and public APIs;
- multi-repository HTTP, RPC, event and schema contracts.

#### Depth fixtures

- imports and re-exports;
- alias and namespace resolution;
- overloads, interfaces, generics and dynamic dispatch;
- receiver chains, awaits, indexing and fluent APIs;
- decorators/annotations/macros where supported;
- callbacks, closures and higher-order functions;
- caller/callee, impact and affected-test ground truth;
- CFG branch/loop/exception behavior;
- reaching definitions and taint source/sink/sanitizer behavior;
- community/process recovery;
- cross-repository boundary trace.

#### Mutation fixtures

- new, modified, deleted and renamed files;
- import target changes;
- signature changes;
- multi-file atomic edits;
- generated metadata changes;
- interrupted incremental build;
- concurrent reader during publication;
- dependency closure above and below escalation threshold.

Report precision, recall, abstention, unsupported rate, truncation, build time, peak memory, database size and incremental/full rebuild ratio. A graph that returns more edges but invents owners is worse than a smaller graph with honest abstention.

### 10.4 Tool and delivery fixture suite

For every product feature, prove this chain:

```text
capability available
→ deterministic trigger or explicit invocation
→ graph/retriever query
→ source-backed result
→ model-visible bytes
→ next agent action
→ behavioral validation or contradiction
```

Test at least:

- implementation owner;
- identity ambiguity;
- inspection dependencies;
- public surface;
- impact/change surface;
- affected tests;
- processes;
- supporting files;
- new-file precedent;
- failure analysis;
- verification selection;
- route/tool/API trace;
- data-flow explanation;
- cross-repository contract.

Irrelevant features must terminate as `NOT_APPLICABLE`; unsafe candidates as `ABSTAINED`; unavailable backend capabilities as capability-limited. None may be reported as silently “not triggered.”

### 10.5 Agent task population

Use a stratified, frozen task set rather than one leaderboard average:

- single-file local repairs;
- identity-ambiguous repairs;
- cross-module implementation changes;
- public API and compatibility changes;
- framework routing/DI/ORM changes;
- caller/impact-heavy changes;
- test-selection-heavy changes;
- failure-driven debugging;
- multi-language repositories;
- cross-repository contract changes;
- data-flow/security defects;
- unfamiliar or unseen repositories.

Include DeepSWE, Terminal-Bench 2 and SWE-Live Lite only after deterministic gates pass. Keep task family in the statistical model; 20 correlated tasks are not 20 independent universes.

### 10.6 Outcome and decision-value metrics

#### Quality first

- official resolved rate;
- per-task solve probability across repeated trials;
- mean pass@1;
- strict pass^k;
- regression count and paired flips;
- harness-owned hidden-oracle pass;
- patch correctness and unrelated-change rate.

#### Decision value

- first correct implementation-owner rank;
- time/actions to first correct target inspection;
- time/actions to first correct edit;
- unrelated inspections before correct edit;
- target switches and reverted edits;
- public/change-surface coverage;
- affected-test precision and recall;
- failure-to-relevant-action latency;
- delivered-fact consumption rate;
- delivered-fact contradiction rate;
- useful explicit-tool invocation rate;
- automatic-packet marginal uptake;
- stale/duplicate/truncated delivery rate.

#### Consequence efficiency

- provider calls and tokens;
- delivery tokens;
- wall time;
- model cost;
- graph build and refresh CPU/wall time;
- graph artifact bytes;
- cost per verified solve;
- efficiency variance by task class.

Never report aggregate savings from unequal completed-task sets. Produce both complete-case paired accounting and intent-to-treat accounting, with infrastructure failures shown separately.

### 10.7 Statistical contract

- Freeze the analysis before running.
- Use repeated trials per task and arm.
- Pair trials through the same task/model/configuration block.
- Report task-clustered bootstrap confidence intervals.
- For one paired binary pass use exact McNemar, but do not confuse a non-significant directional result with proof.
- Correct for multiple primary comparisons or name one primary contrast in advance.
- Report the entire per-task matrix, not only the mean.
- Require zero missing terminal receipts for an official efficiency claim.
- Treat infrastructure-invalid rows as intent-to-treat failures or report them separately; never silently drop them.

## 11. What Instinct should borrow—and what it should reject

### Borrow from GitNexus

1. The explicit ingestion phase DAG and typed phase outputs.
2. Language-provider abstraction with compile-time completeness.
3. Deep scope/receiver/type resolution.
4. Framework route, tool, ORM, DI, AOP and MRO passes.
5. Community and process extraction.
6. Contract-based cross-repository bridge with a documented boundary limit.
7. Optional CFG/PDG/taint layers with bounded reads and explicit limitations.
8. Structured MCP tools for precise semantic questions.
9. Incremental escalation and persisted incomplete-index reasons.
10. The workflow benchmark's hidden oracle, containment, paired candidate promotion and negative-result honesty.

### Borrow from GroundTruth

1. Separate validation relevance from structural/symbol/caller capability.
2. Source revision, graph source revision, logical graph revision and build identity.
3. Source evidence and authority on every delivered claim.
4. Abstention for ambiguity rather than confident heuristic ownership.
5. Decision-boundary delivery instead of fixed or purely model-initiated timing.
6. Exact provider-visible byte and request hashing.
7. Feature lifecycle from applicability through validation/contradiction.
8. Same-observation injection and uptake auditing.
9. Terminal receipts for exceptions, timeouts and finalization failures.
10. Frozen treatment/release identity and mechanical benchmark authorization.

### Reject from either system

- silent treatment degradation;
- capability claims inferred from package installation;
- “hybrid” benchmark labels when embeddings are disabled;
- call count as a proxy for useful tool use;
- static delivery quotas;
- graph completeness inferred from a successful process exit;
- cache keys that omit analyzer/configuration identity;
- authored tests as the only correctness oracle;
- missing receipts treated as zero cost;
- broad product claims from a narrow treatment arm;
- solve-rate superiority claims from one small, underpowered trial.

## 12. Implementation order for Instinct

The sequence is deliberately dependency-ordered.

1. **Common evidence schema:** task, workspace, graph, capability, query, delivery, action and validation identities.
2. **Complete discovery census:** every source candidate and exclusion reason.
3. **Language capability envelope:** validation, structure, symbol, caller, framework and data-flow tiers.
4. **Typed phase DAG:** structure → parse → scope → cross-file → type/receiver → framework → communities/processes → optional PDG.
5. **One authority boundary:** exact, ambiguous, heuristic, external, suppressed and unsupported relations.
6. **Transactional graph publication:** content-addressed complete graph, journal, read lease, rollback and incremental escalation.
7. **Structured tool surface:** context, impact, trace, route, tool, API, change, test, process, contract and data flow.
8. **Decision-boundary compiler:** source-backed, novel, relevant, role-assigned, actionable packets.
9. **Exact provider exposure:** same-observation injection plus request and payload hashes.
10. **Causal lifecycle:** delivered → consumed → validated or contradicted.
11. **Fail-closed terminal receipt:** every selected task ends with one authoritative row.
12. **Graph fixtures and mutation gates:** no agent benchmark before these pass.
13. **Common Mini-SWE arm adapters:** GT, GitNexus and Instinct under one parity contract.
14. **Hidden behavioral oracles and repeated paired trials.**
15. **Quality-first promotion gate:** only then optimize calls, tokens and cost.

## 13. Release gates

Instinct is head-to-head ready only when all of these are true:

- 100% task and terminal-receipt completeness;
- 100% source/graph revision agreement for delivered graph facts;
- zero stale deliveries;
- zero silent backend degradation;
- zero duplicate unchanged packets;
- every triggered feature has a complete lifecycle;
- every delivered fact has independently checkable source evidence;
- at least 98% precision for certified source facts on the fixture suite;
- at least 90% top-three implementation-owner recall on independently enumerated fixtures;
- explicit per-language capability accuracy;
- killed incremental publication preserves the prior complete graph;
- concurrent readers never observe a partial graph;
- no duplicate full rebuild for one workspace revision;
- hidden-oracle results are isolated from model-visible verification;
- model, provider, scaffold, task, limits and retry policies are identical across arms;
- no benchmark task names or repository-specific exceptions occur in production ranking logic;
- no official efficiency comparison uses unequal or missing usage rows.

## 14. Final verdict

### Which is better today?

- **For graph semantic depth:** GitNexus.
- **For language breadth:** GroundTruth.
- **For framework and cross-repository semantics:** GitNexus.
- **For certified, timely model delivery and causal receipts:** GroundTruth.
- **For current benchmark treatment completeness and fail-closed accounting:** GroundTruth.
- **For hidden-oracle workflow/prompt promotion discipline:** GitNexus's newer workflow benchmark.
- **For proven general solve superiority:** neither.

### What would make Instinct superior?

Instinct becomes superior only if it proves all three layers at once:

1. a GitNexus-class semantic graph;
2. a GT-class revision-certified decision-delivery pipeline;
3. a common hidden-oracle benchmark showing higher verified solve probability or equal quality with materially better decision value and efficiency.

Anything less is a partial product being compared through a favorable harness.

## Primary source map

### GroundTruth

- [GT-Harness active scaffold and measured evidence](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/README.md)
- [Active central relational v3 treatment](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/eval/treatments/tb2_central_relational_v3.json)
- [Decision boundaries and feature lifecycle](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/decision_value.py)
- [Language capability authority](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/language_registry.py)
- [Central Mini-SWE integration](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/eval/gt_central_agent.py)
- [Graph/index publication integration](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/indexer.py)
- [Hybrid retrieval authority and ranking](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/gt_engine/hybrid_retrieval.py)
- [Graph schema](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/vendor/gt-index-src/internal/store/sqlite.go)
- [Logical graph revision](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/vendor/gt-index-src/internal/store/revision.go)
- [Normalized edge metadata](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/vendor/gt-index-src/internal/store/edge_metadata.go)
- [Terminal-Bench Mini-SWE workflow](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/.github/workflows/tb2_miniswe_central.yml)
- [DeepSWE Mini-SWE/Pier workflow](https://github.com/harneet2512/gt-harness/blob/249cfc1efc23341e403eaa10cf734271f8b7f47f/.github/workflows/deepswe_miniswe_central.yml)

### GitNexus

- [Architecture and ingestion DAG](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/ARCHITECTURE.md)
- [MCP tool registry](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/mcp/tools.ts)
- [Hybrid BM25/semantic RRF](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/search/hybrid-search.ts)
- [Incremental escalation gate](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/incremental/escalation-gate.ts)
- [Index completeness and graph-write health](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/gitnexus/src/core/index-freshness.ts)
- [General SWE-bench evaluation](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/README.md)
- [Mini-SWE treatment agent](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/agents/gitnexus_agent.py)
- [Benchmark wrapper registry](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/tool_registry.py)
- [Benchmark environment and fail-open setup behavior](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/environments/gitnexus_docker.py)
- [Native lexical-only mode](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/configs/modes/native.yaml)
- [Native augmentation mode](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/configs/modes/native_augment.yaml)
- [Workflow benchmark trust and promotion model](https://github.com/abhigyanpatwari/GitNexus/blob/b059ab3541ea68c2ce292955fc367a5de04b39ea/eval/workflow_bench/README.md)
