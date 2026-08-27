# GT Harness architecture type

Status: authoritative architecture reference for the GT Harness prerelease.

This document defines what GT Harness is intended to do, the production path
that currently implements that intent, the contracts between its components,
and the conditions under which the product must abstain or fail. It describes
the checked-in product rather than historical experiments. When prose and code
disagree, `eval/benchmark_product_contract.json`, the installed `gt-harness`
entry point, and emitted receipts are the executable authorities; repair this
document in the same change that repairs the discrepancy.

## Definition of `arch_type`

`arch_type` is the repository's architecture contract. It answers four
questions that a directory listing cannot answer:

1. What product is being shipped?
2. Which code path is authoritative at runtime?
3. What evidence must cross each boundary before the next component may act?
4. What observable receipt proves that the component actually worked?

It is not a feature wish list, competitor checklist, benchmark report, or map
of every historical module. A capability is part of the architecture only if
the canonical `gt-harness run` path reaches it or a production certification
gate verifies it. Research code remains useful evidence, but it does not become
production merely because it exists or has tests.

## Product thesis and intended outcome

GT Harness is a model-agnostic benchmark product for coding agents. Its
treatment converts an exact repository checkout and issue statement into
bounded, deterministic, source-backed repository facts. Those facts are added
to the same Mini-SWE-Agent action loop used by the bare comparison arm. The
intended causal chain is:

```text
more accurate repository facts
  -> less blind search and fewer wrong-file edits
  -> earlier inspection of the real edit/public/integration/test surfaces
  -> better implementation and verification decisions
  -> higher solve rate with no unacceptable negative flips
  -> lower steps, tokens, latency, or cost per solved task
```

The product does not depend on a particular reasoning model. The model route is
an experiment parameter. Repository discovery, graph construction, dense
embedding, retrieval, fact compilation, delivery, and receipt generation make
no provider calls.

The architecture makes three deliberately separate claims:

- **Substrate correctness:** the graph and dense index represent the exact
  checkout and disclose limitations.
- **Treatment correctness:** every delivered claim is real, revision-bound,
  bounded, timed correctly, and visible to the agent.
- **Product utility:** the treatment improves controlled benchmark outcomes and
  efficiency without an unacceptable regression rate.

The first two claims do not prove the third. A READY graph can still be paired
with poor localization, irrelevant ranking, or unhelpful delivery. Product
utility is established only by official paired outcomes plus trajectory review.

## Product boundary

The sole installed executable is `gt-harness`, provided by
`gt_harness.cli:main`. Its production commands are:

| Command | Responsibility | Completion evidence |
| --- | --- | --- |
| `gt-harness doctor` | Check Python, Git, Go, the source-built `gt-index`, and product dependencies. | Structured doctor output and zero exit status. |
| `gt-harness graph build` | Construct and atomically publish the canonical repository graph. | `gt.graph_receipt.v5` with a ready or explicit non-ready state. |
| `gt-harness graph status` | Revalidate graph identity, checksum, and SQLite health against the current checkout. | Fresh public graph receipt. |
| `gt-harness graph query` | Answer a supported structural query only through a revalidated graph. | Query response bound to the graph receipt. |
| `gt-harness run --treatment bare` | Run the unaugmented comparison arm. | `gt.run_receipt.v1` plus trajectory. |
| `gt-harness run --treatment groundtruth` | Run Mini-SWE-Agent with deterministic GT context. | Run, treatment, delivery, graph, dense, and trajectory receipts. |
| `gt-harness record-outcome` / `record-harbor-outcomes` | Bind an independent verifier result to a product run. | Content-addressed outcome receipt. |
| `gt-harness compare` | Reject invalid pairings and calculate provider-free paired metrics. | Comparison receipt/report. |
| `gt-harness certify` | Validate a complete provider-free product evidence bundle. | Certified or explicit `NOT_CERTIFIED` result. |

MCP is not the GT Harness benchmark boundary. Nano is not a supported scaffold.
Benchmark adapters may provision environments and translate suite protocols;
they may not reimplement the treatment, inject benchmark answers, or alter the
agent loop.

## End-to-end production flow

```text
official benchmark task and immutable repository image
  -> suite orchestrator provisions /app and official verifier
  -> suite adapter installs exact GT Harness SHA, Mini-SWE-Agent 2.4.6,
     source-built gt-index, and checksum-pinned Snowflake ONNX assets
  -> adapter invokes gt-harness run with the real issue, task identity,
     repository root, private state directory, model route, and time budget
  -> GroundTruthTreatment computes repository identity
  -> RepositoryGraphService builds or revalidates an immutable graph generation
  -> PersistentDenseSemanticIndex builds or revalidates repository embeddings
  -> hybrid retrieval gathers exact, sparse, structural, and dense candidates
  -> RepositoryContextCompiler separates edit authority from inspection roles
  -> persisted graph projector adds bounded processes, impact, and tests
  -> semantic graph adds supported deterministic source-semantic facts
  -> compact gt.agent_context.v7 requirement packet is attached to the task
  -> Mini-SWE-Agent 2.4.6 chooses and executes shell actions
  -> GT observes each complete assistant turn, refreshes stale state, and may
     append at most one bounded delta to the final triggering observation
  -> agent submits a patch
  -> official suite verifier grades the patch independently
  -> adapter binds verifier result to exact run/product/task identities
  -> suite attestation verifies every expected task and artifact
  -> paired comparison and trajectory audit determine utility
```

No benchmark script is allowed to substitute a cached brief, mock graph,
reference patch, expected file list, or manually prepared repository packet for
this path.

## Layer 1: experiment and release control plane

### Machine-readable product contract

`eval/benchmark_product_contract.json` pins:

- product name and version;
- `gt-harness run` as the product command;
- Mini-SWE-Agent 2.4.6 as the scaffold;
- `hybrid_required` retrieval for the active treatment;
- one adapter and workflow per benchmark suite;
- required environment and artifact names;
- graph/dense delivery invariants; and
- forbidden historical treatment paths.

Workflows must validate this contract before provider spend. A workflow that
uses another scaffold version, source SHA, adapter, or treatment boundary is a
different experiment and cannot be merged into the official comparison.

### Supported benchmark adapters

| Suite | Orchestrator | Canonical adapter | Canonical workflow |
| --- | --- | --- | --- |
| Terminal-Bench 2 | Harbor 0.20.0 | `eval.harbor_gt_harness_adapter:GtHarnessMiniSwe246Agent` | `.github/workflows/tb2_miniswe_product.yml` |
| DeepSWE | DataCurve Pier 0.3.1 | `eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent` | `.github/workflows/deepswe_gt_harness_product.yml` |
| SWE-bench Live Lite | Direct immutable task container | `eval.swe_live_lite_gt_harness_adapter` | `.github/workflows/swe_live_lite_gt_harness_product.yml` |

No historical dispatch wrapper is part of the product. Each suite workflow
accepts `bare` or `groundtruth` and calls the same Mini-SWE-Agent 2.4.6 product
boundary. `production-surface.toml` rejects any additional dispatchable
workflow.

Adapters own installation, transport, environment scoping, invocation, and
artifact preservation. The external suite owns task material, repository
provisioning, time ceilings, and grading. `gt-harness run` owns treatment and
agent execution.

### Controlled-comparison identity

An apples-to-apples comparison requires equality of:

- official task ID and task checksum;
- repository revision and task image;
- model route and temperature;
- Mini-SWE-Agent version and templates;
- maximum iterations and task-derived time budget;
- tool policy and environment constraints;
- attempt count; and
- official verifier implementation.

Job success only means orchestration completed. Solve status comes exclusively
from the official verifier receipt.

## Layer 2: repository identity and discovery

`gt_engine.repository_graph_service.compute_repository_identity` is the
canonical identity function. It resolves the repository root and records:

- Git commit SHA, branch, and clean/dirty/not-Git state;
- tracked and non-ignored files relevant to the graph;
- graph-input content hashes, sizes, and filesystem fingerprints;
- dirty and untracked graph-input paths;
- submodule state;
- total discovered files, graph-input file count, and source bytes; and
- `source_revision`, a digest over commit, submodule state, and actual graph
  inputs.

The commit SHA alone is insufficient because a working tree can contain
modified or untracked source. The source revision is the checkout identity used
by graph, dense, compiler, delivery, and lifecycle receipts.

Discovery is Git-authoritative when Git metadata is available. Generated,
vendored, build-output, cache, non-regular, unsupported, and unreadable paths
are counted and assigned explicit reasons. A skipped path is a limitation, not
a successful parse.

## Layer 3: structural graph substrate

### Builder

The production graph writer is the checked-in Go source under
`vendor/gt-index-src`. `gt_harness.indexer_setup` builds it locally using a
content-addressed build identity; prerelease workflows build it from the exact
checkout rather than trusting a floating or preinstalled binary.

The builder performs:

- language resolution from path plus bounded content when suffixes are
  ambiguous;
- tree-sitter or explicitly declared bounded structural parsing;
- file, declaration, symbol, and test-node extraction;
- deterministic import and named re-export resolution;
- call, reference, hierarchy, composition, assertion, and test relationship
  construction where the language adapter can support them;
- deterministic ordering for map-derived writes;
- SQLite schema and FTS population; and
- complete discovery, parsing, hash, limitation, and component receipts.

### Canonical persisted state

The only production-authoritative graph is the immutable generation named by
the atomically replaced `CURRENT` pointer:

```text
<state-dir>/CURRENT
<state-dir>/build-attempt.json
<state-dir>/generations/<generation-id>/graph.db
<state-dir>/generations/<generation-id>/graph.manifest.json
<state-dir>/generations/<generation-id>/graph-receipt.json
```

The ordinary repository-local default is `.groundtruth/`. Benchmark adapters
pass a private task state directory. Old `index.db`, SymbolStore, MCP-owned
graphs, benchmark-generated graphs, and cached briefs are not authoritative.

### Graph states

`RepositoryGraphService` exposes these states:

| State | Meaning | Query allowed |
| --- | --- | --- |
| `ABSENT` | No published graph exists. | No |
| `BUILDING` | A build has started but no complete candidate is published. | No |
| `READY` | Complete, healthy, exact-revision graph without declared limitations. | Yes |
| `READY_WITH_DECLARED_LIMITATIONS` | Exact-revision graph is usable and every known limitation is disclosed. | Yes |
| `DEGRADED` | Construction finished but health or coverage is insufficient. | No |
| `FAILED` | Build, schema, receipt, checksum, or database validation failed. | No |
| `STALE` | Repository identity differs from the published graph. | No |

The `GraphReceipt` constructor enforces that `query_ready` is true exactly for
the two READY states. Query methods revalidate the receipt; possession of a
SQLite file is never sufficient authority.

### Readiness invariant

Graph-derived evidence may cross the product boundary only when:

```text
current commit == graph receipt commit
current source revision == graph receipt source revision
current builder identity == graph receipt builder identity
graph schema is accepted
graph checksum is valid
SQLite quick_check succeeds
discovery accounting is complete
parse and file-hash accounting is complete
component_failures is empty
build_status in {READY, READY_WITH_DECLARED_LIMITATIONS}
query_ready == true
```

A large repository with suspiciously low coverage, missing expected structural
channels, parser failure, incomplete receipt, or identity change becomes an
explicit limitation or non-ready state. It cannot silently serve partial facts.

### Lifecycle and concurrency

Cold builds persist a durable build attempt, build and seal a new generation,
then publish only its `CURRENT` pointer atomically. Database, manifest, and
receipt never mix across generations. Warm starts recompute repository identity
and validate the current generation, manifest checksum, database seal, and
generation identity before reuse. Additions,
modifications, deletions, renames, commit changes, and dirty-tree changes make
the old receipt stale and currently trigger an atomic full publication. The
file-keyed incremental implementation remains non-canonical until whole-graph
relationship parity is proven.

Cross-process publication is serialized. An interrupted build cannot expose
its partial generation as ready; restart records the abandoned attempt and
continues to serve only the previously complete exact-revision generation, or
rebuilds when that generation is no longer current.

### Query surface

The canonical graph modes are:

```text
definition, search, callers, callees, imports, importers,
reexports, exporters, implementations, subclasses, references,
impact, tests
```

Aliases normalize to these modes. Hierarchy queries require type-like anchors.
Ambiguous or unsupported anchors are reported rather than guessed.

## Layer 4: dense and hybrid retrieval

`PersistentDenseSemanticIndex` stores repository-wide file documents and
embeddings in `dense-semantic-index.v1.json`. Its receipt binds repository
source revision, document checksum, model identity, tokenizer identity, and
query readiness. The embedding backend is the locally provisioned,
checksum-pinned Snowflake ONNX model. It is not a provider call.

In `hybrid_required` mode, an ACTIVE treatment requires both a query-ready
structural graph and a query-ready dense index. Dense candidates are ranking
and inspection evidence; semantic similarity alone never grants edit
authority.

The hybrid repository and retriever combine:

- exact symbol and path matches;
- task-obligation terms;
- SQLite FTS/BM25 and lexical evidence;
- certified structural neighbors;
- dense file similarity; and
- current action state: active, changed, inspected, diagnostic, and validation
  paths.

Candidate fusion is deterministic and bounded. Retrieval scores rank possible
evidence; only source-backed identities and verified graph relationships may
be serialized as facts.

## Layer 5: task contract and context compiler

`extract_task_contract` converts the issue into deterministic obligations.
`RepositoryContextCompiler` turns each obligation into one or more `TaskFacet`
objects. A facet has a stable ID, role, exact symbols, unresolved symbols,
query terms, and optional owner/module constraints.

The compiler maintains role separation:

| Role/output | Meaning | Authority |
| --- | --- | --- |
| `EXACT_EDIT_TARGET` | Source evidence is strong enough to advise an edit location. | Edit authority, still not a command. |
| `INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY` | Relevant file or symbol lacks enough independent support for edit advice. | Inspect only. |
| `INSPECT_PUBLIC_SURFACE` | Export, declaration, compatibility, docs, or API boundary that may need preservation/update. | Inspect/public-surface role. |
| `INSPECT_INTEGRATION` | Registration, construction, dispatch, configuration, or caller boundary. | Inspect/integration role. |
| `AFFECTED_TEST` | Existing test structurally connected to selected implementation facts. | Validation evidence. |
| `PROPOSED_NEW_FILE` | Task implies a file that does not exist and exact precedent supports creating it. | Explicit new-file proposal. |
| `UNCOVERED_FACET` | No sufficiently supported repository fact covers an obligation. | Honest gap; search remains necessary. |

The compiler must never collapse edit, inspect, public surface, integration,
support, and validation into one candidate list. A successful comparator patch
is an audit oracle after a run; it is never retrieval input.

The packet also includes verified structural relations, bounded execution
processes, bounded impact facts, semantic facts, uncertainties, coverage, and
stable claim IDs. Set cover selects a compact group across distinct facets.
Duplicate natural-language obligations are deduplicated before serialization.

### Higher-order graph projections

`PersistedGraphProjector` reads the already-certified SQLite graph. It does not
build another graph. It emits:

- bounded CALLS/relationship process paths;
- bounded forward and reverse impact facts;
- test/assertion relationships; and
- truncation and lower-bound receipts.

Process and impact ceilings prevent graph explosion. A truncated traversal is
serialized as a lower bound and never represented as complete.

### Semantic graph

`gt_engine.semantic_graph` creates deterministic source-semantic facts over
selected exact spans. The current deep implementation is Python-AST-specific:
value/return/control-flow facts, uniquely bound local call arguments,
tensor-shape assertions, and versioned library contracts. Ambiguous bindings,
parse failures, and unsupported languages abstain or declare limitations.

TypeScript, JavaScript, Go, Rust, Java, and other graph languages still receive
structural graph, hybrid retrieval, process, impact, import, and test evidence;
they must not be represented as having Python-style semantic flow.

## Layer 6: provider-visible delivery

The initial packet uses schema `gt.agent_context.v7`. It is appended to the
real task before provider call 1. It contains typed requirements with intent,
entity, resolution (`RESOLVED`, `AMBIGUOUS_IDENTITY`, `UNRESOLVED`, or
`NEW_FILE_REQUIRED`), coverage, and deterministic mechanism. Edit, inspection,
public-surface, integration, and validation roles remain separate. An uncovered
requirement is stated explicitly and cannot be converted into edit authority.
Its hard budget is measured with the same
deterministic conservative token counter used by the treatment; current
benchmark packets are bounded to the configured context ceiling.

Every provider-visible claim carries or maps to a stable claim ID. The union of
`provider_delivery_receipts[].serialized_claim_ids` must equal the treatment's
`delivered_claim_ids`. Visible feature counts, context hashes, source revision,
and delivery timing are independently receipted.

After each assistant response, `TreatmentMiniSweAgent` executes the actions and
preserves each raw observation. `GroundTruthTreatment.after_actions` observes
the complete turn and may emit at most one augmentation, attached to the final
observation that triggered it. The augmentation records the raw-output hash,
turn-observation hash, context hash, source revision, token count, claims, and
the provider call before which it becomes visible.

The treatment observes actions but cannot select, rewrite, reject, retry, or
execute them. `before_model_call` is an integrity barrier, not a late-injection
channel. Therefore GT remains model-agnostic and arm-neutral except for bounded
repository evidence.

### Feature lifecycle

Provider-facing features move through explicit states:

```text
NOT_TRIGGERED -> CANDIDATE -> DELIVERED -> AVAILABLE_TO_AGENT
                                      -> FOLLOWED -> EDITED -> VALIDATED
                                      -> IGNORED or CONTRADICTED
```

Reading an attributed path may establish FOLLOWED. Only a content change to an
attributed path can establish EDITED. Only an applicable passing validation
after such a change can establish VALIDATED. A read-only test, unrelated dirty
file, or word such as “error” in ordinary output cannot manufacture progress.

## Layer 7: Mini-SWE-Agent execution

`gt_harness.miniswe_runner.TreatmentMiniSweAgent` subclasses the pinned
Mini-SWE-Agent 2.4.6 default agent only to add the treatment observation seam.
System prompt, action semantics, tool schema, iteration limit, provider route,
and submission protocol remain common between bare and GT arms.

`CredentialIsolatedEnvironment` removes provider credentials from repository
shell actions. Provider credentials are available to the model client but not
to code executed inside the task checkout.

The run receipt records scaffold class/version, model, base-URL hash,
temperature, task fingerprint, provider calls, token usage, iterations,
duration, start/end repository identities, transcript, treatment receipt, and
terminal state. A canceled process is terminalized from its durable RUNNING
checkpoint so orchestration failure remains diagnosable.

## Layer 8: verification, attestation, and outcome analysis

Every official GT task must preserve:

```text
benchmark-adapter.json
gt-run.json
gt-run.trajectory.json
official-verifier-result.json
```

An ACTIVE treatment must additionally expose its graph receipt, dense receipt,
provider delivery receipts, and persistent private state within the artifact.
Artifacts upload under `always()` so failed treatments are auditable.

The suite attestation rejects:

- missing expected tasks or artifacts;
- nonterminal runs;
- wrong source SHA, task ID, attempt, model, or scaffold version;
- absent or non-ready ACTIVE graphs/dense indexes;
- delivery/claim reconciliation errors;
- provider-call mismatch between run and trajectory;
- product calls made by the deterministic treatment; and
- unbound official rewards.

Certification and attestation prove execution integrity. They do not convert an
unsolved patch into a solve and do not prove causal uplift.

Trajectory review must inspect every task, including solves, and classify:

1. whether each delivered fact is true at the cited revision;
2. whether edit authority was actually justified;
3. whether important edit/public/integration/test roles were absent;
4. whether the agent followed, ignored, or was diverted by the context;
5. whether the final patch and tests match repository reality; and
6. whether the loss is attributable to GT, evaluator mismatch, treatment
   failure, or unresolved stochastic uncertainty.

## Language support boundary

The language registry covers more authored-source formats than the prerelease
certification matrix. These concepts must remain distinct:

- **Recognized source:** affects repository identity and validation scope.
- **Structural indexing:** parser can emit nodes/imports and supported edges.
- **Caller depth:** parser/spec can certify caller/callee relationships.
- **Semantic graph:** source-semantic facts beyond the structural graph.
- **Certified language:** passed real-repository build, truth, lifecycle, query,
  persistence, and update gates for the current candidate.

Historical real-repository certification covers Python, JavaScript,
TypeScript, Go, Rust, and Java structural graphs with declared parser limits.
Only Python currently has the deep semantic-graph layer. A new candidate SHA
must rerun the language matrix before release; registry presence alone is not a
certification claim.

## Failure policy

Treatment states are `ACTIVE`, `NOT_APPLICABLE`, and `FAILED`.

- `ACTIVE` requires a ready exact-revision graph, at least one revision-bound
  evidence claim, successful compilation, and in `hybrid_required` mode a ready
  dense index.
- `NOT_APPLICABLE` is reserved for a genuinely unsupported repository/task
  where no honest graph treatment can be supplied.
- `FAILED` identifies a product defect or runtime failure.

When GroundTruth is requested, graph/dense/compile failure occurs before the
first provider request and raises `TreatmentUnavailableError`; it never
silently falls back to bare. During a run, stale state is rebuilt before new
context can be delivered. If refresh cannot restore readiness, no graph-derived
delta is emitted and the failure is recorded.

Explicit failure is preferable to apparently valid but wrong intelligence.
However, explicit correctness of the infrastructure does not excuse bad
ranking: a real but irrelevant edit target is a treatment-quality defect and a
benchmark regression can block release even when every receipt is valid.

## Canonical code ownership

| Area | Classification | Architectural responsibility |
| --- | --- | --- |
| `gt_harness/` | PRODUCTION | CLI, treatment lifecycle, Mini-SWE seam, comparison, outcomes, certification, and indexer provisioning. |
| `gt_engine/repository_graph_service.py` | PRODUCTION | Sole graph identity/readiness/build/query boundary. |
| `vendor/gt-index-src/` | PRODUCTION | Source-built multi-language structural graph writer. |
| `gt_engine/dense_semantic_index.py` and `snowflake_onnx.py` | PRODUCTION | Persistent exact-revision dense retrieval and pinned local inference. |
| `gt_engine/hybrid_repository.py` and `hybrid_retrieval.py` | PRODUCTION | Exact/sparse/structural/dense candidate construction and fusion. |
| `gt_engine/repository_context_compiler.py` | PRODUCTION | Task facets, role separation, selection, evidence ledger, and context packet. |
| `gt_engine/graph_db_projection.py` | PRODUCTION | Bounded persisted process, impact, and test projections. |
| `gt_engine/semantic_graph.py` | PRODUCTION | Deterministic supported source-semantic facts. |
| `eval/*_gt_harness_adapter.py` | PRODUCTION ADAPTERS | Suite protocol translation into the same product command. |
| Canonical product workflows | PRODUCTION WORKFLOWS | Immutable planning, provider gate, task execution, artifacts, and attestation. |
| `production-surface.toml` | PRODUCTION CONTRACT | Exact installed-module, workflow, schema, budget, and language-candidate allowlist. |
| `src/groundtruth/` | LEGACY / GIT HISTORY | Excluded from the wheel and forbidden from the canonical runtime dependency closure. |
| `eval/gt_central_agent.py`, older central control layers, historical workflows | RESEARCH / LEGACY | Experimental evidence; not a dispatchable or certifying product path. |
| `eval/pier_gt_adapter.py`, Nano paths, MCP treatment, old headless/mini-patch scripts | FORBIDDEN | Must not be used for official GT Harness evaluation. |

A module's presence does not establish reachability. Before deleting legacy or
research code, prove it has no canonical imports, workflow references, console
entry points, or unique behavior required by the product; then remove it or
archive its evidence with an explicit disposition.

## Security and isolation

- Provider credentials are scoped to provider transport and redacted from
  receipts.
- Repository shell commands execute without provider credentials.
- The graph and dense systems make no external provider calls.
- Model directories and binaries are checksum/content-addressed.
- Receipts store hashes for sensitive configuration such as the base URL rather
  than secret values.
- Benchmark task and verifier artifacts remain separated from retrieval input.

## Performance model

Costs are measured independently:

- cold graph build time and peak memory;
- warm validation/reuse time;
- dense build and query time;
- graph/dense persistent bytes;
- context compile and delivery time;
- context tokens per delivery;
- provider calls, input/cache/output tokens, wall time, and steps; and
- total cost and cost per solved task.

Correctness gates precede optimization. Bounded retrieval, projection ceilings,
set cover, claim deduplication, and compact serialization control overhead.
Any optimization that removes relevant facts or promotes weak candidates to
edit authority is a regression, even if it saves tokens.

## Verification map

| Boundary | Required verification |
| --- | --- |
| Install/build | Clean Linux checkout, pinned dependencies, source-built Go indexer with `sqlite_fts5`, pinned ONNX checksum. |
| Graph truth | Real repositories plus independent AST/LSP/compiler/search evidence; precision/recall by relationship and language. |
| Lifecycle | Cold/warm, dirty tree, commit change, add/modify/delete/rename, interrupted build, corruption, concurrent access. |
| Compiler | Real task statements, post-hoc successful-patch path audit, exact-role recall, false edit-authority rate, packet bounds. |
| Treatment | Zero treatment provider calls, initial delivery before call 1, one delta per turn, raw-output preservation, claim reconciliation. |
| Agent | Mini-SWE-Agent 2.4.6 identity, common prompt/tools/settings, complete trajectory, terminal receipt. |
| Benchmark | Official verifier, exact paired identity, solve/flip/efficiency metrics, all-trajectory review. |
| Release | Exact candidate SHA certification and hosted artifact attestation; no critical defect or excessive negative flips. |

Provider-free pre-dispatch verification is defined in `AGENTS.md` and the
canonical certification workflow. Paid smoke results are evidence only for the
exact source SHA and experiment contract they executed.

## Current architectural risk exposed by the smoke campaign

The DeepSWE smoke run `32928374228` at `eac111b` demonstrated why the
architecture separates graph health from localization utility: all graded
treatments had valid ACTIVE receipts while seven baseline-only losses
included confident-but-wrong edit authority (`arktype` bound the quoted
prose noun `'type'`; `bandit-interprocedural` case-matched the acronym
`CWE.SQL_INJECTION` onto a same-named dataclass). The compiler now enforces
identity-quality rules that keep those failures offline:

- bare lowercase generic prose nouns never bind symbol identity;
- short ALL-CAPS task tokens require exact-case repository matches;
- an all-lowercase symbol naming its own file or filename token
  (entry/barrel/plugin shape) is inspection evidence, never edit authority;
- exception entities introduced by throw/raise verbs stay retrieval terms;
- a globally unscoped symbol name resolving across unrelated files demotes
  every candidate unless certified export structure connects them;
- zero-facet exact-path rows become edit targets only when the task itself
  cites the file path or name; and
- provider compaction retains decision-grade roles before rank-only noise.

The release gate therefore includes the committed
[smoke20 localization truth report](docs/deepswe_smoke20_localization_truth.json)
verified by `scripts/localization_truth_gate.py` as a certification step.
The report is fingerprint-bound to the context compiler, must show zero
wrong edit targets and zero case failures, must hold mean edit-target
precision at or above 0.7, and must hold mean edit-target recall at or
above 0.5 across the frozen cohort replay. Regenerating the report is a
deterministic, provider-free local action against the exact cohort
revisions.

### Latent-regression sweep (2026-08-26)

A second sweep found and repaired latent major-regression risks that a
paid run would otherwise have surfaced:

- distinct obligations were dropped by substring deduplication
  (`Create foo.txt.bak` vanished behind `Create foo.txt`); dedup is now
  exact-key only;
- `_DIRECTIVE_RE` missed `fix/update/patch/refactor/bug` prose, so plain
  non-bullet obligations like `Fix NPE in Foo when config is null` never
  became a facet; the directive set now covers the edit family;
- `_task_cites_path` matched an extensionless filename token (script
  `config`) against the prose word `config`, granting a wrong edit target;
  the bare filename now requires a word boundary;
- a dense inspection candidate could seed graph-expansion file anchors and
  promote spurious certified public-surface/integration rows; dense-only
  file anchors are excluded from `file_anchors`;
- packet `truncated` ignored repository-side branch/expansion truncation,
  so high-fan-out graphs could claim `truncated=false`; repository
  truncation reasons now propagate to the packet.

The implementation now uses typed requirement-resolution rows, exposes
`AMBIGUOUS_IDENTITY` without granting false edit authority, separates all
candidate roles, and attaches bounded post-action evidence on the triggering
observation. The old 0.0845 recall receipt remains historical evidence, not a
claim about this revision. A fresh `hybrid_required` smoke20 replay must meet
the fail-closed precision, recall, treatment, and dense-readiness gates before
the candidate SHA can be called benchmark-ready.

GT Harness is complete only when the substrate is exact, the delivered facts
are decision-useful, the integration is faithful, and controlled results show
that those facts improve or preserve outcomes efficiently.

## Hosted-certification drift correction (2026-08-26)

The v5 graph architecture publishes build progress in `build-attempt.json` and
only publishes an immutable generation through `CURRENT` after validation.
Run `33013230307` proved that two audit programs still watched the obsolete
mutable receipt path. That was verifier drift: the graph correctly refused to
publish a partial generation, but the campaigns could not deterministically
intercept the build. Both campaigns now launch an isolated process group,
observe the durable attempt journal, terminate the whole build tree, and assert
that no interrupted generation is queryable. An interrupted cold build may
terminalize to `ABSENT`; an interrupted update must expose the existing
generation as `STALE`, never current.

The same run exposed a delivery defect, not missing substrate facts. Task prose
often names a repository type as the grammatical subject of a behavior clause
without quoting it, for example `Reporter constructor ...`. Context v7 now
admits that form only when an exact repository symbol exists. Identity
selection then prefers production definitions over test/example/generated
homonyms while preserving same-tier production ambiguity. Exact identity,
task-path, and hybrid retrieval owner candidates remain inspection/edit typed
and are selected with bounded facet cover. This raises decision-point recall
without turning arbitrary capitalized prose or graph proximity into edit
authority.

The invariant remains conservative: qualification alone does not authorize an
edit. A referenced future call such as `via Server.resetAbort()` keeps `Server`
as an inspection owner unless a sentence-scoped edit directive names it. Thus
the recall repair preserves the architecture's separation between where the
agent may edit and what it should inspect, verify, or understand.

Run `33024039628` refined that invariant. A PascalCase behavioral subject is
now eligible only when the repository contains the exact spelling; `Handle`
cannot case-fold onto a lowercase function `handle`. Edit-scope matching is
also identifier-bounded, so `handle` cannot match inside `handles` or another
identifier.

Owner delivery is ordered by direct identity affinity before graph centrality
or broad facet coverage. The compiler compares the task to the candidate's own
symbol and path components, demotes package-echo modules, retains a bounded
24-row internal pool, and selects three owners with deterministic tie-breaks.
At the emergency 500-token floor the serializer chooses one scoped
implementation owner ahead of unrelated ambiguity or rank-only evidence.
This is not oracle-fed ranking: every input is task text plus exact repository
identity and typed evidence provenance.

Run `33031285044` exposed the final known localization boundary in this
sequence. Exact graph membership did not guarantee that a literal module path
entered the task-conditioned repository projection, and a compiled acceptable
owner did not guarantee survival at the emergency provider-token floor. The
architecture therefore has two explicit, independently bounded recall paths:

1. fused exact/sparse/structural/dense retrieval for ranked relevance; and
2. per-term graph-FTS path identity for task-named repository artifacts that a
   combined rank window could crowd out.

The second path is inspection-only input to the same context compiler. It can
neither create an edit target nor bypass source revision, graph readiness, or
facet checks. Final owner ordering measures the candidate's own symbol/file
identity, leaf-plus-parent scope, and uncovered obligation contribution;
shared package directories and graph degree cannot dominate those signals.
Fresh exact-revision KaTeX and Bandit replays prove that the resulting owners
survive final delivery with coverage 1.0 and no false edit authority. A new
exact-SHA hosted campaign must still certify the complete twenty-task cohort.

Run `33037863387` tested that augmentation at commit
`1b68176fd43b7d749f1f502c519636208888e8a6`. Every graph, lifecycle,
language, dense-readiness, Mini-SWE-Agent E2E, and failure-campaign gate
passed. Localization retained exact-edit precision 1.0 and ambiguity recall
1.0, but required-fact coverage fell to 0.85 because three fallback owners
displaced stronger evidence at the 500-token boundary. This was a compiler
ordering defect, not a graph or model failure.

The correction distinguishes three claims. First, explicit node identities
and graph-projection facts are stronger than path-only augmentation and must
be materialized before the document bound is applied. Second, a common-noun
path is only a fallback: the task must locally name a leaf/parent scope or all
components of a compound filename. Thus `array-like environments` can identify
`environments/array.ts`, while distant `size`/`filtering` clauses cannot create
`filter/size.rs`, and isolated API words cannot create `eval.rs`, `convert.rs`,
or `shared.rs`. Third, an exact non-edit symbol becomes an implementation owner
only when a task facet identifies it as an owner; an argument named `handle`
remains ordinary inspection evidence. Within equal identity affinity, direct
evidence precedes fallback evidence.

Warm production-path replays of the five affected exact revisions now have
required-fact coverage 1.0, no false edit authority, no treatment failures,
and no dense-readiness failures. The full Python suite, Go SQLite-FTS5 suite,
changed-file Ruff, product-surface lint, wheel verification, and CLI lifecycle
also pass locally. These constraints recover decision-point delivery without
expanding edit authority or packet size. A new exact-SHA hosted certification
is still required.

## Provider planning and repository architecture closure (2026-08-27)

The provider boundary now has one selection policy. The compiler retains the
complete typed evidence ledger; `gt_harness.provider_planning` selects a
source/graph-generation-bound subset by requirement coverage, proof authority,
decision role, serialized cost, and stable claim identity. It rejects edit
claims without exact identity, records every omitted claim and reason, and
searches for the largest plan that fits the actual 500/350-token serializer.
The previous role slices and emergency compaction sequence was deleted because
it could discard a second requirement owner, silently change policy after
selection, or collapse a fitting packet to zero evidence.

Provider roles are not an undifferentiated file list: `EDIT`,
`IMPLEMENTATION_OWNER`, `PUBLIC_SURFACE`, `INTEGRATION`, `RELATION`, `PROCESS`,
`IMPACT`, `AFFECTED_TEST`, `VALIDATION`, `ARCHITECTURE`, `SEMANTIC`,
`AMBIGUITY`, and `INSPECTION`. Exact identities and certified relations outrank
manifest architecture; architecture outranks loose semantic or rank-only
support. Package/build metadata cannot grant edit authority.

`gt_engine.repository_architecture` supplies deterministic structure between
file/symbol graphs and higher-order decisions. One bounded manifest scan
projects packages, workspaces, public surfaces, entry points, build targets,
test targets, and dependencies for Python, JavaScript/TypeScript, Go, Rust,
Maven, and literal Gradle projects. Every projection binds the exact source
revision plus hashes of its manifests. Dynamic declarations are explicit
limitations. The treatment caches only the matching source revision and
selects at most eight task/path-scoped architecture facts.

```text
exact repository snapshot
  -> atomic graph generation and receipt
  -> sparse/structural/dense query repository
  -> typed requirements and evidence authorities
  -> symbol/relation/process/impact/semantic projections
  -> source-bound manifest architecture projection
  -> single coverage-aware provider plan and omission receipt
  -> Mini-SWE-Agent 2.4.6 observation
  -> uptake/edit/validation attribution
```

No GitNexus code or data is imported. GT retains the useful deterministic
higher-order-views lesson while differentiating through proof-carrying revision
receipts, authority separation, correct-or-quiet degradation, and an auditable
provider-budget planner.
